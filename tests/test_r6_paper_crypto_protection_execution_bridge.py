from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from autotrade.brokers.alpaca_paper_crypto_protection_execution_attempt import (
    SQLiteCryptoProtectionExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_protection_execution_bridge import (
    CryptoProtectionExecutionBridge,
    CryptoProtectionExecutionBridgeBlocked,
    CryptoProtectionExecutionStageResult,
    crypto_protection_execution_handoff_id,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_protection_final_guard import _preconsume


def _case(tmp_path):
    setup_with_pre = _preconsume(tmp_path / "protection")
    setup, pre = setup_with_pre[:-1], setup_with_pre[-1]
    ctx, _entry_reconciliation, market, decision, prepared, operator_registry, operator_decision, _guard = setup
    attempts = SQLiteCryptoProtectionExecutionAttemptRegistry(
        SQLiteRuntime(tmp_path / "protection-attempt.sqlite3")
    )
    checkpoint = attempts.record_pre_consume(pre)
    return ctx, market, decision, prepared, operator_registry, operator_decision, checkpoint


def _stage(case, *, consume_at=None, stage_at=None):
    ctx, market, decision, prepared, operator_registry, operator_decision, checkpoint = case
    return CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(
        package=prepared.package,
        operator_decision=operator_decision,
        operator_registry=operator_registry,
        checkpoint=checkpoint,
        risk_decision=decision,
        market=market.market,
        consume_at=consume_at or NOW + timedelta(seconds=7, milliseconds=100),
        stage_at=stage_at or NOW + timedelta(seconds=7, milliseconds=200),
    )


def test_protection_execution_bridge_consumes_exact_checkpoint_before_oms_stage(tmp_path) -> None:
    case = _case(tmp_path)
    ctx, _market, _decision, prepared, operator_registry, operator_decision, checkpoint = case

    result = _stage(case)

    assert result.package_hash == prepared.package.package_hash
    assert result.operator_decision_hash == operator_decision.decision_hash
    assert result.attempt_id == checkpoint.attempt_id
    assert result.checkpoint_hash == checkpoint.record_hash
    assert result.order.status.value == "SUBMITTING"
    assert result.handoff.order_id == prepared.package.order_id
    assert result.handoff.handoff_id == crypto_protection_execution_handoff_id(
        package=prepared.package,
        operator_decision=operator_decision,
        checkpoint=checkpoint,
    )
    durable = operator_registry.get(operator_decision.context.preparation_hash)
    assert durable.status.value == "CONSUMED"
    assert durable.consumed_attempt_id == checkpoint.attempt_id

    lifecycle_state = ctx.lifecycle.snapshot(prepared.package.lifecycle_id).state
    assert lifecycle_state.status.value == "PROTECTION_PREPARED"
    assert lifecycle_state.protection_attempt_count == 0


def test_protection_execution_bridge_exact_replay_is_same_attempt_idempotent(tmp_path) -> None:
    case = _case(tmp_path)
    _ctx, _market, _decision, _prepared, operator_registry, operator_decision, _checkpoint = case

    first = _stage(case)
    second = _stage(case)

    assert second.handoff == first.handoff
    assert second.order == first.order
    durable = operator_registry.get(operator_decision.context.preparation_hash)
    assert durable.event_sequence == 2


def test_protection_execution_bridge_resumes_after_consumed_decision_before_oms_stage(tmp_path) -> None:
    case = _case(tmp_path)
    _ctx, _market, _decision, _prepared, operator_registry, operator_decision, checkpoint = case
    operator_registry.consume(
        decision=operator_decision,
        attempt_id=checkpoint.attempt_id,
        now=NOW + timedelta(seconds=7, milliseconds=50),
    )

    result = _stage(case)

    assert result.order.status.value == "SUBMITTING"
    durable = operator_registry.get(operator_decision.context.preparation_hash)
    assert durable.status.value == "CONSUMED"
    assert durable.event_sequence == 2


def test_protection_execution_bridge_requires_exact_types(tmp_path) -> None:
    case = _case(tmp_path)
    ctx, market, decision, prepared, operator_registry, operator_decision, checkpoint = case
    bridge = CryptoProtectionExecutionBridge(oms=ctx.oms)
    base = dict(
        package=prepared.package,
        operator_decision=operator_decision,
        operator_registry=operator_registry,
        checkpoint=checkpoint,
        risk_decision=decision,
        market=market.market,
        consume_at=NOW + timedelta(seconds=7, milliseconds=100),
        stage_at=NOW + timedelta(seconds=7, milliseconds=200),
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
        with pytest.raises(CryptoProtectionExecutionBridgeBlocked):
            bridge.stage_after_checkpoint(**kwargs)


def test_protection_execution_bridge_rejects_time_travel_naive_and_expired_stage(tmp_path) -> None:
    case = _case(tmp_path)
    ctx, market, decision, prepared, operator_registry, operator_decision, checkpoint = case
    bridge = CryptoProtectionExecutionBridge(oms=ctx.oms)

    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="cannot occur after"):
        bridge.stage_after_checkpoint(
            package=prepared.package,
            operator_decision=operator_decision,
            operator_registry=operator_registry,
            checkpoint=checkpoint,
            risk_decision=decision,
            market=market.market,
            consume_at=NOW + timedelta(seconds=8),
            stage_at=NOW + timedelta(seconds=7),
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        bridge.stage_after_checkpoint(
            package=prepared.package,
            operator_decision=operator_decision,
            operator_registry=operator_registry,
            checkpoint=checkpoint,
            risk_decision=decision,
            market=market.market,
            consume_at=NOW.replace(tzinfo=None),
            stage_at=NOW + timedelta(seconds=7),
        )

    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="execution deadline"):
        _stage(
            case,
            consume_at=prepared.package.execution_deadline - timedelta(milliseconds=1),
            stage_at=prepared.package.execution_deadline,
        )


def test_protection_execution_bridge_rejects_risk_decision_drift_before_consumption(tmp_path) -> None:
    case = _case(tmp_path)
    ctx, market, decision, prepared, operator_registry, operator_decision, checkpoint = case
    drifted = replace(decision, decision_id="different-protection-risk-decision")

    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="RiskDecision id"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(
            package=prepared.package,
            operator_decision=operator_decision,
            operator_registry=operator_registry,
            checkpoint=checkpoint,
            risk_decision=drifted,
            market=market.market,
            consume_at=NOW + timedelta(seconds=7, milliseconds=100),
            stage_at=NOW + timedelta(seconds=7, milliseconds=200),
        )
    assert operator_registry.get(operator_decision.context.preparation_hash).status.value == "ISSUED"


def test_protection_execution_bridge_rejects_valid_checkpoint_from_other_package(tmp_path) -> None:
    first = _case(tmp_path / "first")
    second = _case(tmp_path / "second")
    ctx, market, decision, prepared, operator_registry, operator_decision, _checkpoint = first
    foreign_checkpoint = second[-1]

    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="checkpoint package"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(
            package=prepared.package,
            operator_decision=operator_decision,
            operator_registry=operator_registry,
            checkpoint=foreign_checkpoint,
            risk_decision=decision,
            market=market.market,
            consume_at=NOW + timedelta(seconds=7, milliseconds=100),
            stage_at=NOW + timedelta(seconds=7, milliseconds=200),
        )
    assert operator_registry.get(operator_decision.context.preparation_hash).status.value == "ISSUED"


def test_protection_execution_handoff_is_deterministic_and_checkpoint_bound(tmp_path) -> None:
    case = _case(tmp_path)
    _ctx, _market, _decision, prepared, _operator_registry, operator_decision, checkpoint = case

    first = crypto_protection_execution_handoff_id(
        package=prepared.package,
        operator_decision=operator_decision,
        checkpoint=checkpoint,
    )
    second = crypto_protection_execution_handoff_id(
        package=prepared.package,
        operator_decision=operator_decision,
        checkpoint=checkpoint,
    )

    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)


def test_protection_execution_stage_result_requires_submitting_and_matching_handoff(tmp_path) -> None:
    case = _case(tmp_path)
    good = _stage(case)
    assert isinstance(good, CryptoProtectionExecutionStageResult)

    validated = replace(good.order, status=good.order.status.__class__.VALIDATED)
    with pytest.raises(ValueError, match="SUBMITTING"):
        CryptoProtectionExecutionStageResult(
            package_hash=good.package_hash,
            operator_decision_hash=good.operator_decision_hash,
            attempt_id=good.attempt_id,
            checkpoint_hash=good.checkpoint_hash,
            order=validated,
            handoff=good.handoff,
        )
