from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ashare_agent.api.app import create_app
from ashare_agent.services.investment_dashboard_service import InvestmentDashboardService


SHANGHAI = ZoneInfo("Asia/Shanghai")


class WorkbenchFake:
    """Expose canonical valuation and fail if the dashboard tries to trade."""

    def __init__(self) -> None:
        self.calls = 0

    def get(self) -> dict:
        self.calls += 1
        return {
            "asset_valuation": {
                "service_version": "valuation-v1.0.0",
                "valued_at": "2026-08-12T09:30:00+08:00",
                "cash": 70000.0,
                "market_value": 30000.0,
                "equity": 100000.0,
                "pnl": 1200.0,
                "drawdown": -0.02,
                "max_drawdown": -0.04,
                "exposure_ratio": 0.3,
            },
            "positions": [{
                "symbol": "600000.SH",
                "name": "浦发银行",
                "quantity": 3000,
                "market_value": 30000.0,
                "weight": 0.3,
                "unrealized_pnl": 1200.0,
                "unrealized_pnl_pct": 0.04,
            }],
            "next_session_plan": {
                "scope": "all_actual_positions",
                "position_count": 1,
                "position_count_limit_enabled": False,
                "max_positions": None,
                "positions": [{"symbol": "600000.SH"}],
            },
        }

    def submit_order(self, *_args, **_kwargs) -> None:
        raise AssertionError("Dashboard cannot submit orders")


class DailyResearchFake:
    """Return one formal ResearchPipeline result with full evidence identity."""

    def __init__(self) -> None:
        self.calls = 0

    def dashboard(self) -> dict:
        self.calls += 1
        return {
            "latest_research": {
                "run_id": "run-20260811-cross-sectional-v2.0.0-data-test",
                "research_date": "2026-08-11",
                "generated_at": "2026-08-11T15:10:00+08:00",
                "used_for_execution": True,
                "candidates": [{
                    "rank": 1,
                    "symbol": "600000.SH",
                    "name": "浦发银行",
                    "score": 88.0,
                    "reason": "七维综合得分88；趋势与质量证据完整",
                    "risk_flags": ["行业集中度需观察"],
                }],
            },
            "latest_preview": None,
        }

    def execute(self) -> None:
        raise AssertionError("Dashboard cannot execute plans")

    def monitor(self) -> None:
        raise AssertionError("Dashboard cannot monitor or match paper orders")


class InvestmentOSFake:
    """Expose risk, positions and operating tasks without report generation."""

    def __init__(self) -> None:
        self.calls = 0

    def dashboard(self) -> dict:
        self.calls += 1
        return {
            "market": {
                "scope": "research_pool",
                "label": "研究池状态（非全市场指数）",
                "plan_state": "awaiting_execution_window",
                "preview_state": "none",
            },
            "portfolio": {
                "positions": [{
                    "symbol": "600000.SH",
                    "name": "浦发银行",
                    "quantity": 3000,
                    "market_value": 30000.0,
                    "weight": 0.3,
                    "unrealized_pnl": 1200.0,
                    "unrealized_pnl_pct": 0.04,
                }],
            },
            "risk": {
                "flags": [{"level": "warning", "message": "行业集中度接近上限"}],
                "assessment": {
                    "engine_version": "portfolio-risk-v1.1.0",
                    "weighted_security_risk": 35.0,
                    "security_risks": [{
                        "symbol": "600000.SH",
                        "risk_score": 35.0,
                        "risk_level": "low",
                    }],
                },
                "exit_plan": {"signals": []},
            },
            "recent_tasks": [{
                "task_id": "task-closing",
                "job_name": "closing_review",
                "status": "succeeded",
                "scheduled_for": "2026-08-11T15:30:00+08:00",
                "report_id": "ops-report-1",
            }],
            "jobs": [
                {"job_name": "closing_review", "time_of_day": "15:30"},
                {"job_name": "morning_report", "time_of_day": "08:45"},
            ],
        }

    def generate_report(self, *_args, **_kwargs) -> None:
        raise AssertionError("Dashboard GET cannot generate reports")


class PersonalOSFake:
    """Expose journal counts while forbidding Personal OS state changes."""

    def __init__(self) -> None:
        self.calls = 0

    def dashboard(self) -> dict:
        self.calls += 1
        return {"counts": {"journal_count": 2}}

    def create_journal(self, *_args, **_kwargs) -> None:
        raise AssertionError("Dashboard GET cannot create a journal")


