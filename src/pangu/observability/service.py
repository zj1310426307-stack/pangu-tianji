"""Read-mostly facade for local observability and human governance actions."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
import threading
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from .alert_engine import AlertEngine
from .adapters import LocalEvidenceAdapter
from .context import create_context, telemetry_context
from .contracts import JobStatus, MetricType, OBSERVABILITY_SERVICE_VERSION, TelemetryStatus
from .incident_service import IncidentService
from .job_monitor import JobMonitor
from .metrics import MetricRegistry
from .retention import RetentionService
from .slo import SLOService
from .store import ObservabilityStore
from .tracing import TraceService


class ObservabilityService:
    """Compose telemetry and governance without importing execution modules."""

    def __init__(
        self,
        project_root: Path,
        config: dict[str, Any],
        config_hash: str,
        *,
        engineering_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.root = Path(os.path.abspath(project_root))
        self.config = dict(config)
        db = Path(str(self.config["database"]))
        self.store = ObservabilityStore(self.root / db)
        self.metrics = MetricRegistry(self.store)
        self.traces = TraceService(self.store)
        self.jobs = JobMonitor(self.store, self.metrics)
        self.slos = SLOService(self.store, self.metrics, list(self.config.get("slos") or []), config_hash)
        self.alerts = AlertEngine(
            self.store,
            cooldown_seconds=int(self.config.get("alert_cooldown_seconds", 900)),
            consecutive_triggers=int(self.config.get("alert_consecutive_triggers", 1)),
        )
        self.incidents = IncidentService(self.store)
        self.retention = RetentionService(self.store, dict(self.config["retention"]))
        self.engineering_provider = engineering_provider
        self.evidence_adapter = LocalEvidenceAdapter(self.root)
        self._request_lock = threading.Lock()
        self._active_requests = 0

    @staticmethod
    def safety() -> dict[str, bool]:
        """Fix all investment and remediation capabilities to false."""
        return {
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
            "can_modify_portfolio": False,
            "can_modify_risk": False,
            "can_auto_remediate": False,
            "can_access_broker_credentials": False,
        }

    def begin_request(self, *, request_id: str | None = None, trace_id: str | None = None) -> tuple[Any, float]:
        """Start a generic API trace before routing, without reading request content."""
        context = create_context(trace_id=trace_id, request_id=request_id, operation_id="api.request")
        self.traces.start_trace("api.request", context=context, linked_ids={"request_id": context.request_id or ""})
        with self._request_lock:
            self._active_requests += 1
            active = self._active_requests
        self.metrics.record("api.request.concurrent", MetricType.GAUGE, active, "count", labels={"status": "active"}, source="fastapi", trace_id=context.trace_id)
        return context, perf_counter()

    def finish_request(
        self,
        context,
        started: float,
        *,
        operation_id: str,
        method: str,
        status_code: int,
        blocked: bool = False,
        error_code: str | None = None,
    ) -> None:
        """Finish an API trace and append bounded duration/success/error metrics."""
        duration_ms = max(0.0, (perf_counter() - started) * 1000)
        status_class = f"{status_code // 100}xx"
        with self._request_lock:
            self._active_requests = max(0, self._active_requests - 1)
            active = self._active_requests
        with telemetry_context(context):
            span = self.traces.start_span(
                operation_id, "fastapi",
                attributes={"operation_id": operation_id, "method": method, "status_class": status_class},
            )
            terminal = TelemetryStatus.BLOCKED if blocked else (TelemetryStatus.ERROR if status_code >= 500 else TelemetryStatus.OK)
            self.traces.finish_span(span, status=terminal, error_code=error_code)
            self.metrics.record("api.request.count", MetricType.COUNTER, 1, "count", labels={"operation_id": operation_id, "method": method, "status_class": status_class}, source="fastapi")
            self.metrics.record("api.request.duration", MetricType.DURATION, duration_ms, "ms", labels={"operation_id": operation_id, "method": method, "status_class": status_class}, source="fastapi")
            self.metrics.record("api.request.concurrent", MetricType.GAUGE, active, "count", labels={"status": "active"}, source="fastapi")
            self.metrics.record("api.request.success", MetricType.STATUS, 1 if status_code < 400 else 0, "ratio", labels={"operation_id": operation_id, "status_class": status_class}, source="fastapi")
            if status_code >= 500:
                self.metrics.record("api.request.server_error", MetricType.COUNTER, 1, "count", labels={"operation_id": operation_id, "status_class": status_class}, source="fastapi")
            if blocked:
                self.metrics.record("api.local_write.rejected", MetricType.COUNTER, 1, "count", labels={"operation_id": operation_id, "status": "blocked"}, source="fastapi")
            self.traces.finish_trace(context.trace_id, status=terminal, error_code=error_code)

    def evaluate(self) -> dict[str, Any]:
        """Sample existing engineering evidence and evaluate SLO/alerts explicitly."""
        context = self.traces.start_trace("observability.evaluate", linked_ids={})
        job = self.jobs.create("observability_evaluation", f"observability:{datetime.now(timezone.utc).isoformat()}", evidence={"source": "explicit"})
        self.store.execute("UPDATE job_runs SET trace_id=? WHERE job_id=?", (context.trace_id, job["job_id"]))
        self.jobs.transition(job["job_id"], JobStatus.RUNNING)
        try:
            with telemetry_context(context):
                engineering = self.engineering_provider() if self.engineering_provider else {}
                external = self.evidence_adapter.collect()
                health = dict(engineering.get("health") or {})
                if health.get("score") is not None:
                    self.metrics.record("engineering.health.score", MetricType.GAUGE, float(health["score"]), "score", labels={"status": str(health.get("status") or "unknown")}, source="health_center")
                components = dict(health.get("components") or {})
                data = dict(components.get("data") or {})
                database = dict(components.get("database") or {})
                ai = dict(components.get("ai") or {})
                strategy = dict(components.get("strategy") or {})
                if data.get("score") is not None:
                    self.metrics.record("data.health.score", "GAUGE", float(data["score"]), "score", labels={"status": str(data.get("status") or "unknown")}, source="health_center")
                self.metrics.record("data.verified.run.count", "GAUGE", float(external["data"]["verified_run_count"]), "count", labels={"source": "data_center"}, source="data_center")
                data_intelligence = dict(external["data_intelligence"])
                if data_intelligence.get("latest_score") is not None:
                    self.metrics.record(
                        "data.intelligence.health.score", "GAUGE",
                        float(data_intelligence["latest_score"]), "score",
                        labels={"status": str(data_intelligence.get("latest_status") or "unknown")},
                        source="data_intelligence",
                    )
                for level in ("warning", "error", "blocked"):
                    self.metrics.record(
                        "data.intelligence.incident.count", "GAUGE",
                        float(data_intelligence.get(f"open_{level}_count") or 0), "count",
                        labels={"status": level}, source="data_intelligence",
                    )
                self.metrics.record("quant.artifact.invalid.count", "GAUGE", float(external["quant_lab"]["invalid_json_artifact_count"]), "count", labels={"source": "quant_lab"}, source="quant_lab")
                db_details = list((database.get("evidence") or {}).get("databases") or [])
                for item in db_details:
                    self.metrics.record("database.quick_check.success", "STATUS", 1 if item.get("quick_check") == "ok" else 0, "ratio", labels={"database": Path(str(item.get("path") or "unknown")).name[:64]}, source="health_center")
                    database_path = Path(str(item.get("path") or ""))
                    if database_path.is_file():
                        self.metrics.record("database.file.size", "GAUGE", float(database_path.stat().st_size), "bytes", labels={"database": database_path.name[:64]}, source="health_center")
                self.metrics.record("database.file.count", "GAUGE", float(len(db_details)), "count", labels={"source": "health_center"}, source="health_center")
                backups = list(engineering.get("backups") or [])
                if backups:
                    self.metrics.record("backup.verification.success", "STATUS", 1 if backups[0].get("status") == "SUCCEEDED" else 0, "ratio", labels={"status": str(backups[0].get("status") or "unknown")}, source="backup")
                ai_audit = dict(external["ai"])
                if ai_audit.get("latest_status") in {"succeeded", "failed"}:
                    self.metrics.record(
                        "ai.explicit.success", "STATUS",
                        1 if ai_audit["latest_status"] == "succeeded" else 0, "ratio",
                        labels={"status": str(ai_audit["latest_status"])}, source="ai_audit",
                    )
                self.alerts.observe("engineering-unhealthy", health.get("status") == "UNHEALTHY", root_key="engineering", evidence={"health_id": health.get("health_id"), "score": health.get("score"), "status": health.get("status")}, trace_id=context.trace_id, job_id=job["job_id"])
                data_intelligence_failed = (
                    data_intelligence.get("latest_status") in {"ERROR", "BLOCKED"}
                    or int(data_intelligence.get("open_error_count") or 0) > 0
                    or int(data_intelligence.get("open_blocked_count") or 0) > 0
                )
                self.alerts.observe("data-intelligence-error", data_intelligence_failed, root_key="data-intelligence", evidence=data_intelligence, trace_id=context.trace_id, job_id=job["job_id"])
                self.alerts.observe("sqlite-quick-check", any(item.get("quick_check") != "ok" for item in db_details), root_key="database", evidence={"failed": [item.get("path") for item in db_details if item.get("quick_check") != "ok"]}, trace_id=context.trace_id, job_id=job["job_id"])
                self.alerts.observe("strategy-version-drift", strategy.get("status") == "UNHEALTHY", root_key="production-contract", evidence=strategy.get("evidence") or {}, trace_id=context.trace_id, job_id=job["job_id"])
                self.alerts.observe("quant-artifact-invalid", external["quant_lab"]["invalid_json_artifact_count"] > 0, root_key="quant-lab-json", evidence=external["quant_lab"], trace_id=context.trace_id, job_id=job["job_id"])
                stale_data = self._is_stale(external["data"].get("latest_mtime"), int(self.config.get("latest_data_stale_hours", 36)))
                self.alerts.observe("latest-data-stale", stale_data, root_key="data-center-latest", evidence=external["data"], trace_id=context.trace_id, job_id=job["job_id"])
                formal_due = self._formal_research_due()
                if formal_due:
                    self.metrics.record("research.formal.available", "STATUS", 0 if stale_data else 1, "ratio", labels={"status": "missing" if stale_data else "available"}, source="data_center")
                formal_missing = stale_data or external["data"].get("latest_run_kind") != "formal_close_plan"
                self.alerts.observe("formal-research-missing", formal_due and formal_missing, root_key=f"formal-research:{datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()}", evidence={"due": formal_due, **external["data"]}, trace_id=context.trace_id, job_id=job["job_id"])
                backup_evidence = {"audit": backups[:1], **external["backups"]}
                latest_backup_time = self._backup_timestamp(backups[0]) if backups else external["backups"].get("latest_at")
                backup_bad = (
                    not backups
                    or backups[0].get("status") != "SUCCEEDED"
                    or self._is_stale(latest_backup_time, int(self.config.get("backup_stale_hours", 168)))
                )
                self.alerts.observe("backup-stale-or-invalid", backup_bad, root_key="engineering-backup", evidence=backup_evidence, trace_id=context.trace_id, job_id=job["job_id"])
                stale_jobs = self.jobs.mark_stale(int(self.config.get("job_stale_seconds", 900)))
                known_stale_jobs = self.jobs.list(status="STALE", limit=50)
                self.alerts.observe("job-heartbeat-stale", bool(known_stale_jobs), root_key="observability-jobs", evidence={"newly_stale_job_ids": stale_jobs, "stale_job_ids": [item["job_id"] for item in known_stale_jobs]}, trace_id=context.trace_id, job_id=job["job_id"])
                api_start = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                api_latency = self.metrics.metric_summary("api.request.duration", start=api_start)
                api_errors = self.metrics.metric_summary("api.request.server_error", start=api_start)
                api_bad = (
                    (api_latency.get("p95") is not None and float(api_latency["p95"]) > float(self.config.get("api_slow_ms", 1500)))
                    or int(api_errors.get("count") or 0) > 0
                )
                self.alerts.observe("api-error-or-latency", api_bad, root_key="local-fastapi", evidence={"duration": api_latency, "server_errors": api_errors}, trace_id=context.trace_id, job_id=job["job_id"])
                self.alerts.observe("ai-explicit-failures", int(ai_audit.get("trailing_failure_count") or 0) >= int(self.config.get("ai_consecutive_failure_threshold", 2)), root_key="explicit-ai", evidence=ai_audit, trace_id=context.trace_id, job_id=job["job_id"])
                ai_enabled = str((ai.get("evidence") or {}).get("state") or "").lower() in {"connected", "ready", "healthy"}
                slos = self.slos.evaluate_all(ai_enabled=ai_enabled)
                snapshot = {
                    "health_id": health.get("health_id"),
                    "health_status": health.get("status"),
                    "health_score": health.get("score"),
                    "components": components,
                    "config_hash": health.get("config_hash"),
                    "version_manifest_hash": health.get("version_manifest_hash"),
                    "trace_id": context.trace_id,
                    "job_id": job["job_id"],
                }
                snapshot_hash = hashlib.sha256(
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                self.store.execute(
                    """INSERT INTO observability_snapshots(snapshot_id,created_at,status,payload_json,evidence_hash,can_trade,can_create_orders,can_auto_remediate)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (f"snapshot-{uuid4().hex}", datetime.now(timezone.utc).isoformat(), str(health.get("status") or "NOT_EVALUATED"), json.dumps(snapshot, ensure_ascii=False, sort_keys=True), snapshot_hash, 0, 0, 0),
                )
                self.metrics.record("job.run.success", "STATUS", 1, "ratio", labels={"job_type": "observability_evaluation", "status": "succeeded"}, source="job_monitor")
                self.jobs.transition(job["job_id"], JobStatus.SUCCEEDED, evidence={"health_id": health.get("health_id"), "slo_count": len(slos)})
                trace = self.traces.finish_trace(context.trace_id)
                return {"trace": trace, "job": self.jobs.get(job["job_id"]), "slos": slos, "alerts": self.alerts.list(limit=50), "safety": self.safety(), **self.safety()}
        except Exception as exc:
            self.jobs.transition(job["job_id"], JobStatus.FAILED, error_code=type(exc).__name__, error_message=str(exc))
            self.metrics.record("job.run.success", "STATUS", 0, "ratio", labels={"job_type": "observability_evaluation", "status": "failed"}, source="job_monitor", trace_id=context.trace_id)
            self.traces.finish_trace(context.trace_id, status=TelemetryStatus.ERROR, error_code=type(exc).__name__)
            raise

    def _formal_research_due(self) -> bool:
        """Return whether a weekday has passed the configured local research cutoff."""
        current = datetime.now(ZoneInfo("Asia/Shanghai"))
        if current.weekday() >= 5:
            return False
        raw = str(self.config.get("formal_research_deadline_local", "16:00"))
        try:
            hour, minute = (int(part) for part in raw.split(":", 1))
            cutoff = time(hour, minute)
        except (TypeError, ValueError):
            cutoff = time(16, 0)
        return current.time().replace(tzinfo=None) >= cutoff

    @staticmethod
    def _is_stale(timestamp: str | None, threshold_hours: int) -> bool:
        """Treat missing, invalid or older timestamps as stale evidence."""
        if not timestamp:
            return True
        try:
            observed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return datetime.now(timezone.utc) - observed.astimezone(timezone.utc) > timedelta(hours=max(1, threshold_hours))

    @staticmethod
    def _backup_timestamp(backup: dict[str, Any]) -> str | None:
        """Read a known backup audit timestamp without guessing from payload text."""
        for key in ("completed_at", "created_at", "started_at", "verified_at"):
            if backup.get(key):
                return str(backup[key])
        return None

    def dashboard(self, *, hours: int = 24) -> dict[str, Any]:
        """Read one server-composed dashboard; never compute investment values."""
        selected_hours = max(1, min(int(hours), int(self.config.get("max_query_days", 90)) * 24))
        start = (datetime.now(timezone.utc) - timedelta(hours=selected_hours)).isoformat()
        traces = self.traces.list_traces(start=start, limit=50)
        jobs = self.jobs.list(limit=50)
        alerts = self.alerts.list(limit=50)
        incidents = self.incidents.list(limit=30)
        slo = self.slos.latest()
        metric_names = [
            "api.request.duration", "api.request.success", "api.request.server_error",
            "api.request.concurrent", "job.duration", "job.failed",
            "engineering.health.score", "data.health.score", "database.quick_check.success",
            "backup.verification.success",
        ]
        metrics = [self.metrics.metric_summary(name, start=start) for name in metric_names]
        active_alerts = [item for item in alerts if item["status"] in {"OPEN", "ACKNOWLEDGED"}]
        source_snapshot = self.store.one("SELECT * FROM observability_snapshots ORDER BY created_at DESC LIMIT 1")
        if source_snapshot:
            source_snapshot["payload"] = json.loads(source_snapshot.pop("payload_json"))
        status = "BREACHED" if any(item["status"] == "BREACHED" for item in slo) else ("ALERTING" if active_alerts else "OBSERVING")
        return {
            "service_version": OBSERVABILITY_SERVICE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(), "time_range_hours": selected_hours,
            "status": status, "metrics": metrics, "traces": traces, "jobs": jobs,
            "alerts": alerts, "incidents": incidents, "slos": slo, "retention": self.retention.history(),
            "source_health_snapshot": source_snapshot,
            "counts": {"trace_count": len(traces), "job_count": len(jobs), "active_alert_count": len(active_alerts), "incident_count": len(incidents)},
            "safety": self.safety(), **self.safety(),
        }

    def list_metrics(self, **filters) -> dict[str, Any]:
        return {"items": self.metrics.list_samples(**filters), "safety": self.safety(), **self.safety()}

    def list_traces(self, **filters) -> dict[str, Any]:
        return {"items": self.traces.list_traces(**filters), "safety": self.safety(), **self.safety()}

    def list_jobs(self, **filters) -> dict[str, Any]:
        return {"items": self.jobs.list(**filters), "safety": self.safety(), **self.safety()}

    def list_alerts(self, **filters) -> dict[str, Any]:
        return {"items": self.alerts.list(**filters), "safety": self.safety(), **self.safety()}

    def list_incidents(self, **filters) -> dict[str, Any]:
        return {"items": self.incidents.list(**filters), "safety": self.safety(), **self.safety()}

    def list_slos(self) -> dict[str, Any]:
        return {"items": self.slos.latest(), "safety": self.safety(), **self.safety()}
