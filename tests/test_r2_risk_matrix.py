from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import MarketSnapshot, OrderType, Side
from autotrade.ledger import InMemoryEventLedger
from autotrade.safety import CapitalSafetyKernel


D = Decimal


def flat_market(now, *, bid="100", ask="100", last="100"):
    return MarketSnapshot(
        symbol="TEST-USD",
        bid=D(bid),
        ask=D(ask),
        last=D(last),
        observed_at=now,
    )


def evaluate(limits, intent, market, portfolio, now):
    return CapitalSafetyKernel(limits, InMemoryEventLedger()).evaluate(
        intent=intent,
        market=market,
        portfolio=portfolio,
        now=now,
    )


def single_strategy_portfolio(
    empty_portfolio,
    *,
    strategy_id,
    symbol="TEST-USD",
    equity="100000",
    position="0",
    other_position="0",
    daily_pnl="0",
    drawdown="0",
):
    own = D(position)
    other = D(other_position)
    aggregate = {}
    strategy_positions = {}
    if own != 0:
        aggregate[symbol] = own
        strategy_positions.setdefault(strategy_id, {})[symbol] = own
    if other != 0:
        aggregate["OTHER-USD"] = other
        strategy_positions.setdefault("other-strategy", {})["OTHER-USD"] = other
    gross = sum((abs(value) for value in aggregate.values()), D("0"))
    net = sum(aggregate.values(), D("0"))
    strategy_gross = {
        sid: sum((abs(value) for value in values.values()), D("0"))
        for sid, values in strategy_positions.items()
    }
    return replace(
        empty_portfolio,
        equity=D(equity),
        gross_exposure=gross,
        net_exposure=net,
        daily_pnl=D(daily_pnl),
        drawdown=D(drawdown),
        signed_position_notional_by_symbol=aggregate,
        strategy_gross_exposure=strategy_gross,
        strategy_signed_position_notional_by_symbol=strategy_positions,
    )


def test_order_notional_exact_boundary_passes_and_epsilon_rejects(
    limits, now, empty_portfolio, market_buy_intent
):
    market = flat_market(now)
    exact = replace(market_buy_intent, quantity=D("100"))
    over = replace(
        market_buy_intent,
        intent_id="over",
        idempotency_key="over",
        quantity=D("100.0001"),
    )
    assert evaluate(limits, exact, market, empty_portfolio, now).reason_code == "APPROVED"
    assert evaluate(limits, over, market, empty_portfolio, now).reason_code == "MAX_ORDER_NOTIONAL"


def test_price_sanity_band_exact_boundary_passes_and_epsilon_rejects(
    limits, now, empty_portfolio, market_buy_intent
):
    market = flat_market(now, bid="99", ask="101", last="100")
    exact = replace(
        market_buy_intent,
        order_type=OrderType.LIMIT,
        limit_price=D("101"),
    )
    over = replace(
        exact,
        intent_id="over-band",
        idempotency_key="over-band",
        limit_price=D("101.0001"),
    )
    assert evaluate(limits, exact, market, empty_portfolio, now).reason_code == "APPROVED"
    assert evaluate(limits, over, market, empty_portfolio, now).reason_code == "PRICE_SANITY_BAND"


def test_market_age_exact_boundary_passes_then_stale_and_future_reject(
    limits, now, empty_portfolio, market_buy_intent
):
    exact_market = flat_market(now - timedelta(milliseconds=1000))
    stale_market = flat_market(now - timedelta(milliseconds=1001))
    future_market = flat_market(now + timedelta(milliseconds=1))
    assert evaluate(limits, market_buy_intent, exact_market, empty_portfolio, now).reason_code == "APPROVED"
    assert evaluate(limits, market_buy_intent, stale_market, empty_portfolio, now).reason_code == "STALE_MARKET_DATA"
    assert evaluate(limits, market_buy_intent, future_market, empty_portfolio, now).reason_code == "MARKET_FROM_FUTURE"


