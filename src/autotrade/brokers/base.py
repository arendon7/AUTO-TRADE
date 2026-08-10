from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain import Fill, MarketSnapshot, OrderRecord, OrderStatus


@dataclass(frozen=True, slots=True)
class BrokerExecution:
    status: OrderStatus
    fills: tuple[Fill, ...] = ()


class ExecutionBroker(Protocol):
    def submit(self, *, order: OrderRecord, market: MarketSnapshot, now: datetime) -> BrokerExecution: ...
