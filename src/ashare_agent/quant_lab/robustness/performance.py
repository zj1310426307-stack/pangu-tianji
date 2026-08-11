from __future__ import annotations

import math
from typing import Iterable

import numpy as np

from ..exceptions import ContractError


def net_returns_after_cost(
    gross_returns: Iterable[float],
    turnovers: Iterable[float],
    *,
    commission_rate: float,
    slippage_rate: float,
    sell_tax_rate: float,
    cost_multiplier: float = 1.0,
) -> np.ndarray:
    """Apply explicit one-way turnover costs to one unchanged gross return path.

    Commission and slippage apply to all traded notional. Sell tax applies to half
    of aggregate turnover, a documented symmetric buy/sell approximation.
    """
    returns = np.asarray(tuple(gross_returns), dtype=float)
    turnover = np.asarray(tuple(turnovers), dtype=float)
    if len(returns) != len(turnover):
        raise ContractError("收益与换手证据必须等长")
    rate = cost_multiplier * (commission_rate + slippage_rate + sell_tax_rate * 0.5)
    result = returns - turnover * rate
    if np.any(result <= -1) or not np.isfinite(result).all():
        raise ContractError("成本压力产生无效收益路径")
    return result


def performance_metrics(
    returns: Iterable[float],
    *,
    turnovers: Iterable[float] | None = None,
    periods_per_year: int = 252,
) -> dict[str, float | int]:
    """Calculate one consistent return, risk and turnover metric contract."""
    values = np.asarray(tuple(returns), dtype=float)
    if len(values) < 2 or not np.isfinite(values).all() or np.any(values <= -1):
        raise ContractError("绩效指标需要至少2个有限且大于-100%的收益观测")
    equity = np.cumprod(1.0 + values)
    total_return = float(equity[-1] - 1.0)
    years = max(len(values) / periods_per_year, 1 / periods_per_year)
    annualized_return = float(equity[-1] ** (1.0 / years) - 1.0)
    volatility = float(values.std(ddof=0) * math.sqrt(periods_per_year))
    sharpe = (
        float(values.mean() / values.std(ddof=0) * math.sqrt(periods_per_year))
        if values.std(ddof=0) > 0 else 0.0
    )
    peaks = np.maximum.accumulate(equity)
    drawdowns = equity / peaks - 1.0
    turnover_values = np.asarray(
        tuple(turnovers) if turnovers is not None else np.zeros(len(values)), dtype=float
    )
    if len(turnover_values) != len(values) or not np.isfinite(turnover_values).all():
        raise ContractError("换手证据与收益观测不一致")
    return {
        "observation_count": int(len(values)),
        "total_return": total_return,
        "annualized_return": annualized_return,
        "annualized_volatility": volatility,
        "max_drawdown": float(drawdowns.min()),
        "sharpe": sharpe,
        "turnover": float(turnover_values.sum()),
        "positive_period_ratio": float((values > 0).mean()),
    }


def bounded_score(value: float) -> float:
    """Clamp a descriptive component score to the public 0-100 range."""
    return float(max(0.0, min(100.0, value)))
