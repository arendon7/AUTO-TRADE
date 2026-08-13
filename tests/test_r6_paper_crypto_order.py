from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from autotrade.brokers.alpaca_paper_crypto_order import (
    CryptoOrderContractError,
    CryptoOrderRole,
    build_crypto_entry_order,
    build_crypto_long_protection_order,
    deterministic_crypto_client_order_id,
)
from autotrade.product_profile import BrokerOrderType, ProductCapabilities, TimeInForce


NOW = datetime(2026, 8, 13, 3, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64


def asset(symbol: str = "BTC/USD") -> AlpacaPaperCryptoAssetAttestation:
    return AlpacaPaperCryptoAssetAttestation(
        symbol=symbol,
        asset_id="276e2673-764b-4ab6-a611-caf665ca6340",
        asset_class="crypto",
        exchange="CRYPTO",
        status="active",
        tradable=True,
        fractionable=True,
        marginable=False,
        shortable=False,
        min_order_size=Decimal("0.0001"),
        min_trade_increment=Decimal("0.0001"),
        price_increment=Decimal("1"),
        account_attestation_fingerprint=HASH_A,
        credential_reference=HASH_B,
        observed_at=NOW,
        request_id="req-crypto",
        response_sha256="c" * 64,
        source_path="/v2/assets/" + symbol.replace("/", "%2F"),
    )


def profile(value: AlpacaPaperCryptoAssetAttestation) -> ProductCapabilities:
    return ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=value.fingerprint,
        observed_at=value.observed_at,
        fractionable=value.fractionable,
        marginable=value.marginable,
        shortable=value.shortable,
    )


def test_crypto_limit_entry_is_profile_bound_and_rounds_without_increasing_quantity() -> None:
    a = asset()
    p = profile(a)
    order = build_crypto_entry_order(
        symbol="btc/usd",
        quantity=Decimal("0.00129"),
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        client_order_id="entry-1",
        product_profile=p,
        asset_attestation=a,
        limit_price=Decimal("99999.10"),
    )
    assert order.quantity == Decimal("0.0012")
    assert order.limit_price == Decimal("100000")
    assert order.product_profile_fingerprint == p.fingerprint
    assert order.asset_attestation_fingerprint == a.fingerprint
    assert order.to_payload() == {
        "symbol": "BTC/USD",
        "qty": "0.0012",
        "side": "buy",
        "type": "limit",
        "time_in_force": "gtc",
        "client_order_id": "entry-1",
        "limit_price": "100000",
    }
    assert "order_class" not in order.to_payload()
    assert len(order.payload_hash) == 64
    assert len(order.fingerprint) == 64


def test_crypto_market_entry_contains_no_limit_or_stop_price() -> None:
    a = asset()
    p = profile(a)
    order = build_crypto_entry_order(
        symbol="BTC/USD",
        quantity=Decimal("0.0001"),
        order_type=BrokerOrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        client_order_id="entry-market-1",
        product_profile=p,
        asset_attestation=a,
    )
    payload = order.to_payload()
    assert payload["type"] == "market"
    assert "limit_price" not in payload
    assert "stop_price" not in payload


def test_entry_rejects_wrong_profile_or_unsupported_order_semantics() -> None:
    a = asset()
    wrong_asset = asset("ETH/USD")
    wrong_profile = profile(wrong_asset)
    with pytest.raises(CryptoOrderContractError, match="exact asset attestation"):
        build_crypto_entry_order(
            symbol="BTC/USD",
            quantity=Decimal("0.001"),
            order_type=BrokerOrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            client_order_id="entry-1",
            product_profile=wrong_profile,
            asset_attestation=a,
            limit_price=Decimal("100000"),
        )

    equity = ProductCapabilities.us_equity_alpaca_paper(
        source_fingerprint=a.fingerprint,
        observed_at=NOW,
        fractionable=True,
        marginable=True,
        shortable=True,
    )
    with pytest.raises(CryptoOrderContractError, match="CRYPTO ProductCapabilities"):
        build_crypto_entry_order(
            symbol="BTC/USD",
            quantity=Decimal("0.001"),
            order_type=BrokerOrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            client_order_id="entry-2",
            product_profile=equity,
            asset_attestation=a,
            limit_price=Decimal("100000"),
        )

    with pytest.raises(Exception):
        build_crypto_entry_order(
            symbol="BTC/USD",
            quantity=Decimal("0.001"),
            order_type=BrokerOrderType.STOP_LIMIT,
            time_in_force=TimeInForce.GTC,
            client_order_id="entry-3",
            product_profile=profile(a),
            asset_attestation=a,
            limit_price=Decimal("100000"),
        )


