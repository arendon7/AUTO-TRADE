from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math

import pytest

from autotrade.research.oss3_factor_matrix_artifact import (
    FactorDefinition,
    FactorMatrixArtifact,
    FactorMatrixGovernanceError,
    FactorMatrixIntegrityError,
    FactorMatrixPartition,
    FactorMatrixRow,
    OSS3B_ARTIFACT_VERSION,
    OSS3B_EVIDENCE_VERSION,
    OSS3B_PRODUCER_ID,
)


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
CAMPAIGN_ID = "oss3b-campaign-001"
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64
H5 = "5" * 64
H6 = "6" * 64
H7 = "7" * 64


def _features() -> tuple[FactorDefinition, ...]:
    return (
        FactorDefinition(
            name="momentum_20",
            dtype="float64",
            role="FEATURE",
            formula_hash=H1,
            source_id="aligned-bars-v1",
            source_hash=H2,
            lookback_bars=20,
        ),
        FactorDefinition(
            name="volatility_20",
            dtype="float64",
            role="FEATURE",
            formula_hash=H3,
            source_id="aligned-bars-v1",
            source_hash=H2,
            lookback_bars=20,
        ),
    )


def _rows() -> tuple[FactorMatrixRow, ...]:
    day1 = BASE + timedelta(days=1)
    day2 = BASE + timedelta(days=2)
    return (
        FactorMatrixRow(
            as_of=day1.isoformat(),
            available_at=(day1 - timedelta(minutes=1)).isoformat(),
            symbol="BTCUSDT",
            values=(0.10, 0.25),
        ),
        FactorMatrixRow(
            as_of=day1.isoformat(),
            available_at=day1.isoformat(),
            symbol="ETHUSDT",
            values=(-0.05, 0.30),
        ),
        FactorMatrixRow(
            as_of=day2.isoformat(),
            available_at=(day2 - timedelta(seconds=1)).isoformat(),
            symbol="BTCUSDT",
            values=(0.15, 0.20),
        ),
        FactorMatrixRow(
            as_of=day2.isoformat(),
            available_at=day2.isoformat(),
            symbol="ETHUSDT",
            values=(0.08, 0.28),
        ),
    )


def _artifact(
    *,
    partition=FactorMatrixPartition.TRAIN,
    campaign_id=CAMPAIGN_ID,
    research_split_hash=H7,
    features=None,
    rows=None,
):
    return FactorMatrixArtifact.build(
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
        partition=partition,
        partition_start=BASE,
        partition_end=BASE + timedelta(days=10),
        producer_code_hash=H4,
        source_dataset_hash=H5,
        source_universe_hash=H6,
        features=_features() if features is None else features,
        rows=_rows() if rows is None else rows,
    )


def _write(path, document, *, canonical=True):
    if canonical:
        text = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=True) + "\n"
    else:
        text = json.dumps(document, indent=2, allow_nan=True) + "\n"
    path.write_text(text, encoding="utf-8")


def test_build_round_trip_and_evidence_are_deterministic(tmp_path):
    artifact = _artifact()
    assert artifact.artifact_version == OSS3B_ARTIFACT_VERSION
    assert artifact.manifest.producer_id == OSS3B_PRODUCER_ID
    assert artifact.manifest.campaign_id == CAMPAIGN_ID
    assert artifact.manifest.research_split_hash == H7
    assert artifact.manifest.partition == "TRAIN"
    assert artifact.manifest.feature_count == 2
    assert artifact.manifest.row_count == 4
    assert artifact.artifact_hash == _artifact().artifact_hash

    target = tmp_path / "factors.json"
    artifact.write(target)
    restored = FactorMatrixArtifact.read(target)
    assert restored == artifact

    evidence = restored.to_research_evidence()
    assert evidence.evidence_version == OSS3B_EVIDENCE_VERSION
    assert evidence.artifact_hash == artifact.artifact_hash
    assert evidence.qlib_training_dataset_hash == artifact.artifact_hash
    assert evidence.campaign_id == CAMPAIGN_ID
    assert evidence.research_split_hash == H7
    assert evidence.feature_schema_hash == artifact.manifest.feature_schema_hash
    assert evidence.labels_included is False
    assert evidence.final_holdout_included is False
    assert evidence.execution_authorized is False
    assert evidence.paper_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"
    assert evidence.fingerprint == restored.to_research_evidence().fingerprint


