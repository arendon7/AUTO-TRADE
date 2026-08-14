from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.brokers.alpaca_paper_crypto_execution_attempt import (
    SQLiteCryptoExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from autotrade.brokers.alpaca_paper_crypto_unknown_recovery import (
    CryptoPaperUnknownRecoveryCoordinator,
    CryptoUnknownRecoveryBlocked,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_final_guard import _authorize_pre, _setup
from test_r6_paper_crypto_unknown_recovery import (
    _entry_unknown,
    _flat,
    _protection_unknown,
    _unknown,
)


def _entry_long_case(tmp_path):
    ctx, checkpoint = _entry_unknown(tmp_path)
    observed = NOW + timedelta(seconds=5)
    evidence = _unknown(
        ctx.broker_order,
        credential_reference=checkpoint.pre_consume.credential_reference,
        quantity=Decimal("0.0004"),
        observed_at=observed,
    )
    return ctx, checkpoint, evidence, observed


def _entry_flat_case(tmp_path):
    ctx, checkpoint = _entry_unknown(tmp_path)
    observed = NOW + timedelta(seconds=5)
    evidence = _unknown(
        ctx.broker_order,
        credential_reference=checkpoint.pre_consume.credential_reference,
        quantity=Decimal("0"),
        observed_at=observed,
    )
    flat = _flat(ctx.fresh_account, observed_at=NOW + timedelta(seconds=5, milliseconds=50))
    return ctx, checkpoint, evidence, flat, observed


def _recover_entry(ctx, checkpoint, evidence, *, account=None, flat=None, now=None):
    return CryptoPaperUnknownRecoveryCoordinator().recover(
        lifecycle=ctx.lifecycle,
        lifecycle_id=ctx.package.lifecycle_id,
        requested_order=ctx.broker_order,
        reconciliation=evidence,
        checkpoint=checkpoint,
        fresh_account=ctx.fresh_account if account is None else account,
        flat_account=flat,
        now=NOW + timedelta(seconds=5, milliseconds=100) if now is None else now,
    )


def test_unknown_recovery_rejects_wrong_runtime_input_types(tmp_path) -> None:
    ctx, checkpoint, evidence, _ = _entry_long_case(tmp_path)
    coordinator = CryptoPaperUnknownRecoveryCoordinator()
    base = dict(
        lifecycle=ctx.lifecycle,
        lifecycle_id=ctx.package.lifecycle_id,
        requested_order=ctx.broker_order,
        reconciliation=evidence,
        checkpoint=checkpoint,
        fresh_account=ctx.fresh_account,
        flat_account=None,
        now=NOW + timedelta(seconds=5, milliseconds=100),
    )

    with pytest.raises(CryptoUnknownRecoveryBlocked, match="authoritative crypto lifecycle"):
        coordinator.recover(**{**base, "lifecycle": object()})  # type: ignore[arg-type]
    with pytest.raises(CryptoUnknownRecoveryBlocked, match="exact requested crypto order"):
        coordinator.recover(**{**base, "requested_order": object()})  # type: ignore[arg-type]
    with pytest.raises(CryptoUnknownRecoveryBlocked, match="exact order-404 UNKNOWN reconciliation"):
        coordinator.recover(**{**base, "reconciliation": object()})  # type: ignore[arg-type]
    with pytest.raises(CryptoUnknownRecoveryBlocked, match="fresh PAPER account attestation"):
        coordinator.recover(**{**base, "fresh_account": object()})  # type: ignore[arg-type]


def test_entry_recovery_rejects_wrong_checkpoint_type_and_non_unknown_state(tmp_path) -> None:
    ctx, checkpoint, evidence, _ = _entry_long_case(tmp_path / "wrong-type")
    with pytest.raises(CryptoUnknownRecoveryBlocked, match="ENTRY execution checkpoint"):
        CryptoPaperUnknownRecoveryCoordinator().recover(
            lifecycle=ctx.lifecycle,
            lifecycle_id=ctx.package.lifecycle_id,
            requested_order=ctx.broker_order,
            reconciliation=evidence,
            checkpoint=object(),  # type: ignore[arg-type]
            fresh_account=ctx.fresh_account,
            flat_account=None,
            now=NOW + timedelta(seconds=5, milliseconds=100),
        )

    prepared = _setup(tmp_path / "not-unknown")
    pre = _authorize_pre(prepared)
    registry = SQLiteCryptoExecutionAttemptRegistry(SQLiteRuntime(tmp_path / "not-unknown-checkpoint.sqlite3"))
    prepared_checkpoint = registry.record_pre_consume(pre)
    not_unknown_evidence = _unknown(
        prepared.broker_order,
        credential_reference=prepared_checkpoint.pre_consume.credential_reference,
        quantity=Decimal("0.0001"),
        observed_at=NOW + timedelta(seconds=5),
    )
    with pytest.raises(CryptoUnknownRecoveryBlocked, match="ENTRY_SUBMISSION_UNKNOWN attempt=1"):
        CryptoPaperUnknownRecoveryCoordinator().recover(
            lifecycle=prepared.lifecycle,
            lifecycle_id=prepared.package.lifecycle_id,
            requested_order=prepared.broker_order,
            reconciliation=not_unknown_evidence,
            checkpoint=prepared_checkpoint,
            fresh_account=prepared.fresh_account,
            flat_account=None,
            now=NOW + timedelta(seconds=5, milliseconds=100),
        )
    assert prepared.lifecycle.snapshot(prepared.package.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_PREPARED


def test_protection_recovery_rejects_entry_checkpoint_and_foreign_lifecycle_checkpoint(tmp_path) -> None:
    entry_ctx, entry_checkpoint = _entry_unknown(tmp_path / "entry")
    ctx, prepared, protection_checkpoint = _protection_unknown(tmp_path / "protection")
    observed = NOW + timedelta(seconds=8)
    evidence = _unknown(
        prepared.broker_order,
        credential_reference=protection_checkpoint.pre_consume.credential_reference,
        quantity=prepared.package.confirmed_net_long_quantity,
        observed_at=observed,
    )
    coordinator = CryptoPaperUnknownRecoveryCoordinator()

    with pytest.raises(CryptoUnknownRecoveryBlocked, match="exact protection checkpoint"):
        coordinator.recover(
            lifecycle=ctx.lifecycle,
            lifecycle_id=prepared.package.lifecycle_id,
            requested_order=prepared.broker_order,
            reconciliation=evidence,
            checkpoint=entry_checkpoint,
            fresh_account=ctx.prepared_account,
            flat_account=None,
            now=NOW + timedelta(seconds=8, milliseconds=100),
        )

    other_ctx, other_prepared, other_checkpoint = _protection_unknown(tmp_path / "other-protection")
    assert other_prepared.package.lifecycle_id != prepared.package.lifecycle_id
    with pytest.raises(CryptoUnknownRecoveryBlocked, match="checkpoint lifecycle mismatch"):
        coordinator.recover(
            lifecycle=ctx.lifecycle,
            lifecycle_id=prepared.package.lifecycle_id,
            requested_order=prepared.broker_order,
            reconciliation=evidence,
            checkpoint=other_checkpoint,
            fresh_account=ctx.prepared_account,
            flat_account=None,
            now=NOW + timedelta(seconds=8, milliseconds=100),
        )
    assert entry_ctx is not None and other_ctx is not None


def test_remaining_long_cannot_be_overridden_by_flat_account_evidence(tmp_path) -> None:
    ctx, checkpoint, evidence, _ = _entry_long_case(tmp_path)
    flat = _flat(ctx.fresh_account, observed_at=NOW + timedelta(seconds=5, milliseconds=50))
    with pytest.raises(CryptoUnknownRecoveryBlocked, match="must halt"):
        _recover_entry(ctx, checkpoint, evidence, flat=flat)
    state = ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert state.entry_attempt_count == 1


@pytest.mark.parametrize(
    ("account_mutation", "match"),
    [
        ({"credential_reference": "e" * 64}, "credential differs"),
        ({"status": "INACTIVE"}, "not ACTIVE USD"),
        ({"currency": "EUR"}, "not ACTIVE USD"),
        ({"source_host": "api.alpaca.markets"}, "provenance is invalid"),
        ({"source_path": "/v2/orders"}, "provenance is invalid"),
        ({"attested_at": NOW - timedelta(seconds=30)}, "is stale"),
        ({"attested_at": NOW + timedelta(seconds=20)}, "future-dated"),
    ],
)
def test_unknown_recovery_rejects_fresh_account_drift(tmp_path, account_mutation, match) -> None:
    ctx, checkpoint, evidence, _ = _entry_long_case(tmp_path)
    account = replace(ctx.fresh_account, **account_mutation)
    with pytest.raises(CryptoUnknownRecoveryBlocked, match=match):
        _recover_entry(ctx, checkpoint, evidence, account=account)


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda evidence: replace(evidence, order_absence=replace(evidence.order_absence, client_order_id="different-client-id")), "client_order_id mismatch"),
        (lambda evidence: replace(evidence, position=replace(evidence.position, symbol="ETH/USD")), "position symbol mismatch"),
        (
            lambda evidence: replace(
                evidence,
                order_absence=replace(evidence.order_absence, observed_at=evidence.observed_at - timedelta(seconds=1)),
            ),
            "timestamps are not one atomic observation",
        ),
        (
            lambda evidence: replace(
                evidence,
                order_absence=replace(evidence.order_absence, observed_at=evidence.observed_at - timedelta(seconds=30)),
                position=replace(evidence.position, observed_at=evidence.observed_at - timedelta(seconds=30)),
                observed_at=evidence.observed_at - timedelta(seconds=30),
            ),
            "UNKNOWN reconciliation is stale",
        ),
        (
            lambda evidence: replace(
                evidence,
                order_absence=replace(evidence.order_absence, observed_at=evidence.observed_at + timedelta(seconds=20)),
                position=replace(evidence.position, observed_at=evidence.observed_at + timedelta(seconds=20)),
                observed_at=evidence.observed_at + timedelta(seconds=20),
            ),
            "future-dated",
        ),
    ],
)
def test_unknown_recovery_rejects_reconciliation_identity_or_time_drift(tmp_path, mutator, match) -> None:
    ctx, checkpoint, evidence, _ = _entry_long_case(tmp_path)
    forged = mutator(evidence)
    with pytest.raises(CryptoUnknownRecoveryBlocked, match=match):
        _recover_entry(ctx, checkpoint, forged)


