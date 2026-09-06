from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from autotrade.research.oss3_factor_matrix_artifact import (
    FactorMatrixArtifact,
    FactorMatrixPartition,
)
from autotrade.research.registry import HoldoutPermit
from labs.oss3_qlib.final_holdout_evaluator import (
    OSS3FinalHoldoutAlreadyConsumed,
    OSS3FinalHoldoutDecision,
    OSS3FinalHoldoutEvaluationGovernanceError,
    OSS3FinalHoldoutEvaluationIntegrityError,
    ProtectedOSS3FinalHoldout,
    SQLiteOSS3FinalHoldoutEvaluationRegistry,
    evaluator_semantic_hash,
    read_oss3d2k_evaluation_read_only,
)
from labs.oss3_qlib.tests.d2k_fixture import build_d2k_source


UTC = timezone.utc
NOW = datetime(2026, 7, 1, tzinfo=UTC)


def _evaluate(source, tmp_path, *, evaluation_id="oss3d2k-evaluation-001"):
    registry = SQLiteOSS3FinalHoldoutEvaluationRegistry(tmp_path / "d2k-evaluation.sqlite3")
    receipt = registry.evaluate(
        evaluation_id=evaluation_id,
        protocol=source.protocol,
        source_request=source.source_request,
        training_bundle=source.training_bundle,
        train_features=source.train_features,
        train_labels=source.train_labels,
        holdout=source.holdout,
        now=NOW,
    )
    return registry, receipt


def test_real_qlib_one_shot_final_holdout_passes_predictive_gates_only(tmp_path):
    source = build_d2k_source(tmp_path, label_mode="aligned")
    registry, receipt = _evaluate(source, tmp_path)

    assert receipt.decision is OSS3FinalHoldoutDecision.PASS
    assert receipt.predictive_validation_passed is True
    assert receipt.failure_code == ""
    assert receipt.metrics is not None
    assert receipt.metrics.observation_count == 120
    assert receipt.metrics.valid_cross_section_count == 40
    assert receipt.metrics.nonzero_rank_ic_cross_section_count == 40
    assert receipt.metrics.mean_cross_sectional_rank_ic >= 0.02
    assert receipt.metrics.one_sided_exact_sign_test_p_value <= 0.05
    assert tuple(gate.passed for gate in receipt.gates) == (True, True, True)
    assert receipt.failed_gate_ids == ()

    assert receipt.final_holdout_observed is True
    assert receipt.final_holdout_consumed is True
    assert receipt.holdout_permit_consumed is True
    assert receipt.retuning_allowed is False
    assert receipt.reselection_allowed is False
    assert receipt.fallback_candidate_allowed is False
    assert receipt.second_attempt_allowed is False
    assert receipt.profitability_claim_authorized is False
    assert receipt.promotion_authorized is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"

    reconstructed = read_oss3d2k_evaluation_read_only(
        registry.path,
        protocol_id=source.protocol.protocol_id,
    )
    assert reconstructed == receipt


def test_real_qlib_reversed_holdout_is_terminal_fail_without_fallback(tmp_path):
    source = build_d2k_source(tmp_path, label_mode="reversed")
    _, receipt = _evaluate(source, tmp_path)

    assert receipt.decision is OSS3FinalHoldoutDecision.FAIL
    assert receipt.predictive_validation_passed is False
    assert receipt.failure_code == ""
    assert receipt.metrics is not None
    assert receipt.failed_gate_ids
    assert receipt.reselection_allowed is False
    assert receipt.retuning_allowed is False
    assert receipt.fallback_candidate_allowed is False
    assert receipt.second_attempt_allowed is False
    assert receipt.profitability_claim_authorized is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"


def test_second_attempt_is_rejected_after_successful_terminal_result(tmp_path):
    source = build_d2k_source(tmp_path, label_mode="aligned")
    registry, first = _evaluate(source, tmp_path)
    assert first.decision is OSS3FinalHoldoutDecision.PASS

    fresh_wrapper = ProtectedOSS3FinalHoldout(source.material)
    with pytest.raises(OSS3FinalHoldoutAlreadyConsumed, match="already consumed"):
        registry.evaluate(
            evaluation_id="oss3d2k-evaluation-002",
            protocol=source.protocol,
            source_request=source.source_request,
            training_bundle=source.training_bundle,
            train_features=source.train_features,
            train_labels=source.train_labels,
            holdout=fresh_wrapper,
            now=NOW,
        )


