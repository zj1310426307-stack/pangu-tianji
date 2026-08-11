from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ashare_agent.api.app import create_app
from ashare_agent.core.contracts import ModelJsonCompletion, ModelState
from ashare_agent.model_provider import DisabledModelProvider, ModelProviderStatus
from ashare_agent.quant_ai import (
    AIQuantResearchService,
    QuantAIExperimentReader,
    ResearchAnalystScheduler,
    ResearchEvidenceBundle,
    ResearchEvidenceItem,
)
from ashare_agent.quant_ai.research_memory import ResearchMemoryStore
from ashare_agent.quant_lab.contracts import sha256_json
from ashare_agent.services.model_service import ModelService


PROTECTED_HASHES = {
    "src/ashare_agent/research_pipeline.py": "1FD79ED464E75EE1D6F1477AB7267178FD00D9329AA8EFC311C5B57058678577",
    "src/ashare_agent/factor_model.py": "4AE32405EA360C5C33F23A1D2A4EDC011A0F3A9A09182624084FF1FCCDFF0295",
    "src/ashare_agent/data_center.py": "0294A451837019C0FA6678C359FB919884A066D674662F62F86A34204B99E6BD",
    "src/ashare_agent/data_center_models.py": "153699C313892C4610779316E138061ECB2BE770B2008A827E2D785B41BAEC62",
    "src/ashare_agent/services/portfolio_service.py": "41F3037DB2E0157B049B7496F30C36748A434B4108A5CC64C41872E71A892280",
    "src/ashare_agent/services/portfolio_risk_engine.py": "ED6CC9125795946954D6909033254A5D0A5B2E77D7D277FA04B2ED5D984336D2",
    "src/ashare_agent/services/portfolio_risk_store.py": "BEA51A648203BF3AD3CA9081E2C23F6B7B2A72BF109E4BFD177B422C7CEAC30C",
    "src/ashare_agent/services/exit_engine.py": "8E0ECAA5A20EECBCDBCAB5259238F989D7710DED8EFFCA6792C2AB732B2F5264",
    "src/ashare_agent/services/valuation_service.py": "5331F1E806A4EDFF6DD46A38311ACCE6B791349ADEA300C197760D66D29153B4",
    "src/ashare_agent/paper_portfolio.py": "940A25CFF6FE871531D0272B6C17592619ED434D597190D478F7C2C3D94D28F0",
    "src/ashare_agent/execution_rules.py": "A9A791C9919639923168B6AA7C75BF7D36A029154952005D05FC7E45B814BB04",
    "src/ashare_agent/broker.py": "3E8C2B1BD4E222A3CA2BB7D0EA0CC40E4C16A8EBC6E3C40EB2C3CD46E6487045",
    "src/ashare_agent/cross_sectional_backtest.py": "1378875FEBCC15FD95C3180771145BE15B0D888E15E054B73ADD89BC2A8330C2",
    "src/ashare_agent/quant_lab/factor_research/service.py": "D40DA401B2EAE468EF0A8D4D610384E6728CBFE69C7AB37A2486396D5CE810BE",
    "src/ashare_agent/quant_lab/robustness/service.py": "A741B73CC21FA5D8964AF04A7569EA363991AA15A1569C004AAB8AA28E65D511",
    "src/ashare_agent/quant_lab/validation_gate/service.py": "788513688A1DA66729A1767FE26B0109B97E6FCEBD7E2AD398D11F9115320ECB",
}


def _item(evidence_id: str, source_type: str, payload: dict) -> ResearchEvidenceItem:
    return ResearchEvidenceItem(
        evidence_id=evidence_id,
        source_type=source_type,
        source_id=evidence_id.lower(),
        source_hash=sha256_json(payload),
        observed_at="2026-08-10T00:00:00+00:00",
        payload=payload,
    )


