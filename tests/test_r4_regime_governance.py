from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json

import pytest

from autotrade.research.portfolio_dependence import CalibrationPhase
from autotrade.research.regimes import (
    RegimeCalibrationError,
    RegimeCalibrationSeries,
    RegimeCalibrationSpec,
    RegimeEvaluationPhase,
    RegimeEvaluationSeries,
    RegimeFeatureObservation,
    RegimeGovernanceError,
    RegimeModelConflict,
    RegimeState,
    SQLiteRegimeModelRegistry,
    calibrate_regime_model,
    classify_regime,
    evaluate_regime_model,
)


D = Decimal


def observations(now, values, *, delay=timedelta(0)):
    return tuple(
        RegimeFeatureObservation(
            occurred_at=now + timedelta(minutes=index),
            available_at=now + timedelta(minutes=index) + delay,
            value=D(str(value)),
        )
        for index, value in enumerate(values)
    )


def calibration_series(now, values=(1, 2, 3, 4, 5), *, phase=CalibrationPhase.TRAIN):
    return RegimeCalibrationSeries(
        feature_name="realized_volatility_20",
        phase=phase,
        source_hash="a" * 64,
        observations=observations(now, values),
    )


def spec():
    return RegimeCalibrationSpec(
        low_quantile=D("0.40"),
        high_quantile=D("0.80"),
        min_observations=5,
    )


def model(now, values=(1, 2, 3, 4, 5)):
    return calibrate_regime_model(
        model_id="vol-regime",
        version=1,
        series=calibration_series(now, values),
        spec=spec(),
        now=now + timedelta(minutes=4),
    )


def test_holdout_is_structurally_evaluation_only(now):
    assert {phase.value for phase in CalibrationPhase} == {"TRAIN", "DEVELOPMENT"}
    assert "FINAL_HOLDOUT" in {phase.value for phase in RegimeEvaluationPhase}
    holdout = RegimeEvaluationSeries(
        feature_name="realized_volatility_20",
        phase=RegimeEvaluationPhase.FINAL_HOLDOUT,
        source_hash="b" * 64,
        observations=observations(now, (1, 2, 3, 4, 5)),
    )
    with pytest.raises(TypeError, match="HOLDOUT evaluation series cannot calibrate"):
        calibrate_regime_model(
            model_id="unsafe",
            version=1,
            series=holdout,  # type: ignore[arg-type]
            spec=spec(),
            now=now + timedelta(minutes=4),
        )


def test_regime_feature_timestamps_and_values_are_strict(now):
    with pytest.raises(ValueError, match="timezone-aware"):
        RegimeFeatureObservation(now.replace(tzinfo=None), now, D("1"))
    with pytest.raises(ValueError, match="cannot precede"):
        RegimeFeatureObservation(now, now - timedelta(seconds=1), D("1"))
    with pytest.raises(ValueError, match="finite Decimal"):
        RegimeFeatureObservation(now, now, D("NaN"))


def test_delayed_noncausal_features_are_rejected_for_calibration_and_evaluation(now):
    delayed = observations(now, (1, 2, 3), delay=timedelta(seconds=1))
    with pytest.raises(RegimeGovernanceError, match="causally available"):
        RegimeCalibrationSeries(
            "realized_volatility_20", CalibrationPhase.TRAIN, "a" * 64, delayed
        )
    with pytest.raises(RegimeGovernanceError, match="causally available"):
        RegimeEvaluationSeries(
            "realized_volatility_20", RegimeEvaluationPhase.FINAL_HOLDOUT, "a" * 64, delayed
        )


def test_calibration_quantile_boundaries_and_model_fingerprint_are_deterministic(now):
    first = model(now)
    second = model(now)
    assert first.low_threshold == D("2")
    assert first.high_threshold == D("4")
    assert first.fingerprint == second.fingerprint
    assert first.to_payload()["fingerprint"] == first.fingerprint
    assert len(first.fingerprint) == 64


