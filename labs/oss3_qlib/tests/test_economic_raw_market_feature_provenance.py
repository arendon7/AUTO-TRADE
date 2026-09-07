from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from autotrade.research.market import Bar, MarketDataset
from autotrade.research.oss3_factor_matrix_artifact import (
    FactorDefinition,
    FactorMatrixArtifact,
    FactorMatrixPartition,
)
from autotrade.research.universe import AlignedMarketUniverse
from labs.oss3_qlib.economic_holdout_evaluator import SQLiteOSS3EconomicHoldoutEvaluationRegistry
from labs.oss3_qlib.economic_prediction_provenance import (
    EconomicFeatureRow,
    EconomicPredictionFeatureArtifact,
)
from labs.oss3_qlib.economic_raw_market_feature_provenance import (
    CANONICAL_FEATURE_NAMES,
    CANONICAL_LOOKBACK_BARS,
    OSS3D2Q_RECEIPT_VERSION,
    RawMarketFeatureProvenanceGovernanceError,
    RawMarketFeatureProvenanceIntegrityError,
    RawMarketFeatureSource,
    canonical_oss3d2q_formula_registry_hash,
    canonical_oss3d2q_formulas,
    derive_canonical_feature_rows,
    derive_economic_feature_artifact_from_raw_market,
    raw_market_feature_producer_semantic_hash,
    verify_raw_market_feature_provenance,
)
from labs.oss3_qlib.tests.d2q_fixture import (
    build_d2q_pre_d2k_source,
    protected_economic_holdout,
    run_d2k,
)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    return build_d2q_pre_d2k_source(
        tmp_path_factory.mktemp("oss3d2q-source"),
        market_mode="favorable",
        admit=True,
    )


def test_formula_registry_is_exact_finite_and_hash_bound():
    formulas = canonical_oss3d2q_formulas()
    assert tuple(item.name for item in formulas) == CANONICAL_FEATURE_NAMES
    assert tuple(item.lookback_bars for item in formulas) == (20, 20)
    assert all(item.input_fields == ("close",) for item in formulas)
    assert len({item.formula_hash for item in formulas}) == 2
    assert len(canonical_oss3d2q_formula_registry_hash()) == 64
    assert all(len(item.formula_hash) == 64 for item in formulas)


def test_d2q_derives_exact_d2o_artifact_and_receipt(source):
    assert source.d2q_receipt.receipt_version == OSS3D2Q_RECEIPT_VERSION
    assert source.d2q_receipt.economic_feature_artifact_hash == source.economic_features.artifact_hash
    assert source.economic_features.feature_source_hash == source.raw_source.source_hash
    assert source.economic_features.feature_producer_code_hash == raw_market_feature_producer_semantic_hash()
    assert source.economic_features.feature_names == CANONICAL_FEATURE_NAMES
    assert source.d2q_receipt.training_formula_identity_verified is True
    assert source.d2q_receipt.training_feature_values_rederived is False
    assert source.d2q_receipt.raw_market_values_loaded is True
    assert source.d2q_receipt.full_economic_path_loaded is True
    assert source.d2q_receipt.causal_prefix_enforced is True
    assert source.d2q_receipt.future_market_values_used_per_row is False
    assert source.d2q_receipt.economic_labels_loaded is False
    assert source.d2q_receipt.prediction_values_loaded is False
    assert source.d2q_receipt.economic_metrics_loaded is False
    assert source.provenance_admission is not None


def test_first_flat_signal_uses_warmup_and_current_close_only(source):
    rows = source.economic_features.rows
    first_time = rows[0].as_of
    first_cross_section = tuple(row for row in rows if row.as_of == first_time)
    assert len(first_cross_section) == len(source.economic_universe.symbols)
    # Economic fixture index 0 is flat for all symbols and warmup is flat at 100.
    assert all(row.values == (0.0, 0.0) for row in first_cross_section)
    assert all(row.available_at == row.as_of for row in first_cross_section)