@pytest.mark.parametrize(
    "field,value",
    [("bid", "0"), ("ask", "-1"), ("last", "NaN"), ("last", "Infinity")],
)
def test_invalid_market_prices_fail_closed(
    limits, now, empty_portfolio, market_buy_intent, field, value
):
    market = replace(flat_market(now), **{field: D(value)})
    assert evaluate(limits, market_buy_intent, market, empty_portfolio, now).reason_code == "INVALID_MARKET_PRICE"


def test_crossed_book_and_symbol_mismatch_fail_closed(
    limits, now, empty_portfolio, market_buy_intent
):
    crossed = flat_market(now, bid="101", ask="100", last="100")
    assert evaluate(limits, market_buy_intent, crossed, empty_portfolio, now).reason_code == "INVALID_MARKET_BOOK"
    mismatch = replace(flat_market(now), symbol="OTHER")
    assert evaluate(limits, market_buy_intent, mismatch, empty_portfolio, now).reason_code == "MARKET_SYMBOL_MISMATCH"


def test_position_boundary_exact_passes_epsilon_rejects(
    limits, now, empty_portfolio, market_buy_intent
):
    isolated = replace(
        limits,
        max_order_notional=D("100000"),
        max_strategy_gross_exposure=D("100000"),
        max_portfolio_gross_exposure=D("200000"),
        max_net_exposure=D("200000"),
        max_leverage=D("10"),
    )
    base = single_strategy_portfolio(
        empty_portfolio,
        strategy_id=market_buy_intent.strategy_id,
        position="15000",
    )
    market = flat_market(now)
    exact = replace(market_buy_intent, quantity=D("50"))
    over = replace(exact, intent_id="p-over", idempotency_key="p-over", quantity=D("50.0001"))
    assert evaluate(isolated, exact, market, base, now).reason_code == "APPROVED"
    assert evaluate(isolated, over, market, base, now).reason_code == "MAX_POSITION_NOTIONAL"


def test_strategy_gross_boundary_exact_passes_epsilon_rejects(
    limits, now, empty_portfolio, market_buy_intent
):
    isolated = replace(
        limits,
        max_order_notional=D("100000"),
        max_position_notional=D("100000"),
        max_portfolio_gross_exposure=D("200000"),
        max_net_exposure=D("200000"),
        max_leverage=D("10"),
    )
    base = single_strategy_portfolio(
        empty_portfolio,
        strategy_id=market_buy_intent.strategy_id,
        position="20000",
    )
    market = flat_market(now)
    exact = replace(market_buy_intent, quantity=D("50"))
    over = replace(exact, intent_id="s-over", idempotency_key="s-over", quantity=D("50.0001"))
    assert evaluate(isolated, exact, market, base, now).reason_code == "APPROVED"
    assert evaluate(isolated, over, market, base, now).reason_code == "MAX_STRATEGY_GROSS"


def test_portfolio_gross_boundary_exact_passes_epsilon_rejects(
    limits, now, empty_portfolio, market_buy_intent
):
    isolated = replace(
        limits,
        max_order_notional=D("100000"),
        max_position_notional=D("100000"),
        max_strategy_gross_exposure=D("100000"),
        max_net_exposure=D("200000"),
        max_leverage=D("10"),
    )
    base = single_strategy_portfolio(
        empty_portfolio,
        strategy_id=market_buy_intent.strategy_id,
        other_position="40000",
    )
    market = flat_market(now)
    exact = replace(market_buy_intent, quantity=D("100"))
    over = replace(exact, intent_id="g-over", idempotency_key="g-over", quantity=D("100.0001"))
    assert evaluate(isolated, exact, market, base, now).reason_code == "APPROVED"
    assert evaluate(isolated, over, market, base, now).reason_code == "MAX_PORTFOLIO_GROSS"


def test_net_exposure_boundary_exact_passes_epsilon_rejects(
    limits, now, empty_portfolio, market_buy_intent
):
    isolated = replace(
        limits,
        max_order_notional=D("100000"),
        max_position_notional=D("100000"),
        max_strategy_gross_exposure=D("100000"),
        max_portfolio_gross_exposure=D("200000"),
        max_leverage=D("10"),
    )
    base = single_strategy_portfolio(
        empty_portfolio,
        strategy_id=market_buy_intent.strategy_id,
        other_position="20000",
    )
    market = flat_market(now)
    exact = replace(market_buy_intent, quantity=D("100"))
    over = replace(exact, intent_id="n-over", idempotency_key="n-over", quantity=D("100.0001"))
    assert evaluate(isolated, exact, market, base, now).reason_code == "APPROVED"
    assert evaluate(isolated, over, market, base, now).reason_code == "MAX_NET_EXPOSURE"


