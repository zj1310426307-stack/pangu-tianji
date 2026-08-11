from pathlib import Path
import json
import sqlite3

from .models import Order, Signal, Trade


class Storage:
    """Persist one CLI run to the working SQLite database."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
                trade_date TEXT,
                symbol TEXT,
                score REAL,
                target_weight REAL,
                reason TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                client_order_id TEXT PRIMARY KEY,
                trade_date TEXT,
                symbol TEXT,
                side TEXT,
                quantity INTEGER,
                requested_price REAL,
                status TEXT,
                reject_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS trades (
                client_order_id TEXT,
                trade_date TEXT,
                symbol TEXT,
                side TEXT,
                quantity INTEGER,
                fill_price REAL,
                fee REAL
            );
            CREATE TABLE IF NOT EXISTS daily_equity (
                trade_date TEXT PRIMARY KEY,
                cash REAL,
                market_value REAL,
                equity REAL,
                positions_json TEXT
            );
            """
        )
        self.conn.commit()

    def clear_run_data(self) -> None:
        self.conn.executescript(
            """
            DELETE FROM signals;
            DELETE FROM orders;
            DELETE FROM trades;
            DELETE FROM daily_equity;
            """
        )
        self.conn.commit()

    def save_signal(self, signal: Signal) -> None:
        self.conn.execute(
            "INSERT INTO signals VALUES (?, ?, ?, ?, ?)",
            (
                signal.trade_date.isoformat(),
                signal.symbol,
                signal.score,
                signal.target_weight,
                signal.reason,
            ),
        )
        self.conn.commit()

    def save_order(self, order: Order) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order.client_order_id,
                order.trade_date.isoformat(),
                order.symbol,
                order.side.value,
                order.quantity,
                order.requested_price,
                order.status.value,
                order.reject_reason,
            ),
        )
        self.conn.commit()

    def save_trade(self, trade: Trade) -> None:
        self.conn.execute(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                trade.client_order_id,
                trade.trade_date.isoformat(),
                trade.symbol,
                trade.side.value,
                trade.quantity,
                trade.fill_price,
                trade.fee,
            ),
        )
        self.conn.commit()

    def save_equity(
        self,
        trade_date,
        cash: float,
        market_value: float,
        equity: float,
        positions: dict,
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO daily_equity VALUES (?, ?, ?, ?, ?)",
            (
                trade_date.isoformat(),
                cash,
                market_value,
                equity,
                json.dumps(positions, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self.conn.close()

    def __enter__(self) -> "Storage":
        """Return this storage instance for exception-safe use."""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Always release SQLite resources when a run exits."""
        self.close()
