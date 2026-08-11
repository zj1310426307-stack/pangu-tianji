from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
import math
from typing import Any, Callable, Mapping

from ..contracts import FrozenMapping, sha256_json
from ..exceptions import ContractError


ROBUSTNESS_SCHEMA_VERSION = "pangu-strategy-robustness-v1.0.0"
MARKET_REGIMES = ("bull", "bear", "sideways")


@dataclass(frozen=True)
class ScenarioRequest:
    """Describe one predeclared historical scenario without choosing an optimum."""

    scenario_id: str
    family: str
    overrides: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "overrides", FrozenMapping(self.overrides))
        if not self.scenario_id or not self.family:
            raise ContractError("稳健性场景必须包含scenario_id和family")
        if self.family not in {"baseline", "parameter", "delay"}:
            raise ContractError("不支持的稳健性场景类型")


@dataclass(frozen=True)
class StrategyPath:
    """Carry one read-only historical strategy path produced by the shared backtest route.

    Returns are deliberately gross-before-cost so every cost stress uses the same
    return path and explicit turnover evidence rather than double-counting costs.
    """

    scenario_id: str
    dates: tuple[str, ...]
    gross_returns: tuple[float, ...]
    turnovers: tuple[float, ...]
    market_regimes: tuple[str | None, ...] = field(default_factory=tuple)
    market_regime_source: str = "unavailable"
    engine_version: str = ""
    result_hash: str = ""
    return_basis: str = "gross_before_cost"
    used_shared_research_pipeline: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "dates", tuple(str(item) for item in self.dates))
        object.__setattr__(self, "gross_returns", tuple(float(item) for item in self.gross_returns))
        object.__setattr__(self, "turnovers", tuple(float(item) for item in self.turnovers))
        regimes = self.market_regimes or tuple(None for _ in self.dates)
        object.__setattr__(self, "market_regimes", tuple(regimes))
        self.validate()

    def validate(self) -> None:
        """Reject misaligned, non-finite or non-production-equivalent scenario evidence."""
        count = len(self.dates)
        if count < 2 or len(self.gross_returns) != count or len(self.turnovers) != count:
            raise ContractError("策略路径日期、收益和换手必须等长且至少2期")
        if len(self.market_regimes) != count:
            raise ContractError("市场状态必须与策略路径等长")
        parsed = []
        for value in self.dates:
            try:
                parsed.append(date.fromisoformat(value))
            except ValueError as exc:
                raise ContractError("策略路径日期必须为YYYY-MM-DD") from exc
        if parsed != sorted(parsed) or len(set(parsed)) != count:
            raise ContractError("策略路径日期必须严格递增且唯一")
        if any(not math.isfinite(value) or value <= -1 for value in self.gross_returns):
            raise ContractError("策略收益必须有限且大于-100%")
        if any(not math.isfinite(value) or value < 0 for value in self.turnovers):
            raise ContractError("策略换手必须为非负有限数")
        invalid_regimes = {item for item in self.market_regimes if item not in {*MARKET_REGIMES, None}}
        if invalid_regimes:
            raise ContractError("市场状态只支持bull、bear、sideways或缺失")
        if self.return_basis != "gross_before_cost":
            raise ContractError("成本压力要求gross_before_cost收益口径")
        if not self.used_shared_research_pipeline:
            raise ContractError("稳健性场景必须来自共享ResearchPipeline历史链路")
        if not self.engine_version or not self.result_hash:
            raise ContractError("策略路径必须绑定历史引擎版本和结果哈希")
        if any(self.market_regimes) and self.market_regime_source == "unavailable":
            raise ContractError("显式市场状态必须绑定点时来源")

    @property
    def path_hash(self) -> str:
        """Hash the complete scenario evidence for replay and registry lineage."""
        return sha256_json(asdict(self))


ScenarioRunner = Callable[[ScenarioRequest], StrategyPath]


