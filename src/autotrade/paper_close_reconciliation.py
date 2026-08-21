from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Mapping

from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperPolicyError,
    AlpacaPaperReadRequest,
    AlpacaPaperReadTransport,
    AlpacaPaperUnavailable,
    UrllibAlpacaPaperReadTransport,
)
from autotrade.brokers.paper_portfolio import AlpacaPaperPortfolioGateway, PaperPortfolioSnapshot
from autotrade.paper_close_lifecycle import SQLitePaperCloseLifecycle
from autotrade.paper_close_plan import PaperCryptoClosePlan


ORDER_BY_CLIENT_PATH = "/v2/orders:by_client_order_id"
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ORDER_STATUSES = {"accepted", "pending_new", "new", "partially_filled", "filled", "canceled", "expired", "rejected"}


class PaperCloseReconciliationError(RuntimeError):
    pass


class PaperCloseReconciliationDisabled(PaperCloseReconciliationError):
    pass


class PaperCloseReconciliationIntegrityError(PaperCloseReconciliationError):
    pass


@dataclass(frozen=True, slots=True)
class PaperCloseBrokerOrderTruth:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    time_in_force: str
    status: str
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal
    request_id: str
    response_sha256: str
    observed_at: datetime

    @property
    def terminal(self) -> bool:
        return self.status in {"filled", "canceled", "expired", "rejected"}

    @property
    def fingerprint(self) -> str:
        return _hash(
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
                "limit_price": _decimal_text(self.limit_price),
                "request_id": self.request_id,
                "response_sha256": self.response_sha256,
                "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class PaperCloseReconciliation:
    attempt_id: str
    plan_hash: str
    order: PaperCloseBrokerOrderTruth
    portfolio: PaperPortfolioSnapshot
    remaining_position: Decimal
    observed_at: datetime

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "attempt_id": self.attempt_id,
                "plan_hash": self.plan_hash,
                "order_fingerprint": self.order.fingerprint,
                "portfolio_fingerprint": self.portfolio.fingerprint,
                "remaining_position": _decimal_text(self.remaining_position),
                "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
                "retry_post": False,
                "live_trading": "BLOCKED",
            }
        )


@dataclass(frozen=True, slots=True)
class _ExactOrderReadPolicy:
    expected_url: str

    def validate(self, request: AlpacaPaperReadRequest) -> None:
        if request.method != "GET":
            raise AlpacaPaperPolicyError("R7 close reconciliation is GET-only")
        if request.url != self.expected_url:
            raise AlpacaPaperPolicyError("R7 close reconciliation URL is not exact allowlist")
        if not 0 < request.timeout_seconds <= 15:
            raise AlpacaPaperPolicyError("R7 close reconciliation timeout is invalid")
        _validate_headers(request.headers)

    def validate_final_url(self, url: str) -> None:
        if url != self.expected_url:
            raise AlpacaPaperPolicyError("R7 close reconciliation final URL changed")


