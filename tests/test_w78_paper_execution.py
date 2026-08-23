from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.brokers.paper_execution import (
    DeterministicPaperExecutionBroker,
    PaperExecutionConfig,
    PaperExecutionConflict,
    PaperExecutionMarketError,
)
from autotrade.domain import MarketSnapshot, OrderRecord, OrderStatus, OrderType, Side
from autotrade.engine import TradingPipeline
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.safety import CapitalSafetyKernel


def _order(*, intent, order_id: str = "order-1") -> OrderRecord:
    return OrderRecord(
        order_id=order_id,
        intent=intent,
        risk_decision_id="risk-1",
        status=OrderStatus.VALIDATED,
        created_at=intent.created_at,
    )


def test_market_buy_uses_adverse_slippage(market, market_buy_intent):
    broker = DeterministicPaperExecutionBroker(
        config=PaperExecutionConfig(slippage_bps=Decimal("10"))
    )
    execution = broker.submit(
        order=_order(intent=market_buy_intent),
        market=market,
        now=market.observed_at,
    )

    assert execution.status is OrderStatus.FILLED
    assert len(execution.fills) == 1
    assert execution.fills[0].quantity == Decimal("10")
    assert execution.fills[0].price == Decimal("101.101")
    assert broker.submission_count == 1


def test_market_sell_uses_adverse_slippage(market, market_buy_intent):
    intent = replace(market_buy_intent, side=Side.SELL)
    broker = DeterministicPaperExecutionBroker(
        config=PaperExecutionConfig(slippage_bps=Decimal("10"))
    )
    execution = broker.submit(order=_order(intent=intent), market=market, now=market.observed_at)

    assert execution.status is OrderStatus.FILLED
    assert execution.fills[0].price == Decimal("98.901")


def test_limit_order_must_remain_marketable_after_slippage(market, market_buy_intent):
    intent = replace(
        market_buy_intent,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("101.05"),
    )
    broker = DeterministicPaperExecutionBroker(
        config=PaperExecutionConfig(slippage_bps=Decimal("10"))
    )

    execution = broker.submit(order=_order(intent=intent), market=market, now=market.observed_at)

    assert execution.status is OrderStatus.SUBMITTED
    assert execution.fills == ()


def test_marketable_limit_fills_at_adverse_touch_not_at_magic_limit(market, market_buy_intent):
    intent = replace(
        market_buy_intent,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("102"),
    )
    broker = DeterministicPaperExecutionBroker(
        config=PaperExecutionConfig(slippage_bps=Decimal("10"))
    )

    execution = broker.submit(order=_order(intent=intent), market=market, now=market.observed_at)

    assert execution.status is OrderStatus.FILLED
    assert execution.fills[0].price == Decimal("101.101")
    assert execution.fills[0].price < intent.limit_price


def test_partial_fill_is_explicit_and_never_silently_promoted_to_full(market, market_buy_intent):
    broker = DeterministicPaperExecutionBroker(
        config=PaperExecutionConfig(
            slippage_bps=Decimal("0"),
            max_fill_fraction=Decimal("0.4"),
        )
    )

    execution = broker.submit(
        order=_order(intent=market_buy_intent),
        market=market,
        now=market.observed_at,
    )

    assert execution.status is OrderStatus.PARTIALLY_FILLED
    assert execution.fills[0].quantity == Decimal("4.0")


def test_same_local_order_replay_is_broker_idempotent(market, market_buy_intent):
    broker = DeterministicPaperExecutionBroker()
    order = _order(intent=market_buy_intent)

    first = broker.submit(order=order, market=market, now=market.observed_at)
    second = broker.submit(order=order, market=market, now=market.observed_at)

    assert first == second
    assert broker.submission_count == 1
    assert first.fills[0].fill_id == second.fills[0].fill_id


def test_same_local_order_id_with_changed_intent_fails_closed(market, market_buy_intent):
    broker = DeterministicPaperExecutionBroker()
    broker.submit(
        order=_order(intent=market_buy_intent),
        market=market,
        now=market.observed_at,
    )
    changed = replace(market_buy_intent, intent_id="intent-changed", quantity=Decimal("9"))

    with pytest.raises(PaperExecutionConflict, match="different intent"):
        broker.submit(
            order=_order(intent=changed),
            market=market,
            now=market.observed_at,
        )
    assert broker.submission_count == 1


