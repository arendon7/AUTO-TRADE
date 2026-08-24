from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import Side
from autotrade.fee_accounting import (
    FeeAccountingIntegrityError,
    FeeAccountingStatus,
    FeeEvidenceSource,
)
from autotrade.fee_product_economics import (
    FeeChargeConvention,
    FeeLiquidityRole,
    FeeProductEconomicsIntegrityError,
    FeeProductEconomicsStatus,
    build_fee_product_economics_evidence,
    build_fee_product_policy,
)
from autotrade.paper_fee_activity_evidence import (
    FeeActivityStatus,
    FeeNormalizationRule,
    PaperFeeActivityIntegrityError,
    build_paper_fee_activity_evidence,
    build_pending_paper_fee_activity_evidence,
)
from test_w82_fee_accounting import _build, _cost, _matrix


def _valid(limits, market, empty_portfolio, market_buy_intent, *, cost=None, intent=None):
    return _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=intent or market_buy_intent,
        cost=cost,
    )


def _product_policy(*, market, symbol, minimum_fee_bps="5"):
    return build_fee_product_policy(
        policy_id="alpaca-crypto-worst-case",
        product_id="test-product",
        asset_class="crypto",
        venue="alpaca-paper-model",
        symbol=symbol,
        base_currency="TEST",
        quote_currency="USD",
        charge_convention=FeeChargeConvention.RECEIVED_ASSET_PERCENT,
        liquidity_role=FeeLiquidityRole.WORST_CASE,
        minimum_fee_bps=Decimal(minimum_fee_bps),
        source_reference="preregistered-product-fee-policy",
        effective_at=market.observed_at - timedelta(seconds=1),
    )


def test_w82_contract_rejects_currency_basis_and_source_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    *_, contract, _ = _valid(limits, market, empty_portfolio, market_buy_intent)
    with pytest.raises(FeeAccountingIntegrityError, match="settlement_currency"):
        replace(contract, settlement_currency="usd")
    with pytest.raises(FeeAccountingIntegrityError, match="settlement currency"):
        replace(contract, fee_currency="EUR")
    with pytest.raises(FeeAccountingIntegrityError, match="only filled-notional"):
        replace(contract, fee_basis="ASSET_DEBIT")
    with pytest.raises(FeeAccountingIntegrityError, match="impersonate broker-authoritative"):
        replace(contract, source=FeeEvidenceSource.BROKER_AUTHORITATIVE)
    with pytest.raises(FeeAccountingIntegrityError, match="no broker-authoritative"):
        replace(contract, broker_authoritative_supported=True)
    with pytest.raises(FeeAccountingIntegrityError, match="scope"):
        replace(contract, accounting_scope="REALIZED_BROKER")


def test_w82_observation_rejects_double_count_source_and_net_economics_tamper(
    limits, market, empty_portfolio, market_buy_intent
):
    *_, evidence = _valid(limits, market, empty_portfolio, market_buy_intent)
    observation = evidence.observations[0]
    with pytest.raises(FeeAccountingIntegrityError, match="double-counted"):
        replace(observation, non_fee_components_counted_as_fee=True)
    with pytest.raises(FeeAccountingIntegrityError, match="broker authority"):
        replace(observation, broker_authoritative=True)
    with pytest.raises(FeeAccountingIntegrityError, match="broker authority"):
        replace(observation, source=FeeEvidenceSource.BROKER_AUTHORITATIVE)
    with pytest.raises(FeeAccountingIntegrityError, match="net quote cash delta"):
        replace(
            observation,
            net_quote_cash_delta=observation.net_quote_cash_delta + Decimal("0.01"),
        )
    with pytest.raises(FeeAccountingIntegrityError, match="gross notional"):
        replace(observation, gross_notional=observation.gross_notional + Decimal("1"))


def test_w82_aggregate_rejects_partial_or_falsified_completeness(
    limits, market, empty_portfolio, market_buy_intent
):
    *_, evidence = _valid(limits, market, empty_portfolio, market_buy_intent)
    with pytest.raises(FeeAccountingIntegrityError, match="aggregate fee economics"):
        replace(evidence, total_fee_amount=evidence.total_fee_amount + Decimal("0.01"))
    with pytest.raises(FeeAccountingIntegrityError, match="COMPLETE"):
        replace(evidence, status=FeeAccountingStatus.BLOCKED)
    with pytest.raises(FeeAccountingIntegrityError, match="mark fee accounting complete"):
        replace(evidence, fee_accounting_complete=False)
    with pytest.raises(FeeAccountingIntegrityError, match="broker-authoritative source"):
        replace(evidence, source=FeeEvidenceSource.BROKER_AUTHORITATIVE)
    with pytest.raises(FeeAccountingIntegrityError, match="evidence hash"):
        replace(evidence, evidence_hash="0" * 64)
    with pytest.raises(FeeAccountingIntegrityError, match="requires scenario observations"):
        replace(evidence, observations=())


