from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from .alpaca_paper_crypto_final_guard import (
    CryptoFinalWriteAttestation,
    CryptoFinalWritePhase,
)
from .alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from .alpaca_paper_crypto_order import CryptoOrderRole
from .alpaca_paper_crypto_protection_final_guard import (
    CryptoProtectionFinalWriteAttestation,
    CryptoProtectionFinalWritePhase,
)
from .alpaca_paper_crypto_writer import (
    ALPACA_PAPER_TRADING_HOST,
    CRYPTO_ORDERS_PATH,
    AlpacaPaperCryptoWriteResponse,
    AlpacaPaperCryptoWriteTransport,
    CryptoPaperWriterIntegrityError,
    CryptoPaperWriterPolicyError,
    GuardedAlpacaPaperCryptoWriteTransport,
)


CryptoPreIoAuthorizer = Callable[[], CryptoFinalWriteAttestation]
CryptoProtectionPreIoAuthorizer = Callable[[], CryptoProtectionFinalWriteAttestation]


class CryptoPreIoInterlockError(CryptoPaperWriterIntegrityError):
    pass


class FinalGuardedCryptoEntryTransport(GuardedAlpacaPaperCryptoWriteTransport):
    """ENTRY PRE_IO interlock between durable UNKNOWN and delegated transport."""

    role = CryptoOrderRole.ENTRY

    def __init__(
        self,
        *,
        delegate: AlpacaPaperCryptoWriteTransport,
        authorizer: CryptoPreIoAuthorizer,
    ) -> None:
        if delegate is None:
            raise TypeError("guarded crypto transport requires a delegate")
        if not callable(authorizer):
            raise TypeError("guarded crypto transport requires PRE_IO authorizer")
        self._delegate = delegate
        self._authorizer = authorizer
        self._last_attestation: CryptoFinalWriteAttestation | None = None
        self._delegated_calls = 0

    @property
    def last_attestation(self) -> CryptoFinalWriteAttestation | None:
        return self._last_attestation

    @property
    def delegated_calls(self) -> int:
        return self._delegated_calls

    def post(
        self,
        *,
        host: str,
        path: str,
        headers,
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> AlpacaPaperCryptoWriteResponse:
        if host != ALPACA_PAPER_TRADING_HOST or path != CRYPTO_ORDERS_PATH:
            raise CryptoPaperWriterPolicyError("guarded crypto transport requires exact PAPER orders endpoint")
        if self._last_attestation is not None or self._delegated_calls != 0:
            raise CryptoPreIoInterlockError("guarded crypto transport is one-shot")
        client_order_id = _client_order_id_from_body(body)

        attestation = self._authorizer()
        if not isinstance(attestation, CryptoFinalWriteAttestation):
            raise CryptoPreIoInterlockError("PRE_IO authorizer returned invalid ENTRY evidence")
        if attestation.phase is not CryptoFinalWritePhase.PRE_IO:
            raise CryptoPreIoInterlockError("guarded ENTRY transport requires PRE_IO attestation")
        if attestation.lifecycle_status is not CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
            raise CryptoPreIoInterlockError("ENTRY PRE_IO attestation must observe durable ENTRY_SUBMISSION_UNKNOWN")
        if attestation.entry_attempt_count != 1:
            raise CryptoPreIoInterlockError("ENTRY PRE_IO attestation must observe exactly one entry attempt")
        if attestation.client_order_id != client_order_id:
            raise CryptoPreIoInterlockError("ENTRY PRE_IO attestation client_order_id differs from request")

        # Persist only sanitized authority evidence, never headers/credentials.
        self._last_attestation = attestation
        response = self._delegate.post(
            host=host,
            path=path,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        self._delegated_calls += 1
        return response


class FinalGuardedCryptoProtectionTransport(GuardedAlpacaPaperCryptoWriteTransport):
    """PROTECTION PRE_IO interlock for the exact same durable one-shot attempt."""

    role = CryptoOrderRole.PROTECTION

    def __init__(
        self,
        *,
        delegate: AlpacaPaperCryptoWriteTransport,
        authorizer: CryptoProtectionPreIoAuthorizer,
    ) -> None:
        if delegate is None:
            raise TypeError("guarded crypto protection transport requires a delegate")
        if not callable(authorizer):
            raise TypeError("guarded crypto protection transport requires PRE_IO authorizer")
        self._delegate = delegate
        self._authorizer = authorizer
        self._last_attestation: CryptoProtectionFinalWriteAttestation | None = None
        self._delegated_calls = 0

    @property
    def last_attestation(self) -> CryptoProtectionFinalWriteAttestation | None:
        return self._last_attestation

    @property
    def delegated_calls(self) -> int:
        return self._delegated_calls

    def post(
        self,
        *,
        host: str,
        path: str,
        headers,
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> AlpacaPaperCryptoWriteResponse:
        if host != ALPACA_PAPER_TRADING_HOST or path != CRYPTO_ORDERS_PATH:
            raise CryptoPaperWriterPolicyError(
                "guarded crypto protection transport requires exact PAPER orders endpoint"
            )
        if self._last_attestation is not None or self._delegated_calls != 0:
            raise CryptoPreIoInterlockError("guarded crypto protection transport is one-shot")
        client_order_id = _client_order_id_from_body(body)

        attestation = self._authorizer()
        if not isinstance(attestation, CryptoProtectionFinalWriteAttestation):
            raise CryptoPreIoInterlockError("PRE_IO authorizer returned invalid PROTECTION evidence")
        if attestation.phase is not CryptoProtectionFinalWritePhase.PRE_IO:
            raise CryptoPreIoInterlockError("guarded PROTECTION transport requires PRE_IO attestation")
        if attestation.lifecycle_status is not CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN:
            raise CryptoPreIoInterlockError(
                "PROTECTION PRE_IO attestation must observe durable PROTECTION_SUBMISSION_UNKNOWN"
            )
        if attestation.protection_attempt_count != 1:
            raise CryptoPreIoInterlockError(
                "PROTECTION PRE_IO attestation must observe exactly one protection attempt"
            )
        if attestation.client_order_id != client_order_id:
            raise CryptoPreIoInterlockError(
                "PROTECTION PRE_IO attestation client_order_id differs from request"
            )

        self._last_attestation = attestation
        response = self._delegate.post(
            host=host,
            path=path,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        self._delegated_calls += 1
        return response


@dataclass(slots=True)
class DeterministicCryptoPaperSimulationTransport:
    """In-memory PAPER transport for end-to-end authority rehearsal only."""

    calls: int = 0

    def post(
        self,
        *,
        host: str,
        path: str,
        headers,
        body: bytes,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> AlpacaPaperCryptoWriteResponse:
        del timeout_seconds, max_response_bytes
        if host != ALPACA_PAPER_TRADING_HOST or path != CRYPTO_ORDERS_PATH:
            raise CryptoPaperWriterPolicyError("simulation transport requires exact PAPER orders endpoint")
        if headers.get("APCA-API-KEY-ID") != "simulation-paper-key":
            raise CryptoPaperWriterPolicyError("simulation transport accepts only synthetic credentials")
        if headers.get("APCA-API-SECRET-KEY") != "simulation-paper-secret":
            raise CryptoPaperWriterPolicyError("simulation transport accepts only synthetic credentials")
        if self.calls != 0:
            raise CryptoPreIoInterlockError("simulation transport is one-shot")
        try:
            request = json.loads(body.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CryptoPreIoInterlockError("simulation request body is invalid JSON") from exc
        if not isinstance(request, dict):
            raise CryptoPreIoInterlockError("simulation request body root must be object")
        required = ("client_order_id", "symbol", "side", "type", "time_in_force", "qty")
        if any(not isinstance(request.get(key), str) or not request.get(key) for key in required):
            raise CryptoPreIoInterlockError("simulation request is missing canonical order fields")
        self.calls += 1
        response = {
            "id": f"simulation-broker-order-{self.calls}",
            "client_order_id": request["client_order_id"],
            "symbol": request["symbol"],
            "asset_class": "crypto",
            "side": request["side"],
            "type": request["type"],
            "time_in_force": request["time_in_force"],
            "status": "accepted",
            "qty": request["qty"],
            "filled_qty": "0",
            "limit_price": request.get("limit_price"),
            "stop_price": request.get("stop_price"),
        }
        encoded = json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return AlpacaPaperCryptoWriteResponse(
            status_code=200,
            body=encoded,
            headers={
                "content-type": "application/json",
                "x-request-id": f"simulation-request-{self.calls}",
            },
        )


def _client_order_id_from_body(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoPreIoInterlockError("guarded crypto request body is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CryptoPreIoInterlockError("guarded crypto request body root must be object")
    value = payload.get("client_order_id")
    if not isinstance(value, str) or not value:
        raise CryptoPreIoInterlockError("guarded crypto request lacks client_order_id")
    return value


__all__ = [
    "CryptoPreIoAuthorizer",
    "CryptoPreIoInterlockError",
    "CryptoProtectionPreIoAuthorizer",
    "DeterministicCryptoPaperSimulationTransport",
    "FinalGuardedCryptoEntryTransport",
    "FinalGuardedCryptoProtectionTransport",
]