def test_campaign_and_split_identity_are_required_and_hash_bound():
    baseline = _artifact()
    changed_campaign = _artifact(campaign_id="oss3b-campaign-002")
    changed_split = _artifact(research_split_hash="8" * 64)
    assert changed_campaign.artifact_hash != baseline.artifact_hash
    assert changed_split.artifact_hash != baseline.artifact_hash
    assert changed_campaign.to_research_evidence().fingerprint != baseline.to_research_evidence().fingerprint
    assert changed_split.to_research_evidence().fingerprint != baseline.to_research_evidence().fingerprint

    with pytest.raises(ValueError, match="campaign_id"):
        _artifact(campaign_id="bad campaign")
    with pytest.raises(ValueError, match="research_split_hash"):
        _artifact(research_split_hash="broken")


def test_development_partition_is_allowed_but_final_holdout_is_not():
    artifact = _artifact(partition=FactorMatrixPartition.DEVELOPMENT)
    assert artifact.manifest.partition == "DEVELOPMENT"

    manifest = artifact.manifest
    with pytest.raises(FactorMatrixGovernanceError, match="TRAIN or DEVELOPMENT"):
        replace(manifest, partition="FINAL_HOLDOUT")
    with pytest.raises(FactorMatrixGovernanceError, match="TRAIN or DEVELOPMENT"):
        replace(manifest, partition="HOLDOUT")
    with pytest.raises(TypeError, match="FactorMatrixPartition"):
        FactorMatrixArtifact.build(
            campaign_id=CAMPAIGN_ID,
            research_split_hash=H7,
            partition="TRAIN",
            partition_start=BASE,
            partition_end=BASE + timedelta(days=10),
            producer_code_hash=H4,
            source_dataset_hash=H5,
            source_universe_hash=H6,
            features=_features(),
            rows=_rows(),
        )


def test_row_enforces_point_in_time_availability():
    as_of = BASE + timedelta(days=1)
    with pytest.raises(FactorMatrixGovernanceError, match="not available"):
        FactorMatrixRow(
            as_of=as_of.isoformat(),
            available_at=(as_of + timedelta(microseconds=1)).isoformat(),
            symbol="BTCUSDT",
            values=(1.0,),
        )

    row = FactorMatrixRow(
        as_of=as_of.isoformat(),
        available_at=as_of.isoformat(),
        symbol="BTCUSDT",
        values=(1.0,),
    )
    assert row.availability_at == row.as_of_at


def test_rows_must_be_inside_declared_partition_window():
    before = FactorMatrixRow(
        as_of=(BASE - timedelta(seconds=1)).isoformat(),
        available_at=(BASE - timedelta(seconds=2)).isoformat(),
        symbol="BTCUSDT",
        values=(0.1, 0.2),
    )
    with pytest.raises(FactorMatrixGovernanceError, match="outside partition"):
        _artifact(rows=(before,))

    end = FactorMatrixRow(
        as_of=(BASE + timedelta(days=10)).isoformat(),
        available_at=(BASE + timedelta(days=9)).isoformat(),
        symbol="BTCUSDT",
        values=(0.1, 0.2),
    )
    with pytest.raises(FactorMatrixGovernanceError, match="outside partition"):
        _artifact(rows=(end,))


def test_feature_definition_is_numeric_feature_only_and_hash_bound():
    feature = _features()[0]
    assert feature.fingerprint == _features()[0].fingerprint
    with pytest.raises(FactorMatrixGovernanceError, match="dtype"):
        replace(feature, dtype="string")
    with pytest.raises(FactorMatrixGovernanceError, match="FEATURE role"):
        replace(feature, role="LABEL")
    with pytest.raises(ValueError, match="feature name"):
        replace(feature, name="bad feature")
    with pytest.raises(ValueError, match="source_id"):
        replace(feature, source_id="bad source")
    with pytest.raises(ValueError, match="sha256"):
        replace(feature, formula_hash="broken")
    with pytest.raises(ValueError, match="sha256"):
        replace(feature, source_hash="broken")
    for lookback in (-1, True, 1_000_001):
        with pytest.raises(ValueError, match="lookback_bars"):
            replace(feature, lookback_bars=lookback)


def test_v1_forbids_missing_nonfinite_and_nonnumeric_values():
    as_of = BASE + timedelta(days=1)
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(FactorMatrixGovernanceError, match="non-finite"):
            FactorMatrixRow(
                as_of=as_of.isoformat(),
                available_at=as_of.isoformat(),
                symbol="BTCUSDT",
                values=(value,),
            )
    for value in (None, "1.0", True):
        with pytest.raises(ValueError, match="numeric"):
            FactorMatrixRow(
                as_of=as_of.isoformat(),
                available_at=as_of.isoformat(),
                symbol="BTCUSDT",
                values=(value,),
            )
    with pytest.raises(TypeError, match="immutable tuple"):
        FactorMatrixRow(
            as_of=as_of.isoformat(),
            available_at=as_of.isoformat(),
            symbol="BTCUSDT",
            values=[1.0],
        )


