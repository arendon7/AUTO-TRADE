from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import http.client
import json
import re
from typing import Mapping, Protocol

from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
)
from autotrade.brokers.paper_portfolio import PaperPortfolioSnapshot
from autotrade.paper_close_lifecycle import (
    PaperCloseLifecycleStatus,
    SQLitePaperCloseLifecycle,
)
from autotrade.paper_close_plan import PaperCryptoClosePlan


ORDERS_PATH = "/v2/orders"
DECISION_TTL = timedelta(seconds=20)
FINAL_PORTFOLIO_TTL = timedelta(seconds=5)
_CONFIRMATION = "CERRAR PAPER"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")


class PaperCloseWriterError(RuntimeError):
    pass


class PaperCloseWriterDisabled(PaperCloseWriterError):
    pass


class PaperCloseWriterBlocked(PaperCloseWriterError):
    pass


class PaperCloseWriterAmbiguous(PaperCloseWriterError):
    """POST authority is burned; caller must reconcile via GET only."""


@dataclass(frozen=True, slots=True)
class PaperCloseOperatorDecision:
    attempt_id: str
    plan_hash: str
    portfolio_fingerprint: str
    symbol: str
    quantity: Decimal
    limit_price: Decimal
    approved: bool
    issued_at: datetime
    expires_at: datetime
    decision_hash: str

    def __post_init__(self) -> None:
        _require_id(self.attempt_id, "attempt_id")
        for label, value in (
            ("plan_hash", self.plan_hash),
            ("portfolio_fingerprint", self.portfolio_fingerprint),
            ("decision_hash", self.decision_hash),
        ):
            _require_hash(value, label)
        if not isinstance(self.quantity, Decimal) or not self.quantity.is_finite() or self.quantity <= 0:
            raise ValueError("operator close quantity must be finite positive Decimal")
        if not isinstance(self.limit_price, Decimal) or not self.limit_price.is_finite() or self.limit_price <= 0:
            raise ValueError("operator close limit must be finite positive Decimal")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at or self.expires_at - self.issued_at > DECISION_TTL:
            raise ValueError("operator close decision TTL is invalid")
        if self.decision_hash != _hash(_decision_payload(self, include_hash=False)):
            raise ValueError("operator close decision hash mismatch")

    def valid_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        return self.approved and self.issued_at.astimezone(timezone.utc) <= instant < self.expires_at.astimezone(timezone.utc)


def issue_paper_close_operator_decision(
    *,
    attempt_id: str,
    plan: PaperCryptoClosePlan,
    confirmation: str,
    now: datetime,
) -> PaperCloseOperatorDecision:
    _require_id(attempt_id, "attempt_id")
    if not isinstance(plan, PaperCryptoClosePlan):
        raise TypeError("operator decision requires PaperCryptoClosePlan")
    _require_aware(now, "now")
    instant = now.astimezone(timezone.utc)
    if instant < plan.prepared_at.astimezone(timezone.utc) or instant >= plan.expires_at.astimezone(timezone.utc):
        raise PaperCloseWriterBlocked("operator decision requires a fresh close plan")
    if confirmation != _CONFIRMATION:
        raise PaperCloseWriterBlocked("operator confirmation phrase does not match CERRAR PAPER")
    values = {
        "attempt_id": attempt_id,
        "plan_hash": plan.plan_hash,
        "portfolio_fingerprint": plan.portfolio_fingerprint,
        "symbol": plan.symbol,
        "quantity": plan.quantity,
        "limit_price": plan.limit_price,
        "approved": True,
        "issued_at": instant,
        "expires_at": min(plan.expires_at.astimezone(timezone.utc), instant + DECISION_TTL),
    }
    return PaperCloseOperatorDecision(
        **values,
        decision_hash=_hash(_decision_payload_from_values(values)),
    )


