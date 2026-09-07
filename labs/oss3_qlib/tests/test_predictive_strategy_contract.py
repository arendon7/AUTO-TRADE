from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import sqlite3

import pytest

from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from labs.oss3_qlib.predictive_strategy_contract import (
    EXECUTION_DELAY_BARS,
    PredictiveStrategyContractConflict,
    PredictiveStrategyContractGovernanceError,
    PredictiveStrategyContractIntegrityError,
    SQLiteOSS3PredictiveStrategyRegistry,
    build_predictive_strategy_binding,
    canonical_oss3d2l_policy,
    predictive_strategy_code_hash,
    project_prediction_artifact,
    read_oss3d2l_preregistration_read_only,
)
from labs.oss3_qlib.tests.d2l_fixture import build_d2l_source


UTC = timezone.utc
NOW = datetime(2026, 6, 15, tzinfo=UTC)


def _binding(source):
    return build_predictive_strategy_binding(
        protocol=source.protocol,
        winner_output=source.winner_output,
    )


def _prediction(source, *, scores=None, symbols=None, **manifest_overrides):
    original = source.winner_output.prediction
    manifest = original.manifest
    selected_symbols = tuple(symbols or sorted({row.symbol for row in original.rows}))
    score_map = dict(scores or {symbol: float(index) for index, symbol in enumerate(selected_symbols, start=1)})
    timestamps = tuple(sorted({row.timestamp for row in original.rows}))
    rows = tuple(
        QlibPredictionRow(
            timestamp=timestamp,
            symbol=symbol,
            score=float(score_map[symbol]),
        )
        for timestamp in timestamps
        for symbol in selected_symbols
    )
    values = {
        "qlib_version": manifest.qlib_version,
        "model_family": manifest.model_family,
        "model_config_hash": manifest.model_config_hash,
        "training_dataset_hash": manifest.training_dataset_hash,
        "feature_schema_hash": manifest.feature_schema_hash,
        "producer_code_hash": manifest.producer_code_hash,
        "train_start": datetime.fromisoformat(manifest.train_start),
        "train_end": datetime.fromisoformat(manifest.train_end),
        "inference_start": datetime.fromisoformat(manifest.inference_start),
        "inference_end": datetime.fromisoformat(manifest.inference_end),
        "rows": rows,
    }
    values.update(manifest_overrides)
    return QlibPredictionArtifact.build(**values)


def test_canonical_policy_is_safety_first_and_non_adaptive():
    policy = canonical_oss3d2l_policy()
    assert policy.selection_fraction == Decimal("0.25")
    assert policy.gross_target == Decimal("0.75")
    assert policy.max_weight_per_asset == Decimal("0.25")
    assert policy.reserve_cash_min == Decimal("0.25")
    assert policy.execution_delay_bars == 1
    assert policy.rank_score_only is True
    assert policy.adaptive_portfolio_search is False
    assert policy.hyperparameter_optimization is False
    assert policy.score_sign_flip_allowed is False
    assert policy.shorting_allowed is False
    assert policy.leverage_allowed is False
    assert policy.same_bar_execution_allowed is False


def test_exact_d2j_winner_builds_deterministic_predictive_strategy(tmp_path):
    source = build_d2l_source(tmp_path)
    binding = _binding(source)

    assert binding.selected_trial_id == source.winner_seal.selected_trial_id
    assert binding.model_config_hash == source.winner_seal.model_config_hash
    assert binding.request_hash == source.winner_seal.request_hash
    assert binding.development_prediction_artifact_hash == source.winner_output.prediction.artifact_hash
    assert binding.binding_code_hash == predictive_strategy_code_hash()
    assert binding.strategy_version == f"oss3d2l-{binding.strategy_semantic_hash[:24]}"
    assert binding.development_predictions_used is True
    assert binding.development_labels_used is False
    assert binding.policy_frozen_before_final_holdout is True
    assert binding.final_holdout_observed is False
    assert binding.final_holdout_consumed is False
    assert binding.profitability_claim_authorized is False
    assert binding.promotion_authorized is False
    assert binding.execution_authorized is False
    assert binding.paper_execution_authorized is False
    assert binding.capital_authority == "NONE"
    assert binding.live_trading == "BLOCKED"
    assert len(binding.development_allocation_semantic_fingerprints) == 3

    first = project_prediction_artifact(
        binding=binding,
        prediction=source.winner_output.prediction,
    )
    second = project_prediction_artifact(
        binding=binding,
        prediction=source.winner_output.prediction,
    )
    assert first == second
    assert tuple(item.semantic_fingerprint for item in first) == binding.development_allocation_semantic_fingerprints
    for allocation in first:
        assert allocation.execution_delay_bars == EXECUTION_DELAY_BARS
        assert allocation.selected_asset_count == 1
        assert allocation.invested_weight == Decimal("0.25")
        assert allocation.cash_weight == Decimal("0.75")
        assert sum(1 for _, weight in allocation.target_weights if weight > 0) == 1
        assert allocation.order_intents_generated is False
        assert allocation.execution_authorized is False
        assert allocation.paper_execution_authorized is False
        assert allocation.capital_authority == "NONE"
        assert allocation.live_trading == "BLOCKED"


