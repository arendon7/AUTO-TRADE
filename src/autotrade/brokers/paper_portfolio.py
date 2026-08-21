from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Mapping
from urllib.parse import parse_qs, urlsplit

from .alpaca_paper_gateway import (
    ALPACA_LIVE_TRADING_HOST,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperPolicyError,
    AlpacaPaperReadRequest,
    AlpacaPaperReadTransport,
    AlpacaPaperUnavailable,
    UrllibAlpacaPaperReadTransport,
)


POSITIONS_PATH = "/v2/positions"
ORDERS_PATH = "/v2/orders"
OPEN_ORDERS_QUERY = "status=open&limit=500&direction=asc&nested=true"
USER_AGENT = "AUTO-TRADE-R7/0.1"
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_EQUITY_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,31}$")
_COMPACT_CRYPTO_RE = re.compile(r"^[A-Z0-9]{5,24}$")
_CRYPTO_QUOTES = ("USDT", "USDC", "USD")


class PaperPortfolioError(RuntimeError):
    pass


class PaperPortfolioDisabled(PaperPortfolioError):
    pass


class PaperPortfolioIntegrityError(PaperPortfolioError):
    pass


@dataclass(frozen=True, slots=True)
class PaperPortfolioReadPolicy:
    allowed_host: str = ALPACA_PAPER_TRADING_HOST

    def validate(self, request: AlpacaPaperReadRequest) -> None:
        if request.method != "GET":
            raise AlpacaPaperPolicyError("PAPER portfolio truth is GET-only")
        if not 0 < request.timeout_seconds <= 15:
            raise AlpacaPaperPolicyError("PAPER portfolio timeout is invalid")
        parsed = urlsplit(request.url)
        if parsed.scheme != "https":
            raise AlpacaPaperPolicyError("PAPER portfolio requires HTTPS")
        if parsed.hostname != self.allowed_host or parsed.hostname == ALPACA_LIVE_TRADING_HOST:
            raise AlpacaPaperPolicyError("PAPER portfolio host is not exactly allowlisted")
        if parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment:
            raise AlpacaPaperPolicyError("PAPER portfolio URL is non-canonical")
        if parsed.path == POSITIONS_PATH:
            if parsed.query:
                raise AlpacaPaperPolicyError("PAPER portfolio positions query is forbidden")
        elif parsed.path == ORDERS_PATH:
            if not _is_exact_open_orders_query(parsed.query):
                raise AlpacaPaperPolicyError("PAPER portfolio open-orders query is not canonical")
        else:
            raise AlpacaPaperPolicyError("PAPER portfolio path is not allowlisted")
        _validate_auth_headers(request.headers)

    def validate_final_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != self.allowed_host:
            raise AlpacaPaperPolicyError("PAPER portfolio final URL host is not allowlisted")
        if parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment:
            raise AlpacaPaperPolicyError("PAPER portfolio final URL is non-canonical")
        if parsed.path == POSITIONS_PATH and not parsed.query:
            return
        if parsed.path == ORDERS_PATH and _is_exact_open_orders_query(parsed.query):
            return
        raise AlpacaPaperPolicyError("PAPER portfolio final URL path/query is not allowlisted")


@dataclass(frozen=True, slots=True)
class PaperPortfolioPosition:
    asset_id: str
    broker_symbol: str
    symbol: str
    asset_class: str
    exchange: str
    side: str
    quantity: Decimal
    available_quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    market_value: Decimal
    cost_basis: Decimal
    unrealized_pl: Decimal
    unrealized_plpc: Decimal

    @property
    def risk_direction(self) -> str:
        return "LONG" if self.quantity > 0 else "SHORT"

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "broker_symbol": self.broker_symbol,
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "exchange": self.exchange,
            "side": self.side,
            "quantity": _decimal_text(self.quantity),
            "available_quantity": _decimal_text(self.available_quantity),
            "avg_entry_price": _decimal_text(self.avg_entry_price),
            "current_price": _decimal_text(self.current_price),
            "market_value": _decimal_text(self.market_value),
            "cost_basis": _decimal_text(self.cost_basis),
            "unrealized_pl": _decimal_text(self.unrealized_pl),
            "unrealized_plpc": _decimal_text(self.unrealized_plpc),
            "risk_direction": self.risk_direction,
        }


