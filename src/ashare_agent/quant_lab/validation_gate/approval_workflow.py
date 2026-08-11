from __future__ import annotations

import re

from ..exceptions import InvalidStateTransitionError
from .contracts import ApprovalStatus, StrategyState
from .promotion import PromotionPolicy
from .strategy_registry import StrategyRegistry


_NON_HUMAN = re.compile(r"^(?:ai|system|scheduler|automation|model|deepseek)$", re.I)


class ApprovalWorkflow:
    """Separate system recommendation from an explicit, attributable human decision."""

    def __init__(self, registry: StrategyRegistry, policy: PromotionPolicy | None = None) -> None:
        self.registry = registry
        self.policy = policy or PromotionPolicy()

    def request(self, review_id: str, requested_by: str) -> dict:
        """Create a pending request without changing the strategy state."""
        actor = self._human(requested_by)
        review = self.registry.review(review_id)
        target = review.get("recommended_state")
        if not target:
            raise InvalidStateTransitionError("系统未建议晋级，不能创建审批申请")
        self.policy.validate(StrategyState(review["current_state"]), StrategyState(target))
        return self.registry.create_promotion(review_id=review_id, requested_by=actor)

    def approve(self, promotion_id: str, *, approved_by: str, reason: str) -> dict:
        """Apply one sequential research-state promotion after explicit human approval."""
        actor = self._human(approved_by)
        explanation = self._reason(reason)
        promotion = self.registry.promotion(promotion_id)
        self.policy.validate(
            StrategyState(promotion["from_state"]), StrategyState(promotion["to_state"])
        )
        return self.registry.decide_promotion(
            promotion_id=promotion_id,
            status=ApprovalStatus.APPROVED,
            decided_by=actor,
            reason=explanation,
        )

    def reject(self, promotion_id: str, *, rejected_by: str, reason: str) -> dict:
        """Record a human rejection without mutating the strategy state."""
        return self.registry.decide_promotion(
            promotion_id=promotion_id,
            status=ApprovalStatus.REJECTED,
            decided_by=self._human(rejected_by),
            reason=self._reason(reason),
        )

    def request_retirement(self, review_id: str, requested_by: str) -> dict:
        """Open a manual retirement request; automatic retirement is forbidden."""
        return self.registry.create_retirement(
            review_id=review_id,
            requested_by=self._human(requested_by),
        )

    @staticmethod
    def _human(value: str) -> str:
        actor = str(value or "").strip()
        if len(actor) < 2 or len(actor) > 100 or _NON_HUMAN.fullmatch(actor):
            raise ValueError("审批人必须是可追溯的人工身份，AI/系统不得审批")
        return actor

    @staticmethod
    def _reason(value: str) -> str:
        reason = str(value or "").strip()
        if len(reason) < 8 or len(reason) > 1000:
            raise ValueError("审批理由必须为8至1000字符")
        return reason
