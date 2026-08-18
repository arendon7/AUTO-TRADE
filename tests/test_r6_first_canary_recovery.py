from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest

from autotrade.first_canary_execution_gate import execute_first_canary_once
from autotrade.first_canary_recovery import (
    CryptoFirstCanaryRecoveryError,
    recover_first_canary,
)
import autotrade.first_canary_recovery as recovery_mod
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
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
    assert session.attempt.reconciliation_failure_path.is_file()
    assert session.attempt.reconciliation_pending_path.exists() is False
    assert session.attempt.reconciliation_path.exists() is False
    return session, inputs, execute_at


def _rewrite_attempt_document(session, *, path: Path, hash_key: str, mutate) -> dict[str, object]:
    document = session.attempt.read(path=path)
    changed = deepcopy(document)
    mutate(changed)
    changed.pop(hash_key, None)
    changed[hash_key] = session.attempt.document_hash(changed, hash_key=hash_key)
    path.write_text(
        json.dumps(changed, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return changed


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


def test_get_only_recovery_returns_pending_for_found_nonterminal_order_without_persisting_final_resolution(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    recovery_at = execute_at + timedelta(seconds=1)
    found = _FoundReconciler(status="accepted").reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=recovery_at,
    )
    result = recover_first_canary(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=inputs.credentials,
        now=recovery_at,
        reconciliation_gateway=_StaticReconciliationGateway(found),
    )
    assert result["status"] == "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECONCILIATION_PENDING_NO_RETRY"
    assert result["persisted_final_resolution"] is False
    assert result["retry_post"] is False
    assert session.attempt.recovery_resolution_path.exists() is False


def test_get_only_recovery_404_plus_exposure_halts_without_flatness_read(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    recovery_at = execute_at + timedelta(seconds=1)
    final = _final(inputs, at=recovery_at)
    unknown = _UnknownReconciler().reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=recovery_at,
    )
    exposed_position = replace(
        unknown.position,
        quantity=inputs.package.quantity,
        market_value=inputs.package.notional,
        average_entry_price=inputs.package.limit_price,
        absent=False,
    )
    exposed = replace(unknown, position=exposed_position)
    flat_gateway = _StaticFlatGateway(final.flat_account)
    result = recover_first_canary(
        workspace_path=session.workspace.root,
        attempt_id=session.attempt.attempt_id,
        credentials=inputs.credentials,
        now=recovery_at,
        reconciliation_gateway=_StaticReconciliationGateway(exposed),
        account_gateway=_StaticAccountGateway(final.account),
        flat_gateway=flat_gateway,
    )
    assert result["status"] == "CRYPTO_PAPER_FIRST_CANARY_RECOVERED_HALTED_EXPOSURE_NO_RETRY"
    assert result["retry_post"] is False
    assert result["position_quantity"] == str(inputs.package.quantity)
    assert flat_gateway.calls == 0


def test_get_only_recovery_refuses_wrong_effective_credential_before_broker_get(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    wrong = type(inputs.credentials)(
        key_id="different-paper-key",
        secret_key="different-paper-secret",
    )
    gateway = _StaticReconciliationGateway(object())

    with pytest.raises(Exception, match="credential"):
        recover_first_canary(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=wrong,
            now=execute_at + timedelta(seconds=1),
            reconciliation_gateway=gateway,
        )
    assert gateway.calls == 0


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("retry_forbidden", False, "permanently forbid POST retry"),
        ("writer_invocation_permitted_once", False, "one-shot writer latch"),
    ],
)
def test_recovery_rejects_rehashed_execution_latch_policy_drift_before_get(
    tmp_path,
    monkeypatch,
    field,
    value,
    match,
) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    _rewrite_attempt_document(
        session,
        path=session.attempt.execution_started_path,
        hash_key="execution_started_hash",
        mutate=lambda document: document.__setitem__(field, value),
    )
    gateway = _StaticReconciliationGateway(object())
    with pytest.raises(CryptoFirstCanaryRecoveryError, match=match):
        recover_first_canary(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=inputs.credentials,
            now=execute_at + timedelta(seconds=1),
            reconciliation_gateway=gateway,
        )
    assert gateway.calls == 0


def test_recovery_rejects_rehashed_attempt_and_package_binding_drift_before_get(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    original_started = session.attempt.execution_started_path.read_text(encoding="utf-8")
    _rewrite_attempt_document(
        session,
        path=session.attempt.execution_started_path,
        hash_key="execution_started_hash",
        mutate=lambda document: document.__setitem__("package_hash", "f" * 64),
    )
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="package hash"):
        recover_first_canary(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=inputs.credentials,
            now=execute_at + timedelta(seconds=1),
            reconciliation_gateway=_StaticReconciliationGateway(object()),
        )
    session.attempt.execution_started_path.write_text(original_started, encoding="utf-8")

    _rewrite_attempt_document(
        session,
        path=session.attempt.preparation_path,
        hash_key="preparation_hash",
        mutate=lambda document: document.__setitem__("attempt_id", "first-canary-ffffffffffffffffffffffffffffffff"),
    )
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="preparation attempt mismatch"):
        recover_first_canary(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=inputs.credentials,
            now=execute_at + timedelta(seconds=1),
            reconciliation_gateway=_StaticReconciliationGateway(object()),
        )


