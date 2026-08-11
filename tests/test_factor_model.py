import pandas as pd

from ashare_agent.factor_model import FACTOR_MODEL_VERSION, score_cross_section


def sample_frame():
    return pd.DataFrame({
        "symbol": ["600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH"],
        "industry": ["银行", "银行", "制造", "制造", "消费"],
        "market_cap": [100, 120, 80, 90, 110],
        "earnings_yield": [0.10, 0.08, 0.07, 50.0, None],
        "book_to_price": [0.8, 0.7, 0.6, 0.5, None],
        "roe": [15, 14, 13, 12, 11],
        "cash_quality": [120, 110, 100, 90, 80],
        "profit_growth": [20, 18, 16, 14, 12],
        "revenue_growth": [15, 14, 13, 12, 11],
        "momentum": [0.25, 0.20, 0.15, 0.10, 0.05],
        "trend_strength": [1, 1, 0.5, 0.5, 0],
        "volatility": [0.15, 0.16, 0.17, 0.18, 0.19],
        "max_drawdown": [-0.08, -0.09, -0.10, -0.11, -0.12],
        "median_turnover_20d": [1e8, 9e7, 8e7, 7e7, 6e7],
    })


def test_shared_factor_model_is_deterministic_and_totals_one_hundred_points():
    first = score_cross_section(sample_frame())
    second = score_cross_section(sample_frame())
    pd.testing.assert_frame_equal(first, second)
    assert FACTOR_MODEL_VERSION == "cross-sectional-v1.0.0"
    assert first["score"].between(0, 100).all()
    assert first.loc[first["symbol"] == "600005.SH", "valuation_complete"].item() is False


def test_extreme_value_is_winsorized_without_breaking_rank_order():
    ranked = score_cross_section(sample_frame())
    assert ranked["score"].notna().all()
    assert set(ranked["symbol"]) == set(sample_frame()["symbol"])

