from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from uuid import uuid4

from .brokers.base import BrokerExecution, ExecutionBroker
from .domain import (
    Fill,
    MarketSnapshot,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    RiskDecision,
    RiskDecisionStatus,
    intent_fingerprint,
    market_fingerprint,
)
from .execution_state import FillIntegrityConflict, FillStore, InMemoryFillStore, fill_fingerprint
from .ledger import DuplicateLedgerEvent, EventLedger, LedgerEvent
from .state import InMemoryOrderStore, OrderStore, SafetyStateStore


class OrderRejectedByControlPlane(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    pass


class BrokerSubmissionAmbiguous(RuntimeError):
    pass


class BrokerCancellationAmbiguous(RuntimeError):
    pass


class BrokerCancellationUnsupported(RuntimeError):
    pass


class BrokerStateConflict(RuntimeError):
    pass


class OrderManagementSystem:
    def __init__(
        self,
        *,
        broker: ExecutionBroker,
        ledger: EventLedger,
        order_store: OrderStore | None = None,
        safety_state_store: SafetyStateStore | None = None,
        fill_store: FillStore | None = None,
    ) -> None:
        self._broker = broker
        self._ledger = ledger
        self._orders = order_store or InMemoryOrderStore()
        self._safety_state_store = safety_state_store
        self._fills = fill_store or InMemoryFillStore()

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
                self._append_idempotent(
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
        self._append_idempotent(
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

        submitting = replace(order, status=OrderStatus.SUBMITTING, submitted_at=now)
        self._orders.update(submitting)

        try:
            execution = self._broker.submit(order=order, market=market, now=now)
            return self._apply_broker_snapshot(
                order=submitting,
                execution=execution,
                now=now,
                recovered=False,
            )
        except Exception as exc:
            # SystemExit/BaseException intentionally remains a crash point with
            # SUBMITTING durably persisted, preserving recovery semantics.
            unknown = replace(submitting, status=OrderStatus.UNKNOWN)
            self._orders.update(unknown)
            self._append_idempotent(
                LedgerEvent(
                    event_id=f"order-unknown:{order.order_id}:submit",
                    event_type="ORDER_STATE_UNKNOWN",
                    occurred_at=now,
                    payload={"order_id": order.order_id, "operation": "submit", "error_type": type(exc).__name__},
                )
            )
            raise BrokerSubmissionAmbiguous(order.order_id) from exc

    def sync_from_broker(
        self,
        *,
        order_id: str,
        execution: BrokerExecution,
        now: datetime,
        recovered: bool = True,
    ) -> OrderRecord:
        order = self._orders.get_by_order_id(order_id)
        if order is None:
            raise KeyError(order_id)
        if order.status.terminal:
            self._validate_terminal_replay(order=order, execution=execution)
            self._record_execution(
                final=order,
                all_fills=execution.fills,
                now=now,
                recovered=recovered,
                changed=False,
            )
            return order
        if not order.status.broker_open:
            raise BrokerStateConflict(f"order {order_id} is not broker-open: {order.status.value}")
        return self._apply_broker_snapshot(
            order=order,
            execution=execution,
            now=now,
            recovered=recovered,
        )

    def reconcile_from_broker(
        self,
        *,
        order_id: str,
        execution: BrokerExecution,
        now: datetime,
    ) -> OrderRecord:
        return self.sync_from_broker(order_id=order_id, execution=execution, now=now, recovered=True)

    def cancel(self, *, order_id: str, now: datetime) -> OrderRecord:
        order = self._orders.get_by_order_id(order_id)
        if order is None:
            raise KeyError(order_id)
        if order.status.terminal:
            return order
        cancel_fn = getattr(self._broker, "cancel", None)
        if cancel_fn is None:
            raise BrokerCancellationUnsupported(type(self._broker).__name__)
        if not order.status.broker_open:
            raise BrokerStateConflict(f"order {order_id} cannot be cancelled from {order.status.value}")

        pending = replace(order, status=OrderStatus.CANCEL_PENDING)
        self._orders.update(pending)
        self._append_idempotent(
            LedgerEvent(
                event_id=f"cancel-requested:{order.order_id}",
                event_type="ORDER_CANCEL_REQUESTED",
                occurred_at=now,
                payload={"order_id": order.order_id},
            )
        )
        try:
            execution = cancel_fn(order_id=order.order_id, now=now)
            return self._apply_broker_snapshot(
                order=pending,
                execution=execution,
                now=now,
                recovered=False,
            )
        except Exception as exc:
            unknown = replace(pending, status=OrderStatus.UNKNOWN)
            self._orders.update(unknown)
            self._append_idempotent(
                LedgerEvent(
                    event_id=f"order-unknown:{order.order_id}:cancel",
                    event_type="ORDER_STATE_UNKNOWN",
                    occurred_at=now,
                    payload={"order_id": order.order_id, "operation": "cancel", "error_type": type(exc).__name__},
                )
            )
            raise BrokerCancellationAmbiguous(order.order_id) from exc

    def mark_replace_pending(
        self,
        *,
        order_id: str,
        replacement_intent_id: str,
        now: datetime,
    ) -> OrderRecord:
        if not replacement_intent_id.strip():
            raise ValueError("replacement_intent_id is required")
        order = self._orders.get_by_order_id(order_id)
        if order is None:
            raise KeyError(order_id)
        if order.status not in {
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.REPLACE_PENDING,
        }:
            raise BrokerStateConflict(
                f"order {order_id} cannot enter replace from {order.status.value}"
            )
        self._append_idempotent(
            LedgerEvent(
                event_id=f"replace-requested:{order.order_id}",
                event_type="ORDER_REPLACE_REQUESTED",
                occurred_at=now,
                payload={
                    "order_id": order.order_id,
                    "replacement_intent_id": replacement_intent_id,
                },
            )
        )
        if order.status is OrderStatus.REPLACE_PENDING:
            return order
        pending = replace(order, status=OrderStatus.REPLACE_PENDING)
        self._orders.update(pending)
        return pending

    def replacement_request_matches(
        self, *, order_id: str, replacement_intent_id: str
    ) -> bool:
        event_id = f"replace-requested:{order_id}"
        for event in self._ledger.all_events():
            if event.event_id != event_id:
                continue
            if event.event_type != "ORDER_REPLACE_REQUESTED":
                raise BrokerStateConflict(
                    f"replace event identity has wrong type: {event.event_type}"
                )
            existing = event.payload.get("replacement_intent_id", "")
            if existing != replacement_intent_id:
                raise BrokerStateConflict(
                    f"replacement intent conflict for order {order_id}"
                )
            return True
        return False

    def fills_for_order(self, order_id: str) -> tuple[Fill, ...]:
        return self._fills.fills_for_order(order_id)

    def get_by_idempotency_key(self, key: str) -> OrderRecord | None:
        return self._orders.get_by_idempotency_key(key)

    def get_by_order_id(self, order_id: str) -> OrderRecord | None:
        return self._orders.get_by_order_id(order_id)

    def all_orders(self) -> tuple[OrderRecord, ...]:
        return self._orders.all_orders()

    def _apply_broker_snapshot(
        self,
        *,
        order: OrderRecord,
        execution: BrokerExecution,
        now: datetime,
        recovered: bool,
    ) -> OrderRecord:
        self._validate_broker_execution_snapshot(order=order, execution=execution)

        existing = self._fills.fills_for_order(order.order_id)
        existing_by_id = {fill.fill_id: fill for fill in existing}
        snapshot_by_id = {fill.fill_id: fill for fill in execution.fills}
        missing = set(existing_by_id) - set(snapshot_by_id)
        if missing:
            raise BrokerStateConflict(
                f"broker snapshot lost previously observed fills: {sorted(missing)}"
            )
        for fill_id, old_fill in existing_by_id.items():
            if fill_fingerprint(old_fill) != fill_fingerprint(snapshot_by_id[fill_id]):
                raise FillIntegrityConflict(fill_id)

        for fill in execution.fills:
            self._fills.record(fill)

        all_fills = self._fills.fills_for_order(order.order_id)
        self._validate_cumulative_status(order=order, status=execution.status, fills=all_fills)

        effective_status = execution.status
        if order.status in {OrderStatus.CANCEL_PENDING, OrderStatus.REPLACE_PENDING} and execution.status in {
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        }:
            effective_status = order.status
        self._validate_transition(current=order.status, target=effective_status)

        total = sum((fill.quantity for fill in all_fills), Decimal("0"))
        average = None
        if total > 0:
            value = sum((fill.quantity * fill.price for fill in all_fills), Decimal("0"))
            average = value / total
        final = replace(
            order,
            status=effective_status,
            submitted_at=order.submitted_at or now,
            filled_quantity=total,
            average_fill_price=average,
        )
        changed = final != order
        if changed:
            self._orders.update(final)
        self._record_execution(
            final=final,
            all_fills=all_fills,
            now=now,
            recovered=recovered,
            changed=changed,
        )
        return final

    def _validate_control_plane(
        self,
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
        if self._safety_state_store is not None:
            current = self._safety_state_store.get()
            if current.version != decision.safety_state_version:
                raise OrderRejectedByControlPlane("safety state changed after risk approval")

    def _record_execution(
        self,
        *,
        final: OrderRecord,
        all_fills: tuple[Fill, ...],
        now: datetime,
        recovered: bool,
        changed: bool,
    ) -> None:
        # Replay all fill ledger events idempotently. This repairs a crash after
        # fill-store commit but before ledger append without double-accounting.
        for fill in all_fills:
            self._append_idempotent(
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
        snapshot_key = json.dumps(
            {
                "status": final.status.value,
                "filled_quantity": str(final.filled_quantity),
                "average_fill_price": str(final.average_fill_price) if final.average_fill_price is not None else "",
                "fill_ids": [fill.fill_id for fill in all_fills],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot_hash = sha256(snapshot_key.encode("utf-8")).hexdigest()[:20]
        self._append_idempotent(
            LedgerEvent(
                event_id=f"order-result:{final.order_id}:{snapshot_hash}",
                event_type="ORDER_BROKER_RESULT",
                occurred_at=now,
                payload={
                    "order_id": final.order_id,
                    "status": final.status.value,
                    "filled_quantity": str(final.filled_quantity),
                    "average_fill_price": (
                        str(final.average_fill_price) if final.average_fill_price is not None else ""
                    ),
                    "recovered": str(recovered).lower(),
                },
            )
        )

    def _append_idempotent(self, event: LedgerEvent) -> None:
        try:
            self._ledger.append(event)
        except DuplicateLedgerEvent:
            for existing in self._ledger.all_events():
                if existing.event_id == event.event_id:
                    if (
                        existing.event_type != event.event_type
                        or existing.occurred_at != event.occurred_at
                        or dict(existing.payload) != dict(event.payload)
                    ):
                        raise BrokerStateConflict(
                            f"ledger event identity conflict: {event.event_id}"
                        )
                    return
            raise

    @staticmethod
    def _validate_broker_execution_snapshot(*, order: OrderRecord, execution: BrokerExecution) -> None:
        if execution.status not in {
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }:
            raise ValueError(f"invalid broker status: {execution.status}")
        seen: set[str] = set()
        for fill in execution.fills:
            if fill.fill_id in seen:
                raise ValueError("duplicate fill_id in broker snapshot")
            seen.add(fill.fill_id)
            if fill.order_id != order.order_id:
                raise ValueError("fill order_id mismatch")
            if fill.symbol != order.intent.symbol or fill.side is not order.intent.side:
                raise ValueError("fill instrument/side mismatch")
            if not fill.quantity.is_finite() or fill.quantity <= 0:
                raise ValueError("invalid fill quantity")
            if not fill.price.is_finite() or fill.price <= 0:
                raise ValueError("invalid fill price")
            if fill.occurred_at.tzinfo is None or fill.occurred_at.utcoffset() is None:
                raise ValueError("fill timestamp must be timezone-aware")
        OrderManagementSystem._validate_cumulative_status(
            order=order,
            status=execution.status,
            fills=execution.fills,
        )

    @staticmethod
    def _validate_cumulative_status(
        *, order: OrderRecord, status: OrderStatus, fills: tuple[Fill, ...]
    ) -> None:
        total = sum((fill.quantity for fill in fills), Decimal("0"))
        if total > order.intent.quantity:
            raise ValueError("broker overfilled order")
        if status is OrderStatus.SUBMITTED and total != 0:
            raise ValueError("SUBMITTED status cannot contain fills")
        if status is OrderStatus.PARTIALLY_FILLED and not Decimal("0") < total < order.intent.quantity:
            raise ValueError("PARTIALLY_FILLED requires quantity between zero and intent quantity")
        if status is OrderStatus.FILLED and total != order.intent.quantity:
            raise ValueError("FILLED status without full quantity")
        if status is OrderStatus.REJECTED and total != 0:
            raise ValueError("REJECTED status cannot contain fills")

    @staticmethod
    def _validate_transition(*, current: OrderStatus, target: OrderStatus) -> None:
        if current.terminal:
            if current is not target:
                raise BrokerStateConflict(f"terminal order regression: {current.value}->{target.value}")
            return
        allowed = {
            OrderStatus.SUBMITTING: {
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.REJECTED,
                OrderStatus.EXPIRED,
                OrderStatus.CANCEL_PENDING,
                OrderStatus.REPLACE_PENDING,
            },
            OrderStatus.UNKNOWN: {
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.REJECTED,
                OrderStatus.EXPIRED,
                OrderStatus.CANCEL_PENDING,
                OrderStatus.REPLACE_PENDING,
            },
            OrderStatus.SUBMITTED: {
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.REJECTED,
                OrderStatus.EXPIRED,
                OrderStatus.CANCEL_PENDING,
                OrderStatus.REPLACE_PENDING,
            },
            OrderStatus.PARTIALLY_FILLED: {
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.EXPIRED,
                OrderStatus.CANCEL_PENDING,
                OrderStatus.REPLACE_PENDING,
            },
            OrderStatus.CANCEL_PENDING: {
                OrderStatus.CANCEL_PENDING,
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.EXPIRED,
            },
            OrderStatus.REPLACE_PENDING: {
                OrderStatus.REPLACE_PENDING,
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.EXPIRED,
            },
        }
        if target not in allowed.get(current, set()):
            raise BrokerStateConflict(f"invalid order transition: {current.value}->{target.value}")

    def _validate_terminal_replay(self, *, order: OrderRecord, execution: BrokerExecution) -> None:
        self._validate_broker_execution_snapshot(order=order, execution=execution)
        if execution.status is not order.status:
            raise BrokerStateConflict(
                f"terminal broker replay mismatch: local={order.status.value}, broker={execution.status.value}"
            )
        fills = self._fills.fills_for_order(order.order_id)
        if {fill.fill_id for fill in fills} != {fill.fill_id for fill in execution.fills}:
            raise BrokerStateConflict("terminal broker replay fill-set mismatch")
        for fill in execution.fills:
            self._fills.record(fill)

    @staticmethod
    def _validate_broker_execution(*, order: OrderRecord, execution: BrokerExecution) -> None:
        """Compatibility alias for older tests/callers."""
        OrderManagementSystem._validate_broker_execution_snapshot(order=order, execution=execution)
