from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from autotrade.research.oss3_development_evaluation import (
    KEY_POLICY_ID,
    METRIC_POLICY_ID,
    OSS3D2D_EVALUATION_VERSION,
    DevelopmentEvaluationCompatibilityError,
    DevelopmentEvaluationGovernanceError,
    DevelopmentEvaluationIntegrityError,
    DevelopmentEvaluationManifest,
    evaluate_development_predictions,
)
from autotrade.research.oss3_development_inference import DevelopmentInferenceRequest
from autotrade.research.oss3_factor_matrix_artifact import (
    FactorDefinition,
    FactorMatrixArtifact,
    FactorMatrixPartition,
    FactorMatrixRow,
)
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from autotrade.research.oss3_supervised_label_artifact import (
    LabelDefinition,
    LabelPartition,
    SupervisedLabelArtifact,
    SupervisedLabelRow,
)
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
TRAIN_END = BASE + timedelta(days=10)
DEV_START = TRAIN_END
DEV_END = DEV_START + timedelta(days=3)
CAMPAIGN = "oss3d2d-campaign-001"
SPLIT = "1" * 64
UNIVERSE = "2" * 64
FEATURE_CODE = "3" * 64
LABEL_CODE = "4" * 64
TRAIN_FEATURE_DATA = "5" * 64
TRAIN_LABEL_DATA = "6" * 64
DEV_FEATURE_DATA = "7" * 64
MODEL_CONFIG = "8" * 64
RUNNER_CODE = "9" * 64
ENVIRONMENT = "a" * 64
MODEL_FAMILY = "qlib_linear_ridge_v1"
QLIB_VERSION = "0.9.7"
SYMBOLS = ("AAA", "BBB", "CCC")


FEATURES = (
    FactorDefinition(
        name="momentum_5",
        dtype="float64",
        role="FEATURE",
        formula_hash="b" * 64,
        source_id="bars-v1",
        source_hash="c" * 64,
        lookback_bars=5,
    ),
    FactorDefinition(
        name="volatility_5",
        dtype="float64",
        role="FEATURE",
        formula_hash="d" * 64,
        source_id="bars-v1",
        source_hash="e" * 64,
        lookback_bars=5,
    ),
)
LABEL = LabelDefinition(
    name="forward_return_1h",
    dtype="float64",
    role="LABEL",
    formula_hash="f" * 64,
    source_id="bars-v1",
    source_hash="0" * 64,
)


def _factor_rows(start: datetime, days: int) -> tuple[FactorMatrixRow, ...]:
    rows = []
    counter = 1
    for day in range(days):
        timestamp = start + timedelta(days=day)
        for symbol in SYMBOLS:
            rows.append(
                FactorMatrixRow(
                    as_of=timestamp.isoformat(),
                    available_at=(timestamp - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(counter / 10.0, counter / 100.0),
                )
            )
            counter += 1
    return tuple(rows)


def _features(*, partition: FactorMatrixPartition, start: datetime, end: datetime, source_hash: str, days: int):
    return FactorMatrixArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=partition,
        partition_start=start,
        partition_end=end,
        producer_code_hash=FEATURE_CODE,
        source_dataset_hash=source_hash,
        source_universe_hash=UNIVERSE,
        features=FEATURES,
        rows=_factor_rows(start + (timedelta(days=1) if partition is FactorMatrixPartition.TRAIN else timedelta()), days),
    )


def _train_labels() -> SupervisedLabelArtifact:
    rows = []
    for index, symbol in enumerate(SYMBOLS, start=1):
        timestamp = BASE + timedelta(days=1)
        rows.append(
            SupervisedLabelRow(
                label_as_of=timestamp.isoformat(),
                horizon_end=(timestamp + timedelta(hours=1)).isoformat(),
                available_at=(timestamp + timedelta(hours=1, minutes=1)).isoformat(),
                symbol=symbol,
                value=index / 100.0,
            )
        )
    return SupervisedLabelArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=LabelPartition.TRAIN,
        partition_start=BASE,
        partition_end=TRAIN_END,
        producer_code_hash=LABEL_CODE,
        source_dataset_hash=TRAIN_LABEL_DATA,
        source_universe_hash=UNIVERSE,
        label=LABEL,
        rows=tuple(rows),
    )