def test_rank_semantics_use_descending_score_and_symbol_tie_break(tmp_path):
    source = build_d2l_source(tmp_path)
    binding = _binding(source)
    tied = _prediction(
        source,
        scores={"BTCUSDT": 1.0, "ETHUSDT": 1.0, "SOLUSDT": 1.0},
    )
    allocations = project_prediction_artifact(binding=binding, prediction=tied)
    assert allocations
    for allocation in allocations:
        assert allocation.rankings[0].symbol == "BTCUSDT"
        assert allocation.rankings[0].selected is True
        assert dict(allocation.target_weights)["BTCUSDT"] == Decimal("0.25")
        assert dict(allocation.target_weights)["ETHUSDT"] == Decimal("0")
        assert dict(allocation.target_weights)["SOLUSDT"] == Decimal("0")


def test_nonwinner_output_cannot_be_bound(tmp_path):
    source = build_d2l_source(tmp_path)
    nonwinner = next(
        output for output in source.outputs if output.candidate_id != source.winner_seal.selected_trial_id
    )
    with pytest.raises(PredictiveStrategyContractIntegrityError, match="winner lineage mismatch"):
        build_predictive_strategy_binding(
            protocol=source.protocol,
            winner_output=nonwinner,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("model_config_hash", "0" * 64),
        ("training_dataset_hash", "1" * 64),
        ("feature_schema_hash", "2" * 64),
        ("producer_code_hash", "3" * 64),
    ),
)
def test_future_prediction_must_match_frozen_model_lineage(tmp_path, field, value):
    source = build_d2l_source(tmp_path)
    binding = _binding(source)
    changed = _prediction(source, **{field: value})
    with pytest.raises(PredictiveStrategyContractIntegrityError, match=field):
        project_prediction_artifact(binding=binding, prediction=changed)


def test_cross_section_below_support_floor_is_rejected(tmp_path):
    source = build_d2l_source(tmp_path)
    binding = _binding(source)
    too_small = _prediction(
        source,
        symbols=("BTCUSDT", "ETHUSDT"),
        scores={"BTCUSDT": 1.0, "ETHUSDT": 2.0},
    )
    with pytest.raises(PredictiveStrategyContractGovernanceError, match="support floor"):
        project_prediction_artifact(binding=binding, prediction=too_small)


def test_durable_preregistration_precedes_d2k_and_round_trips_read_only(tmp_path):
    source = build_d2l_source(tmp_path)
    binding = _binding(source)
    path = tmp_path / "shared-d2l-d2k.sqlite3"
    registry = SQLiteOSS3PredictiveStrategyRegistry(path)
    receipt = registry.preregister(
        protocol=source.protocol,
        binding=binding,
        now=NOW,
    )
    assert receipt.shared_sqlite_ordering_enforced is True
    assert receipt.d2k_start_absent_at_commit is True
    assert receipt.final_holdout_observed is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"

    reconstructed = read_oss3d2l_preregistration_read_only(
        path,
        protocol_id=source.protocol.protocol_id,
    )
    assert reconstructed == receipt
    assert registry.preregister(protocol=source.protocol, binding=binding, now=NOW) == receipt