@dataclass(frozen=True, slots=True)
class PaperPortfolioOpenOrder:
    broker_order_id: str
    client_order_id: str
    broker_symbol: str
    symbol: str
    asset_class: str
    side: str
    order_type: str
    time_in_force: str
    status: str
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None

    def to_dict(self) -> dict[str, object]:
        return {
            "broker_order_id": self.broker_order_id,
            "client_order_id": self.client_order_id,
            "broker_symbol": self.broker_symbol,
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "side": self.side,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "status": self.status,
            "quantity": _decimal_text(self.quantity),
            "filled_quantity": _decimal_text(self.filled_quantity),
            "limit_price": _optional_decimal_text(self.limit_price),
            "stop_price": _optional_decimal_text(self.stop_price),
        }


@dataclass(frozen=True, slots=True)
class PaperPortfolioSnapshot:
    account: AlpacaPaperAccountAttestation
    positions: tuple[PaperPortfolioPosition, ...]
    open_orders: tuple[PaperPortfolioOpenOrder, ...]
    positions_request_id: str
    orders_request_id: str
    positions_response_sha256: str
    orders_response_sha256: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        for value in (self.positions_response_sha256, self.orders_response_sha256):
            if not _HASH_RE.fullmatch(value):
                raise ValueError("portfolio response fingerprint must be sha256")
        for value in (self.positions_request_id, self.orders_request_id):
            if not _REQUEST_ID_RE.fullmatch(value):
                raise ValueError("portfolio request id is invalid")

    @property
    def gross_exposure(self) -> Decimal:
        return sum((abs(item.market_value) for item in self.positions), Decimal("0"))

    @property
    def net_market_value(self) -> Decimal:
        return sum((item.market_value for item in self.positions), Decimal("0"))

    @property
    def unrealized_pl(self) -> Decimal:
        return sum((item.unrealized_pl for item in self.positions), Decimal("0"))

    @property
    def fingerprint(self) -> str:
        return _hash_json(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account.account_id,
            "account_reference": self.account.account_reference,
            "credential_reference": self.account.credential_reference,
            "portfolio_value": _decimal_text(self.account.portfolio_value),
            "buying_power": _decimal_text(self.account.buying_power),
            "position_count": len(self.positions),
            "open_order_count": len(self.open_orders),
            "gross_exposure": _decimal_text(self.gross_exposure),
            "net_market_value": _decimal_text(self.net_market_value),
            "unrealized_pl": _decimal_text(self.unrealized_pl),
            "positions": [item.to_dict() for item in self.positions],
            "open_orders": [item.to_dict() for item in self.open_orders],
            "positions_request_id": self.positions_request_id,
            "orders_request_id": self.orders_request_id,
            "positions_response_sha256": self.positions_response_sha256,
            "orders_response_sha256": self.orders_response_sha256,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "broker_write_performed": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        }


class AlpacaPaperPortfolioGateway:
    """Broker-truth PAPER portfolio snapshot with no write surface.

    The gateway performs one account GET followed by exact GETs for current
    positions and open orders. It never exposes order submission, cancellation,
    replacement or LIVE endpoints. Credentials are accepted only for the calls
    and are reduced to the existing credential_reference in returned evidence.
    """

    def __init__(
        self,
        *,
        config: AlpacaPaperGatewayConfig | None = None,
        account_gateway: AlpacaPaperAccountGateway | None = None,
        transport: AlpacaPaperReadTransport | None = None,
        policy: PaperPortfolioReadPolicy | None = None,
    ) -> None:
        self._config = config or AlpacaPaperGatewayConfig()
        self._policy = policy or PaperPortfolioReadPolicy()
        self._transport = transport or UrllibAlpacaPaperReadTransport(
            policy=self._policy,  # type: ignore[arg-type]
            max_response_bytes=self._config.max_response_bytes,
        )
        self._account_gateway = account_gateway or AlpacaPaperAccountGateway(config=self._config)

    def snapshot(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        expected_account_id: str,
        now: datetime,
    ) -> PaperPortfolioSnapshot:
        if not self._config.enabled:
            raise PaperPortfolioDisabled("PAPER portfolio gateway is disabled by default")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        instant = now.astimezone(timezone.utc)
        account = self._account_gateway.attest_account(
            credentials=credentials,
            expected_account_id=expected_account_id,
            now=instant,
        )
        if account.credential_reference != credentials.credential_reference:
            raise PaperPortfolioIntegrityError("PAPER portfolio account credential binding mismatch")
        positions_response = self._read(credentials=credentials, path=POSITIONS_PATH, query=None)
        orders_response = self._read(credentials=credentials, path=ORDERS_PATH, query=OPEN_ORDERS_QUERY)
        positions_payload = _strict_json_array(positions_response, "positions")
        orders_payload = _strict_json_array(orders_response, "open orders")
        positions = tuple(_parse_position(item) for item in positions_payload)
        orders = tuple(_parse_open_order(item) for item in orders_payload)
        return PaperPortfolioSnapshot(
            account=account,
            positions=positions,
            open_orders=orders,
            positions_request_id=_request_id(positions_response),
            orders_request_id=_request_id(orders_response),
            positions_response_sha256=sha256(positions_response.body).hexdigest(),
            orders_response_sha256=sha256(orders_response.body).hexdigest(),
            observed_at=instant,
        )

    def _read(self, *, credentials: AlpacaPaperCredentials, path: str, query: str | None) -> AlpacaPaperHttpResponse:
        url = f"{self._config.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = AlpacaPaperReadRequest(
            method="GET",
            url=url,
            timeout_seconds=self._config.timeout_seconds,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
            },
        )
        self._policy.validate(request)
        response = self._transport.read(request)
        self._policy.validate_final_url(response.final_url)
        if response.status_code != 200:
            raise AlpacaPaperUnavailable(f"unexpected PAPER portfolio status: {response.status_code}")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise PaperPortfolioIntegrityError("PAPER portfolio response must be application/json")
        return response


