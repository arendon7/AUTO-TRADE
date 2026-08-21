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


def test_rotated_credential_alias_rejects_invalid_prepared_reference() -> None:
    import autotrade.first_canary_rotated_credential_recovery as rotation_mod

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        rotation_mod._SameAccountRecoveryCredentialAlias(
            key_id="rotated-paper-key",
            secret_key="rotated-paper-secret",
            prepared_credential_reference="not-a-sha",
        )


def test_rotation_requires_ephemeral_paper_credentials_and_aware_time(tmp_path, monkeypatch) -> None:
    from datetime import datetime

    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    with pytest.raises(TypeError, match="ephemeral PAPER credentials"):
        _credential_for_recovery(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=object(),  # type: ignore[arg-type]
            now=execute_at + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        _credential_for_recovery(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=inputs.credentials,
            now=datetime(2026, 8, 21, 0, 0, 0),
        )


def test_terminal_resolution_short_circuits_rotation_without_account_get(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    document = {
        "schema_version": 1,
        "status": "TEST_TERMINAL_RECOVERY",
        "retry_post": False,
        "recovery_get_only": True,
        "live_trading": "BLOCKED",
    }
    document["recovery_resolution_hash"] = session.attempt.document_hash(
        document,
        hash_key="recovery_resolution_hash",
    )
    session.attempt.write_once(
        path=session.attempt.recovery_resolution_path,
        document=document,
    )
    rotated = _rotated_credentials()
    gateway = _StaticAccountGateway(object())
    effective = _credential_for_recovery(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=rotated,
        now=execute_at + timedelta(seconds=1),
        rotation_account_gateway=gateway,
    )
    assert effective is rotated
    assert gateway.calls == 0


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("attempt_id", "first-canary-ffffffffffffffffffffffffffffffff", "execution-start attempt mismatch"),
        ("retry_forbidden", False, "permanently forbid POST retry"),
        ("writer_invocation_permitted_once", False, "one-shot writer latch"),
    ],
)
def test_rotation_rejects_execution_latch_drift_before_account_get(
    tmp_path, monkeypatch, field, value, match
) -> None:
    from test_r6_first_canary_recovery import _rewrite_attempt_document

    session, _inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    _rewrite_attempt_document(
        session,
        path=session.attempt.execution_started_path,
        hash_key="execution_started_hash",
        mutate=lambda document: document.__setitem__(field, value),
    )
    gateway = _StaticAccountGateway(object())
    with pytest.raises(FirstCanaryCredentialRotationRecoveryError, match=match):
        _credential_for_recovery(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=_rotated_credentials(),
            now=execute_at + timedelta(seconds=1),
            rotation_account_gateway=gateway,
        )
    assert gateway.calls == 0


def test_rotation_rejects_preparation_attempt_and_reference_drift(tmp_path, monkeypatch) -> None:
    from test_r6_first_canary_recovery import _rewrite_attempt_document

    session, _inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    original = session.attempt.preparation_path.read_text(encoding="utf-8")
    _rewrite_attempt_document(
        session,
        path=session.attempt.preparation_path,
        hash_key="preparation_hash",
        mutate=lambda document: document.__setitem__(
            "attempt_id", "first-canary-ffffffffffffffffffffffffffffffff"
        ),
    )
    with pytest.raises(FirstCanaryCredentialRotationRecoveryError, match="preparation attempt mismatch"):
        _credential_for_recovery(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=_rotated_credentials(),
            now=execute_at + timedelta(seconds=1),
            rotation_account_gateway=_StaticAccountGateway(object()),
        )

    session.attempt.preparation_path.write_text(original, encoding="utf-8")
    _rewrite_attempt_document(
        session,
        path=session.attempt.preparation_path,
        hash_key="preparation_hash",
        mutate=lambda document: document.__setitem__("credential_reference", "short"),
    )
    with pytest.raises(FirstCanaryCredentialRotationRecoveryError, match="missing or invalid"):
        _credential_for_recovery(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=_rotated_credentials(),
            now=execute_at + timedelta(seconds=1),
            rotation_account_gateway=_StaticAccountGateway(object()),
        )


