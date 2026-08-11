from datetime import date
import json

import numpy as np
import pandas as pd
import pytest

from ashare_agent.data_monitor import DataQualityGateError
from ashare_agent.stock_research import DailyStockResearch, ThsStockResearchClient


class FakeResearchClient:
    def __init__(self) -> None:
        self.history_ends = []

    def trading_days(self):
        return {"2026-07-20"}

    def tickers(self):
        items = [
            {"thscode": f"600{i:03d}.SH", "ticker": f"600{i:03d}", "name": f"主板{i}"}
            for i in range(50)
        ]
        items += [
            {"thscode": "300001.SZ", "ticker": "300001", "name": "创业板"},
            {"thscode": "600999.SH", "ticker": "600999", "name": "ST风险"},
        ]
        return items

    def snapshot(self, symbols=None):
        all_symbols = [item["thscode"] for item in self.tickers()]
        selected = symbols or all_symbols
        rows = []
        for index, symbol in enumerate(selected):
            rows.append({
                "thscode": symbol, "last_price": 10 + index / 10,
                "turnover": 200_000_000 - index * 1_000_000,
                "volume": 1_000_000, "price_change_ratio_pct": 1.0,
            })
        timestamp = int(pd.Timestamp("2026-07-20 15:05", tz="Asia/Shanghai").timestamp() * 1000)
        return {"item": rows, "total": len(rows), "timestamp": timestamp}

    def history(self, symbol, start, end):
        self.history_ends.append(end)
        seed = int(symbol[:6]) % 23
        dates = pd.bdate_range(end="2026-07-20", periods=280)
        values = 10 + np.arange(280) * (0.01 + seed / 10000) + np.sin(np.arange(280) / 11) * 0.03
        milliseconds = [int(pd.Timestamp(item, tz="Asia/Shanghai").timestamp() * 1000) for item in dates]
        return pd.DataFrame({
            "date_ms": milliseconds,
            "close_price": values,
            "volume": 1_000_000,
            "turnover": 100_000_000 + seed * 1_000_000,
        })

    def indicators(self, symbol, report):
        seed = int(symbol[:6]) % 17
        return {
            "net_profit_yoy_growth_ratio": 5 + seed,
            "operating_income_yoy_growth_ratio": 4 + seed,
            "index_weighted_avg_roe": 8 + seed / 3,
            "net_profit_cash_content": 70 + seed,
            "earnings_yield": 0.03 + seed / 1000,
            "book_to_price": 0.2 + seed / 100,
        }

    def hot_symbols(self):
        return {"600000.SH"}, []


def config():
    return {
        "minimum_turnover": 50_000_000, "minimum_price": 2.0,
        "maximum_price": 200.0, "history_days": 520,
        "minimum_history_rows": 250, "prefilter_count": 40,
        "financial_count": 20, "target_count": 3,
        "recommendation_count": 10, "entry_rank": 8,
        # Synthetic histories deliberately move together; this test isolates
        # deterministic ranking while correlation selection has its own tests.
        "max_pairwise_correlation": 1.01,
        "max_positions_per_industry": 2,
        "max_single_position_pct": 0.20,
        "target_total_exposure_pct": 0.60,
    }


def test_dynamic_mainboard_ranking_is_deterministic_and_has_no_future_data(tmp_path):
    client = FakeResearchClient()
    research = DailyStockResearch(client, config(), tmp_path)
    first = research.run(date(2026, 7, 20))
    second = research.run(date(2026, 7, 20))
    assert first == second
    assert len(first["candidates"]) == 10
    assert len(first["targets"]) == 3
    assert all(item["symbol"].startswith("600") for item in first["candidates"])
    assert all(end == date(2026, 7, 20) for end in client.history_ends)
    assert [item["rank"] for item in first["candidates"]] == list(range(1, 11))
    assert all(item["components"]["financial_total"] > 0 for item in first["candidates"])
    assert first["strategy_version"] == "cross-sectional-v2.0.0"
    assert first["factor_model_version"] == "cross-sectional-v1.0.0"
    assert first["research_pipeline"] == [
        "Security Universe",
        "Data Snapshot",
        "Feature Calculation",
        "Factor Score",
        "Ranking",
        "Portfolio Construction",
    ]
    assert first["execution_ready"] is True
    assert first["run_id"].startswith(
        f"2026-07-20_{first['strategy_version']}_dv-{first['data_version']}_"
    )
    manifest = tmp_path / first["data_center_manifest"]
    assert manifest.exists()
    assert json.loads(manifest.read_text(encoding="utf-8"))["run_id"] == first["run_id"]
    assert first["investment_profile"]["profile_id"].startswith("profile-")
    assert first["target_portfolio"]["source_run_id"] == first["run_id"]
    assert first["target_portfolio"]["execution_authorized"] is False
    assert first["portfolio_risk"]["drawdown_source"] == "ValuationService_required"
    target_weights = {
        item["symbol"]: item["target_weight"]
        for item in first["target_portfolio"]["positions"]
    }
    candidate_weights = {
        item["symbol"]: item["target_weight"] for item in first["candidates"]
    }
    assert first["targets"] == list(target_weights)
    assert all(candidate_weights[symbol] == target_weights[symbol] for symbol in first["targets"])
    assert first["portfolio_evidence"]["portfolio_id"].startswith("portfolio-")
    assert (tmp_path / "portfolio_risk_center.db").exists()


