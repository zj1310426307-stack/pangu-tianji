from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from ..contracts import sha256_json
from ..exceptions import ContractError


FACTOR_RESEARCH_SCHEMA_VERSION = "pangu-factor-research-v1.0.0"
FACTOR_NAMES = (
    "value",
    "quality",
    "growth",
    "momentum",
    "trend",
    "low_risk",
    "liquidity",
)
FACTOR_COLUMNS = {name: f"points_{name}" for name in FACTOR_NAMES}
DEFAULT_HORIZONS = (1, 5, 10, 20, 60, 120)
MARKET_REGIMES = ("bull", "bear", "sideways")


@dataclass(frozen=True)
class FactorResearchConfig:
    """Freeze research-only analysis choices without changing production weights."""

    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    quantile_counts: tuple[int, ...] = (5, 10)
    minimum_cross_section: int = 5
    annualization_periods: int = 252
    ablation_horizon: int = 20
    ablation_top_fraction: float = 0.2
    correlation_threshold: float = 0.8
    schema_version: str = FACTOR_RESEARCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "horizons", tuple(int(item) for item in self.horizons))
        object.__setattr__(self, "quantile_counts", tuple(int(item) for item in self.quantile_counts))
        self.validate()

    def validate(self) -> None:
        """Reject incomplete or misleading factor-research settings."""
        if not self.horizons or any(item <= 0 for item in self.horizons):
            raise ContractError("因子研究horizon必须为正整数")
        if len(set(self.horizons)) != len(self.horizons):
            raise ContractError("因子研究horizon不得重复")
        if set(self.quantile_counts) - {5, 10} or not self.quantile_counts:
            raise ContractError("分层研究只支持五分组和十分组")
        if self.minimum_cross_section < 3:
            raise ContractError("横截面最少需要3只证券")
        if self.annualization_periods <= 0:
            raise ContractError("年化周期必须为正数")
        if self.ablation_horizon not in self.horizons:
            raise ContractError("消融持有期必须包含在研究horizon中")
        if not 0 < self.ablation_top_fraction <= 0.5:
            raise ContractError("消融Top比例必须在(0,0.5]范围")
        if not 0 < self.correlation_threshold <= 1:
            raise ContractError("相关性阈值必须在(0,1]范围")

    @property
    def config_hash(self) -> str:
        """Hash the frozen research configuration for reproducibility."""
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class FactorResearchResult:
    """Describe one sealed factor-research run with no execution capability."""

    experiment_id: str
    run_id: str
    dataset_version: str
    dataset_label: str
    strategy_version: str
    factor_version: str
    factor_contract_hash: str
    config_hash: str
    metrics: Mapping[str, Any]
    factor_report: Mapping[str, Any]
    artifact_manifest: Mapping[str, Any]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = FACTOR_RESEARCH_SCHEMA_VERSION
    can_trade: bool = False
    can_create_orders: bool = False
    can_modify_strategy: bool = False
    can_modify_factor_weights: bool = False

    def __post_init__(self) -> None:
        if any((self.can_trade, self.can_create_orders, self.can_modify_strategy,
                self.can_modify_factor_weights)):
            raise ContractError("因子研究结果不得拥有交易或策略修改能力")

    @property
    def result_hash(self) -> str:
        """Hash the sealed result identity and evidence references."""
        return sha256_json(asdict(self))
