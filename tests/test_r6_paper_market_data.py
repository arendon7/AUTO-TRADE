from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_market_data import (
    ALPACA_MARKET_DATA_HOST,
    AlpacaPaperEquityMarketDataGateway,
    AlpacaPaperMarketDataConfig,
    AlpacaPaperMarketDataDisabled,
    AlpacaPaperMarketDataHttpResponse,
    AlpacaPaperMarketDataIntegrityError,
    AlpacaPaperMarketDataPolicyError,
)
from autotrade.domain import market_fingerprint


NOW = datetime(2026, 8, 11, 19, 30, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials(key_id="paper-key-id", secret_key="paper-secret-key")


def body(
    *,
    symbol: str = "AAPL",
    bid: float = 189.10,
    ask: float = 189.12,
    last: float = 189.11,
    quote_time: datetime | None = None,
    trade_time: datetime | None = None,
) -> bytes:
    quote_time = quote_time or (NOW - timedelta(milliseconds=500))
    trade_time = trade_time or (NOW - timedelta(seconds=1))
    return json.dumps(
        {
            "symbol": symbol,
            "latestQuote": {
                "bp": bid,
                "ap": ask,
                "t": quote_time.isoformat().replace("+00:00", "Z"),
            },
            "latestTrade": {
                "p": last,
                "t": trade_time.isoformat().replace("+00:00", "Z"),
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


class FakeTransport:
    def __init__(self, response: AlpacaPaperMarketDataHttpResponse) -> None:
        self.response = response
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        return self.response


def response(payload: bytes | None = None, *, final_url: str | None = None, status: int = 200):
    url = (
        final_url
        or "https://data.alpaca.markets/v2/stocks/AAPL/snapshot?feed=iex&currency=USD"
    )
    return AlpacaPaperMarketDataHttpResponse(
        status_code=status,
        body=payload if payload is not None else body(),
        final_url=url,
        headers={},
    )


def gateway(fake: FakeTransport, **overrides):
    config = AlpacaPaperMarketDataConfig(enabled=True, **overrides)
    return AlpacaPaperEquityMarketDataGateway(config, transport=fake)


def test_market_data_gateway_is_disabled_by_default() -> None:
    fake = FakeTransport(response())
    subject = AlpacaPaperEquityMarketDataGateway(transport=fake)
    with pytest.raises(AlpacaPaperMarketDataDisabled):
        subject.attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)
    assert fake.requests == []


def test_market_data_gateway_returns_conservative_market_snapshot_and_exact_request() -> None:
    fake = FakeTransport(response())
    attestation = gateway(fake).attest_snapshot(
        credentials=CREDS,
        symbol="AAPL",
        now=NOW,
    )

    assert attestation.market.symbol == "AAPL"
    assert str(attestation.market.bid) == "189.1"
    assert str(attestation.market.ask) == "189.12"
    assert str(attestation.market.last) == "189.11"
    assert attestation.market.observed_at == NOW - timedelta(seconds=1)
    assert attestation.feed == "iex"
    assert attestation.currency == "USD"
    assert attestation.source_host == ALPACA_MARKET_DATA_HOST
    assert len(attestation.response_sha256) == 64
    assert len(attestation.fingerprint) == 64
    assert market_fingerprint(attestation.market) in attestation.fingerprint or len(attestation.fingerprint) == 64

    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.method == "GET"
    assert request.url == (
        "https://data.alpaca.markets/v2/stocks/AAPL/snapshot?feed=iex&currency=USD"
    )
    assert request.headers["APCA-API-KEY-ID"] == CREDS.key_id
    assert request.headers["APCA-API-SECRET-KEY"] == CREDS.secret_key
    assert "paper-secret-key" not in repr(request)


def test_market_data_configuration_is_pinned_to_exact_host_iex_usd() -> None:
    with pytest.raises(ValueError, match="exact Alpaca data host"):
        AlpacaPaperMarketDataConfig(enabled=True, base_url="https://example.com")
    with pytest.raises(ValueError, match="pinned to IEX"):
        AlpacaPaperMarketDataConfig(enabled=True, feed="sip")
    with pytest.raises(ValueError, match="currency must be USD"):
        AlpacaPaperMarketDataConfig(enabled=True, currency="EUR")


def test_market_data_symbol_and_final_url_are_fail_closed() -> None:
    fake = FakeTransport(response())
    subject = gateway(fake)
    for invalid in ("aapl", " AAPL", "AAPL ", "AAPL?x=1", ""):
        with pytest.raises((TypeError, ValueError)):
            subject.attest_snapshot(credentials=CREDS, symbol=invalid, now=NOW)
    assert fake.requests == []

    redirected = FakeTransport(response(final_url="https://data.alpaca.markets/v2/stocks/MSFT/snapshot?feed=iex&currency=USD"))
    with pytest.raises(AlpacaPaperMarketDataPolicyError, match="final URL changed"):
        gateway(redirected).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)


def test_market_data_rejects_stale_future_or_skewed_components() -> None:
    stale = FakeTransport(
        response(body(quote_time=NOW - timedelta(seconds=31), trade_time=NOW - timedelta(seconds=1)))
    )
    with pytest.raises(AlpacaPaperMarketDataIntegrityError, match="latestQuote is stale"):
        gateway(stale).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)

    future = FakeTransport(
        response(body(quote_time=NOW + timedelta(seconds=3), trade_time=NOW - timedelta(seconds=1)))
    )
    with pytest.raises(AlpacaPaperMarketDataIntegrityError, match="timestamp is in the future"):
        gateway(future).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)

    skewed = FakeTransport(
        response(body(quote_time=NOW, trade_time=NOW - timedelta(seconds=10)))
    )
    with pytest.raises(AlpacaPaperMarketDataIntegrityError, match="skew exceeds policy"):
        gateway(skewed, max_quote_trade_skew_seconds=5).attest_snapshot(
            credentials=CREDS, symbol="AAPL", now=NOW
        )


def test_market_data_rejects_bad_prices_symbol_and_shape() -> None:
    cases = (
        (body(bid=190.0, ask=189.0), "bid exceeds ask"),
        (body(bid=0.0), "positive decimal"),
        (body(symbol="MSFT"), "symbol mismatch"),
        (json.dumps({"symbol": "AAPL", "latestQuote": {}}).encode(), "requires latestQuote and latestTrade"),
        (b"not-json", "invalid JSON"),
    )
    for payload, message in cases:
        fake = FakeTransport(response(payload))
        with pytest.raises(AlpacaPaperMarketDataIntegrityError, match=message):
            gateway(fake).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)


def test_market_data_rejects_non_200_and_oversized_body() -> None:
    non_200 = FakeTransport(response(status=429))
    with pytest.raises(Exception, match="HTTP 429"):
        gateway(non_200).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)

    oversized_payload = b"x" * 2049
    oversized = FakeTransport(response(oversized_payload))
    with pytest.raises(AlpacaPaperMarketDataIntegrityError, match="body exceeds limit"):
        gateway(oversized, max_response_bytes=2048).attest_snapshot(
            credentials=CREDS, symbol="AAPL", now=NOW
        )


def test_market_attestation_requires_oldest_component_timestamp() -> None:
    fake = FakeTransport(response())
    attestation = gateway(fake).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)
    with pytest.raises(ValueError, match="oldest component"):
        replace(
            attestation,
            market=replace(attestation.market, observed_at=attestation.quote_observed_at),
        )
