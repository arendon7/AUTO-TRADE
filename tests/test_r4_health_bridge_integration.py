from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import sqlite3

import pytest

from autotrade.bootstrap import build_durable_paper_core
from autotrade.brokers.paper import PaperBroker
from autotrade.domain import OrderIntent, PortfolioSnapshot, RiskDecisionStatus, Side
from autotrade.health_bridge import HealthBridgePolicy, SQLiteHealthBridgeStore
from autotrade.oms import OrderManagementSystem, OrderRejectedByControlPlane
from autotrade.persistence import SQLiteEventLedger, SQLiteRuntime
from autotrade.research.health import HealthControlState, HealthEntityKind, HealthState
from autotrade.risk_state import SQLiteR2SafetyStateStore
from autotrade.safety import CapitalSafetyKernel


D = Decimal


class Reader:
    def __init__(self) -> None:
        self.state: HealthControlState | None = None

    def get(self, entity_id: str, entity_kind: HealthEntityKind) -> HealthControlState | None:
        if self.state is None:
            return None
        if self.state.entity_id != entity_id or self.state.entity_kind is not entity_kind:
            return None
        return self.state


def health(now, state: HealthState, version: int, *, assessment="c") -> HealthControlState:
    return HealthControlState(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        state=state,
        version=version,
        distinct_quarantine_count=(1 if state in {HealthState.QUARANTINED, HealthState.RETIRED} else 0),
        baseline_fingerprint="a" * 64,
        policy_fingerprint="b" * 64,
        last_assessment_fingerprint=assessment * 64,
        updated_at=now,
    )


def setup_control(tmp_path, *, policy=None):
    runtime = SQLiteRuntime(tmp_path / "integration.db")
    ledger = SQLiteEventLedger(runtime)
    safety_state = SQLiteR2SafetyStateStore(runtime)
    reader = Reader()
    bridge = SQLiteHealthBridgeStore(runtime, health_reader=reader, policy=policy)
    return runtime, ledger, safety_state, reader, bridge


def test_enabled_bridge_missing_required_strategy_health_rejects_new_risk(
    tmp_path, now, limits, market, empty_portfolio, market_buy_intent
):
    _, ledger, safety_state, _, bridge = setup_control(tmp_path)
    safety = CapitalSafetyKernel(
        limits,
        ledger,
        state_store=safety_state,
        health_bridge=bridge,
    )
    decision = safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=now,
    )
    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.reason_code == "HEALTH_NO_NEW_RISK"
    assert "MISSING_STRATEGY_HEALTH_CONTROL" in decision.reason_detail


def test_degraded_health_enforces_reduced_order_cap_without_replacing_hard_limits(
    tmp_path, now, limits, market, empty_portfolio, market_buy_intent
):
    _, ledger, safety_state, reader, bridge = setup_control(tmp_path)
    reader.state = health(now, HealthState.DEGRADED, 1)
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    safety = CapitalSafetyKernel(
        limits,
        ledger,
        state_store=safety_state,
        health_bridge=bridge,
    )

    small = safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=now,
    )
    assert small.status is RiskDecisionStatus.APPROVED

    large_intent = replace(
        market_buy_intent,
        intent_id="intent-large",
        idempotency_key="idem-large",
        quantity=D("60"),
    )
    large = safety.evaluate(
        intent=large_intent,
        market=market,
        portfolio=empty_portfolio,
        now=now,
    )
    assert large.status is RiskDecisionStatus.REJECTED
    assert large.reason_code == "HEALTH_MAX_ORDER_NOTIONAL"


def test_quarantine_blocks_new_risk_but_keeps_true_risk_reduction_available(
    tmp_path, now, limits, market, market_buy_intent
):
    _, ledger, safety_state, reader, bridge = setup_control(tmp_path)
    reader.state = health(now, HealthState.QUARANTINED, 1)
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    safety = CapitalSafetyKernel(
        limits,
        ledger,
        state_store=safety_state,
        health_bridge=bridge,
    )

    exposed = PortfolioSnapshot(
        snapshot_id="exposed-v1",
        equity=D("100000"),
        gross_exposure=D("5000"),
        net_exposure=D("5000"),
        daily_pnl=D("0"),
        drawdown=D("0"),
        open_orders=0,
        signed_position_notional_by_symbol={"TEST-USD": D("5000")},
        strategy_gross_exposure={"strategy-a": D("5000")},
        strategy_signed_position_notional_by_symbol={
            "strategy-a": {"TEST-USD": D("5000")}
        },
        reconciliation_ok=True,
        broker_state_known=True,
    )
    blocked = safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=exposed,
        now=now,
    )
    assert blocked.status is RiskDecisionStatus.REJECTED
    assert blocked.reason_code == "HEALTH_NO_NEW_RISK"

    reducing = replace(
        market_buy_intent,
        intent_id="intent-reduce",
        idempotency_key="idem-reduce",
        side=Side.SELL,
        quantity=D("10"),
    )
    exit_decision = safety.evaluate(
        intent=reducing,
        market=market,
        portfolio=exposed,
        now=now,
    )
    assert exit_decision.status is RiskDecisionStatus.APPROVED
    assert exit_decision.risk_reducing is True


