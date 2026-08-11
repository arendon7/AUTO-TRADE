from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Protocol

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import ClientConnection, connect as websocket_connect

from .alpaca_paper_gateway import AlpacaPaperCredentials


ALPACA_PAPER_TRADE_UPDATES_URL = "wss://paper-api.alpaca.markets/stream"
ALPACA_LIVE_TRADE_UPDATES_URL = "wss://api.alpaca.markets/stream"
_TRADE_UPDATES_STREAM = "trade_updates"


class PaperTradeUpdatesTransportError(RuntimeError):
    pass


class PaperTradeUpdatesDisabled(PaperTradeUpdatesTransportError):
    pass


class PaperTradeUpdatesPolicyError(PaperTradeUpdatesTransportError):
    pass


class PaperTradeUpdatesHandshakeError(PaperTradeUpdatesTransportError):
    pass


class PaperTradeUpdatesDegraded(PaperTradeUpdatesTransportError):
    pass


class PaperTradeUpdatesState(StrEnum):
    DISABLED = "DISABLED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    SUBSCRIBING = "SUBSCRIBING"
    LISTENING = "LISTENING"
    DEGRADED = "DEGRADED"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class PaperTradeUpdatesConfig:
    enabled: bool = False
    endpoint: str = ALPACA_PAPER_TRADE_UPDATES_URL
    open_timeout_seconds: float = 5.0
    handshake_timeout_seconds: float = 5.0
    max_message_bytes: int = 256 * 1024
    max_queue_frames: int = 4
    ping_interval_seconds: float = 20.0
    ping_timeout_seconds: float = 10.0
    close_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.endpoint != ALPACA_PAPER_TRADE_UPDATES_URL:
            if self.endpoint == ALPACA_LIVE_TRADE_UPDATES_URL:
                raise ValueError("LIVE trade_updates endpoint is forbidden")
            raise ValueError("trade_updates endpoint must be exact Alpaca PAPER WSS endpoint")
        if not 0 < self.open_timeout_seconds <= 15:
            raise ValueError("open_timeout_seconds must be > 0 and <= 15")
        if not 0 < self.handshake_timeout_seconds <= 15:
            raise ValueError("handshake_timeout_seconds must be > 0 and <= 15")
        if not 1 <= self.max_message_bytes <= 1_048_576:
            raise ValueError("max_message_bytes must be between 1 and 1048576")
        if not 1 <= self.max_queue_frames <= 16:
            raise ValueError("max_queue_frames must be between 1 and 16")
        if not 1 <= self.ping_interval_seconds <= 60:
            raise ValueError("ping_interval_seconds must be between 1 and 60")
        if not 1 <= self.ping_timeout_seconds <= 30:
            raise ValueError("ping_timeout_seconds must be between 1 and 30")
        if not 0 < self.close_timeout_seconds <= 10:
            raise ValueError("close_timeout_seconds must be > 0 and <= 10")


class PaperTradeUpdatesSocket(Protocol):
    def send(self, message: str) -> None: ...
    def recv(self, timeout: float | None = None) -> str | bytes: ...
    def close(self) -> None: ...


class PaperTradeUpdatesConnector(Protocol):
    def open(
        self,
        *,
        endpoint: str,
        config: PaperTradeUpdatesConfig,
    ) -> PaperTradeUpdatesSocket: ...


class WebsocketsPaperTradeUpdatesConnector:
    """Exact PAPER WSS connector; construction performs no I/O."""

    def open(
        self,
        *,
        endpoint: str,
        config: PaperTradeUpdatesConfig,
    ) -> PaperTradeUpdatesSocket:
        if endpoint != ALPACA_PAPER_TRADE_UPDATES_URL:
            raise PaperTradeUpdatesPolicyError("PAPER trade_updates endpoint mismatch before I/O")
        try:
            return websocket_connect(
                endpoint,
                proxy=None,
                compression=None,
                open_timeout=config.open_timeout_seconds,
                ping_interval=config.ping_interval_seconds,
                ping_timeout=config.ping_timeout_seconds,
                close_timeout=config.close_timeout_seconds,
                max_size=config.max_message_bytes,
                max_queue=config.max_queue_frames,
                user_agent_header="AUTO-TRADE-R6/0.28R",
            )
        except TimeoutError as exc:
            raise PaperTradeUpdatesHandshakeError("PAPER trade_updates WSS open timed out") from exc
        except (OSError, WebSocketException) as exc:
            raise PaperTradeUpdatesHandshakeError("PAPER trade_updates WSS open failed") from exc


