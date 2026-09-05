from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math

import pytest

from autotrade.research.oss3_supervised_label_artifact import (
    LabelDefinition,
    LabelGovernanceError,
    LabelIntegrityError,
    LabelPartition,
    OSS3C_ARTIFACT_VERSION,
    OSS3C_EVIDENCE_VERSION,
    SupervisedLabelArtifact,
    SupervisedLabelRow,
)


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
CAMPAIGN = "oss3c-campaign-001"
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64
H5 = "5" * 64
H6 = "6" * 64


def _label() -> LabelDefinition:
    return LabelDefinition(
        name="forward_return",
        dtype="float64",
        role="LABEL",
        formula_hash=H1,
        source_id="aligned-bars-v1",
        source_hash=H2,
    )


def _rows() -> tuple[SupervisedLabelRow, ...]:
    d1 = BASE + timedelta(days=1)
    d2 = BASE + timedelta(days=2)
    return (
        SupervisedLabelRow(
            label_as_of=d1.isoformat(),
            horizon_end=(d1 + timedelta(hours=1)).isoformat(),
            available_at=(d1 + timedelta(hours=1, minutes=1)).isoformat(),
            symbol="BTCUSDT",
            value=0.012,
        ),
        SupervisedLabelRow(
            label_as_of=d1.isoformat(),
            horizon_end=(d1 + timedelta(hours=2)).isoformat(),
            available_at=(d1 + timedelta(hours=2)).isoformat(),
            symbol="ETHUSDT",
            value=-0.008,
        ),
        SupervisedLabelRow(
            label_as_of=d2.isoformat(),
            horizon_end=(d2 + timedelta(hours=1)).isoformat(),
            available_at=(d2 + timedelta(hours=1)).isoformat(),
            symbol="BTCUSDT",
            value=0.004,
        ),
    )


def _artifact(*, partition=LabelPartition.TRAIN, campaign_id=CAMPAIGN, split_hash=H6, rows=None):
    return SupervisedLabelArtifact.build(
        campaign_id=campaign_id,
        research_split_hash=split_hash,
        partition=partition,
        partition_start=BASE,
        partition_end=BASE + timedelta(days=10),
        producer_code_hash=H3,
        source_dataset_hash=H4,
        source_universe_hash=H5,
        label=_label(),
        rows=_rows() if rows is None else rows,
    )


def _write(path, document, *, canonical=True):
    if canonical:
        text = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=True) + "\n"
    else:
        text = json.dumps(document, indent=2, allow_nan=True) + "\n"
    path.write_text(text, encoding="utf-8")


def test_round_trip_and_evidence_are_deterministic(tmp_path):
    artifact = _artifact()
    assert artifact.artifact_version == OSS3C_ARTIFACT_VERSION
    assert artifact.artifact_hash == _artifact().artifact_hash
    target = tmp_path / "labels.json"
    artifact.write(target)
    restored = SupervisedLabelArtifact.read(target)
    assert restored == artifact

    evidence = restored.to_research_evidence()
    assert evidence.evidence_version == OSS3C_EVIDENCE_VERSION
    assert evidence.qlib_label_dataset_hash == artifact.artifact_hash
    assert evidence.campaign_id == CAMPAIGN
    assert evidence.research_split_hash == H6
    assert evidence.final_holdout_included is False
    assert evidence.execution_authorized is False
    assert evidence.paper_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"


def test_campaign_and_split_are_hash_bound():
    baseline = _artifact()
    assert _artifact(campaign_id="oss3c-campaign-002").artifact_hash != baseline.artifact_hash
    assert _artifact(split_hash="a" * 64).artifact_hash != baseline.artifact_hash
    with pytest.raises(ValueError, match="campaign_id"):
        _artifact(campaign_id="bad campaign")
    with pytest.raises(ValueError, match="research_split_hash"):
        _artifact(split_hash="broken")


def test_development_allowed_but_holdout_is_structurally_forbidden():
    assert _artifact(partition=LabelPartition.DEVELOPMENT).manifest.partition == "DEVELOPMENT"
    with pytest.raises(TypeError, match="LabelPartition"):
        SupervisedLabelArtifact.build(
            campaign_id=CAMPAIGN,
            research_split_hash=H6,
            partition="FINAL_HOLDOUT",
            partition_start=BASE,
            partition_end=BASE + timedelta(days=10),
            producer_code_hash=H3,
            source_dataset_hash=H4,
            source_universe_hash=H5,
            label=_label(),
            rows=_rows(),
        )
    with pytest.raises(LabelGovernanceError, match="FINAL_HOLDOUT"):
        replace(_artifact().manifest, partition="FINAL_HOLDOUT")


