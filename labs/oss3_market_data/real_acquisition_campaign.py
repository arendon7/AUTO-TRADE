"""OSS-3D2V restart-safe execution of the frozen D2U public-data campaign.

D2V does not choose data, partitions, features, models, metrics or execution
policy.  It executes the already-certified D2U canonical collection plan and
turns successful public archive acquisitions into durable local evidence.

Restart semantics are deliberately fail-closed:

* a descriptor seal is append-only;
* a sealed descriptor is reused only after ZIP/CHECKSUM/receipt/D2T snapshot
  are fully re-read and reverified;
* an unsealed final directory may be reconciled only after the same full
  reverification (covers a crash after atomic directory rename but before the
  SQLite seal insert);
* a stale staging directory is never trusted or silently deleted;
* the campaign seal can exist only after all planned descriptors verify and
  D2U can assemble the exact WARMUP/TRAIN/DEVELOPMENT collection.

The real evidence root must resolve outside the git repository.  D2V has no
broker, OMS, Safety writer, OrderIntent, PAPER, capital or LIVE authority and
never requests FINAL_HOLDOUT values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Callable, Mapping

from autotrade.research.external_data import ReadOnlyHttpTransport
from autotrade.research.oss3_market_collection import (
    HistoricalCollectionPlan,
    HistoricalResearchPartitionMaterial,
    SQLiteHistoricalCollectionPlanRegistry,
    assemble_historical_collection,
    canonical_oss3d2u_collection_plan,
)
from autotrade.research.oss3_market_snapshot import (
    BinanceSpotArchiveDescriptor,
    HistoricalMarketSnapshotArtifact,
)

from labs.oss3_market_data.archive_acquisition import (
    OSS3D2U_ACQUIRED_MATERIAL_VERSION,
    ArchiveAcquisitionReceipt,
    AcquiredArchiveMaterial,
    acquire_preregistered_archive,
    build_real_archive_transport,
)


OSS3D2V_DESCRIPTOR_SEAL_VERSION = "OSS3D2V_DESCRIPTOR_EVIDENCE_SEAL_V1"
OSS3D2V_CAMPAIGN_SEAL_VERSION = "OSS3D2V_COMPLETE_COLLECTION_SEAL_V1"
OSS3D2V_RESULT_VERSION = "OSS3D2V_CAMPAIGN_RUN_RESULT_V1"
RESTART_POLICY = "REUSE_ONLY_AFTER_FULL_RAW_RECEIPT_SNAPSHOT_REVERIFICATION_V1"
STAGING_POLICY = "ATOMIC_DIRECTORY_RENAME_NO_STALE_STAGE_REUSE_V1"
STORAGE_POLICY = "REAL_EVIDENCE_ROOT_MUST_RESOLVE_OUTSIDE_GIT_REPOSITORY_V1"
CAMPAIGN_POLICY = "EXECUTE_EXACT_CERTIFIED_D2U_PLAN_WITHOUT_FAMILY_OR_PARTITION_DRIFT_V1"
NETWORK_POLICY = "PUBLIC_GET_ONLY_THROUGH_D2U_TRIPLE_CHECKSUM_ADAPTER_V1"
LEDGER_FILENAME = "oss3d2v-campaign.sqlite3"
D2U_PLAN_LEDGER_FILENAME = "oss3d2u-plan.sqlite3"
STAGING_DIRNAME = ".oss3d2v-staging"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class RealAcquisitionCampaignError(RuntimeError):
    pass


class RealAcquisitionCampaignIntegrityError(RealAcquisitionCampaignError):
    pass


class RealAcquisitionCampaignGovernanceError(RealAcquisitionCampaignError):
    pass


class RealAcquisitionCampaignIncomplete(RealAcquisitionCampaignError):
    pass


@dataclass(frozen=True, slots=True)
class DescriptorEvidenceSeal:
    seal_version: str
    collection_id: str
    plan_fingerprint: str
    descriptor_fingerprint: str
    symbol: str
    period: str
    archive_sha256: str
    checksum_payload_sha256: str
    snapshot_artifact_hash: str
    normalized_dataset_hash: str
    acquisition_receipt_hash: str
    acquired_material_fingerprint: str
    evidence_relative_directory: str
    sealed_at: str
    restart_policy: str
    staging_policy: str
    network_used_to_create_material: bool
    full_reverification_required_on_reuse: bool
    final_holdout_values_requested: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.seal_version != OSS3D2V_DESCRIPTOR_SEAL_VERSION:
            raise RealAcquisitionCampaignIntegrityError("noncanonical D2V descriptor seal version")
        if not self.collection_id.strip() or not self.symbol.strip() or not self.period.strip():
            raise RealAcquisitionCampaignIntegrityError("D2V descriptor seal identity is incomplete")
        for name in (
            "plan_fingerprint",
            "descriptor_fingerprint",
            "archive_sha256",
            "checksum_payload_sha256",
            "snapshot_artifact_hash",
            "normalized_dataset_hash",
            "acquisition_receipt_hash",
            "acquired_material_fingerprint",
        ):
            _require_hash(getattr(self, name), name)
        if self.evidence_relative_directory != f"{self.symbol}/{self.period}":
            raise RealAcquisitionCampaignIntegrityError("D2V evidence directory is not canonical")
        _parse_utc(self.sealed_at)
        if self.restart_policy != RESTART_POLICY or self.staging_policy != STAGING_POLICY:
            raise RealAcquisitionCampaignGovernanceError("D2V restart/staging policy drifted")
        if not self.network_used_to_create_material:
            raise RealAcquisitionCampaignIntegrityError("D2V descriptor material must originate from D2U public GET acquisition")
        if not self.full_reverification_required_on_reuse:
            raise RealAcquisitionCampaignGovernanceError("D2V reuse must require full reverification")
        if self.final_holdout_values_requested:
            raise RealAcquisitionCampaignGovernanceError("D2V cannot request FINAL_HOLDOUT values")
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "seal_version": self.seal_version,
            "collection_id": self.collection_id,
            "plan_fingerprint": self.plan_fingerprint,
            "descriptor_fingerprint": self.descriptor_fingerprint,
            "symbol": self.symbol,
            "period": self.period,
            "archive_sha256": self.archive_sha256,
            "checksum_payload_sha256": self.checksum_payload_sha256,
            "snapshot_artifact_hash": self.snapshot_artifact_hash,
            "normalized_dataset_hash": self.normalized_dataset_hash,
            "acquisition_receipt_hash": self.acquisition_receipt_hash,
            "acquired_material_fingerprint": self.acquired_material_fingerprint,
            "evidence_relative_directory": self.evidence_relative_directory,
            "sealed_at": self.sealed_at,
            "restart_policy": self.restart_policy,
            "staging_policy": self.staging_policy,
            "network_used_to_create_material": self.network_used_to_create_material,
            "full_reverification_required_on_reuse": self.full_reverification_required_on_reuse,
            "final_holdout_values_requested": self.final_holdout_values_requested,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class CompleteCollectionSeal:
    seal_version: str
    collection_id: str
    plan_fingerprint: str
    descriptor_count: int
    descriptor_seal_fingerprints: tuple[str, ...]
    d2u_assembly_evidence_fingerprint: str
    d2u_partition_material_fingerprint: str
    training_warmup_universe_hash: str
    training_universe_hash: str
    development_warmup_universe_hash: str
    development_universe_hash: str
    sealed_at: str
    campaign_policy: str
    restart_policy: str
    storage_policy: str
    exact_complete_descriptor_family: bool
    all_descriptor_material_reverified: bool
    final_holdout_values_loaded: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.seal_version != OSS3D2V_CAMPAIGN_SEAL_VERSION:
            raise RealAcquisitionCampaignIntegrityError("noncanonical D2V campaign seal version")
        if not self.collection_id.strip() or self.descriptor_count < 1:
            raise RealAcquisitionCampaignIntegrityError("invalid D2V campaign seal identity")
        if len(self.descriptor_seal_fingerprints) != self.descriptor_count:
            raise RealAcquisitionCampaignIntegrityError("D2V campaign seal count mismatch")
        if len(set(self.descriptor_seal_fingerprints)) != self.descriptor_count:
            raise RealAcquisitionCampaignIntegrityError("D2V campaign seal contains duplicate descriptor seals")
        for value in (
            self.plan_fingerprint,
            *self.descriptor_seal_fingerprints,
            self.d2u_assembly_evidence_fingerprint,
            self.d2u_partition_material_fingerprint,
            self.training_warmup_universe_hash,
            self.training_universe_hash,
            self.development_warmup_universe_hash,
            self.development_universe_hash,
        ):
            _require_hash(value, "D2V campaign seal hash")
        _parse_utc(self.sealed_at)
        if self.campaign_policy != CAMPAIGN_POLICY or self.restart_policy != RESTART_POLICY:
            raise RealAcquisitionCampaignGovernanceError("D2V campaign/restart policy drifted")
        if self.storage_policy != STORAGE_POLICY:
            raise RealAcquisitionCampaignGovernanceError("D2V storage policy drifted")
        if not self.exact_complete_descriptor_family or not self.all_descriptor_material_reverified:
            raise RealAcquisitionCampaignGovernanceError("D2V complete campaign requires exact fully reverified family")
        if self.final_holdout_values_loaded:
            raise RealAcquisitionCampaignGovernanceError("D2V campaign cannot load FINAL_HOLDOUT values")
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "seal_version": self.seal_version,
            "collection_id": self.collection_id,
            "plan_fingerprint": self.plan_fingerprint,
            "descriptor_count": self.descriptor_count,
            "descriptor_seal_fingerprints": list(self.descriptor_seal_fingerprints),
            "d2u_assembly_evidence_fingerprint": self.d2u_assembly_evidence_fingerprint,
            "d2u_partition_material_fingerprint": self.d2u_partition_material_fingerprint,
            "training_warmup_universe_hash": self.training_warmup_universe_hash,
            "training_universe_hash": self.training_universe_hash,
            "development_warmup_universe_hash": self.development_warmup_universe_hash,
            "development_universe_hash": self.development_universe_hash,
            "sealed_at": self.sealed_at,
            "campaign_policy": self.campaign_policy,
            "restart_policy": self.restart_policy,
            "storage_policy": self.storage_policy,
            "exact_complete_descriptor_family": self.exact_complete_descriptor_family,
            "all_descriptor_material_reverified": self.all_descriptor_material_reverified,
            "final_holdout_values_loaded": self.final_holdout_values_loaded,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class CampaignRunResult:
    result_version: str
    collection_id: str
    plan_fingerprint: str
    descriptor_count: int
    acquired_from_network: int
    reused_after_seal_reverification: int
    reconciled_unsealed_final_material: int
    missing_without_network_authority: int
    complete: bool
    campaign_seal_fingerprint: str | None
    network_policy: str
    final_holdout_values_loaded: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.result_version != OSS3D2V_RESULT_VERSION:
            raise RealAcquisitionCampaignIntegrityError("noncanonical D2V run result version")
        _require_hash(self.plan_fingerprint, "plan_fingerprint")
        if self.descriptor_count < 1:
            raise RealAcquisitionCampaignIntegrityError("D2V result descriptor count must be positive")
        counters = (
            self.acquired_from_network,
            self.reused_after_seal_reverification,
            self.reconciled_unsealed_final_material,
            self.missing_without_network_authority,
        )
        if any(value < 0 for value in counters):
            raise RealAcquisitionCampaignIntegrityError("D2V result counters cannot be negative")
        if sum(counters) != self.descriptor_count:
            raise RealAcquisitionCampaignIntegrityError("D2V result counters must cover exact descriptor family")
        if self.complete != (self.missing_without_network_authority == 0):
            raise RealAcquisitionCampaignIntegrityError("D2V result completion state is inconsistent")
        if self.complete:
            if self.campaign_seal_fingerprint is None:
                raise RealAcquisitionCampaignIntegrityError("complete D2V result requires campaign seal")
            _require_hash(self.campaign_seal_fingerprint, "campaign_seal_fingerprint")
        elif self.campaign_seal_fingerprint is not None:
            raise RealAcquisitionCampaignIntegrityError("incomplete D2V result cannot expose campaign seal")
        if self.network_policy != NETWORK_POLICY:
            raise RealAcquisitionCampaignGovernanceError("D2V network policy drifted")
        if self.final_holdout_values_loaded:
            raise RealAcquisitionCampaignGovernanceError("D2V result cannot expose FINAL_HOLDOUT values")
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
        )


class SQLiteRealAcquisitionCampaignLedger:
    """Append-only descriptor and complete-campaign seals."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS oss3d2v_descriptor_seals (
                    descriptor_fingerprint TEXT PRIMARY KEY,
                    collection_id TEXT NOT NULL,
                    plan_fingerprint TEXT NOT NULL,
                    seal_fingerprint TEXT NOT NULL UNIQUE,
                    seal_json TEXT NOT NULL,
                    sealed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oss3d2v_campaign_seals (
                    collection_id TEXT PRIMARY KEY,
                    plan_fingerprint TEXT NOT NULL,
                    seal_fingerprint TEXT NOT NULL UNIQUE,
                    seal_json TEXT NOT NULL,
                    sealed_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS oss3d2v_descriptor_no_update
                BEFORE UPDATE ON oss3d2v_descriptor_seals
                BEGIN SELECT RAISE(ABORT, 'OSS3D2V_APPEND_ONLY'); END;
                CREATE TRIGGER IF NOT EXISTS oss3d2v_descriptor_no_delete
                BEFORE DELETE ON oss3d2v_descriptor_seals
                BEGIN SELECT RAISE(ABORT, 'OSS3D2V_APPEND_ONLY'); END;
                CREATE TRIGGER IF NOT EXISTS oss3d2v_campaign_no_update
                BEFORE UPDATE ON oss3d2v_campaign_seals
                BEGIN SELECT RAISE(ABORT, 'OSS3D2V_APPEND_ONLY'); END;
                CREATE TRIGGER IF NOT EXISTS oss3d2v_campaign_no_delete
                BEFORE DELETE ON oss3d2v_campaign_seals
                BEGIN SELECT RAISE(ABORT, 'OSS3D2V_APPEND_ONLY'); END;
                """
            )

    def put_descriptor_seal(self, seal: DescriptorEvidenceSeal) -> None:
        existing = self.get_descriptor_seal(seal.descriptor_fingerprint)
        if existing is not None:
            if existing.fingerprint != seal.fingerprint:
                raise RealAcquisitionCampaignGovernanceError("D2V descriptor already sealed with different evidence")
            return
        payload = _canonical_json(seal.to_dict())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO oss3d2v_descriptor_seals
                    (descriptor_fingerprint, collection_id, plan_fingerprint, seal_fingerprint, seal_json, sealed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    seal.descriptor_fingerprint,
                    seal.collection_id,
                    seal.plan_fingerprint,
                    seal.fingerprint,
                    payload,
                    seal.sealed_at,
                ),
            )

    def get_descriptor_seal(self, descriptor_fingerprint: str) -> DescriptorEvidenceSeal | None:
        _require_hash(descriptor_fingerprint, "descriptor_fingerprint")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT seal_json, seal_fingerprint FROM oss3d2v_descriptor_seals WHERE descriptor_fingerprint = ?",
                (descriptor_fingerprint,),
            ).fetchone()
        if row is None:
            return None
        document = _decode_json_object(row["seal_json"], "D2V descriptor seal")
        seal = DescriptorEvidenceSeal(**document)
        if seal.fingerprint != row["seal_fingerprint"]:
            raise RealAcquisitionCampaignIntegrityError("D2V descriptor ledger fingerprint mismatch")
        return seal

    def put_campaign_seal(self, seal: CompleteCollectionSeal) -> None:
        existing = self.get_campaign_seal(seal.collection_id)
        if existing is not None:
            if existing.fingerprint != seal.fingerprint:
                raise RealAcquisitionCampaignGovernanceError("D2V campaign already sealed with different evidence")
            return
        payload = _canonical_json(seal.to_dict())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO oss3d2v_campaign_seals
                    (collection_id, plan_fingerprint, seal_fingerprint, seal_json, sealed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    seal.collection_id,
                    seal.plan_fingerprint,
                    seal.fingerprint,
                    payload,
                    seal.sealed_at,
                ),
            )

    def get_campaign_seal(self, collection_id: str) -> CompleteCollectionSeal | None:
        if not isinstance(collection_id, str) or not collection_id.strip():
            raise ValueError("collection_id is required")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT seal_json, seal_fingerprint FROM oss3d2v_campaign_seals WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        if row is None:
            return None
        document = _decode_json_object(row["seal_json"], "D2V campaign seal")
        raw_fingerprints = document.get("descriptor_seal_fingerprints")
        if not isinstance(raw_fingerprints, list):
            raise RealAcquisitionCampaignIntegrityError("D2V campaign seal descriptor list is invalid")
        document["descriptor_seal_fingerprints"] = tuple(str(value) for value in raw_fingerprints)
        seal = CompleteCollectionSeal(**document)
        if seal.fingerprint != row["seal_fingerprint"]:
            raise RealAcquisitionCampaignIntegrityError("D2V campaign ledger fingerprint mismatch")
        return seal

    def descriptor_seal_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM oss3d2v_descriptor_seals").fetchone()
        assert row is not None
        return int(row["count"])


def run_restart_safe_campaign(
    *,
    evidence_root: str | Path,
    plan: HistoricalCollectionPlan,
    now: datetime,
    allow_network: bool,
    transport: ReadOnlyHttpTransport | None = None,
    repository_root: str | Path | None = None,
) -> tuple[CampaignRunResult, HistoricalResearchPartitionMaterial | None]:
    """Execute or verify one D2V campaign without changing the frozen D2U plan."""
    _require_aware(now, "now")
    root = validate_external_evidence_root(evidence_root, repository_root=repository_root)
    root.mkdir(parents=True, exist_ok=True)

    d2u_registry = SQLiteHistoricalCollectionPlanRegistry(root / D2U_PLAN_LEDGER_FILENAME)
    d2u_registry.preregister(plan, now=now)
    d2u_registry.require_exact(plan)
    ledger = SQLiteRealAcquisitionCampaignLedger(root / LEDGER_FILENAME)

    if allow_network and transport is None:
        transport = build_real_archive_transport(plan=plan)
    if not allow_network and transport is not None:
        raise RealAcquisitionCampaignGovernanceError("D2V no-network verification cannot receive a transport")

    acquired = 0
    reused = 0
    reconciled = 0
    missing = 0
    materials: list[AcquiredArchiveMaterial] = []
    seal_fingerprints: list[str] = []

    for descriptor in plan.descriptors:
        existing_seal = ledger.get_descriptor_seal(descriptor.fingerprint)
        final_directory = _final_directory(root, descriptor)

        if existing_seal is not None:
            material = load_and_reverify_material(root=root, plan=plan, descriptor=descriptor)
            _verify_descriptor_seal(existing_seal, plan=plan, descriptor=descriptor, material=material)
            materials.append(material)
            seal_fingerprints.append(existing_seal.fingerprint)
            reused += 1
            continue

        if final_directory.exists():
            material = load_and_reverify_material(root=root, plan=plan, descriptor=descriptor)
            seal = _descriptor_seal(plan=plan, descriptor=descriptor, material=material, now=now)
            ledger.put_descriptor_seal(seal)
            materials.append(material)
            seal_fingerprints.append(seal.fingerprint)
            reconciled += 1
            continue

        if not allow_network:
            missing += 1
            continue

        assert transport is not None
        material = acquire_preregistered_archive(
            registry=d2u_registry,
            plan=plan,
            descriptor=descriptor,
            transport=transport,
            now=now,
        )
        persist_material_with_atomic_directory_commit(root=root, material=material)
        reloaded = load_and_reverify_material(root=root, plan=plan, descriptor=descriptor)
        if reloaded.fingerprint != material.fingerprint:
            raise RealAcquisitionCampaignIntegrityError("D2V persisted material differs from acquired material")
        seal = _descriptor_seal(plan=plan, descriptor=descriptor, material=reloaded, now=now)
        ledger.put_descriptor_seal(seal)
        materials.append(reloaded)
        seal_fingerprints.append(seal.fingerprint)
        acquired += 1

    if missing:
        if materials and len(materials) + missing != len(plan.descriptors):
            raise RealAcquisitionCampaignIntegrityError("D2V partial verification accounting mismatch")
        return (
            CampaignRunResult(
                result_version=OSS3D2V_RESULT_VERSION,
                collection_id=plan.collection_id,
                plan_fingerprint=plan.fingerprint,
                descriptor_count=len(plan.descriptors),
                acquired_from_network=acquired,
                reused_after_seal_reverification=reused,
                reconciled_unsealed_final_material=reconciled,
                missing_without_network_authority=missing,
                complete=False,
                campaign_seal_fingerprint=None,
                network_policy=NETWORK_POLICY,
                final_holdout_values_loaded=False,
                execution_authorized=False,
                paper_execution_authorized=False,
                capital_authority="NONE",
                live_trading="BLOCKED",
            ),
            None,
        )

    if len(materials) != len(plan.descriptors) or len(seal_fingerprints) != len(plan.descriptors):
        raise RealAcquisitionCampaignIntegrityError("D2V cannot seal incomplete descriptor family")
    if ledger.descriptor_seal_count() != len(plan.descriptors):
        raise RealAcquisitionCampaignIntegrityError("D2V ledger descriptor seal count differs from plan")

    by_descriptor = {material.descriptor.fingerprint: material for material in materials}
    ordered_materials = tuple(by_descriptor[descriptor.fingerprint] for descriptor in plan.descriptors)
    receipt_hashes = {
        descriptor.fingerprint: by_descriptor[descriptor.fingerprint].receipt.fingerprint
        for descriptor in plan.descriptors
    }
    partition_material = assemble_historical_collection(
        plan=plan,
        artifacts=tuple(item.snapshot for item in ordered_materials),
        acquisition_receipt_hashes=receipt_hashes,
    )

    seal = CompleteCollectionSeal(
        seal_version=OSS3D2V_CAMPAIGN_SEAL_VERSION,
        collection_id=plan.collection_id,
        plan_fingerprint=plan.fingerprint,
        descriptor_count=len(plan.descriptors),
        descriptor_seal_fingerprints=tuple(seal_fingerprints),
        d2u_assembly_evidence_fingerprint=partition_material.evidence.fingerprint,
        d2u_partition_material_fingerprint=partition_material.fingerprint,
        training_warmup_universe_hash=partition_material.training_warmup.universe_hash,
        training_universe_hash=partition_material.training.universe_hash,
        development_warmup_universe_hash=partition_material.development_warmup.universe_hash,
        development_universe_hash=partition_material.development.universe_hash,
        sealed_at=now.astimezone(timezone.utc).isoformat(),
        campaign_policy=CAMPAIGN_POLICY,
        restart_policy=RESTART_POLICY,
        storage_policy=STORAGE_POLICY,
        exact_complete_descriptor_family=True,
        all_descriptor_material_reverified=True,
        final_holdout_values_loaded=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )
    existing_campaign = ledger.get_campaign_seal(plan.collection_id)
    if existing_campaign is not None:
        _verify_campaign_seal(existing_campaign, expected=seal)
        seal = existing_campaign
    else:
        ledger.put_campaign_seal(seal)

    return (
        CampaignRunResult(
            result_version=OSS3D2V_RESULT_VERSION,
            collection_id=plan.collection_id,
            plan_fingerprint=plan.fingerprint,
            descriptor_count=len(plan.descriptors),
            acquired_from_network=acquired,
            reused_after_seal_reverification=reused,
            reconciled_unsealed_final_material=reconciled,
            missing_without_network_authority=0,
            complete=True,
            campaign_seal_fingerprint=seal.fingerprint,
            network_policy=NETWORK_POLICY,
            final_holdout_values_loaded=False,
            execution_authorized=False,
            paper_execution_authorized=False,
            capital_authority="NONE",
            live_trading="BLOCKED",
        ),
        partition_material,
    )


def run_canonical_campaign(
    *,
    evidence_root: str | Path,
    now: datetime,
    allow_network: bool,
    transport: ReadOnlyHttpTransport | None = None,
    repository_root: str | Path | None = None,
) -> tuple[CampaignRunResult, HistoricalResearchPartitionMaterial | None]:
    return run_restart_safe_campaign(
        evidence_root=evidence_root,
        plan=canonical_oss3d2u_collection_plan(),
        now=now,
        allow_network=allow_network,
        transport=transport,
        repository_root=repository_root,
    )


def validate_external_evidence_root(
    evidence_root: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> Path:
    root = Path(evidence_root).expanduser()
    if root.exists() and root.is_symlink():
        raise RealAcquisitionCampaignGovernanceError("D2V evidence root may not be a symlink")
    resolved = root.resolve()
    repo = (
        Path(repository_root).expanduser().resolve()
        if repository_root is not None
        else Path(__file__).resolve().parents[2]
    )
    if resolved == repo or repo in resolved.parents:
        raise RealAcquisitionCampaignGovernanceError("D2V real evidence root must be outside git repository")
    return resolved


def persist_material_with_atomic_directory_commit(
    *,
    root: Path,
    material: AcquiredArchiveMaterial,
) -> None:
    descriptor = material.descriptor
    final_directory = _final_directory(root, descriptor)
    if final_directory.exists():
        raise RealAcquisitionCampaignGovernanceError("D2V refuses to overwrite existing final descriptor evidence")
    staging_base = root / STAGING_DIRNAME / descriptor.fingerprint
    if staging_base.exists():
        raise RealAcquisitionCampaignGovernanceError("D2V stale staging evidence requires explicit operator review")
    staging_base.mkdir(parents=True, exist_ok=False)
    try:
        material.write(staging_base)
        staged_directory = _final_directory(staging_base, descriptor)
        if not staged_directory.is_dir():
            raise RealAcquisitionCampaignIntegrityError("D2V staged material directory is missing")
        staged_material = _load_material_from_directory(
            directory=staged_directory,
            plan_fingerprint=material.receipt.plan_fingerprint,
            descriptor=descriptor,
        )
        if staged_material.fingerprint != material.fingerprint:
            raise RealAcquisitionCampaignIntegrityError("D2V staged material failed full reverification")
        final_directory.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_directory, final_directory)
    except Exception:
        # Cleanup is safe only within the staging namespace created by this
        # invocation.  Final evidence is never removed or overwritten here.
        if staging_base.exists():
            shutil.rmtree(staging_base)
        raise
    finally:
        _remove_empty_parents(staging_base, stop=root / STAGING_DIRNAME)


def load_and_reverify_material(
    *,
    root: Path,
    plan: HistoricalCollectionPlan,
    descriptor: BinanceSpotArchiveDescriptor,
) -> AcquiredArchiveMaterial:
    if descriptor.fingerprint not in set(plan.descriptor_fingerprints):
        raise RealAcquisitionCampaignGovernanceError("D2V cannot load evidence outside frozen D2U plan")
    directory = _final_directory(root, descriptor)
    if not directory.is_dir():
        raise RealAcquisitionCampaignIntegrityError("D2V final descriptor evidence directory is missing")
    material = _load_material_from_directory(
        directory=directory,
        plan_fingerprint=plan.fingerprint,
        descriptor=descriptor,
    )
    if material.receipt.collection_id != plan.collection_id:
        raise RealAcquisitionCampaignIntegrityError("D2V receipt collection id differs from plan")
    return material


def _load_material_from_directory(
    *,
    directory: Path,
    plan_fingerprint: str,
    descriptor: BinanceSpotArchiveDescriptor,
) -> AcquiredArchiveMaterial:
    expected_names = {
        descriptor.archive_filename,
        descriptor.checksum_filename,
        "d2t-snapshot.json",
        "d2u-acquisition-receipt.json",
    }
    actual_names = {path.name for path in directory.iterdir() if path.is_file()}
    if actual_names != expected_names:
        raise RealAcquisitionCampaignIntegrityError("D2V descriptor evidence directory file set is not exact")
    if any(path.is_dir() for path in directory.iterdir()):
        raise RealAcquisitionCampaignIntegrityError("D2V descriptor evidence directory cannot contain nested directories")

    archive_bytes = (directory / descriptor.archive_filename).read_bytes()
    try:
        checksum_text = (directory / descriptor.checksum_filename).read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RealAcquisitionCampaignIntegrityError("D2V persisted checksum is not UTF-8") from exc
    snapshot = HistoricalMarketSnapshotArtifact.read(directory / "d2t-snapshot.json")
    receipt = _read_receipt(directory / "d2u-acquisition-receipt.json")
    if receipt.plan_fingerprint != plan_fingerprint:
        raise RealAcquisitionCampaignIntegrityError("D2V receipt plan fingerprint differs from frozen plan")
    if receipt.descriptor_fingerprint != descriptor.fingerprint:
        raise RealAcquisitionCampaignIntegrityError("D2V receipt descriptor differs from expected descriptor")
    return AcquiredArchiveMaterial(
        material_version=OSS3D2U_ACQUIRED_MATERIAL_VERSION,
        descriptor=descriptor,
        archive_bytes=archive_bytes,
        checksum_text=checksum_text,
        snapshot=snapshot,
        receipt=receipt,
    )


def _read_receipt(path: Path) -> ArchiveAcquisitionReceipt:
    raw = path.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RealAcquisitionCampaignIntegrityError("D2V acquisition receipt is invalid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise RealAcquisitionCampaignIntegrityError("D2V acquisition receipt top level must be object")
    if raw != (_canonical_json(document) + "\n").encode("utf-8"):
        raise RealAcquisitionCampaignIntegrityError("D2V acquisition receipt serialization is not canonical")
    expected = set(ArchiveAcquisitionReceipt.__dataclass_fields__)
    if set(document) != expected:
        raise RealAcquisitionCampaignIntegrityError("D2V acquisition receipt schema mismatch")
    try:
        return ArchiveAcquisitionReceipt(**document)
    except (TypeError, ValueError) as exc:
        raise RealAcquisitionCampaignIntegrityError("D2V acquisition receipt fields are invalid") from exc


def _descriptor_seal(
    *,
    plan: HistoricalCollectionPlan,
    descriptor: BinanceSpotArchiveDescriptor,
    material: AcquiredArchiveMaterial,
    now: datetime,
) -> DescriptorEvidenceSeal:
    if material.descriptor.fingerprint != descriptor.fingerprint:
        raise RealAcquisitionCampaignIntegrityError("D2V cannot seal different descriptor material")
    return DescriptorEvidenceSeal(
        seal_version=OSS3D2V_DESCRIPTOR_SEAL_VERSION,
        collection_id=plan.collection_id,
        plan_fingerprint=plan.fingerprint,
        descriptor_fingerprint=descriptor.fingerprint,
        symbol=descriptor.instrument.symbol,
        period=descriptor.period,
        archive_sha256=material.receipt.archive_payload_sha256,
        checksum_payload_sha256=material.receipt.checksum_a_payload_sha256,
        snapshot_artifact_hash=material.snapshot.artifact_hash,
        normalized_dataset_hash=material.snapshot.manifest.normalized_dataset_hash,
        acquisition_receipt_hash=material.receipt.fingerprint,
        acquired_material_fingerprint=material.fingerprint,
        evidence_relative_directory=f"{descriptor.instrument.symbol}/{descriptor.period}",
        sealed_at=now.astimezone(timezone.utc).isoformat(),
        restart_policy=RESTART_POLICY,
        staging_policy=STAGING_POLICY,
        network_used_to_create_material=True,
        full_reverification_required_on_reuse=True,
        final_holdout_values_requested=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )


def _verify_descriptor_seal(
    seal: DescriptorEvidenceSeal,
    *,
    plan: HistoricalCollectionPlan,
    descriptor: BinanceSpotArchiveDescriptor,
    material: AcquiredArchiveMaterial,
) -> None:
    expected = _descriptor_seal(
        plan=plan,
        descriptor=descriptor,
        material=material,
        now=_parse_utc(seal.sealed_at),
    )
    if expected.fingerprint != seal.fingerprint:
        raise RealAcquisitionCampaignIntegrityError("D2V sealed descriptor evidence no longer reproduces")


def _verify_campaign_seal(existing: CompleteCollectionSeal, *, expected: CompleteCollectionSeal) -> None:
    reconstructed = CompleteCollectionSeal(
        **{
            **expected.to_dict(),
            "descriptor_seal_fingerprints": expected.descriptor_seal_fingerprints,
            "sealed_at": existing.sealed_at,
        }
    )
    if reconstructed.fingerprint != existing.fingerprint:
        raise RealAcquisitionCampaignIntegrityError("D2V existing campaign seal no longer reproduces")


def _final_directory(root: Path, descriptor: BinanceSpotArchiveDescriptor) -> Path:
    return root / descriptor.instrument.symbol / descriptor.period


def _remove_empty_parents(path: Path, *, stop: Path) -> None:
    current = path
    while current != stop.parent:
        if current.exists():
            try:
                current.rmdir()
            except OSError:
                break
        if current == stop:
            break
        current = current.parent


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RealAcquisitionCampaignIntegrityError("D2V JSON contains duplicate object key")
        result[key] = value
    return result


def _decode_json_object(raw: str, label: str) -> dict[str, object]:
    try:
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except json.JSONDecodeError as exc:
        raise RealAcquisitionCampaignIntegrityError(f"{label} is invalid JSON") from exc
    if not isinstance(document, dict):
        raise RealAcquisitionCampaignIntegrityError(f"{label} must be JSON object")
    if _canonical_json(document) != raw:
        raise RealAcquisitionCampaignIntegrityError(f"{label} is not canonical JSON")
    return document


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase sha256")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RealAcquisitionCampaignIntegrityError("invalid D2V timestamp") from exc
    _require_aware(parsed, "timestamp")
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        raise RealAcquisitionCampaignIntegrityError("D2V timestamp must be canonical UTC")
    return normalized


def _deny_authority(
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise RealAcquisitionCampaignGovernanceError("D2V execution authority is forbidden")
    if capital_authority != "NONE":
        raise RealAcquisitionCampaignGovernanceError("D2V capital authority must be NONE")
    if live_trading != "BLOCKED":
        raise RealAcquisitionCampaignGovernanceError("D2V LIVE trading must remain BLOCKED")
