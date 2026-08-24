from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import OrderType, Side
from autotrade.fee_product_economics import (
    FeeChargeConvention,
    FeeLiquidityRole,
    FeeProductEconomicsIntegrityError,
    FeeProductEconomicsStatus,
    build_fee_product_economics_evidence,
    build_fee_product_policy,
)
from test_w82_fee_accounting import _build, _cost


def _bundle(limits, market, empty_portfolio, market_buy_intent, *, minimum="5", intent=None):
    intent = intent or market_buy_intent
    cost = _cost(fee="5")
    *_, fee = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=intent,
        cost=cost,
    )
    policy = build_fee_product_policy(
        policy_id="validation-policy",
        product_id="test-product",
        asset_class="crypto",
        venue="alpaca-paper-model",
        symbol=intent.symbol,
        base_currency="TEST",
        quote_currency="USD",
        charge_convention=FeeChargeConvention.RECEIVED_ASSET_PERCENT,
        liquidity_role=FeeLiquidityRole.WORST_CASE,
        minimum_fee_bps=Decimal(minimum),
        source_reference="validation-source",
        effective_at=market.observed_at - timedelta(seconds=1),
    )
    evidence = build_fee_product_economics_evidence(
        evidence_id="validation-product-evidence",
        policy=policy,
        fee_evidence=fee,
        cost_model=cost,
        execution_intent=intent,
        assessed_at=fee.assessed_at + timedelta(seconds=1),
    )
    return cost, fee, policy, evidence


def test_w82_product_policy_structural_validation(limits, market, empty_portfolio, market_buy_intent):
    _, _, policy, _ = _bundle(limits, market, empty_portfolio, market_buy_intent)
    mutations = (
        {"policy_id": " bad"},
        {"version": "OLD"},
        {"symbol": ""},
        {"base_currency": "usd"},
        {"quote_currency": "usd"},
        {"quote_currency": "TEST"},
        {"charge_convention": "RECEIVED_ASSET_PERCENT"},
        {"liquidity_role": "WORST_CASE"},
        {"minimum_fee_bps": Decimal("-1")},
        {"minimum_fee_bps": Decimal("NaN")},
        {"source_reference": ""},
        {"effective_at": market.observed_at.replace(tzinfo=None)},
        {"policy_hash": "bad"},
        {"policy_hash": "0" * 64},
    )
    for mutation in mutations:
        with pytest.raises(FeeProductEconomicsIntegrityError):
            replace(policy, **mutation)


def test_w82_product_scenario_structural_validation(limits, market, empty_portfolio, market_buy_intent):
    _, _, _, evidence = _bundle(limits, market, empty_portfolio, market_buy_intent)
    scenario = evidence.scenarios[0]
    mutations = (
        {"scenario_id": " bad"},
        {"source_fee_observation_hash": "bad"},
        {"symbol": ""},
        {"side": "BUY"},
        {"gross_filled_quantity": Decimal("-1")},
        {"execution_price": Decimal("0")},
        {"gross_quote_notional": Decimal("-1")},
        {"research_fee_bps": Decimal("-1")},
        {"required_minimum_fee_bps": Decimal("-1")},
        {"charged_fee_currency": "usd"},
        {"charged_fee_amount": Decimal("-1")},
        {"fee_quote_equivalent": Decimal("-1")},
        {"net_base_quantity_delta": Decimal("NaN")},
        {"net_quote_cash_delta": Decimal("Infinity")},
        {"status": "PASS"},
        {"reason_code": ""},
        {"status": FeeProductEconomicsStatus.BLOCKED},
        {"reason_code": "RESEARCH_FEE_BELOW_POLICY"},
        {"gross_quote_notional": scenario.gross_quote_notional + Decimal("1")},
        {"scenario_hash": "0" * 64},
    )
    for mutation in mutations:
        with pytest.raises(FeeProductEconomicsIntegrityError):
            replace(scenario, **mutation)


def test_w82_blocked_scenario_reason_must_match_policy_floor(limits, market, empty_portfolio, market_buy_intent):
    _, _, _, blocked = _bundle(
        limits, market, empty_portfolio, market_buy_intent, minimum="25"
    )
    scenario = blocked.scenarios[0]
    assert scenario.status is FeeProductEconomicsStatus.BLOCKED
    with pytest.raises(FeeProductEconomicsIntegrityError):
        replace(scenario, reason_code="FEE_POLICY_CONSERVATIVE")


