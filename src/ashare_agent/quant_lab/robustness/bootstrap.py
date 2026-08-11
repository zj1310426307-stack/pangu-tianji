from __future__ import annotations

import numpy as np

from .contracts import RobustnessConfig, StrategyPath
from .performance import net_returns_after_cost, performance_metrics


def analyze_bootstrap(path: StrategyPath, config: RobustnessConfig) -> dict:
    """Run deterministic moving-block bootstrap to preserve short-range return dependence."""
    net = net_returns_after_cost(
        path.gross_returns, path.turnovers,
        commission_rate=config.base_commission_rate, slippage_rate=config.base_slippage_rate,
        sell_tax_rate=config.base_sell_tax_rate,
    )
    if len(net) < config.bootstrap_block_length * 2:
        return {
            "availability": "UNAVAILABLE",
            "reason": "insufficient_observations_for_moving_block_bootstrap",
            "observation_count": int(len(net)),
            "required_observations": int(config.bootstrap_block_length * 2),
        }
    rng = np.random.default_rng(config.random_seed)
    block = config.bootstrap_block_length
    starts = np.arange(0, len(net) - block + 1)
    annual_returns = []
    drawdowns = []
    sharpes = []
    for _ in range(config.bootstrap_iterations):
        chosen = rng.choice(starts, size=int(np.ceil(len(net) / block)), replace=True)
        sampled = np.concatenate([net[index:index + block] for index in chosen])[:len(net)]
        metrics = performance_metrics(sampled, periods_per_year=config.annualization_periods)
        annual_returns.append(metrics["annualized_return"])
        drawdowns.append(metrics["max_drawdown"])
        sharpes.append(metrics["sharpe"])
    def distribution(values: list[float]) -> dict[str, float]:
        """Summarize one simulation distribution without selecting a favorable draw."""
        data = np.asarray(values, dtype=float)
        return {
            "mean": float(data.mean()),
            "p05": float(np.quantile(data, 0.05)),
            "p50": float(np.quantile(data, 0.50)),
            "p95": float(np.quantile(data, 0.95)),
        }
    return {
        "availability": "AVAILABLE",
        "method": "moving_block_bootstrap",
        "iterations": config.bootstrap_iterations,
        "block_length": block,
        "random_seed": config.random_seed,
        "annualized_return": distribution(annual_returns),
        "max_drawdown": distribution(drawdowns),
        "sharpe": distribution(sharpes),
        "probability_positive_annualized_return": float(np.mean(np.asarray(annual_returns) > 0)),
        "selected_simulation": None,
    }
