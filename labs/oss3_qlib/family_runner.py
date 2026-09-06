"""OSS-3D2G isolated Qlib runner for the exact OSS-3D2F family.

One invocation consumes one preregistered OSS-3D2A request. The request's
model_config_hash is resolved only against the source-frozen D2F family; no
runtime hyperparameters are accepted. The runner uses TRAIN labels and
DEVELOPMENT features, never DEVELOPMENT labels or FINAL_HOLDOUT data.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from autotrade.research.oss3_development_inference import (
    DevelopmentInferenceRequest,
    DevelopmentPredictionReceipt,
)
from autotrade.research.oss3_factor_matrix_artifact import FactorMatrixArtifact
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from autotrade.research.oss3_supervised_label_artifact import SupervisedLabelArtifact
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact

from .dataset_adapter import QlibArtifactDatasetAdapter
from .family_environment_attestation import (
    CandidateEnvironmentAttestation,
    collect_candidate_environment_attestation,
)
from .family_model_contract import (
    QLIB_VERSION,
    candidate_runtime_config,
    assert_family_request_contract,
    family_runner_code_hash,
)
from .network_guard import deny_network


OSS3D2G_RUN_EVIDENCE_VERSION = "OSS3D2G_CANDIDATE_RUN_EVIDENCE_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SENSITIVE_ENV_PREFIXES = (
    "APCA_",
    "ALPACA_",
    "IBKR_",
    "BINANCE_",
    "COINBASE_",
    "KRAKEN_",
    "BYBIT_",
    "OKX_",
    "BITGET_",
    "KUCOIN_",
    "BROKER_",
)


class QlibFamilyLabError(RuntimeError):
    """Base isolated D2G lab failure."""


class QlibFamilyLabGovernanceError(QlibFamilyLabError):
    """Invocation violates the research-only/no-secret D2G boundary."""


class QlibFamilyLabIntegrityError(QlibFamilyLabError):
    """Concrete artifacts or Qlib output drifted from the frozen request."""


@dataclass(frozen=True, slots=True)
class FamilyCandidateRunEvidence:
    evidence_version: str
    candidate_id: str
    model_config_hash: str
    shared_runner_code_hash: str
    request_hash: str
    prediction_artifact_hash: str
    prediction_receipt_hash: str
    environment_attestation_hash: str
    runtime_environment_hash: str
    development_labels_loaded: bool = False
    final_holdout_loaded: bool = False
    broker_credentials_present: bool = False
    network_allowed: bool = False
    adaptive_search: bool = False
    hyperparameter_optimization: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D2G_RUN_EVIDENCE_VERSION:
            raise QlibFamilyLabIntegrityError("noncanonical D2G run evidence version")
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError("candidate_id must be non-empty")
        for name in (
            "model_config_hash",
            "shared_runner_code_hash",
            "request_hash",
            "prediction_artifact_hash",
            "prediction_receipt_hash",
            "environment_attestation_hash",
            "runtime_environment_hash",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise ValueError(f"invalid {name}")
        if (
            self.development_labels_loaded
            or self.final_holdout_loaded
            or self.broker_credentials_present
            or self.network_allowed
            or self.adaptive_search
            or self.hyperparameter_optimization
        ):
            raise QlibFamilyLabGovernanceError("D2G evidence violates frozen isolated research boundary")
        if self.execution_authorized or self.paper_execution_authorized:
            raise QlibFamilyLabGovernanceError("D2G cannot authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise QlibFamilyLabGovernanceError("D2G cannot grant capital or LIVE authority")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_version": self.evidence_version,
            "candidate_id": self.candidate_id,
            "model_config_hash": self.model_config_hash,
            "shared_runner_code_hash": self.shared_runner_code_hash,
            "request_hash": self.request_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "prediction_receipt_hash": self.prediction_receipt_hash,
            "environment_attestation_hash": self.environment_attestation_hash,
            "runtime_environment_hash": self.runtime_environment_hash,
            "development_labels_loaded": self.development_labels_loaded,
            "final_holdout_loaded": self.final_holdout_loaded,
            "broker_credentials_present": self.broker_credentials_present,
            "network_allowed": self.network_allowed,
            "adaptive_search": self.adaptive_search,
            "hyperparameter_optimization": self.hyperparameter_optimization,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


def run_isolated_qlib_family_candidate(
    *,
    request_path: str | Path,
    training_bundle_path: str | Path,
    train_features_path: str | Path,
    train_labels_path: str | Path,
    development_features_path: str | Path,
    prediction_output_path: str | Path,
    receipt_output_path: str | Path,
    environment_attestation_output_path: str | Path,
    runtime_identity_output_path: str | Path,
    run_evidence_output_path: str | Path,
) -> FamilyCandidateRunEvidence:
    """Execute exactly one source-preregistered D2F candidate in isolation."""
    _reject_broker_credentials()
    paths = _validate_paths(
        request_path=request_path,
        training_bundle_path=training_bundle_path,
        train_features_path=train_features_path,
        train_labels_path=train_labels_path,
        development_features_path=development_features_path,
        prediction_output_path=prediction_output_path,
        receipt_output_path=receipt_output_path,
        environment_attestation_output_path=environment_attestation_output_path,
        runtime_identity_output_path=runtime_identity_output_path,
        run_evidence_output_path=run_evidence_output_path,
    )

    request = DevelopmentInferenceRequest.read(paths["request"])
    bundle = TrainingBundleArtifact.read(paths["bundle"])
    train_features = FactorMatrixArtifact.read(paths["train_features"])
    train_labels = SupervisedLabelArtifact.read(paths["train_labels"])
    development_features = FactorMatrixArtifact.read(paths["development_features"])

    rebuilt_bundle = TrainingBundleArtifact.build(features=train_features, labels=train_labels)
    if rebuilt_bundle.artifact_hash != bundle.artifact_hash:
        raise QlibFamilyLabIntegrityError("concrete TRAIN artifacts do not reproduce training bundle")
    if train_features.artifact_hash != bundle.manifest.feature_artifact_hash:
        raise QlibFamilyLabIntegrityError("TRAIN feature artifact hash mismatch")
    if train_labels.artifact_hash != bundle.manifest.label_artifact_hash:
        raise QlibFamilyLabIntegrityError("TRAIN label artifact hash mismatch")

    request.verify_inputs(training_bundle=bundle, development_features=development_features)
    candidate = assert_family_request_contract(request.manifest)
    config = candidate_runtime_config(candidate)

    dataset = QlibArtifactDatasetAdapter(
        train_features=train_features,
        train_labels=train_labels,
        development_features=development_features,
    )

    with deny_network():
        import qlib
        from qlib.contrib.model.linear import LinearModel

        actual_version = str(getattr(qlib, "__version__", ""))
        if actual_version != QLIB_VERSION:
            raise QlibFamilyLabIntegrityError(
                f"Qlib runtime version mismatch: expected {QLIB_VERSION}, got {actual_version!r}"
            )
        model = LinearModel(
            estimator=str(config["estimator"]),
            alpha=float(config["alpha"]),
            fit_intercept=bool(config["fit_intercept"]),
            include_valid=bool(config["include_valid"]),
        )
        model.fit(dataset)
        scores = model.predict(dataset, segment=str(config["prediction_segment"]))

    rows = _prediction_rows(scores=scores, development_features=development_features)
    manifest = request.manifest
    shared_runner_hash = family_runner_code_hash()
    prediction = QlibPredictionArtifact.build(
        qlib_version=QLIB_VERSION,
        model_family=manifest.model_family,
        model_config_hash=manifest.model_config_hash,
        training_dataset_hash=bundle.artifact_hash,
        feature_schema_hash=manifest.feature_schema_hash,
        producer_code_hash=shared_runner_hash,
        train_start=datetime.fromisoformat(manifest.train_start),
        train_end=datetime.fromisoformat(manifest.train_end),
        inference_start=datetime.fromisoformat(manifest.inference_start),
        inference_end=datetime.fromisoformat(manifest.inference_end),
        rows=rows,
    )
    receipt = request.bind_prediction(
        prediction=prediction,
        training_bundle=bundle,
        development_features=development_features,
    )

    attestation = collect_candidate_environment_attestation(
        model_config_hash=manifest.model_config_hash
    )
    attestation.verify_current_contract()
    if attestation.manifest.runner_code_hash != manifest.expected_runner_code_hash:
        raise QlibFamilyLabIntegrityError("environment attestation runner hash differs from request")
    runtime_identity = attestation.runtime_environment

    evidence = FamilyCandidateRunEvidence(
        evidence_version=OSS3D2G_RUN_EVIDENCE_VERSION,
        candidate_id=candidate.candidate_id,
        model_config_hash=manifest.model_config_hash,
        shared_runner_code_hash=shared_runner_hash,
        request_hash=request.request_hash,
        prediction_artifact_hash=prediction.artifact_hash,
        prediction_receipt_hash=receipt.fingerprint,
        environment_attestation_hash=attestation.artifact_hash,
        runtime_environment_hash=runtime_identity.fingerprint,
    )

    prediction.write(paths["prediction_output"])
    _write_receipt(paths["receipt_output"], receipt)
    attestation.write(paths["environment_attestation_output"])
    _write_runtime_identity(paths["runtime_identity_output"], runtime_identity)
    _write_evidence(paths["run_evidence_output"], evidence)
    return evidence


def verify_family_candidate_outputs(
    *,
    request: DevelopmentInferenceRequest,
    prediction: QlibPredictionArtifact,
    receipt: DevelopmentPredictionReceipt,
    attestation: CandidateEnvironmentAttestation,
    evidence: FamilyCandidateRunEvidence,
) -> None:
    """Rebind all D2G outputs to one request and the current frozen runtime."""
    candidate = assert_family_request_contract(request.manifest)
    attestation.verify_current_contract()
    if prediction.manifest.model_family != request.manifest.model_family:
        raise QlibFamilyLabIntegrityError("prediction model family mismatch")
    if prediction.manifest.model_config_hash != request.manifest.model_config_hash:
        raise QlibFamilyLabIntegrityError("prediction model config mismatch")
    if prediction.manifest.producer_code_hash != family_runner_code_hash():
        raise QlibFamilyLabIntegrityError("prediction producer code hash mismatch")
    if receipt.request_hash != request.request_hash:
        raise QlibFamilyLabIntegrityError("receipt request hash mismatch")
    if receipt.prediction_artifact_hash != prediction.artifact_hash:
        raise QlibFamilyLabIntegrityError("receipt prediction artifact mismatch")
    if attestation.manifest.model_config_hash != request.manifest.model_config_hash:
        raise QlibFamilyLabIntegrityError("attestation model config mismatch")
    if attestation.manifest.runner_code_hash != request.manifest.expected_runner_code_hash:
        raise QlibFamilyLabIntegrityError("attestation runner hash mismatch")
    expected = FamilyCandidateRunEvidence(
        evidence_version=OSS3D2G_RUN_EVIDENCE_VERSION,
        candidate_id=candidate.candidate_id,
        model_config_hash=request.manifest.model_config_hash,
        shared_runner_code_hash=request.manifest.expected_runner_code_hash,
        request_hash=request.request_hash,
        prediction_artifact_hash=prediction.artifact_hash,
        prediction_receipt_hash=receipt.fingerprint,
        environment_attestation_hash=attestation.artifact_hash,
        runtime_environment_hash=attestation.runtime_environment.fingerprint,
    )
    if evidence.fingerprint != expected.fingerprint:
        raise QlibFamilyLabIntegrityError("D2G run evidence does not rebind to concrete outputs")


def _prediction_rows(
    *, scores: object, development_features: FactorMatrixArtifact
) -> tuple[QlibPredictionRow, ...]:
    if not hasattr(scores, "index") or not hasattr(scores, "items"):
        raise QlibFamilyLabIntegrityError("Qlib LinearModel predict() did not return an indexed series")
    expected_keys = tuple((row.as_of, row.symbol) for row in development_features.rows)
    actual_keys: list[tuple[str, str]] = []
    values: list[float] = []
    for key, score in scores.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise QlibFamilyLabIntegrityError("Qlib prediction index must be (datetime, instrument)")
        timestamp, symbol = key
        if not hasattr(timestamp, "to_pydatetime"):
            raise QlibFamilyLabIntegrityError("Qlib prediction datetime index is invalid")
        observed = timestamp.to_pydatetime()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise QlibFamilyLabIntegrityError("Qlib prediction datetime must remain timezone-aware")
        actual_keys.append((observed.isoformat(), str(symbol)))
        numeric = float(score)
        if not isfinite(numeric):
            raise QlibFamilyLabIntegrityError("Qlib prediction score is non-finite")
        values.append(numeric)
    if tuple(actual_keys) != expected_keys:
        raise QlibFamilyLabIntegrityError("Qlib prediction index drifted from DEVELOPMENT keyset")
    return tuple(
        QlibPredictionRow(timestamp=timestamp, symbol=symbol, score=score)
        for (timestamp, symbol), score in zip(actual_keys, values, strict=True)
    )


def _reject_broker_credentials() -> None:
    present = sorted(
        key
        for key, value in os.environ.items()
        if value and any(key.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES)
    )
    if present:
        raise QlibFamilyLabGovernanceError(
            "OSS-3D2G refuses broker/exchange credential variables: " + ",".join(present)
        )


def _validate_paths(**raw_paths: object) -> dict[str, Path]:
    input_names = {
        "request",
        "bundle",
        "train_features",
        "train_labels",
        "development_features",
    }
    mapping = {
        "request": raw_paths["request_path"],
        "bundle": raw_paths["training_bundle_path"],
        "train_features": raw_paths["train_features_path"],
        "train_labels": raw_paths["train_labels_path"],
        "development_features": raw_paths["development_features_path"],
        "prediction_output": raw_paths["prediction_output_path"],
        "receipt_output": raw_paths["receipt_output_path"],
        "environment_attestation_output": raw_paths["environment_attestation_output_path"],
        "runtime_identity_output": raw_paths["runtime_identity_output_path"],
        "run_evidence_output": raw_paths["run_evidence_output_path"],
    }
    normalized: dict[str, Path] = {}
    for name, value in mapping.items():
        path = Path(value).expanduser().resolve()
        if name in input_names and not path.is_file():
            raise QlibFamilyLabIntegrityError(f"OSS-3D2G input does not exist: {name}")
        normalized[name] = path
    inputs = {normalized[name] for name in input_names}
    outputs = [normalized[name] for name in normalized if name.endswith("_output")]
    if len(outputs) != len(set(outputs)):
        raise QlibFamilyLabGovernanceError("all D2G output paths must be distinct")
    if any(output in inputs for output in outputs):
        raise QlibFamilyLabGovernanceError("D2G output may not overwrite an input artifact")
    return normalized


def _write_receipt(path: Path, receipt: DevelopmentPredictionReceipt) -> None:
    _write_json(
        path,
        {"receipt": receipt.to_dict(), "receipt_fingerprint": receipt.fingerprint},
    )


def _write_runtime_identity(path: Path, runtime_identity: object) -> None:
    to_dict = getattr(runtime_identity, "to_dict", None)
    fingerprint = getattr(runtime_identity, "fingerprint", None)
    if not callable(to_dict) or not isinstance(fingerprint, str):
        raise QlibFamilyLabIntegrityError("invalid runtime environment identity")
    _write_json(
        path,
        {"runtime_environment": to_dict(), "runtime_environment_hash": fingerprint},
    )


def _write_evidence(path: Path, evidence: FamilyCandidateRunEvidence) -> None:
    _write_json(
        path,
        {"evidence": evidence.to_dict(), "evidence_fingerprint": evidence.fingerprint},
    )


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(raw, encoding="utf-8")
    temporary.replace(path)


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one exact OSS-3D2F candidate in the isolated OSS-3D2G Qlib lab"
    )
    parser.add_argument("--request", required=True)
    parser.add_argument("--training-bundle", required=True)
    parser.add_argument("--train-features", required=True)
    parser.add_argument("--train-labels", required=True)
    parser.add_argument("--development-features", required=True)
    parser.add_argument("--prediction-output", required=True)
    parser.add_argument("--receipt-output", required=True)
    parser.add_argument("--environment-attestation-output", required=True)
    parser.add_argument("--runtime-identity-output", required=True)
    parser.add_argument("--run-evidence-output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run_isolated_qlib_family_candidate(
        request_path=args.request,
        training_bundle_path=args.training_bundle,
        train_features_path=args.train_features,
        train_labels_path=args.train_labels,
        development_features_path=args.development_features,
        prediction_output_path=args.prediction_output,
        receipt_output_path=args.receipt_output,
        environment_attestation_output_path=args.environment_attestation_output,
        runtime_identity_output_path=args.runtime_identity_output,
        run_evidence_output_path=args.run_evidence_output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
