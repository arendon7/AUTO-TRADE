from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from autotrade.research.oss3_market_collection import canonical_oss3d2u_collection_plan
from autotrade.research.oss3_market_snapshot import (
    BinanceSpotArchiveDescriptor,
    HistoricalMarketSnapshotArtifact,
    build_binance_spot_archive_snapshot,
)
from autotrade.research.oss3_real_campaign_evidence import (
    ARTIFACT_ZIP_SHA256,
    EVIDENCE_TAR_SHA256,
    EXPECTED_SEAL_FINGERPRINT as D2Y_SEAL_FINGERPRINT,
    SOURCE_INVENTORY_SHA256,
    canonical_oss3d2y_real_campaign_evidence_seal,
    verify_oss3d2y_real_campaign_evidence_seal,
)


OSS3D2Z_CONTRACT_VERSION = "OSS3D2Z_DURABLE_DESCRIPTOR_MATERIAL_MANIFEST_V1"
MATERIAL_MANIFEST_VERSION = "OSS3D2Z_REAL_DESCRIPTOR_MATERIAL_MANIFEST_V1"
IDENTITY_POLICY = "EXACT_PROVIDER_ARCHIVE_CHECKSUM_AND_D2T_OUTPUT_RECEIPT_TIMESTAMP_INDEPENDENT_V1"
ENTRY_SCHEMA = (
    "descriptor_fingerprint",
    "archive_sha256",
    "checksum_payload_sha256",
    "snapshot_artifact_hash",
    "normalized_dataset_hash",
    "material_identity_hash",
)
DESCRIPTOR_COUNT = 99
MATERIAL_MANIFEST_FILE_SHA256 = "0edaf50aadb7a56d106b38c398ba2f7e6a4b09a0b21cb0fdd38c9e18573f696b"
STABLE_MATERIAL_ROOT = "8aef56ea8e96c2c880765440da86e31041d846324f7b9a3ebd79d0a1a470cdaf"
SOURCE_INVENTORY_SHA256_EXPECTED = "94d47d9bfef334b590a0946fa0094b6534c542f1e5ef41d6a481b893f5a6ce57"
SOURCE_EVIDENCE_TAR_SHA256 = "c5d0f694a68f8bf7b67c35774900efaaaad7b425e95735f60660824e07446277"
SOURCE_ARTIFACT_ZIP_SHA256 = "23598e3bd2f42b645095291115451d115dae60455b813c5653f28ebd5a6b7dc7"

