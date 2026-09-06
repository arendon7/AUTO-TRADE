"""OSS-3D2G family-aware contract for the isolated Qlib runtime.

This module deliberately leaves the certified OSS-3D2B single-Ridge canary
untouched. D2G accepts only model configuration hashes preregistered by
OSS-3D2F and gives every candidate one shared semantic runner-code identity.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Mapping

from autotrade.research.oss3_concrete_model_family import (
    CANONICAL_CANDIDATES,
    MODEL_FAMILY,
    QLIB_VERSION,
    ConcreteModelCandidate,
)


OSS3D2G_CONTRACT_VERSION = "OSS3D2G_ISOLATED_FAMILY_MODEL_CONTRACT_V1"

# Every file that can alter candidate resolution, dataset exposure, network
# isolation, runtime execution or attestation semantics is part of the common
# runner identity. The D2F source itself is included so changing the frozen
# family necessarily changes the D2G runner hash.
SEMANTIC_FILES = (
    "src/autotrade/research/oss3_concrete_model_family.py",
    "labs/oss3_qlib/family_model_contract.py",
    "labs/oss3_qlib/dataset_adapter.py",
    "labs/oss3_qlib/network_guard.py",
    "labs/oss3_qlib/family_runner.py",
    "labs/oss3_qlib/family_environment_attestation.py",
    "labs/oss3_qlib/requirements.txt",
)


class FamilyModelContractError(RuntimeError):
    """A request or source tree drifted from the frozen D2F/D2G contract."""


def family_runner_code_hash(*, repo_root: Path | None = None) -> str:
    """Hash the common semantic runtime used by every D2F candidate."""
    root = Path(__file__).resolve().parents[2] if repo_root is None else Path(repo_root)
    digest = sha256()
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise FamilyModelContractError(f"OSS-3D2G semantic file is missing: {relative}")
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def candidate_from_config_hash(model_config_hash: str) -> ConcreteModelCandidate:
    """Resolve one source-frozen candidate by its canonical D2F config hash."""
    matches = tuple(
        candidate
        for candidate in CANONICAL_CANDIDATES
        if candidate.model_config_hash == model_config_hash
    )
    if len(matches) != 1:
        raise FamilyModelContractError(
            "OSS-3D2G request model_config_hash is outside the frozen D2F family"
        )
    return matches[0]


def assert_family_request_contract(manifest: object) -> ConcreteModelCandidate:
    """Fail closed unless a D2A request names one exact D2F candidate."""
    expected_common = {
        "model_family": MODEL_FAMILY,
        "required_qlib_version": QLIB_VERSION,
        "expected_runner_code_hash": family_runner_code_hash(),
    }
    for field, value in expected_common.items():
        actual = getattr(manifest, field, None)
        if actual != value:
            raise FamilyModelContractError(f"OSS-3D2G request {field} mismatch")
    config_hash = getattr(manifest, "model_config_hash", None)
    if not isinstance(config_hash, str):
        raise FamilyModelContractError("OSS-3D2G request model_config_hash is invalid")
    return candidate_from_config_hash(config_hash)


def candidate_runtime_config(candidate: ConcreteModelCandidate) -> Mapping[str, object]:
    """Return the exact immutable LinearModel kwargs/segment for a candidate."""
    canonical = candidate_from_config_hash(candidate.model_config_hash)
    if canonical.candidate_id != candidate.candidate_id:
        raise FamilyModelContractError("candidate identity/config hash mismatch")
    return dict(canonical.model_config)


def public_family_runtime_contract() -> Mapping[str, object]:
    return {
        "contract_version": OSS3D2G_CONTRACT_VERSION,
        "model_family": MODEL_FAMILY,
        "qlib_version": QLIB_VERSION,
        "runner_code_hash": family_runner_code_hash(),
        "candidate_count": len(CANONICAL_CANDIDATES),
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "model_config_hash": candidate.model_config_hash,
                "model_config": dict(candidate.model_config),
            }
            for candidate in CANONICAL_CANDIDATES
        ],
        "adaptive_search": False,
        "hyperparameter_optimization": False,
        "development_labels_observable": False,
        "final_holdout_observable": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
