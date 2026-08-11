from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import re
from typing import Any, Mapping

from ..quant_lab.contracts import json_safe, sha256_json, utc_now


EVOLUTION_SCHEMA_VERSION = "pangu-strategy-evolution-v1.0.0"
EVOLUTION_SERVICE_VERSION = "strategy-evolution-engine-v1.0.0"
FACTOR_NAMES = (
    "value", "quality", "growth", "momentum", "trend",
    "low_risk", "liquidity",
)
HEALTH_WEIGHTS = {
    "performance": 0.25,
    "risk": 0.20,
    "factor": 0.25,
    "execution": 0.15,
    "environment": 0.15,
}
_HASH = re.compile(r"^[0-9a-fA-F]{64}$")


class StrategyEvolutionError(RuntimeError):
    """Base error for evidence, lifecycle and immutable evolution failures."""


class EvolutionEvidenceError(StrategyEvolutionError):
    """Reject missing, mismatched or mutable upstream evidence."""


class EvolutionStateError(StrategyEvolutionError):
    """Reject non-sequential or non-human lifecycle decisions."""


class EvolutionArtifactError(StrategyEvolutionError):
    """Reject a changed report or manifest after it has been sealed."""


class EvolutionLifecycleState(str, Enum):
    """Describe research governance only; no value enables real trading."""

    DRAFT = "DRAFT"
    RESEARCH = "RESEARCH"
    VALIDATED = "VALIDATED"
    PAPER_RUNNING = "PAPER_RUNNING"
    PRODUCTION_CANDIDATE = "PRODUCTION_CANDIDATE"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"


class EvolutionHealthStatus(str, Enum):
    """Separate health observation from lifecycle and execution state."""

    HEALTHY = "HEALTHY"
    WATCH = "WATCH"
    DECAYING = "DECAYING"
    CRITICAL = "CRITICAL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class StrategyBranchSpec:
    """Identify one immutable strategy branch without storing executable code."""

    strategy_id: str
    strategy_name: str
    version: str
    branch_name: str
    parent_version: str | None
    version_hash: str
    code_hash: str
    parameter_hash: str
    factor_version: str
    data_version: str
    dataset_label: str
    validation_state: str
    validation_review_id: str | None
    evidence_ids: tuple[str, ...]
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        """Validate immutable identities and prohibit untraceable branches."""
        required = (
            self.strategy_id, self.strategy_name, self.version, self.branch_name,
            self.factor_version, self.data_version, self.dataset_label,
        )
        if any(not str(value).strip() for value in required):
            raise EvolutionEvidenceError("策略分支身份字段不得为空")
        for label, value in (
            ("version_hash", self.version_hash),
            ("code_hash", self.code_hash),
            ("parameter_hash", self.parameter_hash),
        ):
            if not _HASH.fullmatch(str(value)):
                raise EvolutionEvidenceError(f"{label}必须是64位SHA-256")
        if self.dataset_label not in {"POINT_IN_TIME", "SYNTHETIC_TEST_ONLY"}:
            raise EvolutionEvidenceError("策略分支必须声明点时或合成测试数据")
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(self.evidence_ids)))
        if not self.evidence_ids:
            raise EvolutionEvidenceError("策略分支证据链不得为空")

    @property
    def spec_hash(self) -> str:
        """Return the immutable branch identity hash."""
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class StrategyObservation:
    """Normalize one sealed review bundle into monitor-owned read-only metrics."""

    strategy_id: str
    strategy_version: str
    review_id: str
    observed_at: str
    dataset_label: str
    performance: Mapping[str, float | None]
    risk: Mapping[str, float | None]
    factors: Mapping[str, Mapping[str, float | None]]
    execution: Mapping[str, float | None]
    environment: Mapping[str, Any]
    evidence_ids: tuple[str, ...]
    evidence_hash: str
    data_gaps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Require a complete evidence identity while allowing honest metric gaps."""
        if not self.strategy_id or not self.strategy_version or not self.review_id:
            raise EvolutionEvidenceError("策略观察必须绑定策略、版本和审查")
        if not _HASH.fullmatch(str(self.evidence_hash)):
            raise EvolutionEvidenceError("策略观察evidence_hash无效")
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(self.evidence_ids)))
        object.__setattr__(self, "data_gaps", tuple(dict.fromkeys(self.data_gaps)))
        if not self.evidence_ids:
            raise EvolutionEvidenceError("策略观察证据链不得为空")

    def as_dict(self) -> dict[str, Any]:
        """Return a canonical JSON-safe observation for hashing and persistence."""
        return json_safe(asdict(self))


def stable_id(prefix: str, *parts: Any) -> str:
    """Create deterministic identifiers so retried evaluations remain idempotent."""
    digest = sha256_json({"prefix": prefix, "parts": list(parts)})
    return f"{prefix}-{digest[:24]}"


def safety_contract() -> dict[str, bool]:
    """Expose the immutable capability ceiling at every boundary."""
    return {
        "can_trade": False,
        "can_create_orders": False,
        "can_modify_strategy": False,
        "can_modify_parameters": False,
        "can_modify_factor_weights": False,
        "can_replace_production_strategy": False,
        "can_auto_transition": False,
        "can_launch_experiment": False,
    }