def test_stale_market_fails_before_simulated_submission(market, market_buy_intent):
    broker = DeterministicPaperExecutionBroker(
        config=PaperExecutionConfig(max_market_age=timedelta(seconds=1))
    )

    with pytest.raises(PaperExecutionMarketError, match="stale"):
        broker.submit(
            order=_order(intent=market_buy_intent),
            market=market,
            now=market.observed_at + timedelta(seconds=2),
        )
    assert broker.submission_count == 0


def test_future_market_fails_before_simulated_submission(market, market_buy_intent):
    broker = DeterministicPaperExecutionBroker()

    with pytest.raises(PaperExecutionMarketError, match="future"):
        broker.submit(
            order=_order(intent=market_buy_intent),
            market=market,
            now=market.observed_at - timedelta(microseconds=1),
        )
    assert broker.submission_count == 0


def test_crossed_market_fails_closed(market, market_buy_intent):
    crossed = replace(market, bid=Decimal("102"), ask=Decimal("101"))
    broker = DeterministicPaperExecutionBroker()

    with pytest.raises(PaperExecutionMarketError, match="crossed"):
        broker.submit(
            order=_order(intent=market_buy_intent),
            market=crossed,
            now=market.observed_at,
        )


def test_overwide_spread_fails_closed(market_buy_intent):
    now = market_buy_intent.created_at
    wide = MarketSnapshot(
        symbol=market_buy_intent.symbol,
        bid=Decimal("90"),
        ask=Decimal("110"),
        last=Decimal("100"),
        observed_at=now,
    )
    broker = DeterministicPaperExecutionBroker(
        config=PaperExecutionConfig(max_spread_bps=Decimal("100"))
    )

    with pytest.raises(PaperExecutionMarketError, match="spread"):
        broker.submit(order=_order(intent=market_buy_intent), market=wide, now=now)


def test_cancel_preserves_observed_partial_fill_and_is_terminal(market, market_buy_intent):
    broker = DeterministicPaperExecutionBroker(
        config=PaperExecutionConfig(
            slippage_bps=Decimal("0"),
            max_fill_fraction=Decimal("0.25"),
        )
    )
    order = _order(intent=market_buy_intent)
    partial = broker.submit(order=order, market=market, now=market.observed_at)

    cancelled = broker.cancel(order_id=order.order_id, now=market.observed_at)
    replay = broker.cancel(order_id=order.order_id, now=market.observed_at)

    assert partial.status is OrderStatus.PARTIALLY_FILLED
    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.fills == partial.fills
    assert replay == cancelled
    assert broker.cancel_count == 1


def test_oms_and_safety_reuse_existing_control_plane_for_partial_fill_and_cancel(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    ledger = InMemoryEventLedger()
    broker = DeterministicPaperExecutionBroker(
        config=PaperExecutionConfig(
            slippage_bps=Decimal("2"),
            max_fill_fraction=Decimal("0.4"),
        )
    )
    safety = CapitalSafetyKernel(limits, ledger)
    oms = OrderManagementSystem(broker=broker, ledger=ledger)
    pipeline = TradingPipeline(safety=safety, oms=oms)

    result = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )

    assert result.order is not None
    assert result.order.status is OrderStatus.PARTIALLY_FILLED
    assert result.order.filled_quantity == Decimal("4.0")
    assert result.order.average_fill_price == Decimal("101.0202")
    assert broker.submission_count == 1

    cancelled = oms.cancel(order_id=result.order.order_id, now=market.observed_at)
    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.filled_quantity == Decimal("4.0")
    assert broker.cancel_count == 1
    assert [event.event_type for event in ledger.all_events()] == [
        "RISK_DECISION",
        "ORDER_VALIDATED",
        "FILL",
        "ORDER_BROKER_RESULT",
        "ORDER_CANCEL_REQUESTED",
        "ORDER_BROKER_RESULT",
    ]


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"slippage_bps": Decimal("-1")}, "slippage_bps"),
        ({"slippage_bps": Decimal("501")}, "slippage_bps"),
        ({"max_fill_fraction": Decimal("0")}, "max_fill_fraction"),
        ({"max_fill_fraction": Decimal("1.01")}, "max_fill_fraction"),
        ({"max_spread_bps": Decimal("0")}, "max_spread_bps"),
        ({"max_market_age": timedelta(0)}, "max_market_age"),
    ],
)
def test_execution_assumptions_are_bounded(kwargs, error):
    with pytest.raises((TypeError, ValueError), match=error):
        PaperExecutionConfig(**kwargs)
