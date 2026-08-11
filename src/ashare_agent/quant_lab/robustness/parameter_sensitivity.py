from __future__ import annotations

from typing import Mapping

import numpy as np

from .contracts import RobustnessConfig, StrategyPath
from .performance import bounded_score, net_returns_after_cost, performance_metrics


def analyze_parameter_sensitivity(
    paths: Mapping[str, StrategyPath], config: RobustnessConfig
) -> dict:
    """Compare every declared one-factor perturbation against the fixed baseline."""
    baseline = paths["baseline"]
    baseline_net = net_returns_after_cost(
        baseline.gross_returns,
        baseline.turnovers,
        commission_rate=config.base_commission_rate,
        slippage_rate=config.base_slippage_rate,
        sell_tax_rate=config.base_sell_tax_rate,
    )
    baseline_metrics = performance_metrics(
        baseline_net, turnovers=baseline.turnovers, periods_per_year=config.annualization_periods
    )
    scenarios = []
    stability_values = []
    scale = abs(float(baseline_metrics["annualized_return"])) + 0.05
    for scenario_id in sorted(paths):
        if not scenario_id.startswith("parameter."):
            continue
        path = paths[scenario_id]
        net = net_returns_after_cost(
            path.gross_returns,
            path.turnovers,
            commission_rate=config.base_commission_rate,
            slippage_rate=config.base_slippage_rate,
            sell_tax_rate=config.base_sell_tax_rate,
        )
        metrics = performance_metrics(
            net, turnovers=path.turnovers, periods_per_year=config.annualization_periods
        )
        annual_change = float(metrics["annualized_return"] - baseline_metrics["annualized_return"])
        drawdown_change = float(metrics["max_drawdown"] - baseline_metrics["max_drawdown"])
        local_score = 100.0 * float(np.exp(-abs(annual_change) / scale))
        if float(metrics["annualized_return"]) * float(baseline_metrics["annualized_return"]) < 0:
            local_score *= 0.5
        stability_values.append(local_score)
        scenarios.append({
            "scenario_id": scenario_id,
            "path_hash": path.path_hash,
            "metrics": metrics,
            "annualized_return_change": annual_change,
            "max_drawdown_change": drawdown_change,
            "stability_score": bounded_score(local_score),
        })
    return {
        "method": "predeclared_one_factor_at_a_time_no_optimization",
        "baseline": baseline_metrics,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "stability_score": bounded_score(float(np.mean(stability_values))) if stability_values else None,
        "selected_best_scenario": None,
        "auto_applies_results": False,
        "changes_production_parameters": False,
    }