def _parse_position(raw: object) -> PaperPortfolioPosition:
    payload = _mapping(raw, "position")
    asset_class = _string(payload, "asset_class").lower()
    broker_symbol = _string(payload, "symbol").upper()
    symbol = _canonical_symbol(broker_symbol, asset_class)
    quantity = _decimal(payload, "qty", allow_negative=True)
    if quantity == 0:
        raise PaperPortfolioIntegrityError("broker position quantity may not be zero")
    side = _string(payload, "side").lower()
    if side not in {"long", "short"}:
        raise PaperPortfolioIntegrityError("broker position side is invalid")
    if (quantity > 0 and side != "long") or (quantity < 0 and side != "short"):
        raise PaperPortfolioIntegrityError("broker position quantity/side mismatch")
    available = _decimal_optional(payload, "qty_available")
    if available is None:
        available = abs(quantity)
    if available < 0 or available > abs(quantity):
        raise PaperPortfolioIntegrityError("broker available position quantity is invalid")
    return PaperPortfolioPosition(
        asset_id=_string(payload, "asset_id"),
        broker_symbol=broker_symbol,
        symbol=symbol,
        asset_class=asset_class,
        exchange=_string(payload, "exchange"),
        side=side,
        quantity=quantity,
        available_quantity=available,
        avg_entry_price=_decimal(payload, "avg_entry_price"),
        current_price=_decimal(payload, "current_price"),
        market_value=_decimal(payload, "market_value", allow_negative=True),
        cost_basis=_decimal(payload, "cost_basis", allow_negative=True),
        unrealized_pl=_decimal(payload, "unrealized_pl", allow_negative=True),
        unrealized_plpc=_decimal(payload, "unrealized_plpc", allow_negative=True),
    )


def _parse_open_order(raw: object) -> PaperPortfolioOpenOrder:
    payload = _mapping(raw, "open order")
    asset_class = _string(payload, "asset_class").lower()
    broker_symbol = _string(payload, "symbol").upper()
    symbol = _canonical_symbol(broker_symbol, asset_class)
    status = _string(payload, "status").lower()
    if status in {"filled", "canceled", "expired", "rejected"}:
        raise PaperPortfolioIntegrityError("terminal order appeared in canonical open-order response")
    quantity = _decimal(payload, "qty")
    filled = _decimal(payload, "filled_qty", allow_zero=True)
    if filled > quantity:
        raise PaperPortfolioIntegrityError("open order filled quantity exceeds requested quantity")
    side = _string(payload, "side").lower()
    if side not in {"buy", "sell"}:
        raise PaperPortfolioIntegrityError("open order side is invalid")
    return PaperPortfolioOpenOrder(
        broker_order_id=_string(payload, "id"),
        client_order_id=_string(payload, "client_order_id"),
        broker_symbol=broker_symbol,
        symbol=symbol,
        asset_class=asset_class,
        side=side,
        order_type=_string(payload, "type").lower(),
        time_in_force=_string(payload, "time_in_force").lower(),
        status=status,
        quantity=quantity,
        filled_quantity=filled,
        limit_price=_decimal_optional(payload, "limit_price"),
        stop_price=_decimal_optional(payload, "stop_price"),
    )


