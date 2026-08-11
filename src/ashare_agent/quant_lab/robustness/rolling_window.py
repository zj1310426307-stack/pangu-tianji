from __future__ import annotations

import numpy as np

from .contracts import RobustnessConfig, StrategyPath
from .performance import bounded_score, net_returns_after_cost, performance_metrics


def analyze_rolling_windows(path: StrategyPath, config: RobustnessConfig) -> dict:
    """Evaluate chronological fixed-length windows without resampling or look-ahead."""
    net = net_returns_after_cost(
        path.gross_returns, path.turnovers,
        commission_rate=config.base_commission_rate, slippage_rate=config.base_slippage_rate,
        sell_tax_rate=config.base_sell_tax_rate,
    )
    window = config.rolling_window_periods
    if len(net) < window:
        return {
            "availability": "UNAVAILABLE",
            "reason": "insufficient_observations_for_rolling_window",
            "observation_count": int(len(net)),
            "required_observations": int(window),
            "windows": [],
            "stability_score": None,
        }
    rows = []
    for start in range(0, len(net) - window + 1, config.rolling_step_periods):
        end = start + window
        metrics = performance_metrics(
            net[start:end], turnovers=path.turnovers[start:end],
            periods_per_year=config.annualization_periods,
        )
        rows.append({
            "start_date": path.dates[start],
            "end_date": path.dates[end - 1],
            "metrics": metrics,
        })
    positive_ratio = float(np.mean([row["metrics"]["annualized_return"] > 0 for row in rows]))
    sharpe_consistency = float(np.mean([row["metrics"]["sharpe"] > 0 for row in rows]))
    score = bounded_score(100.0 * (0.6 * positive_ratio + 0.4 * sharpe_consistency))
    return {
        "availability": "AVAILABLE",
        "method": "chronological_rolling_window",
        "window_periods": window,
        "step_periods": config.rolling_step_periods,
        "window_count": len(rows),
        "windows": rows,
        "positive_annualized_return_ratio": positive_ratio,
        "positive_sharpe_ratio": sharpe_consistency,
        "stability_score": score,
    }
