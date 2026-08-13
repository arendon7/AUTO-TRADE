from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Mapping

from .alpaca_paper_crypto_asset import normalize_crypto_pair
from .alpaca_paper_crypto_lifecycle import SQLiteCryptoPaperLifecycle
from .alpaca_paper_crypto_order import AlpacaPaperCryptoOrderRequest, CryptoOrderRole
from .alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperPolicyError,
    AlpacaPaperReadRequest,
    AlpacaPaperReadTransport,
    AlpacaPaperUnavailable,
    UrllibAlpacaPaperReadTransport,
    _validate_auth_headers,
)


ORDER_BY_CLIENT_PATH = "/v2/orders:by_client_order_id"
POSITION_PATH_PREFIX = "/v2/positions/"
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_TERMINAL_ORDER_STATUSES = {"filled", "canceled", "expired", "rejected"}
_OPEN_ORDER_STATUSES = {"accepted", "pending_new", "new", "partially_filled"}


class CryptoPaperReconciliationError(RuntimeError):
    pass


class CryptoPaperReconciliationDisabled(CryptoPaperReconciliationError):
    pass


class CryptoPaperReconciliationIntegrityError(CryptoPaperReconciliationError):
    pass


@dataclass(frozen=True, slots=True)
class _ExactReadPolicy:
    expected_url: str

    def validate(self, request: AlpacaPaperReadRequest) -> None:
        if request.method != "GET":
            raise AlpacaPaperPolicyError("crypto reconciliation is GET-only")
        if not 0 < request.timeout_seconds <= 15:
            raise AlpacaPaperPolicyError("crypto reconciliation timeout is invalid")
        if request.url != self.expected_url:
            raise AlpacaPaperPolicyError("crypto reconciliation URL is not exact allowlist")
        _validate_auth_headers(request.headers)

    def validate_final_url(self, url: str) -> None:
        if url != self.expected_url:
            raise AlpacaPaperPolicyError("crypto reconciliation final URL changed")


