"""OSS-3D3B exact rehydration of the original certified D2Y Actions artifact.

D3B consumes a local copy of the exact GitHub Actions artifact already frozen by
D2Y.  It does not download the artifact itself and performs no provider/network
I/O.  The rehydrator proves the complete container/inventory/tar evidence, then
turns the original 99 descriptor ZIP/CHECKSUM pairs plus their original receipt
hashes into D3A inputs.

Because D3B uses the *original* D2Y receipts rather than a future reacquisition,
it requires the D3A/D2U partition-material fingerprint to reproduce the exact
original D2Y fingerprint as an additional audit equality gate.

Research only: no labels, Qlib, metrics, FINAL_HOLDOUT, broker, OMS, Safety,
PAPER, capital or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import Mapping
import zipfile

from autotrade.research.oss3_market_collection import canonical_oss3d2u_collection_plan
from autotrade.research.oss3_real_campaign_evidence import (
    ARTIFACT_SIZE_BYTES,
    ARTIFACT_ZIP_SHA256,
    DESCRIPTOR_COUNT,
    D2U_PARTITION_MATERIAL_FINGERPRINT,
    EVIDENCE_TAR_CHECKSUM_FILE_SHA256,
    EVIDENCE_TAR_SHA256,
    FIRST_RESULT_SHA256,
    OFFLINE_RESULT_SHA256,
    SOURCE_FILE_COUNT,
    SOURCE_INVENTORY_SHA256,
    DurableRealCampaignEvidenceSeal,
    canonical_oss3d2y_real_campaign_evidence_seal,
    verify_oss3d2y_real_campaign_evidence_seal,
)
from autotrade.research.oss3_real_descriptor_identity import (
    DurableDescriptorMaterialManifest,
    StableDescriptorMaterialIdentity,
    load_canonical_oss3d2z_descriptor_material_manifest,
    verify_durable_descriptor_material_manifest,
)

from .stable_rehydrated_raw_split import (
    RehydratedDescriptorMaterial,
    StableRehydratedRawSplitMaterial,
    build_canonical_stable_rehydrated_raw_split,
)


OSS3D3B_EVIDENCE_VERSION = "OSS3D3B_CERTIFIED_ARTIFACT_REAL_REHYDRATION_EVIDENCE_V1"
OSS3D3B_MATERIAL_VERSION = "OSS3D3B_CERTIFIED_ARTIFACT_REAL_REHYDRATION_MATERIAL_V1"
ARTIFACT_POLICY = "EXACT_D2Y_ACTIONS_ZIP_THEN_EXACT_INVENTORY_AND_TAR_V1"
SOURCE_POLICY = "ORIGINAL_D2Y_DESCRIPTOR_BYTES_AND_RECEIPTS_ONLY_V1"
PARTITION_POLICY = "ORIGINAL_D2Y_RECEIPTS_MUST_REPRODUCE_ORIGINAL_PARTITION_FINGERPRINT_V1"
DOWNSTREAM_POLICY = "D3A_STABLE_RAW_SPLIT_ONLY_NO_LABEL_OR_MODEL_EXECUTION_V1"

FIRST_RESULT_NAME = "d2x-v2-real-result.json"
OFFLINE_RESULT_NAME = "d2x-v2-offline-reverify-result.json"
INVENTORY_NAME = "d2x-v2-evidence-inventory.json"
EVIDENCE_TAR_NAME = "oss3d2x-v2-real-evidence-20260908.tar.gz"
EVIDENCE_TAR_CHECKSUM_NAME = EVIDENCE_TAR_NAME + ".sha256"
EXPECTED_ARTIFACT_MEMBERS = (
    FIRST_RESULT_NAME,
    OFFLINE_RESULT_NAME,
    INVENTORY_NAME,
    EVIDENCE_TAR_NAME,
    EVIDENCE_TAR_CHECKSUM_NAME,
)
EVIDENCE_ROOT_NAME = "oss3d2x-v2-evidence"
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_TAR_BYTES = 12 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 32 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class CertifiedArtifactRehydrationError(RuntimeError):
    pass


class CertifiedArtifactRehydrationIntegrityError(CertifiedArtifactRehydrationError):
    pass


class CertifiedArtifactRehydrationGovernanceError(CertifiedArtifactRehydrationError):
    pass


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _require_hash(value: str, field: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise CertifiedArtifactRehydrationIntegrityError(f"D3B {field} must be a lowercase SHA-256 digest")


def _read_json_exact(raw: bytes, field: str) -> Mapping[str, object]:
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CertifiedArtifactRehydrationIntegrityError(f"D3B {field} is not canonical UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise CertifiedArtifactRehydrationIntegrityError(f"D3B {field} must be a JSON object")
    if raw != _canonical_json_bytes(document) + b"\n":
        raise CertifiedArtifactRehydrationIntegrityError(f"D3B {field} serialization is not canonical")
    return document


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CertifiedArtifactRehydrationIntegrityError(f"D3B duplicate JSON key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class ArtifactEnvelopeExpectations:
    artifact_size_bytes: int
    artifact_zip_sha256: str
    first_result_sha256: str
    offline_result_sha256: str
    inventory_sha256: str
    evidence_tar_sha256: str
    evidence_tar_checksum_sha256: str
    source_file_count: int

    def __post_init__(self) -> None:
        if not 0 < self.artifact_size_bytes <= MAX_ARTIFACT_BYTES:
            raise CertifiedArtifactRehydrationGovernanceError("D3B artifact size expectation is outside bound")
        if not 1 <= self.source_file_count <= 10_000:
            raise CertifiedArtifactRehydrationGovernanceError("D3B source file count expectation is outside bound")
        for name in (
            "artifact_zip_sha256",
            "first_result_sha256",
            "offline_result_sha256",
            "inventory_sha256",
            "evidence_tar_sha256",
            "evidence_tar_checksum_sha256",
        ):
            _require_hash(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ArtifactSourceFile:
    relative_path: str
    sha256_hex: str
    size_bytes: int
    content: bytes

    def __post_init__(self) -> None:
        _validate_relative_path(self.relative_path)
        _require_hash(self.sha256_hex, "source file sha256")
        if not isinstance(self.content, bytes):
            raise TypeError("D3B source file content must be bytes")
        if self.size_bytes != len(self.content) or self.size_bytes < 1:
            raise CertifiedArtifactRehydrationIntegrityError("D3B source file size differs from content")
        if self.size_bytes > MAX_SOURCE_FILE_BYTES:
            raise CertifiedArtifactRehydrationGovernanceError("D3B source file exceeds per-file bound")
        if sha256(self.content).hexdigest() != self.sha256_hex:
            raise CertifiedArtifactRehydrationIntegrityError("D3B source file content differs from inventory hash")


@dataclass(frozen=True, slots=True)
class CertifiedArtifactEnvelope:
    artifact_zip_sha256: str
    artifact_size_bytes: int
    first_result_sha256: str
    offline_result_sha256: str
    inventory_sha256: str
    evidence_tar_sha256: str
    evidence_tar_checksum_sha256: str
    inventory: Mapping[str, object]
    source_files: tuple[ArtifactSourceFile, ...]

    def __post_init__(self) -> None:
        for name in (
            "artifact_zip_sha256",
            "first_result_sha256",
            "offline_result_sha256",
            "inventory_sha256",
            "evidence_tar_sha256",
            "evidence_tar_checksum_sha256",
        ):
            _require_hash(getattr(self, name), name)
        if self.artifact_size_bytes < 1:
            raise CertifiedArtifactRehydrationIntegrityError("D3B artifact size is invalid")
        if len(self.source_files) < 1:
            raise CertifiedArtifactRehydrationIntegrityError("D3B artifact envelope has no source evidence")
        paths = tuple(item.relative_path for item in self.source_files)
        if len(set(paths)) != len(paths):
            raise CertifiedArtifactRehydrationIntegrityError("D3B source evidence contains duplicate paths")

    @property
    def source_file_map(self) -> dict[str, ArtifactSourceFile]:
        return {item.relative_path: item for item in self.source_files}

    @property
    def source_inventory_root(self) -> str:
        return _hash([[item.relative_path, item.sha256_hex, item.size_bytes] for item in self.source_files])


@dataclass(frozen=True, slots=True)
class CertifiedArtifactRealRehydrationEvidence:
    evidence_version: str
    d2y_seal_fingerprint: str
    artifact_zip_sha256: str
    source_inventory_sha256: str
    evidence_tar_sha256: str
    source_inventory_root: str
    source_file_count: int
    descriptor_count: int
    d2z_stable_material_root: str
    d3a_scientific_fingerprint: str
    d3a_evidence_fingerprint: str
    d3a_material_fingerprint: str
    original_d2y_partition_material_fingerprint: str
    rehydrated_partition_material_fingerprint: str
    raw_training_source_hash: str
    raw_development_source_hash: str
    training_universe_hash: str
    development_universe_hash: str
    artifact_policy: str
    source_policy: str
    partition_policy: str
    downstream_policy: str
    exact_artifact_verified: bool
    exact_inventory_verified: bool
    exact_tar_verified: bool
    exact_source_file_family_verified: bool
    exact_original_receipt_family_verified: bool
    exact_d2z_material_verified: bool
    exact_d3a_scientific_identity_verified: bool
    exact_original_partition_fingerprint_reproduced: bool
    network_used_by_rehydrator: bool
    provider_network_used_by_rehydrator: bool
    train_label_artifact_materialized: bool
    development_label_artifact_materialized: bool
    prediction_values_loaded: bool
    development_metrics_computed: bool
    qlib_runtime_used: bool
    final_holdout_values_loaded: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D3B_EVIDENCE_VERSION:
            raise CertifiedArtifactRehydrationIntegrityError("noncanonical D3B evidence version")
        for name in (
            "d2y_seal_fingerprint",
            "artifact_zip_sha256",
            "source_inventory_sha256",
            "evidence_tar_sha256",
            "source_inventory_root",
            "d2z_stable_material_root",
            "d3a_scientific_fingerprint",
            "d3a_evidence_fingerprint",
            "d3a_material_fingerprint",
            "original_d2y_partition_material_fingerprint",
            "rehydrated_partition_material_fingerprint",
            "raw_training_source_hash",
            "raw_development_source_hash",
            "training_universe_hash",
            "development_universe_hash",
        ):
            _require_hash(getattr(self, name), name)
        if self.source_file_count != SOURCE_FILE_COUNT or self.descriptor_count != DESCRIPTOR_COUNT:
            raise CertifiedArtifactRehydrationIntegrityError("D3B canonical evidence counts drifted")
        if self.artifact_policy != ARTIFACT_POLICY or self.source_policy != SOURCE_POLICY:
            raise CertifiedArtifactRehydrationGovernanceError("D3B artifact/source policy drifted")
        if self.partition_policy != PARTITION_POLICY or self.downstream_policy != DOWNSTREAM_POLICY:
            raise CertifiedArtifactRehydrationGovernanceError("D3B partition/downstream policy drifted")
        if not all(
            (
                self.exact_artifact_verified,
                self.exact_inventory_verified,
                self.exact_tar_verified,
                self.exact_source_file_family_verified,
                self.exact_original_receipt_family_verified,
                self.exact_d2z_material_verified,
                self.exact_d3a_scientific_identity_verified,
                self.exact_original_partition_fingerprint_reproduced,
            )
        ):
            raise CertifiedArtifactRehydrationGovernanceError("D3B exact evidence gates are incomplete")
        if self.original_d2y_partition_material_fingerprint != self.rehydrated_partition_material_fingerprint:
            raise CertifiedArtifactRehydrationIntegrityError("D3B original D2Y partition fingerprint was not reproduced")
        if self.network_used_by_rehydrator or self.provider_network_used_by_rehydrator:
            raise CertifiedArtifactRehydrationGovernanceError("D3B local artifact rehydrator must be offline")
        if (
            self.train_label_artifact_materialized
            or self.development_label_artifact_materialized
            or self.prediction_values_loaded
            or self.development_metrics_computed
            or self.qlib_runtime_used
            or self.final_holdout_values_loaded
            or self.promotion_authorized
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise CertifiedArtifactRehydrationGovernanceError("D3B may rehydrate raw research material only")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise CertifiedArtifactRehydrationGovernanceError("D3B has no capital/LIVE authority")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class CertifiedArtifactRealRehydrationMaterial:
    material_version: str
    raw_split: StableRehydratedRawSplitMaterial
    evidence: CertifiedArtifactRealRehydrationEvidence

    def __post_init__(self) -> None:
        if self.material_version != OSS3D3B_MATERIAL_VERSION:
            raise CertifiedArtifactRehydrationIntegrityError("noncanonical D3B material version")
        if not isinstance(self.raw_split, StableRehydratedRawSplitMaterial):
            raise TypeError("raw_split must be StableRehydratedRawSplitMaterial")
        if self.raw_split.scientific_fingerprint != self.evidence.d3a_scientific_fingerprint:
            raise CertifiedArtifactRehydrationIntegrityError("D3B D3A scientific fingerprint differs from evidence")
        if self.raw_split.evidence.fingerprint != self.evidence.d3a_evidence_fingerprint:
            raise CertifiedArtifactRehydrationIntegrityError("D3B D3A evidence fingerprint differs")
        if self.raw_split.fingerprint != self.evidence.d3a_material_fingerprint:
            raise CertifiedArtifactRehydrationIntegrityError("D3B D3A material fingerprint differs")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "material_version": self.material_version,
                "d3a_material_fingerprint": self.raw_split.fingerprint,
                "evidence_fingerprint": self.evidence.fingerprint,
            }
        )


def canonical_artifact_expectations(
    d2y: DurableRealCampaignEvidenceSeal | None = None,
) -> ArtifactEnvelopeExpectations:
    seal = d2y or canonical_oss3d2y_real_campaign_evidence_seal()
    verify_oss3d2y_real_campaign_evidence_seal(seal)
    return ArtifactEnvelopeExpectations(
        artifact_size_bytes=seal.artifact_size_bytes,
        artifact_zip_sha256=seal.artifact_zip_sha256,
        first_result_sha256=seal.first_result_sha256,
        offline_result_sha256=seal.offline_result_sha256,
        inventory_sha256=seal.source_inventory_sha256,
        evidence_tar_sha256=seal.evidence_tar_sha256,
        evidence_tar_checksum_sha256=seal.evidence_tar_checksum_file_sha256,
        source_file_count=seal.source_file_count,
    )


def load_certified_artifact_envelope(
    artifact_zip_path: str | Path,
    *,
    expectations: ArtifactEnvelopeExpectations,
) -> CertifiedArtifactEnvelope:
    target = Path(artifact_zip_path)
    if not target.is_file():
        raise CertifiedArtifactRehydrationIntegrityError("D3B artifact ZIP does not exist")
    if target.stat().st_size > MAX_ARTIFACT_BYTES:
        raise CertifiedArtifactRehydrationGovernanceError("D3B artifact ZIP exceeds size bound")
    raw_zip = target.read_bytes()
    if len(raw_zip) != expectations.artifact_size_bytes:
        raise CertifiedArtifactRehydrationIntegrityError("D3B artifact ZIP size differs from certified expectation")
    if sha256(raw_zip).hexdigest() != expectations.artifact_zip_sha256:
        raise CertifiedArtifactRehydrationIntegrityError("D3B artifact ZIP hash differs from certified expectation")

    top = _read_exact_zip_members(raw_zip)
    _require_member_hash(top, FIRST_RESULT_NAME, expectations.first_result_sha256)
    _require_member_hash(top, OFFLINE_RESULT_NAME, expectations.offline_result_sha256)
    _require_member_hash(top, INVENTORY_NAME, expectations.inventory_sha256)
    _require_member_hash(top, EVIDENCE_TAR_NAME, expectations.evidence_tar_sha256)
    _require_member_hash(top, EVIDENCE_TAR_CHECKSUM_NAME, expectations.evidence_tar_checksum_sha256)

    inventory = _read_json_exact(top[INVENTORY_NAME], "source inventory")
    inventory_records = _validate_inventory(inventory, expectations)
    _validate_tar_checksum_file(top[EVIDENCE_TAR_CHECKSUM_NAME], expectations.evidence_tar_sha256)
    source_files = _read_exact_tar_source_files(top[EVIDENCE_TAR_NAME], inventory_records)

    return CertifiedArtifactEnvelope(
        artifact_zip_sha256=expectations.artifact_zip_sha256,
        artifact_size_bytes=len(raw_zip),
        first_result_sha256=expectations.first_result_sha256,
        offline_result_sha256=expectations.offline_result_sha256,
        inventory_sha256=expectations.inventory_sha256,
        evidence_tar_sha256=expectations.evidence_tar_sha256,
        evidence_tar_checksum_sha256=expectations.evidence_tar_checksum_sha256,
        inventory=inventory,
        source_files=source_files,
    )


def rehydrate_canonical_d2y_artifact(
    artifact_zip_path: str | Path,
) -> CertifiedArtifactRealRehydrationMaterial:
    d2y = canonical_oss3d2y_real_campaign_evidence_seal()
    verify_oss3d2y_real_campaign_evidence_seal(d2y)
    d2z = load_canonical_oss3d2z_descriptor_material_manifest()
    verify_durable_descriptor_material_manifest(d2z)
    plan = canonical_oss3d2u_collection_plan()
    envelope = load_certified_artifact_envelope(
        artifact_zip_path,
        expectations=canonical_artifact_expectations(d2y),
    )
    if envelope.inventory.get("collection_id") != plan.collection_id:
        raise CertifiedArtifactRehydrationIntegrityError("D3B inventory collection differs from canonical D2U")
    if envelope.inventory.get("plan_fingerprint") != plan.fingerprint:
        raise CertifiedArtifactRehydrationIntegrityError("D3B inventory plan differs from canonical D2U")
    if envelope.inventory.get("descriptor_count") != len(plan.descriptors):
        raise CertifiedArtifactRehydrationIntegrityError("D3B inventory descriptor count differs from canonical D2U")

    sources = envelope.source_file_map
    entries = {item.descriptor_fingerprint: item for item in d2z.entries}
    materials: list[RehydratedDescriptorMaterial] = []
    for descriptor in plan.descriptors:
        entry = entries.get(descriptor.fingerprint)
        if entry is None:
            raise CertifiedArtifactRehydrationIntegrityError("D3B D2Z identity missing for canonical descriptor")
        base = f"{descriptor.instrument.symbol}/{descriptor.period}"
        archive = _source_content(sources, f"{base}/{descriptor.archive_filename}")
        checksum = _source_content(sources, f"{base}/{descriptor.checksum_filename}")
        snapshot_raw = _source_content(sources, f"{base}/d2t-snapshot.json")
        receipt_raw = _source_content(sources, f"{base}/d2u-acquisition-receipt.json")
        _verify_original_snapshot_document(snapshot_raw, descriptor.fingerprint, entry)
        receipt_hash = _verify_original_receipt_document(
            receipt_raw,
            descriptor_fingerprint=descriptor.fingerprint,
            d2y=d2y,
            entry=entry,
        )
        materials.append(
            RehydratedDescriptorMaterial(
                descriptor=descriptor,
                archive_bytes=archive,
                checksum_bytes=checksum,
                acquisition_receipt_hash=receipt_hash,
            )
        )

    raw_split = build_canonical_stable_rehydrated_raw_split(materials=tuple(materials))
    if raw_split.partition_material.fingerprint != d2y.d2u_partition_material_fingerprint:
        raise CertifiedArtifactRehydrationIntegrityError(
            "D3B original artifact did not reproduce original D2Y D2U partition material fingerprint"
        )
    if raw_split.evidence.reacquired_d2u_partition_material_fingerprint != D2U_PARTITION_MATERIAL_FINGERPRINT:
        raise CertifiedArtifactRehydrationIntegrityError("D3B D3A evidence partition fingerprint drifted")

    evidence = CertifiedArtifactRealRehydrationEvidence(
        evidence_version=OSS3D3B_EVIDENCE_VERSION,
        d2y_seal_fingerprint=d2y.fingerprint,
        artifact_zip_sha256=envelope.artifact_zip_sha256,
        source_inventory_sha256=envelope.inventory_sha256,
        evidence_tar_sha256=envelope.evidence_tar_sha256,
        source_inventory_root=envelope.source_inventory_root,
        source_file_count=len(envelope.source_files),
        descriptor_count=len(plan.descriptors),
        d2z_stable_material_root=d2z.stable_material_root,
        d3a_scientific_fingerprint=raw_split.scientific_fingerprint,
        d3a_evidence_fingerprint=raw_split.evidence.fingerprint,
        d3a_material_fingerprint=raw_split.fingerprint,
        original_d2y_partition_material_fingerprint=d2y.d2u_partition_material_fingerprint,
        rehydrated_partition_material_fingerprint=raw_split.partition_material.fingerprint,
        raw_training_source_hash=raw_split.training_source.source_hash,
        raw_development_source_hash=raw_split.development_source.source_hash,
        training_universe_hash=raw_split.partition_material.training.universe_hash,
        development_universe_hash=raw_split.partition_material.development.universe_hash,
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
    return CertifiedArtifactRealRehydrationMaterial(
        material_version=OSS3D3B_MATERIAL_VERSION,
        raw_split=raw_split,
        evidence=evidence,
    )


def _read_exact_zip_members(raw_zip: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(BytesIO(raw_zip), "r") as archive:
            infos = archive.infolist()
            names = tuple(info.filename for info in infos)
            if names != EXPECTED_ARTIFACT_MEMBERS:
                raise CertifiedArtifactRehydrationIntegrityError("D3B artifact ZIP member family/order drifted")
            if len(set(names)) != len(names):
                raise CertifiedArtifactRehydrationIntegrityError("D3B artifact ZIP contains duplicate members")
            result = {}
            for info in infos:
                if info.is_dir() or _zip_member_is_link(info):
                    raise CertifiedArtifactRehydrationGovernanceError("D3B artifact ZIP may contain regular files only")
                if info.file_size > MAX_SOURCE_TOTAL_BYTES:
                    raise CertifiedArtifactRehydrationGovernanceError("D3B artifact member exceeds size bound")
                result[info.filename] = archive.read(info)
            return result
    except CertifiedArtifactRehydrationError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise CertifiedArtifactRehydrationIntegrityError("D3B artifact is not a valid ZIP") from exc


def _zip_member_is_link(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _require_member_hash(members: Mapping[str, bytes], name: str, expected: str) -> None:
    if sha256(members[name]).hexdigest() != expected:
        raise CertifiedArtifactRehydrationIntegrityError(f"D3B artifact member hash drifted: {name}")


def _validate_inventory(
    inventory: Mapping[str, object],
    expectations: ArtifactEnvelopeExpectations,
) -> dict[str, tuple[str, int]]:
    if inventory.get("descriptor_count") != DESCRIPTOR_COUNT:
        raise CertifiedArtifactRehydrationIntegrityError("D3B inventory descriptor count drifted")
    if inventory.get("file_count") != expectations.source_file_count:
        raise CertifiedArtifactRehydrationIntegrityError("D3B inventory source file count drifted")
    raw_files = inventory.get("files")
    if not isinstance(raw_files, list) or len(raw_files) != expectations.source_file_count:
        raise CertifiedArtifactRehydrationIntegrityError("D3B inventory file list is incomplete")
    result: dict[str, tuple[str, int]] = {}
    total = 0
    previous = None
    for record in raw_files:
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "bytes"}:
            raise CertifiedArtifactRehydrationIntegrityError("D3B inventory record schema drifted")
        path = record["path"]
        digest = record["sha256"]
        size = record["bytes"]
        if not isinstance(path, str):
            raise CertifiedArtifactRehydrationIntegrityError("D3B inventory path must be text")
        _validate_relative_path(path)
        _require_hash(digest, "inventory file sha256")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_SOURCE_FILE_BYTES:
            raise CertifiedArtifactRehydrationGovernanceError("D3B inventory file size is outside bound")
        if path in result:
            raise CertifiedArtifactRehydrationIntegrityError("D3B inventory contains duplicate path")
        if previous is not None and path <= previous:
            raise CertifiedArtifactRehydrationIntegrityError("D3B inventory paths must be strictly sorted")
        previous = path
        result[path] = (digest, size)
        total += size
    if total > MAX_SOURCE_TOTAL_BYTES:
        raise CertifiedArtifactRehydrationGovernanceError("D3B inventory total bytes exceed bound")
    return result


def _validate_relative_path(path: str) -> None:
    pure = PurePosixPath(path)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise CertifiedArtifactRehydrationGovernanceError("D3B evidence path is not safe relative POSIX path")
    if str(pure) != path or "\\" in path:
        raise CertifiedArtifactRehydrationGovernanceError("D3B evidence path is not canonical POSIX form")


def _validate_tar_checksum_file(raw: bytes, expected_digest: str) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CertifiedArtifactRehydrationIntegrityError("D3B tar checksum file is not UTF-8") from exc
    expected = f"{expected_digest}  {EVIDENCE_TAR_NAME}\n"
    if text != expected:
        raise CertifiedArtifactRehydrationIntegrityError("D3B tar checksum file payload drifted")


def _read_exact_tar_source_files(
    raw_tar: bytes,
    inventory: Mapping[str, tuple[str, int]],
) -> tuple[ArtifactSourceFile, ...]:
    if len(raw_tar) > MAX_EVIDENCE_TAR_BYTES:
        raise CertifiedArtifactRehydrationGovernanceError("D3B evidence tar exceeds size bound")
    found: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=BytesIO(raw_tar), mode="r:gz") as archive:
            for member in archive.getmembers():
                pure = PurePosixPath(member.name)
                if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                    raise CertifiedArtifactRehydrationGovernanceError("D3B tar contains unsafe path")
                if not pure.parts or pure.parts[0] != EVIDENCE_ROOT_NAME:
                    raise CertifiedArtifactRehydrationIntegrityError("D3B tar member is outside certified evidence root")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise CertifiedArtifactRehydrationGovernanceError("D3B tar may contain regular files/directories only")
                relative = str(PurePosixPath(*pure.parts[1:]))
                _validate_relative_path(relative)
                if relative not in inventory:
                    raise CertifiedArtifactRehydrationIntegrityError("D3B tar contains file absent from inventory")
                if relative in found:
                    raise CertifiedArtifactRehydrationIntegrityError("D3B tar contains duplicate source file")
                expected_hash, expected_size = inventory[relative]
                if member.size != expected_size or member.size > MAX_SOURCE_FILE_BYTES:
                    raise CertifiedArtifactRehydrationIntegrityError("D3B tar source file size differs from inventory")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise CertifiedArtifactRehydrationIntegrityError("D3B tar source file could not be read")
                content = extracted.read(MAX_SOURCE_FILE_BYTES + 1)
                if len(content) != expected_size or sha256(content).hexdigest() != expected_hash:
                    raise CertifiedArtifactRehydrationIntegrityError("D3B tar source file differs from inventory")
                found[relative] = content
    except CertifiedArtifactRehydrationError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise CertifiedArtifactRehydrationIntegrityError("D3B evidence tar is invalid") from exc
    if set(found) != set(inventory):
        raise CertifiedArtifactRehydrationIntegrityError("D3B tar source file family differs from inventory")
    return tuple(
        ArtifactSourceFile(path, inventory[path][0], inventory[path][1], found[path])
        for path in sorted(inventory)
    )


def _source_content(sources: Mapping[str, ArtifactSourceFile], path: str) -> bytes:
    item = sources.get(path)
    if item is None:
        raise CertifiedArtifactRehydrationIntegrityError(f"D3B source file missing: {path}")
    return item.content


def _verify_original_snapshot_document(
    raw: bytes,
    descriptor_fingerprint: str,
    entry: StableDescriptorMaterialIdentity,
) -> None:
    document = _read_json_exact(raw, "D2T snapshot")
    manifest = document.get("manifest")
    if not isinstance(manifest, dict):
        raise CertifiedArtifactRehydrationIntegrityError("D3B original snapshot manifest is missing")
    expected = {
        "artifact_hash": entry.snapshot_artifact_hash,
        "descriptor_fingerprint": descriptor_fingerprint,
        "archive_sha256": entry.archive_sha256,
        "checksum_payload_sha256": entry.checksum_payload_sha256,
        "normalized_dataset_hash": entry.normalized_dataset_hash,
    }
    observed = {
        "artifact_hash": document.get("artifact_hash"),
        "descriptor_fingerprint": manifest.get("descriptor_fingerprint"),
        "archive_sha256": manifest.get("archive_sha256"),
        "checksum_payload_sha256": manifest.get("checksum_payload_sha256"),
        "normalized_dataset_hash": manifest.get("normalized_dataset_hash"),
    }
    if observed != expected:
        raise CertifiedArtifactRehydrationIntegrityError("D3B original D2T snapshot differs from D2Z identity")


def _verify_original_receipt_document(
    raw: bytes,
    *,
    descriptor_fingerprint: str,
    d2y: DurableRealCampaignEvidenceSeal,
    entry: StableDescriptorMaterialIdentity,
) -> str:
    document = _read_json_exact(raw, "D2U acquisition receipt")
    required = {
        "collection_id": d2y.collection_id,
        "plan_fingerprint": d2y.plan_fingerprint,
        "descriptor_fingerprint": descriptor_fingerprint,
        "archive_payload_sha256": entry.archive_sha256,
        "checksum_a_payload_sha256": entry.checksum_payload_sha256,
        "checksum_b_payload_sha256": entry.checksum_payload_sha256,
        "d2t_snapshot_artifact_hash": entry.snapshot_artifact_hash,
        "d2t_normalized_dataset_hash": entry.normalized_dataset_hash,
        "checksum_a_status": 200,
        "archive_status": 200,
        "checksum_b_status": 200,
        "checksum_payloads_identical": True,
        "exact_final_urls": True,
        "request_count": 3,
        "retries_performed": 0,
        "network_used": True,
        "provider_credentials_used": False,
        "trading_endpoints_used": False,
        "final_holdout_values_requested": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    for key, value in required.items():
        if document.get(key) != value:
            raise CertifiedArtifactRehydrationIntegrityError(f"D3B original D2U receipt field drifted: {key}")
    if not isinstance(document.get("acquired_at"), str) or not document["acquired_at"]:
        raise CertifiedArtifactRehydrationIntegrityError("D3B original D2U receipt acquired_at is missing")
    return sha256(_canonical_json_bytes(document)).hexdigest()
