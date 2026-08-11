from datetime import date

import pytest

from ashare_agent.models import Order, Side
from ashare_agent.risk import RiskEngine


def make_order(symbol="510300.SH", qty=100, price=3.0):
    return Order(
        client_order_id="test-1",
        trade_date=date(2026, 1, 2),
        symbol=symbol,
        side=Side.BUY,
        quantity=qty,
        requested_price=price,
    )


def base_config(kill_switch=False):
    return {
        "kill_switch": kill_switch,
        "reject_unknown_symbols": True,
        "reject_duplicate_orders": True,
        "max_orders_per_day": 4,
        "max_order_value_pct": 0.9,
        "max_total_exposure_pct": 0.9,
        "max_single_position_pct": 0.9,
    }


def test_unknown_symbol_is_rejected():
    risk = RiskEngine(
        config=base_config(),
        whitelist=["510300.SH"],
        initial_cash=1000,
    )
    decision = risk.check(
        make_order(symbol="UNKNOWN"),
        cash=1000,
        current_market_value=0,
        current_symbol_value=0,
    )
    assert not decision.approved


def test_kill_switch_blocks_order():
    risk = RiskEngine(
        config=base_config(kill_switch=True),
        whitelist=["510300.SH"],
        initial_cash=1000,
    )
    decision = risk.check(
        make_order(),
        cash=1000,
        current_market_value=0,
        current_symbol_value=0,
    )
    assert not decision.approved


def test_sell_is_not_blocked_by_buy_order_value_cap():
    """Risk reduction must remain possible when a position is oversized."""
    config = base_config()
    config["max_order_value_pct"] = 0.01
    risk = RiskEngine(config=config, whitelist=["510300.SH"], initial_cash=1000)
    order = make_order(qty=100, price=3.0)
    order.side = Side.SELL
    decision = risk.check(
        order,
        cash=0,
        current_market_value=1000,
        current_symbol_value=1000,
    )
    assert decision.approved


@pytest.mark.parametrize("price", [0.0, -1.0, float("nan"), float("inf")])
def test_non_finite_or_non_positive_requested_price_is_rejected(price):
    risk = RiskEngine(base_config(), ["510300.SH"], 1000)
    decision = risk.check(
        make_order(price=price),
        cash=1000,
        current_market_value=0,
        current_symbol_value=0,
    )
    assert not decision.approved
    assert "委托价格" in decision.reason


def test_invalid_estimated_execution_price_is_rejected():
    risk = RiskEngine(base_config(), ["510300.SH"], 1000)
    decision = risk.check(
        make_order(),
        cash=1000,
        current_market_value=0,
        current_symbol_value=0,
        estimated_execution_price=float("nan"),
    )
    assert not decision.approved
    assert "预估成交价格" in decision.reason


def test_fee_is_included_in_buy_order_limit_and_cash_check():
    risk = RiskEngine(base_config(), ["510300.SH"], 1000)
    decision = risk.check(
        make_order(price=8.8),
        cash=1000,
        current_market_value=0,
        current_symbol_value=0,
        estimated_execution_price=8.8,
        estimated_fee=30,
    )
    assert not decision.approved
    assert "单笔订单金额" in decision.reason
