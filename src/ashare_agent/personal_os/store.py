from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping, Sequence

from .contracts import (
    EVENT_TYPES,
    JOURNAL_TYPES,
    JOURNAL_REVIEW_STATES,
    JOURNAL_SOURCES,
    KNOWLEDGE_CATEGORIES,
    PERSONAL_OS_SCHEMA_VERSION,
    REMINDER_STATES,
    REMINDER_TYPES,
    REPORT_TYPES,
    THESIS_STATES,
    canonical_json,
    content_hash,
    safety_contract,
    stable_id,
    utc_now,
    validate_evidence_ids,
    validate_iso_date,
)


class PersonalOSStoreError(RuntimeError):
    """Signal invalid or conflicting Personal OS persistence operations."""


class PersonalOSStore:
    """Persist personal investment evidence without becoming an execution input."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open one bounded SQLite transaction with foreign keys enabled."""
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        """Create additive Personal OS tables with database-level zero authority."""
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            self._migrate_journal_v2(connection)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS investor_profile(
                    profile_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL CHECK(revision>=1),
                    capital REAL NOT NULL CHECK(capital>0),
                    risk_level TEXT NOT NULL CHECK(risk_level IN ('conservative','balanced','aggressive')),
                    holding_period TEXT NOT NULL CHECK(holding_period IN ('short','medium','long')),
                    investment_style TEXT NOT NULL,
                    max_drawdown REAL NOT NULL CHECK(max_drawdown>=0.03 AND max_drawdown<=0.30),
                    behavior_json TEXT NOT NULL,
                    source_profile_id TEXT,
                    created_time TEXT NOT NULL,
                    updated_time TEXT NOT NULL,
                    can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                    can_modify_portfolio INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_portfolio=0),
                    can_modify_risk INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_risk=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                );
                CREATE TABLE IF NOT EXISTS investment_events(
                    event_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('BUY','SELL','OBSERVE','REVIEW','LEARN')),
                    trade_date TEXT NOT NULL,
                    symbol TEXT,
                    name TEXT,
                    reason TEXT NOT NULL,
                    score REAL,
                    risk_score REAL,
                    result_pnl REAL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0)
                );
                CREATE INDEX IF NOT EXISTS idx_investment_events_date
                    ON investment_events(trade_date DESC,created_time DESC);
                CREATE INDEX IF NOT EXISTS idx_investment_events_symbol
                    ON investment_events(symbol,trade_date DESC);
                CREATE TABLE IF NOT EXISTS investment_journal(
                    journal_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    entry_type TEXT NOT NULL CHECK(entry_type IN ('OBSERVE','DECISION','REVIEW','LESSON')),
                    event_id TEXT,
                    trade_date TEXT NOT NULL,
                    symbol TEXT,
                    title TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    ai_summary TEXT,
                    expected_horizon TEXT,
                    expected_condition TEXT,
                    invalid_condition TEXT,
                    risk_notes TEXT,
                    related_run_id TEXT,
                    related_portfolio_snapshot_id TEXT,
                    related_trade_id TEXT,
                    review_due_at TEXT,
                    review_status TEXT NOT NULL CHECK(review_status IN ('NOT_DUE','DUE','IN_REVIEW','DONE')),
                    source TEXT NOT NULL CHECK(source IN ('USER','AI_SAVED','MIGRATED')),
                    outcome TEXT,
                    lesson TEXT,
                    evidence_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','archived')),
                    version INTEGER NOT NULL CHECK(version>=1),
                    created_time TEXT NOT NULL,
                    updated_time TEXT,
                    archived_time TEXT,
                    can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    FOREIGN KEY(event_id) REFERENCES investment_events(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_investment_journal_date
                    ON investment_journal(trade_date DESC,created_time DESC);
                CREATE INDEX IF NOT EXISTS idx_investment_journal_review
                    ON investment_journal(review_status,review_due_at,trade_date DESC);
                CREATE TABLE IF NOT EXISTS investment_reviews(
                    review_id TEXT PRIMARY KEY,
                    journal_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    facts_changed_json TEXT NOT NULL,
                    thesis_status TEXT NOT NULL CHECK(thesis_status IN ('STILL_VALID','WEAKENED','INVALIDATED','INSUFFICIENT_EVIDENCE')),
                    risk_status TEXT NOT NULL,
                    result_summary TEXT NOT NULL,
                    mistakes_json TEXT NOT NULL,
                    good_decisions_json TEXT NOT NULL,
                    lesson_candidate TEXT,
                    evidence_refs_json TEXT NOT NULL,
                    draft_json TEXT NOT NULL,
                    user_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(user_confirmed IN (0,1)),
                    created_time TEXT NOT NULL,
                    confirmed_time TEXT,
                    can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0),
                    can_modify_portfolio INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_portfolio=0),
                    can_modify_risk INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_risk=0),
                    can_auto_remediate INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_remediate=0),
                    FOREIGN KEY(journal_id) REFERENCES investment_journal(journal_id)
                );
                CREATE INDEX IF NOT EXISTS idx_investment_reviews_journal
                    ON investment_reviews(journal_id,created_time DESC);
                CREATE TABLE IF NOT EXISTS investment_reminders(
                    reminder_id TEXT PRIMARY KEY,
                    dedup_key TEXT NOT NULL UNIQUE,
                    reminder_type TEXT NOT NULL CHECK(reminder_type IN ('REVIEW_DUE','RISK_CHANGED','RESEARCH_CHANGED','DAILY_REVIEW')),
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('OPEN','READ','DISMISSED','DONE')),
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    updated_time TEXT,
                    can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0),
                    can_modify_portfolio INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_portfolio=0),
                    can_modify_risk INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_risk=0),
                    can_auto_remediate INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_remediate=0)
                );
                CREATE INDEX IF NOT EXISTS idx_investment_reminders_status
                    ON investment_reminders(status,trade_date DESC,created_time DESC);
                CREATE TABLE IF NOT EXISTS investment_review_snapshots(
                    snapshot_id TEXT PRIMARY KEY,
                    snapshot_type TEXT NOT NULL CHECK(snapshot_type IN ('RISK','RESEARCH')),
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    source_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                );
                CREATE INDEX IF NOT EXISTS idx_investment_review_snapshots
                    ON investment_review_snapshots(snapshot_type,symbol,observed_at DESC);
                CREATE TABLE IF NOT EXISTS personal_os_schema_migrations(
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_base(
                    knowledge_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    category TEXT NOT NULL CHECK(category IN ('company','market','personal_lesson')),
                    subject TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
                    status TEXT NOT NULL CHECK(status IN ('active','archived')),
                    version INTEGER NOT NULL CHECK(version>=1),
                    created_time TEXT NOT NULL,
                    updated_time TEXT,
                    archived_time TEXT,
                    can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_base_category
                    ON knowledge_base(category,updated_time DESC,created_time DESC);
                CREATE TABLE IF NOT EXISTS committee_reports(
                    report_id TEXT PRIMARY KEY,
                    report_type TEXT NOT NULL CHECK(report_type IN ('weekly_committee','monthly_review','personal_coach')),
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    report_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('published','degraded')),
                    created_time TEXT NOT NULL,
                    used_for_execution INTEGER NOT NULL DEFAULT 0 CHECK(used_for_execution=0),
                    can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0),
                    can_modify_risk INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_risk=0),
                    can_approve_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_approve_strategy=0)
                );
                CREATE INDEX IF NOT EXISTS idx_committee_reports_created
                    ON committee_reports(created_time DESC);
                CREATE TABLE IF NOT EXISTS personal_score(
                    score_id TEXT PRIMARY KEY,
                    as_of_date TEXT NOT NULL UNIQUE,
                    total_score REAL,
                    coverage REAL NOT NULL CHECK(coverage>=0 AND coverage<=1),
                    dimensions_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    score_hash TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO personal_os_schema_migrations(version,applied_at) VALUES(?,?)",
                (PERSONAL_OS_SCHEMA_VERSION, utc_now()),
            )

    @staticmethod
    def _migrate_journal_v2(connection: sqlite3.Connection) -> None:
        """Rebuild the legacy Journal table once while retaining every old record."""
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='investment_journal'"
        ).fetchone()
        if not exists:
            return
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(investment_journal)")
        }
        if "reason" in columns and "review_status" in columns:
            return
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.executescript(
            """
            ALTER TABLE investment_journal RENAME TO investment_journal_legacy_v1;
            CREATE TABLE investment_journal(
                journal_id TEXT PRIMARY KEY,idempotency_key TEXT NOT NULL UNIQUE,
                request_hash TEXT NOT NULL,entry_type TEXT NOT NULL CHECK(entry_type IN ('OBSERVE','DECISION','REVIEW','LESSON')),
                event_id TEXT,trade_date TEXT NOT NULL,symbol TEXT,title TEXT NOT NULL,
                reason TEXT NOT NULL,user_text TEXT NOT NULL,ai_summary TEXT,expected_horizon TEXT,
                expected_condition TEXT,invalid_condition TEXT,risk_notes TEXT,related_run_id TEXT,
                related_portfolio_snapshot_id TEXT,related_trade_id TEXT,review_due_at TEXT,
                review_status TEXT NOT NULL CHECK(review_status IN ('NOT_DUE','DUE','IN_REVIEW','DONE')),
                source TEXT NOT NULL CHECK(source IN ('USER','AI_SAVED','MIGRATED')),
                outcome TEXT,lesson TEXT,evidence_ids_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active','archived')),
                version INTEGER NOT NULL CHECK(version>=1),created_time TEXT NOT NULL,
                updated_time TEXT,archived_time TEXT,
                can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                FOREIGN KEY(event_id) REFERENCES investment_events(event_id)
            );
            INSERT INTO investment_journal(
                journal_id,idempotency_key,request_hash,entry_type,event_id,trade_date,symbol,
                title,reason,user_text,ai_summary,expected_horizon,expected_condition,
                invalid_condition,risk_notes,related_run_id,related_portfolio_snapshot_id,
                related_trade_id,review_due_at,review_status,source,outcome,lesson,
                evidence_ids_json,status,version,created_time,updated_time,archived_time,
                can_affect_execution,can_trade,can_create_orders
            ) SELECT journal_id,idempotency_key,request_hash,
                CASE entry_type WHEN 'observation' THEN 'OBSERVE' WHEN 'review' THEN 'REVIEW'
                    WHEN 'lesson' THEN 'LESSON' ELSE 'DECISION' END,
                event_id,trade_date,symbol,title,content,content,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
                NULL,NULL,CASE WHEN entry_type IN ('review','lesson') THEN 'DONE' ELSE 'NOT_DUE' END,
                'MIGRATED',outcome,lesson,evidence_ids_json,status,version,created_time,updated_time,
                archived_time,can_affect_execution,can_trade,can_create_orders
            FROM investment_journal_legacy_v1;
            DROP TABLE investment_journal_legacy_v1;
            """
        )
        connection.execute("PRAGMA foreign_keys=ON")

    def profile(self) -> dict[str, Any] | None:
        """Read the current digital twin without touching production constraints."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investor_profile WHERE profile_id='investor-digital-twin'"
            ).fetchone()
        return self._profile_payload(dict(row)) if row else None

    def save_profile(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Upsert the user-owned digital twin under optimistic revision control."""
        behavior = [str(item).strip()[:120] for item in payload.get("behavior") or []]
        behavior = [item for item in behavior if item][:20]
        now = utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision,created_time FROM investor_profile WHERE profile_id='investor-digital-twin'"
            ).fetchone()
            revision = int(row["revision"]) + 1 if row else 1
            created = str(row["created_time"]) if row else now
            connection.execute(
                """INSERT OR REPLACE INTO investor_profile(
                    profile_id,revision,capital,risk_level,holding_period,investment_style,
                    max_drawdown,behavior_json,source_profile_id,created_time,updated_time,
                    can_affect_execution,can_modify_portfolio,can_modify_risk,can_trade,can_create_orders
                ) VALUES('investor-digital-twin',?,?,?,?,?,?,?,?,?,?,0,0,0,0,0)""",
                (
                    revision,
                    float(payload["capital"]),
                    str(payload["risk_level"]),
                    str(payload["holding_period"]),
                    str(payload["investment_style"]),
                    float(payload["max_drawdown"]),
                    canonical_json(behavior),
                    payload.get("source_profile_id"),
                    created,
                    now,
                ),
            )
        return self.profile() or {}

    def save_event(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Persist one idempotent investment event or replay the existing record."""
        event_type = str(payload["event_type"]).upper()
        if event_type not in EVENT_TYPES:
            raise PersonalOSStoreError("投资事件类型无效")
        trade_date = validate_iso_date(str(payload["trade_date"]))
        key = str(payload["idempotency_key"])
        evidence_ids = validate_evidence_ids(payload.get("evidence_ids"))
        request = {
            "event_type": event_type,
            "trade_date": trade_date,
            "symbol": payload.get("symbol"),
            "name": payload.get("name"),
            "reason": str(payload["reason"]),
            "score": payload.get("score"),
            "risk_score": payload.get("risk_score"),
            "result_pnl": payload.get("result_pnl"),
            "source_type": str(payload["source_type"]),
            "source_id": str(payload["source_id"]),
            "evidence_ids": evidence_ids,
            "source_hash": str(payload["source_hash"]),
            "payload": dict(payload.get("payload") or {}),
        }
        request_hash = content_hash(request)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM investment_events WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if str(existing["request_hash"]) != request_hash:
                    raise PersonalOSStoreError("事件幂等键已用于不同内容")
                return self._event_payload(dict(existing)), False
            event_id = stable_id("investment-event", key)
            connection.execute(
                """INSERT INTO investment_events(
                    event_id,idempotency_key,request_hash,event_type,trade_date,symbol,name,
                    reason,score,risk_score,result_pnl,source_type,source_id,evidence_ids_json,
                    source_hash,payload_json,created_time,can_affect_execution,can_trade,
                    can_create_orders,can_modify_strategy
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,0)""",
                (
                    event_id,key,request_hash,event_type,trade_date,payload.get("symbol"),
                    payload.get("name"),str(payload["reason"]),payload.get("score"),
                    payload.get("risk_score"),payload.get("result_pnl"),
                    str(payload["source_type"]),str(payload["source_id"]),
                    canonical_json(evidence_ids),str(payload["source_hash"]),
                    canonical_json(dict(payload.get("payload") or {})),utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM investment_events WHERE event_id=?", (event_id,)
            ).fetchone()
        return self._event_payload(dict(row)), True

    def events(self, limit: int = 200) -> list[dict[str, Any]]:
        """List newest investment events without deriving new events on read."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM investment_events ORDER BY trade_date DESC,created_time DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._event_payload(dict(row)) for row in rows]

    def create_journal(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Create one idempotent journal entry that remains outside execution."""
        entry_type = str(payload["entry_type"]).upper()
        if entry_type not in JOURNAL_TYPES:
            raise PersonalOSStoreError("投资日志类型无效")
        source = str(payload.get("source") or "USER").upper()
        if source not in JOURNAL_SOURCES:
            raise PersonalOSStoreError("投资日志来源无效")
        review_status = str(payload.get("review_status") or "NOT_DUE").upper()
        if review_status not in JOURNAL_REVIEW_STATES:
            raise PersonalOSStoreError("投资日志复盘状态无效")
        key = str(payload["idempotency_key"])
        evidence_ids = validate_evidence_ids(payload.get("evidence_ids"))
        reason = str(payload.get("reason") or payload.get("content") or "").strip()
        user_text = str(payload.get("user_text") or reason).strip()
        if not reason or not user_text:
            raise PersonalOSStoreError("投资日志必须保留用户原话")
        ai_summary = str(payload.get("ai_summary") or "").strip() or None
        if source == "AI_SAVED" and not ai_summary:
            raise PersonalOSStoreError("保存AI回答时必须单独保留AI摘要")
        request = {
            "entry_type": entry_type,
            "event_id": payload.get("event_id"),
            "trade_date": validate_iso_date(str(payload["trade_date"])),
            "symbol": payload.get("symbol"),
            "title": str(payload["title"]).strip(),
            "reason": reason,
            "user_text": user_text,
            "ai_summary": ai_summary,
            "expected_horizon": payload.get("expected_horizon"),
            "expected_condition": payload.get("expected_condition"),
            "invalid_condition": payload.get("invalid_condition"),
            "risk_notes": payload.get("risk_notes"),
            "related_run_id": payload.get("related_run_id"),
            "related_portfolio_snapshot_id": payload.get("related_portfolio_snapshot_id"),
            "related_trade_id": payload.get("related_trade_id"),
            "review_due_at": payload.get("review_due_at"),
            "review_status": review_status,
            "source": source,
            "outcome": str(payload.get("outcome") or "").strip() or None,
            "lesson": str(payload.get("lesson") or "").strip() or None,
            "evidence_ids": evidence_ids,
        }
        request_hash = content_hash(request)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM investment_journal WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if str(existing["request_hash"]) != request_hash:
                    raise PersonalOSStoreError("日志幂等键已用于不同内容")
                return self._journal_payload(dict(existing))
            journal_id = stable_id("investment-journal", key)
            connection.execute(
                """INSERT INTO investment_journal(
                    journal_id,idempotency_key,request_hash,entry_type,event_id,trade_date,
                    symbol,title,reason,user_text,ai_summary,expected_horizon,expected_condition,
                    invalid_condition,risk_notes,related_run_id,related_portfolio_snapshot_id,
                    related_trade_id,review_due_at,review_status,source,outcome,lesson,
                    evidence_ids_json,status,version,
                    created_time,updated_time,archived_time,can_affect_execution,can_trade,
                    can_create_orders
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',1,?,NULL,NULL,0,0,0)""",
                (
                    journal_id,key,request_hash,entry_type,payload.get("event_id"),
                    request["trade_date"],payload.get("symbol"),request["title"],
                    reason,user_text,ai_summary,request["expected_horizon"],
                    request["expected_condition"],request["invalid_condition"],
                    request["risk_notes"],request["related_run_id"],
                    request["related_portfolio_snapshot_id"],request["related_trade_id"],
                    request["review_due_at"],review_status,source,request["outcome"],
                    request["lesson"],canonical_json(evidence_ids),utc_now(),
                ),
            )
        return self.journal(journal_id)

    def journal(self, journal_id: str) -> dict[str, Any]:
        """Read one journal record for review or optimistic updates."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investment_journal WHERE journal_id=?", (journal_id,)
            ).fetchone()
        if not row:
            raise KeyError(journal_id)
        return self._journal_payload(dict(row))

    def journals(self, limit: int = 200) -> list[dict[str, Any]]:
        """List journal entries with archived records retained for audit."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM investment_journal ORDER BY trade_date DESC,created_time DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._journal_payload(dict(row)) for row in rows]

    def update_journal(self, journal_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Update reflection fields with optimistic versioning and no execution effect."""
        expected = int(payload["expected_version"])
        current = self.journal(journal_id)
        if current["status"] != "active" or current["version"] != expected:
            raise PersonalOSStoreError("日志版本冲突或已归档")
        title = str(payload.get("title", current["title"])).strip()
        reason = str(payload.get("reason", payload.get("content", current["reason"]))).strip()
        user_text = str(payload.get("user_text", current["user_text"])).strip()
        outcome = payload.get("outcome", current.get("outcome"))
        lesson = payload.get("lesson", current.get("lesson"))
        review_status = str(payload.get("review_status", current["review_status"])).upper()
        if review_status not in JOURNAL_REVIEW_STATES:
            raise PersonalOSStoreError("投资日志复盘状态无效")
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE investment_journal SET title=?,reason=?,user_text=?,outcome=?,lesson=?,
                    review_due_at=?,review_status=?,expected_horizon=?,expected_condition=?,
                    invalid_condition=?,risk_notes=?,version=version+1,updated_time=?
                    WHERE journal_id=? AND version=? AND status='active'""",
                (
                    title,reason,user_text,outcome,lesson,
                    payload.get("review_due_at",current.get("review_due_at")),review_status,
                    payload.get("expected_horizon",current.get("expected_horizon")),
                    payload.get("expected_condition",current.get("expected_condition")),
                    payload.get("invalid_condition",current.get("invalid_condition")),
                    payload.get("risk_notes",current.get("risk_notes")),utc_now(),journal_id,expected,
                ),
            ).rowcount
        if not updated:
            raise PersonalOSStoreError("日志并发更新冲突")
        return self.journal(journal_id)

    def save_review(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Persist one user-confirmed review; drafts never enter this table."""
        if not bool(payload.get("user_confirmed")):
            raise PersonalOSStoreError("复盘必须由用户确认后才能保存")
        thesis = str(payload["thesis_status"]).upper()
        if thesis not in THESIS_STATES:
            raise PersonalOSStoreError("原投资逻辑状态无效")
        journal_id = str(payload["journal_id"])
        journal = self.journal(journal_id)
        if journal["status"] != "active":
            raise PersonalOSStoreError("已归档日志不能复盘")
        key = str(payload["idempotency_key"])
        refs = list(payload.get("evidence_refs") or [])
        request = {
            "journal_id": journal_id,
            "reviewed_at": str(payload["reviewed_at"]),
            "facts_changed": list(payload.get("facts_changed") or []),
            "thesis_status": thesis,
            "risk_status": str(payload.get("risk_status") or "INSUFFICIENT_EVIDENCE"),
            "result_summary": str(payload["result_summary"]).strip(),
            "mistakes": list(payload.get("mistakes") or []),
            "good_decisions": list(payload.get("good_decisions") or []),
            "lesson_candidate": str(payload.get("lesson_candidate") or "").strip() or None,
            "evidence_refs": refs,
            "draft": dict(payload.get("draft") or {}),
            "user_confirmed": True,
        }
        request_hash = content_hash(request)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM investment_reviews WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if str(existing["request_hash"]) != request_hash:
                    raise PersonalOSStoreError("复盘幂等键已用于不同内容")
                return self._review_payload(dict(existing)), False
            review_id = stable_id("investment-review", key)
            now = utc_now()
            connection.execute(
                """INSERT INTO investment_reviews(
                    review_id,journal_id,idempotency_key,request_hash,reviewed_at,
                    facts_changed_json,thesis_status,risk_status,result_summary,mistakes_json,
                    good_decisions_json,lesson_candidate,evidence_refs_json,draft_json,
                    user_confirmed,created_time,confirmed_time,can_affect_execution,can_trade,
                    can_create_orders,can_modify_strategy,can_modify_portfolio,can_modify_risk,
                    can_auto_remediate
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,0,0,0,0,0,0,0)""",
                (
                    review_id,journal_id,key,request_hash,request["reviewed_at"],
                    canonical_json(request["facts_changed"]),thesis,request["risk_status"],
                    request["result_summary"],canonical_json(request["mistakes"]),
                    canonical_json(request["good_decisions"]),request["lesson_candidate"],
                    canonical_json(refs),canonical_json(request["draft"]),now,now,
                ),
            )
            connection.execute(
                "UPDATE investment_journal SET review_status='DONE',version=version+1,updated_time=? WHERE journal_id=?",
                (now,journal_id),
            )
        return self.review(review_id), True

    def review(self, review_id: str) -> dict[str, Any]:
        """Read one confirmed review with its exact evidence references."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investment_reviews WHERE review_id=?", (review_id,)
            ).fetchone()
        if not row:
            raise KeyError(review_id)
        return self._review_payload(dict(row))

    def reviews(self, limit: int = 200) -> list[dict[str, Any]]:
        """List user-confirmed reviews newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM investment_reviews ORDER BY reviewed_at DESC,created_time DESC LIMIT ?",
                (max(1,min(int(limit),500)),),
            ).fetchall()
        return [self._review_payload(dict(row)) for row in rows]

    def create_reminder(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create one same-day in-app reminder or replay its deduplicated record."""
        reminder_type = str(payload["reminder_type"]).upper()
        if reminder_type not in REMINDER_TYPES:
            raise PersonalOSStoreError("提醒类型无效")
        trade_date = validate_iso_date(str(payload["trade_date"]))
        symbol = str(payload.get("symbol") or "").upper()
        dedup_key = f"{symbol}|{reminder_type}|{trade_date}"
        refs = list(payload.get("evidence_refs") or [])
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM investment_reminders WHERE dedup_key=?", (dedup_key,)
            ).fetchone()
            if existing:
                return self._reminder_payload(dict(existing)), False
            reminder_id = stable_id("investment-reminder", dedup_key)
            connection.execute(
                """INSERT INTO investment_reminders(
                    reminder_id,dedup_key,reminder_type,trade_date,symbol,title,message,status,
                    source_type,source_id,evidence_refs_json,created_time,updated_time,
                    can_affect_execution,can_trade,can_create_orders,can_modify_strategy,
                    can_modify_portfolio,can_modify_risk,can_auto_remediate
                ) VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?,?,NULL,0,0,0,0,0,0,0)""",
                (
                    reminder_id,dedup_key,reminder_type,trade_date,symbol,
                    str(payload["title"]),str(payload["message"]),
                    str(payload["source_type"]),str(payload["source_id"]),
                    canonical_json(refs),utc_now(),
                ),
            )
        return self.reminder(reminder_id), True

    def reminder(self, reminder_id: str) -> dict[str, Any]:
        """Read one reminder without changing its acknowledgement state."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investment_reminders WHERE reminder_id=?", (reminder_id,)
            ).fetchone()
        if not row:
            raise KeyError(reminder_id)
        return self._reminder_payload(dict(row))

    def reminders(self, limit: int = 200) -> list[dict[str, Any]]:
        """List in-app reminders with active items first."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM investment_reminders
                   ORDER BY CASE status WHEN 'OPEN' THEN 0 WHEN 'READ' THEN 1 ELSE 2 END,
                            trade_date DESC,created_time DESC LIMIT ?""",
                (max(1,min(int(limit),500)),),
            ).fetchall()
        return [self._reminder_payload(dict(row)) for row in rows]

    def update_reminder(self, reminder_id: str, status: str) -> dict[str, Any]:
        """Apply one explicit reminder acknowledgement transition."""
        selected = str(status).upper()
        if selected not in REMINDER_STATES:
            raise PersonalOSStoreError("提醒状态无效")
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE investment_reminders SET status=?,updated_time=? WHERE reminder_id=?",
                (selected,utc_now(),reminder_id),
            ).rowcount
        if not updated:
            raise KeyError(reminder_id)
        return self.reminder(reminder_id)

    def mark_review_reminders_done(self, journal_id: str) -> None:
        """Close due reminders after the linked journal review is confirmed."""
        with self._connect() as connection:
            connection.execute(
                """UPDATE investment_reminders SET status='DONE',updated_time=?
                   WHERE reminder_type='REVIEW_DUE' AND source_id=? AND status IN ('OPEN','READ')""",
                (utc_now(),journal_id),
            )

    def mark_journal_due(self, journal_id: str) -> None:
        """Mark one scheduled journal due without changing its user-authored content."""
        with self._connect() as connection:
            connection.execute(
                """UPDATE investment_journal SET review_status='DUE',updated_time=?
                   WHERE journal_id=? AND status='active' AND review_status='NOT_DUE'""",
                (utc_now(),journal_id),
            )

    def save_snapshot(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Persist one authority-owned risk or research snapshot for later comparison."""
        snapshot_type = str(payload["snapshot_type"]).upper()
        if snapshot_type not in {"RISK","RESEARCH"}:
            raise PersonalOSStoreError("复盘快照类型无效")
        snapshot_id = stable_id(
            "review-snapshot",snapshot_type,str(payload.get("symbol") or ""),
            str(payload["source_id"]),str(payload["payload_hash"]),
        )
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM investment_review_snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
            if existing:
                return self._snapshot_payload(dict(existing)), False
            connection.execute(
                """INSERT INTO investment_review_snapshots(
                    snapshot_id,snapshot_type,trade_date,symbol,source_id,payload_hash,
                    payload_json,observed_at,created_time,can_trade,can_create_orders
                ) VALUES(?,?,?,?,?,?,?,?,?,0,0)""",
                (
                    snapshot_id,snapshot_type,validate_iso_date(str(payload["trade_date"])),
                    str(payload.get("symbol") or ""),str(payload["source_id"]),
                    str(payload["payload_hash"]),canonical_json(dict(payload["payload"])),
                    str(payload["observed_at"]),utc_now(),
                ),
            )
        return self.snapshot(snapshot_id), True

    def snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """Read one immutable comparison snapshot."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investment_review_snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
        if not row:
            raise KeyError(snapshot_id)
        return self._snapshot_payload(dict(row))

    def latest_snapshot(self, snapshot_type: str, symbol: str = "") -> dict[str, Any] | None:
        """Read the most recent prior comparison snapshot for one authority and symbol."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM investment_review_snapshots
                   WHERE snapshot_type=? AND symbol=? ORDER BY observed_at DESC LIMIT 1""",
                (str(snapshot_type).upper(),str(symbol).upper()),
            ).fetchone()
        return self._snapshot_payload(dict(row)) if row else None

    def archive_journal(self, journal_id: str, expected_version: int) -> dict[str, Any]:
        """Soft-archive a journal while preserving its historical content."""
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE investment_journal SET status='archived',version=version+1,
                    archived_time=?,updated_time=? WHERE journal_id=? AND version=? AND status='active'""",
                (utc_now(),utc_now(),journal_id,int(expected_version)),
            ).rowcount
        if not updated:
            raise PersonalOSStoreError("日志版本冲突或已归档")
        return self.journal(journal_id)

    def create_knowledge(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Create one bounded knowledge note with optional evidence references."""
        category = str(payload["category"])
        if category not in KNOWLEDGE_CATEGORIES:
            raise PersonalOSStoreError("知识库分类无效")
        key = str(payload["idempotency_key"])
        evidence_ids = validate_evidence_ids(payload.get("evidence_ids"))
        request = {
            "category": category,
            "subject": str(payload["subject"]).strip(),
            "title": str(payload["title"]).strip(),
            "content": str(payload["content"]).strip(),
            "evidence_ids": evidence_ids,
            "confidence": float(payload.get("confidence", 1.0)),
        }
        request_hash = content_hash(request)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM knowledge_base WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if str(existing["request_hash"]) != request_hash:
                    raise PersonalOSStoreError("知识幂等键已用于不同内容")
                return self._knowledge_payload(dict(existing))
            knowledge_id = stable_id("knowledge", key)
            connection.execute(
                """INSERT INTO knowledge_base(
                    knowledge_id,idempotency_key,request_hash,category,subject,title,content,
                    evidence_ids_json,confidence,status,version,created_time,updated_time,
                    archived_time,can_affect_execution,can_trade,can_create_orders
                ) VALUES(?,?,?,?,?,?,?,?,?,'active',1,?,NULL,NULL,0,0,0)""",
                (
                    knowledge_id,key,request_hash,category,request["subject"],request["title"],
                    request["content"],canonical_json(evidence_ids),request["confidence"],utc_now(),
                ),
            )
        return self.knowledge(knowledge_id)

    def knowledge(self, knowledge_id: str) -> dict[str, Any]:
        """Read one knowledge item including its audit state."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_base WHERE knowledge_id=?", (knowledge_id,)
            ).fetchone()
        if not row:
            raise KeyError(knowledge_id)
        return self._knowledge_payload(dict(row))

    def knowledge_items(self, limit: int = 200) -> list[dict[str, Any]]:
        """List personal knowledge without treating it as production research."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_base ORDER BY COALESCE(updated_time,created_time) DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._knowledge_payload(dict(row)) for row in rows]

    def archive_knowledge(self, knowledge_id: str, expected_version: int) -> dict[str, Any]:
        """Soft-archive personal knowledge while retaining the evidence trail."""
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE knowledge_base SET status='archived',version=version+1,
                    archived_time=?,updated_time=? WHERE knowledge_id=? AND version=? AND status='active'""",
                (utc_now(),utc_now(),knowledge_id,int(expected_version)),
            ).rowcount
        if not updated:
            raise PersonalOSStoreError("知识版本冲突或已归档")
        return self.knowledge(knowledge_id)

    def save_report(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Persist one immutable evidence-linked coach, committee or monthly report."""
        report_type = str(payload["report_type"])
        if report_type not in REPORT_TYPES:
            raise PersonalOSStoreError("Personal OS报告类型无效")
        start = validate_iso_date(str(payload["period_start"]))
        end = validate_iso_date(str(payload["period_end"]))
        evidence = list(payload.get("evidence") or [])
        if not evidence:
            raise PersonalOSStoreError("Personal OS报告缺少证据")
        evidence_ids = validate_evidence_ids(
            [str(item.get("evidence_id") or "") for item in evidence]
        )
        if len(evidence_ids) != len(evidence):
            raise PersonalOSStoreError("Personal OS报告证据重复")
        evidence_hash = content_hash(evidence)
        content = dict(payload["content"])
        report_content = {
            "schema_version": PERSONAL_OS_SCHEMA_VERSION,
            "report_type": report_type,
            "period_start": start,
            "period_end": end,
            "evidence_hash": evidence_hash,
            "evidence_ids": evidence_ids,
            "content": content,
            "status": str(payload["status"]),
            **safety_contract(),
        }
        report_hash = content_hash(report_content)
        report_id = stable_id("personal-report", report_type, start, end, evidence_hash)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM committee_reports WHERE report_id=?", (report_id,)
            ).fetchone()
            if existing:
                return self._report_payload(dict(existing)), False
            connection.execute(
                """INSERT INTO committee_reports(
                    report_id,report_type,period_start,period_end,evidence_hash,content_json,
                    report_hash,status,created_time,used_for_execution,can_affect_execution,
                    can_trade,can_create_orders,can_modify_strategy,can_modify_risk,
                    can_approve_strategy
                ) VALUES(?,?,?,?,?,?,?,?,?,0,0,0,0,0,0,0)""",
                (
                    report_id,report_type,start,end,evidence_hash,
                    canonical_json(report_content),report_hash,str(payload["status"]),utc_now(),
                ),
            )
        return self.report(report_id), True

    def report(self, report_id: str) -> dict[str, Any]:
        """Read one report and reject any content/hash mismatch."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM committee_reports WHERE report_id=?", (report_id,)
            ).fetchone()
        if not row:
            raise KeyError(report_id)
        payload = self._report_payload(dict(row))
        if content_hash(payload["report_content"]) != str(row["report_hash"]):
            raise PersonalOSStoreError("Personal OS报告哈希不一致")
        return payload

    def reports(self, limit: int = 100) -> list[dict[str, Any]]:
        """List report summaries without triggering new analysis."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM committee_reports ORDER BY created_time DESC LIMIT ?",
                (max(1, min(int(limit), 300)),),
            ).fetchall()
        return [self._report_payload(dict(row)) for row in rows]

    def save_score(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Replace the same-day process score with a deterministic evidence snapshot."""
        as_of = validate_iso_date(str(payload["as_of_date"]))
        evidence_ids = validate_evidence_ids(payload.get("evidence_ids"))
        score_content = {
            "as_of_date": as_of,
            "total_score": payload.get("total_score"),
            "coverage": float(payload["coverage"]),
            "dimensions": list(payload["dimensions"]),
            "evidence_ids": evidence_ids,
        }
        score_id = stable_id("personal-score", as_of)
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO personal_score(
                    score_id,as_of_date,total_score,coverage,dimensions_json,
                    evidence_ids_json,score_hash,created_time,can_affect_execution,
                    can_trade,can_create_orders
                ) VALUES(?,?,?,?,?,?,?,?,0,0,0)""",
                (
                    score_id,as_of,payload.get("total_score"),float(payload["coverage"]),
                    canonical_json(list(payload["dimensions"])),canonical_json(evidence_ids),
                    content_hash(score_content),utc_now(),
                ),
            )
        return self.score(as_of) or {}

    def score(self, as_of_date: str | None = None) -> dict[str, Any] | None:
        """Read the requested or latest persisted process score."""
        with self._connect() as connection:
            if as_of_date:
                row = connection.execute(
                    "SELECT * FROM personal_score WHERE as_of_date=?", (as_of_date,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM personal_score ORDER BY as_of_date DESC LIMIT 1"
                ).fetchone()
        return self._score_payload(dict(row)) if row else None

    def counts(self) -> dict[str, int]:
        """Return small dashboard counts without exposing write handles."""
        with self._connect() as connection:
            return {
                "event_count": int(connection.execute("SELECT COUNT(*) FROM investment_events").fetchone()[0]),
                "journal_count": int(connection.execute("SELECT COUNT(*) FROM investment_journal WHERE status='active'").fetchone()[0]),
                "review_count": int(connection.execute("SELECT COUNT(*) FROM investment_reviews WHERE user_confirmed=1").fetchone()[0]),
                "pending_review_count": int(connection.execute("SELECT COUNT(*) FROM investment_journal WHERE status='active' AND review_status IN ('DUE','IN_REVIEW')").fetchone()[0]),
                "open_reminder_count": int(connection.execute("SELECT COUNT(*) FROM investment_reminders WHERE status='OPEN'").fetchone()[0]),
                "knowledge_count": int(connection.execute("SELECT COUNT(*) FROM knowledge_base WHERE status='active'").fetchone()[0]),
                "report_count": int(connection.execute("SELECT COUNT(*) FROM committee_reports").fetchone()[0]),
                "score_count": int(connection.execute("SELECT COUNT(*) FROM personal_score").fetchone()[0]),
            }

    @staticmethod
    def _profile_payload(row: dict[str, Any]) -> dict[str, Any]:
        row["behavior"] = json.loads(row.pop("behavior_json"))
        row.update(safety_contract())
        return row

    @staticmethod
    def _event_payload(row: dict[str, Any]) -> dict[str, Any]:
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        row["payload"] = json.loads(row.pop("payload_json"))
        row.update(safety_contract())
        return row

    @staticmethod
    def _journal_payload(row: dict[str, Any]) -> dict[str, Any]:
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        row["content"] = row.get("reason")
        row.update(safety_contract())
        return row

    @staticmethod
    def _review_payload(row: dict[str, Any]) -> dict[str, Any]:
        for source, target in (
            ("facts_changed_json","facts_changed"),("mistakes_json","mistakes"),
            ("good_decisions_json","good_decisions"),("evidence_refs_json","evidence_refs"),
            ("draft_json","draft"),
        ):
            row[target] = json.loads(row.pop(source))
        row["user_confirmed"] = bool(row["user_confirmed"])
        row.update(safety_contract())
        return row

    @staticmethod
    def _reminder_payload(row: dict[str, Any]) -> dict[str, Any]:
        row["evidence_refs"] = json.loads(row.pop("evidence_refs_json"))
        row.update(safety_contract())
        return row

    @staticmethod
    def _snapshot_payload(row: dict[str, Any]) -> dict[str, Any]:
        row["payload"] = json.loads(row.pop("payload_json"))
        row.update(safety_contract())
        return row

    @staticmethod
    def _knowledge_payload(row: dict[str, Any]) -> dict[str, Any]:
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        row.update(safety_contract())
        return row

    @staticmethod
    def _report_payload(row: dict[str, Any]) -> dict[str, Any]:
        row["report_content"] = json.loads(row.pop("content_json"))
        row.update(safety_contract())
        return row

    @staticmethod
    def _score_payload(row: dict[str, Any]) -> dict[str, Any]:
        row["dimensions"] = json.loads(row.pop("dimensions_json"))
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        row.update(safety_contract())
        return row
