"""OSS-3D3A rehydration of the certified D2Y real-data campaign.

This module is deliberately Qlib-free.  It accepts only the already-downloaded
GitHub Actions artifact payload produced by the certified D2X/D2Y campaign,
verifies the durable D2Y/D2Z commitments, safely reconstructs the external D2V
evidence root, re-verifies every descriptor against D2Z, and finally executes
the existing D2W handoff with network disabled.

It does not acquire market data, materialize DEVELOPMENT labels, execute models,
observe FINAL_HOLDOUT, or grant PAPER/capital/LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from typing import Mapping

from autotrade.research.oss3_market_collection import canonical_oss3d2u_collection_plan
from autotrade.research.oss3_real_campaign_evidence import (
    ARTIFACT_NAME,
    EVIDENCE_TAR_SHA256,
    EXPECTED_SEAL_FINGERPRINT as D2Y_SEAL_FINGERPRINT,
    SOURCE_FILE_COUNT,
    SOURCE_INVENTORY_SHA256,
    SOURCE_TOTAL_BYTES,
    WORKFLOW_RUN_ID,
    canonical_oss3d2y_real_campaign_evidence_seal,
    verify_oss3d2y_real_campaign_evidence_seal,
)
from autotrade.research.oss3_real_descriptor_identity import (
    STABLE_MATERIAL_ROOT,
    load_canonical_oss3d2z_descriptor_material_manifest,
    verify_rehydrated_descriptor_material,
)
from labs.oss3_market_data.real_acquisition_campaign import (
    load_and_reverify_material,
    validate_external_evidence_root,
)
from labs.oss3_qlib.sealed_raw_split_handoff import (
    SealedRawSplitHandoffMaterial,
    build_canonical_sealed_raw_split_handoff,
)


OSS3D3A_REHYDRATION_VERSION = "OSS3D3A_CERTIFIED_REAL_CAMPAIGN_REHYDRATION_V1"
INVENTORY_FILENAME = "d2x-v2-evidence-inventory.json"
EVIDENCE_TAR_FILENAME = "oss3d2x-v2-real-evidence-20260908.tar.gz"
EVIDENCE_TAR_PREFIX = "oss3d2x-v2-evidence/"
EXTRACTION_POLICY = "EXACT_D2Y_INVENTORY_SAFE_REGULAR_FILES_ONLY_ATOMIC_EXTERNAL_ROOT_V1"
IDENTITY_POLICY = "EVERY_DESCRIPTOR_MUST_PASS_D2Z_BEFORE_D2W_HANDOFF_V1"
DOWNSTREAM_POLICY = "D2W_RAW_TRAIN_AND_PRELABEL_DEVELOPMENT_ONLY_V1"


class RealCampaignRehydrationError(RuntimeError):
    pass


class RealCampaignRehydrationIntegrityError(RealCampaignRehydrationError):
    pass


class RealCampaignRehydrationGovernanceError(RealCampaignRehydrationError):
    pass


@dataclass(frozen=True, slots=True)
class RealCampaignRehydrationEvidence:
    evidence_version: str
    source_workflow_run_id: int
    source_artifact_name: str
    d2y_seal_fingerprint: str
    d2z_stable_material_root: str
    source_inventory_sha256: str
    source_evidence_tar_sha256: str
    source_file_count: int
    descriptor_count: int
    d2z_descriptor_identities_verified: int
    d2v_campaign_seal_fingerprint: str
    d2u_partition_material_fingerprint: str
    d2w_evidence_fingerprint: str
    research_split_hash: str
    research_universe_identity_hash: str
    raw_training_source_hash: str
    raw_development_source_hash: str
    training_universe_hash: str
    development_universe_hash: str
    extraction_policy: str
    identity_policy: str
    downstream_policy: str
    source_inventory_verified: bool
    source_tar_verified: bool
    exact_extracted_file_family_verified: bool
    d2w_offline_reverification_complete: bool
    network_used_during_rehydration: bool
    qlib_runtime_used: bool
    development_labels_materialized: bool
    development_metrics_computed: bool
    final_holdout_observed: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D3A_REHYDRATION_VERSION:
            raise RealCampaignRehydrationIntegrityError("noncanonical D3A rehydration version")
        if self.source_workflow_run_id != WORKFLOW_RUN_ID or self.source_artifact_name != ARTIFACT_NAME:
            raise RealCampaignRehydrationIntegrityError("D3A source artifact identity drifted")
        if self.d2y_seal_fingerprint != D2Y_SEAL_FINGERPRINT:
            raise RealCampaignRehydrationIntegrityError("D3A D2Y root drifted")
        if self.d2z_stable_material_root != STABLE_MATERIAL_ROOT:
            raise RealCampaignRehydrationIntegrityError("D3A D2Z root drifted")
        for name in (
            "source_inventory_sha256",
            "source_evidence_tar_sha256",
            "d2v_campaign_seal_fingerprint",
            "d2u_partition_material_fingerprint",
            "d2w_evidence_fingerprint",
            "research_split_hash",
            "research_universe_identity_hash",
            "raw_training_source_hash",
            "raw_development_source_hash",
            "training_universe_hash",
            "development_universe_hash",
        ):
            _require_hash(getattr(self, name), name)
        if self.source_inventory_sha256 != SOURCE_INVENTORY_SHA256:
            raise RealCampaignRehydrationIntegrityError("D3A inventory hash drifted")
        if self.source_evidence_tar_sha256 != EVIDENCE_TAR_SHA256:
            raise RealCampaignRehydrationIntegrityError("D3A tar hash drifted")
        if self.source_file_count != SOURCE_FILE_COUNT or self.descriptor_count != 99:
            raise RealCampaignRehydrationIntegrityError("D3A source family count drifted")
        if self.d2z_descriptor_identities_verified != self.descriptor_count:
            raise RealCampaignRehydrationIntegrityError("D3A must verify all D2Z descriptor identities")
        if self.extraction_policy != EXTRACTION_POLICY or self.identity_policy != IDENTITY_POLICY:
            raise RealCampaignRehydrationGovernanceError("D3A extraction/identity policy drifted")
        if self.downstream_policy != DOWNSTREAM_POLICY:
            raise RealCampaignRehydrationGovernanceError("D3A downstream policy drifted")
        if not (
            self.source_inventory_verified
            and self.source_tar_verified
            and self.exact_extracted_file_family_verified
            and self.d2w_offline_reverification_complete
        ):
            raise RealCampaignRehydrationIntegrityError("D3A rehydration proof is incomplete")
        if (
            self.network_used_during_rehydration
            or self.qlib_runtime_used
            or self.development_labels_materialized
            or self.development_metrics_computed
            or self.final_holdout_observed
            or self.promotion_authorized
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise RealCampaignRehydrationGovernanceError("D3A rehydration cannot escalate research authority")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise RealCampaignRehydrationGovernanceError("D3A rehydration cannot grant capital or LIVE")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class RehydratedRealCampaign:
    evidence_root: Path
    handoff: SealedRawSplitHandoffMaterial
    evidence: RealCampaignRehydrationEvidence


def rehydrate_certified_real_campaign(
    *,
    artifact_directory: str | Path,
    extraction_parent: str | Path,
    now: datetime,
    repository_root: str | Path | None = None,
) -> RehydratedRealCampaign:
    """Reconstruct and fully reverify the exact certified D2Y evidence root."""
    artifact_dir = Path(artifact_directory).expanduser().resolve()
    if not artifact_dir.is_dir():
        raise RealCampaignRehydrationIntegrityError("D3A artifact directory does not exist")

    d2y = canonical_oss3d2y_real_campaign_evidence_seal()
    verify_oss3d2y_real_campaign_evidence_seal(d2y)
    d2z = load_canonical_oss3d2z_descriptor_material_manifest()
    plan = canonical_oss3d2u_collection_plan()
    if d2z.plan_fingerprint != plan.fingerprint or d2z.collection_id != plan.collection_id:
        raise RealCampaignRehydrationIntegrityError("D3A D2Z/D2U plan binding drifted")

    inventory_path = artifact_dir / INVENTORY_FILENAME
    tar_path = artifact_dir / EVIDENCE_TAR_FILENAME
    inventory_raw = _read_exact_file(inventory_path, expected_sha=SOURCE_INVENTORY_SHA256)
    tar_raw = _read_exact_file(tar_path, expected_sha=EVIDENCE_TAR_SHA256)
    inventory = _parse_inventory(inventory_raw)

    parent = validate_external_evidence_root(extraction_parent, repository_root=repository_root)
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / "oss3d3a-certified-d2y-evidence"
    if target.exists():
        _verify_extracted_root(target, inventory)
    else:
        _extract_inventory_tar_atomically(tar_path=tar_path, target=target, inventory=inventory)
        _verify_extracted_root(target, inventory)

    entries_by_descriptor = {
        entry.descriptor_fingerprint: entry
        for entry in d2z.entries
    }
    verified = 0
    for descriptor in plan.descriptors:
        entry = entries_by_descriptor.get(descriptor.fingerprint)
        if entry is None:
            raise RealCampaignRehydrationIntegrityError("D3A D2Z manifest is missing canonical descriptor")
        material = load_and_reverify_material(root=target, plan=plan, descriptor=descriptor)
        rebuilt = verify_rehydrated_descriptor_material(
            entry,
            descriptor=descriptor,
            archive_bytes=material.archive_bytes,
            checksum_bytes=material.checksum_text.encode("utf-8"),
        )
        if rebuilt.artifact_hash != material.snapshot.artifact_hash:
            raise RealCampaignRehydrationIntegrityError("D3A D2Z rebuild differs from persisted D2T snapshot")
        verified += 1

    handoff = build_canonical_sealed_raw_split_handoff(
        evidence_root=target,
        now=now,
        repository_root=repository_root,
    )
    h = handoff.evidence
    if h.d2v_campaign_seal_fingerprint != d2y.campaign_seal_fingerprint:
        raise RealCampaignRehydrationIntegrityError("D3A D2W campaign seal differs from D2Y")
    if h.d2u_partition_material_fingerprint != d2y.d2u_partition_material_fingerprint:
        raise RealCampaignRehydrationIntegrityError("D3A D2W partition material differs from D2Y")
    if h.training_universe_hash != d2y.training_universe_hash:
        raise RealCampaignRehydrationIntegrityError("D3A TRAIN universe differs from D2Y")
    if h.development_universe_hash != d2y.development_universe_hash:
        raise RealCampaignRehydrationIntegrityError("D3A DEVELOPMENT universe differs from D2Y")

    evidence = RealCampaignRehydrationEvidence(
        evidence_version=OSS3D3A_REHYDRATION_VERSION,
        source_workflow_run_id=WORKFLOW_RUN_ID,
        source_artifact_name=ARTIFACT_NAME,
        d2y_seal_fingerprint=d2y.fingerprint,
        d2z_stable_material_root=d2z.stable_material_root,
        source_inventory_sha256=SOURCE_INVENTORY_SHA256,
        source_evidence_tar_sha256=EVIDENCE_TAR_SHA256,
        source_file_count=SOURCE_FILE_COUNT,
        descriptor_count=len(plan.descriptors),
        d2z_descriptor_identities_verified=verified,
        d2v_campaign_seal_fingerprint=h.d2v_campaign_seal_fingerprint,
        d2u_partition_material_fingerprint=h.d2u_partition_material_fingerprint,
        d2w_evidence_fingerprint=h.fingerprint,
        research_split_hash=h.research_split_hash,
        research_universe_identity_hash=h.research_universe_identity_hash,
        raw_training_source_hash=h.raw_training_source_hash,
        raw_development_source_hash=h.raw_development_source_hash,
        training_universe_hash=h.training_universe_hash,
        development_universe_hash=h.development_universe_hash,
        extraction_policy=EXTRACTION_POLICY,
        identity_policy=IDENTITY_POLICY,
        downstream_policy=DOWNSTREAM_POLICY,
        source_inventory_verified=True,
        source_tar_verified=True,
        exact_extracted_file_family_verified=True,
        d2w_offline_reverification_complete=True,
        network_used_during_rehydration=False,
        qlib_runtime_used=False,
        development_labels_materialized=False,
        development_metrics_computed=False,
        final_holdout_observed=False,
        promotion_authorized=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )
    return RehydratedRealCampaign(evidence_root=target, handoff=handoff, evidence=evidence)


def write_rehydration_evidence(evidence: RealCampaignRehydrationEvidence, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = evidence.to_dict()
    payload["fingerprint"] = evidence.fingerprint
    target.write_bytes(_canonical_json_bytes(payload) + b"\n")


def _read_exact_file(path: Path, *, expected_sha: str) -> bytes:
    if not path.is_file():
        raise RealCampaignRehydrationIntegrityError(f"D3A artifact file missing: {path.name}")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != expected_sha:
        raise RealCampaignRehydrationIntegrityError(f"D3A artifact file hash drifted: {path.name}")
    return raw


def _parse_inventory(raw: bytes) -> dict[str, tuple[int, str]]:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RealCampaignRehydrationIntegrityError("D3A inventory is invalid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise RealCampaignRehydrationIntegrityError("D3A inventory top level must be object")
    if document.get("file_count") != SOURCE_FILE_COUNT or document.get("descriptor_count") != 99:
        raise RealCampaignRehydrationIntegrityError("D3A inventory family count drifted")
    raw_files = document.get("files")
    if not isinstance(raw_files, list) or len(raw_files) != SOURCE_FILE_COUNT:
        raise RealCampaignRehydrationIntegrityError("D3A inventory file list is incomplete")
    result: dict[str, tuple[int, str]] = {}
    total = 0
    for item in raw_files:
        if not isinstance(item, Mapping):
            raise RealCampaignRehydrationIntegrityError("D3A inventory file entry is invalid")
        rel = item.get("path")
        size = item.get("bytes")
        digest = item.get("sha256")
        if not isinstance(rel, str) or not _safe_relative(rel):
            raise RealCampaignRehydrationIntegrityError("D3A inventory path is unsafe")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise RealCampaignRehydrationIntegrityError("D3A inventory size is invalid")
        _require_hash(digest, "inventory sha256")
        if rel in result:
            raise RealCampaignRehydrationIntegrityError("D3A inventory contains duplicate path")
        result[rel] = (size, digest)
        total += size
    if total != SOURCE_TOTAL_BYTES:
        raise RealCampaignRehydrationIntegrityError("D3A inventory total byte count drifted")
    return result


def _extract_inventory_tar_atomically(
    *,
    tar_path: Path,
    target: Path,
    inventory: Mapping[str, tuple[int, str]],
) -> None:
    staging = target.with_name(target.name + ".staging")
    if staging.exists():
        raise RealCampaignRehydrationGovernanceError("D3A stale extraction staging requires operator review")
    staging.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(tar_path, mode="r:gz") as archive:
            file_members: dict[str, tarfile.TarInfo] = {}
            for member in archive.getmembers():
                name = member.name
                if member.isdir():
                    continue
                if not member.isfile():
                    raise RealCampaignRehydrationGovernanceError("D3A tar may contain regular files only")
                if not name.startswith(EVIDENCE_TAR_PREFIX):
                    raise RealCampaignRehydrationIntegrityError("D3A tar member is outside certified prefix")
                rel = name[len(EVIDENCE_TAR_PREFIX):]
                if not _safe_relative(rel) or rel in file_members:
                    raise RealCampaignRehydrationIntegrityError("D3A tar member path is unsafe or duplicated")
                file_members[rel] = member
            if set(file_members) != set(inventory):
                raise RealCampaignRehydrationIntegrityError("D3A tar file family differs from certified inventory")
            for rel, member in file_members.items():
                expected_size, expected_sha = inventory[rel]
                if member.size != expected_size:
                    raise RealCampaignRehydrationIntegrityError("D3A tar member size differs from inventory")
                source = archive.extractfile(member)
                if source is None:
                    raise RealCampaignRehydrationIntegrityError("D3A tar regular file is unreadable")
                raw = source.read(expected_size + 1)
                if len(raw) != expected_size or sha256(raw).hexdigest() != expected_sha:
                    raise RealCampaignRehydrationIntegrityError("D3A tar member bytes differ from inventory")
                destination = staging / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
        os.replace(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _verify_extracted_root(root: Path, inventory: Mapping[str, tuple[int, str]]) -> None:
    if not root.is_dir() or root.is_symlink():
        raise RealCampaignRehydrationIntegrityError("D3A extracted evidence root is invalid")
    actual = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
    }
    if set(actual) != set(inventory):
        raise RealCampaignRehydrationIntegrityError("D3A extracted file family differs from inventory")
    for rel, path in actual.items():
        if path.is_symlink():
            raise RealCampaignRehydrationGovernanceError("D3A extracted evidence cannot contain symlinks")
        expected_size, expected_sha = inventory[rel]
        raw = path.read_bytes()
        if len(raw) != expected_size or sha256(raw).hexdigest() != expected_sha:
            raise RealCampaignRehydrationIntegrityError("D3A extracted file differs from certified inventory")


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and path.as_posix() == value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise RealCampaignRehydrationIntegrityError(f"D3A {name} must be lowercase SHA-256")
