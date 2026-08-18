from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Protocol

from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_unknown_recovery import (
    CryptoColdStartUnknownRecoveryCoordinator,
)
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    ATTEMPT_ID_RE,
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from autotrade.brokers.alpaca_paper_crypto_order import (
    AlpacaPaperCryptoOrderRequest,
    CryptoOrderRole,
    CryptoOrderSide,
)
from autotrade.brokers.alpaca_paper_crypto_reconciliation import (
    AlpacaPaperCryptoReconciliationGateway,
    CryptoBrokerReconciliation,
    CryptoBrokerUnknownReconciliation,
)
from autotrade.brokers.alpaca_paper_flat_account import AlpacaPaperFlatAccountGateway
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import BrokerOrderType, TimeInForce


EXPECTED_SYMBOL = "BTC/USD"
_FINAL_INITIAL_RECONCILIATION_STATUS = (
    "CRYPTO_PAPER_FIRST_CANARY_RECONCILED_NO_RETRY"
)


class CryptoFirstCanaryRecoveryError(RuntimeError):
    pass


class FirstCanaryReconciliationGateway(Protocol):
    def reconcile(self, *, credentials, order, now): ...


class FirstCanaryAccountGateway(Protocol):
    def attest_account(self, *, credentials, expected_account_id, now): ...


class FirstCanaryFlatGateway(Protocol):
    def attest_flatness(
        self,
        *,
        credentials,
        account_attestation_fingerprint,
        expected_credential_reference,
        now,
    ): ...


