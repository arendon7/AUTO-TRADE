from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3

import pytest

from autotrade.research.universe import AlignedMarketUniverse
from labs.oss3_qlib.economic_holdout_evaluator import (
    INITIAL_CASH,
    NO_TERMINAL_LIQUIDATION_POLICY,
    PROFIT_FACTOR_POLICY,
    OSS3EconomicDecision,
    OSS3EconomicHoldoutAlreadyConsumed,
    OSS3EconomicHoldoutGovernanceError,
    OSS3EconomicHoldoutIntegrityError,
    EconomicHoldoutMaterial,
    ProtectedEconomicHoldout,
    SQLiteOSS3EconomicHoldoutEvaluationRegistry,
    economic_evaluator_semantic_hash,
    read_oss3d2n_evaluation_read_only,
)
from labs.oss3_qlib.final_holdout_evaluator import OSS3FinalHoldoutAlreadyConsumed
from labs.oss3_qlib.tests.d2n_fixture import build_d2n_source


UTC = timezone.utc
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _evaluate(source, *, evaluation_id="oss3d2n-evaluation-001"):
    registry = SQLiteOSS3EconomicHoldoutEvaluationRegistry(source.shared_sqlite_path)
    receipt = registry.evaluate(
        evaluation_id=evaluation_id,
        economic_protocol=source.d2m_protocol,
        d2l_receipt=source.d2l_receipt,
        d2j_protocol=source.d2j_protocol,
        holdout=ProtectedEconomicHoldout(source.economic_material),
        now=NOW,
    )
    return registry, receipt


def test_favorable_market_passes_all_preregistered_economic_gates(tmp_path):
    source = build_d2n_source(tmp_path, market_mode="favorable")
    assert source.d2k_receipt.predictive_validation_passed is True

    registry, receipt = _evaluate(source)
    assert receipt.decision is OSS3EconomicDecision.PASS
    assert receipt.predictive_validation_passed is True
    assert receipt.economic_validation_passed is True
    assert receipt.failure_code == ""
    assert receipt.metrics is not None
    metrics = receipt.metrics
    assert metrics.initial_cash == INITIAL_CASH
    assert metrics.net_return > 0
    assert metrics.sharpe >= 1.5
    assert metrics.profit_factor >= 1.3
    assert metrics.max_drawdown <= 0.15
    assert metrics.fills >= 10
    assert metrics.rebalances >= 10
    assert metrics.realized_closing_fills >= 10
    assert metrics.profit_factor_policy == PROFIT_FACTOR_POLICY
    assert metrics.terminal_liquidation_policy == NO_TERMINAL_LIQUIDATION_POLICY
    assert all(gate.passed for gate in receipt.gates)
    assert receipt.failed_gate_ids == ()
    assert receipt.economic_holdout_observed is True
    assert receipt.economic_holdout_consumed is True
    assert receipt.second_attempt_allowed is False
    assert receipt.retuning_allowed is False
    assert receipt.reselection_allowed is False
    assert receipt.profitability_claim_authorized is False
    assert receipt.promotion_authorized is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"

    reconstructed = read_oss3d2n_evaluation_read_only(
        registry.path,
        economic_protocol_id=source.d2m_protocol.economic_protocol_id,
    )
    assert reconstructed == receipt


def test_adverse_market_is_terminal_metric_fail_without_fallback(tmp_path):
    source = build_d2n_source(tmp_path, market_mode="adverse")
    registry, receipt = _evaluate(source, evaluation_id="oss3d2n-adverse")
    assert receipt.decision is OSS3EconomicDecision.FAIL
    assert receipt.economic_validation_passed is False
    assert receipt.metrics is not None
    assert receipt.metrics.net_return < 0
    assert receipt.metrics.profit_factor < 1.3
    assert receipt.failed_gate_ids
    assert receipt.second_attempt_allowed is False
    assert receipt.retuning_allowed is False
    assert receipt.reselection_allowed is False
    assert receipt.profitability_claim_authorized is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"

    with pytest.raises(OSS3EconomicHoldoutAlreadyConsumed):
        registry.evaluate(
            evaluation_id="oss3d2n-adverse-retry",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            holdout=ProtectedEconomicHoldout(source.economic_material),
            now=NOW,
        )


def test_second_attempt_after_pass_is_rejected_even_with_fresh_wrapper(tmp_path):
    source = build_d2n_source(tmp_path, market_mode="favorable")
    registry, first = _evaluate(source, evaluation_id="oss3d2n-pass-first")
    assert first.decision is OSS3EconomicDecision.PASS
    with pytest.raises(OSS3EconomicHoldoutAlreadyConsumed):
        registry.evaluate(
            evaluation_id="oss3d2n-pass-second",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            holdout=ProtectedEconomicHoldout(source.economic_material),
            now=NOW,
        )


