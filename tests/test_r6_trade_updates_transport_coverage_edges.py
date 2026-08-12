from __future__ import annotations

import json

import pytest

import autotrade.brokers.alpaca_paper_trade_updates_transport as module
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_trade_updates_transport import (
    ALPACA_PAPER_TRADE_UPDATES_URL,
    AlpacaPaperTradeUpdatesTransport,
    PaperTradeUpdatesConfig,
    PaperTradeUpdatesDegraded,
    PaperTradeUpdatesHandshakeError,
    PaperTradeUpdatesPolicyError,
    PaperTradeUpdatesSession,
    PaperTradeUpdatesState,
    WebsocketsPaperTradeUpdatesConnector,
)
from test_r6_paper_trade_updates_transport import (
    FakeConnector,
    FakeSocket,
    auth_ok,
    credentials,
    enabled_config,
    listening_ok,
)


class CloseFailSocket(FakeSocket):
    def close(self) -> None:
        raise OSError("synthetic close failure")


class OpenFailureConnector:
    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    def open(self, *, endpoint, config):
        del endpoint, config
        self.calls += 1
        raise self.exc


@pytest.mark.parametrize("timeout", [0, -1, 61])
def test_receive_timeout_bound_is_enforced(timeout) -> None:
    session = PaperTradeUpdatesSession(FakeSocket())
    with pytest.raises(ValueError, match="timeout_seconds"):
        session.receive(timeout_seconds=timeout)


def test_receive_oserror_sets_sticky_degraded() -> None:
    socket = FakeSocket([OSError("synthetic receive failure")])
    session = PaperTradeUpdatesSession(socket)
    with pytest.raises(PaperTradeUpdatesDegraded, match="receive failed"):
        session.receive(timeout_seconds=1)
    assert session.state is PaperTradeUpdatesState.DEGRADED
    with pytest.raises(PaperTradeUpdatesDegraded, match="sticky"):
        session.receive(timeout_seconds=1)


def test_close_oserror_sets_degraded_and_fails_closed() -> None:
    session = PaperTradeUpdatesSession(CloseFailSocket())
    with pytest.raises(PaperTradeUpdatesDegraded, match="close failed"):
        session.close()
    assert session.state is PaperTradeUpdatesState.DEGRADED


def test_transport_blocks_second_connect_while_already_listening() -> None:
    socket = FakeSocket([auth_ok(), listening_ok()])
    connector = FakeConnector(socket)
    transport = AlpacaPaperTradeUpdatesTransport(enabled_config(), connector=connector)
    transport.connect_and_listen(credentials=credentials())
    with pytest.raises(PaperTradeUpdatesPolicyError, match="LISTENING"):
        transport.connect_and_listen(credentials=credentials())
    assert len(connector.calls) == 1


def test_transport_open_failure_without_socket_is_degraded_and_not_retried() -> None:
    connector = OpenFailureConnector(OSError("open failed"))
    transport = AlpacaPaperTradeUpdatesTransport(enabled_config(), connector=connector)
    with pytest.raises(PaperTradeUpdatesHandshakeError, match="transport failed"):
        transport.connect_and_listen(credentials=credentials())
    assert transport.state is PaperTradeUpdatesState.DEGRADED
    assert connector.calls == 1


def test_transport_unexpected_connector_exception_is_wrapped_and_no_retry() -> None:
    connector = OpenFailureConnector(RuntimeError("unexpected connector bug"))
    transport = AlpacaPaperTradeUpdatesTransport(enabled_config(), connector=connector)
    with pytest.raises(PaperTradeUpdatesHandshakeError, match="handshake failed"):
        transport.connect_and_listen(credentials=credentials())
    assert transport.state is PaperTradeUpdatesState.DEGRADED
    assert connector.calls == 1


def test_cleanup_failure_during_handshake_does_not_hide_original_error() -> None:
    socket = CloseFailSocket([b'{"stream":"authorization","data":{}}'])
    connector = FakeConnector(socket)
    transport = AlpacaPaperTradeUpdatesTransport(enabled_config(), connector=connector)
    with pytest.raises(PaperTradeUpdatesHandshakeError, match="authorization rejected"):
        transport.connect_and_listen(credentials=credentials())
    assert transport.state is PaperTradeUpdatesState.DEGRADED


