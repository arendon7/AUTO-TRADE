from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from autotrade.brokers.paper import PaperBroker
from autotrade.domain import RiskDecisionStatus
from autotrade.health_bridge import (
    HealthBridgeConflict,
    HealthBridgePolicy,
    HealthRiskMode,
    SQLiteHealthBridgeStore,
)
from autotrade.oms import OrderManagementSystem, OrderRejectedByControlPlane
from autotrade.persistence import SQLiteEventLedger, SQLiteRuntime
from autotrade.research.health import HealthControlState, HealthEntityKind, HealthState
from autotrade.risk_state import SQLiteR2SafetyStateStore
from autotrade.safety import CapitalSafetyKernel


D = Decimal


def _sha(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


class Reader:
    def __init__(self) -> None:
        self.state: HealthControlState | None = None
        self.error: Exception | None = None

    def get(self, entity_id: str, entity_kind: HealthEntityKind) -> HealthControlState | None:
        if self.error is not None:
            raise self.error
        state = self.state
        if state is None:
            return None
        if state.entity_id != entity_id or state.entity_kind is not entity_kind:
            return None
        return state


def health(now, *, state: HealthState, version: int, assessment: str, baseline="baseline", policy="policy"):
    return HealthControlState(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        state=state,
        version=version,
        distinct_quarantine_count=(
            1 if state in {HealthState.QUARANTINED, HealthState.RETIRED} else 0
        ),
        baseline_fingerprint=_sha(baseline),
        policy_fingerprint=_sha(policy),
        last_assessment_fingerprint=_sha(assessment),
        updated_at=now,
    )


def setup_bridge(tmp_path, *, policy=None):
    runtime = SQLiteRuntime(tmp_path / "overlay.db")
    ledger = SQLiteEventLedger(runtime)
    safety_state = SQLiteR2SafetyStateStore(runtime)
    reader = Reader()
    bridge = SQLiteHealthBridgeStore(
        runtime,
        health_reader=reader,
        policy=policy or HealthBridgePolicy(),
    )
    return runtime, ledger, safety_state, reader, bridge


def test_unsynced_authoritative_quarantine_tightens_immediately_without_mutating_projection(tmp_path, now):
    _, _, _, reader, bridge = setup_bridge(tmp_path)
    reader.state = health(now, state=HealthState.HEALTHY, version=1, assessment="healthy-v1")
    persisted = bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    assert persisted.mode is HealthRiskMode.NORMAL

    reader.state = health(
        now + timedelta(seconds=1),
        state=HealthState.QUARANTINED,
        version=2,
        assessment="quarantine-v2",
    )
    control = bridge.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="",
        now=now + timedelta(seconds=1),
    )
    assert control.mode is HealthRiskMode.NO_NEW_RISK
    assert control.order_multiplier == D("0")
    assert "UNSYNCED_WORSENING" in control.reason

    # Read overlay is defensive only; it does not silently rewrite durable bridge state.
    still_persisted = bridge.get("strategy-a", HealthEntityKind.STRATEGY)
    assert still_persisted == persisted
    assert still_persisted.mode is HealthRiskMode.NORMAL


def test_unsynced_authoritative_degradation_reduces_capacity_immediately(tmp_path, now):
    _, _, _, reader, bridge = setup_bridge(
        tmp_path,
        policy=HealthBridgePolicy(degraded_risk_multiplier=D("0.35")),
    )
    reader.state = health(now, state=HealthState.HEALTHY, version=1, assessment="healthy-v1")
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    reader.state = health(
        now + timedelta(seconds=1),
        state=HealthState.DEGRADED,
        version=2,
        assessment="degraded-v2",
    )
    control = bridge.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="",
        now=now + timedelta(seconds=1),
    )
    assert control.mode is HealthRiskMode.REDUCED
    assert control.order_multiplier == D("0.35")


def test_unsynced_authoritative_recovery_never_relaxes_bridge(tmp_path, now):
    _, _, _, reader, bridge = setup_bridge(tmp_path)
    reader.state = health(now, state=HealthState.QUARANTINED, version=1, assessment="bad-v1")
    persisted = bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    assert persisted.mode is HealthRiskMode.NO_NEW_RISK

    reader.state = health(
        now + timedelta(seconds=1),
        state=HealthState.HEALTHY,
        version=2,
        assessment="healthy-v2",
    )
    control = bridge.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="",
        now=now + timedelta(seconds=1),
    )
    assert control.mode is HealthRiskMode.NO_NEW_RISK
    assert control.order_multiplier == D("0")
    assert "RECOVERY_PENDING" in control.reason


