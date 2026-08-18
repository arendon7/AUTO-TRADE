from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from autotrade.cold_start_oms import ColdStartOrderManagementSystem
import autotrade.brokers.alpaca_paper_crypto_cold_start_execution_bridge as bridge_module
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    CryptoColdStartExecutionAttemptConflict,
    CryptoColdStartExecutionAttemptIntegrityError,
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_bridge import (
    CryptoColdStartExecutionBridge,
    CryptoColdStartExecutionBridgeBlocked,
    CryptoColdStartOmsStageContext,
    crypto_cold_start_handoff_id,
)
from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
)
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_cold_start_execution_bridge import (
    _bridge,
    _checkpoint,
    _risk_and_market,
)
from test_r6_paper_crypto_cold_start_final_guard import _advance, _pre, _setup


def _stage_kwargs(ctx):
    checkpoint = _checkpoint(ctx)
    decision, market = _risk_and_market(ctx)
    return {
        "package": ctx.package,
        "operator_decision": ctx.operator_decision,
        "operator_registry": ctx.operator_registry,
        "checkpoint": checkpoint,
        "risk_decision": decision,
        "market": market,
        "consume_at": NOW + timedelta(seconds=4, milliseconds=200),
        "stage_at": NOW + timedelta(seconds=4, milliseconds=300),
    }


def _checkpoint_shape(ctx, checkpoint, **changes):
    values = {
        "package_hash": checkpoint.package_hash,
        "preparation_hash": checkpoint.preparation_hash,
        "operator_decision_hash": checkpoint.operator_decision_hash,
        "attempt_id": checkpoint.attempt_id,
        "order_id": checkpoint.order_id,
        "client_order_id": checkpoint.client_order_id,
        "record_hash": checkpoint.record_hash,
        "authority_state_fingerprint": checkpoint.authority_state_fingerprint,
        "pre_consume": SimpleNamespace(bootstrap_scope=bridge_module.COLD_START_SCOPE),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_stage_context_and_bridge_constructors_reject_wrong_capabilities(tmp_path) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    _, _, oms = _bridge(ctx)

    with pytest.raises(TypeError, match="ColdStartOrderManagementSystem"):
        CryptoColdStartExecutionBridge(oms=object(), authority_provider=ctx.authority)
    with pytest.raises(TypeError, match="authoritative core provider"):
        CryptoColdStartExecutionBridge(oms=oms, authority_provider=object())

    with pytest.raises(ValueError, match="prepared package"):
        CryptoColdStartOmsStageContext(
            package=object(),
            operator_decision=ctx.operator_decision,
            checkpoint=checkpoint,
            consumed_at=NOW,
        )
    with pytest.raises(ValueError, match="operator decision"):
        CryptoColdStartOmsStageContext(
            package=ctx.package,
            operator_decision=object(),
            checkpoint=checkpoint,
            consumed_at=NOW,
        )
    with pytest.raises(ValueError, match="durable checkpoint"):
        CryptoColdStartOmsStageContext(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            checkpoint=object(),
            consumed_at=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        CryptoColdStartOmsStageContext(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            checkpoint=checkpoint,
            consumed_at=NOW.replace(tzinfo=None),
        )


def test_stage_after_checkpoint_rejects_wrong_types_times_and_expiry(tmp_path) -> None:
    ctx = _setup(tmp_path)
    bridge, _, _ = _bridge(ctx)
    base = _stage_kwargs(ctx)

    cases = (
        ({"package": object()}, "prepared crypto PAPER package"),
        ({"operator_decision": object()}, "operator decision"),
        ({"operator_registry": object()}, "operator registry"),
        ({"checkpoint": object()}, "PRE_CONSUME checkpoint"),
        ({"risk_decision": object()}, "RiskDecision and MarketSnapshot"),
        ({"market": object()}, "RiskDecision and MarketSnapshot"),
    )
    for overrides, message in cases:
        values = dict(base)
        values.update(overrides)
        with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match=message):
            bridge.stage_after_checkpoint(**values)

    for field in ("consume_at", "stage_at"):
        values = dict(base)
        values[field] = NOW.replace(tzinfo=None)
        with pytest.raises(ValueError, match="timezone-aware"):
            bridge.stage_after_checkpoint(**values)

    values = dict(base)
    values["consume_at"] = NOW + timedelta(seconds=4, milliseconds=400)
    values["stage_at"] = NOW + timedelta(seconds=4, milliseconds=300)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="consumption cannot occur after staging"):
        bridge.stage_after_checkpoint(**values)

    values = dict(base)
    values["stage_at"] = ctx.package.execution_deadline
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="expired before cold-start staging"):
        bridge.stage_after_checkpoint(**values)


