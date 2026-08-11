import pandas as pd

from ashare_agent.strategy import TrendMomentumStrategy


def test_strategy_selects_positive_trend():
    dates = pd.bdate_range("2025-01-01", periods=100)
    rising = pd.DataFrame({"date": dates, "close": [1 + i * 0.01 for i in range(100)]})
    falling = pd.DataFrame({"date": dates, "close": [2 - i * 0.005 for i in range(100)]})

    strategy = TrendMomentumStrategy(
        long_ma_window=60,
        momentum_window=20,
        max_positions=1,
        target_total_exposure_pct=0.8,
    )
    signals = strategy.generate_signals(
        signal_date=dates[-1].date(),
        market_history={"A.SH": rising, "B.SH": falling},
    )
    assert len(signals) == 1
    assert signals[0].symbol == "A.SH"
