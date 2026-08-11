from datetime import date, datetime
from zoneinfo import ZoneInfo

from ashare_agent.paper_portfolio import PaperPortfolio


CONFIG = {
    "initial_cash": 100_000.0, "max_positions": 3,
    "target_position_pct": 0.20, "max_total_exposure_pct": 0.60,
    "stop_loss_pct": 0.08, "max_drawdown_pct": 0.10,
}
COSTS = {"commission_rate": 0.0003, "minimum_commission": 5.0, "sell_tax_rate": 0.0005, "slippage_rate": 0.001}


def plan(symbols=("600000.SH", "600001.SH", "600002.SH", "600003.SH")):
    return {
        "execution_ready": True, "research_date": "2026-07-20",
        "targets": list(symbols[:3]),
        "candidates": [{"symbol": symbol, "name": symbol} for symbol in symbols],
    }


def quotes(symbols=("600000.SH", "600001.SH", "600002.SH", "600003.SH"), price=10.0):
    return {symbol: {"last_price": price, "volume": 1_000_000, "price_change_ratio_pct": 1.0} for symbol in symbols}


def test_paper_buy_is_round_lot_idempotent_and_persistent(tmp_path):
    db = tmp_path / "paper.db"
    portfolio = PaperPortfolio(db, CONFIG, COSTS)
    first = portfolio.execute_plan(plan(), quotes(), date(2026, 7, 21))
    second = portfolio.execute_plan(plan(), quotes(), date(2026, 7, 21))
    assert len([order for order in first["orders"] if order["status"] == "FILLED"]) == 3
    assert second["orders"] == []
    assert all(position["quantity"] % 100 == 0 for position in portfolio.positions())
    assert first["account"]["market_value"] <= first["account"]["equity"] * 0.600001
    portfolio.close()
    reopened = PaperPortfolio(db, CONFIG, COSTS)
    assert len(reopened.positions()) == 3
    assert len(reopened.activity()["trades"]) == 3
    reopened.close()


def test_t1_stop_is_deferred_then_sold_before_any_rebuy(tmp_path):
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    portfolio.execute_plan(plan(), quotes(), date(2026, 7, 21))
    observed = datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    events = portfolio.monitor(quotes(price=8.9), observed)
    assert {event["event_type"] for event in events} == {"STOP_TRIGGERED", "EXIT_DEFERRED_T1"}
    next_day = portfolio.execute_plan(plan(), quotes(price=8.9), date(2026, 7, 22))
    assert all(order["side"] == "SELL" for order in next_day["orders"])
    assert portfolio.positions() == []
    portfolio.close()


def test_drawdown_blocks_new_positions(tmp_path):
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    portfolio.execute_plan(plan(), quotes(), date(2026, 7, 21))
    portfolio.mark_to_market({symbol: 1.0 for symbol in quotes()}, date(2026, 7, 22))
    portfolio.conn.execute("DELETE FROM paper_positions")
    portfolio.conn.commit()
    result = portfolio.execute_plan(plan(), quotes(), date(2026, 7, 22))
    assert result["allow_buys"] is False
    assert portfolio.positions() == []
    portfolio.close()


def test_manual_buy_is_idempotent_and_enforces_single_position_cap(tmp_path):
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    first = portfolio.manual_buy(
        symbol="600000.SH", name="股票0", quantity=100,
        quotes=quotes(), trade_date=date(2026, 7, 21), request_key="request-1",
    )
    duplicate = portfolio.manual_buy(
        symbol="600000.SH", name="股票0", quantity=100,
        quotes=quotes(), trade_date=date(2026, 7, 21), request_key="request-1",
    )
    oversized = portfolio.manual_buy(
        symbol="600001.SH", name="股票1", quantity=2100,
        quotes=quotes(), trade_date=date(2026, 7, 21), request_key="request-2",
    )
    assert first["order"]["status"] == "FILLED"
    assert duplicate["order"]["client_order_id"] == first["order"]["client_order_id"]
    assert oversized["order"]["status"] == "RISK_REJECTED"
    assert "20%" in oversized["order"]["reason"]
    assert len(portfolio.activity()["trades"]) == 1
    portfolio.close()


