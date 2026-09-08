"""OSS-3D2W offline handoff from a complete D2V seal into D2R/D2S raw sources.

D2W is intentionally a provenance bridge, not a feature/model runner.  It
requires a pre-existing complete D2V campaign seal, re-verifies the entire D2V
collection with network disabled, and constructs the already-certified D2R
TRAIN raw source and D2S DEVELOPMENT raw source from the exact D2U partition
material bound by that same seal.

No TRAIN/DEVELOPMENT labels, predictions, metrics, Qlib runtime, broker, OMS,
Safety, PAPER, capital or LIVE authority is introduced here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re

from autotrade.research.oss3_market_collection import (
    HistoricalCollectionPlan,
    HistoricalResearchPartitionMaterial,
    canonical_oss3d2u_collection_plan,
)

from labs.oss3_market_data.real_acquisition_campaign import (
    LEDGER_FILENAME,
    CompleteCollectionSeal,
    SQLiteRealAcquisitionCampaignLedger,
    run_restart_safe_campaign,
    validate_external_evidence_root,
)
from labs.oss3_qlib.raw_development_provenance import RawDevelopmentMarketSource
from labs.oss3_qlib.raw_training_bundle_provenance import (
    RawTrainingMarketSource,
    research_universe_identity_hash,
)


OSS3D2W_EVIDENCE_VERSION = "OSS3D2W_SEALED_RAW_SPLIT_HANDOFF_EVIDENCE_V1"
OSS3D2W_MATERIAL_VERSION = "OSS3D2W_SEALED_RAW_SPLIT_HANDOFF_MATERIAL_V1"
SPLIT_POLICY = "ONE_D2V_SEAL_TO_EXACT_D2R_TRAIN_AND_D2S_DEVELOPMENT_RAW_SOURCES_V1"
REVERIFICATION_POLICY = "PREEXISTING_D2V_COMPLETE_SEAL_THEN_FULL_OFFLINE_REVERIFICATION_V1"
DOWNSTREAM_POLICY = "D2R_TRAIN_AND_D2S_PRELABEL_DEVELOPMENT_ONLY_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")


class SealedRawSplitHandoffError(RuntimeError):
    pass


class SealedRawSplitHandoffIntegrityError(SealedRawSplitHandoffError):
    pass


class SealedRawSplitHandoffGovernanceError(SealedRawSplitHandoffError):
    pass


@dataclass(frozen=True, slots=True)
class SealedRawSplitHandoffEvidence:
    evidence_version: str
    source_campaign_id: str
    collection_id: str
    d2u_plan_fingerprint: str
    d2v_campaign_seal_fingerprint: str
    d2u_partition_material_fingerprint: str
    d2u_assembly_evidence_fingerprint: str
    research_split_hash: str
    research_universe_identity_hash: str
    raw_training_source_hash: str
    raw_development_source_hash: str
    training_warmup_universe_hash: str
    training_universe_hash: str
    development_warmup_universe_hash: str
    development_universe_hash: str
    training_warmup_bars: int
    training_bars: int
    development_warmup_bars: int
    development_bars: int
    split_policy: str
    reverification_policy: str
    downstream_policy: str
    preexisting_complete_d2v_seal_required: bool
    d2v_offline_reverification_complete: bool
    network_used_during_handoff: bool
    train_label_artifact_materialized: bool
    development_label_artifact_materialized: bool
    prediction_values_loaded: bool
    development_metrics_computed: bool
    final_holdout_values_loaded: bool
    qlib_runtime_used: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D2W_EVIDENCE_VERSION:
            raise SealedRawSplitHandoffIntegrityError("noncanonical D2W evidence version")
        _require_id(self.source_campaign_id, "source_campaign_id")
        _require_id(self.collection_id, "collection_id")
        for name in (
            "d2u_plan_fingerprint",
            "d2v_campaign_seal_fingerprint",
            "d2u_partition_material_fingerprint",
            "d2u_assembly_evidence_fingerprint",
            "research_split_hash",
            "research_universe_identity_hash",
            "raw_training_source_hash",
            "raw_development_source_hash",
            "training_warmup_universe_hash",
            "training_universe_hash",
            "development_warmup_universe_hash",
            "development_universe_hash",
        ):
            _require_hash(getattr(self, name), name)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (
                self.training_warmup_bars,
                self.training_bars,
                self.development_warmup_bars,
                self.development_bars,
            )
        ):
            raise SealedRawSplitHandoffIntegrityError("D2W partition bar counts must be positive integers")
        if self.split_policy != SPLIT_POLICY:
            raise SealedRawSplitHandoffGovernanceError("D2W split policy drifted")
        if self.reverification_policy != REVERIFICATION_POLICY:
            raise SealedRawSplitHandoffGovernanceError("D2W reverification policy drifted")
        if self.downstream_policy != DOWNSTREAM_POLICY:
            raise SealedRawSplitHandoffGovernanceError("D2W downstream policy drifted")
        if not self.preexisting_complete_d2v_seal_required or not self.d2v_offline_reverification_complete:
            raise SealedRawSplitHandoffGovernanceError("D2W requires a pre-existing fully reverified D2V campaign seal")
        if self.network_used_during_handoff:
            raise SealedRawSplitHandoffGovernanceError("D2W handoff must be offline")
        if (
            self.train_label_artifact_materialized
            or self.development_label_artifact_materialized
            or self.prediction_values_loaded
            or self.development_metrics_computed
            or self.final_holdout_values_loaded
            or self.qlib_runtime_used
            or self.promotion_authorized
        ):
            raise SealedRawSplitHandoffGovernanceError("D2W may hand off raw partition sources only")
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
            "evidence_version": self.evidence_version,
            "source_campaign_id": self.source_campaign_id,
            "collection_id": self.collection_id,
            "d2u_plan_fingerprint": self.d2u_plan_fingerprint,
            "d2v_campaign_seal_fingerprint": self.d2v_campaign_seal_fingerprint,
            "d2u_partition_material_fingerprint": self.d2u_partition_material_fingerprint,
            "d2u_assembly_evidence_fingerprint": self.d2u_assembly_evidence_fingerprint,
            "research_split_hash": self.research_split_hash,
            "research_universe_identity_hash": self.research_universe_identity_hash,
            "raw_training_source_hash": self.raw_training_source_hash,
            "raw_development_source_hash": self.raw_development_source_hash,
            "training_warmup_universe_hash": self.training_warmup_universe_hash,
            "training_universe_hash": self.training_universe_hash,
            "development_warmup_universe_hash": self.development_warmup_universe_hash,
            "development_universe_hash": self.development_universe_hash,
            "training_warmup_bars": self.training_warmup_bars,
            "training_bars": self.training_bars,
            "development_warmup_bars": self.development_warmup_bars,
            "development_bars": self.development_bars,
            "split_policy": self.split_policy,
            "reverification_policy": self.reverification_policy,
            "downstream_policy": self.downstream_policy,
            "preexisting_complete_d2v_seal_required": self.preexisting_complete_d2v_seal_required,
            "d2v_offline_reverification_complete": self.d2v_offline_reverification_complete,
            "network_used_during_handoff": self.network_used_during_handoff,
            "train_label_artifact_materialized": self.train_label_artifact_materialized,
            "development_label_artifact_materialized": self.development_label_artifact_materialized,
            "prediction_values_loaded": self.prediction_values_loaded,
            "development_metrics_computed": self.development_metrics_computed,
            "final_holdout_values_loaded": self.final_holdout_values_loaded,
            "qlib_runtime_used": self.qlib_runtime_used,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class SealedRawSplitHandoffMaterial:
    material_version: str
    training_source: RawTrainingMarketSource
    development_source: RawDevelopmentMarketSource
    evidence: SealedRawSplitHandoffEvidence

    def __post_init__(self) -> None:
        if self.material_version != OSS3D2W_MATERIAL_VERSION:
            raise SealedRawSplitHandoffIntegrityError("noncanonical D2W material version")
        if not isinstance(self.training_source, RawTrainingMarketSource):
            raise TypeError("training_source must be RawTrainingMarketSource")
        if not isinstance(self.development_source, RawDevelopmentMarketSource):
            raise TypeError("development_source must be RawDevelopmentMarketSource")
        if self.training_source.source_hash != self.evidence.raw_training_source_hash:
            raise SealedRawSplitHandoffIntegrityError("D2W TRAIN source hash differs from evidence")
        if self.development_source.source_hash != self.evidence.raw_development_source_hash:
            raise SealedRawSplitHandoffIntegrityError("D2W DEVELOPMENT source hash differs from evidence")
        if self.training_source.universe_identity_hash != self.evidence.research_universe_identity_hash:
            raise SealedRawSplitHandoffIntegrityError("D2W TRAIN research-universe identity differs from evidence")
        if self.development_source.universe_identity_hash != self.evidence.research_universe_identity_hash:
            raise SealedRawSplitHandoffIntegrityError("D2W DEVELOPMENT research-universe identity differs from evidence")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "material_version": self.material_version,
                "training_source_hash": self.training_source.source_hash,
                "development_source_hash": self.development_source.source_hash,
                "evidence_fingerprint": self.evidence.fingerprint,
            }
        )


def build_sealed_raw_split_handoff(
    *,
    evidence_root: str | Path,
    plan: HistoricalCollectionPlan,
    now: datetime,
    repository_root: str | Path | None = None,
) -> SealedRawSplitHandoffMaterial:
    """Require an existing D2V seal, reverify offline, then build D2R/D2S sources."""
    if not isinstance(plan, HistoricalCollectionPlan):
        raise TypeError("plan must be HistoricalCollectionPlan")
    root = validate_external_evidence_root(evidence_root, repository_root=repository_root)
    ledger = SQLiteRealAcquisitionCampaignLedger(root / LEDGER_FILENAME)
    preexisting_seal = ledger.get_campaign_seal(plan.collection_id)
    if preexisting_seal is None:
        raise SealedRawSplitHandoffGovernanceError("D2W requires a pre-existing complete D2V campaign seal")
    _verify_seal_plan_binding(preexisting_seal, plan)

    result, partition_material = run_restart_safe_campaign(
        evidence_root=root,
        plan=plan,
        now=now,
        allow_network=False,
        repository_root=repository_root,
    )
    if not result.complete or partition_material is None:
        raise SealedRawSplitHandoffIntegrityError("D2W D2V offline reverification did not reproduce a complete collection")
    if result.acquired_from_network != 0:
        raise SealedRawSplitHandoffGovernanceError("D2W may not acquire network material")
    if result.campaign_seal_fingerprint != preexisting_seal.fingerprint:
        raise SealedRawSplitHandoffIntegrityError("D2W offline D2V seal differs from pre-existing campaign seal")
    _verify_partition_material_against_seal(partition_material, preexisting_seal)

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
        raise SealedRawSplitHandoffIntegrityError("D2W TRAIN/DEVELOPMENT research-universe identities differ")
    if training_source.universe_identity_hash != development_source.universe_identity_hash:
        raise SealedRawSplitHandoffIntegrityError("D2W downstream raw-source universe identities differ")

    research_split_hash = _research_split_hash(
        plan=plan,
        seal=preexisting_seal,
        material=partition_material,
        research_universe_identity_hash_value=training_identity,
    )
    source_campaign_id = f"{plan.collection_id}:raw-split-v1"
    _require_id(source_campaign_id, "source_campaign_id")
    evidence = SealedRawSplitHandoffEvidence(
        evidence_version=OSS3D2W_EVIDENCE_VERSION,
        source_campaign_id=source_campaign_id,
        collection_id=plan.collection_id,
        d2u_plan_fingerprint=plan.fingerprint,
        d2v_campaign_seal_fingerprint=preexisting_seal.fingerprint,
        d2u_partition_material_fingerprint=partition_material.fingerprint,
        d2u_assembly_evidence_fingerprint=partition_material.evidence.fingerprint,
        research_split_hash=research_split_hash,
        research_universe_identity_hash=training_identity,
        raw_training_source_hash=training_source.source_hash,
        raw_development_source_hash=development_source.source_hash,
        training_warmup_universe_hash=partition_material.training_warmup.universe_hash,
        training_universe_hash=partition_material.training.universe_hash,
        development_warmup_universe_hash=partition_material.development_warmup.universe_hash,
        development_universe_hash=partition_material.development.universe_hash,
        training_warmup_bars=partition_material.training_warmup.bar_count,
        training_bars=partition_material.training.bar_count,
        development_warmup_bars=partition_material.development_warmup.bar_count,
        development_bars=partition_material.development.bar_count,
        split_policy=SPLIT_POLICY,
        reverification_policy=REVERIFICATION_POLICY,
        downstream_policy=DOWNSTREAM_POLICY,
        preexisting_complete_d2v_seal_required=True,
        d2v_offline_reverification_complete=True,
        network_used_during_handoff=False,
        train_label_artifact_materialized=False,
        development_label_artifact_materialized=False,
        prediction_values_loaded=False,
        development_metrics_computed=False,
        final_holdout_values_loaded=False,
        qlib_runtime_used=False,
        promotion_authorized=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )
    return SealedRawSplitHandoffMaterial(
        material_version=OSS3D2W_MATERIAL_VERSION,
        training_source=training_source,
        development_source=development_source,
        evidence=evidence,
    )


def build_canonical_sealed_raw_split_handoff(
    *,
    evidence_root: str | Path,
    now: datetime,
    repository_root: str | Path | None = None,
) -> SealedRawSplitHandoffMaterial:
    return build_sealed_raw_split_handoff(
        evidence_root=evidence_root,
        plan=canonical_oss3d2u_collection_plan(),
        now=now,
        repository_root=repository_root,
    )


def _verify_seal_plan_binding(seal: CompleteCollectionSeal, plan: HistoricalCollectionPlan) -> None:
    if seal.collection_id != plan.collection_id:
        raise SealedRawSplitHandoffIntegrityError("D2W D2V seal collection id differs from D2U plan")
    if seal.plan_fingerprint != plan.fingerprint:
        raise SealedRawSplitHandoffIntegrityError("D2W D2V seal plan fingerprint differs from D2U plan")
    if seal.descriptor_count != len(plan.descriptors):
        raise SealedRawSplitHandoffIntegrityError("D2W D2V seal descriptor count differs from D2U plan")
    if seal.final_holdout_values_loaded:
        raise SealedRawSplitHandoffGovernanceError("D2W cannot consume a D2V seal with FINAL_HOLDOUT values")


def _verify_partition_material_against_seal(
    material: HistoricalResearchPartitionMaterial,
    seal: CompleteCollectionSeal,
) -> None:
    expected = (
        (material.fingerprint, seal.d2u_partition_material_fingerprint, "partition material"),
        (material.evidence.fingerprint, seal.d2u_assembly_evidence_fingerprint, "assembly evidence"),
        (material.training_warmup.universe_hash, seal.training_warmup_universe_hash, "TRAIN warmup"),
        (material.training.universe_hash, seal.training_universe_hash, "TRAIN"),
        (material.development_warmup.universe_hash, seal.development_warmup_universe_hash, "DEVELOPMENT warmup"),
        (material.development.universe_hash, seal.development_universe_hash, "DEVELOPMENT"),
    )
    for actual, sealed, label in expected:
        if actual != sealed:
            raise SealedRawSplitHandoffIntegrityError(f"D2W {label} hash differs from D2V complete seal")
    if material.evidence.final_holdout_values_loaded:
        raise SealedRawSplitHandoffGovernanceError("D2W partition material contains FINAL_HOLDOUT values")


def _research_split_hash(
    *,
    plan: HistoricalCollectionPlan,
    seal: CompleteCollectionSeal,
    material: HistoricalResearchPartitionMaterial,
    research_universe_identity_hash_value: str,
) -> str:
    return _hash(
        {
            "split_policy": SPLIT_POLICY,
            "collection_id": plan.collection_id,
            "d2u_plan_fingerprint": plan.fingerprint,
            "d2v_campaign_seal_fingerprint": seal.fingerprint,
            "d2u_partition_material_fingerprint": material.fingerprint,
            "research_universe_identity_hash": research_universe_identity_hash_value,
            "training_warmup_universe_hash": material.training_warmup.universe_hash,
            "training_universe_hash": material.training.universe_hash,
            "development_warmup_universe_hash": material.development_warmup.universe_hash,
            "development_universe_hash": material.development.universe_hash,
            "train_start": plan.train_start,
            "development_start": plan.development_start,
            "development_end": plan.development_end,
            "warmup_bars": plan.warmup_bars,
        }
    )


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")


def _require_id(value: object, name: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _deny_authority(
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise SealedRawSplitHandoffGovernanceError("D2W execution authority is forbidden")
    if capital_authority != "NONE":
        raise SealedRawSplitHandoffGovernanceError("D2W capital authority must be NONE")
    if live_trading != "BLOCKED":
        raise SealedRawSplitHandoffGovernanceError("D2W LIVE trading must remain BLOCKED")
