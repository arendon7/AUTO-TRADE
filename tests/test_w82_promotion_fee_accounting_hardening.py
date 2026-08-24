from dataclasses import fields
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import Side
import autotrade.fee_product_economics as product_module
from autotrade.fee_schedule_attestation import (
    build_alpaca_crypto_worst_case_fee_attestation,
)
from autotrade.promotion_fee_accounting import (
    PromotionFeeAccountingIntegrityError,
    resolve_promotion_fee_accounting,
)
from test_w82_promotion_fee_accounting import _candidate


def _rehash_scenario(scenario, **changes):
    values = {
        field.name: getattr(scenario, field.name)
        for field in fields(scenario)
        if field.name != "scenario_hash"
    }
    values.update(changes)
    return product_module.FeeProductScenarioEconomics(
        **values,
        scenario_hash=product_module._hash(
            product_module._scenario_payload_from_values(values)
        ),
    )


def _rehash_product(product, **changes):
    values = {
        field.name: getattr(product, field.name)
        for field in fields(product)
        if field.name != "evidence_hash"
    }
    values.update(changes)
    return product_module.FeeProductEconomicsEvidence(
        **values,
        evidence_hash=product_module._hash(
            product_module._evidence_payload_from_values(values)
        ),
    )


def _resolve(*, w81, fee, product, attestation, intent, resolved_at=None):
    return resolve_promotion_fee_accounting(
        resolution_id="w82-hardening-resolution",
        w81_resolution=w81,
        fee_evidence=fee,
        product_economics=product,
        fee_schedule_attestation=attestation,
        execution_intent=intent,
        resolved_at=resolved_at or product.assessed_at + timedelta(seconds=1),
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ({"product_id": "other-product"}, "product binding mismatch"),
        ({"asset_class": "us_equity"}, "asset-class binding mismatch"),
        ({"venue": "other-venue"}, "venue binding mismatch"),
        ({"research_cost_model_hash": "c" * 64}, "cost-model binding mismatch"),
    ),
)
def test_w82_final_resolution_rejects_validly_rehashed_parent_identity_drift(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    mutation,
    message,
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    drifted = _rehash_product(product, **mutation)

    with pytest.raises(PromotionFeeAccountingIntegrityError, match=message):
        _resolve(
            w81=w81,
            fee=fee,
            product=drifted,
            attestation=attestation,
            intent=market_buy_intent,
        )


def test_w82_final_resolution_rejects_validly_rehashed_market_time_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    drifted = _rehash_product(
        product,
        market_observed_at=product.market_observed_at - timedelta(microseconds=1),
    )

    with pytest.raises(
        PromotionFeeAccountingIntegrityError,
        match="market-time binding mismatch",
    ):
        _resolve(
            w81=w81,
            fee=fee,
            product=drifted,
            attestation=attestation,
            intent=market_buy_intent,
        )


def test_w82_final_resolution_rejects_validly_rehashed_symbol_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    scenarios = tuple(
        _rehash_scenario(item, symbol="OTHER-USD")
        for item in product.scenarios
    )
    drifted = _rehash_product(product, symbol="OTHER-USD", scenarios=scenarios)

    with pytest.raises(
        PromotionFeeAccountingIntegrityError,
        match="symbol binding mismatch",
    ):
        _resolve(
            w81=w81,
            fee=fee,
            product=drifted,
            attestation=attestation,
            intent=market_buy_intent,
        )


def test_w82_final_resolution_rejects_validly_rehashed_side_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    scenarios = tuple(
        _rehash_scenario(item, side=Side.SELL)
        for item in product.scenarios
    )
    drifted = _rehash_product(product, side=Side.SELL, scenarios=scenarios)

    with pytest.raises(
        PromotionFeeAccountingIntegrityError,
        match="side binding mismatch",
    ):
        _resolve(
            w81=w81,
            fee=fee,
            product=drifted,
            attestation=attestation,
            intent=market_buy_intent,
        )


def test_w82_final_resolution_rejects_percent_fee_above_full_notional(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    drifted = _rehash_product(
        product, research_fee_bps=Decimal("10000.0001")
    )

    with pytest.raises(
        PromotionFeeAccountingIntegrityError,
        match="percent fee may not exceed 100%",
    ):
        _resolve(
            w81=w81,
            fee=fee,
            product=drifted,
            attestation=attestation,
            intent=market_buy_intent,
        )


def test_w82_final_resolution_rejects_negative_net_base_after_buy_fee(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    first, *rest = product.scenarios
    impossible = _rehash_scenario(
        first, net_base_quantity_delta=Decimal("-0.00000001")
    )
    scenarios = tuple(
        sorted((impossible, *rest), key=lambda item: item.scenario_id)
    )
    drifted = _rehash_product(product, scenarios=scenarios)

    with pytest.raises(
        PromotionFeeAccountingIntegrityError,
        match="BUY product fee economics have impossible net direction",
    ):
        _resolve(
            w81=w81,
            fee=fee,
            product=drifted,
            attestation=attestation,
            intent=market_buy_intent,
        )


def test_w82_final_resolution_rejects_received_asset_buy_fee_currency_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    first, *rest = product.scenarios
    drifted_scenario = _rehash_scenario(
        first, charged_fee_currency=product.quote_currency
    )
    scenarios = tuple(
        sorted((drifted_scenario, *rest), key=lambda item: item.scenario_id)
    )
    drifted = _rehash_product(product, scenarios=scenarios)

    with pytest.raises(
        PromotionFeeAccountingIntegrityError,
        match="received-asset BUY fee currency binding mismatch",
    ):
        _resolve(
            w81=w81,
            fee=fee,
            product=drifted,
            attestation=attestation,
            intent=market_buy_intent,
        )


def test_w82_final_resolution_rejects_fee_schedule_attestation_identity_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, _ = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    wrong = build_alpaca_crypto_worst_case_fee_attestation(
        attestation_id="w82-wrong-venue-attestation",
        product_id=product.product_id,
        venue="other-venue",
        symbol=product.symbol,
    )
    with pytest.raises(
        PromotionFeeAccountingIntegrityError,
        match="attestation venue mismatch",
    ):
        _resolve(
            w81=w81,
            fee=fee,
            product=product,
            attestation=wrong,
            intent=market_buy_intent,
        )


def test_w82_final_resolution_rejects_stale_documented_fee_schedule(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    stale_resolution_time = attestation.source_checked_at + timedelta(days=31)
    with pytest.raises(
        PromotionFeeAccountingIntegrityError,
        match="attestation is stale",
    ):
        _resolve(
            w81=w81,
            fee=fee,
            product=product,
            attestation=attestation,
            intent=market_buy_intent,
            resolved_at=stale_resolution_time,
        )
