from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from autotrade.first_canary_execution_gate import execute_first_canary_once
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_unknown_recovery import (
    CryptoColdStartUnknownRecoveryBlocked,
    CryptoColdStartUnknownRecoveryCoordinator,
    CryptoColdStartUnknownRecoveryReceipt,
)
import autotrade.brokers.alpaca_paper_crypto_cold_start_unknown_recovery as recovery_mod
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


def _unknown_and_final(inputs, *, at):
    final = _final(inputs, at=at)
    unknown = _UnknownReconciler().reconcile(
        credentials=inputs.credentials,
        order=inputs.broker_order,
        now=at,
    )
    return unknown, final


def _pre_consume_namespace(checkpoint, **overrides):
    base = {
        "lifecycle_binding_hash": checkpoint.pre_consume.lifecycle_binding_hash,
        "lifecycle_status": checkpoint.pre_consume.lifecycle_status,
        "portfolio_snapshot_id": checkpoint.pre_consume.portfolio_snapshot_id,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


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


def test_cold_start_unknown_recover_entry_type_guards_fail_before_state_mutation(tmp_path, monkeypatch) -> None:
    _, inputs, lifecycle, checkpoint, execute_at = _durable_unknown(tmp_path, monkeypatch)
    recovery_at = execute_at + timedelta(seconds=1)
    unknown, final = _unknown_and_final(inputs, at=recovery_at)
    coordinator = CryptoColdStartUnknownRecoveryCoordinator()
    common = dict(
        lifecycle=lifecycle,
        lifecycle_id=inputs.package.lifecycle_id,
        requested_order=inputs.broker_order,
        reconciliation=unknown,
        checkpoint=checkpoint,
        fresh_account=final.account,
        flat_account=final.flat_account,
        now=recovery_at,
    )

    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="authoritative crypto lifecycle"):
        coordinator.recover_entry(**{**common, "lifecycle": object()})
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="exact requested crypto order"):
        coordinator.recover_entry(**{**common, "requested_order": object()})
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="order-404 UNKNOWN"):
        coordinator.recover_entry(**{**common, "reconciliation": object()})
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="PRE_CONSUME checkpoint"):
        coordinator.recover_entry(**{**common, "checkpoint": object()})
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="fresh PAPER account attestation"):
        coordinator.recover_entry(**{**common, "fresh_account": object()})


def test_cold_start_unknown_checkpoint_validation_matrix(tmp_path, monkeypatch) -> None:
    _, inputs, lifecycle, checkpoint, _ = _durable_unknown(tmp_path, monkeypatch)
    snapshot = lifecycle.snapshot(inputs.package.lifecycle_id)
    validator = CryptoColdStartUnknownRecoveryCoordinator._validate_checkpoint
    valid_pre = _pre_consume_namespace(checkpoint)

    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="client_order_id mismatch"):
        validator(
            checkpoint=SimpleNamespace(
                client_order_id="other-client-order-id",
                pre_consume=valid_pre,
            ),
            requested_order=inputs.broker_order,
            lifecycle_id=inputs.package.lifecycle_id,
            binding_hash=snapshot.binding.fingerprint,
            state=snapshot.state,
        )
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="lifecycle binding mismatch"):
        validator(
            checkpoint=SimpleNamespace(
                client_order_id=inputs.broker_order.client_order_id,
                pre_consume=_pre_consume_namespace(
                    checkpoint,
                    lifecycle_binding_hash="0" * 64,
                ),
            ),
            requested_order=inputs.broker_order,
            lifecycle_id=inputs.package.lifecycle_id,
            binding_hash=snapshot.binding.fingerprint,
            state=snapshot.state,
        )
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="did not bind ENTRY_PREPARED"):
        validator(
            checkpoint=SimpleNamespace(
                client_order_id=inputs.broker_order.client_order_id,
                pre_consume=_pre_consume_namespace(
                    checkpoint,
                    lifecycle_status=CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN,
                ),
            ),
            requested_order=inputs.broker_order,
            lifecycle_id=inputs.package.lifecycle_id,
            binding_hash=snapshot.binding.fingerprint,
            state=snapshot.state,
        )
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="durable ENTRY_SUBMISSION_UNKNOWN"):
        validator(
            checkpoint=SimpleNamespace(
                client_order_id=inputs.broker_order.client_order_id,
                pre_consume=valid_pre,
            ),
            requested_order=inputs.broker_order,
            lifecycle_id=inputs.package.lifecycle_id,
            binding_hash=snapshot.binding.fingerprint,
            state=SimpleNamespace(
                status=CryptoLifecycleStatus.ENTRY_PREPARED,
                entry_attempt_count=0,
            ),
        )
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="Portfolio provenance"):
        validator(
            checkpoint=SimpleNamespace(
                client_order_id=inputs.broker_order.client_order_id,
                pre_consume=_pre_consume_namespace(
                    checkpoint,
                    portfolio_snapshot_id="invalid-provenance",
                ),
            ),
            requested_order=inputs.broker_order,
            lifecycle_id=inputs.package.lifecycle_id,
            binding_hash=snapshot.binding.fingerprint,
            state=snapshot.state,
        )
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="lifecycle_id is required"):
        validator(
            checkpoint=SimpleNamespace(
                client_order_id=inputs.broker_order.client_order_id,
                pre_consume=valid_pre,
            ),
            requested_order=inputs.broker_order,
            lifecycle_id=" ",
            binding_hash=snapshot.binding.fingerprint,
            state=snapshot.state,
        )