def test_rotation_rejects_checkpoint_hash_drift_before_account_get(tmp_path, monkeypatch) -> None:
    from test_r6_first_canary_recovery import _rewrite_attempt_document

    session, _inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    _rewrite_attempt_document(
        session,
        path=session.attempt.execution_started_path,
        hash_key="execution_started_hash",
        mutate=lambda document: document.__setitem__("checkpoint_hash", "f" * 64),
    )
    gateway = _StaticAccountGateway(object())
    with pytest.raises(FirstCanaryCredentialRotationRecoveryError, match="checkpoint hash"):
        _credential_for_recovery(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=_rotated_credentials(),
            now=execute_at + timedelta(seconds=1),
            rotation_account_gateway=gateway,
        )
    assert gateway.calls == 0


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda account, rotated: replace(account, credential_reference="e" * 64), "attestation credential mismatch"),
        (lambda account, rotated: replace(account, credential_reference=rotated.credential_reference, status="INACTIVE"), "not ACTIVE USD"),
        (lambda account, rotated: replace(account, credential_reference=rotated.credential_reference, currency="EUR"), "not ACTIVE USD"),
        (lambda account, rotated: replace(account, credential_reference=rotated.credential_reference, source_host="example.invalid"), "provenance is invalid"),
        (lambda account, rotated: replace(account, credential_reference=rotated.credential_reference, source_path="/wrong"), "provenance is invalid"),
    ],
)
def test_rotation_rejects_invalid_same_account_attestation(tmp_path, monkeypatch, mutator, match) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    rotated = _rotated_credentials()
    final = _final(inputs, at=execute_at + timedelta(seconds=1))
    bad = mutator(final.account, rotated)
    gateway = _StaticAccountGateway(bad)
    with pytest.raises(FirstCanaryCredentialRotationRecoveryError, match=match):
        _credential_for_recovery(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=rotated,
            now=execute_at + timedelta(seconds=1),
            rotation_account_gateway=gateway,
        )
    assert gateway.calls == 1


def test_existing_rotation_proof_is_reused_and_conflict_fails_closed(tmp_path, monkeypatch) -> None:
    from test_r6_first_canary_recovery import _rewrite_attempt_document

    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    rotated = _rotated_credentials()
    recovery_at = execute_at + timedelta(seconds=1)
    final = _final(inputs, at=recovery_at)
    same_account_new_key = replace(
        final.account,
        credential_reference=rotated.credential_reference,
    )
    gateway = _StaticAccountGateway(same_account_new_key)

    first = _credential_for_recovery(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=rotated,
        now=recovery_at,
        rotation_account_gateway=gateway,
    )
    second = _credential_for_recovery(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=rotated,
        now=recovery_at + timedelta(seconds=1),
        rotation_account_gateway=gateway,
    )
    assert first.credential_reference == second.credential_reference
    assert gateway.calls == 2

    proof_path = next(session.attempt.attempt_root.glob("recovery_credential_rotation-*.json"))
    _rewrite_attempt_document(
        session,
        path=proof_path,
        hash_key="credential_rotation_proof_hash",
        mutate=lambda document: document.__setitem__("live_trading", "NOT_BLOCKED"),
    )
    with pytest.raises(FirstCanaryCredentialRotationRecoveryError, match="proof conflicts"):
        _credential_for_recovery(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=rotated,
            now=recovery_at + timedelta(seconds=2),
            rotation_account_gateway=gateway,
        )


def test_safe_rotation_wrapper_builds_default_compact_gateway_and_forwards_optional_readers(
    tmp_path, monkeypatch
) -> None:
    import autotrade.first_canary_rotated_credential_recovery as rotation_mod

    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    account_reader = object()
    flat_reader = object()
    captured = {}

    def fake_fee_aware(**kwargs):
        captured.update(kwargs)
        return {
            "status": "SYNTHETIC_GET_ONLY",
            "retry_post": False,
            "recovery_get_only": True,
            "live_trading": "BLOCKED",
        }

    monkeypatch.setattr(rotation_mod, "recover_first_canary_fee_aware", fake_fee_aware)
    result = rotation_mod.recover_first_canary_with_safe_credential_rotation(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=inputs.credentials,
        now=execute_at + timedelta(seconds=1),
        account_gateway=account_reader,
        flat_gateway=flat_reader,
    )
    assert result["retry_post"] is False
    assert isinstance(
        captured["reconciliation_gateway"],
        FirstCanaryCompactPositionReconciliationGateway,
    )
    assert captured["account_gateway"] is account_reader
    assert captured["flat_gateway"] is flat_reader
    assert captured["credentials"] is inputs.credentials
