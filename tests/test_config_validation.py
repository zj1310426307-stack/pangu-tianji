from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from ashare_agent.config import load_config


BASE_CONFIG = {
    "environment": {
        "mode": "paper",
        "live_trading_enabled": False,
        "require_manual_confirmation": True,
    },
    "account": {"initial_cash": 1000.0},
    "research": {
        "enabled": True, "market": "sh_sz_mainboard", "snapshot_limit": 10000,
        "minimum_turnover": 50_000_000, "minimum_price": 2.0, "maximum_price": 200.0,
        "prefilter_count": 40, "financial_count": 20, "recommendation_count": 10,
        "target_count": 3, "history_days": 220, "minimum_history_rows": 120,
        "quote_max_age_seconds": 120,
    },
    "paper_account": {
        "enabled": True, "automatic_execution": True, "initial_cash": 1000.0,
        "max_positions": 3, "target_position_pct": 0.2,
        "max_total_exposure_pct": 0.6, "stop_loss_pct": 0.08,
        "max_drawdown_pct": 0.1, "execute_time": "09:35",
        "execution_window_minutes": 10, "monitor_interval_minutes": 5,
        "quote_refresh_seconds": 5,
    },
    "universe": {"symbols": ["510300.SH", "159915.SZ"]},
    "strategy": {
        "long_ma_window": 60,
        "momentum_window": 20,
        "max_positions": 1,
        "rebalance_weekday": 0,
        "target_total_exposure_pct": 0.8,
    },
    "risk": {
        "max_total_exposure_pct": 0.9,
        "max_single_position_pct": 0.9,
        "max_order_value_pct": 0.9,
        "max_orders_per_day": 4,
        "reject_duplicate_orders": True,
        "reject_unknown_symbols": True,
        "kill_switch": False,
    },
    "execution": {
        "lot_size": 100,
        "allow_fractional_lots": False,
        "unknown_order_retry": False,
    },
    "costs": {
        "commission_rate": 0.0003,
        "minimum_commission": 0.0,
        "sell_tax_rate": 0.0,
        "slippage_rate": 0.001,
    },
    "data": {
        "provider": "ths_finance",
        "base_url": "https://fuyao.aicubes.cn",
        "api_key_env": "THS_FINANCE_API_KEY",
        "start_date": "2023-01-03",
        "end_date": "2026-07-15",
        "interval": "1d",
        "adjust": "none",
        "request_timeout_seconds": 20,
        "minimum_rows": 120,
        "allow_cached_on_error": True,
        "max_staleness_days": 10,
    },
    "storage": {"sqlite_path": "output/agent.db"},
}


def write_full_config(tmp_path: Path, raw: dict) -> Path:
    project_root = tmp_path / "project"
    config_dir = project_root / "config"
    config_dir.mkdir(parents=True)
    path = config_dir / "settings.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def changed(path: str, value: object) -> dict:
    raw = deepcopy(BASE_CONFIG)
    section, key = path.split(".")
    raw[section][key] = value
    return raw


def test_complete_valid_config_loads(tmp_path: Path) -> None:
    config = load_config(write_full_config(tmp_path, deepcopy(BASE_CONFIG)))
    assert config.initial_cash == 1000.0
    assert config.symbols == ["510300.SH", "159915.SZ"]
    assert config.raw["data"]["provider"] == "ths_finance"
    assert config.raw["investment_profile"]["risk_level"] == "balanced"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk_level", "unknown"),
        ("investment_horizon", "forever"),
        ("max_drawdown_tolerance", 0.01),
        ("investment_style", "rumor"),
    ],
)
def test_invalid_investment_profile_is_rejected(tmp_path: Path, field: str, value: object) -> None:
    raw = deepcopy(BASE_CONFIG)
    raw["investment_profile"] = {
        "capital": 1000.0,
        "risk_level": "balanced",
        "investment_horizon": "medium",
        "max_drawdown_tolerance": 0.10,
        "investment_style": "balanced",
        field: value,
    }
    with pytest.raises(ValueError, match="Investment Profile"):
        load_config(write_full_config(tmp_path, raw))


