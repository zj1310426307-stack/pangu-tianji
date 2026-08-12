"""SQLite WAL store for telemetry, governance and retention evidence."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from pangu.core.exceptions import AuditStoreError

from .contracts import OBSERVABILITY_SCHEMA_VERSION


def utc_now() -> str:
    """Return an aware UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class ObservabilityStore:
    """Own only engineering telemetry; never copy investment facts as truth."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        """Create one short-lived WAL connection suitable for concurrent writers."""
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        """Create the versioned v1 schema idempotently."""
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta(
                  component TEXT PRIMARY KEY, schema_version TEXT NOT NULL, applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metric_samples(
                  sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  metric_name TEXT NOT NULL, metric_type TEXT NOT NULL CHECK(metric_type IN ('COUNTER','GAUGE','HISTOGRAM','DURATION','STATUS')),
                  timestamp TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL,
                  labels_json TEXT NOT NULL, labels_hash TEXT NOT NULL, source TEXT NOT NULL,
                  trace_id TEXT, evidence_hash TEXT NOT NULL,
                  can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                  can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                  can_auto_remediate INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_remediate=0)
                );
                CREATE INDEX IF NOT EXISTS idx_metric_name_time ON metric_samples(metric_name,timestamp DESC);
                CREATE TABLE IF NOT EXISTS metric_rollups(
                  rollup_id TEXT PRIMARY KEY, metric_name TEXT NOT NULL, window_start TEXT NOT NULL, window_end TEXT NOT NULL,
                  labels_json TEXT NOT NULL, count INTEGER NOT NULL CHECK(count>=0), minimum REAL, maximum REAL,
                  average REAL, p50 REAL, p95 REAL, p99 REAL, evidence_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS traces(
                  trace_id TEXT PRIMARY KEY, root_operation TEXT NOT NULL, started_at TEXT NOT NULL,
                  completed_at TEXT, duration_ms REAL CHECK(duration_ms IS NULL OR duration_ms>=0),
                  status TEXT NOT NULL CHECK(status IN ('RUNNING','OK','ERROR','TIMEOUT','BLOCKED')),
                  error_code TEXT, linked_ids_json TEXT NOT NULL, evidence_hash TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_traces_time ON traces(started_at DESC);
                CREATE TABLE IF NOT EXISTS spans(
                  span_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL REFERENCES traces(trace_id), parent_span_id TEXT REFERENCES spans(span_id),
                  name TEXT NOT NULL, module TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
                  duration_ms REAL CHECK(duration_ms IS NULL OR duration_ms>=0),
                  status TEXT NOT NULL CHECK(status IN ('RUNNING','OK','ERROR','TIMEOUT','BLOCKED')),
                  attributes_json TEXT NOT NULL, error_code TEXT, error_message TEXT, evidence_hash TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id,started_at);
                CREATE TABLE IF NOT EXISTS job_runs(
                  job_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, job_type TEXT NOT NULL,
                  scheduled_for TEXT, started_at TEXT, heartbeat_at TEXT, completed_at TEXT,
                  status TEXT NOT NULL CHECK(status IN ('CREATED','RUNNING','SUCCEEDED','FAILED','BLOCKED','CANCELLED','STALE')),
                  attempt INTEGER NOT NULL CHECK(attempt>=1), max_attempts INTEGER NOT NULL CHECK(max_attempts>=1),
                  trace_id TEXT, linked_run_id TEXT, error_code TEXT, error_message TEXT,
                  evidence_json TEXT NOT NULL, evidence_hash TEXT NOT NULL,
                  can_auto_retry INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_retry=0),
                  can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                  can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_time ON job_runs(COALESCE(started_at,scheduled_for) DESC);
                CREATE TABLE IF NOT EXISTS slo_definitions(
                  slo_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, window_seconds INTEGER NOT NULL CHECK(window_seconds>0),
                  target REAL NOT NULL, comparison TEXT NOT NULL CHECK(comparison IN ('GTE','LTE')),
                  minimum_samples INTEGER NOT NULL CHECK(minimum_samples>=1), metric_name TEXT,
                  enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), optional_dependency INTEGER NOT NULL CHECK(optional_dependency IN (0,1)),
                  config_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS slo_evaluations(
                  evaluation_id TEXT PRIMARY KEY, slo_id TEXT NOT NULL REFERENCES slo_definitions(slo_id), evaluated_at TEXT NOT NULL,
                  window_start TEXT NOT NULL, window_end TEXT NOT NULL, target REAL NOT NULL, actual REAL,
                  status TEXT NOT NULL CHECK(status IN ('HEALTHY','AT_RISK','BREACHED','NOT_APPLICABLE','INSUFFICIENT_DATA')),
                  error_budget REAL, sample_count INTEGER NOT NULL CHECK(sample_count>=0), evidence_json TEXT NOT NULL, evidence_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_slo_eval_time ON slo_evaluations(slo_id,evaluated_at DESC);
                CREATE TABLE IF NOT EXISTS alerts(
                  alert_id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
                  severity TEXT NOT NULL CHECK(severity IN ('INFO','WARNING','ERROR','CRITICAL')),
                  status TEXT NOT NULL CHECK(status IN ('OPEN','ACKNOWLEDGED','RESOLVED','SUPPRESSED')),
                  opened_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, resolved_at TEXT, trace_id TEXT, job_id TEXT,
                  consecutive_count INTEGER NOT NULL CHECK(consecutive_count>=1), cooldown_until TEXT, suppressed_until TEXT,
                  evidence_json TEXT NOT NULL, evidence_hash TEXT NOT NULL, message TEXT NOT NULL,
                  recommended_manual_action TEXT NOT NULL,
                  can_auto_remediate INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_remediate=0),
                  can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                  can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                );
                CREATE INDEX IF NOT EXISTS idx_alert_status ON alerts(status,last_seen_at DESC);
                CREATE TABLE IF NOT EXISTS alert_trigger_state(
                  fingerprint TEXT PRIMARY KEY, rule_id TEXT NOT NULL, consecutive_count INTEGER NOT NULL CHECK(consecutive_count>=0),
                  last_seen_at TEXT NOT NULL, evidence_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_events(
                  event_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alerts(alert_id), event_type TEXT NOT NULL,
                  occurred_at TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL, evidence_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS incidents(
                  incident_id TEXT PRIMARY KEY, title TEXT NOT NULL, severity TEXT NOT NULL CHECK(severity IN ('INFO','WARNING','ERROR','CRITICAL')),
                  status TEXT NOT NULL CHECK(status IN ('OPEN','INVESTIGATING','MITIGATED','RESOLVED','CLOSED')),
                  opened_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT,
                  root_cause TEXT NOT NULL, impact TEXT NOT NULL, timeline_json TEXT NOT NULL, resolution_note TEXT NOT NULL,
                  version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),
                  can_auto_remediate INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_remediate=0),
                  can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                  can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                );
                CREATE INDEX IF NOT EXISTS idx_incident_status ON incidents(status,updated_at DESC);
                CREATE TABLE IF NOT EXISTS incident_links(
                  incident_id TEXT NOT NULL REFERENCES incidents(incident_id), link_type TEXT NOT NULL CHECK(link_type IN ('alert','trace','job','backup','data_incident')),
                  link_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(incident_id,link_type,link_id)
                );
                CREATE TABLE IF NOT EXISTS observability_snapshots(
                  snapshot_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                  payload_json TEXT NOT NULL, evidence_hash TEXT NOT NULL,
                  can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                  can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                  can_auto_remediate INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_remediate=0)
                );
                CREATE TABLE IF NOT EXISTS retention_runs(
                  retention_id TEXT PRIMARY KEY, plan_hash TEXT NOT NULL, planned_at TEXT NOT NULL, executed_at TEXT,
                  status TEXT NOT NULL CHECK(status IN ('PLANNED','SUCCEEDED','FAILED')),
                  plan_json TEXT NOT NULL, deleted_json TEXT NOT NULL, error_message TEXT,
                  can_auto_remediate INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_remediate=0)
                );
                """
            )
            row = connection.execute("SELECT schema_version FROM schema_meta WHERE component='observability'").fetchone()
            if row and row[0] != OBSERVABILITY_SCHEMA_VERSION:
                raise AuditStoreError(f"不支持的可观测数据库版本：{row[0]}")
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(component,schema_version,applied_at) VALUES('observability',?,?)",
                (OBSERVABILITY_SCHEMA_VERSION, utc_now()),
            )
            connection.execute("PRAGMA user_version=1")
            connection.commit()

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> int:
        """Execute one bounded mutation and return the affected row count."""
        try:
            with closing(self.connect()) as connection:
                cursor = connection.execute(sql, tuple(parameters))
                connection.commit()
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise AuditStoreError(f"可观测数据库写入失败：{exc}") from exc

    def insert(self, sql: str, parameters: Iterable[Any] = ()) -> int:
        """Insert one row and return its row id."""
        try:
            with closing(self.connect()) as connection:
                cursor = connection.execute(sql, tuple(parameters))
                connection.commit()
                return int(cursor.lastrowid)
        except sqlite3.Error as exc:
            raise AuditStoreError(f"可观测数据库插入失败：{exc}") from exc

    def one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        """Read one row as a detached dictionary."""
        with closing(self.connect()) as connection:
            row = connection.execute(sql, tuple(parameters)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        """Read a bounded result set as detached dictionaries."""
        with closing(self.connect()) as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [dict(row) for row in rows]

    def transaction(self) -> sqlite3.Connection:
        """Return an explicit connection for compare-and-set state transitions."""
        return self.connect()
