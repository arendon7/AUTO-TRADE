from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import OrderType, RiskDecisionStatus, Side
from autotrade.ledger import InMemoryEventLedger
from autotrade.safety import CapitalSafetyKernel, InvalidSafetyConfiguration


def make_kernel(limits):
    ledger = InMemoryEventLedger()
    return CapitalSafetyKernel(limits, ledger), ledger


@pytest.mark.parametrize(
    ("mutate_intent", "mutate_market", "reason"),
    [
        (lambda i: replace(i, intent_id=""), lambda m: m, "INVALID_INTENT_IDENTITY"),
        (lambda i: replace(i, symbol="OTHER"), lambda m: m, "SYMBOL_NOT_ALLOWED"),
        (lambda i: i, lambda m: replace(m, symbol="OTHER"), "MARKET_SYMBOL_MISMATCH"),
        (lambda i: i, lambda m: replace(m, ask=Decimal("0")), "INVALID_MARKET_PRICE"),
        (lambda i: i, lambda m: replace(m, bid=Decimal("102")), "INVALID_MARKET_BOOK"),
    ],
)
def test_basic_fail_closed_branches(
    mutate_intent,
    mutate_market,
    reason,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    safety, _ = make_kernel(limits)
    decision = safety.evaluate(
        intent=mutate_intent(market_buy_intent),
        market=mutate_market(market),
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    assert decision.reason_code == reason


def test_rejects_naive_timestamp(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = make_kernel(limits)
    naive_now = market.observed_at.replace(tzinfo=None)
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=empty_portfolio, now=naive_now)
    assert decision.reason_code == "NAIVE_TIMESTAMP"


def test_rejects_market_timestamp_from_future(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = make_kernel(limits)
    future = replace(market, observed_at=market.observed_at + timedelta(milliseconds=1))
    decision = safety.evaluate(intent=market_buy_intent, market=future, portfolio=empty_portfolio, now=market.observed_at)
    assert decision.reason_code == "MARKET_FROM_FUTURE"


def test_limit_requires_positive_price(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = make_kernel(limits)
    intent = replace(market_buy_intent, order_type=OrderType.LIMIT, limit_price=None)
    decision = safety.evaluate(intent=intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    assert decision.reason_code == "INVALID_LIMIT_PRICE"


def test_market_order_rejects_limit_price(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = make_kernel(limits)
    intent = replace(market_buy_intent, limit_price=Decimal("100"))
    decision = safety.evaluate(intent=intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    assert decision.reason_code == "UNEXPECTED_LIMIT_PRICE"


def test_order_type_allowlist_is_enforced(limits, market, empty_portfolio, market_buy_intent):
    restricted = replace(limits, allowed_order_types=frozenset({OrderType.LIMIT}))
    safety, _ = make_kernel(restricted)
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    assert decision.reason_code == "ORDER_TYPE_NOT_ALLOWED"


def test_position_limit(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = make_kernel(limits)
    portfolio = replace(
        empty_portfolio,
        gross_exposure=Decimal("19500"),
        net_exposure=Decimal("19500"),
        signed_position_notional_by_symbol={"TEST-USD": Decimal("19500")},
        strategy_gross_exposure={"strategy-a": Decimal("19500")},
        strategy_signed_position_notional_by_symbol={"strategy-a": {"TEST-USD": Decimal("19500")}},
    )
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.reason_code == "MAX_POSITION_NOTIONAL"


def test_strategy_gross_limit(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = make_kernel(limits)
    portfolio = replace(
        empty_portfolio,
        gross_exposure=Decimal("24500"),
        strategy_gross_exposure={"strategy-a": Decimal("24500")},
    )
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.reason_code == "MAX_STRATEGY_GROSS"


def test_portfolio_gross_limit(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = make_kernel(limits)
    portfolio = replace(empty_portfolio, gross_exposure=Decimal("49500"))
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.reason_code == "MAX_PORTFOLIO_GROSS"


def test_net_exposure_limit(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = make_kernel(limits)
    portfolio = replace(empty_portfolio, gross_exposure=Decimal("29000"), net_exposure=Decimal("29500"))
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.reason_code == "MAX_NET_EXPOSURE"


def test_leverage_limit(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = make_kernel(limits)
    portfolio = replace(empty_portfolio, equity=Decimal("10000"), gross_exposure=Decimal("19500"))
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.reason_code == "MAX_LEVERAGE"


def test_reducing_order_can_move_toward_compliance_when_position_already_over_limit(
    limits, market, empty_portfolio, market_buy_intent
):
    safety, _ = make_kernel(limits)
    portfolio = replace(
        empty_portfolio,
        gross_exposure=Decimal("25000"),
        net_exposure=Decimal("25000"),
        signed_position_notional_by_symbol={"TEST-USD": Decimal("25000")},
        strategy_gross_exposure={"strategy-a": Decimal("25000")},
        strategy_signed_position_notional_by_symbol={"strategy-a": {"TEST-USD": Decimal("25000")}},
    )
    intent = replace(
        market_buy_intent,
        intent_id="reduce-over-limit",
        idempotency_key="reduce-over-limit",
        side=Side.SELL,
        quantity=Decimal("10"),
    )
    decision = safety.evaluate(intent=intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.risk_reducing is True


@pytest.mark.parametrize(
    "portfolio",
    [
        {"signed_position_notional_by_symbol": {"TEST-USD": Decimal("NaN")}},
        {
            "gross_exposure": Decimal("100"),
            "signed_position_notional_by_symbol": {"TEST-USD": Decimal("200")},
        },
        {
            "strategy_gross_exposure": {"strategy-a": Decimal("100")},
            "strategy_signed_position_notional_by_symbol": {"strategy-a": {"TEST-USD": Decimal("200")}},
        },
    ],
)
def test_inconsistent_position_snapshots_fail_closed(
    portfolio, limits, market, empty_portfolio, market_buy_intent
):
    safety, _ = make_kernel(limits)
    snapshot = replace(empty_portfolio, **portfolio)
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=snapshot, now=market.observed_at)
    assert decision.reason_code == "INVALID_PORTFOLIO_SNAPSHOT"


def test_kill_switch_reset_requires_operator_identity(limits, market, empty_portfolio, market_buy_intent):
    safety, ledger = make_kernel(limits)
    safety.activate_kill_switch(reason="test", now=market.observed_at)
    assert safety.kill_switch_active is True
    with pytest.raises(ValueError):
        safety.reset_kill_switch(confirmed_by="", now=market.observed_at)
    safety.reset_kill_switch(confirmed_by="operator-1", now=market.observed_at)
    assert safety.kill_switch_active is False
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    assert decision.status is RiskDecisionStatus.APPROVED
    assert any(event.event_type == "KILL_SWITCH_RESET" for event in ledger.all_events())


@pytest.mark.parametrize(
    "updates",
    [
        {"max_open_orders": 0},
        {"stale_market_data_ms": 0},
        {"decision_ttl_ms": 0},
        {"price_deviation_bps": Decimal("-1")},
        {"max_leverage": Decimal("NaN")},
    ],
)
def test_invalid_safety_configurations_fail_at_startup(limits, updates):
    with pytest.raises(InvalidSafetyConfiguration):
        replace(limits, **updates)
