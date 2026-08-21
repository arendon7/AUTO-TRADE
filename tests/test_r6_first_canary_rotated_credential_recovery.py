from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.first_canary_fee_aware_recovery import (
    FirstCanaryCompactPositionReconciliationGateway,
)
from autotrade.first_canary_rotated_credential_recovery import (
    FirstCanaryCredentialRotationRecoveryError,
    _credential_for_recovery,
    recover_first_canary_with_safe_credential_rotation,
)
from test_r6_first_canary_execution_gate import (
    _FoundReconciler,
    _final,
)
from test_r6_first_canary_recovery import (
    _StaticAccountGateway,
    _StaticReconciliationGateway,
    _burn_to_unknown,
)


class _CompactStaticGateway(FirstCanaryCompactPositionReconciliationGateway):
    def __init__(self, evidence) -> None:
        self.evidence = evidence
        self.calls = 0

    def reconcile(self, **_kwargs):
        self.calls += 1
        return self.evidence


def _rotated_credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(
        key_id="rotated-paper-key",
        secret_key="rotated-paper-secret",
    )


def test_rotated_key_is_aliased_only_after_same_account_get_proof(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    rotated = _rotated_credentials()
    final = _final(inputs, at=execute_at + timedelta(seconds=1))
    same_account_new_key = replace(
        final.account,
        credential_reference=rotated.credential_reference,
    )
    account_gateway = _StaticAccountGateway(same_account_new_key)

    effective = _credential_for_recovery(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=rotated,
        now=execute_at + timedelta(seconds=1),
        rotation_account_gateway=account_gateway,
    )

    preparation = session.attempt.read(path=session.attempt.preparation_path)
    assert effective.key_id == rotated.key_id
    assert effective.secret_key == rotated.secret_key
    assert effective.credential_reference == preparation["credential_reference"]
    assert effective.credential_reference != rotated.credential_reference
    assert account_gateway.calls == 1
    proofs = list(session.attempt.attempt_root.glob("recovery_credential_rotation-*.json"))
    assert len(proofs) == 1
    proof = session.attempt.read(path=proofs[0])
    session.attempt.require_document_hash(
        proof,
        hash_key="credential_rotation_proof_hash",
        label="credential rotation proof",
    )
    assert proof["prepared_credential_reference"] == preparation["credential_reference"]
    assert proof["recovery_credential_reference"] == rotated.credential_reference
    assert proof["account_reference"] == same_account_new_key.account_reference
    assert proof["retry_post"] is False
    assert proof["recovery_get_only"] is True
    assert proof["credentials_persisted"] is False
    assert proof["live_trading"] == "BLOCKED"
    text = proofs[0].read_text(encoding="utf-8")
    assert rotated.key_id not in text
    assert rotated.secret_key not in text


def test_rotated_key_wrong_account_fails_before_order_reconciliation(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    rotated = _rotated_credentials()
    final = _final(inputs, at=execute_at + timedelta(seconds=1))
    wrong_account = replace(
        final.account,
        account_reference="e" * 64,
        credential_reference=rotated.credential_reference,
    )
    account_gateway = _StaticAccountGateway(wrong_account)
    found = _FoundReconciler(status="canceled").reconcile(
        credentials=rotated,
        order=inputs.broker_order,
        now=execute_at + timedelta(seconds=1),
    )
    reconciliation_gateway = _CompactStaticGateway(found)

    with pytest.raises(
        FirstCanaryCredentialRotationRecoveryError,
        match="different account",
    ):
        recover_first_canary_with_safe_credential_rotation(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=rotated,
            now=execute_at + timedelta(seconds=1),
            reconciliation_gateway=reconciliation_gateway,
            rotation_account_gateway=account_gateway,
        )
    assert account_gateway.calls == 1
    assert reconciliation_gateway.calls == 0


def test_rotated_same_account_key_recovers_found_order_with_no_post_retry(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    rotated = _rotated_credentials()
    recovery_at = execute_at + timedelta(seconds=1)
    final = _final(inputs, at=recovery_at)
    same_account_new_key = replace(
        final.account,
        credential_reference=rotated.credential_reference,
    )
    found = _FoundReconciler(status="canceled").reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=recovery_at,
    )
    reconciliation_gateway = _CompactStaticGateway(found)

    result = recover_first_canary_with_safe_credential_rotation(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=rotated,
        now=recovery_at,
        reconciliation_gateway=reconciliation_gateway,
        rotation_account_gateway=_StaticAccountGateway(same_account_new_key),
    )

    assert result["status"] == "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECONCILIATION_FINAL_NO_RETRY"
    assert result["retry_post"] is False
    assert result["recovery_get_only"] is True
    assert result["entry_attempt_count"] == 1
    assert reconciliation_gateway.calls == 1


def test_safe_rotation_wrapper_rejects_generic_reconciliation_gateway(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    with pytest.raises(
        FirstCanaryCredentialRotationRecoveryError,
        match="compact BTCUSD GET-only",
    ):
        recover_first_canary_with_safe_credential_rotation(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=inputs.credentials,
            now=execute_at + timedelta(seconds=1),
            reconciliation_gateway=_StaticReconciliationGateway(object()),
        )


def test_same_prepared_key_needs_no_rotation_account_get(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    gateway = _StaticAccountGateway(object())
    effective = _credential_for_recovery(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=inputs.credentials,
        now=execute_at + timedelta(seconds=1),
        rotation_account_gateway=gateway,
    )
    assert effective is inputs.credentials
    assert gateway.calls == 0
