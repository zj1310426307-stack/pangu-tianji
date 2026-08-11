from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data_center import DataCenter, DataCenterRunRecord
from .factor_model import FACTOR_CONTRACT_HASH, FACTOR_MODEL_VERSION
from .research_pipeline import (
    STRATEGY_VERSION,
    ResearchPipeline,
    ResearchPipelineConfig,
    ResearchPipelineResult,
)


@dataclass(frozen=True)
class CrossSectionalBacktestConfig:
    """Hold production-equivalent portfolio and execution assumptions."""

    initial_cash: float = 100_000.0
    max_positions: int = 5
    entry_rank: int = 8
    hold_rank: int = 20
    minimum_holding_days: int = 5
    target_total_exposure: float = 0.60
    max_single_weight: float = 0.15
    max_positions_per_industry: int = 2
    maximum_pairwise_correlation: float = 0.85
    minimum_turnover: float = 50_000_000.0
    minimum_price: float = 2.0
    maximum_price: float = 200.0
    minimum_history_rows: int = 250
    prefilter_count: int = 120
    financial_count: int = 60
    recommendation_count: int = 30
    rebalance_every_n_days: int = 1
    lot_size: int = 100
    commission_rate: float = 0.0003
    minimum_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_rate: float = 0.001
    stop_loss_pct: float = 0.08
    max_drawdown_pct: float = 0.10
    max_daily_turnover_pct: float = 0.25


@dataclass
class CrossSectionalBacktestResult:
    """Expose auditable history, fills and risk/performance metrics."""

    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    rankings: pd.DataFrame
    metrics: dict[str, Any]


class FrameResearchDataSource:
    """Adapt immutable historical frames to the shared Research Pipeline contract."""

    def __init__(
        self,
        bars: pd.DataFrame,
        fundamentals: pd.DataFrame,
        universe: pd.DataFrame,
    ) -> None:
        self.bars = bars.copy()
        self.bars["date"] = pd.to_datetime(self.bars["date"], errors="coerce").dt.normalize()
        self.fundamentals_frame = fundamentals.copy()
        self.fundamentals_frame["available_at"] = pd.to_datetime(
            self.fundamentals_frame["available_at"], errors="coerce"
        ).dt.normalize()
        self.universe_frame = universe.copy()
        self.universe_frame["list_date"] = pd.to_datetime(
            self.universe_frame["list_date"], errors="coerce"
        ).dt.normalize()
        self.universe_frame["delist_date"] = pd.to_datetime(
            self.universe_frame["delist_date"], errors="coerce"
        ).dt.normalize()

    def security_universe(self, as_of: pd.Timestamp) -> pd.DataFrame:
        """Return securities whose historical listing interval includes the signal date."""
        active = self.universe_frame[
            (self.universe_frame["list_date"] <= as_of)
            & (
                self.universe_frame["delist_date"].isna()
                | (self.universe_frame["delist_date"] > as_of)
            )
        ].copy()
        return active

    def data_snapshot(self, as_of: pd.Timestamp, universe: pd.DataFrame) -> pd.DataFrame:
        """Normalize the completed signal-day bar into one point-in-time snapshot."""
        rows = self.bars[self.bars["date"] == as_of].copy()
        rows = rows.rename(columns={"close": "last_price"})
        tradeable = rows["volume"].astype(float) > 0
        if "suspended" in rows:
            tradeable &= ~rows["suspended"].fillna(False).astype(bool)
        if "upper_limit_price" in rows:
            upper = pd.to_numeric(rows["upper_limit_price"], errors="coerce")
            tradeable &= upper.isna() | (rows["last_price"].astype(float) < upper - 0.005)
        rows["tradeable"] = tradeable
        return rows

    def price_history(self, as_of: pd.Timestamp, symbols: list[str]) -> pd.DataFrame:
        """Return completed historical bars through the signal date."""
        return self.bars[
            self.bars["symbol"].astype(str).isin(symbols) & (self.bars["date"] <= as_of)
        ].copy()

    def fundamentals(self, as_of: pd.Timestamp, symbols: list[str]) -> pd.DataFrame:
        """Return only financial rows that were visible at the signal time."""
        visible = self.fundamentals_frame[
            self.fundamentals_frame["symbol"].astype(str).isin(symbols)
            & (self.fundamentals_frame["available_at"] <= as_of)
        ]
        if visible.empty:
            return visible.copy()
        return visible.sort_values("available_at").groupby("symbol", as_index=False).tail(1)


