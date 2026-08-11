from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from autotrade.research.market import InstrumentMetadata
from autotrade.research.streaming import (
    ClosedKlineStream,
    ClosedKlineStreamPolicy,
    ClosedKlineSubscription,
    StreamDisabled,
    StreamIntegrityError,
    StreamOpenRequest,
    StreamPolicyError,
    StreamState,
    StreamUnavailable,
)


UTC = timezone.utc
START = datetime(2026, 8, 11, 6, 0, tzinfo=UTC)


class FakeSession:
    def __init__(self, frames: list[str | bytes | None | BaseException]) -> None:
        self.frames = list(frames)
        self.closed = False
        self.receive_calls = 0

    def receive(self, *, timeout_seconds: float) -> str | bytes | None:
        assert 0 < timeout_seconds <= 30
        self.receive_calls += 1
        if not self.frames:
            return None
        value = self.frames.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, session: FakeSession | None = None, error: BaseException | None = None) -> None:
        self.session = session or FakeSession([])
        self.error = error
        self.open_calls = 0
        self.requests: list[StreamOpenRequest] = []

    def open(self, request: StreamOpenRequest) -> FakeSession:
        self.open_calls += 1
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.session


def instrument() -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol="BTCUSDT",
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.00001"),
    )


def subscription(*, enabled: bool = True, max_lag: float = 30.0) -> ClosedKlineSubscription:
    return ClosedKlineSubscription(
        instrument=instrument(),
        interval="1m",
        enabled=enabled,
        max_event_lag_seconds=max_lag,
    )


def payload(
    start: datetime,
    *,
    closed: bool = True,
    close: str = "100.5",
    event_time: datetime | None = None,
) -> str:
    start_ms = int(start.timestamp() * 1000)
    close_ms = start_ms + 60_000 - 1
    event = event_time or datetime.fromtimestamp((close_ms + 1) / 1000, tz=UTC)
    return json.dumps(
        {
            "e": "kline",
            "E": int(event.timestamp() * 1000),
            "s": "BTCUSDT",
            "k": {
                "t": start_ms,
                "T": close_ms,
                "s": "BTCUSDT",
                "i": "1m",
                "o": "100.0",
                "c": close,
                "h": "101.0",
                "l": "99.5",
                "v": "12.5",
                "x": closed,
            },
        },
        separators=(",", ":"),
    )


def open_stream(*frames: str | bytes | None | BaseException) -> tuple[ClosedKlineStream, FakeTransport, FakeSession]:
    session = FakeSession(list(frames))
    transport = FakeTransport(session)
    stream = ClosedKlineStream(subscription=subscription(), transport=transport)
    stream.open()
    return stream, transport, session


def received(start: datetime = START) -> datetime:
    return start + timedelta(seconds=61)


def test_stream_is_disabled_by_default_and_performs_no_io() -> None:
    transport = FakeTransport()
    stream = ClosedKlineStream(subscription=subscription(enabled=False), transport=transport)

    assert stream.state == StreamState.DISABLED
    with pytest.raises(StreamDisabled):
        stream.open()
    assert transport.open_calls == 0


def test_policy_rejects_wrong_host_query_and_path_before_transport_io() -> None:
    transport = FakeTransport()
    stream = ClosedKlineStream(
        subscription=subscription(),
        transport=transport,
        policy=ClosedKlineStreamPolicy(allowed_host="not-binance.example"),
    )
    with pytest.raises(StreamPolicyError):
        stream.open()
    assert transport.open_calls == 0

    policy = ClosedKlineStreamPolicy()
    with pytest.raises(StreamPolicyError):
        policy.validate(
            StreamOpenRequest(
                url="wss://data-stream.binance.vision/ws/btcusdt@kline_1m?token=forbidden",
                timeout_seconds=1,
            ),
            expected_stream_name="btcusdt@kline_1m",
        )
    with pytest.raises(StreamPolicyError):
        policy.validate(
            StreamOpenRequest(
                url="wss://data-stream.binance.vision/ws/ethusdt@kline_1m",
                timeout_seconds=1,
            ),
            expected_stream_name="btcusdt@kline_1m",
        )


def test_open_kline_is_non_authoritative_and_does_not_advance_cursor() -> None:
    stream, _, _ = open_stream(payload(START, closed=False), payload(START, closed=True))

    first = stream.poll_once(received_at=received())
    assert first.ignored_open_kline is True
    assert first.accepted is False
    assert stream.last_bar is None

    second = stream.poll_once(received_at=received())
    assert second.accepted is True
    assert second.bar is not None
    assert second.bar.started_at == START


def test_identical_duplicate_is_idempotent_noop() -> None:
    frame = payload(START)
    stream, _, _ = open_stream(frame, frame)

    first = stream.poll_once(received_at=received())
    second = stream.poll_once(received_at=received())

    assert first.accepted is True
    assert second.accepted is False
    assert second.duplicate is True
    assert second.bar_fingerprint == first.bar_fingerprint
    assert stream.state == StreamState.ACTIVE