@dataclass(frozen=True, slots=True)
class CryptoBrokerOrderSnapshot:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    time_in_force: str
    status: str
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    request_id: str
    response_sha256: str
    observed_at: datetime

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_ORDER_STATUSES

    @property
    def fingerprint(self) -> str:
        return sha256(
            json.dumps(
                {
                    "broker_order_id": self.broker_order_id,
                    "client_order_id": self.client_order_id,
                    "symbol": self.symbol,
                    "side": self.side,
                    "order_type": self.order_type,
                    "time_in_force": self.time_in_force,
                    "status": self.status,
                    "quantity": _decimal_text(self.quantity),
                    "filled_quantity": _decimal_text(self.filled_quantity),
                    "limit_price": None if self.limit_price is None else _decimal_text(self.limit_price),
                    "stop_price": None if self.stop_price is None else _decimal_text(self.stop_price),
                    "request_id": self.request_id,
                    "response_sha256": self.response_sha256,
                    "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class CryptoBrokerPositionSnapshot:
    symbol: str
    quantity: Decimal
    market_value: Decimal | None
    average_entry_price: Decimal | None
    request_id: str
    response_sha256: str
    observed_at: datetime
    absent: bool = False


@dataclass(frozen=True, slots=True)
class CryptoBrokerReconciliation:
    order: CryptoBrokerOrderSnapshot
    position: CryptoBrokerPositionSnapshot
    observed_at: datetime

    @property
    def fingerprint(self) -> str:
        return sha256(
            json.dumps(
                {
                    "order_fingerprint": self.order.fingerprint,
                    "symbol": self.position.symbol,
                    "position_quantity": _decimal_text(self.position.quantity),
                    "position_absent": self.position.absent,
                    "position_response_sha256": self.position.response_sha256,
                    "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


class AlpacaPaperCryptoReconciliationGateway:
    """Exact GET reconciliation by durable client_order_id plus exact crypto position."""

    def __init__(
        self,
        *,
        config: AlpacaPaperGatewayConfig | None = None,
        order_transport: AlpacaPaperReadTransport | None = None,
        position_transport: AlpacaPaperReadTransport | None = None,
    ) -> None:
        self._config = config or AlpacaPaperGatewayConfig()
        self._order_transport = order_transport
        self._position_transport = position_transport

    def reconcile(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        order: AlpacaPaperCryptoOrderRequest,
        now: datetime,
    ) -> CryptoBrokerReconciliation:
        if not self._config.enabled:
            raise CryptoPaperReconciliationDisabled("crypto PAPER reconciliation is disabled")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if not _CLIENT_ID_RE.fullmatch(order.client_order_id):
            raise ValueError("crypto client_order_id is invalid")
        symbol = normalize_crypto_pair(order.symbol)
        observed_at = now.astimezone(timezone.utc)

        order_url = (
            "https" + "://" + ALPACA_PAPER_TRADING_HOST + ORDER_BY_CLIENT_PATH
            + "?client_order_id=" + order.client_order_id
        )
        order_policy = _ExactReadPolicy(order_url)
        order_transport = self._order_transport or UrllibAlpacaPaperReadTransport(
            policy=order_policy,  # type: ignore[arg-type]
            max_response_bytes=self._config.max_response_bytes,
        )
        order_response = order_transport.read(
            _request(credentials=credentials, url=order_url, timeout=self._config.timeout_seconds)
        )
        order_policy.validate_final_url(order_response.final_url)
        broker_order = _parse_order(
            response=order_response,
            expected=order,
            observed_at=observed_at,
        )

        position_url = (
            "https" + "://" + ALPACA_PAPER_TRADING_HOST + POSITION_PATH_PREFIX
            + symbol.replace("/", "%2F")
        )
        position_policy = _ExactReadPolicy(position_url)
        position_transport = self._position_transport or UrllibAlpacaPaperReadTransport(
            policy=position_policy,  # type: ignore[arg-type]
            max_response_bytes=self._config.max_response_bytes,
        )
        position_response = position_transport.read(
            _request(credentials=credentials, url=position_url, timeout=self._config.timeout_seconds)
        )
        position_policy.validate_final_url(position_response.final_url)
        position = _parse_position(
            response=position_response,
            expected_symbol=symbol,
            observed_at=observed_at,
        )
        return CryptoBrokerReconciliation(
            order=broker_order,
            position=position,
            observed_at=observed_at,
        )

    @staticmethod
    def apply_to_lifecycle(
        *,
        lifecycle: SQLiteCryptoPaperLifecycle,
        lifecycle_id: str,
        requested_order: AlpacaPaperCryptoOrderRequest,
        reconciliation: CryptoBrokerReconciliation,
        at: datetime,
    ):
        if requested_order.role is CryptoOrderRole.ENTRY:
            return lifecycle.reconcile_entry(
                lifecycle_id,
                broker_order_id=reconciliation.order.broker_order_id,
                broker_status=reconciliation.order.status,
                filled_quantity=reconciliation.order.filled_quantity,
                confirmed_net_long_quantity=reconciliation.position.quantity,
                at=at,
            )
        if requested_order.role is CryptoOrderRole.PROTECTION:
            return lifecycle.reconcile_protection(
                lifecycle_id,
                broker_order_id=reconciliation.order.broker_order_id,
                broker_status=reconciliation.order.status,
                filled_quantity=reconciliation.order.filled_quantity,
                confirmed_net_long_quantity=reconciliation.position.quantity,
                at=at,
            )
        raise CryptoPaperReconciliationIntegrityError("unsupported crypto order role")


def _request(*, credentials: AlpacaPaperCredentials, url: str, timeout: float) -> AlpacaPaperReadRequest:
    request = AlpacaPaperReadRequest(
        method="GET",
        url=url,
        timeout_seconds=timeout,
        headers={
            "Accept": "application/json",
            "User-Agent": "AUTO-TRADE-R6/0.28R",
            "APCA-API-KEY-ID": credentials.key_id,
            "APCA-API-SECRET-KEY": credentials.secret_key,
        },
    )
    _ExactReadPolicy(url).validate(request)
    return request


def _parse_order(
    *,
    response: AlpacaPaperHttpResponse,
    expected: AlpacaPaperCryptoOrderRequest,
    observed_at: datetime,
) -> CryptoBrokerOrderSnapshot:
    if response.status_code == 404:
        raise CryptoPaperReconciliationIntegrityError(
            "broker cannot find durable crypto client_order_id; POST outcome remains unresolved"
        )
    if response.status_code != 200:
        raise AlpacaPaperUnavailable(f"unexpected crypto order reconciliation status: {response.status_code}")
    payload, request_id = _json_payload(response, "crypto order reconciliation")
    client_order_id = _string(payload, "client_order_id")
    symbol = normalize_crypto_pair(_string(payload, "symbol"))
    asset_class = _string(payload, "asset_class").lower()
    side = _string(payload, "side").lower()
    order_type = _string(payload, "type").lower()
    tif = _string(payload, "time_in_force").lower()
    status = _string(payload, "status").lower()
    quantity = _decimal(payload.get("qty"), "qty", nonnegative=True)
    filled = _decimal(payload.get("filled_qty", "0"), "filled_qty", nonnegative=True)
    if client_order_id != expected.client_order_id:
        raise CryptoPaperReconciliationIntegrityError("reconciled client_order_id mismatch")
    if symbol != expected.symbol or asset_class != "crypto":
        raise CryptoPaperReconciliationIntegrityError("reconciled crypto product identity mismatch")
    if side != expected.side.value or order_type != expected.order_type.value or tif != expected.time_in_force.value:
        raise CryptoPaperReconciliationIntegrityError("reconciled crypto order semantics mismatch")
    if status not in _OPEN_ORDER_STATUSES | _TERMINAL_ORDER_STATUSES:
        raise CryptoPaperReconciliationIntegrityError("unsupported reconciled broker order status")
    if quantity != expected.quantity or filled > quantity:
        raise CryptoPaperReconciliationIntegrityError("reconciled crypto quantity mismatch")
    _match_price(payload, expected, "limit_price")
    _match_price(payload, expected, "stop_price")
    return CryptoBrokerOrderSnapshot(
        broker_order_id=_string(payload, "id"),
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        time_in_force=tif,
        status=status,
        quantity=quantity,
        filled_quantity=filled,
        limit_price=_optional_decimal(payload.get("limit_price"), "limit_price"),
        stop_price=_optional_decimal(payload.get("stop_price"), "stop_price"),
        request_id=request_id,
        response_sha256=sha256(response.body).hexdigest(),
        observed_at=observed_at,
    )


def _parse_position(
    *,
    response: AlpacaPaperHttpResponse,
    expected_symbol: str,
    observed_at: datetime,
) -> CryptoBrokerPositionSnapshot:
    if response.status_code == 404:
        return CryptoBrokerPositionSnapshot(
            symbol=expected_symbol,
            quantity=Decimal("0"),
            market_value=None,
            average_entry_price=None,
            request_id=_request_id(response),
            response_sha256=sha256(response.body).hexdigest(),
            observed_at=observed_at,
            absent=True,
        )
    if response.status_code != 200:
        raise AlpacaPaperUnavailable(f"unexpected crypto position reconciliation status: {response.status_code}")
    payload, request_id = _json_payload(response, "crypto position reconciliation")
    symbol = normalize_crypto_pair(_string(payload, "symbol"))
    if symbol != expected_symbol or _string(payload, "asset_class").lower() != "crypto":
        raise CryptoPaperReconciliationIntegrityError("reconciled crypto position identity mismatch")
    quantity = _decimal(payload.get("qty"), "position qty", nonnegative=True)
    side = _string(payload, "side").lower()
    if quantity > 0 and side != "long":
        raise CryptoPaperReconciliationIntegrityError("R6 crypto position must be long-only")
    market_value = _optional_decimal(payload.get("market_value"), "market_value", allow_negative=False)
    average_entry_price = _optional_decimal(payload.get("avg_entry_price"), "avg_entry_price", allow_negative=False)
    return CryptoBrokerPositionSnapshot(
        symbol=symbol,
        quantity=quantity,
        market_value=market_value,
        average_entry_price=average_entry_price,
        request_id=request_id,
        response_sha256=sha256(response.body).hexdigest(),
        observed_at=observed_at,
        absent=False,
    )


def _json_payload(response: AlpacaPaperHttpResponse, label: str) -> tuple[dict[str, object], str]:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise CryptoPaperReconciliationIntegrityError(f"{label} must be application/json")
    request_id = _request_id(response)
    try:
        payload = json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoPaperReconciliationIntegrityError(f"{label} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CryptoPaperReconciliationIntegrityError(f"{label} root must be object")
    return payload, request_id


def _request_id(response: AlpacaPaperHttpResponse) -> str:
    request_id = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise CryptoPaperReconciliationIntegrityError("crypto reconciliation lacks valid X-Request-ID")
    return request_id


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CryptoPaperReconciliationIntegrityError(f"crypto reconciliation field {key} is required")
    return value.strip()


def _decimal(value: object, label: str, *, nonnegative: bool) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise CryptoPaperReconciliationIntegrityError(f"{label} must be decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CryptoPaperReconciliationIntegrityError(f"{label} must be decimal") from exc
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise CryptoPaperReconciliationIntegrityError(f"{label} is outside allowed range")
    return parsed


def _optional_decimal(value: object, label: str, *, allow_negative: bool = False) -> Decimal | None:
    if value in (None, ""):
        return None
    parsed = _decimal(value, label, nonnegative=not allow_negative)
    return parsed


def _match_price(payload: Mapping[str, object], expected: AlpacaPaperCryptoOrderRequest, key: str) -> None:
    expected_value = getattr(expected, key)
    actual = _optional_decimal(payload.get(key), key)
    if expected_value is None:
        if actual is not None:
            raise CryptoPaperReconciliationIntegrityError(f"reconciled order unexpectedly contains {key}")
        return
    if actual != expected_value:
        raise CryptoPaperReconciliationIntegrityError(f"reconciled {key} mismatch")


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


__all__ = [
    "AlpacaPaperCryptoReconciliationGateway",
    "CryptoBrokerOrderSnapshot",
    "CryptoBrokerPositionSnapshot",
    "CryptoBrokerReconciliation",
    "CryptoPaperReconciliationDisabled",
    "CryptoPaperReconciliationError",
    "CryptoPaperReconciliationIntegrityError",
]