def test_recovery_rejects_unsupported_reconciliation_evidence(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="unsupported evidence"):
        recover_first_canary(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=inputs.credentials,
            now=execute_at + timedelta(seconds=1),
            reconciliation_gateway=_StaticReconciliationGateway(object()),
        )


def test_recovery_rejects_fresh_account_binding_drift(tmp_path, monkeypatch) -> None:
    session, inputs, execute_at = _burn_to_unknown(tmp_path, monkeypatch)
    recovery_at = execute_at + timedelta(seconds=1)
    final = _final(inputs, at=recovery_at)
    unknown = _UnknownReconciler().reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=recovery_at,
    )
    wrong_account = replace(final.account, account_reference="e" * 64)
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="account differs"):
        recover_first_canary(
            workspace_path=session.workspace.root,
            attempt_id=session.attempt.attempt_id,
            credentials=inputs.credentials,
            now=recovery_at,
            reconciliation_gateway=_StaticReconciliationGateway(unknown),
            account_gateway=_StaticAccountGateway(wrong_account),
        )


def test_resolve_found_order_entry_prepared_is_manual_review_and_no_retry(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    found = _FoundReconciler(status="accepted").reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=NOW + timedelta(seconds=5),
    )
    result = recovery_mod._resolve_found_order(
        attempt=session.attempt,
        lifecycle=SQLiteCryptoPaperLifecycle(inputs.attempt_runtime),
        lifecycle_id=inputs.package.lifecycle_id,
        order=inputs.broker_order,
        evidence=found,
        before_status=CryptoLifecycleStatus.ENTRY_PREPARED,
        now=NOW + timedelta(seconds=5),
    )
    assert result["status"].endswith("MANUAL_REVIEW_NO_RETRY")
    assert result["manual_review_required"] is True
    assert result["retry_post"] is False


def test_resolve_found_order_rejects_incompatible_lifecycle_without_mutation(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    found = _FoundReconciler(status="accepted").reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=NOW + timedelta(seconds=5),
    )
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="incompatible with lifecycle"):
        recovery_mod._resolve_found_order(
            attempt=session.attempt,
            lifecycle=SQLiteCryptoPaperLifecycle(inputs.attempt_runtime),
            lifecycle_id=inputs.package.lifecycle_id,
            order=inputs.broker_order,
            evidence=found,
            before_status=CryptoLifecycleStatus.FLAT_RECONCILED,
            now=NOW + timedelta(seconds=5),
        )


