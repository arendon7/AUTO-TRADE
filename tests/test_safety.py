from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import OrderType, PortfolioSnapshot, RiskDecisionStatus, Side
from autotrade.ledger import InMemoryEventLedger
from autotrade.safety import CapitalSafetyKernel, InvalidSafetyConfiguration, SafetyLimits


def kernel(limits: SafetyLimits):
    ledger = InMemoryEventLedger()
    return CapitalSafetyKernel(limits, ledger), ledger


def test_approves_normal_order(limits, market, empty_portfolio, market_buy_intent):
    safety, ledger = kernel(limits)
    decision = safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.approved_notional == Decimal("1010")
    assert ledger.all_events()[-1].payload["status"] == "APPROVED"


def test_rejects_oversized_order(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = kernel(limits)
    intent = replace(market_buy_intent, quantity=Decimal("200"))
    decision = safety.evaluate(intent=intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.reason_code == "MAX_ORDER_NOTIONAL"


def test_rejects_nan_quantity(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = kernel(limits)
    intent = replace(market_buy_intent, quantity=Decimal("NaN"))
    decision = safety.evaluate(intent=intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    assert decision.reason_code == "INVALID_QUANTITY"


def test_rejects_stale_market_data(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = kernel(limits)
    stale = replace(market, observed_at=market.observed_at - timedelta(seconds=2))
    decision = safety.evaluate(intent=market_buy_intent, market=stale, portfolio=empty_portfolio, now=market.observed_at)
    assert decision.reason_code == "STALE_MARKET_DATA"


def test_rejects_absurd_limit_price(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = kernel(limits)
    intent = replace(
        market_buy_intent,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("120"),
    )
    decision = safety.evaluate(intent=intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    assert decision.reason_code == "PRICE_SANITY_BAND"


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("reconciliation_ok", "RECONCILIATION_MISMATCH"),
        ("broker_state_known", "BROKER_STATE_UNKNOWN"),
    ],
)
def test_ambiguity_blocks_all_new_orders(field, reason, limits, market, empty_portfolio, market_buy_intent):
    safety, _ = kernel(limits)
    portfolio = replace(empty_portfolio, **{field: False})
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.reason_code == reason


def test_max_open_orders_blocks_order(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = kernel(limits)
    portfolio = replace(empty_portfolio, open_orders=limits.max_open_orders)
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.reason_code == "MAX_OPEN_ORDERS"


def test_kill_switch_blocks_risk_increase(limits, market, empty_portfolio, market_buy_intent):
    safety, ledger = kernel(limits)
    safety.activate_kill_switch(reason="operator emergency", now=market.observed_at)
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=empty_portfolio, now=market.observed_at)
    assert decision.reason_code == "KILL_SWITCH_ACTIVE"
    assert any(event.event_type == "KILL_SWITCH_ACTIVATED" for event in ledger.all_events())


def test_kill_switch_and_loss_limits_allow_strict_risk_reduction(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = kernel(limits)
    safety.activate_kill_switch(reason="operator emergency", now=market.observed_at)
    portfolio = replace(
        empty_portfolio,
        gross_exposure=Decimal("1000"),
        net_exposure=Decimal("1000"),
        daily_pnl=Decimal("-1500"),
        drawdown=Decimal("0.20"),
        signed_position_notional_by_symbol={"TEST-USD": Decimal("1000")},
        strategy_gross_exposure={"strategy-a": Decimal("1000")},
        strategy_signed_position_notional_by_symbol={"strategy-a": {"TEST-USD": Decimal("1000")}},
    )
    reduce_intent = replace(
        market_buy_intent,
        intent_id="intent-reduce",
        idempotency_key="idem-reduce",
        side=Side.SELL,
        quantity=Decimal("5"),
    )
    decision = safety.evaluate(intent=reduce_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.risk_reducing is True


def test_loss_limit_blocks_new_risk(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = kernel(limits)
    portfolio = replace(empty_portfolio, daily_pnl=Decimal("-1000"))
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.reason_code == "MAX_DAILY_LOSS"


def test_drawdown_limit_blocks_new_risk(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = kernel(limits)
    portfolio = replace(empty_portfolio, drawdown=Decimal("0.10"))
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.reason_code == "MAX_DRAWDOWN"


def test_invalid_portfolio_fails_closed(limits, market, empty_portfolio, market_buy_intent):
    safety, _ = kernel(limits)
    portfolio = replace(empty_portfolio, equity=Decimal("0"))
    decision = safety.evaluate(intent=market_buy_intent, market=market, portfolio=portfolio, now=market.observed_at)
    assert decision.reason_code == "INVALID_PORTFOLIO_SNAPSHOT"


def test_safety_configuration_has_no_dangerous_empty_universe_defaults(limits):
    with pytest.raises(InvalidSafetyConfiguration):
        replace(limits, allowed_symbols=frozenset())
