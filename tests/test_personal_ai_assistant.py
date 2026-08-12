from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ashare_agent.api.app import create_app
from ashare_agent.services.personal_ai_assistant_service import (
    READ_ONLY_EVIDENCE_WHITELIST,
    PersonalAIAssistantService,
)


TZ = ZoneInfo("Asia/Shanghai")


class FakeDashboard:
    def __init__(self, formal: bool = True) -> None:
        self.formal = formal

    def overview(self):
        return {
            "research": {
                "run_id": "formal-run-20260812" if self.formal else "preview-run-20260812",
                "source_mode": "formal_close_plan" if self.formal else "intraday_preview",
            },
            "risk": {"level": "MEDIUM", "reasons": ["行业暴露证据需持续观察"]},
            "data_gaps": ["缺少正式Market Regime证据"],
        }


class FakeCopilot:
    def __init__(self) -> None:
        self.generated = []

    def evidence(self, *, run_id, report_type, symbol=None):
        return {
            "run_id": run_id,
            "strategy_version": "cross-sectional-v2.0.0",
            "factor_version": "factor-v2",
            "evidence_hash": "e" * 64,
            "data_gaps": [],
            "evidence_items": [{
                "evidence_id": "E-DC-MANIFEST",
                "source": "data_center",
                "observed_at": "2026-08-12T15:10:00+08:00",
                "payload": {"secret": "must-not-leak"},
            }],
        }

    def generate(self, **kwargs):
        self.generated.append(kwargs)
        return {
            "report_id": "report-1",
            "run_id": kwargs["run_id"],
            "evidence_ids": ["E-DC-MANIFEST"],
            "evidence_hash": "e" * 64,
            "content": {
                "summary": "当前证据支持继续观察研究结果。",
                "findings": [{"title": "排名", "detail": "评分来自正式run。"}],
                "risks": [{"level": "warning", "message": "数据存在时点缺口。"}],
            },
        }


class FakePersonalOS:
    def journals(self, limit=200):
        return {"items": [{
            "journal_id":"journal-1","entry_type":"DECISION","trade_date":"2026-08-10",
            "symbol":"600519.SH","user_text":"我自己的持有理由","ai_summary":"AI摘要",
            "review_status":"NOT_DUE","source":"AI_SAVED","status":"active",
        }]}

    def dashboard(self):
        return {
            "investor_profile": {"risk_level": "medium", "max_drawdown_tolerance": 0.15},
            "coach": {
                "status": "degraded",
                "summary": "基于日志和事件复盘，不以收益倒推决策质量。",
                "patterns": [{"statement": "本期记录不足", "evidence_ids": ["E-POS-1"]}],
                "improvements": ["记录投资理由"],
                "data_gaps": ["缺少结果日志"],
                "evidence": [{
                    "evidence_id": "E-POS-1",
                    "source_type": "personal_os_journals",
                    "payload_hash": "p" * 64,
                    "observed_at": "2026-08-12T15:30:00+08:00",
                }],
            },
            "reviews": [{"review_id":"review-1","user_confirmed":True}],
            "journals": [
                {"journal_id":"lesson-1","entry_type":"LESSON","status":"active"},
            ],
        }


def make_service(*, formal=True):
    copilot = FakeCopilot()
    service = PersonalAIAssistantService(
        investment_dashboard_service=FakeDashboard(formal=formal),
        copilot_service=copilot,
        personal_os_service=FakePersonalOS(),
        now_provider=lambda: datetime(2026, 8, 12, 18, 0, tzinfo=TZ),
    )
    return service, copilot


