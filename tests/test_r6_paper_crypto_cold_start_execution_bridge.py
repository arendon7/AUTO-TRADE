from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from autotrade.cold_start_oms import ColdStartOrderManagementSystem
from autotrade.domain import OrderStatus
from autotrade.ledger import InMemoryEventLedger, LedgerEvent
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_bridge import (
    CryptoColdStartExecutionBridge,
    CryptoColdStartExecutionBridgeBlocked,
    crypto_cold_start_handoff_id,
)
from test_r6_paper_crypto_canary_coordinator import NOW, _NoBroker, _decision, _intent, _market
from test_r6_paper_crypto_cold_start_final_guard import _pre, _setup


def _risk_and_market(ctx):
    market_attestation = _market()
    intent = _intent(quantity=ctx.package.quantity, limit_price=ctx.package.limit_price)
    decision = _decision(intent, market_attestation, approved_notional=ctx.package.notional)
    return decision, market_attestation.market


def _checkpoint(ctx):
    pre = _pre(ctx)
    return SQLiteCryptoColdStartExecutionAttemptRegistry(ctx.core).record_pre_consume(pre)


def _bridge(ctx, ledger=None):
    ledger = ledger or InMemoryEventLedger()
    oms = ColdStartOrderManagementSystem(
        broker=_NoBroker(),
        ledger=ledger,
        order_store=ctx.order_store,
        safety_state_store=ctx.safety,
    )
    return (
        CryptoColdStartExecutionBridge(
            oms=oms,
            authority_provider=ctx.authority,
        ),
        ledger,
        oms,
    )


def test_cold_start_bridge_stages_exact_checkpoint_under_oms_ownership(tmp_path) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    bridge, ledger, oms = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    consumed_at = NOW + timedelta(seconds=4, milliseconds=200)

    result = bridge.stage_after_checkpoint(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        checkpoint=checkpoint,
        risk_decision=decision,
        market=market,
        consume_at=consumed_at,
        stage_at=NOW + timedelta(seconds=4, milliseconds=300),
    )

    assert result.order.status is OrderStatus.SUBMITTING
    assert result.order.submitted_at == consumed_at
    assert result.handoff.package_hash == ctx.package.package_hash
    assert result.handoff.checkpoint_hash == checkpoint.record_hash
    assert result.handoff.authority_state_fingerprint == checkpoint.authority_state_fingerprint
    assert result.handoff.attempt_id == checkpoint.attempt_id
    assert result.handoff.client_order_id == ctx.package.client_order_id
    assert len(result.handoff.handoff_hash) == 64
    events = tuple(ledger.all_events())
    assert len(events) == 1
    assert events[0].event_type == "COLD_START_EXTERNAL_ORDER_HANDOFF_AUTHORIZED"
    assert oms.resolve_cold_start_external_submission_handoff(
        order_id=ctx.package.order_id,
        authorization_id=result.handoff.authorization_id,
    ) == result.handoff
    durable = ctx.operator_registry.get(ctx.operator_decision.context.preparation_hash)
    assert durable.status.value == "CONSUMED"
    assert durable.consumed_attempt_id == checkpoint.attempt_id


def test_cold_start_bridge_same_attempt_replay_recovers_original_consumption_timestamp(tmp_path) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    first_consumed = NOW + timedelta(seconds=4, milliseconds=200)

    first = bridge.stage_after_checkpoint(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        checkpoint=checkpoint,
        risk_decision=decision,
        market=market,
        consume_at=first_consumed,
        stage_at=NOW + timedelta(seconds=4, milliseconds=300),
    )
    replay = bridge.stage_after_checkpoint(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        checkpoint=checkpoint,
        risk_decision=decision,
        market=market,
        consume_at=NOW + timedelta(seconds=4, milliseconds=400),
        stage_at=NOW + timedelta(seconds=4, milliseconds=500),
    )

    assert replay.handoff == first.handoff
    assert replay.order.submitted_at == first_consumed


def test_cold_start_bridge_refuses_authority_change_before_consumption(tmp_path) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    ctx.safety.activate_circuit(reason="LATE_RISK_BREACH", now=NOW + timedelta(seconds=4))

    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="core changed"):
        bridge.stage_after_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            checkpoint=checkpoint,
            risk_decision=decision,
            market=market,
            consume_at=NOW + timedelta(seconds=4, milliseconds=200),
            stage_at=NOW + timedelta(seconds=4, milliseconds=300),
        )
    assert ctx.operator_registry.get(ctx.operator_decision.context.preparation_hash).status.value == "ISSUED"


