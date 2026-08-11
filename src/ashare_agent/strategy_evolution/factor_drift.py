from __future__ import annotations

import math
from typing import Any, Mapping

from .contracts import FACTOR_NAMES


class FactorDriftMonitor:
    """Monitor seven named factors while refusing to change their production weights."""

    monitor_version = "strategy-factor-drift-v1.0.0"

    def evaluate(
        self,
        current: Mapping[str, Mapping[str, Any]],
        baseline: Mapping[str, Mapping[str, Any]] | None,
    ) -> dict[str, Any]:
        """Compare mean, distribution, IC, ICIR and contribution where evidence exists."""
        factors: list[dict[str, Any]] = []
        scores: list[float] = []
        baseline = baseline or {}
        for name in FACTOR_NAMES:
            now = dict(current.get(name) or {})
            before = dict(baseline.get(name) or {})
            metrics: list[dict[str, Any]] = []
            penalty = 0.0
            available = 0
            for metric, warning, critical in (
                ("mean", 1.0, 2.0),
                ("ic", 0.015, 0.035),
                ("icir", 0.20, 0.50),
                ("contribution", 0.10, 0.25),
            ):
                recent = self._number(now.get(metric))
                historical = self._number(before.get(metric))
                if recent is None or historical is None:
                    metrics.append({"metric": metric, "availability": "unavailable"})
                    continue
                available += 1
                delta = recent - historical
                if metric == "mean":
                    std = abs(self._number(before.get("std")) or 0.0)
                    deterioration = abs(delta) / std if std > 1e-12 else abs(delta)
                else:
                    deterioration = max(0.0, -delta)
                level = "NORMAL"
                if deterioration >= critical:
                    level, penalty = "DECAYING", penalty + 35.0
                elif deterioration >= warning:
                    level, penalty = "WATCH", penalty + 15.0
                metrics.append({
                    "metric": metric,
                    "baseline": historical,
                    "recent": recent,
                    "delta": round(delta, 8),
                    "status": level,
                })
            if not baseline:
                status, score = "BASELINE_BUILDING", None
            elif available == 0:
                status, score = "UNAVAILABLE", None
            else:
                score = round(max(0.0, 100.0 - penalty), 6)
                status = (
                    "DECAYING" if any(item.get("status") == "DECAYING" for item in metrics)
                    else "WATCH" if any(item.get("status") == "WATCH" for item in metrics)
                    else "STABLE"
                )
                scores.append(score)
            factors.append({
                "factor_name": name,
                "status": status,
                "drift_score": score,
                "metrics": metrics,
                "current": now,
                "baseline": before,
                "can_modify_weight": False,
            })
        return {
            "monitor_version": self.monitor_version,
            "status": (
                "DECAYING" if any(item["status"] == "DECAYING" for item in factors)
                else "WATCH" if any(item["status"] == "WATCH" for item in factors)
                else "STABLE" if scores
                else "BASELINE_BUILDING" if not baseline
                else "UNAVAILABLE"
            ),
            "score": round(sum(scores) / len(scores), 6) if scores else None,
            "coverage": round(len(scores) / len(FACTOR_NAMES), 6),
            "factors": factors,
            "automatic_weight_change": False,
        }

    @staticmethod
    def _number(value: Any) -> float | None:
        """Normalize finite factor metrics while preserving missing values."""
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

