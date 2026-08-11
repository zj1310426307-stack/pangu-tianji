from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .contracts import FACTOR_COLUMNS


def _summary(values: pd.Series) -> dict[str, Any]:
    """Summarize one daily Rank IC sequence with explicit approximation semantics."""
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {
            "availability": "unavailable", "observation_count": 0, "ic_mean": None,
            "ic_std": None, "icir": None, "ic_hit_rate": None, "t_stat": None,
            "p_value": None, "significance_method": "normal_approximation_two_sided",
        }
    mean = float(clean.mean())
    standard_deviation = float(clean.std(ddof=1)) if len(clean) > 1 else 0.0
    t_stat = (
        mean / (standard_deviation / math.sqrt(len(clean)))
        if standard_deviation > 0 else None
    )
    return {
        "availability": "available",
        "observation_count": int(len(clean)),
        "ic_mean": mean,
        "ic_std": standard_deviation,
        "icir": mean / standard_deviation if standard_deviation > 0 else None,
        "ic_hit_rate": float((clean > 0).mean()),
        "t_stat": float(t_stat) if t_stat is not None else None,
        "p_value": (
            float(math.erfc(abs(t_stat) / math.sqrt(2.0))) if t_stat is not None else None
        ),
        "significance_method": (
            "normal_approximation_two_sided"
            if t_stat is not None else "unavailable_zero_variance"
        ),
    }


def analyze_rank_ic(
    panel: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    minimum_cross_section: int,
) -> dict[str, Any]:
    """Calculate daily cross-sectional Spearman Rank IC for all factors and horizons."""
    factor_results: dict[str, Any] = {}
    daily_records: list[dict[str, Any]] = []
    for factor_name, factor_column in FACTOR_COLUMNS.items():
        factor_horizons: dict[str, Any] = {}
        for horizon in horizons:
            return_column = f"forward_return_{horizon}d"
            values: list[float] = []
            for research_date, group in panel.groupby("date", sort=True):
                sample = group[[factor_column, return_column]].replace(
                    [np.inf, -np.inf], np.nan
                ).dropna()
                if len(sample) < minimum_cross_section:
                    continue
                correlation = sample[factor_column].rank(method="average").corr(
                    sample[return_column].rank(method="average"), method="pearson"
                )
                if pd.notna(correlation):
                    value = float(correlation)
                    values.append(value)
                    daily_records.append({
                        "date": pd.Timestamp(research_date).date().isoformat(),
                        "factor": factor_name,
                        "horizon": int(horizon),
                        "rank_ic": value,
                        "cross_section_size": int(len(sample)),
                    })
            factor_horizons[str(horizon)] = _summary(pd.Series(values, dtype=float))
        factor_results[factor_name] = factor_horizons
    return {
        "method": "daily_cross_sectional_spearman_rank_ic",
        "minimum_cross_section": int(minimum_cross_section),
        "factors": factor_results,
        "daily_records": daily_records,
    }
