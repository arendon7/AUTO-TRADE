from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from autotrade.first_canary_execution_gate import execute_first_canary_once
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_unknown_recovery import (
    CryptoColdStartUnknownRecoveryCoordinator,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
import autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io as cold_pre_io
from test_r6_first_canary_execution_gate import (
    NOW,
    _AmbiguousDelegate,
    _UnknownReconciler,
    _final,
    _prepare_session,
)


class _UnavailableReconciler:
    def reconcile(self, **_kwargs):
        raise TimeoutError("synthetic first reconciliation outage")


def _durable_unknown(tmp_path, monkeypatch):
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
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
    checkpoint = SQLiteCryptoColdStartExecutionAttemptRegistry(
        inputs.attempt_runtime
    ).get(inputs.attempt.attempt_id)
    lifecycle = SQLiteCryptoPaperLifecycle(inputs.attempt_runtime)
    return session, inputs, lifecycle, checkpoint, execute_at


def test_cold_start_unknown_404_plus_fresh_all_account_flatness_recovers_flat(tmp_path, monkeypatch) -> None:
    _, inputs, lifecycle, checkpoint, execute_at = _durable_unknown(
        tmp_path, monkeypatch
    )
    recovery_at = execute_at + timedelta(seconds=1)
    final = _final(inputs, at=recovery_at)
    unknown = _UnknownReconciler().reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=recovery_at,
    )

    receipt = CryptoColdStartUnknownRecoveryCoordinator().recover_entry(
        lifecycle=lifecycle,
        lifecycle_id=inputs.package.lifecycle_id,
        requested_order=inputs.broker_order,
        reconciliation=unknown,
        checkpoint=checkpoint,
        fresh_account=final.account,
        flat_account=final.flat_account,
        now=recovery_at,
    )

    assert receipt.resulting_status is CryptoLifecycleStatus.FLAT_RECONCILED
    assert receipt.attempt_count == 1
    assert receipt.retry_authorized is False
    assert receipt.client_order_id == inputs.broker_order.client_order_id
    assert receipt.checkpoint_hash == checkpoint.record_hash
    assert lifecycle.snapshot(inputs.package.lifecycle_id).state.status is CryptoLifecycleStatus.FLAT_RECONCILED


def test_cold_start_unknown_404_with_remaining_position_halts_and_preserves_attempt_count(tmp_path, monkeypatch) -> None:
    _, inputs, lifecycle, checkpoint, execute_at = _durable_unknown(
        tmp_path, monkeypatch
    )
    recovery_at = execute_at + timedelta(seconds=1)
    final = _final(inputs, at=recovery_at)
    unknown = _UnknownReconciler().reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=recovery_at,
    )
    long_position = replace(
        unknown.position,
        quantity=inputs.broker_order.quantity,
        absent=False,
    )
    unknown_with_long = replace(unknown, position=long_position)

    receipt = CryptoColdStartUnknownRecoveryCoordinator().recover_entry(
        lifecycle=lifecycle,
        lifecycle_id=inputs.package.lifecycle_id,
        requested_order=inputs.broker_order,
        reconciliation=unknown_with_long,
        checkpoint=checkpoint,
        fresh_account=final.account,
        flat_account=None,
        now=recovery_at,
    )

    state = lifecycle.snapshot(inputs.package.lifecycle_id).state
    assert receipt.resulting_status is CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED
    assert receipt.observed_position_quantity == inputs.broker_order.quantity
    assert receipt.retry_authorized is False
    assert receipt.attempt_count == 1
    assert state.status is CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED
    assert state.entry_attempt_count == 1
    assert state.confirmed_net_long_quantity == inputs.broker_order.quantity
