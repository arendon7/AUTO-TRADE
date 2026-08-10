from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Mapping


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> Decimal:
        return Decimal("1") if self is Side.BUY else Decimal("-1")


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class RiskDecisionStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OrderStatus(StrEnum):
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: str
    idempotency_key: str
    strategy_id: str
    symbol: str
    side: Side
    quantity: Decimal
    order_type: OrderType
    created_at: datetime
    limit_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_id: str
    intent_id: str
    status: RiskDecisionStatus
    reason_code: str
    reason_detail: str
    evaluated_at: datetime
    valid_until: datetime
    limits_version: str
    intent_fingerprint: str
    market_fingerprint: str
    approved_notional: Decimal | None = None
    risk_reducing: bool = False


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    snapshot_id: str
    equity: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    daily_pnl: Decimal
    drawdown: Decimal
    open_orders: int
    signed_position_notional_by_symbol: Mapping[str, Decimal]
    strategy_gross_exposure: Mapping[str, Decimal]
    strategy_signed_position_notional_by_symbol: Mapping[str, Mapping[str, Decimal]]
    reconciliation_ok: bool
    broker_state_known: bool


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OrderRecord:
    order_id: str
    intent: OrderIntent
    risk_decision_id: str
    status: OrderStatus
    created_at: datetime
    submitted_at: datetime | None = None
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None


def _canonical_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def intent_fingerprint(intent: OrderIntent) -> str:
    payload = {
        "intent_id": intent.intent_id,
        "idempotency_key": intent.idempotency_key,
        "strategy_id": intent.strategy_id,
        "symbol": intent.symbol,
        "side": intent.side.value,
        "quantity": _canonical_decimal(intent.quantity),
        "order_type": intent.order_type.value,
        "limit_price": _canonical_decimal(intent.limit_price),
        "created_at": intent.created_at.isoformat(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()


def market_fingerprint(market: MarketSnapshot) -> str:
    payload = {
        "symbol": market.symbol,
        "bid": _canonical_decimal(market.bid),
        "ask": _canonical_decimal(market.ask),
        "last": _canonical_decimal(market.last),
        "observed_at": market.observed_at.isoformat(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()