def test_stage_after_checkpoint_defends_package_invariants_even_if_object_is_tampered(tmp_path) -> None:
    ctx = _setup(tmp_path / "write")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)
    object.__setattr__(ctx.package, "network_write_authorized", True)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="must remain non-executable"):
        bridge.stage_after_checkpoint(**values)

    ctx = _setup(tmp_path / "action")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)
    object.__setattr__(ctx.package, "next_action", "EXECUTE")
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="must remain non-executable"):
        bridge.stage_after_checkpoint(**values)

    for name, notional in (("low", Decimal("0.5")), ("high", Decimal("6"))):
        ctx = _setup(tmp_path / name)
        bridge, _, _ = _bridge(ctx)
        values = _stage_kwargs(ctx)
        object.__setattr__(ctx.package, "notional", notional)
        with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="USD 1-5"):
            bridge.stage_after_checkpoint(**values)


def test_checkpoint_binding_and_handoff_derivation_fail_closed(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    validate = CryptoColdStartExecutionBridge._validate_checkpoint

    bad_cases = (
        ({"package_hash": "0" * 64}, "checkpoint package mismatch"),
        ({"preparation_hash": "0" * 64}, "checkpoint preparation mismatch"),
        ({"operator_decision_hash": "0" * 64}, "checkpoint decision hash mismatch"),
        ({"attempt_id": "different-attempt"}, "checkpoint attempt mismatch"),
        ({"order_id": "different-order"}, "checkpoint order identity mismatch"),
        ({"client_order_id": "different-client"}, "checkpoint order identity mismatch"),
        ({"pre_consume": SimpleNamespace(bootstrap_scope="WRONG")}, "outside cold-start scope"),
    )
    for changes, message in bad_cases:
        with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match=message):
            validate(
                package=ctx.package,
                operator_decision=ctx.operator_decision,
                checkpoint=_checkpoint_shape(ctx, checkpoint, **changes),
            )

    monkeypatch.setattr(bridge_module, "COLD_START_OMS_SCOPE", "DRIFTED_SCOPE")
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="scope drift"):
        validate(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            checkpoint=_checkpoint_shape(ctx, checkpoint),
        )

    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="mismatched package"):
        crypto_cold_start_handoff_id(
            package=SimpleNamespace(package_hash="0" * 64),
            operator_decision=ctx.operator_decision,
            checkpoint=checkpoint,
        )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="mismatched decision"):
        crypto_cold_start_handoff_id(
            package=ctx.package,
            operator_decision=SimpleNamespace(decision_hash="0" * 64),
            checkpoint=checkpoint,
        )


def test_decision_and_market_binding_checks_each_authority_field(tmp_path) -> None:
    ctx = _setup(tmp_path)
    decision, market = _risk_and_market(ctx)
    validate = bridge_module._validate_decision_package

    cases = (
        (replace(decision, decision_id="different-decision"), market, "RiskDecision id mismatch"),
        (replace(decision, valid_until=decision.valid_until + timedelta(microseconds=1)), market, "RiskDecision expiry mismatch"),
        (replace(decision, safety_state_version=decision.safety_state_version + 1), market, "Safety version mismatch"),
        (replace(decision, market_fingerprint="0" * 64), market, "RiskDecision market mismatch"),
        (replace(decision, intent_fingerprint="0" * 64), market, "RiskDecision intent mismatch"),
        (replace(decision, reason_detail="tampered-risk-material"), market, "RiskDecision fingerprint mismatch"),
        (decision, replace(market, last=market.last + Decimal("1")), "MarketSnapshot mismatch"),
    )
    for candidate_decision, candidate_market, message in cases:
        with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match=message):
            validate(
                package=ctx.package,
                risk_decision=candidate_decision,
                market=candidate_market,
            )


def test_authorize_oms_stage_rejects_context_order_intent_and_future_consumption(tmp_path) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    order = ctx.order_store.get_by_order_id(ctx.package.order_id)
    assert order is not None
    context = CryptoColdStartOmsStageContext(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        checkpoint=checkpoint,
        consumed_at=NOW + timedelta(seconds=4, milliseconds=200),
    )

    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="context is invalid"):
        bridge.authorize_oms_stage(
            order=order, decision=decision, market=market, now=NOW + timedelta(seconds=4, milliseconds=300), context=object()
        )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="OMS order differs"):
        bridge.authorize_oms_stage(
            order=replace(order, risk_decision_id="different-risk"),
            decision=decision,
            market=market,
            now=NOW + timedelta(seconds=4, milliseconds=300),
            context=context,
        )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="OMS intent differs"):
        bridge.authorize_oms_stage(
            order=replace(order, intent=replace(order.intent, quantity=order.intent.quantity + Decimal("0.0001"))),
            decision=decision,
            market=market,
            now=NOW + timedelta(seconds=4, milliseconds=300),
            context=context,
        )
    future_context = replace(context, consumed_at=NOW + timedelta(seconds=4, milliseconds=400))
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="future-dated"):
        bridge.authorize_oms_stage(
            order=order,
            decision=decision,
            market=market,
            now=NOW + timedelta(seconds=4, milliseconds=300),
            context=future_context,
        )


