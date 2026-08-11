from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .contracts import FACTOR_COLUMNS, MARKET_REGIMES
from .factor_return import forward_return_metrics


def _regime_factor(
    panel: pd.DataFrame,
    factor_column: str,
    *,
    horizon: int,
    minimum_cross_section: int,
    periods_per_year: int,
) -> dict[str, Any]:
    """Calculate descriptive Rank IC and quintile spread inside one explicit regime."""
    return_column = f"forward_return_{horizon}d"
    rank_ics: list[float] = []
    spreads: list[float] = []
    dates = 0
    for _, group in panel.dropna(subset=[return_column]).groupby("date", sort=True):
        if len(group) < minimum_cross_section:
            continue
        factor_rank = group[factor_column].rank(method="average")
        return_rank = group[return_column].rank(method="average")
        correlation = factor_rank.corr(return_rank)
        if pd.notna(correlation):
            rank_ics.append(float(correlation))
        ordered = group.sort_values([factor_column, "symbol"], ascending=[True, True])
        bucket = max(1, int(np.ceil(len(ordered) / 5)))
        low = ordered.head(bucket)[return_column].mean()
        high = ordered.tail(bucket)[return_column].mean()
        spreads.append(float(high - low))
        dates += 1
    metrics = forward_return_metrics(
        pd.Series(spreads, dtype=float), horizon=horizon, periods_per_year=periods_per_year
    )
    metrics.update({
        "date_count": dates,
        "rank_ic_mean": float(np.mean(rank_ics)) if rank_ics else None,
        "rank_ic_std": float(np.std(rank_ics, ddof=1)) if len(rank_ics) > 1 else 0.0 if rank_ics else None,
        "rank_ic_hit_rate": float(np.mean(np.asarray(rank_ics) > 0)) if rank_ics else None,
        "spread_definition": "highest_quintile_minus_lowest_quintile",
    })
    return metrics


def analyze_stability(
    panel: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    minimum_cross_section: int,
    periods_per_year: int,
) -> dict[str, Any]:
    """Compare factors across explicit point-in-time bull/bear/sideways labels."""
    labelled = panel[panel["market_regime"].isin(MARKET_REGIMES)].copy()
    if labelled.empty:
        return {
            "availability": "unavailable",
            "reason": "Data Center factor_store或manifest未提供点时market_regime标签",
            "regime_source": "factor_store.market_regime_or_manifest.metadata.market_regime",
            "regimes": {},
        }
    regimes: dict[str, Any] = {}
    for regime in MARKET_REGIMES:
        subset = labelled[labelled["market_regime"] == regime]
        factor_metrics: dict[str, Any] = {}
        for factor_name, factor_column in FACTOR_COLUMNS.items():
            factor_metrics[factor_name] = {
                str(horizon): _regime_factor(
                    subset,
                    factor_column,
                    horizon=horizon,
                    minimum_cross_section=minimum_cross_section,
                    periods_per_year=periods_per_year,
                )
                for horizon in horizons
            }
        regimes[regime] = {
            "date_count": int(subset["date"].nunique()),
            "row_count": int(len(subset)),
            "factors": factor_metrics,
        }
    return {
        "availability": "available",
        "method": "explicit_point_in_time_market_regime_segmentation",
        "regime_source": "factor_store.market_regime_or_manifest.metadata.market_regime",
        "inferred_from_future_returns": False,
        "regimes": regimes,
    }
