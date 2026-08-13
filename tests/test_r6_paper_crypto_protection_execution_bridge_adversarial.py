from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from autotrade.domain import OrderStatus
from autotrade.brokers.alpaca_paper_crypto_protection_execution_bridge import (
    CryptoProtectionExecutionBridge,
    CryptoProtectionExecutionBridgeBlocked,
    CryptoProtectionExecutionStageResult,
    crypto_protection_execution_handoff_id,
)
from autotrade.brokers.alpaca_paper_crypto_protection_operator_decision import (
    CryptoProtectionOperatorDecisionStatus,
    SQLiteCryptoProtectionOperatorDecisionRegistry,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_protection_execution_bridge import _case


def _kwargs(case):
    ctx, market, decision, prepared, operator_registry, operator_decision, checkpoint = case
    return ctx, dict(
        package=prepared.package,
        operator_decision=operator_decision,
        operator_registry=operator_registry,
        checkpoint=checkpoint,
        risk_decision=decision,
        market=market.market,
        consume_at=NOW + timedelta(seconds=7, milliseconds=100),
        stage_at=NOW + timedelta(seconds=7, milliseconds=200),
    )


def test_bridge_constructor_rejects_non_oms() -> None:
    with pytest.raises(TypeError, match="OrderManagementSystem"):
        CryptoProtectionExecutionBridge(oms=object())  # type: ignore[arg-type]


def test_bridge_checks_stage_time_awareness_independently(tmp_path) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    kwargs["stage_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)


def test_bridge_rejects_consume_before_package_preparation(tmp_path) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    package = kwargs["package"]
    kwargs["consume_at"] = package.prepared_at - timedelta(milliseconds=1)
    kwargs["stage_at"] = package.prepared_at + timedelta(milliseconds=1)
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="execution deadline"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("valid_until", NOW + timedelta(seconds=19), "expiry"),
        ("safety_state_version", 1, "Safety version"),
        ("market_fingerprint", "e" * 64, "market fingerprint"),
        ("intent_fingerprint", "f" * 64, "intent fingerprint"),
        ("reason_detail", "well-formed but drifted decision", "RiskDecision fingerprint"),
        ("approved_notional", None, "RiskDecision fingerprint"),
    ],
)
def test_bridge_rejects_each_risk_decision_binding_drift(
    tmp_path,
    field,
    value,
    match,
) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    decision = kwargs["risk_decision"]
    kwargs["risk_decision"] = replace(decision, **{field: value})
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match=match):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)
    registry = kwargs["operator_registry"]
    operator_decision = kwargs["operator_decision"]
    assert registry.get(operator_decision.context.preparation_hash).status.value == "ISSUED"


def test_bridge_rejects_market_snapshot_drift_before_consumption(tmp_path) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    market = kwargs["market"]
    kwargs["market"] = replace(market, last=market.last + 1)
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="MarketSnapshot"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)


def test_bridge_rejects_expired_issued_human_authority(tmp_path) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    operator_decision = kwargs["operator_decision"]
    package = kwargs["package"]
    assert operator_decision.valid_until < package.execution_deadline
    kwargs["consume_at"] = operator_decision.valid_until
    kwargs["stage_at"] = operator_decision.valid_until + timedelta(milliseconds=1)
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="expired or not yet valid"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)


def test_bridge_requires_durable_operator_record(tmp_path) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    kwargs["operator_registry"] = SQLiteCryptoProtectionOperatorDecisionRegistry(
        SQLiteRuntime(tmp_path / "empty-operator.sqlite3")
    )
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="unavailable or invalid"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)


@pytest.mark.parametrize("consumed_at", [None, NOW + timedelta(seconds=7)])
def test_bridge_rejects_durable_consumed_state_bound_to_other_attempt(
    tmp_path,
    monkeypatch,
    consumed_at,
) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    registry = kwargs["operator_registry"]
    operator_decision = kwargs["operator_decision"]
    real = registry.get(operator_decision.context.preparation_hash)
    forged = SimpleNamespace(
        decision=operator_decision,
        status=CryptoProtectionOperatorDecisionStatus.CONSUMED,
        consumed_attempt_id="different-attempt",
        consumed_at=consumed_at,
    )
    monkeypatch.setattr(registry, "get", lambda _preparation_hash: forged)
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="consumed by another attempt"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)
    assert real.status is CryptoProtectionOperatorDecisionStatus.ISSUED


def test_bridge_wraps_operator_consumption_failure(tmp_path, monkeypatch) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    registry = kwargs["operator_registry"]

    def fail_consume(**_values):
        raise RuntimeError("simulated durable consume failure")

    monkeypatch.setattr(registry, "consume", fail_consume)
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="consumption failed"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)


def test_bridge_rejects_invalid_consumption_result(tmp_path, monkeypatch) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    registry = kwargs["operator_registry"]
    checkpoint = kwargs["checkpoint"]
    invalid = SimpleNamespace(
        status=CryptoProtectionOperatorDecisionStatus.ISSUED,
        consumed_attempt_id=checkpoint.attempt_id,
    )
    monkeypatch.setattr(registry, "consume", lambda **_values: invalid)
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="not durably consumed"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)


