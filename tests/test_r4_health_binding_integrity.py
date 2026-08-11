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


def _source_hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _obs(now, values, *, offset=0):
    return tuple(
        HealthReturnObservation(
            occurred_at=now + timedelta(minutes=offset + index),
            available_at=now + timedelta(minutes=offset + index),
            value=D(str(value)),
        )
        for index, value in enumerate(values)
    )


def _baseline(now, *, entity_id="shared-id", kind=HealthEntityKind.STRATEGY, source="a", values=None):
    values = values or ("0.01", "0.03", "0.02", "0.04", "0.02")
    return build_health_baseline(
        HealthBaselineSeries(
            entity_id=entity_id,
            entity_kind=kind,
            phase=CalibrationPhase.TRAIN,
            source_hash=_source_hash(source),
            observations=_obs(now, values),
        )
    )


def _window(now, *, entity_id="shared-id", kind=HealthEntityKind.STRATEGY, source="b", values=None, offset=10):
    values = values or ("-0.01", "0.002", "-0.005", "0.001", "-0.003")
    return HealthObservationSeries(
        entity_id=entity_id,
        entity_kind=kind,
        source_hash=_source_hash(source),
        observations=_obs(now, values, offset=offset),
    )


def _healthy_window(
    now,
    *,
    entity_id="shared-id",
    kind=HealthEntityKind.STRATEGY,
    source="c",
    offset=10,
    values=None,
):
    values = values or ("0.011", "0.029", "0.021", "0.039", "0.020")
    return _window(
        now,
        entity_id=entity_id,
        kind=kind,
        source=source,
        values=values,
        offset=offset,
    )


def _policy(**overrides):
    values = dict(
        min_observations=5,
        degraded_mean_loss_fraction=D("0.25"),
        quarantined_mean_loss_fraction=D("0.60"),
        degraded_volatility_ratio=D("1.5"),
        quarantined_volatility_ratio=D("2.5"),
        retire_after_distinct_quarantines=3,
        max_observation_age_seconds=3600,
    )
    values.update(overrides)
    return HealthPolicy(**values)


def _assessment(baseline, window, policy, now):
    return assess_health(baseline, window, policy, now=now)


def test_strategy_and_portfolio_with_same_text_id_are_independent(tmp_path, now):
    store = SQLiteHealthStateStore(tmp_path / "kind-separation.db")
    p = _policy()
    t = now + timedelta(minutes=20)

    strategy_baseline = _baseline(now, kind=HealthEntityKind.STRATEGY)
    portfolio_values = ("0.005", "0.012", "0.008", "0.015", "0.009")
    portfolio_baseline = _baseline(
        now,
        kind=HealthEntityKind.PORTFOLIO,
        source="portfolio-baseline",
        values=portfolio_values,
    )
    strategy_assessment = _assessment(
        strategy_baseline,
        _window(now, kind=HealthEntityKind.STRATEGY),
        p,
        t,
    )
    portfolio_assessment = _assessment(
        portfolio_baseline,
        _healthy_window(
            now,
            kind=HealthEntityKind.PORTFOLIO,
            source="portfolio-observed",
            values=("0.0055", "0.0115", "0.0085", "0.0145", "0.0095"),
        ),
        p,
        t,
    )

    strategy_state = store.apply_assessment(strategy_assessment, p, now=t)
    portfolio_state = store.apply_assessment(portfolio_assessment, p, now=t)

    assert strategy_state.entity_key == "STRATEGY:shared-id"
    assert portfolio_state.entity_key == "PORTFOLIO:shared-id"
    assert strategy_state.state is HealthState.QUARANTINED
    assert portfolio_state.state is HealthState.HEALTHY
    assert store.get("shared-id", HealthEntityKind.STRATEGY) == strategy_state
    assert store.get("shared-id", HealthEntityKind.PORTFOLIO) == portfolio_state


def test_existing_state_rejects_different_baseline_even_for_same_entity(tmp_path, now):
    store = SQLiteHealthStateStore(tmp_path / "baseline-binding.db")
    p = _policy()
    t = now + timedelta(minutes=20)
    baseline_a = _baseline(now, source="baseline-a")
    bad = _assessment(baseline_a, _window(now), p, t)
    store.apply_assessment(bad, p, now=t)

    baseline_b = _baseline(
        now,
        source="baseline-b",
        values=("0.02", "0.04", "0.03", "0.05", "0.03"),
    )
    later = _assessment(
        baseline_b,
        _healthy_window(
            now,
            source="baseline-b-window",
            values=("0.021", "0.039", "0.031", "0.049", "0.030"),
        ),
        p,
        t + timedelta(seconds=1),
    )
    with pytest.raises(HealthStateConflict, match="baseline fingerprint mismatch"):
        store.apply_assessment(later, p, now=t + timedelta(seconds=1))


