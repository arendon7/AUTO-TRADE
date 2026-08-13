from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import socket
import ssl
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
)


ALPACA_PAPER_CLOCK_PATH = "/v2/clock"
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")


class AlpacaPaperMarketClockError(RuntimeError):
    pass


class AlpacaPaperMarketClockDisabled(AlpacaPaperMarketClockError):
    pass


class AlpacaPaperMarketClockPolicyError(AlpacaPaperMarketClockError):
    pass


class AlpacaPaperMarketClockUnavailable(AlpacaPaperMarketClockError):
    pass


class AlpacaPaperMarketClockIntegrityError(AlpacaPaperMarketClockError):
    pass


@dataclass(frozen=True, slots=True)
class AlpacaPaperMarketClockConfig:
    enabled: bool = False
    base_url: str = f"https://{ALPACA_PAPER_TRADING_HOST}"
    timeout_seconds: float = 5.0
    max_response_bytes: int = 32 * 1024
    max_clock_age_seconds: float = 30.0
    future_tolerance_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.base_url != f"https://{ALPACA_PAPER_TRADING_HOST}":
            raise ValueError("PAPER clock base_url must be exact Alpaca PAPER host")
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("PAPER clock timeout must be >0 and <=15 seconds")
        if not 1 <= self.max_response_bytes <= 256 * 1024:
            raise ValueError("PAPER clock response limit is invalid")
        if not 0 < self.max_clock_age_seconds <= 120:
            raise ValueError("PAPER clock max age must be >0 and <=120 seconds")
        if not 0 <= self.future_tolerance_seconds <= 5:
            raise ValueError("PAPER clock future tolerance must be between 0 and 5 seconds")


@dataclass(frozen=True, slots=True)
class AlpacaPaperMarketClockRequest:
    method: str
    url: str
    timeout_seconds: float
    headers: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class AlpacaPaperMarketClockHttpResponse:
    status_code: int
    body: bytes
    final_url: str
    headers: Mapping[str, str]


class AlpacaPaperMarketClockTransport(Protocol):
    def read(self, request: AlpacaPaperMarketClockRequest) -> AlpacaPaperMarketClockHttpResponse: ...


@dataclass(frozen=True, slots=True)
class AlpacaPaperMarketClock:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime
    received_at: datetime
    request_id: str
    source_host: str = ALPACA_PAPER_TRADING_HOST
    source_path: str = ALPACA_PAPER_CLOCK_PATH

    def __post_init__(self) -> None:
        for value, label in (
            (self.timestamp, "timestamp"),
            (self.next_open, "next_open"),
            (self.next_close, "next_close"),
            (self.received_at, "received_at"),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be timezone-aware")
        if not isinstance(self.is_open, bool):
            raise ValueError("is_open must be boolean")
        if self.source_host != ALPACA_PAPER_TRADING_HOST or self.source_path != ALPACA_PAPER_CLOCK_PATH:
            raise ValueError("PAPER clock source must be exact allowlisted endpoint")
        if not _REQUEST_ID_RE.fullmatch(self.request_id):
            raise ValueError("PAPER clock request id is invalid")


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AlpacaPaperMarketClockPolicyError("PAPER clock redirects are forbidden")


class UrllibAlpacaPaperMarketClockTransport:
    def __init__(self, *, max_response_bytes: int = 32 * 1024) -> None:
        self._max_response_bytes = max_response_bytes
        context = ssl.create_default_context()
        self._opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _RejectRedirectHandler())

    def read(self, request: AlpacaPaperMarketClockRequest) -> AlpacaPaperMarketClockHttpResponse:
        try:
            response = self._opener.open(
                Request(request.url, headers=dict(request.headers), method="GET"),
                timeout=request.timeout_seconds,
            )
            try:
                body = response.read(self._max_response_bytes + 1)
                if len(body) > self._max_response_bytes:
                    raise AlpacaPaperMarketClockIntegrityError("PAPER clock response exceeds configured limit")
                return AlpacaPaperMarketClockHttpResponse(
                    status_code=int(response.status),
                    body=body,
                    final_url=str(response.geturl()),
                    headers={str(k).lower(): str(v) for k, v in response.headers.items()},
                )
            finally:
                response.close()
        except HTTPError as exc:
            raise AlpacaPaperMarketClockUnavailable(f"Alpaca PAPER clock HTTP error {exc.code}") from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise AlpacaPaperMarketClockUnavailable("Alpaca PAPER clock request failed") from exc


