from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import socket
import ssl
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from .alpaca_paper_gateway import (
    ALPACA_LIVE_TRADING_HOST,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)


_CLIENT_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_BROKER_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")


class PaperReconciliationGatewayError(RuntimeError):
    pass


class PaperReconciliationGatewayDisabled(PaperReconciliationGatewayError):
    pass


class PaperReconciliationPolicyError(PaperReconciliationGatewayError):
    pass


class PaperReconciliationUnavailable(PaperReconciliationGatewayError):
    pass


class PaperReconciliationIntegrityError(PaperReconciliationGatewayError):
    pass


@dataclass(frozen=True, slots=True)
class AlpacaPaperReconciliationConfig:
    enabled: bool = False
    base_url: str = f"https://{ALPACA_PAPER_TRADING_HOST}"
    timeout_seconds: float = 5.0
    max_response_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("timeout_seconds must be > 0 and <= 15")
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("max_response_bytes must be between 1 and 1048576")


@dataclass(frozen=True, slots=True)
class AlpacaPaperLookupRequest:
    method: str
    url: str
    timeout_seconds: float
    headers: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class AlpacaPaperLookupResponse:
    status_code: int
    body: bytes
    final_url: str
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class AlpacaPaperLookupResult:
    found: bool
    request_id: str
    client_order_id: str
    broker_order_id: str | None
    body: bytes | None


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PaperReconciliationPolicyError("PAPER reconciliation redirects are forbidden")


