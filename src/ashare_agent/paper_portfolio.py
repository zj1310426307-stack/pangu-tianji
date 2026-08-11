from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .execution_rules import assess_tradeability
from .services.valuation_service import ValuationService


class PaperPortfolio:
    """Persist and transact a paper-only A-share portfolio across restarts."""

    def __init__(
        self,
        db_path: Path,
        config: dict[str, Any],
        costs: dict[str, Any],
        valuation_service: ValuationService | None = None,
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.config, self.costs = config, costs
        self.valuation_service = valuation_service or ValuationService(
            float(config["initial_cash"])
        )
        self._schema()

    def _schema(self) -> None:
        """Create the paper ledger and migrate older v0.9 databases in place."""
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS paper_account(id INTEGER PRIMARY KEY CHECK(id=1), cash REAL NOT NULL, peak_equity REAL NOT NULL, kill_switch INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_positions(symbol TEXT PRIMARY KEY, name TEXT NOT NULL, quantity INTEGER NOT NULL, available_quantity INTEGER NOT NULL, average_cost REAL NOT NULL, acquired_date TEXT NOT NULL, pending_exit INTEGER NOT NULL DEFAULT 0, highest_price REAL NOT NULL DEFAULT 0, lowest_price REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS paper_orders(client_order_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, trade_date TEXT NOT NULL, plan_date TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, requested_price REAL NOT NULL, status TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_trades(client_order_id TEXT UNIQUE NOT NULL, trade_date TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, fill_price REAL NOT NULL, fee REAL NOT NULL, gross_value REAL NOT NULL DEFAULT 0, commission REAL NOT NULL DEFAULT 0, stamp_tax REAL NOT NULL DEFAULT 0, transfer_fee REAL NOT NULL DEFAULT 0, realized_pnl REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS paper_nav(trade_date TEXT PRIMARY KEY, cash REAL NOT NULL, market_value REAL NOT NULL, equity REAL NOT NULL, drawdown REAL NOT NULL, positions_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS monitor_events(event_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, symbol TEXT NOT NULL, event_type TEXT NOT NULL, price REAL, message TEXT NOT NULL);
        """)
        self._ensure_column("paper_positions", "highest_price", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("paper_positions", "lowest_price", "REAL NOT NULL DEFAULT 0")
        for name in ("gross_value", "commission", "stamp_tax", "transfer_fee", "realized_pnl"):
            self._ensure_column("paper_trades", name, "REAL NOT NULL DEFAULT 0")
        order_columns = {
            "name": "TEXT NOT NULL DEFAULT ''",
            "order_type": "TEXT NOT NULL DEFAULT 'MARKET'",
            "limit_price": "REAL",
            "time_in_force": "TEXT NOT NULL DEFAULT 'DAY'",
            "remaining_quantity": "INTEGER NOT NULL DEFAULT 0",
            "filled_quantity": "INTEGER NOT NULL DEFAULT 0",
            "frozen_cash": "REAL NOT NULL DEFAULT 0",
            "frozen_quantity": "INTEGER NOT NULL DEFAULT 0",
            "last_quote_price": "REAL",
            "last_quote_at": "TEXT",
            "updated_at": "TEXT",
            "cancelled_at": "TEXT",
        }
        for name, definition in order_columns.items():
            self._ensure_column("paper_orders", name, definition)
        self.conn.execute(
            """UPDATE paper_orders
            SET remaining_quantity=CASE
                    WHEN status IN ('PENDING','SUBMITTED') AND remaining_quantity=0
                    THEN quantity ELSE remaining_quantity END,
                filled_quantity=CASE
                    WHEN status='FILLED' AND filled_quantity=0
                    THEN quantity ELSE filled_quantity END,
                updated_at=COALESCE(updated_at,created_at)"""
        )
        self.conn.execute(
            "UPDATE paper_positions SET highest_price=average_cost WHERE highest_price<=0"
        )
        self.conn.execute(
            "UPDATE paper_positions SET lowest_price=average_cost WHERE lowest_price<=0"
        )
        now = datetime.now(timezone.utc).isoformat()
        initial = float(self.config["initial_cash"])
        self.conn.execute("INSERT OR IGNORE INTO paper_account VALUES(1,?,?,0,?)", (initial, initial, now))
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        """Add one backward-compatible SQLite column when an older ledger lacks it."""
        columns = {str(row[1]) for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _fee_breakdown(self, gross: float, side: str) -> dict[str, float]:
        """Calculate commission, stamp tax and transfer fee without double counting."""
        commission = max(
            gross * float(self.costs["commission_rate"]),
            float(self.costs["minimum_commission"]),
        )
        stamp_tax = gross * float(self.costs["sell_tax_rate"]) if side == "SELL" else 0.0
        transfer_fee = gross * float(self.costs.get("transfer_fee_rate", 0.0))
        total = commission + stamp_tax + transfer_fee
        return {
            "commission": commission,
            "stamp_tax": stamp_tax,
            "transfer_fee": transfer_fee,
            "total": total,
        }

    def _account(self) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM paper_account WHERE id=1").fetchone()

    def positions(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM paper_positions WHERE quantity>0 ORDER BY symbol")]

    def _open_orders(self) -> list[dict[str, Any]]:
        """Return active paper orders that still reserve local account resources."""
        return [
            dict(row)
            for row in self.conn.execute(
                """SELECT * FROM paper_orders
                WHERE status IN ('PENDING','SUBMITTED')
                ORDER BY created_at,client_order_id"""
            )
        ]

    def _reserved_resources(self) -> tuple[float, dict[str, int]]:
        """Aggregate implicit cash/share reservations from active paper orders."""
        frozen_cash = 0.0
        frozen_by_symbol: dict[str, int] = {}
        for order in self._open_orders():
            frozen_cash += float(order.get("frozen_cash") or 0)
            symbol = str(order["symbol"])
            frozen_by_symbol[symbol] = frozen_by_symbol.get(symbol, 0) + int(
                order.get("frozen_quantity") or 0
            )
        return frozen_cash, frozen_by_symbol

    def snapshot(self, quotes: dict[str, float] | None = None) -> dict[str, Any]:
        """Delegate the authoritative local account view to ValuationService."""
        account = self._account()
        positions = self.positions()
        frozen_cash, frozen_by_symbol = self._reserved_resources()
        trades = [dict(row) for row in self.conn.execute("SELECT * FROM paper_trades")]
        nav = [dict(row) for row in self.conn.execute(
            "SELECT * FROM paper_nav ORDER BY trade_date DESC"
        )]
        valued = self.valuation_service.value_account(
            account=dict(account),
            positions=positions,
            prices=quotes,
            frozen_cash=frozen_cash,
            frozen_by_symbol=frozen_by_symbol,
            trades=trades,
            nav=nav,
        )
        asset = valued["asset_valuation"]
        return {
            **asset,
            "net_pnl": asset["pnl"],
            "kill_switch": bool(account["kill_switch"]),
            "positions": valued["positions"],
            "asset_valuation": asset,
            "equity_curve": valued["equity_curve"],
            "limits": {
                "max_positions": int(self.config["max_positions"]),
                "target_position_pct": float(self.config["target_position_pct"]),
                "max_total_exposure_pct": float(self.config["max_total_exposure_pct"]),
                "stop_loss_pct": float(self.config["stop_loss_pct"]),
                "max_drawdown_pct": float(self.config["max_drawdown_pct"]),
            },
            "live_trading_enabled": False,
            "can_submit_orders": False,
            "paper_order_capability": "local_snapshot_matching_only",
        }

    def mark_to_market(self, quotes: dict[str, float], trade_date: date) -> dict[str, Any]:
        """Persist NAV and the high-water mark used by drawdown controls."""
        view = self.snapshot(quotes)
        for position in view["positions"]:
            price = float(position["last_price"])
            self.conn.execute(
                """UPDATE paper_positions
                SET highest_price=MAX(highest_price,?),
                    lowest_price=CASE WHEN lowest_price<=0 THEN ? ELSE MIN(lowest_price,?) END
                WHERE symbol=?""",
                (price, price, price, position["symbol"]),
            )
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE paper_account SET peak_equity=?,updated_at=? WHERE id=1",
            (view["peak_equity"], now),
        )
        self.conn.execute(
            """INSERT INTO paper_nav VALUES(?,?,?,?,?,?)
            ON CONFLICT(trade_date) DO UPDATE SET cash=excluded.cash,
            market_value=excluded.market_value,equity=excluded.equity,
            drawdown=excluded.drawdown,positions_json=excluded.positions_json""",
            (
                trade_date.isoformat(), view["cash"], view["market_value"],
                view["equity"], view["drawdown"],
                json.dumps(view["positions"], ensure_ascii=False, sort_keys=True),
            ),
        )
        self.conn.commit()
        return view

    def set_kill_switch(self, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE paper_account SET kill_switch=?,updated_at=? WHERE id=1",
            (int(bool(enabled)), datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def roll_t1(self, trade_date: date) -> None:
        """Release quantities acquired before the current trade date."""
        self.conn.execute(
            "UPDATE paper_positions SET available_quantity=quantity WHERE acquired_date<?",
            (trade_date.isoformat(),),
        )
        self.conn.commit()

    @staticmethod
    def _quantity_rejection(
        side: str,
        quantity: int,
        position: sqlite3.Row | dict[str, Any] | None,
    ) -> str | None:
        """Validate A-share round lots while allowing an exact odd-lot liquidation.

        Buys always require 100-share lots. A sell may use an odd quantity only
        when it closes the entire position and every share is currently
        available; this keeps the simulator aligned with its own documented
        reduce-only behavior without permitting arbitrary odd-lot partial sells.
        """
        if quantity <= 0:
            return "数量必须大于0"
        if quantity % 100 == 0:
            return None
        if side == "SELL" and position is not None:
            total = int(position["quantity"])
            available = int(position["available_quantity"])
            if quantity == total == available:
                return None
            return "卖出数量必须是100股整数倍；仅清仓时允许卖出零股尾数"
        return "买入数量必须是100股整数倍"

    def assess_manual_order(
        self,
        *,
        side: str,
        symbol: str,
        name: str,
        quantity: int,
        quotes: dict[str, dict[str, Any]],
        trade_date: date,
        order_type: str = "MARKET",
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        """Return a non-ordering server-side estimate using the execution rules.

        The estimate shares quantity, quote, cash, position and exposure checks
        with manual execution. It may roll T+1 availability for the supplied
        trade date, but it never inserts an order or trade.
        """
        normalized_side = side.upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("side必须是BUY或SELL")
        normalized_type = order_type.upper()
        if normalized_type not in {"MARKET", "LIMIT"}:
            raise ValueError("order_type必须是MARKET或LIMIT")
        if normalized_type == "LIMIT" and (
            limit_price is None
            or not math.isfinite(float(limit_price))
            or float(limit_price) <= 0
        ):
            raise ValueError("限价委托必须提供大于0的限价")
        self.roll_t1(trade_date)
        prices = {
            code: float(quote["last_price"])
            for code, quote in quotes.items()
            if quote.get("last_price") is not None
        }
        quote = quotes.get(symbol) or {}
        try:
            price = float(quote.get("last_price", 0))
        except (TypeError, ValueError):
            price = 0.0
        account = self.snapshot(prices)
        positions = self.positions()
        active_buy_orders = [
            item for item in self._open_orders() if item.get("side") == "BUY"
        ]
        pending_buy_value = sum(
            float(item.get("frozen_cash") or 0) for item in active_buy_orders
        )
        pending_buy_by_symbol: dict[str, float] = {}
        for item in active_buy_orders:
            pending_buy_by_symbol[str(item["symbol"])] = (
                pending_buy_by_symbol.get(str(item["symbol"]), 0.0)
                + float(item.get("frozen_cash") or 0)
            )
        reserved_symbols = {
            item["symbol"] for item in positions
        } | {str(item["symbol"]) for item in active_buy_orders}
        position = next((item for item in positions if item["symbol"] == symbol), None)
        account_position = next(
            (item for item in account["positions"] if item["symbol"] == symbol), None
        )
        if position is not None and account_position is not None:
            position = {
                **position,
                "available_quantity": int(account_position["sellable_quantity"]),
            }
        tradeability = assess_tradeability(quote, normalized_side)
        rejection: str | None = None
        quantity_rejection = self._quantity_rejection(
            normalized_side, quantity, position
        )

        if not math.isfinite(price) or price <= 0:
            rejection = "同花顺最新价无效"
        elif not tradeability.tradable:
            rejection = tradeability.reason
        elif quantity_rejection:
            rejection = quantity_rejection
        elif normalized_side == "BUY":
            if account["kill_switch"]:
                rejection = "Kill Switch已开启"
            elif account["drawdown"] <= -float(self.config["max_drawdown_pct"]):
                rejection = "组合回撤已达到停止开仓线"
            elif (
                position is None
                and symbol not in reserved_symbols
                and len(reserved_symbols) >= int(self.config["max_positions"])
            ):
                rejection = "模拟持仓数量已达到上限"
        else:
            if position is None or int(position["quantity"]) <= 0:
                rejection = "模拟账户未持有该股票"
            elif int(position["available_quantity"]) <= 0:
                rejection = "当日买入持仓受T+1限制，当日不可卖出"
            elif quantity > int(position["available_quantity"]):
                rejection = f"卖出数量超过可卖{int(position['available_quantity'])}股"

        requested_price = (
            float(limit_price) if normalized_type == "LIMIT" else price
        )
        marketable = normalized_type == "MARKET" or (
            normalized_side == "BUY" and price <= requested_price
        ) or (
            normalized_side == "SELL" and price >= requested_price
        )
        slip = float(self.costs["slippage_rate"])
        slipped = price * (1 + slip if normalized_side == "BUY" else 1 - slip)
        if normalized_type == "LIMIT":
            fill = min(slipped, requested_price) if normalized_side == "BUY" else max(slipped, requested_price)
        else:
            fill = slipped
        gross = max(fill, 0.0) * max(quantity, 0)
        fees = self._fee_breakdown(gross, normalized_side) if gross > 0 else {
            "commission": 0.0,
            "stamp_tax": 0.0,
            "transfer_fee": 0.0,
            "total": 0.0,
        }
        current_symbol_value = (
            price * int(position["quantity"]) if position else 0.0
        ) + pending_buy_by_symbol.get(symbol, 0.0)
        if normalized_side == "BUY":
            cash_after = float(account["available_cash"]) - gross - fees["total"]
            market_after = (
                float(account["market_value"])
                + pending_buy_value
                + price * max(quantity, 0)
            )
            symbol_value_after = current_symbol_value + price * max(quantity, 0)
        else:
            cash_after = float(account["available_cash"]) + gross - fees["total"]
            market_after = max(
                0.0, float(account["market_value"]) - price * max(quantity, 0)
            )
            symbol_value_after = max(0.0, current_symbol_value - price * max(quantity, 0))
        equity_after = cash_after + market_after
        exposure_after = market_after / equity_after if equity_after > 0 else 0.0
        symbol_weight_after = symbol_value_after / equity_after if equity_after > 0 else 0.0

        if rejection is None and normalized_side == "BUY":
            if gross + fees["total"] > float(account["available_cash"]):
                rejection = "模拟账户现金不足"
            elif symbol_weight_after > float(self.config["target_position_pct"]) + 1e-12:
                limit = float(self.config["target_position_pct"])
                rejection = f"买入后单只仓位将超过{limit:.0%}上限"
            elif exposure_after > float(self.config["max_total_exposure_pct"]) + 1e-12:
                limit = float(self.config["max_total_exposure_pct"])
                rejection = f"买入后总仓位将超过{limit:.0%}上限"

        realized_estimate = 0.0
        if normalized_side == "SELL" and position is not None and quantity > 0:
            realized_estimate = (
                (fill - float(position["average_cost"])) * quantity - fees["total"]
            )
        return {
            "side": normalized_side,
            "symbol": symbol,
            "name": name,
            "quantity": quantity,
            "order_type": normalized_type,
            "limit_price": float(limit_price) if limit_price is not None else None,
            "time_in_force": "DAY",
            "marketable_now": marketable,
            "allowed": rejection is None,
            "blocked_reason": rejection,
            "blocked_state": (
                tradeability.state
                if rejection and not tradeability.tradable
                else "RISK_REJECTED"
            ),
            "requested_price": requested_price,
            "estimated_fill_price": fill,
            "gross_value": gross,
            "fee_breakdown": fees,
            "estimated_slippage_cost": abs(fill - price) * max(quantity, 0),
            "estimated_realized_pnl": realized_estimate,
            "before": {
                "cash": float(account["cash"]),
                "available_cash": float(account["available_cash"]),
                "frozen_cash": float(account["frozen_cash"]),
                "market_value": float(account["market_value"]),
                "equity": float(account["equity"]),
                "exposure_ratio": (
                    float(account["market_value"]) / float(account["equity"])
                    if float(account["equity"]) > 0
                    else 0.0
                ),
                "position_quantity": int(position["quantity"]) if position else 0,
                "available_quantity": int(position["available_quantity"]) if position else 0,
            },
            "after": {
                "cash": cash_after,
                "market_value": market_after,
                "equity": equity_after,
                "exposure_ratio": exposure_after,
                "symbol_weight": symbol_weight_after,
                "position_quantity": (
                    (int(position["quantity"]) if position else 0)
                    + (quantity if normalized_side == "BUY" else -quantity)
                ),
            },
            "live_trading_enabled": False,
            "can_submit_orders": False,
        }

    def _trade(
        self, trade_date: date, plan_date: str, symbol: str, name: str,
        side: str, quantity: int, price: float, reason: str,
        *, idempotency_key: str | None = None,
        precheck_rejection: str | None = None,
        precheck_status: str = "RISK_REJECTED",
    ) -> dict[str, Any]:
        """Execute one idempotent local fill; UNKNOWN is never synthesized or retried."""
        key = idempotency_key or f"{trade_date.isoformat()}:{plan_date}:{symbol}:{side}"
        existing = self.conn.execute("SELECT * FROM paper_orders WHERE idempotency_key=?", (key,)).fetchone()
        if existing:
            return dict(existing)
        client_id = str(uuid4())
        slip = float(self.costs["slippage_rate"])
        fill = price * (1 + slip if side == "BUY" else 1 - slip)
        gross = fill * quantity
        fees = self._fee_breakdown(gross, side)
        fee = fees["total"]
        account = self._account()
        account_view = self.snapshot({symbol: price})
        cash = float(account["cash"])
        available_cash = float(account_view["available_cash"])
        position = self.conn.execute("SELECT * FROM paper_positions WHERE symbol=?", (symbol,)).fetchone()
        position_view = next(
            (item for item in account_view["positions"] if item["symbol"] == symbol),
            None,
        )
        status, reject = "FILLED", precheck_rejection
        if precheck_rejection:
            status = precheck_status
        elif quantity_rejection := self._quantity_rejection(side, quantity, position):
            status, reject = "RISK_REJECTED", quantity_rejection
        elif side == "BUY" and gross + fee > available_cash:
            status, reject = "RISK_REJECTED", "模拟账户现金不足"
        elif side == "SELL" and (
            not position_view
            or int(position_view["sellable_quantity"]) < quantity
        ):
            status, reject = "RISK_REJECTED", "T+1可卖数量不足"
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO paper_orders
            (client_order_id,idempotency_key,trade_date,plan_date,symbol,side,
             quantity,requested_price,status,reason,created_at,name,order_type,
             time_in_force,remaining_quantity,filled_quantity,frozen_cash,
             frozen_quantity,last_quote_price,last_quote_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                client_id, key, trade_date.isoformat(), plan_date, symbol, side,
                quantity, price, status, reject or reason, now, name, "MARKET",
                "DAY", 0 if status == "FILLED" else quantity,
                quantity if status == "FILLED" else 0, 0.0, 0, price, now, now,
            ),
        )
        if status == "FILLED":
            if side == "BUY":
                old_qty = int(position["quantity"]) if position else 0
                old_cost = float(position["average_cost"]) if position else 0.0
                new_qty = old_qty + quantity
                # average_cost is deliberately fee-inclusive so PnL and stop
                # thresholds cannot be flattered by omitted buy commission.
                avg = (old_cost * old_qty + gross + fee) / new_qty
                self.conn.execute(
                    """INSERT INTO paper_positions
                    (symbol,name,quantity,available_quantity,average_cost,acquired_date,pending_exit,highest_price,lowest_price)
                    VALUES(?,?,?,?,?,?,0,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,
                    quantity=excluded.quantity,available_quantity=paper_positions.available_quantity,
                    average_cost=excluded.average_cost,acquired_date=excluded.acquired_date,
                    highest_price=MAX(paper_positions.highest_price,excluded.highest_price),
                    lowest_price=CASE WHEN paper_positions.lowest_price<=0 THEN excluded.lowest_price ELSE MIN(paper_positions.lowest_price,excluded.lowest_price) END""",
                    (symbol, name, new_qty, 0, avg, trade_date.isoformat(), fill, fill),
                )
                cash -= gross + fee
            else:
                realized_pnl = (fill - float(position["average_cost"])) * quantity - fee
                remaining = int(position["quantity"]) - quantity
                available = int(position["available_quantity"]) - quantity
                self.conn.execute(
                    """UPDATE paper_positions
                    SET quantity=?,available_quantity=?,
                        average_cost=CASE WHEN ?=0 THEN 0 ELSE average_cost END,
                        pending_exit=CASE WHEN ?=0 THEN 0 ELSE pending_exit END
                    WHERE symbol=?""",
                    (remaining, available, remaining, remaining, symbol),
                )
                cash += gross - fee
            if side == "BUY":
                realized_pnl = 0.0
            self.conn.execute(
                """INSERT INTO paper_trades
                (client_order_id,trade_date,symbol,side,quantity,fill_price,fee,
                 gross_value,commission,stamp_tax,transfer_fee,realized_pnl)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    client_id, trade_date.isoformat(), symbol, side, quantity, fill, fee,
                    gross, fees["commission"], fees["stamp_tax"], fees["transfer_fee"], realized_pnl,
                ),
            )
            self.conn.execute("UPDATE paper_account SET cash=?,updated_at=? WHERE id=1", (cash, now))
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM paper_orders WHERE client_order_id=?", (client_id,)).fetchone())

    def _fill_pending_order(
        self,
        order: dict[str, Any],
        quote: dict[str, Any],
        observed_at: datetime,
    ) -> dict[str, Any]:
        """Fill one active local order against a tradeable polling snapshot.

        The simulator has no exchange queue or Level-2 depth. It therefore
        performs an all-or-none snapshot fill and never describes the result as
        a brokerage or exchange execution.
        """
        current = self.conn.execute(
            "SELECT * FROM paper_orders WHERE client_order_id=?",
            (order["client_order_id"],),
        ).fetchone()
        if current is None or str(current["status"]) not in {"PENDING", "SUBMITTED"}:
            return dict(current) if current else order
        order = dict(current)
        side = str(order["side"])
        price = float(quote.get("last_price") or 0)
        if not math.isfinite(price) or price <= 0:
            return order
        limit_price = (
            float(order["limit_price"])
            if order.get("limit_price") is not None
            else None
        )
        if str(order.get("order_type") or "MARKET") == "LIMIT":
            if side == "BUY" and price > float(limit_price):
                return order
            if side == "SELL" and price < float(limit_price):
                return order
        tradeability = assess_tradeability(quote, side)
        if not tradeability.tradable:
            return order

        slip = float(self.costs["slippage_rate"])
        slipped = price * (1 + slip if side == "BUY" else 1 - slip)
        if limit_price is not None:
            fill = min(slipped, limit_price) if side == "BUY" else max(slipped, limit_price)
        else:
            fill = slipped
        quantity = int(order["remaining_quantity"] or order["quantity"])
        gross = fill * quantity
        fees = self._fee_breakdown(gross, side)
        fee = fees["total"]
        account = self._account()
        cash = float(account["cash"])
        frozen_cash, frozen_by_symbol = self._reserved_resources()
        other_frozen_cash = max(0.0, frozen_cash - float(order.get("frozen_cash") or 0))
        available_cash = cash - other_frozen_cash
        position = self.conn.execute(
            "SELECT * FROM paper_positions WHERE symbol=?", (order["symbol"],)
        ).fetchone()
        own_frozen_quantity = int(order.get("frozen_quantity") or 0)
        other_frozen_quantity = max(
            0,
            frozen_by_symbol.get(str(order["symbol"]), 0) - own_frozen_quantity,
        )
        sellable_quantity = (
            max(0, int(position["available_quantity"]) - other_frozen_quantity)
            if position
            else 0
        )
        if side == "BUY" and gross + fee > available_cash + 1e-9:
            return order
        if side == "SELL" and sellable_quantity < quantity:
            return order

        now = observed_at.astimezone(timezone.utc).isoformat()
        if side == "BUY":
            old_qty = int(position["quantity"]) if position else 0
            old_cost = float(position["average_cost"]) if position else 0.0
            new_qty = old_qty + quantity
            average_cost = (old_cost * old_qty + gross + fee) / new_qty
            self.conn.execute(
                """INSERT INTO paper_positions
                (symbol,name,quantity,available_quantity,average_cost,acquired_date,
                 pending_exit,highest_price,lowest_price)
                VALUES(?,?,?,?,?,?,0,?,?)
                ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,
                quantity=excluded.quantity,
                available_quantity=paper_positions.available_quantity,
                average_cost=excluded.average_cost,
                acquired_date=excluded.acquired_date,
                highest_price=MAX(paper_positions.highest_price,excluded.highest_price),
                lowest_price=CASE WHEN paper_positions.lowest_price<=0
                    THEN excluded.lowest_price
                    ELSE MIN(paper_positions.lowest_price,excluded.lowest_price) END""",
                (
                    order["symbol"], order.get("name") or order["symbol"], new_qty,
                    0, average_cost, observed_at.date().isoformat(), fill, fill,
                ),
            )
            cash -= gross + fee
            realized_pnl = 0.0
        else:
            realized_pnl = (fill - float(position["average_cost"])) * quantity - fee
            remaining = int(position["quantity"]) - quantity
            available = int(position["available_quantity"]) - quantity
            self.conn.execute(
                """UPDATE paper_positions
                SET quantity=?,available_quantity=?,
                    average_cost=CASE WHEN ?=0 THEN 0 ELSE average_cost END,
                    pending_exit=CASE WHEN ?=0 THEN 0 ELSE pending_exit END
                WHERE symbol=?""",
                (remaining, available, remaining, remaining, order["symbol"]),
            )
            cash += gross - fee

        self.conn.execute(
            """INSERT INTO paper_trades
            (client_order_id,trade_date,symbol,side,quantity,fill_price,fee,
             gross_value,commission,stamp_tax,transfer_fee,realized_pnl)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                order["client_order_id"], observed_at.date().isoformat(),
                order["symbol"], side, quantity, fill, fee, gross,
                fees["commission"], fees["stamp_tax"], fees["transfer_fee"],
                realized_pnl,
            ),
        )
        self.conn.execute(
            """UPDATE paper_orders
            SET status='FILLED',remaining_quantity=0,filled_quantity=quantity,
                frozen_cash=0,frozen_quantity=0,last_quote_price=?,
                last_quote_at=?,updated_at=?,reason=?
            WHERE client_order_id=?""",
            (
                price, now, now,
                "同花顺轮询快照达到委托条件，本地模拟全量成交",
                order["client_order_id"],
            ),
        )
        self.conn.execute(
            "UPDATE paper_account SET cash=?,updated_at=? WHERE id=1", (cash, now)
        )
        self.conn.commit()
        return dict(
            self.conn.execute(
                "SELECT * FROM paper_orders WHERE client_order_id=?",
                (order["client_order_id"],),
            ).fetchone()
        )

    def place_broker_order(
        self,
        *,
        side: str,
        symbol: str,
        name: str,
        quantity: int,
        order_type: str,
        limit_price: float | None,
        quotes: dict[str, dict[str, Any]],
        trade_date: date,
        request_key: str,
        observed_at: datetime,
    ) -> dict[str, Any]:
        """Create an idempotent broker-style local paper order and maybe match it."""
        key = f"broker:{request_key}"
        existing = self.conn.execute(
            "SELECT * FROM paper_orders WHERE idempotency_key=?", (key,)
        ).fetchone()
        if existing:
            order = dict(existing)
            trade = self.conn.execute(
                "SELECT * FROM paper_trades WHERE client_order_id=?",
                (order["client_order_id"],),
            ).fetchone()
            prices = {
                code: float(item["last_price"])
                for code, item in quotes.items()
                if item.get("last_price") is not None
            }
            return {
                "order": order,
                "trade": dict(trade) if trade else None,
                "account": self.snapshot(prices),
                "receipt": {"idempotent_replay": True},
            }

        assessment = self.assess_manual_order(
            side=side,
            symbol=symbol,
            name=name,
            quantity=quantity,
            quotes=quotes,
            trade_date=trade_date,
            order_type=order_type,
            limit_price=limit_price,
        )
        client_id = str(uuid4())
        now = observed_at.astimezone(timezone.utc).isoformat()
        status = "PENDING" if assessment["allowed"] else str(
            assessment.get("blocked_state") or "RISK_REJECTED"
        )
        frozen_cash = (
            float(assessment["gross_value"])
            + float(assessment["fee_breakdown"]["total"])
            if status == "PENDING" and side == "BUY"
            else 0.0
        )
        frozen_quantity = quantity if status == "PENDING" and side == "SELL" else 0
        reason = assessment.get("blocked_reason") or (
            "等待同花顺轮询快照达到限价"
            if order_type.upper() == "LIMIT" and not assessment["marketable_now"]
            else "等待本地快照撮合"
        )
        self.conn.execute(
            """INSERT INTO paper_orders
            (client_order_id,idempotency_key,trade_date,plan_date,symbol,side,
             quantity,requested_price,status,reason,created_at,name,order_type,
             limit_price,time_in_force,remaining_quantity,filled_quantity,
             frozen_cash,frozen_quantity,last_quote_price,last_quote_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                client_id, key, trade_date.isoformat(), "MANUAL_BROKER", symbol,
                side.upper(), quantity, assessment["requested_price"], status,
                reason, now, name, order_type.upper(), limit_price, "DAY",
                quantity if status == "PENDING" else 0, 0, frozen_cash,
                frozen_quantity, float(quotes[symbol]["last_price"]), now, now,
            ),
        )
        self.conn.commit()
        order = dict(
            self.conn.execute(
                "SELECT * FROM paper_orders WHERE client_order_id=?", (client_id,)
            ).fetchone()
        )
        if status == "PENDING" and assessment["marketable_now"]:
            order = self._fill_pending_order(order, quotes[symbol], observed_at)
        trade = self.conn.execute(
            "SELECT * FROM paper_trades WHERE client_order_id=?", (client_id,)
        ).fetchone()
        prices = {
            code: float(item["last_price"])
            for code, item in quotes.items()
            if item.get("last_price") is not None
        }
        account = self.mark_to_market(prices, trade_date)
        return {
            "order": order,
            "trade": dict(trade) if trade else None,
            "account": account,
            "receipt": {**assessment, "idempotent_replay": False, "order_status": order["status"]},
        }

    def cancel_broker_order(self, client_order_id: str, cancelled_at: datetime) -> dict[str, Any]:
        """Cancel one active local paper order and release its implicit reservation."""
        row = self.conn.execute(
            "SELECT * FROM paper_orders WHERE client_order_id=?", (client_order_id,)
        ).fetchone()
        if row is None:
            raise ValueError("模拟委托不存在")
        order = dict(row)
        if order["status"] == "CANCELLED":
            return order
        if order["status"] not in {"PENDING", "SUBMITTED"}:
            raise ValueError(f"状态{order['status']}的模拟委托不能撤销")
        now = cancelled_at.astimezone(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE paper_orders
            SET status='CANCELLED',reason='用户撤销本地模拟委托',
                frozen_cash=0,frozen_quantity=0,cancelled_at=?,updated_at=?
            WHERE client_order_id=?""",
            (now, now, client_order_id),
        )
        self.conn.commit()
        return dict(
            self.conn.execute(
                "SELECT * FROM paper_orders WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
        )

    def expire_day_orders(self, current_date: date, expired_at: datetime) -> int:
        """Expire older DAY orders so reserved cash and shares are released."""
        now = expired_at.astimezone(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """UPDATE paper_orders
            SET status='EXPIRED',reason='当日限价委托已过期',frozen_cash=0,
                frozen_quantity=0,cancelled_at=?,updated_at=?
            WHERE status IN ('PENDING','SUBMITTED') AND time_in_force='DAY'
              AND trade_date<?""",
            (now, now, current_date.isoformat()),
        )
        self.conn.commit()
        return int(cursor.rowcount)

    def match_broker_orders(
        self,
        quotes: dict[str, dict[str, Any]],
        observed_at: datetime,
    ) -> dict[str, Any]:
        """Match all active DAY orders against one fresh THS snapshot batch."""
        expired = self.expire_day_orders(observed_at.date(), observed_at)
        matched: list[dict[str, Any]] = []
        waiting: list[dict[str, Any]] = []
        now = observed_at.astimezone(timezone.utc).isoformat()
        for order in self._open_orders():
            quote = quotes.get(str(order["symbol"]))
            if not quote:
                waiting.append(order)
                continue
            try:
                price = float(quote.get("last_price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            self.conn.execute(
                """UPDATE paper_orders
                SET last_quote_price=?,last_quote_at=?,updated_at=?
                WHERE client_order_id=?""",
                (price if price > 0 else None, now, now, order["client_order_id"]),
            )
            updated = self._fill_pending_order(order, quote, observed_at)
            if updated.get("status") == "FILLED":
                matched.append(updated)
            else:
                waiting.append(updated)
        self.conn.commit()
        prices = {
            symbol: float(quote["last_price"])
            for symbol, quote in quotes.items()
            if quote.get("last_price") is not None
        }
        return {
            "matched": matched,
            "waiting": waiting,
            "expired_count": expired,
            "account": self.mark_to_market(prices, observed_at.date()),
            "matching_model": "ths_polling_snapshot_all_or_none",
            "live_trading_enabled": False,
            "can_submit_orders": False,
        }

    def manual_buy(
        self,
        *,
        symbol: str,
        name: str,
        quantity: int,
        quotes: dict[str, dict[str, Any]],
        trade_date: date,
        request_key: str,
    ) -> dict[str, Any]:
        """Apply hard paper-account limits before recording one manual BUY attempt."""
        assessment = self.assess_manual_order(
            side="BUY",
            symbol=symbol,
            name=name,
            quantity=quantity,
            quotes=quotes,
            trade_date=trade_date,
        )
        prices = {
            code: float(quote["last_price"])
            for code, quote in quotes.items()
            if quote.get("last_price") is not None
        }
        replay = self.conn.execute(
            "SELECT 1 FROM paper_orders WHERE idempotency_key=?",
            (f"manual:{request_key}",),
        ).fetchone() is not None

        order = self._trade(
            trade_date,
            "MANUAL",
            symbol,
            name,
            "BUY",
            quantity,
            float(assessment["requested_price"]),
            "用户确认的本地模拟买入",
            idempotency_key=f"manual:{request_key}",
            precheck_rejection=assessment["blocked_reason"],
        )
        account = self.mark_to_market(prices, trade_date)
        trade = self.conn.execute(
            "SELECT * FROM paper_trades WHERE client_order_id=?",
            (order["client_order_id"],),
        ).fetchone()
        return {
            "order": order,
            "trade": dict(trade) if trade else None,
            "account": account,
            "receipt": {
                **assessment,
                "idempotent_replay": replay,
                "order_status": order["status"],
                "after": {
                    **assessment["after"],
                    "cash": account["cash"],
                    "market_value": account["market_value"],
                    "equity": account["equity"],
                    "exposure_ratio": (
                        account["market_value"] / account["equity"]
                        if account["equity"]
                        else 0.0
                    ),
                },
            },
        }

    def manual_sell(
        self,
        *,
        symbol: str,
        name: str,
        quantity: int,
        quotes: dict[str, dict[str, Any]],
        trade_date: date,
        request_key: str,
    ) -> dict[str, Any]:
        """Persist one reduce-only paper SELL after T+1 and quote checks."""
        assessment = self.assess_manual_order(
            side="SELL",
            symbol=symbol,
            name=name,
            quantity=quantity,
            quotes=quotes,
            trade_date=trade_date,
        )
        prices = {
            code: float(quote["last_price"])
            for code, quote in quotes.items()
            if quote.get("last_price") is not None
        }
        replay = self.conn.execute(
            "SELECT 1 FROM paper_orders WHERE idempotency_key=?",
            (f"manual-sell:{request_key}",),
        ).fetchone() is not None

        order = self._trade(
            trade_date,
            "MANUAL",
            symbol,
            name,
            "SELL",
            quantity,
            float(assessment["requested_price"]),
            "用户确认的本地模拟卖出",
            idempotency_key=f"manual-sell:{request_key}",
            precheck_rejection=assessment["blocked_reason"],
            precheck_status=str(assessment["blocked_state"]),
        )
        account = self.mark_to_market(prices, trade_date)
        trade = self.conn.execute(
            "SELECT * FROM paper_trades WHERE client_order_id=?",
            (order["client_order_id"],),
        ).fetchone()
        return {
            "order": order,
            "trade": dict(trade) if trade else None,
            "account": account,
            "receipt": {
                **assessment,
                "idempotent_replay": replay,
                "order_status": order["status"],
                "after": {
                    **assessment["after"],
                    "cash": account["cash"],
                    "market_value": account["market_value"],
                    "equity": account["equity"],
                    "exposure_ratio": (
                        account["market_value"] / account["equity"]
                        if account["equity"]
                        else 0.0
                    ),
                },
            },
        }

    def execute_plan(self, plan: dict[str, Any], quotes: dict[str, dict[str, Any]], trade_date: date) -> dict[str, Any]:
        """Sell first, then fill ranked positions using paper cash only."""
        if not plan.get("execution_ready"):
            raise RuntimeError("研究计划不完整，禁止模拟执行")
        self.roll_t1(trade_date)
        prices = {symbol: float(quote["last_price"]) for symbol, quote in quotes.items()}
        account_view = self.snapshot(prices)
        allow_buys = not account_view["kill_switch"] and account_view["drawdown"] > -float(self.config["max_drawdown_pct"])
        targets = list(plan["targets"])
        candidate_names = {item["symbol"]: item["name"] for item in plan["candidates"]}
        candidate_by_symbol = {item["symbol"]: item for item in plan["candidates"]}
        rank_by_symbol = {item["symbol"]: int(item.get("rank", 10_000)) for item in plan["candidates"]}
        hold_rank = int(self.config.get("hold_rank", 20))
        minimum_holding_days = int(self.config.get("minimum_holding_days", 0))
        orders: list[dict[str, Any]] = []
        exited_symbols: set[str] = set()

        for position in self.positions():
            holding_days = (trade_date - date.fromisoformat(str(position["acquired_date"]))).days
            ranking_exit = rank_by_symbol.get(position["symbol"], 10_000) > hold_rank
            must_exit = bool(position["pending_exit"]) or (
                ranking_exit and holding_days >= minimum_holding_days
            )
            if must_exit and int(position["available_quantity"]) > 0 and position["symbol"] in quotes:
                tradeability = assess_tradeability(quotes[position["symbol"]], "SELL")
                order = self._trade(
                    trade_date, plan["research_date"], position["symbol"], position["name"],
                    "SELL", int(position["available_quantity"]), prices[position["symbol"]],
                    "待退出或跌出持有缓冲区，卖出优先",
                    precheck_rejection=None if tradeability.tradable else tradeability.reason,
                    precheck_status="RISK_REJECTED" if tradeability.tradable else tradeability.state,
                )
                orders.append(order)
                if order["status"] == "FILLED":
                    exited_symbols.add(position["symbol"])

        held = {item["symbol"] for item in self.positions() if int(item["quantity"]) > 0}
        turnover_used = sum(
            float(order["requested_price"]) * int(order["quantity"])
            for order in orders
            if order["status"] == "FILLED"
        )
        if allow_buys:
            max_positions = int(self.config.get("max_positions", 3))
            ranked_symbols = targets
            for symbol in ranked_symbols:
                if len(held) >= max_positions:
                    break
                if symbol in held or symbol in exited_symbols or symbol not in quotes:
                    continue
                quote = quotes[symbol]
                price = float(quote["last_price"])
                tradeability = assess_tradeability(quote, "BUY")
                if not tradeability.tradable:
                    continue
                current = self.snapshot(prices)
                configured_weight = float(
                    candidate_by_symbol.get(symbol, {}).get(
                        "target_weight", self.config["target_position_pct"]
                    )
                )
                target_value = current["equity"] * min(
                    configured_weight, float(self.config["target_position_pct"])
                )
                exposure_room = (
                    current["equity"] * float(self.config["max_total_exposure_pct"])
                    - current["committed_exposure_value"]
                )
                turnover_room = max(
                    0.0,
                    current["equity"] * float(self.config.get("max_daily_turnover_pct", 1.0))
                    - turnover_used,
                )
                quantity = int(min(target_value, exposure_room, turnover_room) / price / 100) * 100
                while quantity > 0:
                    projected_gross = price * (1 + float(self.costs["slippage_rate"])) * quantity
                    projected_fee = max(
                        projected_gross * float(self.costs["commission_rate"]),
                        float(self.costs["minimum_commission"]),
                    )
                    projected_equity = current["equity"] - projected_fee
                    projected_market = current["committed_exposure_value"] + price * quantity
                    if projected_market <= projected_equity * float(self.config["max_total_exposure_pct"]):
                        break
                    quantity -= 100
                if quantity <= 0:
                    continue
                order = self._trade(
                    trade_date, plan["research_date"], symbol,
                    candidate_names.get(symbol, symbol), "BUY", quantity, price,
                    "按确定性榜单执行每日模拟调仓",
                )
                orders.append(order)
                if order["status"] == "FILLED":
                    held.add(symbol)
                    turnover_used += float(order["requested_price"]) * int(order["quantity"])
        return {
            "trade_date": trade_date.isoformat(), "plan_date": plan["research_date"],
            "allow_buys": allow_buys, "orders": orders,
            "account": self.mark_to_market(prices, trade_date),
        }

    def monitor(self, quotes: dict[str, dict[str, Any]], observed_at: datetime) -> list[dict[str, Any]]:
        """Trigger stops without pretending a T+1 or limit-down exit was filled."""
        self.roll_t1(observed_at.date())
        events: list[dict[str, Any]] = []
        valuation_prices = {
            symbol: float(quote["last_price"])
            for symbol, quote in quotes.items()
            if quote.get("last_price") is not None
        }
        account_positions = {
            item["symbol"]: item
            for item in self.snapshot(valuation_prices)["positions"]
        }
        for position in self.positions():
            quote = quotes.get(position["symbol"])
            if not quote:
                continue
            price = float(quote["last_price"])
            stop = float(position["average_cost"]) * (1 - float(self.config["stop_loss_pct"]))
            if price > stop:
                continue
            trigger = {
                "event_id": str(uuid4()), "observed_at": observed_at.isoformat(),
                "symbol": position["symbol"], "event_type": "STOP_TRIGGERED",
                "price": price, "message": f"价格跌破含费成本止损线{stop:.3f}",
            }
            self.conn.execute(
                "INSERT INTO monitor_events VALUES(?,?,?,?,?,?)",
                (trigger["event_id"], trigger["observed_at"], trigger["symbol"], trigger["event_type"], trigger["price"], trigger["message"]),
            )
            events.append(trigger)
            sellable_quantity = int(
                account_positions.get(position["symbol"], {}).get(
                    "sellable_quantity", position["available_quantity"]
                )
            )
            if sellable_quantity > 0:
                tradeability = assess_tradeability(quote, "SELL")
                if tradeability.tradable:
                    order = self._trade(
                        observed_at.date(), "STOP", position["symbol"], position["name"],
                        "SELL", sellable_quantity, price, "触发模拟止损",
                    )
                    message, event_type = f"止损退出订单：{order['status']}", "EXIT_FILLED"
                else:
                    order = self._trade(
                        observed_at.date(), "STOP", position["symbol"], position["name"],
                        "SELL", int(position["available_quantity"]), price, "止损退出受阻",
                        precheck_rejection=tradeability.reason,
                        precheck_status=tradeability.state,
                    )
                    self.conn.execute(
                        "UPDATE paper_positions SET pending_exit=1 WHERE symbol=?",
                        (position["symbol"],),
                    )
                    message, event_type = tradeability.reason or "止损退出受阻", tradeability.state
            elif int(position["available_quantity"]) > 0:
                self.conn.execute("UPDATE paper_positions SET pending_exit=1 WHERE symbol=?", (position["symbol"],))
                message, event_type = "可卖股份已被其他模拟委托冻结，登记后续优先退出", "EXIT_DEFERRED_ACTIVE_ORDER"
            else:
                self.conn.execute("UPDATE paper_positions SET pending_exit=1 WHERE symbol=?", (position["symbol"],))
                message, event_type = "触发止损但受T+1限制，登记次日优先退出", "EXIT_DEFERRED_T1"
            event = {
                "event_id": str(uuid4()), "observed_at": observed_at.isoformat(),
                "symbol": position["symbol"], "event_type": event_type,
                "price": price, "message": message,
            }
            self.conn.execute(
                "INSERT INTO monitor_events VALUES(?,?,?,?,?,?)",
                (event["event_id"], event["observed_at"], event["symbol"], event["event_type"], event["price"], event["message"]),
            )
            events.append(event)
        self.conn.commit()
        self.mark_to_market(
            {symbol: float(quote["last_price"]) for symbol, quote in quotes.items()},
            observed_at.date(),
        )
        return events

    def activity(self, limit: int = 100) -> dict[str, Any]:
        """Return bounded persisted paper-account evidence for audit views."""
        orders = [dict(row) for row in self.conn.execute("SELECT * FROM paper_orders ORDER BY created_at DESC LIMIT ?", (limit,))]
        trades = [dict(row) for row in self.conn.execute(
            """SELECT paper_trades.*,paper_orders.created_at,paper_orders.reason
            FROM paper_trades
            LEFT JOIN paper_orders USING(client_order_id)
            ORDER BY COALESCE(paper_orders.created_at,paper_trades.trade_date) DESC
            LIMIT ?""",
            (limit,),
        )]
        events = [dict(row) for row in self.conn.execute("SELECT * FROM monitor_events ORDER BY observed_at DESC LIMIT ?", (limit,))]
        nav = [dict(row) for row in self.conn.execute("SELECT * FROM paper_nav ORDER BY trade_date DESC LIMIT ?", (limit,))]
        return {"orders": orders, "trades": trades, "events": events, "nav": nav}

    def review(
        self,
        limit: int = 200,
        quotes: dict[str, float] | None = None,
        valuation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a review snapshot only from persisted paper positions and activity.

        Fresh read-only quotes take priority when supplied by the workbench.
        Otherwise the latest stored NAV supplies valuation prices; a position
        with neither source falls back to fee-inclusive average cost. Every
        fallback is labeled explicitly and no configured universe participates.
        """
        activity = self.activity(limit)
        latest_nav = activity["nav"][0] if activity["nav"] else None
        stored_positions: dict[str, dict[str, Any]] = {}
        if latest_nav:
            try:
                decoded = json.loads(str(latest_nav.get("positions_json") or "[]"))
            except json.JSONDecodeError:
                decoded = []
            if isinstance(decoded, list):
                stored_positions = {
                    str(item.get("symbol")): item
                    for item in decoded
                    if isinstance(item, dict) and item.get("symbol")
                }

        prices: dict[str, float] = {}
        live_symbols: set[str] = set()
        for position in self.positions():
            stored = stored_positions.get(position["symbol"], {})
            candidate = (quotes or {}).get(position["symbol"])
            if candidate is not None:
                live_symbols.add(position["symbol"])
            else:
                candidate = stored.get("last_price")
            if candidate is None and int(position["quantity"]) > 0:
                market_value = stored.get("market_value")
                if market_value is not None:
                    candidate = float(market_value) / int(position["quantity"])
            try:
                price = float(candidate)
            except (TypeError, ValueError):
                price = float(position["average_cost"])
            prices[position["symbol"]] = price if math.isfinite(price) and price > 0 else float(position["average_cost"])

        account = self.snapshot(prices)
        asset_valuation = account["asset_valuation"]
        initial_cash = float(self.config["initial_cash"])
        enriched_positions: list[dict[str, Any]] = []
        valuation_date = datetime.now(timezone.utc).date()
        if valuation and valuation.get("observed_at"):
            try:
                valuation_date = datetime.fromisoformat(
                    str(valuation["observed_at"])
                ).date()
            except ValueError:
                pass
        elif latest_nav and latest_nav.get("trade_date"):
            valuation_date = date.fromisoformat(str(latest_nav["trade_date"]))
        for position in account["positions"]:
            last_price = float(position["last_price"])
            average_cost = float(position["average_cost"])
            acquired = date.fromisoformat(str(position["acquired_date"]))
            highest = float(position.get("highest_price") or average_cost)
            lowest = float(position.get("lowest_price") or average_cost)
            enriched_positions.append(
                {
                    **position,
                    "valuation_source": (
                        "ths_polling_snapshot"
                        if position["symbol"] in live_symbols
                        else (
                            "latest_paper_nav"
                            if position["symbol"] in stored_positions
                            else "average_cost_fallback"
                        )
                    ),
                    "holding_days": max((valuation_date - acquired).days, 0),
                    "mfe_pct": highest / average_cost - 1 if average_cost > 0 else 0.0,
                    "mae_pct": lowest / average_cost - 1 if average_cost > 0 else 0.0,
                    "stop_price": average_cost
                    * (1 - float(self.config["stop_loss_pct"])),
                    "distance_to_stop_pct": (
                        last_price
                        / (average_cost * (1 - float(self.config["stop_loss_pct"])))
                        - 1
                        if average_cost > 0
                        else 0.0
                    ),
                    "risk_state": (
                        "PENDING_EXIT"
                        if position["pending_exit"]
                        else "ORDER_FROZEN"
                        if int(position.get("frozen_quantity") or 0) > 0
                        else "T1_LOCKED"
                        if int(position["available_quantity"]) <= 0
                        else "LOSS_ALERT"
                        if last_price / average_cost - 1 <= -0.05
                        else "NORMAL"
                    ),
                }
            )

        trades = activity["trades"]
        total_fees = float(asset_valuation["total_fees"])
        realized_pnl = float(asset_valuation["realized_pnl"])
        turnover = float(asset_valuation["turnover"])
        unrealized_pnl = float(asset_valuation["unrealized_pnl"])
        exposure_ratio = float(asset_valuation["exposure_ratio"])
        net_pnl = float(asset_valuation["pnl"])
        pnl_reconciliation_gap = float(asset_valuation["pnl_reconciliation_gap"])
        account_view = {
            **asset_valuation,
            "net_pnl": net_pnl,
            "kill_switch": account["kill_switch"],
            "live_trading_enabled": account["live_trading_enabled"],
            "limits": account["limits"],
        }

        sell_trades = [item for item in trades if item.get("side") == "SELL"]
        winning_trades = [item for item in sell_trades if float(item.get("realized_pnl") or 0) > 0]
        losing_trades = [item for item in sell_trades if float(item.get("realized_pnl") or 0) < 0]
        gross_profit = sum(float(item.get("realized_pnl") or 0) for item in winning_trades)
        gross_loss = abs(sum(float(item.get("realized_pnl") or 0) for item in losing_trades))
        nav_ascending = list(reversed(activity["nav"]))
        previous_equity: float | None = None
        for item in nav_ascending:
            equity = float(item["equity"])
            item["daily_return"] = (
                equity / previous_equity - 1
                if previous_equity is not None and previous_equity > 0
                else 0.0
            )
            previous_equity = equity
        daily_returns = [float(item["daily_return"]) for item in nav_ascending[1:]]
        performance = {
            "net_pnl": net_pnl,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "pnl_reconciliation_gap": pnl_reconciliation_gap,
            "sell_trade_count": len(sell_trades),
            "winning_trade_count": len(winning_trades),
            "losing_trade_count": len(losing_trades),
            "win_rate": len(winning_trades) / len(sell_trades) if sell_trades else None,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
            "average_win": gross_profit / len(winning_trades) if winning_trades else None,
            "average_loss": -gross_loss / len(losing_trades) if losing_trades else None,
            "best_day_return": max(daily_returns) if daily_returns else None,
            "worst_day_return": min(daily_returns) if daily_returns else None,
            "nav_days": len(nav_ascending),
            "turnover_ratio": turnover / initial_cash if initial_cash else 0.0,
        }
        fee_breakdown = {
            "commission": sum(float(item.get("commission") or 0) for item in trades),
            "stamp_tax": sum(float(item.get("stamp_tax") or 0) for item in trades),
            "transfer_fee": sum(float(item.get("transfer_fee") or 0) for item in trades),
            "total": total_fees,
        }
        realized_by_symbol: dict[str, float] = {}
        for item in trades:
            symbol = str(item.get("symbol") or "")
            realized_by_symbol[symbol] = realized_by_symbol.get(symbol, 0.0) + float(
                item.get("realized_pnl") or 0
            )
        position_by_symbol = {item["symbol"]: item for item in enriched_positions}
        attribution = []
        for symbol in sorted(set(realized_by_symbol) | set(position_by_symbol)):
            position = position_by_symbol.get(symbol)
            realized = realized_by_symbol.get(symbol, 0.0)
            unrealized = float(position["unrealized_pnl"]) if position else 0.0
            attribution.append({
                "symbol": symbol,
                "name": position["name"] if position else symbol,
                "realized_pnl": realized,
                "unrealized_pnl": unrealized,
                "total_pnl": realized + unrealized,
                "weight": float(position["weight"]) if position else 0.0,
            })
        attribution.sort(key=lambda item: abs(float(item["total_pnl"])), reverse=True)
        return {
            "account": account_view,
            "asset_valuation": asset_valuation,
            "positions": enriched_positions,
            "activity": activity,
            "nav": nav_ascending,
            "equity_curve": account["equity_curve"],
            "performance": performance,
            "fee_breakdown": fee_breakdown,
            "symbol_attribution": attribution,
            "valuation": valuation or {
                "source": "paper_nav" if latest_nav else "average_cost_fallback",
                "observed_at": None,
                "age_seconds": None,
                "stale": latest_nav is None,
                "message": (
                    f"最近模拟净值日 {latest_nav['trade_date']}"
                    if latest_nav
                    else "没有估值快照，按含费成本估值"
                ),
            },
            "source_nav_date": latest_nav["trade_date"] if latest_nav else None,
        }

    def close(self) -> None:
        self.conn.close()
