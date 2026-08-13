from __future__ import annotations

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


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
SOURCE_HASH = "a" * 64


def _crypto_kwargs() -> dict[str, object]:
    return {
        "asset_class": AssetClass.CRYPTO,
        "venue": "ALPACA_PAPER_CRYPTO",
        "market_hours_model": MarketHoursModel.CONTINUOUS_24_7,
        "allowed_order_types": frozenset(
            {BrokerOrderType.MARKET, BrokerOrderType.LIMIT, BrokerOrderType.STOP_LIMIT}
        ),
        "allowed_time_in_force": frozenset({TimeInForce.GTC, TimeInForce.IOC}),
        "fractionable": True,
        "marginable": False,
        "shortable": False,
        "protection_model": ProtectionModel.CRYPTO_STOP_LIMIT,
        "source": "ALPACA_PAPER_ASSET_ATTESTATION",
        "source_fingerprint": SOURCE_HASH,
        "observed_at": NOW,
    }


def _equity_kwargs() -> dict[str, object]:
    return {
        "asset_class": AssetClass.US_EQUITY,
        "venue": "ALPACA_PAPER_US_EQUITY",
        "market_hours_model": MarketHoursModel.SESSION_CLOCKED,
        "allowed_order_types": frozenset({BrokerOrderType.MARKET, BrokerOrderType.LIMIT}),
        "allowed_time_in_force": frozenset({TimeInForce.DAY, TimeInForce.GTC}),
        "fractionable": False,
        "marginable": False,
        "shortable": False,
        "protection_model": ProtectionModel.EQUITY_BRACKET,
        "source": "ALPACA_PAPER_ASSET_ATTESTATION",
        "source_fingerprint": SOURCE_HASH,
        "observed_at": NOW,
    }


def _assert_crypto_rejected(*, key: str, value: object, message: str) -> None:
    kwargs = _crypto_kwargs()
    kwargs[key] = value
    with pytest.raises(ProductCapabilityError, match=message):
        ProductCapabilities(**kwargs)  # type: ignore[arg-type]


def test_product_capabilities_requires_non_empty_venue() -> None:
    _assert_crypto_rejected(key="venue", value="   ", message="venue is required")


def test_product_capabilities_requires_non_empty_source() -> None:
    _assert_crypto_rejected(key="source", value="   ", message="source is required")


def test_product_capabilities_requires_at_least_one_order_type() -> None:
    _assert_crypto_rejected(
        key="allowed_order_types",
        value=frozenset(),
        message="allowed_order_types may not be empty",
    )


def test_product_capabilities_requires_at_least_one_time_in_force() -> None:
    _assert_crypto_rejected(
        key="allowed_time_in_force",
        value=frozenset(),
        message="allowed_time_in_force may not be empty",
    )


def test_crypto_product_capabilities_must_be_fractionable() -> None:
    _assert_crypto_rejected(
        key="fractionable",
        value=False,
        message="crypto profile must be fractionable",
    )


def test_crypto_product_capabilities_rejects_equity_only_order_type() -> None:
    _assert_crypto_rejected(
        key="allowed_order_types",
        value=frozenset({BrokerOrderType.MARKET, BrokerOrderType.STOP}),
        message="crypto profile contains unsupported order type",
    )


def test_crypto_product_capabilities_rejects_session_only_tif() -> None:
    _assert_crypto_rejected(
        key="allowed_time_in_force",
        value=frozenset({TimeInForce.GTC, TimeInForce.DAY}),
        message="crypto profile contains unsupported time-in-force",
    )


def test_equity_product_capabilities_rejects_crypto_247_market_model() -> None:
    kwargs = _equity_kwargs()
    kwargs["market_hours_model"] = MarketHoursModel.CONTINUOUS_24_7
    with pytest.raises(ProductCapabilityError, match="US equity must use SESSION_CLOCKED"):
        ProductCapabilities(**kwargs)  # type: ignore[arg-type]


def test_equity_product_capabilities_rejects_crypto_protection_model() -> None:
    kwargs = _equity_kwargs()
    kwargs["protection_model"] = ProtectionModel.CRYPTO_STOP_LIMIT
    with pytest.raises(ProductCapabilityError, match="requires the certified bracket model"):
        ProductCapabilities(**kwargs)  # type: ignore[arg-type]
