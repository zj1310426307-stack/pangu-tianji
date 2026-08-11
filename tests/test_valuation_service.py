from __future__ import annotations

import pytest

from ashare_agent.services.valuation_service import ValuationService


def test_valuation_service_is_the_single_asset_math_contract():
    """Reconcile cash, market value, equity, PnL and drawdown in one result."""
    service = ValuationService(100_000.0)
    result = service.value_account(
        account={"cash": 80_000.0, "peak_equity": 110_000.0},
        positions=[{
            "symbol": "600000.SH",
            "name": "浦发银行",
            "quantity": 100,
            "available_quantity": 100,
            "average_cost": 100.0,
        }],
        prices={"600000.SH": 90.0},
        frozen_cash=1_000.0,
        frozen_by_symbol={"600000.SH": 20},
        trades=[{"realized_pnl": -10_000.0, "fee": 20.0, "gross_value": 30_000.0}],
        nav=[{"trade_date": "2026-07-19", "equity": 95_000.0, "drawdown": -0.15}],
    )
    value = result["asset_valuation"]

    assert value["service_version"] == "valuation-v1.0.0"
    assert value["cash"] == 80_000.0
    assert value["available_cash"] == 79_000.0
    assert value["market_value"] == 9_000.0
    assert value["equity"] == 89_000.0
    assert value["pnl"] == -11_000.0
    assert value["realized_pnl"] == -10_000.0
    assert value["unrealized_pnl"] == -1_000.0
    assert value["pnl_reconciliation_gap"] == pytest.approx(0.0)
    assert value["drawdown"] == pytest.approx(89_000 / 110_000 - 1)
    assert value["max_drawdown"] == pytest.approx(89_000 / 110_000 - 1)
    assert result["positions"][0]["sellable_quantity"] == 80
    assert result["positions"][0]["weight"] == pytest.approx(9_000 / 89_000)
    assert result["equity_curve"][-1]["valuation_point"] is True
    assert result["equity_curve"][-1]["equity"] == 89_000.0


def test_price_cache_is_scoped_to_provider_and_exact_holdings():
    """A changed symbol set must not reuse another page's cached prices."""
    calls: list[tuple[str, ...]] = []

    def provider(symbols: list[str]) -> dict:
        calls.append(tuple(symbols))
        return {
            "prices": {symbol: 10.0 for symbol in symbols},
            "source": "test",
            "observed_at": None,
            "age_seconds": 0.0,
            "stale": False,
            "message": "test",
        }

    service = ValuationService(100_000.0, provider)
    service.market_prices(["600000.SH"])
    service.market_prices(["600000.SH"])
    service.market_prices(["600001.SH"])

    assert calls == [("600000.SH",), ("600001.SH",)]


def test_buying_power_is_calculated_by_backend_service():
    """The UI receives a round-lot capacity and performs no asset arithmetic."""
    assert ValuationService.round_lot_buying_power(10_000.0, 12.5) == 800
    assert ValuationService.round_lot_buying_power(99.0, 12.5) == 0
