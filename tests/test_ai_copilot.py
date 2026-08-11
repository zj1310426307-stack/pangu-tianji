from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from ashare_agent.ai_copilot.copilot_service import CopilotGroundingError, CopilotService
from ashare_agent.ai_copilot.evaluation import CopilotEvaluation
from ashare_agent.ai_copilot.evidence_reader import EvidenceReader
from ashare_agent.ai_copilot.memory_store import CopilotStore, CopilotStoreError
from ashare_agent.core.contracts import ModelJsonCompletion, ModelState
from ashare_agent.data_center import DataCenter
from ashare_agent.investment_profile import InvestmentProfile
from ashare_agent.model_provider import ModelExplanation, ModelProviderStatus
from ashare_agent.research_pipeline import ResearchPipelineResult
from ashare_agent.services.model_service import ModelService
from ashare_agent.services.portfolio_risk_engine import PortfolioRiskEngine
from ashare_agent.services.portfolio_risk_store import PortfolioRiskStore
from ashare_agent.services.portfolio_service import PortfolioService


def build_result() -> ResearchPipelineResult:
    """Build one complete two-stock ResearchPipeline result for Copilot evidence."""
    rows = [
        {
            "symbol": "600000.SH", "name": "浦发银行", "industry": "银行",
            "rank": 1, "score": 85.0, "last_price": 10.0,
            "turnover": 120_000_000.0, "volume": 1_200_000.0,
            "market_cap": 200_000_000_000.0, "momentum": 0.10,
            "trend_strength": 0.8, "volatility": 0.20, "max_drawdown": -0.08,
            "median_turnover_20d": 110_000_000.0, "ma20": 9.8, "ma60": 9.2,
            "profit_growth": 12.0, "revenue_growth": 10.0, "roe": 11.0,
            "cash_quality": 90.0, "valuation_complete": True,
            "points_value": 15.0, "points_quality": 15.0, "points_growth": 10.0,
            "points_momentum": 18.0, "points_trend": 10.0,
            "points_low_risk": 10.0, "points_liquidity": 7.0,
        },
        {
            "symbol": "600001.SH", "name": "测试股份", "industry": "制造",
            "rank": 2, "score": 78.0, "last_price": 12.0,
            "turnover": 90_000_000.0, "volume": 900_000.0,
            "market_cap": 50_000_000_000.0, "momentum": 0.08,
            "trend_strength": 0.7, "volatility": 0.24, "max_drawdown": -0.12,
            "median_turnover_20d": 80_000_000.0, "ma20": 11.5, "ma60": 10.8,
            "profit_growth": 8.0, "revenue_growth": 7.0, "roe": 9.0,
            "cash_quality": 80.0, "valuation_complete": True,
            "points_value": 12.0, "points_quality": 13.0, "points_growth": 9.0,
            "points_momentum": 16.0, "points_trend": 9.0,
            "points_low_risk": 9.0, "points_liquidity": 6.0,
        },
    ]
    ranked = pd.DataFrame(rows)
    universe = ranked[["symbol", "name", "industry"]].copy()
    snapshot = ranked[["symbol", "last_price", "turnover", "volume"]].copy()
    history = pd.DataFrame([
        {"symbol": symbol, "date": day, "close": close, "turnover": turnover}
        for symbol, close, turnover in (
            ("600000.SH", 10.0, 120_000_000.0),
            ("600001.SH", 12.0, 90_000_000.0),
        )
        for day in ("2026-08-07", "2026-08-08")
    ])
    fundamentals = pd.DataFrame([
        {"symbol": "600000.SH", "available_at": "2026-06-30", "roe": 11.0},
        {"symbol": "600001.SH", "available_at": "2026-06-30", "roe": 9.0},
    ])
    return ResearchPipelineResult(
        strategy_version="cross-sectional-v2.0.0",
        factor_model_version="cross-sectional-v1.0.0",
        factor_contract_hash="factor-contract",
        as_of=pd.Timestamp("2026-08-08"),
        raw_universe=universe.rename(columns={"symbol": "thscode"}),
        raw_snapshot=snapshot.rename(columns={"symbol": "thscode"}),
        raw_history=history,
        raw_fundamentals=fundamentals,
        universe=universe,
        snapshot=snapshot,
        features=ranked,
        factor_results=ranked,
        ranked=ranked,
        targets=["600000.SH", "600001.SH"],
        target_weights={"600000.SH": 0.10, "600001.SH": 0.10},
        portfolio_rejections={},
        return_history={},
        stage_counts={
            "security_universe": 2,
            "data_snapshot": 2,
            "feature_calculation": 2,
            "factor_score": 2,
            "ranking": 2,
            "portfolio_construction": 2,
        },
        data_quality={"financial_coverage": 1.0, "industry_neutralization": True},
    )


