from __future__ import annotations

from decimal import Decimal

import pytest
from websockets.exceptions import InvalidURI, WebSocketException

from autotrade.research.market import InstrumentMetadata
from autotrade.research import stream_transport
from autotrade.research.stream_transport import (
    WebsocketsReadOnlySession,
    WebsocketsReadOnlyTransport,
    WebsocketsTransportLimits,
    build_binance_closed_kline_stream,
)
from autotrade.research.streaming import (
    ClosedKlineSubscription,
    StreamOpenRequest,
    StreamState,
)


class FakeConnection:
    def __init__(self, result="frame", receive_error: BaseException | None = None, close_error: BaseException | None = None):
        self.result = result
        self.receive_error = receive_error
        self.close_error = close_error
        self.recv_timeouts: list[float] = []
        self.close_calls = 0

    def recv(self, *, timeout: float):
        self.recv_timeouts.append(timeout)
        if self.receive_error is not None:
            raise self.receive_error
        return self.result

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def instrument() -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol="BTCUSDT",
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.00001"),
    )


def request(*, read_only: bool = True, timeout: float = 7.0) -> StreamOpenRequest:
    return StreamOpenRequest(
        url="wss://data-stream.binance.vision/ws/btcusdt@kline_1s",
        timeout_seconds=timeout,
        read_only=read_only,
    )


def test_transport_opens_with_proxy_compression_and_buffers_hardened(monkeypatch) -> None:
    connection = FakeConnection()
    calls = []

    def fake_connect(url, **kwargs):
        calls.append((url, kwargs))
        return connection

    monkeypatch.setattr(stream_transport, "websocket_connect", fake_connect)
    limits = WebsocketsTransportLimits(
        max_message_bytes=32_768,
        max_queue_frames=2,
        ping_interval_seconds=15,
        ping_timeout_seconds=5,
        close_timeout_seconds=3,
    )
    transport = WebsocketsReadOnlyTransport(limits=limits)

    session = transport.open(request(timeout=6))

    assert isinstance(session, WebsocketsReadOnlySession)
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == request().url
    assert kwargs["proxy"] is None
    assert kwargs["compression"] is None
    assert kwargs["open_timeout"] == 6
    assert kwargs["max_size"] == 32_768
    assert kwargs["max_queue"] == 2
    assert kwargs["ping_interval"] == 15
    assert kwargs["ping_timeout"] == 5
    assert kwargs["close_timeout"] == 3
    assert kwargs["user_agent_header"] == "AUTO-TRADE-R5/0.28R"


def test_transport_refuses_non_read_only_request_before_connect(monkeypatch) -> None:
    called = False

    def fake_connect(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not connect")

    monkeypatch.setattr(stream_transport, "websocket_connect", fake_connect)
    transport = WebsocketsReadOnlyTransport()

    with pytest.raises(ValueError, match="read-only"):
        transport.open(request(read_only=False))
    assert called is False


def test_transport_normalizes_handshake_failures(monkeypatch) -> None:
    def fake_connect(*args, **kwargs):
        raise InvalidURI("not-wss", "invalid")

    monkeypatch.setattr(stream_transport, "websocket_connect", fake_connect)
    with pytest.raises(OSError, match="open failed"):
        WebsocketsReadOnlyTransport().open(request())


def test_transport_preserves_open_timeout(monkeypatch) -> None:
    def fake_connect(*args, **kwargs):
        raise TimeoutError("open timeout")

    monkeypatch.setattr(stream_transport, "websocket_connect", fake_connect)
    with pytest.raises(TimeoutError, match="open timeout"):
        WebsocketsReadOnlyTransport().open(request())


def test_session_receive_is_receive_only_and_timeout_bound() -> None:
    connection = FakeConnection(result=b"json")
    session = WebsocketsReadOnlySession(connection)  # type: ignore[arg-type]

    assert session.receive(timeout_seconds=4.5) == b"json"
    assert connection.recv_timeouts == [4.5]
    assert not hasattr(session, "send")


def test_session_timeout_propagates_for_stream_degraded_semantics() -> None:
    connection = FakeConnection(receive_error=TimeoutError("late"))
    session = WebsocketsReadOnlySession(connection)  # type: ignore[arg-type]

    with pytest.raises(TimeoutError, match="late"):
        session.receive(timeout_seconds=1)


def test_session_normalizes_websocket_receive_failure() -> None:
    connection = FakeConnection(receive_error=WebSocketException("protocol"))
    session = WebsocketsReadOnlySession(connection)  # type: ignore[arg-type]

    with pytest.raises(OSError, match="receive failed"):
        session.receive(timeout_seconds=1)


def test_session_connection_closed_is_terminal_none(monkeypatch) -> None:
    class FakeClosed(Exception):
        pass

    monkeypatch.setattr(stream_transport, "ConnectionClosed", FakeClosed)
    connection = FakeConnection(receive_error=FakeClosed("closed"))
    session = WebsocketsReadOnlySession(connection)  # type: ignore[arg-type]

    assert session.receive(timeout_seconds=1) is None


def test_session_close_is_idempotent_surface_without_send() -> None:
    connection = FakeConnection()
    session = WebsocketsReadOnlySession(connection)  # type: ignore[arg-type]
    session.close()
    assert connection.close_calls == 1
    assert not hasattr(session, "send")


def test_factory_construction_performs_no_io(monkeypatch) -> None:
    called = False

    def fake_connect(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("construction must not perform I/O")

    monkeypatch.setattr(stream_transport, "websocket_connect", fake_connect)
    subscription = ClosedKlineSubscription(instrument=instrument(), interval="1s")
    stream = build_binance_closed_kline_stream(subscription)

    assert stream.state == StreamState.DISABLED
    assert called is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_message_bytes": 0},
        {"max_message_bytes": 1_048_577},
        {"max_queue_frames": 0},
        {"max_queue_frames": 17},
        {"ping_interval_seconds": 0},
        {"ping_timeout_seconds": 31},
        {"close_timeout_seconds": 0},
    ],
)
def test_transport_limits_are_bounded(kwargs) -> None:
    with pytest.raises(ValueError):
        WebsocketsTransportLimits(**kwargs)
