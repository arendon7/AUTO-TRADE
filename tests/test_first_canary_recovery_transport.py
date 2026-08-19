from __future__ import annotations

from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

import pytest

from autotrade.first_canary_recovery_transport import FirstCanaryRecoveryReadTransport
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperReadRequest,
    AlpacaPaperUnavailable,
    UrllibAlpacaPaperReadTransport,
)


URL = "https://paper-api.alpaca.markets/v2/orders:by_client_order_id?client_order_id=atr6c-entry-abc"


def _request() -> AlpacaPaperReadRequest:
    return AlpacaPaperReadRequest(
        method="GET",
        url=URL,
        timeout_seconds=5,
        headers={
            "Accept": "application/json",
            "User-Agent": "AUTO-TRADE-R6/0.28R",
            "APCA-API-KEY-ID": "paper-key",
            "APCA-API-SECRET-KEY": "paper-secret",
        },
    )


def _http_error(code: int, *, body: bytes = b'{"code":40410000,"message":"order not found"}') -> HTTPError:
    headers = Message()
    headers["Content-Type"] = "application/json"
    headers["X-Request-ID"] = "req-first-canary-recovery-404"
    return HTTPError(URL, code, "synthetic", headers, BytesIO(body))


def test_exact_404_is_returned_as_read_evidence(monkeypatch) -> None:
    def inner_read(self, request):
        error = _http_error(404)
        raise AlpacaPaperUnavailable("PAPER Trading API HTTP error: 404") from error

    monkeypatch.setattr(UrllibAlpacaPaperReadTransport, "read", inner_read)
    response = FirstCanaryRecoveryReadTransport().read(_request())
    assert response.status_code == 404
    assert response.final_url == URL
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-request-id"] == "req-first-canary-recovery-404"
    assert b"order not found" in response.body


def test_non_404_remains_fail_closed(monkeypatch) -> None:
    def inner_read(self, request):
        error = _http_error(500)
        raise AlpacaPaperUnavailable("PAPER Trading API HTTP error: 500") from error

    monkeypatch.setattr(UrllibAlpacaPaperReadTransport, "read", inner_read)
    with pytest.raises(AlpacaPaperUnavailable, match="HTTP error: 500"):
        FirstCanaryRecoveryReadTransport().read(_request())


def test_404_body_remains_bounded(monkeypatch) -> None:
    def inner_read(self, request):
        error = _http_error(404, body=b"x" * 17)
        raise AlpacaPaperUnavailable("PAPER Trading API HTTP error: 404") from error

    monkeypatch.setattr(UrllibAlpacaPaperReadTransport, "read", inner_read)
    with pytest.raises(AlpacaPaperUnavailable, match="exceeded size limit"):
        FirstCanaryRecoveryReadTransport(max_response_bytes=16).read(_request())
