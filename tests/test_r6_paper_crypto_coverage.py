from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from autotrade.brokers.alpaca_paper_crypto_asset import (
    CRYPTO_ASSET_PATH,
    AlpacaPaperCryptoAssetGateway,
    PaperCryptoAssetDisabled,
    PaperCryptoAssetIntegrityError,
    PaperCryptoAssetReadPolicy,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    LATEST_QUOTE_PATH,
    LATEST_TRADE_PATH,
    AlpacaPaperCryptoMarketDataConfig,
    AlpacaPaperCryptoMarketDataDisabled,
    AlpacaPaperCryptoMarketDataGateway,
    AlpacaPaperCryptoMarketDataIntegrityError,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperPolicyError,
    AlpacaPaperReadRequest,
)
from autotrade.brokers.alpaca_paper_market_data import AlpacaPaperMarketDataHttpResponse


NOW = datetime(2026, 8, 13, 2, 0, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")
HASH = "a" * 64


class NeverRead:
    def read(self, request):
        raise AssertionError("transport must not be called")


def test_crypto_asset_disabled_and_policy_fail_closed() -> None:
    disabled = AlpacaPaperCryptoAssetGateway(transport=NeverRead())
    with pytest.raises(PaperCryptoAssetDisabled):
        disabled.attest_asset(
            credentials=CREDS,
            account_attestation_fingerprint=HASH,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )
    policy = PaperCryptoAssetReadPolicy()
    headers = {
        "Accept": "application/json",
        "User-Agent": "AUTO-TRADE-R6/0.28R",
        "APCA-API-KEY-ID": "paper-key",
        "APCA-API-SECRET-KEY": "paper-secret",
    }
    url = f"https://{ALPACA_PAPER_TRADING_HOST}{CRYPTO_ASSET_PATH}"
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate(AlpacaPaperReadRequest("POST", url, 5, headers))
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate(AlpacaPaperReadRequest("GET", url, 0, headers))
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate(AlpacaPaperReadRequest("GET", url + "?x=1", 5, headers))
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate_final_url(url + "?redirect=1")


class BadAssetTransport:
    def __init__(self, *, body: bytes, content_type: str = "application/json", request_id: str = "req-1"):
        self.body = body
        self.content_type = content_type
        self.request_id = request_id

    def read(self, request):
        return AlpacaPaperHttpResponse(
            status_code=200,
            body=self.body,
            final_url=request.url,
            headers={"content-type": self.content_type, "x-request-id": self.request_id},
        )


def test_crypto_asset_rejects_non_json_missing_request_id_and_non_object() -> None:
    base = dict(
        credentials=CREDS,
        account_attestation_fingerprint=HASH,
        expected_credential_reference=CREDS.credential_reference,
        now=NOW,
    )
    for transport in (
        BadAssetTransport(body=b"{}", content_type="text/plain"),
        BadAssetTransport(body=b"{}", request_id=""),
        BadAssetTransport(body=b"not-json"),
        BadAssetTransport(body=b"[]"),
    ):
        with pytest.raises(PaperCryptoAssetIntegrityError):
            AlpacaPaperCryptoAssetGateway(
                config=AlpacaPaperGatewayConfig(enabled=True), transport=transport
            ).attest_asset(**base)


def test_crypto_asset_rejects_bad_fingerprint_credential_binding_and_naive_time() -> None:
    gateway = AlpacaPaperCryptoAssetGateway(
        config=AlpacaPaperGatewayConfig(enabled=True), transport=NeverRead()
    )
    with pytest.raises(ValueError, match="fingerprint"):
        gateway.attest_asset(
            credentials=CREDS,
            account_attestation_fingerprint="bad",
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )
    with pytest.raises(PaperCryptoAssetIntegrityError, match="credentials"):
        gateway.attest_asset(
            credentials=CREDS,
            account_attestation_fingerprint=HASH,
            expected_credential_reference="b" * 64,
            now=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        gateway.attest_asset(
            credentials=CREDS,
            account_attestation_fingerprint=HASH,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW.replace(tzinfo=None),
        )


def test_crypto_market_config_and_disabled_gateway_fail_closed() -> None:
    for kwargs in (
        {"timeout_seconds": 0},
        {"max_response_bytes": 0},
        {"fresh_activity_age_seconds": 0},
        {"fresh_activity_age_seconds": 121},
        {"fresh_activity_age_seconds": 61, "max_reference_age_seconds": 60},
        {"max_reference_age_seconds": 901},
        {"max_spread_bps": -1},
        {"max_spread_bps": 1001},
        {"max_trade_mid_deviation_bps": -1},
        {"max_trade_mid_deviation_bps": 1001},
        {"future_tolerance_seconds": 6},
    ):
        with pytest.raises(ValueError):
            AlpacaPaperCryptoMarketDataConfig(**kwargs)
    with pytest.raises(AlpacaPaperCryptoMarketDataDisabled):
        AlpacaPaperCryptoMarketDataGateway(transport=NeverRead()).attest_snapshot(
            credentials=CREDS, now=NOW
        )


class MarketShapeTransport:
    def __init__(self, quotes: object, trades: object):
        self.quotes = quotes
        self.trades = trades

    def read(self, request):
        if LATEST_QUOTE_PATH in request.url:
            body = json.dumps({"quotes": self.quotes}).encode()
        elif LATEST_TRADE_PATH in request.url:
            body = json.dumps({"trades": self.trades}).encode()
        else:
            raise AssertionError(request.url)
        return AlpacaPaperMarketDataHttpResponse(
            status_code=200,
            body=body,
            final_url=request.url,
            headers={},
        )


def _quote(*, bid: object = "99999", ask: object = "100001", timestamp: str | None = None):
    return {
        "BTC/USD": {
            "bp": bid,
            "bs": "1",
            "ap": ask,
            "as": "1",
            "t": timestamp or NOW.isoformat().replace("+00:00", "Z"),
        }
    }


def _trades(*, price: object = "100000", timestamp: str | None = None):
    return {
        "BTC/USD": {
            "p": price,
            "s": "0.001",
            "t": timestamp or NOW.isoformat().replace("+00:00", "Z"),
        }
    }


@pytest.mark.parametrize(
    ("quotes", "trades"),
    [
        ([], _trades()),
        ({}, _trades()),
        ({"BTC/USD": {}}, _trades()),
        (_quote(bid="100002", ask="100001"), _trades()),
        (_quote(ask="0"), _trades()),
        (_quote(), []),
        (_quote(), {}),
        (_quote(), _trades(price="0")),
        (_quote(timestamp="not-time"), _trades()),
    ],
)
def test_crypto_market_rejects_malformed_shapes(quotes: object, trades: object) -> None:
    gateway = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True), transport=MarketShapeTransport(quotes, trades)
    )
    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError):
        gateway.attest_snapshot(credentials=CREDS, now=NOW)


def test_crypto_market_rejects_future_and_naive_now() -> None:
    future_stamp = (NOW + timedelta(seconds=4)).isoformat().replace("+00:00", "Z")
    gateway = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True),
        transport=MarketShapeTransport(
            _quote(timestamp=future_stamp),
            _trades(timestamp=future_stamp),
        ),
    )
    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="future"):
        gateway.attest_snapshot(credentials=CREDS, now=NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        gateway.attest_snapshot(credentials=CREDS, now=NOW.replace(tzinfo=None))


def test_crypto_market_rejects_wide_spread_and_trade_quote_dislocation() -> None:
    wide = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True, max_spread_bps=50),
        transport=MarketShapeTransport(_quote(bid="99000", ask="101000"), _trades(price="100000")),
    )
    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="spread exceeds"):
        wide.attest_snapshot(credentials=CREDS, now=NOW)

    dislocated = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True, max_trade_mid_deviation_bps=50),
        transport=MarketShapeTransport(_quote(), _trades(price="99000")),
    )
    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="deviates from quote midpoint"):
        dislocated.attest_snapshot(credentials=CREDS, now=NOW)
