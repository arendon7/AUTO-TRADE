from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import PreparedCryptoPaperCanaryPackage
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_bridge import (
    CryptoColdStartExecutionBridge,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_final_guard import (
    CryptoColdStartFinalWritePhase,
    CryptoColdStartPaperFinalWriteGuard,
    SQLiteCryptoColdStartAuthorityProvider,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io import (
    ColdStartFinalGuardedCryptoEntryTransport,
    CryptoColdStartPreIoExecutionContext,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io_authority import (
    CryptoColdStartPreIoAuthority,
)
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketAttestation
from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_order import AlpacaPaperCryptoOrderRequest, CryptoOrderRole
from autotrade.brokers.alpaca_paper_crypto_reconciliation import (
    AlpacaPaperCryptoReconciliationGateway,
    CryptoBrokerReconciliation,
    CryptoBrokerUnknownReconciliation,
)
from autotrade.brokers.alpaca_paper_crypto_writer import (
    AlpacaPaperCryptoWriteTransport,
    AlpacaPaperCryptoWriter,
    AlpacaPaperCryptoWriterConfig,
    CryptoPaperWriteReceipt,
    CryptoPaperWriterAmbiguous,
)
from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation, AlpacaPaperCredentials
from autotrade.cold_start_oms import ColdStartOrderManagementSystem
from autotrade.domain import RiskDecision
from autotrade.persistence import SQLiteEventLedger, SQLiteOrderStore, SQLiteRuntime
from autotrade.product_profile import ProductCapabilities
from autotrade.risk_state import SQLiteR2SafetyStateStore


MAX_NOTIONAL = Decimal("5")
MIN_NOTIONAL = Decimal("1")
FINAL_EVIDENCE_TTL = timedelta(seconds=5)


class FirstCanaryExecutionError(RuntimeError):
    pass


class FirstCanaryExecutionBlocked(FirstCanaryExecutionError):
    pass


class FirstCanaryReconciler(Protocol):
    def reconcile(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        order: AlpacaPaperCryptoOrderRequest,
        now: datetime,
    ) -> CryptoBrokerReconciliation | CryptoBrokerUnknownReconciliation:
        ...


class _NoBrokerExecutionSurface:
    def submit(self, **_kwargs):
        raise FirstCanaryExecutionBlocked(
            "cold-start OMS has no direct broker submission surface"
        )


@dataclass(frozen=True, slots=True)
class FirstCanaryExecutionInputs:
    attempt: FirstCanaryAttemptWorkspace
    core_runtime: SQLiteRuntime
    attempt_runtime: SQLiteRuntime
    credentials: AlpacaPaperCredentials
    package: PreparedCryptoPaperCanaryPackage
    broker_order: AlpacaPaperCryptoOrderRequest
    prepared_account: AlpacaPaperAccountAttestation
    prepared_asset: AlpacaPaperCryptoAssetAttestation
    prepared_product_profile: ProductCapabilities
    prepared_market: AlpacaPaperCryptoMarketAttestation
    risk_decision: RiskDecision
    preparation_authority_state_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, FirstCanaryAttemptWorkspace):
            raise TypeError("first-canary execution requires exact attempt workspace")
        if not isinstance(self.core_runtime, SQLiteRuntime) or not isinstance(
            self.attempt_runtime, SQLiteRuntime
        ):
            raise TypeError("first-canary execution requires core + attempt SQLite runtimes")
        if Path(self.attempt_runtime.path).resolve() != self.attempt.database_path.resolve():
            raise FirstCanaryExecutionBlocked("attempt runtime is not exact attempt database")
        if not isinstance(self.credentials, AlpacaPaperCredentials):
            raise TypeError("first-canary execution requires ephemeral PAPER credentials")
        if not isinstance(self.package, PreparedCryptoPaperCanaryPackage):
            raise TypeError("first-canary execution requires prepared crypto package")
        if not isinstance(self.broker_order, AlpacaPaperCryptoOrderRequest):
            raise TypeError("first-canary execution requires exact broker order")
        if self.broker_order.role is not CryptoOrderRole.ENTRY:
            raise FirstCanaryExecutionBlocked("first-canary execution accepts ENTRY only")
        if self.broker_order.fingerprint != self.package.crypto_order_fingerprint:
            raise FirstCanaryExecutionBlocked("broker order/package fingerprint mismatch")
        if self.broker_order.payload_hash != self.package.crypto_order_payload_hash:
            raise FirstCanaryExecutionBlocked("broker order/package payload hash mismatch")
        if not MIN_NOTIONAL <= self.package.notional <= MAX_NOTIONAL:
            raise FirstCanaryExecutionBlocked("first-canary notional must remain within USD 1-5")
        if self.credentials.credential_reference != self.prepared_account.credential_reference:
            raise FirstCanaryExecutionBlocked("effective credential differs from prepared account")
        if self.prepared_asset.fingerprint != self.package.asset_attestation_fingerprint:
            raise FirstCanaryExecutionBlocked("prepared asset/package fingerprint mismatch")
        if self.prepared_product_profile.fingerprint != self.package.product_profile_fingerprint:
            raise FirstCanaryExecutionBlocked("prepared ProductCapabilities/package mismatch")
        if self.risk_decision.decision_id != self.package.risk_decision_id:
            raise FirstCanaryExecutionBlocked("prepared RiskDecision/package mismatch")
        if self.prepared_market.fingerprint != self.package.market_attestation_fingerprint:
            raise FirstCanaryExecutionBlocked("prepared market/package mismatch")


@dataclass(frozen=True, slots=True)
class FirstCanaryFinalEvidence:
    account: AlpacaPaperAccountAttestation
    asset: AlpacaPaperCryptoAssetAttestation
    product_profile: ProductCapabilities
    market: AlpacaPaperCryptoMarketAttestation
    flat_account: PaperFlatAccountAttestation

    def __post_init__(self) -> None:
        if self.account.status != "ACTIVE" or self.account.currency != "USD":
            raise FirstCanaryExecutionBlocked("final PAPER account must be ACTIVE USD")
        if self.asset.account_attestation_fingerprint != self.account.fingerprint:
            raise FirstCanaryExecutionBlocked("final asset/account binding mismatch")
        if self.asset.credential_reference != self.account.credential_reference:
            raise FirstCanaryExecutionBlocked("final asset credential binding mismatch")
        if self.product_profile.source_fingerprint != self.asset.fingerprint:
            raise FirstCanaryExecutionBlocked("final ProductCapabilities/asset binding mismatch")
        if self.market.market.symbol != self.asset.symbol:
            raise FirstCanaryExecutionBlocked("final market/asset symbol mismatch")
        if not self.flat_account.clean_for_first_canary:
            raise FirstCanaryExecutionBlocked("final PAPER account must remain flat")
        if self.flat_account.account_attestation_fingerprint != self.account.fingerprint:
            raise FirstCanaryExecutionBlocked("final flat-account/account binding mismatch")
        if self.flat_account.credential_reference != self.account.credential_reference:
            raise FirstCanaryExecutionBlocked("final flat-account credential binding mismatch")


@dataclass(frozen=True, slots=True)
class FirstCanaryExecutionOutcome:
    status: str
    attempt_id: str
    client_order_id: str
    execution_started_hash: str
    execution_result_hash: str
    reconciliation_hash: str | None
    lifecycle_status: str
    broker_post_outcome: str
    retry_forbidden: bool


def execute_first_canary_once(
    *,
    inputs: FirstCanaryExecutionInputs,
    final_evidence: FirstCanaryFinalEvidence,
    delegate: AlpacaPaperCryptoWriteTransport,
    reconciler: FirstCanaryReconciler,
    now: datetime,
) -> FirstCanaryExecutionOutcome:
    """Cross the certified cold-start gate exactly once using an injected delegate.

    This orchestrator contains no HTTP implementation and never constructs a raw
    network transport. Production may inject the already-audited PAPER HTTPS
    delegate only from a separate explicit Mac write gate. Tests inject a
    deterministic simulation delegate. Once `execution_started.json` exists the
    attempt is burned for POST replay, even after process/output loss.
    """

    instant = _aware(now, "now")
    if not isinstance(inputs, FirstCanaryExecutionInputs):
        raise TypeError("inputs must be FirstCanaryExecutionInputs")
    if not isinstance(final_evidence, FirstCanaryFinalEvidence):
        raise TypeError("final_evidence must be FirstCanaryFinalEvidence")
    if delegate is None or reconciler is None:
        raise TypeError("delegate and reconciler are required")
    inputs.attempt.assert_unexecuted()
    _verify_persisted_preparation(inputs)
    _verify_final_evidence(inputs=inputs, final=final_evidence, now=instant)

    authority = SQLiteCryptoColdStartAuthorityProvider(inputs.core_runtime)
    current_authority = authority.snapshot()
    if (
        current_authority.state_fingerprint
        != inputs.preparation_authority_state_fingerprint
    ):
        raise FirstCanaryExecutionBlocked(
            "authoritative Safety/Portfolio/Health state changed since preparation"
        )

    operator_registry = SQLiteCryptoOperatorDecisionRegistry(inputs.attempt_runtime)
    context = CryptoOperatorDecisionContext.from_prepared_package(
        inputs.package,
        attempt_id=inputs.attempt.attempt_id,
    )
    try:
        operator_state = operator_registry.get(context.preparation_hash)
    except Exception as exc:
        raise FirstCanaryExecutionBlocked(
            "new execution-specific human approval is missing or corrupt"
        ) from exc
    if operator_state.status is not CryptoOperatorDecisionStatus.ISSUED:
        raise FirstCanaryExecutionBlocked(
            "execution-specific human approval is not pristine ISSUED"
        )
    operator_decision = operator_state.decision
    if operator_decision.context != context:
        raise FirstCanaryExecutionBlocked("durable human approval binds a different attempt/package")
    if not operator_decision.is_valid_at(instant):
        raise FirstCanaryExecutionBlocked("human approval expired before PRE_CONSUME")

    lifecycle = SQLiteCryptoPaperLifecycle(inputs.attempt_runtime)
    order_store = SQLiteOrderStore(inputs.attempt_runtime)
    guard = CryptoColdStartPaperFinalWriteGuard(
        order_store=order_store,
        authority_provider=authority,
    )
    pre_consume = guard.authorize(
        package=inputs.package,
        operator_decision=operator_decision,
        operator_registry=operator_registry,
        broker_order=inputs.broker_order,
        lifecycle=lifecycle,
        prepared_account=inputs.prepared_account,
        prepared_asset=inputs.prepared_asset,
        prepared_product_profile=inputs.prepared_product_profile,
        fresh_account=final_evidence.account,
        fresh_asset=final_evidence.asset,
        fresh_product_profile=final_evidence.product_profile,
        fresh_market=final_evidence.market,
        fresh_flat_account=final_evidence.flat_account,
        now=instant,
        phase=CryptoColdStartFinalWritePhase.PRE_CONSUME,
    )
    checkpoint_registry = SQLiteCryptoColdStartExecutionAttemptRegistry(
        inputs.attempt_runtime
    )
    checkpoint = checkpoint_registry.record_pre_consume(pre_consume)

    cold_oms = ColdStartOrderManagementSystem(
        broker=_NoBrokerExecutionSurface(),
        ledger=SQLiteEventLedger(inputs.attempt_runtime),
        order_store=order_store,
        safety_state_store=SQLiteR2SafetyStateStore(inputs.core_runtime),
    )
    bridge = CryptoColdStartExecutionBridge(
        oms=cold_oms,
        authority_provider=authority,
    )
    consume_at = instant + timedelta(milliseconds=10)
    stage_at = instant + timedelta(milliseconds=20)
    stage = bridge.stage_after_checkpoint(
        package=inputs.package,
        operator_decision=operator_decision,
        operator_registry=operator_registry,
        checkpoint=checkpoint,
        risk_decision=inputs.risk_decision,
        market=inputs.prepared_market.market,
        consume_at=consume_at,
        stage_at=stage_at,
    )

    started: dict[str, object] = {
        "schema_version": 1,
        "status": "CRYPTO_PAPER_FIRST_CANARY_EXECUTION_STARTED_POST_REPLAY_FORBIDDEN",
        "attempt_id": inputs.attempt.attempt_id,
        "client_order_id": inputs.package.client_order_id,
        "package_hash": inputs.package.package_hash,
        "operator_preparation_hash": context.preparation_hash,
        "operator_decision_hash": operator_decision.decision_hash,
        "pre_consume_attestation_hash": pre_consume.attestation_hash,
        "checkpoint_hash": checkpoint.record_hash,
        "oms_handoff_authorization_id": stage.handoff.authorization_id,
        "oms_handoff_checkpoint_hash": stage.handoff.checkpoint_hash,
        "authority_state_fingerprint": checkpoint.authority_state_fingerprint,
        "started_at": stage_at.isoformat(),
        "oms_status": stage.order.status.value,
        "operator_decision_consumed": True,
        "writer_invocation_permitted_once": True,
        "external_post_authorized": False,
        "broker_post_outcome": "NOT_YET_INVOKED",
        "retry_forbidden": True,
        "live_trading": "BLOCKED",
    }
    started["execution_started_hash"] = inputs.attempt.document_hash(
        started,
        hash_key="execution_started_hash",
    )
    inputs.attempt.write_once(
        path=inputs.attempt.execution_started_path,
        document=started,
    )

    pre_io_authority = CryptoColdStartPreIoAuthority(
        guard=guard,
        checkpoint_registry=checkpoint_registry,
        oms=cold_oms,
    )
    pre_io_context = CryptoColdStartPreIoExecutionContext(
        package=inputs.package,
        operator_decision=operator_decision,
        operator_registry=operator_registry,
        broker_order=inputs.broker_order,
        lifecycle=lifecycle,
        prepared_account=inputs.prepared_account,
        prepared_asset=inputs.prepared_asset,
        prepared_product_profile=inputs.prepared_product_profile,
        fresh_account=final_evidence.account,
        fresh_asset=final_evidence.asset,
        fresh_product_profile=final_evidence.product_profile,
        fresh_market=final_evidence.market,
        fresh_flat_account=final_evidence.flat_account,
    )
    guarded_transport = ColdStartFinalGuardedCryptoEntryTransport(
        delegate=delegate,
        authority=pre_io_authority,
        context=pre_io_context,
    )
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True),
        transport=guarded_transport,
    )

    writer_at = instant + timedelta(milliseconds=30)
    receipt: CryptoPaperWriteReceipt | None = None
    writer_error: Exception | None = None
    try:
        receipt = writer.submit_once(
            lifecycle=lifecycle,
            lifecycle_id=inputs.package.lifecycle_id,
            order=inputs.broker_order,
            credentials=inputs.credentials,
            now=writer_at,
        )
        broker_post_outcome = "BROKER_RESPONSE_RECEIVED"
    except CryptoPaperWriterAmbiguous as exc:
        writer_error = exc
        broker_post_outcome = "UNKNOWN_RECONCILIATION_REQUIRED"
    except Exception as exc:
        writer_error = exc
        state = lifecycle.snapshot(inputs.package.lifecycle_id).state
        if state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
            broker_post_outcome = "UNKNOWN_RECONCILIATION_REQUIRED"
        else:
            broker_post_outcome = "BLOCKED_BEFORE_DURABLE_UNKNOWN"

    lifecycle_after_writer = lifecycle.snapshot(inputs.package.lifecycle_id).state
    result: dict[str, object] = {
        "schema_version": 1,
        "status": (
            "CRYPTO_PAPER_FIRST_CANARY_WRITER_RETURNED_RECONCILIATION_REQUIRED"
            if receipt is not None
            else "CRYPTO_PAPER_FIRST_CANARY_WRITER_AMBIGUOUS_RECONCILIATION_REQUIRED"
            if lifecycle_after_writer.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
            else "CRYPTO_PAPER_FIRST_CANARY_WRITER_BLOCKED_ATTEMPT_BURNED"
        ),
        "attempt_id": inputs.attempt.attempt_id,
        "client_order_id": inputs.package.client_order_id,
        "package_hash": inputs.package.package_hash,
        "execution_started_hash": started["execution_started_hash"],
        "pre_io_attestation_hash": (
            guarded_transport.last_attestation.attestation_hash
            if guarded_transport.last_attestation is not None
            else None
        ),
        "broker_post_outcome": broker_post_outcome,
        "broker_delegate_boundary_crossed": guarded_transport.last_attestation is not None,
        "durable_lifecycle_status": lifecycle_after_writer.status.value,
        "entry_attempt_count": lifecycle_after_writer.entry_attempt_count,
        "writer_error_type": None if writer_error is None else type(writer_error).__name__,
        "writer_error": None if writer_error is None else str(writer_error),
        "receipt": None if receipt is None else _receipt_payload(receipt),
        "retry_forbidden": True,
        "reconciliation_required": (
            lifecycle_after_writer.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
        ),
        "live_trading": "BLOCKED",
    }
    result["execution_result_hash"] = inputs.attempt.document_hash(
        result,
        hash_key="execution_result_hash",
    )
    inputs.attempt.write_once(
        path=inputs.attempt.execution_result_path,
        document=result,
    )

    reconciliation_hash: str | None = None
    if lifecycle_after_writer.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
        reconciliation_hash = _reconcile_once(
            inputs=inputs,
            lifecycle=lifecycle,
            reconciler=reconciler,
            at=instant + timedelta(milliseconds=40),
            execution_result_hash=str(result["execution_result_hash"]),
        )
    final_state = lifecycle.snapshot(inputs.package.lifecycle_id).state
    if lifecycle_after_writer.status is not CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
        raise FirstCanaryExecutionBlocked(
            "attempt burned before writer reached durable UNKNOWN; POST replay remains forbidden"
        ) from writer_error

    return FirstCanaryExecutionOutcome(
        status=_execution_outcome_status(final_state.status),
        attempt_id=inputs.attempt.attempt_id,
        client_order_id=inputs.package.client_order_id,
        execution_started_hash=str(started["execution_started_hash"]),
        execution_result_hash=str(result["execution_result_hash"]),
        reconciliation_hash=reconciliation_hash,
        lifecycle_status=final_state.status.value,
        broker_post_outcome=broker_post_outcome,
        retry_forbidden=True,
    )


