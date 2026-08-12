from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Mapping

from .alpaca_paper_gateway import (
    ALPACA_LIVE_TRADING_HOST,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperIntegrityError,
    AlpacaPaperPolicyError,
    AlpacaPaperReadRequest,
    AlpacaPaperReadTransport,
    AlpacaPaperUnavailable,
    UrllibAlpacaPaperReadTransport,
    _validate_auth_headers,
)


ASSET_PATH_PREFIX = "/v2/assets/"
_ALLOWED_EXCHANGES = frozenset({"NASDAQ", "NYSE", "ARCA", "NYSEARCA", "AMEX", "BATS"})
_DENIED_FIRST_CANARY_ATTRIBUTES = frozenset({"ipo", "ptp_no_exception", "ptp_with_exception"})
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperAssetError(RuntimeError):
    pass


class PaperAssetDisabled(PaperAssetError):
    pass


class PaperAssetIntegrityError(PaperAssetError):
    pass


@dataclass(frozen=True, slots=True)
class PaperAssetReadPolicy:
    allowed_host: str = ALPACA_PAPER_TRADING_HOST

    def validate(self, request: AlpacaPaperReadRequest) -> None:
        if request.method != "GET":
            raise AlpacaPaperPolicyError("PAPER asset preflight is GET-only")
        if not 0 < request.timeout_seconds <= 15:
            raise AlpacaPaperPolicyError("PAPER asset timeout is invalid")
        path = _exact_path_from_url(request.url, allowed_host=self.allowed_host)
        _symbol_from_path(path)
        _validate_auth_headers(request.headers)

    def validate_final_url(self, url: str) -> None:
        path = _exact_path_from_url(url, allowed_host=self.allowed_host)
        _symbol_from_path(path)


