from dataclasses import dataclass
from datetime import date, time
import math
from numbers import Real
from pathlib import Path
import re
from typing import Any

import yaml

from .investment_profile import InvestmentProfile
from .research_pipeline import STRATEGY_VERSION


ETF_SYMBOL_RE = re.compile(r"^(?P<code>\d{6})\.(?P<exchange>SH|SZ)$")


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]
    project_root: Path

    @property
    def initial_cash(self) -> float:
        return float(self.raw["account"]["initial_cash"])

    @property
    def symbols(self) -> list[str]:
        return list(self.raw["universe"]["symbols"])

    def path(self, value: str) -> Path:
        return self.project_root / value


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"配置段{name}必须是键值对象")
    return value


def _required(section: dict[str, Any], key: str, qualified_name: str) -> Any:
    if key not in section:
        raise ValueError(f"缺少配置项{qualified_name}")
    return section[key]


def _strict_bool(section: dict[str, Any], key: str, qualified_name: str) -> bool:
    value = _required(section, key, qualified_name)
    if not isinstance(value, bool):
        raise ValueError(f"{qualified_name}必须是布尔值")
    return value


def _strict_int(
    section: dict[str, Any],
    key: str,
    qualified_name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = _required(section, key, qualified_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{qualified_name}必须是整数")
    if minimum is not None and value < minimum:
        raise ValueError(f"{qualified_name}不得小于{minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{qualified_name}不得大于{maximum}")
    return value


def _finite_number(
    section: dict[str, Any],
    key: str,
    qualified_name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_inclusive: bool = True,
    maximum_inclusive: bool = True,
) -> float:
    value = _required(section, key, qualified_name)
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{qualified_name}必须是数值")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{qualified_name}必须是有限数值")
    if minimum is not None:
        too_small = result < minimum if minimum_inclusive else result <= minimum
        if too_small:
            operator = "不得小于" if minimum_inclusive else "必须大于"
            raise ValueError(f"{qualified_name}{operator}{minimum}")
    if maximum is not None:
        too_large = result > maximum if maximum_inclusive else result >= maximum
        if too_large:
            operator = "不得大于" if maximum_inclusive else "必须小于"
            raise ValueError(f"{qualified_name}{operator}{maximum}")
    return result


def _ratio(section: dict[str, Any], key: str, qualified_name: str) -> float:
    return _finite_number(
        section,
        key,
        qualified_name,
        minimum=0.0,
        maximum=1.0,
        minimum_inclusive=False,
    )


def _iso_date(section: dict[str, Any], key: str, qualified_name: str) -> date:
    value = _required(section, key, qualified_name)
    if not isinstance(value, str):
        raise ValueError(f"{qualified_name}必须是YYYY-MM-DD字符串")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{qualified_name}必须是有效的YYYY-MM-DD日期") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{qualified_name}必须使用YYYY-MM-DD格式")
    return parsed


