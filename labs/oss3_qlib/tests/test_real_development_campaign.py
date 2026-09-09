from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from autotrade.research.oss3_concrete_model_family import CANONICAL_CANDIDATES, MODEL_FAMILY, QLIB_VERSION
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from labs.oss3_qlib.family_evaluation_batch import FrozenCandidateOutput, OSS3D2G_RUN_EVIDENCE_VERSION
from labs.oss3_qlib.family_model_contract import family_runner_code_hash
from labs.oss3_qlib.real_development_campaign import (
    MIN_EVALUABLE_CANDIDATES,
    OSS3D3A_CAMPAIGN_EVIDENCE_VERSION,
    STATUS_PRELABEL_BLOCKED,
    STRUCTURAL_FAILURE_CODE,
    STRUCTURAL_POLICY,
    PredictionStructuralProfile,
    RealDevelopmentCampaignEvidence,
    RealDevelopmentCampaignGovernanceError,
    prediction_structural_profile,
)
from labs.oss3_qlib.tests.d2i_fixture import (
    BASE,
    DEV_END,
    DEV_START,
    D2IRuntimeFreeRunEvidence,
    _candidate_output,
    _development_features,
    _train_features,
    _train_labels,
)
from autotrade.research.oss3_concrete_model_family import build_concrete_model_request_set
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact


def _outputs():
    train_features = _train_features()
    train_labels = _train_labels(train_features)
    bundle = TrainingBundleArtifact.build(features=train_features, labels=train_labels)
    development_features = _development_features()
    _, requests = build_concrete_model_request_set(
        training_bundle=bundle,
        development_features=development_features,
        shared_runner_code_hash=family_runner_code_hash(),
    )
    outputs = tuple(
        _candidate_output(
            index=index,
            binding=binding,
            bundle=bundle,
            development_features=development_features,
        )
        for index, binding in enumerate(requests.bindings)
    )
    return bundle, development_features, requests, outputs


def test_structural_profiles_accept_runtime_free_six_candidate_fixture():
    _, _, _, outputs = _outputs()
    profiles = tuple(prediction_structural_profile(output) for output in outputs)
    expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
    assert tuple(profile.candidate_id for profile in profiles) == expected_ids
    assert all(profile.evaluable for profile in profiles)
    assert all(profile.global_nonconstant for profile in profiles)
    assert all(profile.full_cross_section_support for profile in profiles)
    assert all(profile.failure_code is None for profile in profiles)
    assert MIN_EVALUABLE_CANDIDATES == 2


def test_constant_prediction_is_blocked_before_labels_without_retuning():
    bundle, development_features, requests, outputs = _outputs()
    original = outputs[0]
    binding = requests.bindings[0]
    constant_rows = tuple(
        QlibPredictionRow(timestamp=row.as_of, symbol=row.symbol, score=1.0)
        for row in development_features.rows
    )
    manifest = binding.request.manifest
    prediction = QlibPredictionArtifact.build(
        qlib_version=QLIB_VERSION,
        model_family=MODEL_FAMILY,
        model_config_hash=binding.model_config_hash,
        training_dataset_hash=bundle.artifact_hash,
        feature_schema_hash=manifest.feature_schema_hash,
        producer_code_hash=family_runner_code_hash(),
        train_start=datetime.fromisoformat(manifest.train_start),
        train_end=datetime.fromisoformat(manifest.train_end),
        inference_start=datetime.fromisoformat(manifest.inference_start),
        inference_end=datetime.fromisoformat(manifest.inference_end),
        rows=constant_rows,
    )
    receipt = binding.request.bind_prediction(
        prediction=prediction,
        training_bundle=bundle,
        development_features=development_features,
    )
    run_evidence = D2IRuntimeFreeRunEvidence(
        evidence_version=OSS3D2G_RUN_EVIDENCE_VERSION,
        candidate_id=binding.candidate_id,
        model_config_hash=binding.model_config_hash,
        shared_runner_code_hash=family_runner_code_hash(),
        request_hash=binding.request.request_hash,
        prediction_artifact_hash=prediction.artifact_hash,
        prediction_receipt_hash=receipt.fingerprint,
        environment_attestation_hash=original.attestation.artifact_hash,
        runtime_environment_hash=original.attestation.runtime_environment.fingerprint,
    )
    constant = FrozenCandidateOutput(
        candidate_id=binding.candidate_id,
        request=binding.request,
        prediction=prediction,
        receipt=receipt,
        attestation=original.attestation,
        run_evidence=run_evidence,
    )
    profile = prediction_structural_profile(constant)
    assert profile.evaluable is False
    assert profile.global_nonconstant is False
    assert profile.full_cross_section_support is False
    assert profile.failure_code == STRUCTURAL_FAILURE_CODE
    assert constant.request.request_hash == original.request.request_hash
    assert constant.request.manifest.model_config_hash == original.request.manifest.model_config_hash


