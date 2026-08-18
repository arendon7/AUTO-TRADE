from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Mapping

from autotrade.product_profile import ProductCapabilities

from .alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from .alpaca_paper_crypto_canary_coordinator import PreparedCryptoPaperCanaryPackage
from .alpaca_paper_crypto_cold_start_final_guard import (
    CryptoColdStartFinalWriteAttestation,
    CryptoColdStartFinalWritePhase,
)
from .alpaca_paper_crypto_cold_start_pre_io_authority import CryptoColdStartPreIoAuthority
from .alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus, SQLiteCryptoPaperLifecycle
from .alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketAttestation
from .alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecision,
    SQLiteCryptoOperatorDecisionRegistry,
)
from .alpaca_paper_crypto_order import AlpacaPaperCryptoOrderRequest, CryptoOrderRole
from .alpaca_paper_crypto_writer import (
    ALPACA_PAPER_TRADING_HOST,
    CRYPTO_ORDERS_PATH,
    AlpacaPaperCryptoWriteResponse,
    AlpacaPaperCryptoWriteTransport,
    CryptoPaperWriterIntegrityError,
    CryptoPaperWriterPolicyError,
    GuardedAlpacaPaperCryptoWriteTransport,
)
from .alpaca_paper_flat_account import PaperFlatAccountAttestation
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation, AlpacaPaperCredentials


