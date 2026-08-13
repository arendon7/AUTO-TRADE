from datetime import datetime, timezone

import pytest

from autotrade.product_profile import (
    AssetClass,
    BrokerOrderType,
    MarketHoursModel,
    ProductCapabilities,
    ProductCapabilityError,
    ProtectionModel,
    TimeInForce,
)


NOW = datetime(2026, 8, 13, 2, 30, tzinfo=timezone.utc)
SOURCE = "a" * 64


def _crypto() -> ProductCapabilities:
    return ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=SOURCE,
        observed_at=NOW,
        fractionable=True,
        marginable=False,
        shortable=False,
    )


def test_crypto_profile_is_explicit_24_7_and_not_equity_bracket() -> None:
    profile = _crypto()
    assert profile.asset_class is AssetClass.CRYPTO
    assert profile.market_hours_model is MarketHoursModel.CONTINUOUS_24_7
    assert profile.protection_model is ProtectionModel.CRYPTO_STOP_LIMIT
    assert profile.fractionable is True
    assert profile.marginable is False
    assert profile.shortable is False
    assert len(profile.fingerprint) == 64


def test_crypto_profile_allows_only_documented_order_types_and_tif() -> None:
    profile = _crypto()
    for order_type in (BrokerOrderType.MARKET, BrokerOrderType.LIMIT, BrokerOrderType.STOP_LIMIT):
        for tif in (TimeInForce.GTC, TimeInForce.IOC):
            profile.require_order(order_type=order_type, time_in_force=tif)
    with pytest.raises(ProductCapabilityError, match="order type"):
        profile.require_order(order_type=BrokerOrderType.STOP, time_in_force=TimeInForce.GTC)
    with pytest.raises(ProductCapabilityError, match="time-in-force"):
        profile.require_order(order_type=BrokerOrderType.LIMIT, time_in_force=TimeInForce.DAY)


def test_crypto_profile_rejects_margin_short_and_equity_protection() -> None:
    profile = _crypto()
    with pytest.raises(ProductCapabilityError, match="opening short"):
        profile.require_opening_short(opening_short=True)
    with pytest.raises(ProductCapabilityError, match="margin"):
        profile.require_margin(uses_margin=True)
    with pytest.raises(ProductCapabilityError, match="bracket"):
        ProductCapabilities(
            asset_class=AssetClass.CRYPTO,
            venue="ALPACA_PAPER_CRYPTO",
            market_hours_model=MarketHoursModel.CONTINUOUS_24_7,
            allowed_order_types=frozenset({BrokerOrderType.LIMIT}),
            allowed_time_in_force=frozenset({TimeInForce.GTC}),
            fractionable=True,
            marginable=False,
            shortable=False,
            protection_model=ProtectionModel.EQUITY_BRACKET,
            source="test",
            source_fingerprint=SOURCE,
            observed_at=NOW,
        )


def test_crypto_profile_rejects_capability_overclaim() -> None:
    with pytest.raises(ProductCapabilityError, match="margin or short"):
        ProductCapabilities.crypto_alpaca_paper(
            source_fingerprint=SOURCE,
            observed_at=NOW,
            fractionable=True,
            marginable=True,
            shortable=False,
        )
    with pytest.raises(ProductCapabilityError, match="fractionable"):
        ProductCapabilities.crypto_alpaca_paper(
            source_fingerprint=SOURCE,
            observed_at=NOW,
            fractionable=False,
            marginable=False,
            shortable=False,
        )


def test_us_equity_profile_keeps_session_clock_and_bracket_contract() -> None:
    profile = ProductCapabilities.us_equity_alpaca_paper(
        source_fingerprint=SOURCE,
        observed_at=NOW,
        fractionable=True,
        marginable=True,
        shortable=True,
    )
    assert profile.asset_class is AssetClass.US_EQUITY
    assert profile.market_hours_model is MarketHoursModel.SESSION_CLOCKED
    assert profile.protection_model is ProtectionModel.EQUITY_BRACKET
    profile.require_order(order_type=BrokerOrderType.LIMIT, time_in_force=TimeInForce.DAY)


def test_profile_rejects_untrusted_provenance_and_naive_time() -> None:
    with pytest.raises(ProductCapabilityError, match="sha256"):
        ProductCapabilities.crypto_alpaca_paper(
            source_fingerprint="bad",
            observed_at=NOW,
            fractionable=True,
            marginable=False,
            shortable=False,
        )
    with pytest.raises(ProductCapabilityError, match="timezone-aware"):
        ProductCapabilities.crypto_alpaca_paper(
            source_fingerprint=SOURCE,
            observed_at=NOW.replace(tzinfo=None),
            fractionable=True,
            marginable=False,
            shortable=False,
        )


def test_profile_fingerprint_binds_broker_evidence() -> None:
    first = _crypto()
    second = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint="b" * 64,
        observed_at=NOW,
        fractionable=True,
        marginable=False,
        shortable=False,
    )
    assert first.fingerprint != second.fingerprint
