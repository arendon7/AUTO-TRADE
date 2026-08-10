from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol

from .brokers.base import BrokerExecution


@dataclass(frozen=True, slots=True)
class BrokerAccountState:
    observed_at: datetime
    state_known: bool
    signed_position_notional_by_symbol: Mapping[str, Decimal]
    open_order_ids: frozenset[str]


class InspectableBroker(Protocol):
    def get_execution(self, order_id: str) -> BrokerExecution | None: ...

    def account_state(self, *, now: datetime) -> BrokerAccountState: ...