def _execution_outcome_status(status: CryptoLifecycleStatus) -> str:
    if status in {
        CryptoLifecycleStatus.ENTRY_TERMINAL_NO_FILL,
        CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED,
    }:
        return "RECONCILED_FINAL"
    if status in {
        CryptoLifecycleStatus.ENTRY_ACKNOWLEDGED,
        CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED,
    }:
        return "RECONCILIATION_PENDING_NO_RETRY"
    return "UNKNOWN_HALTED_NO_RETRY"


def _reconcile_once(
    *,
    inputs: FirstCanaryExecutionInputs,
    lifecycle: SQLiteCryptoPaperLifecycle,
    reconciler: FirstCanaryReconciler,
    at: datetime,
    execution_result_hash: str,
) -> str:
    try:
        evidence = reconciler.reconcile(
            credentials=inputs.credentials,
            order=inputs.broker_order,
            now=at,
        )
    except Exception as exc:
        document: dict[str, object] = {
            "schema_version": 1,
            "status": "CRYPTO_PAPER_FIRST_CANARY_RECONCILIATION_FAILURE_NO_RETRY",
            "attempt_id": inputs.attempt.attempt_id,
            "client_order_id": inputs.package.client_order_id,
            "execution_result_hash": execution_result_hash,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "retry_post": False,
            "reconciliation_retry_get_only": True,
            "persisted_final_resolution": False,
            "live_trading": "BLOCKED",
        }
        document["reconciliation_hash"] = inputs.attempt.document_hash(
            document,
            hash_key="reconciliation_hash",
        )
        inputs.attempt.write_once(
            path=inputs.attempt.reconciliation_failure_path,
            document=document,
        )
        return str(document["reconciliation_hash"])

    if isinstance(evidence, CryptoBrokerUnknownReconciliation):
        document = {
            "schema_version": 1,
            "status": "CRYPTO_PAPER_FIRST_CANARY_RECONCILIATION_PENDING_ORDER_404_NO_RETRY",
            "attempt_id": inputs.attempt.attempt_id,
            "client_order_id": inputs.package.client_order_id,
            "execution_result_hash": execution_result_hash,
            "evidence_type": "ORDER_ABSENCE_PLUS_POSITION",
            "evidence_fingerprint": evidence.fingerprint,
            "position_quantity": str(evidence.position.quantity),
            "position_absent": evidence.position.absent,
            "observed_at": evidence.observed_at.astimezone(timezone.utc).isoformat(),
            "retry_post": False,
            "reconciliation_retry_get_only": True,
            "persisted_final_resolution": False,
            "live_trading": "BLOCKED",
        }
        target_path = inputs.attempt.reconciliation_pending_path
    elif isinstance(evidence, CryptoBrokerReconciliation):
        AlpacaPaperCryptoReconciliationGateway.apply_to_lifecycle(
            lifecycle=lifecycle,
            lifecycle_id=inputs.package.lifecycle_id,
            requested_order=inputs.broker_order,
            reconciliation=evidence,
            at=at,
        )
        state = lifecycle.snapshot(inputs.package.lifecycle_id).state
        terminal = evidence.order.terminal
        document = {
            "schema_version": 1,
            "status": (
                "CRYPTO_PAPER_FIRST_CANARY_RECONCILED_FINAL_NO_RETRY"
                if terminal
                else "CRYPTO_PAPER_FIRST_CANARY_RECONCILIATION_PENDING_ORDER_OPEN_NO_RETRY"
            ),
            "attempt_id": inputs.attempt.attempt_id,
            "client_order_id": inputs.package.client_order_id,
            "execution_result_hash": execution_result_hash,
            "evidence_type": "ORDER_PLUS_POSITION",
            "evidence_fingerprint": evidence.fingerprint,
            "broker_order_id": evidence.order.broker_order_id,
            "broker_order_status": evidence.order.status,
            "broker_filled_quantity": str(evidence.order.filled_quantity),
            "position_quantity": str(evidence.position.quantity),
            "lifecycle_status": state.status.value,
            "observed_at": evidence.observed_at.astimezone(timezone.utc).isoformat(),
            "retry_post": False,
            "reconciliation_retry_get_only": not terminal,
            "persisted_final_resolution": terminal,
            "live_trading": "BLOCKED",
        }
        target_path = (
            inputs.attempt.reconciliation_path
            if terminal
            else inputs.attempt.reconciliation_pending_path
        )
    else:
        raise FirstCanaryExecutionBlocked(
            "reconciler returned unsupported evidence; POST retry remains forbidden"
        )
    document["reconciliation_hash"] = inputs.attempt.document_hash(
        document,
        hash_key="reconciliation_hash",
    )
    inputs.attempt.write_once(
        path=target_path,
        document=document,
    )
    return str(document["reconciliation_hash"])


