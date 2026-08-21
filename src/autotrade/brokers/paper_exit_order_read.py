from __future__ import annotations

from dataclasses import dataclass
import http.client
import re
from typing import Mapping

from .alpaca_paper_gateway import ALPACA_PAPER_TRADING_HOST


EXIT_ORDER_BY_CLIENT_PATH = "/v2/orders:by_client_order_id"
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PaperExitOrderReadError(RuntimeError):
    pass


class PaperExitOrderReadPolicyError(PaperExitOrderReadError):
    pass


class PaperExitOrderReadUnavailable(PaperExitOrderReadError):
    pass


@dataclass(frozen=True, slots=True)
class PaperExitOrderReadResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PaperExitOrderReadConfig:
    host: str = ALPACA_PAPER_TRADING_HOST
    timeout_seconds: float = 5.0
    max_response_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if self.host != ALPACA_PAPER_TRADING_HOST:
            raise PaperExitOrderReadPolicyError("R7 exit order read host must be exact Alpaca PAPER host")
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("R7 exit order read timeout must be >0 and <=15")
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("R7 exit order read response limit is invalid")


class HttpsPaperExitOrderReadTransport:
    """One exact GET by client_order_id; preserves HTTP 404 as broker evidence."""

    def __init__(self, *, config: PaperExitOrderReadConfig | None = None) -> None:
        self._config = config or PaperExitOrderReadConfig()

    def read(self, *, client_order_id: str, headers: Mapping[str, str]) -> PaperExitOrderReadResponse:
        if not isinstance(client_order_id, str) or not _CLIENT_ID_RE.fullmatch(client_order_id):
            raise PaperExitOrderReadPolicyError("R7 exit client_order_id is invalid")
        expected_headers = {
            "accept",
            "user-agent",
            "apca-api-key-id",
            "apca-api-secret-key",
        }
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        if set(normalized) != expected_headers:
            raise PaperExitOrderReadPolicyError("R7 exit order GET headers must match exact allowlist")
        if normalized["accept"] != "application/json" or normalized["user-agent"] != "AUTO-TRADE-R7/0.1":
            raise PaperExitOrderReadPolicyError("R7 exit order GET headers are non-canonical")
        for name in ("apca-api-key-id", "apca-api-secret-key"):
            value = normalized[name]
            if not value or value != value.strip() or len(value) > 512:
                raise PaperExitOrderReadPolicyError("R7 exit order GET credential header is invalid")
            if any(ord(char) < 33 or ord(char) == 127 for char in value):
                raise PaperExitOrderReadPolicyError("R7 exit order GET credential header contains control data")

        path = f"{EXIT_ORDER_BY_CLIENT_PATH}?client_order_id={client_order_id}"
        connection = http.client.HTTPSConnection(self._config.host, timeout=self._config.timeout_seconds)
        try:
            connection.request("GET", path, headers=dict(headers))
            response = connection.getresponse()
            body = response.read(self._config.max_response_bytes + 1)
            if len(body) > self._config.max_response_bytes:
                raise PaperExitOrderReadUnavailable("R7 exit order GET response exceeds size limit")
            return PaperExitOrderReadResponse(
                status_code=int(response.status),
                body=body,
                headers={str(key).lower(): str(value).strip() for key, value in response.getheaders()},
            )
        except PaperExitOrderReadError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise PaperExitOrderReadUnavailable("R7 exit order GET failed") from exc
        finally:
            connection.close()


__all__ = [
    "EXIT_ORDER_BY_CLIENT_PATH",
    "HttpsPaperExitOrderReadTransport",
    "PaperExitOrderReadConfig",
    "PaperExitOrderReadError",
    "PaperExitOrderReadPolicyError",
    "PaperExitOrderReadResponse",
    "PaperExitOrderReadUnavailable",
]
