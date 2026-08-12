from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.connectivity_canary_authority import CONNECTIVITY_CANARY_STRATEGY_ID
from autotrade.connectivity_oms_stage import (
    ConnectivityOmsStageConflict,
    ConnectivityOmsStageRejected,
    ConnectivityOmsStager,
)
from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    OrderType,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
)
from autotrade.ledger import InMemoryEventLedger, LedgerEvent
from autotrade.state import InMemoryOrderStore, InMemorySafetyStateStore

NOW = datetime(2026, 8, 12, 6, 30, tzinfo=timezone.utc)
BINDING_HASH = "a" * 64
PERMIT_HASH = "b" * 64
ATTEMPT_ID = "connectivity-attempt-1"


def order(*, strategy_id: str = CONNECTIVITY_CANARY_STRATEGY_ID) -> OrderRecord:
    intent = OrderIntent(
        intent_id="connectivity-intent-1",
        idempotency_key="connectivity-idempotency-1",
        strategy_id=strategy_id,
        symbol="FIVE",
        side=Side.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.LIMIT,
        created_at=NOW - timedelta(minutes=5),
        limit_price=Decimal("5.01"),
    )
    return OrderRecord(
        order_id="connectivity-order-1",
        intent=intent,
        risk_decision_id="original-risk-1",
        status=OrderStatus.VALIDATED,
        created_at=NOW - timedelta(minutes=5),
    )


def market(*, symbol: str = "FIVE", ask: str = "5.01") -> MarketSnapshot:
    ask_value = Decimal(ask)
    return MarketSnapshot(
        symbol=symbol,
        bid=ask_value - Decimal("0.01"),
        ask=ask_value,
        last=ask_value - Decimal("0.01"),
        observed_at=NOW,
    )


def decision(current: OrderRecord, current_market: MarketSnapshot, *, status=RiskDecisionStatus.APPROVED, safety_version=0) -> RiskDecision:
    return RiskDecision(
        decision_id="fresh-risk-1",
        intent_id=current.intent.intent_id,
        status=status,
        reason_code="OK",
        reason_detail="fresh connectivity safety approved",
        evaluated_at=NOW,
        valid_until=NOW + timedelta(seconds=5),
        limits_version="r6-connectivity-final-freshness-v1",
        intent_fingerprint=intent_fingerprint(current.intent),
        market_fingerprint=market_fingerprint(current_market),
        approved_notional=Decimal("5.01") if status is RiskDecisionStatus.APPROVED else None,
        safety_state_version=safety_version,
    )


def fixture():
    orders = InMemoryOrderStore()
    current = order()
    orders.create_if_absent(current)
    ledger = InMemoryEventLedger()
    safety = InMemorySafetyStateStore()
    stager = ConnectivityOmsStager(
        order_store=orders,
        ledger=ledger,
        safety_state_store=safety,
    )
    return current, orders, ledger, safety, stager


def stage(stager: ConnectivityOmsStager, current: OrderRecord, current_market: MarketSnapshot, *, at=NOW):
    return stager.stage(
        order_id=current.order_id,
        attempt_id=ATTEMPT_ID,
        execution_freshness_binding_hash=BINDING_HASH,
        final_freshness_permit_hash=PERMIT_HASH,
        decision=decision(current, current_market),
        market=current_market,
        now=at,
        valid_until=NOW + timedelta(seconds=5),
    )


def test_connectivity_oms_stage_happy_path_is_oms_only() -> None:
    current, orders, ledger, _, stager = fixture()
    current_market = market()
    staged, handoff = stage(stager, current, current_market)
    assert staged.status is OrderStatus.SUBMITTING
    assert staged.risk_decision_id == "fresh-risk-1"
    assert staged.submitted_at == NOW
    assert handoff.purpose == "CONNECTIVITY_CANARY"
    assert handoff.original_risk_decision_id == "original-risk-1"
    assert handoff.fresh_risk_decision_id == "fresh-risk-1"
    assert handoff.execution_freshness_binding_hash == BINDING_HASH
    assert handoff.final_freshness_permit_hash == PERMIT_HASH
    assert handoff.is_valid_at(NOW) is True
    assert stager.verify_handoff(handoff) == staged
    events = ledger.all_events()
    assert len(events) == 1
    assert events[0].event_type == "CONNECTIVITY_EXTERNAL_HANDOFF_AUTHORIZED"
    assert orders.get_by_order_id(current.order_id) == staged


def test_connectivity_oms_stage_same_attempt_replay_is_idempotent() -> None:
    current, _, ledger, _, stager = fixture()
    current_market = market()
    first_order, first_handoff = stage(stager, current, current_market)
    replay_order, replay_handoff = stage(
        stager, current, current_market, at=NOW + timedelta(seconds=1)
    )
    assert replay_order == first_order
    assert replay_handoff == first_handoff
    assert len(ledger.all_events()) == 1


def test_connectivity_oms_stage_rejects_wrong_market_symbol_even_with_matching_decision() -> None:
    current, _, _, _, stager = fixture()
    wrong_market = market(symbol="OTHER")
    with pytest.raises(ConnectivityOmsStageRejected, match="market symbol"):
        stage(stager, current, wrong_market)