@dataclass(frozen=True, slots=True)
class AlpacaPaperEquityAssetAttestation:
    symbol: str
    asset_id: str
    asset_class: str
    exchange: str
    status: str
    tradable: bool
    fractionable: bool
    min_order_size: Decimal
    min_trade_increment: Decimal
    price_increment: Decimal
    attributes: tuple[str, ...]
    account_attestation_fingerprint: str
    credential_reference: str
    observed_at: datetime
    request_id: str
    response_sha256: str
    source_host: str = ALPACA_PAPER_TRADING_HOST
    source_path: str = ""

    def __post_init__(self) -> None:
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("asset symbol must be canonical uppercase US-equity symbol")
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("asset_id is required")
        if self.asset_class != "us_equity":
            raise ValueError("first PAPER canary requires exact us_equity asset class")
        if self.exchange not in _ALLOWED_EXCHANGES:
            raise ValueError("first PAPER canary exchange is not allowlisted")
        if self.status != "active":
            raise ValueError("first PAPER canary asset must be active")
        if self.tradable is not True:
            raise ValueError("first PAPER canary asset must be tradable")
        if not isinstance(self.fractionable, bool):
            raise ValueError("fractionable must be boolean")
        for label, value in (
            ("min_order_size", self.min_order_size),
            ("min_trade_increment", self.min_trade_increment),
            ("price_increment", self.price_increment),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{label} must be finite and positive")
        if self.min_order_size > Decimal("1"):
            raise ValueError("first PAPER canary requires one whole share to satisfy min_order_size")
        if Decimal("1") % self.min_trade_increment != 0:
            raise ValueError("first PAPER canary requires one whole share to align to min_trade_increment")
        if tuple(sorted(set(self.attributes))) != self.attributes:
            raise ValueError("asset attributes must be sorted and unique")
        if any(not isinstance(value, str) or not value for value in self.attributes):
            raise ValueError("asset attributes must be non-empty strings")
        denied = _DENIED_FIRST_CANARY_ATTRIBUTES.intersection(self.attributes)
        if denied:
            raise ValueError(f"first PAPER canary rejects asset attributes: {sorted(denied)}")
        for label, value in (
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("credential_reference", self.credential_reference),
            ("response_sha256", self.response_sha256),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("asset observed_at must be timezone-aware")
        if not _REQUEST_ID_RE.fullmatch(self.request_id):
            raise ValueError("asset request_id is invalid")
        if self.source_host != ALPACA_PAPER_TRADING_HOST:
            raise ValueError("asset source host must be exact PAPER Trading API")
        if self.source_path != f"{ASSET_PATH_PREFIX}{self.symbol}":
            raise ValueError("asset source path must match exact requested symbol")

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
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
            "min_order_size": str(self.min_order_size),
            "min_trade_increment": str(self.min_trade_increment),
            "price_increment": str(self.price_increment),
            "attributes": list(self.attributes),
            "account_attestation_fingerprint": self.account_attestation_fingerprint,
            "credential_reference": self.credential_reference,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "request_id": self.request_id,
            "response_sha256": self.response_sha256,
            "source_host": self.source_host,
            "source_path": self.source_path,
            "whole_share_canary_supported": True,
        }


class AlpacaPaperEquityAssetGateway:
    """Exact GET-only PAPER asset metadata gateway; no mutation surface exists here."""

    def __init__(
        self,
        *,
        config: AlpacaPaperGatewayConfig | None = None,
        transport: AlpacaPaperReadTransport | None = None,
        policy: PaperAssetReadPolicy | None = None,
    ) -> None:
        self._config = config or AlpacaPaperGatewayConfig()
        self._policy = policy or PaperAssetReadPolicy()
        self._transport = transport or UrllibAlpacaPaperReadTransport(
            policy=self._policy,  # type: ignore[arg-type]
            max_response_bytes=self._config.max_response_bytes,
        )

    def attest_asset(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        symbol: str,
        account_attestation_fingerprint: str,
        expected_credential_reference: str,
        now: datetime,
    ) -> AlpacaPaperEquityAssetAttestation:
        if not self._config.enabled:
            raise PaperAssetDisabled("PAPER asset preflight is disabled by default")
        canonical_symbol = symbol.strip().upper()
        if not _SYMBOL_RE.fullmatch(canonical_symbol):
            raise ValueError("symbol must be canonical uppercase US-equity symbol")
        if not _HASH_RE.fullmatch(account_attestation_fingerprint):
            raise ValueError("account_attestation_fingerprint must be SHA-256")
        if credentials.credential_reference != expected_credential_reference:
            raise PaperAssetIntegrityError("PAPER credentials do not match account preflight evidence")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        path = f"{ASSET_PATH_PREFIX}{canonical_symbol}"
        request = AlpacaPaperReadRequest(
            method="GET",
            url=f"{self._config.base_url}{path}",
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
        return _attestation_from_response(
            response=response,
            symbol=canonical_symbol,
            account_attestation_fingerprint=account_attestation_fingerprint,
            credential_reference=credentials.credential_reference,
            now=now,
        )


def _attestation_from_response(
    *,
    response: AlpacaPaperHttpResponse,
    symbol: str,
    account_attestation_fingerprint: str,
    credential_reference: str,
    now: datetime,
) -> AlpacaPaperEquityAssetAttestation:
    if response.status_code != 200:
        raise AlpacaPaperUnavailable(f"unexpected PAPER asset status: {response.status_code}")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise AlpacaPaperIntegrityError("PAPER asset response must be application/json")
    request_id = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise AlpacaPaperIntegrityError("PAPER asset response is missing a valid X-Request-ID")
    payload = _strict_json_object(response.body)
    if _string(payload, "symbol").upper() != symbol:
        raise PaperAssetIntegrityError("PAPER asset symbol does not match explicit request")
    path = _exact_path_from_url(response.final_url, allowed_host=ALPACA_PAPER_TRADING_HOST)
    try:
        return AlpacaPaperEquityAssetAttestation(
            symbol=symbol,
            asset_id=_string(payload, "id"),
            asset_class=_string(payload, "class"),
            exchange=_string(payload, "exchange").upper(),
            status=_string(payload, "status").lower(),
            tradable=_boolean(payload, "tradable"),
            fractionable=_boolean(payload, "fractionable"),
            min_order_size=_positive_decimal(payload.get("min_order_size"), "min_order_size"),
            min_trade_increment=_positive_decimal(
                payload.get("min_trade_increment"), "min_trade_increment"
            ),
            price_increment=_positive_decimal(payload.get("price_increment"), "price_increment"),
            attributes=_attributes(payload.get("attributes")),
            account_attestation_fingerprint=account_attestation_fingerprint,
            credential_reference=credential_reference,
            observed_at=now.astimezone(timezone.utc),
            request_id=request_id,
            response_sha256=sha256(response.body).hexdigest(),
            source_host=ALPACA_PAPER_TRADING_HOST,
            source_path=path,
        )
    except ValueError as exc:
        raise PaperAssetIntegrityError(str(exc)) from exc


def _exact_path_from_url(url: str, *, allowed_host: str) -> str:
    if not isinstance(url, str) or not url:
        raise AlpacaPaperPolicyError("PAPER asset URL is required")
    prefix = "https" + "://" + allowed_host
    if allowed_host == ALPACA_LIVE_TRADING_HOST:
        raise AlpacaPaperPolicyError("LIVE Trading API host is forbidden")
    if not url.startswith(prefix + ASSET_PATH_PREFIX):
        raise AlpacaPaperPolicyError("PAPER asset URL is not exact allowlisted HTTPS host/path")
    path = url[len(prefix) :]
    if any(token in path for token in ("?", "#", "@", "%", "//")):
        raise AlpacaPaperPolicyError("PAPER asset URL contains forbidden syntax")
    return path


def _symbol_from_path(path: str) -> str:
    if not path.startswith(ASSET_PATH_PREFIX):
        raise AlpacaPaperPolicyError("PAPER asset path is not allowlisted")
    symbol = path[len(ASSET_PATH_PREFIX) :]
    if not _SYMBOL_RE.fullmatch(symbol):
        raise AlpacaPaperPolicyError("PAPER asset path symbol is not canonical")
    return symbol


def _strict_json_object(raw: bytes) -> Mapping[str, object]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=lambda token: _reject_constant(token))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AlpacaPaperIntegrityError("PAPER asset response is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise AlpacaPaperIntegrityError("PAPER asset response root must be an object")
    return value


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PaperAssetIntegrityError(f"PAPER asset field {key} is required")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PaperAssetIntegrityError(f"PAPER asset field {key} contains control characters")
    return value.strip()


def _boolean(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise PaperAssetIntegrityError(f"PAPER asset field {key} must be boolean")
    return value


def _attributes(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PaperAssetIntegrityError("PAPER asset attributes must be an array")
    attributes: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PaperAssetIntegrityError("PAPER asset attribute must be non-empty string")
        attributes.append(item.strip())
    return tuple(sorted(set(attributes)))


def _positive_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise PaperAssetIntegrityError(f"PAPER asset {label} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PaperAssetIntegrityError(f"PAPER asset {label} is invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise PaperAssetIntegrityError(f"PAPER asset {label} must be finite and positive")
    return parsed


__all__ = [
    "ASSET_PATH_PREFIX",
    "AlpacaPaperEquityAssetAttestation",
    "AlpacaPaperEquityAssetGateway",
    "PaperAssetDisabled",
    "PaperAssetError",
    "PaperAssetIntegrityError",
    "PaperAssetReadPolicy",
]
