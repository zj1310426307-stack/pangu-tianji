from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .backtest import BacktestEngine
from .broker import MockBroker
from .config import load_config
from .market import ThsFinanceDataProvider, MarketDataBundle, load_market_data
from .report import write_report
from .risk import RiskEngine
from .storage import Storage
from .strategy import TrendMomentumStrategy


class TradingResearchAgent:
    """Compose the deterministic research pipeline for one paper-mode run."""

    def __init__(
        self, config_path: Path, project_root: Path | None = None
    ) -> None:
        """Load and validate the immutable run configuration."""
        self.config = load_config(config_path, project_root=project_root)

    def run(self) -> dict:
        """Run one backtest and close storage even when execution fails."""
        cfg = self.config.raw
        data_dir = self.config.project_root / "data"
        output_dir = self.config.project_root / "output"

        data = cfg["data"]
        end_date = (
            datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            if data["end_date"] == "latest"
            else data["end_date"]
        )
        if data["provider"] == "ths_finance":
            bundle = ThsFinanceDataProvider().fetch(
                symbols=self.config.symbols,
                start_date=data["start_date"],
                end_date=end_date,
                data_dir=data_dir,
                base_url=data["base_url"],
                api_key_env=data["api_key_env"],
                interval=data["interval"],
                adjust=data["adjust"],
                request_timeout_seconds=int(data["request_timeout_seconds"]),
                minimum_rows=int(data["minimum_rows"]),
                allow_cached_on_error=bool(data["allow_cached_on_error"]),
                max_staleness_days=int(data["max_staleness_days"]),
            )
        else:
            frames = load_market_data(
                self.config.symbols,
                data_dir,
                minimum_rows=int(data["minimum_rows"]),
            )
            bundle = MarketDataBundle(
                frames=frames,
                provider="local_csv",
                cache_used=True,
                fetched_at="local",
                completed_through=min(
                    frame["date"].max().date().isoformat()
                    for frame in frames.values()
                ),
            )
        market_data = bundle.frames

        risk_engine = RiskEngine(
            config=cfg["risk"],
            whitelist=self.config.symbols,
            initial_cash=self.config.initial_cash,
        )
        broker = MockBroker(
            initial_cash=self.config.initial_cash,
            risk_engine=risk_engine,
            lot_size=int(cfg["execution"]["lot_size"]),
            commission_rate=float(cfg["costs"]["commission_rate"]),
            minimum_commission=float(cfg["costs"]["minimum_commission"]),
            sell_tax_rate=float(cfg["costs"]["sell_tax_rate"]),
            slippage_rate=float(cfg["costs"]["slippage_rate"]),
            transfer_fee_rate=float(cfg["costs"].get("transfer_fee_rate", 0.0)),
        )
        strategy = TrendMomentumStrategy(
            long_ma_window=int(cfg["strategy"]["long_ma_window"]),
            momentum_window=int(cfg["strategy"]["momentum_window"]),
            max_positions=int(cfg["strategy"]["max_positions"]),
            target_total_exposure_pct=float(
                cfg["strategy"]["target_total_exposure_pct"]
            ),
        )
        with Storage(self.config.path(cfg["storage"]["sqlite_path"])) as storage:
            storage.clear_run_data()
            engine = BacktestEngine(
                market_data=market_data,
                strategy=strategy,
                broker=broker,
                storage=storage,
                rebalance_weekday=int(cfg["strategy"]["rebalance_weekday"]),
                lot_size=int(cfg["execution"]["lot_size"]),
            )
            result = engine.run()
            write_report(result.metrics, result.equity_curve, output_dir)
        return {
            **result.metrics,
            "data_provider": bundle.provider,
            "data_cache_used": bundle.cache_used,
            "data_completed_through": bundle.completed_through,
        }
