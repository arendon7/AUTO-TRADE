from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from autotrade.research.oss3_development_inference import (
    DEVELOPMENT_POINT_IN_TIME_POLICY,
    LABEL_ACCESS_POLICY,
    OSS3D2A_DRY_RUN_VERSION,
    OSS3D2A_RECEIPT_VERSION,
    OSS3D2A_REQUEST_VERSION,
    PREDICTION_KEY_POLICY,
    DevelopmentInferenceCompatibilityError,
    DevelopmentInferenceGovernanceError,
    DevelopmentInferenceIntegrityError,
    DevelopmentInferenceRequest,
)
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
CAMPAIGN = "oss3d2a-campaign-001"
SPLIT = "1" * 64
UNIVERSE = "2" * 64
TRAIN_FEATURE_DATA = "3" * 64
TRAIN_LABEL_DATA = "4" * 64
DEV_FEATURE_DATA = "5" * 64
FEATURE_CODE = "6" * 64
LABEL_CODE = "7" * 64
RUNNER_CODE = "8" * 64
MODEL_CONFIG = "9" * 64
QLIB_VERSION = "0.9.7"
MODEL_FAMILY = "lightgbm_ranker"


def _feature_defs(*, formula_hash="a" * 64):
    return (
        FactorDefinition(
            name="momentum_20",
            dtype="float64",
            role="FEATURE",
            formula_hash=formula_hash,
            source_id="aligned-bars-v1",
            source_hash="b" * 64,
            lookback_bars=20,
        ),
        FactorDefinition(
            name="volatility_20",
            dtype="float64",
            role="FEATURE",
            formula_hash="c" * 64,
            source_id="aligned-bars-v1",
            source_hash="d" * 64,
            lookback_bars=20,
        ),
    )


def _factor_rows(start: datetime, *, values_shift=0.0, first_symbol="BTCUSDT"):
    identities = (
        (start, first_symbol),
        (start, "ETHUSDT"),
        (start + timedelta(days=1), "BTCUSDT"),
    )
    return tuple(
        FactorMatrixRow(
            as_of=timestamp.isoformat(),
            available_at=(timestamp - timedelta(minutes=1)).isoformat(),
            symbol=symbol,
            values=(float(index) + values_shift, float(index) / 10 + values_shift),
        )
        for index, (timestamp, symbol) in enumerate(identities, start=1)
    )


def _features(
    *,
    partition=FactorMatrixPartition.TRAIN,
    start=BASE,
    end=TRAIN_END,
    campaign=CAMPAIGN,
    split=SPLIT,
    universe=UNIVERSE,
    source_dataset_hash=TRAIN_FEATURE_DATA,
    formula_hash="a" * 64,
    rows=None,
):
    if rows is None:
        row_start = BASE + timedelta(days=1) if partition is FactorMatrixPartition.TRAIN else start
        rows = _factor_rows(row_start)
    return FactorMatrixArtifact.build(
        campaign_id=campaign,
        research_split_hash=split,
        partition=partition,
        partition_start=start,
        partition_end=end,
        producer_code_hash=FEATURE_CODE,
        source_dataset_hash=source_dataset_hash,
        source_universe_hash=universe,
        features=_feature_defs(formula_hash=formula_hash),
        rows=rows,
    )


def _labels(*, source_dataset_hash=TRAIN_LABEL_DATA):
    identities = (
        (BASE + timedelta(days=1), "BTCUSDT"),
        (BASE + timedelta(days=1), "ETHUSDT"),
        (BASE + timedelta(days=2), "BTCUSDT"),
    )
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=timestamp.isoformat(),
            horizon_end=(timestamp + timedelta(hours=1)).isoformat(),
            available_at=(timestamp + timedelta(hours=1, minutes=1)).isoformat(),
            symbol=symbol,
            value=float(index) / 100,
        )
        for index, (timestamp, symbol) in enumerate(identities, start=1)
    )
    return SupervisedLabelArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=LabelPartition.TRAIN,
        partition_start=BASE,
        partition_end=TRAIN_END,
        producer_code_hash=LABEL_CODE,
        source_dataset_hash=source_dataset_hash,
        source_universe_hash=UNIVERSE,
        label=LabelDefinition(
            name="forward_return",
            dtype="float64",
            role="LABEL",
            formula_hash="e" * 64,
            source_id="aligned-bars-v1",
            source_hash="f" * 64,
        ),
        rows=rows,
    )


