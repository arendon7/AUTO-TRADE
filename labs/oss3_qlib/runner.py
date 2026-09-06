"""OSS-3D2B real Qlib integration runner.

This module lives outside the AUTO-TRADE core package.  It consumes only
canonical research artifacts and emits an OSS-3A prediction artifact plus an
OSS-3D2A binding receipt.  It deliberately does not initialize Qlib providers,
use qrun/workflow tracking, access DEVELOPMENT labels, or expose execution
surfaces.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from math import isfinite
import os
from pathlib import Path
from typing import Sequence

from autotrade.research.oss3_development_inference import (
    DevelopmentInferenceRequest,
    DevelopmentPredictionReceipt,
)
from autotrade.research.oss3_factor_matrix_artifact import FactorMatrixArtifact
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from autotrade.research.oss3_supervised_label_artifact import SupervisedLabelArtifact
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact

from .dataset_adapter import QlibArtifactDatasetAdapter
from .model_contract import (
    MODEL_CONFIG,
    MODEL_FAMILY,
    QLIB_VERSION,
    assert_request_model_contract,
    runner_code_hash,
)
from .network_guard import deny_network


SENSITIVE_ENV_PREFIXES = (
    "APCA_",
    "ALPACA_",
    "IBKR_",
    "BINANCE_",
    "BROKER_",
)


class QlibLabError(RuntimeError):
    """Base isolated-lab failure."""


class QlibLabGovernanceError(QlibLabError):
    """The invocation violates the no-authority/no-secret lab boundary."""


class QlibLabIntegrityError(QlibLabError):
    """Concrete artifacts or Qlib output drifted from the preregistered request."""


def run_isolated_qlib_lab(
    *,
    request_path: str | Path,
    training_bundle_path: str | Path,
    train_features_path: str | Path,
    train_labels_path: str | Path,
    development_features_path: str | Path,
    prediction_output_path: str | Path,
    receipt_output_path: str | Path | None = None,
) -> DevelopmentPredictionReceipt:
    """Fit the frozen Qlib ridge canary and produce DEVELOPMENT predictions."""
    _reject_broker_credentials()
    paths = _validate_paths(
        request_path=request_path,
        training_bundle_path=training_bundle_path,
        train_features_path=train_features_path,
        train_labels_path=train_labels_path,
        development_features_path=development_features_path,
        prediction_output_path=prediction_output_path,
        receipt_output_path=receipt_output_path,
    )

    request = DevelopmentInferenceRequest.read(paths["request"])
    bundle = TrainingBundleArtifact.read(paths["bundle"])
    train_features = FactorMatrixArtifact.read(paths["train_features"])
    train_labels = SupervisedLabelArtifact.read(paths["train_labels"])
    development_features = FactorMatrixArtifact.read(paths["development_features"])

    rebuilt_bundle = TrainingBundleArtifact.build(
        features=train_features,
        labels=train_labels,
    )
    if rebuilt_bundle.artifact_hash != bundle.artifact_hash:
        raise QlibLabIntegrityError("concrete TRAIN artifacts do not reproduce training bundle")
    if train_features.artifact_hash != bundle.manifest.feature_artifact_hash:
        raise QlibLabIntegrityError("TRAIN feature artifact hash mismatch")
    if train_labels.artifact_hash != bundle.manifest.label_artifact_hash:
        raise QlibLabIntegrityError("TRAIN label artifact hash mismatch")

    request.verify_inputs(
        training_bundle=bundle,
        development_features=development_features,
    )
    assert_request_model_contract(request.manifest)

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
            raise QlibLabIntegrityError(
                f"Qlib runtime version mismatch: expected {QLIB_VERSION}, got {actual_version!r}"
            )
        model = LinearModel(
            estimator=str(MODEL_CONFIG["estimator"]),
            alpha=float(MODEL_CONFIG["alpha"]),
            fit_intercept=bool(MODEL_CONFIG["fit_intercept"]),
            include_valid=bool(MODEL_CONFIG["include_valid"]),
        )
        model.fit(dataset)
        scores = model.predict(dataset, segment=str(MODEL_CONFIG["prediction_segment"]))

    rows = _prediction_rows(scores=scores, development_features=development_features)
    manifest = request.manifest
    prediction = QlibPredictionArtifact.build(
        qlib_version=QLIB_VERSION,
        model_family=MODEL_FAMILY,
        model_config_hash=manifest.model_config_hash,
        training_dataset_hash=bundle.artifact_hash,
        feature_schema_hash=manifest.feature_schema_hash,
        producer_code_hash=runner_code_hash(),
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
    prediction.write(paths["prediction_output"])
    if paths["receipt_output"] is not None:
        _write_receipt(paths["receipt_output"], receipt)
    return receipt


def _prediction_rows(*, scores: object, development_features: FactorMatrixArtifact) -> tuple[QlibPredictionRow, ...]:
    if not hasattr(scores, "index") or not hasattr(scores, "items"):
        raise QlibLabIntegrityError("Qlib LinearModel predict() did not return an indexed series")
    expected_keys = tuple((row.as_of, row.symbol) for row in development_features.rows)
    actual_keys: list[tuple[str, str]] = []
    values: list[float] = []
    for key, score in scores.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise QlibLabIntegrityError("Qlib prediction index must be (datetime, instrument)")
        timestamp, symbol = key
        if not hasattr(timestamp, "to_pydatetime"):
            raise QlibLabIntegrityError("Qlib prediction datetime index is invalid")
        observed = timestamp.to_pydatetime()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise QlibLabIntegrityError("Qlib prediction datetime must remain timezone-aware")
        actual_keys.append((observed.isoformat(), str(symbol)))
        numeric = float(score)
        if not isfinite(numeric):
            raise QlibLabIntegrityError("Qlib prediction score is non-finite")
        values.append(numeric)
    if tuple(actual_keys) != expected_keys:
        raise QlibLabIntegrityError("Qlib prediction index drifted from DEVELOPMENT keyset")
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
        raise QlibLabGovernanceError(
            "OSS-3D2B refuses to run with broker/exchange credential variables present: "
            + ",".join(present)
        )


def _validate_paths(**raw_paths: object) -> dict[str, Path | None]:
    inputs = {
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
    }
    normalized: dict[str, Path | None] = {}
    for name, value in mapping.items():
        if value is None:
            normalized[name] = None
            continue
        path = Path(value).expanduser().resolve()
        if name in inputs and not path.is_file():
            raise QlibLabIntegrityError(f"OSS-3D2B input does not exist: {name}")
        normalized[name] = path
    input_paths = [normalized[name] for name in sorted(inputs)]
    output_paths = [normalized["prediction_output"], normalized["receipt_output"]]
    for output in output_paths:
        if output is not None and output in input_paths:
            raise QlibLabGovernanceError("OSS-3D2B output may not overwrite an input artifact")
    if output_paths[1] is not None and output_paths[0] == output_paths[1]:
        raise QlibLabGovernanceError("prediction and receipt outputs must be distinct")
    assert normalized["prediction_output"] is not None
    return normalized


def _write_receipt(path: Path, receipt: DevelopmentPredictionReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "receipt": receipt.to_dict(),
        "receipt_fingerprint": receipt.fingerprint,
    }
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the isolated OSS-3D2B Qlib research canary")
    parser.add_argument("--request", required=True)
    parser.add_argument("--training-bundle", required=True)
    parser.add_argument("--train-features", required=True)
    parser.add_argument("--train-labels", required=True)
    parser.add_argument("--development-features", required=True)
    parser.add_argument("--prediction-output", required=True)
    parser.add_argument("--receipt-output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run_isolated_qlib_lab(
        request_path=args.request,
        training_bundle_path=args.training_bundle,
        train_features_path=args.train_features,
        train_labels_path=args.train_labels,
        development_features_path=args.development_features,
        prediction_output_path=args.prediction_output,
        receipt_output_path=args.receipt_output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
