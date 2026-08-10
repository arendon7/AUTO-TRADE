from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.brokers.base import BrokerExecution
from autotrade.brokers.paper import PaperBroker
from autotrade.domain import OrderStatus
from autotrade.engine import TradingPipeline
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import (
    BrokerSubmissionAmbiguous,
    IdempotencyConflict,
    OrderManagementSystem,
    OrderRejectedByControlPlane,
)
from autotrade.safety import CapitalSafetyKernel


def build_stack(limits, broker=None):
    ledger = InMemoryEventLedger()
    broker = broker or PaperBroker()
    safety = CapitalSafetyKernel(limits, ledger)
    oms = OrderManagementSystem(broker=broker, ledger=ledger)
    pipeline = TradingPipeline(safety=safety, oms=oms)
    return ledger, broker, safety, oms, pipeline


def test_approved_intent_flows_to_fill_once(limits, market, empty_portfolio, market_buy_intent):
    ledger, broker, _, _, pipeline = build_stack(limits)
    result = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    assert result.order is not None
    assert result.order.status is OrderStatus.FILLED
    assert result.order.filled_quantity == Decimal("10")
    assert result.order.average_fill_price == Decimal("101")
    assert broker.submission_count == 1
    event_types = [event.event_type for event in ledger.all_events()]
    assert event_types == ["RISK_DECISION", "ORDER_VALIDATED", "ORDER_BROKER_RESULT", "FILL"]


def test_rejected_intent_never_touches_broker(limits, market, empty_portfolio, market_buy_intent):
    _, broker, _, _, pipeline = build_stack(limits)
    oversized = replace(market_buy_intent, quantity=Decimal("200"))
    result = pipeline.process_intent(
        intent=oversized,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    assert result.order is None
    assert result.decision.reason_code == "MAX_ORDER_NOTIONAL"
    assert broker.submission_count == 0


def test_identical_retry_is_idempotent(limits, market, empty_portfolio, market_buy_intent):
    _, broker, _, _, pipeline = build_stack(limits)
    first = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    second = pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    assert first.order == second.order
    assert broker.submission_count == 1


def test_same_idempotency_key_with_different_intent_is_blocked(limits, market, empty_portfolio, market_buy_intent):
    ledger, broker, safety, oms, pipeline = build_stack(limits)
    pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    conflicting = replace(market_buy_intent, intent_id="intent-2", quantity=Decimal("11"))
    decision = safety.evaluate(intent=conflicting, market=market, portfolio=empty_portfolio, now=market.observed_at)
    with pytest.raises(IdempotencyConflict):
        oms.submit(intent=conflicting, decision=decision, market=market, now=market.observed_at)
    assert broker.submission_count == 1
    assert ledger.all_events()[-1].event_type == "IDEMPOTENCY_CONFLICT"


def test_risk_approval_cannot_be_replayed_for_modified_intent(limits, market, empty_portfolio, market_buy_intent):
    _, broker, safety, oms, _ = build_stack(limits)
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    modified = replace(market_buy_intent, quantity=Decimal("11"))
    with pytest.raises(OrderRejectedByControlPlane, match="fingerprint"):
        oms.submit(intent=modified, decision=decision, market=market, now=market.observed_at)
    assert broker.submission_count == 0


def test_expired_risk_decision_cannot_submit(limits, market, empty_portfolio, market_buy_intent):
    _, broker, safety, oms, _ = build_stack(limits)
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    with pytest.raises(OrderRejectedByControlPlane, match="expired"):
        oms.submit(
            intent=market_buy_intent,
            decision=decision,
            market=market,
            now=market.observed_at + timedelta(seconds=1),
        )
    assert broker.submission_count == 0


class MalformedBroker:
    def submit(self, *, order, market, now):
        return BrokerExecution(status=OrderStatus.FILLED, fills=())


def test_invalid_broker_response_becomes_unknown_not_false_success(limits, market, empty_portfolio, market_buy_intent):
    ledger, _, safety, oms, _ = build_stack(limits, broker=MalformedBroker())
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    with pytest.raises(BrokerSubmissionAmbiguous):
        oms.submit(intent=market_buy_intent, decision=decision, market=market, now=market.observed_at)
    stored = oms.get_by_idempotency_key(market_buy_intent.idempotency_key)
    assert stored is not None
    assert stored.status is OrderStatus.UNKNOWN
    assert ledger.all_events()[-1].event_type == "ORDER_STATE_UNKNOWN"
