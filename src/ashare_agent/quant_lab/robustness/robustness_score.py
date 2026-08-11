from __future__ import annotations

from .contracts import RobustnessConfig
from .performance import bounded_score


def calculate_robustness_score(
    *,
    parameter: dict,
    cost: dict,
    regime: dict,
    bootstrap: dict,
    rolling: dict,
    overfitting: dict,
    config: RobustnessConfig,
    out_of_sample_evidence: bool = False,
) -> dict:
    """Combine five fully available evidence groups into a descriptive health score."""
    components = {
        "parameter_stability": parameter.get("stability_score"),
        "cost_resilience": cost.get("resilience_score"),
        "regime_adaptability": regime.get("adaptability_score"),
        "out_of_sample_stability": None,
        "overfitting_resilience": None,
    }
    if (
        out_of_sample_evidence
        and bootstrap.get("availability") == "AVAILABLE"
        and rolling.get("availability") == "AVAILABLE"
    ):
        components["out_of_sample_stability"] = bounded_score(100.0 * (
            0.5 * bootstrap["probability_positive_annualized_return"]
            + 0.5 * rolling["positive_annualized_return_ratio"]
        ))
    dsr = overfitting.get("deflated_sharpe_ratio", {})
    pbo = overfitting.get("pbo", {})
    if dsr.get("availability") == "AVAILABLE" and pbo.get("availability") == "AVAILABLE":
        components["overfitting_resilience"] = bounded_score(100.0 * (
            0.5 * dsr["probability"] + 0.5 * (1.0 - pbo["probability"])
        ))
    unavailable = [name for name, value in components.items() if value is None]
    if unavailable:
        return {
            "availability": "UNAVAILABLE",
            "score": None,
            "grade": None,
            "components": components,
            "missing_components": unavailable,
            "out_of_sample_evidence": out_of_sample_evidence,
            "strategy_admission_decision": "NOT_EVALUATED",
        }
    score = sum(float(components[name]) * float(weight) for name, weight in config.score_weights.items())
    grade = "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D"
    return {
        "availability": "AVAILABLE",
        "score": bounded_score(score),
        "grade": grade,
        "components": components,
        "weights": dict(config.score_weights),
        "out_of_sample_evidence": out_of_sample_evidence,
        "strategy_admission_decision": "NOT_EVALUATED",
        "does_not_authorize_paper_or_live_trading": True,
    }