def bundle(label: str = "POINT_IN_TIME") -> ResearchEvidenceBundle:
    """Build a sealed synthetic fixture with all five research evidence domains."""
    validation = {
        "sections": {
            "01_strategy_overview": {"current_state": "ROBUSTNESS_VALIDATED"},
            "06_committee_recommendation": {
                "recommended_state": "OUT_OF_SAMPLE",
                "system_recommendation": "READY_FOR_HUMAN_REVIEW",
            },
        }
    }
    factor = {
        "sections": {
            "03_ic_analysis": {
                "quality": {"20": {
                    "observation_count": 240,
                    "ic_mean": 0.05,
                    "ic_std": 0.10,
                    "icir": 0.50,
                    "ic_hit_rate": 0.60,
                    "t_stat": 2.0,
                    "p_value": 0.04,
                }}
            },
            "06_correlation_redundancy": {"review_candidates": []},
            "07_ablation": {"contribution_order": [{"factor": "quality", "change": 0.02}]},
            "08_market_regime": {"availability": "available", "regime_date_counts": {"range": 120}},
        }
    }
    robust = {
        "sections": {
            "05_cost_stress": {"availability": "available"},
            "06_execution_delay": {"availability": "available"},
            "07_market_regime": {"availability": "available"},
            "09_overfitting": {"risk": "medium"},
            "10_robustness_score": {"score": 78.0, "grade": "B", "missing_components": []},
        }
    }
    market = {
        "factor_regime": factor["sections"]["08_market_regime"],
        "robustness_regime": robust["sections"]["07_market_regime"],
        "historical_analogy_generated": False,
        "market_label_inferred": False,
    }
    risk = {
        "evidence_items": [{
            "evidence_id": "E-PR-RISK-MONITOR",
            "payload": {"weighted_security_risk": 35.0, "industry_exposure": {"制造": 0.25}},
        }],
        "data_gaps": [],
    }
    return ResearchEvidenceBundle(
        strategy_id="pangu-cross-sectional",
        strategy_version="cross-sectional-v2.0.0",
        experiment_id="factor-exp-1",
        research_run_id="formal-run-1",
        dataset_label=label,
        items=(
            _item("E-QA-VALIDATION-review-1", "strategy_validation_report", validation),
            _item("E-QA-FACTOR-factor-run-1", "factor_research_report", factor),
            _item("E-QA-ROBUST-robust-run-1", "strategy_robustness_report", robust),
            _item("E-QA-MARKET-review-1", "registered_market_regime_evidence", market),
            _item("E-QA-RISK-formal-run-1", "portfolio_risk_evidence_bundle", risk),
        ),
    )


class FakeReader:
    def __init__(self, value: ResearchEvidenceBundle):
        self.value = value

    def latest(self):
        return self.value

    def read(self, _review_id):
        return self.value


class GroundedProvider:
    """Return cited JSON and never expose any execution method to the analyst."""

    def __init__(self, hallucinate: bool = False):
        self.hallucinate = hallucinate
        self.calls = 0

    def status(self):
        return ModelProviderStatus(
            state=ModelState.CONNECTED,
            provider="fake",
            model="fake-quant-v1",
            base_url="https://model.invalid",
            last_checked_at="2026-08-10T00:00:00+00:00",
            message="connected",
        )

    def complete_json(self, _prompt, payload, **_kwargs):
        self.calls += 1
        citation = payload["evidence"]["evidence_items"][0]["evidence_id"]
        summary = "研究证据支持继续人工验证。"
        if self.hallucinate:
            summary = "策略未来收益达到999。"
        return ModelJsonCompletion(
            content={
                "summary": summary,
                "summary_evidence_ids": [citation],
                "conclusions": [{
                    "claim_id": "model-conclusion",
                    "label": "INFERENCE",
                    "title": "证据摘要",
                    "statement": "策略结论仍需独立样本外检验。",
                    "evidence_ids": [citation],
                    "metrics": {},
                }],
            },
            model="fake-quant-v1",
            generated_at="2026-08-10T00:00:00+00:00",
        )


def service(tmp_path: Path, provider=None, label="POINT_IN_TIME"):
    store = ResearchMemoryStore(tmp_path / "agent.db")
    return AIQuantResearchService(
        tmp_path,
        model_service=ModelService(provider or GroundedProvider()),
        strategy_validation_service=object(),
        reader=FakeReader(bundle(label)),
        store=store,
    )