def _verify_persisted_preparation(inputs: FirstCanaryExecutionInputs) -> None:
    try:
        document = inputs.attempt.read(path=inputs.attempt.preparation_path)
    except Exception as exc:
        raise FirstCanaryExecutionBlocked(
            "persisted first-canary preparation is missing or corrupt"
        ) from exc
    inputs.attempt.require_document_hash(
        document,
        hash_key="preparation_hash",
        label="first-canary preparation",
    )
    if document.get("attempt_id") != inputs.attempt.attempt_id:
        raise FirstCanaryExecutionBlocked("persisted preparation attempt mismatch")
    if document.get("credential_reference") != inputs.credentials.credential_reference:
        raise FirstCanaryExecutionBlocked("persisted preparation credential mismatch")
    if document.get("authority_state_fingerprint") != inputs.preparation_authority_state_fingerprint:
        raise FirstCanaryExecutionBlocked("persisted preparation authority fingerprint mismatch")
    operator_context = document.get("operator_context")
    expected_context = CryptoOperatorDecisionContext.from_prepared_package(
        inputs.package,
        attempt_id=inputs.attempt.attempt_id,
    ).to_dict()
    if operator_context != expected_context:
        raise FirstCanaryExecutionBlocked("persisted preparation operator context mismatch")
    broker = document.get("broker_order")
    if not isinstance(broker, dict) or broker.get("payload") != inputs.broker_order.to_payload():
        raise FirstCanaryExecutionBlocked("persisted preparation broker order mismatch")

    try:
        approval = inputs.attempt.read(path=inputs.attempt.approval_receipt_path)
    except Exception as exc:
        raise FirstCanaryExecutionBlocked(
            "new execution-specific human approval is missing or corrupt"
        ) from exc
    inputs.attempt.require_document_hash(
        approval,
        hash_key="approval_receipt_hash",
        label="first-canary approval",
    )
    if approval.get("attempt_id") != inputs.attempt.attempt_id:
        raise FirstCanaryExecutionBlocked("persisted approval attempt mismatch")
    if approval.get("prepared_package_hash") != inputs.package.package_hash:
        raise FirstCanaryExecutionBlocked("persisted approval package mismatch")
    if approval.get("decision_consumed") is not False:
        raise FirstCanaryExecutionBlocked("persisted approval is not pristine unconsumed")


