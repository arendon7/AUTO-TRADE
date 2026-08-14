from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import http.client
import json
import re
from typing import Mapping, Protocol

from .alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBlocked,
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from .alpaca_paper_crypto_order import (
    AlpacaPaperCryptoOrderRequest,
    CryptoOrderRole,
)
from .alpaca_paper_gateway import ALPACA_PAPER_TRADING_HOST, AlpacaPaperCredentials


CRYPTO_ORDERS_PATH = "/v2/orders"
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class CryptoPaperWriterError(RuntimeError):
    pass


class CryptoPaperWriterDisabled(CryptoPaperWriterError):
    pass


class CryptoPaperWriterPolicyError(CryptoPaperWriterError):
    pass


class CryptoPaperWriterAmbiguous(CryptoPaperWriterError):
    """The durable lifecycle is UNKNOWN and reconciliation is mandatory."""


class CryptoPaperWriterIntegrityError(CryptoPaperWriterError):
    pass


@dataclass(frozen=True, slots=True)
class AlpacaPaperCryptoWriterConfig:
    enabled: bool = False
    host: str = ALPACA_PAPER_TRADING_HOST
    timeout_seconds: float = 5.0
    max_response_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if self.host != ALPACA_PAPER_TRADING_HOST:
            raise CryptoPaperWriterPolicyError("crypto writer host must be exact Alpaca PAPER host")
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("crypto writer timeout must be >0 and <=15 seconds")
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("crypto writer response limit is invalid")