_REPO_ROOT = Path(__file__).resolve().parents[3]
MATERIAL_MANIFEST_PATH = (
    _REPO_ROOT
    / "knowledge"
    / "20_RESEARCH"
    / "evidence"
    / "OSS3D2Z_REAL_DESCRIPTOR_MATERIAL_MANIFEST.json"
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class DescriptorMaterialManifestError(RuntimeError):
    pass


class DescriptorMaterialManifestIntegrityError(DescriptorMaterialManifestError):
    pass


class DescriptorMaterialManifestGovernanceError(DescriptorMaterialManifestError):
    pass


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _require_hash(value: str, field: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise DescriptorMaterialManifestIntegrityError(f"D2Z {field} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class StableDescriptorMaterialIdentity:
    descriptor_fingerprint: str
    archive_sha256: str
    checksum_payload_sha256: str
    snapshot_artifact_hash: str
    normalized_dataset_hash: str
    material_identity_hash: str

    def __post_init__(self) -> None:
        for name in ENTRY_SCHEMA:
            _require_hash(getattr(self, name), name)
        if self.material_identity_hash != _hash(list(self.stable_identity_vector())):
            raise DescriptorMaterialManifestIntegrityError("D2Z stable descriptor material identity drifted")

    def stable_identity_vector(self) -> tuple[str, str, str, str, str]:
        return (
            self.descriptor_fingerprint,
            self.archive_sha256,
            self.checksum_payload_sha256,
            self.snapshot_artifact_hash,
            self.normalized_dataset_hash,
        )

    def to_list(self) -> list[str]:
        return [*self.stable_identity_vector(), self.material_identity_hash]


@dataclass(frozen=True, slots=True)
class DurableDescriptorMaterialManifest:
    manifest_version: str
    identity_policy: str
    entry_schema: tuple[str, ...]
    d2y_seal_fingerprint: str
    source_inventory_sha256: str
    source_evidence_tar_sha256: str
    source_artifact_zip_sha256: str
    collection_id: str
    plan_fingerprint: str
    descriptor_count: int
    stable_material_root: str
    entries: tuple[StableDescriptorMaterialIdentity, ...]

    def __post_init__(self) -> None:
        if self.manifest_version != MATERIAL_MANIFEST_VERSION:
            raise DescriptorMaterialManifestIntegrityError("D2Z manifest version drifted")
        if self.identity_policy != IDENTITY_POLICY:
            raise DescriptorMaterialManifestGovernanceError("D2Z identity policy drifted")
        if self.entry_schema != ENTRY_SCHEMA:
            raise DescriptorMaterialManifestIntegrityError("D2Z compact entry schema drifted")
        for name in (
            "d2y_seal_fingerprint",
            "source_inventory_sha256",
            "source_evidence_tar_sha256",
            "source_artifact_zip_sha256",
            "plan_fingerprint",
            "stable_material_root",
        ):
            _require_hash(getattr(self, name), name)
        if self.descriptor_count != DESCRIPTOR_COUNT or len(self.entries) != DESCRIPTOR_COUNT:
            raise DescriptorMaterialManifestIntegrityError("D2Z requires exactly 99 descriptor identities")
        if len({entry.descriptor_fingerprint for entry in self.entries}) != DESCRIPTOR_COUNT:
            raise DescriptorMaterialManifestIntegrityError("D2Z descriptor identities must be unique")
        if self.stable_material_root != _hash([entry.material_identity_hash for entry in self.entries]):
            raise DescriptorMaterialManifestIntegrityError("D2Z stable material root differs from ordered entries")

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "identity_policy": self.identity_policy,
            "entry_schema": list(self.entry_schema),
            "d2y_seal_fingerprint": self.d2y_seal_fingerprint,
            "source_inventory_sha256": self.source_inventory_sha256,
            "source_evidence_tar_sha256": self.source_evidence_tar_sha256,
            "source_artifact_zip_sha256": self.source_artifact_zip_sha256,
            "collection_id": self.collection_id,
            "plan_fingerprint": self.plan_fingerprint,
            "descriptor_count": self.descriptor_count,
            "stable_material_root": self.stable_material_root,
            "entries": [entry.to_list() for entry in self.entries],
        }


def _entry_from_list(value: object) -> StableDescriptorMaterialIdentity:
    if not isinstance(value, list) or len(value) != len(ENTRY_SCHEMA) or any(not isinstance(item, str) for item in value):
        raise DescriptorMaterialManifestIntegrityError("D2Z compact descriptor entry schema drifted")
    return StableDescriptorMaterialIdentity(*value)


def _manifest_from_document(document: object) -> DurableDescriptorMaterialManifest:
    if not isinstance(document, dict):
        raise DescriptorMaterialManifestIntegrityError("D2Z material manifest must be a JSON object")
    raw = dict(document)
    raw_entries = raw.pop("entries", None)
    raw_schema = raw.pop("entry_schema", None)
    if not isinstance(raw_entries, list) or not isinstance(raw_schema, list):
        raise DescriptorMaterialManifestIntegrityError("D2Z manifest entry schema/entries are missing")
    try:
        return DurableDescriptorMaterialManifest(
            entry_schema=tuple(raw_schema),
            entries=tuple(_entry_from_list(entry) for entry in raw_entries),
            **raw,
        )
    except TypeError as exc:
        raise DescriptorMaterialManifestIntegrityError("D2Z material manifest fields drifted") from exc


def verify_durable_descriptor_material_manifest(manifest: DurableDescriptorMaterialManifest) -> None:
    if not isinstance(manifest, DurableDescriptorMaterialManifest):
        raise TypeError("manifest must be DurableDescriptorMaterialManifest")

    d2y = canonical_oss3d2y_real_campaign_evidence_seal()
    verify_oss3d2y_real_campaign_evidence_seal(d2y)
    plan = canonical_oss3d2u_collection_plan()

    if manifest.d2y_seal_fingerprint != D2Y_SEAL_FINGERPRINT or manifest.d2y_seal_fingerprint != d2y.fingerprint:
        raise DescriptorMaterialManifestIntegrityError("D2Z does not rebind to exact certified D2Y seal")
    if manifest.source_inventory_sha256 != SOURCE_INVENTORY_SHA256_EXPECTED:
        raise DescriptorMaterialManifestIntegrityError("D2Z source inventory commitment drifted")
    if manifest.source_inventory_sha256 != SOURCE_INVENTORY_SHA256 or manifest.source_inventory_sha256 != d2y.source_inventory_sha256:
        raise DescriptorMaterialManifestIntegrityError("D2Z source inventory no longer matches D2Y")
    if manifest.source_evidence_tar_sha256 != SOURCE_EVIDENCE_TAR_SHA256 or manifest.source_evidence_tar_sha256 != EVIDENCE_TAR_SHA256:
        raise DescriptorMaterialManifestIntegrityError("D2Z source evidence tar no longer matches D2Y")
    if manifest.source_artifact_zip_sha256 != SOURCE_ARTIFACT_ZIP_SHA256 or manifest.source_artifact_zip_sha256 != ARTIFACT_ZIP_SHA256:
        raise DescriptorMaterialManifestIntegrityError("D2Z source artifact no longer matches D2Y")
    if manifest.collection_id != plan.collection_id or manifest.plan_fingerprint != plan.fingerprint:
        raise DescriptorMaterialManifestIntegrityError("D2Z does not rebind to exact canonical D2U V2 plan")
    if manifest.stable_material_root != STABLE_MATERIAL_ROOT:
        raise DescriptorMaterialManifestIntegrityError("D2Z stable material root differs from certified root")

    expected_order = tuple(descriptor.fingerprint for descriptor in plan.descriptors)
    observed_order = tuple(entry.descriptor_fingerprint for entry in manifest.entries)
    if observed_order != expected_order:
        raise DescriptorMaterialManifestIntegrityError("D2Z descriptor order differs from canonical D2U plan")


def load_canonical_oss3d2z_descriptor_material_manifest() -> DurableDescriptorMaterialManifest:
    try:
        raw = MATERIAL_MANIFEST_PATH.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DescriptorMaterialManifestIntegrityError("D2Z durable material manifest is unreadable") from exc
    if sha256(raw).hexdigest() != MATERIAL_MANIFEST_FILE_SHA256:
        raise DescriptorMaterialManifestIntegrityError("D2Z durable material manifest file hash drifted")
    if raw != _canonical_json_bytes(document) + b"\n":
        raise DescriptorMaterialManifestIntegrityError("D2Z durable material manifest serialization is not canonical")
    manifest = _manifest_from_document(document)
    verify_durable_descriptor_material_manifest(manifest)
    return manifest


def verify_rehydrated_descriptor_material(
    entry: StableDescriptorMaterialIdentity,
    *,
    descriptor: BinanceSpotArchiveDescriptor,
    archive_bytes: bytes,
    checksum_bytes: bytes,
) -> HistoricalMarketSnapshotArtifact:
    """Offline equality gate for future public-provider reacquisition.

    The caller may acquire bytes through the already-certified D2U public GET
    adapter. This function itself performs no network I/O and admits material
    only when raw archive bytes, exact CHECKSUM payload, D2T artifact identity,
    and normalized dataset identity all reproduce the D2Z commitment.
    """
    if not isinstance(entry, StableDescriptorMaterialIdentity):
        raise TypeError("entry must be StableDescriptorMaterialIdentity")
    if not isinstance(descriptor, BinanceSpotArchiveDescriptor):
        raise TypeError("descriptor must be BinanceSpotArchiveDescriptor")
    if not isinstance(archive_bytes, bytes) or not archive_bytes:
        raise DescriptorMaterialManifestIntegrityError("D2Z rehydrated archive bytes are missing")
    if not isinstance(checksum_bytes, bytes) or not checksum_bytes:
        raise DescriptorMaterialManifestIntegrityError("D2Z rehydrated CHECKSUM bytes are missing")
    if descriptor.fingerprint != entry.descriptor_fingerprint:
        raise DescriptorMaterialManifestIntegrityError("D2Z rehydrated descriptor differs from frozen identity")
    if sha256(archive_bytes).hexdigest() != entry.archive_sha256:
        raise DescriptorMaterialManifestIntegrityError("D2Z rehydrated archive differs from frozen provider bytes")
    if sha256(checksum_bytes).hexdigest() != entry.checksum_payload_sha256:
        raise DescriptorMaterialManifestIntegrityError("D2Z rehydrated CHECKSUM differs from frozen provider bytes")
    try:
        checksum_text = checksum_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DescriptorMaterialManifestIntegrityError("D2Z rehydrated CHECKSUM is not UTF-8") from exc

    snapshot = build_binance_spot_archive_snapshot(
        descriptor=descriptor,
        archive_bytes=archive_bytes,
        checksum_text=checksum_text,
    )
    if snapshot.artifact_hash != entry.snapshot_artifact_hash:
        raise DescriptorMaterialManifestIntegrityError("D2Z rehydrated D2T artifact differs from frozen identity")
    if snapshot.manifest.normalized_dataset_hash != entry.normalized_dataset_hash:
        raise DescriptorMaterialManifestIntegrityError("D2Z rehydrated normalized dataset differs from frozen identity")
    snapshot.verify_source(descriptor=descriptor, archive_bytes=archive_bytes, checksum_text=checksum_text)
    return snapshot