def test_grounded_research_brief_creates_memory_questions_and_no_authority(tmp_path):
    analyst = service(tmp_path)
    report = analyst.generate(scheduled_for="2026-08-10T08:00:00+08:00", trigger="scheduler")
    assert report["status"] == "published"
    assert report["evaluation"]["model"]["grounded"] is True
    assert report["can_trade"] is False
    assert report["can_modify_strategy"] is False
    assert report["can_approve_strategy"] is False
    assert analyst.store.memories()
    assert analyst.store.questions()
    assert all(item["status"] == "candidate" for item in analyst.store.memories())
    assert all(item["can_launch_experiment"] is False for item in analyst.store.questions())


def test_hallucinated_number_is_rejected_and_not_persisted_as_model_content(tmp_path):
    analyst = service(tmp_path, GroundedProvider(hallucinate=True))
    report = analyst.generate(scheduled_for="2026-08-10T08:00:00+08:00", trigger="scheduler")
    assert report["status"] == "degraded"
    assert report["evaluation"]["model"]["grounded"] is False
    assert "999" in report["evaluation"]["model"]["numeric_errors"]
    assert "999" not in str(report["model_analysis"])


def test_disabled_model_publishes_only_deterministic_degraded_research(tmp_path):
    analyst = service(tmp_path, DisabledModelProvider())
    report = analyst.generate(scheduled_for="2026-08-10T08:00:00+08:00")
    assert report["status"] == "degraded"
    assert report["model_analysis"]["availability"] == "unavailable_or_rejected"
    assert report["evaluation"]["deterministic"]["grounded"] is True


def test_synthetic_fixture_is_explicit_and_never_becomes_formal_advice(tmp_path):
    report = service(tmp_path, label="SYNTHETIC_TEST_ONLY").generate(
        scheduled_for="2026-08-10T08:00:00+08:00"
    )
    assert report["synthetic_warning"]
    assert report["used_for_execution"] is False


def test_evidence_item_rejects_artifact_hash_mismatch():
    with pytest.raises(ValueError, match="source_hash"):
        ResearchEvidenceItem(
            evidence_id="E-QA-BAD", source_type="factor_research_report",
            source_id="bad", source_hash="0" * 64, observed_at=None,
            payload={"value": 1},
        )


def test_scheduler_is_0800_trading_day_only_and_slot_idempotent(tmp_path):
    analyst = service(tmp_path)
    scheduler = ResearchAnalystScheduler(
        analyst,
        trading_day_provider=lambda selected: selected.weekday() < 5,
    )
    zone = ZoneInfo("Asia/Shanghai")
    due = datetime(2026, 8, 10, 8, 10, tzinfo=zone)
    first = scheduler.tick(due)
    second = scheduler.tick(due)
    assert first["report"]["report_id"] == second["report"]["report_id"]
    assert scheduler.tick(datetime(2026, 8, 9, 8, 10, tzinfo=zone))["state"] == "not_due"
    assert scheduler.tick(datetime(2026, 8, 10, 9, 0, tzinfo=zone))["state"] == "not_due"


