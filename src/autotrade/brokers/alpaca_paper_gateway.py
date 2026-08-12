from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import socket
import ssl
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


ALPACA_PAPER_TRADING_HOST = "paper-api.alpaca.markets"
ALPACA_PAPER_ACCOUNT_PATH = "/v2/account"
ALPACA_LIVE_TRADING_HOST = "api.alpaca.markets"
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_ACCOUNT_ID_RE = re.compile(r"^[0-9a-fA-F-]{16,64}$")


class AlpacaPaperGatewayError(RuntimeError):
    pass


class AlpacaPaperGatewayDisabled(AlpacaPaperGatewayError):
    pass


class AlpacaPaperPolicyError(AlpacaPaperGatewayError):
    pass


class AlpacaPaperUnavailable(AlpacaPaperGatewayError):
    pass


class AlpacaPaperIntegrityError(AlpacaPaperGatewayError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class AlpacaPaperCredentials:
    key_id: str
    secret_key: str

    def __post_init__(self) -> None:
        _validate_secret_field(self.key_id, "key_id")
        _validate_secret_field(self.secret_key, "secret_key")
        if self.key_id == self.secret_key:
            raise ValueError("key_id and secret_key must differ")

    def __repr__(self) -> str:
        return "AlpacaPaperCredentials(key_id=<redacted>, secret_key=<redacted>)"

    @property
    def credential_reference(self) -> str:
        # Secret material is intentionally excluded from all fingerprints,
        # logs, evidence and persistence surfaces.
        return sha256(self.key_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AlpacaPaperGatewayConfig:
    enabled: bool = False
    base_url: str = f"https://{ALPACA_PAPER_TRADING_HOST}"
    timeout_seconds: float = 5.0
    max_response_bytes: int = 128 * 1024

    def __post_init__(self) -> None:
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("timeout_seconds must be > 0 and <= 15")
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("max_response_bytes must be between 1 and 1048576")


@dataclass(frozen=True, slots=True)
class AlpacaPaperReadRequest:
    method: str
    url: str
    timeout_seconds: float
    headers: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class AlpacaPaperHttpResponse:
    status_code: int
    body: bytes
    final_url: str
    headers: Mapping[str, str]


class AlpacaPaperReadTransport(Protocol):
    def read(self, request: AlpacaPaperReadRequest) -> AlpacaPaperHttpResponse: ...


@dataclass(frozen=True, slots=True)
class AlpacaPaperReadPolicy:
    allowed_host: str = ALPACA_PAPER_TRADING_HOST
    allowed_paths: frozenset[str] = frozenset({ALPACA_PAPER_ACCOUNT_PATH})

    def validate(self, request: AlpacaPaperReadRequest) -> None:
        if request.method != "GET":
            raise AlpacaPaperPolicyError("PAPER attestation transport is GET-only")
        if not 0 < request.timeout_seconds <= 15:
            raise AlpacaPaperPolicyError("PAPER request timeout must be > 0 and <= 15 seconds")
        parsed = urlsplit(request.url)
        if parsed.scheme != "https":
            raise AlpacaPaperPolicyError("PAPER Trading API requires HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AlpacaPaperPolicyError("PAPER URL credentials/query/fragment are forbidden")
        if parsed.hostname != self.allowed_host or parsed.port not in (None, 443):
            raise AlpacaPaperPolicyError("PAPER Trading API host is not exactly allowlisted")
        if parsed.hostname == ALPACA_LIVE_TRADING_HOST:
            raise AlpacaPaperPolicyError("LIVE Trading API host is forbidden")
        if parsed.path not in self.allowed_paths:
            raise AlpacaPaperPolicyError("PAPER Trading API path is not allowlisted")
        _validate_auth_headers(request.headers)

    def validate_final_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != self.allowed_host:
            raise AlpacaPaperPolicyError("PAPER response final URL is not allowlisted")
        if parsed.port not in (None, 443) or parsed.path not in self.allowed_paths:
            raise AlpacaPaperPolicyError("PAPER response final URL path/port is not allowlisted")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AlpacaPaperPolicyError("PAPER response final URL is non-canonical")


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AlpacaPaperPolicyError("PAPER Trading API redirects are forbidden")


class UrllibAlpacaPaperReadTransport:
    def __init__(
        self,
        *,
        policy: AlpacaPaperReadPolicy,
        max_response_bytes: int = 128 * 1024,
    ) -> None:
        if not 1 <= max_response_bytes <= 1_048_576:
            raise ValueError("max_response_bytes must be between 1 and 1048576")
        self._policy = policy
        self._max_response_bytes = max_response_bytes
        tls_context = ssl.create_default_context()
        # ProxyHandler({}) prevents HTTPS_PROXY / ALL_PROXY environment values
        # from silently rerouting credential-bearing PAPER requests.
        self._opener = build_opener(
            ProxyHandler({}),
            HTTPSHandler(context=tls_context),
            _RejectRedirectHandler(),
        )

    def read(self, request: AlpacaPaperReadRequest) -> AlpacaPaperHttpResponse:
        self._policy.validate(request)
        raw_request = Request(
            request.url,
            method="GET",
            headers=dict(request.headers),
        )
        try:
            with self._opener.open(raw_request, timeout=request.timeout_seconds) as response:  # noqa: S310
                final_url = response.geturl()
                self._policy.validate_final_url(final_url)
                body = response.read(self._max_response_bytes + 1)
                if len(body) > self._max_response_bytes:
                    raise AlpacaPaperUnavailable("PAPER response exceeded size limit")
                return AlpacaPaperHttpResponse(
                    status_code=int(response.status),
                    body=body,
                    final_url=final_url,
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except AlpacaPaperPolicyError:
            raise
        except HTTPError as exc:
            raise AlpacaPaperUnavailable(f"PAPER Trading API HTTP error: {exc.code}") from exc
        except URLError as exc:
            raise AlpacaPaperUnavailable("PAPER Trading API network request failed") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise AlpacaPaperUnavailable("PAPER Trading API request timed out") from exc


@dataclass(frozen=True, slots=True)
class AlpacaPaperAccountAttestation:
    account_id: str
    account_reference: str
    credential_reference: str
    status: str
    currency: str
    buying_power: Decimal
    portfolio_value: Decimal
    shorting_enabled: bool
    attested_at: datetime
    request_id: str
    source_host: str
    source_path: str

    @property
    def fingerprint(self) -> str:
        payload = {
            "account_id": self.account_id,
            "account_reference": self.account_reference,
            "credential_reference": self.credential_reference,
            "status": self.status,
            "currency": self.currency,
            "buying_power": str(self.buying_power),
            "portfolio_value": str(self.portfolio_value),
            "shorting_enabled": self.shorting_enabled,
            "attested_at": self.attested_at.astimezone(timezone.utc).isoformat(),
            "request_id": self.request_id,
            "source_host": self.source_host,
            "source_path": self.source_path,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        ).hexdigest()


class AlpacaPaperAccountGateway:
    """PAPER account attestation only; this class exposes no order-write API."""

    def __init__(
        self,
        *,
        config: AlpacaPaperGatewayConfig | None = None,
        transport: AlpacaPaperReadTransport | None = None,
        policy: AlpacaPaperReadPolicy | None = None,
    ) -> None:
        self._config = config or AlpacaPaperGatewayConfig()
        self._policy = policy or AlpacaPaperReadPolicy()
        self._transport = transport or UrllibAlpacaPaperReadTransport(
            policy=self._policy,
            max_response_bytes=self._config.max_response_bytes,
        )

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def attest_account(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        expected_account_id: str,
        now: datetime,
    ) -> AlpacaPaperAccountAttestation:
        if not self._config.enabled:
            raise AlpacaPaperGatewayDisabled("external PAPER gateway is disabled by default")
        _require_aware(now, "now")
        if not _ACCOUNT_ID_RE.fullmatch(expected_account_id):
            raise ValueError("expected_account_id must be an explicit UUID-like account id")

        request = AlpacaPaperReadRequest(
            method="GET",
            url=f"{self._config.base_url}{ALPACA_PAPER_ACCOUNT_PATH}",
            timeout_seconds=self._config.timeout_seconds,
            headers={
                "Accept": "application/json",
                "User-Agent": "AUTO-TRADE-R6/0.28R",
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
            },
        )
        # Validate before transport invocation; a custom transport cannot widen
        # host/path/method authority.
        self._policy.validate(request)
        response = self._transport.read(request)
        self._policy.validate_final_url(response.final_url)
        return _attestation_from_response(
            response=response,
            expected_account_id=expected_account_id,
            credential_reference=credentials.credential_reference,
            now=now,
        )


def _attestation_from_response(
    *,
    response: AlpacaPaperHttpResponse,
    expected_account_id: str,
    credential_reference: str,
    now: datetime,
) -> AlpacaPaperAccountAttestation:
    if response.status_code != 200:
        raise AlpacaPaperUnavailable(f"unexpected PAPER account status: {response.status_code}")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise AlpacaPaperIntegrityError("PAPER account response must be application/json")
    request_id = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise AlpacaPaperIntegrityError("PAPER account response is missing a valid X-Request-ID")
    payload = _strict_json_object(response.body)

    account_id = _required_str(payload, "id")
    if account_id != expected_account_id:
        raise AlpacaPaperIntegrityError("PAPER account id does not match explicit expected account")
    if not _ACCOUNT_ID_RE.fullmatch(account_id):
        raise AlpacaPaperIntegrityError("PAPER account id is malformed")
    status = _required_str(payload, "status")
    if status != "ACTIVE":
        raise AlpacaPaperIntegrityError("PAPER account is not ACTIVE")
    currency = _required_str(payload, "currency")
    if currency != "USD":
        raise AlpacaPaperIntegrityError("PAPER account currency must be USD")

    for key in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
        value = payload.get(key)
        if not isinstance(value, bool):
            raise AlpacaPaperIntegrityError(f"PAPER account {key} must be boolean")
        if value:
            raise AlpacaPaperIntegrityError(f"PAPER account is restricted: {key}")
    shorting_enabled = payload.get("shorting_enabled")
    if not isinstance(shorting_enabled, bool):
        raise AlpacaPaperIntegrityError("PAPER account shorting_enabled must be boolean")

    buying_power = _finite_nonnegative_decimal(payload.get("buying_power"), "buying_power")
    portfolio_value = _finite_nonnegative_decimal(payload.get("portfolio_value"), "portfolio_value")
    account_number = _required_str(payload, "account_number")
    account_reference = sha256(account_number.encode("utf-8")).hexdigest()

    parsed = urlsplit(response.final_url)
    return AlpacaPaperAccountAttestation(
        account_id=account_id,
        account_reference=account_reference,
        credential_reference=credential_reference,
        status=status,
        currency=currency,
        buying_power=buying_power,
        portfolio_value=portfolio_value,
        shorting_enabled=shorting_enabled,
        attested_at=now.astimezone(timezone.utc),
        request_id=request_id,
        source_host=parsed.hostname or "",
        source_path=parsed.path,
    )


def _strict_json_object(raw: bytes) -> Mapping[str, object]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=lambda token: _raise_json_constant(token))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AlpacaPaperIntegrityError("PAPER account response is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise AlpacaPaperIntegrityError("PAPER account response root must be an object")
    return value


def _raise_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AlpacaPaperIntegrityError(f"PAPER account field {key} is required")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise AlpacaPaperIntegrityError(f"PAPER account field {key} contains control characters")
    return value.strip()


def _finite_nonnegative_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise AlpacaPaperIntegrityError(f"PAPER account {label} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise AlpacaPaperIntegrityError(f"PAPER account {label} is invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise AlpacaPaperIntegrityError(f"PAPER account {label} must be finite and non-negative")
    return parsed


def _validate_secret_field(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"{label} must be a non-empty string <= 512 characters")
    if value != value.strip():
        raise ValueError(f"{label} must not contain surrounding whitespace")
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} contains whitespace/control characters")


def _validate_auth_headers(headers: Mapping[str, str]) -> None:
    normalized = {key.lower(): value for key, value in headers.items()}
    expected = {"accept", "user-agent", "apca-api-key-id", "apca-api-secret-key"}
    if set(normalized) != expected:
        raise AlpacaPaperPolicyError("PAPER attestation headers must match the exact allowlist")
    if normalized["accept"] != "application/json":
        raise AlpacaPaperPolicyError("PAPER attestation Accept header must be application/json")
    if normalized["user-agent"] != "AUTO-TRADE-R6/0.28R":
        raise AlpacaPaperPolicyError("PAPER attestation User-Agent is not canonical")
    _validate_secret_field(normalized["apca-api-key-id"], "APCA-API-KEY-ID")
    _validate_secret_field(normalized["apca-api-secret-key"], "APCA-API-SECRET-KEY")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
