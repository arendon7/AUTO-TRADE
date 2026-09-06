from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from labs.oss3_qlib.development_winner_seal import seal_development_winner
from labs.oss3_qlib.final_holdout_protocol import (
    FINAL_HOLDOUT_SPLIT,
    FINAL_VALIDATION_PURPOSE,
    MAX_ONE_SIDED_SIGN_TEST_P_VALUE,
    MIN_CROSS_SECTION_OBSERVATIONS,
    MIN_HOLDOUT_CROSS_SECTIONS,
    MIN_HOLDOUT_TOTAL_OBSERVATIONS,
    MIN_MEAN_CROSS_SECTIONAL_RANK_IC,
    MIN_NONZERO_RANK_IC_CROSS_SECTIONS,
    OSS3D2J_COMMITMENT_VERSION,
    OSS3D2J_CONTRACT_VERSION,
    OSS3D2J_WINNER_BINDING_VERSION,
    OSS3FinalHoldoutProtocolConflict,
    OSS3FinalHoldoutProtocolGovernanceError,
    OSS3FinalHoldoutProtocolIntegrityError,
    OSS3FinalHoldoutProtocolPolicy,
    OSS3ProtectedFinalHoldoutCommitment,
    SQLiteOSS3FinalHoldoutProtocolRegistry,
    canonical_oss3d2j_policy,
    read_oss3d2j_protocol_read_only,
)
from labs.oss3_qlib.tests.d2i_fixture import build_completed_d2h_evidence


def _source(tmp_path):
    preregistration, batch = build_completed_d2h_evidence(tmp_path)
    seal = seal_development_winner(
        preregistration=preregistration,
        batch_evidence=batch,
    )
    return preregistration, batch, seal


def _holdout_commitment(preregistration, **updates):
    dataset = preregistration.d2e_plan.dataset
    development_end = datetime.fromisoformat(dataset.evaluation_end)
    values = {
        "commitment_version": OSS3D2J_COMMITMENT_VERSION,
        "source_campaign_id": dataset.source_campaign_id,
        "research_split_hash": dataset.research_split_hash,
        "source_universe_hash": dataset.source_universe_hash,
        "label_definition_hash": dataset.label_definition_hash,
        "feature_artifact_hash": "1" * 64,
        "label_artifact_hash": "2" * 64,
        "evaluation_keyset_hash": "3" * 64,
        "cross_section_key_hash": "4" * 64,
        "partition_start": development_end.isoformat(),
        "partition_end": (development_end + timedelta(days=40)).isoformat(),
        "row_count": 120,
        "cross_section_count": 40,
        "minimum_cross_section_observation_count": 3,
        "label_values_exposed": False,
        "final_holdout_observed": False,
    }
    values.update(updates)
    return OSS3ProtectedFinalHoldoutCommitment(**values)


def _record(tmp_path):
    preregistration, batch, seal = _source(tmp_path)
    commitment = _holdout_commitment(preregistration)
    registry = SQLiteOSS3FinalHoldoutProtocolRegistry(tmp_path / "d2j.sqlite3")
    receipt = registry.preregister_and_record(
        protocol_id="oss3d2j-protocol-001",
        seal=seal,
        preregistration=preregistration,
        batch_evidence=batch,
        holdout_commitment=commitment,
    )
    return preregistration, batch, seal, commitment, registry, receipt