def test_w82_zero_fill_product_scenario_invariants(limits, market, empty_portfolio, market_buy_intent):
    no_fill = replace(
        market_buy_intent,
        intent_id="validation-no-fill",
        idempotency_key="validation-no-fill-idem",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
    )
    _, _, _, evidence = _bundle(
        limits, market, empty_portfolio, market_buy_intent, intent=no_fill
    )
    scenario = evidence.scenarios[0]
    assert scenario.gross_filled_quantity == 0
    with pytest.raises(FeeProductEconomicsIntegrityError):
        replace(scenario, execution_price=Decimal("100"))
    with pytest.raises(FeeProductEconomicsIntegrityError):
        replace(scenario, charged_fee_amount=Decimal("1"))


def test_w82_product_evidence_aggregate_validation(limits, market, empty_portfolio, market_buy_intent):
    _, _, _, evidence = _bundle(limits, market, empty_portfolio, market_buy_intent)
    reverse = tuple(reversed(evidence.scenarios))
    duplicate = (evidence.scenarios[0], evidence.scenarios[0])

    # A scenario cannot be identity-mutated without recomputing its own canonical
    # hash; prove that local defense explicitly rather than trying to sneak an
    # invalid child into the aggregate.
    with pytest.raises(FeeProductEconomicsIntegrityError, match="scenario hash mismatch"):
        replace(evidence.scenarios[0], symbol="OTHER")

    mutations = (
        {"evidence_id": " bad"},
        {"version": "OLD"},
        {"fee_policy_hash": "bad"},
        {"product_id": " bad"},
        {"symbol": ""},
        {"symbol": "OTHER"},
        {"side": "BUY"},
        {"base_currency": "usd"},
        {"quote_currency": "usd"},
        {"charge_convention": "RECEIVED_ASSET_PERCENT"},
        {"liquidity_role": "WORST_CASE"},
        {"research_fee_bps": Decimal("-1")},
        {"required_minimum_fee_bps": Decimal("-1")},
        {"assessed_at": evidence.market_observed_at - timedelta(microseconds=1)},
        {"scenarios": ()},
        {"scenarios": reverse},
        {"scenarios": duplicate},
        {"status": FeeProductEconomicsStatus.BLOCKED},
        {"reason_codes": ("UNEXPECTED",)},
        {"fee_schedule_conservative": False},
        {"product_fee_economics_complete": False},
        {"literal_broker_fee_semantics_modeled": False},
        {"broker_authoritative_fee_proven": True},
        {"realized_profitability_authorized": True},
        {"paper_candidate_authorized": True},
        {"external_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"evidence_hash": "0" * 64},
    )
    for mutation in mutations:
        with pytest.raises(FeeProductEconomicsIntegrityError):
            replace(evidence, **mutation)


def test_w82_product_builder_rejects_wrong_types_and_drift(limits, market, empty_portfolio, market_buy_intent):
    cost, fee, policy, _ = _bundle(limits, market, empty_portfolio, market_buy_intent)
    common = dict(
        evidence_id="builder-validation",
        policy=policy,
        fee_evidence=fee,
        cost_model=cost,
        execution_intent=market_buy_intent,
        assessed_at=fee.assessed_at + timedelta(seconds=1),
    )
    for key, value in (
        ("policy", object()),
        ("fee_evidence", object()),
        ("cost_model", object()),
        ("execution_intent", object()),
    ):
        kwargs = dict(common)
        kwargs[key] = value
        with pytest.raises(TypeError):
            build_fee_product_economics_evidence(**kwargs)

    with pytest.raises(FeeProductEconomicsIntegrityError):
        build_fee_product_economics_evidence(
            **{**common, "assessed_at": fee.assessed_at - timedelta(microseconds=1)}
        )
    with pytest.raises(FeeProductEconomicsIntegrityError):
        build_fee_product_economics_evidence(
            **{**common, "cost_model": _cost(fee="6")}
        )
    drifted_intent = replace(market_buy_intent, idempotency_key="validation-drift")
    with pytest.raises(FeeProductEconomicsIntegrityError):
        build_fee_product_economics_evidence(
            **{**common, "execution_intent": drifted_intent}
        )
