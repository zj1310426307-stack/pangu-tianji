from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from ashare_agent.personal_os.review_loop import InvestmentReviewLoop
from ashare_agent.personal_os.service import PersonalInvestmentOSService
from ashare_agent.personal_os.store import PersonalOSStore, PersonalOSStoreError


class Workbench:
    """Provide authoritative valuation and paper-account facts without order methods."""

    def get(self) -> dict:
        return {
            "asset_valuation":{"service_version":"valuation-v1.0.0","valued_at":"2026-08-12T15:10:00+08:00","equity":100000.0},
            "positions":[{"symbol":"600519.SH","name":"贵州茅台","quantity":100,"unrealized_pnl_pct":0.03}],
            "performance":{},"symbol_attribution":[],"activity_summary":{},"risk_flags":[],
            "activity":{"trades":[]},"discipline":{},"nav":[],
        }


class Operating:
    """Expose existing Portfolio Risk and Exit evidence only."""

    def __init__(self, score: float = 25.0) -> None:
        self.score = score

    def dashboard(self) -> dict:
        return {
            "market":{},"recent_reports":[],
            "risk":{"assessment":{"portfolio_id":"portfolio-1","as_of":"2026-08-12T15:10:00+08:00","security_risks":[{"symbol":"600519.SH","risk_score":self.score,"risk_level":"LOW"}]},"exit_plan":{"signals":[]}},
        }


class Daily:
    """Expose a formal ResearchPipeline result without triggering a research run."""

    def __init__(self, score: float = 88.0, rank: int = 2) -> None:
        self.score,self.rank = score,rank

    def dashboard(self) -> dict:
        return {"latest_research":{"mode":"formal_close_plan","run_id":f"run-{self.score}","research_date":"2026-08-12","observed_at":"2026-08-12T15:10:00+08:00","candidates":[{"symbol":"600519.SH","rank":self.rank,"score":self.score,"in_candidates":True}]}}


class Empty:
    """Satisfy unrelated Personal OS dashboard dependencies."""

    def dashboard(self) -> dict:
        return {"evidence":{},"counts":{},"reports":[],"recent_reports":[]}


def service(tmp_path: Path, *, now: datetime | None = None) -> PersonalInvestmentOSService:
    selected = now or datetime(2026,8,12,16,0,tzinfo=ZoneInfo("Asia/Shanghai"))
    return PersonalInvestmentOSService(
        tmp_path,workbench_service=Workbench(),investment_os_service=Operating(),
        quant_ai_service=Empty(),strategy_validation_service=Empty(),daily_research_service=Daily(),
        store=PersonalOSStore(tmp_path/"agent.db"),now_provider=lambda:selected,
    )


def create_decision(os: PersonalInvestmentOSService, cycle: str = "T5") -> dict:
    return os.create_journal({
        "idempotency_key":"journal-1","entry_type":"DECISION","trade_date":"2026-08-07",
        "symbol":"600519.SH","title":"继续观察质量因子","reason":"质量因子仍有证据",
        "user_text":"我准备继续观察，但不自动交易","expected_horizon":"20日",
        "expected_condition":"正式研究仍在前20","invalid_condition":"跌出前20或风险恶化",
        "risk_notes":"估值与集中度","review_cycle":cycle,"source":"USER","evidence_ids":[],
    })


def test_four_journal_types_and_user_ai_content_are_separate(tmp_path: Path) -> None:
    os = service(tmp_path)
    rows = []
    for index,entry_type in enumerate(("OBSERVE","DECISION","REVIEW","LESSON")):
        rows.append(os.create_journal({
            "idempotency_key":f"j-{index}","entry_type":entry_type,"trade_date":"2026-08-12",
            "title":entry_type,"reason":"用户自己的原话","user_text":"用户自己的原话",
            "ai_summary":"AI只做摘要" if entry_type == "OBSERVE" else None,
            "source":"AI_SAVED" if entry_type == "OBSERVE" else "USER","evidence_ids":["E-TEST-001"],
        }))
    assert {row["entry_type"] for row in rows} == {"OBSERVE","DECISION","REVIEW","LESSON"}
    assert rows[0]["user_text"] != rows[0]["ai_summary"]
    assert rows[0]["can_trade"] is False and rows[0]["can_create_orders"] is False


def test_t5_t20_custom_review_dates_and_due_dedup(tmp_path: Path) -> None:
    loop = InvestmentReviewLoop(PersonalOSStore(tmp_path/"dates.db"))
    assert loop.due_date("2026-08-01","T5") == "2026-08-06"
    assert loop.due_date("2026-08-01","T20") == "2026-08-21"
    assert loop.due_date("2026-08-01","CUSTOM","2026-09-01") == "2026-09-01"
    os = service(tmp_path)
    journal = create_decision(os)
    first = os.sync_review_reminders()
    second = os.sync_review_reminders()
    assert journal["review_due_at"] == "2026-08-12"
    assert {item["reminder_type"] for item in first["items"]} >= {"REVIEW_DUE"}
    assert "DAILY_REVIEW" not in {item["reminder_type"] for item in first["items"]}
    assert second["created_count"] == 0


def test_daily_review_is_once_per_day_and_reminder_statuses_are_bounded(tmp_path: Path) -> None:
    os = service(tmp_path)
    os.create_journal({
        "idempotency_key":"today-1","entry_type":"OBSERVE","trade_date":"2026-08-12",
        "title":"今日观察","reason":"用户原话","user_text":"用户原话",
        "review_cycle":"NONE","source":"USER","evidence_ids":[],
    })
    first = os.sync_review_reminders()
    second = os.sync_review_reminders()
    daily = [item for item in first["items"] if item["reminder_type"] == "DAILY_REVIEW"]
    assert len(daily) == 1
    assert second["created_count"] == 0
    dismissed = os.update_reminder(daily[0]["reminder_id"], "DISMISSED")
    assert dismissed["status"] == "DISMISSED"
    assert dismissed["can_trade"] is False


