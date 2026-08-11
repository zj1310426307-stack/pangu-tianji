from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping


PORTFOLIO_RISK_STORE_VERSION = "portfolio-risk-store-v1.0.0"


class PortfolioRiskStoreError(RuntimeError):
    """Reject incomplete or unsafe portfolio evidence before SQLite persistence."""


class PortfolioRiskStore:
    """Persist portfolio, profile and risk evidence without owning trading state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        """Open one short-lived transactional connection with integrity checks enabled."""
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize_schema(self) -> None:
        """Create the V2 portfolio catalog without touching the paper-account database."""
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS investment_profiles (
                    profile_id TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    capital REAL NOT NULL CHECK(capital > 0),
                    risk_level TEXT NOT NULL,
                    investment_horizon TEXT NOT NULL,
                    max_drawdown_tolerance REAL NOT NULL,
                    investment_style TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolios (
                    portfolio_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    service_version TEXT NOT NULL,
                    positioning_model_version TEXT NOT NULL,
                    target_exposure REAL NOT NULL,
                    cash_weight REAL NOT NULL,
                    execution_authorized INTEGER NOT NULL CHECK(execution_authorized = 0),
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, profile_id),
                    FOREIGN KEY(profile_id) REFERENCES investment_profiles(profile_id)
                );
                CREATE TABLE IF NOT EXISTS portfolio_positions (
                    portfolio_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    industry TEXT,
                    target_weight REAL NOT NULL,
                    current_weight REAL,
                    cost REAL,
                    market_value REAL,
                    target_value REAL NOT NULL,
                    round_lot_quantity INTEGER NOT NULL,
                    research_score REAL NOT NULL,
                    security_risk_score REAL,
                    base_weight REAL NOT NULL,
                    score_coefficient REAL NOT NULL,
                    risk_coefficient REAL NOT NULL,
                    style_coefficient REAL NOT NULL,
                    PRIMARY KEY(portfolio_id, symbol),
                    FOREIGN KEY(portfolio_id) REFERENCES portfolios(portfolio_id)
                );
                CREATE TABLE IF NOT EXISTS risk_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    portfolio_id TEXT NOT NULL,
                    snapshot_type TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    valuation_service_version TEXT,
                    current_drawdown REAL,
                    max_drawdown REAL,
                    portfolio_volatility REAL NOT NULL,
                    predicted_max_drawdown REAL NOT NULL,
                    weighted_security_risk REAL NOT NULL,
                    industry_exposure_json TEXT NOT NULL,
                    style_exposure_json TEXT NOT NULL,
                    size_exposure_json TEXT NOT NULL,
                    cycle_exposure_json TEXT NOT NULL,
                    concentration_json TEXT NOT NULL,
                    risk_flags_json TEXT NOT NULL,
                    FOREIGN KEY(portfolio_id) REFERENCES portfolios(portfolio_id)
                );
                CREATE TABLE IF NOT EXISTS exit_signals (
                    snapshot_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    available_quantity INTEGER NOT NULL,
                    creates_order INTEGER NOT NULL CHECK(creates_order = 0),
                    PRIMARY KEY(snapshot_id, symbol),
                    FOREIGN KEY(snapshot_id) REFERENCES risk_snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_portfolios_run_id ON portfolios(run_id);
                CREATE INDEX IF NOT EXISTS idx_risk_snapshots_portfolio_time
                    ON risk_snapshots(portfolio_id, observed_at);
                """
            )

    def save_research_snapshot(
        self,
        target_portfolio: Mapping[str, Any],
        risk_assessment: Mapping[str, Any],
        *,
        name: str = "盘古天机目标组合",
    ) -> dict[str, str]:
        """Atomically save one research-owned target and planned risk snapshot."""
        if bool(target_portfolio.get("execution_authorized")):
            raise PortfolioRiskStoreError("Portfolio Risk Store拒绝保存已授权执行的目标组合")
        profile = dict(target_portfolio.get("profile") or {})
        required_profile = {
            "profile_id", "version", "capital", "risk_level", "investment_horizon",
            "max_drawdown_tolerance", "investment_style",
        }
        missing = sorted(required_profile - set(profile))
        if missing:
            raise PortfolioRiskStoreError(f"Investment Profile缺少字段：{missing}")
        run_id = str(target_portfolio.get("source_run_id") or "")
        if not run_id or risk_assessment.get("source_run_id") != run_id:
            raise PortfolioRiskStoreError("Target Portfolio与Risk Snapshot的run_id不一致")
        portfolio_id = self._identity("portfolio", run_id, str(profile["profile_id"]))
        observed_at = str(target_portfolio.get("generated_at") or datetime.now(timezone.utc).isoformat())
        snapshot_id = self._identity("risk", portfolio_id, "planned")

        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO investment_profiles(
                    profile_id,version,capital,risk_level,investment_horizon,
                    max_drawdown_tolerance,investment_style,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    profile["profile_id"], profile["version"], float(profile["capital"]),
                    profile["risk_level"], profile["investment_horizon"],
                    float(profile["max_drawdown_tolerance"]), profile["investment_style"],
                    observed_at,
                ),
            )
            connection.execute(
                """INSERT INTO portfolios(
                    portfolio_id,name,run_id,profile_id,strategy_version,data_version,
                    service_version,positioning_model_version,target_exposure,cash_weight,
                    execution_authorized,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,0,?)
                ON CONFLICT(portfolio_id) DO UPDATE SET
                    name=excluded.name,
                    service_version=excluded.service_version,
                    positioning_model_version=excluded.positioning_model_version,
                    target_exposure=excluded.target_exposure,
                    cash_weight=excluded.cash_weight,
                    created_at=excluded.created_at""",
                (
                    portfolio_id, name, run_id, profile["profile_id"],
                    target_portfolio["strategy_version"], target_portfolio["data_version"],
                    target_portfolio["service_version"],
                    target_portfolio["positioning_model_version"],
                    float(target_portfolio["target_exposure"]),
                    float(target_portfolio["cash_reserve_weight"]), observed_at,
                ),
            )
            connection.execute(
                "DELETE FROM portfolio_positions WHERE portfolio_id=?", (portfolio_id,)
            )
            for position in target_portfolio.get("positions", []):
                connection.execute(
                    """INSERT INTO portfolio_positions(
                        portfolio_id,symbol,name,industry,target_weight,current_weight,cost,
                        market_value,target_value,round_lot_quantity,research_score,
                        security_risk_score,base_weight,score_coefficient,risk_coefficient,
                        style_coefficient
                    ) VALUES(?,?,?,?,?,NULL,NULL,NULL,?,?,?,?,?,?,?,?)""",
                    (
                        portfolio_id, position["symbol"], position["name"],
                        position.get("industry"), float(position["target_weight"]),
                        float(position["target_value"]), int(position["round_lot_quantity"]),
                        float(position["research_score"]),
                        self._nullable_float(position.get("security_risk_score")),
                        float(position["base_weight"]), float(position["score_coefficient"]),
                        float(position["risk_coefficient"]), float(position["style_coefficient"]),
                    ),
                )
            connection.execute(
                """INSERT INTO risk_snapshots(
                    snapshot_id,portfolio_id,snapshot_type,observed_at,
                    valuation_service_version,current_drawdown,max_drawdown,
                    portfolio_volatility,predicted_max_drawdown,weighted_security_risk,industry_exposure_json,
                    style_exposure_json,size_exposure_json,cycle_exposure_json,
                    concentration_json,risk_flags_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    portfolio_volatility=excluded.portfolio_volatility,
                    predicted_max_drawdown=excluded.predicted_max_drawdown,
                    weighted_security_risk=excluded.weighted_security_risk,
                    industry_exposure_json=excluded.industry_exposure_json,
                    style_exposure_json=excluded.style_exposure_json,
                    size_exposure_json=excluded.size_exposure_json,
                    cycle_exposure_json=excluded.cycle_exposure_json,
                    concentration_json=excluded.concentration_json,
                    risk_flags_json=excluded.risk_flags_json""",
                (
                    snapshot_id, portfolio_id, "planned", observed_at,
                    None, None, None,
                    float(risk_assessment["portfolio_volatility_proxy"]),
                    float(risk_assessment["predicted_max_drawdown"]),
                    float(risk_assessment["weighted_security_risk"]),
                    self._json(risk_assessment.get("industry_exposure", {})),
                    self._json(risk_assessment.get("style_exposure", {})),
                    self._json(risk_assessment.get("size_exposure", {})),
                    self._json(risk_assessment.get("cycle_exposure", {})),
                    self._json(risk_assessment.get("concentration", {})),
                    self._json(risk_assessment.get("risk_flags", [])),
                ),
            )
        return {
            "store_version": PORTFOLIO_RISK_STORE_VERSION,
            "portfolio_id": portfolio_id,
            "risk_snapshot_id": snapshot_id,
        }

    def save_account_snapshot(
        self,
        target_portfolio: Mapping[str, Any],
        risk_assessment: Mapping[str, Any],
        positions: list[Mapping[str, Any]],
        exit_plan: Mapping[str, Any],
        *,
        observed_at: str,
        snapshot_type: str,
    ) -> dict[str, str]:
        """Persist a ValuationService-backed account risk point after a paper workflow."""
        if risk_assessment.get("drawdown_source") in {None, "ValuationService_required"}:
            raise PortfolioRiskStoreError("账户风险快照必须包含ValuationService回撤证据")
        profile = dict(target_portfolio.get("profile") or {})
        portfolio_id = self._identity(
            "portfolio",
            str(target_portfolio.get("source_run_id") or ""),
            str(profile.get("profile_id") or ""),
        )
        snapshot_id = self._identity(
            "risk", portfolio_id, snapshot_type, observed_at
        )
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM portfolios WHERE portfolio_id=?", (portfolio_id,)
            ).fetchone()
            if exists is None:
                raise PortfolioRiskStoreError("账户风险快照找不到对应Target Portfolio")
            connection.execute(
                "UPDATE portfolio_positions SET current_weight=NULL,cost=NULL,market_value=NULL WHERE portfolio_id=?",
                (portfolio_id,),
            )
            for position in positions:
                symbol = str(position["symbol"])
                current = connection.execute(
                    "SELECT 1 FROM portfolio_positions WHERE portfolio_id=? AND symbol=?",
                    (portfolio_id, symbol),
                ).fetchone()
                values = (
                    self._nullable_float(position.get("weight")),
                    self._nullable_float(position.get("average_cost")),
                    self._nullable_float(position.get("market_value")),
                    portfolio_id,
                    symbol,
                )
                if current:
                    connection.execute(
                        """UPDATE portfolio_positions SET current_weight=?,cost=?,market_value=?
                        WHERE portfolio_id=? AND symbol=?""",
                        values,
                    )
                else:
                    connection.execute(
                        """INSERT INTO portfolio_positions(
                            portfolio_id,symbol,name,industry,target_weight,current_weight,cost,
                            market_value,target_value,round_lot_quantity,research_score,
                            security_risk_score,base_weight,score_coefficient,risk_coefficient,
                            style_coefficient
                        ) VALUES(?,?,?,?,0,?,?,?,0,0,0,NULL,0,0,0,0)""",
                        (
                            portfolio_id, symbol, str(position.get("name") or symbol), None,
                            values[0], values[1], values[2],
                        ),
                    )
            connection.execute(
                """INSERT INTO risk_snapshots(
                    snapshot_id,portfolio_id,snapshot_type,observed_at,
                    valuation_service_version,current_drawdown,max_drawdown,
                    portfolio_volatility,predicted_max_drawdown,weighted_security_risk,
                    industry_exposure_json,style_exposure_json,size_exposure_json,
                    cycle_exposure_json,concentration_json,risk_flags_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    valuation_service_version=excluded.valuation_service_version,
                    current_drawdown=excluded.current_drawdown,
                    max_drawdown=excluded.max_drawdown,
                    portfolio_volatility=excluded.portfolio_volatility,
                    predicted_max_drawdown=excluded.predicted_max_drawdown,
                    weighted_security_risk=excluded.weighted_security_risk,
                    industry_exposure_json=excluded.industry_exposure_json,
                    style_exposure_json=excluded.style_exposure_json,
                    size_exposure_json=excluded.size_exposure_json,
                    cycle_exposure_json=excluded.cycle_exposure_json,
                    concentration_json=excluded.concentration_json,
                    risk_flags_json=excluded.risk_flags_json""",
                (
                    snapshot_id, portfolio_id, snapshot_type, observed_at,
                    risk_assessment["drawdown_source"],
                    float(risk_assessment["current_drawdown"]),
                    float(risk_assessment["max_drawdown"]),
                    float(risk_assessment["portfolio_volatility_proxy"]),
                    float(risk_assessment["predicted_max_drawdown"]),
                    float(risk_assessment["weighted_security_risk"]),
                    self._json(risk_assessment.get("industry_exposure", {})),
                    self._json(risk_assessment.get("style_exposure", {})),
                    self._json(risk_assessment.get("size_exposure", {})),
                    self._json(risk_assessment.get("cycle_exposure", {})),
                    self._json(risk_assessment.get("concentration", {})),
                    self._json(risk_assessment.get("risk_flags", [])),
                ),
            )
            connection.execute(
                "DELETE FROM exit_signals WHERE snapshot_id=?", (snapshot_id,)
            )
            for signal in exit_plan.get("signals", []):
                connection.execute(
                    """INSERT INTO exit_signals(
                        snapshot_id,symbol,action,reasons_json,available_quantity,creates_order
                    ) VALUES(?,?,?,?,?,0)""",
                    (
                        snapshot_id, signal["symbol"], signal["action"],
                        self._json(signal.get("reasons", [])),
                        int(signal.get("available_quantity") or 0),
                    ),
                )
        return {
            "store_version": PORTFOLIO_RISK_STORE_VERSION,
            "portfolio_id": portfolio_id,
            "risk_snapshot_id": snapshot_id,
        }

    def portfolio(self, portfolio_id: str) -> dict[str, Any] | None:
        """Read one persisted target and its positions for audit and tests."""
        with self._connect() as connection:
            portfolio = connection.execute(
                "SELECT * FROM portfolios WHERE portfolio_id=?", (portfolio_id,)
            ).fetchone()
            if portfolio is None:
                return None
            positions = connection.execute(
                "SELECT * FROM portfolio_positions WHERE portfolio_id=? ORDER BY symbol",
                (portfolio_id,),
            ).fetchall()
            risks = connection.execute(
                "SELECT * FROM risk_snapshots WHERE portfolio_id=? ORDER BY observed_at",
                (portfolio_id,),
            ).fetchall()
            exits = connection.execute(
                """SELECT exit_signals.* FROM exit_signals
                JOIN risk_snapshots USING(snapshot_id)
                WHERE risk_snapshots.portfolio_id=? ORDER BY snapshot_id,symbol""",
                (portfolio_id,),
            ).fetchall()
        return {
            "portfolio": dict(portfolio),
            "positions": [dict(item) for item in positions],
            "risk_snapshots": [dict(item) for item in risks],
            "exit_signals": [dict(item) for item in exits],
        }

    @staticmethod
    def _identity(namespace: str, *parts: str) -> str:
        """Create a stable non-secret identity for idempotent persistence."""
        payload = "|".join((namespace, *parts)).encode("utf-8")
        return f"{namespace}-" + hashlib.sha256(payload).hexdigest()[:20]

    @staticmethod
    def _json(value: Any) -> str:
        """Serialize canonical JSON and reject NaN or Infinity evidence."""
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _nullable_float(value: Any) -> float | None:
        """Preserve optional numeric fields as SQL NULL."""
        return None if value is None else float(value)
