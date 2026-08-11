from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from autotrade.bootstrap import build_durable_paper_core
from autotrade.domain import OrderStatus, OrderType


def test_replace_request_retry_keeps_first_event_time_and_does_not_conflict(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "replace-retry-time.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    intent = replace(
        market_buy_intent,
        intent_id="replace-time-old",
        idempotency_key="replace-time-old-key",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.5"),
    )
    opened = core.pipeline.process_intent(intent=intent, market=market, now=market.observed_at)
    order_id = opened.order.order_id
    first_time = market.observed_at + timedelta(milliseconds=1)
    second_time = market.observed_at + timedelta(seconds=1)

    first = core.oms.mark_replace_pending(
        order_id=order_id,
        replacement_intent_id="replacement-time",
        now=first_time,
    )
    second = core.oms.mark_replace_pending(
        order_id=order_id,
        replacement_intent_id="replacement-time",
        now=second_time,
    )
    assert first.status is OrderStatus.REPLACE_PENDING
    assert second == first
    events = [e for e in core.ledger.all_events() if e.event_id == f"replace-requested:{order_id}"]
    assert len(events) == 1
    assert events[0].occurred_at == first_time


def test_terminal_broker_snapshot_replay_is_semantically_idempotent_at_later_time(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "terminal-replay.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    result = core.pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        now=market.observed_at,
    )
    order = result.order
    execution = core.broker.get_execution(order.order_id)
    before = [e for e in core.ledger.all_events() if e.event_type == "ORDER_BROKER_RESULT"]
    assert len(before) == 1

    replay = core.oms.sync_from_broker(
        order_id=order.order_id,
        execution=execution,
        now=market.observed_at + timedelta(seconds=5),
        recovered=True,
    )
    assert replay == order
    after = [e for e in core.ledger.all_events() if e.event_type == "ORDER_BROKER_RESULT"]
    assert after == before
    assert core.ledger.verify_integrity() is True
