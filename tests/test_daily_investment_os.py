from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from ashare_agent.core.contracts import NotificationLevel
from ashare_agent.scheduler.daily_scheduler import DailyScheduler
from ashare_agent.scheduler.job_manager import JobManager
from ashare_agent.scheduler.task_registry import TaskRegistry
from ashare_agent.services.daily_investment_os_service import DailyInvestmentOSService
from ashare_agent.services.investment_report_center import InvestmentReportCenter


SHANGHAI = ZoneInfo("Asia/Shanghai")


class ReadOnlyDailyServiceFake:
    """Expose dashboard evidence and fail if an operating report touches trading flows."""

    def __init__(self) -> None:
        self.dashboard_calls = 0
        self.forbidden_calls: list[str] = []

    def dashboard(self) -> dict:
        """Return a small saved-research view without mutating an account."""
        self.dashboard_calls += 1
        return {
            "latest_research": {
                "research_date": "2026-08-07",
                "generated_at": "2026-08-07T15:10:00+08:00",
                "candidates": [{
                    "rank": 1,
                    "symbol": "600000.SH",
                    "name": "浦发银行",
                    "score": 85.0,
                    "industry": "银行",
                    "last_price": 10.0,
                    "risk_tags": [],
                }],
            },
            "latest_preview": None,
            "plan_state": "ready",
            "preview_state": "none",
            "quote_feed": "polling_snapshot",
            "portfolio_risk_center": {
                "target_portfolio": {
                    "portfolio_id": "portfolio-test",
                    "positions": [],
                },
                "risk_assessment": {"risk_flags": []},
                "exit_plan": {"signals": []},
            },
        }

    def monitor(self) -> None:
        """Represent the forbidden matcher/stop-loss workflow."""
        self.forbidden_calls.append("monitor")
        raise AssertionError("Daily Investment OS must not call monitor")

    def execute(self) -> None:
        """Represent the forbidden automatic paper rebalance workflow."""
        self.forbidden_calls.append("execute")
        raise AssertionError("Daily Investment OS must not call execute")

    def submit_broker_order(self, *_args, **_kwargs) -> None:
        """Represent every forbidden local paper-order creation path."""
        self.forbidden_calls.append("submit_broker_order")
        raise AssertionError("Daily Investment OS must not create orders")


class WorkbenchFake:
    """Return canonical ValuationService-owned account and risk evidence."""

    def __init__(self, *, danger: bool = False) -> None:
        self.calls = 0
        self.danger = danger

    def get(self) -> dict:
        """Return backend-valued account data without frontend arithmetic."""
        self.calls += 1
        flags = []
        if self.danger:
            flags = [{
                "level": "danger",
                "code": "PNL_RECONCILIATION_GAP",
                "message": "账户盈亏无法对平",
            }]
        return {
            "asset_valuation": {
                "service_version": "valuation-v1.0.0",
                "valued_at": "2026-08-10T08:44:00+08:00",
                "cash": 60_000.0,
                "market_value": 40_000.0,
                "equity": 100_000.0,
                "pnl": 0.0,
                "drawdown": 0.0,
                "max_drawdown": 0.0,
                "exposure_ratio": 0.4,
            },
            "positions": [],
            "activity_summary": {"trade_count": 0, "total_fees": 0.0},
            "performance": {
                "net_pnl": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "win_rate": None,
                "turnover_ratio": 0.0,
            },
            "valuation": {"source": "valuation_service", "stale": False},
            "risk_flags": flags,
        }


