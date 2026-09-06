"""OSS-3D2I immutable DEVELOPMENT ranking-winner selection seal.

D2I consumes only completed OSS-3D2H/D2E evidence and freezes the exact
DEVELOPMENT ranking winner for the next *research protocol preregistration*
frontier.  It does not consume or authorize FINAL_HOLDOUT, does not retune or
reselect, and deliberately does not transform a tournament winner into a
statistical-significance, alpha, profitability, PAPER or LIVE claim.

The word "winner" in this module means only: the candidate selected by the
already-preregistered OSS-3D2E ranking rule on DEVELOPMENT.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
import re

from autotrade.research.oss3_development_model_tournament import PRIMARY_METRIC

from .family_evaluation_batch import (
    FamilyEvaluationBatchEvidence,
    FamilyEvaluationPreregistration,
)


OSS3D2I_CONTRACT_VERSION = "OSS3D2I_DEVELOPMENT_WINNER_SELECTION_SEAL_V1"
SELECTION_SCOPE = "DEVELOPMENT_RANKING_WINNER_ONLY"
NEXT_FRONTIER = "OSS3D2J_PROTOCOL_PREREGISTRATION_ONLY"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")


class DevelopmentWinnerSealError(RuntimeError):
    """Base OSS-3D2I failure."""


class DevelopmentWinnerSealIntegrityError(DevelopmentWinnerSealError):
    """Upstream immutable evidence cannot be rebound exactly."""


class DevelopmentWinnerSealGovernanceError(DevelopmentWinnerSealError):
    """Operation attempts to exceed DEVELOPMENT-only selection authority."""


@dataclass(frozen=True, slots=True)
class DevelopmentWinnerSelectionSeal:
    contract_version: str
    selection_scope: str
    next_frontier: str
    preregistration_fingerprint: str
    d2h_batch_evidence_fingerprint: str
    d2e_plan_fingerprint: str
    d2e_tournament_evidence_fingerprint: str
    selected_trial_id: str
    selected_hypothesis_id: str
    model_family: str
    model_config_hash: str
    request_hash: str
    prediction_artifact_hash: str
    prediction_receipt_hash: str
    environment_attestation_hash: str
    d2g_run_evidence_hash: str
    d2d_evaluation_artifact_hash: str
    shared_runner_code_hash: str
    runtime_environment_hash: str
    primary_metric_name: str
    winner_primary_metric: float
    winner_raw_p_value: float
    winner_holm_adjusted_p_value: float
    statistical_significance_claim_authorized: bool = False
    alpha_claim_authorized: bool = False
    profitability_claim_authorized: bool = False
    reselection_allowed: bool = False
    retuning_allowed: bool = False
    final_holdout_observed: bool = False
    final_holdout_authorized: bool = False
    holdout_permit_consumed: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.contract_version != OSS3D2I_CONTRACT_VERSION:
            raise DevelopmentWinnerSealIntegrityError("noncanonical OSS-3D2I contract version")
        if self.selection_scope != SELECTION_SCOPE:
            raise DevelopmentWinnerSealGovernanceError("D2I selection scope drifted")
        if self.next_frontier != NEXT_FRONTIER:
            raise DevelopmentWinnerSealGovernanceError("D2I next frontier drifted")
        for name in (
            "preregistration_fingerprint",
            "d2h_batch_evidence_fingerprint",
            "d2e_plan_fingerprint",
            "d2e_tournament_evidence_fingerprint",
            "model_config_hash",
            "request_hash",
            "prediction_artifact_hash",
            "prediction_receipt_hash",
            "environment_attestation_hash",
            "d2g_run_evidence_hash",
            "d2d_evaluation_artifact_hash",
            "shared_runner_code_hash",
            "runtime_environment_hash",
        ):
            _require_hash(getattr(self, name), name)
        for name in ("selected_trial_id", "selected_hypothesis_id", "model_family"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _ID_RE.fullmatch(value):
                raise ValueError(f"invalid {name}")
        if self.primary_metric_name != PRIMARY_METRIC:
            raise DevelopmentWinnerSealGovernanceError("D2I primary metric must remain D2E primary metric")
        for name in (
            "winner_primary_metric",
            "winner_raw_p_value",
            "winner_holm_adjusted_p_value",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not 0.0 <= self.winner_raw_p_value <= 1.0:
            raise ValueError("winner_raw_p_value outside [0,1]")
        if not 0.0 <= self.winner_holm_adjusted_p_value <= 1.0:
            raise ValueError("winner_holm_adjusted_p_value outside [0,1]")
        if self.winner_holm_adjusted_p_value < self.winner_raw_p_value:
            raise DevelopmentWinnerSealIntegrityError("Holm-adjusted p-value cannot be below raw p-value")
        _deny_authority(self)

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "selection_scope": self.selection_scope,
            "next_frontier": self.next_frontier,
            "preregistration_fingerprint": self.preregistration_fingerprint,
            "d2h_batch_evidence_fingerprint": self.d2h_batch_evidence_fingerprint,
            "d2e_plan_fingerprint": self.d2e_plan_fingerprint,
            "d2e_tournament_evidence_fingerprint": self.d2e_tournament_evidence_fingerprint,
            "selected_trial_id": self.selected_trial_id,
            "selected_hypothesis_id": self.selected_hypothesis_id,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "request_hash": self.request_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "prediction_receipt_hash": self.prediction_receipt_hash,
            "environment_attestation_hash": self.environment_attestation_hash,
            "d2g_run_evidence_hash": self.d2g_run_evidence_hash,
            "d2d_evaluation_artifact_hash": self.d2d_evaluation_artifact_hash,
            "shared_runner_code_hash": self.shared_runner_code_hash,
            "runtime_environment_hash": self.runtime_environment_hash,
            "primary_metric_name": self.primary_metric_name,
            "winner_primary_metric": float(self.winner_primary_metric),
            "winner_raw_p_value": float(self.winner_raw_p_value),
            "winner_holm_adjusted_p_value": float(self.winner_holm_adjusted_p_value),
            "statistical_significance_claim_authorized": self.statistical_significance_claim_authorized,
            "alpha_claim_authorized": self.alpha_claim_authorized,
            "profitability_claim_authorized": self.profitability_claim_authorized,
            "reselection_allowed": self.reselection_allowed,
            "retuning_allowed": self.retuning_allowed,
            "final_holdout_observed": self.final_holdout_observed,
            "final_holdout_authorized": self.final_holdout_authorized,
            "holdout_permit_consumed": self.holdout_permit_consumed,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


def seal_development_winner(
    *,
    preregistration: FamilyEvaluationPreregistration,
    batch_evidence: FamilyEvaluationBatchEvidence,
) -> DevelopmentWinnerSelectionSeal:
    """Freeze the exact D2E DEVELOPMENT ranking winner without HOLDOUT access."""
    if not isinstance(preregistration, FamilyEvaluationPreregistration):
        raise TypeError("preregistration must be FamilyEvaluationPreregistration")
    if not isinstance(batch_evidence, FamilyEvaluationBatchEvidence):
        raise TypeError("batch_evidence must be FamilyEvaluationBatchEvidence")

    if batch_evidence.preregistration_fingerprint != preregistration.fingerprint:
        raise DevelopmentWinnerSealIntegrityError("D2H batch/preregistration fingerprint mismatch")
    if batch_evidence.d2e_plan_fingerprint != preregistration.d2e_plan.fingerprint:
        raise DevelopmentWinnerSealIntegrityError("D2H batch/D2E plan fingerprint mismatch")
    tournament = batch_evidence.tournament_evidence
    if tournament.plan_fingerprint != preregistration.d2e_plan.fingerprint:
        raise DevelopmentWinnerSealIntegrityError("D2E tournament/preregistration plan mismatch")
    if tournament.runtime_environment_hash != batch_evidence.runtime_environment_hash:
        raise DevelopmentWinnerSealIntegrityError("D2E tournament runtime differs from D2H batch")
    if tournament.final_holdout_observed or tournament.promotion_authorized:
        raise DevelopmentWinnerSealGovernanceError("D2I accepts DEVELOPMENT-only non-promoting D2E evidence")

    winner_id = tournament.winner_trial_id
    candidate_matches = tuple(
        candidate for candidate in preregistration.d2e_plan.candidates if candidate.trial_id == winner_id
    )
    if len(candidate_matches) != 1:
        raise DevelopmentWinnerSealIntegrityError("D2E winner is not exactly one frozen candidate")
    candidate = candidate_matches[0]

    output_matches = tuple(
        binding for binding in preregistration.candidate_output_bindings if binding.candidate_id == winner_id
    )
    evaluation_matches = tuple(
        binding for binding in batch_evidence.evaluations if binding.candidate_id == winner_id
    )
    if len(output_matches) != 1 or len(evaluation_matches) != 1:
        raise DevelopmentWinnerSealIntegrityError("winner lacks exact D2H output/evaluation binding")
    output_binding = output_matches[0]
    evaluation_binding = evaluation_matches[0]

    for name, expected, actual in (
        ("model config", candidate.model_config_hash, output_binding.model_config_hash),
        ("request", candidate.request_hash, output_binding.request_hash),
        ("environment attestation", candidate.environment_attestation_hash, output_binding.environment_attestation_hash),
        ("runner code", candidate.expected_runner_code_hash, output_binding.shared_runner_code_hash),
        ("batch runner", batch_evidence.shared_runner_code_hash, output_binding.shared_runner_code_hash),
        ("runtime environment", batch_evidence.runtime_environment_hash, output_binding.runtime_environment_hash),
        ("evaluation request", output_binding.request_hash, evaluation_binding.request_hash),
        ("evaluation prediction", output_binding.prediction_artifact_hash, evaluation_binding.prediction_artifact_hash),
        ("evaluation receipt", output_binding.prediction_receipt_hash, evaluation_binding.receipt_hash),
        ("evaluation attestation", output_binding.environment_attestation_hash, evaluation_binding.environment_attestation_hash),
        ("evaluation D2G evidence", output_binding.d2g_run_evidence_hash, evaluation_binding.d2g_run_evidence_hash),
    ):
        if expected != actual:
            raise DevelopmentWinnerSealIntegrityError(f"winner lineage mismatch: {name}")

    return DevelopmentWinnerSelectionSeal(
        contract_version=OSS3D2I_CONTRACT_VERSION,
        selection_scope=SELECTION_SCOPE,
        next_frontier=NEXT_FRONTIER,
        preregistration_fingerprint=preregistration.fingerprint,
        d2h_batch_evidence_fingerprint=batch_evidence.fingerprint,
        d2e_plan_fingerprint=preregistration.d2e_plan.fingerprint,
        d2e_tournament_evidence_fingerprint=tournament.fingerprint,
        selected_trial_id=winner_id,
        selected_hypothesis_id=candidate.hypothesis_id,
        model_family=candidate.model_family,
        model_config_hash=candidate.model_config_hash,
        request_hash=output_binding.request_hash,
        prediction_artifact_hash=output_binding.prediction_artifact_hash,
        prediction_receipt_hash=output_binding.prediction_receipt_hash,
        environment_attestation_hash=output_binding.environment_attestation_hash,
        d2g_run_evidence_hash=output_binding.d2g_run_evidence_hash,
        d2d_evaluation_artifact_hash=evaluation_binding.d2d_evaluation_artifact_hash,
        shared_runner_code_hash=batch_evidence.shared_runner_code_hash,
        runtime_environment_hash=batch_evidence.runtime_environment_hash,
        primary_metric_name=PRIMARY_METRIC,
        winner_primary_metric=tournament.winner_primary_metric,
        winner_raw_p_value=tournament.winner_raw_p_value,
        winner_holm_adjusted_p_value=tournament.winner_holm_adjusted_p_value,
    )


def verify_development_winner_seal(
    *,
    seal: DevelopmentWinnerSelectionSeal,
    preregistration: FamilyEvaluationPreregistration,
    batch_evidence: FamilyEvaluationBatchEvidence,
) -> None:
    """Rebuild the seal and require exact deterministic identity."""
    if not isinstance(seal, DevelopmentWinnerSelectionSeal):
        raise TypeError("seal must be DevelopmentWinnerSelectionSeal")
    rebuilt = seal_development_winner(
        preregistration=preregistration,
        batch_evidence=batch_evidence,
    )
    if rebuilt.fingerprint != seal.fingerprint or rebuilt.to_dict() != seal.to_dict():
        raise DevelopmentWinnerSealIntegrityError("D2I seal does not rebind to current D2H evidence")


def _deny_authority(seal: DevelopmentWinnerSelectionSeal) -> None:
    if (
        seal.statistical_significance_claim_authorized
        or seal.alpha_claim_authorized
        or seal.profitability_claim_authorized
    ):
        raise DevelopmentWinnerSealGovernanceError(
            "D2I ranking selection cannot authorize significance, alpha or profitability claims"
        )
    if seal.reselection_allowed or seal.retuning_allowed:
        raise DevelopmentWinnerSealGovernanceError("D2I freezes winner and forbids reselection/retuning")
    if seal.final_holdout_observed or seal.final_holdout_authorized or seal.holdout_permit_consumed:
        raise DevelopmentWinnerSealGovernanceError("D2I cannot observe, authorize or consume FINAL_HOLDOUT")
    if seal.promotion_authorized or seal.execution_authorized or seal.paper_execution_authorized:
        raise DevelopmentWinnerSealGovernanceError("D2I cannot authorize promotion or execution")
    if seal.capital_authority != "NONE" or seal.live_trading != "BLOCKED":
        raise DevelopmentWinnerSealGovernanceError("D2I cannot grant capital or LIVE authority")


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