def test_recovery_order_parser_rejects_missing_wrong_product_and_corrupt_payload(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    preparation = session.attempt.read(path=session.attempt.preparation_path)

    missing_broker = deepcopy(preparation)
    missing_broker.pop("broker_order")
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="broker order is missing"):
        recovery_mod._order_from_preparation(preparation=missing_broker)

    missing_payload = deepcopy(preparation)
    missing_payload["broker_order"].pop("payload")  # type: ignore[union-attr]
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="broker payload is missing"):
        recovery_mod._order_from_preparation(preparation=missing_payload)

    wrong_role = deepcopy(preparation)
    wrong_role["broker_order"]["role"] = "PROTECTION"  # type: ignore[index]
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="ENTRY only"):
        recovery_mod._order_from_preparation(preparation=wrong_role)

    wrong_symbol = deepcopy(preparation)
    wrong_symbol["broker_order"]["payload"]["symbol"] = "ETH/USD"  # type: ignore[index]
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="BTC/USD only"):
        recovery_mod._order_from_preparation(preparation=wrong_symbol)

    bad_qty = deepcopy(preparation)
    bad_qty["broker_order"]["payload"]["qty"] = "not-decimal"  # type: ignore[index]
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="qty is invalid"):
        recovery_mod._order_from_preparation(preparation=bad_qty)

    bad_fingerprint = deepcopy(preparation)
    bad_fingerprint["broker_order"]["fingerprint"] = "0" * 64  # type: ignore[index]
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="fingerprint mismatch"):
        recovery_mod._order_from_preparation(preparation=bad_fingerprint)

    bad_payload_hash = deepcopy(preparation)
    bad_payload_hash["broker_order"]["payload_hash"] = "0" * 64  # type: ignore[index]
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="payload hash mismatch"):
        recovery_mod._order_from_preparation(preparation=bad_payload_hash)


def test_recovery_workspace_and_scalar_helpers_fail_closed(tmp_path) -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        recovery_mod._workspace(str(tmp_path))  # type: ignore[arg-type]
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="existing non-symlink"):
        recovery_mod._workspace(tmp_path / "missing")
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="is missing"):
        recovery_mod._required_text({}, "x")
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="decimal string"):
        recovery_mod._decimal(1, label="x")
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="is invalid"):
        recovery_mod._decimal("abc", label="x")
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="must be finite"):
        recovery_mod._decimal("NaN", label="x")
    with pytest.raises(ValueError, match="timezone-aware"):
        recovery_mod._aware(datetime(2026, 8, 18, 10, 0, 0))
    assert recovery_mod._required_text({"x": " value "}, "x") == "value"
    assert recovery_mod._decimal("2.50", label="x") == Decimal("2.50")


def test_account_anchor_rejects_missing_unreadable_nonpaper_and_credential_policy(tmp_path, monkeypatch) -> None:
    _, session, _ = _prepare_session(tmp_path, monkeypatch)
    path = session.workspace.account_attestation_path

    with pytest.raises(CryptoFirstCanaryRecoveryError, match="evidence is required"):
        recovery_mod._account_id_anchor(session.workspace)

    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="unreadable"):
        recovery_mod._account_id_anchor(session.workspace)

    path.write_text(json.dumps({"environment": "LIVE", "credentials_persisted": False}), encoding="utf-8")
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="not PAPER"):
        recovery_mod._account_id_anchor(session.workspace)

    path.write_text(json.dumps({"environment": "PAPER", "credentials_persisted": True, "account_id": "x"}), encoding="utf-8")
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="credential policy"):
        recovery_mod._account_id_anchor(session.workspace)

    path.write_text(json.dumps({"environment": "PAPER", "credentials_persisted": False}), encoding="utf-8")
    with pytest.raises(CryptoFirstCanaryRecoveryError, match="account_id is missing"):
        recovery_mod._account_id_anchor(session.workspace)


def test_recovery_entrypoint_rejects_invalid_attempt_credentials_and_naive_time(tmp_path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        recover_first_canary(
            workspace_path=tmp_path,
            attempt_id="bad",
            credentials=AlpacaPaperCredentials(key_id="key-a", secret_key="secret-b"),
            now=datetime(2026, 8, 18, 10, 0, 0),
        )

    with pytest.raises(CryptoFirstCanaryRecoveryError, match="attempt_id is invalid"):
        recover_first_canary(
            workspace_path=tmp_path,
            attempt_id="bad",
            credentials=AlpacaPaperCredentials(key_id="key-a", secret_key="secret-b"),
            now=NOW,
        )

    with pytest.raises(TypeError, match="ephemeral PAPER credentials"):
        recover_first_canary(
            workspace_path=tmp_path,
            attempt_id="first-canary-0123456789abcdef0123456789abcdef",
            credentials=object(),  # type: ignore[arg-type]
            now=NOW,
        )
