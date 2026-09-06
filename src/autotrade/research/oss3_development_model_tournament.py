"""OSS-3D2E preregistered DEVELOPMENT model tournament.

D2E is downstream of OSS-3D2D. It freezes a finite model family before any
candidate result is recorded, ingests only immutable D2D DEVELOPMENT
evaluations that exactly match their preregistered identity, and reuses the
canonical TrialLedger/Tournament machinery for deterministic ranking.

Scientific/governance boundary:
- DEVELOPMENT only; FINAL_HOLDOUT is structurally forbidden;
- the complete candidate universe is frozen in CampaignSpec before results;
- one primary metric is fixed: mean_cross_sectional_rank_ic / MAXIMIZE;
- exact metric ties use the existing immutable Tournament identity tie-break;
- auxiliary D2D metrics are diagnostic and cannot change ranking;
- the primary metric is recomputed from D2D cross-sections before ingestion;
- one-sided exact sign-test p-values over cross-sectional rank-IC signs are
  recorded per candidate and Holm-adjusted over the complete frozen family;
- all completed candidates must use identical cross-sectional timestamp support;
- no model execution, Qlib runtime, tuning loop, FINAL_HOLDOUT access, PnL,
  Sharpe, broker, OMS, Safety, OrderIntent, PAPER, capital or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from math import comb, isfinite
import re
from typing import Iterable

from .multiple_testing import HolmEvidence, campaign_holm_evidence
from .oss3_development_evaluation import DevelopmentEvaluationArtifact, METRIC_POLICY_ID
from .oss3_development_inference import DevelopmentPredictionReceipt
from .tournament import (
    RankingDirection,
    TournamentEvidence,
    TournamentSpec,
    evaluate_strategy_tournament,
)
from .trials import CampaignSpec, SQLiteTrialLedger, TrialPhase, TrialSpec, TrialStatus


OSS3D2E_PLAN_VERSION = "OSS3D2E_DEVELOPMENT_MODEL_TOURNAMENT_PLAN_V1"
OSS3D2E_EVIDENCE_VERSION = "OSS3D2E_DEVELOPMENT_MODEL_TOURNAMENT_EVIDENCE_V1"
OSS3D2E_FAMILY_ID = "OSS3D2E_QLIB_DEVELOPMENT_MODEL_FAMILY_V1"
OSS3D2E_PURPOSE = "preregistered DEVELOPMENT predictive-model comparison only"
PRIMARY_METRIC = "mean_cross_sectional_rank_ic"
PRIMARY_DIRECTION = RankingDirection.MAXIMIZE
MULTIPLE_TESTING_POLICY = "EXACT_SIGN_TEST_PLUS_HOLM_V1"
COMMON_SUPPORT_POLICY = "EXACT_CROSS_SECTION_TIMESTAMP_SUPPORT_V1"
MIN_CANDIDATES = 2
MAX_CANDIDATES = 32

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


class DevelopmentModelTournamentError(RuntimeError):
    """Base OSS-3D2E failure."""


class DevelopmentModelTournamentIntegrityError(DevelopmentModelTournamentError):
    """Immutable lineage or preregistered identity does not match."""


class DevelopmentModelTournamentGovernanceError(DevelopmentModelTournamentError):
    """The requested operation violates DEVELOPMENT-only governance."""


class DevelopmentModelTournamentCompatibilityError(DevelopmentModelTournamentError):
    """A D2D evaluation is incompatible with its frozen candidate family."""


@dataclass(frozen=True, slots=True)
class DevelopmentDatasetBinding:
    source_campaign_id: str
    research_split_hash: str
    source_universe_hash: str
    label_definition_hash: str
    development_label_artifact_hash: str
    evaluation_keyset_hash: str
    evaluation_start: str
    evaluation_end: str

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.source_campaign_id):
            raise ValueError("invalid source_campaign_id")
        for name in (
            "research_split_hash",
            "source_universe_hash",
            "label_definition_hash",
            "development_label_artifact_hash",
            "evaluation_keyset_hash",
        ):
            _require_hash(getattr(self, name), name)
        start = _parse_canonical_utc(self.evaluation_start, "evaluation_start")
        end = _parse_canonical_utc(self.evaluation_end, "evaluation_end")
        if not start < end:
            raise ValueError("evaluation window must be positive")

    @property
    def dataset_hash(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "source_campaign_id": self.source_campaign_id,
            "research_split_hash": self.research_split_hash,
            "source_universe_hash": self.source_universe_hash,
            "label_definition_hash": self.label_definition_hash,
            "development_label_artifact_hash": self.development_label_artifact_hash,
            "evaluation_keyset_hash": self.evaluation_keyset_hash,
            "evaluation_start": self.evaluation_start,
            "evaluation_end": self.evaluation_end,
        }


@dataclass(frozen=True, slots=True)
class DevelopmentModelCandidate:
    trial_id: str
    hypothesis_id: str
    model_family: str
    model_config_hash: str
    request_hash: str
    qlib_version: str
    expected_runner_code_hash: str
    environment_attestation_hash: str

    def __post_init__(self) -> None:
        for name in ("trial_id", "hypothesis_id", "model_family"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _ID_RE.fullmatch(value):
                raise ValueError(f"invalid {name}")
        for name in (
            "model_config_hash",
            "request_hash",
            "expected_runner_code_hash",
            "environment_attestation_hash",
        ):
            _require_hash(getattr(self, name), name)
        if not _VERSION_RE.fullmatch(self.qlib_version):
            raise ValueError("invalid qlib_version")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    @property
    def model_identity(self) -> tuple[str, str]:
        return (self.model_family, self.model_config_hash)

    def to_dict(self) -> dict[str, object]:
        return {
            "trial_id": self.trial_id,
            "hypothesis_id": self.hypothesis_id,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "request_hash": self.request_hash,
            "qlib_version": self.qlib_version,
            "expected_runner_code_hash": self.expected_runner_code_hash,
            "environment_attestation_hash": self.environment_attestation_hash,
        }


@dataclass(frozen=True, slots=True)
class OSS3D2EPlan:
    plan_version: str
    campaign: CampaignSpec
    dataset: DevelopmentDatasetBinding
    candidates: tuple[DevelopmentModelCandidate, ...]
    trials: tuple[TrialSpec, ...]
    tournament: TournamentSpec
    primary_metric: str = PRIMARY_METRIC
    multiple_testing_policy: str = MULTIPLE_TESTING_POLICY
    common_support_policy: str = COMMON_SUPPORT_POLICY
    final_holdout_observable: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.plan_version != OSS3D2E_PLAN_VERSION:
            raise DevelopmentModelTournamentIntegrityError("noncanonical D2E plan version")
        if self.primary_metric != PRIMARY_METRIC:
            raise DevelopmentModelTournamentGovernanceError("D2E primary metric is frozen")
        if self.multiple_testing_policy != MULTIPLE_TESTING_POLICY:
            raise DevelopmentModelTournamentGovernanceError("noncanonical multiple-testing policy")
        if self.common_support_policy != COMMON_SUPPORT_POLICY:
            raise DevelopmentModelTournamentGovernanceError("noncanonical common-support policy")
        if self.final_holdout_observable:
            raise DevelopmentModelTournamentGovernanceError("D2E may not observe FINAL_HOLDOUT")
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
        )
        if self.campaign.family_id != OSS3D2E_FAMILY_ID:
            raise DevelopmentModelTournamentIntegrityError("campaign family drifted")
        if self.campaign.purpose != OSS3D2E_PURPOSE:
            raise DevelopmentModelTournamentIntegrityError("campaign purpose drifted")
        candidate_ids = tuple(candidate.trial_id for candidate in self.candidates)
        if not MIN_CANDIDATES <= len(candidate_ids) <= MAX_CANDIDATES:
            raise DevelopmentModelTournamentGovernanceError("candidate family size outside D2E bounds")
        if candidate_ids != tuple(sorted(candidate_ids)) or len(set(candidate_ids)) != len(candidate_ids):
            raise DevelopmentModelTournamentIntegrityError("candidates must be unique canonical order")
        if len({candidate.model_identity for candidate in self.candidates}) != len(self.candidates):
            raise DevelopmentModelTournamentGovernanceError("duplicate substantive model identity")
        if len({candidate.request_hash for candidate in self.candidates}) != len(self.candidates):
            raise DevelopmentModelTournamentGovernanceError("duplicate inference request identity")
        if self.campaign.expected_trial_ids != candidate_ids:
            raise DevelopmentModelTournamentIntegrityError("campaign universe differs from candidates")
        trial_ids = tuple(trial.trial_id for trial in self.trials)
        if trial_ids != candidate_ids:
            raise DevelopmentModelTournamentIntegrityError("trial universe differs from candidates")
        if self.tournament.candidate_trial_ids != candidate_ids:
            raise DevelopmentModelTournamentIntegrityError("tournament universe differs from candidates")
        if self.tournament.campaign_id != self.campaign.campaign_id:
            raise DevelopmentModelTournamentIntegrityError("tournament campaign mismatch")
        if self.tournament.metric_name != PRIMARY_METRIC or self.tournament.direction is not PRIMARY_DIRECTION:
            raise DevelopmentModelTournamentGovernanceError("tournament ranking policy drifted")
        for trial, candidate in zip(self.trials, self.candidates, strict=True):
            _verify_trial_matches_candidate(trial, candidate, self)

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def candidate(self, trial_id: str) -> DevelopmentModelCandidate:
        for candidate in self.candidates:
            if candidate.trial_id == trial_id:
                return candidate
        raise DevelopmentModelTournamentGovernanceError("trial_id is outside frozen D2E family")

    def trial(self, trial_id: str) -> TrialSpec:
        for trial in self.trials:
            if trial.trial_id == trial_id:
                return trial
        raise DevelopmentModelTournamentGovernanceError("trial_id is outside frozen D2E family")

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_version": self.plan_version,
            "campaign_fingerprint": self.campaign.fingerprint,
            "dataset": self.dataset.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "trial_fingerprints": [trial.fingerprint for trial in self.trials],
            "tournament_fingerprint": self.tournament.fingerprint,
            "primary_metric": self.primary_metric,
            "multiple_testing_policy": self.multiple_testing_policy,
            "common_support_policy": self.common_support_policy,
            "final_holdout_observable": self.final_holdout_observable,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class OSS3D2ETournamentEvidence:
    evidence_version: str
    plan_fingerprint: str
    tournament: TournamentEvidence
    holm: HolmEvidence
    family_size: int
    winner_trial_id: str
    winner_primary_metric: float
    winner_raw_p_value: float
    winner_holm_adjusted_p_value: float
    common_cross_section_key_hash: str
    research_only: bool = True
    final_holdout_observed: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D2E_EVIDENCE_VERSION:
            raise DevelopmentModelTournamentIntegrityError("noncanonical D2E evidence version")
        _require_hash(self.plan_fingerprint, "plan_fingerprint")
        _require_hash(self.common_cross_section_key_hash, "common_cross_section_key_hash")
        if not MIN_CANDIDATES <= self.family_size <= MAX_CANDIDATES:
            raise DevelopmentModelTournamentIntegrityError("invalid evidence family_size")
        if not self.winner_trial_id:
            raise DevelopmentModelTournamentGovernanceError("D2E requires an eligible winner")
        for name in ("winner_primary_metric", "winner_raw_p_value", "winner_holm_adjusted_p_value"):
            if not isfinite(float(getattr(self, name))):
                raise DevelopmentModelTournamentIntegrityError(f"{name} must be finite")
        if not 0.0 <= self.winner_raw_p_value <= 1.0:
            raise DevelopmentModelTournamentIntegrityError("winner_raw_p_value outside [0,1]")
        if not 0.0 <= self.winner_holm_adjusted_p_value <= 1.0:
            raise DevelopmentModelTournamentIntegrityError("winner adjusted p-value outside [0,1]")
        if not self.research_only or self.final_holdout_observed or self.promotion_authorized:
            raise DevelopmentModelTournamentGovernanceError("D2E evidence cannot promote or observe HOLDOUT")
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
        )
        if self.tournament.winner_trial_id != self.winner_trial_id:
            raise DevelopmentModelTournamentIntegrityError("winner identity mismatch")
        if self.holm.family_size != self.family_size:
            raise DevelopmentModelTournamentIntegrityError("Holm family size mismatch")
        if self.holm.campaign_id != self.tournament.campaign_id:
            raise DevelopmentModelTournamentIntegrityError("Holm campaign mismatch")
        if self.holm.raw_p_values.get(self.winner_trial_id) != self.winner_raw_p_value:
            raise DevelopmentModelTournamentIntegrityError("winner raw p-value mismatch")
        if self.holm.adjusted_p_values.get(self.winner_trial_id) != self.winner_holm_adjusted_p_value:
            raise DevelopmentModelTournamentIntegrityError("winner Holm p-value mismatch")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_version": self.evidence_version,
            "plan_fingerprint": self.plan_fingerprint,
            "tournament": self.tournament.to_payload(),
            "holm": {
                "campaign_id": self.holm.campaign_id,
                "family_size": self.holm.family_size,
                "raw_p_values": dict(sorted(self.holm.raw_p_values.items())),
                "adjusted_p_values": dict(sorted(self.holm.adjusted_p_values.items())),
                "failed_trial_ids": list(self.holm.failed_trial_ids),
            },
            "family_size": self.family_size,
            "winner_trial_id": self.winner_trial_id,
            "winner_primary_metric": self.winner_primary_metric,
            "winner_raw_p_value": self.winner_raw_p_value,
            "winner_holm_adjusted_p_value": self.winner_holm_adjusted_p_value,
            "common_cross_section_key_hash": self.common_cross_section_key_hash,
            "research_only": self.research_only,
            "final_holdout_observed": self.final_holdout_observed,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


def build_oss3d2e_plan(
    *,
    tournament_campaign_id: str,
    tournament_id: str,
    dataset: DevelopmentDatasetBinding,
    candidates: Iterable[DevelopmentModelCandidate],
    code_version: str,
) -> OSS3D2EPlan:
    """Freeze the entire DEVELOPMENT candidate universe before results exist."""
    if not _ID_RE.fullmatch(tournament_campaign_id):
        raise ValueError("invalid tournament_campaign_id")
    if not _ID_RE.fullmatch(tournament_id):
        raise ValueError("invalid tournament_id")
    if not isinstance(dataset, DevelopmentDatasetBinding):
        raise TypeError("dataset must be DevelopmentDatasetBinding")
    _require_hash(code_version, "code_version")
    candidate_tuple = tuple(sorted(tuple(candidates), key=lambda item: item.trial_id))
    if not MIN_CANDIDATES <= len(candidate_tuple) <= MAX_CANDIDATES:
        raise DevelopmentModelTournamentGovernanceError("candidate family size outside D2E bounds")
    if len({candidate.trial_id for candidate in candidate_tuple}) != len(candidate_tuple):
        raise DevelopmentModelTournamentGovernanceError("duplicate candidate trial_id")
    if len({candidate.model_identity for candidate in candidate_tuple}) != len(candidate_tuple):
        raise DevelopmentModelTournamentGovernanceError("duplicate substantive model identity")
    if len({candidate.request_hash for candidate in candidate_tuple}) != len(candidate_tuple):
        raise DevelopmentModelTournamentGovernanceError("duplicate inference request identity")
    if len({candidate.qlib_version for candidate in candidate_tuple}) != 1:
        raise DevelopmentModelTournamentCompatibilityError("all candidates must use one Qlib version")
    if len({candidate.expected_runner_code_hash for candidate in candidate_tuple}) != 1:
        raise DevelopmentModelTournamentCompatibilityError("all candidates must use one runner code hash")
    if len({candidate.environment_attestation_hash for candidate in candidate_tuple}) != 1:
        raise DevelopmentModelTournamentCompatibilityError("all candidates must use one environment attestation")

    trial_ids = tuple(candidate.trial_id for candidate in candidate_tuple)
    campaign = CampaignSpec(
        campaign_id=tournament_campaign_id,
        family_id=OSS3D2E_FAMILY_ID,
        expected_trial_ids=trial_ids,
        code_version=code_version,
        purpose=OSS3D2E_PURPOSE,
    )
    trials = tuple(
        TrialSpec(
            trial_id=candidate.trial_id,
            campaign_id=tournament_campaign_id,
            hypothesis_id=candidate.hypothesis_id,
            strategy_id=f"qlib:{candidate.model_family}",
            strategy_version=candidate.model_config_hash,
            dataset_hash=dataset.dataset_hash,
            split_name="DEVELOPMENT",
            phase=TrialPhase.DEVELOPMENT,
            parameters={
                "source_campaign_id": dataset.source_campaign_id,
                "research_split_hash": dataset.research_split_hash,
                "source_universe_hash": dataset.source_universe_hash,
                "label_definition_hash": dataset.label_definition_hash,
                "development_label_artifact_hash": dataset.development_label_artifact_hash,
                "evaluation_keyset_hash": dataset.evaluation_keyset_hash,
                "evaluation_start": dataset.evaluation_start,
                "evaluation_end": dataset.evaluation_end,
                "model_family": candidate.model_family,
                "model_config_hash": candidate.model_config_hash,
                "request_hash": candidate.request_hash,
                "qlib_version": candidate.qlib_version,
                "expected_runner_code_hash": candidate.expected_runner_code_hash,
                "environment_attestation_hash": candidate.environment_attestation_hash,
                "metric_policy_id": METRIC_POLICY_ID,
                "primary_metric": PRIMARY_METRIC,
                "multiple_testing_policy": MULTIPLE_TESTING_POLICY,
                "common_support_policy": COMMON_SUPPORT_POLICY,
            },
            code_version=code_version,
        )
        for candidate in candidate_tuple
    )
    tournament = TournamentSpec(
        tournament_id=tournament_id,
        campaign_id=tournament_campaign_id,
        metric_name=PRIMARY_METRIC,
        direction=PRIMARY_DIRECTION,
        candidate_trial_ids=trial_ids,
    )
    return OSS3D2EPlan(
        plan_version=OSS3D2E_PLAN_VERSION,
        campaign=campaign,
        dataset=dataset,
        candidates=candidate_tuple,
        trials=trials,
        tournament=tournament,
    )


def preregister_oss3d2e_plan(
    ledger: SQLiteTrialLedger,
    plan: OSS3D2EPlan,
    *,
    now: datetime,
) -> None:
    """Durably create the frozen campaign and preregister every candidate."""
    if not isinstance(plan, OSS3D2EPlan):
        raise TypeError("plan must be OSS3D2EPlan")
    ledger.create_campaign(plan.campaign, now=now)
    for trial in plan.trials:
        ledger.preregister(trial, now=now)


def record_oss3d2e_evaluation(
    ledger: SQLiteTrialLedger,
    plan: OSS3D2EPlan,
    *,
    trial_id: str,
    evaluation: DevelopmentEvaluationArtifact,
    receipt: DevelopmentPredictionReceipt,
    now: datetime,
):
    """Bind one immutable D2D evaluation to its preregistered candidate."""
    candidate = plan.candidate(trial_id)
    frozen_trial = plan.trial(trial_id)
    record = ledger.get_trial(trial_id)
    if record is None:
        raise DevelopmentModelTournamentGovernanceError("candidate was not preregistered")
    if record.spec.fingerprint != frozen_trial.fingerprint:
        raise DevelopmentModelTournamentIntegrityError("durable trial differs from frozen D2E plan")
    if record.status.terminal:
        raise DevelopmentModelTournamentGovernanceError("candidate result is already terminal")
    _verify_evaluation_binding(plan, candidate, evaluation, receipt)

    rank_ics = tuple(float(item.spearman_ic) for item in evaluation.cross_sections)
    raw_p = _one_sided_exact_sign_test(rank_ics)
    metrics = {
        PRIMARY_METRIC: float(evaluation.metrics.mean_cross_sectional_rank_ic),
        "mean_cross_sectional_ic": float(evaluation.metrics.mean_cross_sectional_ic),
        "global_spearman_ic": float(evaluation.metrics.spearman_ic),
        "global_pearson_ic": float(evaluation.metrics.pearson_ic),
        "sign_accuracy": float(evaluation.metrics.sign_accuracy),
        "mae": float(evaluation.metrics.mae),
        "rmse": float(evaluation.metrics.rmse),
        "cross_section_count": evaluation.metrics.cross_section_count,
        "cross_section_key_hash": _cross_section_key_hash(evaluation),
        "evaluation_artifact_hash": evaluation.artifact_hash,
        "prediction_receipt_hash": receipt.fingerprint,
        "environment_attestation_hash": evaluation.manifest.environment_attestation_hash,
    }
    return ledger.record_completed(
        trial_id=trial_id,
        metrics=metrics,
        p_value=Decimal(str(raw_p)),
        now=now,
    )


def record_oss3d2e_failure(
    ledger: SQLiteTrialLedger,
    plan: OSS3D2EPlan,
    *,
    trial_id: str,
    failure_code: str,
    now: datetime,
):
    """Record an explicit terminal candidate failure without fabricating metrics."""
    plan.candidate(trial_id)
    frozen_trial = plan.trial(trial_id)
    record = ledger.get_trial(trial_id)
    if record is None:
        raise DevelopmentModelTournamentGovernanceError("candidate was not preregistered")
    if record.spec.fingerprint != frozen_trial.fingerprint:
        raise DevelopmentModelTournamentIntegrityError("durable trial differs from frozen D2E plan")
    if record.status.terminal:
        raise DevelopmentModelTournamentGovernanceError("candidate result is already terminal")
    return ledger.record_failed(trial_id=trial_id, failure_code=failure_code, now=now)


def evaluate_oss3d2e_tournament(
    ledger: SQLiteTrialLedger,
    plan: OSS3D2EPlan,
) -> OSS3D2ETournamentEvidence:
    """Rank the complete frozen family and attach Holm-adjusted sign evidence."""
    accounting = ledger.require_complete_campaign(plan.campaign.campaign_id)
    if accounting.expected_trial_ids != plan.campaign.expected_trial_ids:
        raise DevelopmentModelTournamentIntegrityError("durable campaign universe drifted")
    records = {record.spec.trial_id: record for record in ledger.list_trials(plan.campaign.campaign_id)}
    completed = [records[trial_id] for trial_id in accounting.completed_trial_ids]
    if not completed:
        raise DevelopmentModelTournamentGovernanceError("no completed D2E candidate exists")

    support_hashes: set[str] = set()
    support_counts: set[int] = set()
    for record in completed:
        support_hash = record.metrics.get("cross_section_key_hash")
        support_count = record.metrics.get("cross_section_count")
        if not isinstance(support_hash, str):
            raise DevelopmentModelTournamentIntegrityError("completed candidate lacks support hash")
        _require_hash(support_hash, "cross_section_key_hash")
        if not isinstance(support_count, int) or isinstance(support_count, bool) or support_count < 1:
            raise DevelopmentModelTournamentIntegrityError("completed candidate lacks support count")
        support_hashes.add(support_hash)
        support_counts.add(support_count)
    if len(support_hashes) != 1 or len(support_counts) != 1:
        raise DevelopmentModelTournamentCompatibilityError(
            "completed candidates do not share exact cross-sectional support"
        )

    tournament = evaluate_strategy_tournament(ledger, plan.tournament)
    if not tournament.winner_trial_id:
        raise DevelopmentModelTournamentGovernanceError("tournament produced no eligible winner")
    holm = campaign_holm_evidence(ledger, plan.campaign.campaign_id)
    winner = records[tournament.winner_trial_id]
    winner_metric = winner.metrics.get(PRIMARY_METRIC)
    if isinstance(winner_metric, bool) or not isinstance(winner_metric, (int, float)):
        raise DevelopmentModelTournamentIntegrityError("winner lacks primary metric")
    if winner.p_value is None:
        raise DevelopmentModelTournamentIntegrityError("winner lacks preregistered p-value")
    return OSS3D2ETournamentEvidence(
        evidence_version=OSS3D2E_EVIDENCE_VERSION,
        plan_fingerprint=plan.fingerprint,
        tournament=tournament,
        holm=holm,
        family_size=len(plan.candidates),
        winner_trial_id=tournament.winner_trial_id,
        winner_primary_metric=float(winner_metric),
        winner_raw_p_value=float(winner.p_value),
        winner_holm_adjusted_p_value=float(holm.adjusted_p_values[tournament.winner_trial_id]),
        common_cross_section_key_hash=next(iter(support_hashes)),
    )


def _verify_trial_matches_candidate(
    trial: TrialSpec,
    candidate: DevelopmentModelCandidate,
    plan: OSS3D2EPlan,
) -> None:
    if trial.phase is not TrialPhase.DEVELOPMENT or trial.split_name != "DEVELOPMENT":
        raise DevelopmentModelTournamentGovernanceError("D2E trials must be DEVELOPMENT only")
    if trial.campaign_id != plan.campaign.campaign_id:
        raise DevelopmentModelTournamentIntegrityError("trial campaign mismatch")
    if trial.dataset_hash != plan.dataset.dataset_hash:
        raise DevelopmentModelTournamentIntegrityError("trial dataset binding mismatch")
    if trial.strategy_id != f"qlib:{candidate.model_family}":
        raise DevelopmentModelTournamentIntegrityError("trial model family mismatch")
    if trial.strategy_version != candidate.model_config_hash:
        raise DevelopmentModelTournamentIntegrityError("trial model config mismatch")
    if trial.code_version != plan.campaign.code_version:
        raise DevelopmentModelTournamentIntegrityError("trial code version mismatch")
    expected = {
        "source_campaign_id": plan.dataset.source_campaign_id,
        "research_split_hash": plan.dataset.research_split_hash,
        "source_universe_hash": plan.dataset.source_universe_hash,
        "label_definition_hash": plan.dataset.label_definition_hash,
        "development_label_artifact_hash": plan.dataset.development_label_artifact_hash,
        "evaluation_keyset_hash": plan.dataset.evaluation_keyset_hash,
        "evaluation_start": plan.dataset.evaluation_start,
        "evaluation_end": plan.dataset.evaluation_end,
        "model_family": candidate.model_family,
        "model_config_hash": candidate.model_config_hash,
        "request_hash": candidate.request_hash,
        "qlib_version": candidate.qlib_version,
        "expected_runner_code_hash": candidate.expected_runner_code_hash,
        "environment_attestation_hash": candidate.environment_attestation_hash,
        "metric_policy_id": METRIC_POLICY_ID,
        "primary_metric": PRIMARY_METRIC,
        "multiple_testing_policy": MULTIPLE_TESTING_POLICY,
        "common_support_policy": COMMON_SUPPORT_POLICY,
    }
    if dict(trial.parameters) != expected:
        raise DevelopmentModelTournamentIntegrityError("trial parameter surface drifted")


def _verify_evaluation_binding(
    plan: OSS3D2EPlan,
    candidate: DevelopmentModelCandidate,
    evaluation: DevelopmentEvaluationArtifact,
    receipt: DevelopmentPredictionReceipt,
) -> None:
    if not isinstance(evaluation, DevelopmentEvaluationArtifact):
        raise TypeError("evaluation must be DevelopmentEvaluationArtifact")
    if not isinstance(receipt, DevelopmentPredictionReceipt):
        raise TypeError("receipt must be DevelopmentPredictionReceipt")
    m = evaluation.manifest
    d = plan.dataset
    for name, expected, actual in (
        ("source campaign", d.source_campaign_id, m.campaign_id),
        ("research split", d.research_split_hash, m.research_split_hash),
        ("source universe", d.source_universe_hash, m.source_universe_hash),
        ("label definition", d.label_definition_hash, m.label_definition_hash),
        ("DEVELOPMENT label artifact", d.development_label_artifact_hash, m.development_label_artifact_hash),
        ("evaluation keyset", d.evaluation_keyset_hash, m.evaluation_keyset_hash),
        ("evaluation start", d.evaluation_start, m.evaluation_start),
        ("evaluation end", d.evaluation_end, m.evaluation_end),
        ("model family", candidate.model_family, m.model_family),
        ("model config", candidate.model_config_hash, m.model_config_hash),
        ("Qlib version", candidate.qlib_version, m.qlib_version),
        ("runner code", candidate.expected_runner_code_hash, m.producer_code_hash),
        ("environment attestation", candidate.environment_attestation_hash, m.environment_attestation_hash),
        ("prediction receipt", receipt.fingerprint, m.prediction_receipt_hash),
        ("request hash", candidate.request_hash, receipt.request_hash),
        ("receipt campaign", d.source_campaign_id, receipt.campaign_id),
        ("receipt split", d.research_split_hash, receipt.research_split_hash),
        ("receipt universe", d.source_universe_hash, receipt.source_universe_hash),
        ("receipt label definition", d.label_definition_hash, receipt.label_definition_hash),
        ("receipt model family", candidate.model_family, receipt.model_family),
        ("receipt model config", candidate.model_config_hash, receipt.model_config_hash),
        ("receipt Qlib version", candidate.qlib_version, receipt.qlib_version),
        ("receipt runner code", candidate.expected_runner_code_hash, receipt.producer_code_hash),
        ("receipt inference start", d.evaluation_start, receipt.inference_start),
        ("receipt inference end", d.evaluation_end, receipt.inference_end),
        ("receipt/evaluation observations", receipt.prediction_count, m.observation_count),
    ):
        if expected != actual:
            raise DevelopmentModelTournamentCompatibilityError(f"{name} mismatch")
    if m.metric_policy_id != METRIC_POLICY_ID:
        raise DevelopmentModelTournamentGovernanceError("D2D metric policy drifted")
    if evaluation.metrics.cross_section_count != len(evaluation.cross_sections):
        raise DevelopmentModelTournamentIntegrityError("D2D cross-section count mismatch")
    rank_ics = tuple(float(item.spearman_ic) for item in evaluation.cross_sections)
    if not rank_ics:
        raise DevelopmentModelTournamentGovernanceError("D2D has no cross-sectional rank IC evidence")
    if any(not isfinite(value) for value in rank_ics):
        raise DevelopmentModelTournamentIntegrityError("primary rank-IC evidence must be finite")
    recomputed_primary = sum(rank_ics) / len(rank_ics)
    if float(evaluation.metrics.mean_cross_sectional_rank_ic) != recomputed_primary:
        raise DevelopmentModelTournamentIntegrityError("D2D primary metric does not rebind to cross-sections")


def _one_sided_exact_sign_test(values: tuple[float, ...]) -> float:
    """Return P[X >= observed positives], X~Binomial(n_nonzero, 0.5)."""
    if not values:
        raise DevelopmentModelTournamentGovernanceError("sign test requires cross-sectional evidence")
    positives = 0
    negatives = 0
    for value in values:
        if not isfinite(value):
            raise DevelopmentModelTournamentIntegrityError("rank IC sign evidence must be finite")
        if value > 0.0:
            positives += 1
        elif value < 0.0:
            negatives += 1
    n = positives + negatives
    if n == 0:
        return 1.0
    return sum(comb(n, k) for k in range(positives, n + 1)) / float(2**n)


def _cross_section_key_hash(evaluation: DevelopmentEvaluationArtifact) -> str:
    timestamps = [item.timestamp for item in evaluation.cross_sections]
    if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
        raise DevelopmentModelTournamentIntegrityError("D2D cross-section timestamps are not canonical")
    for timestamp in timestamps:
        _parse_canonical_utc(timestamp, "cross-section timestamp")
    return _hash(timestamps)


def _deny_authority(
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise DevelopmentModelTournamentGovernanceError("D2E cannot authorize execution")
    if capital_authority != "NONE" or live_trading != "BLOCKED":
        raise DevelopmentModelTournamentGovernanceError("D2E cannot grant capital or LIVE")


def _parse_canonical_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a canonical UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be UTC")
    if value != parsed.astimezone(timezone.utc).isoformat():
        raise ValueError(f"{name} must use canonical UTC serialization")
    return parsed


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase sha256")


def _hash(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()
