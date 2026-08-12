from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from urllib.error import HTTPError, URLError

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_market_data import (
    ALPACA_MARKET_DATA_HOST,
    AlpacaPaperEquityMarketAttestation,
    AlpacaPaperEquityMarketDataGateway,
    AlpacaPaperMarketDataConfig,
    AlpacaPaperMarketDataDisabled,
    AlpacaPaperMarketDataHttpResponse,
    AlpacaPaperMarketDataIntegrityError,
    AlpacaPaperMarketDataPolicyError,
    AlpacaPaperMarketDataRequest,
    AlpacaPaperMarketDataUnavailable,
    UrllibAlpacaPaperMarketDataTransport,
)
from autotrade.domain import MarketSnapshot, market_fingerprint


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
    assert len(market_fingerprint(attestation.market)) == 64

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


def test_market_data_configuration_rejects_unsafe_bounds() -> None:
    for kwargs, message in (
        ({"timeout_seconds": 0}, "timeout"),
        ({"timeout_seconds": 16}, "timeout"),
        ({"max_response_bytes": 0}, "max_response_bytes"),
        ({"max_response_bytes": 1_048_577}, "max_response_bytes"),
        ({"max_component_age_seconds": 0}, "max_component_age_seconds"),
        ({"max_component_age_seconds": 121}, "max_component_age_seconds"),
        ({"max_quote_trade_skew_seconds": -1}, "skew"),
        ({"max_quote_trade_skew_seconds": 121}, "skew"),
        ({"future_tolerance_seconds": -1}, "future_tolerance_seconds"),
        ({"future_tolerance_seconds": 6}, "future_tolerance_seconds"),
    ):
        with pytest.raises(ValueError, match=message):
            AlpacaPaperMarketDataConfig(enabled=True, **kwargs)

    with pytest.raises(ValueError, match="response limit"):
        UrllibAlpacaPaperMarketDataTransport(max_response_bytes=0)


def test_market_data_symbol_and_final_url_are_fail_closed() -> None:
    fake = FakeTransport(response())
    subject = gateway(fake)
    for invalid in ("aapl", " AAPL", "AAPL ", "AAPL?x=1", ""):
        with pytest.raises((TypeError, ValueError)):
            subject.attest_snapshot(credentials=CREDS, symbol=invalid, now=NOW)
    with pytest.raises(TypeError, match="symbol must be string"):
        subject.attest_snapshot(credentials=CREDS, symbol=123, now=NOW)  # type: ignore[arg-type]
    assert fake.requests == []

    redirected = FakeTransport(response(final_url="https://data.alpaca.markets/v2/stocks/MSFT/snapshot?feed=iex&currency=USD"))
    with pytest.raises(AlpacaPaperMarketDataPolicyError, match="final URL changed"):
        gateway(redirected).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)


def test_market_data_gateway_requires_credentials_and_aware_clock_before_network() -> None:
    fake = FakeTransport(response())
    subject = gateway(fake)
    with pytest.raises(TypeError, match="credentials"):
        subject.attest_snapshot(credentials=object(), symbol="AAPL", now=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        subject.attest_snapshot(
            credentials=CREDS,
            symbol="AAPL",
            now=datetime(2026, 8, 11, 19, 30),
        )
    assert fake.requests == []


def test_market_data_request_policy_rejects_every_noncanonical_surface() -> None:
    fake = FakeTransport(response())
    subject = gateway(fake)
    exact_url = "https://data.alpaca.markets/v2/stocks/AAPL/snapshot?feed=iex&currency=USD"
    headers = {
        "APCA-API-KEY-ID": CREDS.key_id,
        "APCA-API-SECRET-KEY": CREDS.secret_key,
        "Accept": "application/json",
    }

    cases = (
        (AlpacaPaperMarketDataRequest("POST", exact_url, 5, headers), "GET-only"),
        (AlpacaPaperMarketDataRequest("GET", exact_url, 0, headers), "timeout"),
        (AlpacaPaperMarketDataRequest("GET", "https://example.com/v2/stocks/AAPL/snapshot?feed=iex&currency=USD", 5, headers), "host"),
        (AlpacaPaperMarketDataRequest("GET", "https://data.alpaca.markets:444/v2/stocks/AAPL/snapshot?feed=iex&currency=USD", 5, headers), "path/port"),
        (AlpacaPaperMarketDataRequest("GET", "https://user:data@data.alpaca.markets/v2/stocks/AAPL/snapshot?feed=iex&currency=USD", 5, headers), "credentials/fragment"),
        (AlpacaPaperMarketDataRequest("GET", "https://data.alpaca.markets/v2/stocks/AAPL/snapshot?feed=sip&currency=USD", 5, headers), "IEX/USD"),
        (AlpacaPaperMarketDataRequest("GET", exact_url, 5, {**headers, "X-Extra": "1"}), "headers"),
        (AlpacaPaperMarketDataRequest("GET", exact_url, 5, {**headers, "Accept": "text/plain"}), "Accept"),
    )
    for request, message in cases:
        with pytest.raises(AlpacaPaperMarketDataPolicyError, match=message):
            subject._validate_request(request, symbol="AAPL")


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
        (json.dumps(["not", "object"]).encode(), "root must be object"),
        (b"not-json", "invalid JSON"),
        (b"\xff", "invalid JSON"),
    )
    for payload, message in cases:
        fake = FakeTransport(response(payload))
        with pytest.raises(AlpacaPaperMarketDataIntegrityError, match=message):
            gateway(fake).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)


