from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import sqlite3

import pytest

from autotrade.health_bridge import (
    HealthBridgeConflict,
    HealthBridgeEvidenceMissing,
    HealthBridgePolicy,
    HealthBridgeRecoveryRejected,
    HealthRiskMode,
    SQLiteHealthBridgeStore,
)
from autotrade.persistence import SQLiteEventLedger, SQLiteRuntime
from autotrade.research.health import HealthControlState, HealthEntityKind, HealthState
from autotrade.risk_state import SQLiteR2SafetyStateStore


D = Decimal


class Reader:
    def __init__(self) -> None:
        self.states: dict[tuple[HealthEntityKind, str], HealthControlState] = {}

    def put(self, state: HealthControlState) -> None:
        self.states[(state.entity_kind, state.entity_id)] = state

    def get(self, entity_id: str, entity_kind: HealthEntityKind) -> HealthControlState | None:
        return self.states.get((entity_kind, entity_id))


def health_state(
    now,
    *,
    state=HealthState.HEALTHY,
    version=1,
    entity_id="strategy-a",
    kind=HealthEntityKind.STRATEGY,
    baseline="a",
    policy="b",
    assessment="c",
):
    return HealthControlState(
        entity_id=entity_id,
        entity_kind=kind,
        state=state,
        version=version,
        distinct_quarantine_count=(1 if state in {HealthState.QUARANTINED, HealthState.RETIRED} else 0),
        baseline_fingerprint=baseline * 64,
        policy_fingerprint=policy * 64,
        last_assessment_fingerprint=assessment * 64,
        updated_at=now,
    )


def bridge(tmp_path, *, policy=None):
    runtime = SQLiteRuntime(tmp_path / "bridge.db")
    SQLiteR2SafetyStateStore(runtime)
    reader = Reader()
    store = SQLiteHealthBridgeStore(runtime, health_reader=reader, policy=policy)
    return runtime, reader, store


def test_policy_is_strict():
    assert HealthBridgePolicy().degraded_risk_multiplier == D("0.50")
    for bad in (D("0"), D("1"), D("NaN")):
        with pytest.raises(ValueError, match="degraded_risk_multiplier"):
            HealthBridgePolicy(degraded_risk_multiplier=bad)
    with pytest.raises(ValueError, match="max_state_age_seconds"):
        HealthBridgePolicy(max_state_age_seconds=0)


def test_missing_authoritative_health_cannot_be_synced(tmp_path, now):
    _, _, store = bridge(tmp_path)
    with pytest.raises(HealthBridgeEvidenceMissing, match="missing authoritative health state"):
        store.sync_from_health(
            entity_id="strategy-a",
            entity_kind=HealthEntityKind.STRATEGY,
            now=now,
        )


def test_automatic_sync_only_tightens_and_replay_is_idempotent(tmp_path, now):
    runtime, reader, store = bridge(tmp_path)
    safety = SQLiteR2SafetyStateStore(runtime)

    degraded = health_state(now, state=HealthState.DEGRADED, version=1)
    reader.put(degraded)
    first = store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    assert first.mode is HealthRiskMode.REDUCED
    assert first.risk_multiplier == D("0.50")
    assert safety.get().version == 1

    replay = store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now + timedelta(seconds=1),
    )
    assert replay == first
    assert safety.get().version == 1

    healthy = health_state(
        now + timedelta(seconds=2),
        state=HealthState.HEALTHY,
        version=2,
        assessment="d",
    )
    reader.put(healthy)
    second = store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now + timedelta(seconds=2),
    )
    assert second.mode is HealthRiskMode.REDUCED
    assert second.risk_multiplier == D("0.50")
    assert second.health_state_version == 2
    assert safety.get().version == 2

    reader.put(degraded)
    old_replay = store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now + timedelta(seconds=3),
    )
    assert old_replay == second
    assert safety.get().version == 2


def test_quarantine_blocks_new_risk_and_recovery_is_explicit_one_step(tmp_path, now):
    runtime, reader, store = bridge(tmp_path)
    safety = SQLiteR2SafetyStateStore(runtime)

    quarantined = health_state(now, state=HealthState.QUARANTINED, version=1)
    reader.put(quarantined)
    blocked = store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    assert blocked.mode is HealthRiskMode.NO_NEW_RISK
    assert blocked.risk_multiplier == D("0")

    healthy = health_state(
        now + timedelta(seconds=1),
        state=HealthState.HEALTHY,
        version=2,
        assessment="d",
    )
    reader.put(healthy)
    still_blocked = store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now + timedelta(seconds=1),
    )
    assert still_blocked.mode is HealthRiskMode.NO_NEW_RISK

    reduced = store.acknowledge_recovery(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        confirmed_by="risk-officer",
        now=now + timedelta(seconds=2),
    )
    assert reduced.mode is HealthRiskMode.REDUCED
    normal = store.acknowledge_recovery(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        confirmed_by="risk-officer",
        now=now + timedelta(seconds=3),
    )
    assert normal.mode is HealthRiskMode.NORMAL
    assert normal.risk_multiplier == D("1")
    assert safety.get().version == 4