def test_feature_schema_names_are_unique_and_row_width_is_exact():
    duplicate = (_features()[0], replace(_features()[1], name="momentum_20"))
    with pytest.raises(FactorMatrixGovernanceError, match="duplicate feature"):
        _artifact(features=duplicate)

    short = FactorMatrixRow(
        as_of=(BASE + timedelta(days=1)).isoformat(),
        available_at=(BASE + timedelta(days=1)).isoformat(),
        symbol="BTCUSDT",
        values=(0.1,),
    )
    with pytest.raises(FactorMatrixIntegrityError, match="row width"):
        _artifact(rows=(short,))


def test_rows_must_be_unique_and_canonically_sorted():
    rows = _rows()
    with pytest.raises(FactorMatrixGovernanceError, match="canonically sorted"):
        _artifact(rows=(rows[1], rows[0], rows[2], rows[3]))
    with pytest.raises(FactorMatrixGovernanceError, match="duplicate factor row"):
        _artifact(rows=(rows[0], rows[0]))


def test_manifest_enforces_campaign_provenance_hashes_policies_counts_and_window():
    manifest = _artifact().manifest
    with pytest.raises(FactorMatrixGovernanceError, match="producer"):
        replace(manifest, producer_id="other")
    with pytest.raises(ValueError, match="campaign_id"):
        replace(manifest, campaign_id="bad campaign")
    for field in (
        "producer_code_hash",
        "research_split_hash",
        "source_dataset_hash",
        "source_universe_hash",
        "feature_schema_hash",
        "row_payload_hash",
    ):
        with pytest.raises(ValueError, match="sha256"):
            replace(manifest, **{field: "broken"})
    with pytest.raises(FactorMatrixGovernanceError, match="window"):
        replace(manifest, partition_end=manifest.partition_start)
    with pytest.raises(FactorMatrixGovernanceError, match="missing-value"):
        replace(manifest, missing_value_policy="ALLOW_NULL")
    with pytest.raises(FactorMatrixGovernanceError, match="point-in-time"):
        replace(manifest, point_in_time_policy="TRUST_CALLER")
    for value in (0, True, 513):
        with pytest.raises(ValueError, match="feature_count"):
            replace(manifest, feature_count=value)
    for value in (0, True, 2_000_001):
        with pytest.raises(ValueError, match="row_count"):
            replace(manifest, row_count=value)


def test_artifact_constructor_detects_all_identity_drift():
    artifact = _artifact()
    with pytest.raises(FactorMatrixIntegrityError, match="version"):
        replace(artifact, artifact_version="WRONG")
    with pytest.raises(ValueError, match="sha256"):
        replace(artifact, artifact_hash="broken")
    with pytest.raises(FactorMatrixIntegrityError, match="artifact hash"):
        replace(artifact, artifact_hash="f" * 64)
    with pytest.raises(FactorMatrixIntegrityError, match="feature_count"):
        replace(artifact, manifest=replace(artifact.manifest, feature_count=1))
    with pytest.raises(FactorMatrixIntegrityError, match="row_count"):
        replace(artifact, manifest=replace(artifact.manifest, row_count=3))
    with pytest.raises(FactorMatrixIntegrityError, match="feature schema hash"):
        replace(artifact, manifest=replace(artifact.manifest, feature_schema_hash="f" * 64))
    with pytest.raises(FactorMatrixIntegrityError, match="row payload hash"):
        replace(artifact, manifest=replace(artifact.manifest, row_payload_hash="f" * 64))
    with pytest.raises(FactorMatrixIntegrityError, match="artifact hash"):
        replace(artifact, manifest=replace(artifact.manifest, campaign_id="oss3b-campaign-999"))
    with pytest.raises(FactorMatrixIntegrityError, match="artifact hash"):
        replace(artifact, manifest=replace(artifact.manifest, research_split_hash="9" * 64))


def test_empty_feature_or_row_artifacts_fail_closed():
    with pytest.raises(ValueError, match="feature_count"):
        _artifact(features=())
    with pytest.raises(ValueError, match="row_count"):
        _artifact(rows=())


