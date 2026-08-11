from datetime import date

import pytest

from ashare_agent.broker import MockBroker
from ashare_agent.models import OrderStatus, Position, Side
from ashare_agent.risk import RiskEngine


def build_broker():
    risk = RiskEngine(
        config={
            "kill_switch": False,
            "reject_unknown_symbols": True,
            "reject_duplicate_orders": True,
            "max_orders_per_day": 4,
            "max_order_value_pct": 0.9,
            "max_total_exposure_pct": 0.9,
            "max_single_position_pct": 0.9,
        },
        whitelist=["510300.SH"],
        initial_cash=1000,
    )
    return MockBroker(
        initial_cash=1000,
        risk_engine=risk,
        lot_size=100,
        commission_rate=0,
        minimum_commission=0,
        sell_tax_rate=0,
        slippage_rate=0,
    )


def test_buy_order_fills():
    broker = build_broker()
    order = broker.new_order(
        date(2026, 1, 2), "510300.SH", Side.BUY, 100, 3.0
    )
    order, trade = broker.submit(order, {"510300.SH": 3.0})
    assert order.status == OrderStatus.FILLED
    assert trade is not None
    assert broker.positions["510300.SH"].quantity == 100


def test_non_lot_order_rejected():
    broker = build_broker()
    order = broker.new_order(
        date(2026, 1, 2), "510300.SH", Side.BUY, 50, 3.0
    )
    order, trade = broker.submit(order, {"510300.SH": 3.0})
    assert order.status == OrderStatus.RISK_REJECTED
    assert trade is None


@pytest.mark.parametrize("price", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_requested_price_is_risk_rejected(price):
    broker = build_broker()
    order = broker.new_order(
        date(2026, 1, 2), "510300.SH", Side.BUY, 100, price
    )
    order, trade = broker.submit(order, {"510300.SH": 3.0})
    assert order.status == OrderStatus.RISK_REJECTED
    assert "委托价格" in order.reject_reason
    assert trade is None


@pytest.mark.parametrize("quote", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_current_quote_is_risk_rejected(quote):
    broker = build_broker()
    order = broker.new_order(
        date(2026, 1, 2), "510300.SH", Side.BUY, 100, 3.0
    )
    order, trade = broker.submit(order, {"510300.SH": quote})
    assert order.status == OrderStatus.RISK_REJECTED
    assert "行情价格" in order.reject_reason
    assert trade is None


def test_slippage_and_fee_are_rechecked_before_approval():
    risk = RiskEngine(
        config={
            "kill_switch": False,
            "reject_unknown_symbols": True,
            "reject_duplicate_orders": True,
            "max_orders_per_day": 4,
            "max_order_value_pct": 0.9,
            "max_total_exposure_pct": 0.9,
            "max_single_position_pct": 0.9,
        },
        whitelist=["510300.SH"],
        initial_cash=1000,
    )
    broker = MockBroker(
        initial_cash=1000,
        risk_engine=risk,
        lot_size=100,
        commission_rate=0,
        minimum_commission=2,
        sell_tax_rate=0,
        slippage_rate=0.02,
    )
    order = broker.new_order(
        date(2026, 1, 2), "510300.SH", Side.BUY, 100, 8.82
    )
    order, trade = broker.submit(order, {"510300.SH": 8.82})
    assert order.status == OrderStatus.RISK_REJECTED
    assert "单笔订单金额" in order.reject_reason
    assert trade is None
    assert broker.cash == 1000


def test_market_value_requires_a_valid_quote_for_each_position():
    broker = build_broker()
    broker.positions["510300.SH"] = Position("510300.SH", quantity=100)
    with pytest.raises(ValueError, match="持仓缺少"):
        broker.market_value({})
