from datetime import timedelta
from decimal import Decimal

from autotrade.domain import OrderStatus
from autotrade.engine import DurableTradingPipeline
from autotrade.execution_state import SQLiteFillAwarePortfolioStore, SQLiteFillStore
from autotrade.ledger import SQLiteEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.paper_execution_evidence import capture_paper_execution_evidence
from autotrade.paper_execution_scenarios import build_paper_execution_scenario
from autotrade.persistence import (
    SQLiteOrderStore,
    SQLiteReservationStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
)
from autotrade.safety import CapitalSafetyKernel
from autotrade.state import ReservationStatus


def _durable_w78_stack(*, db_path, limits, empty_portfolio, now, fill_fraction: str):
    runtime = SQLiteRuntime(db_path)
    ledger = SQLiteEventLedger(runtime)
    portfolio = SQLiteFillAwarePortfolioStore(runtime)
    portfolio.initialize(empty_portfolio, now=now)
    safety_state = SQLiteSafetyStateStore(runtime)
    reservations = SQLiteReservationStore(runtime)
    fills = SQLiteFillStore(runtime)
    scenario = build_paper_execution_scenario(
        scenario_id="durable_stress",
        purpose="Durable OMS partial-fill qualification",
        slippage_bps=Decimal("2"),
        max_fill_fraction=Decimal(fill_fraction),
        max_market_age=timedelta(seconds=2),
        max_spread_bps=Decimal("250"),
    )
    broker = scenario.build_broker()
    safety = CapitalSafetyKernel(limits, ledger, state_store=safety_state)
    oms = OrderManagementSystem(
        broker=broker,
        ledger=ledger,
        order_store=SQLiteOrderStore(runtime),
        safety_state_store=safety_state,
        fill_store=fills,
    )
    pipeline = DurableTradingPipeline(
        safety=safety,
        oms=oms,
        portfolio_store=portfolio,
        reservation_store=reservations,
    )
    return runtime, ledger, portfolio, reservations, scenario, broker, oms, pipeline


def test_partial_fill_updates_durable_portfolio_and_keeps_remaining_order_reserved(
    tmp_path,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    _, ledger, portfolio, reservations, scenario, broker, oms, pipeline = _durable_w78_stack(
        db_path=tmp_path / "w78.db",
        limits=limits,
        empty_portfolio=empty_portfolio,
        now=market.observed_at,
        fill_fraction="0.4",
    )

    result = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        now=market.observed_at,
    )

    assert result.order is not None
    assert result.order.status is OrderStatus.PARTIALLY_FILLED
    assert result.order.filled_quantity == Decimal("4.0")
    assert result.order.average_fill_price == Decimal("101.0202")
    assert broker.submission_count == 1
    assert portfolio.get().snapshot.gross_exposure == Decimal("404.08080")
    reservation = reservations.get(market_buy_intent.idempotency_key)
    assert reservation is not None
    assert reservation.status is ReservationStatus.OPEN
    assert oms.fills_for_order(result.order.order_id)[0].quantity == Decimal("4.0")
    assert ledger.verify_integrity() is True

    evidence = capture_paper_execution_evidence(
        scenario=scenario,
        order=result.order,
        market=market,
        captured_at=market.observed_at,
    )
    assert evidence.fill_ratio == Decimal("0.4")


def test_cancel_partial_fill_releases_reservation_without_erasing_filled_exposure(
    tmp_path,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    _, ledger, portfolio, reservations, scenario, broker, _, pipeline = _durable_w78_stack(
        db_path=tmp_path / "w78.db",
        limits=limits,
        empty_portfolio=empty_portfolio,
        now=market.observed_at,
        fill_fraction="0.4",
    )
    opened = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        now=market.observed_at,
    )
    assert opened.order is not None
    before = portfolio.get().snapshot.gross_exposure

    cancelled = pipeline.cancel_order(
        order_id=opened.order.order_id,
        now=market.observed_at + timedelta(milliseconds=1),
    )

    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.filled_quantity == Decimal("4.0")
    assert portfolio.get().snapshot.gross_exposure == before
    reservation = reservations.get(market_buy_intent.idempotency_key)
    assert reservation is not None
    assert reservation.status is ReservationStatus.RELEASED
    assert broker.cancel_count == 1
    assert ledger.verify_integrity() is True

    evidence = capture_paper_execution_evidence(
        scenario=scenario,
        order=cancelled,
        market=market,
        captured_at=market.observed_at + timedelta(milliseconds=1),
    )
    assert evidence.order_status == OrderStatus.CANCELLED.value
    assert evidence.fill_ratio == Decimal("0.4")


def test_full_fill_releases_durable_reservation_and_projects_full_position(
    tmp_path,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    _, ledger, portfolio, reservations, _, broker, _, pipeline = _durable_w78_stack(
        db_path=tmp_path / "w78.db",
        limits=limits,
        empty_portfolio=empty_portfolio,
        now=market.observed_at,
        fill_fraction="1",
    )

    result = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        now=market.observed_at,
    )

    assert result.order is not None
    assert result.order.status is OrderStatus.FILLED
    assert result.order.average_fill_price == Decimal("101.0202")
    assert portfolio.get().snapshot.gross_exposure == Decimal("1010.2020")
    reservation = reservations.get(market_buy_intent.idempotency_key)
    assert reservation is not None
    assert reservation.status is ReservationStatus.RELEASED
    assert broker.submission_count == 1
    assert ledger.verify_integrity() is True