def test_manual_sell_includes_costs_and_respects_t1(tmp_path):
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    bought = portfolio.manual_buy(
        symbol="600000.SH", name="股票0", quantity=100,
        quotes=quotes(), trade_date=date(2026, 7, 21), request_key="buy",
    )
    assert bought["account"]["positions"][0]["average_cost"] > 10.01
    blocked = portfolio.manual_sell(
        symbol="600000.SH", name="股票0", quantity=100,
        quotes=quotes(), trade_date=date(2026, 7, 21), request_key="sell-t0",
    )
    assert blocked["order"]["status"] == "RISK_REJECTED"
    sold = portfolio.manual_sell(
        symbol="600000.SH", name="股票0", quantity=100,
        quotes=quotes(), trade_date=date(2026, 7, 22), request_key="sell-t1",
    )
    assert sold["order"]["status"] == "FILLED"
    trade = portfolio.activity()["trades"][0]
    assert trade["side"] == "SELL"
    assert trade["stamp_tax"] > 0
    assert trade["realized_pnl"] < 0
    portfolio.close()


def test_review_contains_only_actual_positions_and_persisted_activity(tmp_path):
    """Build recap evidence from MockBroker state without any configured universe."""
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    portfolio.manual_buy(
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
        request_key="review-only-actual",
    )
    review = portfolio.review()
    assert [item["symbol"] for item in review["positions"]] == ["600519.SH"]
    assert review["positions"][0]["name"] == "贵州茅台"
    assert review["positions"][0]["valuation_source"] == "latest_paper_nav"
    assert len(review["activity"]["orders"]) == 1
    assert len(review["activity"]["trades"]) == 1
    assert review["source_nav_date"] == "2026-07-21"
    portfolio.close()


def test_manual_order_assessment_is_read_only_and_exposes_costs(tmp_path):
    """Preview one paper order without writing orders or trades."""
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    preview = portfolio.assess_manual_order(
        side="BUY",
        symbol="600000.SH",
        name="股票0",
        quantity=100,
        quotes=quotes(),
        trade_date=date(2026, 7, 21),
    )
    assert preview["allowed"] is True
    assert abs(preview["estimated_fill_price"] - 10.01) < 1e-9
    assert preview["fee_breakdown"]["commission"] == 5.0
    assert preview["after"]["cash"] < preview["before"]["cash"]
    assert preview["after"]["exposure_ratio"] > 0
    assert portfolio.activity()["orders"] == []
    assert portfolio.activity()["trades"] == []
    portfolio.close()


def test_exact_odd_lot_liquidation_is_allowed_but_partial_odd_sell_is_rejected(tmp_path):
    """Allow only a reduce-only exact odd-lot liquidation after T+1."""
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    portfolio.manual_buy(
        symbol="600000.SH", name="股票0", quantity=100,
        quotes=quotes(), trade_date=date(2026, 7, 21), request_key="odd-buy",
    )
    portfolio.conn.execute(
        "UPDATE paper_positions SET quantity=150,available_quantity=150 WHERE symbol=?",
        ("600000.SH",),
    )
    portfolio.conn.commit()
    partial = portfolio.manual_sell(
        symbol="600000.SH", name="股票0", quantity=50,
        quotes=quotes(), trade_date=date(2026, 7, 22), request_key="odd-partial",
    )
    assert partial["order"]["status"] == "RISK_REJECTED"
    assert "仅清仓时" in partial["order"]["reason"]
    full = portfolio.manual_sell(
        symbol="600000.SH", name="股票0", quantity=150,
        quotes=quotes(), trade_date=date(2026, 7, 22), request_key="odd-full",
    )
    assert full["order"]["status"] == "FILLED"
    assert portfolio.positions() == []
    portfolio.close()


