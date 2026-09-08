"""OSS-3D2S raw DEVELOPMENT provenance with pre-label durable preregistration.

D2S closes the remaining raw-data gap in the DEVELOPMENT model-selection path.
It deliberately separates three moments:

1. raw DEVELOPMENT OHLCV -> causal OSS-3B DEVELOPMENT features;
2. six D2G predictions -> durable D2S statistical preregistration, with no
   SupervisedLabelArtifact materialized yet; and
3. only after that durable preregistration, raw OHLCV -> OSS-3C DEVELOPMENT
   labels -> ordinary D2H/D2E preregistration/evaluation.

The raw market path is available to the D2S preparation layer because it must be
cryptographically committed, but it is never passed to D2G.  Before the D2S
preregistration becomes durable, the only model-facing output is the causal
feature artifact.  Label values are neither materialized nor hashed as a label
artifact in the D2S preregistration.

Research only.  No FINAL_HOLDOUT/economic-holdout evaluation, broker, OMS,
Safety, OrderIntent, PAPER, capital or LIVE authority exists here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable

from autotrade.research.oss3_concrete_model_family import (
    CANONICAL_CANDIDATES,
    ConcreteModelFamilyPlan,
    ConcreteModelRequestSetEvidence,
)
from autotrade.research.oss3_development_model_tournament import (
    COMMON_SUPPORT_POLICY,
    MULTIPLE_TESTING_POLICY,
    PRIMARY_METRIC,
)
from autotrade.research.oss3_factor_matrix_artifact import (
    FactorMatrixArtifact,
    FactorMatrixPartition,
    FactorMatrixRow,
)
from autotrade.research.oss3_supervised_label_artifact import (
    LabelPartition,
    SupervisedLabelArtifact,
    SupervisedLabelRow,
)
from autotrade.research.universe import AlignedMarketUniverse

from .economic_raw_market_feature_provenance import (
    CANONICAL_LOOKBACK_BARS,
    canonical_oss3d2q_formula_registry_hash,
    canonical_oss3d2q_formulas,
)
from .family_evaluation_batch import (
    FamilyEvaluationBatchEvidence,
    FamilyEvaluationPreregistration,
    FrozenCandidateOutput,
    family_evaluation_code_hash,
    prepare_family_evaluation_preregistration,
)
from .raw_training_bundle_provenance import (
    ARITHMETIC_POLICY,
    canonical_oss3d2r_feature_definitions,
    canonical_oss3d2r_label_definition,
    canonical_oss3d2r_label_formula,
    research_universe_identity_hash,
)


OSS3D2S_SOURCE_VERSION = "OSS3D2S_RAW_DEVELOPMENT_MARKET_SOURCE_V1"
OSS3D2S_PREREGISTRATION_VERSION = "OSS3D2S_RAW_DEVELOPMENT_PREREGISTRATION_V1"
OSS3D2S_REVEAL_VERSION = "OSS3D2S_DEVELOPMENT_LABEL_REVEAL_V1"
OSS3D2S_COMPLETED_EVIDENCE_VERSION = "OSS3D2S_RAW_DEVELOPMENT_COMPLETED_EVIDENCE_V1"
FEATURE_CAUSAL_POLICY = "BAR_ENDED_AT_LE_FEATURE_AS_OF_PREFIX_ONLY_V1"
LABEL_REVEAL_POLICY = "MATERIALIZE_ONLY_AFTER_DURABLE_D2S_PREREGISTRATION_V1"
RAW_COMMITMENT_POLICY = "FULL_RAW_PATH_HASHED_BEFORE_LABEL_MATERIALIZATION_V1"
STATISTICAL_POLICY = "D2E_PRIMARY_METRIC_AND_MULTIPLE_TESTING_FROZEN_PRE_LABEL_V1"

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_ZERO = Decimal("0")

SEMANTIC_FILES = (
    "labs/oss3_qlib/raw_development_provenance.py",
    "labs/oss3_qlib/raw_training_bundle_provenance.py",
    "labs/oss3_qlib/economic_raw_market_feature_provenance.py",
    "labs/oss3_qlib/family_evaluation_batch.py",
    "labs/oss3_qlib/family_model_contract.py",
    "src/autotrade/research/market.py",
    "src/autotrade/research/universe.py",
    "src/autotrade/research/oss3_factor_matrix_artifact.py",
    "src/autotrade/research/oss3_supervised_label_artifact.py",
    "src/autotrade/research/oss3_development_inference.py",
    "src/autotrade/research/oss3_development_evaluation.py",
    "src/autotrade/research/oss3_development_model_tournament.py",
)


class RawDevelopmentProvenanceError(RuntimeError):
    """Base OSS-3D2S failure."""


class RawDevelopmentProvenanceIntegrityError(RawDevelopmentProvenanceError):
    """Raw material, candidate, preregistration or revealed evidence drifted."""


class RawDevelopmentProvenanceGovernanceError(RawDevelopmentProvenanceError):
    """Operation violates pre-label DEVELOPMENT governance."""


@dataclass(frozen=True, slots=True)
class RawDevelopmentMarketSource:
    source_version: str
    warmup_universe: AlignedMarketUniverse
    development_universe: AlignedMarketUniverse
    full_development_path_loaded: bool
    supervised_label_artifact_materialized: bool
    prediction_values_included: bool
    development_metrics_included: bool
    final_holdout_values_included: bool
    economic_holdout_values_included: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.source_version != OSS3D2S_SOURCE_VERSION:
            raise RawDevelopmentProvenanceIntegrityError("noncanonical D2S source version")
        if not isinstance(self.warmup_universe, AlignedMarketUniverse):
            raise TypeError("warmup_universe must be AlignedMarketUniverse")
        if not isinstance(self.development_universe, AlignedMarketUniverse):
            raise TypeError("development_universe must be AlignedMarketUniverse")
        if self.warmup_universe.symbols != self.development_universe.symbols:
            raise RawDevelopmentProvenanceIntegrityError("warmup/DEVELOPMENT symbols differ")
        if research_universe_identity_hash(self.warmup_universe) != research_universe_identity_hash(
            self.development_universe
        ):
            raise RawDevelopmentProvenanceIntegrityError(
                "warmup/DEVELOPMENT research-universe identity differs"
            )
        if self.warmup_universe.bar_count != CANONICAL_LOOKBACK_BARS:
            raise RawDevelopmentProvenanceGovernanceError(
                "D2S requires exactly twenty pre-DEVELOPMENT warmup bars"
            )
        if self.development_universe.bar_count < 3:
            raise RawDevelopmentProvenanceGovernanceError(
                "D2S requires at least three DEVELOPMENT bars"
            )
        for symbol in self.development_universe.symbols:
            warmup = self.warmup_universe.dataset(symbol)
            development = self.development_universe.dataset(symbol)
            if warmup.bars[-1].ended_at != development.bars[0].started_at:
                raise RawDevelopmentProvenanceGovernanceError(
                    "DEVELOPMENT warmup must terminate exactly at partition start"
                )
        if self.full_development_path_loaded is not True:
            raise RawDevelopmentProvenanceIntegrityError(
                "D2S must acknowledge full raw DEVELOPMENT material in preparation layer"
            )
        if (
            self.supervised_label_artifact_materialized
            or self.prediction_values_included
            or self.development_metrics_included
            or self.final_holdout_values_included
            or self.economic_holdout_values_included
        ):
            raise RawDevelopmentProvenanceGovernanceError(
                "D2S raw source must contain market bars only before label materialization"
            )
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
        )

    @classmethod
    def build(
        cls,
        *,
        warmup_universe: AlignedMarketUniverse,
        development_universe: AlignedMarketUniverse,
    ) -> "RawDevelopmentMarketSource":
        return cls(
            source_version=OSS3D2S_SOURCE_VERSION,
            warmup_universe=warmup_universe,
            development_universe=development_universe,
            full_development_path_loaded=True,
            supervised_label_artifact_materialized=False,
            prediction_values_included=False,
            development_metrics_included=False,
            final_holdout_values_included=False,
            economic_holdout_values_included=False,
            execution_authorized=False,
            paper_execution_authorized=False,
            capital_authority="NONE",
            live_trading="BLOCKED",
        )

    @property
    def universe_identity_hash(self) -> str:
        return research_universe_identity_hash(self.development_universe)

    @property
    def warmup_dataset_set_hash(self) -> str:
        return _dataset_set_hash(self.warmup_universe)

    @property
    def development_dataset_set_hash(self) -> str:
        return _dataset_set_hash(self.development_universe)

    @property
    def source_hash(self) -> str:
        return _hash(
            {
                "source_version": self.source_version,
                "raw_commitment_policy": RAW_COMMITMENT_POLICY,
                "research_universe_identity_hash": self.universe_identity_hash,
                "warmup_universe_material_hash": self.warmup_universe.universe_hash,
                "warmup_dataset_set_hash": self.warmup_dataset_set_hash,
                "development_universe_material_hash": self.development_universe.universe_hash,
                "development_dataset_set_hash": self.development_dataset_set_hash,
                "warmup_bars": self.warmup_universe.bar_count,
                "development_bars": self.development_universe.bar_count,
            }
        )

    @property
    def partition_start(self) -> datetime:
        return self.development_universe.datasets[0].bars[0].ended_at.astimezone(timezone.utc)

    @property
    def partition_end(self) -> datetime:
        return self.development_universe.datasets[0].bars[-1].ended_at.astimezone(
            timezone.utc
        ) + timedelta(microseconds=1)

    @property
    def sample_count(self) -> int:
        return (self.development_universe.bar_count - 1) * len(self.development_universe.symbols)

    @property
    def evaluation_keys(self) -> tuple[tuple[str, str], ...]:
        result: list[tuple[str, str]] = []
        for signal_index in range(self.development_universe.bar_count - 1):
            timestamp = self.development_universe.datasets[0].bars[signal_index].ended_at.astimezone(
                timezone.utc
            ).isoformat()
            for symbol in self.development_universe.symbols:
                result.append((timestamp, symbol))
        return tuple(result)

    @property
    def evaluation_keyset_hash(self) -> str:
        return _hash_keyset(self.evaluation_keys)


@dataclass(frozen=True, slots=True)
class D2SCandidateBinding:
    candidate_id: str
    request_hash: str
    prediction_artifact_hash: str
    prediction_receipt_hash: str
    environment_attestation_hash: str
    runtime_environment_hash: str
    model_config_hash: str
    shared_runner_code_hash: str

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.candidate_id):
            raise ValueError("invalid candidate_id")
        for name in (
            "request_hash",
            "prediction_artifact_hash",
            "prediction_receipt_hash",
            "environment_attestation_hash",
            "runtime_environment_hash",
            "model_config_hash",
            "shared_runner_code_hash",
        ):
            _require_hash(getattr(self, name), name)

    @classmethod
    def from_output(cls, output: FrozenCandidateOutput) -> "D2SCandidateBinding":
        return cls(
            candidate_id=output.candidate_id,
            request_hash=output.request.request_hash,
            prediction_artifact_hash=output.prediction.artifact_hash,
            prediction_receipt_hash=output.receipt.fingerprint,
            environment_attestation_hash=output.attestation.artifact_hash,
            runtime_environment_hash=output.runtime_environment.fingerprint,
            model_config_hash=output.request.manifest.model_config_hash,
            shared_runner_code_hash=output.request.manifest.expected_runner_code_hash,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "request_hash": self.request_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "prediction_receipt_hash": self.prediction_receipt_hash,
            "environment_attestation_hash": self.environment_attestation_hash,
            "runtime_environment_hash": self.runtime_environment_hash,
            "model_config_hash": self.model_config_hash,
            "shared_runner_code_hash": self.shared_runner_code_hash,
        }


@dataclass(frozen=True, slots=True)
class OSS3RawDevelopmentPreregistration:
    preregistration_version: str
    preregistration_id: str
    tournament_campaign_id: str
    tournament_id: str
    source_campaign_id: str
    research_split_hash: str
    research_universe_identity_hash: str
    raw_development_source_hash: str
    development_universe_material_hash: str
    development_dataset_set_hash: str
    feature_artifact_hash: str
    feature_schema_hash: str
    feature_row_payload_hash: str
    feature_formula_registry_hash: str
    label_formula_hash: str
    label_definition_hash: str
    evaluation_keyset_hash: str
    evaluation_start: str
    evaluation_end: str
    observation_count: int
    d2f_plan_fingerprint: str
    d2f_request_set_fingerprint: str
    d2h_code_version: str
    d2s_code_version: str
    candidate_bindings: tuple[D2SCandidateBinding, ...]
    runtime_environment_hash: str
    primary_metric: str
    multiple_testing_policy: str
    common_support_policy: str
    statistical_policy: str
    label_artifact_materialized: bool = False
    label_values_used: bool = False
    development_metrics_computed: bool = False
    final_holdout_observed: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.preregistration_version != OSS3D2S_PREREGISTRATION_VERSION:
            raise RawDevelopmentProvenanceIntegrityError("noncanonical D2S preregistration version")
        for name in (
            "preregistration_id",
            "tournament_campaign_id",
            "tournament_id",
            "source_campaign_id",
        ):
            _require_id(getattr(self, name), name)
        for name in (
            "research_split_hash",
            "research_universe_identity_hash",
            "raw_development_source_hash",
            "development_universe_material_hash",
            "development_dataset_set_hash",
            "feature_artifact_hash",
            "feature_schema_hash",
            "feature_row_payload_hash",
            "feature_formula_registry_hash",
            "label_formula_hash",
            "label_definition_hash",
            "evaluation_keyset_hash",
            "d2f_plan_fingerprint",
            "d2f_request_set_fingerprint",
            "d2h_code_version",
            "d2s_code_version",
            "runtime_environment_hash",
        ):
            _require_hash(getattr(self, name), name)
        if self.feature_formula_registry_hash != canonical_oss3d2q_formula_registry_hash():
            raise RawDevelopmentProvenanceIntegrityError("D2S feature formula registry drifted")
        if self.label_formula_hash != canonical_oss3d2r_label_formula().formula_hash:
            raise RawDevelopmentProvenanceIntegrityError("D2S label formula drifted")
        if self.d2s_code_version != raw_development_producer_semantic_hash():
            raise RawDevelopmentProvenanceIntegrityError("D2S semantic hash drifted")
        if self.d2h_code_version != family_evaluation_code_hash():
            raise RawDevelopmentProvenanceIntegrityError("D2H semantic hash drifted before D2S preregistration")
        if self.primary_metric != PRIMARY_METRIC:
            raise RawDevelopmentProvenanceGovernanceError("D2S primary metric is not frozen D2E metric")
        if self.multiple_testing_policy != MULTIPLE_TESTING_POLICY:
            raise RawDevelopmentProvenanceGovernanceError("D2S multiple-testing policy drifted")
        if self.common_support_policy != COMMON_SUPPORT_POLICY:
            raise RawDevelopmentProvenanceGovernanceError("D2S common-support policy drifted")
        if self.statistical_policy != STATISTICAL_POLICY:
            raise RawDevelopmentProvenanceGovernanceError("D2S statistical policy drifted")
        expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
        ids = tuple(binding.candidate_id for binding in self.candidate_bindings)
        if ids != expected_ids:
            raise RawDevelopmentProvenanceGovernanceError("D2S requires exact canonical D2F family")
        if len({binding.fingerprint for binding in self.candidate_bindings}) != len(ids):
            raise RawDevelopmentProvenanceIntegrityError("D2S candidate bindings are not unique")
        if {binding.runtime_environment_hash for binding in self.candidate_bindings} != {
            self.runtime_environment_hash
        }:
            raise RawDevelopmentProvenanceIntegrityError("D2S candidates do not share one runtime")
        if isinstance(self.observation_count, bool) or not isinstance(self.observation_count, int) or self.observation_count < 3:
            raise ValueError("D2S observation_count is invalid")
        start = _canonical_utc(self.evaluation_start, "evaluation_start")
        end = _canonical_utc(self.evaluation_end, "evaluation_end")
        if not start < end:
            raise ValueError("D2S evaluation window is invalid")
        if self.label_artifact_materialized or self.label_values_used or self.development_metrics_computed:
            raise RawDevelopmentProvenanceGovernanceError(
                "D2S preregistration must precede label artifact materialization and metrics"
            )
        if self.final_holdout_observed or self.promotion_authorized:
            raise RawDevelopmentProvenanceGovernanceError("D2S preregistration cannot observe holdout/promote")
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
            "preregistration_version": self.preregistration_version,
            "preregistration_id": self.preregistration_id,
            "tournament_campaign_id": self.tournament_campaign_id,
            "tournament_id": self.tournament_id,
            "source_campaign_id": self.source_campaign_id,
            "research_split_hash": self.research_split_hash,
            "research_universe_identity_hash": self.research_universe_identity_hash,
            "raw_development_source_hash": self.raw_development_source_hash,
            "development_universe_material_hash": self.development_universe_material_hash,
            "development_dataset_set_hash": self.development_dataset_set_hash,
            "feature_artifact_hash": self.feature_artifact_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "feature_row_payload_hash": self.feature_row_payload_hash,
            "feature_formula_registry_hash": self.feature_formula_registry_hash,
            "label_formula_hash": self.label_formula_hash,
            "label_definition_hash": self.label_definition_hash,
            "evaluation_keyset_hash": self.evaluation_keyset_hash,
            "evaluation_start": self.evaluation_start,
            "evaluation_end": self.evaluation_end,
            "observation_count": self.observation_count,
            "d2f_plan_fingerprint": self.d2f_plan_fingerprint,
            "d2f_request_set_fingerprint": self.d2f_request_set_fingerprint,
            "d2h_code_version": self.d2h_code_version,
            "d2s_code_version": self.d2s_code_version,
            "candidate_bindings": [binding.to_dict() for binding in self.candidate_bindings],
            "candidate_binding_hashes": [binding.fingerprint for binding in self.candidate_bindings],
            "runtime_environment_hash": self.runtime_environment_hash,
            "primary_metric": self.primary_metric,
            "multiple_testing_policy": self.multiple_testing_policy,
            "common_support_policy": self.common_support_policy,
            "statistical_policy": self.statistical_policy,
            "label_artifact_materialized": self.label_artifact_materialized,
            "label_values_used": self.label_values_used,
            "development_metrics_computed": self.development_metrics_computed,
            "final_holdout_observed": self.final_holdout_observed,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class OSS3DevelopmentLabelRevealReceipt:
    reveal_version: str
    preregistration_fingerprint: str
    raw_development_source_hash: str
    label_formula_hash: str
    label_definition_hash: str
    label_artifact_hash: str
    label_manifest_hash: str
    label_row_payload_hash: str
    evaluation_keyset_hash: str
    observation_count: int
    durable_d2s_preregistration_verified: bool
    predictions_frozen_before_label_materialization: bool
    label_values_materialized_after_durable_preregistration: bool
    label_future_values_confined_to_explicit_one_bar_horizon: bool
    development_metrics_computed: bool = False
    final_holdout_observed: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"
    receipt_hash: str = ""

    def __post_init__(self) -> None:
        if self.reveal_version != OSS3D2S_REVEAL_VERSION:
            raise RawDevelopmentProvenanceIntegrityError("noncanonical D2S reveal version")
        for name in (
            "preregistration_fingerprint",
            "raw_development_source_hash",
            "label_formula_hash",
            "label_definition_hash",
            "label_artifact_hash",
            "label_manifest_hash",
            "label_row_payload_hash",
            "evaluation_keyset_hash",
            "receipt_hash",
        ):
            _require_hash(getattr(self, name), name)
        if not (
            self.durable_d2s_preregistration_verified
            and self.predictions_frozen_before_label_materialization
            and self.label_values_materialized_after_durable_preregistration
            and self.label_future_values_confined_to_explicit_one_bar_horizon
        ):
            raise RawDevelopmentProvenanceGovernanceError("D2S label reveal sequencing proof missing")
        if self.development_metrics_computed or self.final_holdout_observed or self.promotion_authorized:
            raise RawDevelopmentProvenanceGovernanceError("D2S reveal cannot contain metrics/holdout/promotion")
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
        )
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise RawDevelopmentProvenanceIntegrityError("D2S reveal receipt hash mismatch")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "reveal_version": self.reveal_version,
            "preregistration_fingerprint": self.preregistration_fingerprint,
            "raw_development_source_hash": self.raw_development_source_hash,
            "label_formula_hash": self.label_formula_hash,
            "label_definition_hash": self.label_definition_hash,
            "label_artifact_hash": self.label_artifact_hash,
            "label_manifest_hash": self.label_manifest_hash,
            "label_row_payload_hash": self.label_row_payload_hash,
            "evaluation_keyset_hash": self.evaluation_keyset_hash,
            "observation_count": self.observation_count,
            "durable_d2s_preregistration_verified": self.durable_d2s_preregistration_verified,
            "predictions_frozen_before_label_materialization": self.predictions_frozen_before_label_materialization,
            "label_values_materialized_after_durable_preregistration": self.label_values_materialized_after_durable_preregistration,
            "label_future_values_confined_to_explicit_one_bar_horizon": self.label_future_values_confined_to_explicit_one_bar_horizon,
            "development_metrics_computed": self.development_metrics_computed,
            "final_holdout_observed": self.final_holdout_observed,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }
        if include_hash:
            payload["receipt_hash"] = self.receipt_hash
        return payload


@dataclass(frozen=True, slots=True)
class OSS3RawDevelopmentCompletedEvidence:
    evidence_version: str
    d2s_preregistration_fingerprint: str
    label_reveal_receipt_hash: str
    d2h_preregistration_fingerprint: str
    d2e_plan_fingerprint: str
    d2h_batch_evidence_fingerprint: str
    tournament_evidence_hash: str
    label_artifact_hash: str
    raw_development_source_hash: str
    winner_trial_id: str
    label_values_materialized_after_d2s_preregistration: bool = True
    metrics_computed_after_d2h_preregistration: bool = True
    final_holdout_observed: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D2S_COMPLETED_EVIDENCE_VERSION:
            raise RawDevelopmentProvenanceIntegrityError("noncanonical D2S completed evidence version")
        for name in (
            "d2s_preregistration_fingerprint",
            "label_reveal_receipt_hash",
            "d2h_preregistration_fingerprint",
            "d2e_plan_fingerprint",
            "d2h_batch_evidence_fingerprint",
            "tournament_evidence_hash",
            "label_artifact_hash",
            "raw_development_source_hash",
        ):
            _require_hash(getattr(self, name), name)
        _require_id(self.winner_trial_id, "winner_trial_id")
        if not self.label_values_materialized_after_d2s_preregistration:
            raise RawDevelopmentProvenanceGovernanceError("D2S completed evidence lacks label sequencing")
        if not self.metrics_computed_after_d2h_preregistration:
            raise RawDevelopmentProvenanceGovernanceError("D2S completed evidence lacks metric sequencing")
        if self.final_holdout_observed or self.promotion_authorized:
            raise RawDevelopmentProvenanceGovernanceError("D2S completed evidence cannot observe holdout/promote")
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
            "d2s_preregistration_fingerprint": self.d2s_preregistration_fingerprint,
            "label_reveal_receipt_hash": self.label_reveal_receipt_hash,
            "d2h_preregistration_fingerprint": self.d2h_preregistration_fingerprint,
            "d2e_plan_fingerprint": self.d2e_plan_fingerprint,
            "d2h_batch_evidence_fingerprint": self.d2h_batch_evidence_fingerprint,
            "tournament_evidence_hash": self.tournament_evidence_hash,
            "label_artifact_hash": self.label_artifact_hash,
            "raw_development_source_hash": self.raw_development_source_hash,
            "winner_trial_id": self.winner_trial_id,
            "label_values_materialized_after_d2s_preregistration": self.label_values_materialized_after_d2s_preregistration,
            "metrics_computed_after_d2h_preregistration": self.metrics_computed_after_d2h_preregistration,
            "final_holdout_observed": self.final_holdout_observed,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


class SQLiteOSS3RawDevelopmentPreregistrationRegistry:
    """Append-only durable D2S preregistration store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oss3d2s_raw_development_preregistrations (
                    preregistration_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS oss3d2s_prereg_no_update
                BEFORE UPDATE ON oss3d2s_raw_development_preregistrations
                BEGIN
                    SELECT RAISE(ABORT, 'OSS3D2S_PREREGISTRATION_APPEND_ONLY');
                END;
                CREATE TRIGGER IF NOT EXISTS oss3d2s_prereg_no_delete
                BEFORE DELETE ON oss3d2s_raw_development_preregistrations
                BEGIN
                    SELECT RAISE(ABORT, 'OSS3D2S_PREREGISTRATION_APPEND_ONLY');
                END;
                """
            )

    def preregister(
        self,
        preregistration: OSS3RawDevelopmentPreregistration,
        *,
        now: datetime,
    ) -> None:
        if not isinstance(preregistration, OSS3RawDevelopmentPreregistration):
            raise TypeError("preregistration must be OSS3RawDevelopmentPreregistration")
        created_at = _canonical_utc_text(now, "now")
        payload = _canonical_json_text(preregistration.to_dict())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT fingerprint, plan_json FROM oss3d2s_raw_development_preregistrations "
                "WHERE preregistration_id = ?",
                (preregistration.preregistration_id,),
            ).fetchone()
            if row is not None:
                if str(row["fingerprint"]) != preregistration.fingerprint or str(row["plan_json"]) != payload:
                    raise RawDevelopmentProvenanceIntegrityError(
                        "D2S preregistration id already binds different evidence"
                    )
                return
            conn.execute(
                "INSERT INTO oss3d2s_raw_development_preregistrations "
                "(preregistration_id, fingerprint, plan_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    preregistration.preregistration_id,
                    preregistration.fingerprint,
                    payload,
                    created_at,
                ),
            )

    def require_exact(self, preregistration: OSS3RawDevelopmentPreregistration) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fingerprint, plan_json FROM oss3d2s_raw_development_preregistrations "
                "WHERE preregistration_id = ?",
                (preregistration.preregistration_id,),
            ).fetchone()
        if row is None:
            raise RawDevelopmentProvenanceGovernanceError(
                "D2S label materialization requires durable D2S preregistration"
            )
        if str(row["fingerprint"]) != preregistration.fingerprint:
            raise RawDevelopmentProvenanceIntegrityError("durable D2S fingerprint drifted")
        if str(row["plan_json"]) != _canonical_json_text(preregistration.to_dict()):
            raise RawDevelopmentProvenanceIntegrityError("durable D2S plan JSON drifted")


def derive_raw_development_features(
    *,
    raw_source: RawDevelopmentMarketSource,
    campaign_id: str,
    research_split_hash: str,
) -> FactorMatrixArtifact:
    """Derive causal DEVELOPMENT features without constructing labels."""
    if not isinstance(raw_source, RawDevelopmentMarketSource):
        raise TypeError("raw_source must be RawDevelopmentMarketSource")
    _require_id(campaign_id, "campaign_id")
    _require_hash(research_split_hash, "research_split_hash")
    rows = _derive_feature_rows(raw_source)
    return FactorMatrixArtifact.build(
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
        partition=FactorMatrixPartition.DEVELOPMENT,
        partition_start=raw_source.partition_start,
        partition_end=raw_source.partition_end,
        producer_code_hash=raw_development_producer_semantic_hash(),
        source_dataset_hash=raw_source.source_hash,
        source_universe_hash=raw_source.universe_identity_hash,
        features=canonical_oss3d2r_feature_definitions(),
        rows=rows,
    )


def verify_raw_development_features(
    *,
    raw_source: RawDevelopmentMarketSource,
    campaign_id: str,
    research_split_hash: str,
    features: FactorMatrixArtifact,
) -> None:
    expected = derive_raw_development_features(
        raw_source=raw_source,
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
    )
    if features != expected:
        raise RawDevelopmentProvenanceIntegrityError(
            "DEVELOPMENT feature artifact does not reproduce committed raw source"
        )
    keys = tuple((row.as_of, row.symbol) for row in features.rows)
    if keys != raw_source.evaluation_keys:
        raise RawDevelopmentProvenanceIntegrityError("D2S feature support differs from raw support")


def prepare_raw_development_preregistration(
    *,
    preregistration_id: str,
    tournament_campaign_id: str,
    tournament_id: str,
    d2f_plan: ConcreteModelFamilyPlan,
    d2f_request_set: ConcreteModelRequestSetEvidence,
    outputs: Iterable[FrozenCandidateOutput],
    raw_source: RawDevelopmentMarketSource,
    development_features: FactorMatrixArtifact,
) -> OSS3RawDevelopmentPreregistration:
    """Freeze candidate/statistical/raw identities without materializing labels."""
    _require_id(preregistration_id, "preregistration_id")
    _require_id(tournament_campaign_id, "tournament_campaign_id")
    _require_id(tournament_id, "tournament_id")
    if d2f_request_set.family_fingerprint != d2f_plan.fingerprint:
        raise RawDevelopmentProvenanceIntegrityError("D2F plan/request-set mismatch")
    if d2f_request_set.development_feature_artifact_hash != development_features.artifact_hash:
        raise RawDevelopmentProvenanceIntegrityError("D2F request set does not bind D2S DEVELOPMENT features")
    verify_raw_development_features(
        raw_source=raw_source,
        campaign_id=development_features.manifest.campaign_id,
        research_split_hash=development_features.manifest.research_split_hash,
        features=development_features,
    )
    output_tuple = tuple(sorted(tuple(outputs), key=lambda item: item.candidate_id))
    expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
    if tuple(output.candidate_id for output in output_tuple) != expected_ids:
        raise RawDevelopmentProvenanceGovernanceError("D2S requires six frozen canonical D2G outputs")
    if tuple(binding.candidate_id for binding in d2f_request_set.bindings) != expected_ids:
        raise RawDevelopmentProvenanceIntegrityError("D2F request family drifted")
    for binding, output in zip(d2f_request_set.bindings, output_tuple, strict=True):
        if binding.request.request_hash != output.request.request_hash:
            raise RawDevelopmentProvenanceIntegrityError("D2S output request differs from D2F request")

    runtime_hashes = {output.runtime_environment.fingerprint for output in output_tuple}
    if len(runtime_hashes) != 1:
        raise RawDevelopmentProvenanceIntegrityError("D2S outputs do not share one runtime")
    runtime_hash = next(iter(runtime_hashes))
    fm = development_features.manifest
    expected_label_definition = canonical_oss3d2r_label_definition()
    for output in output_tuple:
        receipt = output.receipt
        request = output.request.manifest
        for name, expected, actual in (
            ("campaign", fm.campaign_id, receipt.campaign_id),
            ("research split", fm.research_split_hash, receipt.research_split_hash),
            ("source universe", fm.source_universe_hash, receipt.source_universe_hash),
            ("feature schema", fm.feature_schema_hash, receipt.feature_schema_hash),
            ("label definition", expected_label_definition.fingerprint, receipt.label_definition_hash),
            ("evaluation keyset", raw_source.evaluation_keyset_hash, receipt.inference_keyset_hash),
            ("evaluation start", fm.partition_start, receipt.inference_start),
            ("evaluation end", fm.partition_end, receipt.inference_end),
            ("observation count", fm.row_count, receipt.prediction_count),
            ("request DEVELOPMENT artifact", development_features.artifact_hash, request.development_feature_artifact_hash),
        ):
            if expected != actual:
                raise RawDevelopmentProvenanceIntegrityError(f"D2S candidate {name} mismatch")

    return OSS3RawDevelopmentPreregistration(
        preregistration_version=OSS3D2S_PREREGISTRATION_VERSION,
        preregistration_id=preregistration_id,
        tournament_campaign_id=tournament_campaign_id,
        tournament_id=tournament_id,
        source_campaign_id=fm.campaign_id,
        research_split_hash=fm.research_split_hash,
        research_universe_identity_hash=fm.source_universe_hash,
        raw_development_source_hash=raw_source.source_hash,
        development_universe_material_hash=raw_source.development_universe.universe_hash,
        development_dataset_set_hash=raw_source.development_dataset_set_hash,
        feature_artifact_hash=development_features.artifact_hash,
        feature_schema_hash=fm.feature_schema_hash,
        feature_row_payload_hash=fm.row_payload_hash,
        feature_formula_registry_hash=canonical_oss3d2q_formula_registry_hash(),
        label_formula_hash=canonical_oss3d2r_label_formula().formula_hash,
        label_definition_hash=expected_label_definition.fingerprint,
        evaluation_keyset_hash=raw_source.evaluation_keyset_hash,
        evaluation_start=fm.partition_start,
        evaluation_end=fm.partition_end,
        observation_count=fm.row_count,
        d2f_plan_fingerprint=d2f_plan.fingerprint,
        d2f_request_set_fingerprint=d2f_request_set.fingerprint,
        d2h_code_version=family_evaluation_code_hash(),
        d2s_code_version=raw_development_producer_semantic_hash(),
        candidate_bindings=tuple(D2SCandidateBinding.from_output(output) for output in output_tuple),
        runtime_environment_hash=runtime_hash,
        primary_metric=PRIMARY_METRIC,
        multiple_testing_policy=MULTIPLE_TESTING_POLICY,
        common_support_policy=COMMON_SUPPORT_POLICY,
        statistical_policy=STATISTICAL_POLICY,
    )


def materialize_development_labels_after_preregistration(
    *,
    registry: SQLiteOSS3RawDevelopmentPreregistrationRegistry,
    preregistration: OSS3RawDevelopmentPreregistration,
    raw_source: RawDevelopmentMarketSource,
    development_features: FactorMatrixArtifact,
) -> tuple[SupervisedLabelArtifact, OSS3DevelopmentLabelRevealReceipt]:
    """Materialize DEVELOPMENT labels only after durable D2S preregistration."""
    registry.require_exact(preregistration)
    if preregistration.d2s_code_version != raw_development_producer_semantic_hash():
        raise RawDevelopmentProvenanceIntegrityError("D2S code changed after preregistration")
    if preregistration.d2h_code_version != family_evaluation_code_hash():
        raise RawDevelopmentProvenanceIntegrityError("D2H/D2E code changed after D2S preregistration")
    if raw_source.source_hash != preregistration.raw_development_source_hash:
        raise RawDevelopmentProvenanceIntegrityError("raw DEVELOPMENT source changed after preregistration")
    verify_raw_development_features(
        raw_source=raw_source,
        campaign_id=preregistration.source_campaign_id,
        research_split_hash=preregistration.research_split_hash,
        features=development_features,
    )
    if development_features.artifact_hash != preregistration.feature_artifact_hash:
        raise RawDevelopmentProvenanceIntegrityError("DEVELOPMENT features changed after preregistration")

    labels = _derive_development_labels(
        raw_source=raw_source,
        campaign_id=preregistration.source_campaign_id,
        research_split_hash=preregistration.research_split_hash,
    )
    label_keys = tuple((row.label_as_of, row.symbol) for row in labels.rows)
    if _hash_keyset(label_keys) != preregistration.evaluation_keyset_hash:
        raise RawDevelopmentProvenanceIntegrityError("revealed label support differs from preregistration")
    if labels.manifest.label_definition_hash != preregistration.label_definition_hash:
        raise RawDevelopmentProvenanceIntegrityError("revealed label definition differs from preregistration")
    if labels.manifest.partition_start != preregistration.evaluation_start or labels.manifest.partition_end != preregistration.evaluation_end:
        raise RawDevelopmentProvenanceIntegrityError("revealed label window differs from preregistration")
    values: dict[str, object] = {
        "reveal_version": OSS3D2S_REVEAL_VERSION,
        "preregistration_fingerprint": preregistration.fingerprint,
        "raw_development_source_hash": raw_source.source_hash,
        "label_formula_hash": canonical_oss3d2r_label_formula().formula_hash,
        "label_definition_hash": labels.manifest.label_definition_hash,
        "label_artifact_hash": labels.artifact_hash,
        "label_manifest_hash": labels.manifest.fingerprint,
        "label_row_payload_hash": labels.manifest.row_payload_hash,
        "evaluation_keyset_hash": preregistration.evaluation_keyset_hash,
        "observation_count": labels.manifest.row_count,
        "durable_d2s_preregistration_verified": True,
        "predictions_frozen_before_label_materialization": True,
        "label_values_materialized_after_durable_preregistration": True,
        "label_future_values_confined_to_explicit_one_bar_horizon": True,
        "development_metrics_computed": False,
        "final_holdout_observed": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    receipt_hash = _hash(values)
    return labels, OSS3DevelopmentLabelRevealReceipt(**values, receipt_hash=receipt_hash)


def prepare_d2h_from_d2s_reveal(
    *,
    registry: SQLiteOSS3RawDevelopmentPreregistrationRegistry,
    preregistration: OSS3RawDevelopmentPreregistration,
    reveal: OSS3DevelopmentLabelRevealReceipt,
    labels: SupervisedLabelArtifact,
    d2f_plan: ConcreteModelFamilyPlan,
    d2f_request_set: ConcreteModelRequestSetEvidence,
    outputs: Iterable[FrozenCandidateOutput],
) -> FamilyEvaluationPreregistration:
    """Build ordinary D2H/D2E preregistration as exact extension of D2S."""
    registry.require_exact(preregistration)
    if reveal.preregistration_fingerprint != preregistration.fingerprint:
        raise RawDevelopmentProvenanceIntegrityError("D2S reveal belongs to different preregistration")
    if reveal.label_artifact_hash != labels.artifact_hash:
        raise RawDevelopmentProvenanceIntegrityError("D2S reveal does not bind supplied labels")
    d2h = prepare_family_evaluation_preregistration(
        d2f_plan=d2f_plan,
        d2f_request_set=d2f_request_set,
        outputs=outputs,
        development_labels=labels,
        tournament_campaign_id=preregistration.tournament_campaign_id,
        tournament_id=preregistration.tournament_id,
    )
    _verify_d2h_extends_d2s(preregistration, reveal, d2h, outputs)
    return d2h


def bind_completed_raw_development_evidence(
    *,
    preregistration: OSS3RawDevelopmentPreregistration,
    reveal: OSS3DevelopmentLabelRevealReceipt,
    d2h_preregistration: FamilyEvaluationPreregistration,
    batch_evidence: FamilyEvaluationBatchEvidence,
) -> OSS3RawDevelopmentCompletedEvidence:
    if batch_evidence.preregistration_fingerprint != d2h_preregistration.fingerprint:
        raise RawDevelopmentProvenanceIntegrityError("D2H batch differs from D2H preregistration")
    if batch_evidence.development_label_artifact_hash != reveal.label_artifact_hash:
        raise RawDevelopmentProvenanceIntegrityError("D2H batch label differs from D2S reveal")
    if d2h_preregistration.d2e_plan.fingerprint != batch_evidence.d2e_plan_fingerprint:
        raise RawDevelopmentProvenanceIntegrityError("D2H batch plan differs from D2S-bound plan")
    return OSS3RawDevelopmentCompletedEvidence(
        evidence_version=OSS3D2S_COMPLETED_EVIDENCE_VERSION,
        d2s_preregistration_fingerprint=preregistration.fingerprint,
        label_reveal_receipt_hash=reveal.receipt_hash,
        d2h_preregistration_fingerprint=d2h_preregistration.fingerprint,
        d2e_plan_fingerprint=d2h_preregistration.d2e_plan.fingerprint,
        d2h_batch_evidence_fingerprint=batch_evidence.fingerprint,
        tournament_evidence_hash=batch_evidence.tournament_evidence.fingerprint,
        label_artifact_hash=reveal.label_artifact_hash,
        raw_development_source_hash=preregistration.raw_development_source_hash,
        winner_trial_id=batch_evidence.tournament_evidence.winner_trial_id,
    )


def raw_development_producer_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    payload: list[dict[str, str]] = []
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise RawDevelopmentProvenanceIntegrityError(f"D2S semantic file missing: {relative}")
        payload.append({"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()})
    return _hash(payload)


def _derive_feature_rows(raw_source: RawDevelopmentMarketSource) -> tuple[FactorMatrixRow, ...]:
    formulas = canonical_oss3d2q_formulas()
    rows: list[FactorMatrixRow] = []
    for signal_index in range(raw_source.development_universe.bar_count - 1):
        as_of = raw_source.development_universe.datasets[0].bars[signal_index].ended_at.astimezone(
            timezone.utc
        )
        for symbol in raw_source.development_universe.symbols:
            history = _causal_history(raw_source, symbol, signal_index, as_of)
            values = tuple(_compute_formula(formula.name, history) for formula in formulas)
            rows.append(
                FactorMatrixRow(
                    as_of=as_of.isoformat(),
                    available_at=as_of.isoformat(),
                    symbol=symbol,
                    values=values,
                )
            )
    return tuple(rows)


def _derive_development_labels(
    *,
    raw_source: RawDevelopmentMarketSource,
    campaign_id: str,
    research_split_hash: str,
) -> SupervisedLabelArtifact:
    rows: list[SupervisedLabelRow] = []
    for origin_index in range(raw_source.development_universe.bar_count - 1):
        for symbol in raw_source.development_universe.symbols:
            dataset = raw_source.development_universe.dataset(symbol)
            origin = dataset.bars[origin_index]
            horizon = dataset.bars[origin_index + 1]
            with localcontext() as context:
                context.prec = 50
                value = horizon.close / origin.close - Decimal("1")
            rows.append(
                SupervisedLabelRow(
                    label_as_of=origin.ended_at.astimezone(timezone.utc).isoformat(),
                    horizon_end=horizon.ended_at.astimezone(timezone.utc).isoformat(),
                    available_at=horizon.ended_at.astimezone(timezone.utc).isoformat(),
                    symbol=symbol,
                    value=float(value),
                )
            )
    return SupervisedLabelArtifact.build(
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
        partition=LabelPartition.DEVELOPMENT,
        partition_start=raw_source.partition_start,
        partition_end=raw_source.partition_end,
        producer_code_hash=raw_development_producer_semantic_hash(),
        source_dataset_hash=raw_source.source_hash,
        source_universe_hash=raw_source.universe_identity_hash,
        label=canonical_oss3d2r_label_definition(),
        rows=tuple(rows),
    )


def _causal_history(
    raw_source: RawDevelopmentMarketSource,
    symbol: str,
    signal_index: int,
    as_of: datetime,
):
    warmup = raw_source.warmup_universe.dataset(symbol).bars
    prefix = raw_source.development_universe.dataset(symbol).bars[: signal_index + 1]
    selected = (warmup + prefix)[-(CANONICAL_LOOKBACK_BARS + 1) :]
    if len(selected) != CANONICAL_LOOKBACK_BARS + 1:
        raise RawDevelopmentProvenanceGovernanceError("insufficient D2S causal feature history")
    if selected[-1].ended_at.astimezone(timezone.utc) != as_of:
        raise RawDevelopmentProvenanceIntegrityError("D2S causal history does not end at row as_of")
    if any(bar.ended_at.astimezone(timezone.utc) > as_of for bar in selected):
        raise RawDevelopmentProvenanceGovernanceError("future bar entered D2S feature row")
    return selected


def _compute_formula(name: str, history) -> float:
    closes = tuple(bar.close for bar in history)
    with localcontext() as context:
        context.prec = 50
        if name == "momentum_20":
            value = closes[-1] / closes[0] - Decimal("1")
        elif name == "volatility_20":
            returns = tuple(
                closes[index] / closes[index - 1] - Decimal("1")
                for index in range(1, len(closes))
            )
            mean = sum(returns, _ZERO) / Decimal(len(returns))
            variance = sum(((item - mean) ** 2 for item in returns), _ZERO) / Decimal(len(returns))
            value = variance.sqrt()
        else:
            raise RawDevelopmentProvenanceGovernanceError("unsupported D2S feature formula")
    return float(value)


def _verify_d2h_extends_d2s(
    preregistration: OSS3RawDevelopmentPreregistration,
    reveal: OSS3DevelopmentLabelRevealReceipt,
    d2h: FamilyEvaluationPreregistration,
    outputs: Iterable[FrozenCandidateOutput],
) -> None:
    if d2h.d2f_plan_fingerprint != preregistration.d2f_plan_fingerprint:
        raise RawDevelopmentProvenanceIntegrityError("D2H D2F plan differs from D2S preregistration")
    if d2h.d2f_request_set_fingerprint != preregistration.d2f_request_set_fingerprint:
        raise RawDevelopmentProvenanceIntegrityError("D2H D2F request set differs from D2S preregistration")
    if d2h.d2h_code_version != preregistration.d2h_code_version:
        raise RawDevelopmentProvenanceIntegrityError("D2H code version differs from D2S preregistration")
    if d2h.development_label_artifact_hash != reveal.label_artifact_hash:
        raise RawDevelopmentProvenanceIntegrityError("D2H label artifact differs from D2S reveal")
    plan = d2h.d2e_plan
    dataset = plan.dataset
    checks = (
        ("tournament campaign", preregistration.tournament_campaign_id, plan.campaign.campaign_id),
        ("tournament id", preregistration.tournament_id, plan.tournament.tournament_id),
        ("source campaign", preregistration.source_campaign_id, dataset.source_campaign_id),
        ("research split", preregistration.research_split_hash, dataset.research_split_hash),
        ("source universe", preregistration.research_universe_identity_hash, dataset.source_universe_hash),
        ("label definition", preregistration.label_definition_hash, dataset.label_definition_hash),
        ("label artifact", reveal.label_artifact_hash, dataset.development_label_artifact_hash),
        ("evaluation keyset", preregistration.evaluation_keyset_hash, dataset.evaluation_keyset_hash),
        ("evaluation start", preregistration.evaluation_start, dataset.evaluation_start),
        ("evaluation end", preregistration.evaluation_end, dataset.evaluation_end),
        ("runtime", preregistration.runtime_environment_hash, plan.runtime_environment.fingerprint),
        ("primary metric", preregistration.primary_metric, plan.primary_metric),
        ("multiple testing", preregistration.multiple_testing_policy, plan.multiple_testing_policy),
        ("common support", preregistration.common_support_policy, plan.common_support_policy),
    )
    for name, expected, actual in checks:
        if expected != actual:
            raise RawDevelopmentProvenanceIntegrityError(f"D2H extension {name} mismatch")
    output_tuple = tuple(sorted(tuple(outputs), key=lambda item: item.candidate_id))
    current = tuple(D2SCandidateBinding.from_output(output) for output in output_tuple)
    if current != preregistration.candidate_bindings:
        raise RawDevelopmentProvenanceIntegrityError("D2H output universe differs from D2S frozen outputs")


def _dataset_set_hash(universe: AlignedMarketUniverse) -> str:
    return _hash([[dataset.instrument.symbol, dataset.dataset_hash] for dataset in universe.datasets])


def _hash_keyset(pairs: tuple[tuple[str, str], ...]) -> str:
    raw = json.dumps(
        [[timestamp, symbol] for timestamp, symbol in pairs],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _canonical_utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be UTC")
    if value != parsed.astimezone(timezone.utc).isoformat():
        raise ValueError(f"{name} must use canonical UTC representation")
    return parsed


def _canonical_utc_text(value: datetime, name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat()


def _require_id(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase sha256")


def _canonical_json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json_text(value).encode("utf-8")).hexdigest()


def _deny_authority(
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise RawDevelopmentProvenanceGovernanceError("D2S cannot authorize execution")
    if capital_authority != "NONE" or live_trading != "BLOCKED":
        raise RawDevelopmentProvenanceGovernanceError("D2S cannot grant capital or LIVE")
