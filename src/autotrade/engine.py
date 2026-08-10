from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from .domain import (
    MarketSnapshot,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    PortfolioSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    intent_fingerprint,
)
from .oms import (
    BrokerSubmissionAmbiguous,
    IdempotencyConflict,
    OrderManagementSystem,
    OrderRejectedByControlPlane,
)
from .safety import CapitalSafetyKernel
from .state import (
    PortfolioStore,
    ReservationConflict,
    ReservationRace,
    ReservationStatus,
    ReservationStore,
    RiskReservation,
)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    decision: RiskDecision
    order: OrderRecord | None


class TradingPipeline:
    """Legacy in-process guarded path retained for unit tests and experiments."""

    def __init__(self, *, safety: CapitalSafetyKernel, oms: OrderManagementSystem) -> None:
        self._safety = safety
        self._oms = oms
        self._lock = RLock()

    def process_intent(
        self,
        *,
        intent: OrderIntent,
        market: MarketSnapshot,
        portfolio: PortfolioSnapshot,
        now: datetime,
    ) -> PipelineResult:
        with self._lock:
            decision = self._safety.evaluate(intent=intent, market=market, portfolio=portfolio, now=now)
            if decision.status is RiskDecisionStatus.REJECTED:
                return PipelineResult(decision=decision, order=None)
            order = self._oms.submit(intent=intent, decision=decision, market=market, now=now)
            return PipelineResult(decision=decision, order=order)


@dataclass(frozen=True, slots=True)
class DurablePipelineResult:
    decision: RiskDecision | None
    order: OrderRecord | None
    replayed: bool = False
    recovery_required: bool = False


class ConcurrentRiskStateChanged(RuntimeError):
    pass


