from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Mapping

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


CRYPTO_PAIR = "BTC/USD"
_CRYPTO_LEG_RE = re.compile(r"^[A-Z0-9]{2,16}$")
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
CURRENT_TRADING_API_CRYPTO_EXCHANGE = "CRYPTO"


class PaperCryptoAssetError(RuntimeError):
    pass


class PaperCryptoAssetDisabled(PaperCryptoAssetError):
    pass


class PaperCryptoAssetIntegrityError(PaperCryptoAssetError):
    pass


def normalize_crypto_pair(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise TypeError("crypto pair must be string")
    text = symbol.strip().upper()
    if text.count("/") != 1:
        raise ValueError("crypto pair must use canonical BASE/QUOTE form")
    base, quote_currency = text.split("/", 1)
    if not _CRYPTO_LEG_RE.fullmatch(base) or not _CRYPTO_LEG_RE.fullmatch(quote_currency):
        raise ValueError("crypto pair contains unsupported characters or length")
    if base == quote_currency:
        raise ValueError("crypto base and quote currency must differ")
    return f"{base}/{quote_currency}"


def crypto_asset_path(symbol: str) -> str:
    canonical = normalize_crypto_pair(symbol)
    # The strict grammar above guarantees slash is the only character that
    # needs path encoding. Avoid a general URL-construction helper here so the
    # approved R6 network surface remains narrow and statically auditable.
    return f"/v2/assets/{canonical.replace('/', '%2F')}"


CRYPTO_ASSET_PATH = crypto_asset_path(CRYPTO_PAIR)


@dataclass(frozen=True, slots=True)
class PaperCryptoAssetReadPolicy:
    symbol: str = CRYPTO_PAIR
    allowed_host: str = ALPACA_PAPER_TRADING_HOST

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_crypto_pair(self.symbol))

    @property
    def expected_url(self) -> str:
        return "https" + "://" + self.allowed_host + crypto_asset_path(self.symbol)

    def validate(self, request: AlpacaPaperReadRequest) -> None:
        if request.method != "GET":
            raise AlpacaPaperPolicyError("PAPER crypto asset preflight is GET-only")
        if not 0 < request.timeout_seconds <= 15:
            raise AlpacaPaperPolicyError("PAPER crypto asset timeout is invalid")
        if request.url != self.expected_url:
            raise AlpacaPaperPolicyError("PAPER crypto asset URL is not exact pair allowlist")
        _validate_auth_headers(request.headers)

    def validate_final_url(self, url: str) -> None:
        if url != self.expected_url:
            raise AlpacaPaperPolicyError("PAPER crypto asset final URL changed")


@dataclass(frozen=True, slots=True)
class AlpacaPaperCryptoAssetAttestation:
    symbol: str
    asset_id: str
    asset_class: str
    exchange: str
    status: str
    tradable: bool
    fractionable: bool
    marginable: bool
    shortable: bool
    min_order_size: Decimal
    min_trade_increment: Decimal
    price_increment: Decimal
    account_attestation_fingerprint: str
    credential_reference: str
    observed_at: datetime
    request_id: str
    response_sha256: str
    source_path: str
    source_host: str = ALPACA_PAPER_TRADING_HOST

    def __post_init__(self) -> None:
        canonical = normalize_crypto_pair(self.symbol)
        if self.symbol != canonical:
            raise ValueError("crypto asset symbol must be canonical BASE/QUOTE")
        if not self.asset_id.strip():
            raise ValueError("crypto asset id is required")
        if self.asset_class != "crypto":
            raise ValueError("crypto attestation requires asset class crypto")
        # Alpaca Trading API changed its crypto asset exchange enum to CRYPTO in July 2026.
        # Fail closed on stale/other exchange values so provider drift is visible immediately.
        if self.exchange != CURRENT_TRADING_API_CRYPTO_EXCHANGE:
            raise ValueError("crypto asset exchange must match current Alpaca Trading API CRYPTO enum")
        if self.status != "active" or self.tradable is not True or self.fractionable is not True:
            raise ValueError("crypto pair must be active, tradable and fractionable")
        if self.marginable is not False or self.shortable is not False:
            raise ValueError("R6 crypto policy forbids margin and opening short exposure")
        for label, value in (
            ("min_order_size", self.min_order_size),
            ("min_trade_increment", self.min_trade_increment),
            ("price_increment", self.price_increment),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{label} must be finite and positive")
        for label, value in (
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("credential_reference", self.credential_reference),
            ("response_sha256", self.response_sha256),
        ):
            if not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not _REQUEST_ID_RE.fullmatch(self.request_id):
            raise ValueError("crypto asset request id is invalid")
        if self.source_host != ALPACA_PAPER_TRADING_HOST or self.source_path != crypto_asset_path(canonical):
            raise ValueError("crypto asset source is not exact PAPER pair endpoint")

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "asset_id": self.asset_id,
            "asset_class": self.asset_class,
            "exchange": self.exchange,
            "status": self.status,
            "tradable": self.tradable,
            "fractionable": self.fractionable,
            "marginable": self.marginable,
            "shortable": self.shortable,
            "min_order_size": str(self.min_order_size),
            "min_trade_increment": str(self.min_trade_increment),
            "price_increment": str(self.price_increment),
            "account_attestation_fingerprint": self.account_attestation_fingerprint,
            "credential_reference": self.credential_reference,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "request_id": self.request_id,
            "response_sha256": self.response_sha256,
            "source_host": self.source_host,
            "source_path": self.source_path,
        }


