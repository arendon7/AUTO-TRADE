from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Mapping
from urllib.parse import parse_qs, urlsplit

from .alpaca_paper_gateway import (
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
)


POSITIONS_PATH = "/v2/positions"
ORDERS_PATH = "/v2/orders"
ORDERS_QUERY = "status=open&limit=500&direction=asc&nested=true"
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperFlatAccountError(RuntimeError):
    pass


class PaperFlatAccountDisabled(PaperFlatAccountError):
    pass


class PaperFlatAccountIntegrityError(PaperFlatAccountError):
    pass


@dataclass(frozen=True, slots=True)
class PaperFlatAccountReadPolicy:
    allowed_host: str = ALPACA_PAPER_TRADING_HOST

    def validate(self, request: AlpacaPaperReadRequest) -> None:
        if request.method != "GET":
            raise AlpacaPaperPolicyError("PAPER flat-account preflight is GET-only")
        if not 0 < request.timeout_seconds <= 15:
            raise AlpacaPaperPolicyError("PAPER flat-account timeout is invalid")
        parsed = urlsplit(request.url)
        if parsed.scheme != "https":
            raise AlpacaPaperPolicyError("PAPER flat-account API requires HTTPS")
        if parsed.username or parsed.password or parsed.fragment:
            raise AlpacaPaperPolicyError("PAPER flat-account URL credentials/fragment are forbidden")
        if parsed.hostname != self.allowed_host or parsed.port not in (None, 443):
            raise AlpacaPaperPolicyError("PAPER flat-account host is not exactly allowlisted")
        if parsed.path == POSITIONS_PATH:
            if parsed.query:
                raise AlpacaPaperPolicyError("PAPER positions preflight forbids query parameters")
        elif parsed.path == ORDERS_PATH:
            if not _is_exact_orders_query(parsed.query):
                raise AlpacaPaperPolicyError("PAPER open-orders preflight query is not canonical")
        else:
            raise AlpacaPaperPolicyError("PAPER flat-account path is not allowlisted")
        _validate_auth_headers(request.headers)

    def validate_final_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != self.allowed_host:
            raise AlpacaPaperPolicyError("PAPER flat-account response host is not allowlisted")
        if parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment:
            raise AlpacaPaperPolicyError("PAPER flat-account response URL is non-canonical")
        if parsed.path == POSITIONS_PATH and not parsed.query:
            return
        if parsed.path == ORDERS_PATH and _is_exact_orders_query(parsed.query):
            return
        raise AlpacaPaperPolicyError("PAPER flat-account response path/query is not allowlisted")


@dataclass(frozen=True, slots=True)
class PaperFlatAccountAttestation:
    account_attestation_fingerprint: str
    credential_reference: str
    position_count: int
    open_order_count: int
    positions_response_hash: str
    orders_response_hash: str
    positions_request_id: str
    orders_request_id: str
    attested_at: datetime
    source_host: str = ALPACA_PAPER_TRADING_HOST
    positions_path: str = POSITIONS_PATH
    orders_path: str = f"{ORDERS_PATH}?{ORDERS_QUERY}"

    def __post_init__(self) -> None:
        for value, label in (
            (self.account_attestation_fingerprint, "account_attestation_fingerprint"),
            (self.credential_reference, "credential_reference"),
            (self.positions_response_hash, "positions_response_hash"),
            (self.orders_response_hash, "orders_response_hash"),
        ):
            if not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be sha256")
        if self.position_count < 0 or self.open_order_count < 0:
            raise ValueError("flat-account counts cannot be negative")
        if self.attested_at.tzinfo is None or self.attested_at.utcoffset() is None:
            raise ValueError("attested_at must be timezone-aware")
        for request_id in (self.positions_request_id, self.orders_request_id):
            if not _REQUEST_ID_RE.fullmatch(request_id):
                raise ValueError("flat-account request id is invalid")

    @property
    def clean_for_first_canary(self) -> bool:
        return self.position_count == 0 and self.open_order_count == 0

    @property
    def fingerprint(self) -> str:
        return _hash_payload(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "account_attestation_fingerprint": self.account_attestation_fingerprint,
            "credential_reference": self.credential_reference,
            "position_count": self.position_count,
            "open_order_count": self.open_order_count,
            "positions_response_hash": self.positions_response_hash,
            "orders_response_hash": self.orders_response_hash,
            "positions_request_id": self.positions_request_id,
            "orders_request_id": self.orders_request_id,
            "attested_at": self.attested_at.astimezone(timezone.utc).isoformat(),
            "source_host": self.source_host,
            "positions_path": self.positions_path,
            "orders_path": self.orders_path,
            "clean_for_first_canary": self.clean_for_first_canary,
        }


