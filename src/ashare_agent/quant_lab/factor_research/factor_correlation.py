from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .contracts import FACTOR_COLUMNS, FACTOR_NAMES


def analyze_factor_correlation(
    panel: pd.DataFrame,
    *,
    minimum_cross_section: int,
    high_correlation_threshold: float,
) -> dict[str, Any]:
    """Average daily cross-sectional Spearman matrices without pooling dates."""
    matrices: list[np.ndarray] = []
    dates: list[str] = []
    columns = [FACTOR_COLUMNS[name] for name in FACTOR_NAMES]
    for research_date, group in panel.groupby("date", sort=True):
        sample = group[columns].replace([np.inf, -np.inf], np.nan).dropna()
        if len(sample) < minimum_cross_section:
            continue
        matrix = sample.corr(method="spearman").to_numpy(dtype=float)
        if np.isfinite(matrix).all():
            matrices.append(matrix)
            dates.append(pd.Timestamp(research_date).date().isoformat())
    if not matrices:
        return {
            "availability": "unavailable", "observation_count": 0,
            "factors": list(FACTOR_NAMES), "matrix": [], "high_correlation_pairs": [],
            "redundancy_scores": {}, "redundancy_review_candidates": [],
        }
    average = np.mean(np.stack(matrices), axis=0)
    pairs = []
    for left in range(len(FACTOR_NAMES)):
        for right in range(left + 1, len(FACTOR_NAMES)):
            value = float(average[left, right])
            if abs(value) >= high_correlation_threshold:
                pairs.append({
                    "factor_a": FACTOR_NAMES[left], "factor_b": FACTOR_NAMES[right],
                    "correlation": value,
                    "redundancy_score": abs(value),
                })
    redundancy_scores = {
        FACTOR_NAMES[index]: float(
            max(abs(average[index, other]) for other in range(len(FACTOR_NAMES)) if other != index)
        )
        for index in range(len(FACTOR_NAMES))
    }
    redundancy_ranking = [
        {"factor": factor, "redundancy_score": score}
        for factor, score in sorted(
            redundancy_scores.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return {
        "availability": "available",
        "method": "mean_daily_cross_sectional_spearman",
        "observation_count": len(matrices),
        "dates": dates,
        "factors": list(FACTOR_NAMES),
        "matrix": [[float(value) for value in row] for row in average],
        "high_correlation_threshold": float(high_correlation_threshold),
        "high_correlation_pairs": pairs,
        "redundancy_score_definition": "maximum_absolute_mean_daily_spearman_to_other_factor",
        "redundancy_scores": redundancy_scores,
        "redundancy_ranking": redundancy_ranking,
        "redundancy_review_candidates": pairs,
        "automatic_factor_merge": False,
    }
