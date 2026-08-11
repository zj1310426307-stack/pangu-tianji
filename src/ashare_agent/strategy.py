from datetime import date
import pandas as pd

from .models import Signal


class TrendMomentumStrategy:
    def __init__(
        self,
        long_ma_window: int,
        momentum_window: int,
        max_positions: int,
        target_total_exposure_pct: float,
    ) -> None:
        if long_ma_window <= momentum_window:
            raise ValueError("长期均线窗口应大于动量窗口")
        self.long_ma_window = long_ma_window
        self.momentum_window = momentum_window
        self.max_positions = max_positions
        self.target_total_exposure_pct = target_total_exposure_pct

    def generate_signals(
        self,
        signal_date: date,
        market_history: dict[str, pd.DataFrame],
    ) -> list[Signal]:
        candidates: list[tuple[str, float, float, float]] = []

        for symbol, history in market_history.items():
            hist = history[history["date"].dt.date <= signal_date]
            if len(hist) < self.long_ma_window + 1:
                continue

            close = float(hist.iloc[-1]["close"])
            long_ma = float(hist["close"].tail(self.long_ma_window).mean())
            past_close = float(hist.iloc[-(self.momentum_window + 1)]["close"])
            momentum = close / past_close - 1

            if close > long_ma and momentum > 0:
                candidates.append((symbol, momentum, close, long_ma))

        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = candidates[: self.max_positions]
        if not selected:
            return []

        weight = self.target_total_exposure_pct / len(selected)
        return [
            Signal(
                trade_date=signal_date,
                symbol=symbol,
                score=momentum,
                target_weight=weight,
                reason=(
                    f"收盘价{close:.4f}高于{self.long_ma_window}日均线"
                    f"{long_ma:.4f}，{self.momentum_window}日动量"
                    f"{momentum:.2%}"
                ),
            )
            for symbol, momentum, close, long_ma in selected
        ]