def _development_labels(*, values=None, campaign=CAMPAIGN, split=SPLIT, universe=UNIVERSE, label=LABEL):
    if values is None:
        values = (-0.02, 0.0, 0.02, -0.03, 0.0, 0.03, -0.04, 0.0, 0.04)
    rows = []
    index = 0
    for day in range(3):
        timestamp = DEV_START + timedelta(days=day)
        for symbol in SYMBOLS:
            rows.append(
                SupervisedLabelRow(
                    label_as_of=timestamp.isoformat(),
                    horizon_end=(timestamp + timedelta(hours=1)).isoformat(),
                    available_at=(timestamp + timedelta(hours=1, minutes=1)).isoformat(),
                    symbol=symbol,
                    value=float(values[index]),
                )
            )
            index += 1
    return SupervisedLabelArtifact.build(
        campaign_id=campaign,
        research_split_hash=split,
        partition=LabelPartition.DEVELOPMENT,
        partition_start=DEV_START,
        partition_end=DEV_END,
        producer_code_hash=LABEL_CODE,
        source_dataset_hash="1" * 64,
        source_universe_hash=universe,
        label=label,
        rows=tuple(rows),
    )


def _chain(*, scores=None):
    train_features = _features(
        partition=FactorMatrixPartition.TRAIN,
        start=BASE,
        end=TRAIN_END,
        source_hash=TRAIN_FEATURE_DATA,
        days=1,
    )
    bundle = TrainingBundleArtifact.build(features=train_features, labels=_train_labels())
    dev_features = _features(
        partition=FactorMatrixPartition.DEVELOPMENT,
        start=DEV_START,
        end=DEV_END,
        source_hash=DEV_FEATURE_DATA,
        days=3,
    )
    request = DevelopmentInferenceRequest.build(
        training_bundle=bundle,
        development_features=dev_features,
        model_family=MODEL_FAMILY,
        model_config_hash=MODEL_CONFIG,
        required_qlib_version=QLIB_VERSION,
        expected_runner_code_hash=RUNNER_CODE,
    )
    if scores is None:
        scores = (-0.02, 0.0, 0.02, -0.03, 0.0, 0.03, -0.04, 0.0, 0.04)
    prediction = QlibPredictionArtifact.build(
        qlib_version=QLIB_VERSION,
        model_family=MODEL_FAMILY,
        model_config_hash=MODEL_CONFIG,
        training_dataset_hash=bundle.artifact_hash,
        feature_schema_hash=dev_features.manifest.feature_schema_hash,
        producer_code_hash=RUNNER_CODE,
        train_start=BASE,
        train_end=TRAIN_END,
        inference_start=DEV_START,
        inference_end=DEV_END,
        rows=tuple(
            QlibPredictionRow(timestamp=row.as_of, symbol=row.symbol, score=float(score))
            for row, score in zip(dev_features.rows, scores, strict=True)
        ),
    )
    receipt = request.bind_prediction(
        prediction=prediction,
        training_bundle=bundle,
        development_features=dev_features,
    )
    return bundle, dev_features, request, prediction, receipt


def _evaluate(*, scores=None, labels=None, environment=ENVIRONMENT):
    _, _, _, prediction, receipt = _chain(scores=scores)
    return evaluate_development_predictions(
        receipt=receipt,
        prediction=prediction,
        labels=_development_labels() if labels is None else labels,
        environment_attestation_hash=environment,
    )


