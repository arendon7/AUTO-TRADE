from __future__ import annotations

from datetime import datetime
from typing import Iterable

from autotrade.ledger import EventLedger, LedgerEvent
from autotrade.product_profile import ProductCapabilities

from .alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from .alpaca_paper_crypto_canary_coordinator import PreparedCryptoPaperCanaryPackage
from .alpaca_paper_crypto_cold_start_execution_attempt import (
    CryptoColdStartExecutionAttemptCheckpoint,
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from .alpaca_paper_crypto_cold_start_execution_bridge import (
    CryptoColdStartExecutionBridgeBlocked,
    CryptoColdStartExternalHandoff,
    crypto_cold_start_handoff_id,
)
from .alpaca_paper_crypto_cold_start_final_guard import (
    COLD_START_SCOPE,
    CryptoColdStartFinalWriteAttestation,
    CryptoColdStartFinalWritePhase,
    CryptoColdStartPaperFinalWriteGuard,
)
from .alpaca_paper_crypto_lifecycle import SQLiteCryptoPaperLifecycle
from .alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketAttestation
from .alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecision,
    SQLiteCryptoOperatorDecisionRegistry,
)
from .alpaca_paper_crypto_order import AlpacaPaperCryptoOrderRequest
from .alpaca_paper_flat_account import PaperFlatAccountAttestation
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation


class CryptoColdStartPreIoAuthorityError(RuntimeError):
    pass


class CryptoColdStartPreIoAuthorityBlocked(CryptoColdStartPreIoAuthorityError):
    pass


