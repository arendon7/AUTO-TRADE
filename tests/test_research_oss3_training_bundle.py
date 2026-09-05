from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

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
from autotrade.research.oss3_training_bundle import (
    ModelTrainingReceipt,
    OSS3D1_ARTIFACT_VERSION,
    OSS3D1_RECEIPT_VERSION,
    PAIRING_POLICY,
    TrainingBundleArtifact,
    TrainingBundleCompatibilityError,
    TrainingBundleIntegrityError,
)


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = BASE + timedelta(days=10)
CAMPAIGN = "oss3d1-campaign-001"
SPLIT = "1" * 64
UNIVERSE = "2" * 64
FEATURE_DATA = "3" * 64
LABEL_DATA = "4" * 64
FEATURE_CODE = "5" * 64
LABEL_CODE = "6" * 64
MODEL_CODE = "7" * 64
MODEL_CONFIG = "8" * 64


def _feature_artifact(*, campaign=CAMPAIGN, split=SPLIT, universe=UNIVERSE, partition=FactorMatrixPartition.TRAIN, start=BASE, end=END, rows=None):
    features = (
        FactorDefinition(
            name="momentum_20",
            dtype="float64",
            role="FEATURE",
            formula_hash="a" * 64,
            source_id="aligned-bars-v1",
            source_hash="b" * 64,
            lookback_bars=20,
        ),
    )
    if rows is None:
        rows = tuple(
            FactorMatrixRow(
                as_of=(BASE + timedelta(days=day)).isoformat(),
                available_at=(BASE + timedelta(days=day, minutes=-1)).isoformat(),
                symbol=symbol,
                values=(float(day),),
            )
            for day, symbol in ((1, "BTCUSDT"), (1, "ETHUSDT"), (2, "BTCUSDT"))
        )
    return FactorMatrixArtifact.build(
        campaign_id=campaign,
        research_split_hash=split,
        partition=partition,
        partition_start=start,
        partition_end=end,
        producer_code_hash=FEATURE_CODE,
        source_dataset_hash=FEATURE_DATA,
        source_universe_hash=universe,
        features=features,
        rows=rows,
    )


def _label_artifact(*, campaign=CAMPAIGN, split=SPLIT, universe=UNIVERSE, partition=LabelPartition.TRAIN, start=BASE, end=END, rows=None, source_dataset_hash=LABEL_DATA):
    if rows is None:
        rows = tuple(
            SupervisedLabelRow(
                label_as_of=(BASE + timedelta(days=day)).isoformat(),
                horizon_end=(BASE + timedelta(days=day, hours=1)).isoformat(),
                available_at=(BASE + timedelta(days=day, hours=1, minutes=1)).isoformat(),
                symbol=symbol,
                value=0.01 * day,
            )
            for day, symbol in ((1, "BTCUSDT"), (1, "ETHUSDT"), (2, "BTCUSDT"))
        )
    return SupervisedLabelArtifact.build(
        campaign_id=campaign,
        research_split_hash=split,
        partition=partition,
        partition_start=start,
        partition_end=end,
        producer_code_hash=LABEL_CODE,
        source_dataset_hash=source_dataset_hash,
        source_universe_hash=universe,
        label=LabelDefinition(
            name="forward_return",
            dtype="float64",
            role="LABEL",
            formula_hash="c" * 64,
            source_id="aligned-bars-v1",
            source_hash="d" * 64,
        ),
        rows=rows,
    )


def _bundle(**kwargs):
    return TrainingBundleArtifact.build(
        features=kwargs.get("features", _feature_artifact()),
        labels=kwargs.get("labels", _label_artifact()),
    )


def _prediction(bundle, *, training_hash=None, feature_schema_hash=None, train_start=BASE, train_end=END):
    inference_start = END + timedelta(days=1)
    inference_end = END + timedelta(days=2)
    return QlibPredictionArtifact.build(
        qlib_version="0.9.7",
        model_family="lightgbm_ranker",
        model_config_hash=MODEL_CONFIG,
        training_dataset_hash=bundle.training_dataset_hash if training_hash is None else training_hash,
        feature_schema_hash=bundle.manifest.feature_schema_hash if feature_schema_hash is None else feature_schema_hash,
        producer_code_hash=MODEL_CODE,
        train_start=train_start,
        train_end=train_end,
        inference_start=inference_start,
        inference_end=inference_end,
        rows=(
            QlibPredictionRow(
                timestamp=(inference_start + timedelta(hours=1)).isoformat(),
                symbol="BTCUSDT",
                score=0.7,
            ),
        ),
    )


