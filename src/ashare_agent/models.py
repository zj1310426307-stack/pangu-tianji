from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    RISK_REJECTED = "RISK_REJECTED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass
class Signal:
    trade_date: date
    symbol: str
    score: float
    target_weight: float
    reason: str


@dataclass
class Order:
    client_order_id: str
    trade_date: date
    symbol: str
    side: Side
    quantity: int
    requested_price: float
    status: OrderStatus = OrderStatus.CREATED
    reject_reason: Optional[str] = None


@dataclass
class Trade:
    client_order_id: str
    trade_date: date
    symbol: str
    side: Side
    quantity: int
    fill_price: float
    fee: float


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    average_cost: float = 0.0