def recover_first_canary(
    *,
    workspace_path: Path,
    attempt_id: str,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    reconciliation_gateway: FirstCanaryReconciliationGateway | None = None,
    account_gateway: FirstCanaryAccountGateway | None = None,
    flat_gateway: FirstCanaryFlatGateway | None = None,
) -> dict[str, object]:
    """Recover a burned first-canary attempt using GET-only broker truth.

    This module has no writer, no POST transport and no retry-authority surface.
    `execution_started.json` is treated as an irreversible replay latch. Only a
    final recovery resolution is immutable; pending/open states may be queried
    again with GETs until they become terminal or require a deliberate halt.
    """

    instant = _aware(now)
    if not ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise CryptoFirstCanaryRecoveryError("execution attempt_id is invalid")
    if not isinstance(credentials, AlpacaPaperCredentials):
        raise TypeError("ephemeral PAPER credentials are required")

    workspace = _workspace(workspace_path)
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace.root,
        attempt_id=attempt_id,
    )

    if attempt.recovery_resolution_path.exists():
        existing = attempt.read(path=attempt.recovery_resolution_path)
        attempt.require_document_hash(
            existing,
            hash_key="recovery_resolution_hash",
            label="first-canary recovery resolution",
        )
        return existing

    started = attempt.read(path=attempt.execution_started_path)
    attempt.require_document_hash(
        started,
        hash_key="execution_started_hash",
        label="first-canary execution-start latch",
    )
    if started.get("attempt_id") != attempt_id:
        raise CryptoFirstCanaryRecoveryError("execution-start attempt mismatch")
    if started.get("retry_forbidden") is not True:
        raise CryptoFirstCanaryRecoveryError(
            "execution-start evidence does not permanently forbid POST retry"
        )
    if started.get("writer_invocation_permitted_once") is not True:
        raise CryptoFirstCanaryRecoveryError(
            "execution-start one-shot writer latch is invalid"
        )

    preparation = attempt.read(path=attempt.preparation_path)
    attempt.require_document_hash(
        preparation,
        hash_key="preparation_hash",
        label="first-canary preparation",
    )
    if preparation.get("attempt_id") != attempt_id:
        raise CryptoFirstCanaryRecoveryError("preparation attempt mismatch")
    if preparation.get("credential_reference") != credentials.credential_reference:
        raise CryptoFirstCanaryRecoveryError(
            "effective PAPER credential differs from prepared attempt"
        )

    package = preparation.get("prepared_package")
    if not isinstance(package, dict):
        raise CryptoFirstCanaryRecoveryError(
            "persisted prepared package is missing"
        )
    lifecycle_id = _required_text(package, "lifecycle_id")
    package_hash = _required_text(package, "package_hash")
    package_order_id = _required_text(package, "order_id")
    package_client_order_id = _required_text(package, "client_order_id")
    package_binding_hash = _required_text(package, "lifecycle_binding_hash")

    order = _order_from_preparation(preparation=preparation)
    if order.client_order_id != package_client_order_id:
        raise CryptoFirstCanaryRecoveryError(
            "prepared package/order client_order_id mismatch"
        )
    if started.get("client_order_id") != order.client_order_id:
        raise CryptoFirstCanaryRecoveryError(
            "execution-start client_order_id differs from prepared order"
        )
    if started.get("package_hash") != package_hash:
        raise CryptoFirstCanaryRecoveryError(
            "execution-start package hash differs from prepared package"
        )

    runtime = SQLiteRuntime(attempt.database_path)
    checkpoint_registry = SQLiteCryptoColdStartExecutionAttemptRegistry(runtime)
    checkpoint = checkpoint_registry.get(attempt_id)
    if checkpoint.client_order_id != order.client_order_id:
        raise CryptoFirstCanaryRecoveryError(
            "cold-start checkpoint client_order_id differs from prepared order"
        )
    if checkpoint.package_hash != package_hash:
        raise CryptoFirstCanaryRecoveryError(
            "cold-start checkpoint package differs from prepared package"
        )
    if checkpoint.order_id != package_order_id:
        raise CryptoFirstCanaryRecoveryError(
            "cold-start checkpoint order differs from prepared package"
        )
    if checkpoint.pre_consume.lifecycle_binding_hash != package_binding_hash:
        raise CryptoFirstCanaryRecoveryError(
            "cold-start checkpoint lifecycle binding differs from prepared package"
        )
    if started.get("checkpoint_hash") != checkpoint.record_hash:
        raise CryptoFirstCanaryRecoveryError(
            "execution-start checkpoint hash differs from durable checkpoint"
        )
    if checkpoint.pre_consume.credential_reference != credentials.credential_reference:
        raise CryptoFirstCanaryRecoveryError(
            "cold-start checkpoint credential differs from effective credential"
        )

    lifecycle = SQLiteCryptoPaperLifecycle(runtime)
    before = lifecycle.snapshot(lifecycle_id).state

    # If the same-process execution already persisted a terminal entry
    # reconciliation, it is authoritative enough to show without new network I/O.
    if attempt.reconciliation_path.exists():
        initial = attempt.read(path=attempt.reconciliation_path)
        attempt.require_document_hash(
            initial,
            hash_key="reconciliation_hash",
            label="first-canary initial reconciliation",
        )
        if initial.get("status") == _FINAL_INITIAL_RECONCILIATION_STATUS:
            return initial

    gateway = reconciliation_gateway or AlpacaPaperCryptoReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True)
    )
    evidence = gateway.reconcile(
        credentials=credentials,
        order=order,
        now=instant,
    )

    if isinstance(evidence, CryptoBrokerReconciliation):
        return _resolve_found_order(
            attempt=attempt,
            lifecycle=lifecycle,
            lifecycle_id=lifecycle_id,
            order=order,
            evidence=evidence,
            before_status=before.status,
            now=instant,
        )
    if not isinstance(evidence, CryptoBrokerUnknownReconciliation):
        raise CryptoFirstCanaryRecoveryError(
            "GET-only reconciliation returned unsupported evidence"
        )

    account_reader = account_gateway or AlpacaPaperAccountGateway(
        config=AlpacaPaperGatewayConfig(enabled=True)
    )
    fresh_account = account_reader.attest_account(
        credentials=credentials,
        expected_account_id=_account_id_anchor(workspace),
        now=instant,
    )
    if fresh_account.account_reference != checkpoint.pre_consume.account_reference:
        raise CryptoFirstCanaryRecoveryError(
            "fresh PAPER account differs from cold-start checkpoint"
        )
    if fresh_account.credential_reference != credentials.credential_reference:
        raise CryptoFirstCanaryRecoveryError(
            "fresh PAPER account credential differs from effective credential"
        )

    flat_account = None
    if evidence.position.quantity == 0:
        flat_reader = flat_gateway or AlpacaPaperFlatAccountGateway(
            config=AlpacaPaperGatewayConfig(enabled=True)
        )
        flat_account = flat_reader.attest_flatness(
            credentials=credentials,
            account_attestation_fingerprint=fresh_account.fingerprint,
            expected_credential_reference=credentials.credential_reference,
            now=instant,
        )

    current = lifecycle.snapshot(lifecycle_id).state
    if current.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
        receipt = CryptoColdStartUnknownRecoveryCoordinator().recover_entry(
            lifecycle=lifecycle,
            lifecycle_id=lifecycle_id,
            requested_order=order,
            reconciliation=evidence,
            checkpoint=checkpoint,
            fresh_account=fresh_account,
            flat_account=flat_account,
            now=instant,
        )
        document: dict[str, object] = {
            "schema_version": 1,
            "status": (
                "CRYPTO_PAPER_FIRST_CANARY_RECOVERED_FLAT_NO_RETRY"
                if receipt.resulting_status is CryptoLifecycleStatus.FLAT_RECONCILED
                else "CRYPTO_PAPER_FIRST_CANARY_RECOVERED_HALTED_EXPOSURE_NO_RETRY"
            ),
            "attempt_id": attempt_id,
            "client_order_id": order.client_order_id,
            "checkpoint_hash": checkpoint.record_hash,
            "reconciliation_type": "ORDER_404_PLUS_POSITION",
            "reconciliation_fingerprint": evidence.fingerprint,
            "position_quantity": str(evidence.position.quantity),
            "flat_account_fingerprint": receipt.flat_account_fingerprint,
            "resulting_lifecycle_status": receipt.resulting_status.value,
            "entry_attempt_count": receipt.attempt_count,
            "cold_start_recovery_receipt_hash": receipt.receipt_hash,
            "retry_post": False,
            "recovery_get_only": True,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
            "resolved_at": instant.isoformat(),
        }
        return _persist_resolution(attempt=attempt, document=document)

    if current.status is CryptoLifecycleStatus.ENTRY_PREPARED:
        all_flat = bool(
            evidence.position.quantity == 0
            and flat_account is not None
            and flat_account.clean_for_first_canary
        )
        document = {
            "schema_version": 1,
            "status": (
                "CRYPTO_PAPER_FIRST_CANARY_BURNED_PRE_WRITER_FLAT_NO_RETRY"
                if all_flat
                else "CRYPTO_PAPER_FIRST_CANARY_BURNED_PRE_WRITER_MANUAL_REVIEW_NO_RETRY"
            ),
            "attempt_id": attempt_id,
            "client_order_id": order.client_order_id,
            "checkpoint_hash": checkpoint.record_hash,
            "reconciliation_type": "ORDER_404_PLUS_POSITION_AFTER_REPLAY_LATCH",
            "reconciliation_fingerprint": evidence.fingerprint,
            "position_quantity": str(evidence.position.quantity),
            "all_account_flat": all_flat,
            "resulting_lifecycle_status": current.status.value,
            "entry_attempt_count": current.entry_attempt_count,
            "retry_post": False,
            "recovery_get_only": True,
            "manual_review_required": not all_flat,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
            "resolved_at": instant.isoformat(),
        }
        return _persist_resolution(attempt=attempt, document=document)

    if current.status in {
        CryptoLifecycleStatus.ENTRY_ACKNOWLEDGED,
        CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED,
    }:
        return {
            "schema_version": 1,
            "status": "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECONCILIATION_PENDING_NO_RETRY",
            "attempt_id": attempt_id,
            "client_order_id": order.client_order_id,
            "reconciliation_type": "ORDER_404_PLUS_POSITION_AFTER_PRIOR_ACK",
            "position_quantity": str(evidence.position.quantity),
            "resulting_lifecycle_status": current.status.value,
            "entry_attempt_count": current.entry_attempt_count,
            "retry_post": False,
            "recovery_get_only": True,
            "persisted_final_resolution": False,
            "live_trading": "BLOCKED",
            "observed_at": instant.isoformat(),
        }

    if current.status in {
        CryptoLifecycleStatus.FLAT_RECONCILED,
        CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED,
    }:
        document = {
            "schema_version": 1,
            "status": "CRYPTO_PAPER_FIRST_CANARY_ALREADY_RECOVERED_NO_RETRY",
            "attempt_id": attempt_id,
            "client_order_id": order.client_order_id,
            "checkpoint_hash": checkpoint.record_hash,
            "reconciliation_type": "ORDER_404_PLUS_POSITION_ALREADY_RESOLVED",
            "reconciliation_fingerprint": evidence.fingerprint,
            "position_quantity": str(evidence.position.quantity),
            "resulting_lifecycle_status": current.status.value,
            "entry_attempt_count": current.entry_attempt_count,
            "retry_post": False,
            "recovery_get_only": True,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
            "resolved_at": instant.isoformat(),
        }
        return _persist_resolution(attempt=attempt, document=document)

    raise CryptoFirstCanaryRecoveryError(
        f"lifecycle state {current.status.value} requires manual recovery; POST retry remains forbidden"
    )


