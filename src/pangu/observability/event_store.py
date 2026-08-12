"""Append-only SQLite audit store for engineering events and checks."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from pangu.core.exceptions import AuditStoreError
from pangu.version.schema_versions import ENGINEERING_DB_SCHEMA_VERSION


def utc_now() -> str:
    """Return a timezone-aware ISO timestamp."""
    return datetime.now(timezone.utc).isoformat()


class EngineeringEventStore:
    """Persist non-trading engineering evidence with database-level safeguards."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS system_events (
                        event_id TEXT PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        level TEXT NOT NULL CHECK(level IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')),
                        module TEXT NOT NULL,
                        event TEXT NOT NULL,
                        run_id TEXT,
                        user_id TEXT,
                        trace_id TEXT NOT NULL,
                        message TEXT NOT NULL,
                        extra_json TEXT NOT NULL,
                        schema_version TEXT NOT NULL,
                        can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                        can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                    );
                    CREATE INDEX IF NOT EXISTS idx_system_events_time
                    ON system_events(timestamp DESC);

                    CREATE TABLE IF NOT EXISTS health_checks (
                        health_id TEXT PRIMARY KEY,
                        checked_at TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('HEALTHY','DEGRADED','UNHEALTHY')),
                        score REAL NOT NULL CHECK(score>=0 AND score<=100),
                        components_json TEXT NOT NULL,
                        evidence_hash TEXT NOT NULL,
                        schema_version TEXT NOT NULL,
                        can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                        can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                    );
                    CREATE INDEX IF NOT EXISTS idx_health_checks_time
                    ON health_checks(checked_at DESC);

                    CREATE TABLE IF NOT EXISTS backups (
                        backup_id TEXT PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        status TEXT NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED')),
                        backup_path TEXT NOT NULL,
                        manifest_hash TEXT,
                        file_count INTEGER NOT NULL DEFAULT 0 CHECK(file_count>=0),
                        byte_count INTEGER NOT NULL DEFAULT 0 CHECK(byte_count>=0),
                        error_message TEXT,
                        schema_version TEXT NOT NULL,
                        can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                        can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                    );
                    CREATE INDEX IF NOT EXISTS idx_backups_time
                    ON backups(started_at DESC);
                    """
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise AuditStoreError(f"无法初始化工程审计数据库：{exc}") from exc

    def append_event(self, payload: dict[str, Any]) -> str:
        """Append one sanitized JSON event."""
        event_id = str(payload.get("event_id") or uuid4())
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """INSERT INTO system_events(
                       event_id,timestamp,level,module,event,run_id,user_id,trace_id,
                       message,extra_json,schema_version,can_trade,can_create_orders
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event_id,
                        str(payload.get("timestamp") or utc_now()),
                        str(payload.get("level") or "INFO").upper(),
                        str(payload.get("module") or "unknown"),
                        str(payload.get("event") or "unknown"),
                        payload.get("run_id"),
                        payload.get("user_id"),
                        str(payload.get("trace_id") or uuid4()),
                        str(payload.get("message") or ""),
                        json.dumps(payload.get("extra") or {}, ensure_ascii=False, sort_keys=True),
                        ENGINEERING_DB_SCHEMA_VERSION,
                        0,
                        0,
                    ),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise AuditStoreError(f"无法保存工程事件：{exc}") from exc
        return event_id

    def save_health(self, payload: dict[str, Any]) -> None:
        """Persist one immutable aggregate health result."""
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """INSERT INTO health_checks(
                       health_id,checked_at,status,score,components_json,evidence_hash,
                       schema_version,can_trade,can_create_orders
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        payload["health_id"], payload["checked_at"], payload["status"],
                        payload["score"], json.dumps(payload["components"], ensure_ascii=False, sort_keys=True),
                        payload["evidence_hash"], ENGINEERING_DB_SCHEMA_VERSION, 0, 0,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError:
            return
        except sqlite3.Error as exc:
            raise AuditStoreError(f"无法保存健康检查：{exc}") from exc

    def start_backup(self, backup_id: str, started_at: str, backup_path: str) -> None:
        """Record the start of a backup before any files are copied."""
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """INSERT INTO backups(
                       backup_id,started_at,status,backup_path,schema_version,
                       can_trade,can_create_orders
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (backup_id, started_at, "RUNNING", backup_path, ENGINEERING_DB_SCHEMA_VERSION, 0, 0),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise AuditStoreError(f"无法登记备份任务：{exc}") from exc

    def finish_backup(
        self,
        backup_id: str,
        *,
        status: str,
        manifest_hash: str | None = None,
        file_count: int = 0,
        byte_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        """Move a registered backup to a terminal state."""
        if status not in {"SUCCEEDED", "FAILED"}:
            raise ValueError("备份终态只能为 SUCCEEDED 或 FAILED")
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """UPDATE backups SET completed_at=?,status=?,manifest_hash=?,
                       file_count=?,byte_count=?,error_message=? WHERE backup_id=? AND status='RUNNING'""",
                    (utc_now(), status, manifest_hash, file_count, byte_count, error_message, backup_id),
                )
                if cursor.rowcount != 1:
                    raise AuditStoreError("备份任务不存在或已经结束")
                connection.commit()
        except sqlite3.Error as exc:
            raise AuditStoreError(f"无法结束备份任务：{exc}") from exc

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Read recent engineering events in reverse time order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM system_events ORDER BY timestamp DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._event_row(row) for row in rows]

    def list_health(self, limit: int = 30) -> list[dict[str, Any]]:
        """Read recent health results."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM health_checks ORDER BY checked_at DESC LIMIT ?",
                (max(1, min(int(limit), 365)),),
            ).fetchall()
        return [
            {
                "health_id": row["health_id"], "checked_at": row["checked_at"],
                "status": row["status"], "score": row["score"],
                "components": json.loads(row["components_json"]),
                "evidence_hash": row["evidence_hash"],
            }
            for row in rows
        ]

    def list_backups(self, limit: int = 30) -> list[dict[str, Any]]:
        """Read recent backup attempts including failed terminal states."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM backups ORDER BY started_at DESC LIMIT ?",
                (max(1, min(int(limit), 365)),),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["extra"] = json.loads(payload.pop("extra_json"))
        payload["can_trade"] = False
        payload["can_create_orders"] = False
        return payload
