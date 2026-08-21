from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.first_canary_fee_aware_recovery import (
    FirstCanaryCompactPositionReconciliationGateway,
    recover_first_canary_fee_aware,
)
from autotrade.first_canary_recovery import _account_id_anchor, _workspace
from autotrade.first_canary_recovery_transport import FirstCanaryRecoveryReadTransport
from autotrade.persistence import SQLiteRuntime


_ROTATION_ARTIFACT_PREFIX = "recovery_credential_rotation-"


class FirstCanaryCredentialRotationRecoveryError(RuntimeError):
    pass


class _SameAccountRecoveryCredentialAlias(AlpacaPaperCredentials):
    """Use a rotated key on the wire while preserving the prepared credential binding.

    Construction is permitted only after a fresh GET /v2/account proves the
    effective key belongs to the same anchored PAPER account. The alias lives in
    memory only and is passed exclusively into the GET-only recovery surface.
    """

    __slots__ = ("_prepared_credential_reference",)

    def __init__(
        self,
        *,
        key_id: str,
        secret_key: str,
        prepared_credential_reference: str,
    ) -> None:
        super().__init__(key_id=key_id, secret_key=secret_key)
        if (
            not isinstance(prepared_credential_reference, str)
            or len(prepared_credential_reference) != 64
            or any(char not in "0123456789abcdef" for char in prepared_credential_reference)
        ):
            raise ValueError("prepared credential reference must be lowercase SHA-256")
        object.__setattr__(
            self,
            "_prepared_credential_reference",
            prepared_credential_reference,
        )

    @property
    def credential_reference(self) -> str:
        return self._prepared_credential_reference


def _rotation_proof_path(
    attempt: FirstCanaryAttemptWorkspace,
    recovery_credential_reference: str,
) -> Path:
    return attempt.attempt_root / (
        _ROTATION_ARTIFACT_PREFIX + recovery_credential_reference[:16] + ".json"
    )


def _credential_for_recovery(
    *,
    workspace_path: Path,
    attempt_id: str,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    rotation_account_gateway=None,
) -> AlpacaPaperCredentials:
    if not isinstance(credentials, AlpacaPaperCredentials):
        raise TypeError("ephemeral PAPER credentials are required")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    instant = now.astimezone(timezone.utc)

    workspace = _workspace(workspace_path)
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace.root,
        attempt_id=attempt_id,
    )

    # A terminal resolution is immutable and requires no further network proof.
    if attempt.recovery_resolution_path.exists():
        return credentials

    started = attempt.read(path=attempt.execution_started_path)
    attempt.require_document_hash(
        started,
        hash_key="execution_started_hash",
        label="first-canary execution-start latch",
    )
    if started.get("attempt_id") != attempt_id:
        raise FirstCanaryCredentialRotationRecoveryError(
            "execution-start attempt mismatch"
        )
    if started.get("retry_forbidden") is not True:
        raise FirstCanaryCredentialRotationRecoveryError(
            "execution-start evidence does not permanently forbid POST retry"
        )
    if started.get("writer_invocation_permitted_once") is not True:
        raise FirstCanaryCredentialRotationRecoveryError(
            "execution-start one-shot writer latch is invalid"
        )

    preparation = attempt.read(path=attempt.preparation_path)
    attempt.require_document_hash(
        preparation,
        hash_key="preparation_hash",
        label="first-canary preparation",
    )
    if preparation.get("attempt_id") != attempt_id:
        raise FirstCanaryCredentialRotationRecoveryError(
            "preparation attempt mismatch"
        )
    prepared_reference = preparation.get("credential_reference")
    if not isinstance(prepared_reference, str) or len(prepared_reference) != 64:
        raise FirstCanaryCredentialRotationRecoveryError(
            "prepared credential reference is missing or invalid"
        )
    if prepared_reference == credentials.credential_reference:
        return credentials

    runtime = SQLiteRuntime(attempt.database_path)
    checkpoint = SQLiteCryptoColdStartExecutionAttemptRegistry(runtime).get(attempt_id)
    if checkpoint.pre_consume.credential_reference != prepared_reference:
        raise FirstCanaryCredentialRotationRecoveryError(
            "cold-start checkpoint credential differs from prepared attempt"
        )
    if started.get("checkpoint_hash") != checkpoint.record_hash:
        raise FirstCanaryCredentialRotationRecoveryError(
            "execution-start checkpoint hash differs from durable checkpoint"
        )

    account_reader = rotation_account_gateway or AlpacaPaperAccountGateway(
        config=AlpacaPaperGatewayConfig(enabled=True)
    )
    fresh_account = account_reader.attest_account(
        credentials=credentials,
        expected_account_id=_account_id_anchor(workspace),
        now=instant,
    )
    if fresh_account.account_reference != checkpoint.pre_consume.account_reference:
        raise FirstCanaryCredentialRotationRecoveryError(
            "rotated PAPER credential belongs to a different account"
        )
    if fresh_account.credential_reference != credentials.credential_reference:
        raise FirstCanaryCredentialRotationRecoveryError(
            "rotated PAPER account attestation credential mismatch"
        )
    if fresh_account.status != "ACTIVE" or fresh_account.currency != "USD":
        raise FirstCanaryCredentialRotationRecoveryError(
            "rotated PAPER account is not ACTIVE USD"
        )
    if (
        fresh_account.source_host != ALPACA_PAPER_TRADING_HOST
        or fresh_account.source_path != ALPACA_PAPER_ACCOUNT_PATH
    ):
        raise FirstCanaryCredentialRotationRecoveryError(
            "rotated PAPER account provenance is invalid"
        )

    proof: dict[str, object] = {
        "schema_version": 1,
        "status": "CRYPTO_PAPER_FIRST_CANARY_RECOVERY_CREDENTIAL_ROTATION_ATTESTED",
        "attempt_id": attempt_id,
        "prepared_credential_reference": prepared_reference,
        "recovery_credential_reference": credentials.credential_reference,
        "account_reference": fresh_account.account_reference,
        "fresh_account_fingerprint": fresh_account.fingerprint,
        "recovery_get_only": True,
        "retry_post": False,
        "capital_authority": "NONE",
        "credentials_persisted": False,
        "live_trading": "BLOCKED",
        "attested_at": fresh_account.attested_at.astimezone(timezone.utc).isoformat(),
    }
    proof["credential_rotation_proof_hash"] = attempt.document_hash(
        proof,
        hash_key="credential_rotation_proof_hash",
    )
    proof_path = _rotation_proof_path(attempt, credentials.credential_reference)
    if proof_path.exists():
        existing = attempt.read(path=proof_path)
        attempt.require_document_hash(
            existing,
            hash_key="credential_rotation_proof_hash",
            label="first-canary recovery credential rotation",
        )
        for key in (
            "attempt_id",
            "prepared_credential_reference",
            "recovery_credential_reference",
            "account_reference",
            "recovery_get_only",
            "retry_post",
            "capital_authority",
            "credentials_persisted",
            "live_trading",
        ):
            if existing.get(key) != proof.get(key):
                raise FirstCanaryCredentialRotationRecoveryError(
                    "existing credential-rotation recovery proof conflicts with current recovery"
                )
    else:
        attempt.write_once(path=proof_path, document=proof)

    return _SameAccountRecoveryCredentialAlias(
        key_id=credentials.key_id,
        secret_key=credentials.secret_key,
        prepared_credential_reference=prepared_reference,
    )