def _bundle(*, label_source_dataset_hash=TRAIN_LABEL_DATA):
    return TrainingBundleArtifact.build(
        features=_features(),
        labels=_labels(source_dataset_hash=label_source_dataset_hash),
    )


def _dev_features(**overrides):
    start = overrides.pop("start", DEV_START)
    end = overrides.pop("end", DEV_END)
    rows = overrides.pop("rows", None)
    if rows is None:
        rows = _factor_rows(start, values_shift=overrides.pop("values_shift", 0.0))
    return _features(
        partition=overrides.pop("partition", FactorMatrixPartition.DEVELOPMENT),
        start=start,
        end=end,
        source_dataset_hash=overrides.pop("source_dataset_hash", DEV_FEATURE_DATA),
        rows=rows,
        **overrides,
    )


def _request(*, bundle=None, dev=None, **overrides):
    bundle = _bundle() if bundle is None else bundle
    dev = _dev_features() if dev is None else dev
    return DevelopmentInferenceRequest.build(
        training_bundle=bundle,
        development_features=dev,
        model_family=overrides.get("model_family", MODEL_FAMILY),
        model_config_hash=overrides.get("model_config_hash", MODEL_CONFIG),
        required_qlib_version=overrides.get("required_qlib_version", QLIB_VERSION),
        expected_runner_code_hash=overrides.get("expected_runner_code_hash", RUNNER_CODE),
    )


def _prediction(
    request,
    dev,
    *,
    training_dataset_hash=None,
    feature_schema_hash=None,
    model_family=None,
    model_config_hash=None,
    qlib_version=None,
    producer_code_hash=None,
    train_start=None,
    train_end=None,
    inference_start=None,
    inference_end=None,
    rows=None,
):
    m = request.manifest
    if rows is None:
        rows = tuple(
            QlibPredictionRow(timestamp=row.as_of, symbol=row.symbol, score=float(index) / 10)
            for index, row in enumerate(dev.rows, start=1)
        )
    return QlibPredictionArtifact.build(
        qlib_version=m.required_qlib_version if qlib_version is None else qlib_version,
        model_family=m.model_family if model_family is None else model_family,
        model_config_hash=m.model_config_hash if model_config_hash is None else model_config_hash,
        training_dataset_hash=m.training_bundle_hash if training_dataset_hash is None else training_dataset_hash,
        feature_schema_hash=m.feature_schema_hash if feature_schema_hash is None else feature_schema_hash,
        producer_code_hash=m.expected_runner_code_hash if producer_code_hash is None else producer_code_hash,
        train_start=datetime.fromisoformat(m.train_start) if train_start is None else train_start,
        train_end=datetime.fromisoformat(m.train_end) if train_end is None else train_end,
        inference_start=datetime.fromisoformat(m.inference_start) if inference_start is None else inference_start,
        inference_end=datetime.fromisoformat(m.inference_end) if inference_end is None else inference_end,
        rows=rows,
    )


def _write(path, document, *, canonical=True):
    if canonical:
        raw = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"
    else:
        raw = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.write_text(raw, encoding="utf-8")


def test_request_is_deterministic_canonical_and_round_trips(tmp_path):
    bundle = _bundle()
    dev = _dev_features()
    request = _request(bundle=bundle, dev=dev)
    assert request.request_version == OSS3D2A_REQUEST_VERSION
    assert request.request_hash == _request(bundle=bundle, dev=dev).request_hash
    assert request.manifest.development_partition == "DEVELOPMENT"
    assert request.manifest.development_point_in_time_policy == DEVELOPMENT_POINT_IN_TIME_POLICY
    assert request.manifest.prediction_key_policy == PREDICTION_KEY_POLICY
    assert request.manifest.label_access_policy == LABEL_ACCESS_POLICY
    assert request.manifest.training_bundle_hash == bundle.artifact_hash
    assert request.manifest.development_feature_artifact_hash == dev.artifact_hash
    path = tmp_path / "inference-request.json"
    request.write(path)
    loaded = DevelopmentInferenceRequest.read(path)
    assert loaded == request
    loaded.verify_inputs(training_bundle=bundle, development_features=dev)