def test_calibration_sample_exact_boundary_passes_one_fewer_fails(now):
    exact = calibration_series(now, (1, 2, 3, 4, 5))
    calibrated = calibrate_regime_model(
        model_id="exact",
        version=1,
        series=exact,
        spec=spec(),
        now=now + timedelta(minutes=4),
    )
    assert calibrated.low_threshold < calibrated.high_threshold

    short = calibration_series(now, (1, 2, 3, 4))
    with pytest.raises(RegimeCalibrationError, match="below required 5"):
        calibrate_regime_model(
            model_id="short",
            version=1,
            series=short,
            spec=spec(),
            now=now + timedelta(minutes=4),
        )


def test_calibration_cannot_use_observation_not_yet_available_at_calibration_time(now):
    data = calibration_series(now)
    with pytest.raises(RegimeCalibrationError, match="unavailable"):
        calibrate_regime_model(
            model_id="future",
            version=1,
            series=data,
            spec=spec(),
            now=now + timedelta(minutes=3, seconds=59),
        )


def test_degenerate_calibration_thresholds_fail_closed(now):
    with pytest.raises(RegimeCalibrationError, match="degenerate"):
        model(now, values=(1, 1, 1, 1, 1))


def test_regime_spec_domains_are_fail_closed():
    RegimeCalibrationSpec(D("0.2"), D("0.8"), 3)
    for low, high in ((D("0"), D("0.8")), (D("0.8"), D("0.8")), (D("0.8"), D("0.7")), (D("0.2"), D("1"))):
        with pytest.raises(ValueError, match="0 < low < high < 1"):
            RegimeCalibrationSpec(low, high, 3)
    with pytest.raises(ValueError, match=">= 3"):
        RegimeCalibrationSpec(D("0.2"), D("0.8"), 2)


def test_threshold_exact_values_are_normal_and_epsilon_crosses_state(now):
    frozen = model(now)
    timestamp = now + timedelta(minutes=10)
    at_low = RegimeFeatureObservation(timestamp, timestamp, D("2"))
    below = RegimeFeatureObservation(timestamp, timestamp, D("1.999999"))
    at_high = RegimeFeatureObservation(timestamp, timestamp, D("4"))
    above = RegimeFeatureObservation(timestamp, timestamp, D("4.000001"))

    assert classify_regime(frozen, at_low, now=timestamp, max_age=timedelta(seconds=1)).state is RegimeState.NORMAL
    assert classify_regime(frozen, below, now=timestamp, max_age=timedelta(seconds=1)).state is RegimeState.LOW
    assert classify_regime(frozen, at_high, now=timestamp, max_age=timedelta(seconds=1)).state is RegimeState.NORMAL
    assert classify_regime(frozen, above, now=timestamp, max_age=timedelta(seconds=1)).state is RegimeState.HIGH


def test_missing_future_and_stale_features_are_unknown_not_optimistic(now):
    frozen = model(now)
    current = now + timedelta(minutes=10)
    missing = classify_regime(frozen, None, now=current, max_age=timedelta(minutes=1))
    assert missing.state is RegimeState.UNKNOWN
    assert missing.reason == "MISSING_FEATURE"

    future_obs = RegimeFeatureObservation(current + timedelta(seconds=1), current + timedelta(seconds=1), D("1"))
    future = classify_regime(frozen, future_obs, now=current, max_age=timedelta(minutes=1))
    assert future.state is RegimeState.UNKNOWN
    assert future.reason == "FEATURE_NOT_YET_AVAILABLE"

    stale_obs = RegimeFeatureObservation(current - timedelta(minutes=2), current - timedelta(minutes=2), D("1"))
    stale = classify_regime(frozen, stale_obs, now=current, max_age=timedelta(minutes=1))
    assert stale.state is RegimeState.UNKNOWN
    assert stale.reason == "STALE_FEATURE"


