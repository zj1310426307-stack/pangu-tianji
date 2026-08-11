from datetime import datetime
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

import yaml

from ashare_agent.services.daily_research_service import DailyResearchService


class ExecutionClient:
    def __init__(self, now):
        self.now = now

    def trading_days(self):
        return {"2026-07-20", "2026-07-21"}

    def tickers(self):
        """Return a bounded main-board catalog for quote and paper-order tests."""
        return [
            {"thscode": f"60000{i}.SH", "ticker": f"60000{i}", "name": f"股票{i}"}
            for i in range(5)
        ]

    def snapshot(self, symbols=None):
        return {
            "timestamp": int(self.now().timestamp() * 1000),
            "item": [
                {
                    "thscode": symbol, "last_price": 10.0, "price_change": 0.1,
                    "price_change_ratio_pct": 1.0, "open_price": 9.9,
                    "high_price": 10.1, "low_price": 9.8, "prev_price": 9.9,
                    "volume": 1_000_000, "turnover": 10_000_000,
                }
                for symbol in symbols
            ],
        }


def make_project(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1]
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    shutil.copy2(source / "config" / "settings.yaml", root / "config" / "settings.yaml")
    raw = yaml.safe_load((root / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw["costs"]["minimum_commission"] = 5.0
    (root / "config" / "settings.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    research = root / "output" / "research"
    research.mkdir(parents=True)
    candidates = [{"symbol": f"60000{i}.SH", "name": f"股票{i}"} for i in range(5)]
    plan = {
        "research_date": "2026-07-20", "generated_at": "2026-07-20T07:11:00+00:00",
        "execution_ready": True,
        "targets": [item["symbol"] for item in candidates[:3]], "candidates": candidates,
    }
    (research / "latest.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (research / "status.json").write_text(json.dumps({"research_date":"2026-07-20","state":"succeeded"}), encoding="utf-8")
    return root


def test_only_previous_trading_day_plan_executes_in_window(tmp_path):
    clock = [datetime(2026, 7, 21, 9, 35, tzinfo=ZoneInfo("Asia/Shanghai"))]
    service = DailyResearchService(make_project(tmp_path), client=ExecutionClient(lambda: clock[0]), now_provider=lambda: clock[0])
    result = service.execute()
    # The 25% daily-turnover budget intentionally prevents the initial
    # portfolio from filling every 15% target in one session.
    assert len([order for order in result["orders"] if order["status"] == "FILLED"]) == 2
    clock[0] = datetime(2026, 7, 21, 9, 46, tzinfo=ZoneInfo("Asia/Shanghai"))
    try:
        service.execute()
    except RuntimeError as exc:
        assert "执行窗口" in str(exc)
    else:
        raise AssertionError("expired execution window was accepted")
    service.close()


def test_dashboard_does_not_label_an_old_plan_as_execution_ready(tmp_path):
    clock = datetime(2026, 7, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    client = ExecutionClient(lambda: clock)
    client.trading_days = lambda: {"2026-07-20", "2026-07-21", "2026-07-22"}
    service = DailyResearchService(make_project(tmp_path), client=client, now_provider=lambda: clock)
    dashboard = service.dashboard()
    assert dashboard["plan_state"] == "expired"
    assert dashboard["automation_execution_ready"] is False
    service.close()


def test_dashboard_composes_portfolio_risk_center_from_canonical_valuation(tmp_path):
    """Combine target risk with ValuationService output without frontend arithmetic."""
    clock = datetime(2026, 7, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    root = make_project(tmp_path)
    path = root / "output" / "research" / "latest.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["target_portfolio"] = {
        "source_run_id": "test-run",
        "positions": [{"symbol": "600000.SH", "target_weight": 0.10}],
    }
    plan["portfolio_risk"] = {
        "risk_flags": [],
        "security_risks": [{"symbol": "600000.SH", "risk_score": 25}],
    }
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    service = DailyResearchService(
        root,
        client=ExecutionClient(lambda: clock),
        now_provider=lambda: clock,
    )
    center = service.dashboard()["portfolio_risk_center"]
    assert center["risk_assessment"]["drawdown_source"] == "valuation-v1.0.0"
    assert center["rebalance_actions"][0]["action"] == "INCREASE"
    assert center["exit_plan"]["creates_orders"] is False
    assert center["creates_orders"] is False
    service.close()


def test_paper_workflow_risk_snapshot_is_persisted_without_creating_orders(tmp_path):
    """Persist an account-aware risk point through the separate portfolio catalog."""
    clock = datetime(2026, 7, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    root = make_project(tmp_path)
    service = DailyResearchService(
        root,
        client=ExecutionClient(lambda: clock),
        now_provider=lambda: clock,
    )
    profile = service.investment_profile.to_dict()
    target = {
        "service_version": "portfolio-service-v1.1.0",
        "positioning_model_version": "score-risk-sizing-v1.0.0",
        "generated_at": "2026-07-20T07:11:00+00:00",
        "source_run_id": "test-run",
        "data_version": "test-data",
        "strategy_version": "cross-sectional-v2.0.0",
        "factor_version": "cross-sectional-v1.0.0",
        "factor_contract_hash": "test-hash",
        "profile": profile,
        "target_exposure": 0.0,
        "cash_reserve_weight": 1.0,
        "positions": [],
        "execution_authorized": False,
    }
    risk = {
        "source_run_id": "test-run",
        "security_risks": [],
        "weighted_security_risk": 0.0,
        "industry_exposure": {},
        "style_exposure": {},
        "size_exposure": {},
        "cycle_exposure": {},
        "concentration": {},
        "portfolio_volatility_proxy": 0.0,
        "predicted_max_drawdown": 0.0,
        "max_drawdown": None,
        "current_drawdown": None,
        "drawdown_source": "ValuationService_required",
        "risk_flags": [],
    }
    planned = service.portfolio_store.save_research_snapshot(target, risk)
    plan_path = root / "output" / "research" / "latest.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan.update({"target_portfolio": target, "portfolio_risk": risk})
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    runtime = service._record_portfolio_risk("monitor", plan)
    assert runtime["state"] == "saved"
    persisted = service.portfolio_store.portfolio(planned["portfolio_id"])
    assert len(persisted["risk_snapshots"]) == 2
    assert all(item["creates_orders"] is False for item in [service.dashboard()["portfolio_risk_center"]])
    service.close()


def test_investment_profile_tightens_existing_paper_hard_limits(tmp_path):
    """Apply the stricter profile cap without modifying PaperPortfolio formulas."""
    clock = datetime(2026, 7, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    root = make_project(tmp_path)
    path = root / "config" / "settings.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["investment_profile"].update({
        "risk_level": "conservative",
        "max_drawdown_tolerance": 0.05,
    })
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    service = DailyResearchService(
        root,
        client=ExecutionClient(lambda: clock),
        now_provider=lambda: clock,
    )
    limits = service.paper.snapshot()["limits"]
    assert limits["target_position_pct"] == 0.08
    assert limits["max_total_exposure_pct"] == 0.20
    assert limits["max_drawdown_pct"] == 0.05
    service.close()


def test_manual_candidate_refresh_is_separate_from_formal_plan(tmp_path):
    """The service may publish a current preview without touching the executable plan."""
    clock = datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    root = make_project(tmp_path)
    formal_before = (root / "output" / "research" / "latest.json").read_text(encoding="utf-8")
    service = DailyResearchService(root, client=ExecutionClient(lambda: clock), now_provider=lambda: clock)

    def fake_run(as_of, force=False, preview=False):
        payload = {
            "research_date": as_of.isoformat(), "mode": "intraday_preview",
            "observed_at": clock.isoformat(), "execution_ready": False,
            "used_for_execution": False, "targets": [], "candidates": [],
        }
        path = root / "output" / "research" / "preview.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert force is True and preview is True
        return payload

    service.research.run = fake_run
    preview = service.refresh_preview()
    dashboard = service.dashboard()

    assert preview["used_for_execution"] is False
    assert dashboard["preview_state"] == "current"
    assert dashboard["latest_preview"]["mode"] == "intraday_preview"
    assert (root / "output" / "research" / "latest.json").read_text(encoding="utf-8") == formal_before
    service.close()


def test_old_plan_is_never_backfilled(tmp_path):
    clock = datetime(2026, 7, 21, 9, 35, tzinfo=ZoneInfo("Asia/Shanghai"))
    root = make_project(tmp_path)
    path = root / "output" / "research" / "latest.json"
    plan = json.loads(path.read_text(encoding="utf-8")); plan["research_date"] = "2026-07-17"
    path.write_text(json.dumps(plan), encoding="utf-8")
    service = DailyResearchService(root, client=ExecutionClient(lambda: clock), now_provider=lambda: clock)
    try:
        service.execute()
    except RuntimeError as exc:
        assert "旧计划" in str(exc)
    else:
        raise AssertionError("stale plan was accepted")
    service.close()


def test_live_quote_and_manual_buy_share_fresh_server_side_snapshot(tmp_path):
    clock = datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    service = DailyResearchService(
        make_project(tmp_path),
        client=ExecutionClient(lambda: clock),
        now_provider=lambda: clock,
    )
    quote = service.quote("600000.SH")
    assert quote["feed_type"] == "polling_snapshot"
    assert quote["can_submit_paper_order"] is True
    first = service.manual_buy("600000.SH", 100, "stable-request")
    second = service.manual_buy("600000.SH", 100, "stable-request")
    assert first["order"]["status"] == "FILLED"
    assert second["order"]["client_order_id"] == first["order"]["client_order_id"]
    assert first["account"]["positions"][0]["available_quantity"] == 0
    assert len(service.paper.activity()["trades"]) == 1
    service.close()


def test_manual_sell_is_t1_reduce_only_and_idempotent(tmp_path):
    clock = [datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))]
    service = DailyResearchService(
        make_project(tmp_path),
        client=ExecutionClient(lambda: clock[0]),
        now_provider=lambda: clock[0],
    )
    service.manual_buy("600000.SH", 100, "buy-first")
    blocked = service.manual_sell("600000.SH", 100, "sell-t0")
    assert blocked["order"]["status"] == "RISK_REJECTED"
    assert "T+1" in blocked["order"]["reason"]

    clock[0] = datetime(2026, 7, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    service._trading_day_cache = None
    service.client.trading_days = lambda: {"2026-07-20", "2026-07-21", "2026-07-22"}
    first = service.manual_sell("600000.SH", 100, "sell-next-day")
    duplicate = service.manual_sell("600000.SH", 100, "sell-next-day")
    assert first["order"]["status"] == "FILLED"
    assert duplicate["order"]["client_order_id"] == first["order"]["client_order_id"]
    assert service.paper.positions() == []
    service.close()


def test_manual_buy_is_blocked_outside_market_session(tmp_path):
    clock = datetime(2026, 7, 21, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    service = DailyResearchService(
        make_project(tmp_path),
        client=ExecutionClient(lambda: clock),
        now_provider=lambda: clock,
    )
    quote = service.quote("600000.SH")
    assert quote["market_open"] is False
    assert quote["can_submit_paper_order"] is False
    try:
        service.manual_buy("600000.SH", 100, "outside-session")
    except RuntimeError as exc:
        assert "交易日" in str(exc)
    else:
        raise AssertionError("outside-session paper buy was accepted")
    service.close()