def test_request_requires_verified_input_types():
    with pytest.raises(TypeError, match="TrainingBundleArtifact"):
        DevelopmentInferenceRequest.build(
            training_bundle=object(),
            development_features=_dev_features(),
            model_family=MODEL_FAMILY,
            model_config_hash=MODEL_CONFIG,
            required_qlib_version=QLIB_VERSION,
            expected_runner_code_hash=RUNNER_CODE,
        )
    with pytest.raises(TypeError, match="FactorMatrixArtifact"):
        DevelopmentInferenceRequest.build(
            training_bundle=_bundle(),
            development_features=object(),
            model_family=MODEL_FAMILY,
            model_config_hash=MODEL_CONFIG,
            required_qlib_version=QLIB_VERSION,
            expected_runner_code_hash=RUNNER_CODE,
        )


def test_request_accepts_development_features_only():
    train_shaped = _features(
        partition=FactorMatrixPartition.TRAIN,
        start=DEV_START,
        end=DEV_END,
        source_dataset_hash=DEV_FEATURE_DATA,
        rows=_factor_rows(DEV_START),
    )
    with pytest.raises(DevelopmentInferenceGovernanceError, match="DEVELOPMENT feature"):
        _request(dev=train_shaped)


def test_campaign_split_universe_and_feature_schema_must_match_train_bundle():
    mismatches = (
        (_dev_features(campaign="oss3d2a-campaign-002"), "campaign_id mismatch"),
        (_dev_features(split="a" * 64), "research_split_hash mismatch"),
        (_dev_features(universe="b" * 64), "source_universe_hash mismatch"),
        (_dev_features(formula_hash="0" * 64), "feature_schema_hash mismatch"),
    )
    for dev, message in mismatches:
        with pytest.raises(DevelopmentInferenceCompatibilityError, match=message):
            _request(dev=dev)


def test_train_window_may_touch_but_never_overlap_development():
    touching = _dev_features(start=TRAIN_END)
    assert _request(dev=touching).manifest.inference_start == TRAIN_END.isoformat()

    overlap_start = TRAIN_END - timedelta(hours=1)
    overlap = _dev_features(start=overlap_start, end=DEV_END, rows=_factor_rows(overlap_start))
    with pytest.raises(DevelopmentInferenceCompatibilityError, match="may not overlap"):
        _request(dev=overlap)


def test_model_and_runtime_identity_are_strict_and_hash_bound():
    baseline = _request()
    assert _request(model_family="linear_ranker").request_hash != baseline.request_hash
    assert _request(model_config_hash="a" * 64).request_hash != baseline.request_hash
    assert _request(required_qlib_version="0.9.8").request_hash != baseline.request_hash
    assert _request(expected_runner_code_hash="b" * 64).request_hash != baseline.request_hash
    with pytest.raises(ValueError, match="model_family"):
        _request(model_family="bad model")
    with pytest.raises(ValueError, match="model_config_hash"):
        _request(model_config_hash="broken")
    with pytest.raises(ValueError, match="required_qlib_version"):
        _request(required_qlib_version="bad version")
    with pytest.raises(ValueError, match="expected_runner_code_hash"):
        _request(expected_runner_code_hash="broken")


def test_development_payload_and_keyset_both_change_request_identity():
    baseline_dev = _dev_features()
    baseline = _request(dev=baseline_dev)
    changed_payload_dev = _dev_features(values_shift=100.0)
    changed_payload = _request(dev=changed_payload_dev)
    assert changed_payload.manifest.inference_keyset_hash == baseline.manifest.inference_keyset_hash
    assert changed_payload.manifest.development_row_payload_hash != baseline.manifest.development_row_payload_hash
    assert changed_payload.request_hash != baseline.request_hash

    changed_keys_dev = _dev_features(rows=_factor_rows(DEV_START, first_symbol="ADAUSDT"))
    changed_keys = _request(dev=changed_keys_dev)
    assert changed_keys.manifest.inference_keyset_hash != baseline.manifest.inference_keyset_hash
    assert changed_keys.request_hash != baseline.request_hash


