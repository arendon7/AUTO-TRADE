from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderStatus,
    OrderType,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
)
from autotrade.health_bridge import EffectiveHealthControl, HealthRiskMode
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import (
    ExternalSubmissionHandoffConflict,
    OrderManagementSystem,
)
from autotrade.state import InMemoryOrderStore, InMemorySafetyStateStore


UTC = timezone.utc
T0 = datetime(2026, 8, 11, 16, 55, tzinfo=UTC)
HANDOFF_ID = "a" * 64


class NeverCalledBroker:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, *, order, market, now):
        del order, market, now
        self.calls += 1
        raise AssertionError("external OMS preparation/staging must not invoke broker.submit")


class HealthyBridge:
    def effective_control(self, *, strategy_id, portfolio_entity_id, now):
        del strategy_id, portfolio_entity_id, now
        return EffectiveHealthControl(
            mode=HealthRiskMode.NORMAL,
            order_multiplier=Decimal("1"),
            strategy_multiplier=Decimal("1"),
            portfolio_multiplier=Decimal("1"),
            reason="R6_EXTERNAL_HANDOFF",
            strategy_state_fingerprint="1" * 64,
            portfolio_state_fingerprint="2" * 64,
        )


class ReducedBridge:
    def effective_control(self, *, strategy_id, portfolio_entity_id, now):
        del strategy_id, portfolio_entity_id, now
        return EffectiveHealthControl(
            mode=HealthRiskMode.REDUCED,
            order_multiplier=Decimal("0.5"),
            strategy_multiplier=Decimal("0.5"),
            portfolio_multiplier=Decimal("0.5"),
            reason="R6_REDUCED",
            strategy_state_fingerprint="3" * 64,
            portfolio_state_fingerprint="4" * 64,
        )


class CrashOnceOrderStore(InMemoryOrderStore):
    def __init__(self) -> None:
        super().__init__()
        self.crash_on_submitting = True

    def update(self, order) -> None:
        if self.crash_on_submitting and order.status is OrderStatus.SUBMITTING:
            self.crash_on_submitting = False
            raise SystemExit("synthetic crash after durable handoff event")
        super().update(order)


def intent() -> OrderIntent:
    return OrderIntent(
        intent_id="external-handoff-intent-001",
        idempotency_key="external-handoff-idem-001",
        strategy_id="external-handoff-strategy",
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.LIMIT,
        created_at=T0 - timedelta(seconds=2),
        limit_price=Decimal("10"),
    )


def market() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="AAPL",
        bid=Decimal("9.99"),
        ask=Decimal("10.01"),
        last=Decimal("10"),
        observed_at=T0 - timedelta(milliseconds=100),
    )


def decision(current_intent: OrderIntent | None = None, *, safety_version: int = 0) -> RiskDecision:
    current_intent = current_intent or intent()
    current_market = market()
    return RiskDecision(
        decision_id="external-handoff-risk-001",
        intent_id=current_intent.intent_id,
        status=RiskDecisionStatus.APPROVED,
        reason_code="APPROVED",
        reason_detail="bounded R6 external handoff fixture",
        evaluated_at=T0 - timedelta(milliseconds=50),
        valid_until=T0 + timedelta(seconds=10),
        limits_version="r6-test",
        intent_fingerprint=intent_fingerprint(current_intent),
        market_fingerprint=market_fingerprint(current_market),
        approved_notional=Decimal("10"),
        risk_reducing=False,
        safety_state_version=safety_version,
    )


def stack(*, order_store=None, health_bridge=None):
    broker = NeverCalledBroker()
    ledger = InMemoryEventLedger()
    orders = order_store or InMemoryOrderStore()
    safety = InMemorySafetyStateStore()
    oms = OrderManagementSystem(
        broker=broker,
        ledger=ledger,
        order_store=orders,
        safety_state_store=safety,
        health_bridge=health_bridge or HealthyBridge(),
        portfolio_health_entity_id="portfolio-r6-canary",
    )
    return oms, broker, ledger, orders, safety


def prepare(oms: OrderManagementSystem):
    current_intent = intent()
    current_market = market()
    current_decision = decision(current_intent)
    current_order = oms.validate_for_external_submission(
        intent=current_intent,
        decision=current_decision,
        market=current_market,
        now=T0,
    )
    return current_order, current_decision, current_market


def stage(oms, current_order, current_decision, current_market, *, now=T0 + timedelta(seconds=1)):
    return oms.stage_external_submission(
        order_id=current_order.order_id,
        handoff_id=HANDOFF_ID,
        decision=current_decision,
        market=current_market,
        now=now,
    )


def test_validate_for_external_submission_is_brokerless_idempotent_and_durable() -> None:
    oms, broker, ledger, _, _ = stack()
    current_order, current_decision, current_market = prepare(oms)
    assert current_order.status is OrderStatus.VALIDATED
    assert broker.calls == 0
    assert [event.event_type for event in ledger.all_events()] == ["ORDER_VALIDATED"]

    replay = oms.validate_for_external_submission(
        intent=current_order.intent,
        decision=current_decision,
        market=current_market,
        now=T0 + timedelta(milliseconds=100),
    )
    assert replay == current_order
    assert broker.calls == 0
    assert len(ledger.all_events()) == 1


