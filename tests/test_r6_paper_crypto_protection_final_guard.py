from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from autotrade.persistence import SQLiteRuntime
import autotrade.brokers.alpaca_paper_crypto_protection_final_guard as protection_final_guard_module
from autotrade.brokers.alpaca_paper_crypto_protection_coordinator import CryptoPaperProtectionCoordinator
from autotrade.brokers.alpaca_paper_crypto_protection_final_guard import (
    CryptoPaperProtectionFinalGuard,
    CryptoProtectionFinalGuardBlocked,
    CryptoProtectionFinalWritePhase,
)
from autotrade.brokers.alpaca_paper_crypto_protection_operator_decision import (
    CryptoProtectionOperatorDecisionContext,
    SQLiteCryptoProtectionOperatorDecisionRegistry,
)
from test_r6_paper_crypto_canary_coordinator import NOW, _market
from test_r6_paper_crypto_final_guard import _setup
from test_r6_paper_crypto_protection_coordinator import (
    PROTECTION_LIMIT,
    PROTECTION_STOP,
    _advance_entry_to_unprotected,
    _entry_reconciliation,
    _protection_decision,
    _protection_intent,
)


def _protection_setup(tmp_path):
    ctx = _setup(tmp_path / "entry")
    entry_reconciliation = _entry_reconciliation(ctx)
    _advance_entry_to_unprotected(ctx, entry_reconciliation)
    market = _market(observed=NOW + timedelta(seconds=5))
    intent = _protection_intent(ctx, quantity=entry_reconciliation.position.quantity)
    decision = _protection_decision(intent, market)
    prepared = CryptoPaperProtectionCoordinator(oms=ctx.oms).prepare_protection(
        lifecycle=ctx.lifecycle,
        lifecycle_id=ctx.package.lifecycle_id,
        entry_order=ctx.broker_order,
        entry_reconciliation=entry_reconciliation,
        intent=intent,
        decision=decision,
        market_attestation=market,
        account_attestation=ctx.prepared_account,
        asset_attestation=ctx.prepared_asset,
        product_profile=ctx.prepared_profile,
        stop_price=PROTECTION_STOP,
        limit_price=PROTECTION_LIMIT,
        now=NOW + timedelta(seconds=6),
    )
    registry = SQLiteCryptoProtectionOperatorDecisionRegistry(
        SQLiteRuntime(tmp_path / "protection-operator.sqlite3")
    )
    context = CryptoProtectionOperatorDecisionContext.from_prepared_package(
        prepared.package,
        attempt_id="r6c-protect-attempt-001",
    )
    operator_decision = registry.record_operator_approval(
        context=context,
        operator_id="human-operator-001",
        note="Approve exact protective exit only.",
        now=NOW + timedelta(seconds=6, milliseconds=500),
        ttl=timedelta(seconds=10),
    )
    guard = CryptoPaperProtectionFinalGuard(order_store=ctx.order_store)
    return ctx, entry_reconciliation, market, decision, prepared, registry, operator_decision, guard


def _position(entry_reconciliation, *, observed_at, request_id="position-fresh-001", response_hash="3" * 64):
    return replace(
        entry_reconciliation.position,
        request_id=request_id,
        response_sha256=response_hash,
        observed_at=observed_at,
    )


def _preconsume(tmp_path):
    setup = _protection_setup(tmp_path)
    ctx, entry_reconciliation, _, _, prepared, registry, operator_decision, guard = setup
    pre = guard.authorize(
        package=prepared.package,
        operator_decision=operator_decision,
        operator_registry=registry,
        broker_order=prepared.broker_order,
        lifecycle=ctx.lifecycle,
        fresh_account=ctx.prepared_account,
        fresh_position=_position(entry_reconciliation, observed_at=NOW + timedelta(seconds=6, milliseconds=900)),
        now=NOW + timedelta(seconds=7),
        phase=CryptoProtectionFinalWritePhase.PRE_CONSUME,
    )
    return (*setup, pre)


