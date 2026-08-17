from datetime import date
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import shutil
import time

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd
import yaml

from ashare_agent.api.app import create_app
from ashare_agent.core.contracts import CompletedRunSnapshot, ModelState
from ashare_agent.model_provider import ModelProviderError, ModelProviderStatus
from ashare_agent.services.model_service import ModelService


WRITE_HEADERS = {"X-Ashare-Client": "local-dashboard"}


def make_project(tmp_path: Path) -> Path:
    """Create an isolated local-only project for API integration tests."""
    source_root = Path(__file__).resolve().parents[1]
    (tmp_path / "config").mkdir()
    (tmp_path / "web").mkdir()
    shutil.copy2(source_root / "config" / "settings.yaml", tmp_path / "config" / "settings.yaml")
    config_path = tmp_path / "config" / "settings.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["data"]["provider"] = "local_csv"
    if raw["data"]["end_date"] == "latest":
        raw["data"]["end_date"] = "2026-07-17"
    config_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dates = pd.bdate_range(raw["data"]["start_date"], raw["data"]["end_date"])
    for offset, symbol in enumerate(raw["universe"]["symbols"]):
        close = 3.0 + offset * 0.3 + np.arange(len(dates)) * 0.002 + np.sin(np.arange(len(dates)) / 18) * 0.08
        frame = pd.DataFrame(
            {
                "date": dates,
                "open": close * 0.998,
                "high": close * 1.006,
                "low": close * 0.994,
                "close": close,
                "volume": 1_000_000 + np.arange(len(dates)),
            }
        )
        frame.to_csv(data_dir / f"{symbol.replace('.', '_')}.csv", index=False)
    (tmp_path / "web" / "index.html").write_text("<h1>test</h1>", encoding="utf-8")
    return tmp_path


def wait_for_completion(client: TestClient, run_id: str) -> dict:
    # Windows endpoint scanning can make the first pandas/SQLite snapshot slow.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        record = client.get(f"/api/v1/runs/{run_id}").json()
        if record["state"] in {"succeeded", "failed"}:
            return record
        time.sleep(0.05)
    raise AssertionError("backtest did not complete in time")