def test_conflicting_duplicate_fails_closed_and_sticks_degraded() -> None:
    stream, transport, session = open_stream(payload(START), payload(START, close="100.6"))
    stream.poll_once(received_at=received())

    with pytest.raises(StreamIntegrityError, match="conflicting duplicate"):
        stream.poll_once(received_at=received())

    assert stream.state == StreamState.DEGRADED
    assert session.closed is True
    assert transport.open_calls == 1
    with pytest.raises(StreamUnavailable, match="DEGRADED"):
        stream.open()
    assert transport.open_calls == 1


def test_gap_fails_closed_without_imputation_and_reconnect_cannot_hide_it() -> None:
    stream, transport, _ = open_stream(payload(START), payload(START + timedelta(minutes=2)))
    stream.poll_once(received_at=received())

    with pytest.raises(StreamIntegrityError, match="continuity gap"):
        stream.poll_once(received_at=received(START + timedelta(minutes=2)))

    assert stream.state == StreamState.DEGRADED
    assert stream.last_bar is not None
    assert stream.last_bar.started_at == START
    with pytest.raises(StreamUnavailable):
        stream.open()
    assert transport.open_calls == 1


def test_out_of_order_closed_kline_fails_closed() -> None:
    observation_time = received(START + timedelta(minutes=1))
    stream, _, _ = open_stream(
        payload(START + timedelta(minutes=1)),
        payload(START, event_time=observation_time),
    )
    stream.poll_once(received_at=observation_time)

    with pytest.raises(StreamIntegrityError, match="out-of-order"):
        stream.poll_once(received_at=observation_time)
    assert stream.state == StreamState.DEGRADED


def test_future_event_fails_closed() -> None:
    future_event = received() + timedelta(seconds=3)
    stream, _, _ = open_stream(payload(START, event_time=future_event))

    with pytest.raises(StreamIntegrityError, match="future"):
        stream.poll_once(received_at=received())
    assert stream.state == StreamState.DEGRADED


def test_stale_event_fails_closed() -> None:
    stream, _, _ = open_stream(payload(START))
    with pytest.raises(StreamIntegrityError, match="stale"):
        stream.poll_once(received_at=received() + timedelta(minutes=1))
    assert stream.state == StreamState.DEGRADED


@pytest.mark.parametrize("terminal", [None, TimeoutError("timeout"), OSError("socket")])
def test_socket_termination_timeout_or_error_is_degraded(terminal: BaseException | None) -> None:
    stream, transport, session = open_stream(terminal)

    with pytest.raises(StreamUnavailable):
        stream.poll_once(received_at=received())

    assert stream.state == StreamState.DEGRADED
    assert session.closed is True
    with pytest.raises(StreamUnavailable):
        stream.open()
    assert transport.open_calls == 1


def test_transport_open_failure_is_degraded() -> None:
    transport = FakeTransport(error=TimeoutError("timeout"))
    stream = ClosedKlineStream(subscription=subscription(), transport=transport)

    with pytest.raises(StreamUnavailable):
        stream.open()
    assert stream.state == StreamState.DEGRADED
    assert transport.open_calls == 1


def test_malformed_json_and_nonfinite_prices_fail_closed() -> None:
    malformed, _, _ = open_stream("not-json")
    with pytest.raises(StreamIntegrityError):
        malformed.poll_once(received_at=received())
    assert malformed.state == StreamState.DEGRADED

    nonfinite = json.loads(payload(START))
    nonfinite["k"]["o"] = "NaN"
    stream, _, _ = open_stream(json.dumps(nonfinite))
    with pytest.raises(StreamIntegrityError, match="finite"):
        stream.poll_once(received_at=received())
    assert stream.state == StreamState.DEGRADED


def test_wrong_symbol_interval_or_close_boundary_fails_closed() -> None:
    wrong_symbol = json.loads(payload(START))
    wrong_symbol["s"] = "ETHUSDT"
    stream, _, _ = open_stream(json.dumps(wrong_symbol))
    with pytest.raises(StreamIntegrityError, match="symbol"):
        stream.poll_once(received_at=received())

    wrong_interval = json.loads(payload(START))
    wrong_interval["k"]["i"] = "5m"
    stream, _, _ = open_stream(json.dumps(wrong_interval))
    with pytest.raises(StreamIntegrityError, match="interval"):
        stream.poll_once(received_at=received())

    wrong_close = json.loads(payload(START))
    wrong_close["k"]["T"] += 1
    stream, _, _ = open_stream(json.dumps(wrong_close))
    with pytest.raises(StreamIntegrityError, match="close time"):
        stream.poll_once(received_at=received())
