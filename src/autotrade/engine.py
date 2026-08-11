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
    BrokerCancellationAmbiguous,
    BrokerSubmissionAmbiguous,
    IdempotencyConflict,
    OrderManagementSystem,
    OrderRejectedByControlPlane,
)
from .risk_state import RiskTelemetryStore
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


@dataclass(frozen=True, slots=True)
class ReplacePipelineResult:
    original_order: OrderRecord
    replacement: DurablePipelineResult | None
    aborted_reason: str = ""

    @property
    def replaced(self) -> bool:
        return self.replacement is not None and self.replacement.order is not None


class ConcurrentRiskStateChanged(RuntimeError):
    pass


class ReplacementAborted(RuntimeError):
    pass


class DurableTradingPipeline:
    """Cross-process guarded path with durable reservations and fill accounting."""

    def __init__(
        self,
        *,
        safety: CapitalSafetyKernel,
        oms: OrderManagementSystem,
        portfolio_store: PortfolioStore,
        reservation_store: ReservationStore,
        risk_telemetry_store: RiskTelemetryStore | None = None,
        max_reservation_retries: int = 5,
    ) -> None:
        if max_reservation_retries <= 0:
            raise ValueError("max_reservation_retries must be > 0")
        if not hasattr(portfolio_store, "apply_fills"):
            raise ValueError("durable pipeline requires a fill-aware portfolio store")
        self._safety = safety
        self._oms = oms
        self._portfolio_store = portfolio_store
        self._reservations = reservation_store
        self._risk_telemetry = risk_telemetry_store
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
            effective = self._with_risk_telemetry(effective)
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
            self._mark_ambiguous(idempotency_key=intent.idempotency_key, now=now)
            raise
        except Exception:
            self._mark_ambiguous(idempotency_key=intent.idempotency_key, now=now)
            raise

        self._apply_known_fills(order=order, now=now)
        reservation_status = _reservation_status_for_order(order)
        if reservation_status is ReservationStatus.UNKNOWN:
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

    def cancel_order(self, *, order_id: str, now: datetime) -> OrderRecord:
        order = self._oms.get_by_order_id(order_id)
        if order is None:
            raise KeyError(order_id)
        try:
            final = self._oms.cancel(order_id=order_id, now=now)
        except BrokerCancellationAmbiguous:
            self._mark_ambiguous(idempotency_key=order.intent.idempotency_key, now=now)
            raise

        self._apply_known_fills(order=final, now=now)
        status = _reservation_status_for_order(final)
        self._reservations.set_status(
            idempotency_key=final.intent.idempotency_key,
            status=status,
            now=now,
        )
        if status is ReservationStatus.UNKNOWN:
            self._portfolio_store.set_reconciliation_status(
                reconciliation_ok=False,
                broker_state_known=False,
                now=now,
            )
        return final

    def replace_order(
        self,
        *,
        order_id: str,
        replacement_intent: OrderIntent,
        market: MarketSnapshot,
        now: datetime,
    ) -> ReplacePipelineResult:
        """Authoritative cancel followed by a completely fresh guarded submit.

        The replace request is durably evidenced before cancellation. Retries
        after a crash are safe: REPLACE_PENDING resumes cancellation; CANCELLED
        resumes the fresh replacement submit only when the durable marker binds
        the same replacement intent.
        """
        original = self._oms.get_by_order_id(order_id)
        if original is None:
            raise KeyError(order_id)
        if replacement_intent.idempotency_key == original.intent.idempotency_key:
            raise ReplacementAborted("replacement requires a new idempotency key")
        if replacement_intent.intent_id == original.intent.intent_id:
            raise ReplacementAborted("replacement requires a new intent_id")
        if (
            replacement_intent.symbol != original.intent.symbol
            or replacement_intent.side is not original.intent.side
            or replacement_intent.strategy_id != original.intent.strategy_id
        ):
            raise ReplacementAborted(
                "replacement must preserve symbol, side and strategy identity"
            )

        if original.status.terminal:
            if (
                original.status is OrderStatus.CANCELLED
                and self._oms.replacement_request_matches(
                    order_id=order_id,
                    replacement_intent_id=replacement_intent.intent_id,
                )
            ):
                new_result = self.process_intent(
                    intent=replacement_intent,
                    market=market,
                    now=now,
                )
                return ReplacePipelineResult(
                    original_order=original,
                    replacement=new_result,
                    aborted_reason=(
                        new_result.decision.reason_code
                        if new_result.order is None and new_result.decision is not None
                        else ""
                    ),
                )
            raise ReplacementAborted(
                f"original order already terminal: {original.status.value}"
            )

        self._oms.mark_replace_pending(
            order_id=order_id,
            replacement_intent_id=replacement_intent.intent_id,
            now=now,
        )
        cancelled = self.cancel_order(order_id=order_id, now=now)
        if cancelled.status is not OrderStatus.CANCELLED:
            raise ReplacementAborted(
                f"replacement aborted because original resolved as {cancelled.status.value}"
            )

        new_result = self.process_intent(
            intent=replacement_intent,
            market=market,
            now=now,
        )
        return ReplacePipelineResult(
            original_order=cancelled,
            replacement=new_result,
            aborted_reason=(
                new_result.decision.reason_code
                if new_result.order is None and new_result.decision is not None
                else ""
            ),
        )

    def _with_risk_telemetry(self, portfolio: PortfolioSnapshot) -> PortfolioSnapshot:
        if self._risk_telemetry is None:
            return portfolio
        telemetry = self._risk_telemetry.get()
        return replace(
            portfolio,
            equity=telemetry.current_equity,
            daily_pnl=telemetry.daily_pnl,
            drawdown=telemetry.drawdown,
        )

    def _apply_known_fills(self, *, order: OrderRecord, now: datetime) -> None:
        fills = self._oms.fills_for_order(order.order_id)
        if fills:
            self._portfolio_store.apply_fills(order, fills, now=now)

    def _mark_ambiguous(self, *, idempotency_key: str, now: datetime) -> None:
        self._reservations.set_status(
            idempotency_key=idempotency_key,
            status=ReservationStatus.UNKNOWN,
            now=now,
        )
        self._portfolio_store.set_reconciliation_status(
            reconciliation_ok=False,
            broker_state_known=False,
            now=now,
        )


def _reservation_status_for_order(order: OrderRecord) -> ReservationStatus:
    if order.status.terminal:
        return ReservationStatus.RELEASED
    if order.status in {
        OrderStatus.SUBMITTED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.REPLACE_PENDING,
    }:
        return ReservationStatus.OPEN
    return ReservationStatus.UNKNOWN


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