def test_review_exposes_live_valuation_pnl_attribution_and_risk_evidence(tmp_path):
    """Reconcile fresh valuation, fees and symbol-level PnL in one review."""
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    portfolio.manual_buy(
        symbol="600000.SH", name="股票0", quantity=100,
        quotes=quotes(), trade_date=date(2026, 7, 21), request_key="review-buy",
    )
    review = portfolio.review(
        quotes={"600000.SH": 11.0},
        valuation={
            "source": "ths_finance_snapshot",
            "observed_at": "2026-07-22T10:00:00+08:00",
            "age_seconds": 1.0,
            "stale": False,
            "message": "同花顺轮询快照",
        },
    )
    position = review["positions"][0]
    account = review["account"]
    assert position["valuation_source"] == "ths_polling_snapshot"
    assert position["holding_days"] == 1
    assert position["stop_price"] < position["average_cost"]
    assert position["unrealized_pnl"] > 0
    assert account["unrealized_pnl"] == position["unrealized_pnl"]
    assert abs(account["pnl_reconciliation_gap"]) < 1e-6
    assert review["fee_breakdown"]["total"] > 0
    assert review["symbol_attribution"][0]["symbol"] == "600000.SH"
    assert review["symbol_attribution"][0]["total_pnl"] > 0
    assert review["performance"]["sell_trade_count"] == 0
    assert review["valuation"]["stale"] is False
    portfolio.close()


def test_limit_buy_freezes_cash_then_matches_a_reached_snapshot(tmp_path):
    """Keep a DAY limit order pending until a fresh snapshot reaches its price."""
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    observed = datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    placed = portfolio.place_broker_order(
        side="BUY",
        symbol="600000.SH",
        name="股票0",
        quantity=100,
        order_type="LIMIT",
        limit_price=9.50,
        quotes=quotes(price=10.0),
        trade_date=observed.date(),
        request_key="limit-buy",
        observed_at=observed,
    )
    assert placed["order"]["status"] == "PENDING"
    assert placed["trade"] is None
    assert placed["account"]["frozen_cash"] > 950
    assert placed["account"]["available_cash"] < placed["account"]["cash"]

    waiting = portfolio.match_broker_orders(quotes(price=9.60), observed)
    assert waiting["matched"] == []
    filled = portfolio.match_broker_orders(quotes(price=9.40), observed)
    assert filled["matched"][0]["status"] == "FILLED"
    assert filled["account"]["frozen_cash"] == 0
    assert len(portfolio.activity()["trades"]) == 1
    portfolio.close()


def test_cancel_releases_frozen_cash_and_is_idempotent(tmp_path):
    """Release reservations when a local pending order is cancelled."""
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    observed = datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    placed = portfolio.place_broker_order(
        side="BUY",
        symbol="600000.SH",
        name="股票0",
        quantity=100,
        order_type="LIMIT",
        limit_price=9.00,
        quotes=quotes(price=10.0),
        trade_date=observed.date(),
        request_key="cancel-buy",
        observed_at=observed,
    )
    cancelled = portfolio.cancel_broker_order(placed["order"]["client_order_id"], observed)
    replay = portfolio.cancel_broker_order(placed["order"]["client_order_id"], observed)
    assert cancelled["status"] == replay["status"] == "CANCELLED"
    assert portfolio.snapshot()["frozen_cash"] == 0
    assert portfolio.activity()["trades"] == []
    portfolio.close()


def test_pending_sell_freezes_sellable_quantity_without_breaking_t1(tmp_path):
    """Reserve only T+1-available shares and prevent a second oversell order."""
    portfolio = PaperPortfolio(tmp_path / "paper.db", CONFIG, COSTS)
    portfolio.manual_buy(
        symbol="600000.SH", name="股票0", quantity=200,
        quotes=quotes(), trade_date=date(2026, 7, 21), request_key="sell-freeze-buy",
    )
    observed = datetime(2026, 7, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    placed = portfolio.place_broker_order(
        side="SELL",
        symbol="600000.SH",
        name="股票0",
        quantity=100,
        order_type="LIMIT",
        limit_price=11.00,
        quotes=quotes(price=10.0),
        trade_date=observed.date(),
        request_key="limit-sell",
        observed_at=observed,
    )
    assert placed["order"]["status"] == "PENDING"
    position = placed["account"]["positions"][0]
    assert position["frozen_quantity"] == 100
    assert position["sellable_quantity"] == 100
    oversell = portfolio.place_broker_order(
        side="SELL",
        symbol="600000.SH",
        name="股票0",
        quantity=200,
        order_type="LIMIT",
        limit_price=11.00,
        quotes=quotes(price=10.0),
        trade_date=observed.date(),
        request_key="oversell",
        observed_at=observed,
    )
    assert oversell["order"]["status"] == "RISK_REJECTED"
    assert len(portfolio.activity()["trades"]) == 1
    portfolio.close()
