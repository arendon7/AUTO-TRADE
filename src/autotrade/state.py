from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from threading import RLock
from typing import Protocol

from .domain import Fill, OrderRecord, PortfolioSnapshot


class OrderStore(Protocol):
    def get_by_idempotency_key(self, key: str) -> OrderRecord | None: ...
    def get_by_order_id(self, order_id: str) -> OrderRecord | None: ...
    def create_if_absent(self, order: OrderRecord) -> tuple[bool, OrderRecord]: ...
    def update(self, order: OrderRecord) -> None: ...
    def all_orders(self) -> tuple[OrderRecord, ...]: ...


class InMemoryOrderStore:
    def __init__(self) -> None:
        self._by_key: dict[str, OrderRecord] = {}
        self._by_order_id: dict[str, str] = {}
        self._lock = RLock()

    def get_by_idempotency_key(self, key: str) -> OrderRecord | None:
        with self._lock:
            return self._by_key.get(key)

    def get_by_order_id(self, order_id: str) -> OrderRecord | None:
        with self._lock:
            key = self._by_order_id.get(order_id)
            return self._by_key.get(key) if key is not None else None

    def create_if_absent(self, order: OrderRecord) -> tuple[bool, OrderRecord]:
        key = order.intent.idempotency_key
        with self._lock:
            existing = self._by_key.get(key)
            if existing is not None:
                return False, existing
            if order.order_id in self._by_order_id:
                raise ValueError(f"duplicate order_id: {order.order_id}")
            self._by_key[key] = order
            self._by_order_id[order.order_id] = key
            return True, order

    def update(self, order: OrderRecord) -> None:
        key = order.intent.idempotency_key
        with self._lock:
            existing = self._by_key.get(key)
            if existing is None or existing.order_id != order.order_id:
                raise KeyError(order.order_id)
            self._by_key[key] = order

    def all_orders(self) -> tuple[OrderRecord, ...]:
        with self._lock:
            return tuple(self._by_key.values())


@dataclass(frozen=True, slots=True)
class SafetyControlState:
    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    version: int = 0
    updated_at: datetime | None = None


class SafetyStateStore(Protocol):
    def get(self) -> SafetyControlState: ...
    def activate(self, *, reason: str, now: datetime) -> SafetyControlState: ...
    def reset(self, *, now: datetime) -> SafetyControlState: ...


class InMemorySafetyStateStore:
    def __init__(self) -> None:
        self._state = SafetyControlState()
        self._lock = RLock()

    def get(self) -> SafetyControlState:
        with self._lock:
            return self._state

    def activate(self, *, reason: str, now: datetime) -> SafetyControlState:
        with self._lock:
            self._state = SafetyControlState(
                kill_switch_active=True,
                kill_switch_reason=reason,
                version=self._state.version + 1,
                updated_at=now,
            )
            return self._state

    def reset(self, *, now: datetime) -> SafetyControlState:
        with self._lock:
            self._state = SafetyControlState(
                kill_switch_active=False,
                kill_switch_reason="",
                version=self._state.version + 1,
                updated_at=now,
            )
            return self._state


@dataclass(frozen=True, slots=True)
class VersionedPortfolioSnapshot:
    version: int
    snapshot: PortfolioSnapshot


class PortfolioNotInitialized(RuntimeError):
    pass


class PortfolioStore(Protocol):
    def initialize(self, snapshot: PortfolioSnapshot, *, now: datetime) -> VersionedPortfolioSnapshot: ...
    def get(self) -> VersionedPortfolioSnapshot: ...
    def compare_and_set(
        self, *, expected_version: int, snapshot: PortfolioSnapshot, now: datetime
    ) -> VersionedPortfolioSnapshot | None: ...
    def set_reconciliation_status(
        self, *, reconciliation_ok: bool, broker_state_known: bool, now: datetime
    ) -> VersionedPortfolioSnapshot: ...
    def apply_order_result(self, order: OrderRecord, *, now: datetime) -> VersionedPortfolioSnapshot: ...
    def apply_fills(
        self, order: OrderRecord, fills: tuple[Fill, ...], *, now: datetime
    ) -> VersionedPortfolioSnapshot: ...


