from __future__ import annotations

from datetime import datetime, timezone
import math
from time import monotonic
from typing import Any, Callable, Iterable, Mapping


VALUATION_SERVICE_VERSION = "valuation-v1.0.0"


class ValuationService:
    """Own every paper-account asset valuation and PnL reconciliation formula.

    The service is deliberately independent from SQLite, HTTP and UI code. Callers
    provide ledger rows and prices; this class returns the single authoritative
    cash, market value, equity, PnL and drawdown contract used by every page.
    """

    def __init__(
        self,
        initial_cash: float,
        price_provider: Callable[[list[str]], dict[str, Any]] | None = None,
        cache_seconds: float = 5.0,
    ) -> None:
        if not math.isfinite(float(initial_cash)) or float(initial_cash) <= 0:
            raise ValueError("ValuationService initial_cash must be positive")
        self.initial_cash = float(initial_cash)
        self.price_provider = price_provider
        self.cache_seconds = max(0.0, float(cache_seconds))
        self._price_cache: tuple[tuple[str, ...], int, float, dict[str, Any]] | None = None

    def market_prices(
        self,
        symbols: Iterable[str],
        provider: Callable[[list[str]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Fetch and cache one normalized holdings-price snapshot.

        The cache key includes the exact symbol set and provider identity, so a
        position change cannot accidentally reuse a prior holdings snapshot.
        Failures are labeled and return no invented prices.
        """
        normalized = tuple(sorted({str(symbol).strip().upper() for symbol in symbols if symbol}))
        source = provider or self.price_provider
        if not normalized or source is None:
            return {
                "prices": {},
                "source": "paper_nav",
                "observed_at": None,
                "age_seconds": None,
                "stale": True,
                "message": "没有实际持仓，或未配置持仓行情读取器",
            }
        now = monotonic()
        provider_id = id(source)
        if self._price_cache:
            cached_symbols, cached_provider, cached_at, payload = self._price_cache
            if (
                cached_symbols == normalized
                and cached_provider == provider_id
                and now - cached_at <= self.cache_seconds
            ):
                return payload
        try:
            payload = source(list(normalized))
            if not isinstance(payload, dict) or not isinstance(payload.get("prices"), dict):
                raise RuntimeError("持仓估值响应结构无效")
            validated = dict(payload)
            validated["prices"] = self._valid_prices(payload["prices"])
            self._price_cache = (normalized, provider_id, now, validated)
            return validated
        except Exception as exc:
            return {
                "prices": {},
                "source": "paper_nav",
                "observed_at": None,
                "age_seconds": None,
                "stale": True,
                "message": f"实时估值失败，已回退最近模拟净值：{str(exc)[:160]}",
            }

    @staticmethod
    def _valid_prices(prices: Mapping[str, Any]) -> dict[str, float]:
        """Reject invalid supplied prices instead of converting them to zero."""
        result: dict[str, float] = {}
        for symbol, raw_price in prices.items():
            price = float(raw_price)
            if not math.isfinite(price) or price <= 0:
                raise RuntimeError(f"{symbol}持仓估值价格无效")
            result[str(symbol).upper()] = price
        return result

    def value_account(
        self,
        *,
        account: Mapping[str, Any],
        positions: Iterable[Mapping[str, Any]],
        prices: Mapping[str, Any] | None = None,
        frozen_cash: float = 0.0,
        frozen_by_symbol: Mapping[str, int] | None = None,
        trades: Iterable[Mapping[str, Any]] = (),
        nav: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Return the canonical account valuation and position valuation rows."""
        valid_prices = self._valid_prices(prices or {})
        frozen_by_symbol = frozen_by_symbol or {}
        valued_positions: list[dict[str, Any]] = []
        market_value = 0.0
        unrealized_pnl = 0.0
        for raw_position in positions:
            position = dict(raw_position)
            symbol = str(position["symbol"]).upper()
            quantity = int(position["quantity"])
            average_cost = float(position["average_cost"])
            price = valid_prices.get(symbol, average_cost)
            position_market_value = price * quantity
            position_unrealized = (price - average_cost) * quantity
            frozen_quantity = int(frozen_by_symbol.get(symbol, 0))
            position.update({
                "symbol": symbol,
                "last_price": price,
                "market_value": position_market_value,
                "unrealized_pnl": position_unrealized,
                "unrealized_pnl_pct": (
                    price / average_cost - 1 if average_cost > 0 else 0.0
                ),
                "frozen_quantity": frozen_quantity,
                "sellable_quantity": max(
                    0, int(position.get("available_quantity") or 0) - frozen_quantity
                ),
            })
            valued_positions.append(position)
            market_value += position_market_value
            unrealized_pnl += position_unrealized

        cash = float(account["cash"])
        equity = cash + market_value
        peak_equity = max(float(account.get("peak_equity") or 0.0), equity)
        drawdown = equity / peak_equity - 1 if peak_equity > 0 else 0.0
        for position in valued_positions:
            position["weight"] = (
                float(position["market_value"]) / equity if equity > 0 else 0.0
            )

        trade_rows = [dict(item) for item in trades]
        realized_pnl = sum(float(item.get("realized_pnl") or 0.0) for item in trade_rows)
        total_fees = sum(float(item.get("fee") or 0.0) for item in trade_rows)
        turnover = sum(float(item.get("gross_value") or 0.0) for item in trade_rows)
        pnl = equity - self.initial_cash
        reconciliation_gap = pnl - realized_pnl - unrealized_pnl
        drawdown_history = [
            float(item.get("drawdown") or 0.0) for item in nav
        ] + [drawdown]
        available_cash = max(0.0, cash - float(frozen_cash))
        summary = {
            "service_version": VALUATION_SERVICE_VERSION,
            "valued_at": datetime.now(timezone.utc).isoformat(),
            "cash": cash,
            "available_cash": available_cash,
            "frozen_cash": float(frozen_cash),
            "market_value": market_value,
            "equity": equity,
            "pnl": pnl,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "pnl_reconciliation_gap": reconciliation_gap,
            "initial_cash": self.initial_cash,
            "peak_equity": peak_equity,
            "drawdown": drawdown,
            "max_drawdown": min(drawdown_history) if drawdown_history else 0.0,
            "total_return": equity / self.initial_cash - 1,
            "cash_ratio": cash / equity if equity > 0 else 0.0,
            "exposure_ratio": market_value / equity if equity > 0 else 0.0,
            "committed_exposure_value": market_value + float(frozen_cash),
            "committed_exposure_ratio": (
                (market_value + float(frozen_cash)) / equity if equity > 0 else 0.0
            ),
            "position_count": len(valued_positions),
            "available_position_count": sum(
                int(item["sellable_quantity"]) > 0 for item in valued_positions
            ),
            "total_fees": total_fees,
            "turnover": turnover,
            "fee_drag_pct": total_fees / self.initial_cash,
        }
        return {
            "asset_valuation": summary,
            "positions": valued_positions,
            "equity_curve": self.equity_curve(nav, summary, bool(valued_positions)),
        }

    @staticmethod
    def equity_curve(
        nav: Iterable[Mapping[str, Any]],
        current: Mapping[str, Any],
        has_positions: bool,
    ) -> list[dict[str, Any]]:
        """Append a current valuation point server-side when it adds new evidence."""
        points = [dict(item) for item in reversed([dict(row) for row in nav])]
        latest_equity = float(points[-1]["equity"]) if points else None
        current_equity = float(current["equity"])
        if has_positions and (
            latest_equity is None or abs(latest_equity - current_equity) > 0.01
        ):
            points.append({
                "trade_date": "当前估值",
                "cash": float(current["cash"]),
                "market_value": float(current["market_value"]),
                "equity": current_equity,
                "drawdown": float(current["drawdown"]),
                "valuation_point": True,
            })
        return points

    @staticmethod
    def round_lot_buying_power(available_cash: float, price: float, lot_size: int = 100) -> int:
        """Return a display-only round-lot cash capacity without frontend arithmetic."""
        if available_cash <= 0 or price <= 0 or lot_size <= 0:
            return 0
        return math.floor(float(available_cash) / float(price) / lot_size) * lot_size
