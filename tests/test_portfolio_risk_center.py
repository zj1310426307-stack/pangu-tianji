from __future__ import annotations

import pandas as pd
import pytest

from ashare_agent.investment_profile import InvestmentProfile
from ashare_agent.research_pipeline import ResearchPipelineResult
from ashare_agent.services.exit_engine import ExitEngine
from ashare_agent.services.portfolio_risk_engine import PortfolioRiskEngine
from ashare_agent.services.portfolio_risk_store import (
    PortfolioRiskStore,
    PortfolioRiskStoreError,
)
from ashare_agent.services.portfolio_service import PortfolioService, PortfolioServiceError


def research_result() -> ResearchPipelineResult:
    """Build ranked factor evidence without invoking or changing ResearchPipeline."""
    rows = []
    for index, symbol in enumerate(("600000.SH", "600001.SH", "600002.SH"), start=1):
        rows.append({
            "symbol": symbol,
            "name": f"股票{index}",
            "industry": "银行" if index < 3 else "制造",
            "rank": index,
            "score": 90 - index * 5,
            "last_price": 10 + index,
            "volatility": 0.15 + index * 0.02,
            "max_drawdown": -0.06 - index * 0.01,
            "momentum": 0.10 - index * 0.01,
            "trend_strength": 0.8,
            "median_turnover_20d": 150_000_000,
            "profit_growth": 12.0,
            "revenue_growth": 10.0,
            "roe": 12.0,
            "cash_quality": 90.0,
            "ma20": 10.0,
            "ma60": 9.5,
            "points_value": 10.0 + index,
            "points_quality": 12.0,
            "points_growth": 8.0,
            "points_momentum": 14.0,
            "points_trend": 8.0,
            "points_low_risk": 7.0,
            "points_liquidity": 4.0,
        })
    ranked = pd.DataFrame(rows)
    empty = pd.DataFrame()
    return ResearchPipelineResult(
        strategy_version="cross-sectional-v2.0.0",
        factor_model_version="cross-sectional-v1.0.0",
        factor_contract_hash="factor-contract",
        as_of=pd.Timestamp("2026-08-08"),
        raw_universe=empty,
        raw_snapshot=empty,
        raw_history=empty,
        raw_fundamentals=empty,
        universe=ranked[["symbol", "name", "industry"]],
        snapshot=ranked,
        features=ranked,
        factor_results=ranked,
        ranked=ranked,
        targets=ranked["symbol"].tolist(),
        target_weights={symbol: 0.10 for symbol in ranked["symbol"]},
        portfolio_rejections={},
        return_history={},
        stage_counts={},
        data_quality={},
    )


def evidence() -> dict:
    """Return the minimum verified Data Center contract required by PortfolioService."""
    return {
        "run_id": "2026-08-08_cross-sectional-v2.0.0_dv-test_00000000",
        "data_version": "test",
        "strategy_version": "cross-sectional-v2.0.0",
        "factor_version": "cross-sectional-v1.0.0",
        "factor_contract_hash": "factor-contract",
    }


def test_profile_and_portfolio_service_enforce_user_caps_without_orders():
    """Build a round-lot Target Portfolio bounded by the user profile."""
    profile = InvestmentProfile.from_mapping({
        "capital": 100_000,
        "risk_level": "balanced",
        "investment_horizon": "medium",
        "max_drawdown_tolerance": 0.08,
        "investment_style": "value",
    })
    service = PortfolioService()
    target = service.build_target_portfolio(research_result(), evidence(), profile)

    assert target["execution_authorized"] is False
    assert target["source_run_id"] == evidence()["run_id"]
    assert target["target_exposure"] <= profile.maximum_exposure
    assert all(item["target_weight"] <= profile.maximum_single_weight for item in target["positions"])
    assert all(item["round_lot_quantity"] % 100 == 0 for item in target["positions"])
    assert target["cash_reserve_weight"] == pytest.approx(1 - target["target_exposure"])

    current = [{"symbol": "600000.SH", "weight": 0.01}, {"symbol": "600999.SH", "weight": 0.10}]
    actions = service.rebalance_actions(target, current, profile)
    assert {item["symbol"]: item["action"] for item in actions}["600999.SH"] == "EXIT"
    assert all(item["creates_order"] is False for item in actions)


def test_portfolio_service_rejects_mismatched_data_center_evidence():
    """A target cannot mix one Pipeline result with another run's factor contract."""
    invalid = {**evidence(), "factor_contract_hash": "other"}
    with pytest.raises(PortfolioServiceError, match="factor_contract_hash"):
        PortfolioService().build_target_portfolio(
            research_result(), invalid, InvestmentProfile.from_mapping(None)
        )


