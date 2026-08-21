from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Protocol

from autotrade.brokers.alpaca_paper_crypto_writer import (
    CRYPTO_ORDERS_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCryptoWriteResponse,
    HttpsAlpacaPaperCryptoWriteTransport,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.paper_exit_attempt import PaperExitStatus, SQLitePaperExitAttempt
from autotrade.paper_exit_final_guard import PaperExitWritePermit
from autotrade.paper_exit_order import PaperExitOrder


_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")


class PaperExitWriterError(RuntimeError):
    pass


class PaperExitWriterDisabled(PaperExitWriterError):
    pass


class PaperExitWriterBlocked(PaperExitWriterError):
    pass


class PaperExitWriterAmbiguous(PaperExitWriterError):
    """POST authority is burned; only GET reconciliation is permitted."""


class PaperExitWriteTransport(Protocol):
    def post(
        self,
        *,
        host: str,
        path: str,
        headers,
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> AlpacaPaperCryptoWriteResponse: ...


@dataclass(frozen=True, slots=True)
class PaperExitWriterConfig:
    enabled: bool = False
    host: str = ALPACA_PAPER_TRADING_HOST
    timeout_seconds: float = 5.0
    max_response_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if self.host != ALPACA_PAPER_TRADING_HOST:
            raise ValueError("R7 exit writer host must be exact Alpaca PAPER host")
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("R7 exit writer timeout is invalid")
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("R7 exit writer response limit is invalid")


@dataclass(frozen=True, slots=True)
class PaperExitWriteReceipt:
    attempt_id: str
    client_order_id: str
    request_id: str
    status_code: int
    broker_order_id: str | None
    broker_status: str | None
    response_sha256: str
    submitted_at: datetime
    retry_post: bool = False
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if not _REQUEST_ID_RE.fullmatch(self.request_id):
            raise ValueError("R7 exit receipt request id is invalid")
        if self.submitted_at.tzinfo is None or self.submitted_at.utcoffset() is None:
            raise ValueError("R7 exit receipt submitted_at must be timezone-aware")
        if self.retry_post is not False or self.live_trading != "BLOCKED":
            raise ValueError("R7 exit receipt cannot authorize retry or LIVE")


class PaperExitWriter:
    """Exactly-one POST R7 PAPER exit writer.

    The durable exit lifecycle crosses SUBMISSION_UNKNOWN before the transport
    call. Any response, rejection, malformed response, timeout or crash leaves
    the attempt with consumed authority. Completion is always determined later
    through GET-only broker reconciliation.
    """

    def __init__(
        self,
        *,
        config: PaperExitWriterConfig | None = None,
        transport: PaperExitWriteTransport | None = None,
    ) -> None:
        self._config = config or PaperExitWriterConfig()
        self._transport = transport or HttpsAlpacaPaperCryptoWriteTransport()

    def submit_once(
        self,
        *,
        lifecycle: SQLitePaperExitAttempt,
        order: PaperExitOrder,
        permit: PaperExitWritePermit,
        credentials: AlpacaPaperCredentials,
        now: datetime,
    ) -> PaperExitWriteReceipt:
        if not self._config.enabled:
            raise PaperExitWriterDisabled("R7 PAPER exit writer is disabled by default")
        if now.tzinfo is None or now.utcoffset() is None:
            raise PaperExitWriterBlocked("R7 exit submission time must be timezone-aware")
        instant = now.astimezone(timezone.utc)
        if not isinstance(lifecycle, SQLitePaperExitAttempt) or not isinstance(order, PaperExitOrder):
            raise PaperExitWriterBlocked("authoritative exit lifecycle and exact exit order are required")
        if not isinstance(permit, PaperExitWritePermit) or not isinstance(credentials, AlpacaPaperCredentials):
            raise PaperExitWriterBlocked("exact final permit and ephemeral PAPER credentials are required")
        if instant < permit.observed_at.astimezone(timezone.utc) or instant >= permit.expires_at.astimezone(timezone.utc):
            raise PaperExitWriterBlocked("R7 exit write permit is expired or not yet valid")
        if permit.write_authorized is not True or permit.retry_post is not False or permit.live_trading != "BLOCKED":
            raise PaperExitWriterBlocked("R7 exit permit authority invariants are invalid")
        if permit.attempt_id != order.attempt_id or permit.exit_order_hash != order.order_hash:
            raise PaperExitWriterBlocked("R7 exit permit/order binding mismatch")
        if permit.client_order_id != order.client_order_id or order.retry_post is not False or order.live_trading != "BLOCKED":
            raise PaperExitWriterBlocked("R7 exit order retry/client/LIVE binding mismatch")

        snapshot = lifecycle.snapshot(order.attempt_id)
        if snapshot.state.status is not PaperExitStatus.PREPARED or snapshot.state.attempt_count != 0:
            raise PaperExitWriterBlocked("R7 exit POST authority already consumed; use reconciliation only")
        if snapshot.state.control_hash != permit.lifecycle_control_hash or snapshot.state.event_head_hash != permit.lifecycle_event_head_hash:
            raise PaperExitWriterBlocked("R7 exit lifecycle changed after final guard")
        if snapshot.binding.order_hash != order.order_hash or snapshot.binding.payload_hash != order.payload_hash:
            raise PaperExitWriterBlocked("durable exit order differs from final broker payload")
        if snapshot.binding.credential_reference != credentials.credential_reference:
            raise PaperExitWriterBlocked("effective PAPER credential differs from prepared exit")

        unknown = lifecycle.mark_submission_unknown(order.attempt_id, at=instant)
        if unknown.status is not PaperExitStatus.SUBMISSION_UNKNOWN or unknown.attempt_count != 1:
            raise PaperExitWriterAmbiguous("failed to durably burn R7 exit POST authority")

        body = json.dumps(order.to_broker_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AUTO-TRADE-R7/0.1",
            "APCA-API-KEY-ID": credentials.key_id,
            "APCA-API-SECRET-KEY": credentials.secret_key,
        }
        try:
            response = self._transport.post(
                host=self._config.host,
                path=CRYPTO_ORDERS_PATH,
                headers=headers,
                body=body,
                timeout_seconds=self._config.timeout_seconds,
                max_response_bytes=self._config.max_response_bytes,
            )
        except Exception as exc:
            raise PaperExitWriterAmbiguous(
                "R7 exit POST outcome is ambiguous; retry is forbidden and GET reconciliation is mandatory"
            ) from exc

        try:
            return _receipt(order=order, response=response, submitted_at=instant)
        except Exception as exc:
            raise PaperExitWriterAmbiguous(
                "R7 exit POST response is not trustworthy; retry is forbidden and GET reconciliation is mandatory"
            ) from exc


def _receipt(*, order: PaperExitOrder, response: AlpacaPaperCryptoWriteResponse, submitted_at: datetime) -> PaperExitWriteReceipt:
    request_id = str(response.headers.get("x-request-id", "")).strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("R7 exit POST response lacks valid X-Request-ID")
    broker_order_id: str | None = None
    broker_status: str | None = None
    content_type = str(response.headers.get("content-type", "")).split(";", 1)[0].strip().lower()
    if content_type == "application/json" and response.body:
        raw = json.loads(response.body.decode("utf-8", errors="strict"))
        if not isinstance(raw, dict):
            raise ValueError("R7 exit POST JSON response root must be object")
        raw_client = raw.get("client_order_id")
        if raw_client is not None and str(raw_client) != order.client_order_id:
            raise ValueError("R7 exit POST response client_order_id mismatch")
        if raw.get("id") is not None:
            broker_order_id = str(raw["id"])
        if raw.get("status") is not None:
            broker_status = str(raw["status"]).strip().lower()
    return PaperExitWriteReceipt(
        attempt_id=order.attempt_id,
        client_order_id=order.client_order_id,
        request_id=request_id,
        status_code=int(response.status_code),
        broker_order_id=broker_order_id,
        broker_status=broker_status,
        response_sha256=sha256(response.body).hexdigest(),
        submitted_at=submitted_at,
    )


__all__ = [
    "PaperExitWriteReceipt",
    "PaperExitWriteTransport",
    "PaperExitWriter",
    "PaperExitWriterAmbiguous",
    "PaperExitWriterBlocked",
    "PaperExitWriterConfig",
    "PaperExitWriterDisabled",
    "PaperExitWriterError",
]
