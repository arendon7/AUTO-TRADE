"""OSS-3D2H preregistered DEVELOPMENT family-evaluation batch.

D2H is an orchestration/evidence layer, not a Qlib runner. It consumes already
frozen OSS-3D2G candidate outputs, creates the complete OSS-3D2E tournament
plan without reading label values, requires durable preregistration, and only
then exposes DEVELOPMENT label values to OSS-3D2D evaluation.

The sequence is intentionally two-phase:

    D2G frozen predictions -> prepare D2H/D2E plan -> durable preregistration
        -> D2D evaluations using DEVELOPMENT labels -> D2E tournament evidence

FINAL_HOLDOUT is structurally unavailable. No Qlib execution, network, broker,
OMS, Safety, OrderIntent, PAPER, capital or LIVE authority exists here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Iterable

from autotrade.research.oss3_concrete_model_family import (
    CANONICAL_CANDIDATES,
    ConcreteModelFamilyPlan,
    ConcreteModelRequestSetEvidence,
)
from autotrade.research.oss3_development_evaluation import (
    DevelopmentEvaluationArtifact,
    evaluate_development_predictions,
)
from autotrade.research.oss3_development_inference import (
    DevelopmentInferenceRequest,
    DevelopmentPredictionReceipt,
)
from autotrade.research.oss3_development_model_tournament import (
    DevelopmentDatasetBinding,
    DevelopmentModelCandidate,
    OSS3D2EPlan,
    OSS3D2ETournamentEvidence,
    RuntimeEnvironmentIdentity,
    build_oss3d2e_plan,
    evaluate_oss3d2e_tournament,
    preregister_oss3d2e_plan,
    record_oss3d2e_evaluation,
)
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact
from autotrade.research.oss3_supervised_label_artifact import (
    LabelPartition,
    SupervisedLabelArtifact,
)
from autotrade.research.trials import SQLiteTrialLedger, TrialStatus

from .family_environment_attestation import CandidateEnvironmentAttestation
from .family_model_contract import family_runner_code_hash
from .family_runner import (
    FamilyCandidateRunEvidence,
    verify_family_candidate_outputs,
)


OSS3D2H_PREREGISTRATION_VERSION = "OSS3D2H_FAMILY_EVALUATION_PREREGISTRATION_V1"
OSS3D2H_BATCH_EVIDENCE_VERSION = "OSS3D2H_FAMILY_EVALUATION_BATCH_EVIDENCE_V1"
HYPOTHESIS_PREFIX = "oss3d2h"
MAX_CANDIDATES = 16

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")

# Files that can change how D2H validates frozen inference evidence, exposes
# DEVELOPMENT labels, records D2D metrics or ranks the D2E family.
SEMANTIC_FILES = (
    "labs/oss3_qlib/family_evaluation_batch.py",
    "labs/oss3_qlib/family_model_contract.py",
    "labs/oss3_qlib/family_environment_attestation.py",
    "labs/oss3_qlib/family_runner.py",
    "src/autotrade/research/oss3_concrete_model_family.py",
    "src/autotrade/research/oss3_development_inference.py",
    "src/autotrade/research/oss3_qlib_artifact.py",
    "src/autotrade/research/oss3_supervised_label_artifact.py",
    "src/autotrade/research/oss3_development_evaluation.py",
    "src/autotrade/research/oss3_development_model_tournament.py",
    "src/autotrade/research/trials.py",
    "src/autotrade/research/tournament.py",
    "src/autotrade/research/multiple_testing.py",
)


class FamilyEvaluationBatchError(RuntimeError):
    """Base OSS-3D2H failure."""


class FamilyEvaluationBatchIntegrityError(FamilyEvaluationBatchError):
    """Frozen family, output or durable evidence drifted."""


class FamilyEvaluationBatchGovernanceError(FamilyEvaluationBatchError):
    """Operation violates preregistered DEVELOPMENT-only governance."""


class FamilyEvaluationBatchCompatibilityError(FamilyEvaluationBatchError):
    """Candidate outputs or DEVELOPMENT labels do not share exact support."""


@dataclass(frozen=True, slots=True)
class FrozenCandidateOutput:
    """One already-produced D2G candidate output set.

    Construction re-verifies request -> prediction -> receipt -> attestation ->
    D2G evidence against the current frozen D2F/D2G contract.
    """

    candidate_id: str
    request: DevelopmentInferenceRequest
    prediction: QlibPredictionArtifact
    receipt: DevelopmentPredictionReceipt
    attestation: CandidateEnvironmentAttestation
    run_evidence: FamilyCandidateRunEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not _ID_RE.fullmatch(self.candidate_id):
            raise ValueError("invalid candidate_id")
        verify_family_candidate_outputs(
            request=self.request,
            prediction=self.prediction,
            receipt=self.receipt,
            attestation=self.attestation,
            evidence=self.run_evidence,
        )
        if self.run_evidence.candidate_id != self.candidate_id:
            raise FamilyEvaluationBatchIntegrityError("candidate id differs from D2G evidence")

    @property
    def runtime_environment(self) -> RuntimeEnvironmentIdentity:
        return self.attestation.runtime_environment

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "request_hash": self.request.request_hash,
            "prediction_artifact_hash": self.prediction.artifact_hash,
            "prediction_receipt_hash": self.receipt.fingerprint,
            "environment_attestation_hash": self.attestation.artifact_hash,
            "runtime_environment_hash": self.runtime_environment.fingerprint,
            "d2g_run_evidence_hash": self.run_evidence.fingerprint,
            "model_config_hash": self.request.manifest.model_config_hash,
            "shared_runner_code_hash": self.request.manifest.expected_runner_code_hash,
        }


@dataclass(frozen=True, slots=True)
class FamilyEvaluationPreregistration:
    preregistration_version: str
    d2f_plan_fingerprint: str
    d2f_request_set_fingerprint: str
    d2h_code_version: str
    development_label_artifact_hash: str
    candidate_output_bindings: tuple[dict[str, object], ...]
    d2e_plan: OSS3D2EPlan
    label_values_used: bool = False
    development_metrics_computed: bool = False
    final_holdout_observed: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.preregistration_version != OSS3D2H_PREREGISTRATION_VERSION:
            raise FamilyEvaluationBatchIntegrityError("noncanonical D2H preregistration version")
        for name in (
            "d2f_plan_fingerprint",
            "d2f_request_set_fingerprint",
            "d2h_code_version",
            "development_label_artifact_hash",
        ):
            _require_hash(getattr(self, name), name)
        if not isinstance(self.d2e_plan, OSS3D2EPlan):
            raise TypeError("d2e_plan must be OSS3D2EPlan")
        if not 2 <= len(self.candidate_output_bindings) <= MAX_CANDIDATES:
            raise FamilyEvaluationBatchGovernanceError("D2H candidate count outside bound")
        candidate_ids = tuple(str(item.get("candidate_id", "")) for item in self.candidate_output_bindings)
        if candidate_ids != tuple(candidate.trial_id for candidate in self.d2e_plan.candidates):
            raise FamilyEvaluationBatchIntegrityError("D2H bindings differ from frozen D2E candidate universe")
        if self.label_values_used or self.development_metrics_computed:
            raise FamilyEvaluationBatchGovernanceError(
                "D2H preregistration must exist before label-value use or DEVELOPMENT metrics"
            )
        _deny_authority(
            final_holdout_observed=self.final_holdout_observed,
            execution_authorized=self.execution_authorized,
            paper_execution_authorized=self.paper_execution_authorized,
            capital_authority=self.capital_authority,
            live_trading=self.live_trading,
            promotion_authorized=False,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "preregistration_version": self.preregistration_version,
            "d2f_plan_fingerprint": self.d2f_plan_fingerprint,
            "d2f_request_set_fingerprint": self.d2f_request_set_fingerprint,
            "d2h_code_version": self.d2h_code_version,
            "development_label_artifact_hash": self.development_label_artifact_hash,
            "candidate_output_bindings": list(self.candidate_output_bindings),
            "d2e_plan_fingerprint": self.d2e_plan.fingerprint,
            "d2e_plan": self.d2e_plan.to_dict(),
            "label_values_used": self.label_values_used,
            "development_metrics_computed": self.development_metrics_computed,
            "final_holdout_observed": self.final_holdout_observed,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class CandidateEvaluationBinding:
    candidate_id: str
    frozen_output_hash: str
    request_hash: str
    prediction_artifact_hash: str
    receipt_hash: str
    environment_attestation_hash: str
    d2g_run_evidence_hash: str
    d2d_evaluation_artifact_hash: str

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.candidate_id):
            raise ValueError("invalid candidate_id")
        for name in (
            "frozen_output_hash",
            "request_hash",
            "prediction_artifact_hash",
            "receipt_hash",
            "environment_attestation_hash",
            "d2g_run_evidence_hash",
            "d2d_evaluation_artifact_hash",
        ):
            _require_hash(getattr(self, name), name)

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "frozen_output_hash": self.frozen_output_hash,
            "request_hash": self.request_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "receipt_hash": self.receipt_hash,
            "environment_attestation_hash": self.environment_attestation_hash,
            "d2g_run_evidence_hash": self.d2g_run_evidence_hash,
            "d2d_evaluation_artifact_hash": self.d2d_evaluation_artifact_hash,
        }


@dataclass(frozen=True, slots=True)
class FamilyEvaluationBatchEvidence:
    evidence_version: str
    preregistration_fingerprint: str
    d2e_plan_fingerprint: str
    d2h_code_version: str
    development_label_artifact_hash: str
    shared_runner_code_hash: str
    runtime_environment_hash: str
    evaluations: tuple[CandidateEvaluationBinding, ...]
    tournament_evidence: OSS3D2ETournamentEvidence
    label_values_used_after_preregistration: bool = True
    development_metrics_computed: bool = True
    final_holdout_observed: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D2H_BATCH_EVIDENCE_VERSION:
            raise FamilyEvaluationBatchIntegrityError("noncanonical D2H batch evidence version")
        for name in (
            "preregistration_fingerprint",
            "d2e_plan_fingerprint",
            "d2h_code_version",
            "development_label_artifact_hash",
            "shared_runner_code_hash",
            "runtime_environment_hash",
        ):
            _require_hash(getattr(self, name), name)
        if not 2 <= len(self.evaluations) <= MAX_CANDIDATES:
            raise FamilyEvaluationBatchGovernanceError("D2H evaluation count outside bound")
        ids = tuple(binding.candidate_id for binding in self.evaluations)
        if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
            raise FamilyEvaluationBatchIntegrityError("D2H evaluation bindings must be unique canonical order")
        if not self.label_values_used_after_preregistration or not self.development_metrics_computed:
            raise FamilyEvaluationBatchIntegrityError("completed D2H evidence must contain DEVELOPMENT evaluation")
        if self.tournament_evidence.runtime_environment_hash != self.runtime_environment_hash:
            raise FamilyEvaluationBatchIntegrityError("D2E tournament runtime differs from D2H evidence")
        if self.tournament_evidence.plan_fingerprint != self.d2e_plan_fingerprint:
            raise FamilyEvaluationBatchIntegrityError("D2E tournament plan differs from D2H evidence")
        _deny_authority(
            final_holdout_observed=self.final_holdout_observed,
            execution_authorized=self.execution_authorized,
            paper_execution_authorized=self.paper_execution_authorized,
            capital_authority=self.capital_authority,
            live_trading=self.live_trading,
            promotion_authorized=self.promotion_authorized,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_version": self.evidence_version,
            "preregistration_fingerprint": self.preregistration_fingerprint,
            "d2e_plan_fingerprint": self.d2e_plan_fingerprint,
            "d2h_code_version": self.d2h_code_version,
            "development_label_artifact_hash": self.development_label_artifact_hash,
            "shared_runner_code_hash": self.shared_runner_code_hash,
            "runtime_environment_hash": self.runtime_environment_hash,
            "evaluations": [item.to_dict() for item in self.evaluations],
            "tournament_evidence": self.tournament_evidence.to_dict(),
            "tournament_evidence_hash": self.tournament_evidence.fingerprint,
            "label_values_used_after_preregistration": self.label_values_used_after_preregistration,
            "development_metrics_computed": self.development_metrics_computed,
            "final_holdout_observed": self.final_holdout_observed,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


def family_evaluation_code_hash(*, repo_root: Path | None = None) -> str:
    """Hash the complete semantic evaluation/orchestration surface."""
    root = Path(__file__).resolve().parents[2] if repo_root is None else Path(repo_root)
    digest = sha256()
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise FamilyEvaluationBatchIntegrityError(f"D2H semantic file missing: {relative}")
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def prepare_family_evaluation_preregistration(
    *,
    d2f_plan: ConcreteModelFamilyPlan,
    d2f_request_set: ConcreteModelRequestSetEvidence,
    outputs: Iterable[FrozenCandidateOutput],
    development_labels: SupervisedLabelArtifact,
    tournament_campaign_id: str,
    tournament_id: str,
) -> FamilyEvaluationPreregistration:
    """Freeze D2E plan from D2G outputs without reading DEVELOPMENT values."""
    if not isinstance(d2f_plan, ConcreteModelFamilyPlan):
        raise TypeError("d2f_plan must be ConcreteModelFamilyPlan")
    if not isinstance(d2f_request_set, ConcreteModelRequestSetEvidence):
        raise TypeError("d2f_request_set must be ConcreteModelRequestSetEvidence")
    if not isinstance(development_labels, SupervisedLabelArtifact):
        raise TypeError("development_labels must be SupervisedLabelArtifact")
    if d2f_request_set.family_fingerprint != d2f_plan.fingerprint:
        raise FamilyEvaluationBatchIntegrityError("D2F plan/request-set fingerprint mismatch")
    if d2f_request_set.shared_runner_code_hash != d2f_plan.shared_runner_code_hash:
        raise FamilyEvaluationBatchIntegrityError("D2F shared runner identity mismatch")
    if d2f_plan.shared_runner_code_hash != family_runner_code_hash():
        raise FamilyEvaluationBatchIntegrityError("D2F plan is stale relative to current D2G runtime")

    output_tuple = tuple(sorted(tuple(outputs), key=lambda item: item.candidate_id))
    _verify_exact_family(d2f_plan, d2f_request_set, output_tuple)
    _verify_label_identity_without_values(development_labels, output_tuple)

    runtime = output_tuple[0].runtime_environment
    if {output.runtime_environment.fingerprint for output in output_tuple} != {runtime.fingerprint}:
        raise FamilyEvaluationBatchCompatibilityError("D2G outputs do not share one runtime environment")

    labels_manifest = development_labels.manifest
    first_receipt = output_tuple[0].receipt
    dataset = DevelopmentDatasetBinding(
        source_campaign_id=labels_manifest.campaign_id,
        research_split_hash=labels_manifest.research_split_hash,
        source_universe_hash=labels_manifest.source_universe_hash,
        label_definition_hash=labels_manifest.label_definition_hash,
        development_label_artifact_hash=development_labels.artifact_hash,
        evaluation_keyset_hash=first_receipt.inference_keyset_hash,
        evaluation_start=labels_manifest.partition_start,
        evaluation_end=labels_manifest.partition_end,
    )
    candidates = tuple(
        DevelopmentModelCandidate(
            trial_id=output.candidate_id,
            hypothesis_id=f"{HYPOTHESIS_PREFIX}:{output.candidate_id}",
            model_family=output.request.manifest.model_family,
            model_config_hash=output.request.manifest.model_config_hash,
            request_hash=output.request.request_hash,
            qlib_version=output.request.manifest.required_qlib_version,
            expected_runner_code_hash=output.request.manifest.expected_runner_code_hash,
            environment_attestation_hash=output.attestation.artifact_hash,
        )
        for output in output_tuple
    )
    code_version = family_evaluation_code_hash()
    d2e_plan = build_oss3d2e_plan(
        tournament_campaign_id=tournament_campaign_id,
        tournament_id=tournament_id,
        dataset=dataset,
        runtime_environment=runtime,
        candidates=candidates,
        code_version=code_version,
    )
    bindings = tuple(output.to_dict() for output in output_tuple)
    return FamilyEvaluationPreregistration(
        preregistration_version=OSS3D2H_PREREGISTRATION_VERSION,
        d2f_plan_fingerprint=d2f_plan.fingerprint,
        d2f_request_set_fingerprint=d2f_request_set.fingerprint,
        d2h_code_version=code_version,
        development_label_artifact_hash=development_labels.artifact_hash,
        candidate_output_bindings=bindings,
        d2e_plan=d2e_plan,
    )


def preregister_family_evaluation(
    ledger: SQLiteTrialLedger,
    preregistration: FamilyEvaluationPreregistration,
    *,
    now: datetime,
) -> None:
    """Durably freeze the entire six-candidate D2E universe before metrics."""
    if not isinstance(preregistration, FamilyEvaluationPreregistration):
        raise TypeError("preregistration must be FamilyEvaluationPreregistration")
    if preregistration.d2h_code_version != family_evaluation_code_hash():
        raise FamilyEvaluationBatchIntegrityError("D2H preregistration code version is stale")
    preregister_oss3d2e_plan(ledger, preregistration.d2e_plan, now=now)


def evaluate_preregistered_family(
    ledger: SQLiteTrialLedger,
    preregistration: FamilyEvaluationPreregistration,
    *,
    outputs: Iterable[FrozenCandidateOutput],
    development_labels: SupervisedLabelArtifact,
    now: datetime,
) -> FamilyEvaluationBatchEvidence:
    """Evaluate all six predictions only after durable D2E preregistration.

    All D2D artifacts are built before any terminal ledger write. Therefore an
    invalid candidate/label binding cannot leave a partially evaluated family.
    """
    if preregistration.d2h_code_version != family_evaluation_code_hash():
        raise FamilyEvaluationBatchIntegrityError("D2H code drifted after preregistration")
    if development_labels.artifact_hash != preregistration.development_label_artifact_hash:
        raise FamilyEvaluationBatchIntegrityError("DEVELOPMENT label artifact differs from preregistration")

    output_tuple = tuple(sorted(tuple(outputs), key=lambda item: item.candidate_id))
    _verify_outputs_against_preregistration(preregistration, output_tuple)
    _require_durable_preregistration(ledger, preregistration)
    _verify_label_identity_without_values(development_labels, output_tuple)

    # Label values first become semantically active here, after durable plan
    # verification. Build the full family in memory before writing any result.
    evaluations: tuple[DevelopmentEvaluationArtifact, ...] = tuple(
        evaluate_development_predictions(
            receipt=output.receipt,
            prediction=output.prediction,
            labels=development_labels,
            environment_attestation_hash=output.attestation.artifact_hash,
        )
        for output in output_tuple
    )

    for offset, (output, evaluation) in enumerate(zip(output_tuple, evaluations, strict=True)):
        record_oss3d2e_evaluation(
            ledger,
            preregistration.d2e_plan,
            trial_id=output.candidate_id,
            evaluation=evaluation,
            receipt=output.receipt,
            now=now + timedelta(microseconds=offset),
        )

    tournament = evaluate_oss3d2e_tournament(ledger, preregistration.d2e_plan)
    bindings = tuple(
        CandidateEvaluationBinding(
            candidate_id=output.candidate_id,
            frozen_output_hash=output.fingerprint,
            request_hash=output.request.request_hash,
            prediction_artifact_hash=output.prediction.artifact_hash,
            receipt_hash=output.receipt.fingerprint,
            environment_attestation_hash=output.attestation.artifact_hash,
            d2g_run_evidence_hash=output.run_evidence.fingerprint,
            d2d_evaluation_artifact_hash=evaluation.artifact_hash,
        )
        for output, evaluation in zip(output_tuple, evaluations, strict=True)
    )
    return FamilyEvaluationBatchEvidence(
        evidence_version=OSS3D2H_BATCH_EVIDENCE_VERSION,
        preregistration_fingerprint=preregistration.fingerprint,
        d2e_plan_fingerprint=preregistration.d2e_plan.fingerprint,
        d2h_code_version=preregistration.d2h_code_version,
        development_label_artifact_hash=development_labels.artifact_hash,
        shared_runner_code_hash=output_tuple[0].request.manifest.expected_runner_code_hash,
        runtime_environment_hash=output_tuple[0].runtime_environment.fingerprint,
        evaluations=bindings,
        tournament_evidence=tournament,
    )


def _verify_exact_family(
    d2f_plan: ConcreteModelFamilyPlan,
    request_set: ConcreteModelRequestSetEvidence,
    outputs: tuple[FrozenCandidateOutput, ...],
) -> None:
    expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
    if tuple(candidate.candidate_id for candidate in d2f_plan.candidates) != expected_ids:
        raise FamilyEvaluationBatchIntegrityError("D2F plan does not contain canonical family")
    if tuple(binding.candidate_id for binding in request_set.bindings) != expected_ids:
        raise FamilyEvaluationBatchIntegrityError("D2F request set does not contain canonical family")
    if tuple(output.candidate_id for output in outputs) != expected_ids:
        raise FamilyEvaluationBatchGovernanceError("D2H requires exact complete D2F family")
    for candidate, binding, output in zip(d2f_plan.candidates, request_set.bindings, outputs, strict=True):
        if output.request.request_hash != binding.request.request_hash:
            raise FamilyEvaluationBatchIntegrityError("D2G request differs from D2F preregistered request")
        if output.request.manifest.model_config_hash != candidate.model_config_hash:
            raise FamilyEvaluationBatchIntegrityError("D2G model config differs from D2F candidate")
        if output.run_evidence.model_config_hash != candidate.model_config_hash:
            raise FamilyEvaluationBatchIntegrityError("D2G evidence model config differs from D2F candidate")
        if output.request.manifest.expected_runner_code_hash != d2f_plan.shared_runner_code_hash:
            raise FamilyEvaluationBatchIntegrityError("candidate runner differs from frozen D2F runner")


def _verify_label_identity_without_values(
    labels: SupervisedLabelArtifact,
    outputs: tuple[FrozenCandidateOutput, ...],
) -> None:
    manifest = labels.manifest
    if manifest.partition != LabelPartition.DEVELOPMENT.value:
        raise FamilyEvaluationBatchGovernanceError("D2H accepts DEVELOPMENT labels only")
    if not outputs:
        raise FamilyEvaluationBatchGovernanceError("D2H requires candidate outputs")
    label_keys = tuple((row.label_as_of, row.symbol) for row in labels.rows)
    if len(label_keys) != manifest.row_count:
        raise FamilyEvaluationBatchIntegrityError("DEVELOPMENT label row count drifted")
    for output in outputs:
        receipt = output.receipt
        prediction_keys = tuple((row.timestamp, row.symbol) for row in output.prediction.rows)
        if prediction_keys != label_keys:
            raise FamilyEvaluationBatchCompatibilityError("candidate prediction keys differ from DEVELOPMENT label keys")
        for name, expected, actual in (
            ("campaign", manifest.campaign_id, receipt.campaign_id),
            ("research split", manifest.research_split_hash, receipt.research_split_hash),
            ("source universe", manifest.source_universe_hash, receipt.source_universe_hash),
            ("label definition", manifest.label_definition_hash, receipt.label_definition_hash),
            ("evaluation start", manifest.partition_start, receipt.inference_start),
            ("evaluation end", manifest.partition_end, receipt.inference_end),
            ("row count", manifest.row_count, receipt.prediction_count),
        ):
            if expected != actual:
                raise FamilyEvaluationBatchCompatibilityError(f"DEVELOPMENT {name} mismatch")


def _verify_outputs_against_preregistration(
    preregistration: FamilyEvaluationPreregistration,
    outputs: tuple[FrozenCandidateOutput, ...],
) -> None:
    expected_ids = tuple(candidate.trial_id for candidate in preregistration.d2e_plan.candidates)
    if tuple(output.candidate_id for output in outputs) != expected_ids:
        raise FamilyEvaluationBatchIntegrityError("output universe differs from preregistered D2E plan")
    if tuple(output.to_dict() for output in outputs) != preregistration.candidate_output_bindings:
        raise FamilyEvaluationBatchIntegrityError("frozen D2G outputs differ from preregistration")
    runtime_hashes = {output.runtime_environment.fingerprint for output in outputs}
    if runtime_hashes != {preregistration.d2e_plan.runtime_environment.fingerprint}:
        raise FamilyEvaluationBatchCompatibilityError("runtime environment drifted after preregistration")
    runner_hashes = {output.request.manifest.expected_runner_code_hash for output in outputs}
    if runner_hashes != {family_runner_code_hash()}:
        raise FamilyEvaluationBatchCompatibilityError("shared D2G runner drifted after preregistration")


def _require_durable_preregistration(
    ledger: SQLiteTrialLedger,
    preregistration: FamilyEvaluationPreregistration,
) -> None:
    plan = preregistration.d2e_plan
    try:
        accounting = ledger.campaign_accounting(plan.campaign.campaign_id)
    except Exception as exc:
        raise FamilyEvaluationBatchGovernanceError(
            "D2H evaluation requires durable D2E preregistration"
        ) from exc
    if accounting.expected_trial_ids != plan.campaign.expected_trial_ids:
        raise FamilyEvaluationBatchIntegrityError("durable campaign universe differs from D2E plan")
    if accounting.missing_preregistration_ids:
        raise FamilyEvaluationBatchGovernanceError("D2E campaign is not fully preregistered")
    if accounting.completed_trial_ids or accounting.failed_trial_ids:
        raise FamilyEvaluationBatchGovernanceError("D2H batch requires untouched preregistered trials")
    if accounting.unterminated_trial_ids != plan.campaign.expected_trial_ids:
        raise FamilyEvaluationBatchIntegrityError("durable preregistration state is not canonical")
    records = ledger.list_trials(plan.campaign.campaign_id)
    if tuple(record.spec.fingerprint for record in records) != tuple(
        trial.fingerprint for trial in plan.trials
    ):
        raise FamilyEvaluationBatchIntegrityError("durable trial specs differ from D2E plan")
    if any(record.status is not TrialStatus.PREREGISTERED for record in records):
        raise FamilyEvaluationBatchGovernanceError("all D2H trials must still be PREREGISTERED")


def _deny_authority(
    *,
    final_holdout_observed: bool,
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
    promotion_authorized: bool,
) -> None:
    if final_holdout_observed or promotion_authorized:
        raise FamilyEvaluationBatchGovernanceError("D2H cannot observe HOLDOUT or authorize promotion")
    if execution_authorized or paper_execution_authorized:
        raise FamilyEvaluationBatchGovernanceError("D2H cannot authorize execution")
    if capital_authority != "NONE" or live_trading != "BLOCKED":
        raise FamilyEvaluationBatchGovernanceError("D2H cannot grant capital or LIVE authority")


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
