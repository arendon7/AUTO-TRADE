from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3

import pytest

from labs.oss3_qlib.economic_holdout_evaluator import (
    OSS3EconomicHoldoutAlreadyConsumed,
    SQLiteOSS3EconomicHoldoutEvaluationRegistry,
    read_oss3d2n_evaluation_read_only,
)
from labs.oss3_qlib.economic_prediction_provenance import (
    EconomicFeatureRow,
    EconomicPredictionFeatureArtifact,
    EconomicPredictionProvenanceGovernanceError,
)
from labs.oss3_qlib.economic_prediction_provenance_admission import (
    EconomicPredictionProvenanceAdmissionGovernanceError,
    EconomicPredictionProvenanceAdmissionIntegrityError,
    OSS3D2P_ADMISSION_VERSION,
    OSS3D2P_ORDERING_CONTRACT,
    OSS3D2P_REPLAY_POLICY,
    SQLiteOSS3EconomicPredictionProvenanceAdmissionRegistry,
    economic_prediction_provenance_admission_semantic_hash,
    read_oss3d2p_provenance_admission_read_only,
)
from labs.oss3_qlib.tests.d2p_fixture import (
    build_d2p_pre_d2k_source,
    protected_economic_holdout,
    run_d2k,
)


UTC = timezone.utc
D2N_NOW = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def admitted_source(tmp_path_factory):
    return build_d2p_pre_d2k_source(
        tmp_path_factory.mktemp("oss3d2p-admitted"),
        market_mode="flat",
        admit=True,
    )


def _evaluate_d2n(source, evaluation_id: str):
    return SQLiteOSS3EconomicHoldoutEvaluationRegistry(source.shared_sqlite_path).evaluate(
        evaluation_id=evaluation_id,
        economic_protocol=source.d2m_protocol,
        d2l_receipt=source.d2l_receipt,
        d2j_protocol=source.d2j_protocol,
        holdout=protected_economic_holdout(source),
        now=D2N_NOW,
    )


def test_admission_receipt_binds_replay_precommit_provenance_and_no_authority(admitted_source):
    receipt = admitted_source.provenance_admission
    assert receipt is not None
    assert receipt.receipt_version == OSS3D2P_ADMISSION_VERSION
    assert receipt.ordering_contract == OSS3D2P_ORDERING_CONTRACT
    assert receipt.replay_policy == OSS3D2P_REPLAY_POLICY
    assert receipt.economic_protocol_receipt_hash == admitted_source.d2m_protocol.receipt_hash
    assert receipt.source_d2l_receipt_hash == admitted_source.d2l_receipt.receipt_hash
    assert receipt.prediction_precommit_receipt_hash == admitted_source.prediction_precommit.receipt_hash
    assert receipt.prediction_artifact_hash == admitted_source.prediction.artifact_hash
    assert receipt.prediction_payload_hash == admitted_source.prediction.manifest.prediction_payload_hash
    assert receipt.d2o_provenance_receipt_hash == admitted_source.d2o_receipt.receipt_hash
    assert receipt.economic_feature_artifact_hash == admitted_source.economic_features.artifact_hash
    assert receipt.economic_feature_support_hash == receipt.prediction_support_hash
    assert receipt.prediction_precommit_proven is True
    assert receipt.independent_d2o_replay_performed is True
    assert receipt.replay_prediction_exact_match is True
    assert receipt.provenance_receipt_recomputed is True
    assert receipt.frozen_before_d2k_start is True
    assert receipt.d2k_start_absent_at_commit is True
    assert receipt.d2k_permit_unconsumed_at_commit is True
    assert receipt.d2n_database_trigger_installed is True
    assert receipt.market_derived_features_loaded is True
    assert receipt.economic_labels_loaded is False
    assert receipt.economic_outcomes_loaded is False
    assert receipt.profitability_claim_authorized is False
    assert receipt.promotion_authorized is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"

    reconstructed = read_oss3d2p_provenance_admission_read_only(
        admitted_source.shared_sqlite_path,
        economic_protocol_id=admitted_source.d2m_protocol.economic_protocol_id,
    )
    assert reconstructed == receipt


def test_full_order_admission_then_d2k_then_d2n_produces_terminal_evidence(admitted_source):
    d2k = run_d2k(admitted_source)
    assert d2k.predictive_validation_passed is True

    receipt = _evaluate_d2n(admitted_source, "oss3d2p-admitted-d2n")
    assert receipt.economic_holdout_consumed is True
    assert receipt.second_attempt_allowed is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    reconstructed = read_oss3d2n_evaluation_read_only(
        admitted_source.shared_sqlite_path,
        economic_protocol_id=admitted_source.d2m_protocol.economic_protocol_id,
    )
    assert reconstructed == receipt


