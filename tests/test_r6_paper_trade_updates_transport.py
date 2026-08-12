from __future__ import annotations

import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_trade_updates_transport import (
    ALPACA_LIVE_TRADE_UPDATES_URL,
    ALPACA_PAPER_TRADE_UPDATES_URL,
    AlpacaPaperTradeUpdatesTransport,
    PaperTradeUpdatesConfig,
    PaperTradeUpdatesDegraded,
    PaperTradeUpdatesDisabled,
    PaperTradeUpdatesHandshakeError,
    PaperTradeUpdatesPolicyError,
    PaperTradeUpdatesState,
    WebsocketsPaperTradeUpdatesConnector,
)


class FakeSocket:
    def __init__(self, frames=(), *, fail_send_at=None, text_event=False):
        self.frames = list(frames)
        self.sent: list[str] = []
        self.closed = False
        self.fail_send_at = fail_send_at
        self.text_event = text_event

    def send(self, message: str) -> None:
        if self.fail_send_at == len(self.sent) + 1:
            raise OSError("synthetic send failure")
        self.sent.append(message)

    def recv(self, timeout=None):
        del timeout
        if not self.frames:
            raise TimeoutError
        value = self.frames.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


class FakeConnector:
    def __init__(self, socket: FakeSocket):
        self.socket = socket
        self.calls = []

    def open(self, *, endpoint, config):
        self.calls.append((endpoint, config))
        return self.socket


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(key_id="paper-key-123", secret_key="paper-secret-456")


def auth_ok() -> bytes:
    return json.dumps(
        {
            "stream": "authorization",
            "data": {"status": "authorized", "action": "authenticate"},
        }
    ).encode()


def listening_ok() -> bytes:
    return json.dumps(
        {"stream": "listening", "data": {"streams": ["trade_updates"]}}
    ).encode()


def enabled_config(**overrides) -> PaperTradeUpdatesConfig:
    values = {"enabled": True}
    values.update(overrides)
    return PaperTradeUpdatesConfig(**values)


def connected(socket=None):
    socket = socket or FakeSocket([auth_ok(), listening_ok()])
    connector = FakeConnector(socket)
    transport = AlpacaPaperTradeUpdatesTransport(
        enabled_config(),
        connector=connector,
    )
    session = transport.connect_and_listen(credentials=credentials())
    return transport, session, connector, socket


def test_disabled_default_rejects_before_connector_io() -> None:
    socket = FakeSocket([auth_ok(), listening_ok()])
    connector = FakeConnector(socket)
    transport = AlpacaPaperTradeUpdatesTransport(connector=connector)
    with pytest.raises(PaperTradeUpdatesDisabled, match="disabled by default"):
        transport.connect_and_listen(credentials=credentials())
    assert transport.state is PaperTradeUpdatesState.DISABLED
    assert connector.calls == []
    assert socket.sent == []


def test_config_rejects_live_or_any_nonexact_endpoint_before_io() -> None:
    with pytest.raises(ValueError, match="LIVE"):
        enabled_config(endpoint=ALPACA_LIVE_TRADE_UPDATES_URL)
    with pytest.raises(ValueError, match="exact"):
        enabled_config(endpoint="wss://paper-api.alpaca.markets/other")
    assert enabled_config().endpoint == ALPACA_PAPER_TRADE_UPDATES_URL


def test_handshake_sends_exactly_auth_then_trade_updates_listen() -> None:
    transport, session, connector, socket = connected()
    assert transport.state is PaperTradeUpdatesState.LISTENING
    assert session.state is PaperTradeUpdatesState.LISTENING
    assert len(connector.calls) == 1
    assert connector.calls[0][0] == ALPACA_PAPER_TRADE_UPDATES_URL
    assert len(socket.sent) == 2
    auth = json.loads(socket.sent[0])
    listen = json.loads(socket.sent[1])
    assert auth == {
        "action": "auth",
        "key": "paper-key-123",
        "secret": "paper-secret-456",
    }
    assert listen == {"action": "listen", "data": {"streams": ["trade_updates"]}}


def test_post_handshake_session_has_no_send_subscribe_or_reconnect_surface() -> None:
    _, session, _, _ = connected()
    forbidden = {"send", "subscribe", "listen", "auth", "reconnect", "connect"}
    assert not (forbidden & set(dir(session)))


def test_credentials_remain_redacted() -> None:
    creds = credentials()
    text = repr(creds)
    assert "paper-key-123" not in text
    assert "paper-secret-456" not in text
    assert "redacted" in text


