from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FactorWeights:
    """Define the production cross-sectional model as one versioned contract."""

    value: float = 20.0
    quality: float = 20.0
    growth: float = 15.0
    momentum: float = 20.0
    trend: float = 10.0
    low_risk: float = 10.0
    liquidity: float = 5.0

    def validate(self) -> None:
        """Reject silent weight drift between research and backtesting."""
        total = sum(self.__dict__.values())
        if not math.isclose(total, 100.0, abs_tol=1e-9):
            raise ValueError(f"因子权重必须合计100，当前为{total}")


PRODUCTION_FACTOR_WEIGHTS = FactorWeights()
PRODUCTION_FACTOR_WEIGHTS.validate()
FACTOR_MODEL_VERSION = "cross-sectional-v1.0.0"
FACTOR_CONTRACT_HASH = hashlib.sha256(
    json.dumps(
        {
            "version": FACTOR_MODEL_VERSION,
            "weights": PRODUCTION_FACTOR_WEIGHTS.__dict__,
            "groups": {
                "value": ["earnings_yield", "book_to_price", "free_cash_flow_yield", "dividend_yield"],
                "quality": ["roe", "cash_quality", "roe_stability", "gross_margin_stability", "debt_ratio", "accrual_ratio"],
                "growth": ["profit_growth", "revenue_growth", "profit_cagr_3y", "revenue_cagr_3y"],
                "momentum": ["momentum"],
                "trend": ["trend_strength"],
                "low_risk": ["volatility", "max_drawdown"],
                "liquidity": ["median_turnover_20d"],
            },
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
).hexdigest()[:16]


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric factor series and preserve missingness for quality gates."""
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").astype(float)


def _winsorize(series: pd.Series) -> pd.Series:
    """Clip finite values at robust one and ninety-nine percent boundaries."""
    values = series.replace([np.inf, -np.inf], np.nan)
    finite = values.dropna()
    if finite.empty:
        return values
    lower, upper = finite.quantile([0.01, 0.99])
    return values.clip(float(lower), float(upper))


def _size_neutralize(series: pd.Series, market_cap: pd.Series) -> pd.Series:
    """Remove a linear log-size component when reliable market cap is available."""
    clean = pd.DataFrame({"factor": series, "cap": market_cap}).replace([np.inf, -np.inf], np.nan).dropna()
    clean = clean[clean["cap"] > 0]
    if len(clean) < 5 or clean["cap"].nunique() < 3:
        return series
    x = np.log(clean["cap"].to_numpy(dtype=float))
    y = clean["factor"].to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    result = series.copy()
    result.loc[clean.index] = y - (slope * x + intercept)
    return result


def _rank(
    frame: pd.DataFrame,
    column: str,
    *,
    higher_is_better: bool = True,
    industry_neutral: bool = True,
) -> pd.Series:
    """Rank a winsorized, optionally size- and industry-neutral factor."""
    values = _winsorize(_numeric(frame, column))
    if "market_cap" in frame:
        values = _size_neutralize(values, _numeric(frame, "market_cap"))
    if industry_neutral and "industry" in frame and frame["industry"].notna().sum() >= 5:
        grouped = values.groupby(frame["industry"].fillna("UNKNOWN"), dropna=False)
        ranked = grouped.rank(method="average", pct=True)
    else:
        ranked = values.rank(method="average", pct=True)
    ranked = ranked.fillna(0.5)
    return ranked if higher_is_better else 1.0 - ranked


def _mean_ranks(frame: pd.DataFrame, specs: Iterable[tuple[str, bool]]) -> pd.Series:
    """Average only available factor ranks and expose missing groups as neutral."""
    parts = []
    for column, higher_is_better in specs:
        if column in frame and _numeric(frame, column).notna().any():
            parts.append(_rank(frame, column, higher_is_better=higher_is_better))
    if not parts:
        return pd.Series(0.5, index=frame.index, dtype=float)
    return pd.concat(parts, axis=1).mean(axis=1)


def score_cross_section(
    frame: pd.DataFrame,
    weights: FactorWeights = PRODUCTION_FACTOR_WEIGHTS,
) -> pd.DataFrame:
    """Score production and historical candidates through the exact same model.

    The function never reads dates or network state. Callers must provide only
    point-in-time features that were available on the signal date.
    """
    weights.validate()
    if frame.empty:
        return frame.copy()
    scored = frame.copy()

    scored["points_value"] = _mean_ranks(
        scored,
        (("earnings_yield", True), ("book_to_price", True), ("free_cash_flow_yield", True), ("dividend_yield", True)),
    ) * weights.value
    scored["points_quality"] = _mean_ranks(
        scored,
        (("roe", True), ("cash_quality", True), ("roe_stability", True), ("gross_margin_stability", True), ("debt_ratio", False), ("accrual_ratio", False)),
    ) * weights.quality
    scored["points_growth"] = _mean_ranks(
        scored,
        (("profit_growth", True), ("revenue_growth", True), ("profit_cagr_3y", True), ("revenue_cagr_3y", True)),
    ) * weights.growth
    scored["points_momentum"] = _rank(scored, "momentum", higher_is_better=True) * weights.momentum
    scored["points_trend"] = _numeric(scored, "trend_strength").clip(0, 1).fillna(0) * weights.trend
    scored["points_low_risk"] = _mean_ranks(
        scored, (("volatility", False), ("max_drawdown", True))
    ) * weights.low_risk
    scored["points_liquidity"] = _rank(scored, "median_turnover_20d", higher_is_better=True) * weights.liquidity
    point_columns = [column for column in scored.columns if column.startswith("points_")]
    scored["score"] = scored[point_columns].sum(axis=1)

    valuation_fields = ["earnings_yield", "book_to_price", "free_cash_flow_yield", "dividend_yield"]
    scored["valuation_complete"] = scored[[c for c in valuation_fields if c in scored]].notna().any(axis=1) if any(c in scored for c in valuation_fields) else False
    core_fields = ["roe", "cash_quality", "profit_growth", "revenue_growth"]
    available_core = [column for column in core_fields if column in scored]
    scored["financial_complete"] = scored[available_core].notna().all(axis=1) if available_core else False
    return scored.sort_values(["score", "symbol"], ascending=[False, True]).reset_index(drop=True)
