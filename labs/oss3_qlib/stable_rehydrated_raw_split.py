"""OSS-3D3A stable rehydration bridge from D2Z material into raw D2R/D2S sources.

D3A deliberately separates operational reacquisition lineage from scientific
market-data identity. A future acquisition may produce new D2U receipt hashes
because receipt timestamps change. Those receipt hashes remain auditable and
continue to feed the existing D2U assembler, but they are excluded from D3A's
scientific rehydration fingerprint.

The scientific gate is stricter where it matters: every descriptor must pass
D2Z exact archive/CHECKSUM/D2T equality and the assembled TRAIN and DEVELOPMENT
universes must reproduce the exact D2Y real-campaign universe hashes before raw
D2R/D2S sources are created.

D3A itself performs no network I/O, materializes no labels, executes no model,
and has no FINAL_HOLDOUT, broker, OMS, Safety, PAPER, capital or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Iterable

from autotrade.research.oss3_market_collection import (
    HistoricalCollectionPlan,
    HistoricalResearchPartitionMaterial,
    assemble_historical_collection,
    canonical_oss3d2u_collection_plan,
)
from autotrade.research.oss3_market_snapshot import BinanceSpotArchiveDescriptor
from autotrade.research.oss3_real_campaign_evidence import (
    DurableRealCampaignEvidenceSeal,
    canonical_oss3d2y_real_campaign_evidence_seal,
    verify_oss3d2y_real_campaign_evidence_seal,
)
from autotrade.research.oss3_real_descriptor_identity import (
    MATERIAL_MANIFEST_FILE_SHA256,
    DurableDescriptorMaterialManifest,
    StableDescriptorMaterialIdentity,
    load_canonical_oss3d2z_descriptor_material_manifest,
    verify_durable_descriptor_material_manifest,
    verify_rehydrated_descriptor_material,
)

from .raw_development_provenance import RawDevelopmentMarketSource
from .raw_training_bundle_provenance import (
    RawTrainingMarketSource,
    research_universe_identity_hash,
)


OSS3D3A_EVIDENCE_VERSION = "OSS3D3A_STABLE_REHYDRATED_RAW_SPLIT_EVIDENCE_V1"
OSS3D3A_MATERIAL_VERSION = "OSS3D3A_STABLE_REHYDRATED_RAW_SPLIT_MATERIAL_V1"
SCIENTIFIC_IDENTITY_POLICY = "D2Z_STABLE_MATERIAL_PLUS_D2Y_TRAIN_DEVELOPMENT_UNIVERSE_EQUALITY_V1"
LINEAGE_POLICY = "D2U_REACQUISITION_RECEIPTS_AUDITED_BUT_EXCLUDED_FROM_SCIENTIFIC_IDENTITY_V1"
DOWNSTREAM_POLICY = "D2R_TRAIN_AND_D2S_PRELABEL_DEVELOPMENT_ONLY_V1"
PARTITION_REPRODUCTION_POLICY = "D2U_RECEIPT_BOUND_PARTITION_FINGERPRINT_MAY_CHANGE_BUT_MARKET_UNIVERSES_MUST_NOT_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class StableRehydratedRawSplitError(RuntimeError):
    pass


class StableRehydratedRawSplitIntegrityError(StableRehydratedRawSplitError):
    pass


class StableRehydratedRawSplitGovernanceError(StableRehydratedRawSplitError):
    pass


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _require_hash(value: str, field: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise StableRehydratedRawSplitIntegrityError(f"D3A {field} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class RehydratedDescriptorMaterial:
    """Already-acquired public bytes plus the audit hash of that acquisition receipt."""

    descriptor: BinanceSpotArchiveDescriptor
    archive_bytes: bytes
    checksum_bytes: bytes
    acquisition_receipt_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, BinanceSpotArchiveDescriptor):
            raise TypeError("descriptor must be BinanceSpotArchiveDescriptor")
        if not isinstance(self.archive_bytes, bytes) or not self.archive_bytes:
            raise StableRehydratedRawSplitIntegrityError("D3A archive bytes are missing")
        if not isinstance(self.checksum_bytes, bytes) or not self.checksum_bytes:
            raise StableRehydratedRawSplitIntegrityError("D3A CHECKSUM bytes are missing")
        _require_hash(self.acquisition_receipt_hash, "acquisition_receipt_hash")


@dataclass(frozen=True, slots=True)
class StableRehydratedRawSplitEvidence:
    evidence_version: str
    collection_id: str
    plan_fingerprint: str
    d2y_seal_fingerprint: str
    d2z_manifest_file_sha256: str
    d2z_stable_material_root: str
    descriptor_count: int
    reacquisition_lineage_root: str
    original_d2y_partition_material_fingerprint: str
    reacquired_d2u_partition_material_fingerprint: str
    reacquired_d2u_assembly_evidence_fingerprint: str
    training_warmup_universe_hash: str
    training_universe_hash: str
    development_warmup_universe_hash: str
    development_universe_hash: str
    research_universe_identity_hash: str
    raw_training_source_hash: str
    raw_development_source_hash: str
    scientific_rehydration_fingerprint: str
    scientific_identity_policy: str
    lineage_policy: str
    downstream_policy: str
    partition_reproduction_policy: str
    exact_stable_material_verified: bool
    exact_d2y_training_universe_verified: bool
    exact_d2y_development_universe_verified: bool
    reacquisition_receipts_audited: bool
    receipt_lineage_excluded_from_scientific_identity: bool
    partition_material_fingerprint_reproduction_required: bool
    lineage_timestamps_may_differ: bool
    network_used_by_handoff: bool
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
        if self.evidence_version != OSS3D3A_EVIDENCE_VERSION:
            raise StableRehydratedRawSplitIntegrityError("noncanonical D3A evidence version")
        if not self.collection_id.strip() or self.descriptor_count < 1:
            raise StableRehydratedRawSplitIntegrityError("D3A collection identity is incomplete")
        for name in (
            "plan_fingerprint",
            "d2y_seal_fingerprint",
            "d2z_manifest_file_sha256",
            "d2z_stable_material_root",
            "reacquisition_lineage_root",
            "original_d2y_partition_material_fingerprint",
            "reacquired_d2u_partition_material_fingerprint",
            "reacquired_d2u_assembly_evidence_fingerprint",
            "training_warmup_universe_hash",
            "training_universe_hash",
            "development_warmup_universe_hash",
            "development_universe_hash",
            "research_universe_identity_hash",
            "raw_training_source_hash",
            "raw_development_source_hash",
            "scientific_rehydration_fingerprint",
        ):
            _require_hash(getattr(self, name), name)
        if self.scientific_identity_policy != SCIENTIFIC_IDENTITY_POLICY:
            raise StableRehydratedRawSplitGovernanceError("D3A scientific identity policy drifted")
        if self.lineage_policy != LINEAGE_POLICY:
            raise StableRehydratedRawSplitGovernanceError("D3A reacquisition lineage policy drifted")
        if self.downstream_policy != DOWNSTREAM_POLICY:
            raise StableRehydratedRawSplitGovernanceError("D3A downstream policy drifted")
        if self.partition_reproduction_policy != PARTITION_REPRODUCTION_POLICY:
            raise StableRehydratedRawSplitGovernanceError("D3A partition reproduction policy drifted")
        if not all(
            (
                self.exact_stable_material_verified,
                self.exact_d2y_training_universe_verified,
                self.exact_d2y_development_universe_verified,
                self.reacquisition_receipts_audited,
                self.receipt_lineage_excluded_from_scientific_identity,
                self.lineage_timestamps_may_differ,
            )
        ):
            raise StableRehydratedRawSplitGovernanceError("D3A required provenance/equality assertions are incomplete")
        if self.partition_material_fingerprint_reproduction_required:
            raise StableRehydratedRawSplitGovernanceError(
                "D3A must not require timestamp-sensitive D2U partition fingerprint reproduction"
            )
        if self.network_used_by_handoff:
            raise StableRehydratedRawSplitGovernanceError("D3A handoff itself must remain offline")
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
            raise StableRehydratedRawSplitGovernanceError("D3A is raw-research handoff only")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise StableRehydratedRawSplitGovernanceError("D3A has no capital/LIVE authority")
        expected_scientific = _scientific_rehydration_fingerprint(
            scientific_identity_policy=self.scientific_identity_policy,
            d2y_seal_fingerprint=self.d2y_seal_fingerprint,
            d2z_stable_material_root=self.d2z_stable_material_root,
            collection_id=self.collection_id,
            plan_fingerprint=self.plan_fingerprint,
            training_warmup_universe_hash=self.training_warmup_universe_hash,
            training_universe_hash=self.training_universe_hash,
            development_warmup_universe_hash=self.development_warmup_universe_hash,
            development_universe_hash=self.development_universe_hash,
            research_universe_identity_hash=self.research_universe_identity_hash,
            raw_training_source_hash=self.raw_training_source_hash,
            raw_development_source_hash=self.raw_development_source_hash,
        )
        if self.scientific_rehydration_fingerprint != expected_scientific:
            raise StableRehydratedRawSplitIntegrityError("D3A scientific rehydration fingerprint drifted")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class StableRehydratedRawSplitMaterial:
    material_version: str
    partition_material: HistoricalResearchPartitionMaterial
    training_source: RawTrainingMarketSource
    development_source: RawDevelopmentMarketSource
    evidence: StableRehydratedRawSplitEvidence

    def __post_init__(self) -> None:
        if self.material_version != OSS3D3A_MATERIAL_VERSION:
            raise StableRehydratedRawSplitIntegrityError("noncanonical D3A material version")
        if not isinstance(self.partition_material, HistoricalResearchPartitionMaterial):
            raise TypeError("partition_material must be HistoricalResearchPartitionMaterial")
        if not isinstance(self.training_source, RawTrainingMarketSource):
            raise TypeError("training_source must be RawTrainingMarketSource")
        if not isinstance(self.development_source, RawDevelopmentMarketSource):
            raise TypeError("development_source must be RawDevelopmentMarketSource")
        if self.partition_material.fingerprint != self.evidence.reacquired_d2u_partition_material_fingerprint:
            raise StableRehydratedRawSplitIntegrityError("D3A partition material differs from evidence")
        if self.training_source.source_hash != self.evidence.raw_training_source_hash:
            raise StableRehydratedRawSplitIntegrityError("D3A TRAIN raw source differs from evidence")
        if self.development_source.source_hash != self.evidence.raw_development_source_hash:
            raise StableRehydratedRawSplitIntegrityError("D3A DEVELOPMENT raw source differs from evidence")
        if self.training_source.universe_identity_hash != self.evidence.research_universe_identity_hash:
            raise StableRehydratedRawSplitIntegrityError("D3A TRAIN research-universe identity differs")
        if self.development_source.universe_identity_hash != self.evidence.research_universe_identity_hash:
            raise StableRehydratedRawSplitIntegrityError("D3A DEVELOPMENT research-universe identity differs")

    @property
    def scientific_fingerprint(self) -> str:
        return self.evidence.scientific_rehydration_fingerprint

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "material_version": self.material_version,
                "partition_material_fingerprint": self.partition_material.fingerprint,
                "training_source_hash": self.training_source.source_hash,
                "development_source_hash": self.development_source.source_hash,
                "evidence_fingerprint": self.evidence.fingerprint,
            }
        )


def build_canonical_stable_rehydrated_raw_split(
    *,
    materials: Iterable[RehydratedDescriptorMaterial],
) -> StableRehydratedRawSplitMaterial:
    """Build the canonical real-data raw split after exact D2Y/D2Z rebind."""
    d2y = canonical_oss3d2y_real_campaign_evidence_seal()
    verify_oss3d2y_real_campaign_evidence_seal(d2y)
    d2z = load_canonical_oss3d2z_descriptor_material_manifest()
    verify_durable_descriptor_material_manifest(d2z)
    plan = canonical_oss3d2u_collection_plan()
    if d2z.d2y_seal_fingerprint != d2y.fingerprint:
        raise StableRehydratedRawSplitIntegrityError("D3A D2Z manifest no longer binds canonical D2Y")
    if d2z.collection_id != plan.collection_id or d2z.plan_fingerprint != plan.fingerprint:
        raise StableRehydratedRawSplitIntegrityError("D3A D2Z manifest no longer binds canonical D2U plan")
    return _build_stable_rehydrated_raw_split(
        plan=plan,
        entries=d2z.entries,
        materials=materials,
        d2y_seal_fingerprint=d2y.fingerprint,
        d2z_manifest_file_sha256=MATERIAL_MANIFEST_FILE_SHA256,
        d2z_stable_material_root=d2z.stable_material_root,
        original_d2y_partition_material_fingerprint=d2y.d2u_partition_material_fingerprint,
        expected_training_universe_hash=d2y.training_universe_hash,
        expected_development_universe_hash=d2y.development_universe_hash,
    )


def _build_stable_rehydrated_raw_split(
    *,
    plan: HistoricalCollectionPlan,
    entries: Iterable[StableDescriptorMaterialIdentity],
    materials: Iterable[RehydratedDescriptorMaterial],
    d2y_seal_fingerprint: str,
    d2z_manifest_file_sha256: str,
    d2z_stable_material_root: str,
    original_d2y_partition_material_fingerprint: str,
    expected_training_universe_hash: str,
    expected_development_universe_hash: str,
) -> StableRehydratedRawSplitMaterial:
    """Internal deterministic assembler used by the canonical wrapper and synthetic CI."""
    if not isinstance(plan, HistoricalCollectionPlan):
        raise TypeError("plan must be HistoricalCollectionPlan")
    for value, name in (
        (d2y_seal_fingerprint, "d2y_seal_fingerprint"),
        (d2z_manifest_file_sha256, "d2z_manifest_file_sha256"),
        (d2z_stable_material_root, "d2z_stable_material_root"),
        (original_d2y_partition_material_fingerprint, "original_d2y_partition_material_fingerprint"),
        (expected_training_universe_hash, "expected_training_universe_hash"),
        (expected_development_universe_hash, "expected_development_universe_hash"),
    ):
        _require_hash(value, name)

    entry_tuple = tuple(entries)
    material_tuple = tuple(materials)
    if len(entry_tuple) != len(plan.descriptors) or len(material_tuple) != len(plan.descriptors):
        raise StableRehydratedRawSplitIntegrityError("D3A requires one D2Z identity and one material per descriptor")
    entry_by_descriptor = {entry.descriptor_fingerprint: entry for entry in entry_tuple}
    material_by_descriptor = {item.descriptor.fingerprint: item for item in material_tuple}
    planned = tuple(descriptor.fingerprint for descriptor in plan.descriptors)
    if len(entry_by_descriptor) != len(entry_tuple) or tuple(entry.descriptor_fingerprint for entry in entry_tuple) != planned:
        raise StableRehydratedRawSplitIntegrityError("D3A D2Z identities must match exact canonical plan order")
    if len(material_by_descriptor) != len(material_tuple) or set(material_by_descriptor) != set(planned):
        raise StableRehydratedRawSplitIntegrityError("D3A rehydrated material family differs from plan")
    computed_stable_root = _hash([entry.material_identity_hash for entry in entry_tuple])
    if computed_stable_root != d2z_stable_material_root:
        raise StableRehydratedRawSplitIntegrityError("D3A D2Z stable material root drifted")

    snapshots = []
    receipt_hashes: dict[str, str] = {}
    lineage_vector: list[list[str]] = []
    for descriptor in plan.descriptors:
        fingerprint = descriptor.fingerprint
        item = material_by_descriptor[fingerprint]
        if item.descriptor != descriptor:
            raise StableRehydratedRawSplitIntegrityError("D3A descriptor semantics differ from plan")
        entry = entry_by_descriptor[fingerprint]
        snapshot = verify_rehydrated_descriptor_material(
            entry,
            descriptor=descriptor,
            archive_bytes=item.archive_bytes,
            checksum_bytes=item.checksum_bytes,
        )
        snapshots.append(snapshot)
        receipt_hashes[fingerprint] = item.acquisition_receipt_hash
        lineage_vector.append([fingerprint, item.acquisition_receipt_hash])

    partition_material = assemble_historical_collection(
        plan=plan,
        artifacts=tuple(snapshots),
        acquisition_receipt_hashes=receipt_hashes,
    )
    if partition_material.training.universe_hash != expected_training_universe_hash:
        raise StableRehydratedRawSplitIntegrityError("D3A TRAIN universe differs from frozen D2Y real-data universe")
    if partition_material.development.universe_hash != expected_development_universe_hash:
        raise StableRehydratedRawSplitIntegrityError("D3A DEVELOPMENT universe differs from frozen D2Y real-data universe")

    training_source = RawTrainingMarketSource.build(
        warmup_universe=partition_material.training_warmup,
        training_universe=partition_material.training,
    )
    development_source = RawDevelopmentMarketSource.build(
        warmup_universe=partition_material.development_warmup,
        development_universe=partition_material.development,
    )
    training_identity = research_universe_identity_hash(partition_material.training)
    development_identity = research_universe_identity_hash(partition_material.development)
    if training_identity != development_identity:
        raise StableRehydratedRawSplitIntegrityError("D3A TRAIN/DEVELOPMENT research-universe identities differ")

    scientific_fingerprint = _scientific_rehydration_fingerprint(
        scientific_identity_policy=SCIENTIFIC_IDENTITY_POLICY,
        d2y_seal_fingerprint=d2y_seal_fingerprint,
        d2z_stable_material_root=d2z_stable_material_root,
        collection_id=plan.collection_id,
        plan_fingerprint=plan.fingerprint,
        training_warmup_universe_hash=partition_material.training_warmup.universe_hash,
        training_universe_hash=partition_material.training.universe_hash,
        development_warmup_universe_hash=partition_material.development_warmup.universe_hash,
        development_universe_hash=partition_material.development.universe_hash,
        research_universe_identity_hash=training_identity,
        raw_training_source_hash=training_source.source_hash,
        raw_development_source_hash=development_source.source_hash,
    )
    evidence = StableRehydratedRawSplitEvidence(
        evidence_version=OSS3D3A_EVIDENCE_VERSION,
        collection_id=plan.collection_id,
        plan_fingerprint=plan.fingerprint,
        d2y_seal_fingerprint=d2y_seal_fingerprint,
        d2z_manifest_file_sha256=d2z_manifest_file_sha256,
        d2z_stable_material_root=d2z_stable_material_root,
        descriptor_count=len(plan.descriptors),
        reacquisition_lineage_root=_hash(lineage_vector),
        original_d2y_partition_material_fingerprint=original_d2y_partition_material_fingerprint,
        reacquired_d2u_partition_material_fingerprint=partition_material.fingerprint,
        reacquired_d2u_assembly_evidence_fingerprint=partition_material.evidence.fingerprint,
        training_warmup_universe_hash=partition_material.training_warmup.universe_hash,
        training_universe_hash=partition_material.training.universe_hash,
        development_warmup_universe_hash=partition_material.development_warmup.universe_hash,
        development_universe_hash=partition_material.development.universe_hash,
        research_universe_identity_hash=training_identity,
        raw_training_source_hash=training_source.source_hash,
        raw_development_source_hash=development_source.source_hash,
        scientific_rehydration_fingerprint=scientific_fingerprint,
        scientific_identity_policy=SCIENTIFIC_IDENTITY_POLICY,
        lineage_policy=LINEAGE_POLICY,
        downstream_policy=DOWNSTREAM_POLICY,
        partition_reproduction_policy=PARTITION_REPRODUCTION_POLICY,
        exact_stable_material_verified=True,
        exact_d2y_training_universe_verified=True,
        exact_d2y_development_universe_verified=True,
        reacquisition_receipts_audited=True,
        receipt_lineage_excluded_from_scientific_identity=True,
        partition_material_fingerprint_reproduction_required=False,
        lineage_timestamps_may_differ=True,
        network_used_by_handoff=False,
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
    return StableRehydratedRawSplitMaterial(
        material_version=OSS3D3A_MATERIAL_VERSION,
        partition_material=partition_material,
        training_source=training_source,
        development_source=development_source,
        evidence=evidence,
    )


def _scientific_rehydration_fingerprint(
    *,
    scientific_identity_policy: str,
    d2y_seal_fingerprint: str,
    d2z_stable_material_root: str,
    collection_id: str,
    plan_fingerprint: str,
    training_warmup_universe_hash: str,
    training_universe_hash: str,
    development_warmup_universe_hash: str,
    development_universe_hash: str,
    research_universe_identity_hash: str,
    raw_training_source_hash: str,
    raw_development_source_hash: str,
) -> str:
    return _hash(
        {
            "scientific_identity_policy": scientific_identity_policy,
            "d2y_seal_fingerprint": d2y_seal_fingerprint,
            "d2z_stable_material_root": d2z_stable_material_root,
            "collection_id": collection_id,
            "plan_fingerprint": plan_fingerprint,
            "training_warmup_universe_hash": training_warmup_universe_hash,
            "training_universe_hash": training_universe_hash,
            "development_warmup_universe_hash": development_warmup_universe_hash,
            "development_universe_hash": development_universe_hash,
            "research_universe_identity_hash": research_universe_identity_hash,
            "raw_training_source_hash": raw_training_source_hash,
            "raw_development_source_hash": raw_development_source_hash,
        }
    )
