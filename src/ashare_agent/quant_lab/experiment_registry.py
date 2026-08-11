from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import sqlite3
from typing import Any
from uuid import uuid4

from .contracts import (
    ExperimentSpec,
    ExperimentState,
    StrategyLifecycleState,
    canonical_bytes,
    sha256_json,
    utc_now,
)
from .exceptions import ImmutableExperimentError, InvalidStateTransitionError


_TRANSITIONS = {
    ExperimentState.CREATED: {ExperimentState.VALIDATING, ExperimentState.FAILED},
    ExperimentState.VALIDATING: {
        ExperimentState.DATA_BLOCKED,
        ExperimentState.RUNNING,
        ExperimentState.FAILED,
    },
    ExperimentState.RUNNING: {ExperimentState.COMPLETED, ExperimentState.FAILED},
    ExperimentState.DATA_BLOCKED: set(),
    ExperimentState.FAILED: set(),
    ExperimentState.COMPLETED: set(),
}


class ExperimentRegistry:
    """Own SQLite lifecycle, audit and immutability for Quant Lab experiments."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """Open a constrained WAL connection with typed row access."""
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _initialize(self) -> None:
        """Create experiment metadata, runs, artifacts and validation audit tables."""
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    spec_hash TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    strategy_state TEXT NOT NULL CHECK(strategy_state IN ('DRAFT','DATA_VERIFIED')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiment_runs (
                    run_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('CREATED','VALIDATING','DATA_BLOCKED','RUNNING','FAILED','COMPLETED')),
                    trigger TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    result_hash TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
                );
                CREATE TABLE IF NOT EXISTS experiment_artifacts (
                    run_id TEXT NOT NULL,
                    artifact_name TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                    media_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, artifact_name),
                    FOREIGN KEY(run_id) REFERENCES experiment_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS dataset_validations (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    validation_hash TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES experiment_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS validation_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_run_id TEXT,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES experiment_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS factor_experiments (
                    run_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    factor_version TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    horizons_json TEXT NOT NULL,
                    metrics_hash TEXT NOT NULL,
                    dataset_version TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    dataset_label TEXT NOT NULL CHECK(dataset_label IN ('POINT_IN_TIME','SYNTHETIC_TEST_ONLY')),
                    created_at TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    FOREIGN KEY(run_id) REFERENCES experiment_runs(run_id),
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
                );
                CREATE TABLE IF NOT EXISTS factor_metrics (
                    run_id TEXT NOT NULL,
                    factor_name TEXT NOT NULL,
                    horizon INTEGER NOT NULL CHECK(horizon > 0),
                    period TEXT NOT NULL,
                    ic_mean REAL,
                    ic_std REAL,
                    icir REAL,
                    ic_hit_rate REAL,
                    t_stat REAL,
                    p_value REAL,
                    annual_return REAL,
                    annual_volatility REAL,
                    max_drawdown REAL,
                    turnover REAL,
                    long_short_return REAL,
                    sharpe REAL,
                    redundancy_score REAL,
                    observation_count INTEGER NOT NULL CHECK(observation_count >= 0),
                    created_at TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    PRIMARY KEY(run_id, factor_name, horizon),
                    FOREIGN KEY(run_id) REFERENCES factor_experiments(run_id)
                );
                CREATE TABLE IF NOT EXISTS factor_reports (
                    report_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    experiment_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    report_hash TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    FOREIGN KEY(run_id) REFERENCES factor_experiments(run_id),
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
                );
                CREATE TABLE IF NOT EXISTS robustness_experiments (
                    run_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    metrics_hash TEXT NOT NULL,
                    dataset_version TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    dataset_label TEXT NOT NULL CHECK(dataset_label IN ('POINT_IN_TIME','SYNTHETIC_TEST_ONLY')),
                    created_at TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0),
                    can_promote_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_promote_strategy=0),
                    FOREIGN KEY(run_id) REFERENCES experiment_runs(run_id),
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
                );
                CREATE TABLE IF NOT EXISTS robustness_metrics (
                    run_id TEXT NOT NULL,
                    metric_group TEXT NOT NULL,
                    scenario_id TEXT NOT NULL,
                    availability TEXT NOT NULL CHECK(availability IN ('AVAILABLE','PARTIAL','UNAVAILABLE')),
                    annual_return REAL,
                    max_drawdown REAL,
                    sharpe REAL,
                    cost_impact REAL,
                    stability_score REAL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0),
                    can_promote_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_promote_strategy=0),
                    PRIMARY KEY(run_id, metric_group, scenario_id),
                    FOREIGN KEY(run_id) REFERENCES robustness_experiments(run_id)
                );
                CREATE TABLE IF NOT EXISTS robustness_reports (
                    report_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    experiment_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    report_hash TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    can_trade INTEGER NOT NULL DEFAULT 0 CHECK(can_trade=0),
                    can_create_orders INTEGER NOT NULL DEFAULT 0 CHECK(can_create_orders=0),
                    can_modify_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_modify_strategy=0),
                    can_promote_strategy INTEGER NOT NULL DEFAULT 0 CHECK(can_promote_strategy=0),
                    FOREIGN KEY(run_id) REFERENCES robustness_experiments(run_id),
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
                );
                CREATE INDEX IF NOT EXISTS idx_experiment_runs_experiment
                    ON experiment_runs(experiment_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_validation_events_run
                    ON validation_events(run_id, event_id);
                CREATE INDEX IF NOT EXISTS idx_factor_metrics_factor
                    ON factor_metrics(factor_name, horizon, run_id);
                CREATE INDEX IF NOT EXISTS idx_robustness_metrics_group
                    ON robustness_metrics(metric_group, scenario_id, run_id);
                """
            )
            self._ensure_columns(
                connection,
                "factor_metrics",
                {
                    "annual_return": "REAL",
                    "annual_volatility": "REAL",
                    "max_drawdown": "REAL",
                    "turnover": "REAL",
                    "long_short_return": "REAL",
                    "sharpe": "REAL",
                    "redundancy_score": "REAL",
                },
            )

    @staticmethod
    def _ensure_columns(
        connection: sqlite3.Connection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        """Apply additive SQLite migrations for older local Quant Lab registries."""
        existing = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def register(self, spec: ExperimentSpec) -> None:
        """Register one immutable experiment definition or verify an identical replay."""
        payload = canonical_bytes(asdict(spec)).decode("utf-8")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT spec_hash FROM experiments WHERE experiment_id=?", (spec.experiment_id,)
            ).fetchone()
            if existing and existing["spec_hash"] != spec.spec_hash:
                raise ImmutableExperimentError("experiment_id已绑定不同ExperimentSpec")
            connection.execute(
                """INSERT OR IGNORE INTO experiments(
                    experiment_id,name,spec_hash,spec_json,strategy_state,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    spec.experiment_id,
                    spec.name,
                    spec.spec_hash,
                    payload,
                    StrategyLifecycleState.DRAFT.value,
                    utc_now(),
                ),
            )

    def create_run(self, experiment_id: str, *, trigger: str = "user_action") -> str:
        """Create a new run for the same fixed specification without overwriting history."""
        run_id = f"qlrun-{uuid4().hex}"
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM experiments WHERE experiment_id=?", (experiment_id,)
            ).fetchone() is None:
                raise KeyError(f"未注册实验：{experiment_id}")
            connection.execute(
                "INSERT INTO experiment_runs(run_id,experiment_id,state,trigger,created_at) VALUES(?,?,?,?,?)",
                (run_id, experiment_id, ExperimentState.CREATED.value, trigger, utc_now()),
            )
        return run_id

    def transition(
        self,
        run_id: str,
        state: ExperimentState,
        *,
        result_hash: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Move one run through the explicit state machine and freeze terminal states."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM experiment_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"未知实验run_id：{run_id}")
            current = ExperimentState(row["state"])
            if current in {ExperimentState.COMPLETED, ExperimentState.FAILED, ExperimentState.DATA_BLOCKED}:
                raise ImmutableExperimentError(f"终态实验不可修改：{current.value}")
            if state not in _TRANSITIONS[current]:
                raise InvalidStateTransitionError(f"非法实验状态迁移：{current.value}->{state.value}")
            now = utc_now()
            started_at = now if state == ExperimentState.RUNNING else None
            completed_at = now if state in {
                ExperimentState.COMPLETED,
                ExperimentState.FAILED,
                ExperimentState.DATA_BLOCKED,
            } else None
            connection.execute(
                """UPDATE experiment_runs SET state=?,started_at=COALESCE(started_at,?),
                    completed_at=?,result_hash=?,error_code=?,error_message=? WHERE run_id=?""",
                (state.value, started_at, completed_at, result_hash, error_code,
                 (error_message or "")[:1000] or None, run_id),
            )
            if state == ExperimentState.COMPLETED:
                connection.execute(
                    """UPDATE experiments SET strategy_state='DATA_VERIFIED'
                    WHERE experiment_id=(SELECT experiment_id FROM experiment_runs WHERE run_id=?)""",
                    (run_id,),
                )

    def save_validation(self, run_id: str, validation: Any) -> None:
        """Persist the validation summary and all individual quality events atomically."""
        payload = json.dumps(asdict(validation), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            state = self._state(connection, run_id)
            if state not in {ExperimentState.VALIDATING}:
                raise InvalidStateTransitionError("只有VALIDATING状态可以保存数据验证")
            connection.execute(
                "INSERT INTO dataset_validations VALUES(?,?,?,?,?)",
                (run_id, validation.status.value, validation.validation_hash, payload, validation.checked_at),
            )
            connection.executemany(
                """INSERT INTO validation_events(
                    run_id,code,status,source_run_id,message,details_json,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, event.code, event.status.value, event.run_id, event.message,
                        json.dumps(dict(event.details), ensure_ascii=False, sort_keys=True),
                        validation.checked_at,
                    )
                    for event in validation.events
                ],
            )

    def save_artifacts(self, run_id: str, artifacts: list[dict[str, Any]]) -> None:
        """Index immutable artifacts before a run enters COMPLETED."""
        with self._connect() as connection:
            if self._state(connection, run_id) != ExperimentState.RUNNING:
                raise ImmutableExperimentError("只有RUNNING实验可以登记artifact")
            connection.executemany(
                """INSERT INTO experiment_artifacts(
                    run_id,artifact_name,relative_path,sha256,size_bytes,media_type,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                [
                    (run_id, item["name"], item["path"], item["sha256"], int(item["size_bytes"]),
                     item["media_type"], utc_now())
                    for item in artifacts
                ],
            )

    def save_factor_research(
        self,
        run_id: str,
        *,
        metadata: dict[str, Any],
        report: dict[str, Any],
        factor_metrics: list[dict[str, Any]],
    ) -> None:
        """Persist queryable factor summaries while immutable artifacts remain authoritative."""
        if report.get("can_trade") or report.get("can_create_orders"):
            raise ValueError("因子报告不得拥有交易能力")
        report_hash = sha256_json(report)
        with self._connect() as connection:
            if self._state(connection, run_id) != ExperimentState.RUNNING:
                raise ImmutableExperimentError("只有RUNNING实验可以保存因子研究结果")
            run = connection.execute(
                "SELECT experiment_id FROM experiment_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None or str(run["experiment_id"]) != str(metadata["experiment_id"]):
                raise ValueError("因子研究experiment_id与Registry run不一致")
            now = utc_now()
            connection.execute(
                """INSERT INTO factor_experiments(
                    run_id,experiment_id,factor_version,period_start,period_end,horizons_json,
                    metrics_hash,dataset_version,strategy_version,dataset_label,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, metadata["experiment_id"], metadata["factor_version"],
                    metadata["period_start"], metadata["period_end"],
                    json.dumps(metadata["horizons"], separators=(",", ":")),
                    metadata["metrics_hash"], metadata["dataset_version"],
                    metadata["strategy_version"], metadata["dataset_label"], now,
                ),
            )
            connection.executemany(
                """INSERT INTO factor_metrics(
                    run_id,factor_name,horizon,period,ic_mean,ic_std,icir,ic_hit_rate,
                    t_stat,p_value,annual_return,annual_volatility,max_drawdown,turnover,
                    long_short_return,sharpe,redundancy_score,observation_count,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, item["factor_name"], int(item["horizon"]), item["period"],
                        item["ic_mean"], item["ic_std"], item["icir"], item["ic_hit_rate"],
                        item["t_stat"], item["p_value"], item["annual_return"],
                        item["annual_volatility"], item["max_drawdown"], item["turnover"],
                        item["long_short_return"], item["sharpe"], item["redundancy_score"],
                        int(item["observation_count"]), now,
                    )
                    for item in factor_metrics
                ],
            )
            connection.execute(
                """INSERT INTO factor_reports(
                    report_id,run_id,experiment_id,report_json,report_hash,evidence_ids_json,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    f"factor-report-{run_id}", run_id, metadata["experiment_id"],
                    canonical_bytes(report).decode("utf-8"), report_hash,
                    json.dumps(report.get("evidence_ids") or [], separators=(",", ":")), now,
                ),
            )

    def factor_report(self, run_id: str) -> dict[str, Any]:
        """Read one registered factor report and its integrity identity."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report_json,report_hash FROM factor_reports WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        payload = json.loads(row["report_json"])
        if sha256_json(payload) != row["report_hash"]:
            raise ImmutableExperimentError("因子报告Registry哈希不一致")
        return payload

    def save_robustness(
        self,
        run_id: str,
        *,
        metadata: dict[str, Any],
        report: dict[str, Any],
        robustness_metrics: list[dict[str, Any]],
    ) -> None:
        """Persist queryable robustness summaries with database-level zero capabilities."""
        forbidden = (
            report.get("can_trade"), report.get("can_create_orders"),
            report.get("can_modify_strategy"), report.get("can_promote_strategy"),
        )
        if any(forbidden):
            raise ValueError("稳健性报告不得拥有交易、修改或晋级能力")
        report_hash = sha256_json(report)
        with self._connect() as connection:
            if self._state(connection, run_id) != ExperimentState.RUNNING:
                raise ImmutableExperimentError("只有RUNNING实验可以保存稳健性结果")
            run = connection.execute(
                "SELECT experiment_id FROM experiment_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None or str(run["experiment_id"]) != str(metadata["experiment_id"]):
                raise ValueError("稳健性experiment_id与Registry run不一致")
            now = utc_now()
            connection.execute(
                """INSERT INTO robustness_experiments(
                    run_id,experiment_id,period_start,period_end,config_hash,metrics_hash,
                    dataset_version,strategy_version,dataset_label,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, metadata["experiment_id"], metadata["period_start"],
                    metadata["period_end"], metadata["config_hash"], metadata["metrics_hash"],
                    metadata["dataset_version"], metadata["strategy_version"],
                    metadata["dataset_label"], now,
                ),
            )
            connection.executemany(
                """INSERT INTO robustness_metrics(
                    run_id,metric_group,scenario_id,availability,annual_return,max_drawdown,
                    sharpe,cost_impact,stability_score,payload_hash,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, item["metric_group"], item["scenario_id"],
                        item["availability"], item["annual_return"], item["max_drawdown"],
                        item["sharpe"], item["cost_impact"], item["stability_score"],
                        item["payload_hash"], now,
                    )
                    for item in robustness_metrics
                ],
            )
            connection.execute(
                """INSERT INTO robustness_reports(
                    report_id,run_id,experiment_id,report_json,report_hash,evidence_ids_json,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    f"robustness-report-{run_id}", run_id, metadata["experiment_id"],
                    canonical_bytes(report).decode("utf-8"), report_hash,
                    json.dumps(report.get("evidence_ids") or [], separators=(",", ":")), now,
                ),
            )

    def robustness_report(self, run_id: str) -> dict[str, Any]:
        """Read and verify one registered strategy robustness report."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report_json,report_hash FROM robustness_reports WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        payload = json.loads(row["report_json"])
        if sha256_json(payload) != row["report_hash"]:
            raise ImmutableExperimentError("稳健性报告Registry哈希不一致")
        return payload

    def run(self, run_id: str) -> dict[str, Any]:
        """Return one run and its immutable registry metadata for audit."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiment_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            return dict(row)

    @staticmethod
    def _state(connection: sqlite3.Connection, run_id: str) -> ExperimentState:
        """Read a run state or fail if the registry identity is unknown."""
        row = connection.execute(
            "SELECT state FROM experiment_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return ExperimentState(row["state"])