def test_structural_failure_after_checkout_is_terminal_and_nonretryable(tmp_path, monkeypatch):
    source = build_d2k_source(tmp_path, label_mode="aligned")
    registry = SQLiteOSS3FinalHoldoutEvaluationRegistry(tmp_path / "d2k-evaluation.sqlite3")

    def explode(**_kwargs):
        raise RuntimeError("synthetic evaluator failure")

    monkeypatch.setattr(
        "labs.oss3_qlib.final_holdout_evaluator._run_frozen_final_model",
        explode,
    )
    receipt = registry.evaluate(
        evaluation_id="oss3d2k-evaluation-structural-fail",
        protocol=source.protocol,
        source_request=source.source_request,
        training_bundle=source.training_bundle,
        train_features=source.train_features,
        train_labels=source.train_labels,
        holdout=source.holdout,
        now=NOW,
    )
    assert receipt.decision is OSS3FinalHoldoutDecision.FAIL
    assert receipt.failure_code == "EVALUATION_ERROR:RuntimeError"
    assert receipt.metrics is None
    assert receipt.gates == ()
    assert receipt.predictive_validation_passed is False
    assert receipt.holdout_permit_consumed is True
    assert receipt.second_attempt_allowed is False

    with pytest.raises(OSS3FinalHoldoutAlreadyConsumed):
        registry.evaluate(
            evaluation_id="oss3d2k-evaluation-retry",
            protocol=source.protocol,
            source_request=source.source_request,
            training_bundle=source.training_bundle,
            train_features=source.train_features,
            train_labels=source.train_labels,
            holdout=ProtectedOSS3FinalHoldout(source.material),
            now=NOW,
        )


def test_degenerate_holdout_consumes_once_and_fails_closed(tmp_path):
    source = build_d2k_source(tmp_path, label_mode="constant")
    registry, receipt = _evaluate(source, tmp_path)
    assert receipt.decision is OSS3FinalHoldoutDecision.FAIL
    assert receipt.failure_code == "EVALUATION_ERROR:OSS3FinalHoldoutEvaluationGovernanceError"
    assert receipt.metrics is None
    assert receipt.holdout_permit_consumed is True
    with pytest.raises(OSS3FinalHoldoutAlreadyConsumed):
        registry.evaluate(
            evaluation_id="oss3d2k-degenerate-retry",
            protocol=source.protocol,
            source_request=source.source_request,
            training_bundle=source.training_bundle,
            train_features=source.train_features,
            train_labels=source.train_labels,
            holdout=ProtectedOSS3FinalHoldout(source.material),
            now=NOW,
        )


def test_holdout_mismatch_fails_before_permit_consumption(tmp_path):
    source = build_d2k_source(tmp_path, label_mode="aligned")
    altered_material = replace(
        source.material,
        feature_source_dataset_hash="f" * 64,
    )
    registry = SQLiteOSS3FinalHoldoutEvaluationRegistry(tmp_path / "d2k-evaluation.sqlite3")
    with pytest.raises(OSS3FinalHoldoutEvaluationIntegrityError, match="differs from D2J commitment"):
        registry.evaluate(
            evaluation_id="oss3d2k-mismatch",
            protocol=source.protocol,
            source_request=source.source_request,
            training_bundle=source.training_bundle,
            train_features=source.train_features,
            train_labels=source.train_labels,
            holdout=ProtectedOSS3FinalHoldout(altered_material),
            now=NOW,
        )
    assert read_oss3d2k_evaluation_read_only(
        registry.path,
        protocol_id=source.protocol.protocol_id,
    ) is None


def test_broker_credentials_are_rejected_before_permit_burn(tmp_path, monkeypatch):
    source = build_d2k_source(tmp_path, label_mode="aligned")
    registry = SQLiteOSS3FinalHoldoutEvaluationRegistry(tmp_path / "d2k-evaluation.sqlite3")
    monkeypatch.setenv("APCA_API_KEY_ID", "forbidden-in-research-evaluator")
    with pytest.raises(OSS3FinalHoldoutEvaluationGovernanceError, match="refuses broker"):
        registry.evaluate(
            evaluation_id="oss3d2k-credential-reject",
            protocol=source.protocol,
            source_request=source.source_request,
            training_bundle=source.training_bundle,
            train_features=source.train_features,
            train_labels=source.train_labels,
            holdout=source.holdout,
            now=NOW,
        )
    assert read_oss3d2k_evaluation_read_only(
        registry.path,
        protocol_id=source.protocol.protocol_id,
    ) is None