def test_crypto_protection_is_sell_stop_limit_and_never_exceeds_confirmed_position() -> None:
    a = asset()
    p = profile(a)
    order = build_crypto_long_protection_order(
        symbol="BTC/USD",
        confirmed_entry_filled_quantity=Decimal("0.0015"),
        confirmed_net_long_quantity=Decimal("0.0013"),
        requested_protection_quantity=Decimal("0.00129"),
        stop_price=Decimal("95000.9"),
        limit_price=Decimal("94500.9"),
        client_order_id="protect-1",
        product_profile=p,
        asset_attestation=a,
    )
    assert order.quantity == Decimal("0.0012")
    assert order.stop_price == Decimal("95000")
    assert order.limit_price == Decimal("94500")
    assert order.to_payload() == {
        "symbol": "BTC/USD",
        "qty": "0.0012",
        "side": "sell",
        "type": "stop_limit",
        "time_in_force": "gtc",
        "client_order_id": "protect-1",
        "limit_price": "94500",
        "stop_price": "95000",
    }

    for requested in (Decimal("0.0014"), Decimal("0.002")):
        with pytest.raises(CryptoOrderContractError, match="protection may not exceed"):
            build_crypto_long_protection_order(
                symbol="BTC/USD",
                confirmed_entry_filled_quantity=Decimal("0.0015"),
                confirmed_net_long_quantity=Decimal("0.0013"),
                requested_protection_quantity=requested,
                stop_price=Decimal("95000"),
                limit_price=Decimal("94500"),
                client_order_id="protect-too-large",
                product_profile=p,
                asset_attestation=a,
            )


def test_crypto_protection_rejects_unconfirmed_or_inverted_prices() -> None:
    a = asset()
    p = profile(a)
    with pytest.raises(CryptoOrderContractError, match="confirmed entry filled"):
        build_crypto_long_protection_order(
            symbol="BTC/USD",
            confirmed_entry_filled_quantity=Decimal("0"),
            confirmed_net_long_quantity=Decimal("0.001"),
            requested_protection_quantity=Decimal("0.001"),
            stop_price=Decimal("95000"),
            limit_price=Decimal("94500"),
            client_order_id="protect-0",
            product_profile=p,
            asset_attestation=a,
        )
    with pytest.raises(CryptoOrderContractError, match="limit <= stop"):
        build_crypto_long_protection_order(
            symbol="BTC/USD",
            confirmed_entry_filled_quantity=Decimal("0.001"),
            confirmed_net_long_quantity=Decimal("0.001"),
            requested_protection_quantity=Decimal("0.001"),
            stop_price=Decimal("95000"),
            limit_price=Decimal("95500"),
            client_order_id="protect-inverted",
            product_profile=p,
            asset_attestation=a,
        )


def test_deterministic_crypto_client_ids_are_role_separated_and_stable() -> None:
    entry_a = deterministic_crypto_client_order_id(lifecycle_id="life-123", role=CryptoOrderRole.ENTRY)
    entry_b = deterministic_crypto_client_order_id(lifecycle_id="life-123", role=CryptoOrderRole.ENTRY)
    protection = deterministic_crypto_client_order_id(lifecycle_id="life-123", role=CryptoOrderRole.PROTECTION)
    assert entry_a == entry_b
    assert entry_a != protection
    assert len(entry_a) <= 128
    assert len(protection) <= 128
