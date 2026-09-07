from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pytest

from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from labs.oss3_qlib.economic_holdout_evaluator import (
    OSS3D2N_MATERIAL_VERSION,
    EconomicHoldoutMaterial,
    ProtectedEconomicHoldout,
    OSS3EconomicHoldoutAlreadyConsumed,
    SQLiteOSS3EconomicHoldoutEvaluationRegistry,
)
from labs.oss3_qlib.economic_prediction_precommit import (
    EconomicPredictionPrecommitGovernanceError,
    EconomicPredictionPrecommitIntegrityError,
    SQLiteOSS3EconomicPredictionPrecommitRegistry,
    read_oss3d2n_prediction_precommit_read_only,
)
from labs.oss3_qlib.tests.d2n_fixture import build_d2n_source
from labs.oss3_qlib.tests.d2n_precommit_fixture import build_hardened_d2n_source


UTC = timezone.utc
PRECOMMIT_AT = datetime(2026, 6, 16, 12, tzinfo=UTC)
EVALUATE_AT = datetime(2026, 9, 1, tzinfo=UTC)


def _evaluate(source, *, material=None, evaluation_id="oss3d2n-precommit-eval"):
    registry = SQLiteOSS3EconomicHoldoutEvaluationRegistry(source.shared_sqlite_path)
    selected = source.economic_material if material is None else material
    return registry.evaluate(
        evaluation_id=evaluation_id,
        economic_protocol=source.d2m_protocol,
        d2l_receipt=source.d2l_receipt,
        d2j_protocol=source.d2j_protocol,
        holdout=ProtectedEconomicHoldout(selected),
        now=EVALUATE_AT,
    )


def _changed_prediction(source, *, score_delta=100.0, model_config_hash=None):
    original = source.economic_material.prediction
    manifest = original.manifest
    rows = tuple(
        QlibPredictionRow(
            timestamp=row.timestamp,
            symbol=row.symbol,
            score=float(row.score) + score_delta,
        )
        for row in original.rows
    )
    return QlibPredictionArtifact.build(
        qlib_version=manifest.qlib_version,
        model_family=manifest.model_family,
        model_config_hash=model_config_hash or manifest.model_config_hash,
        training_dataset_hash=manifest.training_dataset_hash,
        feature_schema_hash=manifest.feature_schema_hash,
        producer_code_hash=manifest.producer_code_hash,
        train_start=datetime.fromisoformat(manifest.train_start),
        train_end=datetime.fromisoformat(manifest.train_end),
        inference_start=datetime.fromisoformat(manifest.inference_start),
        inference_end=datetime.fromisoformat(manifest.inference_end),
        rows=rows,
    )


def test_exact_prediction_is_precommitted_before_d2k_and_allows_d2n(tmp_path):
    source = build_hardened_d2n_source(tmp_path, market_mode="favorable")
    receipt = source.prediction_precommit
    assert receipt.prediction_artifact_hash == source.economic_material.prediction.artifact_hash
    assert receipt.prediction_payload_hash == source.economic_material.prediction.manifest.prediction_payload_hash
    assert receipt.frozen_before_d2k_start is True
    assert receipt.prediction_values_frozen is True
    assert receipt.economic_market_values_used is False
    assert receipt.economic_outcomes_used is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"

    durable = read_oss3d2n_prediction_precommit_read_only(
        source.shared_sqlite_path,
        economic_protocol_id=source.d2m_protocol.economic_protocol_id,
    )
    assert durable == receipt

    terminal = _evaluate(source)
    assert terminal.predictive_validation_passed is True
    assert terminal.economic_validation_passed is True