class CryptoColdStartPreIoAuthority:
    """Resolve PRE_IO only from durable bootstrap checkpoint + handoff evidence.

    This coordinator has no credentials or network transport. It proves that the
    supplied PRE_CONSUME attestation is the exact durable checkpoint and that a
    tamper-evident COLD_START_EXTERNAL_HANDOFF_AUTHORIZED event exists for the
    same package/decision/attempt before delegating the fresh-state decision to
    the isolated cold-start Final Guard.
    """

    def __init__(
        self,
        *,
        guard: CryptoColdStartPaperFinalWriteGuard,
        checkpoint_registry: SQLiteCryptoColdStartExecutionAttemptRegistry,
        ledger: EventLedger,
    ) -> None:
        if not isinstance(guard, CryptoColdStartPaperFinalWriteGuard):
            raise TypeError("cold-start PRE_IO authority requires isolated Final Guard")
        if not isinstance(checkpoint_registry, SQLiteCryptoColdStartExecutionAttemptRegistry):
            raise TypeError("cold-start PRE_IO authority requires durable checkpoint registry")
        self._guard = guard
        self._checkpoints = checkpoint_registry
        self._ledger = ledger

    def authorize(
        self,
        *,
        package: PreparedCryptoPaperCanaryPackage,
        operator_decision: CryptoOperatorDecision,
        operator_registry: SQLiteCryptoOperatorDecisionRegistry,
        broker_order: AlpacaPaperCryptoOrderRequest,
        lifecycle: SQLiteCryptoPaperLifecycle,
        prepared_account: AlpacaPaperAccountAttestation,
        prepared_asset: AlpacaPaperCryptoAssetAttestation,
        prepared_product_profile: ProductCapabilities,
        fresh_account: AlpacaPaperAccountAttestation,
        fresh_asset: AlpacaPaperCryptoAssetAttestation,
        fresh_product_profile: ProductCapabilities,
        fresh_market: AlpacaPaperCryptoMarketAttestation,
        fresh_flat_account: PaperFlatAccountAttestation,
        now: datetime,
    ) -> CryptoColdStartFinalWriteAttestation:
        attempt_id = operator_decision.context.attempt_id
        try:
            checkpoint = self._checkpoints.get(attempt_id)
        except Exception as exc:
            raise CryptoColdStartPreIoAuthorityBlocked(
                "durable cold-start PRE_CONSUME checkpoint is unavailable or corrupt"
            ) from exc
        self._validate_checkpoint(
            checkpoint=checkpoint,
            package=package,
            operator_decision=operator_decision,
        )
        handoff = self._load_exact_handoff(
            checkpoint=checkpoint,
            package=package,
            operator_decision=operator_decision,
        )
        if handoff.authority_state_fingerprint != checkpoint.authority_state_fingerprint:
            raise CryptoColdStartPreIoAuthorityBlocked(
                "cold-start handoff authority fingerprint differs from PRE_CONSUME checkpoint"
            )

        attestation = self._guard.authorize(
            package=package,
            operator_decision=operator_decision,
            operator_registry=operator_registry,
            broker_order=broker_order,
            lifecycle=lifecycle,
            prepared_account=prepared_account,
            prepared_asset=prepared_asset,
            prepared_product_profile=prepared_product_profile,
            fresh_account=fresh_account,
            fresh_asset=fresh_asset,
            fresh_product_profile=fresh_product_profile,
            fresh_market=fresh_market,
            fresh_flat_account=fresh_flat_account,
            now=now,
            phase=CryptoColdStartFinalWritePhase.PRE_IO,
            expected_attempt_id=attempt_id,
            previous_attestation=checkpoint.pre_consume,
        )
        if attestation.previous_attestation_hash != checkpoint.pre_consume.attestation_hash:
            raise CryptoColdStartPreIoAuthorityBlocked(
                "PRE_IO attestation predecessor differs from durable checkpoint"
            )
        if attestation.authority_state_fingerprint != checkpoint.authority_state_fingerprint:
            raise CryptoColdStartPreIoAuthorityBlocked(
                "PRE_IO authority fingerprint differs from durable checkpoint"
            )
        if attestation.package_hash != handoff.package_hash:
            raise CryptoColdStartPreIoAuthorityBlocked(
                "PRE_IO package differs from durable cold-start handoff"
            )
        return attestation

    @staticmethod
    def _validate_checkpoint(
        *,
        checkpoint: CryptoColdStartExecutionAttemptCheckpoint,
        package: PreparedCryptoPaperCanaryPackage,
        operator_decision: CryptoOperatorDecision,
    ) -> None:
        context = operator_decision.context
        if checkpoint.package_hash != package.package_hash:
            raise CryptoColdStartPreIoAuthorityBlocked("checkpoint/package hash mismatch")
        if checkpoint.preparation_hash != context.preparation_hash:
            raise CryptoColdStartPreIoAuthorityBlocked("checkpoint/preparation hash mismatch")
        if checkpoint.operator_decision_hash != operator_decision.decision_hash:
            raise CryptoColdStartPreIoAuthorityBlocked("checkpoint/operator decision hash mismatch")
        if checkpoint.attempt_id != context.attempt_id:
            raise CryptoColdStartPreIoAuthorityBlocked("checkpoint/attempt mismatch")
        if checkpoint.order_id != package.order_id or checkpoint.client_order_id != package.client_order_id:
            raise CryptoColdStartPreIoAuthorityBlocked("checkpoint/order identity mismatch")
        if checkpoint.pre_consume.bootstrap_scope != COLD_START_SCOPE:
            raise CryptoColdStartPreIoAuthorityBlocked("checkpoint is outside cold-start bootstrap scope")

    def _load_exact_handoff(
        self,
        *,
        checkpoint: CryptoColdStartExecutionAttemptCheckpoint,
        package: PreparedCryptoPaperCanaryPackage,
        operator_decision: CryptoOperatorDecision,
    ) -> CryptoColdStartExternalHandoff:
        try:
            handoff_id = crypto_cold_start_handoff_id(
                package=package,
                operator_decision=operator_decision,
                checkpoint=checkpoint,
            )
        except CryptoColdStartExecutionBridgeBlocked as exc:
            raise CryptoColdStartPreIoAuthorityBlocked("cannot derive exact cold-start handoff") from exc
        event_id = f"cold-start-external-handoff:{package.order_id}:{handoff_id}"
        matches = tuple(event for event in self._events() if event.event_id == event_id)
        if len(matches) != 1:
            raise CryptoColdStartPreIoAuthorityBlocked(
                "exact durable cold-start handoff event is missing or duplicated"
            )
        event = matches[0]
        return _handoff_from_event(
            event=event,
            expected_handoff_id=handoff_id,
            checkpoint=checkpoint,
            package=package,
            operator_decision=operator_decision,
        )

    def _events(self) -> Iterable[LedgerEvent]:
        try:
            return tuple(self._ledger.all_events())
        except Exception as exc:
            raise CryptoColdStartPreIoAuthorityBlocked("durable cold-start handoff ledger is unavailable") from exc


