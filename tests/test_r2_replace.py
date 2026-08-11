from dataclasses import replace
from decimal import Decimal

import pytest

from autotrade.bootstrap import build_durable_paper_core
from autotrade.domain import OrderStatus, OrderType
from autotrade.engine import ReplacementAborted
from autotrade.oms import BrokerCancellationAmbiguous
from autotrade.state import ReservationStatus


def _open_limit(core, market, intent):
    result = core.pipeline.process_intent(
        intent=intent,
        market=market,
        now=market.observed_at,
    )
    assert result.order is not None
    assert result.order.status is OrderStatus.SUBMITTED
    return result.order


def test_replace_cancels_old_before_fresh_safety_approved_submission(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "replace.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    original_intent = replace(
        market_buy_intent,
        intent_id="replace-old",
        idempotency_key="replace-old-key",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.5"),
    )
    old = _open_limit(core, market, original_intent)

    replacement_intent = replace(
        original_intent,
        intent_id="replace-new",
        idempotency_key="replace-new-key",
        limit_price=Decimal("101"),
    )
    result = core.pipeline.replace_order(
        order_id=old.order_id,
        replacement_intent=replacement_intent,
        market=market,
        now=market.observed_at,
    )

    assert result.original_order.status is OrderStatus.CANCELLED
    assert result.replaced is True
    assert result.replacement.order.status is OrderStatus.FILLED
    assert core.broker.submission_count == 2
    assert core.reservation_store.get(original_intent.idempotency_key).status is ReservationStatus.RELEASED
    assert core.reservation_store.get(replacement_intent.idempotency_key).status is ReservationStatus.RELEASED
    assert core.portfolio_store.get().snapshot.gross_exposure == Decimal("1010")


def test_replace_does_not_submit_new_order_when_cancel_is_ambiguous(
    tmp_path, limits, market, empty_portfolio, market_buy_intent, monkeypatch
):
    core = build_durable_paper_core(
        db_path=tmp_path / "replace-ambiguous.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    original_intent = replace(
        market_buy_intent,
        intent_id="replace-old",
        idempotency_key="replace-old-key",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.5"),
    )
    old = _open_limit(core, market, original_intent)
    replacement_intent = replace(
        original_intent,
        intent_id="replace-new",
        idempotency_key="replace-new-key",
        limit_price=Decimal("101"),
    )

    def lost_cancel_ack(*, order_id, now):
        raise TimeoutError("lost cancel acknowledgement")

    monkeypatch.setattr(core.broker, "cancel", lost_cancel_ack)
    with pytest.raises(BrokerCancellationAmbiguous):
        core.pipeline.replace_order(
            order_id=old.order_id,
            replacement_intent=replacement_intent,
            market=market,
            now=market.observed_at,
        )

    assert core.broker.submission_count == 1
    assert core.oms.get_by_idempotency_key(replacement_intent.idempotency_key) is None
    assert core.oms.get_by_order_id(old.order_id).status is OrderStatus.UNKNOWN
    assert core.reservation_store.get(original_intent.idempotency_key).status is ReservationStatus.UNKNOWN


def test_replace_rechecks_risk_after_authoritative_cancel_and_can_reject_new_risk(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "replace-risk.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    original_intent = replace(
        market_buy_intent,
        intent_id="replace-old",
        idempotency_key="replace-old-key",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.5"),
    )
    old = _open_limit(core, market, original_intent)
    oversized = replace(
        original_intent,
        intent_id="replace-new",
        idempotency_key="replace-new-key",
        quantity=Decimal("200"),
        limit_price=Decimal("101"),
    )

    result = core.pipeline.replace_order(
        order_id=old.order_id,
        replacement_intent=oversized,
        market=market,
        now=market.observed_at,
    )
    assert result.original_order.status is OrderStatus.CANCELLED
    assert result.replaced is False
    assert result.replacement.order is None
    assert result.replacement.decision.reason_code == "MAX_ORDER_NOTIONAL"
    assert result.aborted_reason == "MAX_ORDER_NOTIONAL"
    assert core.broker.submission_count == 1


def test_replace_requires_new_identity_and_same_symbol_side_strategy(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "replace-identity.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    original_intent = replace(
        market_buy_intent,
        intent_id="replace-old",
        idempotency_key="replace-old-key",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.5"),
    )
    old = _open_limit(core, market, original_intent)

    with pytest.raises(ReplacementAborted, match="new idempotency key"):
        core.pipeline.replace_order(
            order_id=old.order_id,
            replacement_intent=replace(original_intent, intent_id="different"),
            market=market,
            now=market.observed_at,
        )

    with pytest.raises(ReplacementAborted, match="new intent_id"):
        core.pipeline.replace_order(
            order_id=old.order_id,
            replacement_intent=replace(original_intent, idempotency_key="different"),
            market=market,
            now=market.observed_at,
        )

    with pytest.raises(ReplacementAborted, match="preserve symbol, side and strategy"):
        core.pipeline.replace_order(
            order_id=old.order_id,
            replacement_intent=replace(
                original_intent,
                intent_id="different",
                idempotency_key="different",
                strategy_id="other-strategy",
            ),
            market=market,
            now=market.observed_at,
        )