def test_external_stage_is_oms_owned_and_verifiable_without_broker_io() -> None:
    oms, broker, ledger, _, _ = stack()
    current_order, current_decision, current_market = prepare(oms)
    staged, handoff = stage(oms, current_order, current_decision, current_market)

    assert staged.status is OrderStatus.SUBMITTING
    assert staged.submitted_at == handoff.authorized_at
    assert handoff.handoff_id == HANDOFF_ID
    assert handoff.intent_fingerprint == intent_fingerprint(current_order.intent)
    assert handoff.risk_decision_id == current_decision.decision_id
    assert handoff.safety_state_version == current_decision.safety_state_version
    assert handoff.market_fingerprint == current_decision.market_fingerprint
    assert handoff.decision_valid_until == current_decision.valid_until
    assert broker.calls == 0
    assert [event.event_type for event in ledger.all_events()] == [
        "ORDER_VALIDATED",
        "EXTERNAL_ORDER_HANDOFF_AUTHORIZED",
    ]
    assert oms.verify_external_submission_handoff(handoff) == staged


def test_stage_replay_is_idempotent_and_does_not_append_second_handoff() -> None:
    oms, broker, ledger, _, _ = stack()
    current_order, current_decision, current_market = prepare(oms)
    first_order, first_handoff = stage(oms, current_order, current_decision, current_market)
    second_order, second_handoff = stage(
        oms,
        current_order,
        current_decision,
        current_market,
        now=T0 + timedelta(seconds=2),
    )
    assert second_order == first_order
    assert second_handoff == first_handoff
    assert broker.calls == 0
    assert sum(event.event_type == "EXTERNAL_ORDER_HANDOFF_AUTHORIZED" for event in ledger.all_events()) == 1


def test_crash_after_handoff_event_before_status_update_is_restart_safe() -> None:
    orders = CrashOnceOrderStore()
    oms, broker, ledger, _, _ = stack(order_store=orders)
    current_order, current_decision, current_market = prepare(oms)

    with pytest.raises(SystemExit, match="synthetic crash"):
        stage(oms, current_order, current_decision, current_market)
    assert orders.get_by_order_id(current_order.order_id).status is OrderStatus.VALIDATED
    assert sum(event.event_type == "EXTERNAL_ORDER_HANDOFF_AUTHORIZED" for event in ledger.all_events()) == 1

    staged, handoff = stage(
        oms,
        current_order,
        current_decision,
        current_market,
        now=T0 + timedelta(seconds=2),
    )
    assert staged.status is OrderStatus.SUBMITTING
    assert staged.submitted_at == T0 + timedelta(seconds=1)
    assert handoff.authorized_at == T0 + timedelta(seconds=1)
    assert broker.calls == 0


def test_submitting_without_durable_external_handoff_event_is_rejected() -> None:
    oms, _, _, orders, _ = stack()
    current_order, current_decision, current_market = prepare(oms)
    orders.update(
        replace(
            current_order,
            status=OrderStatus.SUBMITTING,
            submitted_at=T0 + timedelta(seconds=1),
        )
    )
    with pytest.raises(ExternalSubmissionHandoffConflict, match="without a durable OMS"):
        stage(oms, current_order, current_decision, current_market)


def test_stage_revalidates_risk_safety_version_and_market() -> None:
    oms, _, ledger, _, safety = stack()
    current_order, current_decision, current_market = prepare(oms)
    safety.reset(now=T0 + timedelta(milliseconds=500))
    assert safety.get().kill_switch_active is False
    with pytest.raises(Exception, match="safety state changed"):
        stage(oms, current_order, current_decision, current_market)
    assert all(event.event_type != "EXTERNAL_ORDER_HANDOFF_AUTHORIZED" for event in ledger.all_events())

    oms2, _, ledger2, _, _ = stack()
    current_order2, current_decision2, _ = prepare(oms2)
    changed_market = replace(current_market, last=Decimal("10.02"))
    with pytest.raises(Exception, match="market changed"):
        stage(oms2, current_order2, current_decision2, changed_market)
    assert all(event.event_type != "EXTERNAL_ORDER_HANDOFF_AUTHORIZED" for event in ledger2.all_events())


def test_stage_blocks_kill_circuit_or_non_normal_health_before_event() -> None:
    oms, _, ledger, _, safety = stack()
    current_order, current_decision, current_market = prepare(oms)
    safety.activate(reason="test kill", now=T0 + timedelta(milliseconds=100))
    with pytest.raises(ExternalSubmissionHandoffConflict, match="kill switch"):
        stage(
            oms,
            current_order,
            replace(current_decision, safety_state_version=safety.get().version),
            current_market,
        )
    assert all(event.event_type != "EXTERNAL_ORDER_HANDOFF_AUTHORIZED" for event in ledger.all_events())

    reduced_oms, _, reduced_ledger, _, _ = stack(health_bridge=ReducedBridge())
    reduced_order, reduced_decision, reduced_market = prepare(reduced_oms)
    with pytest.raises(ExternalSubmissionHandoffConflict, match="Health mode"):
        stage(reduced_oms, reduced_order, reduced_decision, reduced_market)
    assert all(event.event_type != "EXTERNAL_ORDER_HANDOFF_AUTHORIZED" for event in reduced_ledger.all_events())


def test_changed_risk_decision_identity_cannot_reuse_validated_order() -> None:
    oms, _, _, _, _ = stack()
    current_order, current_decision, current_market = prepare(oms)
    changed = replace(current_decision, decision_id="external-handoff-risk-002")
    with pytest.raises(ExternalSubmissionHandoffConflict, match="different risk decision"):
        oms.validate_for_external_submission(
            intent=current_order.intent,
            decision=changed,
            market=current_market,
            now=T0 + timedelta(milliseconds=100),
        )


def test_handoff_object_tamper_is_rejected_by_self_hash() -> None:
    oms, _, _, _, _ = stack()
    current_order, current_decision, current_market = prepare(oms)
    _, handoff = stage(oms, current_order, current_decision, current_market)
    with pytest.raises(ValueError, match="hash mismatch"):
        replace(handoff, handoff_hash="f" * 64)