def test_recovery_rejects_stale_or_nonimproving_health(tmp_path, now):
    policy = HealthBridgePolicy(max_state_age_seconds=5)
    _, reader, store = bridge(tmp_path, policy=policy)
    quarantined = health_state(now, state=HealthState.QUARANTINED, version=1)
    reader.put(quarantined)
    store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    with pytest.raises(HealthBridgeRecoveryRejected, match="less restrictive"):
        store.acknowledge_recovery(
            entity_id="strategy-a",
            entity_kind=HealthEntityKind.STRATEGY,
            confirmed_by="risk-officer",
            now=now + timedelta(seconds=1),
        )

    healthy = health_state(
        now,
        state=HealthState.HEALTHY,
        version=2,
        assessment="d",
    )
    reader.put(healthy)
    with pytest.raises(HealthBridgeRecoveryRejected, match="fresh"):
        store.acknowledge_recovery(
            entity_id="strategy-a",
            entity_kind=HealthEntityKind.STRATEGY,
            confirmed_by="risk-officer",
            now=now + timedelta(seconds=6),
        )


def test_binding_cannot_change_implicitly(tmp_path, now):
    _, reader, store = bridge(tmp_path)
    reader.put(health_state(now, state=HealthState.DEGRADED, version=1))
    store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )

    reader.put(
        health_state(
            now + timedelta(seconds=1),
            state=HealthState.HEALTHY,
            version=2,
            baseline="e",
            assessment="d",
        )
    )
    with pytest.raises(HealthBridgeConflict, match="baseline fingerprint mismatch"):
        store.sync_from_health(
            entity_id="strategy-a",
            entity_kind=HealthEntityKind.STRATEGY,
            now=now + timedelta(seconds=1),
        )


def test_effective_control_is_fail_closed_for_missing_and_stale_required_state(tmp_path, now):
    policy = HealthBridgePolicy(max_state_age_seconds=5, require_strategy_state=True)
    _, reader, store = bridge(tmp_path, policy=policy)
    missing = store.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="",
        now=now,
    )
    assert missing.blocks_new_risk
    assert missing.order_multiplier == D("0")
    assert "MISSING_STRATEGY_HEALTH_CONTROL" in missing.reason

    reader.put(health_state(now, state=HealthState.HEALTHY, version=1))
    store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    fresh = store.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="",
        now=now + timedelta(seconds=5),
    )
    assert fresh.mode is HealthRiskMode.NORMAL
    stale = store.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="",
        now=now + timedelta(seconds=6),
    )
    assert stale.blocks_new_risk
    assert "STALE_STRATEGY_HEALTH_CONTROL" in stale.reason


def test_strategy_and_portfolio_controls_compose_stricter_state(tmp_path, now):
    policy = HealthBridgePolicy(
        degraded_risk_multiplier=D("0.40"),
        require_strategy_state=True,
        require_portfolio_state=True,
    )
    _, reader, store = bridge(tmp_path, policy=policy)
    reader.put(health_state(now, state=HealthState.DEGRADED, version=1))
    reader.put(
        health_state(
            now,
            state=HealthState.QUARANTINED,
            version=1,
            entity_id="portfolio-main",
            kind=HealthEntityKind.PORTFOLIO,
            assessment="d",
        )
    )
    store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    store.sync_from_health(
        entity_id="portfolio-main",
        entity_kind=HealthEntityKind.PORTFOLIO,
        now=now,
    )
    control = store.effective_control(
        strategy_id="strategy-a",
        portfolio_entity_id="portfolio-main",
        now=now,
    )
    assert control.mode is HealthRiskMode.NO_NEW_RISK
    assert control.strategy_multiplier == D("0.40")
    assert control.portfolio_multiplier == D("0")
    assert control.order_multiplier == D("0")


def test_bridge_state_hash_tamper_fails_closed(tmp_path, now):
    runtime, reader, store = bridge(tmp_path)
    reader.put(health_state(now, state=HealthState.DEGRADED, version=1))
    store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE health_bridge_state SET mode='NORMAL' WHERE entity_kind='STRATEGY' AND entity_id='strategy-a'"
        )
    finally:
        conn.close()
    with pytest.raises(HealthBridgeConflict, match="state hash mismatch"):
        store.get("strategy-a", HealthEntityKind.STRATEGY)


def test_distinct_bridge_changes_are_ledgered_and_safety_versioned(tmp_path, now):
    runtime, reader, store = bridge(tmp_path)
    reader.put(health_state(now, state=HealthState.HEALTHY, version=1))
    store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    reader.put(
        health_state(
            now + timedelta(seconds=1),
            state=HealthState.DEGRADED,
            version=2,
            assessment="d",
        )
    )
    store.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now + timedelta(seconds=1),
    )
    events = SQLiteEventLedger(runtime).all_events()
    bridge_events = [event for event in events if event.event_type == "HEALTH_BRIDGE_APPLIED"]
    assert len(bridge_events) == 2
    assert SQLiteR2SafetyStateStore(runtime).get().version == 2
