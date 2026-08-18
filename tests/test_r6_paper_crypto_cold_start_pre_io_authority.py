from __future__ import annotations

from datetime import timedelta

import pytest

from autotrade.cold_start_oms import ColdStartOrderManagementSystem
from autotrade.ledger import InMemoryEventLedger, LedgerEvent
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_bridge import (
    CryptoColdStartExecutionBridge,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_final_guard import (
    CryptoColdStartFinalWritePhase,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io_authority import (
    CryptoColdStartPreIoAuthority,
    CryptoColdStartPreIoAuthorityBlocked,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from test_r6_paper_crypto_canary_coordinator import NOW, _NoBroker, _decision, _intent, _market
from test_r6_paper_crypto_cold_start_final_guard import _pre, _setup


def _risk_and_market(ctx):
    attestation = _market()
    intent = _intent(quantity=ctx.package.quantity, limit_price=ctx.package.limit_price)
    decision = _decision(intent, attestation, approved_notional=ctx.package.notional)
    return decision, attestation.market


def _oms(ctx, ledger):
    return ColdStartOrderManagementSystem(
        broker=_NoBroker(),
        ledger=ledger,
        order_store=ctx.order_store,
        safety_state_store=ctx.safety,
    )


def _prepare_chain(ctx, ledger):
    pre = _pre(ctx)
    registry = SQLiteCryptoColdStartExecutionAttemptRegistry(ctx.core)
    checkpoint = registry.record_pre_consume(pre)
    decision, market = _risk_and_market(ctx)
    oms = _oms(ctx, ledger)
    bridge = CryptoColdStartExecutionBridge(
        oms=oms,
        authority_provider=ctx.authority,
    )
    stage = bridge.stage_after_checkpoint(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        checkpoint=checkpoint,
        risk_decision=decision,
        market=market,
        consume_at=NOW + timedelta(seconds=4, milliseconds=200),
        stage_at=NOW + timedelta(seconds=4, milliseconds=300),
    )
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=400),
    )
    return registry, checkpoint, stage, oms


def _authorize(ctx, registry, oms):
    authority = CryptoColdStartPreIoAuthority(
        guard=ctx.guard,
        checkpoint_registry=registry,
        oms=oms,
    )
    return authority.authorize(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        broker_order=ctx.broker_order,
        lifecycle=ctx.lifecycle,
        prepared_account=ctx.prepared_account,
        prepared_asset=ctx.prepared_asset,
        prepared_product_profile=ctx.prepared_profile,
        fresh_account=ctx.fresh_account,
        fresh_asset=ctx.fresh_asset,
        fresh_product_profile=ctx.fresh_profile,
        fresh_market=ctx.fresh_market,
        fresh_flat_account=ctx.fresh_flat,
        now=NOW + timedelta(seconds=4, milliseconds=500),
    )


def test_preio_authority_loads_exact_checkpoint_and_oms_handoff_before_guard(tmp_path) -> None:
    ctx = _setup(tmp_path)
    ledger = InMemoryEventLedger()
    registry, checkpoint, stage, oms = _prepare_chain(ctx, ledger)
    result = _authorize(ctx, registry, oms)

    assert result.phase is CryptoColdStartFinalWritePhase.PRE_IO
    assert result.previous_attestation_hash == checkpoint.pre_consume.attestation_hash
    assert result.authority_state_fingerprint == checkpoint.authority_state_fingerprint
    assert result.package_hash == stage.handoff.package_hash
    assert result.lifecycle_status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert result.entry_attempt_count == 1


def test_preio_authority_refuses_missing_oms_handoff_even_when_local_states_look_ready(tmp_path) -> None:
    ctx = _setup(tmp_path)
    ledger = InMemoryEventLedger()
    registry, _, _, _ = _prepare_chain(ctx, ledger)
    with pytest.raises(CryptoColdStartPreIoAuthorityBlocked, match="OMS cold-start handoff"):
        _authorize(ctx, registry, _oms(ctx, InMemoryEventLedger()))


def test_preio_authority_refuses_tampered_oms_handoff_payload(tmp_path) -> None:
    ctx = _setup(tmp_path)
    ledger = InMemoryEventLedger()
    registry, _, stage, _ = _prepare_chain(ctx, ledger)
    original = tuple(ledger.all_events())[0]
    tampered = InMemoryEventLedger()
    payload = dict(original.payload)
    payload["package_hash"] = "0" * 64
    tampered.append(
        LedgerEvent(
            event_id=original.event_id,
            event_type=original.event_type,
            occurred_at=original.occurred_at,
            payload=payload,
        )
    )
    with pytest.raises(CryptoColdStartPreIoAuthorityBlocked, match="OMS cold-start handoff"):
        _authorize(ctx, registry, _oms(ctx, tampered))
    assert stage.handoff.package_hash == ctx.package.package_hash


def test_preio_authority_refuses_corrupt_checkpoint_before_oms_lookup(tmp_path) -> None:
    ctx = _setup(tmp_path)
    ledger = InMemoryEventLedger()
    registry, checkpoint, _, oms = _prepare_chain(ctx, ledger)
    conn = ctx.core.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_cold_start_execution_attempts SET record_hash=? WHERE attempt_id=?",
            ("0" * 64, checkpoint.attempt_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoColdStartPreIoAuthorityBlocked, match="checkpoint is unavailable or corrupt"):
        _authorize(ctx, registry, oms)


def test_preio_authority_refuses_duplicate_or_unavailable_oms_handoff_ledger(tmp_path) -> None:
    ctx = _setup(tmp_path / "duplicate")
    ledger = InMemoryEventLedger()
    registry, _, _, _ = _prepare_chain(ctx, ledger)
    event = tuple(ledger.all_events())[0]

    class DuplicateLedger:
        def all_events(self):
            return (event, event)

    with pytest.raises(CryptoColdStartPreIoAuthorityBlocked, match="OMS cold-start handoff"):
        _authorize(ctx, registry, _oms(ctx, DuplicateLedger()))

    ctx2 = _setup(tmp_path / "unavailable")
    ledger2 = InMemoryEventLedger()
    registry2, _, _, _ = _prepare_chain(ctx2, ledger2)

    class BrokenLedger:
        def all_events(self):
            raise RuntimeError("synthetic storage failure")

    with pytest.raises(Exception):
        _authorize(ctx2, registry2, _oms(ctx2, BrokenLedger()))
