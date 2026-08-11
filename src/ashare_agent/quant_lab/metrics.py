from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

from .exceptions import ContractError


@dataclass(frozen=True)
class MetricMetadata:
    """Pin return frequency, annualization and risk-free assumptions."""

    frequency: str = "daily"
    periods_per_year: int = 252
    risk_free_rate: float = 0.0
    return_convention: str = "simple"


def calculate_metrics(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame | None = None,
    benchmark_returns: pd.Series | None = None,
    *,
    metadata: MetricMetadata | None = None,
) -> dict[str, Any]:
    """Calculate auditable absolute, cost and benchmark-relative performance metrics."""
    meta = metadata or MetricMetadata()
    if not {"date", "equity"}.issubset(equity_curve.columns):
        raise ContractError("指标计算需要date和equity")
    curve = equity_curve[["date", "equity"]].copy()
    curve["date"] = pd.to_datetime(curve["date"], errors="coerce")
    curve["equity"] = pd.to_numeric(curve["equity"], errors="coerce")
    if curve.isna().any().any() or (curve["equity"] <= 0).any() or len(curve) < 2:
        raise ContractError("净值曲线无效或不足2个观测")
    curve = curve.sort_values("date").drop_duplicates("date").set_index("date")
    returns = curve["equity"].pct_change().dropna()
    years = max(len(returns) / meta.periods_per_year, 1 / meta.periods_per_year)
    total_return = float(curve["equity"].iloc[-1] / curve["equity"].iloc[0] - 1)
    annual_return = float((1 + total_return) ** (1 / years) - 1)
    volatility = float(returns.std(ddof=0) * math.sqrt(meta.periods_per_year))
    periodic_rf = (1 + meta.risk_free_rate) ** (1 / meta.periods_per_year) - 1
    excess_rf = returns - periodic_rf
    downside = excess_rf[excess_rf < 0]
    drawdown = curve["equity"] / curve["equity"].cummax() - 1
    underwater = drawdown < 0
    groups = (underwater != underwater.shift()).cumsum()
    duration = int(underwater.groupby(groups).sum().max()) if underwater.any() else 0
    sharpe = (
        float(excess_rf.mean() / excess_rf.std(ddof=0) * math.sqrt(meta.periods_per_year))
        if excess_rf.std(ddof=0) > 0 else 0.0
    )
    sortino = (
        float(excess_rf.mean() / downside.std(ddof=0) * math.sqrt(meta.periods_per_year))
        if len(downside) and downside.std(ddof=0) > 0 else 0.0
    )
    result: dict[str, Any] = {
        "metadata": {
            "frequency": meta.frequency,
            "periods_per_year": meta.periods_per_year,
            "risk_free_rate": meta.risk_free_rate,
            "return_convention": meta.return_convention,
            "observation_count": int(len(curve)),
            "start_date": curve.index[0].date().isoformat(),
            "end_date": curve.index[-1].date().isoformat(),
        },
        "total_return": total_return,
        "annualized_return": annual_return,
        "annualized_volatility": volatility,
        "max_drawdown": float(drawdown.min()),
        "max_drawdown_duration_periods": duration,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": annual_return / abs(float(drawdown.min())) if drawdown.min() < 0 else 0.0,
    }
    trade_frame = trades.copy() if trades is not None else pd.DataFrame()
    if trade_frame.empty:
        result.update({
            "trade_count": 0, "win_rate": 0.0, "gross_pnl": 0.0, "net_pnl": 0.0,
            "transaction_cost": 0.0, "slippage_cost": 0.0, "turnover": 0.0,
            "unfilled_order_count": 0,
        })
    else:
        for field in ("fee", "realized_pnl", "slippage_cost", "fill_price", "quantity"):
            if field not in trade_frame:
                trade_frame[field] = 0.0
            trade_frame[field] = pd.to_numeric(trade_frame[field], errors="coerce").fillna(0.0)
        side = (
            trade_frame["side"].astype(str).str.upper()
            if "side" in trade_frame
            else pd.Series("", index=trade_frame.index, dtype=str)
        )
        sells = trade_frame[side == "SELL"]
        net_pnl = float(sells["realized_pnl"].sum())
        costs = float(trade_frame["fee"].sum())
        slippage = float(trade_frame["slippage_cost"].sum())
        result.update({
            "trade_count": int(len(trade_frame)),
            "win_rate": float((sells["realized_pnl"] > 0).mean()) if len(sells) else 0.0,
            "gross_pnl": net_pnl + costs + slippage,
            "net_pnl": net_pnl,
            "transaction_cost": costs,
            "slippage_cost": slippage,
            "turnover": float((trade_frame["fill_price"].abs() * trade_frame["quantity"].abs()).sum()
                              / float(curve["equity"].iloc[0])),
            "unfilled_order_count": int((trade_frame.get("status", "FILLED") == "UNFILLED").sum())
            if "status" in trade_frame else 0,
        })
    if benchmark_returns is not None:
        benchmark = pd.to_numeric(benchmark_returns, errors="coerce").dropna()
        aligned = pd.concat([returns.rename("strategy"), benchmark.rename("benchmark")], axis=1).dropna()
        if not aligned.empty:
            excess = aligned["strategy"] - aligned["benchmark"]
            benchmark_total = float((1 + aligned["benchmark"]).prod() - 1)
            result.update({
                "benchmark_total_return": benchmark_total,
                "excess_total_return": total_return - benchmark_total,
                "tracking_error": float(excess.std(ddof=0) * math.sqrt(meta.periods_per_year)),
                "information_ratio": float(
                    excess.mean() / excess.std(ddof=0) * math.sqrt(meta.periods_per_year)
                ) if excess.std(ddof=0) > 0 else 0.0,
            })
        else:
            result["benchmark_availability"] = "unavailable_no_overlap"
    return result
