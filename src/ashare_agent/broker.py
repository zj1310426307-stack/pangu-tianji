from datetime import date
import math
from numbers import Real
from uuid import uuid4

from .models import Order, OrderStatus, Position, Side, Trade
from .risk import RiskEngine


class MockBroker:
    def __init__(
        self,
        initial_cash: float,
        risk_engine: RiskEngine,
        lot_size: int,
        commission_rate: float,
        minimum_commission: float,
        sell_tax_rate: float,
        slippage_rate: float,
        transfer_fee_rate: float = 0.0,
    ) -> None:
        if not self._is_positive_finite(initial_cash):
            raise ValueError("initial_cash必须是有限且大于0的数值")
        if isinstance(lot_size, bool) or not isinstance(lot_size, int) or lot_size <= 0:
            raise ValueError("lot_size必须是大于0的整数")
        self._validate_rate("commission_rate", commission_rate)
        self._validate_nonnegative("minimum_commission", minimum_commission)
        self._validate_rate("sell_tax_rate", sell_tax_rate)
        self._validate_rate("slippage_rate", slippage_rate)
        self._validate_rate("transfer_fee_rate", transfer_fee_rate)

        self.cash = float(initial_cash)
        self.risk_engine = risk_engine
        self.lot_size = lot_size
        self.commission_rate = float(commission_rate)
        self.minimum_commission = float(minimum_commission)
        self.sell_tax_rate = float(sell_tax_rate)
        self.slippage_rate = float(slippage_rate)
        self.transfer_fee_rate = float(transfer_fee_rate)
        self.positions: dict[str, Position] = {}
        self.orders: list[Order] = []
        self.trades: list[Trade] = []

    @staticmethod
    def _is_positive_finite(value: object) -> bool:
        return (
            isinstance(value, Real)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) > 0
        )

    @staticmethod
    def _is_nonnegative_finite(value: object) -> bool:
        return (
            isinstance(value, Real)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0
        )

    @classmethod
    def _validate_nonnegative(cls, name: str, value: object) -> None:
        if not cls._is_nonnegative_finite(value):
            raise ValueError(f"{name}必须是有限且非负数")

    @classmethod
    def _validate_rate(cls, name: str, value: object) -> None:
        cls._validate_nonnegative(name, value)
        if float(value) >= 1:
            raise ValueError(f"{name}必须小于1")

    def _reject(
        self,
        order: Order,
        reason: str,
        status: OrderStatus = OrderStatus.RISK_REJECTED,
    ) -> tuple[Order, None]:
        order.status = status
        order.reject_reason = reason
        self.orders.append(order)
        return order, None

    def market_value(self, prices: dict[str, float]) -> float:
        total = 0.0
        for symbol, position in self.positions.items():
            if position.quantity <= 0:
                continue
            price = prices.get(symbol)
            if not self._is_positive_finite(price):
                raise ValueError(f"{symbol}持仓缺少有限且大于0的行情价格")
            total += position.quantity * float(price)
        if not math.isfinite(total) or total < 0:
            raise ValueError("持仓市值计算结果异常")
        return total

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def position_value(self, symbol: str, price: float) -> float:
        if not self._is_positive_finite(price):
            raise ValueError(f"{symbol}的持仓估值价格必须有限且大于0")
        return self.positions.get(symbol, Position(symbol)).quantity * float(price)

    def new_order(
        self,
        trade_date: date,
        symbol: str,
        side: Side,
        quantity: int,
        requested_price: float,
    ) -> Order:
        return Order(
            client_order_id=str(uuid4()),
            trade_date=trade_date,
            symbol=symbol,
            side=side,
            quantity=quantity,
            requested_price=requested_price,
        )

    def submit(self, order: Order, prices: dict[str, float]) -> tuple[Order, Trade | None]:
        if not self._is_positive_finite(order.requested_price):
            return self._reject(order, "委托价格必须是有限且大于0的数值")

        if (
            isinstance(order.quantity, bool)
            or not isinstance(order.quantity, int)
            or order.quantity <= 0
        ):
            return self._reject(order, "订单数量必须是大于0的整数")

        if order.quantity % self.lot_size != 0:
            return self._reject(order, f"数量不是{self.lot_size}的整数倍")

        quote = prices.get(order.symbol, order.requested_price)
        if not self._is_positive_finite(quote):
            return self._reject(order, "当前行情价格必须是有限且大于0的数值")

        fill_price = float(order.requested_price) * (
            1 + self.slippage_rate if order.side == Side.BUY else 1 - self.slippage_rate
        )
        if not self._is_positive_finite(fill_price):
            return self._reject(order, "考虑滑点后的成交价格异常")

        gross = fill_price * order.quantity
        fee = max(gross * self.commission_rate, self.minimum_commission)
        fee += gross * self.transfer_fee_rate
        if order.side == Side.SELL:
            fee += gross * self.sell_tax_rate
        if not self._is_nonnegative_finite(gross) or not self._is_nonnegative_finite(fee):
            return self._reject(order, "预估成交金额或费用异常")
        if order.side == Side.SELL and fee > gross:
            return self._reject(order, "预估费用超过卖出金额")

        try:
            current_market_value = self.market_value(prices)
            current_symbol_value = self.position_value(order.symbol, float(quote))
        except ValueError as exc:
            return self._reject(order, str(exc))

        decision = self.risk_engine.check(
            order=order,
            cash=self.cash,
            current_market_value=current_market_value,
            current_symbol_value=current_symbol_value,
            estimated_execution_price=fill_price,
            estimated_fee=fee,
        )
        if not decision.approved:
            return self._reject(order, decision.reason)

        self.risk_engine.register_approved(order)
        order.status = OrderStatus.SUBMITTED

        position = self.positions.setdefault(order.symbol, Position(order.symbol))

        if order.side == Side.BUY:
            total_cost = gross + fee
            if total_cost > self.cash:
                return self._reject(
                    order,
                    "考虑费用和滑点后现金不足",
                    status=OrderStatus.REJECTED,
                )

            new_qty = position.quantity + order.quantity
            position.average_cost = (
                position.average_cost * position.quantity + gross + fee
            ) / new_qty
            position.quantity = new_qty
            self.cash -= total_cost
        else:
            if order.quantity > position.quantity:
                return self._reject(
                    order,
                    "卖出数量超过持仓",
                    status=OrderStatus.REJECTED,
                )
            position.quantity -= order.quantity
            self.cash += gross - fee
            if position.quantity == 0:
                position.average_cost = 0.0

        order.status = OrderStatus.FILLED
        trade = Trade(
            client_order_id=order.client_order_id,
            trade_date=order.trade_date,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            fill_price=fill_price,
            fee=fee,
        )
        self.orders.append(order)
        self.trades.append(trade)
        return order, trade
