from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain import Fill, MarketSnapshot, OrderRecord, OrderStatus


@dataclass(frozen=True, slots=True)
class BrokerExecution:
    """Authoritative cumulative execution snapshot for one local order."""

    status: OrderStatus
    fills: tuple[Fill, ...] = ()


class ExecutionBroker(Protocol):
    def submit(self, *, order: OrderRecord, market: MarketSnapshot, now: datetime) -> BrokerExecution: ...


class CancelableExecutionBroker(ExecutionBroker, Protocol):
    def cancel(self, *, order_id: str, now: datetime) -> BrokerExecution: ...