def test_structural_failure_after_checkout_is_terminal_and_nonretryable(tmp_path, monkeypatch):
    source = build_d2n_source(tmp_path, market_mode="favorable")
    registry = SQLiteOSS3EconomicHoldoutEvaluationRegistry(source.shared_sqlite_path)

    def explode(**_kwargs):
        raise RuntimeError("synthetic economic evaluator failure")

    monkeypatch.setattr(
        "labs.oss3_qlib.economic_holdout_evaluator._simulate",
        explode,
    )
    receipt = registry.evaluate(
        evaluation_id="oss3d2n-structural-fail",
        economic_protocol=source.d2m_protocol,
        d2l_receipt=source.d2l_receipt,
        d2j_protocol=source.d2j_protocol,
        holdout=ProtectedEconomicHoldout(source.economic_material),
        now=NOW,
    )
    assert receipt.decision is OSS3EconomicDecision.FAIL
    assert receipt.failure_code == "EVALUATION_ERROR:RuntimeError"
    assert receipt.metrics is None
    assert receipt.gates == ()
    assert receipt.economic_validation_passed is False
    with pytest.raises(OSS3EconomicHoldoutAlreadyConsumed):
        registry.evaluate(
            evaluation_id="oss3d2n-structural-retry",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            holdout=ProtectedEconomicHoldout(source.economic_material),
            now=NOW,
        )


def test_broker_credentials_rejected_before_economic_start(tmp_path, monkeypatch):
    source = build_d2n_source(tmp_path, market_mode="favorable")
    registry = SQLiteOSS3EconomicHoldoutEvaluationRegistry(source.shared_sqlite_path)
    monkeypatch.setenv("APCA_API_KEY_ID", "forbidden-d2n-research-key")
    with pytest.raises(OSS3EconomicHoldoutGovernanceError, match="refuses broker"):
        registry.evaluate(
            evaluation_id="oss3d2n-credential-reject",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            holdout=ProtectedEconomicHoldout(source.economic_material),
            now=NOW,
        )
    conn = sqlite3.connect(registry.path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM oss3_economic_holdout_evaluation_starts"
        ).fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_holdout_commitment_drift_rejected_before_start(tmp_path):
    source = build_d2n_source(tmp_path, market_mode="favorable")
    changed_universe = AlignedMarketUniverse(
        datasets=source.economic_material.universe.datasets,
        universe_name="oss3d2n-drifted-universe",
    )
    changed_material = EconomicHoldoutMaterial(
        material_version=source.economic_material.material_version,
        commitment_id=source.economic_material.commitment_id,
        universe=changed_universe,
        prediction=source.economic_material.prediction,
    )
    registry = SQLiteOSS3EconomicHoldoutEvaluationRegistry(source.shared_sqlite_path)
    with pytest.raises(OSS3EconomicHoldoutIntegrityError, match="differs from D2M commitment"):
        registry.evaluate(
            evaluation_id="oss3d2n-commitment-drift",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            holdout=ProtectedEconomicHoldout(changed_material),
            now=NOW,
        )
    conn = sqlite3.connect(registry.path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM oss3_economic_holdout_evaluation_starts"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_consumed_d2k_without_terminal_blocks_economic_evaluation(tmp_path):
    source = build_d2n_source(tmp_path, market_mode="favorable")
    conn = sqlite3.connect(source.shared_sqlite_path)
    try:
        conn.execute("DROP TRIGGER oss3_final_holdout_terminal_no_delete")
        conn.execute("DELETE FROM oss3_final_holdout_evaluations")
        conn.commit()
    finally:
        conn.close()
    registry = SQLiteOSS3EconomicHoldoutEvaluationRegistry(source.shared_sqlite_path)
    with pytest.raises(
        OSS3FinalHoldoutAlreadyConsumed,
        match="authorization consumed without terminal receipt",
    ):
        registry.evaluate(
            evaluation_id="oss3d2n-no-d2k-terminal",
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            holdout=ProtectedEconomicHoldout(source.economic_material),
            now=NOW,
        )


def test_economic_registry_tables_are_append_only(tmp_path):
    source = build_d2n_source(tmp_path, market_mode="favorable")
    registry, receipt = _evaluate(source, evaluation_id="oss3d2n-append-only")
    conn = sqlite3.connect(registry.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE oss3_economic_holdout_evaluation_starts SET initial_cash = ? WHERE evaluation_id = ?",
                ("1", receipt.evaluation_id),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM oss3_economic_holdout_evaluations WHERE evaluation_id = ?",
                (receipt.evaluation_id,),
            )
        conn.rollback()
    finally:
        conn.close()


def test_evaluator_semantic_hash_is_stable_lowercase_sha256():
    first = economic_evaluator_semantic_hash()
    second = economic_evaluator_semantic_hash()
    assert first == second
    assert len(first) == 64
    assert first == first.lower()
    int(first, 16)
