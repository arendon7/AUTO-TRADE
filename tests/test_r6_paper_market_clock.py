from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperCredentials,
    AlpacaPaperHttpResponse,
    AlpacaPaperPolicyError,
)
from autotrade.brokers.alpaca_paper_market_clock import (
    ALPACA_PAPER_CLOCK_PATH,
    AlpacaPaperMarketClockConfig,
    AlpacaPaperMarketClockGateway,
    AlpacaPaperMarketClockIntegrityError,
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
        return AlpacaPaperHttpResponse(
            status_code=self.status_code,
            body=self.body,
            final_url=self.final_url,
            headers={
                "content-type": "application/json",
                "x-request-id": "req-clock-001",
            },
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
    assert request.headers["User-Agent"] == "AUTO-TRADE-R6/0.28R"
    assert "paper-secret" not in repr(request)


def test_clock_closed_is_valid_read_only_state() -> None:
    fake = FakeTransport(payload(is_open=False))
    result = gateway(fake).read_clock(credentials=CREDS, now=NOW)
    assert result.is_open is False
    assert result.next_open > result.timestamp


def test_clock_rejects_final_url_drift_through_shared_policy() -> None:
    redirected = gateway(FakeTransport(final_url="https://api.alpaca.markets/v2/clock"))
    with pytest.raises(AlpacaPaperPolicyError):
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


def test_clock_is_disabled_by_default_before_transport() -> None:
    fake = FakeTransport()
    disabled = AlpacaPaperMarketClockGateway(transport=fake)
    with pytest.raises(Exception, match="disabled"):
        disabled.read_clock(credentials=CREDS, now=NOW)
    assert fake.requests == []
