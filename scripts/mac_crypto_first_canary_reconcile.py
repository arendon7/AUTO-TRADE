from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
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


WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
EXPECTED_SYMBOL = "BTC/USD"


class CryptoFirstCanaryRecoveryError(RuntimeError):
    pass


class _ReconciliationGateway(Protocol):
    def reconcile(self, *, credentials, order, now): ...


class _AccountGateway(Protocol):
    def attest_account(self, *, credentials, expected_account_id, now): ...


class _FlatGateway(Protocol):
    def attest_flatness(
        self,
        *,
        credentials,
        account_attestation_fingerprint,
        expected_credential_reference,
        now,
    ): ...


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _credentials() -> AlpacaPaperCredentials:
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        raise CryptoFirstCanaryRecoveryError(
            "PAPER Key + Secret are required for GET-only first-canary recovery"
        )
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def _workspace(workspace_path: Path) -> PaperOperationalWorkspace:
    if not isinstance(workspace_path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    raw = workspace_path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise CryptoFirstCanaryRecoveryError(
            "existing non-symlink PAPER workspace is required"
        )
    return PaperOperationalWorkspace(root=raw.resolve())


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
    account_id = raw.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise CryptoFirstCanaryRecoveryError("workspace PAPER account ID is missing")
    return account_id.strip()


def _decimal(value: object, *, label: str) -> Decimal:
    if not isinstance(value, str):
        raise CryptoFirstCanaryRecoveryError(f"{label} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CryptoFirstCanaryRecoveryError(f"{label} is invalid") from exc
    if not parsed.is_finite():
        raise CryptoFirstCanaryRecoveryError(f"{label} must be finite")
    return parsed


def _order_from_preparation(
    *,
    attempt: FirstCanaryAttemptWorkspace,
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
        raise CryptoFirstCanaryRecoveryError("recovery accepts first-canary ENTRY only")
    if payload.get("symbol") != EXPECTED_SYMBOL:
        raise CryptoFirstCanaryRecoveryError("recovery accepts exact BTC/USD only")
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


def recover_first_canary(
    *,
    workspace_path: Path,
    attempt_id: str,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    reconciliation_gateway: _ReconciliationGateway | None = None,
    account_gateway: _AccountGateway | None = None,
    flat_gateway: _FlatGateway | None = None,
) -> dict[str, object]:
    """GET-only recovery for an attempt whose POST replay is already burned."""

    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoFirstCanaryRecoveryError(
            "GET-only recovery refuses broker-write enabled environment"
        )
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
    order = _order_from_preparation(
        attempt=attempt,
        preparation=preparation,
    )
    if started.get("client_order_id") != order.client_order_id:
        raise CryptoFirstCanaryRecoveryError(
            "execution-start client_order_id differs from prepared order"
        )

    runtime = SQLiteRuntime(attempt.database_path)
    checkpoint = SQLiteCryptoColdStartExecutionAttemptRegistry(runtime).get(attempt_id)
    if checkpoint.client_order_id != order.client_order_id:
        raise CryptoFirstCanaryRecoveryError(
            "cold-start checkpoint client_order_id differs from prepared order"
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
    before = lifecycle.snapshot(checkpoint.pre_consume.lifecycle_id).state
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
            lifecycle_id=checkpoint.pre_consume.lifecycle_id,
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

    current = lifecycle.snapshot(checkpoint.pre_consume.lifecycle_id).state
    if current.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
        receipt = CryptoColdStartUnknownRecoveryCoordinator().recover_entry(
            lifecycle=lifecycle,
            lifecycle_id=checkpoint.pre_consume.lifecycle_id,
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
        document = {
            "schema_version": 1,
            "status": (
                "CRYPTO_PAPER_FIRST_CANARY_BURNED_PRE_WRITER_FLAT_NO_RETRY"
                if evidence.position.quantity == 0
                and flat_account is not None
                and flat_account.clean_for_first_canary
                else "CRYPTO_PAPER_FIRST_CANARY_BURNED_PRE_WRITER_MANUAL_REVIEW_NO_RETRY"
            ),
            "attempt_id": attempt_id,
            "client_order_id": order.client_order_id,
            "checkpoint_hash": checkpoint.record_hash,
            "reconciliation_type": "ORDER_404_PLUS_POSITION_AFTER_REPLAY_LATCH",
            "reconciliation_fingerprint": evidence.fingerprint,
            "position_quantity": str(evidence.position.quantity),
            "all_account_flat": bool(
                flat_account is not None and flat_account.clean_for_first_canary
            ),
            "resulting_lifecycle_status": current.status.value,
            "entry_attempt_count": current.entry_attempt_count,
            "retry_post": False,
            "recovery_get_only": True,
            "manual_review_required": evidence.position.quantity > 0,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "GET-only recovery/reconciliation for a burned BTC/USD PAPER first-canary attempt. "
            "This command has no writer/POST surface and can never authorize retry."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--allow-paper-recovery-read", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_recovery_read:
        raise SystemExit(
            "first-canary recovery requires explicit --allow-paper-recovery-read"
        )
    try:
        result = recover_first_canary(
            workspace_path=args.workspace,
            attempt_id=str(args.attempt_id),
            credentials=_credentials(),
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        result = {
            "status": "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECOVERY_BLOCKED",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "retry_post": False,
            "recovery_get_only": True,
            "credentials_persisted": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
