from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping

from ..core.contracts import PromptSpec
from .prompt_registry import PromptRegistry


AI_COPILOT_STORE_VERSION = "ai-copilot-store-v1.2.0"
_MEMORY_CATEGORIES = {"profile", "preference", "decision", "error_pattern", "lesson"}
_TASK_TYPES = {
    "morning_report",
    "close_review",
    "stock_analysis",
    "portfolio_analysis",
    "risk_alert",
    "coach_review",
}
_SECRET_PATTERN = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|api[_ -]?key\s*[:=])", re.I)


class CopilotStoreError(RuntimeError):
    """Reject unsafe or incomplete AI audit and memory records."""


class CopilotStore:
    """Persist AI reports, prompts, memory and evaluation outside trading ledgers."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived SQLite transaction with relational checks enabled."""
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize_schema(self) -> None:
        """Create the isolated Copilot catalog without touching research or trading DBs."""
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS prompt_registry (
                    prompt_version TEXT PRIMARY KEY,
                    agent_type TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    temperature REAL NOT NULL,
                    max_tokens INTEGER NOT NULL,
                    prompt_hash TEXT NOT NULL,
                    created_time TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ai_reports (
                    report_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    subject_symbol TEXT,
                    model_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    created_time TEXT NOT NULL,
                    used_for_execution INTEGER NOT NULL CHECK(used_for_execution=0),
                    can_trade INTEGER NOT NULL CHECK(can_trade=0),
                    FOREIGN KEY(prompt_version) REFERENCES prompt_registry(prompt_version)
                );
                CREATE INDEX IF NOT EXISTS idx_ai_reports_run_created
                    ON ai_reports(run_id,created_time DESC);
                CREATE TABLE IF NOT EXISTS ai_memory (
                    memory_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_report_id TEXT,
                    evidence_ids_json TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
                    status TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    confirmed_time TEXT,
                    can_affect_execution INTEGER NOT NULL CHECK(can_affect_execution=0),
                    FOREIGN KEY(source_report_id) REFERENCES ai_reports(report_id)
                );
                CREATE INDEX IF NOT EXISTS idx_ai_memory_status_created
                    ON ai_memory(status,created_time DESC);
                CREATE TABLE IF NOT EXISTS ai_evaluation (
                    evaluation_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL UNIQUE,
                    evaluation_version TEXT NOT NULL,
                    grounded INTEGER NOT NULL,
                    citation_accuracy REAL NOT NULL,
                    citation_count INTEGER NOT NULL,
                    valid_citation_count INTEGER NOT NULL,
                    unknown_citations_json TEXT NOT NULL,
                    missing_citation_count INTEGER NOT NULL,
                    numeric_claim_count INTEGER NOT NULL,
                    numeric_errors_json TEXT NOT NULL,
                    schema_errors_json TEXT NOT NULL,
                    human_rating INTEGER,
                    human_note TEXT,
                    evaluated_time TEXT NOT NULL,
                    FOREIGN KEY(report_id) REFERENCES ai_reports(report_id)
                );
                CREATE TABLE IF NOT EXISTS ai_tasks (
                    task_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    subject_symbol TEXT,
                    trigger TEXT NOT NULL CHECK(trigger IN ('user_action','daily_os')),
                    status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
                    result_state TEXT NOT NULL
                        CHECK(result_state IN ('pending','generated','reused','rejected','error')),
                    result_report_id TEXT,
                    error_message TEXT,
                    created_time TEXT NOT NULL,
                    started_time TEXT NOT NULL,
                    completed_time TEXT,
                    can_schedule INTEGER NOT NULL CHECK(can_schedule=0),
                    can_affect_execution INTEGER NOT NULL CHECK(can_affect_execution=0),
                    FOREIGN KEY(result_report_id) REFERENCES ai_reports(report_id)
                );
                CREATE INDEX IF NOT EXISTS idx_ai_tasks_status_created
                    ON ai_tasks(status,created_time DESC);
                """
            )
            self._migrate_task_trigger(connection)

    @staticmethod
    def _migrate_task_trigger(connection: sqlite3.Connection) -> None:
        """Widen the legacy trigger constraint without losing AI task audits."""
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_tasks'"
        ).fetchone()
        sql = str(row[0] or "") if row else ""
        compact = "".join(sql.lower().split())
        if "daily_os" in compact:
            return
        connection.execute("DROP INDEX IF EXISTS idx_ai_tasks_status_created")
        connection.execute("ALTER TABLE ai_tasks RENAME TO ai_tasks_legacy")
        connection.executescript(
            """
            CREATE TABLE ai_tasks (
                task_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                run_id TEXT NOT NULL,
                subject_symbol TEXT,
                trigger TEXT NOT NULL CHECK(trigger IN ('user_action','daily_os')),
                status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
                result_state TEXT NOT NULL
                    CHECK(result_state IN ('pending','generated','reused','rejected','error')),
                result_report_id TEXT,
                error_message TEXT,
                created_time TEXT NOT NULL,
                started_time TEXT NOT NULL,
                completed_time TEXT,
                can_schedule INTEGER NOT NULL CHECK(can_schedule=0),
                can_affect_execution INTEGER NOT NULL CHECK(can_affect_execution=0),
                FOREIGN KEY(result_report_id) REFERENCES ai_reports(report_id)
            );
            INSERT INTO ai_tasks SELECT * FROM ai_tasks_legacy;
            DROP TABLE ai_tasks_legacy;
            CREATE INDEX idx_ai_tasks_status_created
                ON ai_tasks(status,created_time DESC);
            """
        )

    def start_task(
        self,
        *,
        task_type: str,
        run_id: str,
        subject_symbol: str | None = None,
        trigger: str = "user_action",
    ) -> dict[str, Any]:
        """Start one audited AI task while keeping scheduling capability disabled."""
        if task_type not in _TASK_TYPES:
            raise CopilotStoreError("不支持的AI任务类型")
        if not run_id.strip():
            raise CopilotStoreError("AI任务缺少run_id")
        if trigger not in {"user_action", "daily_os"}:
            raise CopilotStoreError("不支持的AI任务触发来源")
        now = datetime.now(timezone.utc).isoformat()
        task_id = self._identity(
            "task", task_type, run_id, subject_symbol or "", now
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ai_tasks(
                    task_id,task_type,run_id,subject_symbol,trigger,status,
                    result_state,result_report_id,error_message,created_time,
                    started_time,completed_time,can_schedule,can_affect_execution
                ) VALUES(?,?,?,?,?,'running','pending',NULL,NULL,?,?,NULL,0,0)""",
                (task_id, task_type, run_id, subject_symbol, trigger, now, now),
            )
        return self.task(task_id) or {}

    def complete_task(
        self,
        task_id: str,
        *,
        report_id: str,
        result_state: str,
    ) -> dict[str, Any]:
        """Complete a running task after a report was generated or safely reused."""
        if result_state not in {"generated", "reused"}:
            raise CopilotStoreError("AI成功任务结果状态无效")
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE ai_tasks
                SET status='succeeded',result_state=?,result_report_id=?,
                    error_message=NULL,completed_time=?
                WHERE task_id=? AND status='running'""",
                (
                    result_state,
                    report_id,
                    datetime.now(timezone.utc).isoformat(),
                    task_id,
                ),
            ).rowcount
        if not updated:
            raise CopilotStoreError("AI任务不存在或已结束")
        return self.task(task_id) or {}

    def fail_task(
        self,
        task_id: str,
        *,
        error_message: str,
        report_id: str | None = None,
        result_state: str = "error",
    ) -> dict[str, Any]:
        """Fail one task without retrying, scheduling, or affecting execution."""
        if result_state not in {"rejected", "error"}:
            raise CopilotStoreError("AI失败任务结果状态无效")
        safe_error = (error_message.strip() or "AI任务执行失败")[:1000]
        if _SECRET_PATTERN.search(safe_error):
            safe_error = "AI任务执行失败，错误详情因可能含敏感信息已隐藏"
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE ai_tasks
                SET status='failed',result_state=?,result_report_id=?,
                    error_message=?,completed_time=?
                WHERE task_id=? AND status='running'""",
                (
                    result_state,
                    report_id,
                    safe_error,
                    datetime.now(timezone.utc).isoformat(),
                    task_id,
                ),
            ).rowcount
        if not updated:
            raise CopilotStoreError("AI任务不存在或已结束")
        return self.task(task_id) or {}

    def register_prompt(self, spec: PromptSpec, model_version: str) -> None:
        """Persist immutable prompt metadata without storing provider secrets."""
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO prompt_registry(
                    prompt_version,agent_type,model_version,temperature,max_tokens,
                    prompt_hash,created_time
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    spec.prompt_version,
                    spec.agent_type.value,
                    model_version,
                    float(spec.temperature),
                    int(spec.max_tokens),
                    PromptRegistry.prompt_hash(spec),
                    spec.created_at,
                ),
            )

    def find_report(
        self,
        *,
        run_id: str,
        report_type: str,
        subject_symbol: str | None,
        model_version: str,
        prompt_version: str,
        evidence_hash: str,
    ) -> dict[str, Any] | None:
        """Reuse one grounded immutable report for identical evidence and contracts."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM ai_reports
                WHERE run_id=? AND report_type=? AND COALESCE(subject_symbol,'')=?
                  AND model_version=? AND prompt_version=? AND evidence_hash=?
                  AND status='published'
                ORDER BY created_time DESC LIMIT 1""",
                (
                    run_id,
                    report_type,
                    subject_symbol or "",
                    model_version,
                    prompt_version,
                    evidence_hash,
                ),
            ).fetchone()
        return self._report_payload(dict(row)) if row else None

    def save_report(
        self,
        *,
        run_id: str,
        agent_type: str,
        report_type: str,
        subject_symbol: str | None,
        model_version: str,
        prompt_version: str,
        evidence_ids: list[str],
        evidence_hash: str,
        content: Mapping[str, Any],
        status: str,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Save model output and its exact evidence identity with execution disabled."""
        if status not in {"published", "rejected"}:
            raise CopilotStoreError("不支持的AI报告状态")
        created = datetime.now(timezone.utc).isoformat()
        report_id = self._identity(
            "report",
            run_id,
            report_type,
            subject_symbol or "",
            model_version,
            prompt_version,
            evidence_hash,
            created,
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ai_reports(
                    report_id,run_id,agent_type,report_type,subject_symbol,
                    model_version,prompt_version,evidence_ids_json,evidence_hash,
                    content_json,status,error_message,created_time,used_for_execution,can_trade
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)""",
                (
                    report_id,
                    run_id,
                    agent_type,
                    report_type,
                    subject_symbol,
                    model_version,
                    prompt_version,
                    self._json(evidence_ids),
                    evidence_hash,
                    self._json(content),
                    status,
                    (error_message or "")[:1000] or None,
                    created,
                ),
            )
        return self.report(report_id)

    def save_evaluation(self, report_id: str, evaluation: Mapping[str, Any]) -> dict[str, Any]:
        """Attach automated citation and numeric-grounding results to one report."""
        evaluation_id = self._identity("evaluation", report_id)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ai_evaluation(
                    evaluation_id,report_id,evaluation_version,grounded,citation_accuracy,
                    citation_count,valid_citation_count,unknown_citations_json,
                    missing_citation_count,numeric_claim_count,numeric_errors_json,
                    schema_errors_json,human_rating,human_note,evaluated_time
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,?)
                ON CONFLICT(report_id) DO UPDATE SET
                    evaluation_version=excluded.evaluation_version,
                    grounded=excluded.grounded,
                    citation_accuracy=excluded.citation_accuracy,
                    citation_count=excluded.citation_count,
                    valid_citation_count=excluded.valid_citation_count,
                    unknown_citations_json=excluded.unknown_citations_json,
                    missing_citation_count=excluded.missing_citation_count,
                    numeric_claim_count=excluded.numeric_claim_count,
                    numeric_errors_json=excluded.numeric_errors_json,
                    schema_errors_json=excluded.schema_errors_json,
                    evaluated_time=excluded.evaluated_time""",
                (
                    evaluation_id,
                    report_id,
                    evaluation["evaluation_version"],
                    int(bool(evaluation["grounded"])),
                    float(evaluation["citation_accuracy"]),
                    int(evaluation["citation_count"]),
                    int(evaluation["valid_citation_count"]),
                    self._json(evaluation["unknown_citations"]),
                    int(evaluation["missing_citation_count"]),
                    int(evaluation["numeric_claim_count"]),
                    self._json(evaluation["numeric_errors"]),
                    self._json(evaluation["schema_errors"]),
                    now,
                ),
            )
        return self.evaluation(report_id) or {}

    def add_memory(
        self,
        *,
        category: str,
        content: str,
        source: str,
        confidence: float,
        evidence_ids: list[str] | None = None,
        source_report_id: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Store non-executable investment memory with source and confirmation state."""
        normalized = content.strip()
        if category not in _MEMORY_CATEGORIES:
            raise CopilotStoreError("不支持的投资记忆分类")
        if not normalized or len(normalized) > 2000:
            raise CopilotStoreError("投资记忆内容长度无效")
        if _SECRET_PATTERN.search(normalized):
            raise CopilotStoreError("投资记忆不得保存密钥或令牌")
        bounded_confidence = max(0.0, min(float(confidence), 1.0))
        status = "confirmed" if confirmed else "candidate"
        now = datetime.now(timezone.utc).isoformat()
        memory_id = self._identity(
            "memory", category, normalized, source, source_report_id or ""
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ai_memory(
                    memory_id,category,content,source,source_report_id,evidence_ids_json,
                    confidence,status,created_time,confirmed_time,can_affect_execution
                ) VALUES(?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT(memory_id) DO UPDATE SET
                    confidence=MAX(ai_memory.confidence,excluded.confidence),
                    status=CASE WHEN ai_memory.status='confirmed' THEN 'confirmed' ELSE excluded.status END,
                    confirmed_time=COALESCE(ai_memory.confirmed_time,excluded.confirmed_time)""",
                (
                    memory_id,
                    category,
                    normalized,
                    source,
                    source_report_id,
                    self._json(evidence_ids or []),
                    bounded_confidence,
                    status,
                    now,
                    now if confirmed else None,
                ),
            )
        return self.memory(memory_id) or {}

    def confirm_memory(self, memory_id: str) -> dict[str, Any]:
        """Require an explicit user action before an AI candidate becomes confirmed."""
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE ai_memory SET status='confirmed',confirmed_time=? WHERE memory_id=?",
                (datetime.now(timezone.utc).isoformat(), memory_id),
            ).rowcount
        if not updated:
            raise CopilotStoreError("投资记忆不存在")
        return self.memory(memory_id) or {}

    def rate_report(self, report_id: str, rating: int, note: str = "") -> dict[str, Any]:
        """Record a bounded human quality score without changing the report output."""
        if rating < 1 or rating > 5:
            raise CopilotStoreError("人工评分必须为1到5")
        if len(note) > 1000:
            raise CopilotStoreError("人工评价备注过长")
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE ai_evaluation SET human_rating=?,human_note=? WHERE report_id=?",
                (rating, note.strip() or None, report_id),
            ).rowcount
        if not updated:
            raise CopilotStoreError("AI报告评估记录不存在")
        return self.evaluation(report_id) or {}

    def report(self, report_id: str) -> dict[str, Any] | None:
        """Return one report with its evaluation and explicit safety flags."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_reports WHERE report_id=?", (report_id,)
            ).fetchone()
        if not row:
            return None
        payload = self._report_payload(dict(row))
        payload["evaluation"] = self.evaluation(report_id)
        return payload

    def list_reports(self, limit: int = 30) -> list[dict[str, Any]]:
        """List newest Copilot reports with bounded result size."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_reports ORDER BY created_time DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [self._report_payload(dict(row)) for row in rows]

    def memory(self, memory_id: str) -> dict[str, Any] | None:
        """Return one memory item without execution capabilities."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_memory WHERE memory_id=?", (memory_id,)
            ).fetchone()
        return self._memory_payload(dict(row)) if row else None

    def list_memory(self, limit: int = 100) -> list[dict[str, Any]]:
        """List newest confirmed and candidate investment memories."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_memory ORDER BY created_time DESC LIMIT ?",
                (max(1, min(int(limit), 300)),),
            ).fetchall()
        return [self._memory_payload(dict(row)) for row in rows]

    def task(self, task_id: str) -> dict[str, Any] | None:
        """Return one explicit task audit record with safety flags forced off."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return self._task_payload(dict(row)) if row else None

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        """List recent explicit AI tasks; this method never starts or retries work."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_tasks ORDER BY created_time DESC LIMIT ?",
                (max(1, min(int(limit), 300)),),
            ).fetchall()
        return [self._task_payload(dict(row)) for row in rows]

    def evaluation(self, report_id: str) -> dict[str, Any] | None:
        """Return citation, numeric and human-review evidence for one report."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_evaluation WHERE report_id=?", (report_id,)
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        for field in ("unknown_citations_json", "numeric_errors_json", "schema_errors_json"):
            payload[field.removesuffix("_json")] = json.loads(payload.pop(field))
        payload["grounded"] = bool(payload["grounded"])
        return payload

    def counts(self) -> dict[str, int]:
        """Return report, memory and explicit task counts for status views."""
        with self._connect() as connection:
            report_count = int(connection.execute("SELECT COUNT(*) FROM ai_reports").fetchone()[0])
            published_count = int(connection.execute(
                "SELECT COUNT(*) FROM ai_reports WHERE status='published'"
            ).fetchone()[0])
            memory_count = int(connection.execute("SELECT COUNT(*) FROM ai_memory").fetchone()[0])
            confirmed_count = int(connection.execute(
                "SELECT COUNT(*) FROM ai_memory WHERE status='confirmed'"
            ).fetchone()[0])
            task_count = int(connection.execute("SELECT COUNT(*) FROM ai_tasks").fetchone()[0])
            succeeded_task_count = int(connection.execute(
                "SELECT COUNT(*) FROM ai_tasks WHERE status='succeeded'"
            ).fetchone()[0])
            failed_task_count = int(connection.execute(
                "SELECT COUNT(*) FROM ai_tasks WHERE status='failed'"
            ).fetchone()[0])
        return {
            "report_count": report_count,
            "published_report_count": published_count,
            "memory_count": memory_count,
            "confirmed_memory_count": confirmed_count,
            "task_count": task_count,
            "succeeded_task_count": succeeded_task_count,
            "failed_task_count": failed_task_count,
        }

    @staticmethod
    def _report_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Decode JSON fields and force non-execution flags on every read."""
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        row["content"] = json.loads(row.pop("content_json"))
        row["used_for_execution"] = False
        row["can_trade"] = False
        return row

    @staticmethod
    def _memory_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Decode one memory row and expose its non-execution semantics."""
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        row["can_affect_execution"] = False
        return row

    @staticmethod
    def _task_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Expose a task as audit metadata, never as an executable job."""
        row["can_schedule"] = False
        row["can_affect_execution"] = False
        return row

    @staticmethod
    def _identity(namespace: str, *parts: str) -> str:
        """Create a stable non-secret content identity."""
        digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
        return f"{namespace}-{digest}"

    @staticmethod
    def _json(value: Any) -> str:
        """Write canonical strict JSON for reproducible AI audits."""
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
