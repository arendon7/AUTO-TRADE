from datetime import timedelta
from decimal import Decimal

from autotrade.domain import OrderStatus
from autotrade.state import ReservationStatus

from test_r2_fill_lifecycle import build_scripted_core


def test_partial_fill_then_cancel_preserves_filled_exposure_and_releases_remainder(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, _, _, portfolio, reservations, oms, pipeline, reconciliation = build_scripted_core(
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
    assert first.order.status is OrderStatus.PARTIALLY_FILLED
    assert portfolio.get().snapshot.gross_exposure == Decimal("404")
    assert reservations.get(market_buy_intent.idempotency_key).status is ReservationStatus.OPEN

    cancelled = pipeline.cancel_order(
        order_id=first.order.order_id,
        now=market.observed_at + timedelta(milliseconds=1),
    )
    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.filled_quantity == Decimal("4")
    assert cancelled.average_fill_price == Decimal("101")
    assert portfolio.get().snapshot.gross_exposure == Decimal("404")
    assert reservations.get(market_buy_intent.idempotency_key).status is ReservationStatus.RELEASED

    result = reconciliation.reconcile(now=market.observed_at + timedelta(milliseconds=2))
    assert result.ok is True
    assert portfolio.get().snapshot.gross_exposure == Decimal("404")
    assert len(oms.fills_for_order(first.order.order_id)) == 1
