from __future__ import annotations

import numpy as np

from .contracts import MARKET_REGIMES, RobustnessConfig, StrategyPath
from .performance import bounded_score, net_returns_after_cost, performance_metrics


def analyze_regimes(path: StrategyPath, config: RobustnessConfig) -> dict:
    """Measure explicit point-in-time bull/bear/sideways subsets without future inference."""
    if not any(path.market_regimes):
        return {
            "availability": "UNAVAILABLE",
            "reason": "missing_explicit_point_in_time_market_regime",
            "inferred_from_future_returns": False,
            "regimes": {},
            "adaptability_score": None,
        }
    net = net_returns_after_cost(
        path.gross_returns, path.turnovers,
        commission_rate=config.base_commission_rate, slippage_rate=config.base_slippage_rate,
        sell_tax_rate=config.base_sell_tax_rate,
    )
    regimes = np.asarray(path.market_regimes, dtype=object)
    turnover = np.asarray(path.turnovers, dtype=float)
    results = {}
    for regime in MARKET_REGIMES:
        mask = regimes == regime
        if int(mask.sum()) < 2:
            results[regime] = {"availability": "UNAVAILABLE", "observation_count": int(mask.sum())}
        else:
            results[regime] = {
                "availability": "AVAILABLE",
                **performance_metrics(
                    net[mask], turnovers=turnover[mask], periods_per_year=config.annualization_periods
                ),
            }
    if any(results[name]["availability"] != "AVAILABLE" for name in MARKET_REGIMES):
        availability = "PARTIAL"
        score = None
    else:
        availability = "AVAILABLE"
        positive = sum(results[name]["annualized_return"] > 0 for name in MARKET_REGIMES) / 3
        downside = np.mean([max(0.0, 1.0 + results[name]["max_drawdown"] / 0.30) for name in MARKET_REGIMES])
        score = bounded_score(100.0 * (0.6 * positive + 0.4 * downside))
    return {
        "availability": availability,
        "source": "explicit_point_in_time_labels_only",
        "source_id": path.market_regime_source,
        "inferred_from_future_returns": False,
        "regimes": results,
        "adaptability_score": score,
    }
