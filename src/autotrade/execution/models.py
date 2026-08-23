from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from decimal import Decimal


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    asset: str
    side: str
    quantity: Decimal
    limit_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    strategy: str
    environment: str = "PAPER"
    status: OrderStatus = OrderStatus.CREATED

    def validate_environment(self) -> None:
        if self.environment != "PAPER":
            raise ValueError("LIVE execution is permanently blocked in WAVE 78")


@dataclass(frozen=True)
class Fill:
    order_id: str
    filled_quantity: Decimal
    fill_price: Decimal
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PaperPosition:
    asset: str
    quantity: Decimal
    entry_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    status: PositionStatus = PositionStatus.OPEN

    def unrealized_pnl(self, price: Decimal) -> Decimal:
        return (price - self.entry_price) * self.quantity