def _handoff_from_event(
    *,
    event: LedgerEvent,
    expected_handoff_id: str,
    checkpoint: CryptoColdStartExecutionAttemptCheckpoint,
    package: PreparedCryptoPaperCanaryPackage,
    operator_decision: CryptoOperatorDecision,
) -> CryptoColdStartExternalHandoff:
    if event.event_type != "COLD_START_EXTERNAL_HANDOFF_AUTHORIZED":
        raise CryptoColdStartPreIoAuthorityBlocked("cold-start handoff event type mismatch")
    payload = dict(event.payload)
    expected_keys = {
        "scope",
        "handoff_id",
        "package_hash",
        "operator_decision_hash",
        "checkpoint_hash",
        "authority_state_fingerprint",
        "attempt_id",
        "order_id",
        "client_order_id",
        "risk_decision_id",
        "market_fingerprint",
        "authorized_at",
        "handoff_hash",
    }
    if set(payload) != expected_keys or payload.get("scope") != COLD_START_SCOPE:
        raise CryptoColdStartPreIoAuthorityBlocked("cold-start handoff payload is non-canonical")
    try:
        handoff = CryptoColdStartExternalHandoff(
            handoff_id=str(payload["handoff_id"]),
            package_hash=str(payload["package_hash"]),
            operator_decision_hash=str(payload["operator_decision_hash"]),
            checkpoint_hash=str(payload["checkpoint_hash"]),
            authority_state_fingerprint=str(payload["authority_state_fingerprint"]),
            attempt_id=str(payload["attempt_id"]),
            order_id=str(payload["order_id"]),
            client_order_id=str(payload["client_order_id"]),
            risk_decision_id=str(payload["risk_decision_id"]),
            market_fingerprint=str(payload["market_fingerprint"]),
            authorized_at=datetime.fromisoformat(str(payload["authorized_at"])),
            event_id=event.event_id,
            handoff_hash=str(payload["handoff_hash"]),
        )
    except Exception as exc:
        raise CryptoColdStartPreIoAuthorityBlocked("cold-start handoff evidence is invalid or tampered") from exc
    if event.occurred_at != handoff.authorized_at:
        raise CryptoColdStartPreIoAuthorityBlocked("cold-start handoff ledger timestamp mismatch")
    expected = {
        "handoff_id": expected_handoff_id,
        "package_hash": package.package_hash,
        "operator_decision_hash": operator_decision.decision_hash,
        "checkpoint_hash": checkpoint.record_hash,
        "authority_state_fingerprint": checkpoint.authority_state_fingerprint,
        "attempt_id": checkpoint.attempt_id,
        "order_id": package.order_id,
        "client_order_id": package.client_order_id,
        "risk_decision_id": package.risk_decision_id,
    }
    actual = {key: getattr(handoff, key) for key in expected}
    if actual != expected:
        raise CryptoColdStartPreIoAuthorityBlocked("cold-start handoff does not bind exact checkpoint/package")
    return handoff


__all__ = [
    "CryptoColdStartPreIoAuthority",
    "CryptoColdStartPreIoAuthorityBlocked",
    "CryptoColdStartPreIoAuthorityError",
]