def test_connectivity_oms_stage_rejects_non_reserved_strategy() -> None:
    orders = InMemoryOrderStore()
    current = order(strategy_id="normal-strategy")
    orders.create_if_absent(current)
    stager = ConnectivityOmsStager(
        order_store=orders,
        ledger=InMemoryEventLedger(),
        safety_state_store=InMemorySafetyStateStore(),
    )
    with pytest.raises(ConnectivityOmsStageRejected, match="reserved connectivity"):
        stage(stager, current, market())


def test_connectivity_oms_stage_rejects_non_approved_fresh_decision() -> None:
    current, _, _, _, stager = fixture()
    current_market = market()
    rejected = decision(current, current_market, status=RiskDecisionStatus.REJECTED)
    with pytest.raises(ConnectivityOmsStageRejected, match="not APPROVED"):
        stager.stage(
            order_id=current.order_id,
            attempt_id=ATTEMPT_ID,
            execution_freshness_binding_hash=BINDING_HASH,
            final_freshness_permit_hash=PERMIT_HASH,
            decision=rejected,
            market=current_market,
            now=NOW,
            valid_until=NOW + timedelta(seconds=5),
        )


def test_connectivity_oms_stage_rejects_expired_binding_or_risk_decision() -> None:
    current, _, _, _, stager = fixture()
    current_market = market()
    with pytest.raises(ConnectivityOmsStageRejected, match="binding is expired"):
        stager.stage(
            order_id=current.order_id,
            attempt_id=ATTEMPT_ID,
            execution_freshness_binding_hash=BINDING_HASH,
            final_freshness_permit_hash=PERMIT_HASH,
            decision=decision(current, current_market),
            market=current_market,
            now=NOW + timedelta(seconds=5),
            valid_until=NOW + timedelta(seconds=5),
        )
    with pytest.raises(ConnectivityOmsStageRejected, match="RiskDecision expired"):
        stager.stage(
            order_id=current.order_id,
            attempt_id=ATTEMPT_ID,
            execution_freshness_binding_hash=BINDING_HASH,
            final_freshness_permit_hash=PERMIT_HASH,
            decision=decision(current, current_market),
            market=current_market,
            now=NOW + timedelta(seconds=5),
            valid_until=NOW + timedelta(seconds=6),
        )


def test_connectivity_oms_stage_rejects_safety_version_drift() -> None:
    current, _, _, safety, stager = fixture()
    current_market = market()
    safety.reset(now=NOW)
    with pytest.raises(ConnectivityOmsStageRejected, match="Safety state version changed"):
        stage(stager, current, current_market)


def test_connectivity_oms_stage_rejects_kill_switch_and_circuit() -> None:
    current, _, _, safety, stager = fixture()
    current_market = market()
    killed = safety.activate(reason="test", now=NOW)
    killed_decision = decision(current, current_market, safety_version=killed.version)
    with pytest.raises(ConnectivityOmsStageRejected, match="kill switch"):
        stager.stage(
            order_id=current.order_id,
            attempt_id=ATTEMPT_ID,
            execution_freshness_binding_hash=BINDING_HASH,
            final_freshness_permit_hash=PERMIT_HASH,
            decision=killed_decision,
            market=current_market,
            now=NOW,
            valid_until=NOW + timedelta(seconds=5),
        )

    current2, _, _, safety2, stager2 = fixture()
    circuit = safety2.activate_circuit(reason="test", now=NOW)
    circuit_decision = decision(current2, current_market, safety_version=circuit.version)
    with pytest.raises(ConnectivityOmsStageRejected, match="safety circuit"):
        stager2.stage(
            order_id=current2.order_id,
            attempt_id=ATTEMPT_ID,
            execution_freshness_binding_hash=BINDING_HASH,
            final_freshness_permit_hash=PERMIT_HASH,
            decision=circuit_decision,
            market=current_market,
            now=NOW,
            valid_until=NOW + timedelta(seconds=5),
        )


def test_connectivity_oms_stage_replay_requires_durable_matching_submitting_state() -> None:
    current, orders, _, _, stager = fixture()
    current_market = market()
    staged, _ = stage(stager, current, current_market)
    orders.update(replace(staged, submitted_at=NOW + timedelta(milliseconds=1)))
    with pytest.raises(ConnectivityOmsStageConflict, match="timestamp changed"):
        stage(stager, current, current_market, at=NOW + timedelta(seconds=1))


def test_connectivity_oms_stage_rejects_foreign_event_identity() -> None:
    current, orders, _, safety, _ = fixture()
    ledger = InMemoryEventLedger()
    ledger.append(
        LedgerEvent(
            event_id=f"connectivity-handoff:{current.order_id}:{ATTEMPT_ID}",
            event_type="WRONG_EVENT_TYPE",
            occurred_at=NOW,
            payload={},
        )
    )
    orders.update(
        replace(
            current,
            status=OrderStatus.SUBMITTING,
            risk_decision_id="fresh-risk-1",
            submitted_at=NOW,
        )
    )
    stager = ConnectivityOmsStager(
        order_store=orders,
        ledger=ledger,
        safety_state_store=safety,
    )
    with pytest.raises(ConnectivityOmsStageConflict, match="event type mismatch"):
        stage(stager, current, market(), at=NOW + timedelta(seconds=1))
