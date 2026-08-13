from __future__ import annotations

from datetime import timedelta

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.brokers.alpaca_paper_crypto_execution_attempt import SQLiteCryptoExecutionAttemptRegistry
from autotrade.brokers.alpaca_paper_crypto_execution_simulation import (
    CryptoExecutionSimulationReconcileOnly,
    CryptoExecutionSimulationTimeline,
    CryptoPaperExecutionSimulationCoordinator,
    _simulation_handoff_id,
)
from autotrade.brokers.alpaca_paper_crypto_final_guard import CryptoFinalWritePhase
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_final_guard import _authorize_pre, _setup


def _timeline():
    return CryptoExecutionSimulationTimeline(
        pre_consume_at=NOW + timedelta(seconds=4, milliseconds=200),
        consume_at=NOW + timedelta(seconds=4, milliseconds=300),
        stage_at=NOW + timedelta(seconds=4, milliseconds=400),
        pre_io_at=NOW + timedelta(seconds=4, milliseconds=500),
    )


def _coordinator(ctx, attempts):
    return CryptoPaperExecutionSimulationCoordinator(
        oms=ctx.oms,
        final_guard=ctx.guard,
        attempt_registry=attempts,
    )


def _execute(ctx, attempts, *, timeline=None):
    return _coordinator(ctx, attempts).execute_entry(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        broker_order=ctx.broker_order,
        lifecycle=ctx.lifecycle,
        risk_decision=ctx.decision,
        prepared_market=ctx.prepared_market,
        prepared_account=ctx.prepared_account,
        prepared_asset=ctx.prepared_asset,
        prepared_product_profile=ctx.prepared_profile,
        fresh_account=ctx.fresh_account,
        fresh_asset=ctx.fresh_asset,
        fresh_product_profile=ctx.fresh_profile,
        fresh_market=ctx.fresh_market,
        fresh_flat_account=ctx.fresh_flat,
        timeline=timeline or _timeline(),
    )


def test_full_simulation_orders_authority_unknown_preio_then_one_inmemory_post(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    attempts = SQLiteCryptoExecutionAttemptRegistry(SQLiteRuntime(tmp_path / "attempt.sqlite3"))

    result = _execute(ctx, attempts)

    assert result.checkpoint.attempt_id == ctx.operator_decision.context.attempt_id
    assert result.pre_io_attestation.phase is CryptoFinalWritePhase.PRE_IO
    assert result.pre_io_attestation.previous_attestation_hash == result.checkpoint.pre_consume.attestation_hash
    assert result.pre_io_attestation.lifecycle_status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert result.pre_io_attestation.entry_attempt_count == 1
    assert result.simulated_transport_calls == 1
    assert result.write_receipt.client_order_id == ctx.broker_order.client_order_id
    assert result.write_receipt.broker_order_id == "simulation-broker-order-1"
    assert result.restart_action == "RECONCILE_ONLY"

    operator_state = ctx.operator_registry.get(ctx.operator_decision.context.preparation_hash)
    assert operator_state.status.value == "CONSUMED"
    assert operator_state.consumed_attempt_id == ctx.operator_decision.context.attempt_id
    assert ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    order = ctx.order_store.get_by_order_id(ctx.package.order_id)
    assert order is not None
    assert order.status.value == "SUBMITTING"


def test_restart_after_human_consumption_recovers_checkpoint_and_finishes_same_attempt(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    attempt_path = tmp_path / "attempt.sqlite3"
    attempts = SQLiteCryptoExecutionAttemptRegistry(SQLiteRuntime(attempt_path))
    timeline = _timeline()

    pre = _authorize_pre(ctx, now=timeline.pre_consume_at)
    checkpoint = attempts.record_pre_consume(pre)
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=ctx.operator_decision.context.attempt_id,
        now=timeline.consume_at,
    )

    # New objects emulate a process restart; no second human authority is issued.
    restarted_attempts = SQLiteCryptoExecutionAttemptRegistry(SQLiteRuntime(attempt_path))
    result = _execute(ctx, restarted_attempts, timeline=timeline)

    assert result.checkpoint == checkpoint
    assert result.pre_io_attestation.attempt_id == checkpoint.attempt_id
    assert result.simulated_transport_calls == 1
    assert ctx.operator_registry.get(ctx.operator_decision.context.preparation_hash).event_sequence == 2


def test_restart_after_unknown_is_reconciliation_only_and_never_reposts(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    attempts = SQLiteCryptoExecutionAttemptRegistry(SQLiteRuntime(tmp_path / "attempt.sqlite3"))
    timeline = _timeline()
    pre = _authorize_pre(ctx, now=timeline.pre_consume_at)
    attempts.record_pre_consume(pre)
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=ctx.operator_decision.context.attempt_id,
        now=timeline.consume_at,
    )
    handoff_id = _simulation_handoff_id(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
    )
    ctx.oms.stage_external_submission(
        order_id=ctx.package.order_id,
        handoff_id=handoff_id,
        decision=ctx.decision,
        market=ctx.prepared_market.market,
        now=timeline.stage_at,
    )
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=timeline.pre_io_at,
    )

    with pytest.raises(CryptoExecutionSimulationReconcileOnly, match="already UNKNOWN"):
        _execute(ctx, attempts, timeline=timeline)

    state = ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert state.entry_attempt_count == 1
    assert state.restart_action == "RECONCILE_ONLY"


def test_consumed_decision_without_checkpoint_fails_closed_before_writer(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    attempts = SQLiteCryptoExecutionAttemptRegistry(SQLiteRuntime(tmp_path / "attempt.sqlite3"))
    timeline = _timeline()
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=ctx.operator_decision.context.attempt_id,
        now=timeline.consume_at,
    )

    with pytest.raises(Exception, match="no durable PRE_CONSUME checkpoint"):
        _execute(ctx, attempts, timeline=timeline)
    assert ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_PREPARED
    order = ctx.order_store.get_by_order_id(ctx.package.order_id)
    assert order is not None
    assert order.status.value == "VALIDATED"


def test_timeline_rejects_non_monotonic_or_naive_authority_order() -> None:
    with pytest.raises(ValueError, match="timeline"):
        CryptoExecutionSimulationTimeline(
            pre_consume_at=NOW + timedelta(seconds=4),
            consume_at=NOW + timedelta(seconds=3),
            stage_at=NOW + timedelta(seconds=5),
            pre_io_at=NOW + timedelta(seconds=6),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        CryptoExecutionSimulationTimeline(
            pre_consume_at=NOW.replace(tzinfo=None),
            consume_at=NOW + timedelta(seconds=1),
            stage_at=NOW + timedelta(seconds=2),
            pre_io_at=NOW + timedelta(seconds=3),
        )
