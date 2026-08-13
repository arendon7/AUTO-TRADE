from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_asset import (
    AlpacaPaperEquityAssetGateway,
    PaperAssetIntegrityError,
    PaperAssetReadPolicy,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperPolicyError,
    AlpacaPaperReadRequest,
)


NOW = datetime(2026, 8, 12, 3, 0, tzinfo=timezone.utc)


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")


def asset_payload(**overrides):
    payload = {
        "id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
        "class": "us_equity",
        "exchange": "NASDAQ",
        "symbol": "AAPL",
        "name": "Apple Inc. Common Stock",
        "status": "active",
        "tradable": True,
        "marginable": True,
        "shortable": True,
        "easy_to_borrow": True,
        "fractionable": True,
        "min_order_size": "0.000001",
        "min_trade_increment": "0.000001",
        "price_increment": "0.01",
        "attributes": ["has_options"],
    }
    payload.update(overrides)
    return payload


class FakeTransport:
    def __init__(self, payload=None, *, final_url=None, status_code=200):
        self.payload = payload if payload is not None else asset_payload()
        self.final_url = final_url
        self.status_code = status_code
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        return AlpacaPaperHttpResponse(
            status_code=self.status_code,
            body=json.dumps(self.payload, sort_keys=True).encode("utf-8"),
            final_url=self.final_url or request.url,
            headers={"content-type": "application/json", "x-request-id": "req-asset-001"},
        )


def gateway(transport):
    return AlpacaPaperEquityAssetGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        transport=transport,
    )


def attest(transport, **overrides):
    creds = credentials()
    values = {
        "credentials": creds,
        "symbol": "AAPL",
        "account_attestation_fingerprint": "a" * 64,
        "expected_credential_reference": creds.credential_reference,
        "now": NOW,
    }
    values.update(overrides)
    return gateway(transport).attest_asset(**values)


def test_asset_preflight_happy_path_is_exact_one_get_and_whole_share_safe() -> None:
    transport = FakeTransport()
    attestation = attest(transport)

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == "https://paper-api.alpaca.markets/v2/assets/AAPL"
    assert attestation.symbol == "AAPL"
    assert attestation.asset_class == "us_equity"
    assert attestation.status == "active"
    assert attestation.tradable is True
    assert attestation.min_order_size < 1
    assert attestation.min_trade_increment < 1
    assert attestation.price_increment == attestation.price_increment.copy_abs()
    assert attestation.constraint_source == "ALPACA_ASSET"
    assert attestation.attributes == ("has_options",)
    assert attestation.source_host == "paper-api.alpaca.markets"
    assert attestation.source_path == "/v2/assets/AAPL"


def test_equity_asset_null_precision_fields_narrow_to_r6_whole_share_policy() -> None:
    attestation = attest(
        FakeTransport(
            asset_payload(
                min_order_size=None,
                min_trade_increment=None,
                price_increment=None,
            )
        )
    )
    assert attestation.min_order_size == Decimal("1")
    assert attestation.min_trade_increment == Decimal("1")
    assert attestation.price_increment == Decimal("0.01")
    assert (
        attestation.constraint_source
        == "ALPACA_ASSET_PLUS_R6_US_EQUITY_WHOLE_SHARE_POLICY"
    )
    assert attestation.to_dict()["whole_share_canary_supported"] is True


def test_equity_asset_partial_null_precision_preserves_broker_values_and_narrows_only_null() -> None:
    attestation = attest(
        FakeTransport(
            asset_payload(
                min_order_size=None,
                min_trade_increment="0.5",
                price_increment="0.005",
            )
        )
    )
    assert attestation.min_order_size == Decimal("1")
    assert attestation.min_trade_increment == Decimal("0.5")
    assert attestation.price_increment == Decimal("0.005")
    assert (
        attestation.constraint_source
        == "ALPACA_ASSET_PLUS_R6_US_EQUITY_WHOLE_SHARE_POLICY"
    )


def test_asset_policy_rejects_live_post_query_and_noncanonical_symbol() -> None:
    policy = PaperAssetReadPolicy()
    headers = {
        "Accept": "application/json",
        "User-Agent": "AUTO-TRADE-R6/0.28R",
        "APCA-API-KEY-ID": "key",
        "APCA-API-SECRET-KEY": "secret",
    }
    policy.validate(
        AlpacaPaperReadRequest(
            method="GET",
            url="https://paper-api.alpaca.markets/v2/assets/AAPL",
            timeout_seconds=5,
            headers=headers,
        )
    )
    bad = (
        ("POST", "https://paper-api.alpaca.markets/v2/assets/AAPL"),
        ("GET", "https://api.alpaca.markets/v2/assets/AAPL"),
        ("GET", "https://paper-api.alpaca.markets/v2/assets/AAPL?x=1"),
        ("GET", "https://paper-api.alpaca.markets/v2/assets/BTC%2FUSD"),
    )
    for method, url in bad:
        with pytest.raises(AlpacaPaperPolicyError):
            policy.validate(
                AlpacaPaperReadRequest(
                    method=method,
                    url=url,
                    timeout_seconds=5,
                    headers=headers,
                )
            )


def test_asset_gateway_rejects_credentials_not_bound_to_account_before_network() -> None:
    transport = FakeTransport()
    creds = credentials()
    with pytest.raises(PaperAssetIntegrityError, match="credentials do not match"):
        gateway(transport).attest_asset(
            credentials=creds,
            symbol="AAPL",
            account_attestation_fingerprint="a" * 64,
            expected_credential_reference="b" * 64,
            now=NOW,
        )
    assert transport.requests == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"class": "crypto"}, "us_equity"),
        ({"class": "global_equity"}, "us_equity"),
        ({"exchange": "OTC"}, "exchange"),
        ({"status": "inactive"}, "active"),
        ({"tradable": False}, "tradable"),
        ({"attributes": ["ipo"]}, "attributes"),
        ({"attributes": ["ptp_no_exception"]}, "attributes"),
        ({"attributes": ["ptp_with_exception"]}, "attributes"),
        ({"min_order_size": "2"}, "whole share"),
        ({"min_trade_increment": "0.3"}, "align"),
        ({"price_increment": "0"}, "positive"),
        ({"min_order_size": 1}, "decimal string or null"),
    ],
)
def test_asset_gateway_fails_closed_on_unsupported_or_unsafe_asset(overrides, message) -> None:
    with pytest.raises(PaperAssetIntegrityError, match=message):
        attest(FakeTransport(asset_payload(**overrides)))


@pytest.mark.parametrize("missing", ["min_order_size", "min_trade_increment", "price_increment"])
def test_asset_gateway_requires_precision_field_presence_even_when_null(missing) -> None:
    payload = asset_payload()
    payload.pop(missing)
    with pytest.raises(PaperAssetIntegrityError, match=missing):
        attest(FakeTransport(payload))


def test_asset_gateway_rejects_redirect_or_final_host_drift() -> None:
    transport = FakeTransport(final_url="https://api.alpaca.markets/v2/assets/AAPL")
    with pytest.raises(AlpacaPaperPolicyError):
        attest(transport)
