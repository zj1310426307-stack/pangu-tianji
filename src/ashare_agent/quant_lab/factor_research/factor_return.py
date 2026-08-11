from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ...data_center import DataCenter
from ..contracts import sha256_json
from ..exceptions import ContractError
from .contracts import FACTOR_COLUMNS, FACTOR_NAMES


@dataclass(frozen=True)
class FactorPanel:
    """Hold a validated point-in-time panel and its immutable lineage summary."""

    frame: pd.DataFrame
    manifest: dict[str, Any]


class FactorPanelLoader:
    """Build factor/price labels only from referenced immutable Data Center runs."""

    def __init__(self, data_center_root: Path) -> None:
        self.center = DataCenter(data_center_root)

    def load(
        self,
        run_ids: Iterable[str],
        *,
        horizons: tuple[int, ...],
        data_start: str,
        data_end: str,
    ) -> FactorPanel:
        """Read factor_store and market_data_pit, then calculate future returns by shift."""
        rows: list[pd.DataFrame] = []
        evidence: list[dict[str, Any]] = []
        for run_id in run_ids:
            manifest = self.center.manifest(str(run_id))
            research_date = str(manifest.get("research_date") or "")
            factors = pd.DataFrame(self.center.read_asset(str(run_id), "factor_store"))
            market = pd.DataFrame(self.center.read_asset(str(run_id), "market_data_pit"))
            self._validate_source(factors, market, str(run_id))
            price_column = "last_price" if "last_price" in market else "close"
            factor_columns = ["symbol", *FACTOR_COLUMNS.values()]
            if "market_regime" in factors:
                factor_columns.append("market_regime")
            merged = factors[factor_columns].merge(
                market[["symbol", price_column]], on="symbol", how="inner", validate="one_to_one"
            ).rename(columns={price_column: "close"})
            merged["date"] = research_date
            merged["source_run_id"] = str(run_id)
            manifest_regime = str(
                (manifest.get("metadata") or {}).get("market_regime") or ""
            ).lower()
            if "market_regime" in merged:
                merged["market_regime"] = merged["market_regime"].astype(str).str.lower()
                merged.loc[
                    ~merged["market_regime"].isin({"bull", "bear", "sideways"}),
                    "market_regime",
                ] = None
            else:
                merged["market_regime"] = (
                    manifest_regime
                    if manifest_regime in {"bull", "bear", "sideways"} else None
                )
            rows.append(merged)
            evidence.append({
                "run_id": str(run_id),
                "research_date": research_date,
                "data_version": manifest.get("data_version"),
                "factor_store_sha256": manifest["assets"]["factor_store"]["sha256"],
                "market_data_pit_sha256": manifest["assets"]["market_data_pit"]["sha256"],
            })
        if not rows:
            raise ContractError("因子研究没有Data Center输入")
        panel = pd.concat(rows, ignore_index=True)
        panel["date"] = pd.to_datetime(panel["date"], errors="coerce").dt.normalize()
        panel["symbol"] = panel["symbol"].astype(str)
        panel["close"] = pd.to_numeric(panel["close"], errors="coerce")
        for column in FACTOR_COLUMNS.values():
            panel[column] = pd.to_numeric(panel[column], errors="coerce")
        panel = panel[
            panel["date"].between(pd.Timestamp(data_start), pd.Timestamp(data_end), inclusive="both")
        ].copy()
        self._validate_panel(panel)
        panel = panel.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
        for horizon in horizons:
            future = panel.groupby("symbol", sort=False)["close"].shift(-int(horizon))
            panel[f"forward_return_{int(horizon)}d"] = future / panel["close"] - 1.0
        panel = panel.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
        manifest = {
            "source": "Pangu Data Center",
            "run_count": len(evidence),
            "run_ids": [item["run_id"] for item in evidence],
            "data_versions": sorted({str(item["data_version"]) for item in evidence}),
            "date_start": panel["date"].min().date().isoformat(),
            "date_end": panel["date"].max().date().isoformat(),
            "row_count": int(len(panel)),
            "symbol_count": int(panel["symbol"].nunique()),
            "horizons": list(horizons),
            "factor_columns": dict(FACTOR_COLUMNS),
            "forward_return_method": "per_symbol_trading_observation_shift",
            "market_regime_source": (
                "Data Center factor_store.market_regime, fallback manifest.metadata.market_regime"
            ),
            "evidence": evidence,
        }
        manifest["panel_hash"] = sha256_json(
            panel.fillna("__NA__").assign(date=panel["date"].dt.date.astype(str)).to_dict("records")
        )
        return FactorPanel(panel, manifest)

    @staticmethod
    def _validate_source(factors: pd.DataFrame, market: pd.DataFrame, run_id: str) -> None:
        """Require one complete seven-factor cross section and positive prices per run."""
        factor_required = {"symbol", *FACTOR_COLUMNS.values()}
        price_available = {"last_price", "close"} & set(market.columns)
        missing = sorted(factor_required - set(factors.columns))
        if missing or "symbol" not in market or not price_available:
            raise ContractError(f"Data Center run {run_id} 因子或价格字段不完整：{missing}")
        if factors["symbol"].astype(str).duplicated().any() or market["symbol"].astype(str).duplicated().any():
            raise ContractError(f"Data Center run {run_id} 横截面证券主键重复")

    @staticmethod
    def _validate_panel(panel: pd.DataFrame) -> None:
        """Reject duplicated, non-finite or chronologically invalid panel evidence."""
        if panel.empty or panel["date"].isna().any():
            raise ContractError("因子研究日期无效或范围内无数据")
        if panel.duplicated(["date", "symbol"]).any():
            raise ContractError("因子研究面板date+symbol主键重复")
        if panel["close"].isna().any() or (panel["close"] <= 0).any():
            raise ContractError("因子研究价格必须为正有限数")
        values = panel[list(FACTOR_COLUMNS.values())].replace([np.inf, -np.inf], np.nan)
        if values.isna().any().any():
            raise ContractError("七因子研究输入包含缺失或非有限值")