def build_evidence(project_root: Path) -> str:
    """Persist matching Data Center and Portfolio Risk Center evidence."""
    output = project_root / "output"
    result = build_result()
    center = DataCenter(output / "data_center")
    record = center.record_research(
        result,
        run_kind="formal_close_plan",
        metadata={"provider": "test", "observed_at": "2026-08-08T15:10:00+08:00"},
    )
    manifest = center.manifest(record.run_id)
    profile = InvestmentProfile.from_mapping({
        "capital": 100_000,
        "risk_level": "balanced",
        "investment_horizon": "long",
        "max_drawdown_tolerance": 0.10,
        "investment_style": "value_growth",
    })
    risk_engine = PortfolioRiskEngine()
    security_risks = [
        risk_engine.security_risk(row) for row in result.ranked.to_dict("records")
    ]
    target = PortfolioService().build_target_portfolio(
        result, manifest, profile, security_risks
    )
    risk = risk_engine.assess_target_portfolio(result.ranked, target, profile)
    store = PortfolioRiskStore(output / "portfolio_risk_center.db")
    store.save_research_snapshot(target, risk)
    valued = risk_engine.with_valuation(
        risk,
        {
            "service_version": "valuation-v1.0.0",
            "drawdown": -0.02,
            "max_drawdown": -0.05,
        },
        profile,
    )
    store.save_account_snapshot(
        target,
        valued,
        [{
            "symbol": target["positions"][0]["symbol"],
            "name": target["positions"][0]["name"],
            "weight": 0.08,
            "average_cost": 9.9,
            "market_value": 8_000.0,
        }],
        {"signals": [{
            "symbol": target["positions"][0]["symbol"],
            "action": "WATCH",
            "reasons": [{"type": "SCORE_DECLINE", "message": "评分下降观察"}],
            "available_quantity": 800,
            "creates_order": False,
        }]},
        observed_at="2026-08-08T15:20:00+08:00",
        snapshot_type="monitor",
    )
    return record.run_id


class FakeCopilotProvider:
    """Return one grounded JSON response without external network I/O."""

    def __init__(self) -> None:
        self.calls = 0

    def status(self) -> ModelProviderStatus:
        """Report a connected fake model for service tests."""
        return ModelProviderStatus(
            state=ModelState.CONNECTED,
            provider="fake",
            model="fake-v1",
            base_url="https://model.invalid",
            last_checked_at="2026-08-08T00:00:00+00:00",
            message="connected",
        )

    def test_connection(self) -> ModelProviderStatus:
        """Reuse the deterministic connected status."""
        return self.status()

    def complete_json(self, _prompt, payload, **_kwargs) -> ModelJsonCompletion:
        """Cite only evidence ids present in the provided task payload."""
        self.calls += 1
        report_type = payload["task"]["report_type"]
        memory = []
        if report_type == "coach_review":
            memory = [{
                "category": "lesson",
                "content": "持仓风险需持续对照已保存退出证据。",
                "confidence": 0.5,
                "evidence_ids": ["E-PR-EXIT-600000.SH"],
            }]
        return ModelJsonCompletion(
            content={
                "headline": "证据链已完整读取",
                "summary": "当前排名、目标组合与风险快照均有已保存证据。",
                "findings": [{
                    "title": "研究证据",
                    "detail": "研究版本与数据版本已记录。",
                    "evidence_ids": ["E-DC-MANIFEST"],
                }],
                "risks": [{
                    "level": "warning",
                    "message": "风险快照只用于解释，不会生成订单。",
                    "evidence_ids": ["E-PR-RISK-MONITOR"],
                }],
                "memory_candidates": memory,
                "disclaimer": "仅解释已保存证据，不构成投资建议。",
            },
            model="fake-v1",
            generated_at="2026-08-08T00:00:00+00:00",
        )

    def explain(self, _snapshot) -> ModelExplanation:
        """Keep compatibility with the legacy ModelProvider protocol."""
        raise AssertionError("legacy explanation should not be called")

    def explain_research(self, _snapshot) -> ModelExplanation:
        """Keep compatibility with the legacy ModelProvider protocol."""
        raise AssertionError("legacy explanation should not be called")