def test_future_horizon_and_availability_are_strictly_causal():
    origin = BASE + timedelta(days=1)
    with pytest.raises(LabelGovernanceError, match="strictly after"):
        SupervisedLabelRow(
            label_as_of=origin.isoformat(),
            horizon_end=origin.isoformat(),
            available_at=origin.isoformat(),
            symbol="BTCUSDT",
            value=0.1,
        )
    with pytest.raises(LabelGovernanceError, match="before horizon_end"):
        SupervisedLabelRow(
            label_as_of=origin.isoformat(),
            horizon_end=(origin + timedelta(hours=1)).isoformat(),
            available_at=(origin + timedelta(minutes=59)).isoformat(),
            symbol="BTCUSDT",
            value=0.1,
        )


def test_label_horizon_cannot_cross_partition_boundary():
    end = BASE + timedelta(days=10)
    row = SupervisedLabelRow(
        label_as_of=(end - timedelta(hours=2)).isoformat(),
        horizon_end=(end - timedelta(minutes=1)).isoformat(),
        available_at=end.isoformat(),
        symbol="BTCUSDT",
        value=0.1,
    )
    with pytest.raises(LabelGovernanceError, match="outside partition"):
        _artifact(rows=(row,))

    before = SupervisedLabelRow(
        label_as_of=(BASE - timedelta(seconds=1)).isoformat(),
        horizon_end=(BASE + timedelta(minutes=1)).isoformat(),
        available_at=(BASE + timedelta(minutes=1)).isoformat(),
        symbol="BTCUSDT",
        value=0.1,
    )
    with pytest.raises(LabelGovernanceError, match="outside partition"):
        _artifact(rows=(before,))


def test_label_definition_is_strict_and_hash_bound():
    label = _label()
    assert label.fingerprint == _label().fingerprint
    with pytest.raises(LabelGovernanceError, match="dtype"):
        replace(label, dtype="float32")
    with pytest.raises(LabelGovernanceError, match="LABEL role"):
        replace(label, role="FEATURE")
    with pytest.raises(ValueError, match="label name"):
        replace(label, name="bad label")
    with pytest.raises(ValueError, match="source_id"):
        replace(label, source_id="bad source")
    with pytest.raises(ValueError, match="sha256"):
        replace(label, formula_hash="broken")


def test_label_values_must_be_finite_numeric():
    origin = BASE + timedelta(days=1)
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(LabelGovernanceError, match="non-finite"):
            SupervisedLabelRow(
                label_as_of=origin.isoformat(),
                horizon_end=(origin + timedelta(hours=1)).isoformat(),
                available_at=(origin + timedelta(hours=1)).isoformat(),
                symbol="BTCUSDT",
                value=value,
            )
    for value in (None, "0.1", True):
        with pytest.raises(ValueError, match="numeric"):
            SupervisedLabelRow(
                label_as_of=origin.isoformat(),
                horizon_end=(origin + timedelta(hours=1)).isoformat(),
                available_at=(origin + timedelta(hours=1)).isoformat(),
                symbol="BTCUSDT",
                value=value,
            )


def test_rows_must_be_unique_and_canonically_sorted():
    rows = _rows()
    with pytest.raises(LabelGovernanceError, match="canonically sorted"):
        _artifact(rows=(rows[1], rows[0], rows[2]))
    with pytest.raises(LabelGovernanceError, match="duplicate"):
        _artifact(rows=(rows[0], rows[0]))


def test_empty_artifact_fails_closed():
    with pytest.raises(ValueError, match="row_count"):
        _artifact(rows=())


def test_manifest_policies_hashes_counts_and_window_fail_closed():
    manifest = _artifact().manifest
    with pytest.raises(LabelGovernanceError, match="producer"):
        replace(manifest, producer_id="other")
    with pytest.raises(ValueError, match="campaign_id"):
        replace(manifest, campaign_id="bad campaign")
    for field in (
        "producer_code_hash",
        "research_split_hash",
        "source_dataset_hash",
        "source_universe_hash",
        "label_definition_hash",
        "row_payload_hash",
    ):
        with pytest.raises(ValueError, match="sha256"):
            replace(manifest, **{field: "broken"})
    with pytest.raises(LabelGovernanceError, match="window"):
        replace(manifest, partition_end=manifest.partition_start)
    with pytest.raises(LabelGovernanceError, match="horizon policy"):
        replace(manifest, horizon_policy="IMPLICIT")
    with pytest.raises(LabelGovernanceError, match="availability policy"):
        replace(manifest, availability_policy="TRUST_CALLER")
    with pytest.raises(LabelGovernanceError, match="missing-value"):
        replace(manifest, missing_value_policy="ALLOW_NULL")
    for value in (0, True, 2_000_001):
        with pytest.raises(ValueError, match="row_count"):
            replace(manifest, row_count=value)