def test_cold_start_bridge_refuses_wrong_checkpoint_market_or_decision(tmp_path) -> None:
    ctx = _setup(tmp_path / "one")
    other = _setup(tmp_path / "two")
    checkpoint = _checkpoint(ctx)
    other_checkpoint = _checkpoint(other)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)

    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="checkpoint"):
        bridge.stage_after_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            checkpoint=other_checkpoint,
            risk_decision=decision,
            market=market,
            consume_at=NOW + timedelta(seconds=4, milliseconds=200),
            stage_at=NOW + timedelta(seconds=4, milliseconds=300),
        )

    wrong_market = replace(market, last=market.last + 1)
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="MarketSnapshot mismatch"):
        bridge.stage_after_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            checkpoint=checkpoint,
            risk_decision=decision,
            market=wrong_market,
            consume_at=NOW + timedelta(seconds=4, milliseconds=200),
            stage_at=NOW + timedelta(seconds=4, milliseconds=300),
        )

    wrong_decision = replace(decision, reason_detail="different decision material")
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="fingerprint mismatch"):
        bridge.stage_after_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            checkpoint=checkpoint,
            risk_decision=wrong_decision,
            market=market,
            consume_at=NOW + timedelta(seconds=4, milliseconds=200),
            stage_at=NOW + timedelta(seconds=4, milliseconds=300),
        )


def test_cold_start_oms_refuses_nonresumable_order_or_submitting_without_handoff(tmp_path) -> None:
    ctx = _setup(tmp_path / "terminal")
    checkpoint = _checkpoint(ctx)
    bridge, _, _ = _bridge(ctx)
    decision, market = _risk_and_market(ctx)
    current = ctx.order_store.get_by_order_id(ctx.package.order_id)
    assert current is not None
    ctx.order_store.update(replace(current, status=OrderStatus.CANCELLED))
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="OMS-owned"):
        bridge.stage_after_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            checkpoint=checkpoint,
            risk_decision=decision,
            market=market,
            consume_at=NOW + timedelta(seconds=4, milliseconds=200),
            stage_at=NOW + timedelta(seconds=4, milliseconds=300),
        )

    ctx2 = _setup(tmp_path / "submitting")
    checkpoint2 = _checkpoint(ctx2)
    bridge2, _, _ = _bridge(ctx2)
    decision2, market2 = _risk_and_market(ctx2)
    current2 = ctx2.order_store.get_by_order_id(ctx2.package.order_id)
    assert current2 is not None
    ctx2.order_store.update(
        replace(
            current2,
            status=OrderStatus.SUBMITTING,
            submitted_at=NOW + timedelta(seconds=4, milliseconds=100),
        )
    )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="OMS-owned"):
        bridge2.stage_after_checkpoint(
            package=ctx2.package,
            operator_decision=ctx2.operator_decision,
            operator_registry=ctx2.operator_registry,
            checkpoint=checkpoint2,
            risk_decision=decision2,
            market=market2,
            consume_at=NOW + timedelta(seconds=4, milliseconds=200),
            stage_at=NOW + timedelta(seconds=4, milliseconds=300),
        )


def test_cold_start_oms_rejects_tampered_existing_handoff_event(tmp_path) -> None:
    ctx = _setup(tmp_path)
    checkpoint = _checkpoint(ctx)
    ledger = InMemoryEventLedger()
    bridge, _, _ = _bridge(ctx, ledger)
    decision, market = _risk_and_market(ctx)
    handoff_id = crypto_cold_start_handoff_id(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        checkpoint=checkpoint,
    )
    ledger.append(
        LedgerEvent(
            event_id=f"cold-start-external-handoff:{ctx.package.order_id}:{handoff_id}",
            event_type="WRONG_EVENT_TYPE",
            occurred_at=NOW + timedelta(seconds=4, milliseconds=200),
            payload={},
        )
    )
    with pytest.raises(CryptoColdStartExecutionBridgeBlocked, match="OMS-owned"):
        bridge.stage_after_checkpoint(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            checkpoint=checkpoint,
            risk_decision=decision,
            market=market,
            consume_at=NOW + timedelta(seconds=4, milliseconds=200),
            stage_at=NOW + timedelta(seconds=4, milliseconds=300),
        )
