from __future__ import annotations

import math
from typing import Any, Mapping


def _number(value: Any) -> float | None:
    """Return one finite float or None for unavailable evidence."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clamp(value: float) -> float:
    """Bound a descriptive component to the public 0-100 scale."""
    return round(max(0.0, min(100.0, value)), 6)


class PerformanceTracker:
    """Score sealed return evidence without optimizing or selecting a strategy."""

    tracker_version = "strategy-performance-tracker-v1.0.0"

    def evaluate(self, metrics: Mapping[str, Any]) -> dict[str, Any]:
        """Evaluate return, Sharpe and win-rate evidence with explicit coverage."""
        annual_return = _number(metrics.get("annual_return"))
        total_return = _number(metrics.get("total_return"))
        sharpe = _number(metrics.get("sharpe"))
        win_rate = _number(metrics.get("win_rate"))
        scores: dict[str, float | None] = {
            "return": (
                _clamp(50.0 + 200.0 * annual_return)
                if annual_return is not None else
                _clamp(50.0 + 100.0 * total_return)
                if total_return is not None else None
            ),
            "sharpe": _clamp(40.0 + 40.0 * sharpe) if sharpe is not None else None,
            "win_rate": (
                _clamp((win_rate - 0.35) / 0.30 * 100.0)
                if win_rate is not None else None
            ),
        }
        available = [value for value in scores.values() if value is not None]
        return {
            "tracker_version": self.tracker_version,
            "score": round(sum(available) / len(available), 6) if available else None,
            "coverage": round(len(available) / len(scores), 6),
            "metrics": {
                "annual_return": annual_return,
                "total_return": total_return,
                "sharpe": sharpe,
                "win_rate": win_rate,
            },
            "metric_scores": scores,
            "data_gaps": [key for key, value in scores.items() if value is None],
            "diagnostic_only": True,
        }

    def risk(self, metrics: Mapping[str, Any]) -> dict[str, Any]:
        """Score drawdown and volatility without forecasting future loss."""
        drawdown = _number(metrics.get("max_drawdown"))
        volatility = _number(metrics.get("volatility"))
        values = {
            "drawdown": _clamp(100.0 * (1.0 - abs(drawdown) / 0.30))
            if drawdown is not None else None,
            "volatility": _clamp(100.0 * (1.0 - abs(volatility) / 0.50))
            if volatility is not None else None,
        }
        available = [value for value in values.values() if value is not None]
        return {
            "score": round(sum(available) / len(available), 6) if available else None,
            "coverage": round(len(available) / len(values), 6),
            "metrics": {"max_drawdown": drawdown, "volatility": volatility},
            "metric_scores": values,
            "data_gaps": [key for key, value in values.items() if value is None],
            "forecast": False,
        }

    def execution(self, metrics: Mapping[str, Any]) -> dict[str, Any]:
        """Score turnover, costs and execution deviation as descriptive evidence."""
        turnover = _number(metrics.get("turnover"))
        cost_ratio = _number(metrics.get("cost_ratio"))
        deviation = _number(metrics.get("execution_deviation"))
        values = {
            "turnover": _clamp(110.0 - 35.0 * turnover)
            if turnover is not None else None,
            "cost": _clamp(100.0 * (1.0 - abs(cost_ratio) / 0.20))
            if cost_ratio is not None else None,
            "execution_deviation": _clamp(100.0 * (1.0 - abs(deviation) / 0.05))
            if deviation is not None else None,
        }
        available = [value for value in values.values() if value is not None]
        return {
            "score": round(sum(available) / len(available), 6) if available else None,
            "coverage": round(len(available) / len(values), 6),
            "metrics": {
                "turnover": turnover,
                "cost_ratio": cost_ratio,
                "execution_deviation": deviation,
            },
            "metric_scores": values,
            "data_gaps": [key for key, value in values.items() if value is None],
        }

