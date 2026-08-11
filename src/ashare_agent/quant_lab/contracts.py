from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, Mapping

from .exceptions import ContractError


QUANT_LAB_SCHEMA_VERSION = "pangu-quant-lab-v1.0.0"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")


class FrozenMapping(Mapping[str, Any]):
    """Provide a recursively immutable, deepcopy-safe mapping for experiment config."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = {str(key): _freeze(item) for key, item in values.items()}

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, Any]) -> "FrozenMapping":
        return self


def _freeze(value: Any) -> Any:
    """Recursively freeze mappings and sequences used by ExperimentSpec."""
    if isinstance(value, FrozenMapping):
        return value
    if isinstance(value, Mapping):
        return FrozenMapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class ExperimentState(str, Enum):
    """Govern an experiment independently from strategy promotion state."""

    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    DATA_BLOCKED = "DATA_BLOCKED"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class StrategyLifecycleState(str, Enum):
    """Limit this stage to evidence preparation, never deployment promotion."""

    DRAFT = "DRAFT"
    DATA_VERIFIED = "DATA_VERIFIED"


class ValidationStatus(str, Enum):
    """Describe evidence availability without disguising gaps as passes."""

    PASS = "PASS"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"


def json_safe(value: Any) -> Any:
    """Convert dataclasses, dates and finite numerics into canonical JSON values."""
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("实验合同禁止NaN或Infinity")
    return value


def canonical_bytes(value: Any) -> bytes:
    """Serialize evidence deterministically for hashing and immutability checks."""
    return json.dumps(
        json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    """Return a full SHA-256 over canonical JSON evidence."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def aggregate_dataset_version(data_versions: list[str] | tuple[str, ...]) -> str:
    """Build one stable dataset version from one or many daily Data Center versions."""
    versions = tuple(sorted(set(str(item) for item in data_versions if str(item))))
    if not versions:
        raise ContractError("无法从空Data Center版本集合生成dataset_version")
    return versions[0] if len(versions) == 1 else sha256_json({"data_versions": versions})[:16]