def forward_return_metrics(
    returns: pd.Series,
    *,
    horizon: int,
    periods_per_year: int = 252,
) -> dict[str, Any]:
    """Summarize overlapping forward returns and one explicit non-overlapping path."""
    clean = pd.to_numeric(returns, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return {
            "availability": "unavailable",
            "observation_count": 0,
            "mean_forward_return": None,
            "annualized_return": None,
            "annualized_volatility": None,
            "annualized_sharpe": None,
            "max_drawdown": None,
        }
    mean = float(clean.mean())
    yearly_periods = periods_per_year / max(int(horizon), 1)
    annual_return = float((1 + mean) ** yearly_periods - 1) if mean > -1 else -1.0
    volatility = float(clean.std(ddof=1) * np.sqrt(yearly_periods)) if len(clean) > 1 else 0.0
    standard_deviation = float(clean.std(ddof=1)) if len(clean) > 1 else 0.0
    non_overlapping = clean.iloc[::max(int(horizon), 1)]
    equity = (1.0 + non_overlapping).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return {
        "availability": "available",
        "observation_count": int(len(clean)),
        "mean_forward_return": mean,
        "annualized_return": annual_return,
        "annualized_volatility": volatility,
        "annualized_sharpe": (
            float(mean / standard_deviation * np.sqrt(yearly_periods))
            if standard_deviation > 0 else None
        ),
        "risk_free_rate": 0.0,
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
        "overlapping_forward_returns": True,
        "drawdown_path": "primary_non_overlapping_offset_0",
    }


def selection_turnover(memberships: list[set[str]]) -> float:
    """Calculate average one-way membership turnover for ordered rebalance sets."""
    if len(memberships) < 2:
        return 0.0
    values = []
    for previous, current in zip(memberships, memberships[1:]):
        denominator = max(len(previous), len(current), 1)
        values.append(1.0 - len(previous & current) / denominator)
    return float(np.mean(values))


def top_selection_returns(
    panel: pd.DataFrame,
    score_column: str,
    *,
    horizon: int,
    top_fraction: float,
    periods_per_year: int = 252,
) -> dict[str, Any]:
    """Evaluate a research-only equal-weight top slice without placing orders."""
    return_column = f"forward_return_{horizon}d"
    dated_returns: list[tuple[pd.Timestamp, float]] = []
    memberships: list[set[str]] = []
    for research_date, group in panel.dropna(subset=[return_column]).groupby("date", sort=True):
        count = max(1, int(np.ceil(len(group) * top_fraction)))
        selected = group.sort_values([score_column, "symbol"], ascending=[False, True]).head(count)
        dated_returns.append((pd.Timestamp(research_date), float(selected[return_column].mean())))
        memberships.append(set(selected["symbol"].astype(str)))
    series = pd.Series(
        [item[1] for item in dated_returns],
        index=[item[0] for item in dated_returns],
        dtype=float,
    )
    metrics = forward_return_metrics(series, horizon=horizon, periods_per_year=periods_per_year)
    metrics["turnover"] = selection_turnover(memberships[::max(horizon, 1)])
    metrics["selected_fraction"] = float(top_fraction)
    return metrics


def analyze_factor_returns(
    panel: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    periods_per_year: int = 252,
) -> dict[str, Any]:
    """Estimate research-only rank-weighted, dollar-neutral factor forward returns."""
    output: dict[str, Any] = {}
    for factor_name, factor_column in FACTOR_COLUMNS.items():
        factor_horizons: dict[str, Any] = {}
        for horizon in horizons:
            return_column = f"forward_return_{horizon}d"
            values: list[float] = []
            previous_weights: dict[str, float] | None = None
            turnovers: list[float] = []
            for _, group in panel.dropna(subset=[return_column]).groupby("date", sort=True):
                ranks = group[factor_column].rank(method="average", pct=True) - 0.5
                denominator = float(ranks.abs().sum())
                if denominator <= 0:
                    continue
                weights = ranks / denominator
                values.append(float((weights * group[return_column]).sum()))
                current_weights = dict(zip(group["symbol"].astype(str), weights.astype(float)))
                if previous_weights is not None:
                    symbols = set(previous_weights) | set(current_weights)
                    turnovers.append(
                        0.5 * sum(
                            abs(current_weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0))
                            for symbol in symbols
                        )
                    )
                previous_weights = current_weights
            metrics = forward_return_metrics(
                pd.Series(values, dtype=float),
                horizon=horizon,
                periods_per_year=periods_per_year,
            )
            metrics["turnover"] = float(np.mean(turnovers[::max(horizon, 1)])) if turnovers else 0.0
            metrics["portfolio_definition"] = "demeaned_percentile_rank_dollar_neutral"
            factor_horizons[str(horizon)] = metrics
        output[factor_name] = factor_horizons
    return {
        "method": "daily_rank_weighted_dollar_neutral_forward_factor_return",
        "research_only": True,
        "overlapping_forward_returns": True,
        "factors": output,
    }
