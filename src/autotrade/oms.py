from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from .brokers.base import BrokerExecution, ExecutionBroker
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
from .state import InMemoryOrderStore, OrderStore


class OrderRejectedByControlPlane(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    pass


class BrokerSubmissionAmbiguous(RuntimeError):
    pass


class OrderManagementSystem:
    def __init__(
        self,
        *,
        broker: ExecutionBroker,
        ledger: EventLedger,
        order_store: OrderStore | None = None,
    ) -> None:
        self._broker = broker
        self._ledger = ledger
        self._orders = order_store or InMemoryOrderStore()

    def submit(
        self,
        *,
        intent: OrderIntent,
        decision: RiskDecision,
        market: MarketSnapshot,
        now: datetime,
    ) -> OrderRecord:
        fingerprint = intent_fingerprint(intent)
        self._validate_control_plane(
            intent=intent,
            decision=decision,
            market=market,
            now=now,
            fingerprint=fingerprint,
        )

        candidate = OrderRecord(
            order_id=str(uuid4()),
            intent=intent,
            risk_decision_id=decision.decision_id,
            status=OrderStatus.VALIDATED,
            created_at=now,
        )
        created, stored = self._orders.create_if_absent(candidate)
        if not created:
            if intent_fingerprint(stored.intent) != fingerprint:
                self._ledger.append(
                    LedgerEvent(
                        event_id=f"idem-conflict:{uuid4()}",
                        event_type="IDEMPOTENCY_CONFLICT",
                        occurred_at=now,
                        payload={
                            "idempotency_key": intent.idempotency_key,
                            "existing_order_id": stored.order_id,
                            "new_intent_id": intent.intent_id,
                        },
                    )
                )
                raise IdempotencyConflict(intent.idempotency_key)
            return stored

        order = stored
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

        # Persist ambiguity before broker I/O. If the process dies after this
        # point, recovery must reconcile by client/order id before new risk.
        submitting = replace(order, status=OrderStatus.SUBMITTING, submitted_at=now)
        self._orders.update(submitting)

        try:
            # The broker receives the validated immutable order; SUBMITTING is
            # an OMS persistence state, not a broker-facing order status.
            execution = self._broker.submit(order=order, market=market, now=now)
            self._validate_broker_execution(order=order, execution=execution)
        except Exception as exc:
            unknown = replace(order, status=OrderStatus.UNKNOWN, submitted_at=now)
            self._orders.update(unknown)
            self._ledger.append(
                LedgerEvent(
                    event_id=f"order-unknown:{order.order_id}",
                    event_type="ORDER_STATE_UNKNOWN",
                    occurred_at=now,
                    payload={"order_id": order.order_id, "error_type": type(exc).__name__},
                )
            )
            raise BrokerSubmissionAmbiguous(order.order_id) from exc

        final = self._finalize_execution(order=order, execution=execution, now=now)
        self._orders.update(final)
        self._record_execution(final=final, execution=execution, now=now, recovered=False)
        return final

    def reconcile_from_broker(
        self,
        *,
        order_id: str,
        execution: BrokerExecution,
        now: datetime,
    ) -> OrderRecord:
        order = self._orders.get_by_order_id(order_id)
        if order is None:
            raise KeyError(order_id)
        if order.status not in {OrderStatus.SUBMITTING, OrderStatus.UNKNOWN}:
            return order

        validated_view = replace(order, status=OrderStatus.VALIDATED)
        self._validate_broker_execution(order=validated_view, execution=execution)
        final = self._finalize_execution(order=validated_view, execution=execution, now=now)
        self._orders.update(final)
        self._record_execution(final=final, execution=execution, now=now, recovered=True)
        return final

    def get_by_idempotency_key(self, key: str) -> OrderRecord | None:
        return self._orders.get_by_idempotency_key(key)

    def get_by_order_id(self, order_id: str) -> OrderRecord | None:
        return self._orders.get_by_order_id(order_id)

    def all_orders(self) -> tuple[OrderRecord, ...]:
        return self._orders.all_orders()

    @staticmethod
    def _validate_control_plane(
        *,
        intent: OrderIntent,
        decision: RiskDecision,
        market: MarketSnapshot,
        now: datetime,
        fingerprint: str,
    ) -> None:
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

    @staticmethod
    def _finalize_execution(
        *, order: OrderRecord, execution: BrokerExecution, now: datetime
    ) -> OrderRecord:
        filled_quantity = sum((fill.quantity for fill in execution.fills), Decimal("0"))
        average_fill_price = None
        if filled_quantity > 0:
            fill_value = sum(
                (fill.quantity * fill.price for fill in execution.fills), Decimal("0")
            )
            average_fill_price = fill_value / filled_quantity
        return replace(
            order,
            status=execution.status,
            submitted_at=order.submitted_at or now,
            filled_quantity=filled_quantity,
            average_fill_price=average_fill_price,
        )

    def _record_execution(
        self,
        *,
        final: OrderRecord,
        execution: BrokerExecution,
        now: datetime,
        recovered: bool,
    ) -> None:
        self._ledger.append(
            LedgerEvent(
                event_id=f"order-result:{final.order_id}",
                event_type="ORDER_BROKER_RESULT",
                occurred_at=now,
                payload={
                    "order_id": final.order_id,
                    "status": execution.status.value,
                    "filled_quantity": str(final.filled_quantity),
                    "average_fill_price": (
                        str(final.average_fill_price) if final.average_fill_price is not None else ""
                    ),
                    "recovered": str(recovered).lower(),
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

    @staticmethod
    def _validate_broker_execution(*, order: OrderRecord, execution: BrokerExecution) -> None:
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
