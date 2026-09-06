from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from autotrade.research.oss3_concrete_model_family import CANONICAL_CANDIDATES
from autotrade.research.oss3_development_model_tournament import PRIMARY_METRIC
from labs.oss3_qlib.development_winner_seal import (
    NEXT_FRONTIER,
    OSS3D2I_CONTRACT_VERSION,
    SELECTION_SCOPE,
    DevelopmentWinnerSealGovernanceError,
    DevelopmentWinnerSealIntegrityError,
    seal_development_winner,
    verify_development_winner_seal,
)
from labs.oss3_qlib.tests.d2i_fixture import build_completed_d2h_evidence


def test_seal_freezes_exact_development_ranking_winner(tmp_path):
    preregistration, batch = build_completed_d2h_evidence(tmp_path)
    seal = seal_development_winner(
        preregistration=preregistration,
        batch_evidence=batch,
    )
    winner = batch.tournament_evidence.winner_trial_id
    assert seal.contract_version == OSS3D2I_CONTRACT_VERSION
    assert seal.selection_scope == SELECTION_SCOPE
    assert seal.next_frontier == NEXT_FRONTIER
    assert seal.selected_trial_id == winner == CANONICAL_CANDIDATES[0].candidate_id
    assert seal.primary_metric_name == PRIMARY_METRIC
    assert seal.preregistration_fingerprint == preregistration.fingerprint
    assert seal.d2h_batch_evidence_fingerprint == batch.fingerprint
    assert seal.d2e_plan_fingerprint == preregistration.d2e_plan.fingerprint
    assert seal.d2e_tournament_evidence_fingerprint == batch.tournament_evidence.fingerprint
    assert seal.winner_primary_metric == batch.tournament_evidence.winner_primary_metric
    assert seal.winner_raw_p_value == batch.tournament_evidence.winner_raw_p_value
    assert seal.winner_holm_adjusted_p_value == batch.tournament_evidence.winner_holm_adjusted_p_value
    assert seal.statistical_significance_claim_authorized is False
    assert seal.alpha_claim_authorized is False
    assert seal.profitability_claim_authorized is False
    assert seal.reselection_allowed is False
    assert seal.retuning_allowed is False
    assert seal.final_holdout_observed is False
    assert seal.final_holdout_authorized is False
    assert seal.holdout_permit_consumed is False
    assert seal.promotion_authorized is False
    assert seal.execution_authorized is False
    assert seal.paper_execution_authorized is False
    assert seal.capital_authority == "NONE"
    assert seal.live_trading == "BLOCKED"


def test_winner_lineage_rebinds_to_exact_d2h_bindings(tmp_path):
    preregistration, batch = build_completed_d2h_evidence(tmp_path)
    seal = seal_development_winner(preregistration=preregistration, batch_evidence=batch)
    output = next(
        item for item in preregistration.candidate_output_bindings if item.candidate_id == seal.selected_trial_id
    )
    evaluation = next(
        item for item in batch.evaluations if item.candidate_id == seal.selected_trial_id
    )
    candidate = next(
        item for item in preregistration.d2e_plan.candidates if item.trial_id == seal.selected_trial_id
    )
    assert seal.model_family == candidate.model_family
    assert seal.model_config_hash == candidate.model_config_hash == output.model_config_hash
    assert seal.request_hash == candidate.request_hash == output.request_hash == evaluation.request_hash
    assert seal.prediction_artifact_hash == output.prediction_artifact_hash == evaluation.prediction_artifact_hash
    assert seal.prediction_receipt_hash == output.prediction_receipt_hash == evaluation.receipt_hash
    assert seal.environment_attestation_hash == candidate.environment_attestation_hash
    assert seal.environment_attestation_hash == output.environment_attestation_hash
    assert seal.environment_attestation_hash == evaluation.environment_attestation_hash
    assert seal.d2g_run_evidence_hash == output.d2g_run_evidence_hash == evaluation.d2g_run_evidence_hash
    assert seal.d2d_evaluation_artifact_hash == evaluation.d2d_evaluation_artifact_hash
    assert seal.shared_runner_code_hash == output.shared_runner_code_hash
    assert seal.runtime_environment_hash == output.runtime_environment_hash