def test_policy_is_frozen_predictive_single_candidate_only():
    policy = canonical_oss3d2j_policy()
    assert policy.primary_metric == "mean_cross_sectional_rank_ic"
    assert policy.min_mean_cross_sectional_rank_ic == MIN_MEAN_CROSS_SECTIONAL_RANK_IC == 0.02
    assert policy.max_one_sided_sign_test_p_value == MAX_ONE_SIDED_SIGN_TEST_P_VALUE == 0.05
    assert policy.min_holdout_cross_sections == MIN_HOLDOUT_CROSS_SECTIONS == 30
    assert policy.min_holdout_total_observations == MIN_HOLDOUT_TOTAL_OBSERVATIONS == 90
    assert policy.min_cross_section_observations == MIN_CROSS_SECTION_OBSERVATIONS == 3
    assert policy.min_nonzero_rank_ic_cross_sections == MIN_NONZERO_RANK_IC_CROSS_SECTIONS == 20
    assert policy.max_evaluations == 1
    assert policy.reselection_allowed is False
    assert policy.retuning_allowed is False
    assert policy.fallback_candidate_allowed is False
    assert policy.second_attempt_allowed is False
    assert policy.failure_is_terminal is True
    assert policy.split_name == FINAL_HOLDOUT_SPLIT
    assert policy.permit_purpose == FINAL_VALIDATION_PURPOSE


@pytest.mark.parametrize(
    "updates,match",
    (
        ({"min_mean_cross_sectional_rank_ic": 0.03}, "Rank IC threshold"),
        ({"max_one_sided_sign_test_p_value": 0.10}, "sign-test threshold"),
        ({"min_holdout_cross_sections": 31}, "sample policy"),
        ({"min_holdout_total_observations": 91}, "sample policy"),
        ({"min_cross_section_observations": 4}, "sample policy"),
        ({"min_nonzero_rank_ic_cross_sections": 21}, "sample policy"),
        ({"max_evaluations": 2}, "one future evaluation"),
        ({"reselection_allowed": True}, "forbids retuning"),
        ({"retuning_allowed": True}, "forbids retuning"),
        ({"fallback_candidate_allowed": True}, "fallback"),
        ({"second_attempt_allowed": True}, "second attempts"),
        ({"failure_is_terminal": False}, "failure must be terminal"),
    ),
)
def test_policy_rejects_post_hoc_drift(updates, match):
    with pytest.raises(OSS3FinalHoldoutProtocolGovernanceError, match=match):
        replace(OSS3FinalHoldoutProtocolPolicy(), **updates)


def test_holdout_commitment_is_value_opaque_and_structurally_adequate(tmp_path):
    preregistration, _, _ = _source(tmp_path)
    commitment = _holdout_commitment(preregistration)
    payload = commitment.to_dict()
    assert commitment.commitment_version == OSS3D2J_COMMITMENT_VERSION
    assert commitment.row_count >= MIN_HOLDOUT_TOTAL_OBSERVATIONS
    assert commitment.cross_section_count >= MIN_HOLDOUT_CROSS_SECTIONS
    assert commitment.minimum_cross_section_observation_count >= MIN_CROSS_SECTION_OBSERVATIONS
    assert commitment.label_values_exposed is False
    assert commitment.final_holdout_observed is False
    assert "label_values" not in payload
    assert "rows" not in payload
    assert "outcomes" not in payload


@pytest.mark.parametrize(
    "updates,match",
    (
        ({"row_count": 89}, "row_count"),
        ({"cross_section_count": 29}, "cross_section_count"),
        ({"minimum_cross_section_observation_count": 2}, "too small"),
        ({"label_values_exposed": True}, "value-opaque"),
        ({"final_holdout_observed": True}, "unobserved"),
    ),
)
def test_holdout_commitment_rejects_inadequate_or_observed_material(tmp_path, updates, match):
    preregistration, _, _ = _source(tmp_path)
    with pytest.raises(OSS3FinalHoldoutProtocolGovernanceError, match=match):
        _holdout_commitment(preregistration, **updates)


def test_holdout_commitment_rejects_internally_inconsistent_counts(tmp_path):
    preregistration, _, _ = _source(tmp_path)
    with pytest.raises(OSS3FinalHoldoutProtocolIntegrityError, match="internally inconsistent"):
        _holdout_commitment(
            preregistration,
            row_count=100,
            cross_section_count=40,
            minimum_cross_section_observation_count=3,
        )