def test_future_bar_perturbation_cannot_change_prior_feature_rows(source):
    mutation_index = 50
    target_symbol = source.economic_universe.symbols[1]
    altered_universe = _mutate_close(
        source.economic_universe,
        symbol=target_symbol,
        index=mutation_index,
        multiplier=Decimal("1.25"),
    )
    altered_source = RawMarketFeatureSource.build(
        warmup_universe=source.warmup_universe,
        economic_universe=altered_universe,
    )
    original = derive_canonical_feature_rows(
        raw_source=source.raw_source,
        train_features=source.lineage.train_features,
    )
    altered = derive_canonical_feature_rows(
        raw_source=altered_source,
        train_features=source.lineage.train_features,
    )
    width = len(source.economic_universe.symbols)
    prior_count = mutation_index * width
    assert altered[:prior_count] == original[:prior_count]
    changed_slice = slice(mutation_index * width, (mutation_index + 1) * width)
    assert altered[changed_slice] != original[changed_slice]


def test_formula_hash_drift_fails_closed(source):
    original = source.lineage.train_features
    first, second = original.features
    bad_first = FactorDefinition(
        name=first.name,
        dtype=first.dtype,
        role=first.role,
        formula_hash="f" * 64,
        source_id=first.source_id,
        source_hash=first.source_hash,
        lookback_bars=first.lookback_bars,
    )
    bad = _rebuild_train(original, features=(bad_first, second))
    with pytest.raises(RawMarketFeatureProvenanceIntegrityError, match="formula hash"):
        derive_canonical_feature_rows(raw_source=source.raw_source, train_features=bad)


def test_lookback_drift_fails_closed(source):
    original = source.lineage.train_features
    first, second = original.features
    bad_first = FactorDefinition(
        name=first.name,
        dtype=first.dtype,
        role=first.role,
        formula_hash=first.formula_hash,
        source_id=first.source_id,
        source_hash=first.source_hash,
        lookback_bars=19,
    )
    bad = _rebuild_train(original, features=(bad_first, second))
    with pytest.raises(RawMarketFeatureProvenanceIntegrityError, match="lookback"):
        derive_canonical_feature_rows(raw_source=source.raw_source, train_features=bad)


def test_exact_twenty_bar_warmup_is_mandatory(source):
    shortened = AlignedMarketUniverse.from_datasets(
        datasets=tuple(
            MarketDataset(
                instrument=dataset.instrument,
                bars=dataset.bars[1:],
                source=dataset.source + "#shortened",
            )
            for dataset in source.warmup_universe.datasets
        ),
        universe_name="oss3d2q-short-warmup",
    )
    assert shortened.bar_count == CANONICAL_LOOKBACK_BARS - 1
    with pytest.raises(RawMarketFeatureProvenanceGovernanceError, match="twenty"):
        RawMarketFeatureSource.build(
            warmup_universe=shortened,
            economic_universe=source.economic_universe,
        )


def test_committed_economic_raw_market_identity_cannot_drift(source):
    altered_universe = _mutate_close(
        source.economic_universe,
        symbol=source.economic_universe.symbols[0],
        index=30,
        multiplier=Decimal("1.10"),
    )
    altered_source = RawMarketFeatureSource.build(
        warmup_universe=source.warmup_universe,
        economic_universe=altered_universe,
    )
    with pytest.raises(RawMarketFeatureProvenanceIntegrityError, match="universe hash"):
        derive_economic_feature_artifact_from_raw_market(
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            train_features=source.lineage.train_features,
            raw_source=altered_source,
        )