def _advance_to_preio(setup, pre):
    ctx, entry_reconciliation, market, decision, prepared, registry, operator_decision, guard = setup
    registry.consume(
        decision=operator_decision,
        attempt_id=operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=7, milliseconds=100),
    )
    ctx.oms.stage_external_submission(
        order_id=prepared.package.order_id,
        handoff_id="1" * 64,
        decision=decision,
        market=market.market,
        now=NOW + timedelta(seconds=7, milliseconds=200),
    )
    ctx.lifecycle.mark_protection_submission_unknown(
        prepared.package.lifecycle_id,
        at=NOW + timedelta(seconds=7, milliseconds=300),
    )
    return guard.authorize(
        package=prepared.package,
        operator_decision=operator_decision,
        operator_registry=registry,
        broker_order=prepared.broker_order,
        lifecycle=ctx.lifecycle,
        fresh_account=ctx.prepared_account,
        fresh_position=_position(
            entry_reconciliation,
            observed_at=NOW + timedelta(seconds=7, milliseconds=350),
            request_id="position-fresh-002",
            response_hash="4" * 64,
        ),
        now=NOW + timedelta(seconds=7, milliseconds=400),
        phase=CryptoProtectionFinalWritePhase.PRE_IO,
        expected_attempt_id=operator_decision.context.attempt_id,
        previous_attestation=pre,
    )


def test_protection_preconsume_requires_issued_human_authority_validated_oms_and_exact_position(tmp_path) -> None:
    ctx, entry_reconciliation, _, _, prepared, _, operator_decision, _, pre = _preconsume(tmp_path)

    assert pre.phase is CryptoProtectionFinalWritePhase.PRE_CONSUME
    assert pre.package_hash == prepared.package.package_hash
    assert pre.operator_decision_hash == operator_decision.decision_hash
    assert pre.account_reference == prepared.package.account_reference
    assert pre.credential_reference == prepared.package.credential_reference
    assert pre.fresh_account_fingerprint == ctx.prepared_account.fingerprint
    assert pre.position_credential_reference == ctx.prepared_account.credential_reference
    assert pre.lifecycle_status.value == "PROTECTION_PREPARED"
    assert pre.protection_attempt_count == 0
    assert pre.oms_order_status.value == "VALIDATED"
    assert pre.position_quantity == entry_reconciliation.position.quantity
    assert pre.previous_attestation_hash is None
    assert ctx.lifecycle.snapshot(prepared.package.lifecycle_id).state.protection_attempt_count == 0


def test_protection_preio_requires_consumed_same_attempt_unknown_submitting_and_exact_position(tmp_path) -> None:
    setup_with_pre = _preconsume(tmp_path)
    setup, pre = setup_with_pre[:-1], setup_with_pre[-1]
    final = _advance_to_preio(setup, pre)

    assert final.phase is CryptoProtectionFinalWritePhase.PRE_IO
    assert final.previous_attestation_hash == pre.attestation_hash
    assert final.attempt_id == pre.attempt_id
    assert final.account_reference == pre.account_reference
    assert final.credential_reference == pre.credential_reference
    assert final.position_credential_reference == pre.position_credential_reference
    assert final.lifecycle_status.value == "PROTECTION_SUBMISSION_UNKNOWN"
    assert final.protection_attempt_count == 1
    assert final.oms_order_status.value == "SUBMITTING"
    assert final.position_quantity == pre.position_quantity


def test_protection_preconsume_rejects_other_account_credential_or_position_credential(tmp_path) -> None:
    ctx, entry_reconciliation, _, _, prepared, registry, operator_decision, guard = _protection_setup(tmp_path)
    position = _position(entry_reconciliation, observed_at=NOW + timedelta(seconds=6, milliseconds=900))
    base = dict(
        package=prepared.package,
        operator_decision=operator_decision,
        operator_registry=registry,
        broker_order=prepared.broker_order,
        lifecycle=ctx.lifecycle,
        fresh_position=position,
        now=NOW + timedelta(seconds=7),
        phase=CryptoProtectionFinalWritePhase.PRE_CONSUME,
    )

    other_account = replace(ctx.prepared_account, account_reference="f" * 64)
    with pytest.raises(CryptoProtectionFinalGuardBlocked, match="account reference differs"):
        guard.authorize(**base, fresh_account=other_account)

    other_credential = replace(ctx.prepared_account, credential_reference="e" * 64)
    with pytest.raises(CryptoProtectionFinalGuardBlocked, match="credential reference differs"):
        guard.authorize(**base, fresh_account=other_credential)

    other_position = replace(position, credential_reference="d" * 64)
    with pytest.raises(CryptoProtectionFinalGuardBlocked, match="position credential differs"):
        guard.authorize(
            **{**base, "fresh_position": other_position},
            fresh_account=ctx.prepared_account,
        )