def test_five_fixed_intents_and_uniform_contract():
    service, copilot = make_service()
    for intent in ("daily_attention", "portfolio_analysis", "stock_reason", "portfolio_fit"):
        payload = service.query(
            intent=intent,
            symbol="600519.SH" if intent in {"stock_reason", "portfolio_fit"} else None,
        )
        assert set(("summary", "key_points", "risks", "uncertainties", "evidence_refs", "suggested_next_action")) <= payload.keys()
        assert payload["can_trade"] is False
        assert payload["can_create_orders"] is False
        assert payload["can_launch_experiment"] is False
        assert payload["can_auto_remediate"] is False
        assert payload["read_only_whitelist"] == list(READ_ONLY_EVIDENCE_WHITELIST)
        assert payload["evidence_refs"][0]["evidence_id"] == "E-DC-MANIFEST"
        assert "payload" not in payload["evidence_refs"][0]
    behavior = service.query(intent="behavior_review")
    assert behavior["model_used"] is False
    assert behavior["summary"] == "基于最近2条已确认记录进行复核。"
    assert "当前样本不足，无法形成稳定结论" in behavior["uncertainties"]
    assert behavior["evidence_refs"][0]["source"] == "personal_os_journals"
    assert all(call["trigger"] == "user_action" for call in copilot.generated)


def test_stock_context_separates_user_reason_from_ai_summary_and_does_not_save():
    service, _ = make_service()
    payload = service.query(intent="stock_reason", symbol="600519.SH")
    item = payload["journal_context"]["items"][0]
    assert item["user_text"] == "我自己的持有理由"
    assert item["ai_summary"] == "AI摘要"
    assert item["user_text"] != item["ai_summary"]
    # Querying may call the explanatory model, but no journal create/update method exists on this fake.
    assert payload["can_trade"] is False and payload["can_create_orders"] is False


def test_preview_never_calls_model_and_waits_for_formal_run():
    service, copilot = make_service(formal=False)
    payload = service.query(intent="daily_attention")
    assert payload["status"] == "unavailable"
    assert payload["suggested_next_action"] == "等待正式收盘run"
    assert payload["evidence_refs"] == []
    assert copilot.generated == []


def test_output_filters_transaction_directives():
    service, copilot = make_service()
    original = copilot.generate

    def unsafe(**kwargs):
        report = original(**kwargs)
        report["content"]["summary"] = "建议立即买入这只股票"
        return report

    copilot.generate = unsafe
    payload = service.query(intent="stock_reason", symbol="600519.SH")
    assert "买入" not in payload["summary"]
    assert any("交易指令过滤" in item for item in payload["uncertainties"])


class StubClose:
    def close(self):
        pass


class StubRunService:
    repository = object()

    def shutdown(self):
        pass


class StubModel:
    def status(self):
        return {"enabled": False, "model": "disabled"}


class StubDaily(StubClose):
    def dashboard(self):
        return {}


class StubWorkbench:
    def get(self):
        return {"asset_valuation": {}, "positions": []}


class StubInvestmentOS(StubClose):
    def dashboard(self):
        return {"portfolio": {}, "risk": {}, "market": {}}


class StubJobs:
    pass


class StubObservability:
    jobs = StubJobs()

    def begin_request(self, **kwargs):
        return None


class StubEngineering:
    observability = StubObservability()

    def dashboard(self):
        return {}


class StubQuantAI:
    def dashboard(self):
        return {}


class StubValidation:
    def dashboard(self):
        return {"counts": {}}


class StubData:
    def dashboard(self):
        return {}


class StubEvolution:
    def dashboard(self):
        return {}


def test_api_has_single_assistant_query_and_local_write_protection(tmp_path: Path):
    service, _ = make_service(formal=False)
    app = create_app(
        tmp_path,
        run_service=StubRunService(),
        model_service=StubModel(),
        daily_research_service=StubDaily(),
        workbench_service=StubWorkbench(),
        copilot_service=FakeCopilot(),
        investment_os_service=StubInvestmentOS(),
        investment_job_manager=object(),
        strategy_validation_service=StubValidation(),
        quant_ai_service=StubQuantAI(),
        personal_os_service=FakePersonalOS(),
        data_intelligence_service=StubData(),
        strategy_evolution_service=StubEvolution(),
        engineering_service=StubEngineering(),
        observability_service=StubObservability(),
        investment_dashboard_service=FakeDashboard(formal=False),
        personal_ai_assistant_service=service,
    )
    assistant_paths = [path for path in app.openapi()["paths"] if path.startswith("/api/v1/assistant")]
    assert assistant_paths == ["/api/v1/assistant/query"]
    with TestClient(app) as client:
        forbidden = client.post("/api/v1/assistant/query", json={"intent": "daily_attention"})
        assert forbidden.status_code == 403
        response = client.post(
            "/api/v1/assistant/query",
            headers={"X-Ashare-Client": "local-dashboard"},
            json={"intent": "daily_attention"},
        )
        assert response.status_code == 200
        assert response.json()["suggested_next_action"] == "等待正式收盘run"


