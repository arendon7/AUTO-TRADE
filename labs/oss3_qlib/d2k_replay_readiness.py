"""OSS-3D3H no-holdout D2K replay-readiness seal.

D3H reconstructs only the concrete, already-frozen replay inputs that D2K will
need if a later, separately reviewed one-shot FINAL_HOLDOUT evaluation is ever
authorized.  It consumes certified D3C/D3G public lineage and the already
certified D2X real-history artifact.  It never loads protected Q1 values,
constructs a protected holdout, issues/burns a holdout permit, or invokes D2K.

The preparation phase is Qlib-free.  The sealing phase may inspect the exact
installed pyqlib environment to prove that a future D2K process can reproduce
the winner's frozen D2G environment, but it still performs no model fit or
prediction.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from autotrade.research.oss3_concrete_model_family import build_concrete_model_request_set
from autotrade.research.oss3_development_inference import DevelopmentInferenceRequest
from autotrade.research.oss3_factor_matrix_artifact import FactorMatrixArtifact
from autotrade.research.oss3_supervised_label_artifact import SupervisedLabelArtifact
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact
from labs.oss3_qlib.durable_development_outcome import read_durable_development_outcome
from labs.oss3_qlib.family_environment_attestation import collect_candidate_environment_attestation
from labs.oss3_qlib.family_model_contract import QLIB_VERSION, family_runner_code_hash
from labs.oss3_qlib.final_holdout_protocol import read_oss3d2j_protocol_read_only
from labs.oss3_qlib.raw_development_provenance import derive_raw_development_features
from labs.oss3_qlib.raw_training_bundle_provenance import derive_raw_training_bundle
from labs.oss3_qlib.real_development_campaign import REAL_CAMPAIGN_ID
from labs.oss3_qlib.sealed_raw_split_handoff import build_canonical_sealed_raw_split_handoff


OSS3D3H_PREPARATION_VERSION = "OSS3D3H_D2K_REPLAY_PREPARATION_V1"
OSS3D3H_SEAL_VERSION = "OSS3D3H_D2K_REPLAY_READINESS_SEAL_V1"
REVIEW_FRONTIER = "SEPARATE_EXPLICIT_D2K_ONE_SHOT_AUTHORIZATION_REVIEW_ONLY"
MAX_EVIDENCE_BYTES = 256_000
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")

REQUEST_FILE = "winner-request.json"
BUNDLE_FILE = "training-bundle.json"
TRAIN_FEATURES_FILE = "train-features.json"
TRAIN_LABELS_FILE = "train-labels.json"
DEVELOPMENT_FEATURES_FILE = "development-features.json"
PREPARATION_FILE = "d3h-replay-preparation.json"
ATTESTATION_FILE = "winner-environment-attestation.json"
SEAL_FILE = "d3h-replay-readiness-seal.json"


class D2KReplayReadinessError(RuntimeError):
    pass


class D2KReplayReadinessIntegrityError(D2KReplayReadinessError):
    pass


class D2KReplayReadinessGovernanceError(D2KReplayReadinessError):
    pass


@dataclass(frozen=True, slots=True)
class D2KReplayPreparationEvidence:
    evidence_version: str
    review_frontier: str
    source_d3c_bundle_fingerprint: str
    source_scientific_outcome_fingerprint: str
    source_d2i_seal_fingerprint: str
    source_d3g_evidence_fingerprint: str
    d2j_receipt_hash: str
    d2j_policy_fingerprint: str
    holdout_commitment_fingerprint: str
    expected_holdout_authorization_id: str
    selected_trial_id: str
    model_config_hash: str
    winner_request_hash: str
    expected_runner_code_hash: str
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
    request_file_sha256: str
    training_bundle_file_sha256: str
    training_feature_file_sha256: str
    training_label_file_sha256: str
    development_feature_file_sha256: str
    qlib_runtime_used: bool = False
    development_labels_materialized: bool = False
    development_predictions_materialized: bool = False
    development_metrics_computed: bool = False
    final_holdout_private_material_loaded: bool = False
    protected_holdout_constructed: bool = False
    d2k_evaluator_invoked: bool = False
    holdout_permit_issued: bool = False
    holdout_permit_consumed: bool = False
    final_holdout_checkout_authorized: bool = False
    predictive_validation_passed: bool = False
    profitability_claim_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D3H_PREPARATION_VERSION:
            raise D2KReplayReadinessIntegrityError("noncanonical D3H preparation version")
        if self.review_frontier != REVIEW_FRONTIER:
            raise D2KReplayReadinessGovernanceError("D3H review frontier drifted")
        for name in (
            "source_d3c_bundle_fingerprint", "source_scientific_outcome_fingerprint",
            "source_d2i_seal_fingerprint", "source_d3g_evidence_fingerprint",
            "d2j_receipt_hash", "d2j_policy_fingerprint", "holdout_commitment_fingerprint",
            "model_config_hash", "winner_request_hash", "expected_runner_code_hash",
            "d2w_evidence_fingerprint", "research_split_hash", "raw_training_source_hash",
            "raw_development_source_hash", "d2r_receipt_hash", "training_bundle_hash",
            "training_feature_artifact_hash", "training_label_artifact_hash",
            "development_feature_artifact_hash", "d2f_plan_fingerprint",
            "d2f_request_set_fingerprint", "request_file_sha256",
            "training_bundle_file_sha256", "training_feature_file_sha256",
            "training_label_file_sha256", "development_feature_file_sha256",
        ):
            _require_hash(getattr(self, name), name)
        for name in ("expected_holdout_authorization_id", "selected_trial_id"):
            _require_id(getattr(self, name), name)
        _deny_preparation_authority(self)

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class D2KReplayReadinessSeal:
    seal_version: str
    review_frontier: str
    preparation_fingerprint: str
    source_d3c_bundle_fingerprint: str
    source_d2i_seal_fingerprint: str
    source_d3g_evidence_fingerprint: str
    d2j_receipt_hash: str
    d2j_policy_fingerprint: str
    holdout_commitment_fingerprint: str
    expected_holdout_authorization_id: str
    selected_trial_id: str
    model_config_hash: str
    winner_request_hash: str
    training_bundle_hash: str
    training_feature_artifact_hash: str
    training_label_artifact_hash: str
    source_environment_attestation_hash: str
    source_runtime_environment_hash: str
    current_environment_attestation_hash: str
    current_runtime_environment_hash: str
    current_runner_code_hash: str
    evaluator_semantic_hash: str
    qlib_version: str
    environment_reproduced_exactly: bool
    replay_inputs_reproduced_exactly: bool
    qlib_model_fit_performed: bool = False
    qlib_prediction_performed: bool = False
    development_labels_materialized: bool = False
    final_holdout_private_material_loaded: bool = False
    protected_holdout_constructed: bool = False
    d2k_evaluator_invoked: bool = False
    holdout_permit_issued: bool = False
    holdout_permit_consumed: bool = False
    final_holdout_checkout_authorized: bool = False
    predictive_validation_passed: bool = False
    profitability_claim_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.seal_version != OSS3D3H_SEAL_VERSION:
            raise D2KReplayReadinessIntegrityError("noncanonical D3H seal version")
        if self.review_frontier != REVIEW_FRONTIER:
            raise D2KReplayReadinessGovernanceError("D3H review frontier drifted")
        for name in (
            "preparation_fingerprint", "source_d3c_bundle_fingerprint",
            "source_d2i_seal_fingerprint", "source_d3g_evidence_fingerprint",
            "d2j_receipt_hash", "d2j_policy_fingerprint", "holdout_commitment_fingerprint",
            "model_config_hash", "winner_request_hash", "training_bundle_hash",
            "training_feature_artifact_hash", "training_label_artifact_hash",
            "source_environment_attestation_hash", "source_runtime_environment_hash",
            "current_environment_attestation_hash", "current_runtime_environment_hash",
            "current_runner_code_hash", "evaluator_semantic_hash",
        ):
            _require_hash(getattr(self, name), name)
        for name in ("expected_holdout_authorization_id", "selected_trial_id"):
            _require_id(getattr(self, name), name)
        if self.qlib_version != QLIB_VERSION:
            raise D2KReplayReadinessIntegrityError("D3H requires exact frozen Qlib version")
        if not self.environment_reproduced_exactly or not self.replay_inputs_reproduced_exactly:
            raise D2KReplayReadinessIntegrityError("D3H seal requires exact replay/runtime reproduction")
        if self.source_environment_attestation_hash != self.current_environment_attestation_hash:
            raise D2KReplayReadinessIntegrityError("D3H current environment differs from frozen D2G winner")
        if self.source_runtime_environment_hash != self.current_runtime_environment_hash:
            raise D2KReplayReadinessIntegrityError("D3H current runtime differs from frozen D2G winner")
        _deny_seal_authority(self)

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def prepare_d2k_replay_package(*, evidence_root: str | Path, d3c_bundle_path: str | Path,
    d3g_result_path: str | Path, d3g_registry_path: str | Path, output_root: str | Path,
    now, repository_root: str | Path | None = None) -> D2KReplayPreparationEvidence:
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    d3c = read_durable_development_outcome(d3c_bundle_path)
    d3a = d3c.source_d3a_evidence
    winner = d3c.d2i_winner_seal
    d3g = _read_d3g_result(d3g_result_path)
    protocol = read_oss3d2j_protocol_read_only(d3g_registry_path,
        seal_fingerprint=d3c.d2i_winner_seal_fingerprint)
    if protocol is None:
        raise D2KReplayReadinessIntegrityError("D3H D3G registry lacks exact D2J protocol")
    _verify_d3g_binding(d3c=d3c, d3g=d3g, protocol=protocol)

    handoff = build_canonical_sealed_raw_split_handoff(evidence_root=evidence_root, now=now,
        repository_root=repository_root)
    for name, expected, actual in (
        ("D2W evidence", d3a["d2w_evidence_fingerprint"], handoff.evidence.fingerprint),
        ("research split", d3a["research_split_hash"], handoff.evidence.research_split_hash),
        ("raw TRAIN source", d3a["raw_training_source_hash"], handoff.training_source.source_hash),
        ("raw DEVELOPMENT source", d3a["raw_development_source_hash"], handoff.development_source.source_hash),
    ):
        _require_equal(name, expected, actual)

    train_features, train_labels, bundle, d2r_receipt = derive_raw_training_bundle(
        raw_source=handoff.training_source, campaign_id=REAL_CAMPAIGN_ID,
        research_split_hash=handoff.evidence.research_split_hash)
    development_features = derive_raw_development_features(raw_source=handoff.development_source,
        campaign_id=REAL_CAMPAIGN_ID, research_split_hash=handoff.evidence.research_split_hash)
    for name, expected, actual in (
        ("D2R receipt", d3a["d2r_receipt_hash"], d2r_receipt.receipt_hash),
        ("TRAIN bundle", d3a["training_bundle_hash"], bundle.artifact_hash),
        ("TRAIN features", d3a["training_feature_artifact_hash"], train_features.artifact_hash),
        ("TRAIN labels", d3a["training_label_artifact_hash"], train_labels.artifact_hash),
        ("DEVELOPMENT features", d3a["development_feature_artifact_hash"], development_features.artifact_hash),
    ):
        _require_equal(name, expected, actual)

    d2f_plan, request_set = build_concrete_model_request_set(training_bundle=bundle,
        development_features=development_features, shared_runner_code_hash=family_runner_code_hash())
    _require_equal("D2F plan", d3a["d2f_plan_fingerprint"], d2f_plan.fingerprint)
    _require_equal("D2F request set", d3a["d2f_request_set_fingerprint"], request_set.fingerprint)
    selected_trial_id = _required_string(winner, "selected_trial_id")
    matches = tuple(binding for binding in request_set.bindings if binding.candidate_id == selected_trial_id)
    if len(matches) != 1:
        raise D2KReplayReadinessIntegrityError("D3H frozen winner request is not unique")
    request = matches[0].request
    for name, expected, actual in (
        ("winner request", winner["request_hash"], request.request_hash),
        ("winner model config", winner["model_config_hash"], request.manifest.model_config_hash),
        ("winner runner", winner["shared_runner_code_hash"], request.manifest.expected_runner_code_hash),
        ("D2J request", protocol.winner_binding.request_hash, request.request_hash),
        ("D2J model config", protocol.model_config_hash, request.manifest.model_config_hash),
    ):
        _require_equal(name, expected, actual)
    if request.manifest.required_qlib_version != QLIB_VERSION:
        raise D2KReplayReadinessIntegrityError("D3H winner request Qlib version drifted")

    paths = {"request": output / REQUEST_FILE, "bundle": output / BUNDLE_FILE,
        "train_features": output / TRAIN_FEATURES_FILE, "train_labels": output / TRAIN_LABELS_FILE,
        "development_features": output / DEVELOPMENT_FEATURES_FILE}
    request.write(paths["request"]); bundle.write(paths["bundle"])
    train_features.write(paths["train_features"]); train_labels.write(paths["train_labels"])
    development_features.write(paths["development_features"])
    _verify_written_replay_files(paths, request, bundle, train_features, train_labels, development_features)

    evidence = D2KReplayPreparationEvidence(
        evidence_version=OSS3D3H_PREPARATION_VERSION, review_frontier=REVIEW_FRONTIER,
        source_d3c_bundle_fingerprint=d3c.fingerprint,
        source_scientific_outcome_fingerprint=d3c.scientific_outcome_fingerprint,
        source_d2i_seal_fingerprint=d3c.d2i_winner_seal_fingerprint,
        source_d3g_evidence_fingerprint=_required_string(d3g, "fingerprint"),
        d2j_receipt_hash=protocol.receipt_hash, d2j_policy_fingerprint=protocol.policy_fingerprint,
        holdout_commitment_fingerprint=protocol.holdout_commitment_fingerprint,
        expected_holdout_authorization_id=protocol.expected_holdout_authorization_id,
        selected_trial_id=selected_trial_id, model_config_hash=request.manifest.model_config_hash,
        winner_request_hash=request.request_hash, expected_runner_code_hash=request.manifest.expected_runner_code_hash,
        d2w_evidence_fingerprint=handoff.evidence.fingerprint,
        research_split_hash=handoff.evidence.research_split_hash,
        raw_training_source_hash=handoff.training_source.source_hash,
        raw_development_source_hash=handoff.development_source.source_hash,
        d2r_receipt_hash=d2r_receipt.receipt_hash, training_bundle_hash=bundle.artifact_hash,
        training_feature_artifact_hash=train_features.artifact_hash,
        training_label_artifact_hash=train_labels.artifact_hash,
        development_feature_artifact_hash=development_features.artifact_hash,
        d2f_plan_fingerprint=d2f_plan.fingerprint, d2f_request_set_fingerprint=request_set.fingerprint,
        request_file_sha256=_file_sha(paths["request"]), training_bundle_file_sha256=_file_sha(paths["bundle"]),
        training_feature_file_sha256=_file_sha(paths["train_features"]),
        training_label_file_sha256=_file_sha(paths["train_labels"]),
        development_feature_file_sha256=_file_sha(paths["development_features"]),
    )
    _write_evidence(evidence.to_dict() | {"fingerprint": evidence.fingerprint}, output / PREPARATION_FILE)
    return evidence


def seal_d2k_replay_readiness(*, package_root: str | Path, d3c_bundle_path: str | Path,
    d3g_result_path: str | Path, d3g_registry_path: str | Path) -> D2KReplayReadinessSeal:
    root = Path(package_root)
    preparation = read_d2k_replay_preparation(root / PREPARATION_FILE)
    d3c = read_durable_development_outcome(d3c_bundle_path)
    d3g = _read_d3g_result(d3g_result_path)
    protocol = read_oss3d2j_protocol_read_only(d3g_registry_path,
        seal_fingerprint=d3c.d2i_winner_seal_fingerprint)
    if protocol is None:
        raise D2KReplayReadinessIntegrityError("D3H D3G registry lacks D2J protocol")
    _verify_d3g_binding(d3c=d3c, d3g=d3g, protocol=protocol)
    winner = d3c.d2i_winner_seal

    request = DevelopmentInferenceRequest.read(root / REQUEST_FILE)
    bundle = TrainingBundleArtifact.read(root / BUNDLE_FILE)
    train_features = FactorMatrixArtifact.read(root / TRAIN_FEATURES_FILE)
    train_labels = SupervisedLabelArtifact.read(root / TRAIN_LABELS_FILE)
    development_features = FactorMatrixArtifact.read(root / DEVELOPMENT_FEATURES_FILE)
    paths = {"request": root / REQUEST_FILE, "bundle": root / BUNDLE_FILE,
        "train_features": root / TRAIN_FEATURES_FILE, "train_labels": root / TRAIN_LABELS_FILE,
        "development_features": root / DEVELOPMENT_FEATURES_FILE}
    _verify_written_replay_files(paths, request, bundle, train_features, train_labels, development_features)
    request.verify_inputs(training_bundle=bundle, development_features=development_features)
    if TrainingBundleArtifact.build(features=train_features, labels=train_labels) != bundle:
        raise D2KReplayReadinessIntegrityError("D3H concrete TRAIN artifacts do not rebuild bundle")
    _verify_preparation_files(preparation, root)
    for name, expected, actual in (
        ("preparation D3C", preparation.source_d3c_bundle_fingerprint, d3c.fingerprint),
        ("preparation D3G", preparation.source_d3g_evidence_fingerprint, _required_string(d3g, "fingerprint")),
        ("preparation D2J", preparation.d2j_receipt_hash, protocol.receipt_hash),
        ("preparation request", preparation.winner_request_hash, request.request_hash),
        ("preparation bundle", preparation.training_bundle_hash, bundle.artifact_hash),
        ("preparation TRAIN features", preparation.training_feature_artifact_hash, train_features.artifact_hash),
        ("preparation TRAIN labels", preparation.training_label_artifact_hash, train_labels.artifact_hash),
    ):
        _require_equal(name, expected, actual)

    current = collect_candidate_environment_attestation(model_config_hash=request.manifest.model_config_hash)
    current.verify_current_contract()
    source_attestation = _required_string(winner, "environment_attestation_hash")
    source_runtime = _required_string(winner, "runtime_environment_hash")
    if current.artifact_hash != source_attestation:
        raise D2KReplayReadinessIntegrityError("D3H current environment differs from frozen D2G winner")
    if current.runtime_environment.fingerprint != source_runtime:
        raise D2KReplayReadinessIntegrityError("D3H current runtime differs from frozen D2G winner")
    if current.manifest.runner_code_hash != request.manifest.expected_runner_code_hash:
        raise D2KReplayReadinessIntegrityError("D3H current runner differs from winner request")
    current.write(root / ATTESTATION_FILE)
    from labs.oss3_qlib.final_holdout_evaluator import evaluator_semantic_hash
    semantic_hash = evaluator_semantic_hash()
    seal = D2KReplayReadinessSeal(
        seal_version=OSS3D3H_SEAL_VERSION, review_frontier=REVIEW_FRONTIER,
        preparation_fingerprint=preparation.fingerprint,
        source_d3c_bundle_fingerprint=d3c.fingerprint,
        source_d2i_seal_fingerprint=d3c.d2i_winner_seal_fingerprint,
        source_d3g_evidence_fingerprint=_required_string(d3g, "fingerprint"),
        d2j_receipt_hash=protocol.receipt_hash, d2j_policy_fingerprint=protocol.policy_fingerprint,
        holdout_commitment_fingerprint=protocol.holdout_commitment_fingerprint,
        expected_holdout_authorization_id=protocol.expected_holdout_authorization_id,
        selected_trial_id=preparation.selected_trial_id, model_config_hash=request.manifest.model_config_hash,
        winner_request_hash=request.request_hash, training_bundle_hash=bundle.artifact_hash,
        training_feature_artifact_hash=train_features.artifact_hash,
        training_label_artifact_hash=train_labels.artifact_hash,
        source_environment_attestation_hash=source_attestation,
        source_runtime_environment_hash=source_runtime,
        current_environment_attestation_hash=current.artifact_hash,
        current_runtime_environment_hash=current.runtime_environment.fingerprint,
        current_runner_code_hash=current.manifest.runner_code_hash,
        evaluator_semantic_hash=semantic_hash, qlib_version=QLIB_VERSION,
        environment_reproduced_exactly=True, replay_inputs_reproduced_exactly=True,
    )
    _write_evidence(seal.to_dict() | {"fingerprint": seal.fingerprint}, root / SEAL_FILE)
    return seal


def read_d2k_replay_preparation(path: str | Path) -> D2KReplayPreparationEvidence:
    document = _read_evidence(path, OSS3D3H_PREPARATION_VERSION, "evidence_version")
    fingerprint = document.pop("fingerprint")
    try:
        evidence = D2KReplayPreparationEvidence(**document)
    except (TypeError, ValueError, D2KReplayReadinessError) as exc:
        if isinstance(exc, D2KReplayReadinessError): raise
        raise D2KReplayReadinessIntegrityError("invalid D3H preparation fields") from exc
    if evidence.fingerprint != fingerprint:
        raise D2KReplayReadinessIntegrityError("D3H preparation fingerprint mismatch")
    return evidence


def read_d2k_replay_readiness_seal(path: str | Path) -> D2KReplayReadinessSeal:
    document = _read_evidence(path, OSS3D3H_SEAL_VERSION, "seal_version")
    fingerprint = document.pop("fingerprint")
    try:
        seal = D2KReplayReadinessSeal(**document)
    except (TypeError, ValueError, D2KReplayReadinessError) as exc:
        if isinstance(exc, D2KReplayReadinessError): raise
        raise D2KReplayReadinessIntegrityError("invalid D3H seal fields") from exc
    if seal.fingerprint != fingerprint:
        raise D2KReplayReadinessIntegrityError("D3H seal fingerprint mismatch")
    return seal


def _verify_d3g_binding(*, d3c, d3g: Mapping[str, object], protocol) -> None:
    for name, expected, actual in (
        ("D3G D3C bundle", d3c.fingerprint, d3g.get("source_d3c_bundle_fingerprint")),
        ("D3G science", d3c.scientific_outcome_fingerprint, d3g.get("source_scientific_outcome_fingerprint")),
        ("D3G D2I", d3c.d2i_winner_seal_fingerprint, d3g.get("source_d2i_seal_fingerprint")),
        ("D3G receipt", protocol.receipt_hash, d3g.get("d2j_receipt_hash")),
        ("D3G policy", protocol.policy_fingerprint, d3g.get("d2j_policy_fingerprint")),
        ("D3G commitment", protocol.holdout_commitment_fingerprint, d3g.get("holdout_commitment_fingerprint")),
        ("D3G authorization identity", protocol.expected_holdout_authorization_id, d3g.get("expected_holdout_authorization_id")),
        ("D3G winner", protocol.selected_trial_id, d3g.get("selected_trial_id")),
    ):
        _require_equal(name, expected, actual)
    if any(bool(d3g.get(name)) for name in (
        "final_holdout_observed", "final_holdout_consumed", "holdout_permit_issued",
        "holdout_permit_consumed", "final_holdout_checkout_authorized", "predictive_validation_passed",
        "profitability_claim_authorized", "promotion_authorized", "execution_authorized",
        "paper_execution_authorized")):
        raise D2KReplayReadinessGovernanceError("D3H requires untouched non-authorizing D3G protocol")
    if d3g.get("capital_authority") != "NONE" or d3g.get("live_trading") != "BLOCKED":
        raise D2KReplayReadinessGovernanceError("D3H source may not grant capital/LIVE authority")


def _verify_written_replay_files(paths, request, bundle, train_features, train_labels, development_features) -> None:
    if DevelopmentInferenceRequest.read(paths["request"]) != request: raise D2KReplayReadinessIntegrityError("D3H winner request round-trip drifted")
    if TrainingBundleArtifact.read(paths["bundle"]) != bundle: raise D2KReplayReadinessIntegrityError("D3H TRAIN bundle round-trip drifted")
    if FactorMatrixArtifact.read(paths["train_features"]) != train_features: raise D2KReplayReadinessIntegrityError("D3H TRAIN feature round-trip drifted")
    if SupervisedLabelArtifact.read(paths["train_labels"]) != train_labels: raise D2KReplayReadinessIntegrityError("D3H TRAIN label round-trip drifted")
    if FactorMatrixArtifact.read(paths["development_features"]) != development_features: raise D2KReplayReadinessIntegrityError("D3H DEVELOPMENT feature round-trip drifted")


def _verify_preparation_files(preparation: D2KReplayPreparationEvidence, root: Path) -> None:
    for expected, name in ((preparation.request_file_sha256, REQUEST_FILE),
        (preparation.training_bundle_file_sha256, BUNDLE_FILE),
        (preparation.training_feature_file_sha256, TRAIN_FEATURES_FILE),
        (preparation.training_label_file_sha256, TRAIN_LABELS_FILE),
        (preparation.development_feature_file_sha256, DEVELOPMENT_FEATURES_FILE)):
        _require_equal(f"D3H file digest {name}", expected, _file_sha(root / name))


def _read_d3g_result(path: str | Path) -> dict[str, object]:
    document = _read_canonical_json(path)
    required = {"fingerprint", "source_d3c_bundle_fingerprint", "source_scientific_outcome_fingerprint",
        "source_d2i_seal_fingerprint", "d2j_receipt_hash", "d2j_policy_fingerprint",
        "holdout_commitment_fingerprint", "expected_holdout_authorization_id", "selected_trial_id"}
    if not required.issubset(document):
        raise D2KReplayReadinessIntegrityError("D3H D3G result lacks required frozen identities")
    for name in ("fingerprint", "source_d3c_bundle_fingerprint", "source_scientific_outcome_fingerprint",
        "source_d2i_seal_fingerprint", "d2j_receipt_hash", "d2j_policy_fingerprint", "holdout_commitment_fingerprint"):
        _require_hash(document[name], name)
    return document


def _read_evidence(path: str | Path, expected_version: str, version_key: str) -> dict[str, object]:
    document = _read_canonical_json(path)
    if document.get(version_key) != expected_version: raise D2KReplayReadinessIntegrityError("D3H evidence version drifted")
    _require_hash(document.get("fingerprint"), "fingerprint")
    return document


def _read_canonical_json(path: str | Path) -> dict[str, object]:
    target = Path(path)
    if not target.is_file() or target.stat().st_size > MAX_EVIDENCE_BYTES:
        raise D2KReplayReadinessIntegrityError("D3H evidence file missing or oversized")
    raw = target.read_text(encoding="utf-8")
    try: document = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except json.JSONDecodeError as exc: raise D2KReplayReadinessIntegrityError("D3H evidence is invalid JSON") from exc
    if not isinstance(document, dict): raise D2KReplayReadinessIntegrityError("D3H evidence must be an object")
    if raw != _canonical_json(document) + "\n": raise D2KReplayReadinessIntegrityError("D3H evidence serialization is not canonical")
    return document


def _write_evidence(document: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (_canonical_json(document) + "\n").encode("utf-8")
    if len(raw) > MAX_EVIDENCE_BYTES: raise D2KReplayReadinessGovernanceError("D3H evidence exceeds size bound")
    temporary = path.with_name(path.name + ".tmp"); temporary.write_bytes(raw); temporary.replace(path)


def _deny_preparation_authority(value: D2KReplayPreparationEvidence) -> None:
    if (value.qlib_runtime_used or value.development_labels_materialized or value.development_predictions_materialized
        or value.development_metrics_computed or value.final_holdout_private_material_loaded
        or value.protected_holdout_constructed or value.d2k_evaluator_invoked or value.holdout_permit_issued
        or value.holdout_permit_consumed or value.final_holdout_checkout_authorized
        or value.predictive_validation_passed or value.profitability_claim_authorized or value.promotion_authorized
        or value.execution_authorized or value.paper_execution_authorized):
        raise D2KReplayReadinessGovernanceError("D3H preparation exceeds replay-only authority")
    if value.capital_authority != "NONE" or value.live_trading != "BLOCKED": raise D2KReplayReadinessGovernanceError("D3H preparation cannot grant capital/LIVE")


def _deny_seal_authority(value: D2KReplayReadinessSeal) -> None:
    if (value.qlib_model_fit_performed or value.qlib_prediction_performed or value.development_labels_materialized
        or value.final_holdout_private_material_loaded or value.protected_holdout_constructed or value.d2k_evaluator_invoked
        or value.holdout_permit_issued or value.holdout_permit_consumed or value.final_holdout_checkout_authorized
        or value.predictive_validation_passed or value.profitability_claim_authorized or value.promotion_authorized
        or value.execution_authorized or value.paper_execution_authorized):
        raise D2KReplayReadinessGovernanceError("D3H seal exceeds readiness-only authority")
    if value.capital_authority != "NONE" or value.live_trading != "BLOCKED": raise D2KReplayReadinessGovernanceError("D3H seal cannot grant capital/LIVE")


def _file_sha(path: Path) -> str:
    if not path.is_file(): raise D2KReplayReadinessIntegrityError(f"missing D3H replay file: {path.name}")
    return sha256(path.read_bytes()).hexdigest()


def _hash(value: object) -> str: return sha256(_canonical_json(value).encode("utf-8")).hexdigest()

def _canonical_json(value: object) -> str:
    try: return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc: raise D2KReplayReadinessIntegrityError("D3H value is not canonical JSON") from exc

def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result: raise D2KReplayReadinessIntegrityError(f"duplicate D3H JSON key: {key}")
        result[key] = value
    return result

def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value): raise D2KReplayReadinessIntegrityError(f"{name} must be lowercase sha256")

def _require_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value): raise D2KReplayReadinessIntegrityError(f"invalid {name}")

def _required_string(document: Mapping[str, object], name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value: raise D2KReplayReadinessIntegrityError(f"D3H source lacks {name}")
    return value

def _require_equal(name: str, expected: object, actual: object) -> None:
    if expected != actual: raise D2KReplayReadinessIntegrityError(f"D3H {name} mismatch")
