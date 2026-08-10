from dataclasses import replace
from decimal import Decimal

import pytest

from autotrade.brokers.paper import PaperBroker
from autotrade.domain import OrderRecord, OrderStatus, OrderType, Side
from autotrade.ledger import DuplicateLedgerEvent, InMemoryEventLedger, LedgerEvent


def validated_order(intent, now):
    return OrderRecord(
        order_id="order-1",
        intent=intent,
        risk_decision_id="decision-1",
        status=OrderStatus.VALIDATED,
        created_at=now,
    )


def test_paper_limit_order_can_rest(limits, market, market_buy_intent, now):
    broker = PaperBroker()
    intent = replace(
        market_buy_intent,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("99.5"),
    )
    result = broker.submit(order=validated_order(intent, now), market=market, now=now)
    assert result.status is OrderStatus.SUBMITTED
    assert result.fills == ()
    assert broker.submission_count == 1


def test_paper_marketable_limit_fills_at_touch(market, market_buy_intent, now):
    broker = PaperBroker()
    intent = replace(
        market_buy_intent,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("101"),
    )
    result = broker.submit(order=validated_order(intent, now), market=market, now=now)
    assert result.status is OrderStatus.FILLED
    assert result.fills[0].price == Decimal("101")


def test_paper_sell_market_fills_bid(market, market_buy_intent, now):
    broker = PaperBroker()
    intent = replace(market_buy_intent, side=Side.SELL)
    result = broker.submit(order=validated_order(intent, now), market=market, now=now)
    assert result.fills[0].price == Decimal("99")


def test_paper_broker_rejects_nonvalidated_order(market, market_buy_intent, now):
    broker = PaperBroker()
    order = replace(validated_order(market_buy_intent, now), status=OrderStatus.SUBMITTED)
    with pytest.raises(ValueError, match="VALIDATED"):
        broker.submit(order=order, market=market, now=now)


def test_paper_broker_rejects_symbol_mismatch(market, market_buy_intent, now):
    broker = PaperBroker()
    wrong_market = replace(market, symbol="OTHER")
    with pytest.raises(ValueError, match="symbol mismatch"):
        broker.submit(order=validated_order(market_buy_intent, now), market=wrong_market, now=now)


def test_ledger_is_append_only_by_event_id(now):
    ledger = InMemoryEventLedger()
    event = LedgerEvent(event_id="e1", event_type="TEST", occurred_at=now, payload={})
    ledger.append(event)
    with pytest.raises(DuplicateLedgerEvent):
        ledger.append(event)
    assert ledger.all_events() == (event,)
