from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math

import pytest

from autotrade.research.oss3_qlib_artifact import (
    OSS3A_ARTIFACT_VERSION,
    OSS3A_EVIDENCE_VERSION,
    QLIB_LICENSE_ID,
    QLIB_PRODUCER_ID,
    QlibArtifactGovernanceError,
    QlibArtifactIntegrityError,
    QlibPredictionArtifact,
    QlibPredictionEvidence,
    QlibPredictionManifest,
    QlibPredictionRow,
)


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64


def _rows() -> tuple[QlibPredictionRow, ...]:
    return (
        QlibPredictionRow(
            timestamp=(BASE + timedelta(days=31)).isoformat(), symbol="BTCUSDT", score=0.25
        ),
        QlibPredictionRow(
            timestamp=(BASE + timedelta(days=31)).isoformat(), symbol="ETHUSDT", score=-0.10
        ),
        QlibPredictionRow(
            timestamp=(BASE + timedelta(days=32)).isoformat(), symbol="BTCUSDT", score=0.15
        ),
        QlibPredictionRow(
            timestamp=(BASE + timedelta(days=32)).isoformat(), symbol="ETHUSDT", score=0.40
        ),
    )


def _artifact(rows=None) -> QlibPredictionArtifact:
    return QlibPredictionArtifact.build(
        qlib_version="0.9.7",
        model_family="LightGBMRanker",
        model_config_hash=H1,
        training_dataset_hash=H2,
        feature_schema_hash=H3,
        producer_code_hash=H4,
        train_start=BASE,
        train_end=BASE + timedelta(days=30),
        inference_start=BASE + timedelta(days=31),
        inference_end=BASE + timedelta(days=40),
        rows=_rows() if rows is None else rows,
    )


def _write_document(path, document) -> None:
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=True) + "\n",
        encoding="utf-8",
    )


def test_build_round_trip_and_research_evidence_are_deterministic(tmp_path):
    artifact = _artifact()
    assert artifact.artifact_version == OSS3A_ARTIFACT_VERSION
    assert artifact.manifest.producer_id == QLIB_PRODUCER_ID
    assert artifact.manifest.producer_license == QLIB_LICENSE_ID
    assert artifact.manifest.prediction_count == 4
    assert artifact.artifact_hash == _artifact().artifact_hash

    target = tmp_path / "predictions.json"
    artifact.write(target)
    restored = QlibPredictionArtifact.read(target)
    assert restored == artifact
    assert restored.to_dict() == artifact.to_dict()

    evidence = restored.to_research_evidence()
    assert evidence.evidence_version == OSS3A_EVIDENCE_VERSION
    assert evidence.artifact_hash == artifact.artifact_hash
    assert evidence.manifest_fingerprint == artifact.manifest.fingerprint
    assert evidence.prediction_payload_hash == artifact.manifest.prediction_payload_hash
    assert evidence.execution_authorized is False
    assert evidence.paper_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"
    assert evidence.fingerprint == restored.to_research_evidence().fingerprint


def test_write_is_canonical_and_does_not_persist_python_objects(tmp_path):
    artifact = _artifact()
    target = tmp_path / "artifact.json"
    artifact.write(target)
    raw = target.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert parsed == artifact.to_dict()
    assert "pickle" not in raw.lower()
    assert "__reduce__" not in raw


def test_prediction_row_requires_canonical_utc_symbol_and_finite_score():
    with pytest.raises(ValueError, match="timezone-aware"):
        QlibPredictionRow("2026-01-01T00:00:00", "BTCUSDT", 1.0)
    with pytest.raises(ValueError, match="canonical UTC"):
        QlibPredictionRow("2026-01-01T01:00:00+01:00", "BTCUSDT", 1.0)
    with pytest.raises(ValueError, match="canonical \+00:00"):
        QlibPredictionRow("2026-01-01T00:00:00Z", "BTCUSDT", 1.0)
    with pytest.raises(ValueError, match="symbol"):
        QlibPredictionRow(BASE.isoformat(), "bad symbol", 1.0)
    with pytest.raises(ValueError, match="numeric"):
        QlibPredictionRow(BASE.isoformat(), "BTCUSDT", True)
    for score in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite"):
            QlibPredictionRow(BASE.isoformat(), "BTCUSDT", score)


def test_manifest_requires_exact_qlib_identity_and_mit_license():
    artifact = _artifact()
    manifest = artifact.manifest
    with pytest.raises(QlibArtifactGovernanceError, match="microsoft/qlib"):
        replace(manifest, producer_id="someone/else")
    with pytest.raises(QlibArtifactGovernanceError, match="MIT"):
        replace(manifest, producer_license="GPL-3.0")
    with pytest.raises(ValueError, match="qlib_version"):
        replace(manifest, qlib_version="bad version")
    with pytest.raises(ValueError, match="model_family"):
        replace(manifest, model_family="")