class AlpacaPaperCryptoAssetGateway:
    """One exact GET for a canonical crypto pair metadata record; no mutation/order surface."""

    def __init__(
        self,
        *,
        config: AlpacaPaperGatewayConfig | None = None,
        transport: AlpacaPaperReadTransport | None = None,
        policy: PaperCryptoAssetReadPolicy | None = None,
    ) -> None:
        self._config = config or AlpacaPaperGatewayConfig()
        self._policy = policy
        self._transport = transport

    def attest_asset(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        account_attestation_fingerprint: str,
        expected_credential_reference: str,
        now: datetime,
        symbol: str = CRYPTO_PAIR,
    ) -> AlpacaPaperCryptoAssetAttestation:
        if not self._config.enabled:
            raise PaperCryptoAssetDisabled("PAPER crypto asset preflight is disabled")
        canonical = normalize_crypto_pair(symbol)
        if not _HASH_RE.fullmatch(account_attestation_fingerprint):
            raise ValueError("account attestation fingerprint must be SHA-256")
        if credentials.credential_reference != expected_credential_reference:
            raise PaperCryptoAssetIntegrityError("PAPER credentials do not match account evidence")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        policy = self._policy or PaperCryptoAssetReadPolicy(symbol=canonical)
        if policy.symbol != canonical:
            raise AlpacaPaperPolicyError("crypto asset policy pair does not match requested pair")
        transport = self._transport or UrllibAlpacaPaperReadTransport(
            policy=policy,  # type: ignore[arg-type]
            max_response_bytes=self._config.max_response_bytes,
        )
        path = crypto_asset_path(canonical)
        request = AlpacaPaperReadRequest(
            method="GET",
            url=policy.expected_url,
            timeout_seconds=self._config.timeout_seconds,
            headers={
                "Accept": "application/json",
                "User-Agent": "AUTO-TRADE-R6/0.28R",
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
            },
        )
        policy.validate(request)
        response = transport.read(request)
        policy.validate_final_url(response.final_url)
        return _parse_asset(
            response=response,
            expected_symbol=canonical,
            source_path=path,
            account_attestation_fingerprint=account_attestation_fingerprint,
            credential_reference=credentials.credential_reference,
            now=now,
        )


def _parse_asset(
    *,
    response: AlpacaPaperHttpResponse,
    expected_symbol: str,
    source_path: str,
    account_attestation_fingerprint: str,
    credential_reference: str,
    now: datetime,
) -> AlpacaPaperCryptoAssetAttestation:
    if response.status_code != 200:
        raise AlpacaPaperUnavailable(f"unexpected PAPER crypto asset status: {response.status_code}")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise PaperCryptoAssetIntegrityError("PAPER crypto asset response must be application/json")
    request_id = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise PaperCryptoAssetIntegrityError("PAPER crypto asset response lacks X-Request-ID")
    try:
        payload = json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaperCryptoAssetIntegrityError("PAPER crypto asset response is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise PaperCryptoAssetIntegrityError("PAPER crypto asset response root must be object")
    actual_symbol = normalize_crypto_pair(_string(payload, "symbol"))
    if actual_symbol != expected_symbol:
        raise PaperCryptoAssetIntegrityError("PAPER crypto asset response symbol does not match requested pair")
    return AlpacaPaperCryptoAssetAttestation(
        symbol=actual_symbol,
        asset_id=_string(payload, "id"),
        asset_class=_string(payload, "class").lower(),
        exchange=_string(payload, "exchange").upper(),
        status=_string(payload, "status").lower(),
        tradable=_bool(payload, "tradable"),
        fractionable=_bool(payload, "fractionable"),
        marginable=_bool(payload, "marginable"),
        shortable=_bool(payload, "shortable"),
        min_order_size=_positive_decimal(payload.get("min_order_size"), "min_order_size"),
        min_trade_increment=_positive_decimal(payload.get("min_trade_increment"), "min_trade_increment"),
        price_increment=_positive_decimal(payload.get("price_increment"), "price_increment"),
        account_attestation_fingerprint=account_attestation_fingerprint,
        credential_reference=credential_reference,
        observed_at=now.astimezone(timezone.utc),
        request_id=request_id,
        response_sha256=sha256(response.body).hexdigest(),
        source_path=source_path,
    )


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PaperCryptoAssetIntegrityError(f"crypto asset field {key} is required")
    return value.strip()


def _bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise PaperCryptoAssetIntegrityError(f"crypto asset field {key} must be boolean")
    return value


def _positive_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise PaperCryptoAssetIntegrityError(f"crypto asset {label} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PaperCryptoAssetIntegrityError(f"crypto asset {label} is invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise PaperCryptoAssetIntegrityError(f"crypto asset {label} must be positive")
    return parsed


__all__ = [
    "CRYPTO_PAIR",
    "CRYPTO_ASSET_PATH",
    "CURRENT_TRADING_API_CRYPTO_EXCHANGE",
    "AlpacaPaperCryptoAssetAttestation",
    "AlpacaPaperCryptoAssetGateway",
    "PaperCryptoAssetError",
    "PaperCryptoAssetIntegrityError",
    "PaperCryptoAssetReadPolicy",
    "crypto_asset_path",
    "normalize_crypto_pair",
]
