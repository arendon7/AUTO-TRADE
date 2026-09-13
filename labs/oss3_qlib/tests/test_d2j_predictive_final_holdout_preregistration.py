from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from labs.oss3_qlib.d2j_predictive_final_holdout_preregistration import (
    EXPECTED_Q1_COMMITMENT,
    D2JPredictivePreregistrationIntegrityError,
    build_predictive_commitment_from_public_evidence,
    rehydrate_d3c_lineage,
)
from labs.oss3_qlib.development_winner_seal import seal_development_winner
from labs.oss3_qlib.protected_holdout_materialization import (
    ProtectedDualHoldoutMaterializationEvidence,
)
from labs.oss3_qlib.tests.d2i_fixture import build_completed_d2h_evidence


_REAL_D3F_PUBLIC = r'''{"capital_authority":"NONE","d2j_protocol_registered":false,"d2m_protocol_registered":false,"d2w_evidence_fingerprint":"ab132ff67e52cda64eb638982fc2ef14f38a2983a0b4852ae5bd55c025d8d3ff","d2y_full_offline_reverification_complete":true,"d2y_warmup_policy":"LAST_20_CERTIFIED_DECEMBER_2025_1H_BARS_PER_SYMBOL_V1","d3d_plan_fingerprint":"d6dc987cc2053a6617796fdb89df9467c0895a090c5745c91b157edc5d7a4af2","d3e_campaign_seal_fingerprint":"8ce57f1e6a6ce6999d8599af7df38b5b06662834739aa175cadd672c21eed105","d3e_certified_head":"129e546cc09257b11dfe6e96915d370b91126a3f","d3e_full_offline_reverification_complete":true,"economic_bar_count":2184,"economic_commitment_fingerprint":"349e277981ff08a4918eea68dd8fd86c745c2669f188b55727ac1b0ebfd70369","economic_holdout_observed":false,"economic_outcomes_observed":false,"economic_partition_end":"2026-07-01T00:00:00+00:00","economic_partition_start":"2026-04-01T00:00:00+00:00","economic_source_dataset_set_hash":"dc8018594b13d81d78afab985831db4295d6a3ba8ed5744a69d90de12a8434f7","economic_symbol_count":3,"economic_universe_hash":"dedf1e100ab64175b7759b67c2733f3e3141ef0841f39b4e597a8532cf5072da","evidence_version":"OSS3D3F_PROTECTED_DUAL_HOLDOUT_MATERIALIZATION_EVIDENCE_V1","execution_authorized":false,"feature_schema_hash":"847d083ab5c6ff5afb00311e8d2813b8bad9197a1d90af68149b75a454b5f7aa","final_holdout_observed":false,"fingerprint":"a9681ff31f4d74176d072c2076d129516d8db19cfb8aea093432ec6f7ac9d5b6","holdout_permit_consumed":false,"holdout_permit_issued":false,"label_definition_hash":"8db5d7e2e3c1d2eedf7a4c6f8b7e6838b4c4a955a46d43059a5e7863f9002b36","label_values_exposed":false,"live_trading":"BLOCKED","materialization_policy":"DETERMINISTIC_IN_MEMORY_VALUE_OPAQUE_COMMITMENT_ONLY_V1","paper_execution_authorized":false,"prediction_values_materialized":false,"predictive_commitment_fingerprint":"a6fef49419ef22b75be45385a805f8f3242653a3fcb74cb708be6548ac865bde","predictive_cross_section_count":2159,"predictive_cross_section_key_hash":"493997d59cc1d1583df646bbe14e6ac9c34f937bd8a16e0cf27a6dce62b2d75f","predictive_evaluation_keyset_hash":"cbb6f645c7ae0f9f31e02be7c2ed1570fe8050bc46f15679ab2cd79c0d3e5dfd","predictive_feature_artifact_hash":"2432170d56596c81d0a9bf189823974ddd59cb3e557651ebea646e8ebd80ba33","predictive_label_artifact_hash":"8764fab3881cc331e936c87c4e9bc8105c12b8c979226912bd17a8d25bf1ebbf","predictive_metrics_computed":false,"predictive_minimum_cross_section_observation_count":3,"predictive_partition_end":"2026-04-01T00:00:00+00:00","predictive_partition_start":"2026-01-01T00:00:00+00:00","predictive_protected_feature_values_materialized":true,"predictive_protected_label_values_materialized":true,"predictive_row_count":6477,"profitability_claim_authorized":false,"promotion_authorized":false,"q1_feature_policy":"EXACT_D2Q_20_BAR_CAUSAL_CLOSE_FEATURES_V1","q1_label_policy":"EXACT_D2R_ONE_BAR_FORWARD_CLOSE_RETURN_V1","q2_policy":"RAW_ALIGNED_MARKET_COMMITMENT_ONLY_NO_PREDICTION_OR_OUTCOME_V1","research_split_hash":"e514bbfe03649a4545b0ece709142baf389c1640b311e2988685bd4cf7f4706c","research_universe_identity_hash":"c3aa438b31fd9d168e84d5bb87b68b9153f110604070c1a199cac1f848071b71","temporal_separation_policy":"HALF_OPEN_CONTIGUOUS_Q1_Q2_NO_OVERLAP_V1","training_bundle_hash":"1dbadb75b0c62bdd9dfcdb0280608a6e598bc0cf73cea5238357bd12ff894eac"}'''