def test_manifest_requires_hash_bound_provenance():
    manifest = _artifact().manifest
    for field in (
        "model_config_hash",
        "training_dataset_hash",
        "feature_schema_hash",
        "producer_code_hash",
        "prediction_payload_hash",
    ):
        with pytest.raises(ValueError, match="sha256"):
            replace(manifest, **{field: "NOT-A-HASH"})


def test_manifest_rejects_training_inference_leakage_and_invalid_windows():
    manifest = _artifact().manifest
    with pytest.raises(QlibArtifactGovernanceError, match="training window must be positive"):
        replace(manifest, train_start=manifest.train_end)
    with pytest.raises(QlibArtifactGovernanceError, match="overlap"):
        replace(manifest, train_end=(BASE + timedelta(days=32)).isoformat())
    with pytest.raises(QlibArtifactGovernanceError, match="inference window must be positive"):
        replace(manifest, inference_end=manifest.inference_start)


def test_manifest_prediction_count_is_bounded():
    manifest = _artifact().manifest
    with pytest.raises(ValueError, match="prediction_count"):
        replace(manifest, prediction_count=0)
    with pytest.raises(ValueError, match="prediction_count"):
        replace(manifest, prediction_count=True)
    with pytest.raises(ValueError, match="prediction_count"):
        replace(manifest, prediction_count=2_000_001)


def test_artifact_rejects_unsorted_duplicate_and_out_of_window_rows():
    rows = _rows()
    with pytest.raises(QlibArtifactGovernanceError, match="canonically sorted"):
        _artifact(rows=(rows[1], rows[0], rows[2], rows[3]))

    duplicate = (rows[0], rows[0])
    with pytest.raises(QlibArtifactGovernanceError, match="duplicate"):
        _artifact(rows=duplicate)

    too_early = QlibPredictionRow(
        timestamp=(BASE + timedelta(days=30)).isoformat(), symbol="BTCUSDT", score=0.2
    )
    with pytest.raises(QlibArtifactGovernanceError, match="outside"):
        _artifact(rows=(too_early,))

    at_end = QlibPredictionRow(
        timestamp=(BASE + timedelta(days=40)).isoformat(), symbol="BTCUSDT", score=0.2
    )
    with pytest.raises(QlibArtifactGovernanceError, match="outside"):
        _artifact(rows=(at_end,))


def test_artifact_rejects_empty_predictions():
    with pytest.raises(ValueError, match="prediction_count"):
        _artifact(rows=())


def test_artifact_constructor_rejects_version_and_artifact_hash_drift():
    artifact = _artifact()
    with pytest.raises(QlibArtifactIntegrityError, match="version"):
        replace(artifact, artifact_version="WRONG")
    with pytest.raises(QlibArtifactIntegrityError, match="artifact hash mismatch"):
        replace(artifact, artifact_hash="f" * 64)
    with pytest.raises(ValueError, match="sha256"):
        replace(artifact, artifact_hash="broken")


def test_artifact_constructor_rejects_payload_and_count_drift():
    artifact = _artifact()
    with pytest.raises(QlibArtifactIntegrityError, match="prediction_count"):
        QlibPredictionArtifact(
            artifact_version=artifact.artifact_version,
            manifest=replace(artifact.manifest, prediction_count=3),
            rows=artifact.rows,
            artifact_hash=artifact.artifact_hash,
        )
    with pytest.raises(QlibArtifactIntegrityError, match="payload hash"):
        QlibPredictionArtifact(
            artifact_version=artifact.artifact_version,
            manifest=replace(artifact.manifest, prediction_payload_hash="f" * 64),
            rows=artifact.rows,
            artifact_hash=artifact.artifact_hash,
        )


def test_read_fails_closed_on_missing_large_and_non_json_artifacts(tmp_path):
    with pytest.raises(QlibArtifactIntegrityError, match="does not exist"):
        QlibPredictionArtifact.read(tmp_path / "missing.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{broken", encoding="utf-8")
    with pytest.raises(QlibArtifactIntegrityError, match="UTF-8 JSON"):
        QlibPredictionArtifact.read(broken)

    large = tmp_path / "large.json"
    with large.open("wb") as handle:
        handle.truncate(25_000_001)
    with pytest.raises(QlibArtifactGovernanceError, match="size limit"):
        QlibPredictionArtifact.read(large)


