from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import sqlite3
from uuid import uuid4

import pytest

from autotrade.bootstrap import build_durable_paper_core
from autotrade.brokers.durable_paper import DurablePaperBroker
from autotrade.domain import OrderIntent, OrderStatus, OrderType, Side, intent_fingerprint
from autotrade.engine import DurableTradingPipeline
from autotrade.execution_state import SQLiteFillAwarePortfolioStore, SQLiteFillStore
from autotrade.ledger import LedgerEvent
from autotrade.oms import OrderManagementSystem, OrderRejectedByControlPlane
from autotrade.persistence import (
    LedgerIntegrityError,
    SQLiteEventLedger,
    SQLiteOrderStore,
    SQLitePortfolioStore,
    SQLiteReservationStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
)
from autotrade.safety import CapitalSafetyKernel
from autotrade.state import (
    ReservationRace,
    ReservationStatus,
    RiskReservation,
)


def test_sqlite_ledger_persists_and_verifies_hash_chain(tmp_path, now):
    runtime = SQLiteRuntime(tmp_path / "state.db")
    ledger = SQLiteEventLedger(runtime)
    ledger.append(LedgerEvent("e1", "A", now, {"x": "1"}))
    ledger.append(LedgerEvent("e2", "B", now + timedelta(milliseconds=1), {"x": "2"}))

    reopened = SQLiteEventLedger(SQLiteRuntime(tmp_path / "state.db"))
    assert [event.event_id for event in reopened.all_events()] == ["e1", "e2"]
    assert reopened.verify_integrity() is True


def test_sqlite_ledger_detects_tampering(tmp_path, now):
    runtime = SQLiteRuntime(tmp_path / "state.db")
    ledger = SQLiteEventLedger(runtime)
    ledger.append(LedgerEvent("e1", "A", now, {"x": "1"}))

    conn = sqlite3.connect(runtime.path)
    try:
        conn.execute("UPDATE ledger_events SET payload_json = ? WHERE event_id = ?", ('{"x":"999"}', "e1"))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(LedgerIntegrityError, match="hash mismatch"):
        ledger.verify_integrity()


def test_durable_market_fill_survives_restart_and_retry_is_idempotent(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    db = tmp_path / "state.db"
    core = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    assert core.startup_reconciliation.ok is True

    first = core.pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        now=market.observed_at,
    )
    assert first.order is not None
    assert first.order.status is OrderStatus.FILLED
    assert core.broker.submission_count == 1
    assert core.portfolio_store.get().snapshot.gross_exposure == Decimal("1010")
    assert core.reservation_store.get(market_buy_intent.idempotency_key).status is ReservationStatus.RELEASED

    restarted = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at + timedelta(milliseconds=10),
    )
    assert restarted.startup_reconciliation.ok is True
    replay = restarted.pipeline.process_intent(
        intent=market_buy_intent,
        market=replace(market, observed_at=market.observed_at + timedelta(milliseconds=10)),
        now=market.observed_at + timedelta(milliseconds=10),
    )
    assert replay.replayed is True
    assert replay.order == first.order
    assert restarted.broker.submission_count == 1
    assert restarted.portfolio_store.get().snapshot.gross_exposure == Decimal("1010")


def test_active_open_reservations_consume_capacity(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "state.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )

    def limit_intent(n: int) -> OrderIntent:
        return replace(
            market_buy_intent,
            intent_id=f"intent-{n}",
            idempotency_key=f"idem-{n}",
            quantity=Decimal("100"),
            order_type=OrderType.LIMIT,
            limit_price=Decimal("99.5"),
        )

    first = core.pipeline.process_intent(intent=limit_intent(1), market=market, now=market.observed_at)
    second = core.pipeline.process_intent(intent=limit_intent(2), market=market, now=market.observed_at)
    third = core.pipeline.process_intent(intent=limit_intent(3), market=market, now=market.observed_at)

    assert first.order.status is OrderStatus.SUBMITTED
    assert second.order.status is OrderStatus.SUBMITTED
    assert third.order is None
    assert third.decision.reason_code == "MAX_POSITION_NOTIONAL"
    assert core.broker.submission_count == 2


def test_stale_cross_process_reservation_view_cannot_commit(tmp_path, empty_portfolio, now):
    runtime = SQLiteRuntime(tmp_path / "state.db")
    portfolio = SQLitePortfolioStore(runtime)
    current = portfolio.initialize(empty_portfolio, now=now)
    store_a = SQLiteReservationStore(runtime)
    store_b = SQLiteReservationStore(runtime)
    view_a = store_a.active_view()
    view_b = store_b.active_view()

    first = RiskReservation(
        reservation_id="r1",
        idempotency_key="k1",
        intent_fingerprint="fp1",
        strategy_id="s",
        symbol="TEST-USD",
        signed_notional="100",
        status=ReservationStatus.RESERVED,
        portfolio_version=current.version,
        created_at=now,
        updated_at=now,
    )
    second = replace(first, reservation_id="r2", idempotency_key="k2", intent_fingerprint="fp2")

    store_a.reserve(
        first,
        expected_generation=view_a.generation,
        expected_portfolio_version=current.version,
    )
    with pytest.raises(ReservationRace):
        store_b.reserve(
            second,
            expected_generation=view_b.generation,
            expected_portfolio_version=current.version,
        )


def test_kill_switch_persists_across_restart(tmp_path, limits, market, empty_portfolio):
    db = tmp_path / "state.db"
    first = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    first.safety.activate_kill_switch(reason="operator emergency", now=market.observed_at)
    assert first.safety.kill_switch_active is True

    restarted = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at + timedelta(milliseconds=10),
    )
    assert restarted.safety.kill_switch_active is True


