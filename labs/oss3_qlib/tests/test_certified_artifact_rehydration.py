from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tarfile
import zipfile

import pytest

from autotrade.research.oss3_real_campaign_evidence import (
    DESCRIPTOR_COUNT,
    SOURCE_FILE_COUNT,
    canonical_oss3d2y_real_campaign_evidence_seal,
)
from autotrade.research.oss3_real_descriptor_identity import StableDescriptorMaterialIdentity

from labs.oss3_qlib.certified_artifact_rehydration import (
    ARTIFACT_POLICY,
    DOWNSTREAM_POLICY,
    EVIDENCE_ROOT_NAME,
    EVIDENCE_TAR_CHECKSUM_NAME,
    EVIDENCE_TAR_NAME,
    EXPECTED_ARTIFACT_MEMBERS,
    FIRST_RESULT_NAME,
    INVENTORY_NAME,
    OFFLINE_RESULT_NAME,
    PARTITION_POLICY,
    SOURCE_POLICY,
    ArtifactEnvelopeExpectations,
    CertifiedArtifactRealRehydrationEvidence,
    CertifiedArtifactRehydrationGovernanceError,
    CertifiedArtifactRehydrationIntegrityError,
    _verify_original_receipt_document,
    _verify_original_snapshot_document,
    load_certified_artifact_envelope,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _tar_bytes(source_files: dict[str, bytes], *, extra_name: str | None = None, symlink_name: str | None = None) -> bytes:
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        root = tarfile.TarInfo(EVIDENCE_ROOT_NAME)
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        archive.addfile(root)
        directories = set()
        for path in source_files:
            parts = path.split("/")[:-1]
            current = EVIDENCE_ROOT_NAME
            for part in parts:
                current += "/" + part
                directories.add(current)
        for directory in sorted(directories):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for path, content in sorted(source_files.items()):
            info = tarfile.TarInfo(f"{EVIDENCE_ROOT_NAME}/{path}")
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, BytesIO(content))
        if extra_name is not None:
            content = b"extra\n"
            info = tarfile.TarInfo(f"{EVIDENCE_ROOT_NAME}/{extra_name}")
            info.size = len(content)
            archive.addfile(info, BytesIO(content))
        if symlink_name is not None:
            info = tarfile.TarInfo(f"{EVIDENCE_ROOT_NAME}/{symlink_name}")
            info.type = tarfile.SYMTYPE
            info.linkname = "/tmp/escape"
            archive.addfile(info)
    return buffer.getvalue()


def _artifact_zip_bytes(top_members: dict[str, bytes], *, extra: tuple[str, bytes] | None = None) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in EXPECTED_ARTIFACT_MEMBERS:
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 9, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, top_members[name])
        if extra is not None:
            archive.writestr(extra[0], extra[1])
    return buffer.getvalue()


