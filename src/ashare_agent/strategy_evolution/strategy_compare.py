from __future__ import annotations

from typing import Any, Mapping

from ..quant_lab.contracts import sha256_json, utc_now
from .contracts import safety_contract, stable_id


class StrategyComparisonEngine:
    """Compare immutable health observations without selecting a production winner."""

    engine_version = "strategy-comparison-v1.0.0"
    metric_names = (
        "annual_return", "sharpe", "max_drawdown", "turnover",
        "cost_ratio", "factor_contribution",
    )

    def compare(
        self,
        left: Mapping[str, Any],
        right: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return descriptive deltas and explicit evidence gaps for two versions."""
        left_metrics = self._metrics(left)
        right_metrics = self._metrics(right)
        rows = []
        for name in self.metric_names:
            a, b = left_metrics.get(name), right_metrics.get(name)
            rows.append({
                "metric": name,
                "version_a": a,
                "version_b": b,
                "delta_b_minus_a": round(float(b) - float(a), 8)
                if a is not None and b is not None else None,
                "availability": "available"
                if a is not None and b is not None else "unavailable",
            })
        evidence_hash = sha256_json({
            "left": left.get("health_hash"), "right": right.get("health_hash"),
            "rows": rows,
        })
        comparison = {
            "comparison_id": stable_id(
                "comparison", left.get("health_id"), right.get("health_id"), evidence_hash
            ),
            "engine_version": self.engine_version,
            "strategy_id": left.get("strategy_id"),
            "version_a": left.get("strategy_version"),
            "version_b": right.get("strategy_version"),
            "health_a": left.get("score"),
            "health_b": right.get("score"),
            "metrics": rows,
            "result": "DESCRIPTIVE_COMPARISON_ONLY",
            "automatic_winner_selected": False,
            "evidence_hash": evidence_hash,
            "created_at": utc_now(),
            **safety_contract(),
        }
        comparison["comparison_hash"] = sha256_json(comparison)
        return comparison

    @staticmethod
    def _metrics(health: Mapping[str, Any]) -> dict[str, Any]:
        """Read normalized monitor metrics rather than recalculating strategy returns."""
        observation = health.get("observation") or {}
        performance = observation.get("performance") or {}
        risk = observation.get("risk") or {}
        execution = observation.get("execution") or {}
        factors = observation.get("factors") or {}
        contributions = [
            values.get("contribution") for values in factors.values()
            if values.get("contribution") is not None
        ]
        return {
            "annual_return": performance.get("annual_return"),
            "sharpe": performance.get("sharpe"),
            "max_drawdown": risk.get("max_drawdown"),
            "turnover": execution.get("turnover"),
            "cost_ratio": execution.get("cost_ratio"),
            "factor_contribution": (
                sum(contributions) / len(contributions) if contributions else None
            ),
        }