def test_safety_state_change_invalidates_already_approved_decision(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "state.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    decision = core.safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=core.portfolio_store.get().snapshot,
        now=market.observed_at,
    )
    core.safety.activate_kill_switch(reason="late emergency", now=market.observed_at)

    with pytest.raises(OrderRejectedByControlPlane, match="safety state changed"):
        core.oms.submit(
            intent=market_buy_intent,
            decision=decision,
            market=market,
            now=market.observed_at,
        )
    assert core.broker.submission_count == 0


class CrashAfterBrokerCommit:
    def __init__(self, inner: DurablePaperBroker) -> None:
        self.inner = inner

    def submit(self, *, order, market, now):
        self.inner.submit(order=order, market=market, now=now)
        raise SystemExit("simulated process crash")


def test_startup_reconciliation_recovers_crash_after_broker_commit(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    db = tmp_path / "state.db"
    runtime = SQLiteRuntime(db)
    ledger = SQLiteEventLedger(runtime)
    portfolio = SQLiteFillAwarePortfolioStore(runtime)
    portfolio.initialize(empty_portfolio, now=market.observed_at)
    safety_state = SQLiteSafetyStateStore(runtime)
    reservations = SQLiteReservationStore(runtime)
    fill_store = SQLiteFillStore(runtime)
    durable_broker = DurablePaperBroker(runtime)
    safety = CapitalSafetyKernel(limits, ledger, state_store=safety_state)
    oms = OrderManagementSystem(
        broker=CrashAfterBrokerCommit(durable_broker),
        ledger=ledger,
        order_store=SQLiteOrderStore(runtime),
        safety_state_store=safety_state,
        fill_store=fill_store,
    )
    pipeline = DurableTradingPipeline(
        safety=safety,
        oms=oms,
        portfolio_store=portfolio,
        reservation_store=reservations,
    )

    with pytest.raises(SystemExit, match="simulated process crash"):
        pipeline.process_intent(
            intent=market_buy_intent,
            market=market,
            now=market.observed_at,
        )
    stranded = oms.get_by_idempotency_key(market_buy_intent.idempotency_key)
    assert stranded.status is OrderStatus.SUBMITTING
    assert durable_broker.submission_count == 1

    restarted = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at + timedelta(milliseconds=20),
    )
    assert restarted.startup_reconciliation.ok is True
    assert stranded.order_id in restarted.startup_reconciliation.recovered_order_ids
    recovered = restarted.oms.get_by_order_id(stranded.order_id)
    assert recovered.status is OrderStatus.FILLED
    assert restarted.portfolio_store.get().snapshot.gross_exposure == Decimal("1010")
    assert restarted.reservation_store.get(market_buy_intent.idempotency_key).status is ReservationStatus.RELEASED


def test_reconciliation_detects_position_mismatch(tmp_path, limits, market, empty_portfolio):
    core = build_durable_paper_core(
        db_path=tmp_path / "state.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    current = core.portfolio_store.get()
    wrong = replace(
        current.snapshot,
        gross_exposure=Decimal("50"),
        net_exposure=Decimal("50"),
        signed_position_notional_by_symbol={"TEST-USD": Decimal("50")},
        strategy_gross_exposure={"manual": Decimal("50")},
        strategy_signed_position_notional_by_symbol={"manual": {"TEST-USD": Decimal("50")}},
    )
    assert core.portfolio_store.compare_and_set(
        expected_version=current.version,
        snapshot=wrong,
        now=market.observed_at,
    ) is not None

    result = core.reconciliation.reconcile(now=market.observed_at)
    assert result.ok is False
    assert "POSITION_MISMATCH" in {issue.code for issue in result.issues}
    state = core.portfolio_store.get().snapshot
    assert state.reconciliation_ok is False
    assert state.broker_state_known is True


def test_sqlite_order_store_is_cross_instance_idempotent(tmp_path, now, market_buy_intent):
    from autotrade.domain import OrderRecord

    runtime = SQLiteRuntime(tmp_path / "state.db")
    a = SQLiteOrderStore(runtime)
    b = SQLiteOrderStore(runtime)
    order = OrderRecord(
        order_id=str(uuid4()),
        intent=market_buy_intent,
        risk_decision_id="risk-1",
        status=OrderStatus.VALIDATED,
        created_at=now,
    )
    created, _ = a.create_if_absent(order)
    created_again, existing = b.create_if_absent(replace(order, order_id=str(uuid4())))
    assert created is True
    assert created_again is False
    assert existing.order_id == order.order_id
    assert b.get_by_order_id(order.order_id) == order
