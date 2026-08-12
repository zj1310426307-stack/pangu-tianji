from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from pathlib import Path

import pytest

from pangu.observability.context import create_context, current_context, telemetry_context
from pangu.observability.contracts import JobStatus, MetricType, TelemetryStatus
from pangu.observability.job_monitor import JobMonitor
from pangu.observability.metrics import MetricRegistry
from pangu.observability.store import ObservabilityStore
from pangu.observability.tracing import TraceService


def make_store(tmp_path: Path) -> ObservabilityStore:
    """Create one isolated WAL telemetry database."""
    return ObservabilityStore(tmp_path / "observability.db")


def test_context_is_task_local_and_metric_contract_rejects_cardinality(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    registry = MetricRegistry(store)
    context = create_context(request_id="request-1", operation_id="get_status")
    assert current_context() is None
    with telemetry_context(context):
        sample = registry.record("api.request.duration", MetricType.DURATION, 12.5, "ms", labels={"operation_id": "get_status"})
        assert sample["trace_id"] == context.trace_id
        assert current_context() == context
    assert current_context() is None
    with pytest.raises(ValueError, match="白名单"):
        registry.record("api.request.count", MetricType.COUNTER, 1, "count", labels={"symbol": "600519.SH"})


def test_metric_percentiles_and_concurrent_wal_writes(tmp_path: Path) -> None:
    registry = MetricRegistry(make_store(tmp_path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda value: registry.record("api.request.duration", "DURATION", value, "ms", labels={"operation_id": "test"}), range(1, 101)))
    summary = registry.metric_summary("api.request.duration")
    assert summary["count"] == 100
    assert summary["p50"] == pytest.approx(50.5)
    assert summary["p95"] == pytest.approx(95.05)
    assert summary["p99"] == pytest.approx(99.01)


def test_trace_parent_tree_completion_and_secret_redaction(tmp_path: Path) -> None:
    traces = TraceService(make_store(tmp_path))
    context = traces.start_trace("api.get_status", linked_ids={"run_id": "run-1"})
    with telemetry_context(context):
        root_span = traces.start_span("service", "dashboard", attributes={"Authorization": "Bearer secret", "safe": "ok"})
        with telemetry_context(type(context)(**{**context.to_dict(), "span_id": root_span})):
            child = traces.start_span("database", "repository")
        traces.finish_span(child)
        traces.finish_span(root_span)
    result = traces.finish_trace(context.trace_id)
    assert result["status"] == TelemetryStatus.OK.value
    assert result["spans"][0]["attributes"]["Authorization"] == "***REDACTED***"
    assert result["spans"][1]["parent_span_id"] == root_span
    with pytest.raises(ValueError, match="不可覆盖"):
        traces.finish_trace(context.trace_id)


def test_job_state_machine_idempotency_stale_and_terminal_immutability(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    monitor = JobMonitor(store)
    first = monitor.create("engineering_health", "health:2026-08-12:1")
    assert monitor.create("engineering_health", "health:2026-08-12:1")["job_id"] == first["job_id"]
    monitor.transition(first["job_id"], JobStatus.RUNNING)
    monitor.transition(first["job_id"], JobStatus.SUCCEEDED)
    with pytest.raises(ValueError, match="不可从"):
        monitor.transition(first["job_id"], JobStatus.RUNNING)

    stale = monitor.create("research", "research:stale")
    monitor.transition(stale["job_id"], JobStatus.RUNNING)
    store.execute("UPDATE job_runs SET heartbeat_at='2000-01-01T00:00:00+00:00' WHERE job_id=?", (stale["job_id"],))
    assert monitor.mark_stale(1) == [stale["job_id"]]
    assert monitor.get(stale["job_id"])["status"] == "STALE"


def test_database_safety_checks_are_enforced(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO metric_samples(metric_name,metric_type,timestamp,value,unit,labels_json,labels_hash,source,evidence_hash,can_trade,can_create_orders,can_auto_remediate)
                VALUES('x.y.z','GAUGE','2026-08-12',1,'count','{}','h','test','h',1,0,0)"""
            )
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