def test_protocol_rebinds_exact_d2i_d2h_and_holdout_commitment(tmp_path):
    preregistration, batch, seal, commitment, _, receipt = _record(tmp_path)
    winner = receipt.winner_binding
    holdout = receipt.holdout_commitment
    assert receipt.contract_version == OSS3D2J_CONTRACT_VERSION
    assert winner.binding_version == OSS3D2J_WINNER_BINDING_VERSION
    assert winner.source_d2i_seal_fingerprint == seal.fingerprint
    assert winner.source_d2h_preregistration_fingerprint == preregistration.fingerprint
    assert winner.source_d2h_batch_evidence_fingerprint == batch.fingerprint
    assert winner.selected_trial_id == seal.selected_trial_id
    assert winner.model_config_hash == seal.model_config_hash
    assert winner.d2e_plan_fingerprint == seal.d2e_plan_fingerprint
    assert winner.d2e_tournament_evidence_fingerprint == seal.d2e_tournament_evidence_fingerprint
    assert winner.source_winner_primary_metric == seal.winner_primary_metric
    assert winner.source_winner_raw_p_value == seal.winner_raw_p_value
    assert winner.source_winner_holm_adjusted_p_value == seal.winner_holm_adjusted_p_value
    assert holdout.fingerprint == commitment.fingerprint
    assert holdout.feature_artifact_hash == commitment.feature_artifact_hash
    assert holdout.label_artifact_hash == commitment.label_artifact_hash
    assert holdout.evaluation_keyset_hash == commitment.evaluation_keyset_hash
    assert holdout.cross_section_key_hash == commitment.cross_section_key_hash
    assert receipt.expected_holdout_authorization_id.startswith("oss3d2j:")
    assert receipt.gate_specification == (
        ("FINAL_NONZERO_RANK_IC_CROSS_SECTIONS_MIN", ">=", 20.0),
        ("FINAL_MEAN_CROSS_SECTIONAL_RANK_IC_MIN", ">=", 0.02),
        ("FINAL_ONE_SIDED_EXACT_SIGN_TEST_P_MAX", "<=", 0.05),
    )


def test_protocol_grants_no_holdout_or_execution_authority(tmp_path):
    *_, receipt = _record(tmp_path)
    assert receipt.final_holdout_observed is False
    assert receipt.final_holdout_consumed is False
    assert receipt.holdout_permit_issued is False
    assert receipt.holdout_permit_consumed is False
    assert receipt.final_holdout_checkout_authorized is False
    assert receipt.predictive_validation_passed is False
    assert receipt.profitability_claim_authorized is False
    assert receipt.promotion_authorized is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"


@pytest.mark.parametrize(
    "updates,match",
    (
        ({"final_holdout_observed": True}, "observe or consume"),
        ({"final_holdout_consumed": True}, "observe or consume"),
        ({"holdout_permit_issued": True}, "not a holdout permit"),
        ({"holdout_permit_consumed": True}, "not a holdout permit"),
        ({"final_holdout_checkout_authorized": True}, "not a holdout permit"),
        ({"predictive_validation_passed": True}, "predictive validation"),
        ({"profitability_claim_authorized": True}, "profitability"),
        ({"promotion_authorized": True}, "promotion"),
        ({"execution_authorized": True}, "promotion"),
        ({"paper_execution_authorized": True}, "promotion"),
        ({"capital_authority": "PAPER"}, "capital"),
        ({"live_trading": "ENABLED"}, "capital"),
    ),
)
def test_receipt_rejects_every_authority_escalation(tmp_path, updates, match):
    *_, receipt = _record(tmp_path)
    with pytest.raises(OSS3FinalHoldoutProtocolGovernanceError, match=match):
        replace(receipt, **updates)


def test_registry_is_deterministic_and_idempotent(tmp_path):
    preregistration, batch, seal = _source(tmp_path)
    commitment = _holdout_commitment(preregistration)
    registry = SQLiteOSS3FinalHoldoutProtocolRegistry(tmp_path / "d2j.sqlite3")
    first = registry.preregister_and_record(
        protocol_id="oss3d2j-protocol-001",
        seal=seal,
        preregistration=preregistration,
        batch_evidence=batch,
        holdout_commitment=commitment,
    )
    second = registry.preregister_and_record(
        protocol_id="oss3d2j-protocol-001",
        seal=seal,
        preregistration=preregistration,
        batch_evidence=batch,
        holdout_commitment=commitment,
    )
    assert first == second
    assert first.receipt_hash == second.receipt_hash