def _synthetic_artifact(
    tmp_path: Path,
    *,
    tar_extra: str | None = None,
    tar_symlink: str | None = None,
    zip_extra: tuple[str, bytes] | None = None,
):
    source_files = {
        "BTCUSDT/2025-01/BTCUSDT-1h-2025-01.zip": b"archive-a",
        "BTCUSDT/2025-01/BTCUSDT-1h-2025-01.zip.CHECKSUM": b"checksum-a\n",
        "BTCUSDT/2025-01/d2t-snapshot.json": b'{"artifact_hash":"' + b"a" * 64 + b'"}\n',
        "BTCUSDT/2025-01/d2u-acquisition-receipt.json": b'{"receipt":"original"}\n',
        "oss3d2u-plan.sqlite3": b"plan-ledger",
        "oss3d2v-campaign.sqlite3": b"campaign-ledger",
    }
    inventory = {
        "certified_code_commit": "a" * 40,
        "collection_id": "synthetic",
        "descriptor_count": DESCRIPTOR_COUNT,
        "file_count": len(source_files),
        "files": [
            {"bytes": len(content), "path": path, "sha256": _sha(content)}
            for path, content in sorted(source_files.items())
        ],
        "inventory_version": "SYNTHETIC_V1",
        "plan_fingerprint": "b" * 64,
    }
    inventory_raw = _canonical(inventory) + b"\n"
    tar_raw = _tar_bytes(source_files, extra_name=tar_extra, symlink_name=tar_symlink)
    first = _canonical({"complete": True, "pass": 1}) + b"\n"
    offline = _canonical({"complete": True, "pass": 2}) + b"\n"
    tar_checksum = f"{_sha(tar_raw)}  {EVIDENCE_TAR_NAME}\n".encode("utf-8")
    top = {
        FIRST_RESULT_NAME: first,
        OFFLINE_RESULT_NAME: offline,
        INVENTORY_NAME: inventory_raw,
        EVIDENCE_TAR_NAME: tar_raw,
        EVIDENCE_TAR_CHECKSUM_NAME: tar_checksum,
    }
    artifact_raw = _artifact_zip_bytes(top, extra=zip_extra)
    path = tmp_path / "artifact.zip"
    path.write_bytes(artifact_raw)
    expectations = ArtifactEnvelopeExpectations(
        artifact_size_bytes=len(artifact_raw),
        artifact_zip_sha256=_sha(artifact_raw),
        first_result_sha256=_sha(first),
        offline_result_sha256=_sha(offline),
        inventory_sha256=_sha(inventory_raw),
        evidence_tar_sha256=_sha(tar_raw),
        evidence_tar_checksum_sha256=_sha(tar_checksum),
        source_file_count=len(source_files),
    )
    return path, expectations, source_files, inventory


def test_d3b_envelope_requires_exact_zip_inventory_tar_and_source_family(tmp_path):
    path, expectations, source_files, _ = _synthetic_artifact(tmp_path)
    envelope = load_certified_artifact_envelope(path, expectations=expectations)

    assert envelope.artifact_zip_sha256 == expectations.artifact_zip_sha256
    assert envelope.inventory_sha256 == expectations.inventory_sha256
    assert envelope.evidence_tar_sha256 == expectations.evidence_tar_sha256
    assert len(envelope.source_files) == len(source_files)
    assert set(envelope.source_file_map) == set(source_files)
    for name, raw in source_files.items():
        assert envelope.source_file_map[name].content == raw


def test_d3b_rejects_outer_artifact_hash_or_member_hash_drift(tmp_path):
    path, expectations, _, _ = _synthetic_artifact(tmp_path)
    with pytest.raises(CertifiedArtifactRehydrationIntegrityError, match="artifact ZIP hash"):
        load_certified_artifact_envelope(
            path,
            expectations=replace(expectations, artifact_zip_sha256="0" * 64),
        )
    with pytest.raises(CertifiedArtifactRehydrationIntegrityError, match="member hash drifted"):
        load_certified_artifact_envelope(
            path,
            expectations=replace(expectations, inventory_sha256="1" * 64),
        )


def test_d3b_rejects_extra_zip_member(tmp_path):
    path, expectations, _, _ = _synthetic_artifact(tmp_path, zip_extra=("unexpected.txt", b"x"))
    with pytest.raises(CertifiedArtifactRehydrationIntegrityError, match="member family/order"):
        load_certified_artifact_envelope(path, expectations=expectations)


def test_d3b_rejects_tar_file_absent_from_inventory(tmp_path):
    path, expectations, _, _ = _synthetic_artifact(tmp_path, tar_extra="unexpected.bin")
    with pytest.raises(CertifiedArtifactRehydrationIntegrityError, match="absent from inventory"):
        load_certified_artifact_envelope(path, expectations=expectations)


