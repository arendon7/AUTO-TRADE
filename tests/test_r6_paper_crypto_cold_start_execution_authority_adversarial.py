from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

import autotrade.brokers.alpaca_paper_crypto_cold_start_execution_bridge as bridge_mod
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_bridge import (
    CryptoColdStartExecutionBridge,
    CryptoColdStartExecutionBridgeBlocked,
    CryptoColdStartOmsStageContext,
    _validate_decision_package,
    crypto_cold_start_handoff_id,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io import (
    ColdStartFinalGuardedCryptoEntryTransport,
    CryptoColdStartPreIoExecutionContext,
    CryptoColdStartPreIoInterlockError,
    _ephemeral_credentials,
    _request_payload,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io_authority import (
    CryptoColdStartPreIoAuthority,
    CryptoColdStartPreIoAuthorityBlocked,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.persistence import SQLiteRuntime

from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_cold_start_execution_bridge import (
    _bridge,
    _checkpoint,
    _risk_and_market,
)
from test_r6_paper_crypto_cold_start_final_guard import _setup
from test_r6_paper_crypto_cold_start_pre_io_transport import _setup as _preio_setup


def _stage_kwargs(ctx, checkpoint, decision, market):
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


def _checkpoint_proxy(checkpoint, **changes):
    values = {
        "package_hash": checkpoint.package_hash,
        "preparation_hash": checkpoint.preparation_hash,
        "operator_decision_hash": checkpoint.operator_decision_hash,
        "attempt_id": checkpoint.attempt_id,
        "order_id": checkpoint.order_id,
        "client_order_id": checkpoint.client_order_id,
        "record_hash": checkpoint.record_hash,
        "authority_state_fingerprint": checkpoint.authority_state_fingerprint,
        "pre_consume": checkpoint.pre_consume,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_bridge_constructor_rejects_non_authoritative_dependencies(tmp_path) -> None:
    ctx = _setup(tmp_path)
    _, _, oms = _bridge(ctx)
    with pytest.raises(TypeError, match="ColdStartOrderManagementSystem"):
        CryptoColdStartExecutionBridge(oms=object(), authority_provider=ctx.authority)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="authoritative core provider"):
        CryptoColdStartExecutionBridge(oms=oms, authority_provider=object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("package", object(), "prepared crypto PAPER package"),
        ("operator_decision", object(), "operator decision"),
        ("operator_registry", object(), "operator registry"),
        ("checkpoint", object(), "PRE_CONSUME checkpoint"),
        ("risk_decision", object(), "RiskDecision and MarketSnapshot"),
        ("market", object(), "RiskDecision and MarketSnapshot"),
    ],
)
def test_bridge_rejects_wrong_authority_types(tmp_path, field, value, message) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    kwargs = _stage_kwargs(ctx, checkpoint, decision, market)
    kwargs[field] = value
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match=message):
        bridge.stage_after_checkpoint(**kwargs)


def test_bridge_rejects_naive_reversed_and_expired_stage_times(tmp_path) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    kwargs = _stage_kwargs(ctx, checkpoint, decision, market)

    with pytest.raises(ValueError, match="consume_at must be timezone-aware"):
        bridge.stage_after_checkpoint(**{**kwargs, "consume_at": kwargs["consume_at"].replace(tzinfo=None)})
    with pytest.raises(ValueError, match="stage_at must be timezone-aware"):
        bridge.stage_after_checkpoint(**{**kwargs, "stage_at": kwargs["stage_at"].replace(tzinfo=None)})
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="consumption cannot occur after staging"):
        bridge.stage_after_checkpoint(
            **{
                **kwargs,
                "consume_at": NOW + timedelta(seconds=5),
                "stage_at": NOW + timedelta(seconds=4),
            }
        )
    deadline = ctx.package.execution_deadline
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="expired before cold-start staging"):
        bridge.stage_after_checkpoint(
            **{
                **kwargs,
                "consume_at": deadline - timedelta(milliseconds=1),
                "stage_at": deadline,
            }
        )