def test_timestamp_and_symbol_canonicalization_is_strict():
    with pytest.raises(ValueError, match="timezone-aware"):
        FactorMatrixRow(
            as_of="2026-01-01T00:00:00",
            available_at="2026-01-01T00:00:00+00:00",
            symbol="BTCUSDT",
            values=(1.0,),
        )
    with pytest.raises(ValueError, match="canonical UTC"):
        FactorMatrixRow(
            as_of="2026-01-01T01:00:00+01:00",
            available_at="2026-01-01T00:00:00+00:00",
            symbol="BTCUSDT",
            values=(1.0,),
        )
    with pytest.raises(ValueError, match=r"canonical \+00:00"):
        FactorMatrixRow(
            as_of="2026-01-01T00:00:00Z",
            available_at="2026-01-01T00:00:00+00:00",
            symbol="BTCUSDT",
            values=(1.0,),
        )
    with pytest.raises(ValueError, match="factor symbol"):
        FactorMatrixRow(
            as_of=BASE.isoformat(),
            available_at=BASE.isoformat(),
            symbol="bad symbol",
            values=(1.0,),
        )


def test_build_requires_timezone_aware_partition_bounds():
    with pytest.raises(ValueError, match="timezone-aware"):
        FactorMatrixArtifact.build(
            campaign_id=CAMPAIGN_ID,
            research_split_hash=H7,
            partition=FactorMatrixPartition.TRAIN,
            partition_start=datetime(2026, 1, 1),
            partition_end=BASE + timedelta(days=10),
            producer_code_hash=H4,
            source_dataset_hash=H5,
            source_universe_hash=H6,
            features=_features(),
            rows=_rows(),
        )


def test_read_fails_closed_on_missing_large_and_invalid_json(tmp_path):
    with pytest.raises(FactorMatrixIntegrityError, match="does not exist"):
        FactorMatrixArtifact.read(tmp_path / "missing.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{broken", encoding="utf-8")
    with pytest.raises(FactorMatrixIntegrityError, match="UTF-8 JSON"):
        FactorMatrixArtifact.read(broken)

    large = tmp_path / "large.json"
    with large.open("wb") as handle:
        handle.truncate(50_000_001)
    with pytest.raises(FactorMatrixGovernanceError, match="size limit"):
        FactorMatrixArtifact.read(large)


def test_read_rejects_schema_extensions_at_every_level(tmp_path):
    document = _artifact().to_dict()

    top = deepcopy(document)
    top["python_module"] = "os"
    path = tmp_path / "top.json"
    _write(path, top)
    with pytest.raises(FactorMatrixIntegrityError, match="top-level schema"):
        FactorMatrixArtifact.read(path)

    manifest = deepcopy(document)
    manifest["manifest"]["endpoint"] = "https://example.invalid"
    path = tmp_path / "manifest.json"
    _write(path, manifest)
    with pytest.raises(FactorMatrixIntegrityError, match="manifest schema"):
        FactorMatrixArtifact.read(path)

    feature = deepcopy(document)
    feature["features"][0]["label_horizon"] = 5
    path = tmp_path / "feature.json"
    _write(path, feature)
    with pytest.raises(FactorMatrixIntegrityError, match="feature schema"):
        FactorMatrixArtifact.read(path)

    row = deepcopy(document)
    row["rows"][0]["target"] = 1.0
    path = tmp_path / "row.json"
    _write(path, row)
    with pytest.raises(FactorMatrixIntegrityError, match="row schema"):
        FactorMatrixArtifact.read(path)


def test_read_rejects_duplicate_keys_and_noncanonical_serialization(tmp_path):
    artifact = _artifact()
    canonical = tmp_path / "canonical.json"
    artifact.write(canonical)
    raw = canonical.read_text(encoding="utf-8")

    duplicate = tmp_path / "duplicate.json"
    prefix = '{"artifact_hash":"' + artifact.artifact_hash + '",'
    duplicate.write_text(raw.replace("{", prefix, 1), encoding="utf-8")
    with pytest.raises(FactorMatrixIntegrityError, match="duplicate JSON object key"):
        FactorMatrixArtifact.read(duplicate)

    pretty = tmp_path / "pretty.json"
    _write(pretty, artifact.to_dict(), canonical=False)
    with pytest.raises(FactorMatrixIntegrityError, match="serialization is not canonical"):
        FactorMatrixArtifact.read(pretty)