def test_leverage_boundary_exact_passes_epsilon_rejects(
    limits, now, empty_portfolio, market_buy_intent
):
    isolated = replace(
        limits,
        max_order_notional=D("100000"),
        max_position_notional=D("100000"),
        max_strategy_gross_exposure=D("100000"),
        max_portfolio_gross_exposure=D("200000"),
        max_net_exposure=D("200000"),
    )
    base = single_strategy_portfolio(
        empty_portfolio,
        strategy_id=market_buy_intent.strategy_id,
        equity="10000",
        other_position="10000",
    )
    market = flat_market(now)
    exact = replace(market_buy_intent, quantity=D("100"))
    over = replace(exact, intent_id="l-over", idempotency_key="l-over", quantity=D("100.0001"))
    assert evaluate(isolated, exact, market, base, now).reason_code == "APPROVED"
    assert evaluate(isolated, over, market, base, now).reason_code == "MAX_LEVERAGE"


def test_daily_loss_and_drawdown_exact_boundaries_block_new_risk(
    limits, now, empty_portfolio, market_buy_intent
):
    market = flat_market(now)
    daily = replace(empty_portfolio, daily_pnl=-limits.max_daily_loss)
    dd = replace(empty_portfolio, drawdown=limits.max_drawdown)
    assert evaluate(limits, market_buy_intent, market, daily, now).reason_code == "MAX_DAILY_LOSS"
    assert evaluate(limits, market_buy_intent, market, dd, now).reason_code == "MAX_DRAWDOWN"


def test_active_circuit_allows_strict_reduction_but_not_flip(
    limits, now, empty_portfolio, market_buy_intent
):
    ledger = InMemoryEventLedger()
    kernel = CapitalSafetyKernel(limits, ledger)
    kernel.activate_circuit(reason="risk breach", now=now)
    base = single_strategy_portfolio(
        empty_portfolio,
        strategy_id=market_buy_intent.strategy_id,
        position="5000",
    )
    market = flat_market(now)
    reduce_intent = replace(
        market_buy_intent,
        intent_id="reduce",
        idempotency_key="reduce",
        side=Side.SELL,
        quantity=D("10"),
    )
    flip_intent = replace(
        reduce_intent,
        intent_id="flip",
        idempotency_key="flip",
        quantity=D("60"),
    )
    reduction = kernel.evaluate(intent=reduce_intent, market=market, portfolio=base, now=now)
    flip = kernel.evaluate(intent=flip_intent, market=market, portfolio=base, now=now)
    assert reduction.reason_code == "APPROVED"
    assert reduction.risk_reducing is True
    assert flip.reason_code == "CIRCUIT_ACTIVE"
    assert flip.risk_reducing is False


def test_internally_inconsistent_portfolio_snapshot_fails_closed(
    limits, now, empty_portfolio, market_buy_intent
):
    market = flat_market(now)
    invalid_equity = replace(empty_portfolio, equity=D("NaN"))
    assert evaluate(limits, market_buy_intent, market, invalid_equity, now).reason_code == "INVALID_PORTFOLIO_SNAPSHOT"

    inconsistent = replace(
        empty_portfolio,
        gross_exposure=D("100"),
        net_exposure=D("500"),
        signed_position_notional_by_symbol={"TEST-USD": D("500")},
        strategy_gross_exposure={market_buy_intent.strategy_id: D("500")},
        strategy_signed_position_notional_by_symbol={
            market_buy_intent.strategy_id: {"TEST-USD": D("500")}
        },
    )
    assert evaluate(limits, market_buy_intent, market, inconsistent, now).reason_code == "INVALID_PORTFOLIO_SNAPSHOT"