def test_bridge_rejects_missing_or_expired_durable_operator_decision(tmp_path) -> None:
    ctx = _setup(tmp_path / "missing")
    checkpoint = _checkpoint(ctx)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    empty_registry = bridge_mod.SQLiteCryptoOperatorDecisionRegistry(
        SQLiteRuntime(tmp_path / "empty-operator.sqlite3")
    )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="durable operator decision unavailable"):
        bridge.stage_after_checkpoint(
            **{
                **_stage_kwargs(ctx, checkpoint, decision, market),
                "operator_registry": empty_registry,
            }
        )

    expired = _setup(tmp_path / "expired")
    expired_checkpoint = _checkpoint(expired)
    expired_bridge, _, _ = _bridge(expired)
    expired_decision, expired_market = _risk_and_market(expired)
    after_operator_expiry = expired.operator_decision.expires_at
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="expired before consumption"):
        expired_bridge.stage_after_checkpoint(
            **{
                **_stage_kwargs(expired, expired_checkpoint, expired_decision, expired_market),
                "consume_at": after_operator_expiry,
                "stage_at": after_operator_expiry + timedelta(milliseconds=1),
            }
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"package_hash": "0" * 64}, "checkpoint package mismatch"),
        ({"preparation_hash": "1" * 64}, "checkpoint preparation mismatch"),
        ({"operator_decision_hash": "2" * 64}, "checkpoint decision hash mismatch"),
        ({"attempt_id": "different-attempt"}, "checkpoint attempt mismatch"),
        ({"order_id": "different-order"}, "checkpoint order identity mismatch"),
        ({"client_order_id": "different-client"}, "checkpoint order identity mismatch"),
        ({"pre_consume": SimpleNamespace(bootstrap_scope="WRONG_SCOPE")}, "outside cold-start scope"),
    ],
)
def test_bridge_checkpoint_binding_fails_closed(tmp_path, change, message) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    proxy = _checkpoint_proxy(checkpoint, **change)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match=message):
        CryptoColdStartExecutionBridge._validate_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            checkpoint=proxy,  # type: ignore[arg-type]
        )


def test_bridge_checkpoint_detects_cross_layer_scope_drift(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    monkeypatch.setattr(bridge_mod, "COLD_START_OMS_SCOPE", "DRIFTED_SCOPE")
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="scope drift"):
        CryptoColdStartExecutionBridge._validate_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            checkpoint=checkpoint,
        )


def test_handoff_id_refuses_package_or_decision_rebinding(tmp_path) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="mismatched package"):
        crypto_cold_start_handoff_id(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            checkpoint=_checkpoint_proxy(checkpoint, package_hash="0" * 64),  # type: ignore[arg-type]
        )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="mismatched decision"):
        crypto_cold_start_handoff_id(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            checkpoint=_checkpoint_proxy(checkpoint, operator_decision_hash="0" * 64),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: replace(d, decision_id="different-decision"), "RiskDecision id mismatch"),
        (lambda d: replace(d, valid_until=d.valid_until + timedelta(seconds=1)), "RiskDecision expiry mismatch"),
        (lambda d: replace(d, safety_state_version=d.safety_state_version + 1), "Safety version mismatch"),
        (lambda d: replace(d, market_fingerprint="0" * 64), "RiskDecision market mismatch"),
        (lambda d: replace(d, intent_fingerprint="1" * 64), "RiskDecision intent mismatch"),
        (lambda d: replace(d, reason_detail="fingerprint-drift"), "RiskDecision fingerprint mismatch"),
    ],
)
def test_decision_package_binding_rejects_every_material_drift(tmp_path, mutate, message) -> None:
    ctx = _setup(tmp_path)
    decision, market = _risk_and_market(ctx)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match=message):
        _validate_decision_package(
            package=ctx.package,
            risk_decision=mutate(decision),
            market=market,
        )


