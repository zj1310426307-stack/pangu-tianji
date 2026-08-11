from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from ashare_agent.personal_os.investment_event import InvestmentEventService
from ashare_agent.personal_os.personal_score import PersonalInvestmentScore, SCORE_WEIGHTS
from ashare_agent.personal_os.service import PersonalInvestmentOSService
from ashare_agent.personal_os.store import PersonalOSStore, PersonalOSStoreError


class FakeWorkbench:
    def get(self) -> dict:
        return {
            "asset_valuation": {
                "service_version": "valuation-v1.0.0",
                "valued_at": "2026-08-10T07:00:00+00:00",
                "cash": 80_000.0,
                "market_value": 20_000.0,
                "equity": 100_000.0,
                "pnl": 0.0,
                "drawdown": -0.01,
                "max_drawdown": -0.02,
                "exposure_ratio": 0.20,
            },
            "positions": [{"symbol": "600519.SH", "market_value": 20_000.0}],
            "performance": {"win_rate": 0.5},
            "symbol_attribution": [{"symbol": "600519.SH", "realized_pnl": 100.0}],
            "activity_summary": {"trade_count": 2},
            "risk_flags": [{"level": "warning", "code": "TEST", "message": "测试风险"}],
            "discipline": {"score": 80, "grade": "good"},
            "nav": [
                {"trade_date": "2026-08-01", "equity": 98_000.0},
                {"trade_date": "2026-08-10", "equity": 100_000.0},
            ],
            "activity": {
                "trades": [
                    {
                        "trade_id": "fill-001", "client_order_id": "paper-001",
                        "trade_date": "2026-08-08", "symbol": "600519.SH",
                        "side": "BUY", "quantity": 100, "price": 200.0, "fee": 5.0,
                    },
                    {
                        "trade_id": "fill-002", "client_order_id": "paper-002",
                        "trade_date": "2026-08-10", "symbol": "600519.SH",
                        "side": "SELL", "quantity": 100, "price": 201.0,
                        "fee": 5.0, "realized_pnl": 90.0,
                    },
                ],
            },
        }


class FakeOperatingOS:
    def dashboard(self) -> dict:
        return {
            "market": {"availability": "unavailable", "data_gaps": ["market_regime_missing"]},
            "risk": {"assessment": {"risk_level": "MEDIUM"}},
            "recent_reports": [{"report_id": "daily-1", "report_type": "closing_review", "status": "published"}],
        }


class FakeQuantAI:
    def dashboard(self) -> dict:
        return {
            "evidence": {"evidence_count": 4, "strategy_version": "cross-sectional-v2.0.0"},
            "latest_report": {"report_id": "quant-1", "status": "published"},
            "reports": [{"report_id": "quant-1", "status": "published"}],
        }


class FakeValidation:
    def dashboard(self) -> dict:
        return {"counts": {"strategies": 1, "reviews": 2, "pending_approvals": 0}, "can_auto_promote": False}


