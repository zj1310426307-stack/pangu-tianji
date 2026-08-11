from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from ..contracts import json_safe, sha256_json, utc_now
from ..exceptions import ContractError


VALIDATION_GATE_SCHEMA_VERSION = "pangu-strategy-validation-gate-v1.0.0"


class StrategyState(str, Enum):
    """Represent research governance only; no state grants live-trading capability."""

    DRAFT = "DRAFT"
    RESEARCH = "RESEARCH"
    FACTOR_VALIDATED = "FACTOR_VALIDATED"
    ROBUSTNESS_VALIDATED = "ROBUSTNESS_VALIDATED"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    PAPER_TRADING = "PAPER_TRADING"
    RETIRED = "RETIRED"


class GateName(str, Enum):
    DATA_INTEGRITY = "DATA_INTEGRITY"
    FACTOR_VALIDITY = "FACTOR_VALIDITY"
    ROBUSTNESS = "ROBUSTNESS"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    PAPER_TRADING = "PAPER_TRADING"


class GateStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_DUE = "NOT_DUE"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ValidationGateConfig:
    """Freeze conservative, explainable admission thresholds."""

    minimum_factor_observations: int = 60
    minimum_valid_factors: int = 3
    minimum_factor_icir: float = 0.0
    minimum_factor_hit_rate: float = 0.50
    minimum_monotonic_factor_ratio: float = 0.50
    maximum_redundancy_review_candidates: int = 4
    minimum_robustness_score: float = 70.0
    minimum_oos_observations: int = 120
    minimum_oos_sharpe: float = 0.0
    maximum_oos_drawdown: float = 0.20
    minimum_paper_trading_days: int = 90
    maximum_execution_deviation: float = 0.02
    health_weights: Mapping[str, float] = field(default_factory=lambda: {
        "data_quality": 0.20,
        "factor_validity": 0.20,
        "robustness": 0.20,
        "out_of_sample": 0.25,
        "execution": 0.15,
    })
    rule_version: str = "strategy-validation-rules-v1.0.0"

    def __post_init__(self) -> None:
        if self.minimum_factor_observations < 1 or self.minimum_valid_factors < 1:
            raise ContractError("因子准入样本和因子数量必须为正数")
        if self.minimum_oos_observations < 1 or self.minimum_paper_trading_days < 1:
            raise ContractError("样本外与模拟期门槛必须为正数")
        if abs(sum(float(value) for value in self.health_weights.values()) - 1.0) > 1e-9:
            raise ContractError("Strategy Health Score权重之和必须为1")
        expected = {
            "data_quality", "factor_validity", "robustness",
            "out_of_sample", "execution",
        }
        if set(self.health_weights) != expected:
            raise ContractError("Strategy Health Score组件不完整")

    @property
    def config_hash(self) -> str:
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class StrategyVersionSpec:
    """Identify one immutable strategy version and its research lineage."""

    strategy_id: str
    name: str
    version: str
    creator: str
    description: str
    research_run_id: str
    factor_version: str
    parameter_hash: str
    code_hash: str
    dataset_version: str
    dataset_label: str

    def __post_init__(self) -> None:
        required = (
            self.strategy_id, self.name, self.version, self.creator,
            self.research_run_id, self.factor_version, self.parameter_hash,
            self.code_hash, self.dataset_version,
        )
        if any(not str(value).strip() for value in required):
            raise ContractError("策略版本身份字段不得为空")
        if self.dataset_label not in {"POINT_IN_TIME", "SYNTHETIC_TEST_ONLY"}:
            raise ContractError("策略版本必须声明POINT_IN_TIME或SYNTHETIC_TEST_ONLY")

    @property
    def version_hash(self) -> str:
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class ValidationEvidenceBundle:
    """Carry immutable evidence references and normalized gate inputs."""

    strategy_id: str
    strategy_version: str
    data_integrity: Mapping[str, Any]
    factor_report: Mapping[str, Any] | None = None
    robustness_report: Mapping[str, Any] | None = None
    out_of_sample: Mapping[str, Any] | None = None
    paper_trading: Mapping[str, Any] | None = None
    evidence_ids: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.strategy_id or not self.strategy_version:
            raise ContractError("准入证据必须绑定策略及版本")
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        if not self.evidence_ids:
            raise ContractError("策略准入证据链不得为空")

    @property
    def evidence_hash(self) -> str:
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class GateOutcome:
    """Record one deterministic gate decision with its evidence trace."""

    gate: GateName
    status: GateStatus
    score: float | None
    summary: str
    checks: tuple[Mapping[str, Any], ...]
    evidence_ids: tuple[str, ...]
    block_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyHealthScore:
    """Preserve missing components instead of renormalizing incomplete evidence."""

    score: float | None
    partial_score: float
    coverage: float
    grade: str | None
    components: Mapping[str, float | None]
    weights: Mapping[str, float]
    missing_components: tuple[str, ...]
    diagnostic_only: bool = True
    can_promote_strategy: bool = False


@dataclass(frozen=True)
class ValidationReview:
    """Combine all gate outcomes into a non-binding system recommendation."""

    review_id: str
    strategy_id: str
    strategy_version: str
    current_state: StrategyState
    recommended_state: StrategyState | None
    recommendation: str
    outcomes: tuple[GateOutcome, ...]
    health: StrategyHealthScore
    evidence_hash: str
    evidence_ids: tuple[str, ...]
    generated_at: str
    rule_version: str
    rule_config_hash: str
    dataset_label: str
    human_approval_required: bool = True
    ai_can_approve: bool = False
    used_for_execution: bool = False
    can_trade: bool = False
    can_create_orders: bool = False
    can_auto_promote: bool = False

    def __post_init__(self) -> None:
        if self.can_trade or self.can_create_orders or self.can_auto_promote:
            raise ContractError("策略准入审查不得拥有交易或自动晋级能力")

    def as_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))