@pytest.mark.parametrize(
    ("flat_mutation", "match"),
    [
        ({"account_attestation_fingerprint": "d" * 64}, "not bound to fresh PAPER account"),
        ({"credential_reference": "e" * 64}, "credential differs"),
        ({"source_host": "api.alpaca.markets"}, "host is invalid"),
        ({"positions_path": "/v2/positions/BTC%2FUSD"}, "positions path is invalid"),
        ({"orders_path": "/v2/orders?status=all"}, "open-orders path/query is invalid"),
        ({"attested_at": NOW - timedelta(seconds=30)}, "is stale"),
        ({"attested_at": NOW + timedelta(seconds=20)}, "future-dated"),
    ],
)
def test_flat_recovery_rejects_wrong_account_provenance_or_freshness(tmp_path, flat_mutation, match) -> None:
    ctx, checkpoint, evidence, flat, _ = _entry_flat_case(tmp_path)
    forged = replace(flat, **flat_mutation)
    with pytest.raises(CryptoUnknownRecoveryBlocked, match=match):
        _recover_entry(ctx, checkpoint, evidence, flat=forged)
    assert ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN


def test_unknown_recovery_receipt_is_strictly_tamper_evident(tmp_path) -> None:
    ctx, checkpoint, evidence, flat, _ = _entry_flat_case(tmp_path)
    receipt = _recover_entry(ctx, checkpoint, evidence, flat=flat)

    with pytest.raises(ValueError, match="order_absence_fingerprint"):
        replace(receipt, order_absence_fingerprint="bad")
    with pytest.raises(ValueError, match="flat_account_fingerprint"):
        replace(receipt, flat_account_fingerprint="bad")
    with pytest.raises(ValueError, match="observed_position_quantity"):
        replace(receipt, observed_position_quantity=Decimal("-1"))
    with pytest.raises(ValueError, match="exactly one write attempt"):
        replace(receipt, attempt_count=2)
    with pytest.raises(ValueError, match="never authorizes retry"):
        replace(receipt, retry_authorized=True)
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(receipt, recovered_at=receipt.recovered_at.replace(tzinfo=None))
    with pytest.raises(ValueError, match="receipt hash mismatch"):
        replace(receipt, receipt_hash="0" * 64)