def test_artifact_detects_definition_count_payload_and_identity_drift():
    artifact = _artifact()
    with pytest.raises(LabelIntegrityError, match="version"):
        replace(artifact, artifact_version="WRONG")
    with pytest.raises(LabelIntegrityError, match="label definition hash"):
        replace(artifact, manifest=replace(artifact.manifest, label_definition_hash="f" * 64))
    with pytest.raises(LabelIntegrityError, match="row_count"):
        replace(artifact, manifest=replace(artifact.manifest, row_count=2))
    with pytest.raises(LabelIntegrityError, match="row payload hash"):
        replace(artifact, manifest=replace(artifact.manifest, row_payload_hash="f" * 64))
    with pytest.raises(LabelIntegrityError, match="artifact hash"):
        replace(artifact, manifest=replace(artifact.manifest, campaign_id="oss3c-campaign-999"))
    with pytest.raises(LabelIntegrityError, match="artifact hash"):
        replace(artifact, manifest=replace(artifact.manifest, research_split_hash="e" * 64))
    with pytest.raises(LabelIntegrityError, match="artifact hash"):
        replace(artifact, artifact_hash="f" * 64)


def test_timestamp_and_symbol_canonicalization_is_strict():
    with pytest.raises(ValueError, match="timezone-aware"):
        SupervisedLabelRow(
            label_as_of="2026-01-01T00:00:00",
            horizon_end="2026-01-01T01:00:00+00:00",
            available_at="2026-01-01T01:00:00+00:00",
            symbol="BTCUSDT",
            value=0.1,
        )
    with pytest.raises(ValueError, match="canonical UTC"):
        SupervisedLabelRow(
            label_as_of="2026-01-01T01:00:00+01:00",
            horizon_end="2026-01-01T01:30:00+00:00",
            available_at="2026-01-01T01:30:00+00:00",
            symbol="BTCUSDT",
            value=0.1,
        )
    with pytest.raises(ValueError, match=r"canonical \+00:00"):
        SupervisedLabelRow(
            label_as_of="2026-01-01T00:00:00Z",
            horizon_end="2026-01-01T01:00:00+00:00",
            available_at="2026-01-01T01:00:00+00:00",
            symbol="BTCUSDT",
            value=0.1,
        )
    with pytest.raises(ValueError, match="label symbol"):
        replace(_rows()[0], symbol="bad symbol")


def test_build_requires_aware_partition_bounds():
    with pytest.raises(ValueError, match="timezone-aware"):
        SupervisedLabelArtifact.build(
            campaign_id=CAMPAIGN,
            research_split_hash=H6,
            partition=LabelPartition.TRAIN,
            partition_start=datetime(2026, 1, 1),
            partition_end=BASE + timedelta(days=10),
            producer_code_hash=H3,
            source_dataset_hash=H4,
            source_universe_hash=H5,
            label=_label(),
            rows=_rows(),
        )


