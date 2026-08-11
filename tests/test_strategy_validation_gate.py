from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ashare_agent.api.app import create_app
from ashare_agent.quant_lab.exceptions import (
    ImmutableExperimentError,
    InvalidStateTransitionError,
)
from ashare_agent.quant_lab.validation_gate import (
    PromotionPolicy,
    StrategyState,
    StrategyValidationGate,
    StrategyValidationService,
    StrategyVersionSpec,
    ValidationEvidenceBundle,
)


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
}


def _strategy(label: str = "POINT_IN_TIME") -> StrategyVersionSpec:
    return StrategyVersionSpec(
        strategy_id="pangu-cross-sectional",
        name="盘古横截面七因子",
        version="cross-sectional-v2.0.0",
        creator="研究委员会",
        description="仅用于验证准入治理合同",
        research_run_id="research-run-20260810",
        factor_version="factor-v2.0.0",
        parameter_hash="a" * 64,
        code_hash="b" * 64,
        dataset_version="dc-20260810",
        dataset_label=label,
    )


def _factor_report(label: str) -> dict:
    factors = ["trend", "momentum", "low_volatility", "low_drawdown", "value", "quality", "growth"]
    return {
        "run_id": "factor-run-1",
        "dataset_label": label,
        "strategy_version": "cross-sectional-v2.0.0",
        "factor_version": "factor-v2.0.0",
        "evidence_ids": ["dc-run-1"],
        "sections": {
            "03_ic_analysis": {
                factor: {"20": {
                    "observation_count": 120, "icir": 0.35, "ic_hit_rate": 0.58,
                }} for factor in factors
            },
            "04_quantile_returns": {
                factor: {"5": {"20": {"monotonic_low_to_high": True}}}
                for factor in factors
            },
            "06_correlation_redundancy": {"review_candidates": []},
        },
    }


def _robustness_report(label: str) -> dict:
    return {
        "run_id": "robust-run-1",
        "dataset_label": label,
        "strategy_version": "cross-sectional-v2.0.0",
        "factor_version": "factor-v2.0.0",
        "evidence_ids": [{"type": "data_center_run", "id": "dc-run-1"}],
        "sections": {
            "10_robustness_score": {"availability": "AVAILABLE", "score": 82.0},
            "11_research_conclusion": {"best_parameter_selected": False},
        },
    }


def _bundle(label: str = "POINT_IN_TIME", *, paper: bool = True) -> ValidationEvidenceBundle:
    return ValidationEvidenceBundle(
        strategy_id="pangu-cross-sectional",
        strategy_version="cross-sectional-v2.0.0",
        data_integrity={
            "evidence_id": "data-check-1",
            "dataset_label": label,
            "validation_status": "PASS",
            "future_data_used": False,
            "artifacts_verified": True,
            "asset_hashes_match": True,
            "point_in_time_fields": True,
        },
        factor_report=_factor_report(label),
        robustness_report=_robustness_report(label),
        out_of_sample={
            "evidence_id": "oos-1", "dataset_label": label,
            "holdout_locked_before_evaluation": True,
            "used_for_parameter_selection": False,
            "observation_count": 252, "sharpe": 0.75, "max_drawdown": 0.12,
            "artifact_verified": True,
        },
        paper_trading={
            "evidence_id": "paper-1", "trading_days": 100,
            "total_return": 0.08, "max_drawdown": 0.06,
            "execution_deviation": 0.01, "unknown_order_count": 0,
            "reconciliation_gap_count": 0, "live_orders_created": False,
            "artifact_verified": True,
        } if paper else None,
        evidence_ids=("data-check-1", "factor-run-1", "robust-run-1", "oos-1"),
    )


def test_lifecycle_is_sequential_and_contains_no_live_state():
    policy = PromotionPolicy()
    assert [item.value for item in StrategyState] == [
        "DRAFT", "RESEARCH", "FACTOR_VALIDATED", "ROBUSTNESS_VALIDATED",
        "OUT_OF_SAMPLE", "PAPER_TRADING", "RETIRED",
    ]
    assert policy.next_state(StrategyState.OUT_OF_SAMPLE) == StrategyState.PAPER_TRADING
    assert policy.next_state(StrategyState.PAPER_TRADING) is None
    with pytest.raises(InvalidStateTransitionError):
        policy.validate(StrategyState.DRAFT, StrategyState.OUT_OF_SAMPLE)


def test_synthetic_fixture_can_never_receive_promotion_recommendation():
    review = StrategyValidationGate().review(
        current_state=StrategyState.DRAFT,
        evidence=_bundle("SYNTHETIC_TEST_ONLY"),
    )
    assert review.recommended_state is None
    assert review.recommendation == "RESEARCH_ONLY_SYNTHETIC_EVIDENCE"
    assert review.can_auto_promote is False and review.can_trade is False
    assert review.outcomes[0].status.value == "BLOCKED"


