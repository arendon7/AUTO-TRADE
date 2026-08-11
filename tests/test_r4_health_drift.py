from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.health import (
    HealthBaselineSeries,
    HealthEntityKind,
    HealthGovernanceError,
    HealthObservationSeries,
    HealthPolicy,
    HealthRecoveryRejected,
    HealthReturnObservation,
    HealthState,
    SQLiteHealthStateStore,
    assess_health,
    build_health_baseline,
)
from autotrade.research.portfolio_dependence import CalibrationPhase


D = Decimal


def obs(now, values, *, offset=0):
    return tuple(
        HealthReturnObservation(
            occurred_at=now + timedelta(minutes=offset + index),
            available_at=now + timedelta(minutes=offset + index),
            value=D(str(value)),
        )
        for index, value in enumerate(values)
    )


def baseline_series(now):
    return HealthBaselineSeries(
        entity_id="strategy-a@1",
        entity_kind=HealthEntityKind.STRATEGY,
        phase=CalibrationPhase.TRAIN,
        source_hash="a" * 64,
        observations=obs(now, ("0.01", "0.03", "0.02", "0.04", "0.02")),
    )


def observed(now, values, *, source_char="b", offset=10):
    return HealthObservationSeries(
        entity_id="strategy-a@1",
        entity_kind=HealthEntityKind.STRATEGY,
        source_hash=source_char * 64,
        observations=obs(now, values, offset=offset),
    )


def policy():
    return HealthPolicy(
        min_observations=5,
        degraded_mean_loss_fraction=D("0.25"),
        quarantined_mean_loss_fraction=D("0.60"),
        degraded_volatility_ratio=D("1.5"),
        quarantined_volatility_ratio=D("2.5"),
        retire_after_distinct_quarantines=2,
    )


def test_baseline_is_train_or_development_only_and_deterministic(now):
    series = baseline_series(now)
    baseline = build_health_baseline(series)
    assert baseline.mean_return > 0
    assert baseline.volatility > 0
    assert len(baseline.fingerprint) == 64
    assert baseline.fingerprint == build_health_baseline(series).fingerprint
    assert {phase.value for phase in CalibrationPhase} == {"TRAIN", "DEVELOPMENT"}


def test_health_observation_requires_causal_timestamp_and_finite_return(now):
    with pytest.raises(HealthGovernanceError, match="causally available"):
        HealthReturnObservation(now, now + timedelta(seconds=1), D("0.01"))
    with pytest.raises(ValueError, match="finite Decimal"):
        HealthReturnObservation(now, now, D("NaN"))


def test_baseline_requires_positive_mean_and_nonzero_volatility(now):
    nonpositive = HealthBaselineSeries(
        "strategy-a@1",
        HealthEntityKind.STRATEGY,
        CalibrationPhase.TRAIN,
        "a" * 64,
        obs(now, ("-0.01", "0.01", "-0.01", "0.01", "0")),
    )
    with pytest.raises(HealthGovernanceError, match="baseline mean return"):
        build_health_baseline(nonpositive)

    constant = HealthBaselineSeries(
        "strategy-a@1",
        HealthEntityKind.STRATEGY,
        CalibrationPhase.TRAIN,
        "a" * 64,
        obs(now, ("0.01",) * 5),
    )
    with pytest.raises(HealthGovernanceError, match="baseline volatility"):
        build_health_baseline(constant)


def test_policy_thresholds_leave_a_real_healthy_region():
    policy()
    with pytest.raises(ValueError, match="mean-loss thresholds"):
        HealthPolicy(5, D("0"), D("0.6"), D("1.5"), D("2.5"), 2)
    with pytest.raises(ValueError, match="volatility ratios"):
        HealthPolicy(5, D("0.2"), D("0.6"), D("1"), D("2.5"), 2)


