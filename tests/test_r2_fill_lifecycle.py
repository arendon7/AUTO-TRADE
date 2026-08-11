from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.bootstrap import build_durable_paper_core
from autotrade.broker_state import BrokerAccountState
from autotrade.brokers.base import BrokerExecution
from autotrade.domain import Fill, OrderIntent, OrderRecord, OrderStatus, OrderType
from autotrade.engine import DurableTradingPipeline
from autotrade.execution_state import (
    FillIntegrityConflict,
    SQLiteFillAwarePortfolioStore,
    SQLiteFillStore,
)
from autotrade.oms import BrokerCancellationAmbiguous, OrderManagementSystem
from autotrade.persistence import (
    SQLiteEventLedger,
    SQLiteOrderStore,
    SQLiteReservationStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
)
from autotrade.reconciliation import ReconciliationEngine
from autotrade.safety import CapitalSafetyKernel
from autotrade.state import ReservationStatus


class ScriptedLifecycleBroker:
    def __init__(self, *, partial_quantity: Decimal, partial_price: Decimal) -> None:
        self.partial_quantity = partial_quantity
        self.partial_price = partial_price
        self.executions: dict[str, BrokerExecution] = {}
        self.cancel_ambiguous = False

    def submit(self, *, order: OrderRecord, market, now) -> BrokerExecution:
        fill = Fill(
            fill_id=f"fill-1-{order.order_id}",
            order_id=order.order_id,
            symbol=order.intent.symbol,
            side=order.intent.side,
            quantity=self.partial_quantity,
            price=self.partial_price,
            occurred_at=now,
        )
        execution = BrokerExecution(status=OrderStatus.PARTIALLY_FILLED, fills=(fill,))
        self.executions[order.order_id] = execution
        return execution

    def advance_to_filled(
        self,
        *,
        order_id: str,
        final_quantity: Decimal,
        final_price: Decimal,
        now,
    ) -> BrokerExecution:
        current = self.executions[order_id]
        second = Fill(
            fill_id=f"fill-2-{order_id}",
            order_id=order_id,
            symbol=current.fills[0].symbol,
            side=current.fills[0].side,
            quantity=final_quantity,
            price=final_price,
            occurred_at=now,
        )
        execution = BrokerExecution(
            status=OrderStatus.FILLED,
            fills=current.fills + (second,),
        )
        self.executions[order_id] = execution
        return execution

    def cancel(self, *, order_id: str, now) -> BrokerExecution:
        if self.cancel_ambiguous:
            raise TimeoutError("simulated lost cancel acknowledgement")
        current = self.executions[order_id]
        if current.status.terminal:
            return current
        execution = BrokerExecution(status=OrderStatus.CANCELLED, fills=current.fills)
        self.executions[order_id] = execution
        return execution

    def get_execution(self, order_id: str) -> BrokerExecution | None:
        return self.executions.get(order_id)

    def account_state(self, *, now) -> BrokerAccountState:
        positions: dict[str, Decimal] = {}
        open_ids: set[str] = set()
        for order_id, execution in self.executions.items():
            if not execution.status.terminal:
                open_ids.add(order_id)
            for fill in execution.fills:
                signed = fill.side.sign * fill.quantity * fill.price
                positions[fill.symbol] = positions.get(fill.symbol, Decimal("0")) + signed
        return BrokerAccountState(
            observed_at=now,
            state_known=True,
            signed_position_notional_by_symbol={
                symbol: value for symbol, value in positions.items() if value != 0
            },
            open_order_ids=frozenset(open_ids),
        )


def build_scripted_core(*, tmp_path, limits, empty_portfolio, now):
    runtime = SQLiteRuntime(tmp_path / "scripted-r2.db")
    ledger = SQLiteEventLedger(runtime)
    portfolio = SQLiteFillAwarePortfolioStore(runtime)
    portfolio.initialize(empty_portfolio, now=now)
    safety_state = SQLiteSafetyStateStore(runtime)
    reservations = SQLiteReservationStore(runtime)
    fills = SQLiteFillStore(runtime)
    orders = SQLiteOrderStore(runtime)
    broker = ScriptedLifecycleBroker(
        partial_quantity=Decimal("4"),
        partial_price=Decimal("101"),
    )
    safety = CapitalSafetyKernel(limits, ledger, state_store=safety_state)
    oms = OrderManagementSystem(
        broker=broker,
        ledger=ledger,
        order_store=orders,
        safety_state_store=safety_state,
        fill_store=fills,
    )
    pipeline = DurableTradingPipeline(
        safety=safety,
        oms=oms,
        portfolio_store=portfolio,
        reservation_store=reservations,
    )
    reconciliation = ReconciliationEngine(
        broker=broker,
        oms=oms,
        portfolio_store=portfolio,
        reservation_store=reservations,
        ledger=ledger,
    )
    return runtime, broker, fills, portfolio, reservations, oms, pipeline, reconciliation


