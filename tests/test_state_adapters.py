from dataclasses import replace
from decimal import Decimal

import pytest

from autotrade.domain import OrderRecord, OrderStatus
from autotrade.state import (
    InMemoryOrderStore,
    InMemoryPortfolioStore,
    InMemoryReservationStore,
    InMemorySafetyStateStore,
    PortfolioNotInitialized,
    ReservationConflict,
    ReservationRace,
    ReservationStatus,
    RiskReservation,
)


def _order(*, market_buy_intent, now, order_id="order-1", filled=False):
    return OrderRecord(
        order_id=order_id,
        intent=market_buy_intent,
        risk_decision_id="risk-1",
        status=OrderStatus.FILLED if filled else OrderStatus.VALIDATED,
        created_at=now,
        submitted_at=now if filled else None,
        filled_quantity=Decimal("10") if filled else Decimal("0"),
        average_fill_price=Decimal("101") if filled else None,
    )


def _reservation(*, market_buy_intent, now, version=1, key=None, fp="fp-1"):
    return RiskReservation(
        reservation_id=f"r-{key or market_buy_intent.idempotency_key}",
        idempotency_key=key or market_buy_intent.idempotency_key,
        intent_fingerprint=fp,
        strategy_id=market_buy_intent.strategy_id,
        symbol=market_buy_intent.symbol,
        signed_notional="1010",
        status=ReservationStatus.RESERVED,
        portfolio_version=version,
        created_at=now,
        updated_at=now,
    )


def test_inmemory_order_store_contract(market_buy_intent, now):
    store = InMemoryOrderStore()
    order = _order(market_buy_intent=market_buy_intent, now=now)
    created, stored = store.create_if_absent(order)
    assert created is True
    assert stored == order
    assert store.get_by_idempotency_key(market_buy_intent.idempotency_key) == order
    assert store.get_by_order_id(order.order_id) == order
    assert store.get_by_order_id("missing") is None
    assert store.all_orders() == (order,)

    created_again, existing = store.create_if_absent(replace(order, order_id="other"))
    assert created_again is False
    assert existing == order

    updated = replace(order, status=OrderStatus.SUBMITTED, submitted_at=now)
    store.update(updated)
    assert store.get_by_order_id(order.order_id) == updated

    with pytest.raises(KeyError):
        store.update(replace(updated, order_id="wrong"))


def test_inmemory_order_store_rejects_duplicate_order_id_with_other_key(
    market_buy_intent, now
):
    store = InMemoryOrderStore()
    first = _order(market_buy_intent=market_buy_intent, now=now)
    store.create_if_absent(first)
    other_intent = replace(
        market_buy_intent,
        intent_id="intent-other",
        idempotency_key="idem-other",
    )
    with pytest.raises(ValueError, match="duplicate order_id"):
        store.create_if_absent(
            _order(market_buy_intent=other_intent, now=now, order_id=first.order_id)
        )


def test_inmemory_safety_state_is_versioned_and_resettable(now):
    store = InMemorySafetyStateStore()
    assert store.get().version == 0
    assert store.get().kill_switch_active is False

    active = store.activate(reason="test", now=now)
    assert active.kill_switch_active is True
    assert active.kill_switch_reason == "test"
    assert active.version == 1

    reset = store.reset(now=now)
    assert reset.kill_switch_active is False
    assert reset.kill_switch_reason == ""
    assert reset.version == 2


def test_inmemory_portfolio_store_versioning_and_reconciliation(empty_portfolio, now):
    store = InMemoryPortfolioStore()
    with pytest.raises(PortfolioNotInitialized):
        store.get()

    first = store.initialize(empty_portfolio, now=now)
    assert first.version == 1
    assert store.initialize(replace(empty_portfolio, snapshot_id="ignored"), now=now) == first

    stale = store.compare_and_set(
        expected_version=99,
        snapshot=empty_portfolio,
        now=now,
    )
    assert stale is None

    changed_snapshot = replace(empty_portfolio, daily_pnl=Decimal("25"))
    changed = store.compare_and_set(
        expected_version=1,
        snapshot=changed_snapshot,
        now=now,
    )
    assert changed.version == 2
    assert changed.snapshot.daily_pnl == Decimal("25")

    same = store.set_reconciliation_status(
        reconciliation_ok=True,
        broker_state_known=True,
        now=now,
    )
    assert same.version == 2

    blocked = store.set_reconciliation_status(
        reconciliation_ok=False,
        broker_state_known=False,
        now=now,
    )
    assert blocked.version == 3
    assert blocked.snapshot.reconciliation_ok is False
    assert blocked.snapshot.broker_state_known is False