def test_perfect_predictions_have_unit_ic_zero_error_and_exact_lineage() -> None:
    artifact = _evaluate()
    assert artifact.evaluation_version == OSS3D2D_EVALUATION_VERSION
    assert artifact.metrics.observation_count == 9
    assert artifact.metrics.cross_section_count == 3
    assert artifact.metrics.pearson_ic == pytest.approx(1.0)
    assert artifact.metrics.spearman_ic == pytest.approx(1.0)
    assert artifact.metrics.mae == pytest.approx(0.0)
    assert artifact.metrics.rmse == pytest.approx(0.0)
    assert artifact.metrics.sign_accuracy == pytest.approx(1.0)
    assert artifact.metrics.mean_cross_sectional_ic == pytest.approx(1.0)
    assert artifact.metrics.mean_cross_sectional_rank_ic == pytest.approx(1.0)
    assert artifact.metrics.positive_cross_sectional_ic_ratio == pytest.approx(1.0)
    assert artifact.manifest.metric_policy_id == METRIC_POLICY_ID
    assert artifact.manifest.key_policy_id == KEY_POLICY_ID
    assert artifact.manifest.environment_attestation_hash == ENVIRONMENT
    assert artifact.manifest.research_only is True
    assert artifact.manifest.execution_authorized is False
    assert artifact.manifest.paper_execution_authorized is False
    assert artifact.manifest.capital_authority == "NONE"
    assert artifact.manifest.live_trading == "BLOCKED"


def test_evaluation_is_deterministic() -> None:
    assert _evaluate() == _evaluate()


def test_inverted_predictions_produce_negative_ic_and_zero_sign_accuracy() -> None:
    scores = (0.02, 0.0, -0.02, 0.03, 0.0, -0.03, 0.04, 0.0, -0.04)
    artifact = _evaluate(scores=scores)
    assert artifact.metrics.pearson_ic == pytest.approx(-1.0)
    assert artifact.metrics.spearman_ic == pytest.approx(-1.0)
    assert artifact.metrics.mean_cross_sectional_ic == pytest.approx(-1.0)
    assert artifact.metrics.positive_cross_sectional_ic_ratio == pytest.approx(0.0)
    assert artifact.metrics.sign_accuracy == pytest.approx(1 / 3)


def test_environment_hash_is_lineage_only_and_changes_artifact_identity() -> None:
    left = _evaluate(environment="a" * 64)
    right = _evaluate(environment="b" * 64)
    assert left.metrics == right.metrics
    assert left.manifest.environment_attestation_hash != right.manifest.environment_attestation_hash
    assert left.artifact_hash != right.artifact_hash


def test_non_development_labels_fail_closed() -> None:
    _, _, _, prediction, receipt = _chain()
    with pytest.raises(DevelopmentEvaluationGovernanceError, match="DEVELOPMENT labels only"):
        evaluate_development_predictions(
            receipt=receipt,
            prediction=prediction,
            labels=_train_labels(),
            environment_attestation_hash=ENVIRONMENT,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("campaign", "other-campaign"),
        ("split", "a" * 64),
        ("universe", "b" * 64),
    ],
)
def test_label_provenance_mismatch_fails_closed(field, value) -> None:
    kwargs = {field: value}
    labels = _development_labels(**kwargs)
    _, _, _, prediction, receipt = _chain()
    with pytest.raises(DevelopmentEvaluationCompatibilityError, match="mismatch"):
        evaluate_development_predictions(
            receipt=receipt,
            prediction=prediction,
            labels=labels,
            environment_attestation_hash=ENVIRONMENT,
        )


def test_label_definition_mismatch_fails_closed() -> None:
    changed = LabelDefinition(
        name=LABEL.name,
        dtype="float64",
        role="LABEL",
        formula_hash="a" * 64,
        source_id=LABEL.source_id,
        source_hash=LABEL.source_hash,
    )
    labels = _development_labels(label=changed)
    _, _, _, prediction, receipt = _chain()
    with pytest.raises(DevelopmentEvaluationCompatibilityError, match="label_definition_hash"):
        evaluate_development_predictions(
            receipt=receipt,
            prediction=prediction,
            labels=labels,
            environment_attestation_hash=ENVIRONMENT,
        )