def test_authorize_oms_stage_rejects_changed_core_and_portfolio_binding(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path / "changed")
    checkpoint = _checkpoint(ctx)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    order = ctx.order_store.get_by_order_id(ctx.package.order_id)
    assert order is not None
    context = CryptoColdStartOmsStageContext(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        checkpoint=checkpoint,
        consumed_at=NOW + timedelta(seconds=4, milliseconds=200),
    )
    ctx.safety.activate_circuit(reason="AFTER_CHECKPOINT", now=NOW + timedelta(seconds=4, milliseconds=100))
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="core changed before OMS authorization"):
        bridge.authorize_oms_stage(
            order=order,
            decision=decision,
            market=market,
            now=NOW + timedelta(seconds=4, milliseconds=300),
            context=context,
        )

    ctx = _setup(tmp_path / "portfolio")
    checkpoint = _checkpoint(ctx)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    order = ctx.order_store.get_by_order_id(ctx.package.order_id)
    assert order is not None
    context = CryptoColdStartOmsStageContext(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        checkpoint=checkpoint,
        consumed_at=NOW + timedelta(seconds=4, milliseconds=200),
    )
    baseline = ctx.authority.snapshot()
    monkeypatch.setattr(
        type(ctx.authority),
        "snapshot",
        lambda self: replace(baseline, portfolio_snapshot_id="wrong-account-snapshot"),
    )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="Portfolio snapshot/account mismatch"):
        bridge.authorize_oms_stage(
            order=order,
            decision=decision,
            market=market,
            now=NOW + timedelta(seconds=4, milliseconds=300),
            context=context,
        )


def test_stage_rejects_missing_mismatched_consumed_and_nonresumable_operator_evidence(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path / "missing")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)

    def unavailable(self, preparation_hash):
        raise KeyError(preparation_hash)

    monkeypatch.setattr(SQLiteCryptoOperatorDecisionRegistry, "get", unavailable)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="durable operator decision unavailable"):
        bridge.stage_after_checkpoint(**values)
    monkeypatch.undo()

    ctx = _setup(tmp_path / "mismatch")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)
    monkeypatch.setattr(
        SQLiteCryptoOperatorDecisionRegistry,
        "get",
        lambda self, preparation_hash: SimpleNamespace(decision=object()),
    )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="differs from durable evidence"):
        bridge.stage_after_checkpoint(**values)
    monkeypatch.undo()

    ctx = _setup(tmp_path / "consumed")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)
    monkeypatch.setattr(
        SQLiteCryptoOperatorDecisionRegistry,
        "get",
        lambda self, preparation_hash: SimpleNamespace(
            decision=ctx.operator_decision,
            status=CryptoOperatorDecisionStatus.CONSUMED,
            consumed_attempt_id="other-attempt",
        ),
    )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="consumed by another attempt"):
        bridge.stage_after_checkpoint(**values)
    monkeypatch.undo()

    ctx = _setup(tmp_path / "nonresumable")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)
    monkeypatch.setattr(
        SQLiteCryptoOperatorDecisionRegistry,
        "get",
        lambda self, preparation_hash: SimpleNamespace(
            decision=ctx.operator_decision,
            status=object(),
            consumed_attempt_id=None,
        ),
    )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="state is not resumable"):
        bridge.stage_after_checkpoint(**values)


def test_stage_rejects_not_yet_valid_decision_consumption_failure_and_bad_consumed_result(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path / "early")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)
    values["consume_at"] = ctx.operator_decision.issued_at - timedelta(microseconds=2)
    values["stage_at"] = ctx.operator_decision.issued_at - timedelta(microseconds=1)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="expired before consumption"):
        bridge.stage_after_checkpoint(**values)

    ctx = _setup(tmp_path / "consume-failure")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)
    durable = ctx.operator_registry.get(ctx.operator_decision.context.preparation_hash)
    monkeypatch.setattr(
        SQLiteCryptoOperatorDecisionRegistry,
        "get",
        lambda self, preparation_hash: durable,
    )

    def fail_consume(self, **kwargs):
        raise RuntimeError("synthetic consume failure")

    monkeypatch.setattr(SQLiteCryptoOperatorDecisionRegistry, "consume", fail_consume)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="consumption failed"):
        bridge.stage_after_checkpoint(**values)
    monkeypatch.undo()

    ctx = _setup(tmp_path / "bad-result")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)
    durable = ctx.operator_registry.get(ctx.operator_decision.context.preparation_hash)
    monkeypatch.setattr(
        SQLiteCryptoOperatorDecisionRegistry,
        "get",
        lambda self, preparation_hash: durable,
    )
    monkeypatch.setattr(
        SQLiteCryptoOperatorDecisionRegistry,
        "consume",
        lambda self, **kwargs: SimpleNamespace(
            status=CryptoOperatorDecisionStatus.ISSUED,
            consumed_attempt_id=None,
            consumed_at=None,
        ),
    )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="was not durably consumed"):
        bridge.stage_after_checkpoint(**values)