def test_frontend_has_exactly_six_primary_tabs_and_generated_api_only():
    root = Path(__file__).parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    app_js = (root / "web" / "app.js").read_text(encoding="utf-8")
    client_js = (root / "web" / "generated" / "client.js").read_text(encoding="utf-8")
    primary = re.findall(r'class="workspace-tab[^\"]*"[^>]+data-workspace="([^"]+)"', html)
    assert primary == ["dashboard", "assistant", "market", "etf", "review", "settings"]
    assert "installPersonalWorkspaceTab();" not in app_js
    assert "installOperatingWorkspaceTab();" not in app_js
    assert 'apiRequest("query_personal_ai_assistant"' in app_js
    assert "/api/v1/assistant/query" not in app_js
    assert '"query_personal_ai_assistant"' in client_js
    assert '"path": "/api/v1/assistant/query"' in client_js


def test_protected_baseline_files_unchanged():
    root = Path(__file__).parents[1]
    baselines = {
        "src/ashare_agent/research_pipeline.py": "1FD79ED464E75EE1D6F1477AB7267178FD00D9329AA8EFC311C5B57058678577",
        "src/ashare_agent/factor_model.py": "4AE32405EA360C5C33F23A1D2A4EDC011A0F3A9A09182624084FF1FCCDFF0295",
        "src/ashare_agent/data_center.py": "0294A451837019C0FA6678C359FB919884A066D674662F62F86A34204B99E6BD",
        "src/ashare_agent/data_center_models.py": "153699C313892C4610779316E138061ECB2BE770B2008A827E2D785B41BAEC62",
        "src/ashare_agent/services/portfolio_service.py": "41F3037DB2E0157B049B7496F30C36748A434B4108A5CC64C41872E71A892280",
        "src/ashare_agent/services/portfolio_risk_engine.py": "ED6CC9125795946954D6909033254A5D0A5B2E77D7D277FA04B2ED5D984336D2",
        "src/ashare_agent/services/valuation_service.py": "5331F1E806A4EDFF6DD46A38311ACCE6B791349ADEA300C197760D66D29153B4",
        "src/ashare_agent/paper_portfolio.py": "69FD3B0E81E12CAF0BB21A3014924A58CD536561BBC1C4604142FAE195286DCD",
        "src/ashare_agent/execution_rules.py": "A9A791C9919639923168B6AA7C75BF7D36A029154952005D05FC7E45B814BB04",
        "src/ashare_agent/broker.py": "3E8C2B1BD4E222A3CA2BB7D0EA0CC40E4C16A8EBC6E3C40EB2C3CD46E6487045",
        "src/ashare_agent/ai_copilot/evidence_reader.py": "1A596FAC2DF8981C75EC2700B32CDAEB305D14AC775D119974C97062F3E05D12",
        "src/ashare_agent/ai_copilot/copilot_service.py": "4329F751FE01D5A7E8A5FAA4F71FABF733FA4BDB77B33E68A91B5941A41993B2",
    }
    mismatches = {
        path: hashlib.sha256((root / path).read_bytes()).hexdigest().upper()
        for path, expected in baselines.items()
        if hashlib.sha256((root / path).read_bytes()).hexdigest().upper() != expected
    }
    assert mismatches == {}