class AlpacaPaperCloseReconciliationGateway:
    """GET-only truth for one already-burned R7 close attempt."""

    def __init__(
        self,
        *,
        config: AlpacaPaperGatewayConfig | None = None,
        order_transport: AlpacaPaperReadTransport | None = None,
        portfolio_gateway: AlpacaPaperPortfolioGateway | None = None,
    ) -> None:
        self._config = config or AlpacaPaperGatewayConfig()
        self._order_transport = order_transport
        self._portfolio_gateway = portfolio_gateway

    def reconcile(
        self,
        *,
        lifecycle: SQLitePaperCloseLifecycle,
        attempt_id: str,
        plan: PaperCryptoClosePlan,
        credentials: AlpacaPaperCredentials,
        expected_account_id: str,
        now: datetime,
    ) -> PaperCloseReconciliation:
        if not self._config.enabled:
            raise PaperCloseReconciliationDisabled("R7 close reconciliation is disabled by default")
        if not isinstance(lifecycle, SQLitePaperCloseLifecycle):
            raise PaperCloseReconciliationIntegrityError("authoritative close lifecycle is required")
        if not isinstance(plan, PaperCryptoClosePlan):
            raise PaperCloseReconciliationIntegrityError("exact close plan is required")
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise PaperCloseReconciliationIntegrityError("ephemeral PAPER credentials are required")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        instant = now.astimezone(timezone.utc)
        state = lifecycle.snapshot(attempt_id).state
        if state.plan_hash != plan.plan_hash or state.symbol != plan.symbol or state.requested_quantity != plan.quantity:
            raise PaperCloseReconciliationIntegrityError("close lifecycle does not match reconciliation plan")
        if state.submission_attempt_count != 1:
            raise PaperCloseReconciliationIntegrityError("close reconciliation requires exactly one burned POST attempt")
        if credentials.credential_reference != plan.credential_reference:
            raise PaperCloseReconciliationIntegrityError("effective PAPER credential differs from close plan")

        client_order_id = paper_close_client_order_id(attempt_id=attempt_id, plan_hash=plan.plan_hash)
        order_url = (
            "https" + "://" + ALPACA_PAPER_TRADING_HOST + ORDER_BY_CLIENT_PATH
            + "?client_order_id=" + client_order_id
        )
        policy = _ExactOrderReadPolicy(order_url)
        transport = self._order_transport or UrllibAlpacaPaperReadTransport(
            policy=policy,  # type: ignore[arg-type]
            max_response_bytes=self._config.max_response_bytes,
        )
        request = AlpacaPaperReadRequest(
            method="GET",
            url=order_url,
            timeout_seconds=self._config.timeout_seconds,
            headers=_headers(credentials),
        )
        policy.validate(request)
        response = transport.read(request)
        policy.validate_final_url(response.final_url)
        order = _parse_order(response=response, expected_client_order_id=client_order_id, plan=plan, observed_at=instant)

        portfolio_gateway = self._portfolio_gateway or AlpacaPaperPortfolioGateway(config=self._config)
        portfolio = portfolio_gateway.snapshot(
            credentials=credentials,
            expected_account_id=expected_account_id,
            now=instant,
        )
        if portfolio.account.account_reference != plan.account_reference:
            raise PaperCloseReconciliationIntegrityError("reconciled portfolio account differs from close plan")
        if portfolio.account.credential_reference != plan.credential_reference:
            raise PaperCloseReconciliationIntegrityError("reconciled portfolio credential differs from close plan")
        matches = [position for position in portfolio.positions if position.symbol == plan.symbol]
        if len(matches) > 1:
            raise PaperCloseReconciliationIntegrityError("reconciled portfolio contains duplicate target positions")
        if not matches:
            remaining = Decimal("0")
        else:
            position = matches[0]
            if position.asset_class != "crypto" or position.side != "long" or position.quantity <= 0:
                raise PaperCloseReconciliationIntegrityError("reconciled target position is not positive long crypto")
            remaining = position.quantity
        if remaining > plan.observed_position_quantity:
            raise PaperCloseReconciliationIntegrityError("reconciled exposure increased beyond prepared position")
        if order.filled_quantity > plan.quantity:
            raise PaperCloseReconciliationIntegrityError("reconciled close fill exceeds plan quantity")

        lifecycle.reconcile(
            attempt_id,
            broker_order_id=order.broker_order_id,
            broker_status=order.status,
            filled_quantity=order.filled_quantity,
            remaining_position=remaining,
            at=instant,
        )
        return PaperCloseReconciliation(
            attempt_id=attempt_id,
            plan_hash=plan.plan_hash,
            order=order,
            portfolio=portfolio,
            remaining_position=remaining,
            observed_at=instant,
        )


def paper_close_client_order_id(*, attempt_id: str, plan_hash: str) -> str:
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ValueError("attempt_id is required")
    if not isinstance(plan_hash, str) or not _HASH_RE.fullmatch(plan_hash):
        raise ValueError("plan_hash must be lowercase SHA-256")
    digest = sha256(f"AUTO-TRADE:R7:CLOSE:{attempt_id}:{plan_hash}".encode("utf-8")).hexdigest()
    return f"atr7-close-{digest[:40]}"