def test_health_score_does_not_renormalize_missing_paper_evidence():
    review = StrategyValidationGate().review(
        current_state=StrategyState.OUT_OF_SAMPLE,
        evidence=_bundle(paper=False),
    )
    assert review.recommended_state == StrategyState.PAPER_TRADING
    assert review.health.score is None
    assert review.health.coverage == pytest.approx(0.85)
    assert review.health.partial_score <= 85.0
    assert review.health.missing_components == ("execution",)


def test_review_is_sealed_and_does_not_change_strategy_state(tmp_path):
    service = StrategyValidationService(tmp_path)
    result = service.review(strategy=_strategy(), evidence=_bundle())
    assert result["approval_created"] is False
    assert result["strategy_state_changed"] is False
    assert result["review"]["recommended_state"] == "RESEARCH"
    assert service.registry.strategy("pangu-cross-sectional")["current_state"] == "DRAFT"
    manifest = result["artifact_manifest"]
    assert {item["name"] for item in manifest["artifacts"]} == {
        "committee_report.json", "evidence_bundle.json", "validation_review.json",
    }
    assert (service.artifacts.run_dir(
        "pangu-cross-sectional", result["review"]["review_id"]
    ) / ".completed").is_file()


def test_manual_approval_is_explicit_atomic_and_sequential(tmp_path):
    service = StrategyValidationService(tmp_path)
    strategy = _strategy()
    first = service.review(strategy=strategy, evidence=_bundle())
    request = service.approvals.request(first["review"]["review_id"], "王研究员")
    assert request["approval_status"] == "PENDING"
    assert service.registry.strategy(strategy.strategy_id)["current_state"] == "DRAFT"
    approved = service.approvals.approve(
        request["promotion_id"], approved_by="李委员",
        reason="人工复核点时证据后同意进入研究阶段",
    )
    assert approved["approval_status"] == "APPROVED"
    assert service.registry.strategy(strategy.strategy_id)["current_state"] == "RESEARCH"
    with pytest.raises(InvalidStateTransitionError):
        service.approvals.approve(
            request["promotion_id"], approved_by="李委员",
            reason="不得对同一申请重复进行人工审批操作",
        )


def test_ai_and_system_can_neither_request_nor_approve(tmp_path):
    service = StrategyValidationService(tmp_path)
    result = service.review(strategy=_strategy(), evidence=_bundle())
    with pytest.raises(ValueError, match="人工身份"):
        service.approvals.request(result["review"]["review_id"], "AI")
    request = service.approvals.request(result["review"]["review_id"], "王研究员")
    with pytest.raises(ValueError, match="人工身份"):
        service.approvals.approve(
            request["promotion_id"], approved_by="system",
            reason="系统不允许代替人工进行策略晋级审批",
        )


def test_retirement_also_requires_evidence_linked_manual_approval(tmp_path):
    service = StrategyValidationService(tmp_path)
    result = service.review(strategy=_strategy(), evidence=_bundle())
    request = service.approvals.request_retirement(
        result["review"]["review_id"], "王研究员"
    )
    assert request["to_state"] == "RETIRED"
    assert service.registry.strategy(_strategy().strategy_id)["current_state"] == "DRAFT"
    service.approvals.approve(
        request["promotion_id"], approved_by="李委员",
        reason="人工确认策略版本终止研究并保留全部证据记录",
    )
    assert service.registry.strategy(_strategy().strategy_id)["current_state"] == "RETIRED"
    with pytest.raises(InvalidStateTransitionError):
        service.approvals.request_retirement(result["review"]["review_id"], "王研究员")


def test_paper_validation_never_recommends_live_trading(tmp_path):
    service = StrategyValidationService(tmp_path)
    strategy = _strategy()
    for expected in (
        "RESEARCH", "FACTOR_VALIDATED", "ROBUSTNESS_VALIDATED",
        "OUT_OF_SAMPLE", "PAPER_TRADING",
    ):
        result = service.review(strategy=strategy, evidence=_bundle(paper=False))
        assert result["review"]["recommended_state"] == expected
        request = service.approvals.request(result["review"]["review_id"], "王研究员")
        service.approvals.approve(
            request["promotion_id"], approved_by="李委员",
            reason=f"人工验证前序证据并同意进入{expected}阶段",
        )
    paper = service.review(strategy=strategy, evidence=_bundle(paper=True))
    assert paper["review"]["current_state"] == "PAPER_TRADING"
    assert paper["review"]["recommended_state"] is None
    assert paper["review"]["recommendation"] == "PAPER_EVIDENCE_COMPLETE_HOLD_NO_LIVE"
    assert paper["review"]["can_trade"] is False


