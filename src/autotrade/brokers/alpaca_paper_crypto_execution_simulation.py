from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from autotrade.domain import RiskDecision
from autotrade.oms import OrderManagementSystem
from autotrade.product_profile import ProductCapabilities

from .alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from .alpaca_paper_crypto_canary_coordinator import PreparedCryptoPaperCanaryPackage
from .alpaca_paper_crypto_execution_attempt import (
    CryptoExecutionAttemptCheckpoint,
    SQLiteCryptoExecutionAttemptRegistry,
)
from .alpaca_paper_crypto_final_guard import (
    CryptoFinalWriteAttestation,
    CryptoFinalWritePhase,
    CryptoPaperFinalWriteGuard,
)
from .alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from .alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketAttestation
from .alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecision,
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
)
from .alpaca_paper_crypto_order import AlpacaPaperCryptoOrderRequest, CryptoOrderRole
from .alpaca_paper_crypto_pre_io import (
    DeterministicCryptoPaperSimulationTransport,
    FinalGuardedCryptoEntryTransport,
)
from .alpaca_paper_crypto_writer import (
    AlpacaPaperCryptoWriter,
    AlpacaPaperCryptoWriterConfig,
    CryptoPaperWriteReceipt,
    CryptoPaperWriterAmbiguous,
)
from .alpaca_paper_flat_account import PaperFlatAccountAttestation
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation, AlpacaPaperCredentials


_SIMULATION_CREDENTIALS = AlpacaPaperCredentials(
    key_id="simulation-paper-key",
    secret_key="simulation-paper-secret",
)


class CryptoExecutionSimulationError(RuntimeError):
    pass


class CryptoExecutionSimulationBlocked(CryptoExecutionSimulationError):
    pass


class CryptoExecutionSimulationReconcileOnly(CryptoExecutionSimulationError):
    """The durable lifecycle is UNKNOWN; simulation must not retry the writer."""


