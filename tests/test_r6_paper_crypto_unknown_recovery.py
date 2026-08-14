from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.brokers.alpaca_paper_crypto_execution_attempt import (
    SQLiteCryptoExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from autotrade.brokers.alpaca_paper_crypto_protection_execution_attempt import (
    SQLiteCryptoProtectionExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_reconciliation import (
    CryptoBrokerOrderAbsenceEvidence,
    CryptoBrokerPositionSnapshot,
    CryptoBrokerUnknownReconciliation,
)
from autotrade.brokers.alpaca_paper_crypto_unknown_recovery import (
    CryptoPaperUnknownRecoveryCoordinator,
    CryptoUnknownRecoveryBlocked,
)
from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_final_guard import _authorize_pre, _setup
from test_r6_paper_crypto_protection_final_guard import _advance_to_preio, _preconsume


def _entry_unknown(tmp_path):
    ctx = _setup(tmp_path / "entry")
    pre = _authorize_pre(ctx)
    registry = SQLiteCryptoExecutionAttemptRegistry(SQLiteRuntime(tmp_path / "entry-checkpoint.sqlite3"))
    checkpoint = registry.record_pre_consume(pre)
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=checkpoint.attempt_id,
        now=NOW + timedelta(seconds=4, milliseconds=250),
    )
    ctx.oms.stage_external_submission(
        order_id=ctx.package.order_id,
        handoff_id="a" * 64,
        decision=ctx.decision,
        market=ctx.prepared_market.market,
        now=NOW + timedelta(seconds=4, milliseconds=300),
    )
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=350),
    )
    return ctx, checkpoint


def _protection_unknown(tmp_path):
    setup_with_pre = _preconsume(tmp_path / "protection")
    setup, pre = setup_with_pre[:-1], setup_with_pre[-1]
    ctx, _entry_reconciliation, _market, _decision, prepared, _registry, _operator_decision, _guard = setup
    checkpoint_registry = SQLiteCryptoProtectionExecutionAttemptRegistry(
        SQLiteRuntime(tmp_path / "protection-checkpoint.sqlite3")
    )
    checkpoint = checkpoint_registry.record_pre_consume(pre)
    _advance_to_preio(setup, pre)
    return ctx, prepared, checkpoint


def _unknown(order, *, credential_reference: str, quantity: Decimal, observed_at):
    absence = CryptoBrokerOrderAbsenceEvidence(
        client_order_id=order.client_order_id,
        credential_reference=credential_reference,
        request_id="order-404-request",
        response_sha256="1" * 64,
        observed_at=observed_at,
    )
    position = CryptoBrokerPositionSnapshot(
        symbol=order.symbol,
        quantity=quantity,
        market_value=None,
        average_entry_price=None,
        credential_reference=credential_reference,
        request_id="position-after-404-request",
        response_sha256="2" * 64,
        observed_at=observed_at,
        absent=quantity == 0,
    )
    return CryptoBrokerUnknownReconciliation(
        order_absence=absence,
        position=position,
        observed_at=observed_at,
    )


def _flat(account, *, observed_at, positions=0, orders=0):
    return PaperFlatAccountAttestation(
        account_attestation_fingerprint=account.fingerprint,
        credential_reference=account.credential_reference,
        position_count=positions,
        open_order_count=orders,
        positions_response_hash="3" * 64,
        orders_response_hash="4" * 64,
        positions_request_id="flat-positions-request",
        orders_request_id="flat-orders-request",
        attested_at=observed_at,
    )