class CryptoColdStartPreIoInterlockError(CryptoPaperWriterIntegrityError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoColdStartPreIoExecutionContext:
    package: PreparedCryptoPaperCanaryPackage
    operator_decision: CryptoOperatorDecision
    operator_registry: SQLiteCryptoOperatorDecisionRegistry
    broker_order: AlpacaPaperCryptoOrderRequest
    lifecycle: SQLiteCryptoPaperLifecycle
    prepared_account: AlpacaPaperAccountAttestation
    prepared_asset: AlpacaPaperCryptoAssetAttestation
    prepared_product_profile: ProductCapabilities
    fresh_account: AlpacaPaperAccountAttestation
    fresh_asset: AlpacaPaperCryptoAssetAttestation
    fresh_product_profile: ProductCapabilities
    fresh_market: AlpacaPaperCryptoMarketAttestation
    fresh_flat_account: PaperFlatAccountAttestation

    def __post_init__(self) -> None:
        if not isinstance(self.package, PreparedCryptoPaperCanaryPackage):
            raise ValueError("cold-start PRE_IO context requires prepared package")
        if not isinstance(self.operator_decision, CryptoOperatorDecision):
            raise ValueError("cold-start PRE_IO context requires operator decision")
        if not isinstance(self.operator_registry, SQLiteCryptoOperatorDecisionRegistry):
            raise ValueError("cold-start PRE_IO context requires durable operator registry")
        if not isinstance(self.broker_order, AlpacaPaperCryptoOrderRequest):
            raise ValueError("cold-start PRE_IO context requires broker order")
        if self.broker_order.role is not CryptoOrderRole.ENTRY:
            raise ValueError("cold-start PRE_IO context accepts ENTRY only")
        if self.broker_order.client_order_id != self.package.client_order_id:
            raise ValueError("cold-start PRE_IO context order/package client id mismatch")
        if self.broker_order.fingerprint != self.package.crypto_order_fingerprint:
            raise ValueError("cold-start PRE_IO context order/package fingerprint mismatch")
        if self.broker_order.payload_hash != self.package.crypto_order_payload_hash:
            raise ValueError("cold-start PRE_IO context order/package payload hash mismatch")


class ColdStartFinalGuardedCryptoEntryTransport(GuardedAlpacaPaperCryptoWriteTransport):
    """Production nominal PRE_IO capability for the isolated first PAPER canary.

    The transport owns no HTTP stack. The writer must have already persisted
    ENTRY_SUBMISSION_UNKNOWN before this method runs. At the final boundary it
    resolves durable checkpoint + OMS handoff through `CryptoColdStartPreIoAuthority`,
    binds the actual ephemeral request Key ID to the attestation credential
    reference, records sanitized PRE_IO evidence, and only then delegates once.
    """

    role = CryptoOrderRole.ENTRY

    def __init__(
        self,
        *,
        delegate: AlpacaPaperCryptoWriteTransport,
        authority: CryptoColdStartPreIoAuthority,
        context: CryptoColdStartPreIoExecutionContext,
    ) -> None:
        if delegate is None:
            raise TypeError("cold-start guarded crypto transport requires delegate")
        if not isinstance(authority, CryptoColdStartPreIoAuthority):
            raise TypeError("cold-start guarded crypto transport requires durable PRE_IO authority")
        if not isinstance(context, CryptoColdStartPreIoExecutionContext):
            raise TypeError("cold-start guarded crypto transport requires exact execution context")
        self._delegate = delegate
        self._authority = authority
        self._context = context
        self._last_attestation: CryptoColdStartFinalWriteAttestation | None = None
        self._delegated_calls = 0

    @property
    def last_attestation(self) -> CryptoColdStartFinalWriteAttestation | None:
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
                "cold-start guarded crypto transport requires exact PAPER orders endpoint"
            )
        if self._last_attestation is not None or self._delegated_calls != 0:
            raise CryptoColdStartPreIoInterlockError("cold-start guarded crypto transport is one-shot")
        request_payload = _request_payload(body)
        expected_payload = self._context.broker_order.to_payload()
        if request_payload != expected_payload:
            raise CryptoColdStartPreIoInterlockError(
                "cold-start writer body differs from prepared broker order payload"
            )
        credentials = _ephemeral_credentials(headers)

        attestation = self._authority.authorize(
            package=self._context.package,
            operator_decision=self._context.operator_decision,
            operator_registry=self._context.operator_registry,
            broker_order=self._context.broker_order,
            lifecycle=self._context.lifecycle,
            prepared_account=self._context.prepared_account,
            prepared_asset=self._context.prepared_asset,
            prepared_product_profile=self._context.prepared_product_profile,
            fresh_account=self._context.fresh_account,
            fresh_asset=self._context.fresh_asset,
            fresh_product_profile=self._context.fresh_product_profile,
            fresh_market=self._context.fresh_market,
            fresh_flat_account=self._context.fresh_flat_account,
            now=_utc_now(),
        )
        if not isinstance(attestation, CryptoColdStartFinalWriteAttestation):
            raise CryptoColdStartPreIoInterlockError(
                "cold-start PRE_IO authority returned invalid evidence"
            )
        if attestation.phase is not CryptoColdStartFinalWritePhase.PRE_IO:
            raise CryptoColdStartPreIoInterlockError(
                "cold-start guarded ENTRY requires PRE_IO attestation"
            )
        if attestation.lifecycle_status is not CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
            raise CryptoColdStartPreIoInterlockError(
                "cold-start PRE_IO must observe durable ENTRY_SUBMISSION_UNKNOWN"
            )
        if attestation.entry_attempt_count != 1:
            raise CryptoColdStartPreIoInterlockError(
                "cold-start PRE_IO must observe exactly one entry attempt"
            )
        if attestation.client_order_id != self._context.broker_order.client_order_id:
            raise CryptoColdStartPreIoInterlockError(
                "cold-start PRE_IO client_order_id differs from exact request"
            )
        if attestation.package_hash != self._context.package.package_hash:
            raise CryptoColdStartPreIoInterlockError(
                "cold-start PRE_IO package hash differs from execution context"
            )
        if attestation.credential_reference != credentials.credential_reference:
            raise CryptoColdStartPreIoInterlockError(
                "cold-start PRE_IO credential reference differs from request Key ID"
            )

        # Sanitized evidence is latched before delegate I/O. Even if the delegate
        # times out after a possible broker receive, this transport cannot retry.
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


def _ephemeral_credentials(headers: Mapping[str, str]) -> AlpacaPaperCredentials:
    if not isinstance(headers, Mapping):
        raise CryptoColdStartPreIoInterlockError("cold-start request headers are invalid")
    key_id = headers.get("APCA-API-KEY-ID")
    secret_key = headers.get("APCA-API-SECRET-KEY")
    if not isinstance(key_id, str) or not key_id:
        raise CryptoColdStartPreIoInterlockError("cold-start request Key ID is missing")
    if not isinstance(secret_key, str) or not secret_key:
        raise CryptoColdStartPreIoInterlockError("cold-start request Secret is missing")
    try:
        return AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)
    except ValueError as exc:
        raise CryptoColdStartPreIoInterlockError(
            "cold-start request credentials are invalid"
        ) from exc


def _request_payload(body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoColdStartPreIoInterlockError(
            "cold-start guarded request body is invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise CryptoColdStartPreIoInterlockError(
            "cold-start guarded request body root must be object"
        )
    return payload


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ColdStartFinalGuardedCryptoEntryTransport",
    "CryptoColdStartPreIoExecutionContext",
    "CryptoColdStartPreIoInterlockError",
]
