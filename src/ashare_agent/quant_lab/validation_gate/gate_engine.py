from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from ..contracts import utc_now
from .contracts import (
    GateName,
    GateOutcome,
    GateStatus,
    StrategyHealthScore,
    StrategyState,
    ValidationEvidenceBundle,
    ValidationGateConfig,
    ValidationReview,
)
from .promotion import PromotionPolicy
from .validation_rules import ValidationRules


class StrategyValidationGate:
    """Produce a deterministic advisory review while leaving promotion to humans."""

    engine_version = "strategy-validation-gate-v1.0.0"

    def __init__(
        self,
        config: ValidationGateConfig | None = None,
        policy: PromotionPolicy | None = None,
    ) -> None:
        self.config = config or ValidationGateConfig()
        self.rules = ValidationRules(self.config)
        self.policy = policy or PromotionPolicy()

    def review(
        self,
        *,
        current_state: StrategyState,
        evidence: ValidationEvidenceBundle,
    ) -> ValidationReview:
        """Evaluate evidence and recommend at most one sequential state transition."""
        outcomes = self.rules.evaluate(evidence)
        health = self._health(outcomes)
        target = self.policy.next_state(current_state)
        outcome_by_name = {item.gate.value: item for item in outcomes}
        required = self.policy.required_gates(target) if target else ()
        eligible = bool(target) and all(
            outcome_by_name[name].status == GateStatus.PASSED for name in required
        )
        dataset_label = str(evidence.data_integrity.get("dataset_label") or "UNKNOWN")
        if dataset_label != "POINT_IN_TIME":
            eligible = False

        if current_state == StrategyState.RETIRED:
            recommendation = "RETIRED_NO_FURTHER_REVIEW"
            target = None
        elif current_state == StrategyState.PAPER_TRADING:
            paper = outcome_by_name[GateName.PAPER_TRADING.value]
            recommendation = (
                "PAPER_EVIDENCE_COMPLETE_HOLD_NO_LIVE"
                if paper.status == GateStatus.PASSED
                else "CONTINUE_PAPER_VALIDATION"
            )
            target = None
        elif eligible and target:
            recommendation = f"HUMAN_REVIEW_FOR_{target.value}"
        elif dataset_label != "POINT_IN_TIME":
            recommendation = "RESEARCH_ONLY_SYNTHETIC_EVIDENCE"
            target = None
        else:
            recommendation = "HOLD_EVIDENCE_GAPS"
            target = None

        return ValidationReview(
            review_id=f"gate-review-{uuid4().hex}",
            strategy_id=evidence.strategy_id,
            strategy_version=evidence.strategy_version,
            current_state=current_state,
            recommended_state=target,
            recommendation=recommendation,
            outcomes=outcomes,
            health=health,
            evidence_hash=evidence.evidence_hash,
            evidence_ids=evidence.evidence_ids,
            generated_at=utc_now(),
            rule_version=self.config.rule_version,
            rule_config_hash=self.config.config_hash,
            dataset_label=dataset_label,
        )

    def _health(self, outcomes: tuple[GateOutcome, ...]) -> StrategyHealthScore:
        """Calculate the fixed 20/20/20/25/15 score without filling missing evidence."""
        lookup = {item.gate: item.score for item in outcomes}
        components = {
            "data_quality": lookup.get(GateName.DATA_INTEGRITY),
            "factor_validity": lookup.get(GateName.FACTOR_VALIDITY),
            "robustness": lookup.get(GateName.ROBUSTNESS),
            "out_of_sample": lookup.get(GateName.OUT_OF_SAMPLE),
            "execution": lookup.get(GateName.PAPER_TRADING),
        }
        partial = sum(
            max(0.0, min(100.0, float(value))) * float(self.config.health_weights[name])
            for name, value in components.items() if value is not None
        )
        coverage = sum(
            float(self.config.health_weights[name])
            for name, value in components.items() if value is not None
        )
        missing = tuple(name for name, value in components.items() if value is None)
        score = round(partial, 6) if not missing else None
        grade = None
        if score is not None:
            grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"
        return StrategyHealthScore(
            score=score,
            partial_score=round(partial, 6),
            coverage=round(coverage, 6),
            grade=grade,
            components=components,
            weights=dict(self.config.health_weights),
            missing_components=missing,
        )

    def describe(self) -> dict:
        """Expose the fixed gate contract for reports and read-only UIs."""
        return {
            "engine_version": self.engine_version,
            "rule_version": self.config.rule_version,
            "rule_config_hash": self.config.config_hash,
            "health_weights": dict(self.config.health_weights),
            "thresholds": asdict(self.config),
            "human_approval_required": True,
            "ai_can_approve": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_auto_promote": False,
        }

