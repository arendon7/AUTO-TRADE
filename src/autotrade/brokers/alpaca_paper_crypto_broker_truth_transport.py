from __future__ import annotations

import json
import re
from typing import Mapping

from .alpaca_paper_crypto_writer import (
    AlpacaPaperCryptoWriteResponse,
    AlpacaPaperCryptoWriteTransport,
    CryptoPaperWriterAmbiguous,
    HttpsAlpacaPaperCryptoWriteTransport,
)


_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")


class BrokerTruthAlpacaPaperCryptoWriteTransport:
    """Thin diagnostic wrapper around the already-audited HTTPS writer transport.

    It does not add a second HTTP stack and does not change destination, method,
    timeout or retry behavior. It only converts a received non-200 Alpaca
    response into a sanitized ambiguity message so the durable execution record
    keeps the broker's deterministic rejection class instead of losing it.
    """

    def __init__(self, delegate: AlpacaPaperCryptoWriteTransport | None = None) -> None:
        self._delegate = delegate or HttpsAlpacaPaperCryptoWriteTransport()

    def post(
        self,
        *,
        host: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> AlpacaPaperCryptoWriteResponse:
        response = self._delegate.post(
            host=host,
            path=path,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        if response.status_code == 200:
            return response
        raise CryptoPaperWriterAmbiguous(_sanitized_rejection(response))


def _sanitized_rejection(response: AlpacaPaperCryptoWriteResponse) -> str:
    status = int(response.status_code)
    request_id = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        request_id = "unavailable"

    code = "unavailable"
    message = "unavailable"
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        try:
            payload = json.loads(response.body.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            code = _safe_scalar(payload.get("code"))
            message = _safe_text(payload.get("message"))
    return (
        "Alpaca PAPER order response rejected; "
        f"http_status={status}; broker_code={code}; message={message}; request_id={request_id}; "
        "POST outcome remains burned and reconciliation is GET-only"
    )


def _safe_scalar(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return "unavailable"
    if isinstance(value, (int, float, str)):
        return _safe_text(str(value))
    return "unavailable"


def _safe_text(value: object) -> str:
    if not isinstance(value, str):
        return "unavailable"
    text = " ".join(value.strip().split())
    text = "".join(char for char in text if 32 <= ord(char) < 127)
    if not text:
        return "unavailable"
    return text[:320]


__all__ = ["BrokerTruthAlpacaPaperCryptoWriteTransport"]
