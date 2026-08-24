from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import Side
from autotrade.fee_accounting import FeeAccountingIntegrityError, FeeAccountingStatus, FeeEvidenceSource
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
    PaperFeeActivityIntegrityError,
    PaperFeeActivitySourceUnavailable,
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


def _policy(*, market, symbol, minimum="5", product="test-product", asset="crypto", venue="alpaca-paper-model", quote="USD"):
    return build_fee_product_policy(
        policy_id=f"policy-{product}-{minimum}",
        product_id=product,
        asset_class=asset,
        venue=venue,
        symbol=symbol,
        base_currency="TEST",
        quote_currency=quote,
        charge_convention=FeeChargeConvention.RECEIVED_ASSET_PERCENT,
        liquidity_role=FeeLiquidityRole.WORST_CASE,
        minimum_fee_bps=Decimal(minimum),
        source_reference="preregistered-product-fee-policy",
        effective_at=market.observed_at - timedelta(seconds=1),
    )


def test_w82_contract_and_observation_tamper_fail_closed(limits, market, empty_portfolio, market_buy_intent):
    *_, contract, evidence = _valid(limits, market, empty_portfolio, market_buy_intent)
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

    observation = evidence.observations[0]
    with pytest.raises(FeeAccountingIntegrityError, match="double-counted"):
        replace(observation, non_fee_components_counted_as_fee=True)
    with pytest.raises(FeeAccountingIntegrityError, match="broker authority"):
        replace(observation, broker_authoritative=True)
    with pytest.raises(FeeAccountingIntegrityError, match="net quote cash delta"):
        replace(observation, net_quote_cash_delta=observation.net_quote_cash_delta + Decimal("0.01"))
    with pytest.raises(FeeAccountingIntegrityError, match="gross notional"):
        replace(observation, gross_notional=observation.gross_notional + Decimal("1"))


def test_w82_aggregate_completeness_cannot_be_falsified(limits, market, empty_portfolio, market_buy_intent):
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


def test_w82_zero_fee_base_math_does_not_pass_nonzero_product_floor(limits, market, empty_portfolio, market_buy_intent):
    cost = _cost(fee="0")
    *_, evidence = _valid(limits, market, empty_portfolio, market_buy_intent, cost=cost)
    assert evidence.status is FeeAccountingStatus.COMPLETE
    assert evidence.total_fee_amount == 0
    product = build_fee_product_economics_evidence(
        evidence_id="zero-fee-policy-check",
        policy=_policy(market=market, symbol=market_buy_intent.symbol, minimum="1"),
        fee_evidence=evidence,
        cost_model=cost,
        execution_intent=market_buy_intent,
        assessed_at=evidence.assessed_at + timedelta(seconds=1),
    )
    assert product.status is FeeProductEconomicsStatus.BLOCKED
    assert product.reason_codes == ("RESEARCH_FEE_BELOW_POLICY",)
    assert product.fee_schedule_conservative is False
    assert product.product_fee_economics_complete is False


def test_w82_contract_rejects_cost_model_rebind(limits, market, empty_portfolio, market_buy_intent):
    _, _, qualification, _, _, _, _ = _valid(limits, market, empty_portfolio, market_buy_intent)
    from autotrade.fee_accounting import build_simulated_fee_accounting_contract

    with pytest.raises(FeeAccountingIntegrityError, match="cost model hash"):
        build_simulated_fee_accounting_contract(
            contract_id="rebind-rejected",
            cost_model=_cost(fee="7"),
            qualification=qualification,
            matrix=_matrix(),
            product_id="test-product",
            asset_class="crypto",
            venue="alpaca-paper-model",
            settlement_currency="USD",
            created_at=market.observed_at,
        )


