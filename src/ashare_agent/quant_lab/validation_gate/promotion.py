from __future__ import annotations

from dataclasses import dataclass

from ..exceptions import InvalidStateTransitionError
from .contracts import StrategyState


_FORWARD_TRANSITIONS = {
    StrategyState.DRAFT: StrategyState.RESEARCH,
    StrategyState.RESEARCH: StrategyState.FACTOR_VALIDATED,
    StrategyState.FACTOR_VALIDATED: StrategyState.ROBUSTNESS_VALIDATED,
    StrategyState.ROBUSTNESS_VALIDATED: StrategyState.OUT_OF_SAMPLE,
    StrategyState.OUT_OF_SAMPLE: StrategyState.PAPER_TRADING,
}


@dataclass(frozen=True)
class PromotionPolicy:
    """Own the only allowed research-lifecycle transitions."""

    policy_version: str = "strategy-promotion-policy-v1.0.0"

    def next_state(self, current: StrategyState) -> StrategyState | None:
        """Return the next research state; PAPER_TRADING never leads to live trading."""
        return _FORWARD_TRANSITIONS.get(current)

    def validate(self, current: StrategyState, target: StrategyState) -> None:
        """Reject state skipping and reserve RETIRED for explicit human retirement."""
        if current == StrategyState.RETIRED:
            raise InvalidStateTransitionError("已退役策略不得恢复或晋级")
        if target == StrategyState.RETIRED:
            return
        expected = self.next_state(current)
        if expected != target:
            raise InvalidStateTransitionError(
                f"非法策略状态迁移：{current.value} -> {target.value}"
            )

    def required_gates(self, target: StrategyState) -> tuple[str, ...]:
        """Declare cumulative evidence required before one forward promotion."""
        mapping = {
            StrategyState.RESEARCH: ("DATA_INTEGRITY",),
            StrategyState.FACTOR_VALIDATED: ("DATA_INTEGRITY", "FACTOR_VALIDITY"),
            StrategyState.ROBUSTNESS_VALIDATED: (
                "DATA_INTEGRITY", "FACTOR_VALIDITY", "ROBUSTNESS",
            ),
            StrategyState.OUT_OF_SAMPLE: (
                "DATA_INTEGRITY", "FACTOR_VALIDITY", "ROBUSTNESS", "OUT_OF_SAMPLE",
            ),
            StrategyState.PAPER_TRADING: (
                "DATA_INTEGRITY", "FACTOR_VALIDITY", "ROBUSTNESS", "OUT_OF_SAMPLE",
            ),
        }
        return mapping.get(target, ())