def test_entry_order_404_with_long_halts_and_preserves_one_attempt(tmp_path) -> None:
    ctx, checkpoint = _entry_unknown(tmp_path)
    observed = NOW + timedelta(seconds=5)
    evidence = _unknown(
        ctx.broker_order,
        credential_reference=checkpoint.pre_consume.credential_reference,
        quantity=Decimal("0.0004"),
        observed_at=observed,
    )
    receipt = CryptoPaperUnknownRecoveryCoordinator().recover(
        lifecycle=ctx.lifecycle,
        lifecycle_id=ctx.package.lifecycle_id,
        requested_order=ctx.broker_order,
        reconciliation=evidence,
        checkpoint=checkpoint,
        fresh_account=ctx.fresh_account,
        flat_account=None,
        now=NOW + timedelta(seconds=5, milliseconds=100),
    )

    state = ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED
    assert state.confirmed_net_long_quantity == Decimal("0.0004")
    assert state.entry_attempt_count == 1
    assert state.restart_action == "RECONCILE_ONLY"
    assert ctx.broker_order.client_order_id == receipt.client_order_id
    assert receipt.retry_authorized is False
    assert receipt.attempt_count == 1
    assert receipt.flat_account_fingerprint is None


def test_entry_order_404_zero_position_requires_all_account_flatness(tmp_path) -> None:
    ctx, checkpoint = _entry_unknown(tmp_path)
    observed = NOW + timedelta(seconds=5)
    evidence = _unknown(
        ctx.broker_order,
        credential_reference=checkpoint.pre_consume.credential_reference,
        quantity=Decimal("0"),
        observed_at=observed,
    )
    coordinator = CryptoPaperUnknownRecoveryCoordinator()

    with pytest.raises(CryptoUnknownRecoveryBlocked, match="all-account flatness"):
        coordinator.recover(
            lifecycle=ctx.lifecycle,
            lifecycle_id=ctx.package.lifecycle_id,
            requested_order=ctx.broker_order,
            reconciliation=evidence,
            checkpoint=checkpoint,
            fresh_account=ctx.fresh_account,
            flat_account=None,
            now=NOW + timedelta(seconds=5, milliseconds=100),
        )
    assert ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN

    dirty = _flat(ctx.fresh_account, observed_at=NOW + timedelta(seconds=5, milliseconds=50), orders=1)
    with pytest.raises(CryptoUnknownRecoveryBlocked, match="not flat"):
        coordinator.recover(
            lifecycle=ctx.lifecycle,
            lifecycle_id=ctx.package.lifecycle_id,
            requested_order=ctx.broker_order,
            reconciliation=evidence,
            checkpoint=checkpoint,
            fresh_account=ctx.fresh_account,
            flat_account=dirty,
            now=NOW + timedelta(seconds=5, milliseconds=100),
        )
    assert ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN


def test_entry_order_404_zero_position_plus_clean_account_terminalizes_without_retry(tmp_path) -> None:
    ctx, checkpoint = _entry_unknown(tmp_path)
    observed = NOW + timedelta(seconds=5)
    evidence = _unknown(
        ctx.broker_order,
        credential_reference=checkpoint.pre_consume.credential_reference,
        quantity=Decimal("0"),
        observed_at=observed,
    )
    flat = _flat(ctx.fresh_account, observed_at=NOW + timedelta(seconds=5, milliseconds=50))
    receipt = CryptoPaperUnknownRecoveryCoordinator().recover(
        lifecycle=ctx.lifecycle,
        lifecycle_id=ctx.package.lifecycle_id,
        requested_order=ctx.broker_order,
        reconciliation=evidence,
        checkpoint=checkpoint,
        fresh_account=ctx.fresh_account,
        flat_account=flat,
        now=NOW + timedelta(seconds=5, milliseconds=100),
    )

    snapshot = ctx.lifecycle.snapshot(ctx.package.lifecycle_id)
    assert snapshot.state.status is CryptoLifecycleStatus.FLAT_RECONCILED
    assert snapshot.state.confirmed_net_long_quantity == 0
    assert snapshot.state.entry_attempt_count == 1
    assert snapshot.binding.entry_client_order_id == ctx.broker_order.client_order_id
    assert receipt.retry_authorized is False
    assert receipt.flat_account_fingerprint == flat.fingerprint
    event = snapshot.events[-1]
    assert event.payload["retry_authorized"] is False
    assert event.payload["order_absence_fingerprint"] == evidence.order_absence.fingerprint
    assert event.payload["position_fingerprint"] == evidence.position.fingerprint