def test_w82_received_asset_fee_semantics_for_buy(limits, market, empty_portfolio, market_buy_intent):
    cost = _cost(fee="25")
    *_, fee = _valid(limits, market, empty_portfolio, market_buy_intent, cost=cost)
    product = build_fee_product_economics_evidence(
        evidence_id="buy-product",
        policy=_policy(market=market, symbol=market_buy_intent.symbol, minimum="25"),
        fee_evidence=fee,
        cost_model=cost,
        execution_intent=market_buy_intent,
        assessed_at=fee.assessed_at + timedelta(seconds=1),
    )
    scenario = next(item for item in product.scenarios if item.scenario_id == "baseline")
    assert product.status is FeeProductEconomicsStatus.PASS
    assert product.literal_broker_fee_semantics_modeled is True
    assert product.broker_authoritative_fee_proven is False
    assert scenario.charged_fee_currency == "TEST"
    assert scenario.charged_fee_amount == Decimal("0.0250")
    assert scenario.fee_quote_equivalent == Decimal("2.52550500")
    assert scenario.net_base_quantity_delta == Decimal("9.9750")
    assert scenario.net_quote_cash_delta == Decimal("-1010.2020")


def test_w82_received_asset_policy_sell_fee_reduces_quote_proceeds(limits, market, empty_portfolio, market_buy_intent):
    sell = replace(market_buy_intent, intent_id="product-sell", idempotency_key="product-sell-idem", side=Side.SELL)
    cost = _cost(fee="25")
    *_, fee = _valid(limits, market, empty_portfolio, market_buy_intent, cost=cost, intent=sell)
    product = build_fee_product_economics_evidence(
        evidence_id="sell-product",
        policy=_policy(market=market, symbol=sell.symbol, minimum="25"),
        fee_evidence=fee,
        cost_model=cost,
        execution_intent=sell,
        assessed_at=fee.assessed_at + timedelta(seconds=1),
    )
    scenario = next(item for item in product.scenarios if item.scenario_id == "baseline")
    assert scenario.charged_fee_currency == "USD"
    assert scenario.charged_fee_amount == Decimal("2.47450500")
    assert scenario.net_base_quantity_delta == Decimal("-10")
    assert scenario.net_quote_cash_delta == Decimal("987.32749500")


def test_w82_product_policy_identity_currency_and_time_are_bound(limits, market, empty_portfolio, market_buy_intent):
    cost = _cost(fee="25")
    *_, fee = _valid(limits, market, empty_portfolio, market_buy_intent, cost=cost)
    good = _policy(market=market, symbol=market_buy_intent.symbol, minimum="25")
    with pytest.raises(FeeProductEconomicsIntegrityError, match="policy hash mismatch"):
        replace(good, minimum_fee_bps=Decimal("5"))

    for label, wrong in (
        ("product mismatch", _policy(market=market, symbol=market_buy_intent.symbol, minimum="25", product="other-product")),
        ("asset-class mismatch", _policy(market=market, symbol=market_buy_intent.symbol, minimum="25", asset="equity")),
        ("venue mismatch", _policy(market=market, symbol=market_buy_intent.symbol, minimum="25", venue="other-venue")),
        ("quote currency", _policy(market=market, symbol=market_buy_intent.symbol, minimum="25", quote="EUR")),
    ):
        with pytest.raises(FeeProductEconomicsIntegrityError, match=label):
            build_fee_product_economics_evidence(
                evidence_id=f"wrong-{label}", policy=wrong, fee_evidence=fee,
                cost_model=cost, execution_intent=market_buy_intent,
                assessed_at=fee.assessed_at + timedelta(seconds=1),
            )

    late = build_fee_product_policy(
        policy_id="late-policy", product_id="test-product", asset_class="crypto",
        venue="alpaca-paper-model", symbol=market_buy_intent.symbol,
        base_currency="TEST", quote_currency="USD",
        charge_convention=FeeChargeConvention.RECEIVED_ASSET_PERCENT,
        liquidity_role=FeeLiquidityRole.WORST_CASE,
        minimum_fee_bps=Decimal("25"), source_reference="late-policy-source",
        effective_at=market.observed_at + timedelta(microseconds=1),
    )
    with pytest.raises(FeeProductEconomicsIntegrityError, match="not effective"):
        build_fee_product_economics_evidence(
            evidence_id="late-evidence", policy=late, fee_evidence=fee, cost_model=cost,
            execution_intent=market_buy_intent, assessed_at=fee.assessed_at + timedelta(seconds=1),
        )


