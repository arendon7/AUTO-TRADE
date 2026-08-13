from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.brokers.alpaca_paper_crypto_execution_attempt import SQLiteCryptoExecutionAttemptRegistry
from autotrade.brokers.alpaca_paper_crypto_execution_bridge import (
    CryptoPaperExecutionBridge,
    CryptoPaperExecutionBridgeBlocked,
    CryptoPaperExecutionStageResult,
    crypto_execution_handoff_id,
)
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_final_guard import _authorize_pre, _setup


def _checkpoint(ctx, tmp_path, *, now=None):
    registry = SQLiteCryptoExecutionAttemptRegistry(SQLiteRuntime(tmp_path / "attempt.sqlite3"))
    pre = _authorize_pre(ctx, now=now or NOW + timedelta(seconds=4, milliseconds=200))
    return registry.record_pre_consume(pre)


def _stage(ctx, checkpoint, *, consume_at=None, stage_at=None):
    return CryptoPaperExecutionBridge(oms=ctx.oms).stage_after_checkpoint(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        checkpoint=checkpoint,
        risk_decision=ctx.decision,
        market=ctx.prepared_market.market,
        consume_at=consume_at or NOW + timedelta(seconds=4, milliseconds=300),
        stage_at=stage_at or NOW + timedelta(seconds=4, milliseconds=400),
    )


def test_crypto_execution_bridge_consumes_exact_checkpoint_before_oms_stage(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    checkpoint = _checkpoint(ctx, tmp_path)

    result = _stage(ctx, checkpoint)

    assert result.package_hash == ctx.package.package_hash
    assert result.operator_decision_hash == ctx.operator_decision.decision_hash
    assert result.attempt_id == checkpoint.attempt_id
    assert result.checkpoint_hash == checkpoint.record_hash
    assert result.order.status.value == "SUBMITTING"
    assert result.handoff.order_id == ctx.package.order_id
    assert result.handoff.handoff_id == crypto_execution_handoff_id(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        checkpoint=checkpoint,
    )
    durable = ctx.operator_registry.get(ctx.operator_decision.context.preparation_hash)
    assert durable.status.value == "CONSUMED"
    assert durable.consumed_attempt_id == checkpoint.attempt_id


def test_crypto_execution_bridge_exact_replay_is_same_attempt_idempotent(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    checkpoint = _checkpoint(ctx, tmp_path)

    first = _stage(ctx, checkpoint)
    second = _stage(ctx, checkpoint)

    assert second.handoff == first.handoff
    assert second.order == first.order
    durable = ctx.operator_registry.get(ctx.operator_decision.context.preparation_hash)
    assert durable.event_sequence == 2


def test_crypto_execution_bridge_requires_checkpoint_and_exact_types(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    bridge = CryptoPaperExecutionBridge(oms=ctx.oms)
    checkpoint = _checkpoint(ctx, tmp_path)
    base = dict(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        checkpoint=checkpoint,
        risk_decision=ctx.decision,
        market=ctx.prepared_market.market,
        consume_at=NOW + timedelta(seconds=4, milliseconds=300),
        stage_at=NOW + timedelta(seconds=4, milliseconds=400),
    )

    for key, bad in (
        ("package", object()),
        ("operator_decision", object()),
        ("operator_registry", object()),
        ("checkpoint", object()),
        ("risk_decision", object()),
        ("market", object()),
    ):
        kwargs = dict(base)
        kwargs[key] = bad
        with pytest.raises(CryptoPaperExecutionBridgeBlocked):
            bridge.stage_after_checkpoint(**kwargs)


def test_crypto_execution_bridge_rejects_time_travel_and_naive_times(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    checkpoint = _checkpoint(ctx, tmp_path)
    bridge = CryptoPaperExecutionBridge(oms=ctx.oms)

    with pytest.raises(CryptoPaperExecutionBridgeBlocked, match="cannot occur after"):
        bridge.stage_after_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            checkpoint=checkpoint,
            risk_decision=ctx.decision,
            market=ctx.prepared_market.market,
            consume_at=NOW + timedelta(seconds=5),
            stage_at=NOW + timedelta(seconds=4),
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        bridge.stage_after_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            checkpoint=checkpoint,
            risk_decision=ctx.decision,
            market=ctx.prepared_market.market,
            consume_at=NOW.replace(tzinfo=None),
            stage_at=NOW + timedelta(seconds=4),
        )


def test_crypto_execution_bridge_rejects_expired_stage(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    checkpoint = _checkpoint(ctx, tmp_path)
    with pytest.raises(CryptoPaperExecutionBridgeBlocked, match="execution deadline"):
        _stage(
            ctx,
            checkpoint,
            consume_at=ctx.package.execution_deadline - timedelta(milliseconds=1),
            stage_at=ctx.package.execution_deadline,
        )


def test_crypto_execution_bridge_rejects_risk_decision_drift_before_consumption(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    checkpoint = _checkpoint(ctx, tmp_path)
    drifted = replace(ctx.decision, decision_id="different-risk-decision")

    with pytest.raises(CryptoPaperExecutionBridgeBlocked, match="RiskDecision id"):
        CryptoPaperExecutionBridge(oms=ctx.oms).stage_after_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            checkpoint=checkpoint,
            risk_decision=drifted,
            market=ctx.prepared_market.market,
            consume_at=NOW + timedelta(seconds=4, milliseconds=300),
            stage_at=NOW + timedelta(seconds=4, milliseconds=400),
        )
    assert ctx.operator_registry.get(ctx.operator_decision.context.preparation_hash).status.value == "ISSUED"


def test_crypto_execution_bridge_rejects_checkpoint_rebinding(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    checkpoint = _checkpoint(ctx, tmp_path)
    forged = object()
    with pytest.raises(CryptoPaperExecutionBridgeBlocked, match="PRE_CONSUME checkpoint"):
        CryptoPaperExecutionBridge(oms=ctx.oms).stage_after_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            checkpoint=forged,  # type: ignore[arg-type]
            risk_decision=ctx.decision,
            market=ctx.prepared_market.market,
            consume_at=NOW + timedelta(seconds=4, milliseconds=300),
            stage_at=NOW + timedelta(seconds=4, milliseconds=400),
        )


def test_crypto_execution_handoff_rejects_mismatched_inputs(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    checkpoint = _checkpoint(ctx, tmp_path)
    with pytest.raises(CryptoPaperExecutionBridgeBlocked, match="operator decision"):
        crypto_execution_handoff_id(
            package=ctx.package,
            operator_decision=replace(ctx.operator_decision, decision_hash="f" * 64),
            checkpoint=checkpoint,
        )


def test_execution_stage_result_requires_submitting_and_matching_handoff(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    checkpoint = _checkpoint(ctx, tmp_path)
    good = _stage(ctx, checkpoint)
    assert isinstance(good, CryptoPaperExecutionStageResult)

    validated = replace(good.order, status=good.order.status.__class__.VALIDATED)
    with pytest.raises(ValueError, match="SUBMITTING"):
        CryptoPaperExecutionStageResult(
            package_hash=good.package_hash,
            operator_decision_hash=good.operator_decision_hash,
            attempt_id=good.attempt_id,
            checkpoint_hash=good.checkpoint_hash,
            order=validated,
            handoff=good.handoff,
        )
