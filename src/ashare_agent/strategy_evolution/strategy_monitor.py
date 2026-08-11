from __future__ import annotations

from typing import Any, Mapping

from .contracts import EvolutionHealthStatus, HEALTH_WEIGHTS
from .decay_detector import StrategyDecayDetector
from .factor_drift import FactorDriftMonitor
from .performance_tracker import PerformanceTracker


class StrategyHealthMonitor:
    """Aggregate five fixed components without renormalizing missing evidence."""

    monitor_version = "strategy-health-monitor-v1.0.0"

    def __init__(self) -> None:
        self.performance = PerformanceTracker()
        self.decay = StrategyDecayDetector()
        self.factor = FactorDriftMonitor()

    def evaluate(
        self,
        observation: Mapping[str, Any],
        baseline: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a 0-100 partial score, evidence coverage and non-binding status."""
        recent_flat = self._flat_metrics(observation)
        baseline_flat = self._flat_metrics(baseline or {}) if baseline else None
        performance = self.performance.evaluate(observation.get("performance") or {})
        risk = self.performance.risk(observation.get("risk") or {})
        execution = self.performance.execution(observation.get("execution") or {})
        decay = self.decay.detect(recent_flat, baseline_flat)
        factor = self.factor.evaluate(
            observation.get("factors") or {},
            (baseline or {}).get("factors") if baseline else None,
        )
        environment = self._environment(observation.get("environment") or {})
        if performance["score"] is not None and decay.get("score") is not None:
            performance["score"] = min(performance["score"], decay["score"])
        components = {
            "performance": performance["score"],
            "risk": risk["score"],
            "factor": factor["score"],
            "execution": execution["score"],
            "environment": environment["score"],
        }
        partial = round(sum(
            float(components[name] or 0.0) * HEALTH_WEIGHTS[name]
            for name in HEALTH_WEIGHTS
        ), 6)
        coverage = round(sum(
            HEALTH_WEIGHTS[name] for name, value in components.items()
            if value is not None
        ), 6)
        if coverage < 0.60:
            status = EvolutionHealthStatus.INSUFFICIENT_EVIDENCE.value
            score = None
        else:
            score = partial
            status = (
                EvolutionHealthStatus.CRITICAL.value if partial < 45
                else EvolutionHealthStatus.DECAYING.value if partial < 65
                else EvolutionHealthStatus.WATCH.value if partial < 80
                else EvolutionHealthStatus.HEALTHY.value
            )
            if decay["status"] == "DECAYING" or factor["status"] == "DECAYING":
                status = EvolutionHealthStatus.DECAYING.value
        return {
            "monitor_version": self.monitor_version,
            "score": score,
            "partial_score": partial,
            "coverage": coverage,
            "status": status,
            "components": components,
            "weights": dict(HEALTH_WEIGHTS),
            "missing_components": [
                name for name, value in components.items() if value is None
            ],
            "performance": performance,
            "risk": risk,
            "factor_drift": factor,
            "execution": execution,
            "environment": environment,
            "decay": decay,
            "diagnostic_only": True,
            "can_modify_strategy": False,
            "can_auto_transition": False,
        }

    @staticmethod
    def _flat_metrics(observation: Mapping[str, Any]) -> dict[str, Any]:
        """Flatten only named monitor metrics for baseline decay comparisons."""
        performance = observation.get("performance") or {}
        risk = observation.get("risk") or {}
        execution = observation.get("execution") or {}
        factors = observation.get("factors") or {}
        ic_values = [
            values.get("ic") for values in factors.values()
            if values.get("ic") is not None
        ]
        icir_values = [
            values.get("icir") for values in factors.values()
            if values.get("icir") is not None
        ]
        return {
            **performance,
            **risk,
            **execution,
            "factor_ic": sum(ic_values) / len(ic_values) if ic_values else None,
            "factor_icir": sum(icir_values) / len(icir_values) if icir_values else None,
        }

    @staticmethod
    def _environment(values: Mapping[str, Any]) -> dict[str, Any]:
        """Score registered regime coverage only; never infer a market regime."""
        availability = str(values.get("availability") or "").upper()
        stability = values.get("stability_score")
        if stability is not None:
            score = max(0.0, min(100.0, float(stability)))
        elif availability in {"AVAILABLE", "PARTIAL"}:
            score = 100.0 if availability == "AVAILABLE" else 60.0
        else:
            score = None
        return {
            "score": score,
            "availability": availability or "UNAVAILABLE",
            "regime_source": values.get("regime_source"),
            "market_regime_inferred": False,
            "data_gaps": [] if score is not None else ["registered_regime_evidence"],
        }

