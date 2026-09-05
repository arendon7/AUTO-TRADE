"""Frozen model/runtime identity for the OSS-3D2B Qlib integration canary."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping


QLIB_VERSION = "0.9.7"
MODEL_FAMILY = "qlib_linear_ridge_v1"
MODEL_CONFIG = {
    "implementation": "qlib.contrib.model.linear.LinearModel",
    "estimator": "ridge",
    "alpha": 1.0,
    "fit_intercept": True,
    "include_valid": False,
    "prediction_segment": "test",
}
SEMANTIC_FILES = (
    "model_contract.py",
    "dataset_adapter.py",
    "network_guard.py",
    "runner.py",
    "requirements.txt",
)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def model_config_hash() -> str:
    return sha256(canonical_json(MODEL_CONFIG)).hexdigest()


def runner_code_hash(*, lab_root: Path | None = None) -> str:
    """Hash the exact semantic file set used by the isolated runner.

    The hash is deliberately computed from file bytes and relative names rather
    than embedded in source, avoiding a self-referential commit/hash contract.
    """
    root = Path(__file__).resolve().parent if lab_root is None else Path(lab_root)
    digest = sha256()
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"OSS-3D2B semantic file is missing: {relative}")
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def assert_request_model_contract(manifest: object) -> None:
    """Fail closed if a D2A request asks this runner to execute another model."""
    expected = {
        "model_family": MODEL_FAMILY,
        "model_config_hash": model_config_hash(),
        "required_qlib_version": QLIB_VERSION,
        "expected_runner_code_hash": runner_code_hash(),
    }
    for field, value in expected.items():
        actual = getattr(manifest, field, None)
        if actual != value:
            raise RuntimeError(f"OSS-3D2B request {field} mismatch")


def public_model_contract() -> Mapping[str, object]:
    return {
        "model_family": MODEL_FAMILY,
        "model_config": dict(MODEL_CONFIG),
        "model_config_hash": model_config_hash(),
        "qlib_version": QLIB_VERSION,
        "runner_code_hash": runner_code_hash(),
        "adaptive_search": False,
        "hyperparameter_optimization": False,
    }