def test_read_rejects_missing_large_and_invalid_json(tmp_path):
    with pytest.raises(LabelIntegrityError, match="does not exist"):
        SupervisedLabelArtifact.read(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{broken", encoding="utf-8")
    with pytest.raises(LabelIntegrityError, match="UTF-8 JSON"):
        SupervisedLabelArtifact.read(broken)
    large = tmp_path / "large.json"
    with large.open("wb") as handle:
        handle.truncate(50_000_001)
    with pytest.raises(LabelGovernanceError, match="size limit"):
        SupervisedLabelArtifact.read(large)


def test_read_rejects_schema_extensions(tmp_path):
    document = _artifact().to_dict()
    for where, key, message in (
        ("top", "python_module", "top-level schema"),
        ("manifest", "endpoint", "manifest schema"),
        ("label", "callable", "label schema"),
        ("row", "feature", "row schema"),
    ):
        altered = deepcopy(document)
        if where == "top":
            altered[key] = "x"
        elif where == "manifest":
            altered["manifest"][key] = "x"
        elif where == "label":
            altered["label"][key] = "x"
        else:
            altered["rows"][0][key] = "x"
        path = tmp_path / f"{where}.json"
        _write(path, altered)
        with pytest.raises(LabelIntegrityError, match=message):
            SupervisedLabelArtifact.read(path)


def test_read_rejects_duplicate_keys_and_pretty_json(tmp_path):
    artifact = _artifact()
    canonical = tmp_path / "canonical.json"
    artifact.write(canonical)
    raw = canonical.read_text(encoding="utf-8")
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(raw.replace("{", '{"artifact_hash":"' + artifact.artifact_hash + '",', 1), encoding="utf-8")
    with pytest.raises(LabelIntegrityError, match="duplicate JSON object key"):
        SupervisedLabelArtifact.read(duplicate)
    pretty = tmp_path / "pretty.json"
    _write(pretty, artifact.to_dict(), canonical=False)
    with pytest.raises(LabelIntegrityError, match="not canonical"):
        SupervisedLabelArtifact.read(pretty)


def test_read_detects_label_row_campaign_split_and_provenance_tampering(tmp_path):
    document = _artifact().to_dict()
    cases = []
    label = deepcopy(document)
    label["label"]["formula_hash"] = "f" * 64
    cases.append(("label", label, "label definition hash"))
    row = deepcopy(document)
    row["rows"][0]["value"] = 99.0
    cases.append(("row", row, "row payload hash"))
    for field, value in (
        ("campaign_id", "oss3c-campaign-tampered"),
        ("research_split_hash", "e" * 64),
        ("source_dataset_hash", "d" * 64),
    ):
        changed = deepcopy(document)
        changed["manifest"][field] = value
        cases.append((field, changed, "artifact hash"))
    for name, changed, message in cases:
        path = tmp_path / f"{name}.json"
        _write(path, changed)
        with pytest.raises(LabelIntegrityError, match=message):
            SupervisedLabelArtifact.read(path)


def test_read_rejects_wrong_container_types(tmp_path):
    document = _artifact().to_dict()
    for field, value, message in (
        ("manifest", [], "manifest must be an object"),
        ("label", [], "label must be an object"),
        ("rows", {}, "rows must be an array"),
    ):
        changed = deepcopy(document)
        changed[field] = value
        path = tmp_path / f"{field}-type.json"
        _write(path, changed)
        with pytest.raises(LabelIntegrityError, match=message):
            SupervisedLabelArtifact.read(path)


def test_evidence_denies_identity_policy_holdout_execution_capital_and_live_drift():
    evidence = _artifact().to_research_evidence()
    with pytest.raises(LabelIntegrityError, match="evidence version"):
        replace(evidence, evidence_version="WRONG")
    with pytest.raises(LabelIntegrityError, match="campaign_id"):
        replace(evidence, campaign_id="bad campaign")
    with pytest.raises(ValueError, match="research_split_hash"):
        replace(evidence, research_split_hash="broken")
    with pytest.raises(LabelGovernanceError, match="forbidden partition"):
        replace(evidence, partition="FINAL_HOLDOUT")
    with pytest.raises(LabelIntegrityError, match="partition window"):
        replace(evidence, partition_end=evidence.partition_start)
    with pytest.raises(LabelIntegrityError, match="row_count"):
        replace(evidence, row_count=0)
    with pytest.raises(LabelIntegrityError, match="horizon policy"):
        replace(evidence, horizon_policy="IMPLICIT")
    with pytest.raises(LabelIntegrityError, match="availability policy"):
        replace(evidence, availability_policy="TRUST_CALLER")
    with pytest.raises(LabelGovernanceError, match="FINAL_HOLDOUT"):
        replace(evidence, final_holdout_included=True)
    with pytest.raises(LabelGovernanceError, match="authorize execution"):
        replace(evidence, execution_authorized=True)
    with pytest.raises(LabelGovernanceError, match="authorize execution"):
        replace(evidence, paper_execution_authorized=True)
    with pytest.raises(LabelGovernanceError, match="capital or LIVE"):
        replace(evidence, capital_authority="USD")
    with pytest.raises(LabelGovernanceError, match="capital or LIVE"):
        replace(evidence, live_trading="ENABLED")


def test_evidence_fingerprint_changes_with_campaign_split_or_dataset():
    evidence = _artifact().to_research_evidence()
    assert replace(evidence, campaign_id="oss3c-campaign-003").fingerprint != evidence.fingerprint
    assert replace(evidence, research_split_hash="b" * 64).fingerprint != evidence.fingerprint
    assert replace(evidence, source_dataset_hash="c" * 64).fingerprint != evidence.fingerprint
