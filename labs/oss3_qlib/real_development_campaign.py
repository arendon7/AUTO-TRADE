"""OSS-3D3A real DEVELOPMENT campaign over the certified D2Y/D2Z history.

D3A connects the already-certified raw-data lineage to the existing D2R-D2I
research stack without changing any frozen model, feature, label, metric or
selection policy.

Scientific ordering is strict:

    D2W raw TRAIN / pre-label DEVELOPMENT
      -> D2R TRAIN bundle
      -> D2S DEVELOPMENT features
      -> exact six D2F requests
      -> exact six D2G predictions
      -> pre-label structural prediction profiles
      -> (>=2 evaluable candidates required)
      -> durable D2S preregistration
      -> DEVELOPMENT label materialization
      -> D2H/D2E durable preregistration
      -> valid D2D evaluations + structural failures recorded inside frozen family
      -> D2E tournament/Holm
      -> D2I DEVELOPMENT ranking-winner seal

No candidate is retuned, replaced or retried because of observed results.
FINAL_HOLDOUT is never loaded, authorized or consumed here.  A D2I winner is
only a DEVELOPMENT ranking winner, not evidence of profitability or execution
readiness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

from autotrade.research.oss3_concrete_model_family import (
    CANONICAL_CANDIDATES,
    build_concrete_model_request_set,
)
from autotrade.research.oss3_development_evaluation import (
    DevelopmentEvaluationArtifact,
    DevelopmentEvaluationError,
    evaluate_development_predictions,
)
from autotrade.research.oss3_development_model_tournament import (
    evaluate_oss3d2e_tournament,
    record_oss3d2e_evaluation,
    record_oss3d2e_failure,
)
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact
from autotrade.research.trials import SQLiteTrialLedger
from labs.oss3_qlib.development_winner_seal import (
    DevelopmentWinnerSelectionSeal,
    seal_development_winner,
    verify_development_winner_seal,
)
from labs.oss3_qlib.family_environment_attestation import CandidateEnvironmentAttestation
from labs.oss3_qlib.family_evaluation_batch import (
    CandidateEvaluationBinding,
    FamilyEvaluationBatchEvidence,
    FrozenCandidateOutput,
    FrozenCandidateOutputBinding,
    preregister_family_evaluation,
)
from labs.oss3_qlib.family_model_contract import family_runner_code_hash
from labs.oss3_qlib.family_runner import run_isolated_qlib_family_candidate
from labs.oss3_qlib.raw_development_provenance import (
    OSS3RawDevelopmentCompletedEvidence,
    SQLiteOSS3RawDevelopmentPreregistrationRegistry,
    bind_completed_raw_development_evidence,
    derive_raw_development_features,
    materialize_development_labels_after_preregistration,
    prepare_d2h_from_d2s_reveal,
    prepare_raw_development_preregistration,
)
from labs.oss3_qlib.raw_training_bundle_provenance import derive_raw_training_bundle
from labs.oss3_qlib.sealed_raw_split_handoff import build_canonical_sealed_raw_split_handoff


OSS3D3A_CAMPAIGN_EVIDENCE_VERSION = "OSS3D3A_REAL_DEVELOPMENT_CAMPAIGN_EVIDENCE_V1"
REAL_CAMPAIGN_ID = "oss3d3a-real-development-campaign-v1"
D2S_PREREGISTRATION_ID = "oss3d3a-real-development-prereg-v1"
D2E_TOURNAMENT_CAMPAIGN_ID = "oss3d3a-real-development-tournament-campaign-v1"
D2E_TOURNAMENT_ID = "oss3d3a-real-development-tournament-v1"
STRUCTURAL_POLICY = "PRELABEL_REQUIRE_GLOBAL_AND_EVERY_CROSS_SECTION_SCORE_VARIATION_V1"
STRUCTURAL_FAILURE_CODE = "OSS3D3A_PRELABEL_STRUCTURAL_UNEVALUABLE"
MIN_EVALUABLE_CANDIDATES = 2
STATUS_COMPLETED = "COMPLETED"
STATUS_PRELABEL_BLOCKED = "PRELABEL_BLOCKED"


class RealDevelopmentCampaignError(RuntimeError):
    pass


class RealDevelopmentCampaignIntegrityError(RealDevelopmentCampaignError):
    pass


class RealDevelopmentCampaignGovernanceError(RealDevelopmentCampaignError):
    pass


@dataclass(frozen=True, slots=True)
class PredictionStructuralProfile:
    candidate_id: str
    prediction_artifact_hash: str
    observation_count: int
    cross_section_count: int
    nonconstant_cross_section_count: int
    global_nonconstant: bool
    full_cross_section_support: bool
    evaluable: bool
    failure_code: str | None

    def __post_init__(self) -> None:
        _require_candidate_id(self.candidate_id)
        _require_hash(self.prediction_artifact_hash, "prediction_artifact_hash")
        if self.observation_count < 1 or self.cross_section_count < 1:
            raise RealDevelopmentCampaignIntegrityError("D3A structural profile counts must be positive")
        if not 0 <= self.nonconstant_cross_section_count <= self.cross_section_count:
            raise RealDevelopmentCampaignIntegrityError("D3A nonconstant support count is invalid")
        expected_full = self.nonconstant_cross_section_count == self.cross_section_count
        if self.full_cross_section_support != expected_full:
            raise RealDevelopmentCampaignIntegrityError("D3A structural support flag is inconsistent")
        expected_evaluable = self.global_nonconstant and self.full_cross_section_support
        if self.evaluable != expected_evaluable:
            raise RealDevelopmentCampaignIntegrityError("D3A evaluable flag is inconsistent")
        if self.evaluable:
            if self.failure_code is not None:
                raise RealDevelopmentCampaignIntegrityError("evaluable D3A candidate cannot carry failure code")
        elif self.failure_code != STRUCTURAL_FAILURE_CODE:
            raise RealDevelopmentCampaignIntegrityError("non-evaluable D3A candidate requires frozen failure code")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "observation_count": self.observation_count,
            "cross_section_count": self.cross_section_count,
            "nonconstant_cross_section_count": self.nonconstant_cross_section_count,
            "global_nonconstant": self.global_nonconstant,
            "full_cross_section_support": self.full_cross_section_support,
            "evaluable": self.evaluable,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class CandidateDevelopmentResult:
    candidate_id: str
    status: str
    structural_profile_hash: str
    prediction_artifact_hash: str
    evaluation_artifact_hash: str | None
    primary_metric: float | None
    raw_p_value: float | None

    def __post_init__(self) -> None:
        _require_candidate_id(self.candidate_id)
        _require_hash(self.structural_profile_hash, "structural_profile_hash")
        _require_hash(self.prediction_artifact_hash, "prediction_artifact_hash")
        if self.status not in {"COMPLETED", "FAILED"}:
            raise RealDevelopmentCampaignIntegrityError("D3A candidate result status is invalid")
        if self.status == "COMPLETED":
            if self.evaluation_artifact_hash is None or self.primary_metric is None or self.raw_p_value is None:
                raise RealDevelopmentCampaignIntegrityError("completed D3A candidate result is incomplete")
            _require_hash(self.evaluation_artifact_hash, "evaluation_artifact_hash")
        else:
            if self.evaluation_artifact_hash is not None or self.primary_metric is not None or self.raw_p_value is not None:
                raise RealDevelopmentCampaignIntegrityError("failed D3A candidate cannot expose evaluation metrics")

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status,
            "structural_profile_hash": self.structural_profile_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "evaluation_artifact_hash": self.evaluation_artifact_hash,
            "primary_metric": self.primary_metric,
            "raw_p_value": self.raw_p_value,
        }


@dataclass(frozen=True, slots=True)
class RealDevelopmentCampaignEvidence:
    evidence_version: str
    status: str
    d2w_evidence_fingerprint: str
    research_split_hash: str
    raw_training_source_hash: str
    raw_development_source_hash: str
    d2r_receipt_hash: str
    training_bundle_hash: str
    training_feature_artifact_hash: str
    training_label_artifact_hash: str
    development_feature_artifact_hash: str
    d2f_plan_fingerprint: str
    d2f_request_set_fingerprint: str
    candidate_output_hashes: tuple[tuple[str, str], ...]
    structural_policy: str
    structural_profiles: tuple[PredictionStructuralProfile, ...]
    evaluable_candidate_ids: tuple[str, ...]
    d2s_preregistration_fingerprint: str | None
    development_label_artifact_hash: str | None
    d2h_preregistration_fingerprint: str | None
    d2h_batch_evidence_fingerprint: str | None
    d2s_completed_evidence_fingerprint: str | None
    d2e_tournament_evidence_fingerprint: str | None
    candidate_results: tuple[CandidateDevelopmentResult, ...]
    d2i_winner_seal_fingerprint: str | None
    selected_trial_id: str | None
    winner_primary_metric: float | None
    winner_raw_p_value: float | None
    winner_holm_adjusted_p_value: float | None
    predictions_frozen_before_development_labels: bool
    development_labels_materialized: bool
    development_metrics_computed: bool
    family_retuned: bool
    fallback_candidate_used: bool
    reselection_allowed: bool
    statistical_significance_claim_authorized: bool
    profitability_claim_authorized: bool
    final_holdout_observed: bool
    final_holdout_authorized: bool
    holdout_permit_consumed: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D3A_CAMPAIGN_EVIDENCE_VERSION:
            raise RealDevelopmentCampaignIntegrityError("noncanonical D3A campaign evidence version")
        if self.status not in {STATUS_COMPLETED, STATUS_PRELABEL_BLOCKED}:
            raise RealDevelopmentCampaignIntegrityError("D3A campaign status is invalid")
        for name in (
            "d2w_evidence_fingerprint",
            "research_split_hash",
            "raw_training_source_hash",
            "raw_development_source_hash",
            "d2r_receipt_hash",
            "training_bundle_hash",
            "training_feature_artifact_hash",
            "training_label_artifact_hash",
            "development_feature_artifact_hash",
            "d2f_plan_fingerprint",
            "d2f_request_set_fingerprint",
        ):
            _require_hash(getattr(self, name), name)
        expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
        if tuple(item[0] for item in self.candidate_output_hashes) != expected_ids:
            raise RealDevelopmentCampaignIntegrityError("D3A output family differs from exact D2F six")
        if tuple(profile.candidate_id for profile in self.structural_profiles) != expected_ids:
            raise RealDevelopmentCampaignIntegrityError("D3A structural profiles differ from exact D2F six")
        for _, digest in self.candidate_output_hashes:
            _require_hash(digest, "candidate_output_hash")
        if self.structural_policy != STRUCTURAL_POLICY:
            raise RealDevelopmentCampaignGovernanceError("D3A structural policy drifted")
        if self.evaluable_candidate_ids != tuple(
            profile.candidate_id for profile in self.structural_profiles if profile.evaluable
        ):
            raise RealDevelopmentCampaignIntegrityError("D3A evaluable candidate set is inconsistent")
        if not self.predictions_frozen_before_development_labels:
            raise RealDevelopmentCampaignGovernanceError("D3A must freeze predictions before DEVELOPMENT labels")
        if self.family_retuned or self.fallback_candidate_used or self.reselection_allowed:
            raise RealDevelopmentCampaignGovernanceError("D3A forbids retuning, fallback and reselection")
        if (
            self.statistical_significance_claim_authorized
            or self.profitability_claim_authorized
            or self.final_holdout_observed
            or self.final_holdout_authorized
            or self.holdout_permit_consumed
            or self.promotion_authorized
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise RealDevelopmentCampaignGovernanceError("D3A cannot escalate research authority")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise RealDevelopmentCampaignGovernanceError("D3A cannot grant capital or LIVE")

        if self.status == STATUS_PRELABEL_BLOCKED:
            if len(self.evaluable_candidate_ids) >= MIN_EVALUABLE_CANDIDATES:
                raise RealDevelopmentCampaignIntegrityError("PRELABEL_BLOCKED requires fewer than two evaluable candidates")
            if self.development_labels_materialized or self.development_metrics_computed:
                raise RealDevelopmentCampaignGovernanceError("PRELABEL_BLOCKED cannot materialize DEVELOPMENT labels/metrics")
            for value in (
                self.d2s_preregistration_fingerprint,
                self.development_label_artifact_hash,
                self.d2h_preregistration_fingerprint,
                self.d2h_batch_evidence_fingerprint,
                self.d2s_completed_evidence_fingerprint,
                self.d2e_tournament_evidence_fingerprint,
                self.d2i_winner_seal_fingerprint,
                self.selected_trial_id,
                self.winner_primary_metric,
                self.winner_raw_p_value,
                self.winner_holm_adjusted_p_value,
            ):
                if value is not None:
                    raise RealDevelopmentCampaignIntegrityError("PRELABEL_BLOCKED cannot expose post-label evidence")
            if self.candidate_results:
                raise RealDevelopmentCampaignIntegrityError("PRELABEL_BLOCKED cannot expose DEVELOPMENT candidate results")
        else:
            if len(self.evaluable_candidate_ids) < MIN_EVALUABLE_CANDIDATES:
                raise RealDevelopmentCampaignIntegrityError("COMPLETED requires at least two evaluable candidates")
            if not self.development_labels_materialized or not self.development_metrics_computed:
                raise RealDevelopmentCampaignIntegrityError("COMPLETED D3A campaign requires DEVELOPMENT evaluation")
            for name in (
                "d2s_preregistration_fingerprint",
                "development_label_artifact_hash",
                "d2h_preregistration_fingerprint",
                "d2h_batch_evidence_fingerprint",
                "d2s_completed_evidence_fingerprint",
                "d2e_tournament_evidence_fingerprint",
                "d2i_winner_seal_fingerprint",
            ):
                value = getattr(self, name)
                if value is None:
                    raise RealDevelopmentCampaignIntegrityError(f"COMPLETED D3A campaign lacks {name}")
                _require_hash(value, name)
            if self.selected_trial_id not in self.evaluable_candidate_ids:
                raise RealDevelopmentCampaignIntegrityError("D3A winner must be structurally evaluable")
            if len(self.candidate_results) != len(expected_ids):
                raise RealDevelopmentCampaignIntegrityError("D3A completed result must account for exact six candidates")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_version": self.evidence_version,
            "status": self.status,
            "d2w_evidence_fingerprint": self.d2w_evidence_fingerprint,
            "research_split_hash": self.research_split_hash,
            "raw_training_source_hash": self.raw_training_source_hash,
            "raw_development_source_hash": self.raw_development_source_hash,
            "d2r_receipt_hash": self.d2r_receipt_hash,
            "training_bundle_hash": self.training_bundle_hash,
            "training_feature_artifact_hash": self.training_feature_artifact_hash,
            "training_label_artifact_hash": self.training_label_artifact_hash,
            "development_feature_artifact_hash": self.development_feature_artifact_hash,
            "d2f_plan_fingerprint": self.d2f_plan_fingerprint,
            "d2f_request_set_fingerprint": self.d2f_request_set_fingerprint,
            "candidate_output_hashes": [list(item) for item in self.candidate_output_hashes],
            "structural_policy": self.structural_policy,
            "structural_profiles": [item.to_dict() for item in self.structural_profiles],
            "evaluable_candidate_ids": list(self.evaluable_candidate_ids),
            "d2s_preregistration_fingerprint": self.d2s_preregistration_fingerprint,
            "development_label_artifact_hash": self.development_label_artifact_hash,
            "d2h_preregistration_fingerprint": self.d2h_preregistration_fingerprint,
            "d2h_batch_evidence_fingerprint": self.d2h_batch_evidence_fingerprint,
            "d2s_completed_evidence_fingerprint": self.d2s_completed_evidence_fingerprint,
            "d2e_tournament_evidence_fingerprint": self.d2e_tournament_evidence_fingerprint,
            "candidate_results": [item.to_dict() for item in self.candidate_results],
            "d2i_winner_seal_fingerprint": self.d2i_winner_seal_fingerprint,
            "selected_trial_id": self.selected_trial_id,
            "winner_primary_metric": self.winner_primary_metric,
            "winner_raw_p_value": self.winner_raw_p_value,
            "winner_holm_adjusted_p_value": self.winner_holm_adjusted_p_value,
            "predictions_frozen_before_development_labels": self.predictions_frozen_before_development_labels,
            "development_labels_materialized": self.development_labels_materialized,
            "development_metrics_computed": self.development_metrics_computed,
            "family_retuned": self.family_retuned,
            "fallback_candidate_used": self.fallback_candidate_used,
            "reselection_allowed": self.reselection_allowed,
            "statistical_significance_claim_authorized": self.statistical_significance_claim_authorized,
            "profitability_claim_authorized": self.profitability_claim_authorized,
            "final_holdout_observed": self.final_holdout_observed,
            "final_holdout_authorized": self.final_holdout_authorized,
            "holdout_permit_consumed": self.holdout_permit_consumed,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


def prediction_structural_profile(output: FrozenCandidateOutput) -> PredictionStructuralProfile:
    if not isinstance(output, FrozenCandidateOutput):
        raise TypeError("output must be FrozenCandidateOutput")
    grouped: dict[str, list[float]] = {}
    all_scores: list[float] = []
    for row in output.prediction.rows:
        score = float(row.score)
        grouped.setdefault(row.timestamp, []).append(score)
        all_scores.append(score)
    if not grouped or not all_scores:
        raise RealDevelopmentCampaignIntegrityError("D3A prediction artifact contains no scores")
    expected_symbols = len({row.symbol for row in output.prediction.rows})
    nonconstant = 0
    for timestamp in sorted(grouped):
        scores = grouped[timestamp]
        if len(scores) != expected_symbols:
            raise RealDevelopmentCampaignIntegrityError("D3A prediction cross-section support is incomplete")
        if len(set(scores)) > 1:
            nonconstant += 1
    global_nonconstant = len(set(all_scores)) > 1
    full = nonconstant == len(grouped)
    evaluable = global_nonconstant and full
    return PredictionStructuralProfile(
        candidate_id=output.candidate_id,
        prediction_artifact_hash=output.prediction.artifact_hash,
        observation_count=len(all_scores),
        cross_section_count=len(grouped),
        nonconstant_cross_section_count=nonconstant,
        global_nonconstant=global_nonconstant,
        full_cross_section_support=full,
        evaluable=evaluable,
        failure_code=None if evaluable else STRUCTURAL_FAILURE_CODE,
    )


def run_real_development_campaign(
    *,
    evidence_root: str | Path,
    work_root: str | Path,
    now: datetime,
    repository_root: str | Path | None = None,
    expected_d2w_evidence_fingerprint: str | None = None,
) -> RealDevelopmentCampaignEvidence:
    """Run the exact frozen six-model DEVELOPMENT campaign on rehydrated real data."""
    root = Path(work_root)
    root.mkdir(parents=True, exist_ok=True)
    handoff = build_canonical_sealed_raw_split_handoff(
        evidence_root=evidence_root,
        now=now,
        repository_root=repository_root,
    )
    if expected_d2w_evidence_fingerprint is not None:
        _require_hash(expected_d2w_evidence_fingerprint, "expected_d2w_evidence_fingerprint")
        if handoff.evidence.fingerprint != expected_d2w_evidence_fingerprint:
            raise RealDevelopmentCampaignIntegrityError("D3A model phase D2W evidence differs from rehydration phase")

    train_features, train_labels, training_bundle, d2r_receipt = derive_raw_training_bundle(
        raw_source=handoff.training_source,
        campaign_id=REAL_CAMPAIGN_ID,
        research_split_hash=handoff.evidence.research_split_hash,
    )
    development_features = derive_raw_development_features(
        raw_source=handoff.development_source,
        campaign_id=REAL_CAMPAIGN_ID,
        research_split_hash=handoff.evidence.research_split_hash,
    )
    if development_features.manifest.feature_schema_hash != training_bundle.manifest.feature_schema_hash:
        raise RealDevelopmentCampaignIntegrityError("D3A TRAIN/DEVELOPMENT feature schema drifted")
    if development_features.manifest.source_universe_hash != training_bundle.manifest.source_universe_hash:
        raise RealDevelopmentCampaignIntegrityError("D3A TRAIN/DEVELOPMENT research universe identity drifted")

    d2f_plan, d2f_requests = build_concrete_model_request_set(
        training_bundle=training_bundle,
        development_features=development_features,
        shared_runner_code_hash=family_runner_code_hash(),
    )
    expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
    if tuple(candidate.candidate_id for candidate in d2f_plan.candidates) != expected_ids:
        raise RealDevelopmentCampaignIntegrityError("D3A D2F plan differs from exact frozen family")

    outputs = _run_exact_six_outputs(
        root=root / "candidates",
        d2f_requests=d2f_requests,
        training_bundle=training_bundle,
        train_features=train_features,
        train_labels=train_labels,
        development_features=development_features,
    )
    profiles = tuple(prediction_structural_profile(output) for output in outputs)
    evaluable_ids = tuple(profile.candidate_id for profile in profiles if profile.evaluable)
    output_hashes = tuple((output.candidate_id, output.fingerprint) for output in outputs)

    common = dict(
        evidence_version=OSS3D3A_CAMPAIGN_EVIDENCE_VERSION,
        d2w_evidence_fingerprint=handoff.evidence.fingerprint,
        research_split_hash=handoff.evidence.research_split_hash,
        raw_training_source_hash=handoff.training_source.source_hash,
        raw_development_source_hash=handoff.development_source.source_hash,
        d2r_receipt_hash=d2r_receipt.receipt_hash,
        training_bundle_hash=training_bundle.artifact_hash,
        training_feature_artifact_hash=train_features.artifact_hash,
        training_label_artifact_hash=train_labels.artifact_hash,
        development_feature_artifact_hash=development_features.artifact_hash,
        d2f_plan_fingerprint=d2f_plan.fingerprint,
        d2f_request_set_fingerprint=d2f_requests.fingerprint,
        candidate_output_hashes=output_hashes,
        structural_policy=STRUCTURAL_POLICY,
        structural_profiles=profiles,
        evaluable_candidate_ids=evaluable_ids,
        predictions_frozen_before_development_labels=True,
        family_retuned=False,
        fallback_candidate_used=False,
        reselection_allowed=False,
        statistical_significance_claim_authorized=False,
        profitability_claim_authorized=False,
        final_holdout_observed=False,
        final_holdout_authorized=False,
        holdout_permit_consumed=False,
        promotion_authorized=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )

    if len(evaluable_ids) < MIN_EVALUABLE_CANDIDATES:
        return RealDevelopmentCampaignEvidence(
            status=STATUS_PRELABEL_BLOCKED,
            d2s_preregistration_fingerprint=None,
            development_label_artifact_hash=None,
            d2h_preregistration_fingerprint=None,
            d2h_batch_evidence_fingerprint=None,
            d2s_completed_evidence_fingerprint=None,
            d2e_tournament_evidence_fingerprint=None,
            candidate_results=(),
            d2i_winner_seal_fingerprint=None,
            selected_trial_id=None,
            winner_primary_metric=None,
            winner_raw_p_value=None,
            winner_holm_adjusted_p_value=None,
            development_labels_materialized=False,
            development_metrics_computed=False,
            **common,
        )

    d2s = prepare_raw_development_preregistration(
        preregistration_id=D2S_PREREGISTRATION_ID,
        tournament_campaign_id=D2E_TOURNAMENT_CAMPAIGN_ID,
        tournament_id=D2E_TOURNAMENT_ID,
        d2f_plan=d2f_plan,
        d2f_request_set=d2f_requests,
        outputs=outputs,
        raw_source=handoff.development_source,
        development_features=development_features,
    )
    d2s_registry = SQLiteOSS3RawDevelopmentPreregistrationRegistry(root / "d2s-real.sqlite3")
    d2s_registry.preregister(d2s, now=now)
    d2s_registry.require_exact(d2s)

    development_labels, reveal = materialize_development_labels_after_preregistration(
        registry=d2s_registry,
        preregistration=d2s,
        raw_source=handoff.development_source,
        development_features=development_features,
    )
    d2h = prepare_d2h_from_d2s_reveal(
        registry=d2s_registry,
        preregistration=d2s,
        reveal=reveal,
        labels=development_labels,
        d2f_plan=d2f_plan,
        d2f_request_set=d2f_requests,
        outputs=outputs,
    )
    trial_ledger = SQLiteTrialLedger(root / "d2e-real-trials.sqlite3")
    preregister_family_evaluation(trial_ledger, d2h, now=now)

    evaluations: list[tuple[FrozenCandidateOutput, DevelopmentEvaluationArtifact]] = []
    candidate_results: list[CandidateDevelopmentResult] = []
    profile_by_id = {profile.candidate_id: profile for profile in profiles}
    prereg_binding_by_id = {binding.candidate_id: binding for binding in d2h.candidate_output_bindings}

    for offset, output in enumerate(outputs):
        profile = profile_by_id[output.candidate_id]
        current_binding = FrozenCandidateOutputBinding.from_output(output)
        if current_binding != prereg_binding_by_id[output.candidate_id]:
            raise RealDevelopmentCampaignIntegrityError("D3A output changed after D2H preregistration")
        timestamp = now + timedelta(microseconds=offset)
        if not profile.evaluable:
            record_oss3d2e_failure(
                trial_ledger,
                d2h.d2e_plan,
                trial_id=output.candidate_id,
                failure_code=STRUCTURAL_FAILURE_CODE,
                now=timestamp,
            )
            candidate_results.append(
                CandidateDevelopmentResult(
                    candidate_id=output.candidate_id,
                    status="FAILED",
                    structural_profile_hash=profile.fingerprint,
                    prediction_artifact_hash=output.prediction.artifact_hash,
                    evaluation_artifact_hash=None,
                    primary_metric=None,
                    raw_p_value=None,
                )
            )
            continue
        try:
            evaluation = evaluate_development_predictions(
                receipt=output.receipt,
                prediction=output.prediction,
                labels=development_labels,
                environment_attestation_hash=output.attestation.artifact_hash,
            )
        except DevelopmentEvaluationError as exc:
            raise RealDevelopmentCampaignIntegrityError(
                f"D3A pre-label structural gate admitted non-evaluable candidate {output.candidate_id}"
            ) from exc
        record = record_oss3d2e_evaluation(
            trial_ledger,
            d2h.d2e_plan,
            trial_id=output.candidate_id,
            evaluation=evaluation,
            receipt=output.receipt,
            now=timestamp,
        )
        evaluations.append((output, evaluation))
        metric = record.metrics.get("mean_cross_sectional_rank_ic")
        if isinstance(metric, bool) or not isinstance(metric, (int, float)) or record.p_value is None:
            raise RealDevelopmentCampaignIntegrityError("D3A completed candidate lacks preregistered metric/p-value")
        candidate_results.append(
            CandidateDevelopmentResult(
                candidate_id=output.candidate_id,
                status="COMPLETED",
                structural_profile_hash=profile.fingerprint,
                prediction_artifact_hash=output.prediction.artifact_hash,
                evaluation_artifact_hash=evaluation.artifact_hash,
                primary_metric=float(metric),
                raw_p_value=float(record.p_value),
            )
        )

    if len(evaluations) < MIN_EVALUABLE_CANDIDATES:
        raise RealDevelopmentCampaignIntegrityError("D3A structural accounting fell below minimum after label reveal")

    tournament = evaluate_oss3d2e_tournament(trial_ledger, d2h.d2e_plan)
    evaluation_bindings = tuple(
        CandidateEvaluationBinding(
            candidate_id=output.candidate_id,
            frozen_output_hash=output.fingerprint,
            request_hash=output.request.request_hash,
            prediction_artifact_hash=output.prediction.artifact_hash,
            receipt_hash=output.receipt.fingerprint,
            environment_attestation_hash=output.attestation.artifact_hash,
            d2g_run_evidence_hash=output.run_evidence_fingerprint,
            d2d_evaluation_artifact_hash=evaluation.artifact_hash,
        )
        for output, evaluation in evaluations
    )
    batch = FamilyEvaluationBatchEvidence(
        evidence_version="OSS3D2H_FAMILY_EVALUATION_BATCH_EVIDENCE_V1",
        preregistration_fingerprint=d2h.fingerprint,
        d2e_plan_fingerprint=d2h.d2e_plan.fingerprint,
        d2h_code_version=d2h.d2h_code_version,
        development_label_artifact_hash=development_labels.artifact_hash,
        shared_runner_code_hash=outputs[0].request.manifest.expected_runner_code_hash,
        runtime_environment_hash=outputs[0].runtime_environment.fingerprint,
        evaluations=evaluation_bindings,
        tournament_evidence=tournament,
    )
    completed_d2s: OSS3RawDevelopmentCompletedEvidence = bind_completed_raw_development_evidence(
        preregistration=d2s,
        reveal=reveal,
        d2h_preregistration=d2h,
        batch_evidence=batch,
    )
    winner: DevelopmentWinnerSelectionSeal = seal_development_winner(
        preregistration=d2h,
        batch_evidence=batch,
    )
    verify_development_winner_seal(
        seal=winner,
        preregistration=d2h,
        batch_evidence=batch,
    )

    result_by_id = {item.candidate_id: item for item in candidate_results}
    ordered_results = tuple(result_by_id[candidate_id] for candidate_id in expected_ids)
    return RealDevelopmentCampaignEvidence(
        status=STATUS_COMPLETED,
        d2s_preregistration_fingerprint=d2s.fingerprint,
        development_label_artifact_hash=development_labels.artifact_hash,
        d2h_preregistration_fingerprint=d2h.fingerprint,
        d2h_batch_evidence_fingerprint=batch.fingerprint,
        d2s_completed_evidence_fingerprint=completed_d2s.fingerprint,
        d2e_tournament_evidence_fingerprint=tournament.fingerprint,
        candidate_results=ordered_results,
        d2i_winner_seal_fingerprint=winner.fingerprint,
        selected_trial_id=winner.selected_trial_id,
        winner_primary_metric=winner.winner_primary_metric,
        winner_raw_p_value=winner.winner_raw_p_value,
        winner_holm_adjusted_p_value=winner.winner_holm_adjusted_p_value,
        development_labels_materialized=True,
        development_metrics_computed=True,
        **common,
    )


def write_real_development_campaign_evidence(
    evidence: RealDevelopmentCampaignEvidence,
    path: str | Path,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = evidence.to_dict()
    payload["fingerprint"] = evidence.fingerprint
    target.write_bytes(_canonical_json_bytes(payload) + b"\n")


def _run_exact_six_outputs(
    *,
    root: Path,
    d2f_requests,
    training_bundle,
    train_features,
    train_labels,
    development_features,
) -> tuple[FrozenCandidateOutput, ...]:
    outputs: list[FrozenCandidateOutput] = []
    for binding in d2f_requests.bindings:
        candidate_root = root / binding.candidate_id
        candidate_root.mkdir(parents=True, exist_ok=True)
        paths = {
            "request": candidate_root / "request.json",
            "bundle": candidate_root / "training-bundle.json",
            "train_features": candidate_root / "train-features.json",
            "train_labels": candidate_root / "train-labels.json",
            "development_features": candidate_root / "development-features.json",
            "prediction": candidate_root / "prediction.json",
            "receipt": candidate_root / "receipt.json",
            "attestation": candidate_root / "attestation.json",
            "runtime": candidate_root / "runtime.json",
            "evidence": candidate_root / "run-evidence.json",
        }
        binding.request.write(paths["request"])
        training_bundle.write(paths["bundle"])
        train_features.write(paths["train_features"])
        train_labels.write(paths["train_labels"])
        development_features.write(paths["development_features"])
        run_evidence = run_isolated_qlib_family_candidate(
            request_path=paths["request"],
            training_bundle_path=paths["bundle"],
            train_features_path=paths["train_features"],
            train_labels_path=paths["train_labels"],
            development_features_path=paths["development_features"],
            prediction_output_path=paths["prediction"],
            receipt_output_path=paths["receipt"],
            environment_attestation_output_path=paths["attestation"],
            runtime_identity_output_path=paths["runtime"],
            run_evidence_output_path=paths["evidence"],
        )
        prediction = QlibPredictionArtifact.read(paths["prediction"])
        receipt = binding.request.bind_prediction(
            prediction=prediction,
            training_bundle=training_bundle,
            development_features=development_features,
        )
        attestation = CandidateEnvironmentAttestation.read(paths["attestation"])
        outputs.append(
            FrozenCandidateOutput(
                candidate_id=binding.candidate_id,
                request=binding.request,
                prediction=prediction,
                receipt=receipt,
                attestation=attestation,
                run_evidence=run_evidence,
            )
        )
    result = tuple(outputs)
    expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
    if tuple(item.candidate_id for item in result) != expected_ids:
        raise RealDevelopmentCampaignIntegrityError("D3A runner did not execute exact canonical family order")
    return result


def _require_candidate_id(value: object) -> None:
    expected = {candidate.candidate_id for candidate in CANONICAL_CANDIDATES}
    if not isinstance(value, str) or value not in expected:
        raise RealDevelopmentCampaignIntegrityError("D3A candidate id is outside frozen D2F family")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise RealDevelopmentCampaignIntegrityError(f"D3A {name} must be lowercase SHA-256")