def test_prediction_substitution_after_precommit_is_blocked_by_sql_trigger(tmp_path):
    source = build_hardened_d2n_source(tmp_path, market_mode="favorable")
    changed_prediction = _changed_prediction(source)
    assert changed_prediction.artifact_hash != source.prediction_precommit.prediction_artifact_hash
    changed_material = EconomicHoldoutMaterial(
        material_version=OSS3D2N_MATERIAL_VERSION,
        commitment_id=source.economic_material.commitment_id,
        universe=source.economic_material.universe,
        prediction=changed_prediction,
    )
    # Market commitment is intentionally unchanged; only scores changed.  The
    # prediction-precommit trigger must still reject the D2N start.
    assert changed_material.commitment.fingerprint == source.economic_material.commitment.fingerprint
    with pytest.raises(OSS3EconomicHoldoutAlreadyConsumed, match="durable identity conflict"):
        _evaluate(
            source,
            material=changed_material,
            evaluation_id="oss3d2n-substituted-scores",
        )
    conn = sqlite3.connect(source.shared_sqlite_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM oss3_economic_holdout_evaluation_starts"
        ).fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_missing_prediction_precommit_blocks_direct_internal_evaluator_start(tmp_path):
    # Legacy fixture creates D2K but no prediction precommit. Installing the
    # canonical precommit registry adds the DB admission trigger without adding
    # a receipt. Direct use of the internal evaluator must then fail closed.
    source = build_d2n_source(tmp_path, market_mode="favorable")
    SQLiteOSS3EconomicPredictionPrecommitRegistry(source.shared_sqlite_path)
    with pytest.raises(OSS3EconomicHoldoutAlreadyConsumed, match="durable identity conflict"):
        _evaluate(source, evaluation_id="oss3d2n-no-precommit")
    conn = sqlite3.connect(source.shared_sqlite_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM oss3_economic_holdout_evaluation_starts"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_new_prediction_precommit_is_rejected_after_d2k_start(tmp_path):
    source = build_d2n_source(tmp_path, market_mode="favorable")
    registry = SQLiteOSS3EconomicPredictionPrecommitRegistry(source.shared_sqlite_path)
    with pytest.raises(EconomicPredictionPrecommitGovernanceError, match="before D2K start"):
        registry.preregister(
            precommit_id="oss3d2n-too-late-precommit",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            prediction=source.economic_material.prediction,
            now=EVALUATE_AT,
        )


def test_prediction_model_identity_drift_is_rejected_before_durable_write(tmp_path):
    # Build hardened source to obtain exact model identity, then verify a drifted
    # artifact cannot be admitted under a fresh precommit id. The existing D2K
    # state means we exercise identity validation before the ordering check.
    source = build_hardened_d2n_source(tmp_path, market_mode="favorable")
    drifted = _changed_prediction(source, model_config_hash="0" * 64)
    registry = SQLiteOSS3EconomicPredictionPrecommitRegistry(source.shared_sqlite_path)
    with pytest.raises(EconomicPredictionPrecommitIntegrityError, match="model_config_hash"):
        registry.preregister(
            precommit_id="oss3d2n-model-drift",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            prediction=drifted,
            now=EVALUATE_AT,
        )


def test_prediction_support_drift_is_rejected_before_durable_write(tmp_path):
    source = build_hardened_d2n_source(tmp_path, market_mode="favorable")
    original = source.economic_material.prediction
    manifest = original.manifest
    shortened = QlibPredictionArtifact.build(
        qlib_version=manifest.qlib_version,
        model_family=manifest.model_family,
        model_config_hash=manifest.model_config_hash,
        training_dataset_hash=manifest.training_dataset_hash,
        feature_schema_hash=manifest.feature_schema_hash,
        producer_code_hash=manifest.producer_code_hash,
        train_start=datetime.fromisoformat(manifest.train_start),
        train_end=datetime.fromisoformat(manifest.train_end),
        inference_start=datetime.fromisoformat(manifest.inference_start),
        inference_end=datetime.fromisoformat(manifest.inference_end),
        rows=original.rows[:-1],
    )
    registry = SQLiteOSS3EconomicPredictionPrecommitRegistry(source.shared_sqlite_path)
    with pytest.raises(EconomicPredictionPrecommitIntegrityError, match="support"):
        registry.preregister(
            precommit_id="oss3d2n-support-drift",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            prediction=shortened,
            now=EVALUATE_AT,
        )


def test_prediction_precommit_registry_is_append_only(tmp_path):
    source = build_hardened_d2n_source(tmp_path, market_mode="favorable")
    conn = sqlite3.connect(source.shared_sqlite_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE oss3_economic_prediction_precommits SET prediction_artifact_hash = ?",
                ("f" * 64,),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM oss3_economic_prediction_precommits")
        conn.rollback()
    finally:
        conn.close()
