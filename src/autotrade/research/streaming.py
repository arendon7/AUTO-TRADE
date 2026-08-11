from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Mapping, Protocol
from urllib.parse import urlsplit

from .external_data import FIXED_INTERVAL_MS
from .market import Bar, InstrumentMetadata, InvalidMarketDataset


BINANCE_MARKET_STREAM_HOST = "data-stream.binance.vision"
BINANCE_MARKET_STREAM_BASE_PATH = "/ws/"
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,24}$")
_STREAM_NAME_RE = re.compile(r"^[a-z0-9]{3,24}@kline_[A-Za-z0-9]+$")


class StreamError(RuntimeError):
    pass


class StreamDisabled(StreamError):
    pass


class StreamPolicyError(StreamError):
    pass


class StreamIntegrityError(StreamError):
    pass


class StreamUnavailable(StreamError):
    pass


class StreamState(StrEnum):
    DISABLED = "DISABLED"
    READY = "READY"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class StreamOpenRequest:
    url: str
    timeout_seconds: float
    read_only: bool = True


class ReadOnlyStreamSession(Protocol):
    def receive(self, *, timeout_seconds: float) -> str | bytes | None: ...

    def close(self) -> None: ...


class ReadOnlyStreamTransport(Protocol):
    def open(self, request: StreamOpenRequest) -> ReadOnlyStreamSession: ...


@dataclass(frozen=True, slots=True)
class ClosedKlineSubscription:
    instrument: InstrumentMetadata
    interval: str
    enabled: bool = False
    timeout_seconds: float = 10.0
    max_event_lag_seconds: float = 30.0
    future_tolerance_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.instrument.venue != "BINANCE_SPOT":
            raise ValueError("closed-kline stream currently requires BINANCE_SPOT")
        if not _SYMBOL_RE.fullmatch(self.instrument.symbol):
            raise ValueError("invalid Binance symbol")
        if self.interval not in FIXED_INTERVAL_MS:
            raise ValueError("unsupported/non-fixed Binance interval")
        if not 0 < self.timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be > 0 and <= 30")
        if not 0 < self.max_event_lag_seconds <= 300:
            raise ValueError("max_event_lag_seconds must be > 0 and <= 300")
        if not 0 <= self.future_tolerance_seconds <= 5:
            raise ValueError("future_tolerance_seconds must be between 0 and 5")

    @property
    def stream_name(self) -> str:
        return f"{self.instrument.symbol.lower()}@kline_{self.interval}"

    @property
    def url(self) -> str:
        return f"wss://{BINANCE_MARKET_STREAM_HOST}{BINANCE_MARKET_STREAM_BASE_PATH}{self.stream_name}"

    @property
    def timeframe_seconds(self) -> int:
        return FIXED_INTERVAL_MS[self.interval] // 1000