def test_review_due_reminder_is_done_after_confirmed_review(tmp_path: Path) -> None:
    os = service(tmp_path)
    journal = create_decision(os)
    os.sync_review_reminders()
    draft = os.review_draft(journal["journal_id"])
    os.confirm_review(journal["journal_id"], {
        "idempotency_key":"review-done","reviewed_at":"2026-08-12T16:00:00+08:00",
        "facts_changed":[],"thesis_status":"INSUFFICIENT_EVIDENCE","risk_status":"UNKNOWN",
        "result_summary":"证据不足，继续观察","mistakes":[],"good_decisions":[],
        "evidence_refs":draft["evidence_refs"],"user_confirmed":True,
    })
    due = [item for item in os.reminders()["items"] if item["reminder_type"] == "REVIEW_DUE"]
    assert due and due[0]["status"] == "DONE"


def test_review_draft_is_not_saved_and_confirmed_lesson_requires_user(tmp_path: Path) -> None:
    os = service(tmp_path)
    journal = create_decision(os)
    before = os.store.counts()
    draft = os.review_draft(journal["journal_id"])
    assert os.store.counts() == before
    assert draft["saved"] is False and draft["user_confirmation_required"] is True
    assert draft["facts_changed"]["current_score"] == 88.0
    with pytest.raises(PersonalOSStoreError):
        os.store.save_review({
            "journal_id":journal["journal_id"],"idempotency_key":"no-confirm","reviewed_at":"2026-08-12T16:00:00+08:00",
            "thesis_status":"STILL_VALID","result_summary":"未确认","user_confirmed":False,
        })
    saved = os.confirm_review(journal["journal_id"],{
        "idempotency_key":"review-1","reviewed_at":"2026-08-12T16:00:00+08:00",
        "facts_changed":["评分仍为88"],"thesis_status":"STILL_VALID","risk_status":"UNCHANGED",
        "result_summary":"原逻辑仍有证据支持","mistakes":[],"good_decisions":["记录了失效条件"],
        "lesson_candidate":"以后继续显式记录失效条件","evidence_refs":draft["evidence_refs"],
        "user_confirmed":True,
    })
    assert saved["review"]["user_confirmed"] is True
    assert saved["lesson"]["entry_type"] == "LESSON"
    assert saved["can_trade"] is False


def test_review_rejects_stale_or_mismatched_evidence(tmp_path: Path) -> None:
    os = service(tmp_path)
    journal = create_decision(os)
    with pytest.raises(ValueError, match="Evidence"):
        os.confirm_review(journal["journal_id"],{
            "idempotency_key":"review-bad","reviewed_at":"2026-08-12T16:00:00+08:00",
            "facts_changed":[],"thesis_status":"INSUFFICIENT_EVIDENCE","risk_status":"UNKNOWN",
            "result_summary":"证据不匹配","mistakes":[],"good_decisions":[],
            "evidence_refs":[{"evidence_id":"fake","payload_hash":"bad"}],"user_confirmed":True,
        })


def test_risk_and_research_change_use_prior_authority_snapshot_only(tmp_path: Path) -> None:
    os = service(tmp_path)
    create_decision(os)
    assert os.sync_review_reminders()["created_count"] >= 1
    os.investment_os.score = 75.0
    os.daily_research.score = 60.0
    os.daily_research.rank = 30
    changed = os.sync_review_reminders()
    types = {item["reminder_type"] for item in changed["items"]}
    assert {"RISK_CHANGED","RESEARCH_CHANGED"} <= types
    assert all(item["can_trade"] is False for item in changed["items"])


def test_database_migration_retains_legacy_journal_and_zero_authority(tmp_path: Path) -> None:
    db = tmp_path/"legacy.db"
    store = PersonalOSStore(db)
    with sqlite3.connect(db) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"investment_reviews","investment_reminders","investment_review_snapshots","personal_os_schema_migrations"} <= tables
        connection.execute(
            """INSERT INTO investment_reminders(
                reminder_id,dedup_key,reminder_type,trade_date,symbol,title,message,status,
                source_type,source_id,evidence_refs_json,created_time
            ) VALUES('r1','|DAILY_REVIEW|2026-08-12','DAILY_REVIEW','2026-08-12','','t','m','OPEN','test','s','[]','now')"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE investment_reminders SET can_trade=1 WHERE reminder_id='r1'")


def test_frontend_review_loop_uses_generated_client_and_six_primary_tabs() -> None:
    root = Path(__file__).parents[1]
    app_js = (root/"web"/"app.js").read_text(encoding="utf-8")
    client = (root/"web"/"generated"/"client.js").read_text(encoding="utf-8")
    html = (root/"web"/"index.html").read_text(encoding="utf-8")
    css = (root/"web"/"styles.css").read_text(encoding="utf-8")
    for operation in (
        "get_investment_review_loop","create_investment_review_journal",
        "get_investment_review_draft","confirm_investment_review",
        "sync_investment_review_reminders","update_investment_review_reminder_status",
    ):
        assert f'"{operation}"' in client
        assert f'apiRequest("{operation}"' in app_js
    assert "fetch(" not in app_js
    assert "/api/" not in app_js
    assert html.count('class="workspace-tab') == 6
    assert "@media (max-width:430px)" in css
    assert "grid-template-columns:1fr" in css
