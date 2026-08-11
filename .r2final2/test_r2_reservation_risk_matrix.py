from dataclasses import replace
from decimal import Decimal

from autotrade.bootstrap import build_durable_paper_core
from autotrade.domain import OrderType


D = Decimal


def limit_intent(base, *, n: int, quantity: str):
    return replace(
        base,
        intent_id=f"reservation-{n}",
        idempotency_key=f"reservation-{n}",
        quantity=D(quantity),
        order_type=OrderType.LIMIT,
        limit_price=D("99.5"),
    )


def test_open_reservations_count_against_strategy_gross(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    isolated = replace(
        limits,
        max_order_notional=D("100000"),
        max_position_notional=D("100000"),
        max_strategy_gross_exposure=D("15000"),
        max_portfolio_gross_exposure=D("200000"),
        max_net_exposure=D("200000"),
        max_leverage=D("10"),
    )
    core = build_durable_paper_core(
        db_path=tmp_path / "reservation-strategy.db",
        limits=isolated,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    first = core.pipeline.process_intent(
        intent=limit_intent(market_buy_intent, n=1, quantity="100"),
        market=market,
        now=market.observed_at,
    )
    second = core.pipeline.process_intent(
        intent=limit_intent(market_buy_intent, n=2, quantity="60"),
        market=market,
        now=market.observed_at,
    )
    assert first.order is not None
    assert second.order is None
    assert second.decision.reason_code == "MAX_STRATEGY_GROSS"
    assert core.broker.submission_count == 1


def test_open_reservations_count_against_portfolio_gross(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    isolated = replace(
        limits,
        max_order_notional=D("100000"),
        max_position_notional=D("100000"),
        max_strategy_gross_exposure=D("100000"),
        max_portfolio_gross_exposure=D("15000"),
        max_net_exposure=D("200000"),
        max_leverage=D("10"),
    )
    core = build_durable_paper_core(
        db_path=tmp_path / "reservation-portfolio.db",
        limits=isolated,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    first = core.pipeline.process_intent(
        intent=limit_intent(market_buy_intent, n=1, quantity="100"),
        market=market,
        now=market.observed_at,
    )
    second = core.pipeline.process_intent(
        intent=limit_intent(market_buy_intent, n=2, quantity="60"),
        market=market,
        now=market.observed_at,
    )
    assert first.order is not None
    assert second.order is None
    assert second.decision.reason_code == "MAX_PORTFOLIO_GROSS"
    assert core.broker.submission_count == 1


def test_open_reservations_count_against_leverage(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    isolated = replace(
        limits,
        max_order_notional=D("100000"),
        max_position_notional=D("100000"),
        max_strategy_gross_exposure=D("100000"),
        max_portfolio_gross_exposure=D("200000"),
        max_net_exposure=D("200000"),
        max_leverage=D("1.5"),
    )
    small_equity = replace(empty_portfolio, equity=D("10000"))
    core = build_durable_paper_core(
        db_path=tmp_path / "reservation-leverage.db",
        limits=isolated,
        initial_portfolio=small_equity,
        now=market.observed_at,
    )
    first = core.pipeline.process_intent(
        intent=limit_intent(market_buy_intent, n=1, quantity="100"),
        market=market,
        now=market.observed_at,
    )
    second = core.pipeline.process_intent(
        intent=limit_intent(market_buy_intent, n=2, quantity="60"),
        market=market,
        now=market.observed_at,
    )
    assert first.order is not None
    assert second.order is None
    assert second.decision.reason_code == "MAX_LEVERAGE"
    assert core.broker.submission_count == 1