def test_health_assessment_classifies_healthy_degraded_and_quarantined(now):
    baseline = build_health_baseline(baseline_series(now))
    current_time = now + timedelta(minutes=20)
    healthy = assess_health(
        baseline,
        observed(now, ("0.011", "0.029", "0.021", "0.039", "0.020")),
        policy(),
        now=current_time,
    )
    assert healthy.proposed_state is HealthState.HEALTHY

    degraded = assess_health(
        baseline,
        observed(now, ("0.010", "0.015", "0.012", "0.018", "0.015"), source_char="c"),
        policy(),
        now=current_time,
    )
    assert degraded.proposed_state in {HealthState.DEGRADED, HealthState.QUARANTINED}

    quarantined = assess_health(
        baseline,
        observed(now, ("-0.010", "0.002", "-0.005", "0.001", "-0.003"), source_char="d"),
        policy(),
        now=current_time,
    )
    assert quarantined.proposed_state is HealthState.QUARANTINED


def test_insufficient_or_future_health_evidence_fails_closed(now):
    baseline = build_health_baseline(baseline_series(now))
    short = HealthObservationSeries(
        "strategy-a@1",
        HealthEntityKind.STRATEGY,
        "b" * 64,
        obs(now, ("0.01", "0.02"), offset=10),
    )
    with pytest.raises(HealthGovernanceError, match="insufficient"):
        assess_health(baseline, short, policy(), now=now + timedelta(minutes=20))

    with pytest.raises(HealthGovernanceError, match="unavailable"):
        assess_health(
            baseline,
            observed(now, ("0.01", "0.02", "0.01", "0.02", "0.01"), offset=30),
            policy(),
            now=now + timedelta(minutes=20),
        )


def test_automatic_health_state_is_monotone_and_new_evidence_versions_state(tmp_path, now):
    baseline = build_health_baseline(baseline_series(now))
    store = SQLiteHealthStateStore(tmp_path / "health.db")
    p = policy()
    t = now + timedelta(minutes=20)

    degraded = assess_health(
        baseline,
        observed(now, ("0.010", "0.015", "0.012", "0.018", "0.015"), source_char="c"),
        p,
        now=t,
    )
    first = store.apply_assessment(degraded, p, now=t)
    assert first.state in {HealthState.DEGRADED, HealthState.QUARANTINED}

    healthy = assess_health(
        baseline,
        observed(now, ("0.011", "0.029", "0.021", "0.039", "0.020"), source_char="b"),
        p,
        now=t + timedelta(seconds=1),
    )
    second = store.apply_assessment(healthy, p, now=t + timedelta(seconds=1))
    assert second.state is first.state
    assert second.version == first.version + 1
    assert second.last_assessment_fingerprint == healthy.fingerprint

    replay = store.apply_assessment(healthy, p, now=t + timedelta(seconds=2))
    assert replay == second


def test_distinct_quarantine_evidence_retires_but_replay_does_not_double_count(tmp_path, now):
    baseline = build_health_baseline(baseline_series(now))
    store = SQLiteHealthStateStore(tmp_path / "retire.db")
    p = policy()
    t = now + timedelta(minutes=20)
    first_assessment = assess_health(
        baseline,
        observed(now, ("-0.01", "0.002", "-0.005", "0.001", "-0.003"), source_char="d"),
        p,
        now=t,
    )
    first = store.apply_assessment(first_assessment, p, now=t)
    assert first.state is HealthState.QUARANTINED
    assert first.distinct_quarantine_count == 1

    replay = store.apply_assessment(first_assessment, p, now=t + timedelta(seconds=1))
    assert replay == first

    second_assessment = assess_health(
        baseline,
        observed(now, ("-0.02", "0.001", "-0.004", "0.002", "-0.006"), source_char="e"),
        p,
        now=t + timedelta(seconds=2),
    )
    retired = store.apply_assessment(second_assessment, p, now=t + timedelta(seconds=2))
    assert retired.state is HealthState.RETIRED
    assert retired.distinct_quarantine_count == 2