def _verify_final_evidence(
    *,
    inputs: FirstCanaryExecutionInputs,
    final: FirstCanaryFinalEvidence,
    now: datetime,
) -> None:
    if final.account.account_id != inputs.prepared_account.account_id:
        raise FirstCanaryExecutionBlocked("final PAPER account_id changed")
    if final.account.account_reference != inputs.prepared_account.account_reference:
        raise FirstCanaryExecutionBlocked("final PAPER account reference changed")
    if final.account.credential_reference != inputs.credentials.credential_reference:
        raise FirstCanaryExecutionBlocked("final PAPER credential reference changed")
    if final.asset.contract_fingerprint != inputs.prepared_asset.contract_fingerprint:
        raise FirstCanaryExecutionBlocked("final crypto asset contract changed")
    if (
        final.product_profile.contract_fingerprint
        != inputs.prepared_product_profile.contract_fingerprint
    ):
        raise FirstCanaryExecutionBlocked("final ProductCapabilities contract changed")
    for label, timestamp in (
        ("account", final.account.attested_at),
        ("asset", final.asset.observed_at),
        ("ProductCapabilities", final.product_profile.observed_at),
        ("market", final.market.received_at),
        ("flat-account", final.flat_account.attested_at),
    ):
        observed = _aware(timestamp, label)
        if observed > now + timedelta(seconds=1):
            raise FirstCanaryExecutionBlocked(f"final {label} evidence is future-dated")
        if now - observed > FINAL_EVIDENCE_TTL:
            raise FirstCanaryExecutionBlocked(
                f"final {label} evidence exceeds five-second execution TTL"
            )


def _receipt_payload(receipt: CryptoPaperWriteReceipt) -> dict[str, object]:
    return {
        "fingerprint": receipt.fingerprint,
        "lifecycle_id": receipt.lifecycle_id,
        "role": receipt.role.value,
        "broker_order_id": receipt.broker_order_id,
        "client_order_id": receipt.client_order_id,
        "symbol": receipt.symbol,
        "broker_status": receipt.broker_status,
        "requested_quantity": str(receipt.requested_quantity),
        "broker_filled_quantity": str(receipt.broker_filled_quantity),
        "request_id": receipt.request_id,
        "response_sha256": receipt.response_sha256,
        "submitted_at": receipt.submitted_at.astimezone(timezone.utc).isoformat(),
    }


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


__all__ = [
    "FirstCanaryExecutionBlocked",
    "FirstCanaryExecutionError",
    "FirstCanaryExecutionInputs",
    "FirstCanaryExecutionOutcome",
    "FirstCanaryFinalEvidence",
    "FirstCanaryReconciler",
    "execute_first_canary_once",
]
