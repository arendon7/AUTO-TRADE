from __future__ import annotations

from datetime import datetime

from autotrade.cold_start_oms import (
    ColdStartExternalSubmissionConflict,
    ColdStartOrderManagementSystem,
)
from autotrade.product_profile import ProductCapabilities

from .alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from .alpaca_paper_crypto_canary_coordinator import PreparedCryptoPaperCanaryPackage
from .alpaca_paper_crypto_cold_start_execution_attempt import (
    CryptoColdStartExecutionAttemptCheckpoint,
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from .alpaca_paper_crypto_cold_start_execution_bridge import (
    CryptoColdStartExecutionBridgeBlocked,
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
    """Bind PRE_IO to the exact durable checkpoint and OMS-owned handoff."""

    def __init__(
        self,
        *,
        guard: CryptoColdStartPaperFinalWriteGuard,
        checkpoint_registry: SQLiteCryptoColdStartExecutionAttemptRegistry,
        oms: ColdStartOrderManagementSystem,
    ) -> None:
        if not isinstance(guard, CryptoColdStartPaperFinalWriteGuard):
            raise TypeError("cold-start PRE_IO authority requires isolated Final Guard")
        if not isinstance(checkpoint_registry, SQLiteCryptoColdStartExecutionAttemptRegistry):
            raise TypeError("cold-start PRE_IO authority requires durable checkpoint registry")
        if not isinstance(oms, ColdStartOrderManagementSystem):
            raise TypeError("cold-start PRE_IO authority requires cold-start OMS")
        self._guard = guard
        self._checkpoints = checkpoint_registry
        self._oms = oms

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
        try:
            authorization_id = crypto_cold_start_handoff_id(
                package=package,
                operator_decision=operator_decision,
                checkpoint=checkpoint,
            )
            handoff = self._oms.resolve_cold_start_external_submission_handoff(
                order_id=package.order_id,
                authorization_id=authorization_id,
            )
        except (CryptoColdStartExecutionBridgeBlocked, ColdStartExternalSubmissionConflict) as exc:
            raise CryptoColdStartPreIoAuthorityBlocked(
                "exact durable OMS cold-start handoff is unavailable or invalid"
            ) from exc
        expected = {
            "authorization_id": authorization_id,
            "package_hash": package.package_hash,
            "operator_decision_hash": operator_decision.decision_hash,
            "checkpoint_hash": checkpoint.record_hash,
            "authority_state_fingerprint": checkpoint.authority_state_fingerprint,
            "attempt_id": checkpoint.attempt_id,
            "order_id": package.order_id,
            "client_order_id": package.client_order_id,
            "risk_decision_id": package.risk_decision_id,
        }
        if {key: getattr(handoff, key) for key in expected} != expected:
            raise CryptoColdStartPreIoAuthorityBlocked(
                "OMS cold-start handoff does not bind exact checkpoint/package"
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
                "PRE_IO package differs from durable OMS cold-start handoff"
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


__all__ = [
    "CryptoColdStartPreIoAuthority",
    "CryptoColdStartPreIoAuthorityBlocked",
    "CryptoColdStartPreIoAuthorityError",
]