def test_recovery_recomputes_fresh_healthy_evidence_and_only_improves_one_level(tmp_path, now):
    baseline = build_health_baseline(baseline_series(now))
    store = SQLiteHealthStateStore(tmp_path / "recovery.db")
    p = HealthPolicy(5, D("0.25"), D("0.60"), D("1.5"), D("2.5"), 3)
    t = now + timedelta(minutes=20)
    bad_window = observed(now, ("-0.01", "0.002", "-0.005", "0.001", "-0.003"), source_char="d")
    bad = assess_health(baseline, bad_window, p, now=t)
    quarantined = store.apply_assessment(bad, p, now=t)
    assert quarantined.state is HealthState.QUARANTINED

    healthy_window = observed(now, ("0.011", "0.029", "0.021", "0.039", "0.020"), source_char="b")
    first_recovery = store.acknowledge_recovery(
        baseline,
        healthy_window,
        p,
        recovery_id="test_r4_health_drift-recovery-1",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=1),
    )
    assert first_recovery.state is HealthState.DEGRADED
    second_recovery = store.acknowledge_recovery(
        baseline,
        healthy_window,
        p,
        recovery_id="test_r4_health_drift-recovery-2",
        confirmed_by="risk-officer",
        now=t + timedelta(seconds=2),
    )
    assert second_recovery.state is HealthState.HEALTHY


def test_recovery_rejects_unhealthy_recomputed_evidence_and_retired_state(tmp_path, now):
    baseline = build_health_baseline(baseline_series(now))
    p = policy()
    t = now + timedelta(minutes=20)
    store = SQLiteHealthStateStore(tmp_path / "reject-recovery.db")
    bad_window = observed(now, ("-0.01", "0.002", "-0.005", "0.001", "-0.003"), source_char="d")
    bad = assess_health(baseline, bad_window, p, now=t)
    store.apply_assessment(bad, p, now=t)

    with pytest.raises(HealthRecoveryRejected, match="HEALTHY evidence"):
        store.acknowledge_recovery(
            baseline, bad_window, p, recovery_id="test_r4_health_drift-inline-recovery-1", confirmed_by="risk-officer", now=t + timedelta(seconds=1)
        )

    different_bad = observed(now, ("-0.02", "0.001", "-0.004", "0.002", "-0.006"), source_char="e")
    second = assess_health(baseline, different_bad, p, now=t + timedelta(seconds=2))
    retired = store.apply_assessment(second, p, now=t + timedelta(seconds=2))
    assert retired.state is HealthState.RETIRED
    healthy_window = observed(now, ("0.011", "0.029", "0.021", "0.039", "0.020"), source_char="b")
    with pytest.raises(HealthRecoveryRejected, match="RETIRED"):
        store.acknowledge_recovery(
            baseline, healthy_window, p, recovery_id="test_r4_health_drift-inline-recovery-2", confirmed_by="risk-officer", now=t + timedelta(seconds=3)
        )


def test_health_state_survives_restart(tmp_path, now):
    baseline = build_health_baseline(baseline_series(now))
    path = tmp_path / "restart-health.db"
    p = policy()
    t = now + timedelta(minutes=20)
    assessment = assess_health(
        baseline,
        observed(now, ("-0.01", "0.002", "-0.005", "0.001", "-0.003"), source_char="d"),
        p,
        now=t,
    )
    first = SQLiteHealthStateStore(path)
    state = first.apply_assessment(assessment, p, now=t)
    restarted = SQLiteHealthStateStore(path)
    assert restarted.get("strategy-a@1", HealthEntityKind.STRATEGY) == state


def test_portfolio_health_uses_same_evidence_contract(now):
    baseline = HealthBaselineSeries(
        entity_id="portfolio-main",
        entity_kind=HealthEntityKind.PORTFOLIO,
        phase=CalibrationPhase.DEVELOPMENT,
        source_hash="f" * 64,
        observations=obs(now, ("0.005", "0.012", "0.008", "0.015", "0.009")),
    )
    built = build_health_baseline(baseline)
    assert built.entity_kind is HealthEntityKind.PORTFOLIO