class CopilotFake:
    """Provide bounded evidence and optional AI output without database handles."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.evidence_calls = 0
        self.generate_calls = 0

    def evidence(self, **_kwargs) -> dict:
        """Return one citation-ready immutable evidence bundle."""
        self.evidence_calls += 1
        return {
            "run_id": "2026-08-07_cross-sectional-v2.0.0_test_00000000",
            "data_gaps": [],
            "evidence_items": [{
                "evidence_id": "E-DC-MANIFEST",
                "source": "data_center",
                "observed_at": "2026-08-07T15:10:00+08:00",
                "payload": {"research_date": "2026-08-07"},
            }],
            "can_trade": False,
            "used_for_execution": False,
        }

    def generate(self, **_kwargs) -> dict:
        """Return one harmless report or emulate an unavailable model."""
        self.generate_calls += 1
        if self.fail:
            raise RuntimeError("model unavailable")
        return {
            "report_id": "ai-report-test",
            "content": {"summary": "只解释已保存证据"},
            "evaluation": {"grounded": True},
            "can_trade": False,
            "used_for_execution": False,
        }


def make_service(
    tmp_path: Path,
    *,
    copilot: CopilotFake | None = None,
    danger: bool = False,
) -> tuple[DailyInvestmentOSService, ReadOnlyDailyServiceFake, InvestmentReportCenter]:
    """Build an isolated operating service with no broker or network dependency."""
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "settings.yaml").write_text(
        "daily_investment_os:\n  enabled: true\n  email_enabled: false\n",
        encoding="utf-8",
    )
    center = InvestmentReportCenter(tmp_path / "output" / "daily-investment-os.db")
    daily = ReadOnlyDailyServiceFake()
    service = DailyInvestmentOSService(
        tmp_path,
        daily_service=daily,
        workbench_service=WorkbenchFake(danger=danger),
        copilot_service=copilot or CopilotFake(),
        report_center=center,
        registry=TaskRegistry(),
    )
    return service, daily, center


def test_task_registry_defines_only_bounded_read_only_operating_jobs() -> None:
    """Keep the operating cadence low-frequency and incapable of order creation."""
    registry = TaskRegistry()
    jobs = {item["job_name"]: item for item in registry.definitions()}

    assert set(jobs) == {
        "morning_report",
        "intraday_monitor",
        "closing_review",
        "weekly_report",
    }
    assert jobs["morning_report"]["time_of_day"] == "08:45"
    assert jobs["intraday_monitor"]["interval_minutes"] == 60
    assert jobs["closing_review"]["time_of_day"] == "15:30"
    assert jobs["weekly_report"]["weekday"] == 4
    assert jobs["weekly_report"]["time_of_day"] == "16:00"
    assert all(item["can_create_orders"] is False for item in jobs.values())

    with pytest.raises(ValueError, match="30或60"):
        TaskRegistry({"intraday_interval_minutes": 5})


def test_task_registry_returns_only_the_current_slot_and_never_backfills() -> None:
    """Reject missed slots instead of replaying stale investment reports later."""
    registry = TaskRegistry({"intraday_interval_minutes": 60})

    morning = registry.due_slot(
        "morning_report", datetime(2026, 8, 10, 8, 50, tzinfo=SHANGHAI)
    )
    assert morning == datetime(2026, 8, 10, 8, 45, tzinfo=SHANGHAI)
    assert registry.due_slot(
        "morning_report", datetime(2026, 8, 10, 9, 6, tzinfo=SHANGHAI)
    ) is None

    intraday = registry.due_slot(
        "intraday_monitor", datetime(2026, 8, 10, 10, 50, tzinfo=SHANGHAI)
    )
    assert intraday == datetime(2026, 8, 10, 10, 45, tzinfo=SHANGHAI)
    assert registry.due_slot(
        "intraday_monitor", datetime(2026, 8, 10, 10, 10, tzinfo=SHANGHAI)
    ) is None
    assert registry.due_slot(
        "intraday_monitor", datetime(2026, 8, 10, 11, 50, tzinfo=SHANGHAI)
    ) is None
    assert registry.due_slot(
        "intraday_monitor", datetime(2026, 8, 10, 12, 50, tzinfo=SHANGHAI)
    ) is None

    assert registry.due_slot(
        "weekly_report", datetime(2026, 8, 14, 16, 5, tzinfo=SHANGHAI)
    ) == datetime(2026, 8, 14, 16, 0, tzinfo=SHANGHAI)
    assert registry.due_slot(
        "weekly_report", datetime(2026, 8, 13, 16, 5, tzinfo=SHANGHAI)
    ) is None


def test_report_center_persists_cited_reports_tasks_and_notifications(tmp_path: Path) -> None:
    """Persist one complete operating evidence chain with execution disabled."""
    center = InvestmentReportCenter(tmp_path / "daily-investment-os.db")
    scheduled_for = "2026-08-10T08:45:00+08:00"
    first, created = center.start_task(
        job_name="morning_report",
        trade_date="2026-08-10",
        scheduled_for=scheduled_for,
        trigger="scheduler",
    )
    second, duplicated = center.start_task(
        job_name="morning_report",
        trade_date="2026-08-10",
        scheduled_for=scheduled_for,
        trigger="scheduler",
    )
    assert created is True
    assert duplicated is False
    assert first["task_id"] == second["task_id"]
    assert first["can_trade"] is False
    assert first["can_create_orders"] is False

    report = center.save_report(
        report_type="morning_report",
        trade_date="2026-08-10",
        run_id="2026-08-09_cross-sectional-v2.0.0_test_00000000",
        portfolio_id="portfolio-test",
        agent_version="daily-investment-os-v1.0.0",
        content={
            "market_environment": {"state": "evidence_unavailable"},
            "position_guidance": {"status": "hold"},
            "opportunities": [],
            "risks": [],
            "data_gaps": ["盘前无当日开盘数据"],
            "can_trade": False,
            "can_create_orders": False,
        },
        evidence=[{
            "evidence_id": "E-DC-MANIFEST",
            "source_type": "data_center",
            "source_id": "2026-08-09_cross-sectional-v2.0.0_test_00000000",
            "observed_at": "2026-08-09T15:10:00+08:00",
        }],
        status="degraded",
    )
    finished = center.finish_task(
        first["task_id"], status="succeeded", report_id=report["report_id"]
    )
    notification = center.save_notification(
        level=NotificationLevel.INFO,
        title="今日晨报已生成",
        message="晨报已按已保存证据生成，不触发任何订单。",
        source_type="investment_report",
        source_id=report["report_id"],
        channels=["in_app"],
        delivery={"in_app": "stored", "email": "disabled"},
    )

    saved = center.report(report["report_id"])
    assert finished["status"] == "succeeded"
    assert saved["status"] == "degraded"
    assert saved["evidence"][0]["evidence_id"] == "E-DC-MANIFEST"
    assert saved["can_trade"] is False
    assert saved["can_create_orders"] is False
    assert notification["level"] == "INFO"
    assert notification["channels"] == ["in_app"]
    assert notification["can_trade"] is False
    assert notification["can_create_orders"] is False


def test_report_center_database_constraints_reject_execution_capability(
    tmp_path: Path,
) -> None:
    """Make accidental privilege escalation fail at the SQLite boundary."""
    center = InvestmentReportCenter(tmp_path / "daily-investment-os.db")
    task, _ = center.start_task(
        job_name="intraday_monitor",
        trade_date="2026-08-10",
        scheduled_for="2026-08-10T09:45:00+08:00",
        trigger="scheduler",
    )
    connection = sqlite3.connect(center.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE operating_tasks SET can_create_orders=1 WHERE task_id=?",
                (task["task_id"],),
            )
    finally:
        connection.close()


def test_report_center_records_failed_and_skipped_terminal_states(tmp_path: Path) -> None:
    """Distinguish execution failure from an intentional non-trading-day skip."""
    center = InvestmentReportCenter(tmp_path / "daily-investment-os.db")
    failed, _ = center.start_task(
        job_name="closing_review",
        trade_date="2026-08-10",
        scheduled_for="2026-08-10T15:30:00+08:00",
        trigger="scheduler",
    )
    skipped, _ = center.start_task(
        job_name="weekly_report",
        trade_date="2026-08-16",
        scheduled_for="2026-08-16T16:00:00+08:00",
        trigger="scheduler",
    )

    assert center.finish_task(
        failed["task_id"], status="failed", error_message="必要证据不完整"
    )["status"] == "failed"
    skipped_result = center.finish_task(
        skipped["task_id"], status="skipped", error_message="非交易日"
    )
    assert skipped_result["status"] == "skipped"
    assert skipped_result["error_message"] == "非交易日"
    assert all(item["can_create_orders"] is False for item in center.list_tasks())


def test_report_center_rejects_a_report_without_evidence(tmp_path: Path) -> None:
    """Never publish an operating claim that has no traceable source."""
    center = InvestmentReportCenter(tmp_path / "daily-investment-os.db")
    with pytest.raises(Exception, match="证据"):
        center.save_report(
            report_type="morning_report",
            trade_date="2026-08-10",
            run_id="2026-08-07_cross-sectional-v2.0.0_test_00000000",
            portfolio_id=None,
            agent_version="daily-investment-os-v1.0.0",
            content={"summary": "unsupported"},
            evidence=[],
            status="published",
        )


def test_notifications_are_idempotent_for_one_report_event(tmp_path: Path) -> None:
    """A duplicate scheduler wake must not flood the in-app notification center."""
    center = InvestmentReportCenter(tmp_path / "daily-investment-os.db")
    payload = {
        "level": NotificationLevel.WARNING,
        "title": "组合风险提醒",
        "message": "组合风险已变化。",
        "source_type": "investment_report",
        "source_id": "report-one",
        "channels": ["in_app"],
        "delivery": {"in_app": {"state": "delivered"}},
    }
    first = center.save_notification(**payload)
    second = center.save_notification(**payload)

    assert first["notification_id"] == second["notification_id"]
    assert len(center.list_notifications()) == 1


def test_daily_os_builds_a_cited_report_without_touching_trading_workflows(
    tmp_path: Path,
) -> None:
    """Generate the morning product through dashboard reads and bounded Copilot evidence."""
    copilot = CopilotFake()
    service, daily, center = make_service(tmp_path, copilot=copilot)

    report = service.generate_report(
        "morning_report",
        trade_date="2026-08-10",
        scheduled_for="2026-08-10T08:45:00+08:00",
    )

    assert report["status"] == "published"
    assert report["can_trade"] is False
    assert report["can_create_orders"] is False
    assert report["content"]["safety"]["can_trade"] is False
    assert report["content"]["safety"]["can_create_orders"] is False
    assert report["content"]["asset_valuation"]["equity"] == 100_000.0
    assert {item["evidence_id"] for item in report["evidence"]} >= {
        "E-DC-MANIFEST",
        "E-OPS-VALUATION",
    }
    assert copilot.evidence_calls == 1
    assert copilot.generate_calls == 1
    assert daily.dashboard_calls == 1
    assert daily.forbidden_calls == []
    assert len(center.list_notifications()) == 1


def test_daily_os_model_failure_publishes_a_deterministic_degraded_report(
    tmp_path: Path,
) -> None:
    """Keep valuation and risk evidence available when the language model fails."""
    service, daily, center = make_service(tmp_path, copilot=CopilotFake(fail=True))

    report = service.generate_report(
        "closing_review",
        trade_date="2026-08-10",
        scheduled_for="2026-08-10T15:30:00+08:00",
    )

    assert report["status"] == "degraded"
    assert report["content"]["ai_analysis"]["state"] == "unavailable"
    assert "不影响确定性数据" in report["content"]["data_gaps"][-1]
    assert report["notification"]["level"] == "WARNING"
    assert report["can_trade"] is False
    assert report["can_create_orders"] is False
    assert daily.forbidden_calls == []
    assert center.counts()["report_count"] == 1


def test_deterministic_account_danger_creates_a_critical_notification(
    tmp_path: Path,
) -> None:
    """Derive notification severity from backend risk evidence, never AI wording."""
    service, daily, _center = make_service(tmp_path, danger=True)

    report = service.generate_report(
        "intraday_monitor",
        trade_date="2026-08-10",
        scheduled_for="2026-08-10T10:45:00+08:00",
    )

    assert report["status"] == "published"
    assert report["notification"]["level"] == "CRITICAL"
    assert report["notification"]["source_id"] == report["report_id"]
    assert report["notification"]["can_trade"] is False
    assert report["notification"]["can_create_orders"] is False
    assert daily.forbidden_calls == []


class PersistingReportServiceFake:
    """Persist one minimal report so JobManager idempotency can be tested in isolation."""

    def __init__(self, center: InvestmentReportCenter) -> None:
        self.report_center = center
        self.registry = TaskRegistry()
        self.calls = 0

    def generate_report(
        self,
        report_type: str,
        trade_date: str | None = None,
        scheduled_for: str | None = None,
    ) -> dict:
        """Save one cited report and expose no trading capabilities."""
        self.calls += 1
        return self.report_center.save_report(
            report_type=report_type,
            trade_date=trade_date or "2026-08-10",
            run_id="2026-08-07_cross-sectional-v2.0.0_test_00000000",
            portfolio_id=None,
            agent_version="daily-investment-os-v1.0.0",
            content={
                "scheduled_for": scheduled_for,
                "can_trade": False,
                "can_create_orders": False,
            },
            evidence=[{
                "evidence_id": "E-DC-MANIFEST",
                "source_type": "data_center",
                "source_id": "2026-08-07_cross-sectional-v2.0.0_test_00000000",
                "observed_at": "2026-08-07T15:10:00+08:00",
            }],
            status="published",
        )


def test_job_manager_executes_one_slot_exactly_once(tmp_path: Path) -> None:
    """Deduplicate repeated Windows wakes before invoking report or model work again."""
    center = InvestmentReportCenter(tmp_path / "daily-investment-os.db")
    service = PersistingReportServiceFake(center)
    manager = JobManager(service, report_center=center, registry=service.registry)
    slot = datetime(2026, 8, 10, 8, 45, tzinfo=SHANGHAI)

    first = manager.run("morning_report", scheduled_for=slot)
    second = manager.run("morning_report", scheduled_for=slot)

    assert first["state"] == "succeeded"
    assert second["state"] == "duplicate"
    assert first["task"]["task_id"] == second["task"]["task_id"]
    assert first["report"]["report_id"] == second["report"]["report_id"]
    assert service.calls == 1
    assert first["can_trade"] is False
    assert first["can_create_orders"] is False


def test_scheduler_skips_weekends_before_calling_the_report_service(tmp_path: Path) -> None:
    """Run trading-day products only on an eligible A-share session date."""
    center = InvestmentReportCenter(tmp_path / "daily-investment-os.db")
    service = PersistingReportServiceFake(center)
    manager = JobManager(service, report_center=center, registry=service.registry)
    scheduler = DailyScheduler(manager)

    saturday = datetime(2026, 8, 15, 8, 50, tzinfo=SHANGHAI)
    result = scheduler.run_job("morning_report", now=saturday)

    assert result["state"] == "skipped"
    assert "非交易日" in str(result)
    assert service.calls == 0
    assert result["can_trade"] is False
    assert result["can_create_orders"] is False


class FailingReportServiceFake(PersistingReportServiceFake):
    """Raise once to verify failure audit and absence of automatic retry."""

    def generate_report(self, *_args, **_kwargs) -> dict:
        """Fail deterministically after one invocation."""
        self.calls += 1
        raise RuntimeError("provider unavailable")


def test_job_failure_is_terminal_and_never_retried_automatically(tmp_path: Path) -> None:
    """Persist one failed task and absorb duplicate wakes for the same slot."""
    center = InvestmentReportCenter(tmp_path / "daily-investment-os.db")
    service = FailingReportServiceFake(center)
    manager = JobManager(service, report_center=center, registry=service.registry)
    slot = datetime(2026, 8, 10, 15, 30, tzinfo=SHANGHAI)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        manager.run("closing_review", scheduled_for=slot)
    duplicate = manager.run("closing_review", scheduled_for=slot)

    tasks = center.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["status"] == "failed"
    assert duplicate["state"] == "duplicate"
    assert service.calls == 1
    assert duplicate["can_trade"] is False
    assert duplicate["can_create_orders"] is False