@dataclass(frozen=True, slots=True)
class PaperCloseWriterConfig:
    enabled: bool = False
    host: str = ALPACA_PAPER_TRADING_HOST
    timeout_seconds: float = 5.0
    max_response_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if self.host != ALPACA_PAPER_TRADING_HOST:
            raise ValueError("R7 close writer host must be exact Alpaca PAPER host")
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("R7 close writer timeout must be >0 and <=15")
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("R7 close writer response limit is invalid")


@dataclass(frozen=True, slots=True)
class PaperCloseWriteResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class PaperCloseWriteTransport(Protocol):
    def post(
        self,
        *,
        host: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> PaperCloseWriteResponse:
        ...


class HttpsPaperCloseWriteTransport:
    """Exact PAPER `/v2/orders` TLS delegate. One request call, no retries."""

    def post(
        self,
        *,
        host: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> PaperCloseWriteResponse:
        if host != ALPACA_PAPER_TRADING_HOST or path != ORDERS_PATH:
            raise PaperCloseWriterBlocked("R7 close transport destination is not exact PAPER orders endpoint")
        connection = http.client.HTTPSConnection(host, timeout=timeout_seconds)
        try:
            connection.request("POST", path, body=body, headers=dict(headers))
            response = connection.getresponse()
            response_body = response.read(max_response_bytes + 1)
            if len(response_body) > max_response_bytes:
                raise PaperCloseWriterAmbiguous("R7 close response exceeded bounded read after POST")
            return PaperCloseWriteResponse(
                status_code=int(response.status),
                body=response_body,
                headers={str(k).lower(): str(v).strip() for k, v in response.getheaders()},
            )
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class PaperCloseWriteReceipt:
    attempt_id: str
    plan_hash: str
    decision_hash: str
    client_order_id: str
    request_payload_sha256: str
    broker_order_id: str
    broker_status: str
    request_id: str
    response_sha256: str
    submitted_at: datetime


class PaperCloseWriter:
    """One-shot R7 manual PAPER close writer.

    All validation and fresh broker-truth checks happen before the lifecycle is
    durably moved to SUBMISSION_UNKNOWN. That transition occurs immediately
    before the transport POST. Once crossed, this method can never be called
    again for the same attempt; only GET-only reconciliation may continue it.
    """

    def __init__(self, *, config: PaperCloseWriterConfig | None = None, transport: PaperCloseWriteTransport | None = None) -> None:
        self._config = config or PaperCloseWriterConfig()
        self._transport = transport or HttpsPaperCloseWriteTransport()

    def submit_once(
        self,
        *,
        lifecycle: SQLitePaperCloseLifecycle,
        attempt_id: str,
        plan: PaperCryptoClosePlan,
        decision: PaperCloseOperatorDecision,
        fresh_portfolio: PaperPortfolioSnapshot,
        credentials: AlpacaPaperCredentials,
        now: datetime,
    ) -> PaperCloseWriteReceipt:
        if not self._config.enabled:
            raise PaperCloseWriterDisabled("R7 PAPER close writer is disabled by default")
        if not isinstance(lifecycle, SQLitePaperCloseLifecycle):
            raise PaperCloseWriterBlocked("authoritative close lifecycle is required")
        if not isinstance(plan, PaperCryptoClosePlan):
            raise PaperCloseWriterBlocked("exact close plan is required")
        if not isinstance(decision, PaperCloseOperatorDecision):
            raise PaperCloseWriterBlocked("exact operator close decision is required")
        if not isinstance(fresh_portfolio, PaperPortfolioSnapshot):
            raise PaperCloseWriterBlocked("fresh broker-truth portfolio is required")
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise PaperCloseWriterBlocked("ephemeral Alpaca PAPER credentials are required")
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)

        state = lifecycle.snapshot(attempt_id).state
        if state.status is not PaperCloseLifecycleStatus.PREPARED or state.submission_attempt_count != 0:
            raise PaperCloseWriterBlocked("close attempt is not PREPARED with zero submissions")
        if state.plan_hash != plan.plan_hash or state.symbol != plan.symbol or state.requested_quantity != plan.quantity:
            raise PaperCloseWriterBlocked("close lifecycle does not match exact plan")
        if instant >= plan.expires_at.astimezone(timezone.utc):
            raise PaperCloseWriterBlocked("close plan expired before write")
        if not decision.valid_at(instant):
            raise PaperCloseWriterBlocked("operator close decision is expired or not approved")
        if (
            decision.attempt_id != attempt_id
            or decision.plan_hash != plan.plan_hash
            or decision.portfolio_fingerprint != plan.portfolio_fingerprint
            or decision.symbol != plan.symbol
            or decision.quantity != plan.quantity
            or decision.limit_price != plan.limit_price
        ):
            raise PaperCloseWriterBlocked("operator decision does not match exact close plan/attempt")
        if credentials.credential_reference != plan.credential_reference:
            raise PaperCloseWriterBlocked("effective PAPER credential differs from prepared close plan")

        self._validate_fresh_portfolio(plan=plan, portfolio=fresh_portfolio, credentials=credentials, now=instant)

        client_order_id = _client_order_id(attempt_id=attempt_id, plan_hash=plan.plan_hash)
        payload = {
            "symbol": plan.symbol,
            "qty": _decimal(plan.quantity),
            "side": "sell",
            "type": "limit",
            "time_in_force": "ioc",
            "limit_price": _decimal(plan.limit_price),
            "client_order_id": client_order_id,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AUTO-TRADE-R7-CLOSE/0.1",
            "APCA-API-KEY-ID": credentials.key_id,
            "APCA-API-SECRET-KEY": credentials.secret_key,
        }

        # Critical ordering invariant: durable UNKNOWN before the one network write.
        lifecycle.mark_submission_unknown(attempt_id, at=instant)
        try:
            response = self._transport.post(
                host=self._config.host,
                path=ORDERS_PATH,
                headers=headers,
                body=body,
                timeout_seconds=self._config.timeout_seconds,
                max_response_bytes=self._config.max_response_bytes,
            )
        except Exception as exc:
            raise PaperCloseWriterAmbiguous(
                "R7 close POST outcome is ambiguous; attempt is burned and requires GET-only reconciliation"
            ) from exc

        if response.status_code not in {200, 201}:
            raise PaperCloseWriterAmbiguous(
                f"R7 close POST returned HTTP {response.status_code}; attempt is burned and requires GET-only reconciliation"
            )
        try:
            document = json.loads(response.body.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaperCloseWriterAmbiguous("R7 close POST response is not valid JSON; reconcile GET-only") from exc
        if not isinstance(document, dict):
            raise PaperCloseWriterAmbiguous("R7 close POST response root is not object; reconcile GET-only")
        broker_order_id = _nonempty(document.get("id"), "broker order id")
        broker_status = _nonempty(document.get("status"), "broker order status").lower()
        returned_client = _nonempty(document.get("client_order_id"), "client_order_id")
        if returned_client != client_order_id:
            raise PaperCloseWriterAmbiguous("R7 close POST response client_order_id mismatch; reconcile GET-only")
        request_id = str(response.headers.get("x-request-id", "")).strip()
        if not _REQUEST_ID_RE.fullmatch(request_id):
            raise PaperCloseWriterAmbiguous("R7 close POST response lacks valid request id; reconcile GET-only")
        return PaperCloseWriteReceipt(
            attempt_id=attempt_id,
            plan_hash=plan.plan_hash,
            decision_hash=decision.decision_hash,
            client_order_id=client_order_id,
            request_payload_sha256=sha256(body).hexdigest(),
            broker_order_id=broker_order_id,
            broker_status=broker_status,
            request_id=request_id,
            response_sha256=sha256(response.body).hexdigest(),
            submitted_at=instant,
        )

    @staticmethod
    def _validate_fresh_portfolio(
        *,
        plan: PaperCryptoClosePlan,
        portfolio: PaperPortfolioSnapshot,
        credentials: AlpacaPaperCredentials,
        now: datetime,
    ) -> None:
        age = now - portfolio.observed_at.astimezone(timezone.utc)
        if age < timedelta(0) or age > FINAL_PORTFOLIO_TTL:
            raise PaperCloseWriterBlocked("final broker portfolio is stale")
        if portfolio.account.account_reference != plan.account_reference:
            raise PaperCloseWriterBlocked("final broker portfolio account differs from close plan")
        if portfolio.account.credential_reference != credentials.credential_reference:
            raise PaperCloseWriterBlocked("final broker portfolio credential binding mismatch")
        matches = [p for p in portfolio.positions if p.symbol == plan.symbol]
        if len(matches) != 1:
            raise PaperCloseWriterBlocked("final broker portfolio requires exactly one target position")
        position = matches[0]
        if position.asset_class != "crypto" or position.side != "long" or position.quantity <= 0:
            raise PaperCloseWriterBlocked("target position is no longer positive long crypto exposure")
        if position.quantity > plan.observed_position_quantity:
            raise PaperCloseWriterBlocked("target exposure increased after close preparation")
        if plan.quantity > position.quantity or plan.quantity > position.available_quantity:
            raise PaperCloseWriterBlocked("planned close exceeds fresh broker position/available quantity")
        if any(order.symbol == plan.symbol and order.side == "sell" for order in portfolio.open_orders):
            raise PaperCloseWriterBlocked("existing SELL order overlaps target close position")
        minimum_limit = position.current_price * (Decimal("1") - plan.max_slippage_bps / Decimal("10000"))
        if plan.limit_price < minimum_limit:
            raise PaperCloseWriterBlocked("close limit violates slippage envelope against fresh broker price")


def _decision_payload(value: PaperCloseOperatorDecision, *, include_hash: bool) -> dict[str, object]:
    payload = {
        "attempt_id": value.attempt_id,
        "plan_hash": value.plan_hash,
        "portfolio_fingerprint": value.portfolio_fingerprint,
        "symbol": value.symbol,
        "quantity": _decimal(value.quantity),
        "limit_price": _decimal(value.limit_price),
        "approved": value.approved,
        "issued_at": value.issued_at.astimezone(timezone.utc).isoformat(),
        "expires_at": value.expires_at.astimezone(timezone.utc).isoformat(),
    }
    if include_hash:
        payload["decision_hash"] = value.decision_hash
    return payload


def _decision_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    return {
        "attempt_id": values["attempt_id"],
        "plan_hash": values["plan_hash"],
        "portfolio_fingerprint": values["portfolio_fingerprint"],
        "symbol": values["symbol"],
        "quantity": _decimal(values["quantity"]),  # type: ignore[arg-type]
        "limit_price": _decimal(values["limit_price"]),  # type: ignore[arg-type]
        "approved": values["approved"],
        "issued_at": values["issued_at"].astimezone(timezone.utc).isoformat(),  # type: ignore[union-attr]
        "expires_at": values["expires_at"].astimezone(timezone.utc).isoformat(),  # type: ignore[union-attr]
    }


def _client_order_id(*, attempt_id: str, plan_hash: str) -> str:
    _require_id(attempt_id, "attempt_id")
    _require_hash(plan_hash, "plan_hash")
    digest = sha256(f"AUTO-TRADE:R7:CLOSE:{attempt_id}:{plan_hash}".encode("utf-8")).hexdigest()
    return f"atr7-close-{digest[:40]}"


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperCloseWriterAmbiguous(f"R7 close POST response lacks {label}; reconcile GET-only")
    return value.strip()


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


__all__ = [
    "DECISION_TTL",
    "FINAL_PORTFOLIO_TTL",
    "HttpsPaperCloseWriteTransport",
    "PaperCloseOperatorDecision",
    "PaperCloseWriteReceipt",
    "PaperCloseWriteResponse",
    "PaperCloseWriteTransport",
    "PaperCloseWriter",
    "PaperCloseWriterAmbiguous",
    "PaperCloseWriterBlocked",
    "PaperCloseWriterConfig",
    "PaperCloseWriterDisabled",
    "issue_paper_close_operator_decision",
]