def _resolve_found_order(
    *,
    attempt: FirstCanaryAttemptWorkspace,
    lifecycle: SQLiteCryptoPaperLifecycle,
    lifecycle_id: str,
    order: AlpacaPaperCryptoOrderRequest,
    evidence: CryptoBrokerReconciliation,
    before_status: CryptoLifecycleStatus,
    now: datetime,
) -> dict[str, object]:
    if before_status in {
        CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN,
        CryptoLifecycleStatus.ENTRY_ACKNOWLEDGED,
        CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED,
    }:
        state = AlpacaPaperCryptoReconciliationGateway.apply_to_lifecycle(
            lifecycle=lifecycle,
            lifecycle_id=lifecycle_id,
            requested_order=order,
            reconciliation=evidence,
            at=now,
        )
    elif before_status in {
        CryptoLifecycleStatus.ENTRY_TERMINAL_NO_FILL,
        CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED,
    }:
        state = lifecycle.snapshot(lifecycle_id).state
        if state.entry_broker_order_id != evidence.order.broker_order_id:
            raise CryptoFirstCanaryRecoveryError(
                "durable terminal broker order differs from GET-only reconciliation"
            )
        if state.entry_filled_quantity != evidence.order.filled_quantity:
            raise CryptoFirstCanaryRecoveryError(
                "durable terminal fill differs from GET-only reconciliation"
            )
        if state.confirmed_net_long_quantity != evidence.position.quantity:
            raise CryptoFirstCanaryRecoveryError(
                "durable terminal position differs from GET-only reconciliation"
            )
    elif before_status is CryptoLifecycleStatus.ENTRY_PREPARED:
        document: dict[str, object] = {
            "schema_version": 1,
            "status": "CRYPTO_PAPER_FIRST_CANARY_BROKER_ORDER_FOUND_WITHOUT_DURABLE_UNKNOWN_MANUAL_REVIEW_NO_RETRY",
            "attempt_id": attempt.attempt_id,
            "client_order_id": order.client_order_id,
            "reconciliation_type": "ORDER_PLUS_POSITION_AFTER_REPLAY_LATCH",
            "reconciliation_fingerprint": evidence.fingerprint,
            "broker_order_id": evidence.order.broker_order_id,
            "broker_order_status": evidence.order.status,
            "broker_filled_quantity": str(evidence.order.filled_quantity),
            "position_quantity": str(evidence.position.quantity),
            "resulting_lifecycle_status": before_status.value,
            "entry_attempt_count": 0,
            "retry_post": False,
            "recovery_get_only": True,
            "manual_review_required": True,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
            "resolved_at": now.isoformat(),
        }
        return _persist_resolution(attempt=attempt, document=document)
    else:
        raise CryptoFirstCanaryRecoveryError(
            f"found broker order is incompatible with lifecycle {before_status.value}; POST retry remains forbidden"
        )

    document = {
        "schema_version": 1,
        "status": (
            "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECONCILIATION_FINAL_NO_RETRY"
            if evidence.order.terminal
            else "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECONCILIATION_PENDING_NO_RETRY"
        ),
        "attempt_id": attempt.attempt_id,
        "client_order_id": order.client_order_id,
        "reconciliation_type": "ORDER_PLUS_POSITION",
        "reconciliation_fingerprint": evidence.fingerprint,
        "broker_order_id": evidence.order.broker_order_id,
        "broker_order_status": evidence.order.status,
        "broker_filled_quantity": str(evidence.order.filled_quantity),
        "position_quantity": str(evidence.position.quantity),
        "resulting_lifecycle_status": state.status.value,
        "entry_attempt_count": state.entry_attempt_count,
        "retry_post": False,
        "recovery_get_only": True,
        "credentials_persisted": False,
        "live_trading": "BLOCKED",
        "observed_at": now.isoformat(),
    }
    if not evidence.order.terminal:
        document["persisted_final_resolution"] = False
        return document
    return _persist_resolution(attempt=attempt, document=document)


