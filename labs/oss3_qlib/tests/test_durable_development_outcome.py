from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json

import pytest

from autotrade.research.oss3_concrete_model_family import CANONICAL_CANDIDATES
from labs.oss3_qlib.development_winner_seal import seal_development_winner
from labs.oss3_qlib.durable_development_outcome import (
    OSS3D3C_CONTRACT_VERSION,
    DurableDevelopmentOutcomeBundle,
    DurableDevelopmentOutcomeGovernanceError,
    DurableDevelopmentOutcomeIntegrityError,
    build_durable_development_outcome,
    read_durable_development_outcome,
    write_durable_development_outcome,
)
from labs.oss3_qlib.tests.d2i_fixture import build_completed_d2h_evidence


@dataclass(frozen=True, slots=True)
class _SyntheticD3AEvidence:
    payload: dict[str, object]

    @property
    def fingerprint(self) -> str:
        return _hash(self.payload)

    def to_dict(self) -> dict[str, object]:
        return dict(self.payload)


def _completed_source(preregistration, batch, winner) -> _SyntheticD3AEvidence:
    expected_ids = tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)
    frozen_hashes = {item.candidate_id: item.frozen_output_hash for item in batch.evaluations}
    assert tuple(frozen_hashes) == expected_ids
    return _SyntheticD3AEvidence(
        {
            "evidence_version": "OSS3D3A_REAL_DEVELOPMENT_CAMPAIGN_EVIDENCE_V1",
            "status": "COMPLETED",
            "d2w_evidence_fingerprint": "1" * 64,
            "research_split_hash": preregistration.d2e_plan.dataset.research_split_hash,
            "raw_training_source_hash": "2" * 64,
            "raw_development_source_hash": "3" * 64,
            "d2r_receipt_hash": "4" * 64,
            "training_bundle_hash": "5" * 64,
            "training_feature_artifact_hash": "6" * 64,
            "training_label_artifact_hash": "7" * 64,
            "development_feature_artifact_hash": "8" * 64,
            "d2f_plan_fingerprint": preregistration.d2f_plan_fingerprint,
            "d2f_request_set_fingerprint": preregistration.d2f_request_set_fingerprint,
            "candidate_output_hashes": [
                [candidate_id, frozen_hashes[candidate_id]] for candidate_id in expected_ids
            ],
            "structural_policy": "PRELABEL_REQUIRE_GLOBAL_AND_EVERY_CROSS_SECTION_SCORE_VARIATION_V1",
            "structural_profiles": [],
            "evaluable_candidate_ids": list(expected_ids),
            "d2s_preregistration_fingerprint": "9" * 64,
            "development_label_artifact_hash": preregistration.development_label_artifact_hash,
            "d2h_preregistration_fingerprint": preregistration.fingerprint,
            "d2h_batch_evidence_fingerprint": batch.fingerprint,
            "d2s_completed_evidence_fingerprint": "a" * 64,
            "d2e_tournament_evidence_fingerprint": batch.tournament_evidence.fingerprint,
            "candidate_results": [],
            "d2i_winner_seal_fingerprint": winner.fingerprint,
            "selected_trial_id": winner.selected_trial_id,
            "winner_primary_metric": winner.winner_primary_metric,
            "winner_raw_p_value": winner.winner_raw_p_value,
            "winner_holm_adjusted_p_value": winner.winner_holm_adjusted_p_value,
            "predictions_frozen_before_development_labels": True,
            "development_labels_materialized": True,
            "development_metrics_computed": True,
            "family_retuned": False,
            "fallback_candidate_used": False,
            "reselection_allowed": False,
            "statistical_significance_claim_authorized": False,
            "profitability_claim_authorized": False,
            "final_holdout_observed": False,
            "final_holdout_authorized": False,
            "holdout_permit_consumed": False,
            "promotion_authorized": False,
            "execution_authorized": False,
            "paper_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
    )


def _bundle(tmp_path):
    preregistration, batch = build_completed_d2h_evidence(tmp_path)
    winner = seal_development_winner(
        preregistration=preregistration,
        batch_evidence=batch,
    )
    source = _completed_source(preregistration, batch, winner)
    bundle = build_durable_development_outcome(
        source_d3a_evidence=source,
        preregistration=preregistration,
        batch_evidence=batch,
        winner=winner,
    )
    return preregistration, batch, winner, source, bundle


def test_d3c_roundtrip_is_canonical_and_hash_stable(tmp_path):
    _, _, winner, source, bundle = _bundle(tmp_path)
    path = tmp_path / "d3c.json"
    write_durable_development_outcome(bundle, path)
    restored = read_durable_development_outcome(path)

    assert restored == bundle
    assert restored.fingerprint == bundle.fingerprint
    assert restored.source_d3a_evidence_fingerprint == source.fingerprint
    assert restored.d2i_winner_seal_fingerprint == winner.fingerprint
    assert restored.selected_trial_id == winner.selected_trial_id
    assert restored.contract_version == OSS3D3C_CONTRACT_VERSION
    assert restored.final_holdout_observed is False
    assert restored.final_holdout_authorized is False
    assert restored.holdout_permit_consumed is False
    assert restored.profitability_claim_authorized is False
    assert restored.promotion_authorized is False
    assert restored.execution_authorized is False
    assert restored.paper_execution_authorized is False
    assert restored.capital_authority == "NONE"
    assert restored.live_trading == "BLOCKED"

    raw = path.read_text(encoding="utf-8")
    document = json.loads(raw)
    assert raw == _canonical_json(document) + "\n"
    assert document["artifact_hash"] == bundle.fingerprint


def test_d3c_rejects_cross_wired_source_winner(tmp_path):
    preregistration, batch, winner, source, _ = _bundle(tmp_path)
    payload = source.to_dict()
    other_id = next(
        candidate.candidate_id
        for candidate in CANONICAL_CANDIDATES
        if candidate.candidate_id != winner.selected_trial_id
    )
    payload["selected_trial_id"] = other_id
    cross_wired = _SyntheticD3AEvidence(payload)

    with pytest.raises(DurableDevelopmentOutcomeIntegrityError):
        build_durable_development_outcome(
            source_d3a_evidence=cross_wired,
            preregistration=preregistration,
            batch_evidence=batch,
            winner=winner,
        )


def test_d3c_rejects_source_metric_drift(tmp_path):
    preregistration, batch, winner, source, _ = _bundle(tmp_path)
    payload = source.to_dict()
    payload["winner_primary_metric"] = float(winner.winner_primary_metric) + 0.001
    drifted = _SyntheticD3AEvidence(payload)

    with pytest.raises(DurableDevelopmentOutcomeIntegrityError):
        build_durable_development_outcome(
            source_d3a_evidence=drifted,
            preregistration=preregistration,
            batch_evidence=batch,
            winner=winner,
        )


def test_d3c_rejects_authority_escalation_inside_source(tmp_path):
    preregistration, batch, winner, source, _ = _bundle(tmp_path)
    payload = source.to_dict()
    payload["final_holdout_authorized"] = True
    escalated = _SyntheticD3AEvidence(payload)

    with pytest.raises(DurableDevelopmentOutcomeGovernanceError):
        build_durable_development_outcome(
            source_d3a_evidence=escalated,
            preregistration=preregistration,
            batch_evidence=batch,
            winner=winner,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("final_holdout_observed", True),
        ("final_holdout_authorized", True),
        ("holdout_permit_consumed", True),
        ("profitability_claim_authorized", True),
        ("promotion_authorized", True),
        ("execution_authorized", True),
        ("paper_execution_authorized", True),
        ("capital_authority", "PAPER"),
        ("live_trading", "ENABLED"),
    ),
)
def test_d3c_bundle_cannot_gain_authority(tmp_path, field, value):
    _, _, _, _, bundle = _bundle(tmp_path)
    with pytest.raises(DurableDevelopmentOutcomeGovernanceError):
        replace(bundle, **{field: value})


def test_d3c_read_rejects_payload_tampering_even_with_valid_json(tmp_path):
    _, _, _, _, bundle = _bundle(tmp_path)
    path = tmp_path / "d3c.json"
    write_durable_development_outcome(bundle, path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["source_d3a_evidence"]["winner_primary_metric"] += 0.001
    path.write_text(_canonical_json(document) + "\n", encoding="utf-8")

    with pytest.raises(DurableDevelopmentOutcomeIntegrityError):
        read_durable_development_outcome(path)


def test_d3c_read_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"contract_version":"x","contract_version":"y"}\n', encoding="utf-8")
    with pytest.raises(DurableDevelopmentOutcomeIntegrityError):
        read_durable_development_outcome(path)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
