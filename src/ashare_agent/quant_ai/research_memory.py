from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from ..quant_lab.contracts import canonical_bytes, sha256_json, utc_now


QUANT_AI_STORE_VERSION = "quant-ai-store-v1.0.0"
_SECRET = re.compile(
    r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]{6,}"
)


class QuantAIStoreError(RuntimeError):
    """Signal integrity, idempotency or non-trading contract violations."""


class ResearchMemoryStore:
    """Persist AI research artifacts, not chat history or execution instructions."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _initialize(self) -> None:
        """Create namespaced audit tables with database-enforced zero capabilities."""
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS ai_research_reports (
                    report_id TEXT PRIMARY KEY,
                    report_type TEXT NOT NULL CHECK(report_type IN ('daily_research_brief','strategy_analysis','factor_analysis','risk_analysis','market_analysis')),
                    experiment_id TEXT,
                    strategy_id TEXT,
                    strategy_version TEXT,
                    agent_type TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    evaluation_json TEXT NOT NULL,
                    report_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('published','degraded','rejected')),
                    model_version TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    used_for_execution INTEGER NOT NULL DEFAULT 0 CHECK(used_for_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0),
                    can_modify_factor_weights INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_factor_weights=0),
                    can_approve_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_approve_strategy=0),
                    UNIQUE(report_type,evidence_hash,model_version)
                );
                CREATE TABLE IF NOT EXISTS research_memory (
                    memory_id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL CHECK(memory_type IN ('strategy_observation','factor_observation','market_pattern','error_lesson')),
                    content TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_claim_id TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
                    evidence_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('candidate','confirmed','archived')),
                    created_time TEXT NOT NULL,
                    confirmed_time TEXT,
                    can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    UNIQUE(source_id,source_claim_id)
                );
                CREATE TABLE IF NOT EXISTS research_questions (
                    question_id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    priority TEXT NOT NULL CHECK(priority IN ('HIGH','MEDIUM','LOW')),
                    status TEXT NOT NULL CHECK(status IN ('OPEN','PLANNED','TESTED','REJECTED','ARCHIVED')),
                    related_experiment TEXT,
                    source_report_id TEXT NOT NULL,
                    source_claim_id TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    updated_time TEXT,
                    can_launch_experiment INTEGER NOT NULL DEFAULT 0 CHECK(can_launch_experiment=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    UNIQUE(source_report_id,source_claim_id)
                );
                CREATE TABLE IF NOT EXISTS ai_research_tasks (
                    task_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL CHECK(task_type='morning_brief'),
                    scheduled_for TEXT NOT NULL,
                    trigger TEXT NOT NULL CHECK(trigger IN ('scheduler','user_action')),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','skipped')),
                    result_report_id TEXT,
                    error_message TEXT,
                    created_time TEXT NOT NULL,
                    completed_time TEXT,
                    can_retry_automatically INTEGER NOT NULL DEFAULT 0 CHECK(can_retry_automatically=0),
                    can_affect_execution INTEGER NOT NULL DEFAULT 0 CHECK(can_affect_execution=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0)
                );
                CREATE INDEX IF NOT EXISTS idx_ai_research_reports_created
                    ON ai_research_reports(created_time DESC);
                CREATE INDEX IF NOT EXISTS idx_research_questions_status
                    ON research_questions(status,priority,created_time DESC);
            """)

    def save_report(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """Store one immutable report only after its canonical hash is verified."""
        payload = dict(report)
        report_hash = str(payload.get("report_hash") or "")
        if report_hash != sha256_json({k: v for k, v in payload.items() if k != "report_hash"}):
            raise QuantAIStoreError("AI研究报告哈希无效")
        evidence_ids = list(payload.get("evidence_ids") or [])
        if not evidence_ids:
            raise QuantAIStoreError("AI研究报告缺少证据引用")
        content = canonical_bytes(payload).decode("utf-8")
        evaluation = payload.get("evaluation") or {}
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO ai_research_reports(
                        report_id,report_type,experiment_id,strategy_id,strategy_version,
                        agent_type,content_json,evidence_ids_json,evidence_hash,evaluation_json,
                        report_hash,status,model_version,created_time
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        payload["report_id"], payload["report_type"],
                        payload.get("experiment_id"), payload.get("strategy_id"),
                        payload.get("strategy_version"), payload.get("agent_type", "research_committee"),
                        content, self._json(evidence_ids), payload["evidence_hash"],
                        self._json(evaluation), report_hash, payload["status"],
                        payload.get("model_version", "disabled"), payload["created_time"],
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT report_id FROM ai_research_reports
                       WHERE report_type=? AND evidence_hash=? AND model_version=?""",
                    (payload["report_type"], payload["evidence_hash"], payload.get("model_version", "disabled")),
                ).fetchone()
                if row is None:
                    raise
                return self.report(str(row["report_id"]))
        return self.report(str(payload["report_id"]))

    def report(self, report_id: str) -> dict[str, Any]:
        """Read and re-hash one immutable report before returning it."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT content_json,report_hash FROM ai_research_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
        if row is None:
            raise KeyError(report_id)
        payload = json.loads(str(row["content_json"]))
        expected = sha256_json({k: v for k, v in payload.items() if k != "report_hash"})
        if expected != str(row["report_hash"]) or payload.get("report_hash") != expected:
            raise QuantAIStoreError("AI研究报告Registry哈希不一致")
        return payload

    def reports(self, limit: int = 50) -> list[dict[str, Any]]:
        """List newest reports without invoking a model or recomputing research."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT report_id FROM ai_research_reports ORDER BY created_time DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [self.report(str(row["report_id"])) for row in rows]

    def add_memory(
        self,
        *,
        memory_type: str,
        content: str,
        source_id: str,
        source_claim_id: str,
        confidence: float,
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        """Save one grounded observation as an unconfirmed, non-executable candidate."""
        if _SECRET.search(content):
            raise QuantAIStoreError("研究记忆不得保存密钥或令牌")
        if not evidence_ids:
            raise QuantAIStoreError("研究记忆必须绑定证据")
        memory_id = f"qmem-{uuid4().hex}"
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO research_memory(
                        memory_id,memory_type,content,source_id,source_claim_id,confidence,
                        evidence_ids_json,status,created_time
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        memory_id, memory_type, content[:2000], source_id,
                        source_claim_id, float(confidence), self._json(evidence_ids),
                        "candidate", utc_now(),
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT memory_id FROM research_memory WHERE source_id=? AND source_claim_id=?",
                    (source_id, source_claim_id),
                ).fetchone()
                if row is None:
                    raise
                memory_id = str(row["memory_id"])
        return self.memory(memory_id)

    def confirm_memory(self, memory_id: str) -> dict[str, Any]:
        """Require an explicit local user action before treating a candidate as confirmed."""
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE research_memory SET status='confirmed',confirmed_time=?
                   WHERE memory_id=? AND status='candidate'""",
                (utc_now(), memory_id),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT memory_id FROM research_memory WHERE memory_id=?", (memory_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(memory_id)
        return self.memory(memory_id)

    def memory(self, memory_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_memory WHERE memory_id=?", (memory_id,)
            ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return self._decode_memory(dict(row))

    def memories(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_memory ORDER BY created_time DESC LIMIT ?",
                (max(1, min(int(limit), 300)),),
            ).fetchall()
        return [self._decode_memory(dict(row)) for row in rows]

    def add_question(
        self,
        *,
        question: str,
        rationale: str,
        priority: str,
        related_experiment: str | None,
        source_report_id: str,
        source_claim_id: str,
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        """Register a question only; never launch an experiment or alter parameters."""
        question_id = f"qresearch-{uuid4().hex}"
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO research_questions(
                        question_id,question,rationale,priority,status,related_experiment,
                        source_report_id,source_claim_id,evidence_ids_json,created_time
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        question_id, question[:1000], rationale[:2000], priority, "OPEN",
                        related_experiment, source_report_id, source_claim_id,
                        self._json(evidence_ids), utc_now(),
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT question_id FROM research_questions
                       WHERE source_report_id=? AND source_claim_id=?""",
                    (source_report_id, source_claim_id),
                ).fetchone()
                if row is None:
                    raise
                question_id = str(row["question_id"])
        return self.question(question_id)

    def update_question(self, question_id: str, status: str) -> dict[str, Any]:
        """Update research workflow state without launching the proposed experiment."""
        if status not in {"OPEN", "PLANNED", "TESTED", "REJECTED", "ARCHIVED"}:
            raise ValueError("研究问题状态无效")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE research_questions SET status=?,updated_time=? WHERE question_id=?",
                (status, utc_now(), question_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(question_id)
        return self.question(question_id)

    def question(self, question_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_questions WHERE question_id=?", (question_id,)
            ).fetchone()
        if row is None:
            raise KeyError(question_id)
        return self._decode_question(dict(row))

    def questions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM research_questions
                   ORDER BY CASE priority WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                            created_time DESC LIMIT ?""",
                (max(1, min(int(limit), 300)),),
            ).fetchall()
        return [self._decode_question(dict(row)) for row in rows]

    def start_task(self, scheduled_for: str, trigger: str) -> dict[str, Any]:
        """Create exactly one non-retrying task for a deterministic scheduler slot."""
        if trigger not in {"scheduler", "user_action"}:
            raise ValueError("AI研究任务触发来源无效")
        key = sha256_json({"task": "morning_brief", "scheduled_for": scheduled_for})
        task_id = f"qtask-{uuid4().hex}"
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO ai_research_tasks(
                        task_id,task_type,scheduled_for,trigger,idempotency_key,status,created_time
                    ) VALUES(?,?,?,?,?,'running',?)""",
                    (task_id, "morning_brief", scheduled_for, trigger, key, utc_now()),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM ai_research_tasks WHERE idempotency_key=?", (key,)
                ).fetchone()
                if row is None:
                    raise
                return dict(row)
        return self.task(task_id)

    def finish_task(
        self,
        task_id: str,
        *,
        status: str,
        report_id: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Finalize a task once; failed tasks are never retried automatically."""
        if status not in {"succeeded", "failed", "skipped"}:
            raise ValueError("AI研究任务终态无效")
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE ai_research_tasks SET status=?,result_report_id=?,error_message=?,
                   completed_time=? WHERE task_id=? AND status='running'""",
                (status, report_id, (error_message or "")[:500] or None, utc_now(), task_id),
            )
            if cursor.rowcount == 0:
                raise QuantAIStoreError("AI研究任务已经终结")
        return self.task(task_id)

    def task(self, task_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_research_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return dict(row)

    def tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_research_tasks ORDER BY created_time DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        """Return dashboard counts without exposing raw database objects."""
        with self._connect() as connection:
            reports = connection.execute("SELECT COUNT(*) FROM ai_research_reports").fetchone()[0]
            memory = connection.execute("SELECT COUNT(*) FROM research_memory").fetchone()[0]
            questions = connection.execute("SELECT COUNT(*) FROM research_questions WHERE status IN ('OPEN','PLANNED')").fetchone()[0]
        return {"reports": int(reports), "memory": int(memory), "open_questions": int(questions)}

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_memory(row: dict[str, Any]) -> dict[str, Any]:
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        row.update({"used_for_execution": False, "can_affect_execution": False, "can_trade": False, "can_create_orders": False})
        return row

    @staticmethod
    def _decode_question(row: dict[str, Any]) -> dict[str, Any]:
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        row.update({"can_launch_experiment": False, "can_modify_strategy": False, "can_trade": False, "can_create_orders": False})
        return row

