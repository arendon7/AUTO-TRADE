from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from autotrade.brokers.alpaca_paper_execution_bridge import (
    PaperCanaryExecutionBridge,
    PaperExecutionBridgeBlocked,
)
from autotrade.brokers.alpaca_paper_operator_decision import (
    PaperOperatorDecisionContext,
    PaperOperatorDecisionStatus,
    SQLitePaperOperatorDecisionRegistry,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_canary_coordinator import NOW, decision, market, prepare, stack


def prepared_stack(tmp_path):
    coordinator, broker, safety, submission, permit = stack(tmp_path / "prepare")
    prepared = prepare(coordinator, submission, permit)
    oms = coordinator._oms  # test-only access; production bridge still receives authoritative OMS explicitly
    registry = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(tmp_path / "operator.sqlite"))
    context = PaperOperatorDecisionContext.from_prepared_package(prepared.package)
    issued = registry.record_operator_approval(
        context=context,
        operator_id="operator:arendon7",
        issued_at=NOW + timedelta(milliseconds=100),
        expires_at=NOW + timedelta(seconds=4),
    )
    bridge = PaperCanaryExecutionBridge(oms=oms)
    return prepared, bridge, registry, issued.decision, broker, safety


def stage(prepared, bridge, registry, operator_decision, *, now=None, risk_decision=None, current_market=None):
    return bridge.stage_after_operator_decision(
        package=prepared.package,
        operator_decision=operator_decision,
        operator_registry=registry,
        risk_decision=risk_decision or decision(),
        market=current_market or market(),
        now=now or NOW + timedelta(seconds=1),
    )


def test_human_decision_is_consumed_before_brokerless_oms_staging(tmp_path) -> None:
    prepared, bridge, registry, operator_decision, broker, _ = prepared_stack(tmp_path)
    result = stage(prepared, bridge, registry, operator_decision)

    assert result.order.status is OrderStatus.SUBMITTING
    assert result.handoff.handoff_id == prepared.package.canary_approval_hash
    assert result.handoff.order_id == prepared.package.order_id
    assert result.handoff.market_fingerprint == prepared.package.market_fingerprint
    assert result.handoff.safety_state_version == prepared.package.risk_decision_safety_state_version
    assert result.package_hash == prepared.package.package_hash
    assert result.operator_decision_hash == operator_decision.decision_hash
    durable = registry.get(operator_decision.context.preparation_hash)
    assert durable.status is PaperOperatorDecisionStatus.CONSUMED
    assert durable.consumed_attempt_id == prepared.package.attempt_id
    assert broker.calls == 0
    forbidden = {"submit", "submit_once", "post", "write", "send"}
    assert not (forbidden & set(dir(bridge)))


def test_consumed_same_attempt_can_resume_oms_stage_idempotently(tmp_path) -> None:
    prepared, bridge, registry, operator_decision, broker, _ = prepared_stack(tmp_path)
    registry.consume(
        decision=operator_decision,
        attempt_id=prepared.package.attempt_id,
        now=NOW + timedelta(milliseconds=500),
    )
    first = stage(prepared, bridge, registry, operator_decision)
    second = stage(prepared, bridge, registry, operator_decision)
    assert second == first
    assert broker.calls == 0


def test_expired_human_decision_blocks_before_oms_stage(tmp_path) -> None:
    coordinator, broker, _, submission, permit = stack(tmp_path / "prepare")
    prepared = prepare(coordinator, submission, permit)
    registry = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(tmp_path / "operator.sqlite"))
    context = PaperOperatorDecisionContext.from_prepared_package(prepared.package)
    issued = registry.record_operator_approval(
        context=context,
        operator_id="operator:arendon7",
        issued_at=NOW,
        expires_at=NOW + timedelta(milliseconds=500),
    )
    bridge = PaperCanaryExecutionBridge(oms=coordinator._oms)
    with pytest.raises(PaperExecutionBridgeBlocked, match="expired|not yet valid"):
        stage(
            prepared,
            bridge,
            registry,
            issued.decision,
            now=NOW + timedelta(seconds=1),
        )
    assert registry.get(context.preparation_hash).status is PaperOperatorDecisionStatus.ISSUED
    assert broker.calls == 0


def test_risk_decision_safety_version_is_bound_to_human_reviewed_package(tmp_path) -> None:
    prepared, bridge, registry, operator_decision, broker, _ = prepared_stack(tmp_path)
    forged = replace(decision(), safety_state_version=1)
    with pytest.raises(PaperExecutionBridgeBlocked, match="Safety version"):
        stage(prepared, bridge, registry, operator_decision, risk_decision=forged)
    assert registry.get(operator_decision.context.preparation_hash).status is PaperOperatorDecisionStatus.ISSUED
    assert broker.calls == 0


def test_risk_decision_market_fingerprint_is_bound_to_package(tmp_path) -> None:
    prepared, bridge, registry, operator_decision, broker, _ = prepared_stack(tmp_path)
    forged = replace(decision(), market_fingerprint="f" * 64)
    with pytest.raises(PaperExecutionBridgeBlocked, match="market fingerprint"):
        stage(prepared, bridge, registry, operator_decision, risk_decision=forged)
    assert broker.calls == 0


def test_safety_worsening_after_human_consume_blocks_oms_staging_zero_io(tmp_path) -> None:
    prepared, bridge, registry, operator_decision, broker, safety = prepared_stack(tmp_path)
    safety.activate(reason="bridge-race", now=NOW + timedelta(milliseconds=500))
    with pytest.raises(PaperExecutionBridgeBlocked, match="OMS external staging failed"):
        stage(prepared, bridge, registry, operator_decision)
    durable = registry.get(operator_decision.context.preparation_hash)
    assert durable.status is PaperOperatorDecisionStatus.CONSUMED
    assert durable.consumed_attempt_id == prepared.package.attempt_id
    assert broker.calls == 0


def test_expired_prepared_package_blocks_even_if_human_decision_ttl_is_longer(tmp_path) -> None:
    prepared, bridge, registry, operator_decision, broker, _ = prepared_stack(tmp_path)
    with pytest.raises(PaperExecutionBridgeBlocked, match="execution deadline"):
        stage(
            prepared,
            bridge,
            registry,
            operator_decision,
            now=prepared.package.execution_deadline,
        )
    assert broker.calls == 0


def test_decision_from_another_prepared_package_is_rejected(tmp_path) -> None:
    prepared, bridge, registry, _, broker, _ = prepared_stack(tmp_path / "first")
    coordinator2, _, _, submission2, permit2 = stack(tmp_path / "second")
    prepared2 = prepare(coordinator2, submission2, permit2)
    registry2 = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(tmp_path / "second-operator.sqlite"))
    context2 = PaperOperatorDecisionContext.from_prepared_package(prepared2.package)
    decision2 = registry2.record_operator_approval(
        context=context2,
        operator_id="operator:arendon7",
        issued_at=NOW + timedelta(milliseconds=100),
        expires_at=NOW + timedelta(seconds=4),
    ).decision

    with pytest.raises(PaperExecutionBridgeBlocked, match="exact prepared package"):
        stage(prepared, bridge, registry, decision2)
    assert broker.calls == 0