def test_bridge_wraps_oms_stage_failure_after_human_consumption(tmp_path, monkeypatch) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)

    def fail_stage(**_values):
        raise RuntimeError("simulated OMS persistence failure")

    monkeypatch.setattr(ctx.oms, "stage_external_submission", fail_stage)
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="external staging failed"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)
    registry = kwargs["operator_registry"]
    operator_decision = kwargs["operator_decision"]
    assert registry.get(operator_decision.context.preparation_hash).status.value == "CONSUMED"


def _forged_stage(case, kwargs, **overrides):
    ctx = case[0]
    prepared = case[3]
    operator_decision = case[5]
    checkpoint = case[6]
    current = ctx.order_store.get_by_order_id(prepared.package.order_id)
    assert current is not None
    staged = replace(current, status=OrderStatus.SUBMITTING, submitted_at=kwargs["stage_at"])
    handoff = dict(
        handoff_id=crypto_protection_execution_handoff_id(
            package=prepared.package,
            operator_decision=operator_decision,
            checkpoint=checkpoint,
        ),
        order_id=prepared.package.order_id,
        intent_fingerprint=prepared.package.intent_fingerprint,
        risk_decision_id=prepared.package.risk_decision_id,
        safety_state_version=prepared.package.risk_decision_safety_state_version,
        market_fingerprint=prepared.package.market_fingerprint,
        decision_valid_until=prepared.package.risk_decision_valid_until,
    )
    handoff.update(overrides)
    return staged, SimpleNamespace(**handoff)


@pytest.mark.parametrize(
    "field,wrong,match",
    [
        ("handoff_id", "0" * 64, "handoff id"),
        ("order_id", "wrong-order", "handoff order"),
        ("intent_fingerprint", "1" * 64, "handoff intent"),
        ("risk_decision_id", "wrong-risk", "handoff RiskDecision"),
        ("safety_state_version", 999, "handoff Safety version"),
        ("market_fingerprint", "2" * 64, "handoff market"),
        ("decision_valid_until", NOW, "handoff expiry"),
    ],
)
def test_bridge_rejects_each_oms_handoff_drift(
    tmp_path,
    monkeypatch,
    field,
    wrong,
    match,
) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    staged, handoff = _forged_stage(case, kwargs, **{field: wrong})
    monkeypatch.setattr(
        ctx.oms,
        "stage_external_submission",
        lambda **_values: (staged, handoff),
    )
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match=match):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)


def test_bridge_rejects_non_submitting_oms_result(tmp_path, monkeypatch) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    prepared = case[3]
    current = ctx.order_store.get_by_order_id(prepared.package.order_id)
    assert current is not None
    monkeypatch.setattr(
        ctx.oms,
        "stage_external_submission",
        lambda **_values: (current, SimpleNamespace(order_id=prepared.package.order_id)),
    )
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match="did not enter SUBMITTING"):
        CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)


@pytest.mark.parametrize("drift", ["package", "operator", "lifecycle"])
def test_handoff_id_derivation_rejects_rebinding(tmp_path, drift) -> None:
    case = _case(tmp_path)
    prepared = case[3]
    operator_decision = case[5]
    checkpoint = case[6]
    values = dict(
        package_hash=checkpoint.package_hash,
        operator_decision_hash=checkpoint.operator_decision_hash,
        lifecycle_id=checkpoint.lifecycle_id,
        order_id=checkpoint.order_id,
        record_hash=checkpoint.record_hash,
        attempt_id=checkpoint.attempt_id,
    )
    if drift == "package":
        values["package_hash"] = "0" * 64
        match = "checkpoint package"
    elif drift == "operator":
        values["operator_decision_hash"] = "1" * 64
        match = "operator decision"
    else:
        values["lifecycle_id"] = "different-lifecycle"
        match = "lifecycle/order"
    forged = SimpleNamespace(**values)
    with pytest.raises(CryptoProtectionExecutionBridgeBlocked, match=match):
        crypto_protection_execution_handoff_id(
            package=prepared.package,
            operator_decision=operator_decision,
            checkpoint=forged,  # type: ignore[arg-type]
        )


def test_stage_result_rejects_mismatched_handoff_without_requiring_real_handoff_type(tmp_path) -> None:
    case = _case(tmp_path)
    ctx, kwargs = _kwargs(case)
    good = CryptoProtectionExecutionBridge(oms=ctx.oms).stage_after_checkpoint(**kwargs)
    forged = SimpleNamespace(order_id="different-order")
    with pytest.raises(ValueError, match="order/handoff mismatch"):
        CryptoProtectionExecutionStageResult(
            package_hash=good.package_hash,
            operator_decision_hash=good.operator_decision_hash,
            attempt_id=good.attempt_id,
            checkpoint_hash=good.checkpoint_hash,
            order=good.order,
            handoff=forged,  # type: ignore[arg-type]
        )
