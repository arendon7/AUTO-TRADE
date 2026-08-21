from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.paper_exit_order_read import (
    HttpsPaperExitOrderReadTransport,
    PaperExitOrderReadResponse,
)
from autotrade.brokers.paper_portfolio import PaperPortfolioSnapshot
from autotrade.paper_exit_attempt import PaperExitState, PaperExitStatus, SQLitePaperExitAttempt
from autotrade.paper_exit_order import PaperExitOrder


RECONCILIATION_PORTFOLIO_TTL = timedelta(seconds=10)
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OPEN_STATUSES = {"accepted", "pending_new", "new", "partially_filled"}
_TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected"}


class PaperExitReconciliationError(RuntimeError):
    pass


class PaperExitReconciliationBlocked(PaperExitReconciliationError):
    pass


class PaperExitReconciliationIntegrityError(PaperExitReconciliationError):
    pass


@dataclass(frozen=True, slots=True)
class PaperExitOrderObservation:
    found: bool
    client_order_id: str
    broker_order_id: str | None
    broker_status: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    remaining_position_quantity: Decimal
    request_id: str
    response_sha256: str
    observed_at: datetime
    retry_post: bool
    live_trading: str

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.client_order_id):
            raise ValueError("R7 exit observation client_order_id is invalid")
        if self.found and (self.broker_order_id is None or not _ID_RE.fullmatch(self.broker_order_id)):
            raise ValueError("found R7 exit observation requires broker order id")
        if not self.found and self.broker_order_id is not None:
            raise ValueError("absent R7 exit observation cannot carry broker order id")
        if not _REQUEST_ID_RE.fullmatch(self.request_id):
            raise ValueError("R7 exit observation request id is invalid")
        if len(self.response_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in self.response_sha256):
            raise ValueError("R7 exit observation response hash is invalid")
        for label, value in (
            ("requested_quantity", self.requested_quantity),
            ("filled_quantity", self.filled_quantity),
            ("remaining_position_quantity", self.remaining_position_quantity),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{label} must be finite non-negative Decimal")
        if self.filled_quantity > self.requested_quantity:
            raise ValueError("R7 exit observation fill exceeds request")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("R7 exit observation time must be timezone-aware")
        if self.retry_post is not False or self.live_trading != "BLOCKED":
            raise ValueError("R7 exit observation cannot authorize retry or LIVE")

    @property
    def fingerprint(self) -> str:
        return sha256(
            json.dumps(
                {
                    "found": self.found,
                    "client_order_id": self.client_order_id,
                    "broker_order_id": self.broker_order_id,
                    "broker_status": self.broker_status,
                    "requested_quantity": _decimal(self.requested_quantity),
                    "filled_quantity": _decimal(self.filled_quantity),
                    "remaining_position_quantity": _decimal(self.remaining_position_quantity),
                    "request_id": self.request_id,
                    "response_sha256": self.response_sha256,
                    "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
                    "retry_post": self.retry_post,
                    "live_trading": self.live_trading,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class PaperExitReconciliationResult:
    observation: PaperExitOrderObservation
    state: PaperExitState
    portfolio_fingerprint: str


class PaperExitReconciler:
    """GET-only broker truth for an already-burned R7 PAPER exit attempt."""

    def __init__(self, *, transport: HttpsPaperExitOrderReadTransport | None = None) -> None:
        self._transport = transport or HttpsPaperExitOrderReadTransport()

    def reconcile(
        self,
        *,
        lifecycle: SQLitePaperExitAttempt,
        order: PaperExitOrder,
        credentials: AlpacaPaperCredentials,
        fresh_portfolio: PaperPortfolioSnapshot,
        now: datetime,
    ) -> PaperExitReconciliationResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise PaperExitReconciliationBlocked("R7 exit reconciliation time must be timezone-aware")
        instant = now.astimezone(timezone.utc)
        if not isinstance(lifecycle, SQLitePaperExitAttempt) or not isinstance(order, PaperExitOrder):
            raise PaperExitReconciliationBlocked("authoritative exit lifecycle and exact order are required")
        if not isinstance(credentials, AlpacaPaperCredentials) or not isinstance(fresh_portfolio, PaperPortfolioSnapshot):
            raise PaperExitReconciliationBlocked("ephemeral PAPER credentials and fresh broker portfolio are required")

        snapshot = lifecycle.snapshot(order.attempt_id)
        if snapshot.binding.order_hash != order.order_hash or snapshot.binding.payload_hash != order.payload_hash:
            raise PaperExitReconciliationBlocked("durable R7 exit order binding differs from supplied order")
        if snapshot.state.terminal:
            raise PaperExitReconciliationBlocked("R7 exit is already terminally reconciled")
        if snapshot.state.attempt_count != 1 or snapshot.state.restart_action != "RECONCILE_ONLY":
            raise PaperExitReconciliationBlocked("R7 exit reconciliation requires a burned one-shot POST authority")
        if snapshot.binding.credential_reference != credentials.credential_reference:
            raise PaperExitReconciliationBlocked("effective PAPER credential differs from prepared exit")

        age = instant - fresh_portfolio.observed_at.astimezone(timezone.utc)
        if age < timedelta(0) or age > RECONCILIATION_PORTFOLIO_TTL:
            raise PaperExitReconciliationBlocked("R7 reconciliation portfolio is stale or from the future")
        if fresh_portfolio.account.account_reference != snapshot.binding.account_reference:
            raise PaperExitReconciliationBlocked("R7 reconciliation account differs from prepared exit account")
        if fresh_portfolio.account.credential_reference != snapshot.binding.credential_reference:
            raise PaperExitReconciliationBlocked("R7 reconciliation portfolio credential differs from exit binding")
        remaining = _remaining_position(
            portfolio=fresh_portfolio,
            order=order,
            initial_quantity=snapshot.binding.initial_position_quantity,
        )

        response = self._transport.read(
            client_order_id=order.client_order_id,
            headers={
                "Accept": "application/json",
                "User-Agent": "AUTO-TRADE-R7/0.1",
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
            },
        )
        request_id = _request_id(response)
        response_hash = sha256(response.body).hexdigest()

        if response.status_code == 404:
            observation = PaperExitOrderObservation(
                found=False,
                client_order_id=order.client_order_id,
                broker_order_id=None,
                broker_status="not_found",
                requested_quantity=order.quantity,
                filled_quantity=Decimal("0"),
                remaining_position_quantity=remaining,
                request_id=request_id,
                response_sha256=response_hash,
                observed_at=instant,
                retry_post=False,
                live_trading="BLOCKED",
            )
            state = lifecycle.reconcile_order_absent(
                order.attempt_id,
                remaining_position_quantity=remaining,
                at=instant,
            )
            return PaperExitReconciliationResult(
                observation=observation,
                state=state,
                portfolio_fingerprint=fresh_portfolio.fingerprint,
            )
        if response.status_code != 200:
            raise PaperExitReconciliationBlocked(
                f"R7 exit order GET returned unexpected HTTP {response.status_code}"
            )

        parsed = _parse_order_response(response=response, expected=order)
        observation = PaperExitOrderObservation(
            found=True,
            client_order_id=order.client_order_id,
            broker_order_id=parsed["broker_order_id"],
            broker_status=parsed["broker_status"],
            requested_quantity=order.quantity,
            filled_quantity=parsed["filled_quantity"],
            remaining_position_quantity=remaining,
            request_id=request_id,
            response_sha256=response_hash,
            observed_at=instant,
            retry_post=False,
            live_trading="BLOCKED",
        )
        state = lifecycle.reconcile_order(
            order.attempt_id,
            broker_order_id=observation.broker_order_id or "",
            broker_status=observation.broker_status,
            filled_quantity=observation.filled_quantity,
            remaining_position_quantity=remaining,
            at=instant,
        )
        return PaperExitReconciliationResult(
            observation=observation,
            state=state,
            portfolio_fingerprint=fresh_portfolio.fingerprint,
        )


def _remaining_position(*, portfolio: PaperPortfolioSnapshot, order: PaperExitOrder, initial_quantity: Decimal) -> Decimal:
    matches = [item for item in portfolio.positions if item.symbol == order.symbol]
    if not matches:
        return Decimal("0")
    if len(matches) != 1:
        raise PaperExitReconciliationIntegrityError("R7 exit reconciliation found duplicate broker positions")
    position = matches[0]
    if position.asset_class != "crypto" or position.side != "long":
        raise PaperExitReconciliationIntegrityError("R7 exit reconciliation position is not long crypto exposure")
    if position.quantity < 0 or position.quantity > initial_quantity:
        raise PaperExitReconciliationIntegrityError("R7 exit reconciliation position increased or became invalid")
    return position.quantity


def _parse_order_response(*, response: PaperExitOrderReadResponse, expected: PaperExitOrder) -> dict[str, object]:
    content_type = str(response.headers.get("content-type", "")).split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise PaperExitReconciliationIntegrityError("R7 exit order response must be application/json")
    try:
        raw = json.loads(
            response.body.decode("utf-8", errors="strict"),
            parse_constant=lambda token: _raise_json_constant(token),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperExitReconciliationIntegrityError("R7 exit order response is not strict JSON") from exc
    if not isinstance(raw, dict):
        raise PaperExitReconciliationIntegrityError("R7 exit order response root must be object")

    broker_order_id = _required_text(raw, "id")
    client_order_id = _required_text(raw, "client_order_id")
    symbol = _required_text(raw, "symbol").upper()
    asset_class = _required_text(raw, "asset_class").lower()
    side = _required_text(raw, "side").lower()
    order_type = _required_text(raw, "type").lower()
    tif = _required_text(raw, "time_in_force").lower()
    status = _required_text(raw, "status").lower()
    quantity = _nonnegative_decimal(raw.get("qty"), "qty")
    filled = _nonnegative_decimal(raw.get("filled_qty", "0"), "filled_qty")
    limit_price = _positive_decimal(raw.get("limit_price"), "limit_price")

    if not _ID_RE.fullmatch(broker_order_id):
        raise PaperExitReconciliationIntegrityError("R7 exit broker order id is invalid")
    if client_order_id != expected.client_order_id:
        raise PaperExitReconciliationIntegrityError("R7 exit broker client_order_id mismatch")
    if symbol not in {expected.symbol, expected.broker_symbol}:
        raise PaperExitReconciliationIntegrityError("R7 exit broker symbol mismatch")
    if asset_class != "crypto" or side != "sell" or order_type != "limit" or tif != "ioc":
        raise PaperExitReconciliationIntegrityError("R7 exit broker order shape differs from SELL LIMIT IOC")
    if status not in _OPEN_STATUSES | _TERMINAL_STATUSES:
        raise PaperExitReconciliationIntegrityError("R7 exit broker status is unsupported")
    if quantity != expected.quantity or limit_price != expected.limit_price:
        raise PaperExitReconciliationIntegrityError("R7 exit broker quantity/limit differs from immutable order")
    if filled > expected.quantity:
        raise PaperExitReconciliationIntegrityError("R7 exit broker filled quantity exceeds request")
    return {
        "broker_order_id": broker_order_id,
        "broker_status": status,
        "filled_quantity": filled,
    }


def _request_id(response: PaperExitOrderReadResponse) -> str:
    value = str(response.headers.get("x-request-id", "")).strip()
    if not _REQUEST_ID_RE.fullmatch(value):
        raise PaperExitReconciliationIntegrityError("R7 exit order GET lacks valid X-Request-ID")
    return value


def _required_text(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PaperExitReconciliationIntegrityError(f"R7 exit broker field {key} is required")
    text = value.strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise PaperExitReconciliationIntegrityError(f"R7 exit broker field {key} contains control data")
    return text


def _nonnegative_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise PaperExitReconciliationIntegrityError(f"R7 exit broker {label} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PaperExitReconciliationIntegrityError(f"R7 exit broker {label} is invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise PaperExitReconciliationIntegrityError(f"R7 exit broker {label} must be finite non-negative")
    return parsed


def _positive_decimal(value: object, label: str) -> Decimal:
    parsed = _nonnegative_decimal(value, label)
    if parsed <= 0:
        raise PaperExitReconciliationIntegrityError(f"R7 exit broker {label} must be positive")
    return parsed


def _raise_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "PaperExitOrderObservation",
    "PaperExitReconciler",
    "PaperExitReconciliationBlocked",
    "PaperExitReconciliationError",
    "PaperExitReconciliationIntegrityError",
    "PaperExitReconciliationResult",
    "RECONCILIATION_PORTFOLIO_TTL",
]