def test_d3b_rejects_non_regular_tar_member(tmp_path):
    path, expectations, _, _ = _synthetic_artifact(tmp_path, tar_symlink="link")
    with pytest.raises(CertifiedArtifactRehydrationGovernanceError, match="regular files/directories"):
        load_certified_artifact_envelope(path, expectations=expectations)


def test_d3b_rejects_inventory_serialization_or_file_hash_drift(tmp_path):
    path, expectations, _, inventory = _synthetic_artifact(tmp_path)
    raw = path.read_bytes()
    with zipfile.ZipFile(BytesIO(raw), "r") as archive:
        top = {name: archive.read(name) for name in EXPECTED_ARTIFACT_MEMBERS}

    pretty_inventory = json.dumps(inventory, indent=2).encode("utf-8") + b"\n"
    top[INVENTORY_NAME] = pretty_inventory
    artifact = _artifact_zip_bytes(top)
    path.write_bytes(artifact)
    drifted = replace(
        expectations,
        artifact_size_bytes=len(artifact),
        artifact_zip_sha256=_sha(artifact),
        inventory_sha256=_sha(pretty_inventory),
    )
    with pytest.raises(CertifiedArtifactRehydrationIntegrityError, match="serialization is not canonical"):
        load_certified_artifact_envelope(path, expectations=drifted)


