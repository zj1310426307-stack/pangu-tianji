from __future__ import annotations

from typing import Any

import pandas as pd

from .contracts import FACTOR_COLUMNS, FACTOR_NAMES
from .factor_return import top_selection_returns


def analyze_ablation(
    panel: pd.DataFrame,
    *,
    horizon: int,
    top_fraction: float,
    periods_per_year: int,
) -> dict[str, Any]:
    """Compare the frozen seven point groups with each research-only leave-one-out score."""
    working = panel.copy()
    columns = list(FACTOR_COLUMNS.values())
    working["research_full_score"] = working[columns].sum(axis=1)
    full = top_selection_returns(
        working,
        "research_full_score",
        horizon=horizon,
        top_fraction=top_fraction,
        periods_per_year=periods_per_year,
    )
    models: dict[str, Any] = {
        "full_seven_factor": {**full, "included_factors": list(FACTOR_NAMES)}
    }
    contributions: list[dict[str, Any]] = []
    for factor_name, factor_column in FACTOR_COLUMNS.items():
        score_column = f"research_without_{factor_name}"
        working[score_column] = working["research_full_score"] - working[factor_column]
        metrics = top_selection_returns(
            working,
            score_column,
            horizon=horizon,
            top_fraction=top_fraction,
            periods_per_year=periods_per_year,
        )
        metrics["included_factors"] = [item for item in FACTOR_NAMES if item != factor_name]
        models[f"without_{factor_name}"] = metrics
        full_return = full.get("annualized_return")
        ablated_return = metrics.get("annualized_return")
        full_risk = full.get("annualized_volatility")
        ablated_risk = metrics.get("annualized_volatility")
        full_drawdown = full.get("max_drawdown")
        ablated_drawdown = metrics.get("max_drawdown")
        full_sharpe = full.get("annualized_sharpe")
        ablated_sharpe = metrics.get("annualized_sharpe")
        contributions.append({
            "removed_factor": factor_name,
            "annualized_return_change_ablated_minus_full": (
                ablated_return - full_return
                if ablated_return is not None and full_return is not None else None
            ),
            "annualized_volatility_change_ablated_minus_full": (
                ablated_risk - full_risk
                if ablated_risk is not None and full_risk is not None else None
            ),
            "max_drawdown_change_ablated_minus_full": (
                ablated_drawdown - full_drawdown
                if ablated_drawdown is not None and full_drawdown is not None else None
            ),
            "sharpe_change_ablated_minus_full": (
                ablated_sharpe - full_sharpe
                if ablated_sharpe is not None and full_sharpe is not None else None
            ),
            "descriptive_return_contribution_full_minus_ablated": (
                full_return - ablated_return
                if ablated_return is not None and full_return is not None else None
            ),
        })
    contributions.sort(
        key=lambda row: (
            row["descriptive_return_contribution_full_minus_ablated"] is None,
            -(row["descriptive_return_contribution_full_minus_ablated"] or 0.0),
            row["removed_factor"],
        )
    )
    for rank, row in enumerate(contributions, start=1):
        row["descriptive_contribution_rank"] = rank
    return {
        "method": "leave_one_factor_group_out_from_frozen_production_point_contributions",
        "research_only": True,
        "changes_production_weights": False,
        "auto_applies_results": False,
        "horizon": int(horizon),
        "top_fraction": float(top_fraction),
        "models": models,
        "contribution_order": contributions,
    }