@dataclass(frozen=True, slots=True)
class ClosedKlineStreamPolicy:
    allowed_host: str = BINANCE_MARKET_STREAM_HOST

    def validate(self, request: StreamOpenRequest, *, expected_stream_name: str) -> None:
        if not request.read_only:
            raise StreamPolicyError("stream transport must be read-only")
        if not 0 < request.timeout_seconds <= 30:
            raise StreamPolicyError("stream timeout must be > 0 and <= 30 seconds")
        if not _STREAM_NAME_RE.fullmatch(expected_stream_name):
            raise StreamPolicyError("invalid closed-kline stream name")

        parsed = urlsplit(request.url)
        if parsed.scheme != "wss":
            raise StreamPolicyError("market stream requires WSS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise StreamPolicyError("credentials, query parameters and fragments are forbidden")
        if parsed.hostname != self.allowed_host or parsed.port not in (None, 443):
            raise StreamPolicyError("market stream host is not allowlisted")
        expected_path = f"{BINANCE_MARKET_STREAM_BASE_PATH}{expected_stream_name}"
        if parsed.path != expected_path:
            raise StreamPolicyError("market stream path does not match the exact subscription")


@dataclass(frozen=True, slots=True)
class KlineObservation:
    state: StreamState
    accepted: bool
    duplicate: bool
    ignored_open_kline: bool
    bar: Bar | None
    bar_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class ParsedKline:
    bar: Bar
    event_time: datetime
    bar_fingerprint: str


class ClosedKlineStream:
    def __init__(
        self,
        *,
        subscription: ClosedKlineSubscription,
        transport: ReadOnlyStreamTransport,
        policy: ClosedKlineStreamPolicy | None = None,
    ) -> None:
        self._subscription = subscription
        self._transport = transport
        self._policy = policy or ClosedKlineStreamPolicy()
        self._state = StreamState.READY if subscription.enabled else StreamState.DISABLED
        self._session: ReadOnlyStreamSession | None = None
        self._last_bar: Bar | None = None
        self._last_fingerprint: str | None = None
        self._degraded_reason: str | None = None

    @property
    def state(self) -> StreamState:
        return self._state

    @property
    def last_bar(self) -> Bar | None:
        return self._last_bar

    @property
    def degraded_reason(self) -> str | None:
        return self._degraded_reason

    def open(self) -> None:
        if self._state == StreamState.DISABLED:
            raise StreamDisabled("closed-kline stream is disabled by default")
        if self._state == StreamState.DEGRADED:
            raise StreamUnavailable("DEGRADED stream cannot reconnect without explicit recovery")
        if self._state == StreamState.CLOSED:
            raise StreamUnavailable("closed stream cannot be reopened")
        if self._session is not None:
            return

        request = StreamOpenRequest(
            url=self._subscription.url,
            timeout_seconds=self._subscription.timeout_seconds,
        )
        # Policy is validated before the transport is allowed to perform I/O.
        self._policy.validate(request, expected_stream_name=self._subscription.stream_name)
        try:
            session = self._transport.open(request)
        except (TimeoutError, OSError) as exc:
            self._degrade("stream open failed")
            raise StreamUnavailable("market stream open failed") from exc
        self._session = session
        self._state = StreamState.ACTIVE

    def poll_once(self, *, received_at: datetime) -> KlineObservation:
        _require_aware(received_at, "received_at")
        if self._state != StreamState.ACTIVE or self._session is None:
            raise StreamUnavailable("stream must be ACTIVE before polling")
        try:
            payload = self._session.receive(timeout_seconds=self._subscription.timeout_seconds)
        except (TimeoutError, OSError) as exc:
            self._degrade("stream receive failed or timed out")
            raise StreamUnavailable("market stream receive failed") from exc
        if payload is None:
            self._degrade("stream terminated without a frame")
            raise StreamUnavailable("market stream terminated unexpectedly")
        try:
            return self.ingest(payload, received_at=received_at)
        except StreamIntegrityError:
            self._degrade("stream payload or continuity integrity failure")
            raise

    def ingest(self, payload: str | bytes, *, received_at: datetime) -> KlineObservation:
        _require_aware(received_at, "received_at")
        raw = _decode_payload(payload)
        event = _parse_json_object(raw)
        kline = event.get("k")
        if not isinstance(kline, Mapping):
            raise StreamIntegrityError("kline payload is missing object field 'k'")
        if event.get("e") != "kline":
            raise StreamIntegrityError("unexpected stream event type")
        if event.get("s") != self._subscription.instrument.symbol:
            raise StreamIntegrityError("stream event symbol does not match subscription")
        if kline.get("s") != self._subscription.instrument.symbol:
            raise StreamIntegrityError("kline symbol does not match subscription")
        if kline.get("i") != self._subscription.interval:
            raise StreamIntegrityError("kline interval does not match subscription")

        closed = kline.get("x")
        if not isinstance(closed, bool):
            raise StreamIntegrityError("kline closed flag must be boolean")
        if not closed:
            # Binance kline streams publish in-progress updates. R5 is closed-kline
            # only: an open candle is intentionally non-authoritative and cannot
            # advance cursor, evidence or risk state.
            return KlineObservation(
                state=self._state,
                accepted=False,
                duplicate=False,
                ignored_open_kline=True,
                bar=None,
                bar_fingerprint=None,
            )

        parsed = _parse_closed_kline(
            event=event,
            kline=kline,
            subscription=self._subscription,
            received_at=received_at,
        )
        return self._accept(parsed)

    def close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except OSError:
                pass
        if self._state != StreamState.DEGRADED:
            self._state = StreamState.CLOSED

    def _accept(self, parsed: ParsedKline) -> KlineObservation:
        bar = parsed.bar
        if self._state == StreamState.DEGRADED:
            raise StreamUnavailable("DEGRADED stream cannot advance")

        previous = self._last_bar
        if previous is not None:
            if bar.started_at == previous.started_at:
                if parsed.bar_fingerprint == self._last_fingerprint:
                    return KlineObservation(
                        state=self._state,
                        accepted=False,
                        duplicate=True,
                        ignored_open_kline=False,
                        bar=bar,
                        bar_fingerprint=parsed.bar_fingerprint,
                    )
                self._degrade("conflicting duplicate closed kline")
                raise StreamIntegrityError("conflicting duplicate closed kline")
            if bar.started_at < previous.started_at:
                self._degrade("out-of-order closed kline")
                raise StreamIntegrityError("out-of-order closed kline")
            if bar.started_at != previous.ended_at:
                self._degrade("closed-kline continuity gap")
                raise StreamIntegrityError("closed-kline continuity gap")

        self._last_bar = bar
        self._last_fingerprint = parsed.bar_fingerprint
        return KlineObservation(
            state=self._state,
            accepted=True,
            duplicate=False,
            ignored_open_kline=False,
            bar=bar,
            bar_fingerprint=parsed.bar_fingerprint,
        )

    def _degrade(self, reason: str) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except OSError:
                pass
        self._state = StreamState.DEGRADED
        self._degraded_reason = reason


def _parse_closed_kline(
    *,
    event: Mapping[str, object],
    kline: Mapping[str, object],
    subscription: ClosedKlineSubscription,
    received_at: datetime,
) -> ParsedKline:
    event_time_ms = _strict_int(event.get("E"), "event time")
    start_ms = _strict_int(kline.get("t"), "kline start time")
    close_ms = _strict_int(kline.get("T"), "kline close time")
    interval_ms = FIXED_INTERVAL_MS[subscription.interval]
    if start_ms < 0 or start_ms % interval_ms:
        raise StreamIntegrityError("kline start time is not interval-aligned")
    if close_ms != start_ms + interval_ms - 1:
        raise StreamIntegrityError("kline close time does not match fixed interval")
    if event_time_ms < close_ms:
        raise StreamIntegrityError("closed-kline event precedes kline close")

    event_time = datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc)
    received_utc = received_at.astimezone(timezone.utc)
    if event_time > received_utc + timedelta(seconds=subscription.future_tolerance_seconds):
        raise StreamIntegrityError("stream event time is in the future")
    if received_utc - event_time > timedelta(seconds=subscription.max_event_lag_seconds):
        raise StreamIntegrityError("stream event is stale")

    try:
        bar = Bar(
            symbol=subscription.instrument.symbol,
            started_at=datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
            timeframe_seconds=subscription.timeframe_seconds,
            open=_strict_decimal(kline.get("o"), "open"),
            high=_strict_decimal(kline.get("h"), "high"),
            low=_strict_decimal(kline.get("l"), "low"),
            close=_strict_decimal(kline.get("c"), "close"),
            volume=_strict_decimal(kline.get("v"), "volume"),
        )
    except InvalidMarketDataset as exc:
        raise StreamIntegrityError(f"invalid closed kline: {exc}") from exc

    fingerprint_payload = {
        "symbol": bar.symbol,
        "started_at": bar.started_at.isoformat(),
        "timeframe_seconds": bar.timeframe_seconds,
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
    }
    fingerprint = sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ParsedKline(bar=bar, event_time=event_time, bar_fingerprint=fingerprint)


def _decode_payload(payload: str | bytes) -> str:
    if isinstance(payload, bytes):
        try:
            return payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise StreamIntegrityError("stream payload is not valid UTF-8") from exc
    if isinstance(payload, str):
        return payload
    raise StreamIntegrityError("stream payload must be text or bytes")


def _parse_json_object(raw: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw, parse_constant=lambda token: (_raise_json_constant(token)))
    except (json.JSONDecodeError, ValueError) as exc:
        raise StreamIntegrityError("stream payload is not strict JSON") from exc
    if not isinstance(value, dict):
        raise StreamIntegrityError("stream payload root must be an object")
    return value


def _raise_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StreamIntegrityError(f"{label} must be an integer")
    return value


def _strict_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise StreamIntegrityError(f"{label} must be a decimal string")
    try:
        decimal = Decimal(value)
    except InvalidOperation as exc:
        raise StreamIntegrityError(f"{label} is not a valid decimal") from exc
    if not decimal.is_finite():
        raise StreamIntegrityError(f"{label} must be finite")
    return decimal


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
