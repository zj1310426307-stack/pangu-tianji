from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ashare_agent.api.app import create_app

from pangu.observability.alert_engine import AlertEngine
from pangu.observability.contracts import IncidentStatus, SLOStatus
from pangu.observability.incident_service import IncidentService
from pangu.observability.metrics import MetricRegistry
from pangu.observability.retention import RetentionService
from pangu.observability.slo import SLOService
from pangu.observability.store import ObservabilityStore
from pangu.observability.tracing import TraceService
from pangu.observability.job_monitor import JobMonitor


def store(tmp_path: Path) -> ObservabilityStore:
    return ObservabilityStore(tmp_path / "observability.db")


def test_slo_insufficient_not_applicable_healthy_and_breached(tmp_path: Path) -> None:
    database = store(tmp_path)
    metrics = MetricRegistry(database)
    definitions = [
        {"slo_id": "api", "name": "API", "category": "api", "window_seconds": 3600, "target": .99, "comparison": "GTE", "minimum_samples": 2, "metric_name": "api.request.success", "enabled": True, "optional_dependency": False},
        {"slo_id": "ai", "name": "AI", "category": "ai", "window_seconds": 3600, "target": .95, "comparison": "GTE", "minimum_samples": 2, "metric_name": "ai.explicit.success", "enabled": True, "optional_dependency": True},
    ]
    service = SLOService(database, metrics, definitions, "config")
    assert service.evaluate("api")["status"] == SLOStatus.INSUFFICIENT_DATA.value
    assert service.evaluate("ai", ai_enabled=False)["status"] == SLOStatus.NOT_APPLICABLE.value
    metrics.record("api.request.success", "STATUS", 1, "ratio")
    metrics.record("api.request.success", "STATUS", 1, "ratio")
    assert service.evaluate("api")["status"] == SLOStatus.HEALTHY.value
    metrics.record("api.request.success", "STATUS", 0, "ratio")
    metrics.record("api.request.success", "STATUS", 0, "ratio")
    assert service.evaluate("api")["status"] == SLOStatus.BREACHED.value


def test_alert_dedup_ack_suppress_resolve_and_reopen(tmp_path: Path) -> None:
    engine = AlertEngine(store(tmp_path))
    opened = engine.observe("engineering-unhealthy", True, root_key="root", evidence={"score": 20})
    duplicate = engine.observe("engineering-unhealthy", True, root_key="root", evidence={"score": 10})
    assert duplicate["alert_id"] == opened["alert_id"]
    assert duplicate["consecutive_count"] == 2
    acknowledged = engine.acknowledge(opened["alert_id"], actor="user")
    assert acknowledged["status"] == "ACKNOWLEDGED"
    suppressed = engine.suppress(opened["alert_id"], actor="user", until=datetime.now(timezone.utc) + timedelta(hours=1))
    assert suppressed["status"] == "SUPPRESSED"
    resolved = engine.observe("engineering-unhealthy", False, root_key="root", evidence={})
    assert resolved["status"] == "RESOLVED"
    reopened = engine.observe("engineering-unhealthy", True, root_key="root", evidence={"score": 5})
    assert reopened["status"] == "OPEN"


def test_alert_requires_configured_consecutive_triggers(tmp_path: Path) -> None:
    engine = AlertEngine(store(tmp_path), consecutive_triggers=2)
    assert engine.observe("api-error-or-latency", True, root_key="api", evidence={"p95": 2000}) is None
    opened = engine.observe("api-error-or-latency", True, root_key="api", evidence={"p95": 2100})
    assert opened and opened["status"] == "OPEN"


def test_concurrent_alert_fingerprint_and_job_key_remain_singletons(tmp_path: Path) -> None:
    """Concurrent duplicates converge to one alert and one observational job."""
    database = store(tmp_path)
    alerts = AlertEngine(database)
    jobs = JobMonitor(database)
    with ThreadPoolExecutor(max_workers=8) as pool:
        alert_rows = list(
            pool.map(
                lambda _: alerts.observe(
                    "api-error-or-latency", True,
                    root_key="local-api", evidence={"p95": 2000},
                ),
                range(16),
            )
        )
        job_rows = list(
            pool.map(
                lambda _: jobs.create("daily_os.close_review", "daily-os:close:2026-08-12"),
                range(16),
            )
        )
    assert len({row["alert_id"] for row in alert_rows if row}) == 1
    assert len({row["job_id"] for row in job_rows}) == 1
    assert database.one("SELECT COUNT(*) AS value FROM alerts")["value"] == 1
    assert database.one("SELECT COUNT(*) AS value FROM job_runs")["value"] == 1


