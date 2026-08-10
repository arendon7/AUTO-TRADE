from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol

from .market import Bar


@dataclass(frozen=True, slots=True)
class ResearchSignal:
    signal_id: str
    symbol: str
    generated_at: datetime
    quantity_delta: Decimal
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.signal_id.strip():
            raise ValueError("signal_id is required")
        if not self.symbol.strip():
            raise ValueError("signal symbol is required")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("signal timestamp must be timezone-aware")
        if not self.quantity_delta.is_finite() or self.quantity_delta == 0:
            raise ValueError("quantity_delta must be finite and non-zero")


@dataclass(frozen=True, slots=True)
class StrategyContext:
    symbol: str
    index: int
    history: tuple[Bar, ...]
    current_position_quantity: Decimal
    current_equity: Decimal

    @property
    def current_bar(self) -> Bar:
        return self.history[-1]


class ResearchStrategy(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def strategy_version(self) -> str: ...

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]: ...

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None: ...