def test_risk_engine_reports_security_exposure_concentration_and_valuation_drawdown():
    """Use canonical valuation drawdown while calculating independent target exposures."""
    profile = InvestmentProfile.from_mapping(None)
    result = research_result()
    target = PortfolioService().build_target_portfolio(result, evidence(), profile)
    engine = PortfolioRiskEngine()
    risk = engine.assess_target_portfolio(result.ranked, target, profile)
    assert risk["max_drawdown"] is None
    assert risk["drawdown_source"] == "ValuationService_required"
    assert set(risk["industry_exposure"]) == {"银行", "制造"}
    assert risk["concentration"]["hhi"] > 0
    assert len(risk["security_risks"]) == 3

    valued = engine.with_valuation(risk, {
        "service_version": "valuation-v1.0.0",
        "drawdown": -0.06,
        "max_drawdown": -0.12,
    }, profile)
    assert valued["max_drawdown"] == -0.12
    assert valued["drawdown_source"] == "valuation-v1.0.0"
    assert "MAX_DRAWDOWN_TOLERANCE_BREACHED" in {
        item["code"] for item in valued["risk_flags"]
    }


def test_exit_engine_covers_four_exit_causes_and_t_plus_one():
    """Generate exit intent for score, fundamentals, risk and technical damage."""
    profile = InvestmentProfile.from_mapping(None)
    candidate = {
        "symbol": "600000.SH",
        "rank": 25,
        "score": 40,
        "signals": {
            "profit_growth": -20,
            "revenue_growth": -10,
            "momentum": -0.05,
            "trend_strength": 0,
            "ma60": 11,
        },
    }
    result = ExitEngine().evaluate(
        [{
            "symbol": "600000.SH",
            "quantity": 100,
            "available_quantity": 0,
            "last_price": 10,
            "entry_score": 80,
        }],
        [candidate],
        [{"symbol": "600000.SH", "risk_score": 80}],
        profile,
        {"risk_flags": [{"code": "MAX_DRAWDOWN_TOLERANCE_BREACHED"}]},
    )
    signal = result["signals"][0]
    assert signal["action"] == "DEFER_T1"
    assert {item["type"] for item in signal["reasons"]} == {
        "SCORE_DECLINE",
        "FUNDAMENTAL_DETERIORATION",
        "RISK_TRIGGER",
        "TECHNICAL_BREAKDOWN",
    }
    assert result["creates_orders"] is False


def test_profile_accepts_human_friendly_aliases_and_percent_drawdown():
    """Normalize common UI values into the canonical investment-profile contract."""
    profile = InvestmentProfile.from_mapping({
        "capital": 150_000,
        "risk": "medium",
        "investment_period": "3_year",
        "max_drawdown": 15,
        "investment_style": "value_growth",
    })
    assert profile.risk_level == "balanced"
    assert profile.investment_horizon == "long"
    assert profile.max_drawdown_tolerance == pytest.approx(0.15)
    assert profile.investment_style == "value_growth"


def test_dynamic_position_sizing_combines_score_and_security_risk():
    """A high-risk stock receives less capital than a comparable low-risk stock."""
    profile = InvestmentProfile.from_mapping({
        "capital": 1_000_000,
        "risk_level": "aggressive",
        "investment_horizon": "medium",
        "max_drawdown_tolerance": 0.15,
        "investment_style": "balanced",
    })
    risks = [
        {"symbol": "600000.SH", "risk_score": 20},
        {"symbol": "600001.SH", "risk_score": 50},
        {"symbol": "600002.SH", "risk_score": 80},
    ]
    target = PortfolioService().build_target_portfolio(
        research_result(), evidence(), profile, risks
    )
    positions = {item["symbol"]: item for item in target["positions"]}
    assert positions["600000.SH"]["target_weight"] > positions["600002.SH"]["target_weight"]
    assert positions["600000.SH"]["risk_coefficient"] > positions["600002.SH"]["risk_coefficient"]
    assert all(
        item["target_value"] == pytest.approx(
            item["round_lot_quantity"] * item["reference_price"]
        )
        for item in positions.values()
    )
    assert target["positioning_model_version"] == "score-risk-sizing-v1.0.0"
    assert target["target_exposure"] <= target["exposure_cap"]