def test_api_exposes_safe_capabilities_only(tmp_path: Path) -> None:
    app = create_app(make_project(tmp_path))
    paths = set(app.openapi()["paths"])
    assert "/api/v1/daily-research" in paths
    assert "/api/v1/daily-research/preview" in paths
    assert "/api/v1/daily-research/execute" in paths
    assert "/api/v1/market/quote" in paths
    assert "/api/v1/paper/orders/preview" in paths
    assert "/api/v1/paper/orders/buy" in paths
    assert "/api/v1/paper/orders/sell" in paths
    assert "/api/v1/paper/broker/orders" in paths
    assert "/api/v1/paper/broker/orders/preview" in paths
    assert "/api/v1/paper/broker/match" in paths
    assert "/api/v1/paper/broker/orders/{client_order_id}/cancel" in paths
    assert "/api/v1/model/deepseek/config" in paths
    assert "/api/v1/model/deepseek/clear" in paths
    assert "/api/v1/model/daily-research-explanation" in paths
    assert "/api/v1/copilot/status" in paths
    assert "/api/v1/copilot/evidence" in paths
    assert "/api/v1/copilot/reports" in paths
    assert "/api/v1/copilot/memory" in paths
    assert "/api/v1/investment-os" in paths
    assert "/api/v1/investment-os/reports" in paths
    assert "/api/v1/investment-os/reports/{report_id}" in paths
    assert "/api/v1/investment-os/notifications" in paths
    assert "/api/v1/investment-os/jobs/{job_name}/run" in paths
    assert "/api/v1/investment-os/notifications/{notification_id}/read" in paths
    assert "/api/v1/personal-os" in paths
    assert "/api/v1/personal-os/events/sync" in paths
    assert "/api/v1/personal-os/reports/monthly" in paths
    assert "/api/v1/data-intelligence" in paths
    assert "/api/v1/data-intelligence/evaluate" in paths
    assert "/api/v1/data-intelligence/health" in paths
    assert "/api/v1/data-intelligence/incidents" in paths
    assert "/api/v1/data-intelligence/incidents/{incident_id}/acknowledge" in paths
    assert "/api/v1/data-intelligence/catalog" in paths
    assert "/api/v1/data-intelligence/lineage/{run_id}" in paths
    assert "/api/v1/strategy-evolution" in paths
    assert "/api/v1/strategy-evolution/branches/import" in paths
    assert "/api/v1/strategy-evolution/evaluations" in paths
    assert "/api/v1/strategy-evolution/health" in paths
    assert "/api/v1/strategy-evolution/reports/{report_id}" in paths
    assert "/api/v1/strategy-evolution/comparisons" in paths
    assert "/api/v1/strategy-evolution/lifecycle" in paths
    assert "/api/v1/strategy-evolution/lifecycle/requests" in paths
    assert "/api/v1/orders" not in paths
    assert "/api/v1/config" not in paths
    assert "/api/v1/live" not in paths
    assert "/api/v1/external-paper/status" not in paths
    assert not any(
        path.endswith("/orders")
        and not path.startswith("/api/v1/paper/")
        for path in paths
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        status = client.get("/api/v1/status").json()
        assert status["safety"]["live_trading_enabled"] is False
        assert status["safety"]["model_in_execution"] is False
        assert status["model"]["can_trade"] is False
        workbench = client.get("/api/v1/workbench").json()
        assert workbench["version"] == "1.1.0"
        assert workbench["mode"] == "paper_portfolio_review"
        assert workbench["can_submit_orders"] is False
        assert workbench["source_nav_date"] is None
        assert workbench["positions"] == []
        assert workbench["asset_valuation"]["service_version"] == "valuation-v1.0.0"
        daily = client.get("/api/v1/daily-research").json()
        for field in ("cash", "market_value", "equity", "pnl", "drawdown"):
            assert daily["asset_valuation"][field] == workbench["asset_valuation"][field]
        personal = client.get("/api/v1/personal-os").json()
        assert personal["asset_valuation"]["service_version"] == "valuation-v1.0.0"
        for field in ("cash", "market_value", "equity", "pnl", "drawdown"):
            assert personal["asset_valuation"][field] == workbench["asset_valuation"][field]
        assert personal["personal_score"]["meaning"]
        assert personal["can_trade"] is False
        assert personal["can_create_orders"] is False
        assert personal["can_modify_strategy"] is False
        assert "白名单未参与复盘" in workbench["provenance"]
        assert client.get("/", headers={"Host": "attacker.example"}).status_code == 400


def test_all_ai_consumers_share_one_model_runtime(tmp_path: Path) -> None:
    """Keep one configured DeepSeek runtime behind every AI-facing module."""
    model_service = ModelService()
    app = create_app(make_project(tmp_path), model_service=model_service)
    with TestClient(app):
        assert app.state.dashboard_service.model_service is model_service
        assert app.state.workbench_service.model_service is model_service
        assert app.state.copilot_service.model_service is model_service
        assert app.state.quant_ai_service.model_service is model_service
        assert app.state.investment_os_service.copilot.model_service is model_service
        assert app.state.investment_dashboard_service.copilot.model_service is model_service
        assert app.state.personal_ai_assistant_service.copilot.model_service is model_service

        status = model_service.status()
        assert status["runtime_scope"] == "shared_system"
        assert set(status["consumer_modules"]) == {
            "backtest_explanation",
            "daily_research_explanation",
            "paper_review_explanation",
            "ai_investment_copilot",
            "daily_investment_os",
            "ai_quant_research",
            "personal_ai_assistant",
            "mobile_investment_assistant",
        }
        assert status["can_trade"] is False


def test_web_uses_generated_openapi_client_and_scheduler_keeps_key_out() -> None:
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "web" / "app.js").read_text(encoding="utf-8")
    generated = (root / "web" / "generated" / "client.js").read_text(encoding="utf-8")
    index_html = (root / "web" / "index.html").read_text(encoding="utf-8")
    install = (root / "安装自动任务.cmd").read_text(encoding="utf-8")
    assert 'fetch("/api/' not in app_js
    assert '"execute_daily_paper_plan"' in generated
    assert '"collect_daily_research"' in generated
    assert '"refresh_intraday_candidates"' in generated
    assert '"get_decision_workbench"' in generated
    assert '"start_run"' in generated
    assert '"configure_deepseek"' in generated
    assert '"create_daily_research_explanation"' in generated
    assert '"create_model_explanation"' in generated
    assert '"get_live_market_quote"' in generated
    assert '"preview_paper_order"' in generated
    assert '"submit_paper_buy"' in generated
    assert '"preview_broker_paper_order"' in generated
    assert '"submit_broker_paper_order"' in generated
    assert '"cancel_broker_paper_order"' in generated
    assert '"match_broker_paper_orders"' in generated
    assert '"create_paper_review_explanation"' in generated
    assert '"get_copilot_status"' in generated
    assert '"get_copilot_evidence"' in generated
    assert '"create_copilot_report"' in generated
    assert '"list_copilot_reports"' in generated
    assert '"create_copilot_memory"' in generated
    assert '"list_copilot_memory"' in generated
    assert '"get_investment_os"' in generated
    assert '"list_investment_reports"' in generated
    assert '"get_investment_report"' in generated
    assert '"list_investment_notifications"' in generated
    assert '"run_investment_os_job"' in generated
    assert '"mark_investment_notification_read"' in generated
    assert '"get_personal_investment_os"' in generated
    assert '"sync_personal_investment_events"' in generated
    assert '"create_personal_monthly_review"' in generated
    assert '"get_data_intelligence_dashboard"' in generated
    assert '"evaluate_data_intelligence_run"' in generated
    assert '"get_data_health"' in generated
    assert '"list_data_incidents"' in generated
    assert '"acknowledge_data_incident"' in generated
    assert '"list_data_catalog"' in generated
    assert '"get_data_lineage"' in generated
    assert '"get_strategy_evolution_center"' in generated
    assert '"import_strategy_evolution_branch"' in generated
    assert '"evaluate_strategy_evolution"' in generated
    assert '"list_strategy_evolution_health"' in generated
    assert '"get_strategy_evolution_report"' in generated
    assert '"compare_strategy_versions"' in generated
    assert '"list_strategy_evolution_comparisons"' in generated
    assert '"request_strategy_evolution_transition"' in generated
    assert '"list_strategy_evolution_lifecycle"' in generated
    assert '"approve_strategy_evolution_transition"' in generated
    assert '"reject_strategy_evolution_transition"' in generated
    assert 'id="workspace-dataintel"' in index_html
    assert 'id="workspace-evolution"' in index_html
    assert 'id="workspace-personalos"' in index_html
    assert 'id="workspace-overview"' in index_html
    assert 'id="workspace-market"' in index_html
    assert 'id="workspace-etf"' in index_html
    assert 'id="workspace-assistant"' in index_html
    assert 'id="investmentReportList"' in index_html
    assert 'id="investmentNotificationList"' in index_html
    assert 'id="investmentJobGrid"' in index_html
    assert 'id="investmentReportDialog"' in index_html
    assert 'data-workspace="overview"' in index_html
    assert 'data-workspace="market"' in index_html
    assert 'data-workspace="etf"' in index_html
    assert 'data-workspace="assistant"' in index_html
    assert 'data-workspace="evolution"' in index_html
    assert index_html.count('id="killButton"') == 1
    assert 'id="killSwitchButton"' not in index_html
    assert 'id="rankingBody"' in index_html
    assert 'id="rankingCards"' in index_html
    assert 'id="rankingSearch"' in index_html
    assert 'id="rankingFilter"' in index_html
    assert 'id="candidateDetail"' in index_html
    assert 'id="nextActionButton"' in index_html
    assert 'id="toastStack"' in index_html
    assert 'id="actionOverlay"' in index_html
    assert 'id="liveSyncPanel"' in index_html
    assert 'id="liveSyncEnabled"' in index_html
    assert 'id="candidateAutoEnabled"' in index_html
    assert 'data-live-module="research"' in index_html
    assert 'data-live-module="quote"' in index_html
    assert 'data-live-refresh="system"' in index_html
    assert 'data-live-refresh="candidates"' in index_html
    assert 'id="previewButton"' in index_html
    assert 'id="manualPaperForm"' in index_html
    assert 'id="paperBuyButton"' in index_html
    assert 'id="paperOrderPreview"' in index_html
    assert 'id="performanceStats"' in index_html
    assert 'id="reviewRiskFlags"' in index_html
    assert 'id="attributionBody"' in index_html
    assert 'id="quoteState"' in index_html
    assert 'id="watchlistGrid"' in index_html
    assert 'id="modelExplainTop10Button"' in index_html
    assert 'id="modelExplainReviewButton"' in index_html
    assert 'id="copilotReportGrid"' in index_html
    assert 'id="copilotMemoryForm"' in index_html
    assert 'id="strategyList"' not in index_html
    assert "四只 ETF" not in index_html
    assert 'type="password"' in index_html
    assert "function activateWorkspace" in app_js
    assert "function renderGuidedAction" in app_js
    assert "function renderCandidateDetail" in app_js
    assert "function showToast" in app_js
    assert "function startLiveSync" in app_js
    assert "function refreshCandidatesAutomatically" in app_js
    assert "function isMarketMonitoringWindow" in app_js
    assert "function syncModuleJob" in app_js
    assert "function tickLiveScheduler" in app_js
    assert "function refreshLiveModuleNow" in app_js
    assert "function scheduleJob" in app_js
    assert "refreshInFlight" not in app_js
    assert "quotePollTimer" not in app_js
    assert "lastPersistedEquity" not in app_js
    assert "buyingPower" not in app_js
    assert "Math.floor(availableCash" not in app_js
    assert "payload.account.equity" not in app_js
    assert "payload.account.market_value" not in app_js
    assert 'apiRequest("refresh_intraday_candidates"' in app_js
    assert 'apiRequest("get_live_market_quote"' in app_js
    assert 'apiRequest("preview_broker_paper_order"' in app_js
    assert 'apiRequest("submit_broker_paper_order"' in app_js
    assert 'apiRequest("cancel_broker_paper_order"' in app_js
    assert 'apiRequest("match_broker_paper_orders"' in app_js
    assert 'apiRequest("create_copilot_report"' in app_js
    assert 'apiRequest("list_copilot_reports"' in app_js
    assert 'apiRequest("create_copilot_memory"' in app_js
    assert '"refresh_intraday_candidates"' in app_js
    ids = re.findall(r'\bid="([^"]+)"', index_html)
    assert len(ids) == len(set(ids))
    assert "ASHARE_MODEL_API_KEY" not in app_js
    assert "THS_FINANCE_API_KEY" not in install
    assert "/SC MINUTE /MO 5 /ST 09:35 /ET 14:55" in install


