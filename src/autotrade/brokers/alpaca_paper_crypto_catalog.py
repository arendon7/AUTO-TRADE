from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import Mapping

from .alpaca_paper_crypto_asset import (
    CURRENT_TRADING_API_CRYPTO_EXCHANGE,
    normalize_crypto_pair,
)
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


CRYPTO_ASSETS_PATH = "/v2/assets"
CRYPTO_ASSETS_QUERY = "status=active&asset_class=crypto&exchange=CRYPTO"


class PaperCryptoCatalogError(RuntimeError):
    pass


class PaperCryptoCatalogDisabled(PaperCryptoCatalogError):
    pass


class PaperCryptoCatalogIntegrityError(PaperCryptoCatalogError):
    pass


@dataclass(frozen=True, slots=True)
class PaperCryptoCatalogReadPolicy:
    allowed_host: str = ALPACA_PAPER_TRADING_HOST

    @property
    def expected_url(self) -> str:
        return "https" + "://" + self.allowed_host + CRYPTO_ASSETS_PATH + "?" + CRYPTO_ASSETS_QUERY

    def validate(self, request: AlpacaPaperReadRequest) -> None:
        if request.method != "GET":
            raise AlpacaPaperPolicyError("PAPER crypto catalog is GET-only")
        if not 0 < request.timeout_seconds <= 15:
            raise AlpacaPaperPolicyError("PAPER crypto catalog timeout is invalid")
        if request.url != self.expected_url:
            raise AlpacaPaperPolicyError("PAPER crypto catalog URL is not exact allowlist")
        _validate_auth_headers(request.headers)

    def validate_final_url(self, url: str) -> None:
        if url != self.expected_url:
            raise AlpacaPaperPolicyError("PAPER crypto catalog final URL changed")


@dataclass(frozen=True, slots=True)
class PaperCryptoCatalogItem:
    symbol: str
    name: str
    asset_id: str
    min_order_size: Decimal
    min_trade_increment: Decimal
    price_increment: Decimal

    def __post_init__(self) -> None:
        canonical = normalize_crypto_pair(self.symbol)
        if canonical != self.symbol:
            raise ValueError("catalog symbol must be canonical BASE/QUOTE")
        if not self.name.strip() or not self.asset_id.strip():
            raise ValueError("catalog item name and asset id are required")
        for label, value in (
            ("min_order_size", self.min_order_size),
            ("min_trade_increment", self.min_trade_increment),
            ("price_increment", self.price_increment),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"catalog {label} must be finite and positive")

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_id": self.asset_id,
            "min_order_size": str(self.min_order_size),
            "min_trade_increment": str(self.min_trade_increment),
            "price_increment": str(self.price_increment),
        }


class AlpacaPaperCryptoCatalogGateway:
    """Exact read-only PAPER list of currently eligible R6 crypto pairs."""

    def __init__(
        self,
        *,
        config: AlpacaPaperGatewayConfig | None = None,
        transport: AlpacaPaperReadTransport | None = None,
        policy: PaperCryptoCatalogReadPolicy | None = None,
    ) -> None:
        self._config = config or AlpacaPaperGatewayConfig()
        self._policy = policy or PaperCryptoCatalogReadPolicy()
        self._transport = transport or UrllibAlpacaPaperReadTransport(
            policy=self._policy,  # type: ignore[arg-type]
            max_response_bytes=self._config.max_response_bytes,
        )

    def list_pairs(self, *, credentials: AlpacaPaperCredentials) -> tuple[PaperCryptoCatalogItem, ...]:
        if not self._config.enabled:
            raise PaperCryptoCatalogDisabled("PAPER crypto catalog is disabled")
        request = AlpacaPaperReadRequest(
            method="GET",
            url=self._policy.expected_url,
            timeout_seconds=self._config.timeout_seconds,
            headers={
                "Accept": "application/json",
                "User-Agent": "AUTO-TRADE-R6/0.28R",
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
            },
        )
        self._policy.validate(request)
        response = self._transport.read(request)
        self._policy.validate_final_url(response.final_url)
        return _parse_catalog(response)


def _parse_catalog(response: AlpacaPaperHttpResponse) -> tuple[PaperCryptoCatalogItem, ...]:
    if response.status_code != 200:
        raise AlpacaPaperUnavailable(f"unexpected PAPER crypto catalog status: {response.status_code}")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise PaperCryptoCatalogIntegrityError("PAPER crypto catalog response must be application/json")
    try:
        payload = json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaperCryptoCatalogIntegrityError("PAPER crypto catalog response is invalid JSON") from exc
    if not isinstance(payload, list):
        raise PaperCryptoCatalogIntegrityError("PAPER crypto catalog root must be array")
    if len(payload) > 500:
        raise PaperCryptoCatalogIntegrityError("PAPER crypto catalog unexpectedly exceeds 500 assets")

    items: list[PaperCryptoCatalogItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise PaperCryptoCatalogIntegrityError(f"crypto catalog item {index} must be object")
        if _string(raw, "class").lower() != "crypto":
            raise PaperCryptoCatalogIntegrityError("crypto catalog contains non-crypto asset")
        if _string(raw, "exchange").upper() != CURRENT_TRADING_API_CRYPTO_EXCHANGE:
            raise PaperCryptoCatalogIntegrityError("crypto catalog contains stale/unexpected exchange enum")
        if _string(raw, "status").lower() != "active":
            raise PaperCryptoCatalogIntegrityError("crypto catalog contains inactive asset")
        if _bool(raw, "tradable") is not True or _bool(raw, "fractionable") is not True:
            raise PaperCryptoCatalogIntegrityError("crypto catalog contains non-tradable/non-fractionable asset")
        if _bool(raw, "marginable") is not False or _bool(raw, "shortable") is not False:
            raise PaperCryptoCatalogIntegrityError("crypto catalog violates current no-margin/no-short policy")
        symbol = normalize_crypto_pair(_string(raw, "symbol"))
        if symbol in seen:
            raise PaperCryptoCatalogIntegrityError(f"duplicate crypto pair in catalog: {symbol}")
        seen.add(symbol)
        items.append(
            PaperCryptoCatalogItem(
                symbol=symbol,
                name=_string(raw, "name"),
                asset_id=_string(raw, "id"),
                min_order_size=_positive_decimal(raw.get("min_order_size"), "min_order_size"),
                min_trade_increment=_positive_decimal(raw.get("min_trade_increment"), "min_trade_increment"),
                price_increment=_positive_decimal(raw.get("price_increment"), "price_increment"),
            )
        )
    if not items:
        raise PaperCryptoCatalogIntegrityError("PAPER crypto catalog returned no eligible pairs")
    return tuple(sorted(items, key=lambda item: item.symbol))


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PaperCryptoCatalogIntegrityError(f"crypto catalog field {key} is required")
    return value.strip()


def _bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise PaperCryptoCatalogIntegrityError(f"crypto catalog field {key} must be boolean")
    return value


def _positive_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise PaperCryptoCatalogIntegrityError(f"crypto catalog {label} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PaperCryptoCatalogIntegrityError(f"crypto catalog {label} is invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise PaperCryptoCatalogIntegrityError(f"crypto catalog {label} must be positive")
    return parsed


__all__ = [
    "AlpacaPaperCryptoCatalogGateway",
    "CRYPTO_ASSETS_PATH",
    "CRYPTO_ASSETS_QUERY",
    "PaperCryptoCatalogDisabled",
    "PaperCryptoCatalogError",
    "PaperCryptoCatalogIntegrityError",
    "PaperCryptoCatalogItem",
]
