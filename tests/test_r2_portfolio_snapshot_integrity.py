from dataclasses import replace
from decimal import Decimal

from autotrade.ledger import InMemoryEventLedger
from autotrade.safety import CapitalSafetyKernel


def reason(limits, intent, market, portfolio, now):
    return CapitalSafetyKernel(limits, InMemoryEventLedger()).evaluate(
        intent=intent,
        market=market,
        portfolio=portfolio,
        now=now,
    ).reason_code


def test_nonfinite_position_map_fails_closed(
    limits, market, empty_portfolio, market_buy_intent
):
    snapshot = replace(
        empty_portfolio,
        gross_exposure=Decimal("NaN"),
        signed_position_notional_by_symbol={"TEST-USD": Decimal("NaN")},
    )
    assert reason(limits, market_buy_intent, market, snapshot, market.observed_at) == "INVALID_PORTFOLIO_SNAPSHOT"


def test_strategy_gross_must_exactly_match_strategy_position_map(
    limits, market, empty_portfolio, market_buy_intent
):
    snapshot = replace(
        empty_portfolio,
        gross_exposure=Decimal("100"),
        net_exposure=Decimal("100"),
        signed_position_notional_by_symbol={"TEST-USD": Decimal("100")},
        strategy_gross_exposure={market_buy_intent.strategy_id: Decimal("99")},
        strategy_signed_position_notional_by_symbol={
            market_buy_intent.strategy_id: {"TEST-USD": Decimal("100")}
        },
    )
    assert reason(limits, market_buy_intent, market, snapshot, market.observed_at) == "INVALID_PORTFOLIO_SNAPSHOT"


def test_strategy_gross_without_position_map_fails_closed(
    limits, market, empty_portfolio, market_buy_intent
):
    snapshot = replace(
        empty_portfolio,
        strategy_gross_exposure={market_buy_intent.strategy_id: Decimal("10")},
    )
    assert reason(limits, market_buy_intent, market, snapshot, market.observed_at) == "INVALID_PORTFOLIO_SNAPSHOT"


def test_consistent_multisymbol_snapshot_remains_acceptable(
    limits, market, empty_portfolio, market_buy_intent
):
    snapshot = replace(
        empty_portfolio,
        gross_exposure=Decimal("300"),
        net_exposure=Decimal("100"),
        signed_position_notional_by_symbol={
            "TEST-USD": Decimal("200"),
            "OTHER-USD": Decimal("-100"),
        },
        strategy_gross_exposure={
            market_buy_intent.strategy_id: Decimal("200"),
            "other": Decimal("100"),
        },
        strategy_signed_position_notional_by_symbol={
            market_buy_intent.strategy_id: {"TEST-USD": Decimal("200")},
            "other": {"OTHER-USD": Decimal("-100")},
        },
    )
    assert reason(limits, market_buy_intent, market, snapshot, market.observed_at) == "APPROVED"