def _persist_resolution(
    *,
    attempt: FirstCanaryAttemptWorkspace,
    document: dict[str, object],
) -> dict[str, object]:
    document["recovery_resolution_hash"] = attempt.document_hash(
        document,
        hash_key="recovery_resolution_hash",
    )
    attempt.write_once(
        path=attempt.recovery_resolution_path,
        document=document,
    )
    return document


def _order_from_preparation(
    *,
    preparation: dict[str, object],
) -> AlpacaPaperCryptoOrderRequest:
    broker = preparation.get("broker_order")
    if not isinstance(broker, dict):
        raise CryptoFirstCanaryRecoveryError(
            "persisted first-canary broker order is missing"
        )
    payload = broker.get("payload")
    if not isinstance(payload, dict):
        raise CryptoFirstCanaryRecoveryError(
            "persisted first-canary broker payload is missing"
        )
    if broker.get("role") != CryptoOrderRole.ENTRY.value:
        raise CryptoFirstCanaryRecoveryError(
            "recovery accepts first-canary ENTRY only"
        )
    if payload.get("symbol") != EXPECTED_SYMBOL:
        raise CryptoFirstCanaryRecoveryError(
            "recovery accepts exact BTC/USD only"
        )
    try:
        order = AlpacaPaperCryptoOrderRequest(
            role=CryptoOrderRole.ENTRY,
            symbol=str(payload["symbol"]),
            side=CryptoOrderSide(str(payload["side"])),
            quantity=_decimal(payload.get("qty"), label="qty"),
            order_type=BrokerOrderType(str(payload["type"])),
            time_in_force=TimeInForce(str(payload["time_in_force"])),
            client_order_id=str(payload["client_order_id"]),
            product_profile_fingerprint=str(
                broker["product_profile_fingerprint"]
            ),
            asset_attestation_fingerprint=str(
                broker["asset_attestation_fingerprint"]
            ),
            limit_price=(
                None
                if payload.get("limit_price") is None
                else _decimal(payload.get("limit_price"), label="limit_price")
            ),
            stop_price=(
                None
                if payload.get("stop_price") is None
                else _decimal(payload.get("stop_price"), label="stop_price")
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CryptoFirstCanaryRecoveryError(
            "persisted first-canary broker order is invalid"
        ) from exc
    if order.fingerprint != broker.get("fingerprint"):
        raise CryptoFirstCanaryRecoveryError(
            "persisted first-canary broker order fingerprint mismatch"
        )
    if order.payload_hash != broker.get("payload_hash"):
        raise CryptoFirstCanaryRecoveryError(
            "persisted first-canary broker payload hash mismatch"
        )
    if order.to_payload() != payload:
        raise CryptoFirstCanaryRecoveryError(
            "persisted first-canary broker payload is non-canonical"
        )
    return order


def _account_id_anchor(workspace: PaperOperationalWorkspace) -> str:
    path = workspace.account_attestation_path
    if path.is_symlink() or not path.is_file():
        raise CryptoFirstCanaryRecoveryError(
            "verified PAPER account evidence is required before recovery"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoFirstCanaryRecoveryError(
            "verified PAPER account evidence is unreadable"
        ) from exc
    if not isinstance(raw, dict) or raw.get("environment") != "PAPER":
        raise CryptoFirstCanaryRecoveryError(
            "workspace account evidence is not PAPER"
        )
    if raw.get("credentials_persisted") is not False:
        raise CryptoFirstCanaryRecoveryError(
            "workspace account evidence violates credential policy"
        )
    return _required_text(raw, "account_id")


def _workspace(workspace_path: Path) -> PaperOperationalWorkspace:
    if not isinstance(workspace_path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    raw = workspace_path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise CryptoFirstCanaryRecoveryError(
            "existing non-symlink PAPER workspace is required"
        )
    return PaperOperationalWorkspace(root=raw.resolve())


def _required_text(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CryptoFirstCanaryRecoveryError(f"{key} is missing")
    return value.strip()


def _decimal(value: object, *, label: str) -> Decimal:
    if not isinstance(value, str):
        raise CryptoFirstCanaryRecoveryError(
            f"{label} must be a decimal string"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CryptoFirstCanaryRecoveryError(f"{label} is invalid") from exc
    if not parsed.is_finite():
        raise CryptoFirstCanaryRecoveryError(f"{label} must be finite")
    return parsed


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


__all__ = [
    "CryptoFirstCanaryRecoveryError",
    "FirstCanaryAccountGateway",
    "FirstCanaryFlatGateway",
    "FirstCanaryReconciliationGateway",
    "recover_first_canary",
]