def test_holdout_evaluation_uses_frozen_model_without_mutation(now):
    frozen = model(now)
    before = frozen.fingerprint
    holdout_start = now + timedelta(days=1)
    holdout = RegimeEvaluationSeries(
        feature_name=frozen.feature_name,
        phase=RegimeEvaluationPhase.FINAL_HOLDOUT,
        source_hash="b" * 64,
        observations=observations(holdout_start, (1, 2, 5)),
    )
    evidence = evaluate_regime_model(frozen, holdout, max_age=timedelta(seconds=1))
    assert frozen.fingerprint == before
    assert evidence.model_fingerprint == before
    assert evidence.evaluation_phase is RegimeEvaluationPhase.FINAL_HOLDOUT
    assert tuple(item.state for item in evidence.classifications) == (
        RegimeState.LOW,
        RegimeState.NORMAL,
        RegimeState.HIGH,
    )
    assert len(evidence.fingerprint) == 64


def test_evaluation_feature_mismatch_fails_closed(now):
    frozen = model(now)
    wrong = RegimeEvaluationSeries(
        feature_name="different_feature",
        phase=RegimeEvaluationPhase.DEVELOPMENT,
        source_hash="b" * 64,
        observations=observations(now + timedelta(days=1), (1, 2, 3)),
    )
    with pytest.raises(RegimeGovernanceError, match="does not match frozen model"):
        evaluate_regime_model(frozen, wrong, max_age=timedelta(seconds=1))


def test_model_payload_roundtrip_and_tamper_detection(now):
    frozen = model(now)
    from autotrade.research.regimes import RegimeModel

    restored = RegimeModel.from_payload(frozen.to_payload())
    assert restored == frozen
    tampered = frozen.to_payload()
    tampered["high_threshold"] = "999"
    with pytest.raises(RegimeModelConflict, match="fingerprint mismatch"):
        RegimeModel.from_payload(tampered)


def test_registry_is_append_only_contiguous_idempotent_and_restart_safe(tmp_path, now):
    path = tmp_path / "regimes.db"
    registry = SQLiteRegimeModelRegistry(path)
    v1 = model(now)
    assert registry.register(v1, now=now + timedelta(minutes=5)) == v1
    assert registry.register(v1, now=now + timedelta(minutes=6)) == v1
    assert registry.latest(v1.model_id) == v1

    v2 = replace(v1, version=2, calibrated_at=now + timedelta(days=1))
    registry.register(v2, now=now + timedelta(days=1, minutes=1))
    restarted = SQLiteRegimeModelRegistry(path)
    assert restarted.latest(v1.model_id) == v2

    with pytest.raises(RegimeModelConflict, match="versions must advance exactly by one"):
        restarted.register(
            replace(v2, version=4, calibrated_at=now + timedelta(days=2)),
            now=now + timedelta(days=2),
        )


def test_registry_same_version_changed_model_conflicts(tmp_path, now):
    registry = SQLiteRegimeModelRegistry(tmp_path / "conflict.db")
    frozen = model(now)
    registry.register(frozen, now=now + timedelta(minutes=5))
    with pytest.raises(RegimeModelConflict, match="identity conflict"):
        registry.register(
            replace(frozen, high_threshold=D("4.5")),
            now=now + timedelta(minutes=6),
        )


def test_registry_read_detects_payload_and_fingerprint_tamper(tmp_path, now):
    path = tmp_path / "tamper.db"
    registry = SQLiteRegimeModelRegistry(path)
    frozen = model(now)
    registry.register(frozen, now=now + timedelta(minutes=5))

    import sqlite3
    conn = sqlite3.connect(path)
    try:
        payload = frozen.to_payload()
        altered = replace(frozen, high_threshold=D("4.5"))
        conn.execute(
            "UPDATE regime_models SET payload_json=? WHERE model_id=? AND version=1",
            (json.dumps(altered.to_payload(), sort_keys=True, separators=(",", ":")), frozen.model_id),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(RegimeModelConflict, match="stored regime fingerprint mismatch"):
        registry.latest(frozen.model_id)


def test_classification_time_and_age_policy_are_strict(now):
    frozen = model(now)
    observation = RegimeFeatureObservation(now, now, D("2"))
    with pytest.raises(ValueError, match="timezone-aware"):
        classify_regime(frozen, observation, now=now.replace(tzinfo=None), max_age=timedelta(seconds=1))
    with pytest.raises(ValueError, match="max_age"):
        classify_regime(frozen, observation, now=now, max_age=timedelta(0))