def test_read_rejects_top_level_manifest_and_row_schema_extensions(tmp_path):
    document = _artifact().to_dict()

    top = deepcopy(document)
    top["callable"] = "malicious"
    path = tmp_path / "top.json"
    _write_document(path, top)
    with pytest.raises(QlibArtifactIntegrityError, match="top-level schema"):
        QlibPredictionArtifact.read(path)

    manifest = deepcopy(document)
    manifest["manifest"]["endpoint"] = "https://example.invalid"
    path = tmp_path / "manifest.json"
    _write_document(path, manifest)
    with pytest.raises(QlibArtifactIntegrityError, match="manifest schema"):
        QlibPredictionArtifact.read(path)

    row = deepcopy(document)
    row["rows"][0]["python_module"] = "os"
    path = tmp_path / "row.json"
    _write_document(path, row)
    with pytest.raises(QlibArtifactIntegrityError, match="prediction-row schema"):
        QlibPredictionArtifact.read(path)


def test_read_rejects_tampering_without_recomputed_hashes(tmp_path):
    document = _artifact().to_dict()

    tampered_score = deepcopy(document)
    tampered_score["rows"][0]["score"] = 999.0
    path = tmp_path / "score.json"
    _write_document(path, tampered_score)
    with pytest.raises(QlibArtifactIntegrityError, match="payload hash"):
        QlibPredictionArtifact.read(path)

    tampered_model = deepcopy(document)
    tampered_model["manifest"]["model_config_hash"] = "f" * 64
    path = tmp_path / "model.json"
    _write_document(path, tampered_model)
    with pytest.raises(QlibArtifactIntegrityError, match="artifact hash mismatch"):
        QlibPredictionArtifact.read(path)


def test_read_rejects_wrong_field_types_and_nonfinite_scores(tmp_path):
    document = _artifact().to_dict()

    wrong_hash_type = deepcopy(document)
    wrong_hash_type["artifact_hash"] = 123
    path = tmp_path / "hash-type.json"
    _write_document(path, wrong_hash_type)
    with pytest.raises(QlibArtifactIntegrityError, match="fields are invalid"):
        QlibPredictionArtifact.read(path)

    wrong_count = deepcopy(document)
    wrong_count["manifest"]["prediction_count"] = "4"
    path = tmp_path / "count.json"
    _write_document(path, wrong_count)
    with pytest.raises(QlibArtifactIntegrityError, match="fields are invalid"):
        QlibPredictionArtifact.read(path)

    nonfinite = deepcopy(document)
    nonfinite["rows"][0]["score"] = float("nan")
    path = tmp_path / "nan.json"
    _write_document(path, nonfinite)
    with pytest.raises(QlibArtifactIntegrityError, match="invalid prediction row"):
        QlibPredictionArtifact.read(path)


def test_evidence_constructor_permanently_denies_execution_capital_and_live():
    evidence = _artifact().to_research_evidence()
    with pytest.raises(QlibArtifactIntegrityError, match="evidence version"):
        replace(evidence, evidence_version="WRONG")
    with pytest.raises(QlibArtifactIntegrityError, match="producer identity"):
        replace(evidence, producer_id="other")
    with pytest.raises(QlibArtifactGovernanceError, match="authorize execution"):
        replace(evidence, execution_authorized=True)
    with pytest.raises(QlibArtifactGovernanceError, match="authorize execution"):
        replace(evidence, paper_execution_authorized=True)
    with pytest.raises(QlibArtifactGovernanceError, match="capital or LIVE"):
        replace(evidence, capital_authority="USD")
    with pytest.raises(QlibArtifactGovernanceError, match="capital or LIVE"):
        replace(evidence, live_trading="ENABLED")


def test_manifest_canonical_time_fields_reject_noncanonical_representation():
    manifest = _artifact().manifest
    with pytest.raises(ValueError, match="canonical \+00:00"):
        replace(manifest, train_start="2026-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(manifest, train_start="2026-01-01T00:00:00")
    with pytest.raises(ValueError, match="ISO-8601"):
        replace(manifest, train_start="not-a-time")


def test_build_requires_timezone_aware_datetime_boundaries():
    with pytest.raises(ValueError, match="timezone-aware"):
        QlibPredictionArtifact.build(
            qlib_version="0.9.7",
            model_family="LightGBMRanker",
            model_config_hash=H1,
            training_dataset_hash=H2,
            feature_schema_hash=H3,
            producer_code_hash=H4,
            train_start=datetime(2026, 1, 1),
            train_end=BASE + timedelta(days=30),
            inference_start=BASE + timedelta(days=31),
            inference_end=BASE + timedelta(days=40),
            rows=_rows(),
        )


def test_evidence_fingerprint_changes_with_provenance_identity():
    evidence = _artifact().to_research_evidence()
    changed = replace(evidence, model_config_hash="a" * 64)
    assert changed.fingerprint != evidence.fingerprint