class InMemoryPortfolioStore:
    def __init__(self) -> None:
        self._current: VersionedPortfolioSnapshot | None = None
        self._applied_order_ids: set[str] = set()
        self._applied_fill_ids: set[str] = set()
        self._orders_with_fill_events: set[str] = set()
        self._lock = RLock()

    def initialize(self, snapshot: PortfolioSnapshot, *, now: datetime) -> VersionedPortfolioSnapshot:
        del now
        with self._lock:
            if self._current is None:
                self._current = VersionedPortfolioSnapshot(version=1, snapshot=snapshot)
            return self._current

    def get(self) -> VersionedPortfolioSnapshot:
        with self._lock:
            if self._current is None:
                raise PortfolioNotInitialized("portfolio state is not initialized")
            return self._current

    def compare_and_set(
        self,
        *,
        expected_version: int,
        snapshot: PortfolioSnapshot,
        now: datetime,
    ) -> VersionedPortfolioSnapshot | None:
        del now
        with self._lock:
            current = self.get()
            if current.version != expected_version:
                return None
            self._current = VersionedPortfolioSnapshot(version=current.version + 1, snapshot=snapshot)
            return self._current

    def set_reconciliation_status(
        self,
        *,
        reconciliation_ok: bool,
        broker_state_known: bool,
        now: datetime,
    ) -> VersionedPortfolioSnapshot:
        del now
        with self._lock:
            current = self.get()
            if (
                current.snapshot.reconciliation_ok == reconciliation_ok
                and current.snapshot.broker_state_known == broker_state_known
            ):
                return current
            updated = replace(
                current.snapshot,
                reconciliation_ok=reconciliation_ok,
                broker_state_known=broker_state_known,
            )
            self._current = VersionedPortfolioSnapshot(version=current.version + 1, snapshot=updated)
            return self._current

    def apply_order_result(self, order: OrderRecord, *, now: datetime) -> VersionedPortfolioSnapshot:
        """Foundation compatibility path.

        New R2 execution uses apply_fills. Mixing both paths for one order is
        blocked to avoid double accounting.
        """
        del now
        with self._lock:
            current = self.get()
            if (
                order.order_id in self._applied_order_ids
                or order.order_id in self._orders_with_fill_events
                or order.filled_quantity <= 0
            ):
                return current
            updated = apply_fill_to_portfolio(current.snapshot, order)
            self._applied_order_ids.add(order.order_id)
            self._current = VersionedPortfolioSnapshot(version=current.version + 1, snapshot=updated)
            return self._current

    def apply_fills(
        self,
        order: OrderRecord,
        fills: tuple[Fill, ...],
        *,
        now: datetime,
    ) -> VersionedPortfolioSnapshot:
        del now
        with self._lock:
            current = self.get()
            if order.order_id in self._applied_order_ids:
                return current
            snapshot = current.snapshot
            changed = False
            batch_seen: set[str] = set()
            for fill in sorted(fills, key=lambda value: (value.occurred_at, value.fill_id)):
                _validate_fill_for_order(fill=fill, order=order)
                if fill.fill_id in batch_seen or fill.fill_id in self._applied_fill_ids:
                    continue
                batch_seen.add(fill.fill_id)
                incremental = replace(
                    order,
                    filled_quantity=fill.quantity,
                    average_fill_price=fill.price,
                )
                snapshot = apply_fill_to_portfolio(snapshot, incremental)
                self._applied_fill_ids.add(fill.fill_id)
                self._orders_with_fill_events.add(order.order_id)
                changed = True
            if not changed:
                return current
            self._current = VersionedPortfolioSnapshot(version=current.version + 1, snapshot=snapshot)
            return self._current


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    OPEN = "OPEN"
    UNKNOWN = "UNKNOWN"
    RELEASED = "RELEASED"


@dataclass(frozen=True, slots=True)
class RiskReservation:
    reservation_id: str
    idempotency_key: str
    intent_fingerprint: str
    strategy_id: str
    symbol: str
    signed_notional: str
    status: ReservationStatus
    portfolio_version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReservationView:
    generation: int
    reservations: tuple[RiskReservation, ...]


class ReservationRace(RuntimeError):
    pass


class ReservationConflict(RuntimeError):
    pass