def test_quote_is_read_only_and_manual_buy_requires_local_write_guard(tmp_path: Path) -> None:
    """Keep quote reads public to localhost while protecting local account mutation."""

    class PaperApiStub:
        def close(self) -> None:
            """Match the application lifespan contract."""

        def quote(self, symbol: str) -> dict:
            """Return a contract-complete THS polling snapshot."""
            return {
                "symbol": symbol,
                "name": "测试股份",
                "source": "ths_finance_snapshot",
                "feed_type": "polling_snapshot",
                "timestamp_ms": 1_774_224_900_000,
                "observed_at": "2026-03-23T09:35:00+08:00",
                "age_seconds": 1.2,
                "stale": False,
                "market_open": True,
                "last_price": 10.0,
                "price_change": 0.1,
                "price_change_ratio_pct": 1.01,
                "open_price": 9.95,
                "high_price": 10.1,
                "low_price": 9.9,
                "prev_price": 9.9,
                "volume": 1_000_000,
                "turnover": 10_000_000,
                "can_submit_paper_order": True,
                "blocked_reason": None,
                "live_trading_enabled": False,
                "can_submit_orders": False,
            }

        def manual_buy(self, symbol: str, quantity: int, request_key: str) -> dict:
            """Return a local-only persisted paper order response."""
            return {
                "quote_timestamp_ms": 1_774_224_900_000,
                "order": {
                    "symbol": symbol,
                    "quantity": quantity,
                    "status": "FILLED",
                    "client_order_id": f"manual:{request_key}",
                },
                "account": {"cash": 98_995.0, "positions": []},
                "live_trading_enabled": False,
                "can_submit_orders": False,
            }

        def preview_manual_order(self, side: str, symbol: str, quantity: int) -> dict:
            """Return a non-ordering fee and risk estimate."""
            return {
                "side": side,
                "symbol": symbol,
                "name": "测试股份",
                "quantity": quantity,
                "allowed": True,
                "blocked_reason": None,
                "blocked_state": "RISK_REJECTED",
                "requested_price": 10.0,
                "estimated_fill_price": 10.01,
                "gross_value": 1001.0,
                "fee_breakdown": {
                    "commission": 5.0,
                    "stamp_tax": 0.0,
                    "transfer_fee": 0.02,
                    "total": 5.02,
                },
                "estimated_slippage_cost": 1.0,
                "estimated_realized_pnl": 0.0,
                "before": {"cash": 100000.0, "equity": 100000.0},
                "after": {
                    "cash": 98993.98,
                    "equity": 99993.98,
                    "exposure_ratio": 0.01,
                },
                "quote_timestamp_ms": 1_774_224_900_000,
                "live_trading_enabled": False,
                "can_submit_orders": False,
            }

        def manual_sell(self, symbol: str, quantity: int, request_key: str) -> dict:
            """Return a local-only persisted reduce-only paper order."""
            return {
                "quote_timestamp_ms": 1_774_224_900_000,
                "order": {
                    "symbol": symbol,
                    "quantity": quantity,
                    "status": "FILLED",
                    "client_order_id": f"manual-sell:{request_key}",
                },
                "account": {"cash": 99_995.0, "positions": []},
                "live_trading_enabled": False,
                "can_submit_orders": False,
            }

    app = create_app(make_project(tmp_path), daily_research_service=PaperApiStub())
    payload = {
        "symbol": "600519.SH",
        "quantity": 100,
        "idempotency_key": "00000000-0000-4000-8000-000000000001",
    }
    with TestClient(app) as client:
        quote = client.get("/api/v1/market/quote", params={"symbol": "600519.SH"})
        assert quote.status_code == 200
        assert quote.json()["feed_type"] == "polling_snapshot"
        assert quote.json()["can_submit_orders"] is False
        preview_payload = {
            "symbol": "600519.SH", "quantity": 100, "side": "BUY",
        }
        assert client.post(
            "/api/v1/paper/orders/preview", json=preview_payload
        ).status_code == 403
        preview = client.post(
            "/api/v1/paper/orders/preview",
            json=preview_payload,
            headers=WRITE_HEADERS,
        )
        assert preview.status_code == 200
        assert preview.json()["fee_breakdown"]["total"] == 5.02
        assert preview.json()["allowed"] is True
        assert client.post("/api/v1/paper/orders/buy", json=payload).status_code == 403
        bought = client.post(
            "/api/v1/paper/orders/buy", json=payload, headers=WRITE_HEADERS
        )
        assert bought.status_code == 200
        assert bought.json()["order"]["status"] == "FILLED"
        assert bought.json()["live_trading_enabled"] is False
        assert bought.json()["can_submit_orders"] is False
        assert client.post("/api/v1/paper/orders/sell", json=payload).status_code == 403
        sold = client.post(
            "/api/v1/paper/orders/sell", json=payload, headers=WRITE_HEADERS
        )
        assert sold.status_code == 200
        assert sold.json()["order"]["status"] == "FILLED"


