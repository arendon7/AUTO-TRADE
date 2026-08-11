from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.bootstrap import build_durable_paper_core
from autotrade.domain import OrderStatus, OrderType
from autotrade.oms import BrokerStateConflict


def _open_limit(core, market, intent):
    result = core.pipeline.process_intent(intent=intent, market=market, now=market.observed_at)
    assert result.order is not None
    assert result.order.status is OrderStatus.SUBMITTED
    return result.order


def test_replace_request_is_durably_evidenced_before_cancel(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "replace-evidence.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    old_intent = replace(
        market_buy_intent,
        intent_id="old-evidence",
        idempotency_key="old-evidence-key",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.5"),
    )
    old = _open_limit(core, market, old_intent)
    replacement = replace(
        old_intent,
        intent_id="new-evidence",
        idempotency_key="new-evidence-key",
        limit_price=Decimal("101"),
    )
    result = core.pipeline.replace_order(
        order_id=old.order_id,
        replacement_intent=replacement,
        market=market,
        now=market.observed_at + timedelta(milliseconds=1),
    )
    assert result.replaced is True
    events = [
        event
        for event in core.ledger.all_events()
        if event.event_id == f"replace-requested:{old.order_id}"
    ]
    assert len(events) == 1
    assert events[0].event_type == "ORDER_REPLACE_REQUESTED"
    assert events[0].payload["replacement_intent_id"] == replacement.intent_id


def test_replace_retry_after_crash_between_cancel_and_new_submit_resumes_safely(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "replace-resume.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    old_intent = replace(
        market_buy_intent,
        intent_id="old-resume",
        idempotency_key="old-resume-key",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.5"),
    )
    old = _open_limit(core, market, old_intent)
    replacement = replace(
        old_intent,
        intent_id="new-resume",
        idempotency_key="new-resume-key",
        limit_price=Decimal("101"),
    )
    core.oms.mark_replace_pending(
        order_id=old.order_id,
        replacement_intent_id=replacement.intent_id,
        now=market.observed_at + timedelta(milliseconds=1),
    )
    cancelled = core.pipeline.cancel_order(
        order_id=old.order_id,
        now=market.observed_at + timedelta(milliseconds=2),
    )
    assert cancelled.status is OrderStatus.CANCELLED
    assert core.oms.get_by_idempotency_key(replacement.idempotency_key) is None

    resumed = core.pipeline.replace_order(
        order_id=old.order_id,
        replacement_intent=replacement,
        market=market,
        now=market.observed_at + timedelta(milliseconds=3),
    )
    assert resumed.replaced is True
    assert resumed.replacement.order.status is OrderStatus.FILLED
    assert core.broker.submission_count == 2

    replay = core.pipeline.replace_order(
        order_id=old.order_id,
        replacement_intent=replacement,
        market=market,
        now=market.observed_at + timedelta(milliseconds=4),
    )
    assert replay.replacement.replayed is True
    assert core.broker.submission_count == 2


def test_conflicting_replacement_identity_for_same_original_fails_closed(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "replace-conflict.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    old_intent = replace(
        market_buy_intent,
        intent_id="old-conflict",
        idempotency_key="old-conflict-key",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.5"),
    )
    old = _open_limit(core, market, old_intent)
    core.oms.mark_replace_pending(
        order_id=old.order_id,
        replacement_intent_id="replacement-A",
        now=market.observed_at + timedelta(milliseconds=1),
    )
    with pytest.raises(BrokerStateConflict, match="ledger event identity conflict"):
        core.oms.mark_replace_pending(
            order_id=old.order_id,
            replacement_intent_id="replacement-B",
            now=market.observed_at + timedelta(milliseconds=1),
        )
