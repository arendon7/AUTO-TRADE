from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from labs.oss3_qlib.family_evaluation_batch import FrozenCandidateOutputBinding


def _binding() -> FrozenCandidateOutputBinding:
    return FrozenCandidateOutputBinding(
        candidate_id="linear-ols",
        request_hash="1" * 64,
        prediction_artifact_hash="2" * 64,
        prediction_receipt_hash="3" * 64,
        environment_attestation_hash="4" * 64,
        runtime_environment_hash="5" * 64,
        d2g_run_evidence_hash="6" * 64,
        model_config_hash="7" * 64,
        shared_runner_code_hash="8" * 64,
    )


def test_binding_is_frozen_and_has_no_nested_mutable_mapping():
    binding = _binding()
    with pytest.raises(FrozenInstanceError):
        binding.request_hash = "9" * 64
    assert isinstance(binding.to_dict(), dict)
    assert binding.to_dict()["candidate_id"] == "linear-ols"
    assert len(binding.fingerprint) == 64


def test_binding_rejects_non_hash_identity():
    with pytest.raises(ValueError, match="request_hash"):
        FrozenCandidateOutputBinding(
            candidate_id="linear-ols",
            request_hash="not-a-hash",
            prediction_artifact_hash="2" * 64,
            prediction_receipt_hash="3" * 64,
            environment_attestation_hash="4" * 64,
            runtime_environment_hash="5" * 64,
            d2g_run_evidence_hash="6" * 64,
            model_config_hash="7" * 64,
            shared_runner_code_hash="8" * 64,
        )