def test_verify_inputs_rejects_stale_training_or_development_artifacts():
    bundle = _bundle()
    dev = _dev_features()
    request = _request(bundle=bundle, dev=dev)
    changed_bundle = _bundle(label_source_dataset_hash="a" * 64)
    with pytest.raises(DevelopmentInferenceCompatibilityError, match="training_bundle_hash mismatch"):
        request.verify_inputs(training_bundle=changed_bundle, development_features=dev)
    changed_dev = _dev_features(values_shift=1.0)
    with pytest.raises(
        DevelopmentInferenceCompatibilityError,
        match="development_feature_artifact_hash mismatch",
    ):
        request.verify_inputs(training_bundle=bundle, development_features=changed_dev)
    with pytest.raises(TypeError, match="TrainingBundleArtifact"):
        request.verify_inputs(training_bundle=object(), development_features=dev)
    with pytest.raises(TypeError, match="FactorMatrixArtifact"):
        request.verify_inputs(training_bundle=bundle, development_features=object())


def test_dry_run_revalidates_inputs_and_never_impersonates_qlib():
    bundle = _bundle()
    dev = _dev_features()
    request = _request(bundle=bundle, dev=dev)
    evidence = request.dry_run(training_bundle=bundle, development_features=dev)
    assert evidence.evidence_version == OSS3D2A_DRY_RUN_VERSION
    assert evidence.request_hash == request.request_hash
    assert evidence.development_labels_loaded is False
    assert evidence.final_holdout_loaded is False
    assert evidence.external_runtime_invoked is False
    assert evidence.qlib_imported is False
    assert evidence.prediction_artifact_created is False
    assert evidence.execution_authorized is False
    assert evidence.paper_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"
    assert evidence.fingerprint == request.dry_run(
        training_bundle=bundle,
        development_features=dev,
    ).fingerprint


def test_dry_run_constructor_fails_closed_on_label_runtime_or_authority_drift():
    bundle = _bundle()
    dev = _dev_features()
    evidence = _request(bundle=bundle, dev=dev).dry_run(
        training_bundle=bundle,
        development_features=dev,
    )
    for field in ("development_labels_loaded", "final_holdout_loaded"):
        with pytest.raises(DevelopmentInferenceGovernanceError, match="cannot load"):
            replace(evidence, **{field: True})
    for field in ("external_runtime_invoked", "qlib_imported", "prediction_artifact_created"):
        with pytest.raises(DevelopmentInferenceGovernanceError, match="cannot invoke Qlib"):
            replace(evidence, **{field: True})
    for field in ("execution_authorized", "paper_execution_authorized"):
        with pytest.raises(DevelopmentInferenceGovernanceError, match="cannot authorize execution"):
            replace(evidence, **{field: True})
    with pytest.raises(DevelopmentInferenceGovernanceError, match="capital or LIVE"):
        replace(evidence, capital_authority="TRADE")
    with pytest.raises(DevelopmentInferenceGovernanceError, match="capital or LIVE"):
        replace(evidence, live_trading="ENABLED")
    with pytest.raises(DevelopmentInferenceGovernanceError, match="label policy"):
        replace(evidence, label_access_policy="ALLOW")