def test_risk_engine_adds_stress_drawdown_size_cycle_and_coverage():
    """Expose stress, size and cycle evidence without presenting it as actual drawdown."""
    result = research_result()
    result.ranked.loc[result.ranked["symbol"] == "600000.SH", "market_cap"] = 200e9
    result.ranked.loc[result.ranked["symbol"] == "600001.SH", "market_cap"] = 50e9
    result.ranked.loc[result.ranked["symbol"] == "600002.SH", "market_cap"] = 10e9
    profile = InvestmentProfile.from_mapping(None)
    engine = PortfolioRiskEngine()
    security_risks = [engine.security_risk(row) for row in result.ranked.to_dict("records")]
    target = PortfolioService().build_target_portfolio(
        result, evidence(), profile, security_risks
    )
    risk = engine.assess_target_portfolio(result.ranked, target, profile)
    assert risk["predicted_max_drawdown"] < 0
    assert risk["predicted_drawdown_method"].endswith("仅为压力代理")
    assert set(risk["size_exposure"]) == {"large_cap", "mid_cap", "small_cap", "unknown"}
    assert set(risk["cycle_exposure"]) == {"cyclical", "non_cyclical", "unknown"}
    assert all(
        item["data_coverage"]["capital_flow_20d"] is False
        for item in risk["security_risks"]
    )


def test_exit_engine_supports_watch_and_capital_flow_deterioration():
    """Separate a score watch state from a severe capital-flow exit."""
    profile = InvestmentProfile.from_mapping(None)
    engine = ExitEngine()
    watch = engine.evaluate(
        [{"symbol": "600000.SH", "available_quantity": 100, "entry_score": 90, "last_price": 12}],
        [{
            "symbol": "600000.SH", "rank": 10, "score": 74,
            "signals": {"momentum": 0.1, "trend_strength": 1, "ma20": 11, "ma60": 10},
        }],
        [{"symbol": "600000.SH", "risk_score": 20}],
        profile,
    )
    assert watch["signals"][0]["action"] == "WATCH"

    exit_result = engine.evaluate(
        [{"symbol": "600000.SH", "available_quantity": 100, "entry_score": 90, "last_price": 12}],
        [{
            "symbol": "600000.SH", "rank": 10, "score": 90,
            "signals": {
                "momentum": 0.1, "trend_strength": 1, "ma20": 11, "ma60": 10,
                "net_inflow_20d_ratio": -0.12,
            },
        }],
        [{"symbol": "600000.SH", "risk_score": 20}],
        profile,
    )
    assert exit_result["signals"][0]["action"] == "EXIT"
    assert "CAPITAL_FLOW_DETERIORATION" in {
        item["type"] for item in exit_result["signals"][0]["reasons"]
    }


def test_portfolio_risk_store_persists_idempotent_evidence(tmp_path):
    """Persist the profile, target positions and planned risk in a separate catalog."""
    profile = InvestmentProfile.from_mapping(None)
    result = research_result()
    engine = PortfolioRiskEngine()
    security_risks = [engine.security_risk(row) for row in result.ranked.to_dict("records")]
    target = PortfolioService().build_target_portfolio(
        result, evidence(), profile, security_risks
    )
    risk = engine.assess_target_portfolio(result.ranked, target, profile)
    store = PortfolioRiskStore(tmp_path / "portfolio-risk.db")
    first = store.save_research_snapshot(target, risk)
    second = store.save_research_snapshot(target, risk)
    assert first == second
    persisted = store.portfolio(first["portfolio_id"])
    assert persisted is not None
    assert persisted["portfolio"]["execution_authorized"] == 0
    assert len(persisted["positions"]) == len(target["positions"])
    assert len(persisted["risk_snapshots"]) == 1
    assert persisted["risk_snapshots"][0]["predicted_max_drawdown"] == pytest.approx(
        risk["predicted_max_drawdown"]
    )

    valued_risk = engine.with_valuation(
        risk,
        {
            "service_version": "valuation-v1.0.0",
            "drawdown": -0.02,
            "max_drawdown": -0.05,
        },
        profile,
    )
    runtime = store.save_account_snapshot(
        target,
        valued_risk,
        [{
            "symbol": target["positions"][0]["symbol"],
            "name": target["positions"][0]["name"],
            "weight": 0.08,
            "average_cost": 10.0,
            "market_value": 8_000.0,
        }],
        {"signals": [{
            "symbol": target["positions"][0]["symbol"],
            "action": "WATCH",
            "reasons": [{"type": "SCORE_DECLINE", "message": "观察"}],
            "available_quantity": 100,
            "creates_order": False,
        }]},
        observed_at="2026-08-08T10:00:00+00:00",
        snapshot_type="monitor",
    )
    assert runtime["portfolio_id"] == first["portfolio_id"]
    persisted = store.portfolio(first["portfolio_id"])
    assert len(persisted["risk_snapshots"]) == 2
    assert persisted["positions"][0]["current_weight"] == pytest.approx(0.08)
    assert persisted["exit_signals"][0]["action"] == "WATCH"

    with pytest.raises(PortfolioRiskStoreError, match="拒绝保存"):
        store.save_research_snapshot({**target, "execution_authorized": True}, risk)