def _prelabel_evidence() -> RealDevelopmentCampaignEvidence:
    ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
    profiles = tuple(
        PredictionStructuralProfile(
            candidate_id=candidate_id,
            prediction_artifact_hash=f"{index + 1:064x}",
            observation_count=9,
            cross_section_count=3,
            nonconstant_cross_section_count=3 if index == 0 else 0,
            global_nonconstant=index == 0,
            full_cross_section_support=index == 0,
            evaluable=index == 0,
            failure_code=None if index == 0 else STRUCTURAL_FAILURE_CODE,
        )
        for index, candidate_id in enumerate(ids)
    )
    return RealDevelopmentCampaignEvidence(
        evidence_version=OSS3D3A_CAMPAIGN_EVIDENCE_VERSION,
        status=STATUS_PRELABEL_BLOCKED,
        d2w_evidence_fingerprint="1" * 64,
        research_split_hash="2" * 64,
        raw_training_source_hash="3" * 64,
        raw_development_source_hash="4" * 64,
        d2r_receipt_hash="5" * 64,
        training_bundle_hash="6" * 64,
        training_feature_artifact_hash="7" * 64,
        training_label_artifact_hash="8" * 64,
        development_feature_artifact_hash="9" * 64,
        d2f_plan_fingerprint="a" * 64,
        d2f_request_set_fingerprint="b" * 64,
        candidate_output_hashes=tuple((candidate_id, f"{index + 20:064x}") for index, candidate_id in enumerate(ids)),
        structural_policy=STRUCTURAL_POLICY,
        structural_profiles=profiles,
        evaluable_candidate_ids=(ids[0],),
        d2s_preregistration_fingerprint=None,
        development_label_artifact_hash=None,
        d2h_preregistration_fingerprint=None,
        d2h_batch_evidence_fingerprint=None,
        d2s_completed_evidence_fingerprint=None,
        d2e_tournament_evidence_fingerprint=None,
        candidate_results=(),
        d2i_winner_seal_fingerprint=None,
        selected_trial_id=None,
        winner_primary_metric=None,
        winner_raw_p_value=None,
        winner_holm_adjusted_p_value=None,
        predictions_frozen_before_development_labels=True,
        development_labels_materialized=False,
        development_metrics_computed=False,
        family_retuned=False,
        fallback_candidate_used=False,
        reselection_allowed=False,
        statistical_significance_claim_authorized=False,
        profitability_claim_authorized=False,
        final_holdout_observed=False,
        final_holdout_authorized=False,
        holdout_permit_consumed=False,
        promotion_authorized=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )


def test_prelabel_blocked_evidence_exposes_no_labels_metrics_or_winner():
    evidence = _prelabel_evidence()
    assert evidence.status == STATUS_PRELABEL_BLOCKED
    assert len(evidence.evaluable_candidate_ids) == 1
    assert evidence.development_labels_materialized is False
    assert evidence.development_metrics_computed is False
    assert evidence.d2s_preregistration_fingerprint is None
    assert evidence.development_label_artifact_hash is None
    assert evidence.selected_trial_id is None
    assert evidence.final_holdout_observed is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"


def test_d3a_evidence_rejects_authority_retuning_and_postlabel_state_on_blocked_campaign():
    evidence = _prelabel_evidence()
    for updates in (
        {"family_retuned": True},
        {"fallback_candidate_used": True},
        {"reselection_allowed": True},
        {"statistical_significance_claim_authorized": True},
        {"profitability_claim_authorized": True},
        {"final_holdout_observed": True},
        {"final_holdout_authorized": True},
        {"holdout_permit_consumed": True},
        {"promotion_authorized": True},
        {"execution_authorized": True},
        {"paper_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"development_labels_materialized": True},
    ):
        with pytest.raises(RealDevelopmentCampaignGovernanceError):
            replace(evidence, **updates)



def test_d2e_exact_sign_test_supports_full_hourly_development_without_float_overflow():
    from autotrade.research.oss3_development_model_tournament import _one_sided_exact_sign_test

    # 8,760 non-zero cross-sectional signs approximate a full hourly year and
    # reproduces the magnitude that overflowed the legacy float(2**n) path.
    values = (1.0,) * 4380 + (-1.0,) * 4380
    p_value = _one_sided_exact_sign_test(values)
    assert 0.0 <= p_value <= 1.0
    assert p_value > 0.5

    # Small-n semantics remain the exact same one-sided binomial tail.
    assert _one_sided_exact_sign_test((1.0, 1.0, -1.0)) == 0.5
