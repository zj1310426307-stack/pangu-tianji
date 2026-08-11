from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from ..quant_lab.contracts import sha256_json, utc_now


QUANT_AI_CONTRACT_VERSION = "pangu-quant-ai-v1.0.0"


class ResearchClaimLabel(str, Enum):
    """Separate observed facts from interpretation and testable hypotheses."""

    FACT = "FACT"
    INFERENCE = "INFERENCE"
    HYPOTHESIS = "HYPOTHESIS"


class ResearchAgentType(str, Enum):
    """Name the four bounded research roles required by PANGU-V2-009."""

    STRATEGY = "strategy_analyst"
    FACTOR = "factor_analyst"
    RISK = "risk_analyst"
    MARKET = "market_analyst"


class ResearchReportStatus(str, Enum):
    """Distinguish grounded publication from honest evidence/model degradation."""

    PUBLISHED = "published"
    DEGRADED = "degraded"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ResearchEvidenceItem:
    """Carry one immutable, source-hashed citation into the AI research boundary."""

    evidence_id: str
    source_type: str
    source_id: str
    source_hash: str
    observed_at: str | None
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.source_id or len(self.source_hash) != 64:
            raise ValueError("AI研究证据身份或哈希无效")
        if sha256_json(dict(self.payload)) != self.source_hash:
            raise ValueError("AI研究证据内容与source_hash不一致")


@dataclass(frozen=True)
class ResearchEvidenceBundle:
    """Bind a strategy version to a complete read-only research evidence snapshot."""

    strategy_id: str | None
    strategy_version: str | None
    experiment_id: str | None
    research_run_id: str | None
    dataset_label: str | None
    generated_at: str = field(default_factory=utc_now)
    items: tuple[ResearchEvidenceItem, ...] = ()
    data_gaps: tuple[str, ...] = ()
    reader_version: str = "quant-ai-experiment-reader-v1.0.0"
    can_trade: bool = False
    can_create_orders: bool = False
    can_modify_strategy: bool = False
    can_modify_factor_weights: bool = False
    can_approve_strategy: bool = False

    def __post_init__(self) -> None:
        if any((
            self.can_trade,
            self.can_create_orders,
            self.can_modify_strategy,
            self.can_modify_factor_weights,
            self.can_approve_strategy,
        )):
            raise ValueError("AI量化研究证据不得拥有执行或治理权限")
        ids = [item.evidence_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("AI研究证据ID必须唯一")

    @property
    def evidence_hash(self) -> str:
        """Hash evidence identities and contents without including generation time."""
        return sha256_json({
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "experiment_id": self.experiment_id,
            "research_run_id": self.research_run_id,
            "dataset_label": self.dataset_label,
            "items": [asdict(item) for item in self.items],
            "data_gaps": list(self.data_gaps),
        })

    def public_payload(self) -> dict[str, Any]:
        """Serialize the bounded input consumed by deterministic agents and models."""
        return {
            "contract_version": QUANT_AI_CONTRACT_VERSION,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "experiment_id": self.experiment_id,
            "research_run_id": self.research_run_id,
            "dataset_label": self.dataset_label,
            "generated_at": self.generated_at,
            "evidence_hash": self.evidence_hash,
            "evidence_items": [asdict(item) for item in self.items],
            "data_gaps": list(self.data_gaps),
            "permission": "RESEARCH_READ_ONLY",
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
            "can_approve_strategy": False,
        }


@dataclass(frozen=True)
class ResearchClaim:
    """Represent one cited analyst statement with explicit epistemic status."""

    claim_id: str
    label: ResearchClaimLabel
    title: str
    statement: str
    evidence_ids: tuple[str, ...]
    confidence: float
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.claim_id or not self.title or not self.statement:
            raise ValueError("AI研究结论字段不完整")
        if not self.evidence_ids:
            raise ValueError("每条AI研究结论必须绑定evidence_id")
        if not 0 <= float(self.confidence) <= 1:
            raise ValueError("AI研究置信度必须在0到1之间")


@dataclass(frozen=True)
class AnalystResult:
    """Return one role's deterministic evidence interpretation."""

    agent_type: ResearchAgentType
    availability: str
    summary: str
    claims: tuple[ResearchClaim, ...]
    data_gaps: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type.value,
            "availability": self.availability,
            "summary": self.summary,
            "claims": [
                {**asdict(item), "label": item.label.value}
                for item in self.claims
            ],
            "data_gaps": list(self.data_gaps),
            "used_for_execution": False,
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
        }