@dataclass(frozen=True)
class RobustnessConfig:
    """Freeze all declared stresses, statistical thresholds and scoring weights."""

    base_portfolio_size: int = 5
    base_single_stock_limit: float = 0.15
    base_total_exposure: float = 0.60
    base_rebalance_frequency: str = "weekly"
    portfolio_sizes: tuple[int, ...] = (3, 5, 8, 10, 20)
    single_stock_limits: tuple[float, ...] = (0.08, 0.10, 0.15, 0.20)
    total_exposures: tuple[float, ...] = (0.40, 0.50, 0.60, 0.70, 0.80)
    rebalance_frequencies: tuple[str, ...] = ("daily", "weekly", "biweekly", "monthly")
    base_commission_rate: float = 0.0003
    base_slippage_rate: float = 0.0005
    base_sell_tax_rate: float = 0.0005
    commission_rates: tuple[float, ...] = (0.0003, 0.0005, 0.0008)
    slippage_rates: tuple[float, ...] = (0.0005, 0.0010, 0.0020, 0.0050)
    sell_tax_rates: tuple[float, ...] = (0.0005, 0.0010)
    cost_multipliers: tuple[float, ...] = (1.0, 2.0, 3.0)
    delay_modes: tuple[str, ...] = ("t1_open", "t1_close", "t2_close")
    annualization_periods: int = 252
    bootstrap_iterations: int = 1000
    bootstrap_block_length: int = 20
    rolling_window_periods: int = 756
    rolling_step_periods: int = 252
    cscv_slices: int = 8
    minimum_overfit_observations: int = 120
    random_seed: int = 42
    score_weights: Mapping[str, float] = field(default_factory=lambda: {
        "out_of_sample_stability": 0.25,
        "cost_resilience": 0.20,
        "parameter_stability": 0.20,
        "regime_adaptability": 0.20,
        "overfitting_resilience": 0.15,
    })
    schema_version: str = ROBUSTNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        tuple_fields = (
            "portfolio_sizes", "single_stock_limits", "total_exposures",
            "rebalance_frequencies", "commission_rates", "slippage_rates",
            "sell_tax_rates", "cost_multipliers", "delay_modes",
        )
        for name in tuple_fields:
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "score_weights", FrozenMapping(self.score_weights))
        self.validate()

    def validate(self) -> None:
        """Fail closed on incomplete grids or settings that could disguise robustness gaps."""
        if any(value <= 0 for value in self.portfolio_sizes):
            raise ContractError("portfolio_size必须为正整数")
        if any(not 0 < value <= 1 for value in (*self.single_stock_limits, *self.total_exposures)):
            raise ContractError("仓位比例必须位于(0,1]")
        if set(self.rebalance_frequencies) - {"daily", "weekly", "biweekly", "monthly"}:
            raise ContractError("不支持的调仓频率")
        if set(self.delay_modes) - {"t0_close", "t1_open", "t1_close", "t2_close"}:
            raise ContractError("不支持的执行延迟")
        numeric_rates = (*self.commission_rates, *self.slippage_rates, *self.sell_tax_rates)
        if any(value < 0 for value in numeric_rates) or any(value <= 0 for value in self.cost_multipliers):
            raise ContractError("成本率不得为负且成本倍数必须为正")
        if any(value < 0 for value in (
            self.base_commission_rate, self.base_slippage_rate, self.base_sell_tax_rate
        )):
            raise ContractError("基准成本率不得为负")
        if self.annualization_periods <= 0 or self.random_seed < 0:
            raise ContractError("年化周期必须为正且随机种子不得为负")
        if self.bootstrap_iterations < 100 or self.bootstrap_block_length < 2:
            raise ContractError("Bootstrap至少100次且区块长度至少2")
        if self.rolling_window_periods < 20 or self.rolling_step_periods < 1:
            raise ContractError("滚动窗口设置无效")
        if self.cscv_slices < 4 or self.cscv_slices % 2:
            raise ContractError("CSCV切片数必须为不小于4的偶数")
        expected = {
            "out_of_sample_stability", "cost_resilience", "parameter_stability",
            "regime_adaptability", "overfitting_resilience",
        }
        if set(self.score_weights) != expected or abs(sum(self.score_weights.values()) - 1.0) > 1e-9:
            raise ContractError("Robustness Score五项权重必须完整且合计为1")
        if any(value < 0 for value in self.score_weights.values()):
            raise ContractError("Robustness Score权重不得为负")

    @property
    def config_hash(self) -> str:
        """Return a stable identity for all declared robustness choices."""
        return sha256_json(asdict(self))

    def scenario_requests(self) -> tuple[ScenarioRequest, ...]:
        """Generate a fixed one-factor-at-a-time grid without Cartesian optimization."""
        requests = [ScenarioRequest("baseline", "baseline", {})]
        grids = (
            ("portfolio_size", self.portfolio_sizes),
            ("single_stock_limit", self.single_stock_limits),
            ("total_exposure", self.total_exposures),
            ("rebalance_frequency", self.rebalance_frequencies),
        )
        for parameter, values in grids:
            for value in values:
                requests.append(ScenarioRequest(
                    f"parameter.{parameter}.{value}", "parameter", {parameter: value}
                ))
        for mode in self.delay_modes:
            requests.append(ScenarioRequest(
                f"delay.{mode}", "delay", {"execution_delay": mode}
            ))
        return tuple(requests)


@dataclass(frozen=True)
class RobustnessResult:
    """Expose one sealed descriptive result with every execution capability disabled."""

    experiment_id: str
    run_id: str
    dataset_version: str
    dataset_label: str
    config_hash: str
    metrics: Mapping[str, Any]
    report: Mapping[str, Any]
    artifact_manifest: Mapping[str, Any]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = ROBUSTNESS_SCHEMA_VERSION
    investment_validity: str = "NOT_ESTABLISHED"
    can_trade: bool = False
    can_create_orders: bool = False
    can_modify_strategy: bool = False
    can_modify_factor_weights: bool = False
    can_promote_strategy: bool = False

    def __post_init__(self) -> None:
        if any((self.can_trade, self.can_create_orders, self.can_modify_strategy,
                self.can_modify_factor_weights, self.can_promote_strategy)):
            raise ContractError("稳健性研究不得拥有交易、修改或策略晋级能力")

    @property
    def result_hash(self) -> str:
        """Hash the completed result identity and all referenced evidence."""
        return sha256_json(asdict(self))