def test_strategy_version_is_immutable(tmp_path):
    service = StrategyValidationService(tmp_path)
    service.register(_strategy())
    altered = StrategyVersionSpec(**{
        **_strategy().__dict__, "code_hash": "c" * 64,
    })
    with pytest.raises(ImmutableExperimentError):
        service.register(altered)


def test_database_rejects_execution_and_auto_promotion_capabilities(tmp_path):
    service = StrategyValidationService(tmp_path)
    result = service.review(strategy=_strategy(), evidence=_bundle())
    review_id = result["review"]["review_id"]
    with sqlite3.connect(service.registry.path) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"strategies", "strategy_versions", "validation_records", "promotion_history"} <= tables
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE strategy_review_reports SET can_auto_promote=1 WHERE review_id=?",
                (review_id,),
            )


def test_committee_report_has_six_sections_and_zero_authority(tmp_path):
    result = StrategyValidationService(tmp_path).review(
        strategy=_strategy(), evidence=_bundle()
    )
    report = result["report"]
    assert len(report["sections"]) == 6
    assert report["sections"]["06_committee_recommendation"]["human_approval_required"] is True
    assert report["ai_research_committee_interface"]["can_approve"] is False
    assert report["can_trade"] is False and report["can_auto_promote"] is False
    assert "api_key" not in json.dumps(report, ensure_ascii=False).lower()


def test_strategy_lab_api_requires_local_write_protection_and_human_decision(tmp_path):
    class FakeDaily:
        def close(self):
            pass

    class FakeWorkbench:
        def get(self):
            return {}

    class FakeCopilot:
        pass

    class FakeInvestmentOS:
        center = object()

        def close(self):
            pass

    service = StrategyValidationService(tmp_path)
    result = service.review(strategy=_strategy(), evidence=_bundle())
    app = create_app(
        tmp_path,
        daily_research_service=FakeDaily(),
        workbench_service=FakeWorkbench(),
        copilot_service=FakeCopilot(),
        investment_os_service=FakeInvestmentOS(),
        strategy_validation_service=service,
    )
    headers = {"X-Ashare-Client": "local-dashboard"}
    with TestClient(app) as client:
        dashboard = client.get("/api/v1/strategy-lab")
        assert dashboard.status_code == 200
        assert dashboard.json()["can_auto_promote"] is False
        blocked = client.post(
            f"/api/v1/strategy-lab/reviews/{result['review']['review_id']}/promotion-requests",
            json={"requested_by": "王研究员"},
        )
        assert blocked.status_code == 403
        requested = client.post(
            f"/api/v1/strategy-lab/reviews/{result['review']['review_id']}/promotion-requests",
            json={"requested_by": "王研究员"}, headers=headers,
        )
        assert requested.status_code == 200
        assert service.registry.strategy(_strategy().strategy_id)["current_state"] == "DRAFT"
        promotion_id = requested.json()["promotion_id"]
        approved = client.post(
            f"/api/v1/strategy-lab/promotions/{promotion_id}/approve",
            json={"actor": "李委员", "reason": "人工核对全部数据证据后同意进入研究阶段"},
            headers=headers,
        )
        assert approved.status_code == 200
        assert service.registry.strategy(_strategy().strategy_id)["current_state"] == "RESEARCH"


def test_reading_008b_and_008c_reports_checks_strategy_identity(tmp_path):
    class FakeExperimentRegistry:
        def factor_report(self, run_id):
            assert run_id == "factor-run-1"
            return _factor_report("POINT_IN_TIME")

        def robustness_report(self, run_id):
            assert run_id == "robust-run-1"
            return _robustness_report("POINT_IN_TIME")

    service = StrategyValidationService(tmp_path, experiment_registry=FakeExperimentRegistry())
    evidence = service.evidence_from_quant_lab(
        strategy=_strategy(), data_integrity=_bundle().data_integrity,
        factor_run_id="factor-run-1", robustness_run_id="robust-run-1",
    )
    assert evidence.factor_report["run_id"] == "factor-run-1"
    assert evidence.robustness_report["run_id"] == "robust-run-1"
    assert any(item.startswith("factor_report:") for item in evidence.evidence_ids)


def test_protected_research_portfolio_valuation_and_trading_hashes_are_unchanged():
    root = Path(__file__).resolve().parents[1]
    for relative, expected in PROTECTED_HASHES.items():
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest().upper()
        assert actual == expected