def utc_now() -> str:
    """Return one timezone-aware UTC timestamp for lifecycle audit."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DateRange:
    """Represent one inclusive calendar range in an experiment split."""

    start: str
    end: str

    def validate(self, name: str) -> None:
        """Reject reversed or invalid ISO date ranges."""
        try:
            start = date.fromisoformat(self.start)
            end = date.fromisoformat(self.end)
        except ValueError as exc:
            raise ContractError(f"{name}日期必须为YYYY-MM-DD") from exc
        if start > end:
            raise ContractError(f"{name}起始日期晚于结束日期")


@dataclass(frozen=True)
class SplitDefinition:
    """Pin training, validation, test and untouched final-holdout boundaries."""

    method: str
    train: DateRange
    validation: DateRange
    test: DateRange
    purge_days: int = 0
    embargo_days: int = 0
    final_holdout: DateRange | None = None

    def validate(self) -> None:
        """Enforce chronology and no overlap across all research partitions."""
        if self.method not in {"expanding_walk_forward", "rolling_walk_forward", "final_holdout"}:
            raise ContractError("不支持的实验切分方法")
        if self.purge_days < 0 or self.embargo_days < 0:
            raise ContractError("purge_days和embargo_days不得为负")
        for name, value in (("train", self.train), ("validation", self.validation), ("test", self.test)):
            value.validate(name)
        train_end = date.fromisoformat(self.train.end)
        validation_start = date.fromisoformat(self.validation.start)
        validation_end = date.fromisoformat(self.validation.end)
        test_start = date.fromisoformat(self.test.start)
        if not train_end < validation_start or not validation_end < test_start:
            raise ContractError("训练、验证与测试区间必须严格无重叠且按时间排序")
        if self.final_holdout:
            self.final_holdout.validate("final_holdout")
            if date.fromisoformat(self.test.end) >= date.fromisoformat(self.final_holdout.start):
                raise ContractError("final_holdout必须晚于测试区间且不得暴露给选择过程")


@dataclass(frozen=True)
class ExperimentSpec:
    """Freeze all data, code, strategy, split and cost inputs to one experiment."""

    experiment_id: str
    name: str
    data_center_run_ids: tuple[str, ...]
    dataset_version: str
    data_start: str
    data_end: str
    strategy_version: str
    factor_version: str
    factor_contract_hash: str
    code_hash: str
    code_commit: str | None
    benchmark_id: str
    benchmark_version: str
    cost_model_version: str
    split: SplitDefinition
    random_seed: int
    config: Mapping[str, Any]
    dataset_label: str = "POINT_IN_TIME"
    schema_version: str = QUANT_LAB_SCHEMA_VERSION
    config_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_center_run_ids", tuple(self.data_center_run_ids))
        object.__setattr__(self, "config", _freeze(self.config))
        self.validate()
        object.__setattr__(self, "config_hash", sha256_json(self.config))

    def validate(self) -> None:
        """Fail closed when a specification cannot be independently reproduced."""
        if not _ID.fullmatch(self.experiment_id):
            raise ContractError("experiment_id格式无效")
        if not self.name.strip() or len(self.name) > 200:
            raise ContractError("实验名称不能为空且不得超过200字符")
        if not self.data_center_run_ids:
            raise ContractError("实验必须引用至少一个Data Center run_id")
        if len(set(self.data_center_run_ids)) != len(self.data_center_run_ids):
            raise ContractError("Data Center run_id不得重复")
        if not all((self.dataset_version, self.strategy_version, self.factor_version,
                    self.factor_contract_hash, self.code_hash, self.benchmark_id,
                    self.benchmark_version, self.cost_model_version)):
            raise ContractError("实验版本与哈希字段不得为空")
        DateRange(self.data_start, self.data_end).validate("dataset")
        self.split.validate()
        if self.random_seed < 0:
            raise ContractError("random_seed不得为负")
        if self.dataset_label not in {"POINT_IN_TIME", "SYNTHETIC_TEST_ONLY"}:
            raise ContractError("dataset_label必须明确为真实点时或合成测试数据")
        self._reject_secret_keys(self.config)

    @classmethod
    def _reject_secret_keys(cls, value: Any) -> None:
        """Prevent API keys, tokens and passwords from entering experiment artifacts."""
        if isinstance(value, Mapping):
            for key, item in value.items():
                if re.search(r"(?:api[_-]?key|token|password|secret|authorization|credential)", str(key), re.I):
                    raise ContractError("ExperimentSpec.config不得包含密钥字段")
                cls._reject_secret_keys(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._reject_secret_keys(item)

    @property
    def spec_hash(self) -> str:
        """Hash the full immutable specification including derived config hash."""
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class ValidationEvent:
    """Record one deterministic dataset quality assertion."""

    code: str
    status: ValidationStatus
    message: str
    run_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetValidation:
    """Summarize all evidence checks and the final fail-closed decision."""

    status: ValidationStatus
    checked_at: str
    events: tuple[ValidationEvent, ...]
    dataset_version: str
    run_ids: tuple[str, ...]

    @property
    def can_run(self) -> bool:
        """Allow execution only when no required check is blocked."""
        return self.status in {ValidationStatus.PASS, ValidationStatus.PARTIAL}

    @property
    def validation_hash(self) -> str:
        """Hash the complete quality decision for later replay."""
        return sha256_json(self)


@dataclass(frozen=True)
class ExperimentResult:
    """Capture one immutable run result without implying strategy promotion."""

    experiment_id: str
    run_id: str
    state: ExperimentState
    strategy_state: StrategyLifecycleState
    created_at: str
    started_at: str | None
    completed_at: str | None
    dataset_summary: Mapping[str, Any]
    split_summary: Mapping[str, Any]
    backtest_summary: Mapping[str, Any]
    benchmark_summary: Mapping[str, Any]
    artifact_manifest: Mapping[str, Any]
    warnings: tuple[str, ...] = ()
    block_reasons: tuple[str, ...] = ()
    schema_version: str = QUANT_LAB_SCHEMA_VERSION
    can_trade: bool = False
    can_create_orders: bool = False

    def __post_init__(self) -> None:
        if self.can_trade or self.can_create_orders:
            raise ContractError("Quant Lab结果永远不得拥有交易或创建订单能力")
        if self.state == ExperimentState.COMPLETED and not self.completed_at:
            raise ContractError("COMPLETED结果必须包含completed_at")

    @property
    def result_hash(self) -> str:
        """Return a stable result hash for identical evidence and timestamps."""
        return sha256_json(asdict(self))