def test_prediction_label_keyset_mismatch_fails_closed() -> None:
    labels = _development_labels()
    changed_rows = list(labels.rows)
    row = changed_rows[-1]
    changed_rows[-1] = SupervisedLabelRow(
        label_as_of=row.label_as_of,
        horizon_end=row.horizon_end,
        available_at=row.available_at,
        symbol="DDD",
        value=row.value,
    )
    changed = SupervisedLabelArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=LabelPartition.DEVELOPMENT,
        partition_start=DEV_START,
        partition_end=DEV_END,
        producer_code_hash=LABEL_CODE,
        source_dataset_hash="1" * 64,
        source_universe_hash=UNIVERSE,
        label=LABEL,
        rows=tuple(changed_rows),
    )
    _, _, _, prediction, receipt = _chain()
    with pytest.raises(DevelopmentEvaluationCompatibilityError, match="keysets"):
        evaluate_development_predictions(
            receipt=receipt,
            prediction=prediction,
            labels=changed,
            environment_attestation_hash=ENVIRONMENT,
        )


def test_wrong_prediction_artifact_is_rejected_before_metrics() -> None:
    _, dev, request, prediction, receipt = _chain()
    wrong = QlibPredictionArtifact.build(
        qlib_version=prediction.manifest.qlib_version,
        model_family=prediction.manifest.model_family,
        model_config_hash=prediction.manifest.model_config_hash,
        training_dataset_hash=prediction.manifest.training_dataset_hash,
        feature_schema_hash=prediction.manifest.feature_schema_hash,
        producer_code_hash=prediction.manifest.producer_code_hash,
        train_start=BASE,
        train_end=TRAIN_END,
        inference_start=DEV_START,
        inference_end=DEV_END,
        rows=tuple(
            QlibPredictionRow(timestamp=row.as_of, symbol=row.symbol, score=float(index))
            for index, row in enumerate(dev.rows, start=1)
        ),
    )
    assert wrong.artifact_hash != prediction.artifact_hash
    with pytest.raises(DevelopmentEvaluationCompatibilityError, match="prediction_artifact_hash"):
        evaluate_development_predictions(
            receipt=receipt,
            prediction=wrong,
            labels=_development_labels(),
            environment_attestation_hash=ENVIRONMENT,
        )


def test_constant_global_predictions_fail_closed() -> None:
    with pytest.raises(DevelopmentEvaluationGovernanceError, match="constant values"):
        _evaluate(scores=(1.0,) * 9)


def test_cross_section_without_variance_is_excluded_but_other_sections_remain() -> None:
    scores = (1.0, 1.0, 1.0, -0.03, 0.0, 0.03, -0.04, 0.0, 0.04)
    artifact = _evaluate(scores=scores)
    assert artifact.metrics.cross_section_count == 2
    assert len(artifact.cross_sections) == 2


def test_no_valid_cross_section_fails_closed() -> None:
    scores = (1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0)
    with pytest.raises(DevelopmentEvaluationGovernanceError, match="no timestamp"):
        _evaluate(scores=scores)


def test_invalid_environment_hash_fails_closed() -> None:
    with pytest.raises(ValueError, match="environment_attestation_hash"):
        _evaluate(environment="not-a-hash")


def test_manifest_cannot_be_mutated_into_execution_authority() -> None:
    artifact = _evaluate()
    with pytest.raises(DevelopmentEvaluationGovernanceError):
        replace(artifact.manifest, execution_authorized=True)
    with pytest.raises(DevelopmentEvaluationGovernanceError):
        replace(artifact.manifest, live_trading="ENABLED")


def test_artifact_hash_detects_metric_tampering() -> None:
    artifact = _evaluate()
    changed_metrics = replace(artifact.metrics, mae=0.5)
    with pytest.raises(DevelopmentEvaluationIntegrityError, match="artifact hash mismatch"):
        replace(artifact, metrics=changed_metrics)
