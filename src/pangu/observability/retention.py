"""Plan-first retention confined to unreferenced observability evidence."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import uuid4

from .contracts import canonical_hash
from .store import ObservabilityStore


class RetentionService:
    """Delete only eligible telemetry after a separately persisted plan."""

    def __init__(self, store: ObservabilityStore, config: dict[str, Any]) -> None:
        self.store = store
        self.config = dict(config)

    def plan(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Create an immutable count/time-range plan without deleting anything."""
        current = now or datetime.now(timezone.utc)
        metric_cutoff = current - timedelta(days=int(self.config["raw_metric_retention_days"]))
        trace_cutoff = current - timedelta(days=int(self.config["trace_retention_days"]))
        trace_rows = self.store.all(
            """SELECT trace_id,started_at FROM traces WHERE started_at<? AND trace_id NOT IN
            (SELECT link_id FROM incident_links WHERE link_type='trace')""", (trace_cutoff.isoformat(),)
        )
        metric_row = self.store.one("SELECT COUNT(*) AS count,MIN(timestamp) AS earliest,MAX(timestamp) AS latest FROM metric_samples WHERE timestamp<?", (metric_cutoff.isoformat(),)) or {}
        payload = {
            "retention_id": f"retention-{uuid4().hex}", "planned_at": current.isoformat(),
            "criteria": {"metric_before": metric_cutoff.isoformat(), "trace_before": trace_cutoff.isoformat()},
            "candidates": {
                "metric_samples": {"count": int(metric_row.get("count") or 0), "earliest": metric_row.get("earliest"), "latest": metric_row.get("latest")},
                "traces": {"count": len(trace_rows), "ids": [row["trace_id"] for row in trace_rows[:500]]},
            },
            "protected": {"open_alerts": True, "open_incidents": True, "incident_linked_traces": True, "business_databases": True, "backups": True},
            "can_auto_remediate": False,
        }
        payload["plan_hash"] = canonical_hash(payload)
        self.store.execute(
            "INSERT INTO retention_runs(retention_id,plan_hash,planned_at,status,plan_json,deleted_json,can_auto_remediate) VALUES(?,?,?,?,?,?,?)",
            (payload["retention_id"], payload["plan_hash"], payload["planned_at"], "PLANNED", json.dumps(payload, ensure_ascii=False, sort_keys=True), "{}", 0),
        )
        return payload

    def run(self, retention_id: str, plan_hash: str) -> dict[str, Any]:
        """Execute exactly the persisted plan; referenced traces remain protected."""
        with closing(self.store.transaction()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM retention_runs WHERE retention_id=?", (retention_id,)).fetchone()
            if not row or row["status"] != "PLANNED" or row["plan_hash"] != plan_hash:
                raise ValueError("Retention 计划不存在、已执行或哈希不匹配")
            plan = json.loads(row["plan_json"])
            metric_cursor = connection.execute("DELETE FROM metric_samples WHERE timestamp<?", (plan["criteria"]["metric_before"],))
            trace_ids = [item[0] for item in connection.execute(
                """SELECT trace_id FROM traces WHERE started_at<? AND trace_id NOT IN
                (SELECT link_id FROM incident_links WHERE link_type='trace')""", (plan["criteria"]["trace_before"],)
            ).fetchall()]
            if trace_ids:
                placeholders = ",".join("?" for _ in trace_ids)
                connection.execute(f"DELETE FROM spans WHERE trace_id IN ({placeholders})", trace_ids)
                connection.execute(f"DELETE FROM traces WHERE trace_id IN ({placeholders})", trace_ids)
            deleted = {"metric_samples": metric_cursor.rowcount, "traces": len(trace_ids)}
            connection.execute(
                "UPDATE retention_runs SET executed_at=?,status='SUCCEEDED',deleted_json=? WHERE retention_id=? AND status='PLANNED'",
                (datetime.now(timezone.utc).isoformat(), json.dumps(deleted, sort_keys=True), retention_id),
            )
            connection.commit()
        return {"retention_id": retention_id, "status": "SUCCEEDED", "deleted": deleted, "plan_hash": plan_hash, "can_auto_remediate": False}

    def history(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.store.all("SELECT * FROM retention_runs ORDER BY planned_at DESC LIMIT ?", (max(1, min(limit, 100)),))
        for row in rows:
            row["plan"] = json.loads(row.pop("plan_json"))
            row["deleted"] = json.loads(row.pop("deleted_json"))
        return rows