def test_cold_start_unknown_account_validation_matrix(tmp_path, monkeypatch) -> None:
    _, inputs, _, checkpoint, execute_at = _durable_unknown(tmp_path, monkeypatch)
    recovery_at = execute_at + timedelta(seconds=1)
    _, final = _unknown_and_final(inputs, at=recovery_at)
    validator = CryptoColdStartUnknownRecoveryCoordinator._validate_account
    expected_account = checkpoint.pre_consume.account_reference
    expected_credential = checkpoint.pre_consume.credential_reference

    cases = [
        (replace(final.account, account_reference="0" * 64), "differs from cold-start checkpoint"),
        (replace(final.account, credential_reference="1" * 64), "credential differs"),
        (replace(final.account, status="INACTIVE"), "not ACTIVE USD"),
        (replace(final.account, source_host="api.alpaca.markets"), "provenance is invalid"),
        (replace(final.account, source_path="/v2/other"), "provenance is invalid"),
        (replace(final.account, attested_at=recovery_at + timedelta(seconds=4)), "future-dated"),
        (replace(final.account, attested_at=recovery_at - timedelta(seconds=15)), "stale"),
    ]
    for account, match in cases:
        with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match=match):
            validator(
                account=account,
                expected_account_reference=expected_account,
                expected_credential_reference=expected_credential,
                now=recovery_at,
            )


def test_cold_start_unknown_reconciliation_validation_matrix(tmp_path, monkeypatch) -> None:
    _, inputs, _, checkpoint, execute_at = _durable_unknown(tmp_path, monkeypatch)
    recovery_at = execute_at + timedelta(seconds=1)
    unknown, _ = _unknown_and_final(inputs, at=recovery_at)
    validator = CryptoColdStartUnknownRecoveryCoordinator._validate_reconciliation
    expected_credential = checkpoint.pre_consume.credential_reference

    def evidence(*, absence=None, position=None, observed_at=None):
        return SimpleNamespace(
            order_absence=absence or unknown.order_absence,
            position=position or unknown.position,
            observed_at=observed_at or unknown.observed_at,
        )

    cases = [
        (
            evidence(
                absence=replace(
                    unknown.order_absence,
                    client_order_id="other-valid-client-id",
                )
            ),
            "client_order_id mismatch",
        ),
        (
            evidence(position=replace(unknown.position, symbol="ETH/USD")),
            "position symbol mismatch",
        ),
        (
            evidence(
                absence=replace(
                    unknown.order_absence,
                    credential_reference="0" * 64,
                )
            ),
            "evidence credential mismatch",
        ),
        (
            evidence(
                position=replace(
                    unknown.position,
                    credential_reference="1" * 64,
                )
            ),
            "position credential mismatch",
        ),
        (
            evidence(
                absence=replace(
                    unknown.order_absence,
                    observed_at=recovery_at - timedelta(milliseconds=1),
                )
            ),
            "timestamps are not one atomic observation",
        ),
    ]
    for reconciliation, match in cases:
        with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match=match):
            validator(
                reconciliation=reconciliation,
                requested_order=inputs.broker_order,
                expected_credential_reference=expected_credential,
                now=recovery_at,
            )

    future_at = recovery_at + timedelta(seconds=4)
    future = evidence(
        absence=replace(unknown.order_absence, observed_at=future_at),
        position=replace(unknown.position, observed_at=future_at),
        observed_at=future_at,
    )
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="future-dated"):
        validator(
            reconciliation=future,
            requested_order=inputs.broker_order,
            expected_credential_reference=expected_credential,
            now=recovery_at,
        )

    stale_at = recovery_at - timedelta(seconds=15)
    stale = evidence(
        absence=replace(unknown.order_absence, observed_at=stale_at),
        position=replace(unknown.position, observed_at=stale_at),
        observed_at=stale_at,
    )
    with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match="stale"):
        validator(
            reconciliation=stale,
            requested_order=inputs.broker_order,
            expected_credential_reference=expected_credential,
            now=recovery_at,
        )


