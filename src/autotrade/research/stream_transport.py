from __future__ import annotations

from dataclasses import dataclass

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import ClientConnection, connect as websocket_connect

from .streaming import (
    ClosedKlineStream,
    ClosedKlineSubscription,
    ReadOnlyStreamSession,
    ReadOnlyStreamTransport,
    StreamOpenRequest,
)


@dataclass(frozen=True, slots=True)
class WebsocketsTransportLimits:
    max_message_bytes: int = 64 * 1024
    max_queue_frames: int = 1
    ping_interval_seconds: float = 20.0
    ping_timeout_seconds: float = 10.0
    close_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
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


class WebsocketsReadOnlySession(ReadOnlyStreamSession):
    __slots__ = ("_connection",)

    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection

    def receive(self, *, timeout_seconds: float) -> str | bytes | None:
        try:
            return self._connection.recv(timeout=timeout_seconds)
        except TimeoutError:
            raise
        except ConnectionClosed:
            return None
        except (OSError, WebSocketException) as exc:
            raise OSError("WebSocket receive failed") from exc

    def close(self) -> None:
        try:
            self._connection.close()
        except ConnectionClosed:
            return
        except (OSError, WebSocketException) as exc:
            raise OSError("WebSocket close failed") from exc


class WebsocketsReadOnlyTransport(ReadOnlyStreamTransport):
    """Bounded client transport for public market-data WebSockets only.

    Endpoint authority remains in ClosedKlineStreamPolicy, which validates the
    exact WSS host and path before this transport is called. Proxy discovery is
    explicitly disabled here so environment configuration cannot reroute the
    connection through an unreviewed proxy. Compression is disabled and both
    message size and receive queue are bounded.
    """

    def __init__(self, *, limits: WebsocketsTransportLimits | None = None) -> None:
        self._limits = limits or WebsocketsTransportLimits()

    @property
    def limits(self) -> WebsocketsTransportLimits:
        return self._limits

    def open(self, request: StreamOpenRequest) -> WebsocketsReadOnlySession:
        if not request.read_only:
            raise ValueError("WebSocket transport is read-only")
        if not 0 < request.timeout_seconds <= 30:
            raise ValueError("WebSocket open timeout must be > 0 and <= 30")
        try:
            connection = websocket_connect(
                request.url,
                proxy=None,
                compression=None,
                open_timeout=request.timeout_seconds,
                ping_interval=self._limits.ping_interval_seconds,
                ping_timeout=self._limits.ping_timeout_seconds,
                close_timeout=self._limits.close_timeout_seconds,
                max_size=self._limits.max_message_bytes,
                max_queue=self._limits.max_queue_frames,
                user_agent_header="AUTO-TRADE-R5/0.28R",
            )
        except TimeoutError:
            raise
        except (OSError, WebSocketException) as exc:
            raise OSError("WebSocket open failed") from exc
        return WebsocketsReadOnlySession(connection)


def build_binance_closed_kline_stream(
    subscription: ClosedKlineSubscription,
    *,
    limits: WebsocketsTransportLimits | None = None,
) -> ClosedKlineStream:
    """Build a closed-kline stream; construction itself performs no I/O."""

    return ClosedKlineStream(
        subscription=subscription,
        transport=WebsocketsReadOnlyTransport(limits=limits),
    )