def test_sqlite_fill_store_is_idempotent_and_detects_conflicting_reuse(tmp_path, now):
    store = SQLiteFillStore(SQLiteRuntime(tmp_path / "fills.db"))
    fill = Fill(
        fill_id="fill-1",
        order_id="order-1",
        symbol="TEST-USD",
        side=__import__("autotrade.domain", fromlist=["Side"]).Side.BUY,
        quantity=Decimal("4"),
        price=Decimal("100"),
        occurred_at=now,
    )
    assert store.record(fill) is True
    assert store.record(fill) is False
    assert store.fills_for_order("order-1") == (fill,)

    with pytest.raises(FillIntegrityConflict):
        store.record(replace(fill, price=Decimal("101")))


def test_fill_aware_portfolio_applies_later_partial_fills_exactly_once(
    tmp_path, now, empty_portfolio, market_buy_intent
):
    runtime = SQLiteRuntime(tmp_path / "portfolio.db")
    portfolio = SQLiteFillAwarePortfolioStore(runtime)
    portfolio.initialize(empty_portfolio, now=now)
    order = OrderRecord(
        order_id="order-fill-aware",
        intent=market_buy_intent,
        risk_decision_id="risk-1",
        status=OrderStatus.PARTIALLY_FILLED,
        created_at=now,
        submitted_at=now,
    )
    fill1 = Fill(
        fill_id="f1",
        order_id=order.order_id,
        symbol=order.intent.symbol,
        side=order.intent.side,
        quantity=Decimal("4"),
        price=Decimal("100"),
        occurred_at=now,
    )
    fill2 = Fill(
        fill_id="f2",
        order_id=order.order_id,
        symbol=order.intent.symbol,
        side=order.intent.side,
        quantity=Decimal("6"),
        price=Decimal("110"),
        occurred_at=now + timedelta(seconds=1),
    )

    first = portfolio.apply_fills(order, (fill1,), now=now)
    assert first.snapshot.gross_exposure == Decimal("400")
    replay = portfolio.apply_fills(order, (fill1,), now=now)
    assert replay.version == first.version
    assert replay.snapshot.gross_exposure == Decimal("400")

    second = portfolio.apply_fills(order, (fill1, fill2), now=now + timedelta(seconds=1))
    assert second.snapshot.gross_exposure == Decimal("1060")
    final_replay = portfolio.apply_fills(order, (fill1, fill2), now=now + timedelta(seconds=2))
    assert final_replay.version == second.version
    assert final_replay.snapshot.gross_exposure == Decimal("1060")


def test_partial_fill_progresses_to_full_through_reconciliation_without_double_count(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, broker, _, portfolio, reservations, oms, pipeline, reconciliation = build_scripted_core(
        tmp_path=tmp_path,
        limits=limits,
        empty_portfolio=empty_portfolio,
        now=market.observed_at,
    )

    first = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        now=market.observed_at,
    )
    assert first.order is not None
    assert first.order.status is OrderStatus.PARTIALLY_FILLED
    assert first.order.filled_quantity == Decimal("4")
    assert portfolio.get().snapshot.gross_exposure == Decimal("404")
    assert reservations.get(market_buy_intent.idempotency_key).status is ReservationStatus.OPEN

    broker.advance_to_filled(
        order_id=first.order.order_id,
        final_quantity=Decimal("6"),
        final_price=Decimal("102"),
        now=market.observed_at + timedelta(seconds=1),
    )
    reconciled = reconciliation.reconcile(now=market.observed_at + timedelta(seconds=1))
    assert reconciled.ok is True
    final = oms.get_by_order_id(first.order.order_id)
    assert final is not None
    assert final.status is OrderStatus.FILLED
    assert final.filled_quantity == Decimal("10")
    assert final.average_fill_price == Decimal("101.6")
    assert portfolio.get().snapshot.gross_exposure == Decimal("1016")
    assert reservations.get(market_buy_intent.idempotency_key).status is ReservationStatus.RELEASED

    replay = reconciliation.reconcile(now=market.observed_at + timedelta(seconds=2))
    assert replay.ok is True
    assert portfolio.get().snapshot.gross_exposure == Decimal("1016")
    assert len(oms.fills_for_order(first.order.order_id)) == 2


