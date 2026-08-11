from __future__ import annotations

from typing import Any, Mapping

from ..quant_lab.contracts import sha256_json, utc_now
from .contracts import (
    EvolutionLifecycleState,
    EvolutionStateError,
    stable_id,
)
from .store import StrategyEvolutionStore


_NEXT = {
    EvolutionLifecycleState.DRAFT.value: EvolutionLifecycleState.RESEARCH.value,
    EvolutionLifecycleState.RESEARCH.value: EvolutionLifecycleState.VALIDATED.value,
    EvolutionLifecycleState.VALIDATED.value: EvolutionLifecycleState.PAPER_RUNNING.value,
    EvolutionLifecycleState.PAPER_RUNNING.value: EvolutionLifecycleState.PRODUCTION_CANDIDATE.value,
    EvolutionLifecycleState.PRODUCTION_CANDIDATE.value: EvolutionLifecycleState.DEPRECATED.value,
    EvolutionLifecycleState.DEPRECATED.value: EvolutionLifecycleState.RETIRED.value,
}


class StrategyLifecycleManager:
    """Own the manual research lifecycle without touching Validation Gate or execution."""

    manager_version = "strategy-evolution-lifecycle-v1.0.0"

    def __init__(self, store: StrategyEvolutionStore) -> None:
        self.store = store

    def request(
        self,
        *,
        strategy_id: str,
        version: str,
        target_state: str,
        requested_by: str,
        reason: str,
        eligibility: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create an evidence-bound request only for the next sequential state."""
        self._human(requested_by)
        branch = self.store.branch(strategy_id, version)
        expected = _NEXT.get(str(branch["lifecycle_state"]))
        if expected != target_state:
            raise EvolutionStateError(
                f"生命周期必须顺序变化，当前{branch['lifecycle_state']}下一状态为{expected}"
            )
        reasons = list(eligibility.get("block_reasons") or [])
        if not eligibility.get("eligible"):
            raise EvolutionStateError("生命周期申请证据不足：" + "；".join(reasons))
        evidence_ids = list(dict.fromkeys(eligibility.get("evidence_ids") or []))
        if not evidence_ids:
            raise EvolutionStateError("生命周期申请必须绑定证据")
        requested_at = utc_now()
        evidence_hash = sha256_json({
            "branch_spec_hash": branch["spec_hash"],
            "from_state": branch["lifecycle_state"],
            "to_state": target_state,
            "evidence_ids": evidence_ids,
            "eligibility": dict(eligibility),
        })
        payload = {
            "request_id": stable_id(
                "evolution-transition", strategy_id, version,
                branch["lifecycle_state"], target_state, evidence_hash,
            ),
            "strategy_id": strategy_id,
            "strategy_version": version,
            "from_state": branch["lifecycle_state"],
            "to_state": target_state,
            "requested_by": requested_by,
            "request_reason": reason,
            "evidence_ids": evidence_ids,
            "evidence_hash": evidence_hash,
            "requested_at": requested_at,
            "human_approval_required": True,
            "ai_can_approve": False,
            "can_auto_transition": False,
            "can_trade": False,
            "can_create_orders": False,
        }
        return self.store.create_transition(payload)

    def decide(
        self,
        request_id: str,
        *,
        approved: bool,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Apply a human decision to the research lifecycle only."""
        self._human(actor)
        return self.store.decide_transition(
            request_id,
            status="APPROVED" if approved else "REJECTED",
            decided_by=actor,
            decision_reason=reason,
        )

    @staticmethod
    def next_state(current: str) -> str | None:
        """Return the sole sequential next state or None after retirement."""
        return _NEXT.get(current)

    @staticmethod
    def _human(actor: str) -> None:
        """Reject AI and system identities from lifecycle governance."""
        normalized = str(actor or "").strip().lower()
        if len(normalized) < 2 or normalized in {
            "ai", "system", "agent", "deepseek", "model", "scheduler",
        }:
            raise EvolutionStateError("策略生命周期必须由明确人工身份操作")