@dataclass(frozen=True, slots=True)
class AlpacaPaperCryptoWriteResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class AlpacaPaperCryptoWriteTransport(Protocol):
    def post(
        self,
        *,
        host: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> AlpacaPaperCryptoWriteResponse:
        ...


class GuardedAlpacaPaperCryptoWriteTransport:
    """Nominal capability reserved for role-bound Final-Guard transports."""

    role: CryptoOrderRole


class HttpsAlpacaPaperCryptoWriteTransport:
    """Narrow TLS delegate: one HTTPS POST to the exact PAPER host/path.

    This raw transport is intentionally not a guarded capability. An enabled
    writer can only receive it behind a role-specific Final-Guard transport.
    """

    def post(
        self,
        *,
        host: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> AlpacaPaperCryptoWriteResponse:
        if host != ALPACA_PAPER_TRADING_HOST or path != CRYPTO_ORDERS_PATH:
            raise CryptoPaperWriterPolicyError("crypto transport destination is not exact PAPER orders endpoint")
        connection = http.client.HTTPSConnection(host, timeout=timeout_seconds)
        try:
            connection.request("POST", path, body=body, headers=dict(headers))
            response = connection.getresponse()
            response_body = response.read(max_response_bytes + 1)
            if len(response_body) > max_response_bytes:
                raise CryptoPaperWriterIntegrityError("crypto writer response exceeds configured limit")
            normalized_headers = {str(key).lower(): str(value).strip() for key, value in response.getheaders()}
            return AlpacaPaperCryptoWriteResponse(
                status_code=int(response.status),
                body=response_body,
                headers=normalized_headers,
            )
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class CryptoPaperWriteReceipt:
    lifecycle_id: str
    role: CryptoOrderRole
    request_fingerprint: str
    request_payload_sha256: str
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    time_in_force: str
    broker_status: str
    requested_quantity: Decimal
    broker_filled_quantity: Decimal
    request_id: str
    response_sha256: str
    submitted_at: datetime

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.lifecycle_id):
            raise ValueError("receipt lifecycle_id is invalid")
        if not _ID_RE.fullmatch(self.broker_order_id):
            raise ValueError("receipt broker_order_id is invalid")
        if not _ID_RE.fullmatch(self.client_order_id):
            raise ValueError("receipt client_order_id is invalid")
        if not _REQUEST_ID_RE.fullmatch(self.request_id):
            raise ValueError("receipt request_id is invalid")
        if self.submitted_at.tzinfo is None or self.submitted_at.utcoffset() is None:
            raise ValueError("receipt submitted_at must be timezone-aware")

    @property
    def fingerprint(self) -> str:
        payload = {
            "lifecycle_id": self.lifecycle_id,
            "role": self.role.value,
            "request_fingerprint": self.request_fingerprint,
            "request_payload_sha256": self.request_payload_sha256,
            "broker_order_id": self.broker_order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "broker_status": self.broker_status,
            "requested_quantity": _decimal_text(self.requested_quantity),
            "broker_filled_quantity": _decimal_text(self.broker_filled_quantity),
            "request_id": self.request_id,
            "response_sha256": self.response_sha256,
            "submitted_at": self.submitted_at.astimezone(timezone.utc).isoformat(),
        }
        return sha256(_canonical(payload).encode("utf-8")).hexdigest()


class AlpacaPaperCryptoWriter:
    """One-shot crypto PAPER writer behind a role-specific Final Guard.

    It owns the durable transition to *_SUBMISSION_UNKNOWN immediately before
    the guarded transport call. A second call after UNKNOWN is rejected;
    ambiguity is recovered only through broker reconciliation, never by
    repeating the POST.
    """

    def __init__(
        self,
        *,
        config: AlpacaPaperCryptoWriterConfig | None = None,
        transport: AlpacaPaperCryptoWriteTransport | None = None,
    ) -> None:
        self._config = config or AlpacaPaperCryptoWriterConfig()
        self._transport = transport or HttpsAlpacaPaperCryptoWriteTransport()

    def submit_once(
        self,
        *,
        lifecycle: SQLiteCryptoPaperLifecycle,
        lifecycle_id: str,
        order: AlpacaPaperCryptoOrderRequest,
        credentials: AlpacaPaperCredentials,
        now: datetime,
    ) -> CryptoPaperWriteReceipt:
        if not self._config.enabled:
            raise CryptoPaperWriterDisabled("crypto PAPER writer is disabled")
        if not isinstance(self._transport, GuardedAlpacaPaperCryptoWriteTransport):
            raise CryptoPaperWriterPolicyError(
                "enabled crypto writer requires role-bound Final-Guard transport"
            )
        if getattr(self._transport, "role", None) is not order.role:
            raise CryptoPaperWriterPolicyError(
                "guarded crypto transport role does not match broker order role"
            )
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        snapshot = lifecycle.snapshot(lifecycle_id)
        self._validate_binding(snapshot=snapshot, order=order)

        # Finish every deterministic/local validation before durable UNKNOWN.
        # Once UNKNOWN is committed, the only safe recovery path is broker reconciliation.
        payload = order.to_payload()
        body = _canonical(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AUTO-TRADE-R6/0.28R",
            "APCA-API-KEY-ID": credentials.key_id,
            "APCA-API-SECRET-KEY": credentials.secret_key,
        }
        _validate_headers(headers)

        if order.role is CryptoOrderRole.ENTRY:
            if snapshot.state.status is not CryptoLifecycleStatus.ENTRY_PREPARED:
                raise CryptoLifecycleBlocked("entry POST requires durable ENTRY_PREPARED")
            lifecycle.mark_entry_submission_unknown(lifecycle_id, at=now)
        elif order.role is CryptoOrderRole.PROTECTION:
            if snapshot.state.status is not CryptoLifecycleStatus.PROTECTION_PREPARED:
                raise CryptoLifecycleBlocked("protection POST requires durable PROTECTION_PREPARED")
            lifecycle.mark_protection_submission_unknown(lifecycle_id, at=now)
        else:
            raise CryptoPaperWriterPolicyError("unsupported crypto order role")

        try:
            response = self._transport.post(
                host=self._config.host,
                path=CRYPTO_ORDERS_PATH,
                headers=headers,
                body=body,
                timeout_seconds=self._config.timeout_seconds,
                max_response_bytes=self._config.max_response_bytes,
            )
        except CryptoPaperWriterAmbiguous:
            raise
        except Exception as exc:
            raise CryptoPaperWriterAmbiguous(
                "crypto POST outcome is unknown; lifecycle remains UNKNOWN and must be reconciled"
            ) from exc

        try:
            return _parse_ack(
                response=response,
                lifecycle_id=lifecycle_id,
                order=order,
                request_body=body,
                now=now,
            )
        except CryptoPaperWriterIntegrityError as exc:
            raise CryptoPaperWriterAmbiguous(
                "crypto POST returned untrusted/ambiguous acknowledgement; reconcile by durable client_order_id"
            ) from exc

    @staticmethod
    def _validate_binding(*, snapshot, order: AlpacaPaperCryptoOrderRequest) -> None:
        binding = snapshot.binding
        state = snapshot.state
        if order.symbol != binding.symbol:
            raise CryptoPaperWriterPolicyError("crypto order symbol differs from durable lifecycle")
        if order.asset_attestation_fingerprint != binding.asset_attestation_fingerprint:
            raise CryptoPaperWriterPolicyError("crypto order asset evidence differs from durable lifecycle")
        if order.product_profile_fingerprint != binding.product_profile_fingerprint:
            raise CryptoPaperWriterPolicyError("crypto order product profile differs from durable lifecycle")
        if order.role is CryptoOrderRole.ENTRY:
            if order.fingerprint != binding.entry_order_fingerprint:
                raise CryptoPaperWriterPolicyError("entry order fingerprint differs from durable lifecycle")
            if order.client_order_id != binding.entry_client_order_id:
                raise CryptoPaperWriterPolicyError("entry client_order_id differs from durable lifecycle")
            if order.quantity != binding.entry_quantity:
                raise CryptoPaperWriterPolicyError("entry quantity differs from durable lifecycle")
        elif order.role is CryptoOrderRole.PROTECTION:
            if order.fingerprint != state.protection_order_fingerprint:
                raise CryptoPaperWriterPolicyError("protection order fingerprint differs from durable lifecycle")
            if order.client_order_id != state.protection_client_order_id:
                raise CryptoPaperWriterPolicyError("protection client_order_id differs from durable lifecycle")
            if order.quantity != state.protection_quantity:
                raise CryptoPaperWriterPolicyError("protection quantity differs from durable lifecycle")


def _parse_ack(
    *,
    response: AlpacaPaperCryptoWriteResponse,
    lifecycle_id: str,
    order: AlpacaPaperCryptoOrderRequest,
    request_body: bytes,
    now: datetime,
) -> CryptoPaperWriteReceipt:
    if response.status_code != 200:
        raise CryptoPaperWriterIntegrityError(f"unexpected crypto order HTTP status {response.status_code}")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise CryptoPaperWriterIntegrityError("crypto order acknowledgement must be application/json")
    request_id = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise CryptoPaperWriterIntegrityError("crypto order acknowledgement lacks valid X-Request-ID")
    try:
        payload = json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoPaperWriterIntegrityError("crypto order acknowledgement is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CryptoPaperWriterIntegrityError("crypto order acknowledgement root must be object")

    broker_order_id = _required_string(payload, "id")
    client_order_id = _required_string(payload, "client_order_id")
    symbol = _required_string(payload, "symbol").upper()
    asset_class = _required_string(payload, "asset_class").lower()
    side = _required_string(payload, "side").lower()
    order_type = _required_string(payload, "type").lower()
    time_in_force = _required_string(payload, "time_in_force").lower()
    broker_status = _required_string(payload, "status").lower()
    qty = _nonnegative_decimal(payload.get("qty"), "qty")
    filled_qty = _nonnegative_decimal(payload.get("filled_qty", "0"), "filled_qty")

    expected = order.to_payload()
    if client_order_id != order.client_order_id:
        raise CryptoPaperWriterIntegrityError("crypto acknowledgement client_order_id mismatch")
    if symbol != order.symbol or asset_class != "crypto":
        raise CryptoPaperWriterIntegrityError("crypto acknowledgement product identity mismatch")
    if side != str(expected["side"]) or order_type != str(expected["type"]) or time_in_force != str(expected["time_in_force"]):
        raise CryptoPaperWriterIntegrityError("crypto acknowledgement order semantics mismatch")
    if qty != order.quantity:
        raise CryptoPaperWriterIntegrityError("crypto acknowledgement quantity mismatch")
    if filled_qty > qty:
        raise CryptoPaperWriterIntegrityError("crypto acknowledgement filled quantity exceeds order quantity")
    _match_optional_decimal(payload, expected, "limit_price")
    _match_optional_decimal(payload, expected, "stop_price")

    return CryptoPaperWriteReceipt(
        lifecycle_id=lifecycle_id,
        role=order.role,
        request_fingerprint=order.fingerprint,
        request_payload_sha256=sha256(request_body).hexdigest(),
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        time_in_force=time_in_force,
        broker_status=broker_status,
        requested_quantity=qty,
        broker_filled_quantity=filled_qty,
        request_id=request_id,
        response_sha256=sha256(response.body).hexdigest(),
        submitted_at=now.astimezone(timezone.utc),
    )


def _validate_headers(headers: Mapping[str, str]) -> None:
    if set(headers) != {
        "Accept",
        "Content-Type",
        "User-Agent",
        "APCA-API-KEY-ID",
        "APCA-API-SECRET-KEY",
    }:
        raise CryptoPaperWriterPolicyError("crypto writer headers are non-canonical")
    if headers["Accept"] != "application/json" or headers["Content-Type"] != "application/json":
        raise CryptoPaperWriterPolicyError("crypto writer content negotiation is non-canonical")
    if not headers["APCA-API-KEY-ID"] or not headers["APCA-API-SECRET-KEY"]:
        raise CryptoPaperWriterPolicyError("crypto writer PAPER credentials are required")


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CryptoPaperWriterIntegrityError(f"crypto acknowledgement field {key} is required")
    return value.strip()


def _nonnegative_decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise CryptoPaperWriterIntegrityError(f"crypto acknowledgement {label} must be decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CryptoPaperWriterIntegrityError(f"crypto acknowledgement {label} must be decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise CryptoPaperWriterIntegrityError(f"crypto acknowledgement {label} must be finite non-negative")
    return parsed


def _match_optional_decimal(payload: Mapping[str, object], expected: Mapping[str, object], key: str) -> None:
    expected_value = expected.get(key)
    actual_value = payload.get(key)
    if expected_value is None:
        if actual_value not in (None, ""):
            raise CryptoPaperWriterIntegrityError(f"crypto acknowledgement unexpectedly contains {key}")
        return
    actual = _nonnegative_decimal(actual_value, key)
    if actual != Decimal(str(expected_value)):
        raise CryptoPaperWriterIntegrityError(f"crypto acknowledgement {key} mismatch")


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = [
    "AlpacaPaperCryptoWriteResponse",
    "AlpacaPaperCryptoWriteTransport",
    "AlpacaPaperCryptoWriter",
    "AlpacaPaperCryptoWriterConfig",
    "CryptoPaperWriteReceipt",
    "CryptoPaperWriterAmbiguous",
    "CryptoPaperWriterDisabled",
    "CryptoPaperWriterError",
    "CryptoPaperWriterIntegrityError",
    "CryptoPaperWriterPolicyError",
    "CRYPTO_ORDERS_PATH",
    "GuardedAlpacaPaperCryptoWriteTransport",
    "HttpsAlpacaPaperCryptoWriteTransport",
]