def _real_d3f_evidence() -> ProtectedDualHoldoutMaterializationEvidence:
    payload = json.loads(_REAL_D3F_PUBLIC)
    fingerprint = payload.pop("fingerprint")
    evidence = ProtectedDualHoldoutMaterializationEvidence(**payload)
    assert evidence.fingerprint == fingerprint
    return evidence


def test_exact_public_d3f_rebuilds_only_frozen_q1_commitment():
    evidence = _real_d3f_evidence()
    commitment = build_predictive_commitment_from_public_evidence(evidence)
    assert commitment.fingerprint == EXPECTED_Q1_COMMITMENT
    assert commitment.label_values_exposed is False
    assert commitment.final_holdout_observed is False
    assert commitment.row_count == 6477
    assert commitment.cross_section_count == 2159
    assert commitment.minimum_cross_section_observation_count == 3


def test_public_d3f_identity_drift_fails_before_protocol_registration():
    evidence = _real_d3f_evidence()
    drifted = replace(evidence, predictive_row_count=evidence.predictive_row_count - 3)
    with pytest.raises(D2JPredictivePreregistrationIntegrityError, match="exact certified D3F"):
        build_predictive_commitment_from_public_evidence(drifted)


def test_d3c_embedded_d2h_d2i_payloads_round_trip_to_exact_types(tmp_path):
    preregistration, batch = build_completed_d2h_evidence(tmp_path)
    winner = seal_development_winner(preregistration=preregistration, batch_evidence=batch)
    rebuilt_prereg, rebuilt_batch, rebuilt_winner = rehydrate_d3c_lineage(preregistration_payload=preregistration.to_dict(), batch_payload=batch.to_dict(), winner_payload=winner.to_dict())
    assert rebuilt_prereg == preregistration
    assert rebuilt_batch == batch
    assert rebuilt_winner == winner
    assert rebuilt_prereg.fingerprint == preregistration.fingerprint
    assert rebuilt_batch.fingerprint == batch.fingerprint
    assert rebuilt_winner.fingerprint == winner.fingerprint


def test_cross_wired_d3c_winner_payload_fails_closed(tmp_path):
    preregistration, batch = build_completed_d2h_evidence(tmp_path)
    winner = seal_development_winner(preregistration=preregistration, batch_evidence=batch)
    tampered = deepcopy(winner.to_dict())
    tampered["winner_primary_metric"] = float(tampered["winner_primary_metric"]) + 0.01
    with pytest.raises(D2JPredictivePreregistrationIntegrityError, match="winner payload"):
        rehydrate_d3c_lineage(preregistration_payload=preregistration.to_dict(), batch_payload=batch.to_dict(), winner_payload=tampered)