@pytest.mark.parametrize(
    "symbols",
    [
        [],
        ["510300"],
        ["510300.sh"],
        ["159915.SH"],
        ["510300.SZ"],
        ["510300.SH", "510300.SH"],
    ],
)
def test_invalid_etf_universe_is_rejected(tmp_path: Path, symbols: list[str]) -> None:
    with pytest.raises(ValueError, match="ETF|symbols"):
        load_config(write_full_config(tmp_path, changed("universe.symbols", symbols)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account.initial_cash", 0),
        ("account.initial_cash", float("inf")),
        ("strategy.long_ma_window", 1),
        ("strategy.long_ma_window", 20),
        ("strategy.momentum_window", 0),
        ("strategy.max_positions", 3),
        ("strategy.rebalance_weekday", 5),
        ("strategy.target_total_exposure_pct", 0),
        ("strategy.target_total_exposure_pct", 1.1),
        ("risk.max_total_exposure_pct", float("nan")),
        ("risk.max_single_position_pct", 0),
        ("risk.max_order_value_pct", 1.1),
        ("risk.max_orders_per_day", 0),
        ("execution.lot_size", 0),
        ("execution.lot_size", True),
        ("costs.commission_rate", -0.1),
        ("costs.commission_rate", 1.0),
        ("costs.minimum_commission", -1),
        ("costs.sell_tax_rate", float("inf")),
        ("costs.slippage_rate", 1.0),
        ("data.request_timeout_seconds", 4),
        ("data.minimum_rows", 59),
        ("data.max_staleness_days", 32),
    ],
)
def test_invalid_numeric_boundaries_are_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    with pytest.raises(ValueError):
        load_config(write_full_config(tmp_path, changed(field, value)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("data.provider", "other_provider"),
        ("data.base_url", "http://fuyao.aicubes.cn"),
        ("data.api_key_env", "MY_API_KEY"),
        ("data.interval", "5m"),
        ("data.adjust", "forward"),
    ],
)
def test_untrusted_data_source_settings_are_rejected(
    tmp_path: Path, field: str, value: str
) -> None:
    with pytest.raises(ValueError, match="data"):
        load_config(write_full_config(tmp_path, changed(field, value)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("data.start_date", "2026-02-30"),
        ("data.start_date", "2026-7-01"),
        ("data.end_date", "not-a-date"),
    ],
)
def test_invalid_iso_dates_are_rejected(
    tmp_path: Path, field: str, value: str
) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        load_config(write_full_config(tmp_path, changed(field, value)))


def test_start_date_after_end_date_is_rejected(tmp_path: Path) -> None:
    raw = changed("data.start_date", "2026-07-16")
    raw["data"]["end_date"] = "2026-07-15"
    with pytest.raises(ValueError, match="start_date"):
        load_config(write_full_config(tmp_path, raw))


@pytest.mark.parametrize(
    "sqlite_path",
    ["agent.db", "../agent.db", "output/../agent.db", "C:/outside/agent.db"],
)
def test_sqlite_path_outside_output_is_rejected(
    tmp_path: Path, sqlite_path: str
) -> None:
    raw = changed("storage.sqlite_path", sqlite_path)
    with pytest.raises(ValueError, match="相对路径|project_root/output"):
        load_config(write_full_config(tmp_path, raw))


def test_nested_sqlite_path_inside_output_is_allowed(tmp_path: Path) -> None:
    raw = changed("storage.sqlite_path", "output/runs/agent.sqlite3")
    config = load_config(write_full_config(tmp_path, raw))
    assert config.raw["storage"]["sqlite_path"] == "output/runs/agent.sqlite3"


def test_strategy_target_cannot_exceed_hard_risk_limits(tmp_path: Path) -> None:
    raw = changed("risk.max_total_exposure_pct", 0.7)
    raw["risk"]["max_single_position_pct"] = 0.7
    raw["risk"]["max_order_value_pct"] = 0.7
    with pytest.raises(ValueError, match="目标仓位"):
        load_config(write_full_config(tmp_path, raw))


def test_total_target_is_compared_as_per_position_weight(tmp_path: Path) -> None:
    raw = deepcopy(BASE_CONFIG)
    raw["strategy"]["max_positions"] = 2
    raw["risk"]["max_single_position_pct"] = 0.5
    raw["risk"]["max_order_value_pct"] = 0.5
    config = load_config(write_full_config(tmp_path, raw))
    assert config.raw["strategy"]["target_total_exposure_pct"] == 0.8
