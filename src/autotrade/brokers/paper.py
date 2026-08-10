from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from ..domain import Fill, MarketSnapshot, OrderRecord, OrderStatus, OrderType, Side
from .base import BrokerExecution


class PaperBroker:
    """Deterministic paper broker.

    MARKET fills at current touch. LIMIT fills only when marketable at the
    supplied snapshot. This is deliberately simple and is not a backtest fill
    model.
    """

    def __init__(self) -> None:
        self._submission_count = 0

    @property
    def submission_count(self) -> int:
        return self._submission_count

    def submit(self, *, order: OrderRecord, market: MarketSnapshot, now: datetime) -> BrokerExecution:
        if order.status is not OrderStatus.VALIDATED:
            raise ValueError("paper broker only accepts VALIDATED orders")
        if market.symbol != order.intent.symbol:
            raise ValueError("market/order symbol mismatch")

        self._submission_count += 1
        intent = order.intent

        if intent.order_type is OrderType.MARKET:
            fill_price = market.ask if intent.side is Side.BUY else market.bid
            return BrokerExecution(
                status=OrderStatus.FILLED,
                fills=(self._fill(order=order, price=fill_price, now=now),),
            )

        assert intent.limit_price is not None
        marketable = (
            intent.side is Side.BUY and intent.limit_price >= market.ask
        ) or (
            intent.side is Side.SELL and intent.limit_price <= market.bid
        )
        if not marketable:
            return BrokerExecution(status=OrderStatus.SUBMITTED)

        fill_price = market.ask if intent.side is Side.BUY else market.bid
        return BrokerExecution(
            status=OrderStatus.FILLED,
            fills=(self._fill(order=order, price=fill_price, now=now),),
        )

    @staticmethod
    def _fill(*, order: OrderRecord, price: Decimal, now: datetime) -> Fill:
        return Fill(
            fill_id=str(uuid4()),
            order_id=order.order_id,
            symbol=order.intent.symbol,
            side=order.intent.side,
            quantity=order.intent.quantity,
            price=price,
            occurred_at=now,
        )