def test_seal_is_deterministic_and_reverifiable(tmp_path):
    preregistration, batch = build_completed_d2h_evidence(tmp_path)
    first = seal_development_winner(preregistration=preregistration, batch_evidence=batch)
    second = seal_development_winner(preregistration=preregistration, batch_evidence=batch)
    assert first.to_dict() == second.to_dict()
    assert first.fingerprint == second.fingerprint
    verify_development_winner_seal(
        seal=first,
        preregistration=preregistration,
        batch_evidence=batch,
    )


def test_batch_and_preregistration_cannot_be_cross_wired(tmp_path, tmp_path_factory):
    preregistration, batch = build_completed_d2h_evidence(tmp_path)
    other_dir = tmp_path_factory.mktemp("other-d2i")
    other_preregistration, _ = build_completed_d2h_evidence(other_dir)
    # A different SQLite location alone is intentionally not identity-changing;
    # force an upstream immutable identity mismatch instead.
    altered = replace(
        other_preregistration,
        development_label_artifact_hash="f" * 64,
    )
    with pytest.raises(DevelopmentWinnerSealIntegrityError, match="batch/preregistration"):
        seal_development_winner(preregistration=altered, batch_evidence=batch)


def test_seal_rejects_every_authority_escalation(tmp_path):
    preregistration, batch = build_completed_d2h_evidence(tmp_path)
    seal = seal_development_winner(preregistration=preregistration, batch_evidence=batch)
    cases = (
        ({"statistical_significance_claim_authorized": True}, "significance"),
        ({"alpha_claim_authorized": True}, "significance"),
        ({"profitability_claim_authorized": True}, "significance"),
        ({"reselection_allowed": True}, "reselection"),
        ({"retuning_allowed": True}, "reselection"),
        ({"final_holdout_observed": True}, "FINAL_HOLDOUT"),
        ({"final_holdout_authorized": True}, "FINAL_HOLDOUT"),
        ({"holdout_permit_consumed": True}, "FINAL_HOLDOUT"),
        ({"promotion_authorized": True}, "promotion"),
        ({"execution_authorized": True}, "promotion"),
        ({"paper_execution_authorized": True}, "promotion"),
        ({"capital_authority": "PAPER"}, "capital"),
        ({"live_trading": "ENABLED"}, "capital"),
    )
    for updates, message in cases:
        with pytest.raises(DevelopmentWinnerSealGovernanceError, match=message):
            replace(seal, **updates)


def test_selection_does_not_infer_significance_from_p_values(tmp_path):
    preregistration, batch = build_completed_d2h_evidence(tmp_path)
    seal = seal_development_winner(preregistration=preregistration, batch_evidence=batch)
    # D2I copies p-values as evidence but never turns any numeric threshold into
    # a significance/alpha/profitability authorization.
    assert 0.0 <= seal.winner_raw_p_value <= 1.0
    assert 0.0 <= seal.winner_holm_adjusted_p_value <= 1.0
    assert seal.statistical_significance_claim_authorized is False
    assert seal.alpha_claim_authorized is False
    assert seal.profitability_claim_authorized is False


def test_source_has_no_holdout_permit_or_execution_surface():
    source = (Path(__file__).resolve().parents[1] / "development_winner_seal.py").read_text(
        encoding="utf-8"
    )
    assert "HoldoutPermit" not in source
    assert "consume_holdout_permit" not in source
    assert "ProtectedOSS2FinalHoldout" not in source
    assert "FINAL_HOLDOUT checkout" not in source
    assert "import qlib" not in source
    assert "from qlib" not in source
    assert "submit_order" not in source
    assert "place_order" not in source
    assert "OrderIntent" not in source