def test_feature_artifact_tampering_is_detected_by_raw_rederivation(source):
    first = source.economic_features.rows[0]
    tampered_rows = (
        EconomicFeatureRow(
            as_of=first.as_of,
            available_at=first.available_at,
            symbol=first.symbol,
            values=(first.values[0] + 0.5, first.values[1]),
        ),
        *source.economic_features.rows[1:],
    )
    tampered = EconomicPredictionFeatureArtifact.build(
        economic_protocol=source.d2m_protocol,
        d2l_receipt=source.d2l_receipt,
        feature_source_hash=source.raw_source.source_hash,
        feature_producer_code_hash=raw_market_feature_producer_semantic_hash(),
        feature_names=CANONICAL_FEATURE_NAMES,
        rows=tampered_rows,
    )
    with pytest.raises(RawMarketFeatureProvenanceIntegrityError, match="does not reproduce"):
        verify_raw_market_feature_provenance(
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            train_features=source.lineage.train_features,
            raw_source=source.raw_source,
            artifact=tampered,
            receipt=source.d2q_receipt,
        )


def test_d2q_receipt_cannot_be_mutated_into_promotion_or_execution(source):
    with pytest.raises(RawMarketFeatureProvenanceGovernanceError):
        replace(source.d2q_receipt, promotion_authorized=True)
    with pytest.raises(RawMarketFeatureProvenanceGovernanceError):
        replace(source.d2q_receipt, execution_authorized=True)
    with pytest.raises(RawMarketFeatureProvenanceGovernanceError):
        replace(source.d2q_receipt, capital_authority="PAPER")


def test_d2q_output_runs_through_d2o_d2p_d2k_and_d2n(tmp_path):
    candidate = build_d2q_pre_d2k_source(tmp_path, market_mode="favorable", admit=True)
    predictive = run_d2k(candidate)
    assert predictive.decision.value == "PASS"
    economic = SQLiteOSS3EconomicHoldoutEvaluationRegistry(candidate.shared_sqlite_path).evaluate(
        evaluation_id="oss3d2q-economic-e2e-001",
        economic_protocol=candidate.d2m_protocol,
        d2l_receipt=candidate.d2l_receipt,
        d2j_protocol=candidate.d2j_protocol,
        holdout=protected_economic_holdout(candidate),
        now=datetime(2026, 6, 18, tzinfo=candidate.economic_universe.timestamps[0].tzinfo),
    )
    assert economic.decision.value in {"PASS", "FAIL"}
    assert economic.execution_authorized is False
    assert economic.paper_execution_authorized is False
    assert economic.capital_authority == "NONE"
    assert economic.live_trading == "BLOCKED"


def _rebuild_train(
    original: FactorMatrixArtifact,
    *,
    features: tuple[FactorDefinition, ...],
) -> FactorMatrixArtifact:
    manifest = original.manifest
    return FactorMatrixArtifact.build(
        campaign_id=manifest.campaign_id,
        research_split_hash=manifest.research_split_hash,
        partition=FactorMatrixPartition.TRAIN,
        partition_start=datetime.fromisoformat(manifest.partition_start),
        partition_end=datetime.fromisoformat(manifest.partition_end),
        producer_code_hash=manifest.producer_code_hash,
        source_dataset_hash=manifest.source_dataset_hash,
        source_universe_hash=manifest.source_universe_hash,
        features=features,
        rows=original.rows,
    )


def _mutate_close(
    universe: AlignedMarketUniverse,
    *,
    symbol: str,
    index: int,
    multiplier: Decimal,
) -> AlignedMarketUniverse:
    datasets = []
    for dataset in universe.datasets:
        bars = list(dataset.bars)
        if dataset.instrument.symbol == symbol:
            original = bars[index]
            close = original.close * multiplier
            bars[index] = Bar(
                symbol=original.symbol,
                started_at=original.started_at,
                timeframe_seconds=original.timeframe_seconds,
                open=original.open,
                high=max(original.open, close) * Decimal("1.001"),
                low=min(original.open, close) * Decimal("0.999"),
                close=close,
                volume=original.volume,
            )
        datasets.append(
            MarketDataset(
                instrument=dataset.instrument,
                bars=tuple(bars),
                source=dataset.source + "#future-perturbation",
            )
        )
    return AlignedMarketUniverse.from_datasets(
        datasets=tuple(datasets),
        universe_name=universe.universe_name + "#future-perturbation",
    )