class PaperTradeUpdatesSession:
    """Post-handshake receive-only application surface.

    Authentication and listen control frames are sent internally before this
    object is returned. The session intentionally exposes no send/subscribe or
    reconnect method. Unexpected connection loss becomes sticky DEGRADED.
    """

    __slots__ = ("_socket", "_state")

    def __init__(self, socket: PaperTradeUpdatesSocket) -> None:
        self._socket = socket
        self._state = PaperTradeUpdatesState.LISTENING

    @property
    def state(self) -> PaperTradeUpdatesState:
        return self._state

    def receive(self, *, timeout_seconds: float) -> bytes | None:
        if self._state is PaperTradeUpdatesState.DEGRADED:
            raise PaperTradeUpdatesDegraded("PAPER trade_updates session is sticky DEGRADED")
        if self._state is not PaperTradeUpdatesState.LISTENING:
            raise PaperTradeUpdatesPolicyError(
                f"PAPER trade_updates receive blocked from {self._state.value}"
            )
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be > 0 and <= 60")
        try:
            frame = self._socket.recv(timeout=timeout_seconds)
        except TimeoutError:
            # An idle account stream can legitimately be quiet. Timeout is a
            # polling result, not evidence of a continuity gap.
            return None
        except ConnectionClosed as exc:
            self._state = PaperTradeUpdatesState.DEGRADED
            raise PaperTradeUpdatesDegraded("PAPER trade_updates socket closed unexpectedly") from exc
        except (OSError, WebSocketException) as exc:
            self._state = PaperTradeUpdatesState.DEGRADED
            raise PaperTradeUpdatesDegraded("PAPER trade_updates receive failed") from exc
        if not isinstance(frame, bytes):
            self._state = PaperTradeUpdatesState.DEGRADED
            raise PaperTradeUpdatesDegraded(
                "PAPER trade_updates expected binary frame; text frame is fail-closed"
            )
        return frame

    def close(self) -> None:
        if self._state is PaperTradeUpdatesState.CLOSED:
            return
        try:
            self._socket.close()
        except ConnectionClosed:
            pass
        except (OSError, WebSocketException) as exc:
            self._state = PaperTradeUpdatesState.DEGRADED
            raise PaperTradeUpdatesDegraded("PAPER trade_updates close failed") from exc
        self._state = PaperTradeUpdatesState.CLOSED


