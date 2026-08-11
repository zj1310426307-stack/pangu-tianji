from __future__ import annotations

from typing import Any

from ..contracts import sha256_json
from .contracts import StrategyVersionSpec, ValidationReview


class ResearchCommitteeReport:
    """Render a six-section, evidence-linked report for human committee review."""

    generator_version = "strategy-review-report-v1.0.0"

    def generate(
        self,
        *,
        strategy: StrategyVersionSpec,
        review: ValidationReview,
    ) -> dict[str, Any]:
        """Explain the gate output without turning AI or scores into approval authority."""
        outcomes = {item.gate.value: item for item in review.outcomes}
        sections = {
            "01_strategy_overview": {
                "strategy_id": strategy.strategy_id,
                "name": strategy.name,
                "version": strategy.version,
                "creator": strategy.creator,
                "description": strategy.description,
                "current_state": review.current_state.value,
                "research_run_id": strategy.research_run_id,
            },
            "02_data_quality": self._outcome(outcomes["DATA_INTEGRITY"]),
            "03_factor_research": self._outcome(outcomes["FACTOR_VALIDITY"]),
            "04_robustness_and_oos": {
                "robustness": self._outcome(outcomes["ROBUSTNESS"]),
                "out_of_sample": self._outcome(outcomes["OUT_OF_SAMPLE"]),
                "paper_execution": self._outcome(outcomes["PAPER_TRADING"]),
                "health": {
                    "score": review.health.score,
                    "partial_score": review.health.partial_score,
                    "coverage": review.health.coverage,
                    "grade": review.health.grade,
                    "components": dict(review.health.components),
                    "missing_components": list(review.health.missing_components),
                    "diagnostic_only": True,
                },
            },
            "05_risk_warnings": {
                "failed_or_missing_gates": [
                    item.gate.value for item in review.outcomes
                    if item.status.value != "PASSED"
                ],
                "synthetic_evidence": review.dataset_label != "POINT_IN_TIME",
                "score_is_not_approval": True,
                "paper_is_not_live": True,
                "no_profit_guarantee": True,
            },
            "06_committee_recommendation": {
                "system_recommendation": review.recommendation,
                "recommended_state": (
                    review.recommended_state.value if review.recommended_state else None
                ),
                "human_approval_required": True,
                "approval_status": "NOT_REQUESTED",
                "ai_can_approve": False,
                "automatic_promotion": False,
            },
        }
        report = {
            "schema_version": "pangu-strategy-review-report-v1.0.0",
            "generator_version": self.generator_version,
            "report_type": "strategy_validation_committee_review",
            "report_id": f"committee-{review.review_id}",
            "review_id": review.review_id,
            "generated_at": review.generated_at,
            "strategy_id": strategy.strategy_id,
            "strategy_version": strategy.version,
            "strategy_version_hash": strategy.version_hash,
            "dataset_label": review.dataset_label,
            "rule_version": review.rule_version,
            "rule_config_hash": review.rule_config_hash,
            "evidence_hash": review.evidence_hash,
            "evidence_ids": list(review.evidence_ids),
            "sections": sections,
            "ai_research_committee_interface": {
                "status": "reserved_summary_only",
                "permission": "summarize_existing_evidence",
                "can_approve": False,
                "can_reject": False,
                "can_promote_strategy": False,
                "can_modify_strategy": False,
            },
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_auto_promote": False,
        }
        report["report_hash"] = sha256_json(report)
        return report

    @staticmethod
    def _outcome(outcome) -> dict[str, Any]:
        return {
            "gate": outcome.gate.value,
            "status": outcome.status.value,
            "score": outcome.score,
            "summary": outcome.summary,
            "checks": [dict(item) for item in outcome.checks],
            "evidence_ids": list(outcome.evidence_ids),
            "block_reasons": list(outcome.block_reasons),
        }

