from __future__ import annotations

import math
from typing import Any, Mapping


class StrategyDecayDetector:
    """Compare sealed baseline and recent metrics without inferring missing history."""

    detector_version = "strategy-decay-detector-v1.0.0"

    def detect(
        self,
        recent: Mapping[str, Any],
        baseline: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Flag material return, IC, hit-rate, drawdown and cost deterioration."""
        if not baseline:
            return {
                "detector_version": self.detector_version,
                "status": "BASELINE_BUILDING",
                "score": None,
                "comparisons": [],
                "issues": [],
                "data_gaps": ["historical_baseline_unavailable"],
                "decay_established": False,
            }
        rules = (
            ("annual_return", "decrease", 0.04, 0.08),
            ("sharpe", "decrease", 0.25, 0.60),
            ("win_rate", "decrease", 0.05, 0.10),
            ("factor_ic", "decrease", 0.015, 0.035),
            ("factor_icir", "decrease", 0.20, 0.50),
            ("max_drawdown", "increase", 0.03, 0.07),
            ("turnover", "increase", 0.35, 0.80),
            ("cost_ratio", "increase", 0.02, 0.05),
        )
        comparisons: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        available = 0
        penalty = 0.0
        for metric, direction, warning, critical in rules:
            current = self._number(recent.get(metric))
            reference = self._number(baseline.get(metric))
            if current is None or reference is None:
                comparisons.append({"metric": metric, "availability": "unavailable"})
                continue
            available += 1
            delta = current - reference
            deterioration = -delta if direction == "decrease" else delta
            level = "NORMAL"
            if deterioration >= critical:
                level, penalty = "DECAYING", penalty + 18.0
            elif deterioration >= warning:
                level, penalty = "WATCH", penalty + 8.0
            row = {
                "metric": metric,
                "baseline": reference,
                "recent": current,
                "delta": round(delta, 8),
                "direction": direction,
                "status": level,
            }
            comparisons.append(row)
            if level != "NORMAL":
                issues.append(row)
        if available == 0:
            status = "UNAVAILABLE"
            score = None
        else:
            score = round(max(0.0, 100.0 - penalty), 6)
            status = (
                "DECAYING" if any(item["status"] == "DECAYING" for item in issues)
                else "WATCH" if issues else "STABLE"
            )
        return {
            "detector_version": self.detector_version,
            "status": status,
            "score": score,
            "coverage": round(available / len(rules), 6),
            "comparisons": comparisons,
            "issues": issues,
            "data_gaps": [
                item["metric"] for item in comparisons
                if item.get("availability") == "unavailable"
            ],
            "decay_established": status in {"WATCH", "DECAYING"},
        }

    @staticmethod
    def _number(value: Any) -> float | None:
        """Normalize finite numeric evidence for deterministic comparisons."""
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