class ReservationStore(Protocol):
    def active_view(self) -> ReservationView: ...
    def reserve(
        self,
        reservation: RiskReservation,
        *,
        expected_generation: int,
        expected_portfolio_version: int,
    ) -> RiskReservation: ...
    def set_status(
        self, *, idempotency_key: str, status: ReservationStatus, now: datetime
    ) -> RiskReservation: ...
    def get(self, idempotency_key: str) -> RiskReservation | None: ...


class InMemoryReservationStore:
    def __init__(self, portfolio_store: PortfolioStore) -> None:
        self._portfolio_store = portfolio_store
        self._by_key: dict[str, RiskReservation] = {}
        self._generation = 0
        self._lock = RLock()

    def active_view(self) -> ReservationView:
        with self._lock:
            active = tuple(
                reservation
                for reservation in self._by_key.values()
                if reservation.status is not ReservationStatus.RELEASED
            )
            return ReservationView(generation=self._generation, reservations=active)

    def reserve(
        self,
        reservation: RiskReservation,
        *,
        expected_generation: int,
        expected_portfolio_version: int,
    ) -> RiskReservation:
        with self._lock:
            existing = self._by_key.get(reservation.idempotency_key)
            if existing is not None:
                if existing.intent_fingerprint != reservation.intent_fingerprint:
                    raise ReservationConflict(reservation.idempotency_key)
                return existing
            if self._generation != expected_generation:
                raise ReservationRace("reservation generation changed")
            if self._portfolio_store.get().version != expected_portfolio_version:
                raise ReservationRace("portfolio version changed")
            self._by_key[reservation.idempotency_key] = reservation
            self._generation += 1
            return reservation

    def set_status(
        self,
        *,
        idempotency_key: str,
        status: ReservationStatus,
        now: datetime,
    ) -> RiskReservation:
        with self._lock:
            current = self._by_key[idempotency_key]
            if current.status is status:
                return current
            updated = replace(current, status=status, updated_at=now)
            self._by_key[idempotency_key] = updated
            self._generation += 1
            return updated

    def get(self, idempotency_key: str) -> RiskReservation | None:
        with self._lock:
            return self._by_key.get(idempotency_key)


def _validate_fill_for_order(*, fill: Fill, order: OrderRecord) -> None:
    if fill.order_id != order.order_id:
        raise ValueError("fill order_id mismatch")
    if fill.symbol != order.intent.symbol or fill.side is not order.intent.side:
        raise ValueError("fill instrument/side mismatch")
    if not fill.fill_id.strip():
        raise ValueError("fill_id is required")
    if not fill.quantity.is_finite() or fill.quantity <= 0:
        raise ValueError("invalid fill quantity")
    if not fill.price.is_finite() or fill.price <= 0:
        raise ValueError("invalid fill price")


def apply_fill_to_portfolio(snapshot: PortfolioSnapshot, order: OrderRecord) -> PortfolioSnapshot:
    if order.filled_quantity <= 0 or order.average_fill_price is None:
        return snapshot

    zero = Decimal("0")
    signed_fill = order.intent.side.sign * order.filled_quantity * order.average_fill_price
    positions = dict(snapshot.signed_position_notional_by_symbol)
    positions[order.intent.symbol] = positions.get(order.intent.symbol, zero) + signed_fill
    positions = {symbol: value for symbol, value in positions.items() if value != zero}

    strategy_positions = {
        strategy: dict(values)
        for strategy, values in snapshot.strategy_signed_position_notional_by_symbol.items()
    }
    own = strategy_positions.setdefault(order.intent.strategy_id, {})
    own[order.intent.symbol] = own.get(order.intent.symbol, zero) + signed_fill
    own = {symbol: value for symbol, value in own.items() if value != zero}
    if own:
        strategy_positions[order.intent.strategy_id] = own
    else:
        strategy_positions.pop(order.intent.strategy_id, None)

    strategy_gross = {
        strategy: sum((abs(value) for value in values.values()), start=zero)
        for strategy, values in strategy_positions.items()
    }
    gross = sum((abs(value) for value in positions.values()), start=zero)
    net = sum(positions.values(), start=zero)

    return replace(
        snapshot,
        signed_position_notional_by_symbol=positions,
        strategy_signed_position_notional_by_symbol=strategy_positions,
        strategy_gross_exposure=strategy_gross,
        gross_exposure=gross,
        net_exposure=net,
    )