def test_decision_package_rejects_market_snapshot_drift(tmp_path) -> None:
    ctx = _setup(tmp_path)
    decision, market = _risk_and_market(ctx)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="MarketSnapshot mismatch"):
        _validate_decision_package(
            package=ctx.package,
            risk_decision=decision,
            market=replace(market, last=market.last + 1),
        )


def test_oms_stage_authority_rejects_invalid_or_future_context(tmp_path) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    order = ctx.order_store.get_by_order_id(ctx.package.order_id)
    assert order is not None

    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="context is invalid"):
        bridge.authorize_oms_stage(
            order=order,
            decision=decision,
            market=market,
            now=NOW + timedelta(seconds=4),
            context=object(),
        )

    future_context = CryptoColdStartOmsStageContext(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        checkpoint=checkpoint,
        consumed_at=NOW + timedelta(seconds=5),
    )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="future-dated"):
        bridge.authorize_oms_stage(
            order=order,
            decision=decision,
            market=market,
            now=NOW + timedelta(seconds=4),
            context=future_context,
        )


def test_bridge_wraps_oms_stage_failure_without_network_retry(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    bridge, _, oms = _bridge(ctx)
    decision, market = _risk_and_market(ctx)

    def fail_stage(**_kwargs):
        raise RuntimeError("synthetic durable staging failure")

    monkeypatch.setattr(oms, "stage_cold_start_external_submission", fail_stage)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="OMS-owned cold-start staging failed"):
        bridge.stage_after_checkpoint(**_stage_kwargs(ctx, checkpoint, decision, market))


def test_bridge_detects_authoritative_core_change_during_oms_staging(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    bridge, _, oms = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    original = oms.stage_cold_start_external_submission

    def stage_then_drift(**kwargs):
        result = original(**kwargs)
        ctx.safety.activate_circuit(
            reason="SYNTHETIC_POST_STAGE_DRIFT",
            now=NOW + timedelta(seconds=4, milliseconds=350),
        )
        return result

    monkeypatch.setattr(oms, "stage_cold_start_external_submission", stage_then_drift)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="core changed during OMS staging"):
        bridge.stage_after_checkpoint(**_stage_kwargs(ctx, checkpoint, decision, market))


def test_bridge_rejects_oms_handoff_id_and_checkpoint_drift(tmp_path, monkeypatch) -> None:
    for suffix, drift_field, message in (
        ("id", "authorization_id", "handoff id mismatch"),
        ("checkpoint", "checkpoint_hash", "handoff checkpoint mismatch"),
    ):
        ctx = _setup(tmp_path / suffix)
        checkpoint = _checkpoint(ctx)
        bridge, _, oms = _bridge(ctx)
        decision, market = _risk_and_market(ctx)
        original = oms.stage_cold_start_external_submission

        def stage_then_corrupt(*, _field=drift_field, **kwargs):
            order, handoff = original(**kwargs)
            values = {
                "authorization_id": handoff.authorization_id,
                "checkpoint_hash": handoff.checkpoint_hash,
            }
            values[_field] = "0" * 64
            return order, SimpleNamespace(**values)

        monkeypatch.setattr(oms, "stage_cold_start_external_submission", stage_then_corrupt)
        with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match=message):
            bridge.stage_after_checkpoint(**_stage_kwargs(ctx, checkpoint, decision, market))


