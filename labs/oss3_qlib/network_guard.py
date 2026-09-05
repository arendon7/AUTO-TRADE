"""Fail-closed network guard for the OSS-3D2B isolated process."""

from __future__ import annotations

from contextlib import contextmanager
import socket
from typing import Iterator


class QlibLabNetworkDenied(RuntimeError):
    """Raised when the isolated research runtime attempts network access."""


def _blocked(*_args: object, **_kwargs: object) -> object:
    raise QlibLabNetworkDenied("OSS-3D2B runtime network access is forbidden")


@contextmanager
def deny_network() -> Iterator[None]:
    """Block common socket connection and DNS entry points for the lab scope."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo
    socket.socket.connect = _blocked  # type: ignore[method-assign]
    socket.socket.connect_ex = _blocked  # type: ignore[method-assign]
    socket.create_connection = _blocked  # type: ignore[assignment]
    socket.getaddrinfo = _blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
        socket.create_connection = original_create_connection  # type: ignore[assignment]
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]