def test_inmemory_portfolio_fill_application_is_idempotent(
    empty_portfolio, market_buy_intent, now
):
    store = InMemoryPortfolioStore()
    store.initialize(empty_portfolio, now=now)

    no_fill = _order(market_buy_intent=market_buy_intent, now=now)
    before = store.apply_order_result(no_fill, now=now)
    assert before.version == 1

    filled = _order(
        market_buy_intent=market_buy_intent,
        now=now,
        order_id="filled-1",
        filled=True,
    )
    after = store.apply_order_result(filled, now=now)
    assert after.version == 2
    assert after.snapshot.gross_exposure == Decimal("1010")
    assert after.snapshot.net_exposure == Decimal("1010")
    assert after.snapshot.signed_position_notional_by_symbol == {
        "TEST-USD": Decimal("1010")
    }
    assert after.snapshot.strategy_gross_exposure == {
        "strategy-a": Decimal("1010")
    }

    replay = store.apply_order_result(filled, now=now)
    assert replay == after


def test_inmemory_reservation_store_contract(
    empty_portfolio, market_buy_intent, now
):
    portfolio = InMemoryPortfolioStore()
    current = portfolio.initialize(empty_portfolio, now=now)
    store = InMemoryReservationStore(portfolio)
    initial = store.active_view()
    assert initial.generation == 0
    assert initial.reservations == ()

    reservation = _reservation(
        market_buy_intent=market_buy_intent,
        now=now,
        version=current.version,
    )
    stored = store.reserve(
        reservation,
        expected_generation=0,
        expected_portfolio_version=current.version,
    )
    assert stored == reservation
    assert store.get(reservation.idempotency_key) == reservation
    assert store.get("missing") is None
    assert store.active_view().generation == 1

    same = store.reserve(
        reservation,
        expected_generation=999,
        expected_portfolio_version=999,
    )
    assert same == reservation

    with pytest.raises(ReservationConflict):
        store.reserve(
            replace(reservation, intent_fingerprint="different"),
            expected_generation=1,
            expected_portfolio_version=current.version,
        )

    unchanged = store.set_status(
        idempotency_key=reservation.idempotency_key,
        status=ReservationStatus.RESERVED,
        now=now,
    )
    assert unchanged == reservation

    opened = store.set_status(
        idempotency_key=reservation.idempotency_key,
        status=ReservationStatus.OPEN,
        now=now,
    )
    assert opened.status is ReservationStatus.OPEN
    assert store.active_view().generation == 2

    released = store.set_status(
        idempotency_key=reservation.idempotency_key,
        status=ReservationStatus.RELEASED,
        now=now,
    )
    assert released.status is ReservationStatus.RELEASED
    assert store.active_view().reservations == ()


def test_inmemory_reservation_store_detects_generation_and_portfolio_races(
    empty_portfolio, market_buy_intent, now
):
    portfolio = InMemoryPortfolioStore()
    current = portfolio.initialize(empty_portfolio, now=now)
    store = InMemoryReservationStore(portfolio)

    with pytest.raises(ReservationRace, match="generation"):
        store.reserve(
            _reservation(
                market_buy_intent=market_buy_intent,
                now=now,
                version=current.version,
                key="race-generation",
                fp="fp-g",
            ),
            expected_generation=9,
            expected_portfolio_version=current.version,
        )

    changed = portfolio.compare_and_set(
        expected_version=current.version,
        snapshot=replace(empty_portfolio, snapshot_id="portfolio-v2"),
        now=now,
    )
    assert changed is not None

    with pytest.raises(ReservationRace, match="portfolio"):
        store.reserve(
            _reservation(
                market_buy_intent=market_buy_intent,
                now=now,
                version=current.version,
                key="race-portfolio",
                fp="fp-p",
            ),
            expected_generation=0,
            expected_portfolio_version=current.version,
        )
