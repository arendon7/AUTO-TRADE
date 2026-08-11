from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import sqlite3

import pytest

from autotrade.research.health import (
    HealthBaselineSeries,
    HealthEntityKind,
    HealthObservationSeries,
    HealthPolicy,
    HealthReturnObservation,
    HealthState,
    HealthStateConflict,
    SQLiteHealthStateStore,
    assess_health,
    build_health_baseline,
)
from autotrade.research.portfolio_dependence import CalibrationPhase


D = Decimal


def _sha(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _obs(now, values, *, offset=0):
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
            observations=_obs(now, ("0.01", "0.03", "0.02", "0.04", "0.02")),
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
        observations=_obs(now, values, offset=10),
    )


def _policy():
    return HealthPolicy(
        min_observations=5,
        degraded_mean_loss_fraction=D("0.25"),
        quarantined_mean_loss_fraction=D("0.60"),
        degraded_volatility_ratio=D("1.5"),
        quarantined_volatility_ratio=D("2.5"),
        retire_after_distinct_quarantines=3,
        max_observation_age_seconds=3600,
    )


def _store_with_first_recovery(tmp_path, now):
    path = tmp_path / "health-ack-chain.db"
    store = SQLiteHealthStateStore(path)
    baseline = _baseline(now)
    policy = _policy()
    t = now + timedelta(minutes=20)
    bad = _window(now, healthy=False, source="bad")
    assessment = assess_health(baseline, bad, policy, now=t)
    assert assessment.proposed_state is HealthState.QUARANTINED
    state = store.apply_assessment(assessment, policy, now=t)
    assert state.state is HealthState.QUARANTINED
    healthy = _window(now, healthy=True, source="healthy")
    recovered = store.acknowledge_recovery(
        baseline,
        healthy,
        policy,
        recovery_id="ack-1",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=1),
    )
    assert recovered.state is HealthState.DEGRADED
    assert recovered.recovery_ack_head != "GENESIS"
    return path, store, baseline, healthy, policy, t, recovered


def test_ack_chain_is_anchored_in_hash_protected_health_state(tmp_path, now):
    path, store, _, _, _, _, recovered = _store_with_first_recovery(tmp_path, now)
    reread = store.get("strategy-a", HealthEntityKind.STRATEGY)
    assert reread == recovered

    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT ack_seq,previous_ack_hash,ack_hash FROM health_recovery_acks_v3"
        ).fetchone()
        assert row[0] == 1
        assert row[1] == "GENESIS"
        assert row[2] == recovered.recovery_ack_head
    finally:
        conn.close()


def test_deleting_ack_row_breaks_state_read_before_replay_can_relax_again(tmp_path, now):
    path, store, baseline, healthy, policy, t, recovered = _store_with_first_recovery(tmp_path, now)
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM health_recovery_acks_v3 WHERE recovery_id='ack-1'")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(HealthStateConflict, match="chain head"):
        store.get("strategy-a", HealthEntityKind.STRATEGY)
    with pytest.raises(HealthStateConflict, match="chain head"):
        store.acknowledge_recovery(
            baseline,
            healthy,
            policy,
            recovery_id="ack-1",
            confirmed_by="risk-officer",
            now=t + timedelta(seconds=2),
        )


def test_mutating_ack_payload_breaks_chain_hash(tmp_path, now):
    path, store, _, _, _, _, _ = _store_with_first_recovery(tmp_path, now)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE health_recovery_acks_v3 SET confirmed_by='attacker' WHERE recovery_id='ack-1'"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(HealthStateConflict, match="chain hash mismatch"):
        store.get("strategy-a", HealthEntityKind.STRATEGY)


def test_sequence_gap_or_reorder_is_detected(tmp_path, now):
    path, store, baseline, healthy, policy, t, first = _store_with_first_recovery(tmp_path, now)
    second = store.acknowledge_recovery(
        baseline,
        healthy,
        policy,
        recovery_id="ack-2",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=2),
    )
    assert second.state is HealthState.HEALTHY

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE health_recovery_acks_v3 SET ack_seq=3 WHERE recovery_id='ack-2'"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(HealthStateConflict, match="sequence gap/reorder"):
        store.get("strategy-a", HealthEntityKind.STRATEGY)


def test_healthy_noop_ack_is_still_anchored_and_versions_state(tmp_path, now):
    path = tmp_path / "healthy-noop.db"
    store = SQLiteHealthStateStore(path)
    baseline = _baseline(now)
    policy = _policy()
    healthy = _window(now, healthy=True, source="healthy-initial")
    t = now + timedelta(minutes=20)
    assessment = assess_health(baseline, healthy, policy, now=t)
    state = store.apply_assessment(assessment, policy, now=t)
    assert state.state is HealthState.HEALTHY
    assert state.recovery_ack_head == "GENESIS"

    acknowledged = store.acknowledge_recovery(
        baseline,
        healthy,
        policy,
        recovery_id="healthy-ack",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=1),
    )
    assert acknowledged.state is HealthState.HEALTHY
    assert acknowledged.version == state.version + 1
    assert acknowledged.recovery_ack_head != "GENESIS"

    replay = store.acknowledge_recovery(
        baseline,
        healthy,
        policy,
        recovery_id="healthy-ack",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=30),
    )
    assert replay == acknowledged


def test_assessment_refuses_to_advance_on_corrupt_ack_history(tmp_path, now):
    path, store, baseline, _, policy, t, _ = _store_with_first_recovery(tmp_path, now)
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM health_recovery_acks_v3")
        conn.commit()
    finally:
        conn.close()

    later_bad = _window(now, healthy=False, source="later-bad")
    assessment = assess_health(
        baseline,
        later_bad,
        policy,
        now=t + timedelta(seconds=3),
    )
    with pytest.raises(HealthStateConflict, match="chain head"):
        store.apply_assessment(
            assessment,
            policy,
            now=t + timedelta(seconds=3),
        )


def test_pre_chain_state_is_not_silently_migrated(tmp_path, now):
    path = tmp_path / "pre-chain-state.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE health_state_v2 (
                entity_kind TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL,
                distinct_quarantine_count INTEGER NOT NULL,
                baseline_fingerprint TEXT NOT NULL,
                policy_fingerprint TEXT NOT NULL,
                last_assessment_fingerprint TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                state_hash TEXT NOT NULL,
                PRIMARY KEY(entity_kind, entity_id)
            )
            """
        )
        conn.execute(
            "INSERT INTO health_state_v2 VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "STRATEGY",
                "strategy-a",
                "HEALTHY",
                1,
                0,
                _sha("baseline"),
                _sha("policy"),
                _sha("assessment"),
                now.isoformat(),
                _sha("legacy-state-hash"),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(HealthStateConflict, match="explicit migration/rebaseline"):
        SQLiteHealthStateStore(path)