def test_w82_zero_research_fee_is_only_base_arithmetic_complete_not_policy_complete(
    limits, market, empty_portfolio, market_buy_intent
):
    cost = _cost(fee="0")
    *_, evidence = _valid(
        limits, market, empty_portfolio, market_buy_intent, cost=cost
    )
    assert evidence.status is FeeAccountingStatus.COMPLETE
    assert evidence.fee_accounting_complete is True
    assert evidence.total_fee_amount == 0
    assert all(item.fee_bps == 0 and item.fee_amount == 0 for item in evidence.observations)
    product = build_fee_product_economics_evidence(
        evidence_id="w82-zero-fee-policy-check",
        policy=_product_policy(market=market, symbol=market_buy_intent.symbol, minimum_fee_bps="1"),
        fee_evidence=evidence,
        cost_model=cost,
        execution_intent=market_buy_intent,
        assessed_at=evidence.assessed_at + timedelta(seconds=1),
    )
    assert product.status is FeeProductEconomicsStatus.BLOCKED
    assert product.reason_codes == ("RESEARCH_FEE_BELOW_POLICY",)


def test_w82_contract_rejects_matrix_identity_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    cost, _, qualification, _, _, _, _ = _valid(
        limits, market, empty_portfolio, market_buy_intent
    )
    other_matrix = _matrix()
    changed_cost = _cost(fee="7")
    from autotrade.fee_accounting import build_simulated_fee_accounting_contract

    with pytest.raises(FeeAccountingIntegrityError, match="cost model hash"):
        build_simulated_fee_accounting_contract(
            contract_id="w82-rebind-rejected",
            cost_model=changed_cost,
            qualification=qualification,
            matrix=other_matrix,
            product_id="test-product",
            asset_class="crypto",
            venue="alpaca-paper-model",
            settlement_currency="USD",
            created_at=market.observed_at,
        )


def test_w82_crypto_buy_fee_reduces_received_asset_not_extra_quote_cash(
    limits, market, empty_portfolio, market_buy_intent
):
    cost = _cost(fee="25")
    *_, fee = _valid(limits, market, empty_portfolio, market_buy_intent, cost=cost)
    product = build_fee_product_economics_evidence(
        evidence_id="w82-buy-product",
        policy=_product_policy(market=market, symbol=market_buy_intent.symbol, minimum_fee_bps="25"),
        fee_evidence=fee,
        cost_model=cost,
        execution_intent=market_buy_intent,
        assessed_at=fee.assessed_at + timedelta(seconds=1),
    )
    scenario = next(item for item in product.scenarios if item.scenario_id == "baseline")
    assert product.status is FeeProductEconomicsStatus.PASS
    assert scenario.gross_filled_quantity == Decimal("10")
    assert scenario.execution_price == Decimal("101.0202")
    assert scenario.charged_fee_currency == "TEST"
    assert scenario.charged_fee_amount == Decimal("0.0250")
    assert scenario.fee_quote_equivalent == Decimal("2.52550500")
    assert scenario.net_base_quantity_delta == Decimal("9.9750")
    assert scenario.net_quote_cash_delta == Decimal("-1010.2020")
    base_observation = next(item for item in fee.observations if item.scenario_id == "baseline")
    assert scenario.fee_quote_equivalent == base_observation.fee_amount


def test_w82_crypto_sell_fee_reduces_quote_proceeds(
    limits, market, empty_portfolio, market_buy_intent
):
    sell = replace(
        market_buy_intent,
        intent_id="w82-product-sell",
        idempotency_key="w82-product-sell-idem",
        side=Side.SELL,
    )
    cost = _cost(fee="25")
    *_, fee = _valid(
        limits, market, empty_portfolio, market_buy_intent, cost=cost, intent=sell
    )
    product = build_fee_product_economics_evidence(
        evidence_id="w82-sell-product",
        policy=_product_policy(market=market, symbol=sell.symbol, minimum_fee_bps="25"),
        fee_evidence=fee,
        cost_model=cost,
        execution_intent=sell,
        assessed_at=fee.assessed_at + timedelta(seconds=1),
    )
    scenario = next(item for item in product.scenarios if item.scenario_id == "baseline")
    assert scenario.charged_fee_currency == "USD"
    assert scenario.charged_fee_amount == Decimal("2.47450500")
    assert scenario.fee_quote_equivalent == Decimal("2.47450500")
    assert scenario.net_base_quantity_delta == Decimal("-10")
    assert scenario.net_quote_cash_delta == Decimal("987.32749500")


def test_w82_research_fee_below_preregistered_policy_fails_closed(
    limits, market, empty_portfolio, market_buy_intent
):
    cost = _cost(fee="5")
    *_, fee = _valid(limits, market, empty_portfolio, market_buy_intent, cost=cost)
    product = build_fee_product_economics_evidence(
        evidence_id="w82-underpriced-product",
        policy=_product_policy(market=market, symbol=market_buy_intent.symbol, minimum_fee_bps="25"),
        fee_evidence=fee,
        cost_model=cost,
        execution_intent=market_buy_intent,
        assessed_at=fee.assessed_at + timedelta(seconds=1),
    )
    assert product.status is FeeProductEconomicsStatus.BLOCKED
    assert product.reason_codes == ("RESEARCH_FEE_BELOW_POLICY",)
    assert product.fee_schedule_conservative is False
    assert product.product_fee_economics_complete is False


