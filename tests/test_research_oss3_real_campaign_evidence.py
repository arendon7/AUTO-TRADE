from dataclasses import replace

import pytest

from autotrade.research.oss3_market_collection import canonical_oss3d2u_collection_plan
from autotrade.research.oss3_real_campaign_evidence import (
    ARTIFACT_EXPIRES_AT,
    ARTIFACT_ID,
    ARTIFACT_ZIP_SHA256,
    CAMPAIGN_SEAL_FINGERPRINT,
    COLLECTION_ID,
    D2U_PARTITION_MATERIAL_FINGERPRINT,
    DEVELOPMENT_UNIVERSE_HASH,
    DESCRIPTOR_COUNT,
    DESCRIPTOR_EVIDENCE_FILE_COUNT,
    D2X_CERTIFIED_COMMIT,
    EVIDENCE_TAR_SHA256,
    EXPECTED_SEAL_FINGERPRINT,
    OFFLINE_RESULT_SHA256,
    OPERATIONAL_WRAPPER_COMMIT,
    PLAN_FINGERPRINT,
    RealCampaignEvidenceSealError,
    SOURCE_FILE_COUNT,
    SOURCE_INVENTORY_SHA256,
    TRAINING_UNIVERSE_HASH,
    WORKFLOW_RUN_ID,
    canonical_oss3d2y_real_campaign_evidence_seal,
    verify_oss3d2y_real_campaign_evidence_seal,
)


def test_canonical_d2y_seal_binds_exact_real_campaign_and_current_d2u_v2_plan():
    seal = canonical_oss3d2y_real_campaign_evidence_seal()
    plan = canonical_oss3d2u_collection_plan()

    verify_oss3d2y_real_campaign_evidence_seal(seal)

    assert seal.d2x_certified_commit == D2X_CERTIFIED_COMMIT
    assert seal.operational_wrapper_commit == OPERATIONAL_WRAPPER_COMMIT
    assert seal.workflow_run_id == WORKFLOW_RUN_ID
    assert seal.artifact_id == ARTIFACT_ID
    assert seal.artifact_zip_sha256 == ARTIFACT_ZIP_SHA256
    assert seal.artifact_expires_at == ARTIFACT_EXPIRES_AT
    assert seal.collection_id == COLLECTION_ID == plan.collection_id
    assert seal.plan_fingerprint == PLAN_FINGERPRINT == plan.fingerprint
    assert seal.descriptor_count == DESCRIPTOR_COUNT == len(plan.descriptors) == 99
    assert seal.campaign_seal_fingerprint == CAMPAIGN_SEAL_FINGERPRINT
    assert seal.d2u_partition_material_fingerprint == D2U_PARTITION_MATERIAL_FINGERPRINT
    assert seal.training_universe_hash == TRAINING_UNIVERSE_HASH
    assert seal.development_universe_hash == DEVELOPMENT_UNIVERSE_HASH
    assert seal.source_inventory_sha256 == SOURCE_INVENTORY_SHA256
    assert seal.offline_result_sha256 == OFFLINE_RESULT_SHA256
    assert seal.evidence_tar_sha256 == EVIDENCE_TAR_SHA256
    assert seal.source_file_count == SOURCE_FILE_COUNT == 398
    assert seal.descriptor_evidence_file_count == DESCRIPTOR_EVIDENCE_FILE_COUNT == 396
    assert seal.fingerprint == EXPECTED_SEAL_FINGERPRINT


def test_d2y_proves_real_first_pass_and_zero_network_offline_reverification():
    seal = canonical_oss3d2y_real_campaign_evidence_seal()

    assert seal.first_pass_network_enabled is True
    assert seal.first_pass_acquired_from_network == 99
    assert seal.first_pass_reused_after_seal_reverification == 0
    assert seal.first_pass_missing_without_network_authority == 0
    assert seal.first_pass_complete is True

    assert seal.offline_network_enabled is False
    assert seal.offline_acquired_from_network == 0
    assert seal.offline_reused_after_seal_reverification == 99
    assert seal.offline_missing_without_network_authority == 0
    assert seal.offline_complete is True
    assert seal.same_campaign_seal is True
    assert seal.same_partition_material is True
    assert seal.same_training_universe is True
    assert seal.same_development_universe is True


def test_d2y_denies_holdout_promotion_execution_and_capital_authority():
    seal = canonical_oss3d2y_real_campaign_evidence_seal()

    assert seal.final_holdout_values_loaded is False
    assert seal.promotion_authorized is False
    assert seal.execution_authorized is False
    assert seal.paper_execution_authorized is False
    assert seal.capital_authority == "NONE"
    assert seal.live_trading == "BLOCKED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_run_id", WORKFLOW_RUN_ID + 1),
        ("artifact_id", ARTIFACT_ID + 1),
        ("artifact_zip_sha256", "0" * 64),
        ("campaign_seal_fingerprint", "1" * 64),
        ("d2u_partition_material_fingerprint", "2" * 64),
        ("training_universe_hash", "3" * 64),
        ("development_universe_hash", "4" * 64),
        ("source_inventory_sha256", "5" * 64),
        ("offline_result_sha256", "6" * 64),
        ("first_pass_acquired_from_network", 98),
        ("offline_reused_after_seal_reverification", 98),
        ("same_campaign_seal", False),
        ("same_partition_material", False),
        ("same_training_universe", False),
        ("same_development_universe", False),
        ("final_holdout_values_loaded", True),
        ("promotion_authorized", True),
        ("execution_authorized", True),
        ("paper_execution_authorized", True),
        ("capital_authority", "PAPER"),
        ("live_trading", "ENABLED"),
    ],
)
def test_d2y_rejects_crosswired_or_authority_escalated_seals(field, value):
    seal = canonical_oss3d2y_real_campaign_evidence_seal()
    tampered = replace(seal, **{field: value})

    with pytest.raises(RealCampaignEvidenceSealError):
        verify_oss3d2y_real_campaign_evidence_seal(tampered)


def test_d2y_rejects_structurally_invalid_hashes_and_evidence_counts():
    seal = canonical_oss3d2y_real_campaign_evidence_seal()

    with pytest.raises(RealCampaignEvidenceSealError):
        replace(seal, source_inventory_sha256="not-a-sha")
    with pytest.raises(RealCampaignEvidenceSealError):
        replace(seal, descriptor_evidence_file_count=395)
    with pytest.raises(RealCampaignEvidenceSealError):
        replace(seal, source_file_count=397)


def test_artifact_expiry_is_metadata_not_a_scientific_validity_gate():
    seal = canonical_oss3d2y_real_campaign_evidence_seal()

    assert seal.artifact_expires_at == "2026-10-09T04:14:03Z"
    # Verification intentionally does not consult the wall clock. The durable
    # cryptographic commitments remain valid after GitHub removes the convenience
    # artifact; downstream rehydration must reproduce the frozen identities.
    verify_oss3d2y_real_campaign_evidence_seal(seal)


def test_d2y_fingerprint_is_deterministic_and_changes_on_any_valid_tamper():
    seal = canonical_oss3d2y_real_campaign_evidence_seal()
    other = canonical_oss3d2y_real_campaign_evidence_seal()
    tampered = replace(seal, artifact_id=seal.artifact_id + 1)

    assert seal.fingerprint == other.fingerprint == EXPECTED_SEAL_FINGERPRINT
    assert tampered.fingerprint != seal.fingerprint
