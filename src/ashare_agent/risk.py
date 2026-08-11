from dataclasses import dataclass
from datetime import date
import math
from numbers import Real

from .models import Order, Side


@dataclass
class RiskDecision:
    """Represent a deterministic risk approval or rejection."""

    approved: bool
    reason: str


class RiskEngine:
    """Apply hard, model-independent checks before simulated execution."""

    def __init__(self, config: dict, whitelist: list[str], initial_cash: float) -> None:
        if not self._is_positive_finite(initial_cash):
            raise ValueError("initial_cash必须是有限且大于0的数值")
        self.config = config
        self.whitelist = set(whitelist)
        self.initial_cash = float(initial_cash)
        self.order_ids: set[str] = set()
        self.daily_order_count: dict[date, int] = {}

    @staticmethod
    def _is_positive_finite(value: object) -> bool:
        """Return whether a boundary value is numeric, finite, and positive."""
        return (
            isinstance(value, Real)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) > 0
        )

    @staticmethod
    def _is_nonnegative_finite(value: object) -> bool:
        """Return whether an account or fee value is finite and non-negative."""
        return (
            isinstance(value, Real)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0
        )

    def check(
        self,
        order: Order,
        cash: float,
        current_market_value: float,
        current_symbol_value: float,
        estimated_execution_price: float | None = None,
        estimated_fee: float = 0.0,
    ) -> RiskDecision:
        """Evaluate one order using its conservative estimated execution cost."""
        if self.config["kill_switch"]:
            return RiskDecision(False, "停止开关已启用")

        if self.config["reject_unknown_symbols"] and order.symbol not in self.whitelist:
            return RiskDecision(False, "证券不在白名单")

        if (
            isinstance(order.quantity, bool)
            or not isinstance(order.quantity, int)
            or order.quantity <= 0
        ):
            return RiskDecision(False, "订单数量必须大于0")

        if not self._is_positive_finite(order.requested_price):
            return RiskDecision(False, "委托价格必须是有限且大于0的数值")

        execution_price = (
            order.requested_price
            if estimated_execution_price is None
            else estimated_execution_price
        )
        if not self._is_positive_finite(execution_price):
            return RiskDecision(False, "预估成交价格必须是有限且大于0的数值")
        if not self._is_nonnegative_finite(estimated_fee):
            return RiskDecision(False, "预估费用必须是有限且非负数")
        if not all(
            self._is_nonnegative_finite(value)
            for value in (cash, current_market_value, current_symbol_value)
        ):
            return RiskDecision(False, "账户资金或持仓市值数据异常")

        if self.config["reject_duplicate_orders"] and order.client_order_id in self.order_ids:
            return RiskDecision(False, "检测到重复订单编号")

        count = self.daily_order_count.get(order.trade_date, 0)
        if count >= int(self.config["max_orders_per_day"]):
            return RiskDecision(False, "超过每日订单数量上限")

        order_value = order.quantity * float(execution_price)
        if not math.isfinite(order_value) or order_value <= 0:
            return RiskDecision(False, "订单估值必须是有限且大于0的数值")
        if order.side == Side.BUY:
            account_equity = max(cash + current_market_value, 0.0)
            exposure_base = account_equity or self.initial_cash
            required_cash = order_value + float(estimated_fee)
            if required_cash > exposure_base * float(
                self.config["max_order_value_pct"]
            ):
                return RiskDecision(False, "单笔订单金额超过上限")
            if required_cash > cash:
                return RiskDecision(False, "可用现金不足")

            projected_total = current_market_value + order_value
            if projected_total > exposure_base * float(
                self.config["max_total_exposure_pct"]
            ):
                return RiskDecision(False, "预计总仓位超过上限")

            projected_symbol = current_symbol_value + order_value
            if projected_symbol > exposure_base * float(
                self.config["max_single_position_pct"]
            ):
                return RiskDecision(False, "预计单标的仓位超过上限")

        return RiskDecision(True, "通过")

    def register_approved(self, order: Order) -> None:
        """Record an approved client id and its daily order count."""
        self.order_ids.add(order.client_order_id)
        self.daily_order_count[order.trade_date] = (
            self.daily_order_count.get(order.trade_date, 0) + 1
        )