def _validate_symbols(universe: dict[str, Any]) -> list[str]:
    symbols = _required(universe, "symbols", "universe.symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("universe.symbols必须是非空列表")
    if any(not isinstance(symbol, str) for symbol in symbols):
        raise ValueError("universe.symbols中的ETF代码必须是字符串")
    if len(symbols) != len(set(symbols)):
        raise ValueError("universe.symbols不得包含重复ETF代码")

    for symbol in symbols:
        match = ETF_SYMBOL_RE.fullmatch(symbol)
        if match is None:
            raise ValueError(f"无效ETF代码：{symbol}，应为6位数字加.SH或.SZ")
        code = match.group("code")
        exchange = match.group("exchange")
        if exchange == "SH" and not code.startswith("5"):
            raise ValueError(f"无效上交所ETF代码：{symbol}")
        if exchange == "SZ" and not code.startswith("1"):
            raise ValueError(f"无效深交所ETF代码：{symbol}")
    return symbols


def _validate_storage_path(
    storage: dict[str, Any], project_root: Path, key: str
) -> None:
    qualified_name = f"storage.{key}"
    value = _required(storage, key, qualified_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{qualified_name}必须是非空字符串")
    configured = Path(value)
    if configured.is_absolute():
        raise ValueError(f"{qualified_name}必须是项目内相对路径")

    resolved_project_root = project_root.resolve()
    output_root = (resolved_project_root / "output").resolve()
    resolved_path = (resolved_project_root / configured).resolve()
    if output_root == resolved_path or not resolved_path.is_relative_to(output_root):
        raise ValueError(f"{qualified_name}必须位于project_root/output目录内")


def _validate_config(raw: dict[str, Any], project_root: Path) -> None:
    raw.setdefault("investment_profile", {})
    account = _section(raw, "account")
    investment_profile = _section(raw, "investment_profile")
    universe = _section(raw, "universe")
    strategy = _section(raw, "strategy")
    risk = _section(raw, "risk")
    execution = _section(raw, "execution")
    costs = _section(raw, "costs")
    data = _section(raw, "data")
    storage = _section(raw, "storage")
    research = _section(raw, "research")
    paper_account = _section(raw, "paper_account")

    # Keep older v0.9 configuration files loadable while materializing every
    # v1.0 safety field into the validated in-memory contract.
    costs.setdefault("transfer_fee_rate", 0.0)
    research.setdefault("strategy_version", STRATEGY_VERSION)
    research.setdefault("entry_rank", int(research.get("target_count", 3)))
    research.setdefault("hold_rank", int(research.get("recommendation_count", 10)))
    research.setdefault("max_positions_per_industry", int(research.get("target_count", 3)))
    research.setdefault("max_pairwise_correlation", 0.85)
    research.setdefault("max_single_position_pct", float(paper_account.get("target_position_pct", 0.20)))
    research.setdefault("target_total_exposure_pct", float(paper_account.get("max_total_exposure_pct", 0.60)))
    paper_account.setdefault("hold_rank", int(research["hold_rank"]))
    paper_account.setdefault("minimum_holding_days", 0)
    paper_account.setdefault("max_daily_turnover_pct", 1.0)

    initial_cash = _finite_number(
        account,
        "initial_cash",
        "account.initial_cash",
        minimum=0.0,
        minimum_inclusive=False,
    )
    investment_profile.setdefault("capital", initial_cash)
    investment_profile.setdefault("risk_level", "balanced")
    investment_profile.setdefault("investment_horizon", "medium")
    investment_profile.setdefault("max_drawdown_tolerance", 0.10)
    investment_profile.setdefault("investment_style", "balanced")
    InvestmentProfile.from_mapping(
        investment_profile,
        default_capital=initial_cash,
    )
    symbols = _validate_symbols(universe)

    long_window = _strict_int(
        strategy, "long_ma_window", "strategy.long_ma_window", minimum=2
    )
    momentum_window = _strict_int(
        strategy, "momentum_window", "strategy.momentum_window", minimum=1
    )
    if long_window <= momentum_window:
        raise ValueError("strategy.long_ma_window必须大于strategy.momentum_window")
    max_positions = _strict_int(
        strategy,
        "max_positions",
        "strategy.max_positions",
        minimum=1,
        maximum=len(symbols),
    )
    _strict_int(
        strategy,
        "rebalance_weekday",
        "strategy.rebalance_weekday",
        minimum=0,
        maximum=4,
    )
    target_exposure = _ratio(
        strategy,
        "target_total_exposure_pct",
        "strategy.target_total_exposure_pct",
    )

    max_total = _ratio(
        risk, "max_total_exposure_pct", "risk.max_total_exposure_pct"
    )
    max_single = _ratio(
        risk, "max_single_position_pct", "risk.max_single_position_pct"
    )
    max_order = _ratio(risk, "max_order_value_pct", "risk.max_order_value_pct")
    if max_single > max_total:
        raise ValueError("risk.max_single_position_pct不得大于总仓位上限")
    if max_order > max_single:
        raise ValueError("risk.max_order_value_pct不得大于单标的仓位上限")
    target_per_position = target_exposure / max_positions
    if target_exposure > max_total or target_per_position > max_single:
        raise ValueError("策略目标仓位不得超过硬风控仓位上限")
    _strict_int(risk, "max_orders_per_day", "risk.max_orders_per_day", minimum=1)
    _strict_bool(risk, "reject_duplicate_orders", "risk.reject_duplicate_orders")
    _strict_bool(risk, "reject_unknown_symbols", "risk.reject_unknown_symbols")
    _strict_bool(risk, "kill_switch", "risk.kill_switch")

    lot_size = _strict_int(
        execution, "lot_size", "execution.lot_size", minimum=1
    )
    if lot_size <= 0:  # Kept explicit for defensive readability at this boundary.
        raise ValueError("execution.lot_size必须大于0")
    _strict_bool(
        execution, "allow_fractional_lots", "execution.allow_fractional_lots"
    )
    _strict_bool(
        execution, "unknown_order_retry", "execution.unknown_order_retry"
    )

    _finite_number(
        costs,
        "commission_rate",
        "costs.commission_rate",
        minimum=0.0,
        maximum=1.0,
        maximum_inclusive=False,
    )
    _finite_number(
        costs,
        "minimum_commission",
        "costs.minimum_commission",
        minimum=0.0,
    )
    _finite_number(
        costs,
        "sell_tax_rate",
        "costs.sell_tax_rate",
        minimum=0.0,
        maximum=1.0,
        maximum_inclusive=False,
    )
    _finite_number(
        costs,
        "transfer_fee_rate",
        "costs.transfer_fee_rate",
        minimum=0.0,
        maximum=1.0,
        maximum_inclusive=False,
    )
    _finite_number(
        costs,
        "slippage_rate",
        "costs.slippage_rate",
        minimum=0.0,
        maximum=1.0,
        maximum_inclusive=False,
    )

    start_date = _iso_date(data, "start_date", "data.start_date")
    end_value = _required(data, "end_date", "data.end_date")
    end_date = date.max if end_value == "latest" else _iso_date(data, "end_date", "data.end_date")
    if end_value != "latest" and start_date > end_date:
        raise ValueError("data.start_date不得晚于data.end_date")
    provider = _required(data, "provider", "data.provider")
    if provider not in {"ths_finance", "local_csv"}:
        raise ValueError("data.provider只允许ths_finance或local_csv")
    base_url = _required(data, "base_url", "data.base_url")
    if base_url != "https://fuyao.aicubes.cn":
        raise ValueError("data.base_url必须是同花顺金融数据API官方HTTPS地址")
    api_key_env = _required(data, "api_key_env", "data.api_key_env")
    if api_key_env != "THS_FINANCE_API_KEY":
        raise ValueError("data.api_key_env必须是THS_FINANCE_API_KEY")
    if _required(data, "interval", "data.interval") != "1d":
        raise ValueError("当前策略只允许data.interval=1d日线")
    adjust = _required(data, "adjust", "data.adjust")
    if adjust != "none":
        raise ValueError("data.adjust必须是none（同花顺ETF历史行情当前不提供复权参数）")
    _strict_int(
        data,
        "request_timeout_seconds",
        "data.request_timeout_seconds",
        minimum=5,
        maximum=60,
    )
    _strict_int(data, "minimum_rows", "data.minimum_rows", minimum=60)
    _strict_bool(
        data,
        "allow_cached_on_error",
        "data.allow_cached_on_error",
    )
    _strict_int(
        data,
        "max_staleness_days",
        "data.max_staleness_days",
        minimum=1,
        maximum=31,
    )

    if not math.isfinite(initial_cash):  # Defensive invariant after validation.
        raise ValueError("account.initial_cash必须是有限数值")
    if max_positions > len(symbols):  # Defensive invariant after validation.
        raise ValueError("strategy.max_positions不得超过ETF白名单数量")
    _validate_storage_path(storage, project_root, "sqlite_path")

    _strict_bool(research, "enabled", "research.enabled")
    if _required(research, "market", "research.market") != "sh_sz_mainboard":
        raise ValueError("research.market必须是sh_sz_mainboard")
    _strict_int(research, "snapshot_limit", "research.snapshot_limit", minimum=5000, maximum=10000)
    _finite_number(research, "minimum_turnover", "research.minimum_turnover", minimum=0)
    minimum_price = _finite_number(research, "minimum_price", "research.minimum_price", minimum=0, minimum_inclusive=False)
    maximum_price = _finite_number(research, "maximum_price", "research.maximum_price", minimum=minimum_price, minimum_inclusive=False)
    if maximum_price <= minimum_price:
        raise ValueError("research.maximum_price必须大于minimum_price")
    prefilter = _strict_int(research, "prefilter_count", "research.prefilter_count", minimum=10)
    financial_count = _strict_int(research, "financial_count", "research.financial_count", minimum=3, maximum=prefilter)
    recommendations = _strict_int(research, "recommendation_count", "research.recommendation_count", minimum=3, maximum=financial_count)
    targets = _strict_int(research, "target_count", "research.target_count", minimum=1, maximum=recommendations)
    if _required(research, "strategy_version", "research.strategy_version") != STRATEGY_VERSION:
        raise ValueError("research.strategy_version必须与统一Research Pipeline合同一致")
    entry_rank = _strict_int(research, "entry_rank", "research.entry_rank", minimum=targets, maximum=recommendations)
    hold_rank = _strict_int(research, "hold_rank", "research.hold_rank", minimum=entry_rank, maximum=recommendations)
    _strict_int(research, "max_positions_per_industry", "research.max_positions_per_industry", minimum=1, maximum=targets)
    _ratio(research, "max_pairwise_correlation", "research.max_pairwise_correlation")
    research_single = _ratio(research, "max_single_position_pct", "research.max_single_position_pct")
    research_total = _ratio(research, "target_total_exposure_pct", "research.target_total_exposure_pct")
    if research_single > research_total:
        raise ValueError("research.max_single_position_pct不得超过目标总仓位")
    _strict_int(research, "history_days", "research.history_days", minimum=180)
    _strict_int(research, "minimum_history_rows", "research.minimum_history_rows", minimum=120)
    _strict_int(research, "quote_max_age_seconds", "research.quote_max_age_seconds", minimum=30, maximum=600)

    _strict_bool(paper_account, "enabled", "paper_account.enabled")
    _strict_bool(paper_account, "automatic_execution", "paper_account.automatic_execution")
    paper_cash = _finite_number(paper_account, "initial_cash", "paper_account.initial_cash", minimum=0, minimum_inclusive=False)
    paper_positions = _strict_int(paper_account, "max_positions", "paper_account.max_positions", minimum=1, maximum=targets)
    target_position = _ratio(paper_account, "target_position_pct", "paper_account.target_position_pct")
    max_paper_exposure = _ratio(paper_account, "max_total_exposure_pct", "paper_account.max_total_exposure_pct")
    _ratio(paper_account, "stop_loss_pct", "paper_account.stop_loss_pct")
    _ratio(paper_account, "max_drawdown_pct", "paper_account.max_drawdown_pct")
    paper_hold_rank = _strict_int(paper_account, "hold_rank", "paper_account.hold_rank", minimum=entry_rank, maximum=recommendations)
    _strict_int(paper_account, "minimum_holding_days", "paper_account.minimum_holding_days", minimum=0, maximum=60)
    _ratio(paper_account, "max_daily_turnover_pct", "paper_account.max_daily_turnover_pct")
    _strict_int(paper_account, "execution_window_minutes", "paper_account.execution_window_minutes", minimum=1, maximum=30)
    _strict_int(paper_account, "monitor_interval_minutes", "paper_account.monitor_interval_minutes", minimum=1, maximum=30)
    _strict_int(paper_account, "quote_refresh_seconds", "paper_account.quote_refresh_seconds", minimum=3, maximum=60)
    try:
        time.fromisoformat(str(_required(paper_account, "execute_time", "paper_account.execute_time")))
    except ValueError as exc:
        raise ValueError("paper_account.execute_time必须是HH:MM格式") from exc
    if target_position > max_paper_exposure:
        raise ValueError("模拟单股仓位不得超过paper_account.max_total_exposure_pct")
    if paper_hold_rank != hold_rank:
        raise ValueError("研究与模拟账户的持有排名缓冲必须一致")
    if abs(paper_cash - initial_cash) > 1e-9:
        raise ValueError("account.initial_cash必须与paper_account.initial_cash一致")


def load_config(config_path: Path, project_root: Path | None = None) -> AppConfig:
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError("配置文件根节点必须是键值对象")

    environment = _section(raw, "environment")
    live_enabled = _strict_bool(
        environment,
        "live_trading_enabled",
        "environment.live_trading_enabled",
    )
    mode = _required(environment, "mode", "environment.mode")
    if live_enabled:
        raise RuntimeError(
            "安全拦截：v1.0禁止启用实盘。请将live_trading_enabled改为false。"
        )
    if mode != "paper":
        raise RuntimeError("安全拦截：v1.0只允许paper模式。")
    _strict_bool(
        environment,
        "require_manual_confirmation",
        "environment.require_manual_confirmation",
    )

    resolved_root = (project_root or config_path.resolve().parents[1]).resolve()
    _validate_config(raw, resolved_root)
    return AppConfig(raw=raw, project_root=resolved_root)
