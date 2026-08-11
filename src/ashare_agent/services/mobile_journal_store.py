from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4


MOBILE_JOURNAL_STORE_VERSION = "mobile-journal-store-v1.0.0"
_SYMBOL_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)((?:api[_-]?key|password|passwd|secret|token)\s*[:=]\s*)\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


class MobileJournalStoreError(RuntimeError):
    """Reject malformed, conflicting, or unavailable mobile journal records."""


class MobileJournalStore:
    """Persist non-executable investment notes and audited mobile AI questions."""

    def __init__(self, database_path: Path) -> None:
        """Open the project-owned SQLite store and install fail-closed constraints."""
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def close(self) -> None:
        """Commit and close the journal database created by this service."""
        with self._lock:
            self._connection.commit()
            self._connection.close()

    def add_entry(
        self,
        *,
        idempotency_key: str,
        source_device_id: str,
        entry_type: str,
        trade_date: str,
        title: str,
        content: str,
        symbol: str | None = None,
        linked_report_id: str | None = None,
        review_due_date: str | None = None,
    ) -> dict[str, Any]:
        """Create one immutable user journal entry with retry-safe idempotency."""
        if entry_type not in {"buy_reason", "sell_reason", "review", "general"}:
            raise MobileJournalStoreError("不支持的投资日志类型")
        normalized_symbol = symbol.strip().upper() if symbol else None
        if normalized_symbol and not _SYMBOL_PATTERN.fullmatch(normalized_symbol):
            raise MobileJournalStoreError("股票代码格式无效")
        if entry_type in {"buy_reason", "sell_reason"} and not normalized_symbol:
            raise MobileJournalStoreError("买入或卖出理由必须指定股票代码")
        self._validate_date(trade_date, "日志日期")
        if review_due_date:
            self._validate_date(review_due_date, "复盘日期")
        normalized_title = self._redact_text(title.strip())
        normalized_content = self._redact_text(content.strip())
        if not normalized_title or not normalized_content:
            raise MobileJournalStoreError("日志标题和内容不能为空")
        canonical = {
            "entry_type": entry_type,
            "trade_date": trade_date,
            "symbol": normalized_symbol,
            "title": normalized_title,
            "content": normalized_content,
            "linked_report_id": linked_report_id,
            "review_due_date": review_due_date,
        }
        request_hash = hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        entry_id = f"journal-{uuid4()}"
        with self._lock:
            existing = self._connection.execute(
                "SELECT * FROM investment_journal WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise MobileJournalStoreError("幂等键已用于不同日志内容")
                return self._entry(existing)
            try:
                self._connection.execute(
                    """INSERT INTO investment_journal(
                    entry_id,idempotency_key,request_hash,source_device_id,
                    entry_type,trade_date,symbol,title,content,linked_report_id,
                    review_due_date,created_time,can_affect_execution,can_trade,
                    can_create_orders
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,0,0)""",
                    (
                        entry_id,
                        idempotency_key,
                        request_hash,
                        source_device_id,
                        entry_type,
                        trade_date,
                        normalized_symbol,
                        normalized_title,
                        normalized_content,
                        linked_report_id,
                        review_due_date,
                        now,
                    ),
                )
                self._connection.commit()
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise MobileJournalStoreError("投资日志保存失败") from exc
            row = self._connection.execute(
                "SELECT * FROM investment_journal WHERE entry_id=?",
                (entry_id,),
            ).fetchone()
        if row is None:
            raise MobileJournalStoreError("投资日志保存后读取失败")
        return self._entry(row)

    def list_entries(
        self,
        *,
        limit: int = 100,
        entry_type: str | None = None,
        symbol: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """List immutable notes newest-first without deriving trading actions."""
        bounded_limit = max(1, min(int(limit), 300))
        clauses: list[str] = []
        values: list[Any] = []
        if not include_archived:
            clauses.append("status='active'")
        if entry_type:
            if entry_type not in {"buy_reason", "sell_reason", "review", "general"}:
                raise MobileJournalStoreError("不支持的投资日志类型")
            clauses.append("entry_type=?")
            values.append(entry_type)
        if symbol:
            normalized_symbol = symbol.strip().upper()
            if not _SYMBOL_PATTERN.fullmatch(normalized_symbol):
                raise MobileJournalStoreError("股票代码格式无效")
            clauses.append("symbol=?")
            values.append(normalized_symbol)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"""SELECT * FROM investment_journal {where}
                ORDER BY created_time DESC,entry_id DESC LIMIT ?""",
                (*values, bounded_limit),
            ).fetchall()
        return [self._entry(row) for row in rows]

    def get_entry(self, entry_id: str) -> dict[str, Any]:
        """Return one journal record together with its immutable revision audit."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM investment_journal WHERE entry_id=?",
                (entry_id,),
            ).fetchone()
            revisions = self._connection.execute(
                """SELECT revision_id,operation,created_time,source_device_id
                FROM investment_journal_revisions
                WHERE entry_id=? ORDER BY created_time ASC,rowid ASC""",
                (entry_id,),
            ).fetchall()
        if row is None:
            raise MobileJournalStoreError("投资日志不存在")
        result = self._entry(row)
        result["revisions"] = [dict(item) for item in revisions]
        return result

    def update_entry(
        self,
        *,
        entry_id: str,
        idempotency_key: str,
        source_device_id: str,
        expected_version: int,
        title: str | None = None,
        content: str | None = None,
        review_due_date: str | None = None,
    ) -> dict[str, Any]:
        """Revise editable note text with optimistic locking and a full audit trail."""
        if title is None and content is None and review_due_date is None:
            raise MobileJournalStoreError("至少提供一个需要更新的日志字段")
        if review_due_date:
            self._validate_date(review_due_date, "复盘日期")
        safe_title = None if title is None else self._redact_text(title.strip())
        safe_content = None if content is None else self._redact_text(content.strip())
        request_hash = self._revision_hash(
            "update",
            {
                "entry_id": entry_id,
                "expected_version": int(expected_version),
                "title": safe_title,
                "content": safe_content,
                "review_due_date": review_due_date,
            },
        )
        with self._lock:
            replay = self._connection.execute(
                """SELECT entry_id,request_hash FROM investment_journal_revisions
                WHERE idempotency_key=?""",
                (idempotency_key,),
            ).fetchone()
            if replay:
                if replay["request_hash"] != request_hash:
                    raise MobileJournalStoreError("幂等键已用于不同日志更新")
                return self.get_entry(str(replay["entry_id"]))
            row = self._connection.execute(
                "SELECT * FROM investment_journal WHERE entry_id=?",
                (entry_id,),
            ).fetchone()
            if row is None:
                raise MobileJournalStoreError("投资日志不存在")
            before = self._entry(row)
            if before["status"] != "active":
                raise MobileJournalStoreError("已归档日志不可修改")
            if int(before["version"]) != int(expected_version):
                raise MobileJournalStoreError("日志版本冲突，请刷新后重试")
            next_title = before["title"] if safe_title is None else safe_title
            next_content = before["content"] if safe_content is None else safe_content
            if not next_title or not next_content:
                raise MobileJournalStoreError("日志标题和内容不能为空")
            next_due = before.get("review_due_date") if review_due_date is None else review_due_date
            now = datetime.now(timezone.utc).isoformat()
            after = {
                **before,
                "title": next_title,
                "content": next_content,
                "review_due_date": next_due,
                "updated_time": now,
                "version": int(before["version"]) + 1,
            }
            try:
                self._connection.execute(
                    """UPDATE investment_journal SET
                    title=?,content=?,review_due_date=?,updated_time=?,version=?
                    WHERE entry_id=? AND version=?""",
                    (
                        next_title,
                        next_content,
                        next_due,
                        now,
                        after["version"],
                        entry_id,
                        expected_version,
                    ),
                )
                self._save_revision(
                    entry_id=entry_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    source_device_id=source_device_id,
                    operation="update",
                    before=before,
                    after=after,
                    created_time=now,
                )
                self._connection.commit()
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise MobileJournalStoreError("投资日志更新失败") from exc
        return self.get_entry(entry_id)

    def archive_entry(
        self,
        *,
        entry_id: str,
        idempotency_key: str,
        source_device_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Soft-delete one note while retaining its complete investment audit history."""
        request_hash = self._revision_hash(
            "archive",
            {"entry_id": entry_id, "expected_version": int(expected_version)},
        )
        with self._lock:
            replay = self._connection.execute(
                """SELECT entry_id,request_hash FROM investment_journal_revisions
                WHERE idempotency_key=?""",
                (idempotency_key,),
            ).fetchone()
            if replay:
                if replay["request_hash"] != request_hash:
                    raise MobileJournalStoreError("幂等键已用于不同日志归档")
                return self.get_entry(str(replay["entry_id"]))
            row = self._connection.execute(
                "SELECT * FROM investment_journal WHERE entry_id=?",
                (entry_id,),
            ).fetchone()
            if row is None:
                raise MobileJournalStoreError("投资日志不存在")
            before = self._entry(row)
            if int(before["version"]) != int(expected_version):
                raise MobileJournalStoreError("日志版本冲突，请刷新后重试")
            if before["status"] == "archived":
                return self.get_entry(entry_id)
            now = datetime.now(timezone.utc).isoformat()
            after = {
                **before,
                "status": "archived",
                "archived_time": now,
                "updated_time": now,
                "version": int(before["version"]) + 1,
            }
            try:
                self._connection.execute(
                    """UPDATE investment_journal SET
                    status='archived',archived_time=?,updated_time=?,version=?
                    WHERE entry_id=? AND version=?""",
                    (now, now, after["version"], entry_id, expected_version),
                )
                self._save_revision(
                    entry_id=entry_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    source_device_id=source_device_id,
                    operation="archive",
                    before=before,
                    after=after,
                    created_time=now,
                )
                self._connection.commit()
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise MobileJournalStoreError("投资日志归档失败") from exc
        return self.get_entry(entry_id)

    def add_chat(
        self,
        *,
        source_device_id: str,
        intent: str,
        question: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        """Audit one grounded mobile Copilot response without storing model secrets."""
        now = datetime.now(timezone.utc).isoformat()
        chat_id = f"chat-{uuid4()}"
        safe_question = self._redact_text(question.strip())
        evidence_ids = [str(item) for item in report.get("evidence_ids") or []]
        with self._lock:
            try:
                self._connection.execute(
                    """INSERT INTO mobile_copilot_history(
                    chat_id,source_device_id,intent,question,report_id,run_id,
                    evidence_ids_json,evidence_hash,status,created_time,
                    can_affect_execution,can_trade,can_create_orders
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,0,0,0)""",
                    (
                        chat_id,
                        source_device_id,
                        intent,
                        safe_question,
                        report.get("report_id"),
                        report.get("run_id"),
                        json.dumps(evidence_ids, ensure_ascii=False),
                        report.get("evidence_hash"),
                        report.get("status") or "published",
                        now,
                    ),
                )
                self._connection.commit()
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise MobileJournalStoreError("移动AI问答审计保存失败") from exc
            row = self._connection.execute(
                "SELECT * FROM mobile_copilot_history WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        if row is None:
            raise MobileJournalStoreError("移动AI问答审计读取失败")
        return self._chat(row)

    def list_chats(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent AI interactions without replaying model calls."""
        bounded_limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM mobile_copilot_history
                ORDER BY created_time DESC,chat_id DESC LIMIT ?""",
                (bounded_limit,),
            ).fetchall()
        return [self._chat(row) for row in rows]

    def counts(self) -> dict[str, int]:
        """Return journal and Copilot audit counts for mobile status panels."""
        with self._lock:
            journal_count = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM investment_journal"
                ).fetchone()[0]
            )
            chat_count = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM mobile_copilot_history"
                ).fetchone()[0]
            )
        return {"journal_entries": journal_count, "copilot_chats": chat_count}

    def journal_statistics(self, as_of_date: str) -> dict[str, Any]:
        """Calculate stable active-note counts and disclose unsupported review linkage."""
        selected = date.fromisoformat(as_of_date)
        month_prefix = selected.strftime("%Y-%m-")
        with self._lock:
            total = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM investment_journal WHERE status='active'"
                ).fetchone()[0]
            )
            month_count = int(
                self._connection.execute(
                    """SELECT COUNT(*) FROM investment_journal
                    WHERE status='active' AND trade_date LIKE ?""",
                    (f"{month_prefix}%",),
                ).fetchone()[0]
            )
            reviewed = int(
                self._connection.execute(
                    """SELECT COUNT(*) FROM investment_journal
                    WHERE status='active' AND entry_type='review'"""
                ).fetchone()[0]
            )
        return {
            "as_of_date": selected.isoformat(),
            "scope": "active_entries",
            "total": total,
            "month_count": month_count,
            "reviewed": reviewed,
            "pending_review": None,
            "availability": {
                "total": "available",
                "month_count": "available",
                "reviewed": "available",
                "pending_review": "unavailable",
            },
            "message": "尚未建立复盘日志与原买卖理由的一对一关联，待验证数量不作推断",
        }

    def _create_schema(self) -> None:
        """Create tables whose database constraints forbid execution authority."""
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS investment_journal(
                    entry_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    source_device_id TEXT NOT NULL,
                    entry_type TEXT NOT NULL CHECK(entry_type IN(
                        'buy_reason','sell_reason','review','general'
                    )),
                    trade_date TEXT NOT NULL,
                    symbol TEXT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    linked_report_id TEXT,
                    review_due_date TEXT,
                    created_time TEXT NOT NULL,
                    updated_time TEXT,
                    archived_time TEXT,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN('active','archived')),
                    version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),
                    can_affect_execution INTEGER NOT NULL DEFAULT 0
                        CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0
                        CHECK(can_create_orders=0)
                );
                CREATE INDEX IF NOT EXISTS idx_mobile_journal_date
                    ON investment_journal(trade_date DESC,created_time DESC);
                CREATE INDEX IF NOT EXISTS idx_mobile_journal_symbol
                    ON investment_journal(symbol,created_time DESC);

                CREATE TABLE IF NOT EXISTS investment_journal_revisions(
                    revision_id TEXT PRIMARY KEY,
                    entry_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    source_device_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN('update','archive')),
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    FOREIGN KEY(entry_id) REFERENCES investment_journal(entry_id)
                );

                CREATE TABLE IF NOT EXISTS mobile_copilot_history(
                    chat_id TEXT PRIMARY KEY,
                    source_device_id TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    question TEXT NOT NULL,
                    report_id TEXT,
                    run_id TEXT,
                    evidence_ids_json TEXT NOT NULL,
                    evidence_hash TEXT,
                    status TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    can_affect_execution INTEGER NOT NULL DEFAULT 0
                        CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0
                        CHECK(can_create_orders=0)
                );
                CREATE INDEX IF NOT EXISTS idx_mobile_copilot_created
                    ON mobile_copilot_history(created_time DESC);
                """
            )
            self._migrate_journal_schema()
            self._connection.commit()

    def _migrate_journal_schema(self) -> None:
        """Add audit columns when opening a database created by an early V2-007 build."""
        columns = {
            str(row[1])
            for row in self._connection.execute(
                "PRAGMA table_info(investment_journal)"
            ).fetchall()
        }
        additions = {
            "updated_time": "TEXT",
            "archived_time": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "version": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, definition in additions.items():
            if column not in columns:
                self._connection.execute(
                    f"ALTER TABLE investment_journal ADD COLUMN {column} {definition}"
                )

    def _save_revision(
        self,
        *,
        entry_id: str,
        idempotency_key: str,
        request_hash: str,
        source_device_id: str,
        operation: str,
        before: dict[str, Any],
        after: dict[str, Any],
        created_time: str,
    ) -> None:
        """Append one immutable update/archive audit record inside the active transaction."""
        self._connection.execute(
            """INSERT INTO investment_journal_revisions(
            revision_id,entry_id,idempotency_key,request_hash,source_device_id,
            operation,before_json,after_json,created_time
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                f"revision-{uuid4()}",
                entry_id,
                idempotency_key,
                request_hash,
                source_device_id,
                operation,
                json.dumps(before, ensure_ascii=False, sort_keys=True),
                json.dumps(after, ensure_ascii=False, sort_keys=True),
                created_time,
            ),
        )

    @staticmethod
    def _revision_hash(operation: str, payload: dict[str, Any]) -> str:
        """Hash one journal mutation so future audits can compare exact intent."""
        return hashlib.sha256(
            json.dumps(
                {"operation": operation, "payload": payload},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _entry(row: sqlite3.Row) -> dict[str, Any]:
        """Serialize a journal row while hiding internal request hashes."""
        item = dict(row)
        item.pop("request_hash", None)
        item.pop("idempotency_key", None)
        item["can_affect_execution"] = False
        item["can_trade"] = False
        item["can_create_orders"] = False
        return item

    @staticmethod
    def _chat(row: sqlite3.Row) -> dict[str, Any]:
        """Serialize one chat audit and parse its evidence references."""
        item = dict(row)
        try:
            item["evidence_ids"] = json.loads(item.pop("evidence_ids_json"))
        except (TypeError, json.JSONDecodeError) as exc:
            raise MobileJournalStoreError("移动AI证据索引损坏") from exc
        item["can_affect_execution"] = False
        item["can_trade"] = False
        item["can_create_orders"] = False
        return item

    @staticmethod
    def _validate_date(value: str, label: str) -> None:
        """Require canonical ISO dates so journal reminders remain sortable."""
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise MobileJournalStoreError(f"{label}格式无效") from exc
        if parsed.isoformat() != value:
            raise MobileJournalStoreError(f"{label}必须使用YYYY-MM-DD")

    @staticmethod
    def _redact_text(value: str) -> str:
        """Remove common credential forms before user text reaches persistent storage."""
        result = value
        for pattern in _SECRET_TEXT_PATTERNS:
            result = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]" if match.lastindex else "[REDACTED]", result)
        return result