def test_real_connector_parameters_are_exact_and_network_is_monkeypatched(monkeypatch) -> None:
    captured = {}
    sentinel = object()

    def fake_connect(endpoint, **kwargs):
        captured["endpoint"] = endpoint
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(module, "websocket_connect", fake_connect)
    config = enabled_config(
        open_timeout_seconds=3,
        handshake_timeout_seconds=4,
        max_message_bytes=123456,
        max_queue_frames=3,
        ping_interval_seconds=11,
        ping_timeout_seconds=7,
        close_timeout_seconds=2,
    )
    returned = WebsocketsPaperTradeUpdatesConnector().open(
        endpoint=ALPACA_PAPER_TRADE_UPDATES_URL,
        config=config,
    )
    assert returned is sentinel
    assert captured == {
        "endpoint": ALPACA_PAPER_TRADE_UPDATES_URL,
        "proxy": None,
        "compression": None,
        "open_timeout": 3,
        "ping_interval": 11,
        "ping_timeout": 7,
        "close_timeout": 2,
        "max_size": 123456,
        "max_queue": 3,
        "user_agent_header": "AUTO-TRADE-R6/0.28R",
    }


@pytest.mark.parametrize(
    "exc,reason",
    [
        (TimeoutError(), "open timed out"),
        (OSError("network"), "open failed"),
    ],
)
def test_real_connector_wraps_bounded_open_failures(monkeypatch, exc, reason) -> None:
    def fake_connect(*args, **kwargs):
        del args, kwargs
        raise exc

    monkeypatch.setattr(module, "websocket_connect", fake_connect)
    with pytest.raises(PaperTradeUpdatesHandshakeError, match=reason):
        WebsocketsPaperTradeUpdatesConnector().open(
            endpoint=ALPACA_PAPER_TRADE_UPDATES_URL,
            config=enabled_config(),
        )


@pytest.mark.parametrize(
    "frame,reason",
    [
        (b"\xff", "not UTF-8"),
        (42, "text or bytes"),
        ("[]", "root must be object"),
        ('{"x":NaN}', "strict JSON"),
        ("x" * (64 * 1024 + 1), "size limit"),
    ],
)
def test_control_frame_shape_rejections_are_strict(frame, reason) -> None:
    socket = FakeSocket([frame])
    transport = AlpacaPaperTradeUpdatesTransport(
        enabled_config(),
        connector=FakeConnector(socket),
    )
    with pytest.raises(PaperTradeUpdatesHandshakeError, match=reason):
        transport.connect_and_listen(credentials=credentials())


def test_authorization_stream_and_data_must_be_exact() -> None:
    for frame, reason in (
        (b'{"stream":"wrong","data":{}}', "stream mismatch"),
        (b'{"stream":"authorization","data":[]}', "data missing"),
    ):
        transport = AlpacaPaperTradeUpdatesTransport(
            enabled_config(),
            connector=FakeConnector(FakeSocket([frame])),
        )
        with pytest.raises(PaperTradeUpdatesHandshakeError, match=reason):
            transport.connect_and_listen(credentials=credentials())


def test_listening_stream_and_data_must_be_exact() -> None:
    frames = [
        (b'{"stream":"wrong","data":{"streams":["trade_updates"]}}', "stream mismatch"),
        (b'{"stream":"listening","data":[]}', "data missing"),
    ]
    for second, reason in frames:
        transport = AlpacaPaperTradeUpdatesTransport(
            enabled_config(),
            connector=FakeConnector(FakeSocket([auth_ok(), second])),
        )
        with pytest.raises(PaperTradeUpdatesHandshakeError, match=reason):
            transport.connect_and_listen(credentials=credentials())


def test_closed_transport_can_open_a_new_explicit_session_without_auto_reconnect() -> None:
    first_socket = FakeSocket([auth_ok(), listening_ok()])
    first_connector = FakeConnector(first_socket)
    transport = AlpacaPaperTradeUpdatesTransport(enabled_config(), connector=first_connector)
    first = transport.connect_and_listen(credentials=credentials())
    first.close()
    assert first.state is PaperTradeUpdatesState.CLOSED

    # Transport state represents its own last handshake, not the returned session.
    # A caller must create a new transport/connector; no automatic reconnect exists.
    assert transport.state is PaperTradeUpdatesState.LISTENING
    assert "reconnect" not in dir(transport)
