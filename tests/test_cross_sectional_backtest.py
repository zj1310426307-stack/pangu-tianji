import numpy as np
import pandas as pd

from ashare_agent.cross_sectional_backtest import (
    CrossSectionalBacktestConfig,
    CrossSectionalBacktestEngine,
    point_in_time_dataset_status,
)
from ashare_agent.data_center import DataCenter


def dataset():
    dates = pd.bdate_range("2025-01-02", periods=310)
    symbols = [f"60000{i}.SH" for i in range(8)]
    bar_rows = []
    for offset, symbol in enumerate(symbols):
        wave = np.sin(np.arange(len(dates)) / (9 + offset)) * (0.03 + offset / 1000)
        close = 8 + offset + np.arange(len(dates)) * (0.008 + offset / 10000) + wave
        for index, day in enumerate(dates):
            bar_rows.append({
                "date": day, "symbol": symbol,
                "open": close[index] * (1 + np.sin(index) * 0.0005),
                "high": close[index] * 1.01, "low": close[index] * 0.99,
                "close": close[index], "volume": 5_000_000,
                "turnover": 80_000_000 + offset * 5_000_000,
            })
    fundamentals = []
    for offset, symbol in enumerate(symbols):
        fundamentals.append({
            "symbol": symbol, "available_at": "2025-04-30",
            "profit_growth": 10 + offset, "revenue_growth": 8 + offset,
            "roe": 12 + offset / 2, "cash_quality": 90 + offset,
            "earnings_yield": 0.04 + offset / 1000,
            "book_to_price": 0.4 + offset / 100,
            "debt_ratio": 40 - offset,
        })
        fundamentals.append({
            "symbol": symbol, "available_at": "2027-04-30",
            "profit_growth": 1000 if offset == 0 else -1000,
            "revenue_growth": 1000 if offset == 0 else -1000,
            "roe": 99, "cash_quality": 999,
            "earnings_yield": 9, "book_to_price": 9,
        })
    universe = pd.DataFrame({
        "symbol": symbols,
        "list_date": ["2020-01-01"] * len(symbols),
        "delist_date": [None] * len(symbols),
        "name": [f"测试{i}" for i in range(len(symbols))],
        "industry": [f"行业{i // 2}" for i in range(len(symbols))],
    })
    benchmark = pd.DataFrame({"date": dates, "close": 1000 + np.arange(len(dates)) * 0.2})
    return pd.DataFrame(bar_rows), pd.DataFrame(fundamentals), universe, benchmark


def test_point_in_time_engine_uses_shared_model_and_next_open_execution(tmp_path):
    bars, fundamentals, universe, benchmark = dataset()
    config = CrossSectionalBacktestConfig(maximum_pairwise_correlation=1.01)
    engine = CrossSectionalBacktestEngine(
        bars,
        fundamentals,
        universe,
        benchmark,
        config,
        DataCenter(tmp_path / "data_center"),
    )
    result = engine.run()
    assert not result.rankings.empty
    assert not result.trades.empty
    assert (result.trades["date"] > result.trades["signal_date"]).all()
    assert result.metrics["strategy_version"] == "cross-sectional-v2.0.0"
    assert result.metrics["factor_model_version"] == "cross-sectional-v1.0.0"
    assert {"information_ratio", "beta", "monthly_win_rate_pct", "fee_drag_pct"} <= result.metrics.keys()
    assert result.equity_curve["position_count"].max() <= 5
    assert result.trades["fee"].gt(0).all()
    assert result.metrics["data_center_run_count"] > 0
    assert result.metrics["data_center_verified"] is True
    assert result.rankings["run_id"].notna().all()


def test_future_financial_reports_are_never_visible(tmp_path):
    bars, fundamentals, universe, _ = dataset()
    engine = CrossSectionalBacktestEngine(
        bars, fundamentals, universe, data_center=DataCenter(tmp_path / "data_center")
    )
    visible = engine._latest_fundamentals(pd.Timestamp("2026-03-01"))
    assert visible["available_at"].max() <= pd.Timestamp("2026-03-01")
    assert visible["profit_growth"].max() < 1000


def test_historical_universe_controls_survivorship_and_dataset_gate(tmp_path):
    bars, fundamentals, universe, _ = dataset()
    universe.loc[0, "delist_date"] = "2025-12-31"
    engine = CrossSectionalBacktestEngine(
        bars, fundamentals, universe, data_center=DataCenter(tmp_path / "data_center")
    )
    assert "600000.SH" in set(engine._active_symbols(pd.Timestamp("2025-06-30"))["symbol"])
    assert "600000.SH" not in set(engine._active_symbols(pd.Timestamp("2026-01-02"))["symbol"])
    status = point_in_time_dataset_status(tmp_path)
    assert status["ready"] is False
    assert status["strategy_version"] == "cross-sectional-v2.0.0"
    assert set(status["missing"]) == {"bars", "fundamentals", "universe"}
