"""OSS-3D3F protected dual-holdout materialization.

D3F bridges certified raw Q1/Q2 evidence into the value-opaque commitments
required by D2J and D2M. Q1 feature/label values exist only in process memory;
Q2 remains a raw aligned-universe commitment. No Qlib, prediction, metric,
permit, broker, PAPER, capital or LIVE authority exists here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Sequence

from autotrade.research.market import MarketDataset
from autotrade.research.oss3_market_collection import canonical_oss3d2u_collection_plan
from autotrade.research.oss3_market_snapshot import HistoricalMarketSnapshotArtifact
from autotrade.research.universe import AlignedMarketUniverse
from labs.oss3_market_data.dual_holdout_acquisition import run_dual_holdout_acquisition
from labs.oss3_market_data.real_acquisition_campaign import load_and_reverify_material
from labs.oss3_qlib.dual_holdout_acquisition_preregistration import (
    ECONOMIC_PURPOSE,
    PREDICTIVE_PURPOSE,
    DualHoldoutAcquisitionPlan,
    canonical_oss3d3d_dual_holdout_plan,
)
from labs.oss3_qlib.economic_raw_market_feature_provenance import (
    CANONICAL_LOOKBACK_BARS,
    canonical_oss3d2q_formulas,
)
from labs.oss3_qlib.final_holdout_protocol import (
    OSS3D2J_COMMITMENT_VERSION,
    OSS3ProtectedFinalHoldoutCommitment,
)
from labs.oss3_qlib.predictive_economic_protocol import (
    ECONOMIC_HOLDOUT_PURPOSE,
    OSS3D2M_HOLDOUT_COMMITMENT_VERSION,
    EconomicHoldoutCommitment,
)
from labs.oss3_qlib.raw_training_bundle_provenance import (
    canonical_oss3d2r_feature_definitions,
    canonical_oss3d2r_label_definition,
    canonical_oss3d2r_label_formula,
    derive_raw_training_bundle,
    research_universe_identity_hash,
)
from labs.oss3_qlib.sealed_raw_split_handoff import build_canonical_sealed_raw_split_handoff

OSS3D3F_EVIDENCE_VERSION = "OSS3D3F_PROTECTED_DUAL_HOLDOUT_MATERIALIZATION_EVIDENCE_V1"
EXPECTED_D3E_CERTIFIED_HEAD = "129e546cc09257b11dfe6e96915d370b91126a3f"
EXPECTED_D3E_CAMPAIGN_SEAL = "8ce57f1e6a6ce6999d8599af7df38b5b06662834739aa175cadd672c21eed105"
EXPECTED_D3D_PLAN_FINGERPRINT = "d6dc987cc2053a6617796fdb89df9467c0895a090c5745c91b157edc5d7a4af2"
D2K_MATERIAL_VERSION = "OSS3D2K_PROTECTED_FINAL_HOLDOUT_MATERIAL_V1"
D2K_FEATURE_ARTIFACT_VERSION = "OSS3D2K_PROTECTED_FINAL_FEATURE_ARTIFACT_V1"
D2K_LABEL_ARTIFACT_VERSION = "OSS3D2K_PROTECTED_FINAL_LABEL_ARTIFACT_V1"
ECONOMIC_COMMITMENT_ID = "oss3d3f-q2-2026-economic-holdout-v1"
PREDICTIVE_UNIVERSE_NAME = "oss3d3f-q1-2026-predictive-final-holdout-v1"
ECONOMIC_UNIVERSE_NAME = "oss3d3f-q2-2026-economic-holdout-v1"
WARMUP_UNIVERSE_NAME = "oss3d3f-q1-warmup-december-2025-last-20h-v1"
DECEMBER_WARMUP_PERIOD = "2025-12"
SOURCE_CAMPAIGN_ID = "oss3d3a-real-development-campaign-v1"
MATERIALIZATION_POLICY = "DETERMINISTIC_IN_MEMORY_VALUE_OPAQUE_COMMITMENT_ONLY_V1"
Q1_FEATURE_POLICY = "EXACT_D2Q_20_BAR_CAUSAL_CLOSE_FEATURES_V1"
Q1_LABEL_POLICY = "EXACT_D2R_ONE_BAR_FORWARD_CLOSE_RETURN_V1"
Q2_POLICY = "RAW_ALIGNED_MARKET_COMMITMENT_ONLY_NO_PREDICTION_OR_OUTCOME_V1"
TEMPORAL_SEPARATION_POLICY = "HALF_OPEN_CONTIGUOUS_Q1_Q2_NO_OVERLAP_V1"
D2Y_WARMUP_POLICY = "LAST_20_CERTIFIED_DECEMBER_2025_1H_BARS_PER_SYMBOL_V1"
_HASH_CHARS = frozenset("0123456789abcdef")
_ZERO = Decimal("0")


class ProtectedHoldoutMaterializationError(RuntimeError):
    pass


class ProtectedHoldoutMaterializationIntegrityError(ProtectedHoldoutMaterializationError):
    pass


class ProtectedHoldoutMaterializationGovernanceError(ProtectedHoldoutMaterializationError):
    pass


@dataclass(frozen=True, slots=True)
class ProtectedFeatureRow:
    as_of: str
    available_at: str
    symbol: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        as_of = _parse_utc(self.as_of, "feature as_of")
        available = _parse_utc(self.available_at, "feature available_at")
        if available > as_of:
            raise ProtectedHoldoutMaterializationGovernanceError("D3F feature is not point-in-time available")
        if not self.symbol or not isinstance(self.values, tuple) or not self.values:
            raise ValueError("invalid protected feature row")
        for value in self.values:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                raise ValueError("D3F feature value must be finite")

    def to_dict(self) -> dict[str, object]:
        return {"as_of": self.as_of, "available_at": self.available_at, "symbol": self.symbol, "values": [float(v) for v in self.values]}


@dataclass(frozen=True, slots=True)
class ProtectedLabelRow:
    label_as_of: str
    horizon_end: str
    available_at: str
    symbol: str
    value: float

    def __post_init__(self) -> None:
        origin = _parse_utc(self.label_as_of, "label_as_of")
        horizon = _parse_utc(self.horizon_end, "horizon_end")
        available = _parse_utc(self.available_at, "label available_at")
        if not origin < horizon or available < horizon:
            raise ProtectedHoldoutMaterializationGovernanceError("invalid D3F future-label timing")
        if not self.symbol or isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not isfinite(float(self.value)):
            raise ValueError("invalid protected label row")

    def to_dict(self) -> dict[str, object]:
        return {"label_as_of": self.label_as_of, "horizon_end": self.horizon_end, "available_at": self.available_at, "symbol": self.symbol, "value": float(self.value)}


@dataclass(frozen=True, slots=True)
class ProtectedPredictiveMaterial:
    """Private in-process Q1 material; only ``commitment`` may be persisted."""

    material_version: str
    source_campaign_id: str
    research_split_hash: str
    source_universe_hash: str
    feature_schema_hash: str
    label_definition_hash: str
    feature_source_dataset_hash: str
    label_source_dataset_hash: str
    partition_start: str
    partition_end: str
    feature_names: tuple[str, ...]
    feature_rows: tuple[ProtectedFeatureRow, ...]
    label_rows: tuple[ProtectedLabelRow, ...]

    def __post_init__(self) -> None:
        if self.material_version != D2K_MATERIAL_VERSION:
            raise ProtectedHoldoutMaterializationIntegrityError("D3F predictive material version drifted from D2K")
        for name in ("research_split_hash", "source_universe_hash", "feature_schema_hash", "label_definition_hash", "feature_source_dataset_hash", "label_source_dataset_hash"):
            _require_hash(getattr(self, name), name)
        start = _parse_utc(self.partition_start, "partition_start")
        end = _parse_utc(self.partition_end, "partition_end")
        if not start < end or not self.feature_names or len(set(self.feature_names)) != len(self.feature_names):
            raise ProtectedHoldoutMaterializationIntegrityError("invalid D3F predictive schema/window")
        if not self.feature_rows or len(self.feature_rows) != len(self.label_rows):
            raise ProtectedHoldoutMaterializationIntegrityError("D3F protected feature/label supports differ")
        feature_keys = tuple((row.as_of, row.symbol) for row in self.feature_rows)
        label_keys = tuple((row.label_as_of, row.symbol) for row in self.label_rows)
        if feature_keys != tuple(sorted(feature_keys)) or feature_keys != label_keys or len(set(feature_keys)) != len(feature_keys):
            raise ProtectedHoldoutMaterializationIntegrityError("D3F protected keysets are not exact canonical matches")
        if any(len(row.values) != len(self.feature_names) for row in self.feature_rows):
            raise ProtectedHoldoutMaterializationIntegrityError("D3F feature width differs from schema")
        for timestamp, _ in feature_keys:
            observed = _parse_utc(timestamp, "protected row timestamp")
            if observed < start or observed >= end:
                raise ProtectedHoldoutMaterializationGovernanceError("D3F protected row lies outside committed partition")
        _ = self.commitment

    @property
    def feature_artifact_hash(self) -> str:
        return _hash({"artifact_version": D2K_FEATURE_ARTIFACT_VERSION, "source_campaign_id": self.source_campaign_id, "research_split_hash": self.research_split_hash, "source_universe_hash": self.source_universe_hash, "feature_schema_hash": self.feature_schema_hash, "source_dataset_hash": self.feature_source_dataset_hash, "partition_start": self.partition_start, "partition_end": self.partition_end, "feature_names": list(self.feature_names), "rows": [row.to_dict() for row in self.feature_rows]})

    @property
    def label_artifact_hash(self) -> str:
        return _hash({"artifact_version": D2K_LABEL_ARTIFACT_VERSION, "source_campaign_id": self.source_campaign_id, "research_split_hash": self.research_split_hash, "source_universe_hash": self.source_universe_hash, "label_definition_hash": self.label_definition_hash, "source_dataset_hash": self.label_source_dataset_hash, "partition_start": self.partition_start, "partition_end": self.partition_end, "rows": [row.to_dict() for row in self.label_rows]})

    @property
    def evaluation_keyset_hash(self) -> str:
        return _hash([[row.as_of, row.symbol] for row in self.feature_rows])

    @property
    def cross_section_key_hash(self) -> str:
        counts = _cross_section_counts(tuple(row.as_of for row in self.feature_rows))
        return _hash([[timestamp, count] for timestamp, count in counts])

    @property
    def commitment(self) -> OSS3ProtectedFinalHoldoutCommitment:
        counts = _cross_section_counts(tuple(row.as_of for row in self.feature_rows))
        return OSS3ProtectedFinalHoldoutCommitment(
            commitment_version=OSS3D2J_COMMITMENT_VERSION,
            source_campaign_id=self.source_campaign_id,
            research_split_hash=self.research_split_hash,
            source_universe_hash=self.source_universe_hash,
            label_definition_hash=self.label_definition_hash,
            feature_artifact_hash=self.feature_artifact_hash,
            label_artifact_hash=self.label_artifact_hash,
            evaluation_keyset_hash=self.evaluation_keyset_hash,
            cross_section_key_hash=self.cross_section_key_hash,
            partition_start=self.partition_start,
            partition_end=self.partition_end,
            row_count=len(self.feature_rows),
            cross_section_count=len(counts),
            minimum_cross_section_observation_count=min(count for _, count in counts),
            label_values_exposed=False,
            final_holdout_observed=False,
        )


@dataclass(frozen=True, slots=True)
class ProtectedDualHoldoutMaterializationEvidence:
    evidence_version: str
    d3d_plan_fingerprint: str
    d3e_certified_head: str
    d3e_campaign_seal_fingerprint: str
    d2w_evidence_fingerprint: str
    research_split_hash: str
    research_universe_identity_hash: str
    training_bundle_hash: str
    feature_schema_hash: str
    label_definition_hash: str
    predictive_commitment_fingerprint: str
    predictive_feature_artifact_hash: str
    predictive_label_artifact_hash: str
    predictive_evaluation_keyset_hash: str
    predictive_cross_section_key_hash: str
    predictive_partition_start: str
    predictive_partition_end: str
    predictive_row_count: int
    predictive_cross_section_count: int
    predictive_minimum_cross_section_observation_count: int
    economic_commitment_fingerprint: str
    economic_universe_hash: str
    economic_source_dataset_set_hash: str
    economic_partition_start: str
    economic_partition_end: str
    economic_bar_count: int
    economic_symbol_count: int
    materialization_policy: str
    q1_feature_policy: str
    q1_label_policy: str
    q2_policy: str
    temporal_separation_policy: str
    d2y_warmup_policy: str
    d3e_full_offline_reverification_complete: bool
    d2y_full_offline_reverification_complete: bool
    predictive_protected_feature_values_materialized: bool
    predictive_protected_label_values_materialized: bool
    label_values_exposed: bool
    prediction_values_materialized: bool
    predictive_metrics_computed: bool
    economic_outcomes_observed: bool
    final_holdout_observed: bool
    economic_holdout_observed: bool
    d2j_protocol_registered: bool
    d2m_protocol_registered: bool
    holdout_permit_issued: bool
    holdout_permit_consumed: bool
    profitability_claim_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D3F_EVIDENCE_VERSION:
            raise ProtectedHoldoutMaterializationIntegrityError("noncanonical D3F evidence version")
        if self.d3d_plan_fingerprint != EXPECTED_D3D_PLAN_FINGERPRINT or self.d3e_certified_head != EXPECTED_D3E_CERTIFIED_HEAD or self.d3e_campaign_seal_fingerprint != EXPECTED_D3E_CAMPAIGN_SEAL:
            raise ProtectedHoldoutMaterializationIntegrityError("D3F certified source identity drifted")
        for name in ("d2w_evidence_fingerprint", "research_split_hash", "research_universe_identity_hash", "training_bundle_hash", "feature_schema_hash", "label_definition_hash", "predictive_commitment_fingerprint", "predictive_feature_artifact_hash", "predictive_label_artifact_hash", "predictive_evaluation_keyset_hash", "predictive_cross_section_key_hash", "economic_commitment_fingerprint", "economic_universe_hash", "economic_source_dataset_set_hash"):
            _require_hash(getattr(self, name), name)
        p_start = _parse_utc(self.predictive_partition_start, "predictive_partition_start")
        p_end = _parse_utc(self.predictive_partition_end, "predictive_partition_end")
        e_start = _parse_utc(self.economic_partition_start, "economic_partition_start")
        e_end = _parse_utc(self.economic_partition_end, "economic_partition_end")
        if not p_start < p_end or not e_start < e_end or e_start < p_end:
            raise ProtectedHoldoutMaterializationGovernanceError("D3F predictive/economic windows overlap or are invalid")
        if self.predictive_row_count < 90 or self.predictive_cross_section_count < 30 or self.predictive_minimum_cross_section_observation_count < 3:
            raise ProtectedHoldoutMaterializationGovernanceError("D3F predictive commitment is below D2J sample floors")
        if self.economic_bar_count < 60 or self.economic_symbol_count < 3:
            raise ProtectedHoldoutMaterializationGovernanceError("D3F economic commitment is below D2M sample floors")
        if (self.materialization_policy != MATERIALIZATION_POLICY or self.q1_feature_policy != Q1_FEATURE_POLICY or self.q1_label_policy != Q1_LABEL_POLICY or self.q2_policy != Q2_POLICY or self.temporal_separation_policy != TEMPORAL_SEPARATION_POLICY or self.d2y_warmup_policy != D2Y_WARMUP_POLICY):
            raise ProtectedHoldoutMaterializationGovernanceError("D3F materialization policy drifted")
        if not (self.d3e_full_offline_reverification_complete and self.d2y_full_offline_reverification_complete and self.predictive_protected_feature_values_materialized and self.predictive_protected_label_values_materialized):
            raise ProtectedHoldoutMaterializationIntegrityError("D3F protected materialization proof is incomplete")
        if (self.label_values_exposed or self.prediction_values_materialized or self.predictive_metrics_computed or self.economic_outcomes_observed or self.final_holdout_observed or self.economic_holdout_observed or self.d2j_protocol_registered or self.d2m_protocol_registered or self.holdout_permit_issued or self.holdout_permit_consumed or self.profitability_claim_authorized or self.promotion_authorized or self.execution_authorized or self.paper_execution_authorized):
            raise ProtectedHoldoutMaterializationGovernanceError("D3F cannot expose/evaluate/register/authorize holdout evidence")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise ProtectedHoldoutMaterializationGovernanceError("D3F cannot grant capital or LIVE authority")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class ProtectedDualHoldoutRuntimeMaterial:
    """Ephemeral sensitive material. Never serialize this object."""
    predictive: ProtectedPredictiveMaterial
    economic_universe: AlignedMarketUniverse
    evidence: ProtectedDualHoldoutMaterializationEvidence


def materialize_protected_dual_holdouts(*, d3d_registry_path: str | Path, d3e_evidence_root: str | Path, d2y_evidence_root: str | Path, now: datetime, repository_root: str | Path | None = None) -> ProtectedDualHoldoutRuntimeMaterial:
    _require_aware(now, "now")
    plan = canonical_oss3d3d_dual_holdout_plan()
    if plan.fingerprint != EXPECTED_D3D_PLAN_FINGERPRINT:
        raise ProtectedHoldoutMaterializationIntegrityError("compiled D3D plan fingerprint drifted")
    d3e = run_dual_holdout_acquisition(d3d_registry_path=d3d_registry_path, evidence_root=d3e_evidence_root, now=now, allow_network=False)
    if not d3e.complete or d3e.acquired_from_network != 0 or d3e.reused_after_full_reverification != 18 or d3e.campaign_seal_fingerprint != EXPECTED_D3E_CAMPAIGN_SEAL:
        raise ProtectedHoldoutMaterializationIntegrityError("D3F requires exact complete D3E full offline reverification")

    d2w = build_canonical_sealed_raw_split_handoff(evidence_root=d2y_evidence_root, now=now, repository_root=repository_root)
    train_features, train_labels, training_bundle, _ = derive_raw_training_bundle(raw_source=d2w.training_source, campaign_id=SOURCE_CAMPAIGN_ID, research_split_hash=d2w.evidence.research_split_hash)
    warmup = _load_q1_warmup_from_d2y(d2y_evidence_root=d2y_evidence_root)
    predictive_universe = _load_d3e_universe(plan=plan, evidence_root=d3e_evidence_root, purpose=PREDICTIVE_PURPOSE)
    economic_universe = _load_d3e_universe(plan=plan, evidence_root=d3e_evidence_root, purpose=ECONOMIC_PURPOSE)
    _verify_raw_window_geometry(predictive_universe, plan.predictive)
    _verify_raw_window_geometry(economic_universe, plan.economic)
    _verify_real_universe_identity(warmup=warmup, predictive=predictive_universe, economic=economic_universe, expected=d2w.evidence.research_universe_identity_hash)

    predictive = build_predictive_final_holdout_material(warmup_universe=warmup, holdout_universe=predictive_universe, source_campaign_id=SOURCE_CAMPAIGN_ID, research_split_hash=d2w.evidence.research_split_hash, expected_source_universe_hash=d2w.evidence.research_universe_identity_hash, feature_schema_hash=training_bundle.manifest.feature_schema_hash, label_definition_hash=training_bundle.manifest.label_definition_hash, partition_start=plan.predictive.partition_start, partition_end=plan.predictive.partition_end)
    if tuple(item.name for item in train_features.features) != predictive.feature_names or train_labels.label.fingerprint != predictive.label_definition_hash:
        raise ProtectedHoldoutMaterializationIntegrityError("D3F Q1 schema differs from exact TRAIN schema")
    economic_commitment = build_economic_holdout_commitment(economic_universe)
    _verify_half_open_temporal_separation(predictive.commitment, economic_commitment, require_contiguous=True)
    p = predictive.commitment
    e = economic_commitment
    evidence = ProtectedDualHoldoutMaterializationEvidence(
        evidence_version=OSS3D3F_EVIDENCE_VERSION,
        d3d_plan_fingerprint=plan.fingerprint,
        d3e_certified_head=EXPECTED_D3E_CERTIFIED_HEAD,
        d3e_campaign_seal_fingerprint=d3e.campaign_seal_fingerprint,
        d2w_evidence_fingerprint=d2w.evidence.fingerprint,
        research_split_hash=d2w.evidence.research_split_hash,
        research_universe_identity_hash=d2w.evidence.research_universe_identity_hash,
        training_bundle_hash=training_bundle.artifact_hash,
        feature_schema_hash=training_bundle.manifest.feature_schema_hash,
        label_definition_hash=training_bundle.manifest.label_definition_hash,
        predictive_commitment_fingerprint=p.fingerprint,
        predictive_feature_artifact_hash=p.feature_artifact_hash,
        predictive_label_artifact_hash=p.label_artifact_hash,
        predictive_evaluation_keyset_hash=p.evaluation_keyset_hash,
        predictive_cross_section_key_hash=p.cross_section_key_hash,
        predictive_partition_start=p.partition_start,
        predictive_partition_end=p.partition_end,
        predictive_row_count=p.row_count,
        predictive_cross_section_count=p.cross_section_count,
        predictive_minimum_cross_section_observation_count=p.minimum_cross_section_observation_count,
        economic_commitment_fingerprint=e.fingerprint,
        economic_universe_hash=e.universe_hash,
        economic_source_dataset_set_hash=e.source_dataset_set_hash,
        economic_partition_start=e.partition_start,
        economic_partition_end=e.partition_end,
        economic_bar_count=e.bar_count,
        economic_symbol_count=e.symbol_count,
        materialization_policy=MATERIALIZATION_POLICY,
        q1_feature_policy=Q1_FEATURE_POLICY,
        q1_label_policy=Q1_LABEL_POLICY,
        q2_policy=Q2_POLICY,
        temporal_separation_policy=TEMPORAL_SEPARATION_POLICY,
        d2y_warmup_policy=D2Y_WARMUP_POLICY,
        d3e_full_offline_reverification_complete=True,
        d2y_full_offline_reverification_complete=True,
        predictive_protected_feature_values_materialized=True,
        predictive_protected_label_values_materialized=True,
        label_values_exposed=False,
        prediction_values_materialized=False,
        predictive_metrics_computed=False,
        economic_outcomes_observed=False,
        final_holdout_observed=False,
        economic_holdout_observed=False,
        d2j_protocol_registered=False,
        d2m_protocol_registered=False,
        holdout_permit_issued=False,
        holdout_permit_consumed=False,
        profitability_claim_authorized=False,
        promotion_authorized=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )
    return ProtectedDualHoldoutRuntimeMaterial(predictive=predictive, economic_universe=economic_universe, evidence=evidence)


def build_predictive_final_holdout_material(*, warmup_universe: AlignedMarketUniverse, holdout_universe: AlignedMarketUniverse, source_campaign_id: str, research_split_hash: str, expected_source_universe_hash: str, feature_schema_hash: str, label_definition_hash: str, partition_start: str, partition_end: str) -> ProtectedPredictiveMaterial:
    if warmup_universe.symbols != holdout_universe.symbols:
        raise ProtectedHoldoutMaterializationIntegrityError("D3F warmup/Q1 symbols differ")
    if warmup_universe.bar_count != CANONICAL_LOOKBACK_BARS:
        raise ProtectedHoldoutMaterializationGovernanceError("D3F Q1 requires exactly twenty warmup bars")
    if research_universe_identity_hash(warmup_universe) != expected_source_universe_hash or research_universe_identity_hash(holdout_universe) != expected_source_universe_hash:
        raise ProtectedHoldoutMaterializationIntegrityError("D3F Q1 research universe differs from frozen DEVELOPMENT identity")
    for symbol in holdout_universe.symbols:
        warmup = warmup_universe.dataset(symbol)
        holdout = holdout_universe.dataset(symbol)
        if warmup.instrument != holdout.instrument or warmup.bars[-1].ended_at != holdout.bars[0].started_at:
            raise ProtectedHoldoutMaterializationGovernanceError("D3F warmup/Q1 instrument or temporal continuity drifted")
    if holdout_universe.bar_count < 31:
        raise ProtectedHoldoutMaterializationGovernanceError("D3F Q1 cannot satisfy D2J cross-section floor")
    definitions = canonical_oss3d2r_feature_definitions()
    if feature_schema_hash != _hash([item.to_dict() for item in definitions]):
        raise ProtectedHoldoutMaterializationIntegrityError("D3F feature schema differs from exact D2Q/D2R schema")
    label_definition = canonical_oss3d2r_label_definition()
    if label_definition_hash != label_definition.fingerprint or canonical_oss3d2r_label_formula().horizon_bars != 1:
        raise ProtectedHoldoutMaterializationIntegrityError("D3F label definition differs from exact one-bar D2R label")
    p_start = _parse_utc(partition_start, "partition_start")
    p_end = _parse_utc(partition_end, "partition_end")
    raw_start = holdout_universe.datasets[0].bars[0].started_at.astimezone(timezone.utc)
    raw_end = holdout_universe.datasets[0].bars[-1].ended_at.astimezone(timezone.utc)
    if raw_start != p_start or raw_end != p_end:
        raise ProtectedHoldoutMaterializationIntegrityError("D3F Q1 raw geometry differs from preregistered partition")

    feature_rows: list[ProtectedFeatureRow] = []
    label_rows: list[ProtectedLabelRow] = []
    formulas = canonical_oss3d2q_formulas()
    for signal_index in range(holdout_universe.bar_count - 1):
        as_of = holdout_universe.datasets[0].bars[signal_index].ended_at.astimezone(timezone.utc)
        for symbol in holdout_universe.symbols:
            warmup_bars = warmup_universe.dataset(symbol).bars
            current_prefix = holdout_universe.dataset(symbol).bars[: signal_index + 1]
            history = (warmup_bars + current_prefix)[-(CANONICAL_LOOKBACK_BARS + 1):]
            if len(history) != CANONICAL_LOOKBACK_BARS + 1 or history[-1].ended_at.astimezone(timezone.utc) != as_of or any(bar.ended_at.astimezone(timezone.utc) > as_of for bar in history):
                raise ProtectedHoldoutMaterializationGovernanceError("future or insufficient raw material entered D3F feature row")
            feature_rows.append(ProtectedFeatureRow(as_of=as_of.isoformat(), available_at=as_of.isoformat(), symbol=symbol, values=tuple(_compute_feature(formula.name, history) for formula in formulas)))
            dataset = holdout_universe.dataset(symbol)
            origin = dataset.bars[signal_index]
            horizon = dataset.bars[signal_index + 1]
            with localcontext() as context:
                context.prec = 50
                value = horizon.close / origin.close - Decimal("1")
            label_rows.append(ProtectedLabelRow(label_as_of=origin.ended_at.astimezone(timezone.utc).isoformat(), horizon_end=horizon.ended_at.astimezone(timezone.utc).isoformat(), available_at=horizon.ended_at.astimezone(timezone.utc).isoformat(), symbol=symbol, value=float(value)))

    return ProtectedPredictiveMaterial(
        material_version=D2K_MATERIAL_VERSION,
        source_campaign_id=source_campaign_id,
        research_split_hash=research_split_hash,
        source_universe_hash=expected_source_universe_hash,
        feature_schema_hash=feature_schema_hash,
        label_definition_hash=label_definition_hash,
        feature_source_dataset_hash=_hash({"warmup_dataset_set_hash": _dataset_set_hash(warmup_universe), "holdout_dataset_set_hash": _dataset_set_hash(holdout_universe), "policy": Q1_FEATURE_POLICY}),
        label_source_dataset_hash=_hash({"holdout_dataset_set_hash": _dataset_set_hash(holdout_universe), "policy": Q1_LABEL_POLICY}),
        partition_start=partition_start,
        partition_end=partition_end,
        feature_names=tuple(item.name for item in definitions),
        feature_rows=tuple(feature_rows),
        label_rows=tuple(label_rows),
    )


def build_economic_holdout_commitment(universe: AlignedMarketUniverse) -> EconomicHoldoutCommitment:
    if not isinstance(universe, AlignedMarketUniverse):
        raise TypeError("universe must be AlignedMarketUniverse")
    start = universe.datasets[0].bars[0].started_at.astimezone(timezone.utc)
    end = universe.datasets[0].bars[-1].ended_at.astimezone(timezone.utc)
    return EconomicHoldoutCommitment(
        commitment_version=OSS3D2M_HOLDOUT_COMMITMENT_VERSION,
        commitment_id=ECONOMIC_COMMITMENT_ID,
        purpose=ECONOMIC_HOLDOUT_PURPOSE,
        universe_hash=universe.universe_hash,
        universe_name=universe.universe_name,
        source_dataset_set_hash=_dataset_set_hash(universe),
        symbols=universe.symbols,
        quote_currency=universe.quote_currency,
        timeframe_seconds=universe.timeframe_seconds,
        partition_start=start.isoformat(),
        partition_end=end.isoformat(),
        bar_count=universe.bar_count,
        symbol_count=len(universe.symbols),
        market_values_exposed=False,
        economic_outcomes_observed=False,
    )


def write_public_d3f_evidence(evidence: ProtectedDualHoldoutMaterializationEvidence, path: str | Path) -> None:
    if not isinstance(evidence, ProtectedDualHoldoutMaterializationEvidence):
        raise TypeError("evidence must be ProtectedDualHoldoutMaterializationEvidence")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = evidence.to_dict()
    payload["fingerprint"] = evidence.fingerprint
    raw = _canonical_json(payload) + "\n"
    forbidden = ('"feature_rows"', '"label_rows"', '"prediction_rows"', '"open"', '"high"', '"low"', '"close"', '"volume"')
    if any(token in raw for token in forbidden):
        raise ProtectedHoldoutMaterializationGovernanceError("D3F public evidence attempted to serialize protected market values")
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(raw, encoding="utf-8")
    temporary.replace(target)


def _load_q1_warmup_from_d2y(*, d2y_evidence_root: str | Path) -> AlignedMarketUniverse:
    plan = canonical_oss3d2u_collection_plan()
    root = Path(d2y_evidence_root).expanduser().resolve()
    datasets: list[MarketDataset] = []
    for symbol in plan.symbols:
        descriptor = plan.descriptor(symbol=symbol, period=DECEMBER_WARMUP_PERIOD)
        material = load_and_reverify_material(root=root, plan=plan, descriptor=descriptor)
        dataset = material.snapshot.dataset
        if len(dataset.bars) < CANONICAL_LOOKBACK_BARS:
            raise ProtectedHoldoutMaterializationGovernanceError("D3F certified December source lacks warmup bars")
        datasets.append(MarketDataset(instrument=dataset.instrument, bars=dataset.bars[-CANONICAL_LOOKBACK_BARS:], source=f"{dataset.source}#d3f-last-{CANONICAL_LOOKBACK_BARS}"))
    return AlignedMarketUniverse.from_datasets(datasets=tuple(datasets), universe_name=WARMUP_UNIVERSE_NAME)


def _load_d3e_universe(*, plan: DualHoldoutAcquisitionPlan, evidence_root: str | Path, purpose: str) -> AlignedMarketUniverse:
    if purpose == PREDICTIVE_PURPOSE:
        window, universe_name = plan.predictive, PREDICTIVE_UNIVERSE_NAME
    elif purpose == ECONOMIC_PURPOSE:
        window, universe_name = plan.economic, ECONOMIC_UNIVERSE_NAME
    else:
        raise ValueError("invalid D3F holdout purpose")
    root = Path(evidence_root).expanduser().resolve()
    datasets: list[MarketDataset] = []
    for symbol in plan.symbols:
        descriptors = tuple(descriptor for descriptor in window.descriptors if descriptor.instrument.symbol == symbol)
        if len(descriptors) != len(window.months):
            raise ProtectedHoldoutMaterializationIntegrityError("D3F D3E descriptor support is incomplete")
        snapshots: list[HistoricalMarketSnapshotArtifact] = []
        for descriptor in descriptors:
            snapshot = HistoricalMarketSnapshotArtifact.read(root / purpose / symbol / descriptor.period / "d2t-snapshot.json")
            if snapshot.manifest.descriptor_fingerprint != descriptor.fingerprint or snapshot.instrument != descriptor.instrument:
                raise ProtectedHoldoutMaterializationIntegrityError("D3F D3E snapshot differs from preregistered descriptor")
            snapshots.append(snapshot)
        bars = tuple(bar for snapshot in snapshots for bar in snapshot.dataset.bars)
        source_hash = _hash({"purpose": purpose, "symbol": symbol, "snapshot_artifact_hashes": [snapshot.artifact_hash for snapshot in snapshots]})
        datasets.append(MarketDataset(instrument=snapshots[0].instrument, bars=bars, source=f"OSS3D3F:D3E:{purpose}:{source_hash}"))
    return AlignedMarketUniverse.from_datasets(datasets=tuple(datasets), universe_name=universe_name)


def _verify_real_universe_identity(*, warmup: AlignedMarketUniverse, predictive: AlignedMarketUniverse, economic: AlignedMarketUniverse, expected: str) -> None:
    _require_hash(expected, "expected research universe identity")
    for name, universe in (("warmup", warmup), ("predictive", predictive), ("economic", economic)):
        if research_universe_identity_hash(universe) != expected:
            raise ProtectedHoldoutMaterializationIntegrityError(f"D3F {name} research universe identity drifted")


def _verify_raw_window_geometry(universe: AlignedMarketUniverse, window: object) -> None:
    start = universe.datasets[0].bars[0].started_at.astimezone(timezone.utc)
    end = universe.datasets[0].bars[-1].ended_at.astimezone(timezone.utc)
    expected_start = _parse_utc(getattr(window, "partition_start"), "window start")
    expected_end = _parse_utc(getattr(window, "partition_end"), "window end")
    if start != expected_start or end != expected_end or universe.bar_count != getattr(window, "expected_bars_per_symbol"):
        raise ProtectedHoldoutMaterializationIntegrityError("D3F aligned raw universe differs from D3D preregistered geometry")


def _verify_half_open_temporal_separation(predictive: OSS3ProtectedFinalHoldoutCommitment, economic: EconomicHoldoutCommitment, *, require_contiguous: bool) -> None:
    predictive_end = _parse_utc(predictive.partition_end, "predictive end")
    economic_start = _parse_utc(economic.partition_start, "economic start")
    if economic_start < predictive_end:
        raise ProtectedHoldoutMaterializationGovernanceError("D3F Q1/Q2 half-open holdouts overlap")
    if require_contiguous and economic_start != predictive_end:
        raise ProtectedHoldoutMaterializationGovernanceError("D3F canonical Q1/Q2 reservations must be contiguous")


def _compute_feature(name: str, history: Sequence[object]) -> float:
    closes = tuple(getattr(bar, "close") for bar in history)
    with localcontext() as context:
        context.prec = 50
        if name == "momentum_20":
            value = closes[-1] / closes[0] - Decimal("1")
        elif name == "volatility_20":
            returns = tuple(closes[index] / closes[index - 1] - Decimal("1") for index in range(1, len(closes)))
            mean = sum(returns, _ZERO) / Decimal(len(returns))
            variance = sum(((item - mean) ** 2 for item in returns), _ZERO) / Decimal(len(returns))
            value = variance.sqrt()
        else:
            raise ProtectedHoldoutMaterializationGovernanceError("unsupported D3F feature formula")
    return float(value)


def _dataset_set_hash(universe: AlignedMarketUniverse) -> str:
    return _hash([[dataset.instrument.symbol, dataset.dataset_hash] for dataset in universe.datasets])


def _cross_section_counts(timestamps: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for timestamp in timestamps:
        counts[timestamp] = counts.get(timestamp, 0) + 1
    return tuple((timestamp, counts[timestamp]) for timestamp in sorted(counts))


def _parse_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed) or value != parsed.astimezone(timezone.utc).isoformat():
        raise ValueError(f"{name} must use canonical UTC serialization")
    return parsed


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(character not in _HASH_CHARS for character in value):
        raise ValueError(f"{name} must be lowercase sha256")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
