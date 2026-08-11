from datetime import timedelta
from decimal import Decimal

from autotrade.bootstrap import build_durable_paper_core
from autotrade.domain import OrderStatus, intent_fingerprint
from autotrade.state import ReservationStatus, RiskReservation


def test_startup_repairs_terminal_fill_committed_before_portfolio_projection(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    db = tmp_path / "terminal-before-projection.db"
    core = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )

    portfolio = core.portfolio_store.get()
    decision = core.safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=portfolio.snapshot,
        now=market.observed_at,
    )
    assert decision.approved_notional is not None
    view = core.reservation_store.active_view()
    reservation = RiskReservation(
        reservation_id="crash-reservation",
        idempotency_key=market_buy_intent.idempotency_key,
        intent_fingerprint=intent_fingerprint(market_buy_intent),
        strategy_id=market_buy_intent.strategy_id,
        symbol=market_buy_intent.symbol,
        signed_notional=str(market_buy_intent.side.sign * decision.approved_notional),
        status=ReservationStatus.RESERVED,
        portfolio_version=portfolio.version,
        created_at=market.observed_at,
        updated_at=market.observed_at,
    )
    core.reservation_store.reserve(
        reservation,
        expected_generation=view.generation,
        expected_portfolio_version=portfolio.version,
    )

    # OMS commits terminal order + durable fill evidence, then process crashes
    # before DurableTradingPipeline.apply_fills()/reservation release.
    order = core.oms.submit(
        intent=market_buy_intent,
        decision=decision,
        market=market,
        now=market.observed_at,
    )
    assert order.status is OrderStatus.FILLED
    assert len(core.oms.fills_for_order(order.order_id)) == 1
    assert core.portfolio_store.get().snapshot.gross_exposure == 0
    assert core.reservation_store.get(market_buy_intent.idempotency_key).status is ReservationStatus.RESERVED

    restarted = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at + timedelta(seconds=1),
    )

    assert restarted.startup_reconciliation.ok is True
    assert restarted.portfolio_store.get().snapshot.gross_exposure == Decimal("1010")
    assert restarted.reservation_store.get(market_buy_intent.idempotency_key).status is ReservationStatus.RELEASED
    assert restarted.ledger.verify_integrity() is True
