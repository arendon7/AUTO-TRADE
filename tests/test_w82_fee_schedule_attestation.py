from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect

import pytest

from autotrade.fee_schedule_attestation import (
    ALPACA_CRYPTO_CONSERVATIVE_FLOOR_BPS,
    ALPACA_CRYPTO_FEE_CHARGE_BASIS,
    ALPACA_CRYPTO_FEE_SOURCE_CHECKED_AT,
    ALPACA_CRYPTO_FEE_SOURCE_URL,
    ALPACA_CRYPTO_POSTING_SEMANTICS,
    ALPACA_CRYPTO_TIER1_MAKER_BPS,
    ALPACA_CRYPTO_TIER1_TAKER_BPS,
    FEE_SCHEDULE_ATTESTATION_VERSION,
    FeeScheduleAttestationIntegrityError,
    MAX_FEE_SCHEDULE_ATTESTATION_AGE,
    build_alpaca_crypto_worst_case_fee_attestation,
)


def _attestation():
    return build_alpaca_crypto_worst_case_fee_attestation(
        attestation_id="w82-fee-source",
        product_id="test-product",
        venue="alpaca-paper-model",
        symbol="BTC/USD",
    )


def test_w82_alpaca_crypto_fee_schedule_is_fixed_conservative_and_no_authority():
    value = _attestation()
    assert value.version == FEE_SCHEDULE_ATTESTATION_VERSION
    assert value.source_url == ALPACA_CRYPTO_FEE_SOURCE_URL
    assert value.source_checked_at == ALPACA_CRYPTO_FEE_SOURCE_CHECKED_AT
    assert value.asset_class == "crypto"
    assert value.maker_fee_bps == ALPACA_CRYPTO_TIER1_MAKER_BPS == Decimal("15")
    assert value.taker_fee_bps == ALPACA_CRYPTO_TIER1_TAKER_BPS == Decimal("25")
    assert value.required_fee_floor_bps == ALPACA_CRYPTO_CONSERVATIVE_FLOOR_BPS == Decimal("25")
    assert value.fee_charge_basis == ALPACA_CRYPTO_FEE_CHARGE_BASIS
    assert value.posting_semantics == ALPACA_CRYPTO_POSTING_SEMANTICS
    assert value.broker_authoritative_activity_proven is False
    assert value.external_execution_authorized is False
    assert value.capital_authority == "NONE"
    assert value.live_trading == "BLOCKED"


def test_w82_fee_schedule_factory_does_not_allow_caller_to_refresh_source_timestamp():
    signature = inspect.signature(build_alpaca_crypto_worst_case_fee_attestation)
    assert "source_checked_at" not in signature.parameters


def test_w82_fee_schedule_tampering_fails_closed():
    value = _attestation()
    mutations = (
        {"version": "OLD"},
        {"source_url": "https://example.invalid/fees"},
        {"source_checked_at": value.source_checked_at + timedelta(seconds=1)},
        {"asset_class": "us_equity"},
        {"maker_fee_bps": Decimal("14.99")},
        {"taker_fee_bps": Decimal("24.99")},
        {"required_fee_floor_bps": Decimal("5")},
        {"fee_charge_basis": "QUOTE_ONLY"},
        {"posting_semantics": "REAL_TIME"},
        {"broker_authoritative_activity_proven": True},
        {"external_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"attestation_hash": "0" * 64},
    )
    for mutation in mutations:
        with pytest.raises(FeeScheduleAttestationIntegrityError):
            replace(value, **mutation)


def test_w82_fee_schedule_attestation_binds_product_venue_symbol_and_freshness():
    value = _attestation()
    valid_at = value.source_checked_at + MAX_FEE_SCHEDULE_ATTESTATION_AGE
    value.validate_for(
        product_id=value.product_id,
        asset_class=value.asset_class,
        venue=value.venue,
        symbol=value.symbol,
        at=valid_at,
    )

    cases = (
        ({"product_id": "other-product"}, "product mismatch"),
        ({"asset_class": "us_equity"}, "asset-class mismatch"),
        ({"venue": "other-venue"}, "venue mismatch"),
        ({"symbol": "ETH/USD"}, "symbol mismatch"),
    )
    common = {
        "product_id": value.product_id,
        "asset_class": value.asset_class,
        "venue": value.venue,
        "symbol": value.symbol,
        "at": valid_at,
    }
    for mutation, message in cases:
        with pytest.raises(FeeScheduleAttestationIntegrityError, match=message):
            value.validate_for(**{**common, **mutation})

    with pytest.raises(FeeScheduleAttestationIntegrityError, match="predate source verification"):
        value.validate_for(
            product_id=value.product_id,
            asset_class=value.asset_class,
            venue=value.venue,
            symbol=value.symbol,
            at=value.source_checked_at - timedelta(microseconds=1),
        )
    with pytest.raises(FeeScheduleAttestationIntegrityError, match="stale"):
        value.validate_for(
            product_id=value.product_id,
            asset_class=value.asset_class,
            venue=value.venue,
            symbol=value.symbol,
            at=value.source_checked_at + MAX_FEE_SCHEDULE_ATTESTATION_AGE + timedelta(microseconds=1),
        )


def test_w82_fee_schedule_attestation_hash_is_reproducible():
    first = _attestation()
    second = _attestation()
    assert first.attestation_hash == second.attestation_hash
    assert first.to_dict()["attestation_hash"] == first.attestation_hash
