from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from ..contracts import canonical_bytes, sha256_json, utc_now
from ..exceptions import ImmutableExperimentError, InvalidStateTransitionError
from .contracts import (
    ApprovalStatus,
    StrategyState,
    StrategyVersionSpec,
    ValidationReview,
)


_STATES = tuple(item.value for item in StrategyState)


class StrategyRegistry:
    """Persist strategy governance separately from experiments and trading ledgers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _initialize(self) -> None:
        states = ",".join(f"'{item}'" for item in _STATES)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(f"""
                CREATE TABLE IF NOT EXISTS strategies (
                    strategy_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    current_state TEXT NOT NULL CHECK(current_state IN ({states})),
                    active_version TEXT NOT NULL,
                    creator TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    retired_at TEXT,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_auto_promote INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_promote=0)
                );
                CREATE TABLE IF NOT EXISTS strategy_versions (
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    version_hash TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    research_run_id TEXT NOT NULL,
                    factor_version TEXT NOT NULL,
                    parameter_hash TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    dataset_version TEXT NOT NULL,
                    dataset_label TEXT NOT NULL CHECK(dataset_label IN ('POINT_IN_TIME','SYNTHETIC_TEST_ONLY')),
                    created_at TEXT NOT NULL,
                    immutable INTEGER NOT NULL DEFAULT 1 CHECK(immutable=1),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    PRIMARY KEY(strategy_id,version),
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );
                CREATE TABLE IF NOT EXISTS strategy_review_reports (
                    review_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL UNIQUE,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    current_state TEXT NOT NULL CHECK(current_state IN ({states})),
                    recommended_state TEXT CHECK(recommended_state IS NULL OR recommended_state IN ({states})),
                    recommendation TEXT NOT NULL,
                    health_score REAL,
                    health_partial_score REAL NOT NULL,
                    health_coverage REAL NOT NULL CHECK(health_coverage>=0 AND health_coverage<=1),
                    evidence_hash TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    report_hash TEXT NOT NULL,
                    artifact_manifest_json TEXT NOT NULL,
                    artifact_manifest_hash TEXT NOT NULL,
                    dataset_label TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    human_approval_required INTEGER NOT NULL DEFAULT 1 CHECK(human_approval_required=1),
                    ai_can_approve INTEGER NOT NULL DEFAULT 0 CHECK(ai_can_approve=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_auto_promote INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_promote=0),
                    FOREIGN KEY(strategy_id,strategy_version)
                        REFERENCES strategy_versions(strategy_id,version)
                );
                CREATE TABLE IF NOT EXISTS validation_records (
                    record_id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    gate_name TEXT NOT NULL CHECK(gate_name IN ('DATA_INTEGRITY','FACTOR_VALIDITY','ROBUSTNESS','OUT_OF_SAMPLE','PAPER_TRADING')),
                    gate_status TEXT NOT NULL CHECK(gate_status IN ('PASSED','FAILED','BLOCKED','UNAVAILABLE','NOT_DUE')),
                    score REAL,
                    summary TEXT NOT NULL,
                    checks_json TEXT NOT NULL,
                    block_reasons_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    rule_version TEXT NOT NULL,
                    rule_config_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    can_approve INTEGER NOT NULL DEFAULT 0 CHECK(can_approve=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    UNIQUE(review_id,gate_name),
                    FOREIGN KEY(review_id) REFERENCES strategy_review_reports(review_id)
                );
                CREATE TABLE IF NOT EXISTS promotion_history (
                    promotion_id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    from_state TEXT NOT NULL CHECK(from_state IN ({states})),
                    to_state TEXT NOT NULL CHECK(to_state IN ({states})),
                    system_recommendation TEXT NOT NULL,
                    approval_status TEXT NOT NULL CHECK(approval_status IN ('PENDING','APPROVED','REJECTED','CANCELLED')),
                    requested_by TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    decided_by TEXT,
                    decision_reason TEXT,
                    decided_at TEXT,
                    evidence_hash TEXT NOT NULL,
                    can_auto_promote INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_promote=0),
                    ai_can_approve INTEGER NOT NULL DEFAULT 0 CHECK(ai_can_approve=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    UNIQUE(review_id,to_state),
                    FOREIGN KEY(review_id) REFERENCES strategy_review_reports(review_id),
                    FOREIGN KEY(strategy_id,strategy_version)
                        REFERENCES strategy_versions(strategy_id,version)
                );
                CREATE INDEX IF NOT EXISTS idx_strategy_review_created
                    ON strategy_review_reports(strategy_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_promotion_status
                    ON promotion_history(approval_status,requested_at DESC);
            """)

    def register(self, spec: StrategyVersionSpec) -> dict[str, Any]:
        """Register a strategy/version idempotently and reject mutable redefinition."""
        now = utc_now()
        spec_json = canonical_bytes(asdict(spec)).decode("utf-8")
        with self._connect() as connection:
            strategy = connection.execute(
                "SELECT * FROM strategies WHERE strategy_id=?", (spec.strategy_id,)
            ).fetchone()
            if strategy is None:
                connection.execute(
                    """INSERT INTO strategies(
                        strategy_id,name,current_state,active_version,creator,description,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        spec.strategy_id, spec.name, StrategyState.DRAFT.value,
                        spec.version, spec.creator, spec.description, now, now,
                    ),
                )
            elif str(strategy["name"]) != spec.name:
                raise ImmutableExperimentError("同一strategy_id不得更改策略名称")
            row = connection.execute(
                "SELECT version_hash FROM strategy_versions WHERE strategy_id=? AND version=?",
                (spec.strategy_id, spec.version),
            ).fetchone()
            if row is not None:
                if str(row["version_hash"]) != spec.version_hash:
                    raise ImmutableExperimentError("已注册策略版本不可变更")
            else:
                connection.execute(
                    """INSERT INTO strategy_versions(
                        strategy_id,version,version_hash,spec_json,research_run_id,factor_version,
                        parameter_hash,code_hash,dataset_version,dataset_label,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        spec.strategy_id, spec.version, spec.version_hash, spec_json,
                        spec.research_run_id, spec.factor_version, spec.parameter_hash,
                        spec.code_hash, spec.dataset_version, spec.dataset_label, now,
                    ),
                )
        return self.strategy(spec.strategy_id)

    def strategy(self, strategy_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategies WHERE strategy_id=?", (strategy_id,)
            ).fetchone()
        if row is None:
            raise KeyError(strategy_id)
        return dict(row)

    def version(self, strategy_id: str, version: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_versions WHERE strategy_id=? AND version=?",
                (strategy_id, version),
            ).fetchone()
        if row is None:
            raise KeyError((strategy_id, version))
        payload = dict(row)
        spec = json.loads(payload.pop("spec_json"))
        if sha256_json(spec) != payload["version_hash"]:
            raise ImmutableExperimentError("策略版本Registry哈希不一致")
        payload["spec"] = spec
        return payload

    def list_strategies(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM strategies ORDER BY updated_at DESC,strategy_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_review(
        self,
        review: ValidationReview,
        report: dict[str, Any],
        artifact_manifest: dict[str, Any],
    ) -> None:
        """Persist the immutable committee report and one row per deterministic gate."""
        if any((review.can_trade, review.can_create_orders, review.can_auto_promote)):
            raise ValueError("准入审查不得拥有执行或自动晋级能力")
        report_hash = str(report.get("report_hash") or "")
        if not report_hash or sha256_json({k: v for k, v in report.items() if k != "report_hash"}) != report_hash:
            raise ImmutableExperimentError("委员会报告哈希无效")
        manifest_hash = str(artifact_manifest.get("manifest_hash") or "")
        now = utc_now()
        with self._connect() as connection:
            state = connection.execute(
                "SELECT current_state FROM strategies WHERE strategy_id=?",
                (review.strategy_id,),
            ).fetchone()
            if state is None or str(state["current_state"]) != review.current_state.value:
                raise InvalidStateTransitionError("审查状态与策略Registry当前状态不一致")
            connection.execute(
                """INSERT INTO strategy_review_reports(
                    review_id,report_id,strategy_id,strategy_version,current_state,
                    recommended_state,recommendation,health_score,health_partial_score,
                    health_coverage,evidence_hash,evidence_ids_json,report_json,report_hash,
                    artifact_manifest_json,artifact_manifest_hash,dataset_label,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    review.review_id, report["report_id"], review.strategy_id,
                    review.strategy_version, review.current_state.value,
                    review.recommended_state.value if review.recommended_state else None,
                    review.recommendation, review.health.score, review.health.partial_score,
                    review.health.coverage, review.evidence_hash,
                    json.dumps(list(review.evidence_ids), ensure_ascii=False, separators=(",", ":")),
                    canonical_bytes(report).decode("utf-8"), report_hash,
                    canonical_bytes(artifact_manifest).decode("utf-8"), manifest_hash,
                    review.dataset_label, now,
                ),
            )
            connection.executemany(
                """INSERT INTO validation_records(
                    record_id,review_id,strategy_id,strategy_version,gate_name,gate_status,
                    score,summary,checks_json,block_reasons_json,evidence_ids_json,
                    rule_version,rule_config_hash,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        f"validation-{uuid4().hex}", review.review_id, review.strategy_id,
                        review.strategy_version, outcome.gate.value, outcome.status.value,
                        outcome.score, outcome.summary,
                        json.dumps(list(outcome.checks), ensure_ascii=False, separators=(",", ":")),
                        json.dumps(list(outcome.block_reasons), ensure_ascii=False, separators=(",", ":")),
                        json.dumps(list(outcome.evidence_ids), ensure_ascii=False, separators=(",", ":")),
                        review.rule_version, review.rule_config_hash, now,
                    )
                    for outcome in review.outcomes
                ],
            )

    def review(self, review_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_review_reports WHERE review_id=?", (review_id,)
            ).fetchone()
            records = connection.execute(
                "SELECT * FROM validation_records WHERE review_id=? ORDER BY rowid",
                (review_id,),
            ).fetchall()
        if row is None:
            raise KeyError(review_id)
        result = dict(row)
        report = json.loads(result.pop("report_json"))
        report_content = {key: value for key, value in report.items() if key != "report_hash"}
        if (
            str(report.get("report_hash")) != result["report_hash"]
            or sha256_json(report_content) != result["report_hash"]
        ):
            raise ImmutableExperimentError("委员会报告Registry哈希不一致")
        result["report"] = report
        manifest = json.loads(result.pop("artifact_manifest_json"))
        manifest_content = {
            key: manifest[key]
            for key in ("schema_version", "experiment_id", "run_id", "artifacts")
        }
        if sha256_json(manifest_content) != result["artifact_manifest_hash"]:
            raise ImmutableExperimentError("策略审查artifact manifest哈希不一致")
        result["artifact_manifest"] = manifest
        result["evidence_ids"] = json.loads(result.pop("evidence_ids_json"))
        result["validation_records"] = [self._decode_validation(dict(item)) for item in records]
        return result

    def list_reviews(self, strategy_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM strategy_review_reports"
        values: tuple[Any, ...] = ()
        if strategy_id:
            query += " WHERE strategy_id=?"
            values = (strategy_id,)
        query += " ORDER BY created_at DESC LIMIT ?"
        values += (max(1, min(int(limit), 200)),)
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._decode_review_summary(dict(row)) for row in rows]

    def create_promotion(
        self,
        *,
        review_id: str,
        requested_by: str,
    ) -> dict[str, Any]:
        review = self.review(review_id)
        target = review.get("recommended_state")
        if not target:
            raise InvalidStateTransitionError("该审查没有可提交的晋级建议")
        promotion_id = f"promotion-{uuid4().hex}"
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO promotion_history(
                        promotion_id,review_id,strategy_id,strategy_version,from_state,to_state,
                        system_recommendation,approval_status,requested_by,requested_at,evidence_hash
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        promotion_id, review_id, review["strategy_id"], review["strategy_version"],
                        review["current_state"], target, review["recommendation"],
                        ApprovalStatus.PENDING.value, requested_by, utc_now(), review["evidence_hash"],
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM promotion_history WHERE review_id=? AND to_state=?",
                    (review_id, target),
                ).fetchone()
                if row is None:
                    raise
                return dict(row)
        return self.promotion(promotion_id)

    def create_retirement(self, *, review_id: str, requested_by: str) -> dict[str, Any]:
        """Create an evidence-linked human retirement request from any active state."""
        review = self.review(review_id)
        if review["current_state"] == StrategyState.RETIRED.value:
            raise InvalidStateTransitionError("策略已经退役")
        current = self.strategy(review["strategy_id"])
        if current["current_state"] != review["current_state"]:
            raise InvalidStateTransitionError("只能基于当前状态的最新审查申请退役")
        promotion_id = f"promotion-{uuid4().hex}"
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO promotion_history(
                        promotion_id,review_id,strategy_id,strategy_version,from_state,to_state,
                        system_recommendation,approval_status,requested_by,requested_at,evidence_hash
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        promotion_id, review_id, review["strategy_id"], review["strategy_version"],
                        review["current_state"], StrategyState.RETIRED.value,
                        "HUMAN_RETIREMENT_REQUEST", ApprovalStatus.PENDING.value,
                        requested_by, utc_now(), review["evidence_hash"],
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM promotion_history WHERE review_id=? AND to_state='RETIRED'",
                    (review_id,),
                ).fetchone()
                if row is None:
                    raise
                return dict(row)
        return self.promotion(promotion_id)

    def decide_promotion(
        self,
        *,
        promotion_id: str,
        status: ApprovalStatus,
        decided_by: str,
        reason: str,
    ) -> dict[str, Any]:
        """Atomically record a human decision and only then update research state."""
        if status not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            raise ValueError("人工决策只能是APPROVED或REJECTED")
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            promotion = connection.execute(
                "SELECT * FROM promotion_history WHERE promotion_id=?", (promotion_id,)
            ).fetchone()
            if promotion is None:
                raise KeyError(promotion_id)
            if promotion["approval_status"] != ApprovalStatus.PENDING.value:
                raise InvalidStateTransitionError("晋级申请已终结，不得重复决策")
            strategy = connection.execute(
                "SELECT current_state FROM strategies WHERE strategy_id=?",
                (promotion["strategy_id"],),
            ).fetchone()
            if strategy is None or strategy["current_state"] != promotion["from_state"]:
                raise InvalidStateTransitionError("策略状态已变化，旧晋级申请作废")
            if status == ApprovalStatus.APPROVED:
                connection.execute(
                    """UPDATE strategies SET current_state=?,active_version=?,updated_at=?,
                        retired_at=CASE WHEN ?='RETIRED' THEN ? ELSE retired_at END
                       WHERE strategy_id=?""",
                    (
                        promotion["to_state"], promotion["strategy_version"], now,
                        promotion["to_state"], now, promotion["strategy_id"],
                    ),
                )
            connection.execute(
                """UPDATE promotion_history SET approval_status=?,decided_by=?,
                    decision_reason=?,decided_at=? WHERE promotion_id=?""",
                (status.value, decided_by, reason, now, promotion_id),
            )
        return self.promotion(promotion_id)

    def promotion(self, promotion_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM promotion_history WHERE promotion_id=?", (promotion_id,)
            ).fetchone()
        if row is None:
            raise KeyError(promotion_id)
        return dict(row)

    def list_promotions(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM promotion_history"
        values: tuple[Any, ...] = ()
        if status:
            ApprovalStatus(status)
            query += " WHERE approval_status=?"
            values = (status,)
        query += " ORDER BY requested_at DESC LIMIT ?"
        values += (max(1, min(int(limit), 200)),)
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _decode_validation(row: dict[str, Any]) -> dict[str, Any]:
        for key in ("checks_json", "block_reasons_json", "evidence_ids_json"):
            row[key.removesuffix("_json")] = json.loads(row.pop(key))
        return row

    @staticmethod
    def _decode_review_summary(row: dict[str, Any]) -> dict[str, Any]:
        row.pop("report_json", None)
        row.pop("artifact_manifest_json", None)
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        return row
