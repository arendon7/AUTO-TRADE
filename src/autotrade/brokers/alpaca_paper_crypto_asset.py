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
CRYPTO_ASSET_LOOKUP = "BTCUSD"
CRYPTO_ASSET_PATH = f"/v2/assets/{CRYPTO_ASSET_LOOKUP}"
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperCryptoAssetError(RuntimeError):
    pass


class PaperCryptoAssetDisabled(PaperCryptoAssetError):
    pass


class PaperCryptoAssetIntegrityError(PaperCryptoAssetError):
    pass


@dataclass(frozen=True, slots=True)
class PaperCryptoAssetReadPolicy:
    allowed_host: str = ALPACA_PAPER_TRADING_HOST

    def validate(self, request: AlpacaPaperReadRequest) -> None:
        if request.method != "GET":
            raise AlpacaPaperPolicyError("PAPER crypto asset preflight is GET-only")
        if not 0 < request.timeout_seconds <= 15:
            raise AlpacaPaperPolicyError("PAPER crypto asset timeout is invalid")
        expected = "https" + "://" + self.allowed_host + CRYPTO_ASSET_PATH
        if request.url != expected:
            raise AlpacaPaperPolicyError("PAPER crypto asset URL is not exact BTC/USD allowlist")
        _validate_auth_headers(request.headers)

    def validate_final_url(self, url: str) -> None:
        expected = "https" + "://" + self.allowed_host + CRYPTO_ASSET_PATH
        if url != expected:
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
    source_host: str = ALPACA_PAPER_TRADING_HOST
    source_path: str = CRYPTO_ASSET_PATH

    def __post_init__(self) -> None:
        if self.symbol != CRYPTO_PAIR:
            raise ValueError("crypto rehearsal is pinned to BTC/USD")
        if self.asset_class != "crypto" or self.exchange != "ALPACA":
            raise ValueError("crypto rehearsal requires exact Alpaca crypto asset")
        if self.status != "active" or self.tradable is not True or self.fractionable is not True:
            raise ValueError("BTC/USD must be active, tradable and fractionable")
        if self.marginable is not False or self.shortable is not False:
            raise ValueError("crypto rehearsal forbids margin and shorting")
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
        if self.source_host != ALPACA_PAPER_TRADING_HOST or self.source_path != CRYPTO_ASSET_PATH:
            raise ValueError("crypto asset source is not exact PAPER BTC/USD endpoint")

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
    """One exact GET for BTC/USD metadata; no mutation or order surface."""

    def __init__(
        self,
        *,
        config: AlpacaPaperGatewayConfig | None = None,
        transport: AlpacaPaperReadTransport | None = None,
        policy: PaperCryptoAssetReadPolicy | None = None,
    ) -> None:
        self._config = config or AlpacaPaperGatewayConfig()
        self._policy = policy or PaperCryptoAssetReadPolicy()
        self._transport = transport or UrllibAlpacaPaperReadTransport(
            policy=self._policy,  # type: ignore[arg-type]
            max_response_bytes=self._config.max_response_bytes,
        )

    def attest_asset(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        account_attestation_fingerprint: str,
        expected_credential_reference: str,
        now: datetime,
    ) -> AlpacaPaperCryptoAssetAttestation:
        if not self._config.enabled:
            raise PaperCryptoAssetDisabled("PAPER crypto asset preflight is disabled")
        if not _HASH_RE.fullmatch(account_attestation_fingerprint):
            raise ValueError("account attestation fingerprint must be SHA-256")
        if credentials.credential_reference != expected_credential_reference:
            raise PaperCryptoAssetIntegrityError("PAPER credentials do not match account evidence")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        url = "https" + "://" + self._policy.allowed_host + CRYPTO_ASSET_PATH
        request = AlpacaPaperReadRequest(
            method="GET",
            url=url,
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
        return _parse_asset(
            response=response,
            account_attestation_fingerprint=account_attestation_fingerprint,
            credential_reference=credentials.credential_reference,
            now=now,
        )


def _parse_asset(
    *,
    response: AlpacaPaperHttpResponse,
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
    return AlpacaPaperCryptoAssetAttestation(
        symbol=_string(payload, "symbol"),
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
    "AlpacaPaperCryptoAssetAttestation",
    "AlpacaPaperCryptoAssetGateway",
    "PaperCryptoAssetError",
    "PaperCryptoAssetIntegrityError",
]