@dataclass(frozen=True, slots=True)
class CryptoExecutionSimulationTimeline:
    pre_consume_at: datetime
    consume_at: datetime
    stage_at: datetime
    pre_io_at: datetime

    def __post_init__(self) -> None:
        for label, value in (
            ("pre_consume_at", self.pre_consume_at),
            ("consume_at", self.consume_at),
            ("stage_at", self.stage_at),
            ("pre_io_at", self.pre_io_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be timezone-aware")
        if not self.pre_consume_at < self.consume_at <= self.stage_at < self.pre_io_at:
            raise ValueError("simulation timeline must be PRE_CONSUME < CONSUME <= STAGE < PRE_IO")


@dataclass(frozen=True, slots=True)
class CryptoExecutionSimulationResult:
    checkpoint: CryptoExecutionAttemptCheckpoint
    pre_io_attestation: CryptoFinalWriteAttestation
    write_receipt: CryptoPaperWriteReceipt
    handoff_id: str
    simulated_transport_calls: int
    restart_action: str

    def __post_init__(self) -> None:
        if self.pre_io_attestation.phase is not CryptoFinalWritePhase.PRE_IO:
            raise ValueError("simulation result requires PRE_IO attestation")
        if self.simulated_transport_calls != 1:
            raise ValueError("simulation result requires exactly one simulated transport call")
        if self.restart_action != "RECONCILE_ONLY":
            raise ValueError("post-submit simulation restart action must be RECONCILE_ONLY")


class CryptoPaperExecutionSimulationCoordinator:
    """End-to-end first-entry rehearsal with zero network authority.

    This coordinator proves the real authority ordering while delegating only to
    an in-memory deterministic transport:
      PRE_CONSUME -> durable checkpoint -> human CONSUMED -> OMS SUBMITTING ->
      writer persists ENTRY_SUBMISSION_UNKNOWN -> PRE_IO -> one simulated POST.

    It never reads environment credentials, never constructs the HTTPS transport
    and is intentionally disconnected from Mac/UI surfaces.
    """

    def __init__(
        self,
        *,
        oms: OrderManagementSystem,
        final_guard: CryptoPaperFinalWriteGuard,
        attempt_registry: SQLiteCryptoExecutionAttemptRegistry,
    ) -> None:
        if not isinstance(oms, OrderManagementSystem):
            raise TypeError("simulation coordinator requires authoritative OMS")
        if not isinstance(final_guard, CryptoPaperFinalWriteGuard):
            raise TypeError("simulation coordinator requires crypto Final Freshness guard")
        if not isinstance(attempt_registry, SQLiteCryptoExecutionAttemptRegistry):
            raise TypeError("simulation coordinator requires durable execution-attempt registry")
        self._oms = oms
        self._guard = final_guard
        self._attempts = attempt_registry

    def execute_entry(
        self,
        *,
        package: PreparedCryptoPaperCanaryPackage,
        operator_decision: CryptoOperatorDecision,
        operator_registry: SQLiteCryptoOperatorDecisionRegistry,
        broker_order: AlpacaPaperCryptoOrderRequest,
        lifecycle: SQLiteCryptoPaperLifecycle,
        risk_decision: RiskDecision,
        prepared_market: AlpacaPaperCryptoMarketAttestation,
        prepared_account: AlpacaPaperAccountAttestation,
        prepared_asset: AlpacaPaperCryptoAssetAttestation,
        prepared_product_profile: ProductCapabilities,
        fresh_account: AlpacaPaperAccountAttestation,
        fresh_asset: AlpacaPaperCryptoAssetAttestation,
        fresh_product_profile: ProductCapabilities,
        fresh_market: AlpacaPaperCryptoMarketAttestation,
        fresh_flat_account: PaperFlatAccountAttestation,
        timeline: CryptoExecutionSimulationTimeline,
    ) -> CryptoExecutionSimulationResult:
        self._validate_bindings(
            package=package,
            operator_decision=operator_decision,
            broker_order=broker_order,
        )
        attempt_id = operator_decision.context.attempt_id
        state = operator_registry.get(operator_decision.context.preparation_hash)
        if state.decision != operator_decision:
            raise CryptoExecutionSimulationBlocked("durable operator decision differs from supplied decision")

        if state.status is CryptoOperatorDecisionStatus.ISSUED:
            pre_consume = self._guard.authorize(
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
                now=timeline.pre_consume_at,
                phase=CryptoFinalWritePhase.PRE_CONSUME,
            )
            checkpoint = self._attempts.record_pre_consume(pre_consume)
            operator_registry.consume(
                decision=operator_decision,
                attempt_id=attempt_id,
                now=timeline.consume_at,
            )
        elif state.status is CryptoOperatorDecisionStatus.CONSUMED:
            if state.consumed_attempt_id != attempt_id:
                raise CryptoExecutionSimulationBlocked("operator decision was consumed by another attempt")
            try:
                checkpoint = self._attempts.get(attempt_id)
            except KeyError as exc:
                raise CryptoExecutionSimulationBlocked(
                    "consumed operator decision has no durable PRE_CONSUME checkpoint"
                ) from exc
            self._validate_checkpoint(
                checkpoint=checkpoint,
                package=package,
                operator_decision=operator_decision,
                broker_order=broker_order,
            )
        else:
            raise CryptoExecutionSimulationBlocked("unsupported operator decision state")

        handoff_id = _simulation_handoff_id(
            package=package,
            operator_decision=operator_decision,
        )
        self._oms.stage_external_submission(
            order_id=package.order_id,
            handoff_id=handoff_id,
            decision=risk_decision,
            market=prepared_market.market,
            now=timeline.stage_at,
        )

        lifecycle_state = lifecycle.snapshot(package.lifecycle_id).state
        if lifecycle_state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
            raise CryptoExecutionSimulationReconcileOnly(
                "entry is already UNKNOWN; same-attempt restart is reconciliation-only"
            )
        if lifecycle_state.status is not CryptoLifecycleStatus.ENTRY_PREPARED:
            raise CryptoExecutionSimulationBlocked("entry simulation requires durable ENTRY_PREPARED")

        latest_pre_io: CryptoFinalWriteAttestation | None = None

        def authorize_pre_io() -> CryptoFinalWriteAttestation:
            nonlocal latest_pre_io
            latest_pre_io = self._guard.authorize(
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
                now=timeline.pre_io_at,
                phase=CryptoFinalWritePhase.PRE_IO,
                expected_attempt_id=attempt_id,
                previous_attestation=checkpoint.pre_consume,
            )
            return latest_pre_io

        simulated = DeterministicCryptoPaperSimulationTransport()
        guarded = FinalGuardedCryptoEntryTransport(
            delegate=simulated,
            authorizer=authorize_pre_io,
        )
        writer = AlpacaPaperCryptoWriter(
            config=AlpacaPaperCryptoWriterConfig(enabled=True),
            transport=guarded,
        )
        try:
            receipt = writer.submit_once(
                lifecycle=lifecycle,
                lifecycle_id=package.lifecycle_id,
                order=broker_order,
                credentials=_SIMULATION_CREDENTIALS,
                now=timeline.pre_io_at,
            )
        except CryptoPaperWriterAmbiguous as exc:
            raise CryptoExecutionSimulationReconcileOnly(
                "writer crossed durable UNKNOWN; simulation is reconciliation-only"
            ) from exc

        if latest_pre_io is None or guarded.last_attestation != latest_pre_io:
            raise CryptoExecutionSimulationBlocked("simulated POST occurred without retained PRE_IO evidence")
        state_after = lifecycle.snapshot(package.lifecycle_id).state
        if state_after.status is not CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
            raise CryptoExecutionSimulationBlocked("writer did not retain durable UNKNOWN after acknowledgement")
        return CryptoExecutionSimulationResult(
            checkpoint=checkpoint,
            pre_io_attestation=latest_pre_io,
            write_receipt=receipt,
            handoff_id=handoff_id,
            simulated_transport_calls=simulated.calls,
            restart_action=state_after.restart_action,
        )

    @staticmethod
    def _validate_bindings(
        *,
        package: PreparedCryptoPaperCanaryPackage,
        operator_decision: CryptoOperatorDecision,
        broker_order: AlpacaPaperCryptoOrderRequest,
    ) -> None:
        if broker_order.role is not CryptoOrderRole.ENTRY:
            raise CryptoExecutionSimulationBlocked("simulation coordinator accepts ENTRY only")
        context = operator_decision.context
        if context.prepared_package_hash != package.package_hash:
            raise CryptoExecutionSimulationBlocked("operator decision package hash mismatch")
        if context.lifecycle_id != package.lifecycle_id or context.order_id != package.order_id:
            raise CryptoExecutionSimulationBlocked("operator decision package identity mismatch")
        if context.client_order_id != broker_order.client_order_id:
            raise CryptoExecutionSimulationBlocked("operator decision client_order_id mismatch")
        if broker_order.fingerprint != package.crypto_order_fingerprint:
            raise CryptoExecutionSimulationBlocked("broker order differs from prepared package")

    @staticmethod
    def _validate_checkpoint(
        *,
        checkpoint: CryptoExecutionAttemptCheckpoint,
        package: PreparedCryptoPaperCanaryPackage,
        operator_decision: CryptoOperatorDecision,
        broker_order: AlpacaPaperCryptoOrderRequest,
    ) -> None:
        if checkpoint.attempt_id != operator_decision.context.attempt_id:
            raise CryptoExecutionSimulationBlocked("checkpoint attempt mismatch")
        if checkpoint.package_hash != package.package_hash:
            raise CryptoExecutionSimulationBlocked("checkpoint package mismatch")
        if checkpoint.preparation_hash != operator_decision.context.preparation_hash:
            raise CryptoExecutionSimulationBlocked("checkpoint preparation mismatch")
        if checkpoint.operator_decision_hash != operator_decision.decision_hash:
            raise CryptoExecutionSimulationBlocked("checkpoint operator-decision mismatch")
        if checkpoint.order_id != package.order_id:
            raise CryptoExecutionSimulationBlocked("checkpoint order mismatch")
        if checkpoint.client_order_id != broker_order.client_order_id:
            raise CryptoExecutionSimulationBlocked("checkpoint client_order_id mismatch")


def _simulation_handoff_id(
    *,
    package: PreparedCryptoPaperCanaryPackage,
    operator_decision: CryptoOperatorDecision,
) -> str:
    raw = "|".join(
        (
            "R6_CRYPTO_SIMULATION_HANDOFF",
            package.package_hash,
            operator_decision.decision_hash,
            operator_decision.context.attempt_id,
            package.order_id,
        )
    )
    return sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "CryptoExecutionSimulationBlocked",
    "CryptoExecutionSimulationError",
    "CryptoExecutionSimulationReconcileOnly",
    "CryptoExecutionSimulationResult",
    "CryptoExecutionSimulationTimeline",
    "CryptoPaperExecutionSimulationCoordinator",
]
