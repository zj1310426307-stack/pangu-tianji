from __future__ import annotations

from .contracts import RobustnessConfig, StrategyPath
from .performance import bounded_score, net_returns_after_cost, performance_metrics


def analyze_cost_stress(path: StrategyPath, config: RobustnessConfig) -> dict:
    """Reprice one gross strategy path under declared commission, slippage and tax stresses."""
    definitions: list[dict] = [{
        "scenario_id": "baseline",
        "commission": config.base_commission_rate,
        "slippage": config.base_slippage_rate,
        "sell_tax": config.base_sell_tax_rate,
        "multiplier": 1.0,
    }]
    for value in config.commission_rates:
        definitions.append({"scenario_id": f"commission.{value}", "commission": value,
                            "slippage": config.base_slippage_rate, "sell_tax": config.base_sell_tax_rate,
                            "multiplier": 1.0})
    for value in config.slippage_rates:
        definitions.append({"scenario_id": f"slippage.{value}", "commission": config.base_commission_rate,
                            "slippage": value, "sell_tax": config.base_sell_tax_rate,
                            "multiplier": 1.0})
    for value in config.sell_tax_rates:
        definitions.append({"scenario_id": f"sell_tax.{value}", "commission": config.base_commission_rate,
                            "slippage": config.base_slippage_rate, "sell_tax": value,
                            "multiplier": 1.0})
    for value in config.cost_multipliers:
        definitions.append({"scenario_id": f"cost_multiplier.{value}",
                            "commission": config.base_commission_rate,
                            "slippage": config.base_slippage_rate, "sell_tax": config.base_sell_tax_rate,
                            "multiplier": value})
    scenarios = []
    baseline_metrics = None
    for item in definitions:
        net = net_returns_after_cost(
            path.gross_returns,
            path.turnovers,
            commission_rate=item["commission"],
            slippage_rate=item["slippage"],
            sell_tax_rate=item["sell_tax"],
            cost_multiplier=item["multiplier"],
        )
        metrics = performance_metrics(
            net, turnovers=path.turnovers, periods_per_year=config.annualization_periods
        )
        if item["scenario_id"] == "baseline":
            baseline_metrics = metrics
        scenarios.append({**item, "metrics": metrics})
    assert baseline_metrics is not None
    for item in scenarios:
        item["annualized_return_impact"] = float(
            item["metrics"]["annualized_return"] - baseline_metrics["annualized_return"]
        )
    adverse = [
        item for item in scenarios
        if item["scenario_id"] != "baseline"
        and item["commission"] >= config.base_commission_rate
        and item["slippage"] >= config.base_slippage_rate
        and item["sell_tax"] >= config.base_sell_tax_rate
        and item["multiplier"] >= 1.0
    ]
    positive_ratio = sum(item["metrics"]["annualized_return"] > 0 for item in adverse) / max(len(adverse), 1)
    worst_impact = min(item["annualized_return_impact"] for item in adverse)
    scale = abs(float(baseline_metrics["annualized_return"])) + 0.05
    score = 100.0 * (0.6 * positive_ratio + 0.4 * max(0.0, 1.0 + worst_impact / scale))
    return {
        "method": "same_gross_path_explicit_turnover_repricing",
        "sell_tax_turnover_assumption": "half_of_aggregate_turnover",
        "baseline": baseline_metrics,
        "scenarios": scenarios,
        "scenario_count": len(scenarios),
        "worst_annualized_return_impact": float(worst_impact),
        "positive_annual_return_ratio_under_adverse_costs": float(positive_ratio),
        "resilience_score": bounded_score(score),
        "auto_changes_cost_model": False,
    }