def test_authoritative_state_backward_or_same_version_conflict_fails_closed(tmp_path, now):
    _, _, _, reader, bridge = setup_bridge(tmp_path)
    reader.state = health(now, state=HealthState.DEGRADED, version=2, assessment="v2")
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )

    reader.state = health(
        now + timedelta(seconds=1),
        state=HealthState.HEALTHY,
        version=1,
        assessment="old-v1",
    )
    with pytest.raises(HealthBridgeConflict, match="moved backward"):
        bridge.effective_control(
            strategy_id="strategy-a",
            portfolio_entity_id="",
            now=now + timedelta(seconds=1),
        )

    reader.state = health(
        now + timedelta(seconds=2),
        state=HealthState.QUARANTINED,
        version=2,
        assessment="conflicting-v2",
    )
    with pytest.raises(HealthBridgeConflict, match="version identity conflict"):
        bridge.effective_control(
            strategy_id="strategy-a",
            portfolio_entity_id="",
            now=now + timedelta(seconds=2),
        )


def test_authoritative_binding_mismatch_and_reader_error_fail_closed(tmp_path, now):
    _, _, _, reader, bridge = setup_bridge(tmp_path)
    reader.state = health(now, state=HealthState.HEALTHY, version=1, assessment="healthy-v1")
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    reader.state = health(
        now + timedelta(seconds=1),
        state=HealthState.QUARANTINED,
        version=2,
        assessment="bad-binding",
        baseline="different-baseline",
    )
    with pytest.raises(HealthBridgeConflict, match="baseline fingerprint mismatch"):
        bridge.effective_control(
            strategy_id="strategy-a",
            portfolio_entity_id="",
            now=now + timedelta(seconds=1),
        )

    reader.error = RuntimeError("database read failed")
    with pytest.raises(HealthBridgeConflict, match="authoritative health read failed"):
        bridge.effective_control(
            strategy_id="strategy-a",
            portfolio_entity_id="",
            now=now + timedelta(seconds=2),
        )


def test_missing_authoritative_health_after_projection_exists_is_no_new_risk(tmp_path, now):
    _, _, _, reader, bridge = setup_bridge(tmp_path)
    reader.state = health(now, state=HealthState.HEALTHY, version=1, assessment="healthy-v1")
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    reader.state = None
    control = bridge.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="",
        now=now + timedelta(seconds=1),
    )
    assert control.mode is HealthRiskMode.NO_NEW_RISK
    assert "MISSING_STRATEGY_AUTHORITATIVE_HEALTH" in control.reason


def test_stale_or_future_authoritative_health_is_no_new_risk_even_with_fresh_projection(tmp_path, now):
    policy = HealthBridgePolicy(max_state_age_seconds=5)
    _, _, _, reader, bridge = setup_bridge(tmp_path, policy=policy)
    reader.state = health(now, state=HealthState.HEALTHY, version=1, assessment="healthy-v1")
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )

    stale = bridge.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="",
        now=now + timedelta(seconds=6),
    )
    assert stale.blocks_new_risk
    assert "STALE_STRATEGY_AUTHORITATIVE_HEALTH" in stale.reason

    reader.state = health(
        now + timedelta(seconds=10),
        state=HealthState.HEALTHY,
        version=2,
        assessment="future-v2",
    )
    future = bridge.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="",
        now=now + timedelta(seconds=7),
    )
    assert future.blocks_new_risk
    assert "FUTURE_STRATEGY_AUTHORITATIVE_HEALTH" in future.reason


def test_safety_rejects_unsynced_worsening_without_waiting_for_safety_version_bump(
    tmp_path, now, limits, market, empty_portfolio, market_buy_intent
):
    _, ledger, safety_state, reader, bridge = setup_bridge(tmp_path)
    reader.state = health(now, state=HealthState.HEALTHY, version=1, assessment="healthy-v1")
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
    approved = safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=now,
    )
    assert approved.status is RiskDecisionStatus.APPROVED
    version = safety_state.get().version

    reader.state = health(
        now + timedelta(milliseconds=100),
        state=HealthState.QUARANTINED,
        version=2,
        assessment="quarantine-v2",
    )
    rejected = safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=now + timedelta(milliseconds=100),
    )
    assert rejected.status is RiskDecisionStatus.REJECTED
    assert rejected.reason_code == "HEALTH_NO_NEW_RISK"
    assert safety_state.get().version == version


def test_oms_submit_time_overlay_blocks_unsynced_worsening_even_when_safety_version_is_unchanged(
    tmp_path, now, limits, market, empty_portfolio, market_buy_intent
):
    _, ledger, safety_state, reader, bridge = setup_bridge(tmp_path)
    reader.state = health(now, state=HealthState.HEALTHY, version=1, assessment="healthy-v1")
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    long_ttl = replace(limits, decision_ttl_ms=5000)
    safety = CapitalSafetyKernel(
        long_ttl,
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

    reader.state = health(
        now + timedelta(milliseconds=100),
        state=HealthState.QUARANTINED,
        version=2,
        assessment="quarantine-v2",
    )
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
            now=now + timedelta(milliseconds=100),
        )
    assert safety_state.get().version == version
    assert broker.submission_count == 0