def test_w82_product_policy_identity_and_time_are_hash_bound(
    limits, market, empty_portfolio, market_buy_intent
):
    cost = _cost(fee="25")
    *_, fee = _valid(limits, market, empty_portfolio, market_buy_intent, cost=cost)
    policy = _product_policy(market=market, symbol=market_buy_intent.symbol, minimum_fee_bps="25")
    with pytest.raises(FeeProductEconomicsIntegrityError, match="policy hash mismatch"):
        replace(policy, minimum_fee_bps=Decimal("5"))
    late_values = policy.to_dict()
    assert late_values["effective_at"]
    late_policy = build_fee_product_policy(
        policy_id="late-policy",
        product_id="test-product",
        asset_class="crypto",
        venue="alpaca-paper-model",
        symbol=market_buy_intent.symbol,
        base_currency="TEST",
        quote_currency="USD",
        charge_convention=FeeChargeConvention.RECEIVED_ASSET_PERCENT,
        liquidity_role=FeeLiquidityRole.WORST_CASE,
        minimum_fee_bps=Decimal("25"),
        source_reference="late-policy-source",
        effective_at=market.observed_at + timedelta(microseconds=1),
    )
    with pytest.raises(FeeProductEconomicsIntegrityError, match="not effective"):
        build_fee_product_economics_evidence(
            evidence_id="w82-late-policy",
            policy=late_policy,
            fee_evidence=fee,
            cost_model=cost,
            execution_intent=market_buy_intent,
            assessed_at=fee.assessed_at + timedelta(seconds=1),
        )


def test_w82_missing_same_day_fee_activity_is_pending_never_zero(now):
    pending = build_pending_paper_fee_activity_evidence(
        evidence_id="w82-fee-pending",
        account_fingerprint="a" * 64,
        order_id_query="order-1",
        order_query_hash="b" * 64,
        client_order_id="client-order-1",
        strategy_id="strategy-a",
        symbol="BTC/USD",
        side=Side.BUY,
        trade_observed_at=now,
        checked_at=now + timedelta(minutes=10),
        publication_deadline=now + timedelta(days=1),
    )
    assert pending.status is FeeActivityStatus.PENDING_PUBLICATION
    assert pending.fee_amount is None
    assert pending.zero_fee_inferred is False
    assert pending.broker_authoritative_fee_proven is False
    with pytest.raises(PaperFeeActivityIntegrityError, match="zero fee"):
        replace(pending, zero_fee_inferred=True)


def test_w82_observed_cfee_can_bind_non_usd_fee_with_activity_price(now):
    observed = build_paper_fee_activity_evidence(
        evidence_id="w82-cfee-observed",
        account_fingerprint="a" * 64,
        order_id_query="order-1",
        order_query_hash="b" * 64,
        client_order_id="client-order-1",
        strategy_id="strategy-a",
        symbol="BTC/USD",
        side=Side.BUY,
        activity_id="activity-1",
        activity_type="CFEE",
        fee_currency="BTC",
        quote_currency="USD",
        normalization_rule=FeeNormalizationRule.ABS_QTY_TIMES_PRICE,
        normalized_fee_amount=Decimal("0.000001"),
        activity_price=Decimal("100000"),
        fee_quote_equivalent=Decimal("0.1"),
        activity_created_at=now + timedelta(hours=6),
        captured_at=now + timedelta(hours=7),
        source_payload_sha256="c" * 64,
    )
    assert observed.broker_authoritative is True
    assert observed.paper_only is True
    assert observed.fee_quote_equivalent == Decimal("0.1")
    with pytest.raises(PaperFeeActivityIntegrityError, match="quote equivalent"):
        replace(observed, fee_quote_equivalent=Decimal("0.2"))


def test_w82_fee_activity_cannot_be_backdated_or_leave_paper_scope(now):
    observed = build_paper_fee_activity_evidence(
        evidence_id="w82-fee-time",
        account_fingerprint="a" * 64,
        order_id_query="order-1",
        order_query_hash="b" * 64,
        client_order_id="client-order-1",
        strategy_id="strategy-a",
        symbol="BTC/USD",
        side=Side.SELL,
        activity_id="activity-2",
        activity_type="FEE",
        fee_currency="USD",
        quote_currency="USD",
        normalization_rule=FeeNormalizationRule.ABS_NET_AMOUNT,
        normalized_fee_amount=Decimal("0.25"),
        activity_price=None,
        fee_quote_equivalent=Decimal("0.25"),
        activity_created_at=now + timedelta(hours=1),
        captured_at=now + timedelta(hours=2),
        source_payload_sha256="c" * 64,
    )
    with pytest.raises(PaperFeeActivityIntegrityError, match="PAPER"):
        replace(observed, paper_only=False)
    with pytest.raises(PaperFeeActivityIntegrityError, match="predate"):
        replace(observed, captured_at=now)
