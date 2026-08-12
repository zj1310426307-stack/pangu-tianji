"""Exactly-once observational job records with a strict terminal state machine."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any

from .context import current_context, new_id
from .contracts import JOB_TRANSITIONS, JobStatus, canonical_hash
from .store import ObservabilityStore

try:
    from .metrics import MetricRegistry
except ImportError:  # pragma: no cover - defensive import boundary
    MetricRegistry = Any


class JobMonitor:
    """Record scheduler/CLI/web jobs without owning or rerunning their work."""

    def __init__(self, store: ObservabilityStore, metrics: MetricRegistry | None = None) -> None:
        self.store = store
        self.metrics = metrics

    def create(
        self,
        job_type: str,
        idempotency_key: str,
        *,
        scheduled_for: str | None = None,
        max_attempts: int = 1,
        linked_run_id: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one job row or return the existing row for the same key."""
        existing = self.store.one("SELECT * FROM job_runs WHERE idempotency_key=?", (idempotency_key,))
        if existing:
            return self._decode(existing)
        job_id = new_id("job")
        context = current_context()
        payload = evidence or {}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        try:
            self.store.execute(
                """INSERT INTO job_runs(job_id,idempotency_key,job_type,scheduled_for,status,attempt,max_attempts,trace_id,
                linked_run_id,evidence_json,evidence_hash,can_auto_retry,can_trade,can_create_orders)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id, idempotency_key, job_type, scheduled_for, JobStatus.CREATED.value, 1, max(1, int(max_attempts)),
                    context.trace_id if context else None, linked_run_id, encoded, canonical_hash(payload), 0, 0, 0,
                ),
            )
        except Exception:
            concurrent = self.store.one("SELECT * FROM job_runs WHERE idempotency_key=?", (idempotency_key,))
            if concurrent:
                return self._decode(concurrent)
            raise
        return self.get(job_id)

    def transition(
        self,
        job_id: str,
        target: JobStatus | str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compare-and-set one legal state transition; terminal states cannot reverse."""
        destination = JobStatus(target)
        with closing(self.store.transaction()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM job_runs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise ValueError("Job 不存在")
            origin = JobStatus(row["status"])
            if destination not in JOB_TRANSITIONS.get(origin, frozenset()):
                raise ValueError(f"Job 状态不可从 {origin.value} 转为 {destination.value}")
            now = datetime.now(timezone.utc).isoformat()
            start = now if destination is JobStatus.RUNNING else row["started_at"]
            heartbeat = now if destination is JobStatus.RUNNING else row["heartbeat_at"]
            completed = now if destination not in {JobStatus.CREATED, JobStatus.RUNNING} else None
            payload = evidence if evidence is not None else json.loads(row["evidence_json"])
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            cursor = connection.execute(
                """UPDATE job_runs SET started_at=?,heartbeat_at=?,completed_at=?,status=?,error_code=?,error_message=?,
                evidence_json=?,evidence_hash=? WHERE job_id=? AND status=?""",
                (
                    start, heartbeat, completed, destination.value, error_code, str(error_message or "")[:800] or None,
                    encoded, canonical_hash(payload), job_id, origin.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Job 状态已被并发修改")
            connection.commit()
        result = self.get(job_id)
        self._record_transition(result, destination)
        return result

    def heartbeat(self, job_id: str) -> dict[str, Any]:
        """Update only the heartbeat of a running job."""
        changed = self.store.execute(
            "UPDATE job_runs SET heartbeat_at=? WHERE job_id=? AND status='RUNNING'",
            (datetime.now(timezone.utc).isoformat(), job_id),
        )
        if changed != 1:
            raise ValueError("仅 RUNNING Job 可写心跳")
        return self.get(job_id)

    def _record_transition(self, job: dict[str, Any], status: JobStatus) -> None:
        """Publish low-cardinality job metrics when a registry is configured."""
        if self.metrics is None:
            return
        labels = {"job_type": str(job.get("job_type") or "unknown")[:64], "status": status.value.lower()}
        try:
            if status is JobStatus.RUNNING:
                self.metrics.record("job.started", "COUNTER", 1, "count", labels=labels, source="job_monitor", trace_id=job.get("trace_id"))
                return
            self.metrics.record("job.completed", "COUNTER", 1, "count", labels=labels, source="job_monitor", trace_id=job.get("trace_id"))
            if status is JobStatus.FAILED:
                self.metrics.record("job.failed", "COUNTER", 1, "count", labels=labels, source="job_monitor", trace_id=job.get("trace_id"))
            if status is JobStatus.BLOCKED:
                self.metrics.record("job.blocked", "COUNTER", 1, "count", labels=labels, source="job_monitor", trace_id=job.get("trace_id"))
            if job.get("started_at") and job.get("completed_at"):
                started = datetime.fromisoformat(str(job["started_at"]))
                completed = datetime.fromisoformat(str(job["completed_at"]))
                self.metrics.record("job.duration", "DURATION", max(0.0, (completed - started).total_seconds()), "seconds", labels=labels, source="job_monitor", trace_id=job.get("trace_id"))
            self.metrics.record("job.retry.count", "GAUGE", max(0, int(job.get("attempt") or 1) - 1), "count", labels={"job_type": labels["job_type"], "status": labels["status"]}, source="job_monitor", trace_id=job.get("trace_id"))
        except Exception:
            return

    def mark_stale(self, stale_after_seconds: int) -> list[str]:
        """Mark expired heartbeats STALE without terminating or rerunning business work."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(1, stale_after_seconds))).isoformat()
        rows = self.store.all("SELECT job_id FROM job_runs WHERE status='RUNNING' AND heartbeat_at<?", (cutoff,))
        changed: list[str] = []
        for row in rows:
            self.transition(row["job_id"], JobStatus.STALE, error_code="HEARTBEAT_EXPIRED")
            changed.append(row["job_id"])
        return changed

    def get(self, job_id: str) -> dict[str, Any]:
        """Read one job and restore its typed evidence."""
        row = self.store.one("SELECT * FROM job_runs WHERE job_id=?", (job_id,))
        if not row:
            raise ValueError("Job 不存在")
        return self._decode(row)

    def list(self, *, status: str | None = None, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """List bounded jobs, optionally filtered by status and linked identifiers."""
        clauses: list[str] = []
        parameters: list[Any] = []
        if status:
            clauses.append("status=?")
            parameters.append(JobStatus(status).value)
        if query:
            clauses.append("(job_id LIKE ? OR linked_run_id LIKE ? OR trace_id LIKE ?)")
            needle = f"%{query}%"
            parameters.extend((needle, needle, needle))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(int(limit), 500)))
        return [self._decode(row) for row in self.store.all(f"SELECT * FROM job_runs {where} ORDER BY COALESCE(started_at,scheduled_for) DESC LIMIT ?", parameters)]

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["evidence"] = json.loads(payload.pop("evidence_json"))
        payload["can_auto_retry"] = False
        payload["can_trade"] = False
        payload["can_create_orders"] = False
        return payload