def _parse_order(*, response: AlpacaPaperHttpResponse, expected_client_order_id: str, plan: PaperCryptoClosePlan, observed_at: datetime) -> PaperCloseBrokerOrderTruth:
    if response.status_code == 404:
        raise PaperCloseReconciliationIntegrityError(
            "exact close order is currently absent; attempt remains burned and GET-only reconciliation must continue"
        )
    if response.status_code != 200:
        raise AlpacaPaperUnavailable(f"unexpected R7 close order reconciliation status: {response.status_code}")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise PaperCloseReconciliationIntegrityError("R7 close order truth must be application/json")
    request_id = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise PaperCloseReconciliationIntegrityError("R7 close reconciliation lacks valid X-Request-ID")
    try:
        raw = json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaperCloseReconciliationIntegrityError("R7 close order truth is invalid JSON") from exc
    if not isinstance(raw, dict):
        raise PaperCloseReconciliationIntegrityError("R7 close order truth root must be object")
    client_order_id = _string(raw, "client_order_id")
    symbol = _string(raw, "symbol").upper()
    if symbol == plan.broker_symbol:
        canonical = plan.symbol
    elif symbol == plan.symbol:
        canonical = symbol
    else:
        raise PaperCloseReconciliationIntegrityError("reconciled close symbol mismatch")
    side = _string(raw, "side").lower()
    order_type = _string(raw, "type").lower()
    tif = _string(raw, "time_in_force").lower()
    status = _string(raw, "status").lower()
    quantity = _decimal(raw.get("qty"), "qty")
    filled = _decimal(raw.get("filled_qty", "0"), "filled_qty", allow_zero=True)
    limit = _decimal(raw.get("limit_price"), "limit_price")
    if client_order_id != expected_client_order_id:
        raise PaperCloseReconciliationIntegrityError("reconciled close client_order_id mismatch")
    if canonical != plan.symbol or side != "sell" or order_type != "limit" or tif != "ioc":
        raise PaperCloseReconciliationIntegrityError("reconciled close order semantics mismatch")
    if status not in _ORDER_STATUSES:
        raise PaperCloseReconciliationIntegrityError("unsupported reconciled close broker status")
    if quantity != plan.quantity or filled > quantity or limit != plan.limit_price:
        raise PaperCloseReconciliationIntegrityError("reconciled close quantity/price mismatch")
    return PaperCloseBrokerOrderTruth(
        broker_order_id=_string(raw, "id"),
        client_order_id=client_order_id,
        symbol=plan.symbol,
        side=side,
        order_type=order_type,
        time_in_force=tif,
        status=status,
        quantity=quantity,
        filled_quantity=filled,
        limit_price=limit,
        request_id=request_id,
        response_sha256=sha256(response.body).hexdigest(),
        observed_at=observed_at,
    )


def _headers(credentials: AlpacaPaperCredentials) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "AUTO-TRADE-R7-RECONCILE/0.1",
        "APCA-API-KEY-ID": credentials.key_id,
        "APCA-API-SECRET-KEY": credentials.secret_key,
    }


def _validate_headers(headers: Mapping[str, str]) -> None:
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    expected = {"accept", "user-agent", "apca-api-key-id", "apca-api-secret-key"}
    if set(normalized) != expected:
        raise AlpacaPaperPolicyError("R7 close reconciliation headers are not exact allowlist")
    if normalized["accept"] != "application/json" or normalized["user-agent"] != "AUTO-TRADE-R7-RECONCILE/0.1":
        raise AlpacaPaperPolicyError("R7 close reconciliation headers are noncanonical")
    for key in ("apca-api-key-id", "apca-api-secret-key"):
        value = normalized[key]
        if not value or value != value.strip() or len(value) > 512 or any(ord(char) < 33 or ord(char) == 127 for char in value):
            raise AlpacaPaperPolicyError(f"R7 close reconciliation {key} is invalid")


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PaperCloseReconciliationIntegrityError(f"R7 close field {key} is required")
    return value.strip()


def _decimal(value: object, label: str, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise PaperCloseReconciliationIntegrityError(f"R7 close {label} must be decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PaperCloseReconciliationIntegrityError(f"R7 close {label} must be decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
        raise PaperCloseReconciliationIntegrityError(f"R7 close {label} is outside allowed range")
    return parsed


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


__all__ = [
    "AlpacaPaperCloseReconciliationGateway",
    "PaperCloseBrokerOrderTruth",
    "PaperCloseReconciliation",
    "PaperCloseReconciliationDisabled",
    "PaperCloseReconciliationError",
    "PaperCloseReconciliationIntegrityError",
    "paper_close_client_order_id",
]