class CrossSectionalBacktestEngine:
    """Backtest the production factor contract with point-in-time data only.

    Inputs are long-form tables. ``bars`` must contain date, symbol, OHLC,
    volume and turnover. ``fundamentals`` must contain symbol, available_at and
    the factor columns consumed by ``score_cross_section``. ``universe`` must
    carry historical list/delist dates so today's survivors cannot leak into
    past tests.
    """

    def __init__(
        self,
        bars: pd.DataFrame,
        fundamentals: pd.DataFrame,
        universe: pd.DataFrame,
        benchmark: pd.DataFrame | None = None,
        config: CrossSectionalBacktestConfig | None = None,
        data_center: DataCenter | None = None,
    ) -> None:
        if data_center is None:
            raise ValueError("横截面回测必须显式配置Data Center")
        self.config = config or CrossSectionalBacktestConfig()
        self.data_center = data_center
        self.research_records: list[DataCenterRunRecord] = []
        self.bars = self._validate_bars(bars)
        self.fundamentals = self._validate_fundamentals(fundamentals)
        self.universe = self._validate_universe(universe)
        self.benchmark = self._validate_benchmark(benchmark)
        self.research_source = FrameResearchDataSource(
            self.bars, self.fundamentals, self.universe
        )
        self.research_pipeline = ResearchPipeline(
            ResearchPipelineConfig(
                minimum_turnover=self.config.minimum_turnover,
                minimum_price=self.config.minimum_price,
                maximum_price=self.config.maximum_price,
                minimum_history_rows=self.config.minimum_history_rows,
                prefilter_count=self.config.prefilter_count,
                financial_count=self.config.financial_count,
                recommendation_count=self.config.recommendation_count,
                target_count=self.config.max_positions,
                entry_rank=self.config.entry_rank,
                max_positions_per_industry=self.config.max_positions_per_industry,
                max_pairwise_correlation=self.config.maximum_pairwise_correlation,
                max_single_position_pct=self.config.max_single_weight,
                target_total_exposure_pct=self.config.target_total_exposure,
            )
        )

    @staticmethod
    def _validate_bars(frame: pd.DataFrame) -> pd.DataFrame:
        """Normalize prices and reject duplicate or incomplete daily records."""
        required = {"date", "symbol", "open", "high", "low", "close", "volume", "turnover"}
        if not required.issubset(frame.columns):
            raise ValueError(f"横截面回测行情缺少字段：{sorted(required - set(frame.columns))}")
        result = frame.copy()
        result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.normalize()
        if result["date"].isna().any() or result.duplicated(["date", "symbol"]).any():
            raise ValueError("横截面回测行情存在无效或重复日期")
        for column in ("open", "high", "low", "close", "volume", "turnover"):
            result[column] = pd.to_numeric(result[column], errors="coerce")
        numeric = result[["open", "high", "low", "close", "volume", "turnover"]]
        if not np.isfinite(numeric.to_numpy(dtype=float)).all() or (result[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError("横截面回测行情包含无效价格或成交数据")
        return result.sort_values(["date", "symbol"]).reset_index(drop=True)

    @staticmethod
    def _validate_fundamentals(frame: pd.DataFrame) -> pd.DataFrame:
        """Require explicit availability timestamps to prevent report leakage."""
        required = {"symbol", "available_at", "profit_growth", "revenue_growth", "roe", "cash_quality"}
        if not required.issubset(frame.columns):
            raise ValueError(f"点时财务数据缺少字段：{sorted(required - set(frame.columns))}")
        result = frame.copy()
        result["available_at"] = pd.to_datetime(result["available_at"], errors="coerce").dt.normalize()
        if result["available_at"].isna().any():
            raise ValueError("点时财务数据存在无效披露日期")
        return result.sort_values(["symbol", "available_at"]).reset_index(drop=True)

    @staticmethod
    def _validate_universe(frame: pd.DataFrame) -> pd.DataFrame:
        """Require historical membership dates to control survivorship bias."""
        required = {"symbol", "list_date", "delist_date", "name", "industry"}
        if not required.issubset(frame.columns):
            raise ValueError(f"历史股票池缺少字段：{sorted(required - set(frame.columns))}")
        result = frame.copy()
        result["list_date"] = pd.to_datetime(result["list_date"], errors="coerce").dt.normalize()
        result["delist_date"] = pd.to_datetime(result["delist_date"], errors="coerce").dt.normalize()
        if result["list_date"].isna().any():
            raise ValueError("历史股票池存在无效上市日期")
        return result

    @staticmethod
    def _validate_benchmark(frame: pd.DataFrame | None) -> pd.DataFrame | None:
        """Normalize an optional benchmark close series for excess metrics."""
        if frame is None:
            return None
        if not {"date", "close"}.issubset(frame.columns):
            raise ValueError("基准数据必须包含date和close")
        result = frame[["date", "close"]].copy()
        result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.normalize()
        result["close"] = pd.to_numeric(result["close"], errors="coerce")
        if result.isna().any().any() or (result["close"] <= 0).any():
            raise ValueError("基准数据无效")
        return result.sort_values("date").drop_duplicates("date")

    def _active_symbols(self, signal_date: pd.Timestamp) -> pd.DataFrame:
        """Expose the historical adapter's point-in-time universe for diagnostics."""
        return self.research_source.security_universe(pd.Timestamp(signal_date).normalize())

    def _latest_fundamentals(self, signal_date: pd.Timestamp) -> pd.DataFrame:
        """Expose the historical adapter's visible reports for leakage tests."""
        return self.research_source.fundamentals(
            pd.Timestamp(signal_date).normalize(),
            self.universe["symbol"].astype(str).tolist(),
        )

    def _research(self, signal_date: pd.Timestamp) -> ResearchPipelineResult:
        """Run and replay the production Pipeline through the shared Data Center."""
        pipeline_run = self.data_center.execute_pipeline(
            self.research_pipeline,
            signal_date,
            self.research_source,
            run_kind="historical_backtest",
            metadata={
                "provider": "historical_point_in_time",
                "source": "cross_sectional_parquet",
                "observed_at": pd.Timestamp(signal_date).isoformat(),
                "preview": False,
            },
        )
        self.research_records.append(pipeline_run.record)
        return pipeline_run.result

    def _fee(self, gross: float, side: str) -> float:
        """Return all explicitly modelled A-share transaction costs."""
        commission = max(gross * self.config.commission_rate, self.config.minimum_commission)
        stamp = gross * self.config.stamp_tax_rate if side == "SELL" else 0.0
        return commission + stamp + gross * self.config.transfer_fee_rate

    def run(self) -> CrossSectionalBacktestResult:
        """Execute close-date signals at the next open with T+1 and limit locks."""
        dates = sorted(self.bars["date"].unique())
        cash = float(self.config.initial_cash)
        positions: dict[str, dict[str, Any]] = {}
        trades: list[dict[str, Any]] = []
        ranking_history: list[pd.DataFrame] = []
        equity_records: list[dict[str, Any]] = []
        peak_equity = float(self.config.initial_cash)
        for index, raw_trade_date in enumerate(dates):
            trade_date = pd.Timestamp(raw_trade_date)
            day_rows = self.bars[self.bars["date"] == trade_date].set_index("symbol")
            turnover_used = 0.0
            prior_equity = equity_records[-1]["equity"] if equity_records else self.config.initial_cash
            allow_buys = prior_equity / peak_equity - 1 > -self.config.max_drawdown_pct

            # A stop is observed from the prior completed close and can only be
            # filled at today's open. T+1 and limit-down locks remain binding.
            if index > 0:
                prior_date = pd.Timestamp(dates[index - 1])
                prior_rows = self.bars[self.bars["date"] == prior_date].set_index("symbol")
                for symbol in list(positions):
                    position = positions[symbol]
                    if position["acquired_date"] >= trade_date.date() or symbol not in prior_rows.index:
                        continue
                    if float(prior_rows.loc[symbol, "close"]) > position["average_cost"] * (1 - self.config.stop_loss_pct):
                        continue
                    if symbol not in day_rows.index or float(day_rows.loc[symbol, "volume"]) <= 0:
                        continue
                    row = day_rows.loc[symbol]
                    lower = float(row.get("lower_limit_price", -math.inf))
                    if math.isfinite(lower) and float(row["open"]) <= lower + 0.005:
                        continue
                    fill = float(row["open"]) * (1 - self.config.slippage_rate)
                    gross = fill * position["quantity"]
                    fee = self._fee(gross, "SELL")
                    cash += gross - fee
                    turnover_used += gross
                    trades.append({
                        "date": trade_date, "signal_date": prior_date, "symbol": symbol,
                        "side": "SELL", "quantity": position["quantity"], "fill_price": fill,
                        "fee": fee, "realized_pnl": (fill - position["average_cost"]) * position["quantity"] - fee,
                        "exit_reason": "STOP_LOSS",
                    })
                    del positions[symbol]
            if index >= 251 and index % self.config.rebalance_every_n_days == 0:
                signal_date = pd.Timestamp(dates[index - 1])
                research = self._research(signal_date)
                ranked = research.ranked
                if not ranked.empty:
                    ranked = ranked.copy()
                    ranked["signal_date"] = signal_date
                    ranked["strategy_version"] = research.strategy_version
                    ranked["run_id"] = self.research_records[-1].run_id
                    ranked["data_version"] = self.research_records[-1].data_version
                    ranking_history.append(ranked)
                    rank_map = dict(zip(ranked["symbol"], ranked["rank"]))
                    for symbol in list(positions):
                        position = positions[symbol]
                        holding_days = (trade_date.date() - position["acquired_date"]).days
                        if rank_map.get(symbol, 10_000) <= self.config.hold_rank or holding_days < self.config.minimum_holding_days:
                            continue
                        if symbol not in day_rows.index or float(day_rows.loc[symbol, "volume"]) <= 0:
                            continue
                        row = day_rows.loc[symbol]
                        lower = float(row.get("lower_limit_price", -math.inf))
                        if math.isfinite(lower) and float(row["open"]) <= lower + 0.005:
                            continue
                        fill = float(row["open"]) * (1 - self.config.slippage_rate)
                        gross = fill * position["quantity"]
                        fee = self._fee(gross, "SELL")
                        cash += gross - fee
                        turnover_used += gross
                        trades.append({"date": trade_date, "signal_date": signal_date, "symbol": symbol, "side": "SELL", "quantity": position["quantity"], "fill_price": fill, "fee": fee, "realized_pnl": (fill - position["average_cost"]) * position["quantity"] - fee, "exit_reason": "RANK_BUFFER"})
                        del positions[symbol]

                    selected = research.targets
                    close_prices = {symbol: float(day_rows.loc[symbol, "open"]) for symbol in positions if symbol in day_rows.index}
                    equity = cash + sum(positions[s]["quantity"] * close_prices.get(s, positions[s]["average_cost"]) for s in positions)
                    for symbol in selected:
                        if not allow_buys:
                            break
                        if symbol in positions or symbol not in day_rows.index or len(positions) >= self.config.max_positions:
                            continue
                        row = day_rows.loc[symbol]
                        if float(row["volume"]) <= 0:
                            continue
                        upper = float(row.get("upper_limit_price", math.inf))
                        if math.isfinite(upper) and float(row["open"]) >= upper - 0.005:
                            continue
                        weight = research.target_weights[symbol]
                        fill = float(row["open"]) * (1 + self.config.slippage_rate)
                        turnover_room = max(
                            0.0,
                            equity * self.config.max_daily_turnover_pct - turnover_used,
                        )
                        target_value = min(equity * weight, turnover_room)
                        quantity = math.floor(target_value / fill / self.config.lot_size) * self.config.lot_size
                        if quantity <= 0:
                            continue
                        gross = fill * quantity
                        fee = self._fee(gross, "BUY")
                        if gross + fee > cash:
                            continue
                        cash -= gross + fee
                        turnover_used += gross
                        positions[symbol] = {"quantity": quantity, "average_cost": (gross + fee) / quantity, "acquired_date": trade_date.date()}
                        trades.append({"date": trade_date, "signal_date": signal_date, "symbol": symbol, "side": "BUY", "quantity": quantity, "fill_price": fill, "fee": fee, "realized_pnl": 0.0, "exit_reason": None})

            market_value = 0.0
            for symbol, position in positions.items():
                price = float(day_rows.loc[symbol, "close"]) if symbol in day_rows.index else position["average_cost"]
                market_value += position["quantity"] * price
            current_equity = cash + market_value
            peak_equity = max(peak_equity, current_equity)
            equity_records.append({"date": trade_date, "cash": cash, "market_value": market_value, "equity": current_equity, "position_count": len(positions), "allow_buys": allow_buys})

        curve = pd.DataFrame(equity_records)
        trade_frame = pd.DataFrame(trades)
        rankings = pd.concat(ranking_history, ignore_index=True) if ranking_history else pd.DataFrame()
        metrics = self._metrics(curve, trade_frame)
        metrics["data_center_run_count"] = len(self.research_records)
        metrics["data_center_verified"] = all(item.verified for item in self.research_records)
        return CrossSectionalBacktestResult(curve, trade_frame, rankings, metrics)

    def _metrics(self, curve: pd.DataFrame, trades: pd.DataFrame) -> dict[str, Any]:
        """Calculate absolute, benchmark-relative, downside and cost metrics."""
        equity = curve.set_index("date")["equity"]
        returns = equity.pct_change().dropna()
        years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
        total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
        annual_return = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)
        volatility = float(returns.std(ddof=0) * np.sqrt(252)) if len(returns) else 0.0
        downside = returns[returns < 0]
        downside_risk = float(downside.std(ddof=0) * np.sqrt(252)) if len(downside) else 0.0
        drawdown = equity / equity.cummax() - 1
        max_drawdown = float(drawdown.min())
        underwater = drawdown < 0
        groups = (underwater != underwater.shift()).cumsum()
        drawdown_duration = int(underwater.groupby(groups).sum().max()) if underwater.any() else 0
        monthly = equity.groupby(equity.index.to_period("M")).last().pct_change().dropna()
        sharpe = float(returns.mean() / returns.std(ddof=0) * np.sqrt(252)) if len(returns) and returns.std(ddof=0) > 0 else 0.0
        sortino = float(returns.mean() / downside.std(ddof=0) * np.sqrt(252)) if len(downside) and downside.std(ddof=0) > 0 else 0.0
        completed = trades[trades["side"] == "SELL"] if not trades.empty else pd.DataFrame()
        wins = completed[completed["realized_pnl"] > 0] if not completed.empty else completed
        losses = completed[completed["realized_pnl"] < 0] if not completed.empty else completed
        profit_factor = float(wins["realized_pnl"].sum() / abs(losses["realized_pnl"].sum())) if not losses.empty and losses["realized_pnl"].sum() != 0 else 0.0
        result: dict[str, Any] = {
            "strategy_version": STRATEGY_VERSION,
            "factor_model_version": FACTOR_MODEL_VERSION,
            "factor_contract_hash": FACTOR_CONTRACT_HASH,
            "total_return_pct": total_return * 100,
            "annual_return_pct": annual_return * 100,
            "annual_volatility_pct": volatility * 100,
            "max_drawdown_pct": max_drawdown * 100,
            "max_drawdown_duration_days": drawdown_duration,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": annual_return / abs(max_drawdown) if max_drawdown < 0 else 0.0,
            "trade_count": int(len(trades)),
            "trade_win_rate_pct": float(len(wins) / len(completed) * 100) if len(completed) else 0.0,
            "profit_factor": profit_factor,
            "total_fees": float(trades["fee"].sum()) if not trades.empty else 0.0,
            "fee_drag_pct": float(trades["fee"].sum() / self.config.initial_cash * 100) if not trades.empty else 0.0,
            "annual_turnover": float((trades["fill_price"] * trades["quantity"]).sum() / self.config.initial_cash / years) if not trades.empty else 0.0,
            "monthly_win_rate_pct": float((monthly > 0).mean() * 100) if len(monthly) else 0.0,
            "worst_1d_pct": float(returns.min() * 100) if len(returns) else 0.0,
            "worst_5d_pct": float(equity.pct_change(5).min() * 100) if len(equity) > 5 else 0.0,
            "worst_20d_pct": float(equity.pct_change(20).min() * 100) if len(equity) > 20 else 0.0,
        }
        if self.benchmark is not None:
            benchmark = self.benchmark.set_index("date")["close"].reindex(equity.index).ffill().dropna()
            aligned = pd.concat([returns.rename("strategy"), benchmark.pct_change().rename("benchmark")], axis=1).dropna()
            if not aligned.empty:
                excess = aligned["strategy"] - aligned["benchmark"]
                tracking = float(excess.std(ddof=0) * np.sqrt(252))
                result.update({
                    "benchmark_total_return_pct": float(benchmark.iloc[-1] / benchmark.iloc[0] - 1) * 100,
                    "excess_total_return_pct": (
                        total_return - float(benchmark.iloc[-1] / benchmark.iloc[0] - 1)
                    ) * 100,
                    "information_ratio": float(excess.mean() / excess.std(ddof=0) * np.sqrt(252)) if excess.std(ddof=0) > 0 else 0.0,
                    "tracking_error_pct": tracking * 100,
                    "beta": float(aligned.cov().iloc[0, 1] / aligned["benchmark"].var()) if aligned["benchmark"].var() > 0 else 0.0,
                    "annual_alpha_pct": float(excess.mean() * 252 * 100),
                })
        return result


def point_in_time_dataset_status(root: Path) -> dict[str, Any]:
    """Report whether the three immutable historical inputs are available."""
    data_root = root / "data" / "cross_sectional"
    required = {
        "bars": data_root / "bars.parquet",
        "fundamentals": data_root / "fundamentals.parquet",
        "universe": data_root / "universe.parquet",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    return {
        "ready": not missing,
        "strategy_version": STRATEGY_VERSION,
        "factor_model_version": FACTOR_MODEL_VERSION,
        "factor_contract_hash": FACTOR_CONTRACT_HASH,
        "missing": missing,
        "message": "点时数据齐全，可运行生产同构回测" if not missing else "缺少点时历史数据，旧ETF回测不得替代生产策略验证",
    }