def test_new_d2l_preregistration_is_rejected_after_d2k_start(tmp_path):
    source = build_d2l_source(tmp_path)
    binding = _binding(source)
    path = tmp_path / "shared-d2l-d2k.sqlite3"
    registry = SQLiteOSS3PredictiveStrategyRegistry(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE oss3_final_holdout_evaluation_starts (
                evaluation_id TEXT,
                protocol_id TEXT,
                holdout_authorization_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO oss3_final_holdout_evaluation_starts VALUES (?, ?, ?)",
            (
                "d2k-start-before-d2l",
                source.protocol.protocol_id,
                source.protocol.expected_holdout_authorization_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(PredictiveStrategyContractGovernanceError, match="after D2K start"):
        registry.preregister(protocol=source.protocol, binding=binding, now=NOW)


def test_new_d2l_preregistration_is_rejected_after_permit_consumption(tmp_path):
    source = build_d2l_source(tmp_path)
    binding = _binding(source)
    path = tmp_path / "shared-d2l-d2k.sqlite3"
    registry = SQLiteOSS3PredictiveStrategyRegistry(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE holdout_permits (
                permit_id TEXT,
                issued_by TEXT,
                purpose TEXT,
                used_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO holdout_permits VALUES (?, ?, ?, ?)",
            (
                source.protocol.expected_holdout_authorization_id,
                "OSS3D2K_FINAL_HOLDOUT_EVALUATOR",
                "final_validation",
                NOW.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(PredictiveStrategyContractGovernanceError, match="permit consumption"):
        registry.preregister(protocol=source.protocol, binding=binding, now=NOW)


def test_registry_is_append_only_and_conflicting_binding_is_rejected(tmp_path):
    source = build_d2l_source(tmp_path)
    binding = _binding(source)
    path = tmp_path / "shared-d2l-d2k.sqlite3"
    registry = SQLiteOSS3PredictiveStrategyRegistry(path)
    receipt = registry.preregister(protocol=source.protocol, binding=binding, now=NOW)

    conn = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE oss3_predictive_strategy_preregistrations SET strategy_id = 'tampered' WHERE protocol_id = ?",
                (source.protocol.protocol_id,),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM oss3_predictive_strategy_preregistrations WHERE protocol_id = ?",
                (source.protocol.protocol_id,),
            )
        conn.rollback()
    finally:
        conn.close()

    other_policy = replace(canonical_oss3d2l_policy(), selection_fraction=Decimal("0.50"))
    other_binding = build_predictive_strategy_binding(
        protocol=source.protocol,
        winner_output=source.winner_output,
        policy=other_policy,
    )
    assert other_binding.binding_hash != receipt.binding.binding_hash
    with pytest.raises(PredictiveStrategyContractConflict, match="another D2L strategy binding"):
        registry.preregister(protocol=source.protocol, binding=other_binding, now=NOW)


def test_binding_reconstruction_does_not_require_current_source_tree(tmp_path, monkeypatch):
    source = build_d2l_source(tmp_path)
    binding = _binding(source)
    path = tmp_path / "shared-d2l-d2k.sqlite3"
    registry = SQLiteOSS3PredictiveStrategyRegistry(path)
    receipt = registry.preregister(protocol=source.protocol, binding=binding, now=NOW)

    monkeypatch.setattr(
        "labs.oss3_qlib.predictive_strategy_contract.predictive_strategy_code_hash",
        lambda: "f" * 64,
    )
    reconstructed = read_oss3d2l_preregistration_read_only(
        path,
        protocol_id=source.protocol.protocol_id,
    )
    assert reconstructed == receipt
    with pytest.raises(PredictiveStrategyContractIntegrityError, match="projection code differs"):
        project_prediction_artifact(
            binding=reconstructed.binding,
            prediction=source.winner_output.prediction,
        )


def test_public_contract_has_no_label_or_execution_arguments(tmp_path):
    source = build_d2l_source(tmp_path)
    binding = _binding(source)
    payload = binding.to_dict()
    text = repr(payload)
    assert "label_values" not in text
    assert "OrderIntent" not in text
    assert payload["execution_authorized"] is False
    assert payload["paper_execution_authorized"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["live_trading"] == "BLOCKED"
