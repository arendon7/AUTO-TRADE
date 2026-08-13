from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

from .alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperReadPolicy,
    AlpacaPaperReadRequest,
    AlpacaPaperReadTransport,
    AlpacaPaperUnavailable,
    UrllibAlpacaPaperReadTransport,
)


ALPACA_PAPER_CLOCK_PATH = "/v2/clock"
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")


class AlpacaPaperMarketClockError(RuntimeError):
    pass


class AlpacaPaperMarketClockDisabled(AlpacaPaperMarketClockError):
    pass


class AlpacaPaperMarketClockIntegrityError(AlpacaPaperMarketClockError):
    pass


@dataclass(frozen=True, slots=True)
class AlpacaPaperMarketClockConfig:
    enabled: bool = False
    timeout_seconds: float = 5.0
    max_response_bytes: int = 32 * 1024
    max_clock_age_seconds: float = 30.0
    future_tolerance_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("PAPER clock timeout must be >0 and <=15 seconds")
        if not 1 <= self.max_response_bytes <= 256 * 1024:
            raise ValueError("PAPER clock response limit is invalid")
        if not 0 < self.max_clock_age_seconds <= 120:
            raise ValueError("PAPER clock max age must be >0 and <=120 seconds")
        if not 0 <= self.future_tolerance_seconds <= 5:
            raise ValueError("PAPER clock future tolerance must be between 0 and 5 seconds")


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


class AlpacaPaperMarketClockGateway:
    """Exact GET-only PAPER clock using the already-audited account transport."""

    def __init__(
        self,
        config: AlpacaPaperMarketClockConfig | None = None,
        *,
        transport: AlpacaPaperReadTransport | None = None,
    ) -> None:
        self._config = config or AlpacaPaperMarketClockConfig()
        self._policy = AlpacaPaperReadPolicy(
            allowed_paths=frozenset({ALPACA_PAPER_CLOCK_PATH})
        )
        shared_config = AlpacaPaperGatewayConfig(
            enabled=self._config.enabled,
            timeout_seconds=self._config.timeout_seconds,
            max_response_bytes=self._config.max_response_bytes,
        )
        self._transport = transport or UrllibAlpacaPaperReadTransport(
            policy=self._policy,
            max_response_bytes=shared_config.max_response_bytes,
        )

    def read_clock(
        self, *, credentials: AlpacaPaperCredentials, now: datetime
    ) -> AlpacaPaperMarketClock:
        if not self._config.enabled:
            raise AlpacaPaperMarketClockDisabled("PAPER market clock is disabled")
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("Alpaca PAPER credentials are required")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        request = AlpacaPaperReadRequest(
            method="GET",
            url=(
                "https" + "://" + ALPACA_PAPER_TRADING_HOST + ALPACA_PAPER_CLOCK_PATH
            ),
            timeout_seconds=self._config.timeout_seconds,
            headers={
                "Accept": "application/json",
                "User-Agent": "AUTO-TRADE-R6/0.28R",
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
            },
        )
        self._policy.validate(request)
        try:
            response = self._transport.read(request)
        except AlpacaPaperUnavailable:
            raise
        self._policy.validate_final_url(response.final_url)
        return self._parse(response=response, received_at=now.astimezone(timezone.utc))

    def _parse(
        self, *, response: AlpacaPaperHttpResponse, received_at: datetime
    ) -> AlpacaPaperMarketClock:
        if response.status_code != 200:
            raise AlpacaPaperMarketClockIntegrityError(
                f"unexpected PAPER clock status: {response.status_code}"
            )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type and content_type != "application/json":
            raise AlpacaPaperMarketClockIntegrityError(
                "PAPER clock response must be application/json"
            )
        try:
            raw = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AlpacaPaperMarketClockIntegrityError(
                "PAPER clock response is invalid JSON"
            ) from exc
        if not isinstance(raw, dict):
            raise AlpacaPaperMarketClockIntegrityError(
                "PAPER clock response root must be object"
            )
        is_open = raw.get("is_open")
        if not isinstance(is_open, bool):
            raise AlpacaPaperMarketClockIntegrityError(
                "PAPER clock is_open must be boolean"
            )
        timestamp = _rfc3339(raw.get("timestamp"), "timestamp")
        next_open = _rfc3339(raw.get("next_open"), "next_open")
        next_close = _rfc3339(raw.get("next_close"), "next_close")
        age = (received_at - timestamp.astimezone(timezone.utc)).total_seconds()
        if age < -self._config.future_tolerance_seconds:
            raise AlpacaPaperMarketClockIntegrityError(
                "PAPER clock timestamp is in the future"
            )
        if age > self._config.max_clock_age_seconds:
            raise AlpacaPaperMarketClockIntegrityError(
                "PAPER clock response is stale"
            )
        request_id = response.headers.get("x-request-id", "clock-no-request-id")
        if not _REQUEST_ID_RE.fullmatch(request_id):
            raise AlpacaPaperMarketClockIntegrityError(
                "PAPER clock request id is invalid"
            )
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
        raise AlpacaPaperMarketClockIntegrityError(
            f"PAPER clock {label} must be RFC3339"
        )
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AlpacaPaperMarketClockIntegrityError(
            f"PAPER clock {label} must be RFC3339"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AlpacaPaperMarketClockIntegrityError(
            f"PAPER clock {label} must be timezone-aware"
        )
    return parsed


__all__ = [
    "ALPACA_PAPER_CLOCK_PATH",
    "AlpacaPaperMarketClock",
    "AlpacaPaperMarketClockConfig",
    "AlpacaPaperMarketClockError",
    "AlpacaPaperMarketClockGateway",
    "AlpacaPaperMarketClockIntegrityError",
]