def test_cold_start_unknown_flat_account_validation_matrix(tmp_path, monkeypatch) -> None:
    _, inputs, _, _, execute_at = _durable_unknown(tmp_path, monkeypatch)
    recovery_at = execute_at + timedelta(seconds=1)
    _, final = _unknown_and_final(inputs, at=recovery_at)
    validator = CryptoColdStartUnknownRecoveryCoordinator._validate_flat_account

    cases = [
        (replace(final.flat_account, position_count=1), "not flat"),
        (
            replace(final.flat_account, account_attestation_fingerprint="0" * 64),
            "not bound to fresh PAPER account",
        ),
        (replace(final.flat_account, credential_reference="1" * 64), "credential differs"),
        (replace(final.flat_account, source_host="api.alpaca.markets"), "host is invalid"),
        (replace(final.flat_account, positions_path="/v2/wrong"), "positions path is invalid"),
        (replace(final.flat_account, orders_path="/v2/orders?status=all"), "open-orders path/query is invalid"),
        (replace(final.flat_account, attested_at=recovery_at + timedelta(seconds=4)), "future-dated"),
        (replace(final.flat_account, attested_at=recovery_at - timedelta(seconds=10)), "stale"),
    ]
    for flat_account, match in cases:
        with pytest.raises(CryptoColdStartUnknownRecoveryBlocked, match=match):
            validator(
                flat_account=flat_account,
                fresh_account=final.account,
                now=recovery_at,
            )


def test_cold_start_unknown_receipt_and_payload_fail_closed_edges(tmp_path, monkeypatch) -> None:
    _, inputs, lifecycle, checkpoint, execute_at = _durable_unknown(tmp_path, monkeypatch)
    recovery_at = execute_at + timedelta(seconds=1)
    unknown, final = _unknown_and_final(inputs, at=recovery_at)
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
    assert recovery_mod._receipt_payload(receipt, include_hash=True)["receipt_hash"] == receipt.receipt_hash

    with pytest.raises(ValueError, match="order_absence_fingerprint"):
        replace(receipt, order_absence_fingerprint="bad")
    with pytest.raises(ValueError, match="flat_account_fingerprint"):
        replace(receipt, flat_account_fingerprint="bad")
    with pytest.raises(ValueError, match="finite and non-negative"):
        replace(receipt, observed_position_quantity=Decimal("-1"))
    with pytest.raises(ValueError, match="exactly one write attempt"):
        replace(receipt, attempt_count=2)
    with pytest.raises(ValueError, match="never authorizes retry"):
        replace(receipt, retry_authorized=True)
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(receipt, recovered_at=datetime(2026, 8, 18, 10, 0, 0))
    wrong_hash = "0" * 64 if receipt.receipt_hash != "0" * 64 else "1" * 64
    with pytest.raises(ValueError, match="receipt hash mismatch"):
        replace(receipt, receipt_hash=wrong_hash)

    values = {
        "lifecycle_id": receipt.lifecycle_id,
        "order_absence_fingerprint": receipt.order_absence_fingerprint,
        "reconciliation_fingerprint": receipt.reconciliation_fingerprint,
        "position_fingerprint": receipt.position_fingerprint,
        "fresh_account_fingerprint": receipt.fresh_account_fingerprint,
        "flat_account_fingerprint": receipt.flat_account_fingerprint,
        "observed_position_quantity": receipt.observed_position_quantity,
        "resulting_status": receipt.resulting_status,
        "attempt_count": receipt.attempt_count,
        "client_order_id": receipt.client_order_id,
        "checkpoint_hash": receipt.checkpoint_hash,
        "retry_authorized": receipt.retry_authorized,
        "recovered_at": receipt.recovered_at,
    }
    bad_time = dict(values)
    bad_time["recovered_at"] = "not-datetime"
    with pytest.raises(TypeError, match="recovered_at must be datetime"):
        recovery_mod._receipt_payload_from_values(bad_time)
    bad_quantity = dict(values)
    bad_quantity["observed_position_quantity"] = "0"
    with pytest.raises(TypeError, match="observed_position_quantity must be Decimal"):
        recovery_mod._receipt_payload_from_values(bad_quantity)
    text_status = dict(values)
    text_status["resulting_status"] = "CUSTOM_STATUS"
    assert recovery_mod._receipt_payload_from_values(text_status)["resulting_status"] == "CUSTOM_STATUS"
    with pytest.raises(ValueError, match="timezone-aware"):
        recovery_mod._require_aware(datetime(2026, 8, 18, 10, 0, 0), "x")
    assert len(recovery_mod._hash_payload({"a": 1})) == 64