def test_market_data_rejects_invalid_price_and_timestamp_encodings() -> None:
    raw = json.loads(body())
    for key, value, message in (
        (("latestQuote", "bp"), True, "positive decimal"),
        (("latestTrade", "p"), "NaN", "positive decimal"),
        (("latestQuote", "t"), None, "RFC3339"),
        (("latestTrade", "t"), "not-a-time", "RFC3339"),
    ):
        mutated = json.loads(body())
        mutated[key[0]][key[1]] = value
        fake = FakeTransport(response(json.dumps(mutated).encode()))
        with pytest.raises(AlpacaPaperMarketDataIntegrityError, match=message):
            gateway(fake).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)


def test_market_data_rejects_non_200_and_oversized_body() -> None:
    non_200 = FakeTransport(response(status=429))
    with pytest.raises(AlpacaPaperMarketDataUnavailable, match="HTTP 429"):
        gateway(non_200).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)

    oversized_payload = b"x" * 2049
    oversized = FakeTransport(response(oversized_payload))
    with pytest.raises(AlpacaPaperMarketDataIntegrityError, match="body exceeds limit"):
        gateway(oversized, max_response_bytes=2048).attest_snapshot(
            credentials=CREDS, symbol="AAPL", now=NOW
        )


def test_market_attestation_requires_canonical_metadata() -> None:
    market = MarketSnapshot(
        symbol="AAPL",
        bid=Decimal("189.10"),
        ask=Decimal("189.12"),
        last=Decimal("189.11"),
        observed_at=NOW - timedelta(seconds=1),
    )
    kwargs = dict(
        market=market,
        feed="iex",
        currency="USD",
        quote_observed_at=NOW - timedelta(milliseconds=500),
        trade_observed_at=NOW - timedelta(seconds=1),
        received_at=NOW,
        response_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="feed"):
        AlpacaPaperEquityMarketAttestation(**{**kwargs, "feed": "sip"})
    with pytest.raises(ValueError, match="currency"):
        AlpacaPaperEquityMarketAttestation(**{**kwargs, "currency": "EUR"})
    with pytest.raises(ValueError, match="source host"):
        AlpacaPaperEquityMarketAttestation(**{**kwargs, "source_host": "example.com"})
    with pytest.raises(ValueError, match="response_sha256"):
        AlpacaPaperEquityMarketAttestation(**{**kwargs, "response_sha256": "bad"})
    with pytest.raises(ValueError, match="timezone-aware"):
        AlpacaPaperEquityMarketAttestation(
            **{**kwargs, "received_at": datetime(2026, 8, 11, 19, 30)}
        )


def test_market_attestation_requires_oldest_component_timestamp() -> None:
    fake = FakeTransport(response())
    attestation = gateway(fake).attest_snapshot(credentials=CREDS, symbol="AAPL", now=NOW)
    with pytest.raises(ValueError, match="oldest component"):
        replace(
            attestation,
            market=replace(attestation.market, observed_at=attestation.quote_observed_at),
        )


def test_urllib_transport_is_bounded_and_normalizes_network_failures() -> None:
    class WireResponse:
        status = 200
        headers = {"X-Test": "yes"}

        def __init__(self, payload: bytes) -> None:
            self.payload = payload
            self.closed = False

        def read(self, size: int) -> bytes:
            return self.payload

        def geturl(self) -> str:
            return "https://data.alpaca.markets/v2/stocks/AAPL/snapshot?feed=iex&currency=USD"

        def close(self) -> None:
            self.closed = True

    class Opener:
        def __init__(self, value) -> None:
            self.value = value

        def open(self, wire, timeout):
            if isinstance(self.value, BaseException):
                raise self.value
            return self.value

    request = AlpacaPaperMarketDataRequest(
        method="GET",
        url="https://data.alpaca.markets/v2/stocks/AAPL/snapshot?feed=iex&currency=USD",
        timeout_seconds=1,
        headers={"Accept": "application/json"},
    )

    wire_response = WireResponse(b"{}")
    transport = UrllibAlpacaPaperMarketDataTransport(max_response_bytes=16)
    transport._opener = Opener(wire_response)
    result = transport.read(request)
    assert result.status_code == 200
    assert result.body == b"{}"
    assert result.headers == {"X-Test": "yes"}
    assert wire_response.closed is True

    oversized = WireResponse(b"x" * 17)
    transport._opener = Opener(oversized)
    with pytest.raises(AlpacaPaperMarketDataIntegrityError, match="exceeds configured limit"):
        transport.read(request)
    assert oversized.closed is True

    transport._opener = Opener(HTTPError(request.url, 403, "forbidden", {}, None))
    with pytest.raises(AlpacaPaperMarketDataUnavailable, match="HTTP error 403"):
        transport.read(request)

    transport._opener = Opener(URLError("offline"))
    with pytest.raises(AlpacaPaperMarketDataUnavailable, match="request failed"):
        transport.read(request)