def test_st_suspended_limit_and_non_mainboard_are_filtered(tmp_path):
    client = FakeResearchClient()
    original = client.snapshot

    def filtered_snapshot(symbols=None):
        data = original(symbols)
        for row in data["item"]:
            if row["thscode"] == "600001.SH":
                row["volume"] = 0
            if row["thscode"] == "600002.SH":
                row["price_change_ratio_pct"] = 9.8
            if row["thscode"] == "600003.SH":
                row["turnover"] = 10_000_000
        return data

    client.snapshot = filtered_snapshot
    plan = DailyStockResearch(client, config(), tmp_path).run(date(2026, 7, 20))
    symbols = {item["symbol"] for item in plan["candidates"]}
    assert not symbols & {"300001.SZ", "600999.SH", "600001.SH", "600002.SH", "600003.SH"}


def test_full_market_snapshot_pagination_is_complete():
    client = ThsStockResearchClient("https://example.test", "TEST_KEY")
    calls = []

    def fake_get(path, params=None):
        calls.append(params["offset"])
        if params["offset"] == 0:
            return {"item": [{"thscode": f"S{i}"} for i in range(10000)], "total": 10003, "timestamp": 1}
        return {"item": [{"thscode": f"S{i}"} for i in range(10000, 10003)], "total": 10003, "timestamp": 2}

    client._get = fake_get
    result = client.snapshot()
    assert calls == [0, 10000]
    assert len(result["item"]) == 10003
    assert result["timestamp"] == 2


def test_intraday_preview_uses_separate_non_executable_state(tmp_path):
    """A morning refresh must not overwrite or masquerade as the formal close plan."""
    client = FakeResearchClient()
    original_snapshot = client.snapshot

    def morning_snapshot(symbols=None):
        data = original_snapshot(symbols)
        data["timestamp"] = int(pd.Timestamp("2026-07-20 10:05", tz="Asia/Shanghai").timestamp() * 1000)
        return data

    client.snapshot = morning_snapshot
    research = DailyStockResearch(client, config(), tmp_path)
    preview = research.run(date(2026, 7, 20), force=True, preview=True)

    assert preview["mode"] == "intraday_preview"
    assert preview["used_for_execution"] is False
    assert preview["execution_ready"] is False
    assert preview["strategy_version"] == "cross-sectional-v2.0.0"
    assert preview["targets"] == []
    assert len(preview["preview_leaders"]) == 3
    assert preview["run_id"].startswith(
        f"2026-07-20_{preview['strategy_version']}_dv-{preview['data_version']}_"
    )
    assert (tmp_path / preview["data_center_manifest"]).exists()
    assert (tmp_path / "research" / "preview.json").exists()
    assert not (tmp_path / "research" / "latest.json").exists()


def test_formal_research_data_gate_blocks_plan_publication(tmp_path):
    """A failed data gate must stop before the formal research plan is published."""

    class BlockedDataIntelligence:
        def evaluate_run(self, run_id, *, preview=None, enforce=False):
            assert run_id
            assert preview is False
            assert enforce is True
            raise DataQualityGateError(
                {
                    "status": "BLOCKED",
                    "score": 40,
                    "issues": [{"message": "synthetic data anomaly"}],
                }
            )

    research = DailyStockResearch(
        FakeResearchClient(), config(), tmp_path,
        data_intelligence=BlockedDataIntelligence(),
    )

    with pytest.raises(Exception, match="DATA_BLOCKED"):
        research.run(date(2026, 7, 20))

    assert not (tmp_path / "research" / "latest.json").exists()