class FailingCopilotProvider(FakeCopilotProvider):
    """Fail one explicit model request so the task audit can be verified."""

    def complete_json(self, _prompt, _payload, **_kwargs) -> ModelJsonCompletion:
        """Raise once; Copilot must record failure and must not retry."""
        self.calls += 1
        raise RuntimeError("provider unavailable")


def test_evidence_reader_is_the_only_bounded_source_for_agents(tmp_path: Path) -> None:
    """Read checksum-backed research, portfolio, risk and exit evidence by run_id."""
    run_id = build_evidence(tmp_path)
    reader = EvidenceReader(tmp_path)
    bundle = reader.read(run_id, scope="stock_analysis", symbol="600000.SH")
    ids = {item.evidence_id for item in bundle.evidence_items}
    assert bundle.run_id == run_id
    assert bundle.strategy_version == "cross-sectional-v2.0.0"
    assert "E-DC-RANK-600000.SH" in ids
    assert "E-PR-POSITION-600000.SH" in ids
    assert "E-PR-RISK-MONITOR" in ids
    assert "E-PR-EXIT-600000.SH" in ids
    assert bundle.can_trade is False
    serialized = reader.public_payload(bundle)
    assert serialized["used_for_execution"] is False
    assert "api_key" not in str(serialized).lower()

    stock_bundle = reader.get_stock_evidence("600000.SH", run_id)
    stock_ids = {item.evidence_id for item in stock_bundle.evidence_items}
    assert "E-DC-RANK-600000.SH" in stock_ids
    assert "E-DC-RANK-600001.SH" not in stock_ids
    for bounded_bundle in (
        reader.get_portfolio_evidence(run_id),
        reader.get_risk_evidence(run_id),
        reader.get_review_history(run_id),
    ):
        assert bounded_bundle.run_id == run_id
        assert bounded_bundle.can_trade is False
        assert bounded_bundle.used_for_execution is False


def test_evidence_reader_status_path_has_no_storage_side_effect(tmp_path: Path) -> None:
    """Constructing the read boundary must not initialize Data Center assets."""
    reader = EvidenceReader(tmp_path)

    assert reader.latest_run_id() is None
    assert not (tmp_path / "output").exists()


def test_evaluation_rejects_unknown_citations_and_invented_numbers(tmp_path: Path) -> None:
    """Fail closed when AI cites absent evidence or invents numeric claims."""
    run_id = build_evidence(tmp_path)
    bundle = EvidenceReader(tmp_path).read(run_id, scope="risk_alert")
    content = {
        "headline": "风险报告",
        "summary": "未经证据支持的数字999。",
        "findings": [{
            "title": "错误引用", "detail": "无效证据。",
            "evidence_ids": ["E-UNKNOWN"],
        }],
        "risks": [],
        "memory_candidates": [],
        "disclaimer": "仅供研究。",
    }
    _normalized, evaluation = CopilotEvaluation().evaluate(
        content, bundle, allow_memory=False
    )
    assert evaluation["grounded"] is False
    assert evaluation["unknown_citations"] == ["E-UNKNOWN"]
    assert "999" in evaluation["numeric_errors"]


def test_copilot_publishes_grounded_report_reuses_it_and_stores_memory(tmp_path: Path) -> None:
    """Publish audited output once and keep coach memory unconfirmed until user action."""
    run_id = build_evidence(tmp_path)
    provider = FakeCopilotProvider()
    service = CopilotService(tmp_path, ModelService(provider))
    first = service.generate(report_type="risk_alert", run_id=run_id)
    second = service.generate(report_type="risk_alert", run_id=run_id)
    assert first["report_id"] == second["report_id"]
    assert provider.calls == 1
    assert first["status"] == "published"
    assert first["evaluation"]["grounded"] is True
    assert first["can_trade"] is False
    assert first["used_for_execution"] is False

    risk_tasks = [
        item for item in service.store.list_tasks() if item["task_type"] == "risk_alert"
    ]
    assert sorted(item["result_state"] for item in risk_tasks) == ["generated", "reused"]
    assert all(item["status"] == "succeeded" for item in risk_tasks)
    assert all(item["can_schedule"] is False for item in risk_tasks)
    assert all(item["can_affect_execution"] is False for item in risk_tasks)

    coach = service.generate(report_type="coach_review", run_id=run_id)
    memories = service.memories()["items"]
    candidate = next(item for item in memories if item["source_report_id"] == coach["report_id"])
    assert candidate["status"] == "candidate"
    confirmed = service.confirm_memory(candidate["memory_id"])
    assert confirmed["status"] == "confirmed"
    assert confirmed["can_affect_execution"] is False
    assert any(item["category"] == "profile" for item in memories)