def test_bridge_change_invalidates_previously_approved_decision_before_broker_submit(
    tmp_path, now, limits, market, empty_portfolio, market_buy_intent
):
    _, ledger, safety_state, reader, bridge = setup_control(tmp_path)
    reader.state = health(now, HealthState.HEALTHY, 1)
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    safety = CapitalSafetyKernel(
        limits,
        ledger,
        state_store=safety_state,
        health_bridge=bridge,
    )
    decision = safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=now,
    )
    assert decision.status is RiskDecisionStatus.APPROVED
    approved_version = decision.safety_state_version

    reader.state = health(
        now + timedelta(milliseconds=100),
        HealthState.QUARANTINED,
        2,
        assessment="d",
    )
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now + timedelta(milliseconds=100),
    )
    assert safety_state.get().version > approved_version

    broker = PaperBroker()
    oms = OrderManagementSystem(
        broker=broker,
        ledger=ledger,
        safety_state_store=safety_state,
        health_bridge=bridge,
    )
    with pytest.raises(OrderRejectedByControlPlane, match="safety state changed"):
        oms.submit(
            intent=market_buy_intent,
            decision=decision,
            market=market,
            now=now + timedelta(milliseconds=100),
        )
    assert broker.submission_count == 0


def test_submit_time_staleness_closes_temporal_gap_even_without_version_change(
    tmp_path, now, limits, market, empty_portfolio, market_buy_intent
):
    policy = HealthBridgePolicy(max_state_age_seconds=1)
    _, ledger, safety_state, reader, bridge = setup_control(tmp_path, policy=policy)
    reader.state = health(now, HealthState.HEALTHY, 1)
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    long_ttl_limits = replace(limits, decision_ttl_ms=5000)
    safety = CapitalSafetyKernel(
        long_ttl_limits,
        ledger,
        state_store=safety_state,
        health_bridge=bridge,
    )
    decision = safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=now,
    )
    assert decision.status is RiskDecisionStatus.APPROVED
    version = safety_state.get().version

    broker = PaperBroker()
    oms = OrderManagementSystem(
        broker=broker,
        ledger=ledger,
        safety_state_store=safety_state,
        health_bridge=bridge,
    )
    with pytest.raises(OrderRejectedByControlPlane, match="health control blocks new risk"):
        oms.submit(
            intent=market_buy_intent,
            decision=decision,
            market=market,
            now=now + timedelta(seconds=2),
        )
    assert safety_state.get().version == version
    assert broker.submission_count == 0


def test_corrupt_bridge_state_is_a_rejected_risk_decision_not_an_approval(
    tmp_path, now, limits, market, empty_portfolio, market_buy_intent
):
    runtime, ledger, safety_state, reader, bridge = setup_control(tmp_path)
    reader.state = health(now, HealthState.DEGRADED, 1)
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE health_bridge_state SET risk_multiplier='0.9' WHERE entity_kind='STRATEGY' AND entity_id='strategy-a'"
        )
    finally:
        conn.close()
    safety = CapitalSafetyKernel(
        limits,
        ledger,
        state_store=safety_state,
        health_bridge=bridge,
    )
    decision = safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=now,
    )
    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.reason_code == "HEALTH_CONTROL_UNAVAILABLE"


def test_required_portfolio_health_without_portfolio_identity_is_fail_closed(tmp_path, now):
    policy = HealthBridgePolicy(
        require_strategy_state=False,
        require_portfolio_state=True,
    )
    _, _, _, _, bridge = setup_control(tmp_path, policy=policy)
    control = bridge.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="",
        now=now,
    )
    assert control.blocks_new_risk
    assert "MISSING_PORTFOLIO_HEALTH_ID" in control.reason


def test_bootstrap_bridge_is_explicit_opt_in_and_missing_health_blocks_when_enabled(
    tmp_path, now, limits, market, empty_portfolio, market_buy_intent
):
    disabled = build_durable_paper_core(
        db_path=tmp_path / "disabled.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=now,
    )
    assert disabled.health_bridge is None
    assert disabled.health_state_store is None

    enabled = build_durable_paper_core(
        db_path=tmp_path / "enabled.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=now,
        enable_health_bridge=True,
    )
    assert enabled.health_bridge is not None
    assert enabled.health_state_store is not None
    decision = enabled.safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=enabled.portfolio_store.get().snapshot,
        now=now,
    )
    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.reason_code == "HEALTH_NO_NEW_RISK"


def test_portfolio_health_identity_cannot_be_configured_while_bridge_disabled(
    tmp_path, now, limits, empty_portfolio
):
    with pytest.raises(ValueError, match="enable_health_bridge"):
        build_durable_paper_core(
            db_path=tmp_path / "invalid-config.db",
            limits=limits,
            initial_portfolio=empty_portfolio,
            now=now,
            portfolio_health_entity_id="portfolio-main",
        )
