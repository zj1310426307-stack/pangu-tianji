from __future__ import annotations

from typing import Mapping

import numpy as np

from .contracts import RobustnessConfig, StrategyPath
from .performance import bounded_score, net_returns_after_cost, performance_metrics


def analyze_delay_stress(paths: Mapping[str, StrategyPath], config: RobustnessConfig) -> dict:
    """Compare caller-produced delayed execution paths without synthesizing fill prices."""
    baseline = paths["baseline"]
    base_net = net_returns_after_cost(
        baseline.gross_returns, baseline.turnovers,
        commission_rate=config.base_commission_rate, slippage_rate=config.base_slippage_rate,
        sell_tax_rate=config.base_sell_tax_rate,
    )
    base_metrics = performance_metrics(
        base_net, turnovers=baseline.turnovers, periods_per_year=config.annualization_periods
    )
    rows = []
    local_scores = []
    scale = abs(float(base_metrics["annualized_return"])) + 0.05
    for scenario_id in sorted(paths):
        if not scenario_id.startswith("delay."):
            continue
        path = paths[scenario_id]
        net = net_returns_after_cost(
            path.gross_returns, path.turnovers,
            commission_rate=config.base_commission_rate, slippage_rate=config.base_slippage_rate,
            sell_tax_rate=config.base_sell_tax_rate,
        )
        metrics = performance_metrics(
            net, turnovers=path.turnovers, periods_per_year=config.annualization_periods
        )
        impact = float(metrics["annualized_return"] - base_metrics["annualized_return"])
        local_scores.append(100.0 * float(np.exp(-abs(impact) / scale)))
        rows.append({
            "scenario_id": scenario_id,
            "path_hash": path.path_hash,
            "metrics": metrics,
            "annualized_return_impact": impact,
            "max_drawdown_change": float(metrics["max_drawdown"] - base_metrics["max_drawdown"]),
        })
    return {
        "method": "historical_engine_generated_delay_paths",
        "baseline": base_metrics,
        "scenarios": rows,
        "scenario_count": len(rows),
        "stability_score": bounded_score(float(np.mean(local_scores))) if local_scores else None,
        "best_delay_selected": None,
        "auto_changes_execution": False,
    }