def test_manifest_and_request_constructors_fail_closed_on_identity_policy_and_time_drift():
    request = _request()
    manifest = request.manifest
    with pytest.raises(DevelopmentInferenceGovernanceError, match="producer"):
        replace(manifest, producer_id="other")
    with pytest.raises(ValueError, match="campaign_id"):
        replace(manifest, campaign_id="bad campaign")
    for field in (
        "research_split_hash",
        "training_bundle_hash",
        "training_bundle_manifest_hash",
        "train_feature_artifact_hash",
        "train_label_artifact_hash",
        "source_universe_hash",
        "feature_schema_hash",
        "label_definition_hash",
        "development_feature_artifact_hash",
        "development_source_dataset_hash",
        "development_row_payload_hash",
        "inference_keyset_hash",
        "model_config_hash",
        "expected_runner_code_hash",
    ):
        with pytest.raises(ValueError, match="sha256"):
            replace(manifest, **{field: "broken"})
    with pytest.raises(DevelopmentInferenceGovernanceError, match="DEVELOPMENT feature"):
        replace(manifest, development_partition="TRAIN")
    with pytest.raises(DevelopmentInferenceGovernanceError, match="point-in-time"):
        replace(manifest, development_point_in_time_policy="ALLOW_FUTURE")
    with pytest.raises(DevelopmentInferenceGovernanceError, match="prediction-key"):
        replace(manifest, prediction_key_policy="PARTIAL")
    with pytest.raises(DevelopmentInferenceGovernanceError, match="label-access"):
        replace(manifest, label_access_policy="ALLOW")
    with pytest.raises(DevelopmentInferenceCompatibilityError, match="may not overlap"):
        replace(manifest, train_end=(DEV_START + timedelta(minutes=1)).isoformat())
    with pytest.raises(DevelopmentInferenceCompatibilityError, match="inference window"):
        replace(manifest, inference_end=manifest.inference_start)
    with pytest.raises(ValueError, match="inference_row_count"):
        replace(manifest, inference_row_count=0)
    with pytest.raises(ValueError, match="inference_row_count"):
        replace(manifest, inference_row_count=True)
    with pytest.raises(ValueError, match="canonical UTC"):
        replace(manifest, inference_start="not-a-time")
    with pytest.raises(ValueError, match="canonical UTC serialization"):
        replace(manifest, inference_start=DEV_START.isoformat().replace("+00:00", "Z"))
    with pytest.raises(DevelopmentInferenceIntegrityError, match="request version"):
        replace(request, request_version="WRONG")
    with pytest.raises(ValueError, match="request_hash"):
        replace(request, request_hash="broken")
    with pytest.raises(DevelopmentInferenceIntegrityError, match="request hash"):
        replace(request, request_hash="a" * 64)


def test_request_reader_rejects_missing_invalid_large_duplicate_and_noncanonical_json(tmp_path):
    with pytest.raises(DevelopmentInferenceIntegrityError, match="does not exist"):
        DevelopmentInferenceRequest.read(tmp_path / "missing.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{broken", encoding="utf-8")
    with pytest.raises(DevelopmentInferenceIntegrityError, match="valid UTF-8 JSON"):
        DevelopmentInferenceRequest.read(invalid)

    large = tmp_path / "large.json"
    with large.open("wb") as handle:
        handle.truncate(96_001)
    with pytest.raises(DevelopmentInferenceGovernanceError, match="size limit"):
        DevelopmentInferenceRequest.read(large)

    request = _request()
    canonical = tmp_path / "canonical.json"
    request.write(canonical)
    raw = canonical.read_text(encoding="utf-8")
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        raw.replace("{", '{"request_hash":"' + request.request_hash + '",', 1),
        encoding="utf-8",
    )
    with pytest.raises(DevelopmentInferenceIntegrityError, match="duplicate JSON object key"):
        DevelopmentInferenceRequest.read(duplicate)

    pretty = tmp_path / "pretty.json"
    _write(pretty, request.to_dict(), canonical=False)
    with pytest.raises(DevelopmentInferenceIntegrityError, match="not canonical"):
        DevelopmentInferenceRequest.read(pretty)


def test_request_reader_rejects_schema_extension_and_hash_tampering(tmp_path):
    document = _request().to_dict()
    top = deepcopy(document)
    top["command"] = "run-qlib"
    path = tmp_path / "top.json"
    _write(path, top)
    with pytest.raises(DevelopmentInferenceIntegrityError, match="top-level schema"):
        DevelopmentInferenceRequest.read(path)

    manifest = deepcopy(document)
    manifest["manifest"]["development_label_hash"] = "a" * 64
    path = tmp_path / "manifest.json"
    _write(path, manifest)
    with pytest.raises(DevelopmentInferenceIntegrityError, match="manifest schema"):
        DevelopmentInferenceRequest.read(path)

    tampered = deepcopy(document)
    tampered["manifest"]["model_family"] = "different_ranker"
    path = tmp_path / "tampered.json"
    _write(path, tampered)
    with pytest.raises(DevelopmentInferenceIntegrityError, match="request hash"):
        DevelopmentInferenceRequest.read(path)