def test_stage_wraps_oms_failure_and_detects_core_change_during_staging(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path / "oms")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)

    def fail_stage(self, **kwargs):
        raise RuntimeError("synthetic OMS failure")

    monkeypatch.setattr(ColdStartOrderManagementSystem, "stage_cold_start_external_submission", fail_stage)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="OMS-owned cold-start staging failed"):
        bridge.stage_after_checkpoint(**values)
    monkeypatch.undo()

    ctx = _setup(tmp_path / "authority")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)
    baseline = ctx.authority.snapshot()
    changed = replace(baseline, state_fingerprint="f" * 64)
    snapshots = iter((baseline, baseline, changed))
    monkeypatch.setattr(type(ctx.authority), "snapshot", lambda self: next(snapshots))
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="core changed during OMS staging"):
        bridge.stage_after_checkpoint(**values)


def test_stage_detects_tampered_handoff_identity_and_checkpoint(tmp_path, monkeypatch) -> None:
    real_stage = ColdStartOrderManagementSystem.stage_cold_start_external_submission

    ctx = _setup(tmp_path / "id")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)

    def tamper_id(self, **kwargs):
        order, handoff = real_stage(self, **kwargs)
        object.__setattr__(handoff, "authorization_id", "f" * 64)
        return order, handoff

    monkeypatch.setattr(ColdStartOrderManagementSystem, "stage_cold_start_external_submission", tamper_id)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="handoff id mismatch"):
        bridge.stage_after_checkpoint(**values)
    monkeypatch.undo()

    ctx = _setup(tmp_path / "checkpoint")
    bridge, _, _ = _bridge(ctx)
    values = _stage_kwargs(ctx)

    def tamper_checkpoint(self, **kwargs):
        order, handoff = real_stage(self, **kwargs)
        object.__setattr__(handoff, "checkpoint_hash", "f" * 64)
        return order, handoff

    monkeypatch.setattr(ColdStartOrderManagementSystem, "stage_cold_start_external_submission", tamper_checkpoint)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="handoff checkpoint mismatch"):
        bridge.stage_after_checkpoint(**values)


def test_execution_attempt_registry_rejects_wrong_inputs_preio_missing_and_corruption(tmp_path) -> None:
    with pytest.raises(TypeError, match="SQLiteRuntime"):
        SQLiteCryptoColdStartExecutionAttemptRegistry(object())

    ctx = _setup(tmp_path / "wrong")
    registry = SQLiteCryptoColdStartExecutionAttemptRegistry(ctx.core)
    with pytest.raises(TypeError, match="PRE_CONSUME attestation"):
        registry.record_pre_consume(object())
    with pytest.raises(KeyError):
        registry.get("missing-attempt")

    pre = _pre(ctx)
    final = _advance(ctx, pre)
    with pytest.raises(CryptoColdStartExecutionAttemptConflict, match="only cold-start PRE_CONSUME"):
        registry.record_pre_consume(final)

    ctx = _setup(tmp_path / "json")
    checkpoint = _checkpoint(ctx)
    registry = SQLiteCryptoColdStartExecutionAttemptRegistry(ctx.core)
    conn = ctx.core.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_cold_start_execution_attempts SET record_json = ? WHERE attempt_id = ?",
            ("[]", checkpoint.attempt_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoColdStartExecutionAttemptIntegrityError, match="invalid durable"):
        registry.get(checkpoint.attempt_id)

    ctx = _setup(tmp_path / "index")
    checkpoint = _checkpoint(ctx)
    registry = SQLiteCryptoColdStartExecutionAttemptRegistry(ctx.core)
    conn = ctx.core.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_cold_start_execution_attempts SET authority_state_fingerprint = ? WHERE attempt_id = ?",
            ("f" * 64, checkpoint.attempt_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoColdStartExecutionAttemptIntegrityError, match="invalid durable"):
        registry.get(checkpoint.attempt_id)