def _canonical_symbol(broker_symbol: str, asset_class: str) -> str:
    if asset_class == "crypto":
        if "/" in broker_symbol:
            pieces = broker_symbol.split("/")
            if len(pieces) == 2 and all(pieces):
                return f"{pieces[0]}/{pieces[1]}"
            raise PaperPortfolioIntegrityError("broker crypto pair is malformed")
        if not _COMPACT_CRYPTO_RE.fullmatch(broker_symbol):
            raise PaperPortfolioIntegrityError("broker compact crypto symbol is malformed")
        for quote in _CRYPTO_QUOTES:
            if broker_symbol.endswith(quote):
                base = broker_symbol[: -len(quote)]
                if 2 <= len(base) <= 12 and base != quote:
                    return f"{base}/{quote}"
        raise PaperPortfolioIntegrityError("broker compact crypto quote is not allowlisted")
    if asset_class in {"us_equity", "equity"}:
        if not _EQUITY_SYMBOL_RE.fullmatch(broker_symbol):
            raise PaperPortfolioIntegrityError("broker equity symbol is malformed")
        return broker_symbol
    raise PaperPortfolioIntegrityError(f"unsupported PAPER portfolio asset_class: {asset_class}")


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise PaperPortfolioIntegrityError(f"PAPER {label} entry must be an object")
    return raw


def _strict_json_array(response: AlpacaPaperHttpResponse, label: str) -> list[object]:
    try:
        value = json.loads(response.body.decode("utf-8", errors="strict"), parse_constant=lambda token: _raise_constant(token))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperPortfolioIntegrityError(f"PAPER {label} response is not strict JSON") from exc
    if not isinstance(value, list):
        raise PaperPortfolioIntegrityError(f"PAPER {label} response root must be an array")
    return value


def _request_id(response: AlpacaPaperHttpResponse) -> str:
    value = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(value):
        raise PaperPortfolioIntegrityError("PAPER portfolio response lacks valid X-Request-ID")
    return value


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PaperPortfolioIntegrityError(f"PAPER portfolio field {key} is required")
    result = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise PaperPortfolioIntegrityError(f"PAPER portfolio field {key} contains control characters")
    return result


def _decimal(payload: Mapping[str, object], key: str, *, allow_zero: bool = False, allow_negative: bool = False) -> Decimal:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PaperPortfolioIntegrityError(f"PAPER portfolio field {key} must be decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise PaperPortfolioIntegrityError(f"PAPER portfolio field {key} is invalid") from exc
    if not result.is_finite():
        raise PaperPortfolioIntegrityError(f"PAPER portfolio field {key} must be finite")
    if allow_negative:
        return result
    if result < 0 or (result == 0 and not allow_zero):
        raise PaperPortfolioIntegrityError(f"PAPER portfolio field {key} must be positive")
    return result


def _decimal_optional(payload: Mapping[str, object], key: str) -> Decimal | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise PaperPortfolioIntegrityError(f"PAPER portfolio optional field {key} must be decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise PaperPortfolioIntegrityError(f"PAPER portfolio optional field {key} is invalid") from exc
    if not result.is_finite() or result < 0:
        raise PaperPortfolioIntegrityError(f"PAPER portfolio optional field {key} must be finite non-negative")
    return result


def _validate_auth_headers(headers: Mapping[str, str]) -> None:
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    expected = {"accept", "user-agent", "apca-api-key-id", "apca-api-secret-key"}
    if set(normalized) != expected:
        raise AlpacaPaperPolicyError("PAPER portfolio headers must match exact allowlist")
    if normalized["accept"] != "application/json" or normalized["user-agent"] != USER_AGENT:
        raise AlpacaPaperPolicyError("PAPER portfolio headers are non-canonical")
    for key in ("apca-api-key-id", "apca-api-secret-key"):
        value = normalized[key]
        if not value or value != value.strip() or len(value) > 512:
            raise AlpacaPaperPolicyError(f"PAPER portfolio {key} is invalid")
        if any(ord(char) < 33 or ord(char) == 127 for char in value):
            raise AlpacaPaperPolicyError(f"PAPER portfolio {key} contains whitespace/control characters")


def _is_exact_open_orders_query(query: str) -> bool:
    try:
        parsed = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return parsed == {"status": ["open"], "limit": ["500"], "direction": ["asc"], "nested": ["true"]} and len(query.split("&")) == 4


def _raise_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _hash_json(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


__all__ = [
    "AlpacaPaperPortfolioGateway",
    "PaperPortfolioDisabled",
    "PaperPortfolioError",
    "PaperPortfolioIntegrityError",
    "PaperPortfolioOpenOrder",
    "PaperPortfolioPosition",
    "PaperPortfolioReadPolicy",
    "PaperPortfolioSnapshot",
]