def test_stage_context_rejects_bad_values_and_naive_time(tmp_path) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    with pytest.raises(ValueError, match="prepared package"):
        CryptoColdStartOmsStageContext(
            package=object(),  # type: ignore[arg-type]
            operator_decision=ctx.operator_decision,
            checkpoint=checkpoint,
            consumed_at=NOW,
        )
    with pytest.raises(ValueError, match="operator decision"):
        CryptoColdStartOmsStageContext(
            package=ctx.package,
            operator_decision=object(),  # type: ignore[arg-type]
            checkpoint=checkpoint,
            consumed_at=NOW,
        )
    with pytest.raises(ValueError, match="durable checkpoint"):
        CryptoColdStartOmsStageContext(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            checkpoint=object(),  # type: ignore[arg-type]
            consumed_at=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        CryptoColdStartOmsStageContext(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            checkpoint=checkpoint,
            consumed_at=NOW.replace(tzinfo=None),
        )


def test_preio_context_and_transport_constructors_fail_closed(tmp_path) -> None:
    ctx = _preio_setup(tmp_path)
    valid = ctx.context

    with pytest.raises(ValueError, match="prepared package"):
        replace(valid, package=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="operator decision"):
        replace(valid, operator_decision=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="operator registry"):
        replace(valid, operator_registry=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="broker order"):
        replace(valid, broker_order=object())  # type: ignore[arg-type]

    wrong_client = replace(ctx.broker_order, client_order_id="different-cold-start-client")
    with pytest.raises(ValueError, match="client id mismatch"):
        replace(valid, broker_order=wrong_client)
    wrong_fingerprint = replace(ctx.broker_order, quantity=ctx.broker_order.quantity * 2)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        replace(valid, broker_order=wrong_fingerprint)

    with pytest.raises(TypeError, match="requires delegate"):
        ColdStartFinalGuardedCryptoEntryTransport(
            delegate=None,  # type: ignore[arg-type]
            authority=ctx.authority,
            context=valid,
        )
    with pytest.raises(TypeError, match="durable PRE_IO authority"):
        ColdStartFinalGuardedCryptoEntryTransport(
            delegate=object(),  # type: ignore[arg-type]
            authority=object(),  # type: ignore[arg-type]
            context=valid,
        )
    with pytest.raises(TypeError, match="exact execution context"):
        ColdStartFinalGuardedCryptoEntryTransport(
            delegate=object(),  # type: ignore[arg-type]
            authority=ctx.authority,
            context=object(),  # type: ignore[arg-type]
        )


def test_preio_request_parsing_and_ephemeral_credentials_fail_closed(tmp_path) -> None:
    ctx = _preio_setup(tmp_path)
    with pytest.raises(CryptoColdStartPreIoInterlockError, match="root must be object"):
        _request_payload(b"[]")
    with pytest.raises(CryptoColdStartPreIoInterlockError, match="headers are invalid"):
        _ephemeral_credentials([])  # type: ignore[arg-type]
    with pytest.raises(CryptoColdStartPreIoInterlockError, match="credentials are invalid"):
        _ephemeral_credentials(
            {
                "APCA-API-KEY-ID": "same-synthetic-value",
                "APCA-API-SECRET-KEY": "same-synthetic-value",
            }
        )

    transport = ColdStartFinalGuardedCryptoEntryTransport(
        delegate=object(),  # type: ignore[arg-type]
        authority=ctx.authority,
        context=ctx.context,
    )
    with pytest.raises(CryptoColdStartPreIoInterlockError, match="root must be object"):
        transport.post(
            host="paper-api.alpaca.markets",
            path="/v2/orders",
            headers={
                "APCA-API-KEY-ID": "simulation-paper-key",
                "APCA-API-SECRET-KEY": "simulation-paper-secret",
            },
            body=b"[]",
            timeout_seconds=5,
            max_response_bytes=1024,
        )


def test_preio_authority_constructor_and_checkpoint_binding_fail_closed(tmp_path) -> None:
    ctx = _preio_setup(tmp_path)
    guard = ctx.authority._guard
    checkpoints = ctx.authority._checkpoints
    oms = ctx.authority._oms

    with pytest.raises(TypeError, match="isolated Final Guard"):
        CryptoColdStartPreIoAuthority(
            guard=object(),  # type: ignore[arg-type]
            checkpoint_registry=checkpoints,
            oms=oms,
        )
    with pytest.raises(TypeError, match="checkpoint registry"):
        CryptoColdStartPreIoAuthority(
            guard=guard,
            checkpoint_registry=object(),  # type: ignore[arg-type]
            oms=oms,
        )
    with pytest.raises(TypeError, match="cold-start OMS"):
        CryptoColdStartPreIoAuthority(
            guard=guard,
            checkpoint_registry=checkpoints,
            oms=object(),  # type: ignore[arg-type]
        )

    checkpoint = ctx.checkpoint
    cases = (
        ({"package_hash": "0" * 64}, "package hash mismatch"),
        ({"preparation_hash": "1" * 64}, "preparation hash mismatch"),
        ({"operator_decision_hash": "2" * 64}, "operator decision hash mismatch"),
        ({"attempt_id": "different-attempt"}, "attempt mismatch"),
        ({"order_id": "different-order"}, "order identity mismatch"),
        ({"client_order_id": "different-client"}, "order identity mismatch"),
        ({"pre_consume": SimpleNamespace(bootstrap_scope="WRONG_SCOPE")}, "outside cold-start bootstrap scope"),
    )
    for changes, message in cases:
        with pytest.raises(CryptoColdStartPreIoAuthorityBlocked, match=message):
            CryptoColdStartPreIoAuthority._validate_checkpoint(
                checkpoint=_checkpoint_proxy(checkpoint, **changes),  # type: ignore[arg-type]
                package=ctx.package,
                operator_decision=ctx.context.operator_decision,
            )


def test_preio_authority_blocks_missing_checkpoint_and_handoff(tmp_path, monkeypatch) -> None:
    ctx = _preio_setup(tmp_path)
    empty = SQLiteCryptoColdStartExecutionAttemptRegistry(
        SQLiteRuntime(tmp_path / "empty-checkpoint.sqlite3")
    )
    missing_checkpoint = CryptoColdStartPreIoAuthority(
        guard=ctx.authority._guard,
        checkpoint_registry=empty,
        oms=ctx.authority._oms,
    )
    values = vars(SimpleNamespace(**{
        "package": ctx.context.package,
        "operator_decision": ctx.context.operator_decision,
        "operator_registry": ctx.context.operator_registry,
        "broker_order": ctx.context.broker_order,
        "lifecycle": ctx.context.lifecycle,
        "prepared_account": ctx.context.prepared_account,
        "prepared_asset": ctx.context.prepared_asset,
        "prepared_product_profile": ctx.context.prepared_product_profile,
        "fresh_account": ctx.context.fresh_account,
        "fresh_asset": ctx.context.fresh_asset,
        "fresh_product_profile": ctx.context.fresh_product_profile,
        "fresh_market": ctx.context.fresh_market,
        "fresh_flat_account": ctx.context.fresh_flat_account,
        "now": NOW + timedelta(seconds=4, milliseconds=450),
    }))
    with pytest.raises(CryptoColdStartPreIoAuthorityBlocked, match="checkpoint is unavailable"):
        missing_checkpoint.authorize(**values)

    def missing_handoff(**_kwargs):
        raise bridge_mod.ColdStartExternalSubmissionConflict("synthetic missing handoff")

    monkeypatch.setattr(
        ctx.authority._oms,
        "resolve_cold_start_external_submission_handoff",
        missing_handoff,
    )
    with pytest.raises(CryptoColdStartPreIoAuthorityBlocked, match="handoff is unavailable"):
        ctx.authority.authorize(**values)


def test_preio_authority_detects_handoff_and_guard_result_rebinding(tmp_path, monkeypatch) -> None:
    # Each branch receives a fresh fully staged context so failures are isolated.
    ctx = _preio_setup(tmp_path / "handoff")
    values = {
        "package": ctx.context.package,
        "operator_decision": ctx.context.operator_decision,
        "operator_registry": ctx.context.operator_registry,
        "broker_order": ctx.context.broker_order,
        "lifecycle": ctx.context.lifecycle,
        "prepared_account": ctx.context.prepared_account,
        "prepared_asset": ctx.context.prepared_asset,
        "prepared_product_profile": ctx.context.prepared_product_profile,
        "fresh_account": ctx.context.fresh_account,
        "fresh_asset": ctx.context.fresh_asset,
        "fresh_product_profile": ctx.context.fresh_product_profile,
        "fresh_market": ctx.context.fresh_market,
        "fresh_flat_account": ctx.context.fresh_flat_account,
        "now": NOW + timedelta(seconds=4, milliseconds=450),
    }
    checkpoint = ctx.checkpoint
    authorization_id = crypto_cold_start_handoff_id(
        package=ctx.package,
        operator_decision=ctx.context.operator_decision,
        checkpoint=checkpoint,
    )
    original_resolve = ctx.authority._oms.resolve_cold_start_external_submission_handoff
    actual = original_resolve(order_id=ctx.package.order_id, authorization_id=authorization_id)
    handoff_values = {
        "authorization_id": actual.authorization_id,
        "package_hash": actual.package_hash,
        "operator_decision_hash": actual.operator_decision_hash,
        "checkpoint_hash": "0" * 64,
        "authority_state_fingerprint": actual.authority_state_fingerprint,
        "attempt_id": actual.attempt_id,
        "order_id": actual.order_id,
        "client_order_id": actual.client_order_id,
        "risk_decision_id": actual.risk_decision_id,
    }
    monkeypatch.setattr(
        ctx.authority._oms,
        "resolve_cold_start_external_submission_handoff",
        lambda **_kwargs: SimpleNamespace(**handoff_values),
    )
    with pytest.raises(CryptoColdStartPreIoAuthorityBlocked, match="does not bind exact checkpoint"):
        ctx.authority.authorize(**values)

    for suffix, result, message in (
        (
            "predecessor",
            SimpleNamespace(
                previous_attestation_hash="0" * 64,
                authority_state_fingerprint=checkpoint.authority_state_fingerprint,
                package_hash=ctx.package.package_hash,
            ),
            "predecessor differs",
        ),
        (
            "authority",
            SimpleNamespace(
                previous_attestation_hash=checkpoint.pre_consume.attestation_hash,
                authority_state_fingerprint="0" * 64,
                package_hash=ctx.package.package_hash,
            ),
            "authority fingerprint differs",
        ),
        (
            "package",
            SimpleNamespace(
                previous_attestation_hash=checkpoint.pre_consume.attestation_hash,
                authority_state_fingerprint=checkpoint.authority_state_fingerprint,
                package_hash="0" * 64,
            ),
            "package differs",
        ),
    ):
        fresh = _preio_setup(tmp_path / suffix)
        fresh_values = {
            "package": fresh.context.package,
            "operator_decision": fresh.context.operator_decision,
            "operator_registry": fresh.context.operator_registry,
            "broker_order": fresh.context.broker_order,
            "lifecycle": fresh.context.lifecycle,
            "prepared_account": fresh.context.prepared_account,
            "prepared_asset": fresh.context.prepared_asset,
            "prepared_product_profile": fresh.context.prepared_product_profile,
            "fresh_account": fresh.context.fresh_account,
            "fresh_asset": fresh.context.fresh_asset,
            "fresh_product_profile": fresh.context.fresh_product_profile,
            "fresh_market": fresh.context.fresh_market,
            "fresh_flat_account": fresh.context.fresh_flat_account,
            "now": NOW + timedelta(seconds=4, milliseconds=450),
        }
        monkeypatch.setattr(fresh.authority._guard, "authorize", lambda **_kwargs: result)
        with pytest.raises(CryptoColdStartPreIoAuthorityBlocked, match=message):
            fresh.authority.authorize(**fresh_values)