def test_read_detects_feature_row_campaign_split_and_provenance_tampering(tmp_path):
    document = _artifact().to_dict()

    feature = deepcopy(document)
    feature["features"][0]["lookback_bars"] = 99
    path = tmp_path / "feature-tamper.json"
    _write(path, feature)
    with pytest.raises(FactorMatrixIntegrityError, match="feature schema hash"):
        FactorMatrixArtifact.read(path)

    row = deepcopy(document)
    row["rows"][0]["values"][0] = 999.0
    path = tmp_path / "row-tamper.json"
    _write(path, row)
    with pytest.raises(FactorMatrixIntegrityError, match="row payload hash"):
        FactorMatrixArtifact.read(path)

    for field, value in (
        ("source_dataset_hash", "f" * 64),
        ("research_split_hash", "e" * 64),
        ("campaign_id", "oss3b-campaign-tampered"),
    ):
        tampered = deepcopy(document)
        tampered["manifest"][field] = value
        path = tmp_path / f"{field}-tamper.json"
        _write(path, tampered)
        with pytest.raises(FactorMatrixIntegrityError, match="artifact hash"):
            FactorMatrixArtifact.read(path)


def test_read_rejects_wrong_container_and_value_types(tmp_path):
    document = _artifact().to_dict()

    features = deepcopy(document)
    features["features"] = {}
    path = tmp_path / "features-type.json"
    _write(path, features)
    with pytest.raises(FactorMatrixIntegrityError, match="features must be an array"):
        FactorMatrixArtifact.read(path)

    rows = deepcopy(document)
    rows["rows"] = {}
    path = tmp_path / "rows-type.json"
    _write(path, rows)
    with pytest.raises(FactorMatrixIntegrityError, match="rows must be an array"):
        FactorMatrixArtifact.read(path)

    values = deepcopy(document)
    values["rows"][0]["values"] = "not-an-array"
    path = tmp_path / "values-type.json"
    _write(path, values)
    with pytest.raises(FactorMatrixIntegrityError, match="values must be an array"):
        FactorMatrixArtifact.read(path)

    nonnumeric = deepcopy(document)
    nonnumeric["rows"][0]["values"][0] = "1.0"
    path = tmp_path / "nonnumeric.json"
    _write(path, nonnumeric)
    with pytest.raises(FactorMatrixIntegrityError, match="factor value must be numeric"):
        FactorMatrixArtifact.read(path)


def test_evidence_constructor_denies_identity_drift_labels_holdout_execution_capital_and_live():
    evidence = _artifact().to_research_evidence()
    with pytest.raises(FactorMatrixIntegrityError, match="evidence version"):
        replace(evidence, evidence_version="WRONG")
    with pytest.raises(FactorMatrixIntegrityError, match="campaign_id"):
        replace(evidence, campaign_id="bad campaign")
    with pytest.raises(ValueError, match="research_split_hash"):
        replace(evidence, research_split_hash="broken")
    with pytest.raises(FactorMatrixGovernanceError, match="forbidden partition"):
        replace(evidence, partition="FINAL_HOLDOUT")
    with pytest.raises(FactorMatrixIntegrityError, match="partition window"):
        replace(evidence, partition_end=evidence.partition_start)
    with pytest.raises(FactorMatrixIntegrityError, match="counts"):
        replace(evidence, row_count=0)
    with pytest.raises(FactorMatrixIntegrityError, match="counts"):
        replace(evidence, feature_count=0)
    with pytest.raises(FactorMatrixIntegrityError, match="point-in-time"):
        replace(evidence, point_in_time_policy="TRUST_CALLER")
    with pytest.raises(FactorMatrixGovernanceError, match="labels or FINAL_HOLDOUT"):
        replace(evidence, labels_included=True)
    with pytest.raises(FactorMatrixGovernanceError, match="labels or FINAL_HOLDOUT"):
        replace(evidence, final_holdout_included=True)
    with pytest.raises(FactorMatrixGovernanceError, match="authorize execution"):
        replace(evidence, execution_authorized=True)
    with pytest.raises(FactorMatrixGovernanceError, match="authorize execution"):
        replace(evidence, paper_execution_authorized=True)
    with pytest.raises(FactorMatrixGovernanceError, match="capital or LIVE"):
        replace(evidence, capital_authority="USD")
    with pytest.raises(FactorMatrixGovernanceError, match="capital or LIVE"):
        replace(evidence, live_trading="ENABLED")


def test_evidence_fingerprint_changes_when_dataset_or_split_identity_changes():
    evidence = _artifact().to_research_evidence()
    assert replace(evidence, source_dataset_hash="a" * 64).fingerprint != evidence.fingerprint
    assert replace(evidence, research_split_hash="b" * 64).fingerprint != evidence.fingerprint
    assert replace(evidence, campaign_id="oss3b-campaign-003").fingerprint != evidence.fingerprint