def _entry() -> StableDescriptorMaterialIdentity:
    vector = ["a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64]
    material_hash = sha256(_canonical(vector)).hexdigest()
    return StableDescriptorMaterialIdentity(*vector, material_hash)


def test_original_snapshot_document_is_rebound_to_d2z_identity():
    entry = _entry()
    raw = _canonical(
        {
            "artifact_hash": entry.snapshot_artifact_hash,
            "manifest": {
                "archive_sha256": entry.archive_sha256,
                "checksum_payload_sha256": entry.checksum_payload_sha256,
                "descriptor_fingerprint": entry.descriptor_fingerprint,
                "normalized_dataset_hash": entry.normalized_dataset_hash,
            },
        }
    ) + b"\n"
    _verify_original_snapshot_document(raw, entry.descriptor_fingerprint, entry)

    tampered = json.loads(raw)
    tampered["manifest"]["normalized_dataset_hash"] = "f" * 64
    with pytest.raises(CertifiedArtifactRehydrationIntegrityError, match="differs from D2Z identity"):
        _verify_original_snapshot_document(
            _canonical(tampered) + b"\n",
            entry.descriptor_fingerprint,
            entry,
        )


def test_original_receipt_hash_is_exact_audit_lineage_and_authority_is_denied():
    d2y = canonical_oss3d2y_real_campaign_evidence_seal()
    entry = _entry()
    receipt = {
        "acquired_at": "2026-09-09T04:11:31.716112+00:00",
        "archive_payload_sha256": entry.archive_sha256,
        "archive_status": 200,
        "capital_authority": "NONE",
        "checksum_a_payload_sha256": entry.checksum_payload_sha256,
        "checksum_a_status": 200,
        "checksum_b_payload_sha256": entry.checksum_payload_sha256,
        "checksum_b_status": 200,
        "checksum_payloads_identical": True,
        "collection_id": d2y.collection_id,
        "d2t_normalized_dataset_hash": entry.normalized_dataset_hash,
        "d2t_snapshot_artifact_hash": entry.snapshot_artifact_hash,
        "descriptor_fingerprint": entry.descriptor_fingerprint,
        "exact_final_urls": True,
        "execution_authorized": False,
        "final_holdout_values_requested": False,
        "live_trading": "BLOCKED",
        "network_used": True,
        "paper_execution_authorized": False,
        "plan_fingerprint": d2y.plan_fingerprint,
        "provider_credentials_used": False,
        "request_count": 3,
        "retries_performed": 0,
        "trading_endpoints_used": False,
    }
    raw = _canonical(receipt) + b"\n"
    fingerprint = _verify_original_receipt_document(
        raw,
        descriptor_fingerprint=entry.descriptor_fingerprint,
        d2y=d2y,
        entry=entry,
    )
    assert fingerprint == sha256(_canonical(receipt)).hexdigest()

    with pytest.raises(CertifiedArtifactRehydrationIntegrityError, match="execution_authorized"):
        _verify_original_receipt_document(
            _canonical({**receipt, "execution_authorized": True}) + b"\n",
            descriptor_fingerprint=entry.descriptor_fingerprint,
            d2y=d2y,
            entry=entry,
        )


def _dummy_evidence() -> CertifiedArtifactRealRehydrationEvidence:
    h = lambda value: sha256(value.encode("utf-8")).hexdigest()
    partition = h("partition")
    return CertifiedArtifactRealRehydrationEvidence(
        evidence_version="OSS3D3B_CERTIFIED_ARTIFACT_REAL_REHYDRATION_EVIDENCE_V1",
        d2y_seal_fingerprint=h("d2y"),
        artifact_zip_sha256=h("zip"),
        source_inventory_sha256=h("inventory"),
        evidence_tar_sha256=h("tar"),
        source_inventory_root=h("inventory-root"),
        source_file_count=SOURCE_FILE_COUNT,
        descriptor_count=DESCRIPTOR_COUNT,
        d2z_stable_material_root=h("d2z"),
        d3a_scientific_fingerprint=h("scientific"),
        d3a_evidence_fingerprint=h("d3a-evidence"),
        d3a_material_fingerprint=h("d3a-material"),
        original_d2y_partition_material_fingerprint=partition,
        rehydrated_partition_material_fingerprint=partition,
        raw_training_source_hash=h("train"),
        raw_development_source_hash=h("development"),
        training_universe_hash=h("train-universe"),
        development_universe_hash=h("development-universe"),
        artifact_policy=ARTIFACT_POLICY,
        source_policy=SOURCE_POLICY,
        partition_policy=PARTITION_POLICY,
        downstream_policy=DOWNSTREAM_POLICY,
        exact_artifact_verified=True,
        exact_inventory_verified=True,
        exact_tar_verified=True,
        exact_source_file_family_verified=True,
        exact_original_receipt_family_verified=True,
        exact_d2z_material_verified=True,
        exact_d3a_scientific_identity_verified=True,
        exact_original_partition_fingerprint_reproduced=True,
        network_used_by_rehydrator=False,
        provider_network_used_by_rehydrator=False,
        train_label_artifact_materialized=False,
        development_label_artifact_materialized=False,
        prediction_values_loaded=False,
        development_metrics_computed=False,
        qlib_runtime_used=False,
        final_holdout_values_loaded=False,
        promotion_authorized=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exact_original_partition_fingerprint_reproduced", False),
        ("network_used_by_rehydrator", True),
        ("provider_network_used_by_rehydrator", True),
        ("train_label_artifact_materialized", True),
        ("development_label_artifact_materialized", True),
        ("prediction_values_loaded", True),
        ("development_metrics_computed", True),
        ("qlib_runtime_used", True),
        ("final_holdout_values_loaded", True),
        ("promotion_authorized", True),
        ("execution_authorized", True),
        ("paper_execution_authorized", True),
        ("capital_authority", "PAPER"),
        ("live_trading", "ENABLED"),
    ],
)
def test_d3b_evidence_cannot_weaken_exactness_or_escalate_authority(field, value):
    with pytest.raises((CertifiedArtifactRehydrationGovernanceError, CertifiedArtifactRehydrationIntegrityError)):
        replace(_dummy_evidence(), **{field: value})


def test_d3b_evidence_rejects_nonidentical_original_and_rehydrated_partition_fingerprint():
    with pytest.raises(CertifiedArtifactRehydrationIntegrityError, match="partition fingerprint was not reproduced"):
        replace(_dummy_evidence(), rehydrated_partition_material_fingerprint="f" * 64)
