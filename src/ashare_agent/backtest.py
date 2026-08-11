from dataclasses import dataclass
from datetime import date
import math
import numpy as np
import pandas as pd

from .broker import MockBroker
from .models import Side
from .storage import Storage
from .strategy import TrendMomentumStrategy


@dataclass
class BacktestResult:
    """Collect the equity curve and metrics from one deterministic run."""

    equity_curve: pd.DataFrame
    metrics: dict


class BacktestEngine:
    """Execute previous-close signals at the next eligible open in paper mode."""

    def __init__(
        self,
        market_data: dict[str, pd.DataFrame],
        strategy: TrendMomentumStrategy,
        broker: MockBroker,
        storage: Storage,
        rebalance_weekday: int,
        lot_size: int,
    ) -> None:
        self.market_data = market_data
        self.strategy = strategy
        self.broker = broker
        self.storage = storage
        self.rebalance_weekday = rebalance_weekday
        self.lot_size = lot_size

    def _price_on(self, symbol: str, current_date: date, field: str) -> float | None:
        """Return a finite price for a symbol/date, or None when unavailable."""
        df = self.market_data[symbol]
        row = df[df["date"].dt.date == current_date]
        if row.empty:
            return None
        value = float(row.iloc[0][field])
        return value if math.isfinite(value) and value > 0 else None

    def _history_until(self, signal_date: date) -> dict[str, pd.DataFrame]:
        """Build point-in-time histories that cannot include future rows."""
        return {
            symbol: df[df["date"].dt.date <= signal_date].copy()
            for symbol, df in self.market_data.items()
        }

    def _all_dates(self) -> list[date]:
        """Return dates shared by every validated market-data series."""
        common = None
        for df in self.market_data.values():
            dates = set(df["date"].dt.date.tolist())
            common = dates if common is None else common.intersection(dates)
        return sorted(common or [])

    def _sell_to_targets(
        self,
        trade_date: date,
        signals,
        open_prices: dict[str, float],
    ) -> None:
        """Reduce unselected or overweight positions before placing buys."""
        equity = self.broker.equity(open_prices)
        target_weights = {signal.symbol: signal.target_weight for signal in signals}
        for symbol, position in list(self.broker.positions.items()):
            price = open_prices.get(symbol)
            if position.quantity <= 0 or price is None:
                continue
            target_value = equity * target_weights.get(symbol, 0.0)
            target_qty = math.floor(target_value / price / self.lot_size) * self.lot_size
            sell_qty = max(0, position.quantity - target_qty)
            if sell_qty > 0:
                order = self.broker.new_order(
                    trade_date, symbol, Side.SELL, sell_qty, price
                )
                order, trade = self.broker.submit(order, open_prices)
                self.storage.save_order(order)
                if trade:
                    self.storage.save_trade(trade)

    def _buy_targets(
        self,
        trade_date: date,
        signals,
        open_prices: dict[str, float],
    ) -> None:
        """Buy underweight selected positions after all required reductions."""
        equity = self.broker.equity(open_prices)
        for signal in signals:
            price = open_prices.get(signal.symbol)
            if not price:
                continue

            current_qty = (
                self.broker.positions[signal.symbol].quantity
                if signal.symbol in self.broker.positions
                else 0
            )
            target_value = equity * signal.target_weight
            target_qty = math.floor(target_value / price / self.lot_size) * self.lot_size
            buy_qty = max(0, target_qty - current_qty)

            if buy_qty <= 0:
                continue

            order = self.broker.new_order(
                trade_date, signal.symbol, Side.BUY, buy_qty, price
            )
            order, trade = self.broker.submit(order, open_prices)
            self.storage.save_order(order)
            if trade:
                self.storage.save_trade(trade)

    def run(self) -> BacktestResult:
        """Run the full backtest and persist signals, orders, trades, and equity."""
        dates = self._all_dates()
        records = []

        for idx, trade_date in enumerate(dates):
            open_prices = {
                symbol: self._price_on(symbol, trade_date, "open")
                for symbol in self.market_data
            }
            open_prices = {k: v for k, v in open_prices.items() if v is not None}

            if idx > 0 and trade_date.weekday() == self.rebalance_weekday:
                signal_date = dates[idx - 1]
                history = self._history_until(signal_date)
                signals = self.strategy.generate_signals(signal_date, history)
                for signal in signals:
                    self.storage.save_signal(signal)

                self._sell_to_targets(trade_date, signals, open_prices)
                self._buy_targets(trade_date, signals, open_prices)

            close_prices = {
                symbol: self._price_on(symbol, trade_date, "close")
                for symbol in self.market_data
            }
            close_prices = {k: v for k, v in close_prices.items() if v is not None}
            market_value = self.broker.market_value(close_prices)
            equity = self.broker.cash + market_value
            positions = {
                symbol: position.quantity
                for symbol, position in self.broker.positions.items()
                if position.quantity > 0
            }
            self.storage.save_equity(
                trade_date, self.broker.cash, market_value, equity, positions
            )
            records.append(
                {
                    "date": pd.Timestamp(trade_date),
                    "cash": self.broker.cash,
                    "market_value": market_value,
                    "equity": equity,
                }
            )

        curve = pd.DataFrame(records)
        metrics = self._calculate_metrics(curve)
        return BacktestResult(curve, metrics)

    def _calculate_metrics(self, curve: pd.DataFrame) -> dict:
        """Calculate finite portfolio-level metrics for API-safe serialization."""
        if curve.empty:
            raise RuntimeError("回测没有产生净值记录")

        equity = curve["equity"]
        daily_returns = equity.pct_change().dropna()
        total_return = equity.iloc[-1] / equity.iloc[0] - 1
        years = max(
            (curve["date"].iloc[-1] - curve["date"].iloc[0]).days / 365.25,
            1 / 365.25,
        )
        annual_return = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1

        running_max = equity.cummax()
        drawdown = equity / running_max - 1
        max_drawdown = float(drawdown.min())

        annual_vol = (
            float(daily_returns.std(ddof=0) * np.sqrt(252))
            if len(daily_returns)
            else 0.0
        )
        sharpe = (
            float(daily_returns.mean() / daily_returns.std(ddof=0) * np.sqrt(252))
            if len(daily_returns) and daily_returns.std(ddof=0) > 0
            else 0.0
        )

        return {
            "initial_equity": float(equity.iloc[0]),
            "final_equity": float(equity.iloc[-1]),
            "total_return_pct": float(total_return * 100),
            "annual_return_pct": float(annual_return * 100),
            "annual_volatility_pct": float(annual_vol * 100),
            "max_drawdown_pct": float(max_drawdown * 100),
            "sharpe": sharpe,
            "trade_count": len(self.broker.trades),
            "rejected_order_count": sum(
                1 for order in self.broker.orders if order.reject_reason
            ),
        }
