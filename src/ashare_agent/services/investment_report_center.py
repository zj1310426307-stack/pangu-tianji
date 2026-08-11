from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence

from ..core.contracts import InvestmentReportType, NotificationLevel


INVESTMENT_REPORT_CENTER_VERSION = "investment-report-center-v1.0.0"
INVESTMENT_REPORT_SCHEMA_VERSION = "investment-report-schema-v1.0.0"
_REPORT_TYPES = {item.value for item in InvestmentReportType}
_JOB_NAMES = _REPORT_TYPES
_SECRET_PATTERN = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|api[_ -]?key\s*[:=])", re.I)


class InvestmentReportCenterError(RuntimeError):
    """Reject invalid report, task, notification, or state-transition writes."""


class InvestmentReportCenter:
    """Persist Daily Investment OS reports, evidence, tasks, and notifications."""

    def __init__(self, db_path: Path) -> None:
        """Initialize an isolated operating database outside trading ledgers."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        """Open a short transaction with relational integrity enabled."""
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize_schema(self) -> None:
        """Create the operating catalog with database-enforced non-trading flags."""
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS investment_reports (
                    report_id TEXT PRIMARY KEY,
                    report_type TEXT NOT NULL CHECK(report_type IN (
                        'morning_report','intraday_monitor','closing_review','weekly_report'
                    )),
                    trade_date TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    portfolio_id TEXT,
                    agent_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL DEFAULT 'investment-report-schema-v1.0.0',
                    evidence_hash TEXT NOT NULL DEFAULT '',
                    content_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('published','degraded')),
                    created_time TEXT NOT NULL,
                    can_trade INTEGER NOT NULL CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL CHECK(can_create_orders=0)
                );
                CREATE INDEX IF NOT EXISTS idx_investment_reports_type_created
                    ON investment_reports(report_type,created_time DESC);
                CREATE TABLE IF NOT EXISTS report_evidence (
                    report_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    observed_at TEXT,
                    PRIMARY KEY(report_id,evidence_id),
                    FOREIGN KEY(report_id) REFERENCES investment_reports(report_id)
                );
                CREATE TABLE IF NOT EXISTS operating_tasks (
                    task_id TEXT PRIMARY KEY,
                    job_name TEXT NOT NULL CHECK(job_name IN (
                        'morning_report','intraday_monitor','closing_review','weekly_report'
                    )),
                    trade_date TEXT NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    trigger TEXT NOT NULL CHECK(trigger IN ('scheduler','user_action')),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','skipped')),
                    report_id TEXT,
                    error_message TEXT,
                    created_time TEXT NOT NULL,
                    completed_time TEXT,
                    can_trade INTEGER NOT NULL CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL CHECK(can_create_orders=0),
                    FOREIGN KEY(report_id) REFERENCES investment_reports(report_id)
                );
                CREATE INDEX IF NOT EXISTS idx_operating_tasks_created
                    ON operating_tasks(created_time DESC);
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    level TEXT NOT NULL CHECK(level IN ('INFO','WARNING','CRITICAL')),
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    channels_json TEXT NOT NULL,
                    delivery_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('unread','read')),
                    created_time TEXT NOT NULL,
                    read_time TEXT,
                    can_trade INTEGER NOT NULL CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL CHECK(can_create_orders=0)
                );
                CREATE INDEX IF NOT EXISTS idx_notifications_status_created
                    ON notifications(status,created_time DESC);
                """
            )
            self._migrate_schema(connection)

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection) -> None:
        """Add v1 audit fields to databases created by an earlier preview build."""
        report_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(investment_reports)")
        }
        if "schema_version" not in report_columns:
            connection.execute(
                "ALTER TABLE investment_reports ADD COLUMN schema_version TEXT NOT NULL DEFAULT 'investment-report-schema-v1.0.0'"
            )
        if "evidence_hash" not in report_columns:
            connection.execute(
                "ALTER TABLE investment_reports ADD COLUMN evidence_hash TEXT NOT NULL DEFAULT ''"
            )
        notification_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(notifications)")
        }
        if "idempotency_key" not in notification_columns:
            connection.execute(
                "ALTER TABLE notifications ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            "UPDATE notifications SET idempotency_key='legacy:' || notification_id WHERE idempotency_key=''"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_idempotency ON notifications(idempotency_key)"
        )

    def start_task(
        self,
        *,
        job_name: str,
        trade_date: str,
        scheduled_for: str,
        trigger: str,
    ) -> tuple[dict[str, Any], bool]:
        """Start one idempotent operating task or return the existing slot audit."""
        if job_name not in _JOB_NAMES:
            raise InvestmentReportCenterError("不支持的运营任务")
        if trigger not in {"scheduler", "user_action"}:
            raise InvestmentReportCenterError("不支持的运营任务触发来源")
        self._validate_date(trade_date)
        scheduled = self._validate_timestamp(scheduled_for)
        key = self._identity("slot", job_name, scheduled.isoformat())
        now = datetime.now(timezone.utc).isoformat()
        task_id = self._identity("operating-task", key)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM operating_tasks WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                item = dict(existing)
                if item["status"] == "running":
                    created = self._validate_timestamp(str(item["created_time"]))
                    if datetime.now(timezone.utc) - created.astimezone(timezone.utc) > timedelta(minutes=30):
                        connection.execute(
                            "UPDATE operating_tasks SET status='failed',error_message=?,completed_time=? WHERE task_id=?",
                            (
                                "上次运营任务超时中断，未自动重试",
                                datetime.now(timezone.utc).isoformat(),
                                item["task_id"],
                            ),
                        )
                        item = dict(connection.execute(
                            "SELECT * FROM operating_tasks WHERE task_id=?", (item["task_id"],)
                        ).fetchone())
                return self._task_payload(item), False
            connection.execute(
                """INSERT INTO operating_tasks(
                    task_id,job_name,trade_date,scheduled_for,trigger,idempotency_key,
                    status,report_id,error_message,created_time,completed_time,
                    can_trade,can_create_orders
                ) VALUES(?,?,?,?,?,?,'running',NULL,NULL,?,NULL,0,0)""",
                (task_id, job_name, trade_date, scheduled_for, trigger, key, now),
            )
        return self.task(task_id), True

    def finish_task(
        self,
        task_id: str,
        *,
        status: str,
        report_id: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Move a running task to one terminal state without retry side effects."""
        if status not in {"succeeded", "failed", "skipped"}:
            raise InvestmentReportCenterError("运营任务终态无效")
        message = (error_message or "").strip()[:1000] or None
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE operating_tasks SET status=?,report_id=?,error_message=?,
                    completed_time=? WHERE task_id=? AND status='running'""",
                (
                    status,
                    report_id,
                    message,
                    datetime.now(timezone.utc).isoformat(),
                    task_id,
                ),
            ).rowcount
        if not updated:
            raise InvestmentReportCenterError("运营任务不存在或已结束")
        return self.task(task_id)

    def save_report(
        self,
        *,
        report_type: str,
        trade_date: str,
        run_id: str,
        portfolio_id: str | None,
        agent_version: str,
        content: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        status: str,
    ) -> dict[str, Any]:
        """Atomically save one report and its complete evidence index."""
        if report_type not in _REPORT_TYPES or status not in {"published", "degraded"}:
            raise InvestmentReportCenterError("投资运营报告合同无效")
        self._validate_date(trade_date)
        if not evidence:
            raise InvestmentReportCenterError("投资运营报告缺少证据链")
        evidence_by_id = {str(item.get("evidence_id") or ""): item for item in evidence}
        if "" in evidence_by_id or len(evidence_by_id) != len(evidence):
            raise InvestmentReportCenterError("投资运营报告证据标识无效或重复")
        ordered_evidence = [evidence_by_id[key] for key in sorted(evidence_by_id)]
        evidence_hash = hashlib.sha256(
            self._json(ordered_evidence).encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        report_id = self._identity(
            "investment-report", report_type, run_id, trade_date, agent_version, now
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO investment_reports(
                    report_id,report_type,trade_date,run_id,portfolio_id,agent_version,
                    schema_version,evidence_hash,content_json,status,created_time,
                    can_trade,can_create_orders
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,0)""",
                (
                    report_id,
                    report_type,
                    trade_date,
                    run_id,
                    portfolio_id,
                    agent_version,
                    INVESTMENT_REPORT_SCHEMA_VERSION,
                    evidence_hash,
                    self._json(content),
                    status,
                    now,
                ),
            )
            for item in ordered_evidence:
                connection.execute(
                    """INSERT INTO report_evidence(
                        report_id,evidence_id,source_type,source_id,observed_at
                    ) VALUES(?,?,?,?,?)""",
                    (
                        report_id,
                        str(item["evidence_id"]),
                        str(item["source_type"]),
                        str(item["source_id"]),
                        item.get("observed_at"),
                    ),
                )
        return self.report(report_id)

    def save_notification(
        self,
        *,
        level: NotificationLevel,
        title: str,
        message: str,
        source_type: str,
        source_id: str,
        channels: Sequence[str],
        delivery: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist one in-app notification and non-secret delivery result."""
        safe_title = title[:160].strip()
        safe_message = message[:2000].strip()
        if _SECRET_PATTERN.search(safe_title) or _SECRET_PATTERN.search(safe_message):
            safe_title = "盘古·天机运营通知"
            safe_message = "通知内容可能包含敏感信息，已隐藏"
        key = self._identity("notification-event", level.value, source_type, source_id, safe_title)
        now = datetime.now(timezone.utc).isoformat()
        notification_id = self._identity("notification", key)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM notifications WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                return self._notification_payload(dict(existing))
            connection.execute(
                """INSERT INTO notifications(
                    notification_id,level,title,message,source_type,source_id,
                    idempotency_key,channels_json,delivery_json,status,created_time,read_time,
                    can_trade,can_create_orders
                ) VALUES(?,?,?,?,?,?,?,?,?,'unread',?,NULL,0,0)""",
                (
                    notification_id,
                    level.value,
                    safe_title,
                    safe_message,
                    source_type,
                    source_id,
                    key,
                    self._json(list(channels)),
                    self._json(dict(delivery)),
                    now,
                ),
            )
        return self.notification(notification_id)

    def mark_notification_read(self, notification_id: str) -> dict[str, Any]:
        """Mark one local notification read without changing its source report."""
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE notifications SET status='read',read_time=?
                WHERE notification_id=?""",
                (datetime.now(timezone.utc).isoformat(), notification_id),
            ).rowcount
        if not updated:
            raise InvestmentReportCenterError("通知不存在")
        return self.notification(notification_id)

    def report(self, report_id: str) -> dict[str, Any]:
        """Read one report with all evidence references and safety flags."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investment_reports WHERE report_id=?", (report_id,)
            ).fetchone()
            if not row:
                raise InvestmentReportCenterError("投资运营报告不存在")
            evidence = connection.execute(
                "SELECT evidence_id,source_type,source_id,observed_at FROM report_evidence WHERE report_id=? ORDER BY evidence_id",
                (report_id,),
            ).fetchall()
        payload = self._report_payload(dict(row))
        payload["evidence"] = [dict(item) for item in evidence]
        return payload

    def list_reports(
        self, *, report_type: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List recent operating reports without generating new content."""
        bounded = max(1, min(int(limit), 200))
        with self._connect() as connection:
            if report_type:
                rows = connection.execute(
                    "SELECT * FROM investment_reports WHERE report_type=? ORDER BY created_time DESC LIMIT ?",
                    (report_type, bounded),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM investment_reports ORDER BY created_time DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
        return [self._report_payload(dict(row)) for row in rows]

    def latest_report(self, report_type: str) -> dict[str, Any] | None:
        """Return the latest report of one type for risk-change comparisons."""
        items = self.list_reports(report_type=report_type, limit=1)
        return items[0] if items else None

    def task(self, task_id: str) -> dict[str, Any]:
        """Read one operating task with non-execution flags forced off."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM operating_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if not row:
            raise InvestmentReportCenterError("运营任务不存在")
        return self._task_payload(dict(row))

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent task audits; reading never dispatches a job."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM operating_tasks ORDER BY created_time DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [self._task_payload(dict(row)) for row in rows]

    def notification(self, notification_id: str) -> dict[str, Any]:
        """Read one notification and its sanitized channel delivery state."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM notifications WHERE notification_id=?", (notification_id,)
            ).fetchone()
        if not row:
            raise InvestmentReportCenterError("通知不存在")
        return self._notification_payload(dict(row))

    def list_notifications(self, limit: int = 100) -> list[dict[str, Any]]:
        """List newest notifications for the in-app notification center."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM notifications ORDER BY created_time DESC LIMIT ?",
                (max(1, min(int(limit), 300)),),
            ).fetchall()
        return [self._notification_payload(dict(row)) for row in rows]

    def counts(self) -> dict[str, int]:
        """Return dashboard counts without exposing database implementation details."""
        with self._connect() as connection:
            report_count = int(connection.execute("SELECT COUNT(*) FROM investment_reports").fetchone()[0])
            task_count = int(connection.execute("SELECT COUNT(*) FROM operating_tasks").fetchone()[0])
            unread_count = int(connection.execute("SELECT COUNT(*) FROM notifications WHERE status='unread'").fetchone()[0])
            critical_count = int(connection.execute("SELECT COUNT(*) FROM notifications WHERE level='CRITICAL' AND status='unread'").fetchone()[0])
        return {
            "report_count": report_count,
            "task_count": task_count,
            "unread_notification_count": unread_count,
            "unread_critical_count": critical_count,
        }

    @staticmethod
    def _report_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Decode content while forcing report execution capabilities off."""
        row["content"] = json.loads(row.pop("content_json"))
        row["can_trade"] = False
        row["can_create_orders"] = False
        return row

    @staticmethod
    def _task_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Expose a task as an audit record rather than an executable handle."""
        row["can_trade"] = False
        row["can_create_orders"] = False
        return row

    @staticmethod
    def _notification_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Decode channel state while preserving non-trading semantics."""
        row["channels"] = json.loads(row.pop("channels_json"))
        row["delivery"] = json.loads(row.pop("delivery_json"))
        row["can_trade"] = False
        row["can_create_orders"] = False
        return row

    @staticmethod
    def _identity(namespace: str, *parts: str) -> str:
        """Create a stable non-secret identifier from operating evidence."""
        digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
        return f"{namespace}-{digest}"

    @staticmethod
    def _json(value: Any) -> str:
        """Serialize deterministic strict JSON for audit replay."""
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _validate_date(value: str) -> date:
        """Require canonical ISO dates for deterministic operating partitions."""
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise InvestmentReportCenterError("交易日必须是YYYY-MM-DD") from exc
        if parsed.isoformat() != value:
            raise InvestmentReportCenterError("交易日必须是YYYY-MM-DD")
        return parsed

    @staticmethod
    def _validate_timestamp(value: str) -> datetime:
        """Require timezone-aware ISO timestamps for scheduler idempotency."""
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise InvestmentReportCenterError("调度时间必须是ISO-8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvestmentReportCenterError("调度时间必须包含时区")
        return parsed