def make_service(tmp_path: Path) -> PersonalInvestmentOSService:
    return PersonalInvestmentOSService(
        tmp_path,
        workbench_service=FakeWorkbench(),
        investment_os_service=FakeOperatingOS(),
        quant_ai_service=FakeQuantAI(),
        strategy_validation_service=FakeValidation(),
        production_profile={
            "capital": 100_000.0, "risk_level": "balanced",
            "investment_horizon": "medium", "investment_style": "balanced",
            "max_drawdown_tolerance": 0.10, "profile_id": "production-profile",
        },
        store=PersonalOSStore(tmp_path / "personal.db"),
        now_provider=lambda: datetime(2026, 8, 10, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def test_store_creates_required_tables_and_database_safety_constraints(tmp_path: Path) -> None:
    store = PersonalOSStore(tmp_path / "personal.db")
    with sqlite3.connect(store.db_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "investor_profile", "investment_events", "investment_journal",
            "knowledge_base", "committee_reports", "personal_score",
        } <= tables
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO personal_score(score_id,as_of_date,total_score,coverage,dimensions_json,evidence_ids_json,score_hash,created_time,can_trade) VALUES('x','2026-08-10',0,0,'[]','[]','h','now',1)"
            )


def test_dashboard_is_read_only_and_uses_canonical_valuation(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    before = service.store.counts()
    payload = service.dashboard()
    assert service.store.counts() == before
    assert payload["asset_valuation"]["service_version"] == "valuation-v1.0.0"
    assert payload["asset_valuation"]["equity"] == 100_000.0
    assert payload["investor_profile"]["persisted"] is False
    assert payload["personal_score"]["total_score"] == 57.5
    assert payload["can_trade"] is False
    assert payload["can_create_orders"] is False
    assert payload["can_modify_strategy"] is False


def test_paper_trade_sync_is_idempotent_and_manual_buy_is_forbidden(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    first = service.sync_events()
    second = service.sync_events()
    assert first["created_count"] == 2
    assert second["created_count"] == 0
    assert second["replayed_count"] == 2
    assert {item["event_type"] for item in service.store.events()} == {"BUY", "SELL"}
    with pytest.raises(ValueError):
        service.create_event({
            "idempotency_key": "manual-buy", "event_type": "BUY",
            "trade_date": "2026-08-10", "reason": "not allowed",
        })


def test_digital_twin_does_not_change_production_profile(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    original = dict(service.production_profile)
    saved = service.update_profile({
        "capital": 120_000, "risk_level": "conservative", "holding_period": "long",
        "investment_style": "value", "max_drawdown": 0.08,
        "behavior": ["追涨", "过早卖出"], "source_profile_id": "production-profile",
    })
    assert saved["revision"] == 1
    assert saved["behavior"] == ["追涨", "过早卖出"]
    assert service.production_profile == original
    assert saved["can_modify_portfolio"] is False
    assert saved["can_modify_risk"] is False


def test_journal_and_knowledge_are_versioned_and_soft_archived(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    journal = service.create_journal({
        "idempotency_key": "journal-001", "entry_type": "review",
        "trade_date": "2026-08-10", "title": "复盘", "content": "只记录证据",
        "evidence_ids": [],
    })
    updated = service.update_journal(journal["journal_id"], {
        "expected_version": 1, "lesson": "缺证据不下结论",
    })
    archived = service.archive_journal(journal["journal_id"], 2)
    assert updated["version"] == 2
    assert archived["status"] == "archived"
    knowledge = service.create_knowledge({
        "idempotency_key": "knowledge-001", "category": "personal_lesson",
        "subject": "纪律", "title": "证据先行", "content": "没有证据时明确缺口",
        "evidence_ids": [], "confidence": 0.8,
    })
    assert service.archive_knowledge(knowledge["knowledge_id"], 1)["status"] == "archived"


def test_personal_score_has_fixed_weights_and_never_scores_return() -> None:
    score = PersonalInvestmentScore().calculate(
        as_of_date="2026-08-10",
        workbench={"discipline": {"score": 100}, "risk_flags": []},
        quant_ai={"evidence": {"evidence_count": 0}, "latest_report": None},
        counts={"event_count": 0, "journal_count": 0},
        reports=[], journals=[],
    )
    assert score["weights"] == SCORE_WEIGHTS
    assert sum(SCORE_WEIGHTS.values()) == 100
    assert score["coverage"] == 0.25
    assert score["total_score"] == 25.0
    assert score["missing_weights_are_not_redistributed"] is True
    assert "return" not in json.dumps(score).lower()


def test_personal_reports_require_evidence_are_idempotent_and_non_executable(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.sync_events()
    service.refresh_score()
    weekly = service.generate_weekly()
    replay = service.generate_weekly()
    monthly = service.generate_monthly()
    coach = service.generate_coach()
    assert weekly["created"] is True
    assert replay["created"] is False
    assert weekly["report_id"] == replay["report_id"]
    assert monthly["report_content"]["content"]["performance"]["excess_return"] is None
    assert monthly["status"] == "degraded"
    assert coach["can_trade"] == 0 or coach["can_trade"] is False
    assert all(item["report_content"]["evidence_ids"] for item in service.store.reports())


def test_personal_web_uses_generated_client_and_no_frontend_asset_formula() -> None:
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "web" / "app.js").read_text(encoding="utf-8")
    index = (root / "web" / "index.html").read_text(encoding="utf-8")
    generated = (root / "web" / "generated" / "client.js").read_text(encoding="utf-8")
    assert 'id="workspace-personalos"' in index
    assert 'id="personalProfileCapital" type="number" min="1000" step="1000"' in index
    assert 'data-workspace="personalos"' in app_js
    assert 'apiRequest("get_personal_investment_os"' in app_js
    assert 'fetch("/api/' not in app_js
    assert '"get_personal_investment_os"' in generated
    assert '"create_personal_monthly_review"' in generated
    personal_section = app_js[app_js.index("function renderPersonalInvestmentOS"):app_js.index("async function refreshAll")]
    assert "cash +" not in personal_section
    assert "market_value +" not in personal_section
    assert "equity -" not in personal_section