def test_same_seal_cannot_get_second_protocol_or_second_holdout(tmp_path):
    preregistration, batch, seal = _source(tmp_path)
    first_commitment = _holdout_commitment(preregistration)
    second_commitment = replace(
        first_commitment,
        feature_artifact_hash="5" * 64,
        label_artifact_hash="6" * 64,
        evaluation_keyset_hash="7" * 64,
        cross_section_key_hash="8" * 64,
    )
    registry = SQLiteOSS3FinalHoldoutProtocolRegistry(tmp_path / "d2j.sqlite3")
    registry.preregister_and_record(
        protocol_id="oss3d2j-protocol-001",
        seal=seal,
        preregistration=preregistration,
        batch_evidence=batch,
        holdout_commitment=first_commitment,
    )
    with pytest.raises(OSS3FinalHoldoutProtocolConflict, match="different D2J protocol"):
        registry.preregister_and_record(
            protocol_id="oss3d2j-protocol-002",
            seal=seal,
            preregistration=preregistration,
            batch_evidence=batch,
            holdout_commitment=second_commitment,
        )


def test_forged_d2i_seal_is_rejected_by_full_d2h_reverification(tmp_path):
    preregistration, batch, seal = _source(tmp_path)
    forged = replace(seal, winner_primary_metric=seal.winner_primary_metric + 0.01)
    registry = SQLiteOSS3FinalHoldoutProtocolRegistry(tmp_path / "d2j.sqlite3")
    with pytest.raises(OSS3FinalHoldoutProtocolIntegrityError, match="rebind"):
        registry.preregister_and_record(
            protocol_id="oss3d2j-protocol-001",
            seal=forged,
            preregistration=preregistration,
            batch_evidence=batch,
            holdout_commitment=_holdout_commitment(preregistration),
        )


def test_cross_wired_d2h_preregistration_is_rejected(tmp_path):
    preregistration, batch, seal = _source(tmp_path)
    altered = replace(
        preregistration,
        development_label_artifact_hash="f" * 64,
    )
    registry = SQLiteOSS3FinalHoldoutProtocolRegistry(tmp_path / "d2j.sqlite3")
    with pytest.raises(OSS3FinalHoldoutProtocolIntegrityError, match="rebind"):
        registry.preregister_and_record(
            protocol_id="oss3d2j-protocol-001",
            seal=seal,
            preregistration=altered,
            batch_evidence=batch,
            holdout_commitment=_holdout_commitment(preregistration),
        )


@pytest.mark.parametrize(
    "updates,match",
    (
        ({"source_campaign_id": "different-campaign"}, "holdout campaign"),
        ({"research_split_hash": "f" * 64}, "research split"),
        ({"source_universe_hash": "f" * 64}, "holdout universe"),
        ({"label_definition_hash": "f" * 64}, "label definition"),
    ),
)
def test_holdout_must_match_development_research_identity(tmp_path, updates, match):
    preregistration, batch, seal = _source(tmp_path)
    commitment = _holdout_commitment(preregistration, **updates)
    registry = SQLiteOSS3FinalHoldoutProtocolRegistry(tmp_path / "d2j.sqlite3")
    with pytest.raises(OSS3FinalHoldoutProtocolIntegrityError, match=match):
        registry.preregister_and_record(
            protocol_id="oss3d2j-protocol-001",
            seal=seal,
            preregistration=preregistration,
            batch_evidence=batch,
            holdout_commitment=commitment,
        )