def test_cancel_open_limit_is_authoritative_and_releases_reservation(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "cancel.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    intent = replace(
        market_buy_intent,
        intent_id="limit-cancel",
        idempotency_key="limit-cancel-key",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.5"),
    )
    submitted = core.pipeline.process_intent(
        intent=intent,
        market=market,
        now=market.observed_at,
    )
    assert submitted.order is not None
    assert submitted.order.status is OrderStatus.SUBMITTED

    cancelled = core.pipeline.cancel_order(
        order_id=submitted.order.order_id,
        now=market.observed_at + timedelta(milliseconds=10),
    )
    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.filled_quantity == 0
    assert core.reservation_store.get(intent.idempotency_key).status is ReservationStatus.RELEASED
    assert submitted.order.order_id not in core.broker.account_state(
        now=market.observed_at + timedelta(milliseconds=10)
    ).open_order_ids
    assert core.reconciliation.reconcile(
        now=market.observed_at + timedelta(milliseconds=20)
    ).ok is True


def test_ambiguous_cancel_never_marks_order_cancelled(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, broker, _, portfolio, reservations, oms, pipeline, _ = build_scripted_core(
        tmp_path=tmp_path,
        limits=limits,
        empty_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    first = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        now=market.observed_at,
    )
    broker.cancel_ambiguous = True

    with pytest.raises(BrokerCancellationAmbiguous):
        pipeline.cancel_order(
            order_id=first.order.order_id,
            now=market.observed_at + timedelta(milliseconds=10),
        )
    local = oms.get_by_order_id(first.order.order_id)
    assert local is not None
    assert local.status is OrderStatus.UNKNOWN
    assert local.status is not OrderStatus.CANCELLED
    assert reservations.get(market_buy_intent.idempotency_key).status is ReservationStatus.UNKNOWN
    assert portfolio.get().snapshot.reconciliation_ok is False
    assert portfolio.get().snapshot.broker_state_known is False


def test_broker_snapshot_cannot_drop_or_conflict_with_previously_seen_fill(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, _, _, _, _, oms, pipeline, _ = build_scripted_core(
        tmp_path=tmp_path,
        limits=limits,
        empty_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    first = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        now=market.observed_at,
    )
    fill = oms.fills_for_order(first.order.order_id)[0]

    with pytest.raises(Exception, match="lost previously observed fills"):
        oms.sync_from_broker(
            order_id=first.order.order_id,
            execution=BrokerExecution(status=OrderStatus.SUBMITTED, fills=()),
            now=market.observed_at + timedelta(seconds=1),
        )

    with pytest.raises(FillIntegrityConflict):
        oms.sync_from_broker(
            order_id=first.order.order_id,
            execution=BrokerExecution(
                status=OrderStatus.PARTIALLY_FILLED,
                fills=(replace(fill, price=fill.price + Decimal("1")),),
            ),
            now=market.observed_at + timedelta(seconds=1),
        )


def test_broker_overfill_is_rejected(tmp_path, limits, market, empty_portfolio, market_buy_intent):
    _, _, _, _, _, oms, pipeline, _ = build_scripted_core(
        tmp_path=tmp_path,
        limits=limits,
        empty_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    first = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        now=market.observed_at,
    )
    first_fill = oms.fills_for_order(first.order.order_id)[0]
    excessive = Fill(
        fill_id="excessive",
        order_id=first.order.order_id,
        symbol=first_fill.symbol,
        side=first_fill.side,
        quantity=Decimal("7"),
        price=Decimal("102"),
        occurred_at=market.observed_at + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="overfilled"):
        oms.sync_from_broker(
            order_id=first.order.order_id,
            execution=BrokerExecution(
                status=OrderStatus.FILLED,
                fills=(first_fill, excessive),
            ),
            now=market.observed_at + timedelta(seconds=1),
        )
