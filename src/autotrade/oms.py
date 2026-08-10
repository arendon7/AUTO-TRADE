from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from .brokers.base import ExecutionBroker
from .domain import (
    MarketSnapshot,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    RiskDecision,
    RiskDecisionStatus,
    intent_fingerprint,
    market_fingerprint,
)
from .ledger import EventLedger, LedgerEvent


class OrderRejectedByControlPlane(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    pass


class BrokerSubmissionAmbiguous(RuntimeError):
    pass


class OrderManagementSystem:
    def __init__(self, *, broker: ExecutionBroker, ledger: EventLedger) -> None:
        self._broker = broker
        self._ledger = ledger
        self._by_idempotency_key: dict[str, OrderRecord] = {}
        self._lock = RLock()

    def submit(
        self,
        *,
        intent: OrderIntent,
        decision: RiskDecision,
        market: MarketSnapshot,
        now: datetime,
    ) -> OrderRecord:
        fingerprint = intent_fingerprint(intent)

        if decision.status is not RiskDecisionStatus.APPROVED:
            raise OrderRejectedByControlPlane(f"risk decision is {decision.status.value}")
        if decision.intent_id != intent.intent_id:
            raise OrderRejectedByControlPlane("risk decision intent mismatch")
        if decision.intent_fingerprint != fingerprint:
            raise OrderRejectedByControlPlane("risk decision fingerprint mismatch")
        if decision.market_fingerprint != market_fingerprint(market):
            raise OrderRejectedByControlPlane("market changed after risk approval")
        if now > decision.valid_until:
            raise OrderRejectedByControlPlane("risk decision expired")

        with self._lock:
            existing = self._by_idempotency_key.get(intent.idempotency_key)
            if existing is not None:
                if intent_fingerprint(existing.intent) != fingerprint:
                    self._ledger.append(
                        LedgerEvent(
                            event_id=f"idem-conflict:{uuid4()}",
                            event_type="IDEMPOTENCY_CONFLICT",
                            occurred_at=now,
                            payload={
                                "idempotency_key": intent.idempotency_key,
                                "existing_order_id": existing.order_id,
                                "new_intent_id": intent.intent_id,
                            },
                        )
                    )
                    raise IdempotencyConflict(intent.idempotency_key)
                return existing

            order = OrderRecord(
                order_id=str(uuid4()),
                intent=intent,
                risk_decision_id=decision.decision_id,
                status=OrderStatus.VALIDATED,
                created_at=now,
            )
            # Reserve the key before broker I/O so concurrent retries cannot submit twice.
            self._by_idempotency_key[intent.idempotency_key] = order
            self._ledger.append(
                LedgerEvent(
                    event_id=f"order-validated:{order.order_id}",
                    event_type="ORDER_VALIDATED",
                    occurred_at=now,
                    payload={
                        "order_id": order.order_id,
                        "intent_id": intent.intent_id,
                        "risk_decision_id": decision.decision_id,
                        "idempotency_key": intent.idempotency_key,
                    },
                )
            )

            try:
                execution = self._broker.submit(order=order, market=market, now=now)
                self._validate_broker_execution(order=order, execution=execution)
            except Exception as exc:
                unknown = replace(order, status=OrderStatus.UNKNOWN, submitted_at=now)
                self._by_idempotency_key[intent.idempotency_key] = unknown
                self._ledger.append(
                    LedgerEvent(
                        event_id=f"order-unknown:{order.order_id}",
                        event_type="ORDER_STATE_UNKNOWN",
                        occurred_at=now,
                        payload={"order_id": order.order_id, "error_type": type(exc).__name__},
                    )
                )
                raise BrokerSubmissionAmbiguous(order.order_id) from exc

            filled_quantity = sum((fill.quantity for fill in execution.fills), Decimal("0"))
            average_fill_price = None
            if filled_quantity > 0:
                fill_value = sum((fill.quantity * fill.price for fill in execution.fills), Decimal("0"))
                average_fill_price = fill_value / filled_quantity

            final = replace(
                order,
                status=execution.status,
                submitted_at=now,
                filled_quantity=filled_quantity,
                average_fill_price=average_fill_price,
            )
            self._by_idempotency_key[intent.idempotency_key] = final
            self._ledger.append(
                LedgerEvent(
                    event_id=f"order-result:{order.order_id}",
                    event_type="ORDER_BROKER_RESULT",
                    occurred_at=now,
                    payload={
                        "order_id": order.order_id,
                        "status": execution.status.value,
                        "filled_quantity": str(filled_quantity),
                        "average_fill_price": str(average_fill_price) if average_fill_price is not None else "",
                    },
                )
            )
            for fill in execution.fills:
                self._ledger.append(
                    LedgerEvent(
                        event_id=f"fill:{fill.fill_id}",
                        event_type="FILL",
                        occurred_at=fill.occurred_at,
                        payload={
                            "fill_id": fill.fill_id,
                            "order_id": fill.order_id,
                            "symbol": fill.symbol,
                            "side": fill.side.value,
                            "quantity": str(fill.quantity),
                            "price": str(fill.price),
                        },
                    )
                )
            return final

    def get_by_idempotency_key(self, key: str) -> OrderRecord | None:
        with self._lock:
            return self._by_idempotency_key.get(key)

    @staticmethod
    def _validate_broker_execution(*, order: OrderRecord, execution) -> None:
        if execution.status not in {
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
        }:
            raise ValueError(f"invalid broker status: {execution.status}")
        total = Decimal("0")
        for fill in execution.fills:
            if fill.order_id != order.order_id:
                raise ValueError("fill order_id mismatch")
            if fill.symbol != order.intent.symbol or fill.side is not order.intent.side:
                raise ValueError("fill instrument/side mismatch")
            if not fill.quantity.is_finite() or fill.quantity <= 0:
                raise ValueError("invalid fill quantity")
            if not fill.price.is_finite() or fill.price <= 0:
                raise ValueError("invalid fill price")
            total += fill.quantity
        if total > order.intent.quantity:
            raise ValueError("broker overfilled order")
        if execution.status is OrderStatus.FILLED and total != order.intent.quantity:
            raise ValueError("FILLED status without full quantity")
        if execution.status is OrderStatus.SUBMITTED and total != 0:
            raise ValueError("SUBMITTED status cannot contain fills")