def test_direct_d2n_start_is_db_blocked_when_d2p_schema_exists_but_admission_missing(tmp_path):
    source = build_d2p_pre_d2k_source(tmp_path, market_mode="flat", admit=False)
    d2k = run_d2k(source)
    assert d2k.predictive_validation_passed is True

    with pytest.raises(OSS3EconomicHoldoutAlreadyConsumed, match="durable identity conflict"):
        _evaluate_d2n(source, "oss3d2p-missing-admission-d2n")

    conn = sqlite3.connect(source.shared_sqlite_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM oss3_economic_holdout_evaluation_starts"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_changed_feature_values_cannot_be_admitted_against_frozen_prediction(tmp_path):
    source = build_d2p_pre_d2k_source(tmp_path, market_mode="flat", admit=False)
    original_rows = list(source.economic_features.rows)
    row = original_rows[0]
    changed_values = list(row.values)
    changed_values[0] = float(changed_values[0]) + 100.0
    original_rows[0] = EconomicFeatureRow(
        as_of=row.as_of,
        available_at=row.available_at,
        symbol=row.symbol,
        values=tuple(changed_values),
    )
    changed = EconomicPredictionFeatureArtifact.build(
        economic_protocol=source.d2m_protocol,
        d2l_receipt=source.d2l_receipt,
        feature_source_hash=source.economic_features.feature_source_hash,
        feature_producer_code_hash=source.economic_features.feature_producer_code_hash,
        feature_names=source.economic_features.feature_names,
        rows=tuple(original_rows),
    )
    registry = SQLiteOSS3EconomicPredictionProvenanceAdmissionRegistry(
        source.shared_sqlite_path
    )
    with pytest.raises(
        EconomicPredictionProvenanceAdmissionIntegrityError,
        match="replay prediction .* differs from durable precommit",
    ):
        registry.admit_by_replay(
            admission_id="oss3d2p-changed-feature-admission",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            source_request=source.lineage.winner_output.request,
            training_bundle=source.lineage.training_bundle,
            train_features=source.lineage.train_features,
            train_labels=source.lineage.train_labels,
            economic_features=changed,
            now=datetime(2026, 6, 16, 13, tzinfo=UTC),
        )
    assert read_oss3d2p_provenance_admission_read_only(
        source.shared_sqlite_path,
        economic_protocol_id=source.d2m_protocol.economic_protocol_id,
    ) is None


def test_admission_after_d2k_is_rejected_and_not_persisted(tmp_path):
    source = build_d2p_pre_d2k_source(tmp_path, market_mode="flat", admit=False)
    run_d2k(source)
    registry = SQLiteOSS3EconomicPredictionProvenanceAdmissionRegistry(
        source.shared_sqlite_path
    )
    with pytest.raises(
        EconomicPredictionProvenanceAdmissionGovernanceError,
        match="before D2K start",
    ):
        registry.admit_by_replay(
            admission_id="oss3d2p-too-late-admission",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            source_request=source.lineage.winner_output.request,
            training_bundle=source.lineage.training_bundle,
            train_features=source.lineage.train_features,
            train_labels=source.lineage.train_labels,
            economic_features=source.economic_features,
            now=datetime(2026, 6, 18, tzinfo=UTC),
        )
    assert read_oss3d2p_provenance_admission_read_only(
        source.shared_sqlite_path,
        economic_protocol_id=source.d2m_protocol.economic_protocol_id,
    ) is None


def test_broker_credentials_block_replay_admission_before_durable_row(tmp_path, monkeypatch):
    source = build_d2p_pre_d2k_source(tmp_path, market_mode="flat", admit=False)
    registry = SQLiteOSS3EconomicPredictionProvenanceAdmissionRegistry(
        source.shared_sqlite_path
    )
    monkeypatch.setenv("APCA_API_KEY_ID", "forbidden-d2p-key")
    with pytest.raises(EconomicPredictionProvenanceGovernanceError, match="refuses broker"):
        registry.admit_by_replay(
            admission_id="oss3d2p-credential-reject",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            source_request=source.lineage.winner_output.request,
            training_bundle=source.lineage.training_bundle,
            train_features=source.lineage.train_features,
            train_labels=source.lineage.train_labels,
            economic_features=source.economic_features,
            now=datetime(2026, 6, 16, 13, tzinfo=UTC),
        )
    assert read_oss3d2p_provenance_admission_read_only(
        source.shared_sqlite_path,
        economic_protocol_id=source.d2m_protocol.economic_protocol_id,
    ) is None


def test_admission_table_and_d2p_trigger_are_append_only_and_enforced(tmp_path):
    source = build_d2p_pre_d2k_source(tmp_path, market_mode="flat", admit=True)
    receipt = source.provenance_admission
    assert receipt is not None
    conn = sqlite3.connect(source.shared_sqlite_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE oss3_economic_prediction_provenance_admissions "
                "SET registered_at = ? WHERE admission_id = ?",
                ("2000-01-01T00:00:00+00:00", receipt.admission_id),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM oss3_economic_prediction_provenance_admissions "
                "WHERE admission_id = ?",
                (receipt.admission_id,),
            )
        conn.rollback()
        trigger = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='oss3_d2p_d2n_start_requires_provenance_admission'"
        ).fetchone()
        assert trigger is not None
        assert "replay-verified prediction provenance admission required" in trigger[0]
    finally:
        conn.close()


def test_admission_receipt_cannot_be_mutated_into_authority(admitted_source):
    receipt = admitted_source.provenance_admission
    assert receipt is not None
    with pytest.raises(EconomicPredictionProvenanceAdmissionGovernanceError):
        replace(receipt, execution_authorized=True, receipt_hash="0" * 64)
    with pytest.raises(EconomicPredictionProvenanceAdmissionGovernanceError):
        replace(receipt, paper_execution_authorized=True, receipt_hash="0" * 64)
    with pytest.raises(EconomicPredictionProvenanceAdmissionGovernanceError):
        replace(receipt, capital_authority="PAPER", receipt_hash="0" * 64)


def test_admission_semantic_hash_is_stable_lowercase_sha256():
    first = economic_prediction_provenance_admission_semantic_hash()
    second = economic_prediction_provenance_admission_semantic_hash()
    assert first == second
    assert len(first) == 64
    assert first == first.lower()
    int(first, 16)