def test_copilot_failed_request_is_audited_without_retry(tmp_path: Path) -> None:
    """Persist an explicit failed task while leaving scheduling and trading disabled."""
    run_id = build_evidence(tmp_path)
    provider = FailingCopilotProvider()
    service = CopilotService(tmp_path, ModelService(provider))

    with pytest.raises(RuntimeError, match="provider unavailable"):
        service.generate(report_type="risk_alert", run_id=run_id)

    assert provider.calls == 1
    tasks = service.store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["status"] == "failed"
    assert tasks[0]["result_state"] == "error"
    assert tasks[0]["result_report_id"] is None
    assert tasks[0]["trigger"] == "user_action"
    assert tasks[0]["can_schedule"] is False
    assert tasks[0]["can_affect_execution"] is False
    assert service.store.counts()["failed_task_count"] == 1

    connection = sqlite3.connect(service.store.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE ai_tasks SET can_schedule=1 WHERE task_id=?",
                (tasks[0]["task_id"],),
            )
    finally:
        connection.close()


def test_copilot_accepts_daily_os_audit_source_without_scheduling_power(
    tmp_path: Path,
) -> None:
    """Distinguish external operating cadence from an AI task's own capabilities."""
    run_id = build_evidence(tmp_path)
    service = CopilotService(tmp_path, ModelService(FakeCopilotProvider()))

    report = service.generate(
        report_type="morning_report",
        run_id=run_id,
        trigger="daily_os",
    )

    task = service.store.list_tasks(limit=1)[0]
    assert report["status"] == "published"
    assert task["trigger"] == "daily_os"
    assert task["can_schedule"] is False
    assert task["can_affect_execution"] is False


def test_copilot_store_migrates_legacy_user_action_trigger_without_data_loss(
    tmp_path: Path,
) -> None:
    """Widen the trigger audit domain while preserving historical task rows."""
    path = tmp_path / "legacy-ai.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE ai_tasks (
                task_id TEXT PRIMARY KEY, task_type TEXT NOT NULL, run_id TEXT NOT NULL,
                subject_symbol TEXT, trigger TEXT NOT NULL CHECK(trigger='user_action'),
                status TEXT NOT NULL, result_state TEXT NOT NULL, result_report_id TEXT,
                error_message TEXT, created_time TEXT NOT NULL, started_time TEXT NOT NULL,
                completed_time TEXT, can_schedule INTEGER NOT NULL CHECK(can_schedule=0),
                can_affect_execution INTEGER NOT NULL CHECK(can_affect_execution=0)
            )"""
        )
        connection.execute(
            """INSERT INTO ai_tasks VALUES(
                'legacy-task','risk_alert','legacy-run',NULL,'user_action','failed',
                'error',NULL,'legacy','2026-08-01T00:00:00+00:00',
                '2026-08-01T00:00:00+00:00','2026-08-01T00:00:01+00:00',0,0
            )"""
        )

    store = CopilotStore(path)

    assert store.task("legacy-task")["trigger"] == "user_action"
    created = store.start_task(
        task_type="morning_report",
        run_id="daily-os-run",
        trigger="daily_os",
    )
    assert created["trigger"] == "daily_os"
    assert created["can_schedule"] is False


def test_memory_store_rejects_secrets_and_keeps_execution_disabled(tmp_path: Path) -> None:
    """Never turn investment memory into a secret vault or execution input."""
    store = CopilotStore(tmp_path / "ai.db")
    with pytest.raises(CopilotStoreError, match="密钥"):
        store.add_memory(
            category="preference",
            content="api_key=sk-12345678901234567890",
            source="user_confirmed",
            confidence=1.0,
            confirmed=True,
        )
    saved = store.add_memory(
        category="preference",
        content="更关注价值与成长证据的平衡。",
        source="user_confirmed",
        confidence=1.0,
        confirmed=True,
    )
    assert saved["can_affect_execution"] is False
    assert saved["status"] == "confirmed"