def test_protection_order_404_with_remaining_long_halts_without_rearm(tmp_path) -> None:
    ctx, prepared, checkpoint = _protection_unknown(tmp_path)
    observed = NOW + timedelta(seconds=8)
    evidence = _unknown(
        prepared.broker_order,
        credential_reference=checkpoint.pre_consume.credential_reference,
        quantity=prepared.package.confirmed_net_long_quantity,
        observed_at=observed,
    )
    receipt = CryptoPaperUnknownRecoveryCoordinator().recover(
        lifecycle=ctx.lifecycle,
        lifecycle_id=prepared.package.lifecycle_id,
        requested_order=prepared.broker_order,
        reconciliation=evidence,
        checkpoint=checkpoint,
        fresh_account=ctx.prepared_account,
        flat_account=None,
        now=NOW + timedelta(seconds=8, milliseconds=100),
    )

    state = ctx.lifecycle.snapshot(prepared.package.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED
    assert state.protection_attempt_count == 1
    assert state.protection_client_order_id == prepared.broker_order.client_order_id
    assert state.confirmed_net_long_quantity == prepared.package.confirmed_net_long_quantity
    assert state.restart_action == "RECONCILE_ONLY"
    assert receipt.retry_authorized is False


def test_protection_order_404_flat_requires_full_flat_account_and_can_reconcile_flat(tmp_path) -> None:
    ctx, prepared, checkpoint = _protection_unknown(tmp_path)
    observed = NOW + timedelta(seconds=8)
    evidence = _unknown(
        prepared.broker_order,
        credential_reference=checkpoint.pre_consume.credential_reference,
        quantity=Decimal("0"),
        observed_at=observed,
    )
    flat = _flat(ctx.prepared_account, observed_at=NOW + timedelta(seconds=8, milliseconds=50))
    receipt = CryptoPaperUnknownRecoveryCoordinator().recover(
        lifecycle=ctx.lifecycle,
        lifecycle_id=prepared.package.lifecycle_id,
        requested_order=prepared.broker_order,
        reconciliation=evidence,
        checkpoint=checkpoint,
        fresh_account=ctx.prepared_account,
        flat_account=flat,
        now=NOW + timedelta(seconds=8, milliseconds=100),
    )

    state = ctx.lifecycle.snapshot(prepared.package.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.FLAT_RECONCILED
    assert state.confirmed_net_long_quantity == 0
    assert state.protection_attempt_count == 1
    assert state.protection_client_order_id == prepared.broker_order.client_order_id
    assert receipt.retry_authorized is False
    assert receipt.flat_account_fingerprint == flat.fingerprint


def test_unknown_recovery_rejects_account_or_credential_rebinding(tmp_path) -> None:
    ctx, checkpoint = _entry_unknown(tmp_path)
    observed = NOW + timedelta(seconds=5)
    evidence = _unknown(
        ctx.broker_order,
        credential_reference=checkpoint.pre_consume.credential_reference,
        quantity=Decimal("0.0001"),
        observed_at=observed,
    )
    coordinator = CryptoPaperUnknownRecoveryCoordinator()

    other_account = replace(ctx.fresh_account, account_reference="c" * 64)
    with pytest.raises(CryptoUnknownRecoveryBlocked, match="account differs"):
        coordinator.recover(
            lifecycle=ctx.lifecycle,
            lifecycle_id=ctx.package.lifecycle_id,
            requested_order=ctx.broker_order,
            reconciliation=evidence,
            checkpoint=checkpoint,
            fresh_account=other_account,
            flat_account=None,
            now=NOW + timedelta(seconds=5, milliseconds=100),
        )

    wrong_evidence = _unknown(
        ctx.broker_order,
        credential_reference="d" * 64,
        quantity=Decimal("0.0001"),
        observed_at=observed,
    )
    with pytest.raises(CryptoUnknownRecoveryBlocked, match="evidence credential mismatch"):
        coordinator.recover(
            lifecycle=ctx.lifecycle,
            lifecycle_id=ctx.package.lifecycle_id,
            requested_order=ctx.broker_order,
            reconciliation=wrong_evidence,
            checkpoint=checkpoint,
            fresh_account=ctx.fresh_account,
            flat_account=None,
            now=NOW + timedelta(seconds=5, milliseconds=100),
        )
