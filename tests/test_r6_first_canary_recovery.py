from __future__ import annotations

from datetime import timedelta

from autotrade.first_canary_execution_gate import execute_first_canary_once
from autotrade.first_canary_recovery import recover_first_canary
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
import autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io as cold_pre_io
from test_r6_first_canary_execution_gate import (
    NOW,
    _AmbiguousDelegate,
    _FoundReconciler,
    _UnknownReconciler,
    _final,
    _prepare_session,
)


class _UnavailableReconciler:
    def reconcile(self, **_kwargs):
        raise TimeoutError("synthetic initial reconciliation outage")


class _StaticReconciliationGateway:
    def __init__(self, evidence) -> None:
        self.evidence = evidence
        self.calls = 0

    def reconcile(self, **_kwargs):
        self.calls += 1
        return self.evidence


class _StaticAccountGateway:
    def __init__(self, account) -> None:
        self.account = account
        self.calls = 0

    def attest_account(self, **_kwargs):
        self.calls += 1
        return self.account


class _StaticFlatGateway:
    def __init__(self, flat) -> None:
        self.flat = flat
        self.calls = 0

    def attest_flatness(self, **_kwargs):
        self.calls += 1
        return self.flat


def _burn_to_unknown(tmp_path, monkeypatch):
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    session.workspace.write_account_attestation(session.account)
    execute_at = NOW + timedelta(seconds=4, milliseconds=300)
    monkeypatch.setattr(
        cold_pre_io,
        "_utc_now",
        lambda: execute_at + timedelta(milliseconds=35),
    )
    outcome = execute_first_canary_once(
        inputs=inputs,
        final_evidence=_final(inputs, at=execute_at),
        delegate=_AmbiguousDelegate(),
        reconciler=_UnavailableReconciler(),
        now=execute_at,
    )
    assert outcome.lifecycle_status == "ENTRY_SUBMISSION_UNKNOWN"
    assert session.attempt.execution_started_path.is_file()
    assert session.attempt.reconciliation_path.is_file()
    return session, inputs, execute_at


def test_get_only_recovery_resolves_404_plus_flat_and_is_idempotent_without_more_gets(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    recovery_at = execute_at + timedelta(seconds=1)
    final = _final(inputs, at=recovery_at)
    unknown = _UnknownReconciler().reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=recovery_at,
    )
    reconciliation_gateway = _StaticReconciliationGateway(unknown)
    account_gateway = _StaticAccountGateway(final.account)
    flat_gateway = _StaticFlatGateway(final.flat_account)

    result = recover_first_canary(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=inputs.credentials,
        now=recovery_at,
        reconciliation_gateway=reconciliation_gateway,
        account_gateway=account_gateway,
        flat_gateway=flat_gateway,
    )

    assert result["status"] == "CRYPTO_PAPER_FIRST_CANARY_RECOVERED_FLAT_NO_RETRY"
    assert result["retry_post"] is False
    assert result["recovery_get_only"] is True
    assert result["credentials_persisted"] is False
    assert result["entry_attempt_count"] == 1
    assert session.attempt.recovery_resolution_path.is_file()
    state = SQLiteCryptoPaperLifecycle(inputs.attempt_runtime).snapshot(
        inputs.package.lifecycle_id
    ).state
    assert state.status is CryptoLifecycleStatus.FLAT_RECONCILED
    assert state.entry_attempt_count == 1
    assert reconciliation_gateway.calls == 1
    assert account_gateway.calls == 1
    assert flat_gateway.calls == 1

    no_more_reconciliation = _StaticReconciliationGateway(object())
    no_more_account = _StaticAccountGateway(object())
    no_more_flat = _StaticFlatGateway(object())
    again = recover_first_canary(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=inputs.credentials,
        now=recovery_at + timedelta(seconds=1),
        reconciliation_gateway=no_more_reconciliation,
        account_gateway=no_more_account,
        flat_gateway=no_more_flat,
    )
    assert again == result
    assert no_more_reconciliation.calls == 0
    assert no_more_account.calls == 0
    assert no_more_flat.calls == 0


def test_get_only_recovery_applies_found_terminal_order_without_account_side_reads(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    recovery_at = execute_at + timedelta(seconds=1)
    found = _FoundReconciler(status="canceled").reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=recovery_at,
    )
    reconciliation_gateway = _StaticReconciliationGateway(found)

    result = recover_first_canary(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=inputs.credentials,
        now=recovery_at,
        reconciliation_gateway=reconciliation_gateway,
    )

    assert result["status"] == "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECONCILIATION_FINAL_NO_RETRY"
    assert result["broker_order_status"] == "canceled"
    assert result["retry_post"] is False
    assert result["recovery_get_only"] is True
    assert result["entry_attempt_count"] == 1
    state = SQLiteCryptoPaperLifecycle(inputs.attempt_runtime).snapshot(
        inputs.package.lifecycle_id
    ).state
    assert state.status is CryptoLifecycleStatus.ENTRY_TERMINAL_NO_FILL
    assert state.entry_attempt_count == 1
    assert reconciliation_gateway.calls == 1


def test_get_only_recovery_refuses_wrong_effective_credential_before_broker_get(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    wrong = type(inputs.credentials)(
        key_id="different-paper-key",
        secret_key="different-paper-secret",
    )
    gateway = _StaticReconciliationGateway(object())

    try:
        recover_first_canary(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=wrong,
            now=execute_at + timedelta(seconds=1),
            reconciliation_gateway=gateway,
        )
    except Exception as exc:
        assert "credential" in str(exc).lower()
    else:
        raise AssertionError("wrong credential must fail closed")
    assert gateway.calls == 0