def test_exact_original_train_bundle_is_required_before_consumption(tmp_path):
    source = build_d2k_source(tmp_path, label_mode="aligned")
    original = source.train_features
    manifest = original.manifest
    row = original.rows[0]
    altered_row = replace(
        row,
        values=(float(row.values[0]) + 1.0, float(row.values[1])),
    )
    altered_features = FactorMatrixArtifact.build(
        campaign_id=manifest.campaign_id,
        research_split_hash=manifest.research_split_hash,
        partition=FactorMatrixPartition.TRAIN,
        partition_start=datetime.fromisoformat(manifest.partition_start),
        partition_end=datetime.fromisoformat(manifest.partition_end),
        producer_code_hash=manifest.producer_code_hash,
        source_dataset_hash=manifest.source_dataset_hash,
        source_universe_hash=manifest.source_universe_hash,
        features=original.features,
        rows=(altered_row,) + original.rows[1:],
    )
    registry = SQLiteOSS3FinalHoldoutEvaluationRegistry(tmp_path / "d2k-evaluation.sqlite3")
    with pytest.raises(OSS3FinalHoldoutEvaluationIntegrityError, match="train_feature_artifact_hash"):
        registry.evaluate(
            evaluation_id="oss3d2k-train-drift",
            protocol=source.protocol,
            source_request=source.source_request,
            training_bundle=source.training_bundle,
            train_features=altered_features,
            train_labels=source.train_labels,
            holdout=source.holdout,
            now=NOW,
        )
    assert read_oss3d2k_evaluation_read_only(
        registry.path,
        protocol_id=source.protocol.protocol_id,
    ) is None


def test_protected_wrapper_rejects_direct_second_checkout(tmp_path):
    source = build_d2k_source(tmp_path, label_mode="aligned")
    permit = HoldoutPermit(
        permit_id=source.protocol.expected_holdout_authorization_id,
        issued_by="OSS3D2K_FINAL_HOLDOUT_EVALUATOR",
        purpose="final_validation",
    )
    material = source.holdout._checkout(
        permit=permit,
        expected_authorization_id=source.protocol.expected_holdout_authorization_id,
    )
    assert material.commitment.fingerprint == source.protocol.holdout_commitment.fingerprint
    with pytest.raises(OSS3FinalHoldoutAlreadyConsumed):
        source.holdout._checkout(
            permit=permit,
            expected_authorization_id=source.protocol.expected_holdout_authorization_id,
        )


def test_registry_tables_are_append_only(tmp_path):
    source = build_d2k_source(tmp_path, label_mode="aligned")
    registry, receipt = _evaluate(source, tmp_path)
    conn = sqlite3.connect(registry.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE oss3_final_holdout_evaluation_starts SET model_config_hash = ? WHERE evaluation_id = ?",
                ("f" * 64, receipt.evaluation_id),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM oss3_final_holdout_evaluations WHERE evaluation_id = ?",
                (receipt.evaluation_id,),
            )
        conn.rollback()
    finally:
        conn.close()


def test_semantic_hash_is_stable_lowercase_sha256():
    first = evaluator_semantic_hash()
    second = evaluator_semantic_hash()
    assert first == second
    assert len(first) == 64
    assert first == first.lower()
    int(first, 16)


def test_source_exposes_no_reselection_or_execution_surface():
    source = (Path(__file__).resolve().parents[1] / "final_holdout_evaluator.py").read_text(
        encoding="utf-8"
    )
    assert "development_labels=" not in source
    assert "development_features=" not in source
    assert "submit_order" not in source
    assert "place_order" not in source
    assert "OrderIntent" not in source
    assert "RiskDecision" not in source
    assert "CapitalSafetyKernel" not in source
    assert "paper_execution_authorized=True" not in source
    assert "execution_authorized=True" not in source
    assert "promotion_authorized=True" not in source
    assert "capital_authority=\"PAPER\"" not in source
    assert "live_trading=\"ENABLED\"" not in source