def test_intraday_preview_requires_local_write_guard(tmp_path: Path) -> None:
    """Manual refresh is protected like a local action while remaining non-executable."""

    class PreviewApiStub:
        def close(self) -> None:
            """Match the application lifespan contract."""

        def refresh_preview(self) -> dict:
            """Return a read-only preview payload for route verification."""
            return {
                "mode": "intraday_preview", "used_for_execution": False,
                "execution_ready": False, "targets": [], "candidates": [],
            }

    app = create_app(make_project(tmp_path), daily_research_service=PreviewApiStub())
    with TestClient(app) as client:
        assert client.post("/api/v1/daily-research/preview").status_code == 403
        response = client.post("/api/v1/daily-research/preview", headers=WRITE_HEADERS)
        assert response.status_code == 200
        assert response.json()["used_for_execution"] is False


def test_kill_switch_blocks_new_runs(tmp_path: Path) -> None:
    app = create_app(make_project(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/safety/kill-switch",
            json={"enabled": True},
            headers=WRITE_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["live_trading_enabled"] is False
        blocked = client.post("/api/v1/runs", headers=WRITE_HEADERS)
        assert blocked.status_code == 423


def test_successful_paper_mutation_invalidates_dashboard_cache(tmp_path: Path) -> None:
    """Only a confirmed paper-account write may invalidate the read snapshot."""

    class DashboardCacheSpy:
        def __init__(self) -> None:
            self.invalidations = 0

        def invalidate(self) -> None:
            self.invalidations += 1

        def overview(self) -> dict:
            raise AssertionError("This test only verifies the mutation boundary")

    dashboard = DashboardCacheSpy()
    app = create_app(
        make_project(tmp_path), investment_dashboard_service=dashboard
    )
    with TestClient(app) as client:
        denied = client.post(
            "/api/v1/safety/kill-switch", json={"enabled": True}
        )
        assert denied.status_code == 403
        assert dashboard.invalidations == 0
        accepted = client.post(
            "/api/v1/safety/kill-switch",
            json={"enabled": True},
            headers=WRITE_HEADERS,
        )
        assert accepted.status_code == 200
        assert dashboard.invalidations == 1


def test_workbench_reviews_only_actual_paper_positions(tmp_path: Path) -> None:
    """Exclude configured ETF symbols and expose only MockBroker holdings."""
    app = create_app(make_project(tmp_path))
    app.state.workbench_service.trade_date_provider = lambda: date(2026, 7, 21)
    app.state.workbench_service.valuation_provider = lambda symbols: {
        "prices": {symbol: 100.0 for symbol in symbols},
        "source": "ths_finance_snapshot",
        "observed_at": "2026-07-21T10:00:00+08:00",
        "age_seconds": 0.0,
        "stale": False,
        "message": "测试持仓估值",
    }
    with TestClient(app) as client:
        paper = app.state.daily_research_service.paper
        paper.manual_buy(
            symbol="600519.SH",
            name="贵州茅台",
            quantity=100,
            quotes={
                "600519.SH": {
                    "last_price": 100.0,
                    "volume": 1_000_000,
                    "price_change_ratio_pct": 1.0,
                }
            },
            trade_date=date(2026, 7, 21),
            request_key="review-position",
        )
        payload = client.get("/api/v1/workbench").json()
        assert [item["symbol"] for item in payload["positions"]] == ["600519.SH"]
        assert payload["positions"][0]["name"] == "贵州茅台"
        assert payload["activity_summary"]["position_count"] == 1
        assert payload["activity_summary"]["trade_count"] == 1
        assert payload["positions"][0]["valuation_source"] == "ths_polling_snapshot"
        assert payload["valuation"]["source"] == "ths_finance_snapshot"
        assert payload["valuation_observed_at"] == "2026-07-21T10:00:00+08:00"
        assert payload["valuation_trade_date"] == "2026-07-21"
        assert payload["nav_snapshot_state"] == "PERSISTED_CURRENT"
        assert payload["source_nav_date"] == "2026-07-21"
        assert abs(payload["performance"]["pnl_reconciliation_gap"]) < 1e-6
        assert payload["symbol_attribution"][0]["symbol"] == "600519.SH"
        assert payload["discipline"]["score"] == 100
        assert not {
            "510300.SH", "510500.SH", "512100.SH", "159915.SZ"
        } & {item["symbol"] for item in payload["positions"]}


def test_workbench_refreshes_t1_and_lists_all_positions_in_next_session_plan(tmp_path: Path) -> None:
    """Release prior-day shares and never truncate an account with over five names."""
    app = create_app(make_project(tmp_path))
    selected_date = [date(2026, 7, 21)]
    app.state.workbench_service.trade_date_provider = lambda: selected_date[0]
    app.state.workbench_service.valuation_provider = lambda symbols: {
        "prices": {symbol: 10.0 for symbol in symbols},
        "source": "ths_finance_snapshot",
        "observed_at": "2026-07-22T09:35:00+08:00",
        "age_seconds": 0.0,
        "stale": False,
        "message": "测试持仓估值",
    }
    paper = app.state.daily_research_service.paper
    symbols = [f"60000{index}.SH" for index in range(6)]
    market = {
        symbol: {
            "last_price": 10.0,
            "volume": 1_000_000,
            "price_change_ratio_pct": 1.0,
        }
        for symbol in symbols
    }
    for index, symbol in enumerate(symbols):
        result = paper.manual_buy(
            symbol=symbol,
            name=f"股票{index}",
            quantity=100,
            quotes=market,
            trade_date=selected_date[0],
            request_key=f"workbench-all-{index}",
        )
        assert result["order"]["status"] == "FILLED"
    with TestClient(app) as client:
        same_day = client.get("/api/v1/workbench").json()
        assert len(same_day["positions"]) == 6
        assert all(item["available_quantity"] == 0 for item in same_day["positions"])
        selected_date[0] = date(2026, 7, 22)
        next_day = client.get("/api/v1/workbench").json()
        assert all(item["available_quantity"] == 100 for item in next_day["positions"])
        plan = next_day["next_session_plan"]
        assert plan["scope"] == "all_actual_positions"
        assert plan["position_count"] == 6
        assert plan["position_count_limit_enabled"] is False
        assert plan["max_positions"] is None
        assert {item["symbol"] for item in plan["positions"]} == set(symbols)
        assert next_day["position_refresh_seconds"] == 15
        assert next_day["settlement_rule"] == "A_SHARE_T_PLUS_1"


def test_workbench_separates_live_valuation_time_from_historical_nav(tmp_path: Path) -> None:
    """Persist a complete same-day quote while exposing both time semantics."""
    app = create_app(make_project(tmp_path))
    paper = app.state.daily_research_service.paper
    paper.manual_buy(
        symbol="600519.SH",
        name="贵州茅台",
        quantity=100,
        quotes={
            "600519.SH": {
                "last_price": 100.0,
                "volume": 1_000_000,
                "price_change_ratio_pct": 1.0,
            }
        },
        trade_date=date(2026, 8, 4),
        request_key="historical-nav",
    )
    app.state.workbench_service.trade_date_provider = lambda: date(2026, 8, 12)
    app.state.workbench_service.valuation_provider = lambda symbols: {
        "prices": {symbol: 108.0 for symbol in symbols},
        "source": "ths_finance_snapshot",
        "observed_at": "2026-08-12T21:29:20+08:00",
        "age_seconds": 0.0,
        "stale": False,
        "message": "测试同日持仓估值",
    }
    with TestClient(app) as client:
        payload = client.get("/api/v1/workbench").json()
    assert payload["valuation_observed_at"] == "2026-08-12T21:29:20+08:00"
    assert payload["valuation_trade_date"] == "2026-08-12"
    assert payload["nav_snapshot_state"] == "PERSISTED_CURRENT"
    assert payload["source_nav_date"] == "2026-08-12"
    assert payload["nav"][-1]["trade_date"] == "2026-08-12"


def test_workbench_refuses_unsafe_nav_persistence_but_keeps_live_valuation(tmp_path: Path) -> None:
    """Reject stale, incomplete, mismatched and untrusted NAV write evidence."""
    scenarios = [
        (
            "SKIPPED_STALE",
            {"prices": {"600000.SH": 11.0}, "source": "ths_finance_snapshot", "observed_at": "2026-08-12T10:00:00+08:00", "age_seconds": 999.0, "stale": True, "message": "陈旧"},
        ),
        (
            "SKIPPED_INCOMPLETE",
            {"prices": {}, "source": "ths_finance_snapshot", "observed_at": "2026-08-12T10:00:00+08:00", "age_seconds": 0.0, "stale": False, "message": "不完整"},
        ),
        (
            "SKIPPED_DATE_MISMATCH",
            {"prices": {"600000.SH": 11.0}, "source": "ths_finance_snapshot", "observed_at": "2026-08-11T15:00:00+08:00", "age_seconds": 0.0, "stale": False, "message": "跨日"},
        ),
        (
            "SKIPPED_UNTRUSTED",
            {"prices": {"600000.SH": 11.0}, "source": "test_snapshot", "observed_at": "2026-08-12T10:00:00+08:00", "age_seconds": 0.0, "stale": False, "message": "未信任"},
        ),
    ]
    for index, (expected_state, valuation) in enumerate(scenarios):
        scenario_root = tmp_path / str(index)
        scenario_root.mkdir()
        app = create_app(make_project(scenario_root))
        paper = app.state.daily_research_service.paper
        paper.manual_buy(
            symbol="600000.SH",
            name="浦发银行",
            quantity=100,
            quotes={"600000.SH": {"last_price": 10.0, "volume": 1_000_000, "price_change_ratio_pct": 1.0}},
            trade_date=date(2026, 8, 4),
            request_key=f"unsafe-nav-{index}",
        )
        app.state.workbench_service.trade_date_provider = lambda: date(2026, 8, 12)
        app.state.workbench_service.valuation_provider = lambda _symbols, payload=valuation: payload
        with TestClient(app) as client:
            payload = client.get("/api/v1/workbench").json()
        assert payload["nav_snapshot_state"] == expected_state
        assert payload["source_nav_date"] == "2026-08-04"
        if valuation["prices"]:
            assert payload["positions"][0]["last_price"] == 11.0


def test_workbench_reports_nav_write_failure_without_hiding_valuation(tmp_path: Path) -> None:
    """Keep a successful live valuation visible when durable NAV writing fails."""
    app = create_app(make_project(tmp_path))
    paper = app.state.daily_research_service.paper
    paper.manual_buy(
        symbol="600000.SH",
        name="浦发银行",
        quantity=100,
        quotes={"600000.SH": {"last_price": 10.0, "volume": 1_000_000, "price_change_ratio_pct": 1.0}},
        trade_date=date(2026, 8, 4),
        request_key="nav-write-failure",
    )
    app.state.workbench_service.trade_date_provider = lambda: date(2026, 8, 12)
    app.state.workbench_service.valuation_provider = lambda symbols: {
        "prices": {symbol: 11.0 for symbol in symbols},
        "source": "ths_finance_snapshot",
        "observed_at": "2026-08-12T10:00:00+08:00",
        "age_seconds": 0.0,
        "stale": False,
        "message": "当前估值可用",
    }
    paper.mark_to_market = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk locked"))
    with TestClient(app) as client:
        payload = client.get("/api/v1/workbench").json()
    assert payload["nav_snapshot_state"] == "PERSISTENCE_FAILED"
    assert payload["source_nav_date"] == "2026-08-04"
    assert payload["positions"][0]["last_price"] == 11.0
    assert any(flag["code"] == "NAV_PERSISTENCE_FAILED" for flag in payload["risk_flags"])


def test_workbench_frontend_badge_uses_valuation_time_not_nav_date() -> None:
    """Prevent the holdings badge from relabeling historical NAV as live data."""
    app_js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    assert 'valuationBadgeLabel(currentValuation)' in app_js
    assert 'payload.valuation_observed_at || payload.valuation?.observed_at' in app_js
    assert 'payload.source_nav_date ? `净值 ${payload.source_nav_date}`' not in app_js


def test_daily_dashboard_and_review_can_read_shared_ledger_concurrently(tmp_path: Path) -> None:
    """Match the browser's parallel refresh without racing one SQLite connection."""
    app = create_app(make_project(tmp_path))
    with TestClient(app) as client:
        paths = ["/api/v1/daily-research", "/api/v1/workbench"] * 10
        with ThreadPoolExecutor(max_workers=6) as executor:
            responses = list(executor.map(client.get, paths))
        assert all(response.status_code == 200 for response in responses)


def test_cross_site_or_headerless_writes_are_rejected(tmp_path: Path) -> None:
    app = create_app(make_project(tmp_path))
    with TestClient(app) as client:
        assert client.post("/api/v1/runs").status_code == 403
        response = client.post(
            "/api/v1/runs",
            headers={**WRITE_HEADERS, "Origin": "https://attacker.example"},
        )
        assert response.status_code == 403


def test_deepseek_key_is_write_only_and_never_echoed(tmp_path: Path) -> None:
    service = ModelService()
    captured = {}

    def configure(api_key: str, model: str) -> dict:
        captured.update(api_key=api_key, model=model)
        return {
            "state": "connected",
            "provider": "openai_compatible",
            "model": model,
            "base_url": "https://api.deepseek.com",
            "last_checked_at": "2026-07-20T00:00:00+00:00",
            "message": "连接正常；仅用于研究解读",
            "can_trade": False,
            "api_key_configured": True,
            "runtime_scope": "shared_system",
            "consumer_modules": ["ai_investment_copilot"],
        }

    service.configure_deepseek = configure
    app = create_app(make_project(tmp_path), model_service=service)
    secret = "sk-" + "z" * 30
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/model/deepseek/config",
            json={"api_key": secret, "model": "deepseek-v4-flash"},
        ).status_code == 403
        response = client.post(
            "/api/v1/model/deepseek/config",
            json={"api_key": secret, "model": "deepseek-v4-flash"},
            headers=WRITE_HEADERS,
        )
        assert response.status_code == 200
        encoded = response.text
        assert secret not in encoded
        assert "api_key" not in response.json()
        assert captured == {"api_key": secret, "model": "deepseek-v4-flash"}


def test_model_protocol_error_maps_to_safe_gateway_error(tmp_path: Path) -> None:
    class BrokenProvider:
        def status(self):
            return ModelProviderStatus(
                state=ModelState.CONNECTED,
                provider="fake",
                model="fake",
                base_url="https://api.example/v1",
                last_checked_at=None,
                message="connected",
            )

        def test_connection(self):
            return self.status()

        def explain(self, _snapshot):
            raise ModelProviderError("模型服务返回无效响应")

    app = create_app(make_project(tmp_path), model_service=ModelService(BrokenProvider()))
    run_id = "00000000-0000-0000-0000-000000000001"
    app.state.dashboard_service.completed_snapshot = lambda _run_id: CompletedRunSnapshot(
        run_id=run_id,
        completed_at="2026-07-16T00:00:00+00:00",
        data_source="synthetic",
        metrics={},
        strategy_summary={"model_in_execution": False},
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/model/explanations",
            json={"run_id": run_id},
            headers=WRITE_HEADERS,
        )
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "MODEL_ERROR"


def test_completed_run_has_immutable_snapshot_and_activity(tmp_path: Path) -> None:
    app = create_app(make_project(tmp_path))
    with TestClient(app) as client:
        accepted = client.post("/api/v1/runs", headers=WRITE_HEADERS)
        assert accepted.status_code == 202
        run_id = accepted.json()["run_id"]
        concurrent = client.post("/api/v1/runs", headers=WRITE_HEADERS)
        assert concurrent.status_code == 409
        record = wait_for_completion(client, run_id)
        assert record["state"] == "succeeded", record.get("error_message")
        assert record["config_sha256"]
        assert record["data_sha256"]
        assert record["metrics"]["initial_equity"] == 100000.0
        equity = client.get(f"/api/v1/runs/{run_id}/equity").json()["items"]
        assert len(equity) > 100
        activity = client.get(
            "/api/v1/activity",
            params={"run_id": run_id, "kind": "orders", "limit": 10},
        )
        assert activity.status_code == 200
        assert (tmp_path / "output" / "runs" / run_id / "settings.snapshot.yaml").exists()

        # A completed run must always be explained with its own config snapshot.
        config_path = tmp_path / "config" / "settings.yaml"
        current = config_path.read_text(encoding="utf-8")
        config_path.write_text(
            current.replace("long_ma_window: 60", "long_ma_window: 5"),
            encoding="utf-8",
        )
        snapshot = app.state.dashboard_service.completed_snapshot(run_id)
        assert snapshot.strategy_summary["strategy"]["long_ma_window"] == 60

        workbench = client.get("/api/v1/workbench")
        assert workbench.status_code == 200
        payload = workbench.json()
        assert payload["source_nav_date"] is None
        assert payload["positions"] == []
        assert payload["discipline"]["meaning"].startswith("仅衡量")
        assert payload["discipline"]["score"] is None
        assert payload["discipline"]["dimensions"] == []
        assert payload["activity_summary"]["trade_count"] == 0
        availability = {item["key"]: item for item in payload["data_availability"]}
        assert availability["paper_ledger"]["state"] == "available"
        assert availability["configured_whitelist"]["source"] == "not_used"
        assert payload["can_submit_orders"] is False


def test_copilot_api_keeps_generation_memory_and_rating_local_only(tmp_path: Path) -> None:
    """Expose Copilot reads while protecting every model or memory write."""

    class CopilotApiStub:
        """Return contract-complete Copilot payloads without model network calls."""

        report_payload = {
            "report_id": "report-test",
            "run_id": "2026-08-08_cross-sectional-v2.0.0_dv-test_00000000",
            "agent_type": "risk",
            "report_type": "risk_alert",
            "subject_symbol": None,
            "model_version": "fake-v1",
            "prompt_version": "risk-prompt-v1.0.0",
            "evidence_ids": ["E-DC-MANIFEST"],
            "evidence_hash": "a" * 64,
            "content": {
                "headline": "风险提醒",
                "summary": "只解释证据。",
                "findings": [],
                "risks": [],
                "memory_candidates": [],
                "disclaimer": "不构成投资建议。",
                "can_trade": False,
                "used_for_execution": False,
            },
            "status": "published",
            "error_message": None,
            "created_time": "2026-08-08T00:00:00+00:00",
            "used_for_execution": False,
            "can_trade": False,
            "evaluation": {"grounded": True},
        }
        memory_payload = {
            "memory_id": "memory-test",
            "category": "lesson",
            "content": "只使用已保存证据。",
            "source": "user_confirmed",
            "source_report_id": None,
            "evidence_ids": [],
            "confidence": 1.0,
            "status": "confirmed",
            "created_time": "2026-08-08T00:00:00+00:00",
            "confirmed_time": "2026-08-08T00:00:00+00:00",
            "can_affect_execution": False,
        }

        def status(self):
            """Return independent readiness and safety flags."""
            return {"evidence_ready": True, "can_trade": False, "can_create_orders": False}

        def evidence(self, **_kwargs):
            """Return one read-only evidence preview."""
            return {"run_id": self.report_payload["run_id"], "can_trade": False}

        def generate(self, **_kwargs):
            """Return one published report."""
            return dict(self.report_payload)

        def reports(self, _limit):
            """List one report without regeneration."""
            return {"items": [dict(self.report_payload)], "can_trade": False, "used_for_execution": False}

        def report(self, _report_id):
            """Read one report."""
            return dict(self.report_payload)

        def add_user_memory(self, **_kwargs):
            """Return one explicit confirmed memory."""
            return dict(self.memory_payload)

        def memories(self, _limit):
            """List one non-executable memory."""
            return {"items": [dict(self.memory_payload)], "can_affect_execution": False}

        def confirm_memory(self, _memory_id):
            """Confirm one candidate memory."""
            return dict(self.memory_payload)

        def rate_report(self, _report_id, rating, note):
            """Return one human evaluation update."""
            return {"human_rating": rating, "human_note": note, "grounded": True}

    app = create_app(make_project(tmp_path), copilot_service=CopilotApiStub())
    with TestClient(app) as client:
        assert client.get("/api/v1/copilot/status").json()["can_trade"] is False
        evidence = client.get(
            "/api/v1/copilot/evidence",
            params={"report_type": "risk_alert"},
        )
        assert evidence.status_code == 200
        assert client.post(
            "/api/v1/copilot/reports", json={"report_type": "risk_alert"}
        ).status_code == 403
        report = client.post(
            "/api/v1/copilot/reports",
            json={"report_type": "risk_alert"},
            headers=WRITE_HEADERS,
        )
        assert report.status_code == 200
        assert report.json()["can_trade"] is False
        assert client.get("/api/v1/copilot/reports").json()["items"][0]["report_id"] == "report-test"
        memory = client.post(
            "/api/v1/copilot/memory",
            json={"category": "lesson", "content": "只使用已保存证据。"},
            headers=WRITE_HEADERS,
        )
        assert memory.status_code == 200
        assert memory.json()["can_affect_execution"] is False
        rating = client.post(
            "/api/v1/copilot/reports/report-test/rating",
            json={"rating": 5, "note": "引用清晰"},
            headers=WRITE_HEADERS,
        )
        assert rating.status_code == 200
        assert rating.json()["human_rating"] == 5


def test_data_intelligence_api_is_read_only_and_write_protected(tmp_path: Path) -> None:
    """Expose monitoring evidence while protecting explicit evaluation and acknowledgement."""

    class DataIntelligenceApiStub:
        calls = []
        safety = {
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_historical_data": False,
            "can_modify_strategy": False,
            "can_modify_factor_weights": False,
            "can_bypass_gate": False,
        }
        health_payload = {
            "service_version": "data-intelligence-platform-v1.0.0",
            "run_id": "run-test",
            "data_version": "dv-test",
            "research_date": "2026-08-10",
            "status": "NORMAL",
            "score": 100.0,
            "components": {},
            "issues": [],
            "blocking": False,
            "publish_allowed": True,
            "preview": False,
            "weights": {},
            "evidence": {},
            "safety": safety,
            **safety,
        }
        incident_payload = {
            "incident_id": "incident-test",
            "run_id": "run-test",
            "category": "market",
            "code": "TEST_WARNING",
            "level": "WARNING",
            "description": "测试事件",
            "evidence": {},
            "fingerprint": "f" * 64,
            "status": "ACKNOWLEDGED",
            "created_time": "2026-08-10T00:00:00+00:00",
            "acknowledged_time": "2026-08-10T00:01:00+00:00",
            "resolved_time": None,
            "can_trade": False,
            "can_create_orders": False,
            "can_bypass_gate": False,
        }

        def dashboard(self):
            self.calls.append("dashboard")
            return {
                "service_version": "data-intelligence-platform-v1.0.0",
                "generated_at": "2026-08-10T00:00:00+00:00",
                "data_center": {"available": True},
                "health": self.health_payload,
                "incidents": {"items": [], "counts": {"open": 0}},
                "catalog": [],
                "lineage": None,
                "counts": {"catalog_versions": 0, "open_incidents": 0,
                           "lineage_nodes": 0, "lineage_edges": 0},
                "safety": self.safety,
                **self.safety,
            }

        def evaluate_latest(self):
            self.calls.append("evaluate")
            return dict(self.health_payload)

        def evaluate_run(self, run_id):
            self.calls.append(f"evaluate:{run_id}")
            return dict(self.health_payload, run_id=run_id)

        def health(self, run_id=None):
            self.calls.append(f"health:{run_id or 'latest'}")
            return dict(self.health_payload, run_id=run_id or "run-test")

        def incidents(self, *, status=None, limit=100):
            self.calls.append(f"incidents:{status}:{limit}")
            return {"items": [], "counts": {}, "safety": self.safety}

        def acknowledge_incident(self, incident_id):
            self.calls.append(f"ack:{incident_id}")
            return dict(self.incident_payload, incident_id=incident_id)

        def catalog(self, limit):
            self.calls.append(f"catalog:{limit}")
            return {"items": [], "count": 0, "safety": self.safety}

        def lineage(self, run_id):
            self.calls.append(f"lineage:{run_id}")
            return {"run_id": run_id, "nodes": [], "edges": [], "safety": self.safety,
                    **self.safety}

    service = DataIntelligenceApiStub()
    app = create_app(make_project(tmp_path), data_intelligence_service=service)
    with TestClient(app) as client:
        dashboard = client.get("/api/v1/data-intelligence")
        assert dashboard.status_code == 200
        assert dashboard.json()["can_trade"] is False
        assert service.calls == ["dashboard"]
        assert client.post("/api/v1/data-intelligence/evaluate").status_code == 403
        evaluated = client.post(
            "/api/v1/data-intelligence/evaluate",
            headers=WRITE_HEADERS,
        )
        assert evaluated.status_code == 200
        assert evaluated.json()["can_modify_historical_data"] is False
        assert client.post(
            "/api/v1/data-intelligence/incidents/incident-test/acknowledge"
        ).status_code == 403
        acknowledged = client.post(
            "/api/v1/data-intelligence/incidents/incident-test/acknowledge",
            headers=WRITE_HEADERS,
        )
        assert acknowledged.status_code == 200
        assert acknowledged.json()["can_bypass_gate"] is False
        assert client.get("/api/v1/data-intelligence/lineage/run-test").status_code == 200


def test_strategy_evolution_api_separates_reads_from_human_governance(tmp_path: Path) -> None:
    """Keep health reads passive and protect every research/governance mutation."""

    class StrategyEvolutionApiStub:
        safety = {
            "can_trade": False,
            "can_create_orders": False,
            "can_modify_strategy": False,
            "can_modify_parameters": False,
            "can_modify_factor_weights": False,
            "can_replace_production_strategy": False,
            "can_auto_transition": False,
            "can_launch_experiment": False,
        }

        def __init__(self) -> None:
            self.calls: list[str] = []

        def dashboard(self) -> dict:
            self.calls.append("dashboard")
            return {
                "service_version": "strategy-evolution-engine-v1.0.0",
                "generated_at": "2026-08-11T00:00:00+00:00",
                "branches": [],
                "health_history": [],
                "comparisons": [],
                "lifecycle_history": [],
                "reports": [],
                "available_validation_strategies": [],
                "counts": {
                    "branches": 0, "health_snapshots": 0, "decaying": 0,
                    "comparisons": 0, "pending_transitions": 0,
                },
                "ai_strategy_observer": {
                    "can_launch_experiment": False,
                    "can_modify_strategy": False,
                },
                "safety": self.safety,
                **self.safety,
            }

        def import_branch(self, **payload) -> dict:
            self.calls.append(f"import:{payload['strategy_id']}:{payload['version']}")
            return {**payload, "lifecycle_state": "DRAFT", "safety": self.safety}

        def evaluate(self, **payload) -> dict:
            self.calls.append(f"evaluate:{payload['strategy_id']}:{payload['version']}")
            return {"health": {"status": "HEALTHY", "score": 88}, **self.safety}

        def health(self, strategy_id=None, limit=100) -> dict:
            self.calls.append(f"health:{strategy_id}:{limit}")
            return {"items": [], "count": 0, "safety": self.safety}

        def report(self, report_id) -> dict:
            self.calls.append(f"report:{report_id}")
            return {"report_id": report_id, "sections": {}, **self.safety}

        def compare(self, **payload) -> dict:
            self.calls.append(f"compare:{payload['version_a']}:{payload['version_b']}")
            return {"result": "DESCRIPTIVE_COMPARISON_ONLY", **self.safety}

        def comparisons(self, limit=100) -> dict:
            self.calls.append(f"comparisons:{limit}")
            return {"items": [], "count": 0, "safety": self.safety}

        def request_transition(self, **payload) -> dict:
            self.calls.append(f"request:{payload['target_state']}")
            return {"request_id": "transition-test", "status": "PENDING", **self.safety}

        def lifecycle_history(self, limit=100) -> dict:
            self.calls.append(f"lifecycle:{limit}")
            return {"items": [], "count": 0, "safety": self.safety}

        def decide_transition(self, request_id, *, approved, actor, reason) -> dict:
            self.calls.append(f"decide:{request_id}:{approved}:{actor}")
            return {"request_id": request_id, "status": "APPROVED" if approved else "REJECTED", **self.safety}

    service = StrategyEvolutionApiStub()
    app = create_app(make_project(tmp_path), strategy_evolution_service=service)
    with TestClient(app) as client:
        dashboard = client.get("/api/v1/strategy-evolution")
        assert dashboard.status_code == 200
        assert dashboard.json()["can_modify_strategy"] is False
        assert service.calls == ["dashboard"]

        body = {"strategy_id": "pangu-mf", "version": "v2.0.0", "review_id": None}
        assert client.post("/api/v1/strategy-evolution/evaluations", json=body).status_code == 403
        evaluated = client.post(
            "/api/v1/strategy-evolution/evaluations", json=body, headers=WRITE_HEADERS,
        )
        assert evaluated.status_code == 200
        assert evaluated.json()["can_trade"] is False

        request = client.post(
            "/api/v1/strategy-evolution/lifecycle/requests",
            json={
                "strategy_id": "pangu-mf", "version": "v2.0.0",
                "target_state": "RESEARCH", "requested_by": "研究员",
                "reason": "人工核对不可变研究证据后提交申请",
            },
            headers=WRITE_HEADERS,
        )
        assert request.status_code == 200
        assert request.json()["status"] == "PENDING"
        decision = client.post(
            "/api/v1/strategy-evolution/lifecycle/requests/transition-test/approve",
            json={"actor": "审批员", "reason": "人工确认研究条件与风险边界均已满足"},
            headers=WRITE_HEADERS,
        )
        assert decision.status_code == 200
        assert decision.json()["can_create_orders"] is False
        assert any(call.startswith("decide:transition-test:True") for call in service.calls)