class CopilotFake:
    """Return one published report bound to the same formal run."""

    def __init__(self) -> None:
        self.calls = 0

    def reports(self, limit: int = 50) -> dict:
        self.calls += 1
        assert limit == 50
        return {"items": [{
            "report_id": "report-evidence-1",
            "run_id": "run-20260811-cross-sectional-v2.0.0-data-test",
            "subject_symbol": None,
            "report_type": "morning_report",
            "status": "published",
            "evidence_ids": ["E-DC-RANK", "E-PR-RISK"],
            "evidence_hash": "a" * 64,
            "created_time": "2026-08-12T08:45:00+08:00",
            "content": {"summary": "保持计划仓位，重点观察行业集中风险。"},
        }]}

    def generate(self, *_args, **_kwargs) -> None:
        raise AssertionError("Dashboard GET cannot call a model")


def make_service(clock: list[float]) -> tuple[InvestmentDashboardService, tuple[object, ...]]:
    """Build a deterministic service and expose collaborators for call assertions."""
    workbench = WorkbenchFake()
    daily = DailyResearchFake()
    operating = InvestmentOSFake()
    personal = PersonalOSFake()
    copilot = CopilotFake()
    service = InvestmentDashboardService(
        workbench_service=workbench,
        daily_research_service=daily,
        investment_os_service=operating,
        personal_os_service=personal,
        copilot_service=copilot,
        cache_seconds=30,
        now_provider=lambda: datetime(2026, 8, 12, 9, 35, tzinfo=SHANGHAI),
        monotonic_provider=lambda: clock[0],
    )
    return service, (workbench, daily, operating, personal, copilot)


def test_overview_reuses_one_30_second_snapshot_and_never_calls_execution() -> None:
    clock = [100.0]
    service, collaborators = make_service(clock)
    first = service.overview()
    first["asset"]["equity"] = -1
    second = service.overview()
    assert first["snapshot_id"] == second["snapshot_id"]
    assert second["asset"]["equity"] == 100000.0
    assert [item.calls for item in collaborators] == [1, 1, 1, 1, 1]
    clock[0] = 130.1
    third = service.overview()
    assert [item.calls for item in collaborators] == [2, 2, 2, 2, 2]
    assert third["cache_seconds"] == 30


def test_explicit_invalidation_refreshes_every_dashboard_authority_immediately() -> None:
    """A confirmed paper-ledger mutation must bypass the remaining cache TTL."""
    clock = [100.0]
    service, collaborators = make_service(clock)
    first = service.overview()
    service.invalidate()
    second = service.overview()
    assert first["snapshot_id"] == second["snapshot_id"]
    assert [item.calls for item in collaborators] == [2, 2, 2, 2, 2]


def test_overview_preserves_valuation_risk_ai_and_evidence_identity() -> None:
    service, _ = make_service([100.0])
    result = service.overview()
    assert result["asset"]["service_version"] == "valuation-v1.0.0"
    assert result["asset"]["equity"] == 100000.0
    assert result["risk"]["score"] == 35.0
    assert result["risk_version"] == "portfolio-risk-v1.1.0"
    assert result["ai_summary"]["report_id"] == "report-evidence-1"
    assert result["ai_summary"]["run_id"] == result["research"]["run_id"]
    assert result["ai_summary"]["evidence_id"] == "E-DC-RANK"
    assert result["watchlist"][0]["score"] == 88.0
    assert result["holding_health"][0]["health_score"] == 65.0
    assert len(result["tasks"]) >= 6
    assert {item["label"] for item in result["tasks"]} >= {
        "待复盘", "新提醒", "未完成日志"
    }
    assert result["can_trade"] is False
    assert result["can_create_orders"] is False