def test_holdout_must_be_chronologically_after_development(tmp_path):
    preregistration, batch, seal = _source(tmp_path)
    development_start = datetime.fromisoformat(
        preregistration.d2e_plan.dataset.evaluation_start
    )
    commitment = _holdout_commitment(
        preregistration,
        partition_start=(development_start - timedelta(days=1)).isoformat(),
        partition_end=(development_start + timedelta(days=40)).isoformat(),
    )
    registry = SQLiteOSS3FinalHoldoutProtocolRegistry(tmp_path / "d2j.sqlite3")
    with pytest.raises(OSS3FinalHoldoutProtocolIntegrityError, match="chronological"):
        registry.preregister_and_record(
            protocol_id="oss3d2j-protocol-001",
            seal=seal,
            preregistration=preregistration,
            batch_evidence=batch,
            holdout_commitment=commitment,
        )


def test_holdout_cannot_reuse_development_labels_or_keyset(tmp_path):
    preregistration, batch, seal = _source(tmp_path)
    dataset = preregistration.d2e_plan.dataset
    registry = SQLiteOSS3FinalHoldoutProtocolRegistry(tmp_path / "d2j.sqlite3")
    for updates, match in (
        (
            {"label_artifact_hash": dataset.development_label_artifact_hash},
            "cannot reuse DEVELOPMENT labels",
        ),
        (
            {"evaluation_keyset_hash": dataset.evaluation_keyset_hash},
            "cannot reuse DEVELOPMENT support",
        ),
    ):
        with pytest.raises(OSS3FinalHoldoutProtocolIntegrityError, match=match):
            registry.preregister_and_record(
                protocol_id="oss3d2j-protocol-001",
                seal=seal,
                preregistration=preregistration,
                batch_evidence=batch,
                holdout_commitment=_holdout_commitment(preregistration, **updates),
            )


def test_registry_tables_are_append_only(tmp_path):
    *_, registry, receipt = _record(tmp_path)
    conn = sqlite3.connect(registry.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE oss3_final_holdout_protocols SET selected_trial_id = ? WHERE receipt_hash = ?",
                ("other", receipt.receipt_hash),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM oss3_final_holdout_protocols WHERE receipt_hash = ?",
                (receipt.receipt_hash,),
            )
    finally:
        conn.close()


def test_read_only_reader_round_trips_and_detects_durable_column_tamper(tmp_path):
    _, _, seal, _, registry, receipt = _record(tmp_path)
    read_back = read_oss3d2j_protocol_read_only(
        registry.path,
        seal_fingerprint=seal.fingerprint,
    )
    assert read_back == receipt

    conn = sqlite3.connect(registry.path)
    try:
        conn.execute("DROP TRIGGER oss3_final_holdout_protocols_no_update")
        conn.execute(
            "UPDATE oss3_final_holdout_protocols SET selected_trial_id = ? WHERE receipt_hash = ?",
            ("tampered-trial", receipt.receipt_hash),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(OSS3FinalHoldoutProtocolIntegrityError, match="durable column mismatch"):
        read_oss3d2j_protocol_read_only(
            registry.path,
            seal_fingerprint=seal.fingerprint,
        )


def test_nested_protocol_identity_is_deeply_immutable(tmp_path):
    *_, receipt = _record(tmp_path)
    with pytest.raises(Exception):
        receipt.winner_binding.selected_trial_id = "mutated"  # type: ignore[misc]
    with pytest.raises(Exception):
        receipt.holdout_commitment.row_count = 999  # type: ignore[misc]
    with pytest.raises(Exception):
        receipt.policy.max_evaluations = 2  # type: ignore[misc]


def test_source_has_no_holdout_evaluator_permit_or_execution_surface():
    source = (Path(__file__).resolve().parents[1] / "final_holdout_protocol.py").read_text(
        encoding="utf-8"
    )
    assert "HoldoutPermit" not in source
    assert "consume_holdout_permit" not in source
    assert "ProtectedHoldout" not in source
    assert "ProtectedOSS2FinalHoldout" not in source
    assert "import qlib" not in source
    assert "from qlib" not in source
    assert "submit_order" not in source
    assert "place_order" not in source
    assert "OrderIntent" not in source