def test_exact_prediction_binding_creates_non_authoritative_receipt():
    bundle = _bundle()
    dev = _dev_features()
    request = _request(bundle=bundle, dev=dev)
    prediction = _prediction(request, dev)
    receipt = request.bind_prediction(
        prediction=prediction,
        training_bundle=bundle,
        development_features=dev,
    )
    assert receipt.receipt_version == OSS3D2A_RECEIPT_VERSION
    assert receipt.request_hash == request.request_hash
    assert receipt.request_manifest_hash == request.manifest.fingerprint
    assert receipt.prediction_artifact_hash == prediction.artifact_hash
    assert receipt.training_bundle_hash == bundle.artifact_hash
    assert receipt.development_feature_artifact_hash == dev.artifact_hash
    assert receipt.inference_keyset_hash == request.manifest.inference_keyset_hash
    assert receipt.prediction_count == len(dev.rows)
    assert receipt.development_labels_loaded is False
    assert receipt.final_holdout_loaded is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert receipt.fingerprint == request.bind_prediction(
        prediction=prediction,
        training_bundle=bundle,
        development_features=dev,
    ).fingerprint


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"training_dataset_hash": "a" * 64}, "training_dataset_hash mismatch"),
        ({"feature_schema_hash": "b" * 64}, "feature_schema_hash mismatch"),
        ({"model_family": "linear_ranker"}, "model_family mismatch"),
        ({"model_config_hash": "c" * 64}, "model_config_hash mismatch"),
        ({"qlib_version": "0.9.8"}, "qlib_version mismatch"),
        ({"producer_code_hash": "d" * 64}, "producer_code_hash mismatch"),
        ({"train_start": BASE + timedelta(hours=1)}, "train_start mismatch"),
        ({"train_end": TRAIN_END - timedelta(hours=1)}, "train_end mismatch"),
        ({"inference_end": DEV_END + timedelta(hours=1)}, "inference_end mismatch"),
    ],
)
def test_prediction_binding_rejects_model_training_runtime_and_window_drift(overrides, message):
    bundle = _bundle()
    dev = _dev_features()
    request = _request(bundle=bundle, dev=dev)
    prediction = _prediction(request, dev, **overrides)
    with pytest.raises(DevelopmentInferenceCompatibilityError, match=message):
        request.bind_prediction(
            prediction=prediction,
            training_bundle=bundle,
            development_features=dev,
        )


def test_prediction_binding_rejects_inference_start_drift_before_keyset_check():
    bundle = _bundle()
    dev = _dev_features()
    request = _request(bundle=bundle, dev=dev)
    shifted_start = DEV_START + timedelta(minutes=1)
    shifted_rows = tuple(
        QlibPredictionRow(
            timestamp=(datetime.fromisoformat(row.as_of) + timedelta(minutes=1)).isoformat(),
            symbol=row.symbol,
            score=float(index) / 10,
        )
        for index, row in enumerate(dev.rows, start=1)
    )
    prediction = _prediction(
        request,
        dev,
        inference_start=shifted_start,
        rows=shifted_rows,
    )
    with pytest.raises(DevelopmentInferenceCompatibilityError, match="inference_start mismatch"):
        request.bind_prediction(
            prediction=prediction,
            training_bundle=bundle,
            development_features=dev,
        )


def test_prediction_binding_rejects_missing_extra_and_wrong_prediction_keys():
    bundle = _bundle()
    dev = _dev_features()
    request = _request(bundle=bundle, dev=dev)
    exact_rows = tuple(
        QlibPredictionRow(timestamp=row.as_of, symbol=row.symbol, score=float(index) / 10)
        for index, row in enumerate(dev.rows, start=1)
    )

    with pytest.raises(DevelopmentInferenceCompatibilityError, match="prediction_count mismatch"):
        request.bind_prediction(
            prediction=_prediction(request, dev, rows=exact_rows[:-1]),
            training_bundle=bundle,
            development_features=dev,
        )

    extra = QlibPredictionRow(
        timestamp=(DEV_START + timedelta(days=2)).isoformat(),
        symbol="BTCUSDT",
        score=0.9,
    )
    with pytest.raises(DevelopmentInferenceCompatibilityError, match="prediction_count mismatch"):
        request.bind_prediction(
            prediction=_prediction(request, dev, rows=exact_rows + (extra,)),
            training_bundle=bundle,
            development_features=dev,
        )

    wrong_key_rows = (
        QlibPredictionRow(timestamp=exact_rows[0].timestamp, symbol="ADAUSDT", score=0.1),
        exact_rows[1],
        exact_rows[2],
    )
    with pytest.raises(DevelopmentInferenceCompatibilityError, match="prediction keyset mismatch"):
        request.bind_prediction(
            prediction=_prediction(request, dev, rows=wrong_key_rows),
            training_bundle=bundle,
            development_features=dev,
        )