@pytest.mark.parametrize(
    "first,second,reason",
    [
        (
            b'{"stream":"authorization","data":{"status":"unauthorized","action":"authenticate"}}',
            listening_ok(),
            "authorization rejected",
        ),
        (
            b'{"stream":"authorization","data":{"status":"authorized","action":"authenticate"}}',
            b'{"stream":"listening","data":{"streams":[]}}',
            "exact acknowledged",
        ),
        (
            b'{"action":"error","data":{"error_message":"bad"}}',
            listening_ok(),
            "server returned error",
        ),
    ],
)
def test_handshake_failure_is_sticky_degraded_and_closes_socket(first, second, reason) -> None:
    socket = FakeSocket([first, second])
    connector = FakeConnector(socket)
    transport = AlpacaPaperTradeUpdatesTransport(enabled_config(), connector=connector)
    with pytest.raises(PaperTradeUpdatesHandshakeError, match=reason):
        transport.connect_and_listen(credentials=credentials())
    assert transport.state is PaperTradeUpdatesState.DEGRADED
    assert socket.closed is True
    with pytest.raises(PaperTradeUpdatesDegraded):
        transport.connect_and_listen(credentials=credentials())
    assert len(connector.calls) == 1


def test_handshake_timeout_has_no_retry() -> None:
    socket = FakeSocket([])
    connector = FakeConnector(socket)
    transport = AlpacaPaperTradeUpdatesTransport(enabled_config(), connector=connector)
    with pytest.raises(PaperTradeUpdatesHandshakeError, match="timed out"):
        transport.connect_and_listen(credentials=credentials())
    assert len(connector.calls) == 1
    assert len(socket.sent) == 1
    assert socket.closed is True


def test_send_failure_has_no_retry_and_closes_socket() -> None:
    socket = FakeSocket([auth_ok(), listening_ok()], fail_send_at=2)
    connector = FakeConnector(socket)
    transport = AlpacaPaperTradeUpdatesTransport(enabled_config(), connector=connector)
    with pytest.raises(PaperTradeUpdatesHandshakeError):
        transport.connect_and_listen(credentials=credentials())
    assert len(connector.calls) == 1
    assert len(socket.sent) == 1
    assert socket.closed is True


def test_active_receive_requires_binary_frame_and_text_degrades_session() -> None:
    event = b'{"stream":"trade_updates","data":{"event":"new","order":{}}}'
    socket = FakeSocket([auth_ok(), listening_ok(), event])
    _, session, _, _ = connected(socket)
    assert session.receive(timeout_seconds=1) == event

    text_socket = FakeSocket([auth_ok(), listening_ok(), event.decode()])
    _, text_session, _, _ = connected(text_socket)
    with pytest.raises(PaperTradeUpdatesDegraded, match="binary"):
        text_session.receive(timeout_seconds=1)
    assert text_session.state is PaperTradeUpdatesState.DEGRADED
    with pytest.raises(PaperTradeUpdatesDegraded, match="sticky"):
        text_session.receive(timeout_seconds=1)


def test_idle_receive_timeout_is_not_synthetic_gap_or_degraded() -> None:
    socket = FakeSocket([auth_ok(), listening_ok()])
    _, session, _, _ = connected(socket)
    assert session.receive(timeout_seconds=1) is None
    assert session.state is PaperTradeUpdatesState.LISTENING


def test_close_is_explicit_terminal_state_and_idempotent() -> None:
    _, session, _, socket = connected()
    session.close()
    assert session.state is PaperTradeUpdatesState.CLOSED
    assert socket.closed is True
    session.close()
    with pytest.raises(PaperTradeUpdatesPolicyError, match="CLOSED"):
        session.receive(timeout_seconds=1)


def test_websockets_connector_rejects_nonpaper_endpoint_before_network() -> None:
    connector = WebsocketsPaperTradeUpdatesConnector()
    with pytest.raises(PaperTradeUpdatesPolicyError, match="before I/O"):
        connector.open(
            endpoint=ALPACA_LIVE_TRADE_UPDATES_URL,
            config=enabled_config(),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"open_timeout_seconds": 0},
        {"open_timeout_seconds": 16},
        {"handshake_timeout_seconds": 0},
        {"handshake_timeout_seconds": 16},
        {"max_message_bytes": 0},
        {"max_message_bytes": 1_048_577},
        {"max_queue_frames": 0},
        {"max_queue_frames": 17},
        {"ping_interval_seconds": 0},
        {"ping_timeout_seconds": 31},
        {"close_timeout_seconds": 0},
    ],
)
def test_transport_limits_are_bounded(overrides) -> None:
    with pytest.raises(ValueError):
        enabled_config(**overrides)
