from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_market_clock import (
    ALPACA_PAPER_CLOCK_PATH,
    AlpacaPaperMarketClockConfig,
    AlpacaPaperMarketClockGateway,
    AlpacaPaperMarketClockHttpResponse,
    AlpacaPaperMarketClockIntegrityError,
    AlpacaPaperMarketClockPolicyError,
    AlpacaPaperMarketClockRequest,
)


NOW = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")
URL = "https://paper-api.alpaca.markets/v2/clock"


def payload(*, is_open: bool = True, timestamp: datetime | None = None) -> bytes:
    timestamp = timestamp or NOW
    return json.dumps(
        {
            "timestamp": timestamp.isoformat(),
            "is_open": is_open,
            "next_open": (NOW + timedelta(hours=17)).isoformat(),
            "next_close": (NOW + timedelta(hours=23, minutes=30)).isoformat(),
        }
    ).encode()


class FakeTransport:
    def __init__(self, body: bytes | None = None, *, final_url: str = URL, status_code: int = 200):
        self.body = body if body is not None else payload()
        self.final_url = final_url
        self.status_code = status_code
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        return AlpacaPaperMarketClockHttpResponse(
            status_code=self.status_code,
            body=self.body,
            final_url=self.final_url,
            headers={"x-request-id": "req-clock-001"},
        )


def gateway(fake: FakeTransport):
    return AlpacaPaperMarketClockGateway(
        AlpacaPaperMarketClockConfig(enabled=True), transport=fake
    )


def test_clock_is_exact_one_get_and_preserves_open_state() -> None:
    fake = FakeTransport(payload(is_open=True))
    result = gateway(fake).read_clock(credentials=CREDS, now=NOW)
    assert result.is_open is True
    assert result.source_path == ALPACA_PAPER_CLOCK_PATH
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.method == "GET"
    assert request.url == URL
    assert request.headers["APCA-API-KEY-ID"] == CREDS.key_id
    assert "paper-secret" not in repr(request)


def test_clock_closed_is_valid_read_only_state() -> None:
    fake = FakeTransport(payload(is_open=False))
    result = gateway(fake).read_clock(credentials=CREDS, now=NOW)
    assert result.is_open is False
    assert result.next_open > result.timestamp


def test_clock_policy_rejects_post_live_drift_query_and_redirect() -> None:
    subject = gateway(FakeTransport())
    headers = {
        "APCA-API-KEY-ID": "k",
        "APCA-API-SECRET-KEY": "s",
        "Accept": "application/json",
    }
    for request in (
        AlpacaPaperMarketClockRequest("POST", URL, 5, headers),
        AlpacaPaperMarketClockRequest("GET", "https://api.alpaca.markets/v2/clock", 5, headers),
        AlpacaPaperMarketClockRequest("GET", URL + "?x=1", 5, headers),
    ):
        with pytest.raises(AlpacaPaperMarketClockPolicyError):
            subject._validate_request(request)
    redirected = gateway(FakeTransport(final_url="https://api.alpaca.markets/v2/clock"))
    with pytest.raises(AlpacaPaperMarketClockPolicyError):
        redirected.read_clock(credentials=CREDS, now=NOW)


def test_clock_rejects_stale_future_or_malformed_response() -> None:
    with pytest.raises(AlpacaPaperMarketClockIntegrityError, match="stale"):
        gateway(FakeTransport(payload(timestamp=NOW - timedelta(seconds=31)))).read_clock(
            credentials=CREDS, now=NOW
        )
    with pytest.raises(AlpacaPaperMarketClockIntegrityError, match="future"):
        gateway(FakeTransport(payload(timestamp=NOW + timedelta(seconds=3)))).read_clock(
            credentials=CREDS, now=NOW
        )
    with pytest.raises(AlpacaPaperMarketClockIntegrityError):
        gateway(FakeTransport(b"not-json")).read_clock(credentials=CREDS, now=NOW)


def test_clock_requires_credentials_and_disabled_by_default() -> None:
    fake = FakeTransport()
    disabled = AlpacaPaperMarketClockGateway(transport=fake)
    with pytest.raises(Exception, match="disabled"):
        disabled.read_clock(credentials=CREDS, now=NOW)
    assert fake.requests == []