class UrllibAlpacaPaperLookupTransport:
    def __init__(self, *, max_response_bytes: int = 256 * 1024) -> None:
        if not 1 <= max_response_bytes <= 1_048_576:
            raise ValueError("max_response_bytes must be between 1 and 1048576")
        self._max_response_bytes = max_response_bytes
        self._opener = build_opener(
            ProxyHandler({}),
            HTTPSHandler(context=ssl.create_default_context()),
            _RejectRedirectHandler(),
        )

    def read(self, request: AlpacaPaperLookupRequest) -> AlpacaPaperLookupResponse:
        _validate_request(request)
        raw_request = Request(request.url, method="GET", headers=dict(request.headers))
        try:
            with self._opener.open(raw_request, timeout=request.timeout_seconds) as response:  # noqa: S310
                body = response.read(self._max_response_bytes + 1)
                if len(body) > self._max_response_bytes:
                    raise PaperReconciliationUnavailable("PAPER lookup response exceeded size limit")
                return AlpacaPaperLookupResponse(
                    status_code=int(response.status),
                    body=body,
                    final_url=response.geturl(),
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except PaperReconciliationPolicyError:
            raise
        except HTTPError as exc:
            body = exc.read(self._max_response_bytes + 1)
            if len(body) > self._max_response_bytes:
                raise PaperReconciliationUnavailable("PAPER lookup error response exceeded size limit") from exc
            return AlpacaPaperLookupResponse(
                status_code=int(exc.code),
                body=body,
                final_url=exc.geturl(),
                headers={key.lower(): value for key, value in exc.headers.items()},
            )
        except URLError as exc:
            raise PaperReconciliationUnavailable("PAPER reconciliation network request failed") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise PaperReconciliationUnavailable("PAPER reconciliation request timed out") from exc


class AlpacaPaperOrderLookupGateway:
    """GET-only PAPER order reads for resolving an already UNKNOWN submission."""

    def __init__(
        self,
        *,
        config: AlpacaPaperReconciliationConfig | None = None,
        transport: UrllibAlpacaPaperLookupTransport | None = None,
    ) -> None:
        self._config = config or AlpacaPaperReconciliationConfig()
        self._transport = transport or UrllibAlpacaPaperLookupTransport(
            max_response_bytes=self._config.max_response_bytes
        )

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def lookup_by_client_order_id(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        account_attestation: AlpacaPaperAccountAttestation,
        client_order_id: str,
    ) -> AlpacaPaperLookupResult:
        self._assert_enabled_and_attested(credentials, account_attestation)
        _validate_client_order_id(client_order_id)
        query = urlencode({"client_order_id": client_order_id}, safe="-._:")
        url = f"{self._config.base_url}/v2/orders:by_client_order_id?{query}"
        response = self._transport.read(self._request(credentials=credentials, url=url))
        _validate_final_url(
            response.final_url,
            expected_path="/v2/orders:by_client_order_id",
            expected_query=query,
        )
        request_id = _response_request_id(response)
        if response.status_code == 404:
            _validate_json_error_envelope(response.body)
            return AlpacaPaperLookupResult(
                found=False,
                request_id=request_id,
                client_order_id=client_order_id,
                broker_order_id=None,
                body=None,
            )
        if response.status_code != 200:
            raise PaperReconciliationUnavailable(
                f"unexpected PAPER client-order lookup status: {response.status_code}"
            )
        payload = _strict_json_object(response.body)
        broker_client_id = _required_str(payload, "client_order_id")
        broker_order_id = _required_str(payload, "id")
        _validate_client_order_id(broker_client_id)
        _validate_broker_order_id(broker_order_id)
        if broker_client_id != client_order_id:
            raise PaperReconciliationIntegrityError("broker lookup client_order_id mismatch")
        return AlpacaPaperLookupResult(
            found=True,
            request_id=request_id,
            client_order_id=broker_client_id,
            broker_order_id=broker_order_id,
            body=response.body,
        )

    def get_nested_order(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        account_attestation: AlpacaPaperAccountAttestation,
        broker_order_id: str,
    ) -> AlpacaPaperLookupResult:
        self._assert_enabled_and_attested(credentials, account_attestation)
        _validate_broker_order_id(broker_order_id)
        path = f"/v2/orders/{quote(broker_order_id, safe='-._:')}"
        query = "nested=true"
        url = f"{self._config.base_url}{path}?{query}"
        response = self._transport.read(self._request(credentials=credentials, url=url))
        _validate_final_url(response.final_url, expected_path=path, expected_query=query)
        request_id = _response_request_id(response)
        if response.status_code != 200:
            raise PaperReconciliationIntegrityError(
                f"previously discovered PAPER order is no longer readable: {response.status_code}"
            )
        payload = _strict_json_object(response.body)
        returned_id = _required_str(payload, "id")
        returned_client_id = _required_str(payload, "client_order_id")
        _validate_broker_order_id(returned_id)
        _validate_client_order_id(returned_client_id)
        if returned_id != broker_order_id:
            raise PaperReconciliationIntegrityError("nested broker order id mismatch")
        return AlpacaPaperLookupResult(
            found=True,
            request_id=request_id,
            client_order_id=returned_client_id,
            broker_order_id=returned_id,
            body=response.body,
        )

    def _request(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        url: str,
    ) -> AlpacaPaperLookupRequest:
        request = AlpacaPaperLookupRequest(
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
        _validate_request(request)
        return request

    def _assert_enabled_and_attested(
        self,
        credentials: AlpacaPaperCredentials,
        attestation: AlpacaPaperAccountAttestation,
    ) -> None:
        if not self._config.enabled:
            raise PaperReconciliationGatewayDisabled(
                "PAPER reconciliation reads are disabled by default"
            )
        if credentials.credential_reference != attestation.credential_reference:
            raise PaperReconciliationPolicyError(
                "PAPER reconciliation credentials do not match account attestation"
            )
        if attestation.status != "ACTIVE" or attestation.currency != "USD":
            raise PaperReconciliationPolicyError("PAPER account attestation is not ACTIVE USD")
        if attestation.source_host != ALPACA_PAPER_TRADING_HOST or attestation.source_path != "/v2/account":
            raise PaperReconciliationPolicyError("PAPER account attestation endpoint is not exact")


def _validate_request(request: AlpacaPaperLookupRequest) -> None:
    if request.method != "GET":
        raise PaperReconciliationPolicyError("PAPER reconciliation is GET-only")
    if not 0 < request.timeout_seconds <= 15:
        raise PaperReconciliationPolicyError("PAPER reconciliation timeout is invalid")
    parsed = urlsplit(request.url)
    if parsed.scheme != "https":
        raise PaperReconciliationPolicyError("PAPER reconciliation requires HTTPS")
    if parsed.username or parsed.password or parsed.fragment:
        raise PaperReconciliationPolicyError("PAPER reconciliation URL credentials/fragment are forbidden")
    if parsed.hostname != ALPACA_PAPER_TRADING_HOST or parsed.port not in (None, 443):
        raise PaperReconciliationPolicyError("PAPER reconciliation host is not exact")
    if parsed.hostname == ALPACA_LIVE_TRADING_HOST:
        raise PaperReconciliationPolicyError("LIVE Trading API host is forbidden")
    if not (
        parsed.path == "/v2/orders:by_client_order_id"
        or parsed.path.startswith("/v2/orders/")
    ):
        raise PaperReconciliationPolicyError("PAPER reconciliation path is not allowlisted")
    _validate_auth_headers(request.headers)


def _validate_final_url(url: str, *, expected_path: str, expected_query: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != ALPACA_PAPER_TRADING_HOST:
        raise PaperReconciliationPolicyError("PAPER reconciliation final host is not exact")
    if parsed.port not in (None, 443) or parsed.path != expected_path or parsed.query != expected_query:
        raise PaperReconciliationPolicyError("PAPER reconciliation final URL changed authority")
    if parsed.username or parsed.password or parsed.fragment:
        raise PaperReconciliationPolicyError("PAPER reconciliation final URL is non-canonical")


def _validate_auth_headers(headers: Mapping[str, str]) -> None:
    normalized = {key.lower(): value for key, value in headers.items()}
    expected = {"accept", "user-agent", "apca-api-key-id", "apca-api-secret-key"}
    if set(normalized) != expected:
        raise PaperReconciliationPolicyError("PAPER reconciliation headers must match exact allowlist")
    if normalized["accept"] != "application/json" or normalized["user-agent"] != "AUTO-TRADE-R6/0.28R":
        raise PaperReconciliationPolicyError("PAPER reconciliation headers are non-canonical")
    for key in ("apca-api-key-id", "apca-api-secret-key"):
        value = normalized[key]
        if not value or value != value.strip() or any(ord(char) < 33 or ord(char) == 127 for char in value):
            raise PaperReconciliationPolicyError("PAPER reconciliation credentials are malformed")


def _response_request_id(response: AlpacaPaperLookupResponse) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise PaperReconciliationIntegrityError("PAPER reconciliation response must be application/json")
    request_id = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise PaperReconciliationIntegrityError("PAPER reconciliation response lacks valid X-Request-ID")
    return request_id


def _validate_json_error_envelope(raw: bytes) -> None:
    payload = _strict_json_object(raw)
    if not isinstance(payload.get("message"), str) or not payload["message"]:
        raise PaperReconciliationIntegrityError("PAPER 404 response lacks explicit JSON error message")


def _strict_json_object(raw: bytes) -> Mapping[str, object]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=lambda token: _raise_json_constant(token))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperReconciliationIntegrityError("PAPER reconciliation response is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PaperReconciliationIntegrityError("PAPER reconciliation response root must be object")
    return value


def _raise_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PaperReconciliationIntegrityError(f"PAPER reconciliation field {key} is required")
    return value


def _validate_client_order_id(value: str) -> None:
    if not _CLIENT_ORDER_ID_RE.fullmatch(value):
        raise ValueError("client_order_id must be canonical text <=128 characters")


def _validate_broker_order_id(value: str) -> None:
    if not _BROKER_ORDER_ID_RE.fullmatch(value):
        raise ValueError("broker_order_id must be canonical text <=128 characters")