def test_prediction_binding_revalidates_concrete_inputs_and_prediction_type():
    bundle = _bundle()
    dev = _dev_features()
    request = _request(bundle=bundle, dev=dev)
    prediction = _prediction(request, dev)
    with pytest.raises(DevelopmentInferenceCompatibilityError, match="training_bundle_hash mismatch"):
        request.bind_prediction(
            prediction=prediction,
            training_bundle=_bundle(label_source_dataset_hash="a" * 64),
            development_features=dev,
        )
    with pytest.raises(
        DevelopmentInferenceCompatibilityError,
        match="development_feature_artifact_hash mismatch",
    ):
        request.bind_prediction(
            prediction=prediction,
            training_bundle=bundle,
            development_features=_dev_features(values_shift=5.0),
        )
    with pytest.raises(TypeError, match="QlibPredictionArtifact"):
        request.bind_prediction(
            prediction=object(),
            training_bundle=bundle,
            development_features=dev,
        )


def test_receipt_constructor_denies_identity_label_and_authority_drift():
    bundle = _bundle()
    dev = _dev_features()
    request = _request(bundle=bundle, dev=dev)
    receipt = request.bind_prediction(
        prediction=_prediction(request, dev),
        training_bundle=bundle,
        development_features=dev,
    )
    with pytest.raises(DevelopmentInferenceIntegrityError, match="receipt version"):
        replace(receipt, receipt_version="WRONG")
    for field in (
        "request_hash",
        "request_manifest_hash",
        "prediction_artifact_hash",
        "prediction_manifest_hash",
        "research_split_hash",
        "training_bundle_hash",
        "development_feature_artifact_hash",
        "source_universe_hash",
        "feature_schema_hash",
        "label_definition_hash",
        "inference_keyset_hash",
        "model_config_hash",
        "producer_code_hash",
    ):
        with pytest.raises(ValueError, match="sha256"):
            replace(receipt, **{field: "broken"})
    with pytest.raises(DevelopmentInferenceIntegrityError, match="campaign_id"):
        replace(receipt, campaign_id="bad campaign")
    with pytest.raises(DevelopmentInferenceIntegrityError, match="model_family"):
        replace(receipt, model_family="bad model")
    with pytest.raises(DevelopmentInferenceIntegrityError, match="qlib_version"):
        replace(receipt, qlib_version="bad version")
    with pytest.raises(DevelopmentInferenceIntegrityError, match="prediction_count"):
        replace(receipt, prediction_count=0)
    with pytest.raises(DevelopmentInferenceIntegrityError, match="train/inference"):
        replace(receipt, train_end=(DEV_START + timedelta(minutes=1)).isoformat())
    with pytest.raises(DevelopmentInferenceGovernanceError, match="cannot claim"):
        replace(receipt, development_labels_loaded=True)
    with pytest.raises(DevelopmentInferenceGovernanceError, match="cannot claim"):
        replace(receipt, final_holdout_loaded=True)
    with pytest.raises(DevelopmentInferenceGovernanceError, match="authorize execution"):
        replace(receipt, execution_authorized=True)
    with pytest.raises(DevelopmentInferenceGovernanceError, match="authorize execution"):
        replace(receipt, paper_execution_authorized=True)
    with pytest.raises(DevelopmentInferenceGovernanceError, match="capital or LIVE"):
        replace(receipt, capital_authority="TRADE")
    with pytest.raises(DevelopmentInferenceGovernanceError, match="capital or LIVE"):
        replace(receipt, live_trading="ENABLED")
