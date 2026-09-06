"""OSS-3D2F concrete finite Qlib model-family preregistration.

D2F instantiates the abstract D2E comparison protocol with one explicit,
finite and immutable family of Qlib 0.9.7 LinearModel candidates.  It remains
core-side and Qlib-runtime-free: this module only defines canonical candidate
configs and builds OSS-3D2A DEVELOPMENT inference requests against already
frozen TRAIN/DEVELOPMENT artifacts.

Scientific/governance boundary:
- candidate family is fixed in source and cannot be expanded at runtime;
- no random/grid/Bayesian/adaptive search or hyperparameter optimization;
- DEVELOPMENT features only through OSS-3D2A; no DEVELOPMENT labels here;
- FINAL_HOLDOUT is structurally unobservable;
- all candidates share one caller-supplied isolated-runner code hash;
- model config hashes and request hashes are deterministic and auditable;
- no Qlib runtime, PnL, broker, OMS, Safety, PAPER, capital or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Iterable

from .oss3_development_inference import DevelopmentInferenceRequest
from .oss3_factor_matrix_artifact import FactorMatrixArtifact
from .oss3_training_bundle import TrainingBundleArtifact


OSS3D2F_FAMILY_VERSION = "OSS3D2F_CONCRETE_MODEL_FAMILY_V1"
OSS3D2F_EVIDENCE_VERSION = "OSS3D2F_CONCRETE_MODEL_REQUEST_SET_V1"
FAMILY_ID = "qlib-linear-finite-family-v1"
MODEL_FAMILY = "qlib_linear_finite_v1"
MODEL_IMPLEMENTATION = "qlib.contrib.model.linear.LinearModel"
QLIB_VERSION = "0.9.7"
PREDICTION_SEGMENT = "test"
FIT_INTERCEPT = True
INCLUDE_VALID = False
MIN_CANDIDATES = 2
MAX_CANDIDATES = 16

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_ALLOWED_ESTIMATORS = frozenset({"ols", "ridge", "lasso"})


class ConcreteModelFamilyError(RuntimeError):
    """Base OSS-3D2F failure."""


class ConcreteModelFamilyIntegrityError(ConcreteModelFamilyError):
    """Canonical candidate or request identity drifted."""


class ConcreteModelFamilyGovernanceError(ConcreteModelFamilyError):
    """Operation violates the finite DEVELOPMENT-only research boundary."""


@dataclass(frozen=True, slots=True)
class ConcreteModelCandidate:
    candidate_id: str
    estimator: str
    alpha: float

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not _ID_RE.fullmatch(self.candidate_id):
            raise ValueError("invalid candidate_id")
        if self.estimator not in _ALLOWED_ESTIMATORS:
            raise ConcreteModelFamilyGovernanceError("unsupported estimator in D2F family")
        if isinstance(self.alpha, bool) or not isinstance(self.alpha, (int, float)):
            raise TypeError("alpha must be numeric")
        numeric_alpha = float(self.alpha)
        if numeric_alpha < 0.0:
            raise ValueError("alpha must be non-negative")
        if self.estimator == "ols" and numeric_alpha != 0.0:
            raise ConcreteModelFamilyGovernanceError("OLS requires alpha=0")
        if self.estimator in {"ridge", "lasso"} and numeric_alpha <= 0.0:
            raise ConcreteModelFamilyGovernanceError("regularized estimators require alpha>0")

    @property
    def model_config(self) -> dict[str, object]:
        return {
            "implementation": MODEL_IMPLEMENTATION,
            "estimator": self.estimator,
            "alpha": float(self.alpha),
            "fit_intercept": FIT_INTERCEPT,
            "include_valid": INCLUDE_VALID,
            "prediction_segment": PREDICTION_SEGMENT,
        }

    @property
    def model_config_hash(self) -> str:
        return _hash(self.model_config)

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "model_family": MODEL_FAMILY,
            "model_config": self.model_config,
            "model_config_hash": self.model_config_hash,
            "required_qlib_version": QLIB_VERSION,
        }


# This tuple is the preregistered family. Changing it creates a new D2F version.
CANONICAL_CANDIDATES: tuple[ConcreteModelCandidate, ...] = (
    ConcreteModelCandidate("linear-lasso-a0p001", "lasso", 0.001),
    ConcreteModelCandidate("linear-lasso-a0p01", "lasso", 0.01),
    ConcreteModelCandidate("linear-ols", "ols", 0.0),
    ConcreteModelCandidate("linear-ridge-a0p1", "ridge", 0.1),
    ConcreteModelCandidate("linear-ridge-a1", "ridge", 1.0),
    ConcreteModelCandidate("linear-ridge-a10", "ridge", 10.0),
)


@dataclass(frozen=True, slots=True)
class ConcreteModelFamilyPlan:
    family_version: str
    family_id: str
    shared_runner_code_hash: str
    candidates: tuple[ConcreteModelCandidate, ...] = CANONICAL_CANDIDATES
    adaptive_search: bool = False
    hyperparameter_optimization: bool = False
    development_labels_observable: bool = False
    final_holdout_observable: bool = False
    research_only: bool = True
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.family_version != OSS3D2F_FAMILY_VERSION:
            raise ConcreteModelFamilyIntegrityError("noncanonical D2F family version")
        if self.family_id != FAMILY_ID:
            raise ConcreteModelFamilyIntegrityError("noncanonical D2F family id")
        _require_hash(self.shared_runner_code_hash, "shared_runner_code_hash")
        if self.candidates != CANONICAL_CANDIDATES:
            raise ConcreteModelFamilyGovernanceError(
                "D2F candidate family is immutable; create a new protocol version to change it"
            )
        if not MIN_CANDIDATES <= len(self.candidates) <= MAX_CANDIDATES:
            raise ConcreteModelFamilyGovernanceError("candidate family size outside D2F bounds")
        candidate_ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if candidate_ids != tuple(sorted(candidate_ids)):
            raise ConcreteModelFamilyIntegrityError("candidate order must be canonical")
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ConcreteModelFamilyIntegrityError("duplicate candidate_id")
        config_hashes = tuple(candidate.model_config_hash for candidate in self.candidates)
        if len(set(config_hashes)) != len(config_hashes):
            raise ConcreteModelFamilyIntegrityError("duplicate substantive model configuration")
        if self.adaptive_search or self.hyperparameter_optimization:
            raise ConcreteModelFamilyGovernanceError("D2F forbids adaptive model search")
        if self.development_labels_observable:
            raise ConcreteModelFamilyGovernanceError("D2F request construction cannot observe DEVELOPMENT labels")
        if self.final_holdout_observable:
            raise ConcreteModelFamilyGovernanceError("D2F cannot observe FINAL_HOLDOUT")
        if not self.research_only:
            raise ConcreteModelFamilyGovernanceError("D2F must remain research-only")
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def candidate(self, candidate_id: str) -> ConcreteModelCandidate:
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise ConcreteModelFamilyGovernanceError("candidate_id is outside frozen D2F family")

    def to_dict(self) -> dict[str, object]:
        return {
            "family_version": self.family_version,
            "family_id": self.family_id,
            "model_family": MODEL_FAMILY,
            "required_qlib_version": QLIB_VERSION,
            "shared_runner_code_hash": self.shared_runner_code_hash,
            "candidate_count": len(self.candidates),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "adaptive_search": self.adaptive_search,
            "hyperparameter_optimization": self.hyperparameter_optimization,
            "development_labels_observable": self.development_labels_observable,
            "final_holdout_observable": self.final_holdout_observable,
            "research_only": self.research_only,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class CandidateInferenceRequestBinding:
    candidate_id: str
    model_config_hash: str
    request: DevelopmentInferenceRequest

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.candidate_id):
            raise ValueError("invalid candidate_id")
        _require_hash(self.model_config_hash, "model_config_hash")
        m = self.request.manifest
        if m.model_family != MODEL_FAMILY:
            raise ConcreteModelFamilyIntegrityError("request model family mismatch")
        if m.model_config_hash != self.model_config_hash:
            raise ConcreteModelFamilyIntegrityError("request model config hash mismatch")
        if m.required_qlib_version != QLIB_VERSION:
            raise ConcreteModelFamilyIntegrityError("request Qlib version mismatch")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "model_config_hash": self.model_config_hash,
            "request_hash": self.request.request_hash,
            "request_manifest_hash": self.request.manifest.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ConcreteModelRequestSetEvidence:
    evidence_version: str
    family_fingerprint: str
    training_bundle_hash: str
    development_feature_artifact_hash: str
    shared_runner_code_hash: str
    bindings: tuple[CandidateInferenceRequestBinding, ...]
    development_labels_loaded: bool = False
    final_holdout_loaded: bool = False
    external_runtime_invoked: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D2F_EVIDENCE_VERSION:
            raise ConcreteModelFamilyIntegrityError("noncanonical D2F evidence version")
        for name in (
            "family_fingerprint",
            "training_bundle_hash",
            "development_feature_artifact_hash",
            "shared_runner_code_hash",
        ):
            _require_hash(getattr(self, name), name)
        candidate_ids = tuple(binding.candidate_id for binding in self.bindings)
        if candidate_ids != tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES):
            raise ConcreteModelFamilyIntegrityError("request set does not cover exact frozen family")
        if len({binding.request.request_hash for binding in self.bindings}) != len(self.bindings):
            raise ConcreteModelFamilyIntegrityError("candidate requests must be unique")
        if {binding.request.manifest.expected_runner_code_hash for binding in self.bindings} != {
            self.shared_runner_code_hash
        }:
            raise ConcreteModelFamilyIntegrityError("request set runner hash drifted")
        first = self.bindings[0].request.manifest
        for binding in self.bindings[1:]:
            manifest = binding.request.manifest
            for name in (
                "campaign_id",
                "research_split_hash",
                "training_bundle_hash",
                "development_feature_artifact_hash",
                "source_universe_hash",
                "feature_schema_hash",
                "label_definition_hash",
                "train_start",
                "train_end",
                "inference_start",
                "inference_end",
                "inference_keyset_hash",
                "inference_row_count",
            ):
                if getattr(manifest, name) != getattr(first, name):
                    raise ConcreteModelFamilyIntegrityError(f"candidate request common support drifted: {name}")
        if self.development_labels_loaded or self.final_holdout_loaded or self.external_runtime_invoked:
            raise ConcreteModelFamilyGovernanceError(
                "D2F request-set evidence cannot load evaluation labels/holdout or invoke Qlib"
            )
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
            "family_fingerprint": self.family_fingerprint,
            "training_bundle_hash": self.training_bundle_hash,
            "development_feature_artifact_hash": self.development_feature_artifact_hash,
            "shared_runner_code_hash": self.shared_runner_code_hash,
            "bindings": [binding.to_dict() for binding in self.bindings],
            "development_labels_loaded": self.development_labels_loaded,
            "final_holdout_loaded": self.final_holdout_loaded,
            "external_runtime_invoked": self.external_runtime_invoked,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


def build_concrete_model_request_set(
    *,
    training_bundle: TrainingBundleArtifact,
    development_features: FactorMatrixArtifact,
    shared_runner_code_hash: str,
) -> tuple[ConcreteModelFamilyPlan, ConcreteModelRequestSetEvidence]:
    """Build the exact six preregistered DEVELOPMENT inference requests.

    This is a contract-generation step only. It does not import or invoke Qlib.
    """
    plan = ConcreteModelFamilyPlan(
        family_version=OSS3D2F_FAMILY_VERSION,
        family_id=FAMILY_ID,
        shared_runner_code_hash=shared_runner_code_hash,
    )
    bindings = tuple(
        CandidateInferenceRequestBinding(
            candidate_id=candidate.candidate_id,
            model_config_hash=candidate.model_config_hash,
            request=DevelopmentInferenceRequest.build(
                training_bundle=training_bundle,
                development_features=development_features,
                model_family=MODEL_FAMILY,
                model_config_hash=candidate.model_config_hash,
                required_qlib_version=QLIB_VERSION,
                expected_runner_code_hash=shared_runner_code_hash,
            ),
        )
        for candidate in plan.candidates
    )
    evidence = ConcreteModelRequestSetEvidence(
        evidence_version=OSS3D2F_EVIDENCE_VERSION,
        family_fingerprint=plan.fingerprint,
        training_bundle_hash=training_bundle.artifact_hash,
        development_feature_artifact_hash=development_features.artifact_hash,
        shared_runner_code_hash=shared_runner_code_hash,
        bindings=bindings,
    )
    return plan, evidence


def verify_concrete_model_request_set(
    *,
    plan: ConcreteModelFamilyPlan,
    evidence: ConcreteModelRequestSetEvidence,
    training_bundle: TrainingBundleArtifact,
    development_features: FactorMatrixArtifact,
) -> None:
    """Rebuild every request and require byte-identity-equivalent fingerprints."""
    if evidence.family_fingerprint != plan.fingerprint:
        raise ConcreteModelFamilyIntegrityError("family fingerprint mismatch")
    if evidence.training_bundle_hash != training_bundle.artifact_hash:
        raise ConcreteModelFamilyIntegrityError("training bundle hash mismatch")
    if evidence.development_feature_artifact_hash != development_features.artifact_hash:
        raise ConcreteModelFamilyIntegrityError("DEVELOPMENT feature artifact hash mismatch")
    if evidence.shared_runner_code_hash != plan.shared_runner_code_hash:
        raise ConcreteModelFamilyIntegrityError("shared runner code hash mismatch")
    rebuilt_plan, rebuilt_evidence = build_concrete_model_request_set(
        training_bundle=training_bundle,
        development_features=development_features,
        shared_runner_code_hash=plan.shared_runner_code_hash,
    )
    if rebuilt_plan.fingerprint != plan.fingerprint:
        raise ConcreteModelFamilyIntegrityError("rebuilt family plan drifted")
    if rebuilt_evidence.fingerprint != evidence.fingerprint:
        raise ConcreteModelFamilyIntegrityError("rebuilt request set drifted")
    for binding in evidence.bindings:
        binding.request.verify_inputs(
            training_bundle=training_bundle,
            development_features=development_features,
        )


def canonical_candidate_config(candidate_id: str) -> dict[str, object]:
    """Return a copy of the immutable config for a frozen candidate id."""
    for candidate in CANONICAL_CANDIDATES:
        if candidate.candidate_id == candidate_id:
            return dict(candidate.model_config)
    raise ConcreteModelFamilyGovernanceError("candidate_id is outside frozen D2F family")


def all_candidate_config_hashes() -> tuple[str, ...]:
    return tuple(candidate.model_config_hash for candidate in CANONICAL_CANDIDATES)


def _deny_authority(
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise ConcreteModelFamilyGovernanceError("D2F cannot authorize execution")
    if capital_authority != "NONE" or live_trading != "BLOCKED":
        raise ConcreteModelFamilyGovernanceError("D2F cannot grant capital or LIVE authority")


def _require_hash(value: str, name: str) -> None:
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