def test_existing_state_rejects_policy_change_without_explicit_transition(tmp_path, now):
    store = SQLiteHealthStateStore(tmp_path / "policy-binding.db")
    baseline = _baseline(now)
    p1 = _policy()
    t = now + timedelta(minutes=20)
    first = _assessment(baseline, _window(now), p1, t)
    store.apply_assessment(first, p1, now=t)

    p2 = _policy(degraded_mean_loss_fraction=D("0.20"))
    second = _assessment(
        baseline,
        _healthy_window(now, source="policy-change-window"),
        p2,
        t + timedelta(seconds=1),
    )
    with pytest.raises(HealthStateConflict, match="policy fingerprint mismatch"):
        store.apply_assessment(second, p2, now=t + timedelta(seconds=1))


def test_recovery_cannot_swap_to_easier_baseline(tmp_path, now):
    store = SQLiteHealthStateStore(tmp_path / "recovery-baseline.db")
    p = _policy()
    t = now + timedelta(minutes=20)
    baseline_a = _baseline(now)
    bad = _assessment(baseline_a, _window(now), p, t)
    state = store.apply_assessment(bad, p, now=t)
    assert state.state is HealthState.QUARANTINED

    easier = _baseline(
        now,
        source="easier-baseline",
        values=("0.005", "0.020", "0.010", "0.025", "0.010"),
    )
    with pytest.raises(HealthStateConflict, match="baseline fingerprint mismatch"):
        store.acknowledge_recovery(
            easier,
            _healthy_window(
                now,
                source="easier-baseline-window",
                values=("0.006", "0.019", "0.011", "0.024", "0.010"),
            ),
            p,
            confirmed_by="risk-officer",
            now=t + timedelta(seconds=1),
        )


def test_recovery_cannot_swap_policy(tmp_path, now):
    store = SQLiteHealthStateStore(tmp_path / "recovery-policy.db")
    baseline = _baseline(now)
    p1 = _policy()
    t = now + timedelta(minutes=20)
    state = store.apply_assessment(_assessment(baseline, _window(now), p1, t), p1, now=t)
    assert state.state is HealthState.QUARANTINED

    p2 = _policy(quarantined_mean_loss_fraction=D("0.70"))
    with pytest.raises(HealthStateConflict, match="policy fingerprint mismatch"):
        store.acknowledge_recovery(
            baseline,
            _healthy_window(now, source="recovery-policy-window"),
            p2,
            confirmed_by="risk-officer",
            now=t + timedelta(seconds=1),
        )


def test_nonconsecutive_replay_does_not_increment_quarantine_counter(tmp_path, now):
    store = SQLiteHealthStateStore(tmp_path / "nonconsecutive-replay.db")
    baseline = _baseline(now)
    p = _policy(retire_after_distinct_quarantines=3)
    t = now + timedelta(minutes=20)

    quarantine = _assessment(baseline, _window(now, source="quarantine"), p, t)
    first = store.apply_assessment(quarantine, p, now=t)
    assert first.distinct_quarantine_count == 1

    healthy = _assessment(
        baseline,
        _healthy_window(now, source="healthy"),
        p,
        t + timedelta(seconds=1),
    )
    later = store.apply_assessment(healthy, p, now=t + timedelta(seconds=1))
    assert later.distinct_quarantine_count == 1
    assert later.version == first.version + 1

    replay = store.apply_assessment(quarantine, p, now=t + timedelta(seconds=2))
    assert replay == later
    assert replay.distinct_quarantine_count == 1


def test_state_hash_tamper_fails_closed_on_read(tmp_path, now):
    path = tmp_path / "state-hash.db"
    store = SQLiteHealthStateStore(path)
    baseline = _baseline(now)
    p = _policy()
    t = now + timedelta(minutes=20)
    state = store.apply_assessment(_assessment(baseline, _window(now), p, t), p, now=t)
    assert len(state.fingerprint) == 64

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE health_state_v2 SET state='HEALTHY' WHERE entity_kind='STRATEGY' AND entity_id='shared-id'"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(HealthStateConflict, match="state hash mismatch"):
        store.get("shared-id", HealthEntityKind.STRATEGY)


def test_state_hash_column_tamper_fails_closed(tmp_path, now):
    path = tmp_path / "state-hash-column.db"
    store = SQLiteHealthStateStore(path)
    baseline = _baseline(now)
    p = _policy()
    t = now + timedelta(minutes=20)
    store.apply_assessment(_assessment(baseline, _window(now), p, t), p, now=t)

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE health_state_v2 SET state_hash=? WHERE entity_kind='STRATEGY' AND entity_id='shared-id'",
            ("0" * 64,),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(HealthStateConflict, match="state hash mismatch"):
        store.get("shared-id", HealthEntityKind.STRATEGY)


def test_legacy_ambiguous_state_requires_explicit_rebaseline(tmp_path, now):
    path = tmp_path / "legacy-health.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE health_state (
                entity_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                version INTEGER NOT NULL,
                distinct_quarantine_count INTEGER NOT NULL,
                last_assessment_fingerprint TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO health_state VALUES(?,?,?,?,?,?)",
            ("shared-id", "QUARANTINED", 1, 1, "a" * 64, now.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(HealthStateConflict, match="explicit rebaseline required"):
        SQLiteHealthStateStore(path)


def test_missing_kind_is_not_an_implicit_lookup(tmp_path):
    store = SQLiteHealthStateStore(tmp_path / "typed-get.db")
    with pytest.raises(TypeError):
        store.get("shared-id")  # type: ignore[call-arg]