def test_protection_preconsume_rejects_position_drift_flat_or_stale(tmp_path) -> None:
    ctx, entry_reconciliation, _, _, prepared, registry, operator_decision, guard = _protection_setup(tmp_path)
    base = dict(
        package=prepared.package,
        operator_decision=operator_decision,
        operator_registry=registry,
        broker_order=prepared.broker_order,
        lifecycle=ctx.lifecycle,
        fresh_account=ctx.prepared_account,
        now=NOW + timedelta(seconds=7),
        phase=CryptoProtectionFinalWritePhase.PRE_CONSUME,
    )

    drifted = replace(
        entry_reconciliation.position,
        quantity=entry_reconciliation.position.quantity / 2,
        request_id="position-drift",
        response_sha256="5" * 64,
        observed_at=NOW + timedelta(seconds=6, milliseconds=900),
    )
    with pytest.raises(CryptoProtectionFinalGuardBlocked, match="exact confirmed net long"):
        guard.authorize(**base, fresh_position=drifted)

    flat = replace(
        entry_reconciliation.position,
        quantity=entry_reconciliation.position.quantity * 0,
        absent=True,
        request_id="position-flat",
        response_sha256="6" * 64,
        observed_at=NOW + timedelta(seconds=6, milliseconds=900),
    )
    with pytest.raises(CryptoProtectionFinalGuardBlocked, match="absent or flat"):
        guard.authorize(**base, fresh_position=flat)

    stale = _position(
        entry_reconciliation,
        observed_at=NOW - timedelta(seconds=10),
        request_id="position-stale",
        response_hash="7" * 64,
    )
    with pytest.raises(CryptoProtectionFinalGuardBlocked, match="stale"):
        guard.authorize(**base, fresh_position=stale)


def test_protection_preio_fails_closed_without_previous_or_without_unknown(tmp_path) -> None:
    ctx, entry_reconciliation, _, _, prepared, registry, operator_decision, guard, pre = _preconsume(tmp_path)
    registry.consume(
        decision=operator_decision,
        attempt_id=operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=7, milliseconds=100),
    )
    position = _position(
        entry_reconciliation,
        observed_at=NOW + timedelta(seconds=7, milliseconds=150),
    )
    base = dict(
        package=prepared.package,
        operator_decision=operator_decision,
        operator_registry=registry,
        broker_order=prepared.broker_order,
        lifecycle=ctx.lifecycle,
        fresh_account=ctx.prepared_account,
        fresh_position=position,
        now=NOW + timedelta(seconds=7, milliseconds=200),
        phase=CryptoProtectionFinalWritePhase.PRE_IO,
        expected_attempt_id=operator_decision.context.attempt_id,
    )
    with pytest.raises(CryptoProtectionFinalGuardBlocked, match="requires PRE_CONSUME"):
        guard.authorize(**base, previous_attestation=None)
    with pytest.raises(CryptoProtectionFinalGuardBlocked, match="PROTECTION_SUBMISSION_UNKNOWN"):
        guard.authorize(**base, previous_attestation=pre)


def test_protection_preio_rejects_previous_attestation_rebinding(tmp_path) -> None:
    setup_with_pre = _preconsume(tmp_path)
    setup, pre = setup_with_pre[:-1], setup_with_pre[-1]
    ctx, entry_reconciliation, market, decision, prepared, registry, operator_decision, guard = setup
    registry.consume(
        decision=operator_decision,
        attempt_id=operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=7, milliseconds=100),
    )
    ctx.oms.stage_external_submission(
        order_id=prepared.package.order_id,
        handoff_id="2" * 64,
        decision=decision,
        market=market.market,
        now=NOW + timedelta(seconds=7, milliseconds=200),
    )
    ctx.lifecycle.mark_protection_submission_unknown(
        prepared.package.lifecycle_id,
        at=NOW + timedelta(seconds=7, milliseconds=300),
    )
    forged_payload = protection_final_guard_module._attestation_payload(pre, include_hash=False)
    forged_payload["attempt_id"] = "different-attempt"
    forged = replace(
        pre,
        attempt_id="different-attempt",
        attestation_hash=protection_final_guard_module._hash_json(forged_payload),
    )
    with pytest.raises(CryptoProtectionFinalGuardBlocked, match="previous attempt mismatch"):
        guard.authorize(
            package=prepared.package,
            operator_decision=operator_decision,
            operator_registry=registry,
            broker_order=prepared.broker_order,
            lifecycle=ctx.lifecycle,
            fresh_account=ctx.prepared_account,
            fresh_position=_position(entry_reconciliation, observed_at=NOW + timedelta(seconds=7, milliseconds=350)),
            now=NOW + timedelta(seconds=7, milliseconds=400),
            phase=CryptoProtectionFinalWritePhase.PRE_IO,
            expected_attempt_id=operator_decision.context.attempt_id,
            previous_attestation=forged,
        )
