from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping, Sequence

from .contracts import (
    EVENT_TYPES,
    JOURNAL_TYPES,
    KNOWLEDGE_CATEGORIES,
    PERSONAL_OS_SCHEMA_VERSION,
    REPORT_TYPES,
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
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        """Create additive Personal OS tables with database-level zero authority."""
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
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
                    entry_type TEXT NOT NULL CHECK(entry_type IN ('buy_reason','sell_reason','observation','review','lesson')),
                    event_id TEXT,
                    trade_date TEXT NOT NULL,
                    symbol TEXT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
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
        entry_type = str(payload["entry_type"])
        if entry_type not in JOURNAL_TYPES:
            raise PersonalOSStoreError("投资日志类型无效")
        key = str(payload["idempotency_key"])
        evidence_ids = validate_evidence_ids(payload.get("evidence_ids"))
        request = {
            "entry_type": entry_type,
            "event_id": payload.get("event_id"),
            "trade_date": validate_iso_date(str(payload["trade_date"])),
            "symbol": payload.get("symbol"),
            "title": str(payload["title"]).strip(),
            "content": str(payload["content"]).strip(),
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
                    symbol,title,content,outcome,lesson,evidence_ids_json,status,version,
                    created_time,updated_time,archived_time,can_affect_execution,can_trade,
                    can_create_orders
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'active',1,?,NULL,NULL,0,0,0)""",
                (
                    journal_id,key,request_hash,entry_type,payload.get("event_id"),
                    request["trade_date"],payload.get("symbol"),request["title"],
                    request["content"],request["outcome"],request["lesson"],
                    canonical_json(evidence_ids),utc_now(),
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
        content = str(payload.get("content", current["content"])).strip()
        outcome = payload.get("outcome", current.get("outcome"))
        lesson = payload.get("lesson", current.get("lesson"))
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE investment_journal SET title=?,content=?,outcome=?,lesson=?,
                    version=version+1,updated_time=? WHERE journal_id=? AND version=? AND status='active'""",
                (title,content,outcome,lesson,utc_now(),journal_id,expected),
            ).rowcount
        if not updated:
            raise PersonalOSStoreError("日志并发更新冲突")
        return self.journal(journal_id)

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