class AlpacaPaperMarketClockGateway:
    """Exact GET-only PAPER market clock. No order or mutation surface exists."""

    def __init__(
        self,
        config: AlpacaPaperMarketClockConfig | None = None,
        *,
        transport: AlpacaPaperMarketClockTransport | None = None,
    ) -> None:
        self._config = config or AlpacaPaperMarketClockConfig()
        self._transport = transport or UrllibAlpacaPaperMarketClockTransport(
            max_response_bytes=self._config.max_response_bytes
        )

    def read_clock(self, *, credentials: AlpacaPaperCredentials, now: datetime) -> AlpacaPaperMarketClock:
        if not self._config.enabled:
            raise AlpacaPaperMarketClockDisabled("PAPER market clock is disabled")
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("Alpaca PAPER credentials are required")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        request = AlpacaPaperMarketClockRequest(
            method="GET",
            url=f"{self._config.base_url}{ALPACA_PAPER_CLOCK_PATH}",
            timeout_seconds=self._config.timeout_seconds,
            headers={
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
                "Accept": "application/json",
            },
        )
        self._validate_request(request)
        response = self._transport.read(request)
        self._validate_final_url(response.final_url)
        if response.status_code != 200:
            raise AlpacaPaperMarketClockUnavailable(
                f"Alpaca PAPER clock returned HTTP {response.status_code}"
            )
        return self._parse(response=response, received_at=now.astimezone(timezone.utc))

    def _validate_request(self, request: AlpacaPaperMarketClockRequest) -> None:
        if request.method != "GET":
            raise AlpacaPaperMarketClockPolicyError("PAPER market clock is GET-only")
        parsed = urlsplit(request.url)
        if parsed.scheme != "https" or parsed.hostname != ALPACA_PAPER_TRADING_HOST:
            raise AlpacaPaperMarketClockPolicyError("PAPER clock host is not exact allowlist")
        if parsed.port not in (None, 443) or parsed.path != ALPACA_PAPER_CLOCK_PATH or parsed.query or parsed.fragment:
            raise AlpacaPaperMarketClockPolicyError("PAPER clock URL is non-canonical")
        if set(request.headers) != {"APCA-API-KEY-ID", "APCA-API-SECRET-KEY", "Accept"}:
            raise AlpacaPaperMarketClockPolicyError("PAPER clock headers are non-canonical")
        if request.headers.get("Accept") != "application/json":
            raise AlpacaPaperMarketClockPolicyError("PAPER clock Accept header is invalid")

    def _validate_final_url(self, url: str) -> None:
        if url != f"{self._config.base_url}{ALPACA_PAPER_CLOCK_PATH}":
            raise AlpacaPaperMarketClockPolicyError("PAPER clock final URL changed")

    def _parse(
        self, *, response: AlpacaPaperMarketClockHttpResponse, received_at: datetime
    ) -> AlpacaPaperMarketClock:
        try:
            raw = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AlpacaPaperMarketClockIntegrityError("PAPER clock response is invalid JSON") from exc
        if not isinstance(raw, dict):
            raise AlpacaPaperMarketClockIntegrityError("PAPER clock response root must be object")
        is_open = raw.get("is_open")
        if not isinstance(is_open, bool):
            raise AlpacaPaperMarketClockIntegrityError("PAPER clock is_open must be boolean")
        timestamp = _rfc3339(raw.get("timestamp"), "timestamp")
        next_open = _rfc3339(raw.get("next_open"), "next_open")
        next_close = _rfc3339(raw.get("next_close"), "next_close")
        age = (received_at - timestamp.astimezone(timezone.utc)).total_seconds()
        if age < -self._config.future_tolerance_seconds:
            raise AlpacaPaperMarketClockIntegrityError("PAPER clock timestamp is in the future")
        if age > self._config.max_clock_age_seconds:
            raise AlpacaPaperMarketClockIntegrityError("PAPER clock response is stale")
        request_id = response.headers.get("x-request-id", "clock-no-request-id")
        if not _REQUEST_ID_RE.fullmatch(request_id):
            raise AlpacaPaperMarketClockIntegrityError("PAPER clock request id is invalid")
        return AlpacaPaperMarketClock(
            timestamp=timestamp,
            is_open=is_open,
            next_open=next_open,
            next_close=next_close,
            received_at=received_at,
            request_id=request_id,
        )


def _rfc3339(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AlpacaPaperMarketClockIntegrityError(f"PAPER clock {label} must be RFC3339")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AlpacaPaperMarketClockIntegrityError(f"PAPER clock {label} must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AlpacaPaperMarketClockIntegrityError(f"PAPER clock {label} must be timezone-aware")
    return parsed


__all__ = [
    "ALPACA_PAPER_CLOCK_PATH",
    "AlpacaPaperMarketClock",
    "AlpacaPaperMarketClockConfig",
    "AlpacaPaperMarketClockError",
    "AlpacaPaperMarketClockGateway",
    "AlpacaPaperMarketClockIntegrityError",
    "AlpacaPaperMarketClockPolicyError",
    "AlpacaPaperMarketClockUnavailable",
]