class AlpacaPaperTradeUpdatesTransport:
    """Disabled-by-default PAPER order-event control stream.

    The only outbound application frames are one exact auth frame followed by
    one exact listen frame for trade_updates. No order command or generic send
    surface exists. No reconnect loop exists.
    """

    def __init__(
        self,
        config: PaperTradeUpdatesConfig | None = None,
        *,
        connector: PaperTradeUpdatesConnector | None = None,
    ) -> None:
        self._config = config or PaperTradeUpdatesConfig()
        self._connector = connector or WebsocketsPaperTradeUpdatesConnector()
        self._state = PaperTradeUpdatesState.DISABLED

    @property
    def state(self) -> PaperTradeUpdatesState:
        return self._state

    def connect_and_listen(
        self,
        *,
        credentials: AlpacaPaperCredentials,
    ) -> PaperTradeUpdatesSession:
        if not self._config.enabled:
            raise PaperTradeUpdatesDisabled("PAPER trade_updates is disabled by default")
        if self._state is PaperTradeUpdatesState.DEGRADED:
            raise PaperTradeUpdatesDegraded("PAPER trade_updates transport is sticky DEGRADED")
        if self._state not in {PaperTradeUpdatesState.DISABLED, PaperTradeUpdatesState.CLOSED}:
            raise PaperTradeUpdatesPolicyError(
                f"PAPER trade_updates connect blocked from {self._state.value}"
            )
        if self._config.endpoint != ALPACA_PAPER_TRADE_UPDATES_URL:
            raise PaperTradeUpdatesPolicyError("PAPER trade_updates endpoint mismatch before I/O")

        self._state = PaperTradeUpdatesState.CONNECTING
        socket: PaperTradeUpdatesSocket | None = None
        try:
            socket = self._connector.open(
                endpoint=self._config.endpoint,
                config=self._config,
            )
            self._state = PaperTradeUpdatesState.AUTHENTICATING
            socket.send(_auth_message(credentials))
            authorization = socket.recv(timeout=self._config.handshake_timeout_seconds)
            _validate_authorization_frame(authorization)

            self._state = PaperTradeUpdatesState.SUBSCRIBING
            socket.send(_listen_message())
            listening = socket.recv(timeout=self._config.handshake_timeout_seconds)
            _validate_listening_frame(listening)

            self._state = PaperTradeUpdatesState.LISTENING
            return PaperTradeUpdatesSession(socket)
        except Exception as exc:
            self._state = PaperTradeUpdatesState.DEGRADED
            if socket is not None:
                try:
                    socket.close()
                except Exception:
                    pass
            if isinstance(exc, PaperTradeUpdatesTransportError):
                raise
            if isinstance(exc, TimeoutError):
                raise PaperTradeUpdatesHandshakeError(
                    "PAPER trade_updates handshake timed out"
                ) from exc
            if isinstance(exc, ConnectionClosed):
                raise PaperTradeUpdatesHandshakeError(
                    "PAPER trade_updates socket closed during handshake"
                ) from exc
            if isinstance(exc, (OSError, WebSocketException)):
                raise PaperTradeUpdatesHandshakeError(
                    "PAPER trade_updates handshake transport failed"
                ) from exc
            raise PaperTradeUpdatesHandshakeError(
                "PAPER trade_updates handshake failed"
            ) from exc


def _auth_message(credentials: AlpacaPaperCredentials) -> str:
    return json.dumps(
        {
            "action": "auth",
            "key": credentials.key_id,
            "secret": credentials.secret_key,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _listen_message() -> str:
    return json.dumps(
        {"action": "listen", "data": {"streams": [_TRADE_UPDATES_STREAM]}},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _validate_authorization_frame(frame: str | bytes) -> None:
    payload = _control_object(frame)
    if payload.get("stream") != "authorization":
        raise PaperTradeUpdatesHandshakeError("authorization acknowledgement stream mismatch")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PaperTradeUpdatesHandshakeError("authorization acknowledgement data missing")
    if data.get("status") != "authorized" or data.get("action") != "authenticate":
        raise PaperTradeUpdatesHandshakeError("PAPER trade_updates authorization rejected")


def _validate_listening_frame(frame: str | bytes) -> None:
    payload = _control_object(frame)
    if payload.get("stream") != "listening":
        raise PaperTradeUpdatesHandshakeError("listening acknowledgement stream mismatch")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PaperTradeUpdatesHandshakeError("listening acknowledgement data missing")
    streams = data.get("streams")
    if streams != [_TRADE_UPDATES_STREAM]:
        raise PaperTradeUpdatesHandshakeError(
            "trade_updates was not the exact acknowledged listening stream"
        )


def _control_object(frame: str | bytes) -> dict[str, object]:
    if isinstance(frame, bytes):
        try:
            text = frame.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PaperTradeUpdatesHandshakeError("control frame is not UTF-8") from exc
    elif isinstance(frame, str):
        text = frame
    else:
        raise PaperTradeUpdatesHandshakeError("control frame must be text or bytes")
    if len(text.encode("utf-8")) > 64 * 1024:
        raise PaperTradeUpdatesHandshakeError("control frame exceeded size limit")
    try:
        payload = json.loads(
            text,
            parse_constant=lambda token: _reject_json_constant(token),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise PaperTradeUpdatesHandshakeError("control frame is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise PaperTradeUpdatesHandshakeError("control frame root must be object")
    if payload.get("action") == "error":
        raise PaperTradeUpdatesHandshakeError("PAPER trade_updates server returned error action")
    return payload


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")