def recover_first_canary_with_safe_credential_rotation(
    *,
    workspace_path: Path,
    attempt_id: str,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    reconciliation_gateway=None,
    account_gateway=None,
    flat_gateway=None,
    rotation_account_gateway=None,
) -> dict[str, object]:
    """Recover one burned PAPER canary with same-account credential rotation.

    The only additional network authority is a GET /v2/account proof when the
    effective key_id changed. The broker order/position recovery is forced
    through the compact BTCUSD GET-only gateway. No writer or POST surface is
    imported or reachable here.
    """

    effective_credentials = _credential_for_recovery(
        workspace_path=workspace_path,
        attempt_id=attempt_id,
        credentials=credentials,
        now=now,
        rotation_account_gateway=rotation_account_gateway,
    )

    if reconciliation_gateway is None:
        config = AlpacaPaperGatewayConfig(enabled=True)
        transport = FirstCanaryRecoveryReadTransport(
            max_response_bytes=config.max_response_bytes
        )
        reconciliation_gateway = FirstCanaryCompactPositionReconciliationGateway(
            config=config,
            order_transport=transport,
            position_transport=transport,
        )
    elif not isinstance(
        reconciliation_gateway,
        FirstCanaryCompactPositionReconciliationGateway,
    ):
        raise FirstCanaryCredentialRotationRecoveryError(
            "first-canary recovery requires compact BTCUSD GET-only reconciliation gateway"
        )

    kwargs = {
        "workspace_path": workspace_path,
        "attempt_id": attempt_id,
        "credentials": effective_credentials,
        "now": now,
        "reconciliation_gateway": reconciliation_gateway,
    }
    if account_gateway is not None:
        kwargs["account_gateway"] = account_gateway
    if flat_gateway is not None:
        kwargs["flat_gateway"] = flat_gateway
    return recover_first_canary_fee_aware(**kwargs)


__all__ = [
    "FirstCanaryCredentialRotationRecoveryError",
    "recover_first_canary_with_safe_credential_rotation",
]