def test_incident_lifecycle_optimistic_lock_and_closed_immutability(tmp_path: Path) -> None:
    service = IncidentService(store(tmp_path))
    incident = service.create(title="数据库异常", severity="ERROR", impact="工程读取失败")
    investigating = service.transition(incident["incident_id"], target="INVESTIGATING", expected_version=1, actor="user", note="开始调查")
    with pytest.raises(ValueError, match="其他操作"):
        service.transition(incident["incident_id"], target="MITIGATED", expected_version=1, actor="user", note="旧版本")
    resolved = service.transition(incident["incident_id"], target="RESOLVED", expected_version=2, actor="user", note="已修复", resolution_note="人工恢复")
    closed = service.transition(incident["incident_id"], target="CLOSED", expected_version=3, actor="user", note="关闭")
    assert closed["status"] == IncidentStatus.CLOSED.value
    with pytest.raises(ValueError, match="不可从"):
        service.transition(incident["incident_id"], target="INVESTIGATING", expected_version=4, actor="user", note="重开")
    assert resolved["resolved_at"]


def test_retention_requires_matching_plan_and_protects_incident_trace(tmp_path: Path) -> None:
    database = store(tmp_path)
    metrics = MetricRegistry(database)
    traces = TraceService(database)
    old = "2000-01-01T00:00:00+00:00"
    metrics.record("api.request.count", "COUNTER", 1, "count", timestamp=old)
    context = traces.start_trace("old")
    database.execute("UPDATE traces SET started_at=?,status='OK',completed_at=?,duration_ms=1 WHERE trace_id=?", (old, old, context.trace_id))
    incidents = IncidentService(database)
    incidents.create(title="保留Trace", severity="WARNING", impact="审计", links=[{"link_type": "trace", "link_id": context.trace_id}])
    retention = RetentionService(database, {"raw_metric_retention_days": 30, "trace_retention_days": 30})
    plan = retention.plan()
    with pytest.raises(ValueError, match="哈希"):
        retention.run(plan["retention_id"], "bad")
    result = retention.run(plan["retention_id"], plan["plan_hash"])
    assert result["deleted"]["metric_samples"] == 1
    assert result["deleted"]["traces"] == 0
    assert traces.trace(context.trace_id)


def test_governance_database_rejects_automatic_remediation(tmp_path: Path) -> None:
    database = store(tmp_path)
    with sqlite3.connect(database.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO retention_runs(retention_id,plan_hash,planned_at,status,plan_json,deleted_json,can_auto_remediate)
                VALUES('r','h','2026','PLANNED','{}','{}',1)"""
            )


def test_observability_api_middleware_local_writes_and_generated_contract(tmp_path: Path) -> None:
    """Integration/security: requests create traces and mobile-style writes cannot bypass local protection."""
    from test_api import make_project

    root = make_project(tmp_path)
    app = create_app(root)
    operations = {
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert {
        "get_observability_dashboard", "list_observability_metrics", "list_observability_traces",
        "get_observability_trace", "list_observability_jobs", "list_observability_alerts",
        "acknowledge_observability_alert", "list_observability_incidents", "create_observability_incident",
        "update_observability_incident_status", "list_observability_slos", "evaluate_observability",
        "plan_observability_retention", "run_observability_retention",
    } <= operations
    with TestClient(app) as client:
        response = client.get("/api/v1/status")
        assert response.status_code == 200
        assert response.headers["x-pangu-trace-id"].startswith("trace-")
        dashboard = client.get("/api/v1/observability").json()
        assert dashboard["can_trade"] is False and dashboard["can_auto_remediate"] is False
        assert client.post("/api/v1/observability/evaluate").status_code == 403
        evaluated = client.post("/api/v1/observability/evaluate", headers={"X-Ashare-Client": "local-dashboard"})
        assert evaluated.status_code == 200
        assert evaluated.json()["safety"]["can_create_orders"] is False
        plan = client.post("/api/v1/observability/retention/plan", headers={"X-Ashare-Client": "local-dashboard"})
        assert plan.status_code == 200


def test_observability_frontend_uses_generated_operations_only() -> None:
    root = Path(__file__).resolve().parents[1]
    client = (root / "web" / "generated" / "client.js").read_text(encoding="utf-8")
    app = (root / "web" / "app.js").read_text(encoding="utf-8")
    for operation in ("get_observability_dashboard", "evaluate_observability", "plan_observability_retention"):
        assert f'"{operation}"' in client
        assert operation in app
    assert "fetch(" not in app
    assert '"/api/' not in app and "'/api/" not in app
