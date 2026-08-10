from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from .broker_state import InspectableBroker
from .domain import OrderStatus
from .ledger import EventLedger, LedgerEvent
from .oms import OrderManagementSystem
from .state import PortfolioStore, ReservationStatus, ReservationStore


@dataclass(frozen=True, slots=True)
class ReconciliationIssue:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    ok: bool
    broker_state_known: bool
    recovered_order_ids: tuple[str, ...]
    issues: tuple[ReconciliationIssue, ...]


class ReconciliationEngine:
    def __init__(
        self,
        *,
        broker: InspectableBroker,
        oms: OrderManagementSystem,
        portfolio_store: PortfolioStore,
        reservation_store: ReservationStore,
        ledger: EventLedger,
    ) -> None:
        self._broker = broker
        self._oms = oms
        self._portfolio_store = portfolio_store
        self._reservations = reservation_store
        self._ledger = ledger

    def reconcile(self, *, now: datetime) -> ReconciliationResult:
        account = self._broker.account_state(now=now)
        if not account.state_known:
            self._portfolio_store.set_reconciliation_status(
                reconciliation_ok=False,
                broker_state_known=False,
                now=now,
            )
            result = ReconciliationResult(
                ok=False,
                broker_state_known=False,
                recovered_order_ids=(),
                issues=(ReconciliationIssue("BROKER_STATE_UNKNOWN", "broker account state unavailable"),),
            )
            self._record(result=result, now=now)
            return result

        issues: list[ReconciliationIssue] = []
        recovered: list[str] = []

        for order in self._oms.all_orders():
            if order.status not in {OrderStatus.SUBMITTING, OrderStatus.UNKNOWN}:
                continue
            execution = self._broker.get_execution(order.order_id)
            if execution is None:
                issues.append(
                    ReconciliationIssue(
                        "UNRESOLVED_AMBIGUOUS_ORDER",
                        f"order {order.order_id} has no authoritative broker result",
                    )
                )
                continue

            final = self._oms.reconcile_from_broker(
                order_id=order.order_id,
                execution=execution,
                now=now,
            )
            if final.filled_quantity > 0:
                self._portfolio_store.apply_order_result(final, now=now)
            try:
                if final.status is OrderStatus.FILLED:
                    reservation_status = ReservationStatus.RELEASED
                elif final.status in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}:
                    reservation_status = ReservationStatus.OPEN
                else:
                    reservation_status = ReservationStatus.UNKNOWN
                self._reservations.set_status(
                    idempotency_key=final.intent.idempotency_key,
                    status=reservation_status,
                    now=now,
                )
            except KeyError:
                issues.append(
                    ReconciliationIssue(
                        "MISSING_RISK_RESERVATION",
                        f"order {final.order_id} has no durable risk reservation",
                    )
                )
            recovered.append(final.order_id)

        orders = self._oms.all_orders()
        orders_by_key = {order.intent.idempotency_key: order for order in orders}
        local_open_ids = {
            order.order_id
            for order in orders
            if order.status in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}
        }
        broker_open_ids = set(account.open_order_ids)

        missing_at_broker = sorted(local_open_ids - broker_open_ids)
        external_at_broker = sorted(broker_open_ids - local_open_ids)
        if missing_at_broker:
            issues.append(
                ReconciliationIssue(
                    "LOCAL_OPEN_ORDER_MISSING_AT_BROKER",
                    ",".join(missing_at_broker),
                )
            )
        if external_at_broker:
            issues.append(
                ReconciliationIssue(
                    "UNTRACKED_BROKER_OPEN_ORDER",
                    ",".join(external_at_broker),
                )
            )

        for reservation in self._reservations.active_view().reservations:
            order = orders_by_key.get(reservation.idempotency_key)
            if order is None:
                issues.append(
                    ReconciliationIssue(
                        "ORPHAN_RISK_RESERVATION",
                        reservation.idempotency_key,
                    )
                )
                continue
            if reservation.status is ReservationStatus.OPEN and order.status not in {
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIALLY_FILLED,
            }:
                issues.append(
                    ReconciliationIssue(
                        "RESERVATION_ORDER_STATE_MISMATCH",
                        f"{reservation.idempotency_key}:{reservation.status.value}/{order.status.value}",
                    )
                )
            if reservation.status is ReservationStatus.UNKNOWN and order.status not in {
                OrderStatus.SUBMITTING,
                OrderStatus.UNKNOWN,
            }:
                issues.append(
                    ReconciliationIssue(
                        "RESERVATION_ORDER_STATE_MISMATCH",
                        f"{reservation.idempotency_key}:{reservation.status.value}/{order.status.value}",
                    )
                )

        local_positions = _normalized_positions(
            self._portfolio_store.get().snapshot.signed_position_notional_by_symbol
        )
        broker_positions = _normalized_positions(account.signed_position_notional_by_symbol)
        if local_positions != broker_positions:
            issues.append(
                ReconciliationIssue(
                    "POSITION_MISMATCH",
                    f"local={local_positions};broker={broker_positions}",
                )
            )

        unresolved = [
            order.order_id
            for order in self._oms.all_orders()
            if order.status in {OrderStatus.SUBMITTING, OrderStatus.UNKNOWN}
        ]
        if unresolved:
            issues.append(
                ReconciliationIssue("AMBIGUOUS_ORDER_REMAINS", ",".join(sorted(unresolved)))
            )

        ok = not issues
        self._portfolio_store.set_reconciliation_status(
            reconciliation_ok=ok,
            broker_state_known=True,
            now=now,
        )
        result = ReconciliationResult(
            ok=ok,
            broker_state_known=True,
            recovered_order_ids=tuple(recovered),
            issues=tuple(issues),
        )
        self._record(result=result, now=now)
        return result

    def _record(self, *, result: ReconciliationResult, now: datetime) -> None:
        self._ledger.append(
            LedgerEvent(
                event_id=f"reconcile:{uuid4()}",
                event_type="RECONCILIATION_RESULT",
                occurred_at=now,
                payload={
                    "ok": str(result.ok).lower(),
                    "broker_state_known": str(result.broker_state_known).lower(),
                    "recovered_order_ids": ",".join(result.recovered_order_ids),
                    "issue_codes": ",".join(issue.code for issue in result.issues),
                },
            )
        )


def _normalized_positions(values) -> dict[str, Decimal]:
    zero = Decimal("0")
    return {symbol: Decimal(value) for symbol, value in values.items() if Decimal(value) != zero}
