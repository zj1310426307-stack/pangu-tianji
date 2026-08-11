from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any


@dataclass(frozen=True)
class Tradeability:
    """Describe whether a quote can be used for a simulated fill and why."""

    tradable: bool
    state: str
    reason: str | None
    upper_limit_price: float | None = None
    lower_limit_price: float | None = None
    limit_source: str = "unavailable"


def _finite_positive(value: object) -> float | None:
    """Convert a quote field to a finite positive number when possible."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _round_price(value: float) -> float:
    """Round an A-share reference limit to the ordinary one-cent price tick."""
    return round(value + 1e-12, 2)


def infer_price_limits(quote: dict[str, Any]) -> tuple[float | None, float | None, str]:
    """Prefer supplier limits and otherwise derive ordinary-main-board limits.

    Derived limits are used only when the quote explicitly identifies an
    ordinary main-board security outside its first five trading days. Unknown
    listing age is not silently treated as an ordinary 10% security.
    """
    upper = _finite_positive(quote.get("upper_limit_price"))
    lower = _finite_positive(quote.get("lower_limit_price"))
    if upper and lower:
        return upper, lower, "supplier"

    previous_close = _finite_positive(quote.get("prev_price") or quote.get("previous_close"))
    try:
        listing_days = int(quote.get("listing_days"))
    except (TypeError, ValueError):
        listing_days = -1
    is_st = bool(quote.get("is_st"))
    board = str(quote.get("board") or "mainboard").lower()
    if previous_close and listing_days >= 5 and board == "mainboard":
        ratio = 0.05 if is_st else 0.10
        return (
            _round_price(previous_close * (1 + ratio)),
            _round_price(previous_close * (1 - ratio)),
            "derived_mainboard",
        )
    return None, None, "unavailable"


def assess_tradeability(quote: dict[str, Any], side: str) -> Tradeability:
    """Evaluate suspension, quote validity and limit-lock fill constraints."""
    normalized_side = side.upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError("side必须是BUY或SELL")

    status = str(quote.get("security_status") or quote.get("status") or "").lower()
    if bool(quote.get("suspended")) or status in {"suspended", "halted", "停牌"}:
        return Tradeability(False, "SUSPENDED", "标的停牌或状态不可交易")

    price = _finite_positive(quote.get("last_price") or quote.get("price"))
    volume = _finite_positive(quote.get("volume"))
    if price is None or volume is None:
        return Tradeability(False, "NO_VALID_QUOTE", "标的没有有效价格或成交量")

    upper, lower, source = infer_price_limits(quote)
    tolerance = 0.005
    if normalized_side == "BUY" and upper is not None and price >= upper - tolerance:
        return Tradeability(False, "BUY_BLOCKED_LIMIT_UP", "涨停或封板状态禁止模拟买入", upper, lower, source)
    if normalized_side == "SELL" and lower is not None and price <= lower + tolerance:
        return Tradeability(False, "EXIT_BLOCKED_LIMIT_DOWN", "跌停状态无法假定止损成交", upper, lower, source)

    # When the supplier omits limit prices and listing age, retain a conservative
    # fallback instead of pretending the order is certainly executable.
    if source == "unavailable":
        try:
            change = abs(float(quote.get("price_change_ratio_pct")))
        except (TypeError, ValueError):
            change = math.inf
        if not math.isfinite(change):
            return Tradeability(False, "LIMIT_DATA_MISSING", "涨跌停参考信息不完整")
        if change >= 9.5:
            state = "BUY_BLOCKED_LIMIT_UP" if normalized_side == "BUY" else "EXIT_BLOCKED_LIMIT_DOWN"
            reason = "接近涨跌停且缺少明确价格限制，禁止假定成交"
            return Tradeability(False, state, reason, upper, lower, source)

    return Tradeability(True, "TRADABLE", None, upper, lower, source)