def _write(path, document, *, canonical=True):
    if canonical:
        text = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    else:
        text = json.dumps(document, indent=2, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8")


def test_build_round_trip_is_deterministic_and_portable(tmp_path):
    bundle = _bundle()
    assert bundle.artifact_version == OSS3D1_ARTIFACT_VERSION
    assert bundle.training_dataset_hash == bundle.artifact_hash
    assert bundle.artifact_hash == _bundle().artifact_hash
    assert bundle.manifest.sample_count == 3
    assert bundle.manifest.pairing_policy == PAIRING_POLICY
    assert bundle.manifest.feature_source_dataset_hash == FEATURE_DATA
    assert bundle.manifest.label_source_dataset_hash == LABEL_DATA

    path = tmp_path / "bundle.json"
    bundle.write(path)
    assert TrainingBundleArtifact.read(path) == bundle


def test_source_dataset_hashes_may_differ_but_both_are_bound():
    baseline = _bundle()
    changed = _bundle(labels=_label_artifact(source_dataset_hash="e" * 64))
    assert baseline.manifest.feature_source_dataset_hash != baseline.manifest.label_source_dataset_hash
    assert changed.artifact_hash != baseline.artifact_hash


def test_campaign_split_partition_window_and_universe_must_match():
    feature = _feature_artifact()
    mismatch_builders = (
        _label_artifact(campaign="oss3d1-campaign-002"),
        _label_artifact(split="f" * 64),
        _label_artifact(partition=LabelPartition.DEVELOPMENT),
        _label_artifact(start=BASE + timedelta(hours=1)),
        _label_artifact(end=END + timedelta(hours=1)),
        _label_artifact(universe="e" * 64),
    )
    messages = (
        "campaign_id mismatch",
        "research_split_hash mismatch",
        "partition mismatch",
        "partition_start mismatch",
        "partition_end mismatch",
        "source_universe_hash mismatch",
    )
    for labels, message in zip(mismatch_builders, messages, strict=True):
        with pytest.raises(TrainingBundleCompatibilityError, match=message):
            TrainingBundleArtifact.build(features=feature, labels=labels)


def test_training_bundle_rejects_development_even_when_both_sides_match():
    with pytest.raises(TrainingBundleCompatibilityError, match="TRAIN artifacts only"):
        TrainingBundleArtifact.build(
            features=_feature_artifact(partition=FactorMatrixPartition.DEVELOPMENT),
            labels=_label_artifact(partition=LabelPartition.DEVELOPMENT),
        )


def test_pairing_requires_exact_timestamp_symbol_keyset():
    labels = _label_artifact()
    missing = _label_artifact(rows=labels.rows[:-1])
    with pytest.raises(TrainingBundleCompatibilityError, match="keysets differ"):
        TrainingBundleArtifact.build(features=_feature_artifact(), labels=missing)

    extra_origin = BASE + timedelta(days=3)
    extra = SupervisedLabelRow(
        label_as_of=extra_origin.isoformat(),
        horizon_end=(extra_origin + timedelta(hours=1)).isoformat(),
        available_at=(extra_origin + timedelta(hours=1)).isoformat(),
        symbol="BTCUSDT",
        value=0.02,
    )
    extra_labels = _label_artifact(rows=labels.rows + (extra,))
    with pytest.raises(TrainingBundleCompatibilityError, match="keysets differ"):
        TrainingBundleArtifact.build(features=_feature_artifact(), labels=extra_labels)


def test_build_requires_verified_artifact_types():
    with pytest.raises(TypeError, match="FactorMatrixArtifact"):
        TrainingBundleArtifact.build(features=object(), labels=_label_artifact())
    with pytest.raises(TypeError, match="SupervisedLabelArtifact"):
        TrainingBundleArtifact.build(features=_feature_artifact(), labels=object())


def test_manifest_is_strict_and_hash_bound():
    manifest = _bundle().manifest
    with pytest.raises(TrainingBundleCompatibilityError, match="producer"):
        replace(manifest, producer_id="other")
    with pytest.raises(ValueError, match="campaign_id"):
        replace(manifest, campaign_id="bad campaign")
    for field in (
        "research_split_hash",
        "source_universe_hash",
        "feature_artifact_hash",
        "label_artifact_hash",
        "feature_source_dataset_hash",
        "label_source_dataset_hash",
        "feature_schema_hash",
        "label_definition_hash",
    ):
        with pytest.raises(ValueError, match="sha256"):
            replace(manifest, **{field: "broken"})
    with pytest.raises(TrainingBundleCompatibilityError, match="TRAIN only"):
        replace(manifest, partition="DEVELOPMENT")
    with pytest.raises(TrainingBundleCompatibilityError, match="window"):
        replace(manifest, partition_end=manifest.partition_start)
    for count in (0, True):
        with pytest.raises(ValueError, match="sample_count"):
            replace(manifest, sample_count=count)
    with pytest.raises(TrainingBundleCompatibilityError, match="pairing policy"):
        replace(manifest, pairing_policy="POSITIONAL_JOIN")


def test_artifact_detects_manifest_and_hash_drift():
    bundle = _bundle()
    with pytest.raises(TrainingBundleIntegrityError, match="version"):
        replace(bundle, artifact_version="WRONG")
    with pytest.raises(ValueError, match="sha256"):
        replace(bundle, artifact_hash="broken")
    with pytest.raises(TrainingBundleIntegrityError, match="artifact hash"):
        replace(bundle, artifact_hash="f" * 64)
    with pytest.raises(TrainingBundleIntegrityError, match="artifact hash"):
        replace(bundle, manifest=replace(bundle.manifest, sample_count=99))


def test_read_rejects_missing_invalid_large_duplicate_and_noncanonical_json(tmp_path):
    with pytest.raises(TrainingBundleIntegrityError, match="does not exist"):
        TrainingBundleArtifact.read(tmp_path / "missing.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{broken", encoding="utf-8")
    with pytest.raises(TrainingBundleIntegrityError, match="UTF-8 JSON"):
        TrainingBundleArtifact.read(broken)

    large = tmp_path / "large.json"
    with large.open("wb") as handle:
        handle.truncate(64_001)
    with pytest.raises(TrainingBundleCompatibilityError, match="size limit"):
        TrainingBundleArtifact.read(large)

    bundle = _bundle()
    canonical = tmp_path / "canonical.json"
    bundle.write(canonical)
    raw = canonical.read_text(encoding="utf-8")
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(raw.replace("{", '{"artifact_hash":"' + bundle.artifact_hash + '",', 1), encoding="utf-8")
    with pytest.raises(TrainingBundleIntegrityError, match="duplicate JSON object key"):
        TrainingBundleArtifact.read(duplicate)

    pretty = tmp_path / "pretty.json"
    _write(pretty, bundle.to_dict(), canonical=False)
    with pytest.raises(TrainingBundleIntegrityError, match="not canonical"):
        TrainingBundleArtifact.read(pretty)


def test_read_rejects_schema_extensions_and_manifest_tampering(tmp_path):
    doc = _bundle().to_dict()
    top = deepcopy(doc)
    top["command"] = "train"
    path = tmp_path / "top.json"
    _write(path, top)
    with pytest.raises(TrainingBundleIntegrityError, match="top-level schema"):
        TrainingBundleArtifact.read(path)

    manifest = deepcopy(doc)
    manifest["manifest"]["endpoint"] = "https://example.invalid"
    path = tmp_path / "manifest.json"
    _write(path, manifest)
    with pytest.raises(TrainingBundleIntegrityError, match="manifest schema"):
        TrainingBundleArtifact.read(path)

    tampered = deepcopy(doc)
    tampered["manifest"]["campaign_id"] = "oss3d1-campaign-tampered"
    path = tmp_path / "tampered.json"
    _write(path, tampered)
    with pytest.raises(TrainingBundleIntegrityError, match="artifact hash"):
        TrainingBundleArtifact.read(path)


def test_prediction_binding_creates_immutable_non_authoritative_receipt():
    bundle = _bundle()
    prediction = _prediction(bundle)
    receipt = bundle.bind_prediction(prediction)
    assert receipt.receipt_version == OSS3D1_RECEIPT_VERSION
    assert receipt.training_dataset_hash == bundle.artifact_hash
    assert receipt.training_bundle_manifest_hash == bundle.manifest.fingerprint
    assert receipt.feature_artifact_hash == bundle.manifest.feature_artifact_hash
    assert receipt.label_artifact_hash == bundle.manifest.label_artifact_hash
    assert receipt.prediction_artifact_hash == prediction.artifact_hash
    assert receipt.campaign_id == CAMPAIGN
    assert receipt.research_split_hash == SPLIT
    assert receipt.model_family == prediction.manifest.model_family
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert receipt.fingerprint == bundle.bind_prediction(prediction).fingerprint


def test_prediction_binding_requires_exact_bundle_hash_feature_schema_and_train_window():
    bundle = _bundle()
    with pytest.raises(TrainingBundleCompatibilityError, match="training_dataset_hash mismatch"):
        bundle.bind_prediction(_prediction(bundle, training_hash="f" * 64))
    with pytest.raises(TrainingBundleCompatibilityError, match="feature_schema_hash mismatch"):
        bundle.bind_prediction(_prediction(bundle, feature_schema_hash="e" * 64))
    with pytest.raises(TrainingBundleCompatibilityError, match="train_start mismatch"):
        bundle.bind_prediction(_prediction(bundle, train_start=BASE - timedelta(days=1)))
    with pytest.raises(TrainingBundleCompatibilityError, match="train_end mismatch"):
        bundle.bind_prediction(_prediction(bundle, train_end=END - timedelta(hours=1)))
    with pytest.raises(TypeError, match="QlibPredictionArtifact"):
        bundle.bind_prediction(object())


def test_receipt_constructor_denies_identity_and_authority_drift():
    receipt = _bundle().bind_prediction(_prediction(_bundle()))
    with pytest.raises(TrainingBundleIntegrityError, match="receipt version"):
        replace(receipt, receipt_version="WRONG")
    for field in (
        "training_dataset_hash",
        "training_bundle_manifest_hash",
        "feature_artifact_hash",
        "label_artifact_hash",
        "prediction_artifact_hash",
        "research_split_hash",
        "feature_schema_hash",
        "label_definition_hash",
        "model_config_hash",
        "producer_code_hash",
    ):
        with pytest.raises(ValueError, match="sha256"):
            replace(receipt, **{field: "broken"})
    with pytest.raises(TrainingBundleIntegrityError, match="campaign_id"):
        replace(receipt, campaign_id="bad campaign")
    with pytest.raises(TrainingBundleIntegrityError, match="model_family"):
        replace(receipt, model_family="bad model")
    with pytest.raises(TrainingBundleIntegrityError, match="qlib_version"):
        replace(receipt, qlib_version="")
    with pytest.raises(TrainingBundleCompatibilityError, match="authorize execution"):
        replace(receipt, execution_authorized=True)
    with pytest.raises(TrainingBundleCompatibilityError, match="authorize execution"):
        replace(receipt, paper_execution_authorized=True)
    with pytest.raises(TrainingBundleCompatibilityError, match="capital or LIVE"):
        replace(receipt, capital_authority="USD")
    with pytest.raises(TrainingBundleCompatibilityError, match="capital or LIVE"):
        replace(receipt, live_trading="ENABLED")


def test_receipt_fingerprint_binds_prediction_and_model_identity():
    bundle = _bundle()
    receipt = bundle.bind_prediction(_prediction(bundle))
    assert replace(receipt, prediction_artifact_hash="c" * 64).fingerprint != receipt.fingerprint
    assert replace(receipt, model_config_hash="d" * 64).fingerprint != receipt.fingerprint
