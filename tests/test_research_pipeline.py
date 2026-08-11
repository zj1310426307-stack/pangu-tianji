from datetime import date

import numpy as np
import pandas as pd

from ashare_agent.cross_sectional_backtest import FrameResearchDataSource
from ashare_agent.research_pipeline import (
    STRATEGY_VERSION,
    ResearchPipeline,
    ResearchPipelineConfig,
)
from ashare_agent.stock_research import ThsResearchDataSource


AS_OF = date(2026, 7, 20)


def shared_inputs():
    """Build equivalent THS-shaped and frame-shaped point-in-time inputs."""
    dates = pd.bdate_range(end=AS_OF, periods=250)
    tickers = []
    quotes = []
    bar_rows = []
    indicators = {}
    for offset in range(8):
        symbol = f"60000{offset}.SH"
        name = f"测试{offset}"
        industry = f"行业{offset // 2}"
        tickers.append({
            "thscode": symbol,
            "ticker": symbol[:6],
            "name": name,
            "industry": industry,
            "listing_days": 1000,
        })
        values = 10 + offset + np.arange(len(dates)) * (0.01 + offset / 10000)
        turnover = 100_000_000 + offset * 1_000_000
        quotes.append({
            "thscode": symbol,
            "last_price": float(values[-1]),
            "turnover": turnover,
            "volume": 1_000_000,
            "price_change_ratio_pct": 1.0,
        })
        for index, day in enumerate(dates):
            bar_rows.append({
                "date": day,
                "symbol": symbol,
                "open": float(values[index]),
                "high": float(values[index] * 1.01),
                "low": float(values[index] * 0.99),
                "close": float(values[index]),
                "volume": 1_000_000,
                "turnover": turnover,
            })
        indicators[symbol] = {
            "net_profit_yoy_growth_ratio": 10 + offset,
            "operating_income_yoy_growth_ratio": 8 + offset,
            "index_weighted_avg_roe": 12 + offset,
            "net_profit_cash_content": 90 + offset,
            "earnings_yield": 0.04 + offset / 1000,
            "book_to_price": 0.4 + offset / 100,
            "debt_ratio": 40 - offset,
        }
    fundamentals = pd.DataFrame([
        {
            "symbol": symbol,
            "available_at": AS_OF,
            "profit_growth": values["net_profit_yoy_growth_ratio"],
            "revenue_growth": values["operating_income_yoy_growth_ratio"],
            "roe": values["index_weighted_avg_roe"],
            "cash_quality": values["net_profit_cash_content"],
            "earnings_yield": values["earnings_yield"],
            "book_to_price": values["book_to_price"],
            "debt_ratio": values["debt_ratio"],
        }
        for symbol, values in indicators.items()
    ])
    universe = pd.DataFrame([
        {
            "symbol": item["thscode"],
            "name": item["name"],
            "industry": item["industry"],
            "list_date": "2020-01-01",
            "delist_date": None,
        }
        for item in tickers
    ])
    return tickers, quotes, pd.DataFrame(bar_rows), fundamentals, universe, indicators


class EquivalentThsClient:
    """Expose the THS adapter contract over the same immutable test dataset."""

    def __init__(self):
        self.ticker_rows, self.quotes, self.bars, _, _, self.indicator_rows = shared_inputs()

    def tickers(self):
        return self.ticker_rows

    def snapshot(self, symbols=None):
        selected = self.quotes if symbols is None else [
            row for row in self.quotes if row["thscode"] in symbols
        ]
        timestamp = int(pd.Timestamp("2026-07-20 15:05", tz="Asia/Shanghai").timestamp() * 1000)
        return {"item": selected, "total": len(selected), "timestamp": timestamp}

    def history(self, symbol, start, end):
        selected = self.bars[self.bars["symbol"] == symbol]
        milliseconds = [
            int(pd.Timestamp(day, tz="Asia/Shanghai").timestamp() * 1000)
            for day in selected["date"]
        ]
        return pd.DataFrame({
            "date_ms": milliseconds,
            "close_price": selected["close"].to_numpy(),
            "volume": selected["volume"].to_numpy(),
            "turnover": selected["turnover"].to_numpy(),
        })

    def indicators(self, symbol, report):
        return self.indicator_rows[symbol]


def pipeline_config():
    """Use one compact but production-shaped strategy contract for parity checks."""
    return ResearchPipelineConfig(
        minimum_history_rows=250,
        prefilter_count=8,
        financial_count=8,
        recommendation_count=8,
        target_count=3,
        entry_rank=8,
        max_pairwise_correlation=1.01,
    )


def test_live_and_historical_adapters_produce_identical_research_results():
    """The provider environment must not own any ranking or portfolio logic."""
    tickers, _, bars, fundamentals, universe, _ = shared_inputs()
    mapping = {
        **pipeline_config().__dict__,
        "history_days": 520,
    }
    live_source = ThsResearchDataSource(
        EquivalentThsClient(), mapping, as_of=AS_OF, preview=False
    )
    historical_source = FrameResearchDataSource(bars, fundamentals, universe)
    pipeline = ResearchPipeline(pipeline_config())

    live = pipeline.run(pd.Timestamp(AS_OF), live_source)
    historical = pipeline.run(pd.Timestamp(AS_OF), historical_source)

    assert tickers
    assert live.strategy_version == historical.strategy_version == STRATEGY_VERSION
    assert live.stage_counts == historical.stage_counts
    assert live.targets == historical.targets
    assert live.target_weights == historical.target_weights
    pd.testing.assert_series_equal(
        live.ranked.set_index("symbol")["score"],
        historical.ranked.set_index("symbol")["score"],
        check_names=False,
    )


def test_pipeline_exposes_the_required_six_stage_contract():
    """Every result must remain auditable through the requested strategy stages."""
    _, _, bars, fundamentals, universe, _ = shared_inputs()
    result = ResearchPipeline(pipeline_config()).run(
        pd.Timestamp(AS_OF), FrameResearchDataSource(bars, fundamentals, universe)
    )
    assert list(result.stage_counts) == [
        "security_universe",
        "data_snapshot",
        "feature_calculation",
        "factor_score",
        "ranking",
        "portfolio_construction",
    ]
    assert result.strategy_version == "cross-sectional-v2.0.0"