class AlpacaPaperFlatAccountGateway:
    """Two-read first-canary account flatness gate; no mutation APIs exist here."""

    def __init__(
        self,
        *,
        config: AlpacaPaperGatewayConfig | None = None,
        transport: AlpacaPaperReadTransport | None = None,
        policy: PaperFlatAccountReadPolicy | None = None,
    ) -> None:
        self._config = config or AlpacaPaperGatewayConfig()
        self._policy = policy or PaperFlatAccountReadPolicy()
        self._transport = transport or UrllibAlpacaPaperReadTransport(
            policy=self._policy,  # type: ignore[arg-type]
            max_response_bytes=self._config.max_response_bytes,
        )

    def attest_flatness(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        account_attestation_fingerprint: str,
        expected_credential_reference: str,
        now: datetime,
    ) -> PaperFlatAccountAttestation:
        if not self._config.enabled:
            raise PaperFlatAccountDisabled("PAPER flat-account preflight is disabled by default")
        if not _HASH_RE.fullmatch(account_attestation_fingerprint):
            raise ValueError("account_attestation_fingerprint must be sha256")
        if credentials.credential_reference != expected_credential_reference:
            raise PaperFlatAccountIntegrityError("PAPER credentials do not match account preflight evidence")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        positions = self._read(
            credentials=credentials,
            path=POSITIONS_PATH,
            query=None,
        )
        orders = self._read(
            credentials=credentials,
            path=ORDERS_PATH,
            query=ORDERS_QUERY,
        )
        position_payload = _strict_json_array(positions)
        order_payload = _strict_json_array(orders)
        return PaperFlatAccountAttestation(
            account_attestation_fingerprint=account_attestation_fingerprint,
            credential_reference=credentials.credential_reference,
            position_count=len(position_payload),
            open_order_count=len(order_payload),
            positions_response_hash=_hash_payload(position_payload),
            orders_response_hash=_hash_payload(order_payload),
            positions_request_id=_request_id(positions),
            orders_request_id=_request_id(orders),
            attested_at=now.astimezone(timezone.utc),
        )

    def _read(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        path: str,
        query: str | None,
    ) -> AlpacaPaperHttpResponse:
        url = f"{self._config.base_url}{path}"
        if query:
            url = f"{url}?{query}"
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
        if response.status_code != 200:
            raise AlpacaPaperUnavailable(
                f"unexpected PAPER flat-account status: {response.status_code}"
            )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise AlpacaPaperIntegrityError("PAPER flat-account response must be application/json")
        return response


def _strict_json_array(response: AlpacaPaperHttpResponse) -> list[object]:
    try:
        text = response.body.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=lambda token: _raise_constant(token))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperFlatAccountIntegrityError("PAPER flat-account response is not strict JSON") from exc
    if not isinstance(value, list):
        raise PaperFlatAccountIntegrityError("PAPER flat-account response root must be an array")
    return value


def _request_id(response: AlpacaPaperHttpResponse) -> str:
    value = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(value):
        raise PaperFlatAccountIntegrityError("PAPER flat-account response lacks valid X-Request-ID")
    return value


def _is_exact_orders_query(query: str) -> bool:
    parsed = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    return parsed == {
        "status": ["open"],
        "limit": ["500"],
        "direction": ["asc"],
        "nested": ["true"],
    }


def _validate_auth_headers(headers: Mapping[str, str]) -> None:
    expected = {"Accept", "User-Agent", "APCA-API-KEY-ID", "APCA-API-SECRET-KEY"}
    if set(headers) != expected:
        raise AlpacaPaperPolicyError("PAPER flat-account auth headers are non-canonical")
    if headers.get("Accept") != "application/json":
        raise AlpacaPaperPolicyError("PAPER flat-account Accept header must be application/json")
    for key in ("APCA-API-KEY-ID", "APCA-API-SECRET-KEY"):
        value = headers.get(key, "")
        if not value or value != value.strip():
            raise AlpacaPaperPolicyError("PAPER flat-account credentials are invalid")


def _hash_payload(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _raise_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")
