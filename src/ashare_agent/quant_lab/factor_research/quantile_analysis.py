from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .contracts import FACTOR_COLUMNS
from .factor_return import forward_return_metrics, selection_turnover


def _quantile_labels(group: pd.DataFrame, factor_column: str, count: int) -> pd.Series:
    """Assign deterministic low-to-high groups even when factor values tie."""
    ordered = group.sort_values([factor_column, "symbol"], ascending=[True, True])
    positions = np.arange(len(ordered), dtype=int)
    labels = np.floor(positions * count / max(len(ordered), 1)).astype(int) + 1
    labels = np.minimum(labels, count)
    return pd.Series(labels, index=ordered.index, dtype=int).reindex(group.index)


def analyze_quantiles(
    panel: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    quantile_counts: tuple[int, ...],
    minimum_cross_section: int,
    periods_per_year: int,
) -> dict[str, Any]:
    """Evaluate five/ten-group forward returns, risk, turnover and high-minus-low."""
    output: dict[str, Any] = {}
    for factor_name, factor_column in FACTOR_COLUMNS.items():
        factor_output: dict[str, Any] = {}
        for quantile_count in quantile_counts:
            count_output: dict[str, Any] = {}
            for horizon in horizons:
                return_column = f"forward_return_{horizon}d"
                returns_by_group: dict[int, list[tuple[pd.Timestamp, float]]] = {
                    number: [] for number in range(1, quantile_count + 1)
                }
                members_by_group: dict[int, list[set[str]]] = {
                    number: [] for number in range(1, quantile_count + 1)
                }
                long_short: list[tuple[pd.Timestamp, float]] = []
                for research_date, group in panel.dropna(subset=[return_column]).groupby("date", sort=True):
                    if len(group) < max(minimum_cross_section, quantile_count):
                        continue
                    labels = _quantile_labels(group, factor_column, quantile_count)
                    dated: dict[int, float] = {}
                    for number in range(1, quantile_count + 1):
                        selected = group[labels == number]
                        if selected.empty:
                            continue
                        value = float(selected[return_column].mean())
                        returns_by_group[number].append((pd.Timestamp(research_date), value))
                        members_by_group[number].append(set(selected["symbol"].astype(str)))
                        dated[number] = value
                    if 1 in dated and quantile_count in dated:
                        long_short.append(
                            (pd.Timestamp(research_date), dated[quantile_count] - dated[1])
                        )
                group_metrics: dict[str, Any] = {}
                for number, records in returns_by_group.items():
                    series = pd.Series([item[1] for item in records], dtype=float)
                    metrics = forward_return_metrics(
                        series, horizon=horizon, periods_per_year=periods_per_year
                    )
                    metrics["turnover"] = selection_turnover(
                        members_by_group[number][::max(horizon, 1)]
                    )
                    group_metrics[f"Q{number}"] = metrics
                spread = forward_return_metrics(
                    pd.Series([item[1] for item in long_short], dtype=float),
                    horizon=horizon,
                    periods_per_year=periods_per_year,
                )
                spread["definition"] = f"Q{quantile_count}-Q1"
                means = [
                    group_metrics[f"Q{number}"]["mean_forward_return"]
                    for number in range(1, quantile_count + 1)
                ]
                finite_means = [item for item in means if item is not None]
                monotonic = bool(
                    len(finite_means) == quantile_count
                    and all(left <= right for left, right in zip(finite_means, finite_means[1:]))
                )
                count_output[str(horizon)] = {
                    "groups": group_metrics,
                    "long_short": spread,
                    "monotonic_low_to_high": monotonic,
                }
            factor_output[str(quantile_count)] = count_output
        output[factor_name] = factor_output
    return {
        "method": "deterministic_cross_sectional_equal_count_groups",
        "group_direction": "Q1_low_to_QN_high",
        "factors": output,
    }