def test_overview_keeps_every_candidate_and_uses_the_15_second_default() -> None:
    clock = [100.0]
    service, collaborators = make_service(clock)
    service.cache_seconds = 15
    daily = collaborators[1]
    original = daily.dashboard
    daily.dashboard = lambda: {
        **original(),
        "latest_research": {
            **original()["latest_research"],
            "candidates": [
                {
                    "rank": index + 1,
                    "symbol": f"60000{index}.SH",
                    "name": f"股票{index}",
                    "score": 90.0 - index,
                    "reason": "正式研究候选",
                    "risk_flags": [],
                }
                for index in range(7)
            ],
        },
    }
    result = service.overview()
    assert result["cache_seconds"] == 15
    assert len(result["watchlist"]) == 7
    assert result["next_session_plan"]["scope"] == "all_actual_positions"


def test_ai_and_market_fail_closed_when_evidence_identity_is_missing() -> None:
    service, collaborators = make_service([100.0])
    daily = collaborators[1]
    daily.dashboard = lambda: {
        "latest_research": None,
        "latest_preview": {
            "research_date": "2026-08-12",
            "used_for_execution": False,
            "candidates": [],
        },
    }
    result = service.overview()
    assert result["research"]["source_mode"] == "intraday_preview"
    assert result["research"]["used_for_execution"] is False
    assert result["ai_summary"]["availability"] == "unavailable"
    assert result["ai_summary"]["report_id"] is None
    assert result["market"]["market_regime"] is None
    assert any("run_id" in gap for gap in result["data_gaps"])


def test_api_exposes_one_read_only_dashboard_contract(tmp_path: Path) -> None:
    service, _ = make_service([100.0])

    class ProjectDaily(DailyResearchFake):
        raw = {"paper_account": {}}

        def close(self) -> None:
            pass

        def _trading_days(self) -> set[str]:
            return {"2026-08-12"}

    project = tmp_path
    (project / "config").mkdir()
    (project / "web").mkdir()
    (project / "web" / "index.html").write_text("<h1>test</h1>", encoding="utf-8")
    app = create_app(
        project,
        daily_research_service=ProjectDaily(),
        workbench_service=WorkbenchFake(),
        investment_os_service=InvestmentOSFake(),
        personal_os_service=PersonalOSFake(),
        copilot_service=CopilotFake(),
        investment_dashboard_service=service,
        investment_job_manager=object(),
    )
    assert app.openapi()["paths"]["/api/v1/dashboard/overview"]["get"]["operationId"] == "get_investment_dashboard_overview"
    with TestClient(app) as client:
        response = client.get("/api/v1/dashboard/overview")
        assert response.status_code == 200
        payload = response.json()
        assert payload["valuation_version"] == "valuation-v1.0.0"
        assert payload["can_trade"] is False
        assert payload["can_create_orders"] is False


def test_dashboard_frontend_uses_generated_client_and_has_mobile_single_column() -> None:
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "web" / "app.js").read_text(encoding="utf-8")
    components = (root / "web" / "components" / "dashboard" / "dashboard-components.js").read_text(encoding="utf-8")
    client = (root / "web" / "generated" / "client.js").read_text(encoding="utf-8")
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    css = (root / "web" / "styles.css").read_text(encoding="utf-8")
    assert '"get_investment_dashboard_overview"' in client
    assert 'apiRequest("get_investment_dashboard_overview"' in app_js
    assert "fetch(" not in components
    assert "/api/" not in components
    for component in ("MarketCard", "AssetCard", "RiskCard", "AICard", "StockCard", "HoldingHealthCard", "TaskCard"):
        assert f"export function {component}" in components
    assert 'id="workspace-dashboard"' in html
    assert 'data-workspace="dashboard"' in html
    assert 'const CORE_SYNC_JOBS = [' in app_js
    for job in (
        "system", "daily", "review", "operating", "dashboard", "personal",
        "reviewloop", "data", "evolution", "engineering", "observability",
    ):
        assert f'"{job}"' in app_js
    assert "CORE_SYNC_JOBS.length}个模块通道" in app_js
    mobile_js = (root / "web" / "mobile" / "mobile.js").read_text(encoding="utf-8")
    assert "const FOREGROUND_SYNC_MS = 15_000;" in mobile_js
    assert "@media (max-width:560px)" in css
    assert ".dashboard-decision-grid,.dashboard-stock-grid { grid-template-columns:minmax(0,1fr); }" in css
    forbidden_operators = (
        "asset.cash +", "asset.market_value +", "asset.equity -",
        "asset.market_value /", "asset.pnl /", "asset.drawdown *",
    )
    assert not any(formula in components.lower() for formula in forbidden_operators)