def test_database_constraints_reject_trading_and_auto_experiment_capabilities(tmp_path):
    analyst = service(tmp_path)
    report = analyst.generate(scheduled_for="2026-08-10T08:00:00+08:00")
    question = analyst.store.questions()[0]
    with sqlite3.connect(analyst.store.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE ai_research_reports SET can_trade=1 WHERE report_id=?",
                (report["report_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE research_questions SET can_launch_experiment=1 WHERE question_id=?",
                (question["question_id"],),
            )


def test_experiment_reader_verifies_and_links_all_registered_sources(tmp_path):
    source = bundle()
    validation = next(item.payload for item in source.items if item.source_type == "strategy_validation_report")
    factor = next(item.payload for item in source.items if item.source_type == "factor_research_report")
    robust = next(item.payload for item in source.items if item.source_type == "strategy_robustness_report")

    class StrategyRegistry:
        def list_reviews(self, limit=1):
            return [{"review_id": "review-1"}]

        def review(self, review_id):
            assert review_id == "review-1"
            return {
                "review_id": review_id,
                "strategy_id": "pangu-cross-sectional",
                "strategy_version": "cross-sectional-v2.0.0",
                "dataset_label": "POINT_IN_TIME",
                "created_at": "2026-08-10T00:00:00+00:00",
                "evidence_ids": [
                    f"factor_report:factor-run-1:{sha256_json(factor)}",
                    f"robustness_report:robust-run-1:{sha256_json(robust)}",
                ],
                "report": validation,
            }

        def version(self, _strategy_id, _version):
            return {"spec": {"research_run_id": "formal-run-1"}}

    class ExperimentRegistry:
        def factor_report(self, run_id):
            assert run_id == "factor-run-1"
            return factor

        def robustness_report(self, run_id):
            assert run_id == "robust-run-1"
            return robust

    class PortfolioReader:
        def get_risk_evidence(self, run_id):
            assert run_id == "formal-run-1"
            return SimpleNamespace(data_gaps=())

        def public_payload(self, _bundle):
            return {"generated_at": "2026-08-10T00:00:00+00:00", "evidence_items": []}

    reader = QuantAIExperimentReader(
        tmp_path,
        strategy_registry=StrategyRegistry(),
        experiment_registry=ExperimentRegistry(),
        portfolio_reader=PortfolioReader(),
    )
    result = reader.read("review-1")
    assert {item.source_type for item in result.items} == {
        "strategy_validation_report", "factor_research_report",
        "strategy_robustness_report", "registered_market_regime_evidence",
        "portfolio_risk_evidence_bundle",
    }
    assert result.can_trade is False and result.can_approve_strategy is False


def test_experiment_reader_rejects_validation_link_hash_mismatch(tmp_path):
    source = bundle()
    validation = next(item.payload for item in source.items if item.source_type == "strategy_validation_report")
    factor = next(item.payload for item in source.items if item.source_type == "factor_research_report")

    class StrategyRegistry:
        def review(self, _review_id):
            return {
                "review_id": "review-1",
                "strategy_id": "pangu-cross-sectional",
                "strategy_version": "cross-sectional-v2.0.0",
                "dataset_label": "POINT_IN_TIME",
                "created_at": "2026-08-10T00:00:00+00:00",
                "evidence_ids": [f"factor_report:factor-run-1:{'0' * 64}"],
                "report": validation,
            }

        def version(self, _strategy_id, _version):
            return {"spec": {}}

    class ExperimentRegistry:
        def factor_report(self, _run_id):
            return factor

    reader = QuantAIExperimentReader(
        tmp_path,
        strategy_registry=StrategyRegistry(),
        experiment_registry=ExperimentRegistry(),
        portfolio_reader=object(),
    )
    result = reader.read("review-1")
    assert "factor_report_hash_or_read_failure" in result.data_gaps
    assert all(item.source_type != "factor_research_report" for item in result.items)


def test_ai_research_api_keeps_writes_local_and_never_returns_capabilities(tmp_path):
    analyst = service(tmp_path)

    class FakeDaily:
        def close(self):
            pass

    class FakeWorkbench:
        def get(self):
            return {}

    class FakeInvestmentOS:
        center = object()
        def close(self):
            pass

    class FakeStrategyValidation:
        registry = object()
        approvals = object()
        def dashboard(self):
            return {}

    app = create_app(
        tmp_path,
        daily_research_service=FakeDaily(),
        workbench_service=FakeWorkbench(),
        copilot_service=object(),
        investment_os_service=FakeInvestmentOS(),
        strategy_validation_service=FakeStrategyValidation(),
        quant_ai_service=analyst,
    )
    with TestClient(app) as client:
        dashboard = client.get("/api/v1/ai-research")
        assert dashboard.status_code == 200
        assert dashboard.json()["can_trade"] is False
        blocked = client.post("/api/v1/ai-research/briefs", json={})
        assert blocked.status_code == 403
        created = client.post(
            "/api/v1/ai-research/briefs",
            json={}, headers={"X-Ashare-Client": "local-dashboard"},
        )
        assert created.status_code == 200
        assert created.json()["can_modify_factor_weights"] is False


def test_protected_research_portfolio_valuation_and_trading_hashes_are_unchanged():
    root = Path(__file__).resolve().parents[1]
    for relative, expected in PROTECTED_HASHES.items():
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest().upper()
        assert actual == expected
