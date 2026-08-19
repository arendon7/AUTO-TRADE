from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Mapping

from .alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperIntegrityError,
    AlpacaPaperPolicyError,
    AlpacaPaperReadPolicy,
    AlpacaPaperReadRequest,
    AlpacaPaperReadTransport,
    AlpacaPaperUnavailable,
    UrllibAlpacaPaperReadTransport,
)


_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_ACCOUNT_ID_RE = re.compile(r"^[0-9a-fA-F-]{16,64}$")


class AlpacaPaperCryptoAccountStatusError(RuntimeError):
    pass


class AlpacaPaperCryptoAccountNotActive(AlpacaPaperCryptoAccountStatusError):
    pass


@dataclass(frozen=True, slots=True)
class AlpacaPaperCryptoAccountStatusAttestation:
    account_id: str
    crypto_status: str
    observed_at: datetime
    request_id: str
    response_sha256: str

    def __post_init__(self) -> None:
        if not _ACCOUNT_ID_RE.fullmatch(self.account_id):
            raise ValueError("crypto account status account_id is invalid")
        if not isinstance(self.crypto_status, str) or not self.crypto_status.strip():
            raise ValueError("crypto account status is required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("crypto account status observed_at must be timezone-aware")
        if not _REQUEST_ID_RE.fullmatch(self.request_id):
            raise ValueError("crypto account status request id is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.response_sha256):
            raise ValueError("crypto account status response hash is invalid")

    @property
    def crypto_ready(self) -> bool:
        return self.crypto_status == "ACTIVE"

    @property
    def fingerprint(self) -> str:
        payload = {
            "account_id": self.account_id,
            "crypto_status": self.crypto_status,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "request_id": self.request_id,
            "response_sha256": self.response_sha256,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        ).hexdigest()


def attest_active_crypto_account(
    *,
    credentials: AlpacaPaperCredentials,
    expected_account_id: str,
    now: datetime,
    config: AlpacaPaperGatewayConfig | None = None,
    transport: AlpacaPaperReadTransport | None = None,
) -> AlpacaPaperCryptoAccountStatusAttestation:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not _ACCOUNT_ID_RE.fullmatch(expected_account_id):
        raise ValueError("expected_account_id must be explicit UUID-like account id")

    effective = config or AlpacaPaperGatewayConfig(enabled=True)
    if not effective.enabled:
        raise AlpacaPaperCryptoAccountStatusError("PAPER crypto account status read is disabled")
    policy = AlpacaPaperReadPolicy(allowed_paths=frozenset({ALPACA_PAPER_ACCOUNT_PATH}))
    reader = transport or UrllibAlpacaPaperReadTransport(
        policy=policy,
        max_response_bytes=effective.max_response_bytes,
    )
    request = AlpacaPaperReadRequest(
        method="GET",
        url=f"{effective.base_url}{ALPACA_PAPER_ACCOUNT_PATH}",
        timeout_seconds=effective.timeout_seconds,
        headers={
            "Accept": "application/json",
            "User-Agent": "AUTO-TRADE-R6/0.28R",
            "APCA-API-KEY-ID": credentials.key_id,
            "APCA-API-SECRET-KEY": credentials.secret_key,
        },
    )
    policy.validate(request)
    response = reader.read(request)
    policy.validate_final_url(response.final_url)
    attestation = _parse_response(
        response=response,
        expected_account_id=expected_account_id,
        now=now,
    )
    if not attestation.crypto_ready:
        raise AlpacaPaperCryptoAccountNotActive(
            f"Alpaca PAPER crypto_status is {attestation.crypto_status}; ACTIVE is required before any crypto POST"
        )
    return attestation


def _parse_response(
    *,
    response: AlpacaPaperHttpResponse,
    expected_account_id: str,
    now: datetime,
) -> AlpacaPaperCryptoAccountStatusAttestation:
    if response.status_code != 200:
        raise AlpacaPaperUnavailable(
            f"unexpected PAPER crypto account status HTTP {response.status_code}"
        )
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise AlpacaPaperIntegrityError("PAPER crypto account response must be application/json")
    request_id = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise AlpacaPaperIntegrityError("PAPER crypto account response lacks valid X-Request-ID")
    try:
        payload = json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AlpacaPaperIntegrityError("PAPER crypto account response is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise AlpacaPaperIntegrityError("PAPER crypto account response root must be object")
    account_id = _required_text(payload, "id")
    if account_id != expected_account_id:
        raise AlpacaPaperIntegrityError("PAPER crypto account id differs from workspace account")
    crypto_status = _required_text(payload, "crypto_status").upper()
    return AlpacaPaperCryptoAccountStatusAttestation(
        account_id=account_id,
        crypto_status=crypto_status,
        observed_at=now.astimezone(timezone.utc),
        request_id=request_id,
        response_sha256=sha256(response.body).hexdigest(),
    )


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AlpacaPaperIntegrityError(f"PAPER crypto account field {key} is required")
    text = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise AlpacaPaperIntegrityError(f"PAPER crypto account field {key} contains control characters")
    return text


__all__ = [
    "AlpacaPaperCryptoAccountNotActive",
    "AlpacaPaperCryptoAccountStatusAttestation",
    "AlpacaPaperCryptoAccountStatusError",
    "attest_active_crypto_account",
]