class DurableTradingPipeline:
    """Cross-process guarded path with optimistic, durable risk reservations.

    The safety decision is made against the persisted portfolio plus every
    active reservation. Reservation insertion succeeds only if both the
    portfolio version and reservation generation are unchanged, preventing two
    processes from approving against the same stale risk capacity.
    """

    def __init__(
        self,
        *,
        safety: CapitalSafetyKernel,
        oms: OrderManagementSystem,
        portfolio_store: PortfolioStore,
        reservation_store: ReservationStore,
        max_reservation_retries: int = 5,
    ) -> None:
        if max_reservation_retries <= 0:
            raise ValueError("max_reservation_retries must be > 0")
        self._safety = safety
        self._oms = oms
        self._portfolio_store = portfolio_store
        self._reservations = reservation_store
        self._max_reservation_retries = max_reservation_retries

    def process_intent(
        self,
        *,
        intent: OrderIntent,
        market: MarketSnapshot,
        now: datetime,
    ) -> DurablePipelineResult:
        fingerprint = intent_fingerprint(intent)
        existing = self._oms.get_by_idempotency_key(intent.idempotency_key)
        if existing is not None:
            if intent_fingerprint(existing.intent) != fingerprint:
                raise IdempotencyConflict(intent.idempotency_key)
            return DurablePipelineResult(
                decision=None,
                order=existing,
                replayed=True,
                recovery_required=existing.status in {OrderStatus.SUBMITTING, OrderStatus.UNKNOWN},
            )

        decision: RiskDecision | None = None
        for _ in range(self._max_reservation_retries):
            portfolio = self._portfolio_store.get()
            reservation_view = self._reservations.active_view()
            effective = _effective_portfolio(
                portfolio.snapshot,
                reservation_view.reservations,
                reservation_view.generation,
            )
            decision = self._safety.evaluate(
                intent=intent,
                market=market,
                portfolio=effective,
                now=now,
            )
            if decision.status is RiskDecisionStatus.REJECTED:
                return DurablePipelineResult(decision=decision, order=None)
            if decision.approved_notional is None:
                raise RuntimeError("approved decision missing approved_notional")

            reservation = RiskReservation(
                reservation_id=str(uuid4()),
                idempotency_key=intent.idempotency_key,
                intent_fingerprint=fingerprint,
                strategy_id=intent.strategy_id,
                symbol=intent.symbol,
                signed_notional=str(intent.side.sign * decision.approved_notional),
                status=ReservationStatus.RESERVED,
                portfolio_version=portfolio.version,
                created_at=now,
                updated_at=now,
            )
            try:
                self._reservations.reserve(
                    reservation,
                    expected_generation=reservation_view.generation,
                    expected_portfolio_version=portfolio.version,
                )
                break
            except ReservationRace:
                continue
            except ReservationConflict as exc:
                raise IdempotencyConflict(intent.idempotency_key) from exc
        else:
            raise ConcurrentRiskStateChanged(
                "risk capacity changed repeatedly; no broker submission attempted"
            )

        assert decision is not None
        try:
            order = self._oms.submit(intent=intent, decision=decision, market=market, now=now)
        except OrderRejectedByControlPlane:
            self._reservations.set_status(
                idempotency_key=intent.idempotency_key,
                status=ReservationStatus.RELEASED,
                now=now,
            )
            raise
        except BrokerSubmissionAmbiguous:
            self._reservations.set_status(
                idempotency_key=intent.idempotency_key,
                status=ReservationStatus.UNKNOWN,
                now=now,
            )
            self._portfolio_store.set_reconciliation_status(
                reconciliation_ok=False,
                broker_state_known=False,
                now=now,
            )
            raise
        except Exception:
            # Unknown failures after reservation are conservatively retained.
            self._reservations.set_status(
                idempotency_key=intent.idempotency_key,
                status=ReservationStatus.UNKNOWN,
                now=now,
            )
            self._portfolio_store.set_reconciliation_status(
                reconciliation_ok=False,
                broker_state_known=False,
                now=now,
            )
            raise

        if order.filled_quantity > 0:
            self._portfolio_store.apply_order_result(order, now=now)

        if order.status is OrderStatus.FILLED:
            reservation_status = ReservationStatus.RELEASED
        elif order.status in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}:
            reservation_status = ReservationStatus.OPEN
        else:
            reservation_status = ReservationStatus.UNKNOWN
            self._portfolio_store.set_reconciliation_status(
                reconciliation_ok=False,
                broker_state_known=False,
                now=now,
            )

        self._reservations.set_status(
            idempotency_key=intent.idempotency_key,
            status=reservation_status,
            now=now,
        )
        return DurablePipelineResult(
            decision=decision,
            order=order,
            replayed=False,
            recovery_required=reservation_status is ReservationStatus.UNKNOWN,
        )


def _effective_portfolio(
    base: PortfolioSnapshot,
    reservations: tuple[RiskReservation, ...],
    generation: int,
) -> PortfolioSnapshot:
    zero = Decimal("0")
    positions = dict(base.signed_position_notional_by_symbol)
    strategy_positions = {
        strategy: dict(values)
        for strategy, values in base.strategy_signed_position_notional_by_symbol.items()
    }

    for reservation in reservations:
        signed = Decimal(reservation.signed_notional)
        positions[reservation.symbol] = positions.get(reservation.symbol, zero) + signed
        own = strategy_positions.setdefault(reservation.strategy_id, {})
        own[reservation.symbol] = own.get(reservation.symbol, zero) + signed

    positions = {symbol: value for symbol, value in positions.items() if value != zero}
    strategy_positions = {
        strategy: {symbol: value for symbol, value in values.items() if value != zero}
        for strategy, values in strategy_positions.items()
    }
    strategy_positions = {
        strategy: values for strategy, values in strategy_positions.items() if values
    }
    strategy_gross = {
        strategy: sum((abs(value) for value in values.values()), start=zero)
        for strategy, values in strategy_positions.items()
    }
    gross = sum((abs(value) for value in positions.values()), start=zero)
    net = sum(positions.values(), start=zero)

    return replace(
        base,
        snapshot_id=f"{base.snapshot_id}:r{generation}",
        gross_exposure=gross,
        net_exposure=net,
        open_orders=base.open_orders + len(reservations),
        signed_position_notional_by_symbol=positions,
        strategy_gross_exposure=strategy_gross,
        strategy_signed_position_notional_by_symbol=strategy_positions,
    )
