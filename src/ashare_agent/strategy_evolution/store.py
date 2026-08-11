from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from ..quant_lab.contracts import canonical_bytes, sha256_json, utc_now
from .contracts import (
    EvolutionArtifactError,
    EvolutionStateError,
    StrategyBranchSpec,
)


_LIFECYCLE = (
    "DRAFT", "RESEARCH", "VALIDATED", "PAPER_RUNNING",
    "PRODUCTION_CANDIDATE", "DEPRECATED", "RETIRED",
)


class StrategyEvolutionStore:
    """Persist strategy evolution evidence separately from research and trading stores."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """Open one bounded SQLite connection with foreign keys enabled."""
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _initialize(self) -> None:
        """Create governance tables with database-level zero-capability constraints."""
        states = ",".join(f"'{value}'" for value in _LIFECYCLE)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(f"""
                CREATE TABLE IF NOT EXISTS strategy_registry (
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    branch_name TEXT NOT NULL,
                    parent_version TEXT,
                    lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN ({states})),
                    spec_hash TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    version_hash TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    parameter_hash TEXT NOT NULL,
                    factor_version TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    dataset_label TEXT NOT NULL CHECK(dataset_label IN ('POINT_IN_TIME','SYNTHETIC_TEST_ONLY')),
                    validation_state TEXT NOT NULL,
                    validation_review_id TEXT,
                    evidence_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    immutable INTEGER NOT NULL DEFAULT 1 CHECK(immutable=1),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0),
                    can_auto_transition INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_transition=0),
                    PRIMARY KEY(strategy_id,version)
                );
                CREATE TABLE IF NOT EXISTS strategy_health (
                    health_id TEXT PRIMARY KEY,
                    evaluation_id TEXT NOT NULL UNIQUE,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    score REAL,
                    partial_score REAL NOT NULL CHECK(partial_score>=0 AND partial_score<=100),
                    coverage REAL NOT NULL CHECK(coverage>=0 AND coverage<=1),
                    status TEXT NOT NULL CHECK(status IN ('HEALTHY','WATCH','DECAYING','CRITICAL','INSUFFICIENT_EVIDENCE')),
                    evidence_hash TEXT NOT NULL,
                    health_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    artifact_manifest_json TEXT NOT NULL,
                    artifact_manifest_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0),
                    can_modify_parameters INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_parameters=0),
                    can_modify_factor_weights INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_factor_weights=0),
                    can_auto_transition INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_transition=0),
                    FOREIGN KEY(strategy_id,strategy_version)
                        REFERENCES strategy_registry(strategy_id,version)
                );
                CREATE TABLE IF NOT EXISTS factor_drift (
                    health_id TEXT NOT NULL,
                    factor_name TEXT NOT NULL CHECK(factor_name IN ('value','quality','growth','momentum','trend','low_risk','liquidity')),
                    status TEXT NOT NULL CHECK(status IN ('STABLE','WATCH','DECAYING','UNAVAILABLE','BASELINE_BUILDING')),
                    drift_score REAL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    can_modify_weight INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_weight=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    PRIMARY KEY(health_id,factor_name),
                    FOREIGN KEY(health_id) REFERENCES strategy_health(health_id)
                );
                CREATE TABLE IF NOT EXISTS evolution_reports (
                    report_id TEXT PRIMARY KEY,
                    health_id TEXT NOT NULL UNIQUE,
                    report_hash TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    ai_can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(ai_can_modify_strategy=0),
                    ai_can_launch_experiment INTEGER NOT NULL DEFAULT 0 CHECK(ai_can_launch_experiment=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    FOREIGN KEY(health_id) REFERENCES strategy_health(health_id)
                );
                CREATE TABLE IF NOT EXISTS strategy_comparisons (
                    comparison_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    version_a TEXT NOT NULL,
                    version_b TEXT NOT NULL,
                    comparison_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    automatic_winner_selected INTEGER NOT NULL DEFAULT 0 CHECK(automatic_winner_selected=0),
                    can_replace_production_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_replace_production_strategy=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_requests (
                    request_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    from_state TEXT NOT NULL CHECK(from_state IN ({states})),
                    to_state TEXT NOT NULL CHECK(to_state IN ({states})),
                    status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
                    requested_by TEXT NOT NULL,
                    request_reason TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    decided_by TEXT,
                    decision_reason TEXT,
                    decided_at TEXT,
                    human_approval_required INTEGER NOT NULL DEFAULT 1 CHECK(human_approval_required=1),
                    ai_can_approve INTEGER NOT NULL DEFAULT 0 CHECK(ai_can_approve=0),
                    can_auto_transition INTEGER NOT NULL DEFAULT 0 CHECK(can_auto_transition=0),
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    UNIQUE(strategy_id,strategy_version,from_state,to_state,status),
                    FOREIGN KEY(strategy_id,strategy_version)
                        REFERENCES strategy_registry(strategy_id,version)
                );
                CREATE INDEX IF NOT EXISTS idx_evolution_health
                    ON strategy_health(strategy_id,strategy_version,observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_evolution_lifecycle
                    ON lifecycle_requests(status,requested_at DESC);
            """)

    def register_branch(self, spec: StrategyBranchSpec) -> dict[str, Any]:
        """Register an immutable branch idempotently and reject identity redefinition."""
        spec_json = canonical_bytes(asdict(spec)).decode("utf-8")
        now = utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT spec_hash FROM strategy_registry WHERE strategy_id=? AND version=?",
                (spec.strategy_id, spec.version),
            ).fetchone()
            if row is not None:
                if row["spec_hash"] != spec.spec_hash:
                    raise EvolutionArtifactError("同一策略版本不得重新定义分支身份")
                return self.branch(spec.strategy_id, spec.version)
            connection.execute(
                """INSERT INTO strategy_registry(
                    strategy_id,version,strategy_name,branch_name,parent_version,
                    lifecycle_state,spec_hash,spec_json,version_hash,code_hash,
                    parameter_hash,factor_version,data_version,dataset_label,
                    validation_state,validation_review_id,evidence_ids_json,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,'DRAFT',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    spec.strategy_id, spec.version, spec.strategy_name, spec.branch_name,
                    spec.parent_version, spec.spec_hash, spec_json, spec.version_hash,
                    spec.code_hash, spec.parameter_hash, spec.factor_version,
                    spec.data_version, spec.dataset_label, spec.validation_state,
                    spec.validation_review_id,
                    json.dumps(list(spec.evidence_ids), ensure_ascii=False, separators=(",", ":")),
                    spec.created_at, now,
                ),
            )
        return self.branch(spec.strategy_id, spec.version)

    def branch(self, strategy_id: str, version: str) -> dict[str, Any]:
        """Return one hash-verified immutable branch."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_registry WHERE strategy_id=? AND version=?",
                (strategy_id, version),
            ).fetchone()
        if row is None:
            raise KeyError((strategy_id, version))
        result = dict(row)
        spec = json.loads(result.pop("spec_json"))
        if sha256_json(spec) != result["spec_hash"]:
            raise EvolutionArtifactError("策略分支Registry哈希不一致")
        result["spec"] = spec
        result["evidence_ids"] = json.loads(result.pop("evidence_ids_json"))
        return result

    def branches(self) -> list[dict[str, Any]]:
        """List registered strategy versions without executable code content."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT strategy_id,version FROM strategy_registry ORDER BY updated_at DESC"
            ).fetchall()
        return [self.branch(row["strategy_id"], row["version"]) for row in rows]

    def save_evaluation(
        self,
        health: Mapping[str, Any],
        report: Mapping[str, Any],
        artifact_manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist one immutable health snapshot, seven factor rows and report."""
        health_content = {key: value for key, value in health.items() if key != "health_hash"}
        if sha256_json(health_content) != health.get("health_hash"):
            raise EvolutionArtifactError("Strategy Health payload哈希无效")
        report_content = {key: value for key, value in report.items() if key != "report_hash"}
        if sha256_json(report_content) != report.get("report_hash"):
            raise EvolutionArtifactError("Strategy Health Report哈希无效")
        manifest_content = {
            key: artifact_manifest.get(key)
            for key in ("schema_version", "strategy_id", "evaluation_id", "created_at", "artifacts")
        }
        if sha256_json(manifest_content) != artifact_manifest.get("manifest_hash"):
            raise EvolutionArtifactError("策略进化artifact manifest哈希无效")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT health_hash FROM strategy_health WHERE health_id=?",
                (health["health_id"],),
            ).fetchone()
            if existing is not None:
                if existing["health_hash"] != health["health_hash"]:
                    raise EvolutionArtifactError("同一health_id内容发生变化")
                return self.health(str(health["health_id"]))
            connection.execute(
                """INSERT INTO strategy_health(
                    health_id,evaluation_id,strategy_id,strategy_version,review_id,
                    observed_at,score,partial_score,coverage,status,evidence_hash,
                    health_hash,payload_json,artifact_manifest_json,
                    artifact_manifest_hash,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    health["health_id"], health["evaluation_id"], health["strategy_id"],
                    health["strategy_version"], health["review_id"], health["observed_at"],
                    health.get("score"), health["partial_score"], health["coverage"],
                    health["status"], health["evidence_hash"], health["health_hash"],
                    canonical_bytes(health).decode("utf-8"),
                    canonical_bytes(artifact_manifest).decode("utf-8"),
                    artifact_manifest["manifest_hash"], health["created_at"],
                ),
            )
            rows = (health.get("factor_drift") or {}).get("factors") or []
            connection.executemany(
                """INSERT INTO factor_drift(
                    health_id,factor_name,status,drift_score,payload_json,created_at
                ) VALUES(?,?,?,?,?,?)""",
                [(
                    health["health_id"], item["factor_name"], item["status"],
                    item.get("drift_score"), canonical_bytes(item).decode("utf-8"),
                    health["created_at"],
                ) for item in rows],
            )
            connection.execute(
                """INSERT INTO evolution_reports(
                    report_id,health_id,report_hash,report_json,created_at
                ) VALUES(?,?,?,?,?)""",
                (
                    report["report_id"], health["health_id"], report["report_hash"],
                    canonical_bytes(report).decode("utf-8"), report["generated_at"],
                ),
            )
        return self.health(str(health["health_id"]))

    def health(self, health_id: str) -> dict[str, Any]:
        """Return one hash-verified health payload and artifact manifest."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_health WHERE health_id=?", (health_id,)
            ).fetchone()
        if row is None:
            raise KeyError(health_id)
        stored = dict(row)
        payload = json.loads(stored["payload_json"])
        content = {key: value for key, value in payload.items() if key != "health_hash"}
        if sha256_json(content) != stored["health_hash"] or payload.get("health_hash") != stored["health_hash"]:
            raise EvolutionArtifactError("Strategy Health Registry哈希不一致")
        manifest = json.loads(stored["artifact_manifest_json"])
        if manifest.get("manifest_hash") != stored["artifact_manifest_hash"]:
            raise EvolutionArtifactError("Strategy Health manifest引用不一致")
        payload["artifact_manifest"] = manifest
        return payload

    def latest_health(self, strategy_id: str, version: str) -> dict[str, Any] | None:
        """Read the latest immutable observation for one strategy version."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT health_id FROM strategy_health
                   WHERE strategy_id=? AND strategy_version=?
                   ORDER BY observed_at DESC,created_at DESC LIMIT 1""",
                (strategy_id, version),
            ).fetchone()
        return self.health(row["health_id"]) if row else None

    def health_items(self, strategy_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """List bounded health history while verifying every payload hash."""
        query = "SELECT health_id FROM strategy_health"
        values: tuple[Any, ...] = ()
        if strategy_id:
            query += " WHERE strategy_id=?"
            values = (strategy_id,)
        query += " ORDER BY observed_at DESC,created_at DESC LIMIT ?"
        values += (max(1, min(int(limit), 300)),)
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self.health(row["health_id"]) for row in rows]

    def report(self, report_id: str) -> dict[str, Any]:
        """Return one immutable report after checking its content hash."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM evolution_reports WHERE report_id=?", (report_id,)
            ).fetchone()
        if row is None:
            raise KeyError(report_id)
        report = json.loads(row["report_json"])
        content = {key: value for key, value in report.items() if key != "report_hash"}
        if sha256_json(content) != row["report_hash"] or report.get("report_hash") != row["report_hash"]:
            raise EvolutionArtifactError("Strategy Health Report Registry哈希不一致")
        return report

    def reports(self, limit: int = 100) -> list[dict[str, Any]]:
        """List bounded report summaries without loading model output."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT report_id,health_id,report_hash,created_at
                   FROM evolution_reports ORDER BY created_at DESC LIMIT ?""",
                (max(1, min(int(limit), 300)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_comparison(self, comparison: Mapping[str, Any]) -> dict[str, Any]:
        """Persist a deterministic comparison and prohibit automatic winner selection."""
        content = {key: value for key, value in comparison.items() if key != "comparison_hash"}
        if sha256_json(content) != comparison.get("comparison_hash"):
            raise EvolutionArtifactError("策略比较哈希无效")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT comparison_hash FROM strategy_comparisons WHERE comparison_id=?",
                (comparison["comparison_id"],),
            ).fetchone()
            if existing is not None:
                if existing["comparison_hash"] != comparison["comparison_hash"]:
                    raise EvolutionArtifactError("同一comparison_id内容发生变化")
                return self.comparison(str(comparison["comparison_id"]))
            connection.execute(
                """INSERT INTO strategy_comparisons(
                    comparison_id,strategy_id,version_a,version_b,comparison_hash,
                    payload_json,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    comparison["comparison_id"], comparison["strategy_id"],
                    comparison["version_a"], comparison["version_b"],
                    comparison["comparison_hash"],
                    canonical_bytes(comparison).decode("utf-8"), comparison["created_at"],
                ),
            )
        return self.comparison(str(comparison["comparison_id"]))

    def comparison(self, comparison_id: str) -> dict[str, Any]:
        """Return one hash-verified version comparison."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json,comparison_hash FROM strategy_comparisons WHERE comparison_id=?",
                (comparison_id,),
            ).fetchone()
        if row is None:
            raise KeyError(comparison_id)
        payload = json.loads(row["payload_json"])
        content = {key: value for key, value in payload.items() if key != "comparison_hash"}
        if sha256_json(content) != row["comparison_hash"]:
            raise EvolutionArtifactError("策略比较Registry哈希不一致")
        return payload

    def comparisons(self, limit: int = 100) -> list[dict[str, Any]]:
        """List recent comparison evidence."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT comparison_id FROM strategy_comparisons ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 300)),),
            ).fetchall()
        return [self.comparison(row["comparison_id"]) for row in rows]

    def create_transition(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Create one pending human lifecycle request without changing state."""
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO lifecycle_requests(
                        request_id,strategy_id,strategy_version,from_state,to_state,status,
                        requested_by,request_reason,evidence_ids_json,evidence_hash,requested_at
                    ) VALUES(?,?,?,?,?,'PENDING',?,?,?,?,?)""",
                    (
                        payload["request_id"], payload["strategy_id"], payload["strategy_version"],
                        payload["from_state"], payload["to_state"], payload["requested_by"],
                        payload["request_reason"],
                        json.dumps(payload["evidence_ids"], ensure_ascii=False, separators=(",", ":")),
                        payload["evidence_hash"], payload["requested_at"],
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT request_id FROM lifecycle_requests WHERE
                       strategy_id=? AND strategy_version=? AND from_state=? AND to_state=?
                       AND status='PENDING'""",
                    (
                        payload["strategy_id"], payload["strategy_version"],
                        payload["from_state"], payload["to_state"],
                    ),
                ).fetchone()
                if row is None:
                    raise
                return self.transition(row["request_id"])
        return self.transition(str(payload["request_id"]))

    def decide_transition(
        self,
        request_id: str,
        *,
        status: str,
        decided_by: str,
        decision_reason: str,
    ) -> dict[str, Any]:
        """Atomically record a human decision and update only this research lifecycle."""
        if status not in {"APPROVED", "REJECTED"}:
            raise EvolutionStateError("生命周期决定只能是APPROVED或REJECTED")
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                "SELECT * FROM lifecycle_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if request is None:
                raise KeyError(request_id)
            if request["status"] != "PENDING":
                raise EvolutionStateError("生命周期申请已终结")
            branch = connection.execute(
                """SELECT lifecycle_state FROM strategy_registry
                   WHERE strategy_id=? AND version=?""",
                (request["strategy_id"], request["strategy_version"]),
            ).fetchone()
            if branch is None or branch["lifecycle_state"] != request["from_state"]:
                raise EvolutionStateError("策略生命周期已变化，旧申请失效")
            if status == "APPROVED":
                connection.execute(
                    """UPDATE strategy_registry SET lifecycle_state=?,updated_at=?
                       WHERE strategy_id=? AND version=?""",
                    (
                        request["to_state"], now, request["strategy_id"],
                        request["strategy_version"],
                    ),
                )
            connection.execute(
                """UPDATE lifecycle_requests SET status=?,decided_by=?,decision_reason=?,
                   decided_at=? WHERE request_id=?""",
                (status, decided_by, decision_reason, now, request_id),
            )
        return self.transition(request_id)

    def transition(self, request_id: str) -> dict[str, Any]:
        """Return one lifecycle audit row with decoded evidence references."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lifecycle_requests WHERE request_id=?", (request_id,)
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        result = dict(row)
        result["evidence_ids"] = json.loads(result.pop("evidence_ids_json"))
        return result

    def transitions(self, limit: int = 100) -> list[dict[str, Any]]:
        """List recent lifecycle audit records."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT request_id FROM lifecycle_requests ORDER BY requested_at DESC LIMIT ?",
                (max(1, min(int(limit), 300)),),
            ).fetchall()
        return [self.transition(row["request_id"]) for row in rows]