def test_w82_product_evidence_cannot_mint_authority(limits, market, empty_portfolio, market_buy_intent):
    cost = _cost(fee="25")
    *_, fee = _valid(limits, market, empty_portfolio, market_buy_intent, cost=cost)
    product = build_fee_product_economics_evidence(
        evidence_id="authority-product",
        policy=_policy(market=market, symbol=market_buy_intent.symbol, minimum="25"),
        fee_evidence=fee,
        cost_model=cost,
        execution_intent=market_buy_intent,
        assessed_at=fee.assessed_at + timedelta(seconds=1),
    )
    with pytest.raises(FeeProductEconomicsIntegrityError, match="broker fee proof"):
        replace(product, broker_authoritative_fee_proven=True)
    with pytest.raises(FeeProductEconomicsIntegrityError, match="realized profitability"):
        replace(product, realized_profitability_authorized=True)
    with pytest.raises(FeeProductEconomicsIntegrityError, match="authorize execution"):
        replace(product, paper_candidate_authorized=True)
    with pytest.raises(FeeProductEconomicsIntegrityError, match="capital or LIVE"):
        replace(product, live_trading="ENABLED")
    with pytest.raises(FeeProductEconomicsIntegrityError, match="evidence hash"):
        replace(product, evidence_hash="0" * 64)


def _pending(now):
    return build_pending_paper_fee_activity_evidence(
        evidence_id="fee-pending", account_fingerprint="a" * 64,
        order_id_query="order-1", order_query_hash="b" * 64,
        client_order_id="client-order-1", strategy_id="strategy-a",
        symbol="BTC/USD", side=Side.BUY, trade_observed_at=now,
        checked_at=now + timedelta(minutes=10), publication_deadline=now + timedelta(days=1),
    )


def test_w82_missing_fee_activity_is_pending_never_zero_and_network_free(now):
    pending = _pending(now)
    assert pending.status is FeeActivityStatus.PENDING_PUBLICATION
    assert pending.fee_amount is None
    assert pending.zero_fee_inferred is False
    assert pending.broker_authoritative_fee_proven is False
    assert pending.credentials_persisted is False
    assert pending.broker_network_performed is False
    assert pending.to_dict()["evidence_hash"] == pending.evidence_hash
    with pytest.raises(PaperFeeActivityIntegrityError, match="zero fee"):
        replace(pending, zero_fee_inferred=True)
    with pytest.raises(PaperFeeActivityIntegrityError, match="broker fee proof"):
        replace(pending, broker_authoritative_fee_proven=True)
    with pytest.raises(PaperFeeActivityIntegrityError, match="persist credentials"):
        replace(pending, credentials_persisted=True)
    with pytest.raises(PaperFeeActivityIntegrityError, match="broker network"):
        replace(pending, broker_network_performed=True)


def test_w82_pending_fee_activity_identity_time_and_hash_fail_closed(now):
    pending = _pending(now)
    with pytest.raises(PaperFeeActivityIntegrityError, match="predate trade"):
        replace(pending, checked_at=now - timedelta(microseconds=1))
    with pytest.raises(PaperFeeActivityIntegrityError, match="publication window"):
        replace(pending, publication_deadline=now)
    with pytest.raises(PaperFeeActivityIntegrityError, match="PAPER-only"):
        replace(pending, paper_only=False)
    with pytest.raises(PaperFeeActivityIntegrityError, match="hash mismatch"):
        replace(pending, evidence_hash="0" * 64)
    with pytest.raises(PaperFeeActivityIntegrityError, match="lowercase sha256"):
        replace(pending, account_fingerprint="bad")
    with pytest.raises(PaperFeeActivityIntegrityError, match="canonical W82"):
        replace(pending, version="old")


def test_w82_observed_broker_fee_builder_unavailable_until_adapter_certified():
    with pytest.raises(PaperFeeActivitySourceUnavailable, match="read-only broker fee source"):
        build_paper_fee_activity_evidence(
            activity_id="caller-supplied", normalized_fee_amount=Decimal("0.25"),
            gross_fill_quantity=Decimal("0.00014432"), net_position_quantity=Decimal("0.000143959"),
        )
