from __future__ import annotations

import json

import pytest

from labs.oss3_qlib.d2k_replay_readiness import (
    D2KReplayPreparationEvidence,
    D2KReplayReadinessGovernanceError,
    D2KReplayReadinessIntegrityError,
    D2KReplayReadinessSeal,
    OSS3D3H_PREPARATION_VERSION,
    OSS3D3H_SEAL_VERSION,
    REVIEW_FRONTIER,
    _reject_duplicate_pairs,
)
from labs.oss3_qlib.family_model_contract import QLIB_VERSION

H = "0" * 64
I = "winner"
A = "oss3d2j:authorization"


def _preparation(**overrides):
    values = dict(
        evidence_version=OSS3D3H_PREPARATION_VERSION,
        review_frontier=REVIEW_FRONTIER,
        source_d3c_bundle_fingerprint=H,
        source_scientific_outcome_fingerprint=H,
        source_d2i_seal_fingerprint=H,
        source_d3g_evidence_fingerprint=H,
        d2j_receipt_hash=H,
        d2j_policy_fingerprint=H,
        holdout_commitment_fingerprint=H,
        expected_holdout_authorization_id=A,
        selected_trial_id=I,
        model_config_hash=H,
        winner_request_hash=H,
        expected_runner_code_hash=H,
        d2w_evidence_fingerprint=H,
        research_split_hash=H,
        raw_training_source_hash=H,
        raw_development_source_hash=H,
        d2r_receipt_hash=H,
        training_bundle_hash=H,
        training_feature_artifact_hash=H,
        training_label_artifact_hash=H,
        development_feature_artifact_hash=H,
        d2f_plan_fingerprint=H,
        d2f_request_set_fingerprint=H,
        request_file_sha256=H,
        training_bundle_file_sha256=H,
        training_feature_file_sha256=H,
        training_label_file_sha256=H,
        development_feature_file_sha256=H,
    )
    values.update(overrides)
    return D2KReplayPreparationEvidence(**values)


def _seal(**overrides):
    values = dict(
        seal_version=OSS3D3H_SEAL_VERSION,
        review_frontier=REVIEW_FRONTIER,
        preparation_fingerprint=H,
        source_d3c_bundle_fingerprint=H,
        source_d2i_seal_fingerprint=H,
        source_d3g_evidence_fingerprint=H,
        d2j_receipt_hash=H,
        d2j_policy_fingerprint=H,
        holdout_commitment_fingerprint=H,
        expected_holdout_authorization_id=A,
        selected_trial_id=I,
        model_config_hash=H,
        winner_request_hash=H,
        training_bundle_hash=H,
        training_feature_artifact_hash=H,
        training_label_artifact_hash=H,
        source_environment_attestation_hash=H,
        source_runtime_environment_hash=H,
        current_environment_attestation_hash=H,
        current_runtime_environment_hash=H,
        current_runner_code_hash=H,
        evaluator_semantic_hash=H,
        qlib_version=QLIB_VERSION,
        environment_reproduced_exactly=True,
        replay_inputs_reproduced_exactly=True,
    )
    values.update(overrides)
    return D2KReplayReadinessSeal(**values)


def test_preparation_is_deterministic_and_denies_authority():
    a = _preparation()
    b = _preparation()
    assert a == b
    assert a.fingerprint == b.fingerprint
    assert a.holdout_permit_issued is False
    assert a.final_holdout_private_material_loaded is False
    assert a.capital_authority == "NONE"
    assert a.live_trading == "BLOCKED"


@pytest.mark.parametrize(
    "field",
    [
        "qlib_runtime_used", "development_labels_materialized",
        "development_predictions_materialized", "development_metrics_computed",
        "final_holdout_private_material_loaded", "protected_holdout_constructed",
        "d2k_evaluator_invoked", "holdout_permit_issued", "holdout_permit_consumed",
        "final_holdout_checkout_authorized", "predictive_validation_passed",
        "profitability_claim_authorized", "promotion_authorized", "execution_authorized",
        "paper_execution_authorized",
    ],
)
def test_preparation_rejects_authority_escalation(field):
    with pytest.raises(D2KReplayReadinessGovernanceError):
        _preparation(**{field: True})


def test_seal_requires_exact_environment_identity():
    with pytest.raises(D2KReplayReadinessIntegrityError):
        _seal(current_environment_attestation_hash="1" * 64)
    with pytest.raises(D2KReplayReadinessIntegrityError):
        _seal(current_runtime_environment_hash="1" * 64)


@pytest.mark.parametrize(
    "field",
    [
        "qlib_model_fit_performed", "qlib_prediction_performed",
        "development_labels_materialized", "final_holdout_private_material_loaded",
        "protected_holdout_constructed", "d2k_evaluator_invoked", "holdout_permit_issued",
        "holdout_permit_consumed", "final_holdout_checkout_authorized",
        "predictive_validation_passed", "profitability_claim_authorized",
        "promotion_authorized", "execution_authorized", "paper_execution_authorized",
    ],
)
def test_seal_rejects_execution_or_holdout_escalation(field):
    with pytest.raises(D2KReplayReadinessGovernanceError):
        _seal(**{field: True})


def test_duplicate_json_keys_are_rejected():
    with pytest.raises(D2KReplayReadinessIntegrityError):
        json.loads('{"a":1,"a":2}', object_pairs_hook=_reject_duplicate_pairs)
