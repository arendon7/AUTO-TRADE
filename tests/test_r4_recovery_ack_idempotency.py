from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from autotrade.health_bridge import (
    HealthBridgeConflict,
    HealthBridgePolicy,
    HealthBridgeRecoveryRejected,
    HealthRiskMode,
    SQLiteHealthBridgeStore,
)
from autotrade.persistence import SQLiteEventLedger, SQLiteRuntime
from autotrade.research.health import (
    HealthBaselineSeries,
    HealthEntityKind,
    HealthObservationSeries,
    HealthPolicy,
    HealthReturnObservation,
    HealthState,
    HealthStateConflict,
    HealthControlState,
    SQLiteHealthStateStore,
    assess_health,
    build_health_baseline,
)
from autotrade.research.portfolio_dependence import CalibrationPhase
from autotrade.risk_state import SQLiteR2SafetyStateStore


D = Decimal


def _sha(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _observations(now, values, *, offset=0):
    return tuple(
        HealthReturnObservation(
            occurred_at=now + timedelta(minutes=offset + index),
            available_at=now + timedelta(minutes=offset + index),
            value=D(value),
        )
        for index, value in enumerate(values)
    )


def _baseline(now):
    return build_health_baseline(
        HealthBaselineSeries(
            entity_id="strategy-a",
            entity_kind=HealthEntityKind.STRATEGY,
            phase=CalibrationPhase.TRAIN,
            source_hash=_sha("baseline"),
            observations=_observations(
                now,
                ("0.01", "0.03", "0.02", "0.04", "0.02"),
            ),
        )
    )


def _window(now, *, healthy: bool, source: str):
    values = (
        ("0.011", "0.029", "0.021", "0.039", "0.020")
        if healthy
        else ("-0.010", "0.002", "-0.005", "0.001", "-0.003")
    )
    return HealthObservationSeries(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        source_hash=_sha(source),
        observations=_observations(now, values, offset=10),
    )


def _health_policy():
    return HealthPolicy(
        min_observations=5,
        degraded_mean_loss_fraction=D("0.25"),
        quarantined_mean_loss_fraction=D("0.60"),
        degraded_volatility_ratio=D("1.5"),
        quarantined_volatility_ratio=D("2.5"),
        retire_after_distinct_quarantines=3,
        max_observation_age_seconds=3600,
    )


def _quarantine_health(store, baseline, bad_window, policy, now):
    assessment = assess_health(baseline, bad_window, policy, now=now)
    assert assessment.proposed_state is HealthState.QUARANTINED
    state = store.apply_assessment(assessment, policy, now=now)
    assert state.state is HealthState.QUARANTINED
    return state


def test_health_duplicate_recovery_request_cannot_relax_twice(tmp_path, now):
    store = SQLiteHealthStateStore(tmp_path / "health-retry.db")
    baseline = _baseline(now)
    policy = _health_policy()
    bad = _window(now, healthy=False, source="bad")
    healthy = _window(now, healthy=True, source="healthy")
    t = now + timedelta(minutes=20)
    _quarantine_health(store, baseline, bad, policy, t)

    first = store.acknowledge_recovery(
        baseline,
        healthy,
        policy,
        recovery_id="health-ack-1",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=1),
    )
    assert first.state is HealthState.DEGRADED

    replay = store.acknowledge_recovery(
        baseline,
        healthy,
        policy,
        recovery_id="health-ack-1",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=30),
    )
    assert replay == first
    assert replay.version == first.version
    assert replay.state is HealthState.DEGRADED

    explicit_second = store.acknowledge_recovery(
        baseline,
        healthy,
        policy,
        recovery_id="health-ack-2",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=31),
    )
    assert explicit_second.state is HealthState.HEALTHY
    assert explicit_second.version == first.version + 1


def test_health_recovery_id_conflicting_reuse_fails_closed(tmp_path, now):
    store = SQLiteHealthStateStore(tmp_path / "health-conflict.db")
    baseline = _baseline(now)
    policy = _health_policy()
    bad = _window(now, healthy=False, source="bad")
    healthy = _window(now, healthy=True, source="healthy")
    t = now + timedelta(minutes=20)
    _quarantine_health(store, baseline, bad, policy, t)

    store.acknowledge_recovery(
        baseline,
        healthy,
        policy,
        recovery_id="health-conflict-1",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=1),
    )
    with pytest.raises(HealthStateConflict, match="recovery_id reused"):
        store.acknowledge_recovery(
            baseline,
            healthy,
            policy,
            recovery_id="health-conflict-1",
            confirmed_by="different-officer",
            now=t + timedelta(seconds=2),
        )


def test_health_ack_recorded_while_healthy_cannot_be_replayed_after_future_degradation(tmp_path, now):
    store = SQLiteHealthStateStore(tmp_path / "health-old-ack.db")
    baseline = _baseline(now)
    policy = _health_policy()
    healthy_window = _window(now, healthy=True, source="healthy-initial")
    t = now + timedelta(minutes=20)
    healthy_assessment = assess_health(baseline, healthy_window, policy, now=t)
    state = store.apply_assessment(healthy_assessment, policy, now=t)
    assert state.state is HealthState.HEALTHY

    no_op = store.acknowledge_recovery(
        baseline,
        healthy_window,
        policy,
        recovery_id="health-old-ack",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=1),
    )
    assert no_op == state

    bad_window = _window(now, healthy=False, source="later-bad")
    bad_assessment = assess_health(
        baseline,
        bad_window,
        policy,
        now=t + timedelta(seconds=2),
    )
    degraded = store.apply_assessment(
        bad_assessment,
        policy,
        now=t + timedelta(seconds=2),
    )
    assert degraded.state is HealthState.QUARANTINED

    replay = store.acknowledge_recovery(
        baseline,
        healthy_window,
        policy,
        recovery_id="health-old-ack",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=3),
    )
    assert replay == degraded
    assert replay.state is HealthState.QUARANTINED


class _Reader:
    def __init__(self) -> None:
        self.state: HealthControlState | None = None

    def get(self, entity_id: str, entity_kind: HealthEntityKind) -> HealthControlState | None:
        state = self.state
        if state is None:
            return None
        if state.entity_id != entity_id or state.entity_kind is not entity_kind:
            return None
        return state


def _bridge_health(now, *, state: HealthState, version: int, assessment: str):
    return HealthControlState(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        state=state,
        version=version,
        distinct_quarantine_count=(
            1 if state in {HealthState.QUARANTINED, HealthState.RETIRED} else 0
        ),
        baseline_fingerprint=_sha("bridge-baseline"),
        policy_fingerprint=_sha("bridge-policy"),
        last_assessment_fingerprint=_sha(assessment),
        updated_at=now,
    )


def _bridge(tmp_path, *, policy=None):
    runtime = SQLiteRuntime(tmp_path / "bridge-retry.db")
    safety = SQLiteR2SafetyStateStore(runtime)
    reader = _Reader()
    bridge = SQLiteHealthBridgeStore(
        runtime,
        health_reader=reader,
        policy=policy or HealthBridgePolicy(),
    )
    return runtime, safety, reader, bridge


def test_bridge_duplicate_recovery_request_relaxes_once_and_bumps_safety_once(tmp_path, now):
    runtime, safety, reader, bridge = _bridge(tmp_path)
    reader.state = _bridge_health(
        now,
        state=HealthState.QUARANTINED,
        version=1,
        assessment="quarantined",
    )
    blocked = bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    assert blocked.mode is HealthRiskMode.NO_NEW_RISK
    assert safety.get().version == 1

    reader.state = _bridge_health(
        now + timedelta(seconds=1),
        state=HealthState.HEALTHY,
        version=2,
        assessment="healthy",
    )
    first = bridge.acknowledge_recovery(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        recovery_id="bridge-ack-1",
        confirmed_by="risk-officer",
        now=now + timedelta(seconds=1),
    )
    assert first.mode is HealthRiskMode.REDUCED
    after_first_safety_version = safety.get().version
    assert after_first_safety_version == 2

    replay = bridge.acknowledge_recovery(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        recovery_id="bridge-ack-1",
        confirmed_by="risk-officer",
        now=now + timedelta(seconds=20),
    )
    assert replay == first
    assert replay.bridge_version == first.bridge_version
    assert safety.get().version == after_first_safety_version

    ledger = SQLiteEventLedger(runtime)
    recovery_events = [
        event
        for event in ledger.all_events()
        if event.event_type == "HEALTH_BRIDGE_RECOVERY_ACKNOWLEDGED"
    ]
    assert len(recovery_events) == 1

    second = bridge.acknowledge_recovery(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        recovery_id="bridge-ack-2",
        confirmed_by="risk-officer",
        now=now + timedelta(seconds=21),
    )
    assert second.mode is HealthRiskMode.NORMAL
    assert safety.get().version == after_first_safety_version + 1


def test_bridge_recovery_id_conflicting_reuse_fails_closed(tmp_path, now):
    _, _, reader, bridge = _bridge(tmp_path)
    reader.state = _bridge_health(
        now,
        state=HealthState.QUARANTINED,
        version=1,
        assessment="quarantined",
    )
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    reader.state = _bridge_health(
        now + timedelta(seconds=1),
        state=HealthState.HEALTHY,
        version=2,
        assessment="healthy",
    )
    bridge.acknowledge_recovery(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        recovery_id="bridge-conflict-1",
        confirmed_by="risk-officer",
        now=now + timedelta(seconds=1),
    )
    with pytest.raises(HealthBridgeConflict, match="recovery_id reused"):
        bridge.acknowledge_recovery(
            entity_id="strategy-a",
            entity_kind=HealthEntityKind.STRATEGY,
            recovery_id="bridge-conflict-1",
            confirmed_by="different-officer",
            now=now + timedelta(seconds=2),
        )


def test_bridge_existing_recovery_retry_can_be_stale_but_new_request_cannot(tmp_path, now):
    policy = HealthBridgePolicy(max_state_age_seconds=5)
    _, safety, reader, bridge = _bridge(tmp_path, policy=policy)
    reader.state = _bridge_health(
        now,
        state=HealthState.QUARANTINED,
        version=1,
        assessment="quarantined",
    )
    bridge.sync_from_health(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        now=now,
    )
    reader.state = _bridge_health(
        now + timedelta(seconds=1),
        state=HealthState.HEALTHY,
        version=2,
        assessment="healthy",
    )
    first = bridge.acknowledge_recovery(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        recovery_id="bridge-stale-1",
        confirmed_by="risk-officer",
        now=now + timedelta(seconds=1),
    )
    safety_version = safety.get().version

    replay = bridge.acknowledge_recovery(
        entity_id="strategy-a",
        entity_kind=HealthEntityKind.STRATEGY,
        recovery_id="bridge-stale-1",
        confirmed_by="risk-officer",
        now=now + timedelta(seconds=20),
    )
    assert replay == first
    assert safety.get().version == safety_version

    with pytest.raises(HealthBridgeRecoveryRejected, match="fresh"):
        bridge.acknowledge_recovery(
            entity_id="strategy-a",
            entity_kind=HealthEntityKind.STRATEGY,
            recovery_id="bridge-stale-new",
            confirmed_by="risk-officer",
            now=now + timedelta(seconds=20),
        )
