from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from autotrade.research.market import Bar, MarketDataset
from autotrade.research.oss3_concrete_model_family import build_concrete_model_request_set
from autotrade.research.universe import AlignedMarketUniverse
from labs.oss3_qlib.family_model_contract import family_runner_code_hash
from labs.oss3_qlib.family_runner import run_isolated_qlib_family_candidate
from labs.oss3_qlib.raw_training_bundle_provenance import (
    CANONICAL_LOOKBACK_BARS,
    RawTrainingBundleProvenanceGovernanceError,
    RawTrainingBundleProvenanceIntegrityError,
    RawTrainingMarketSource,
    canonical_oss3d2r_feature_definitions,
    canonical_oss3d2r_label_formula,
    derive_raw_training_bundle,
    research_universe_identity_hash,
    verify_raw_training_bundle_provenance,
)
from labs.oss3_qlib.tests.d2r_fixture import (
    CAMPAIGN_ID,
    SPLIT_HASH,
    build_d2r_source,
    write_d2g_inputs,
)


@pytest.fixture(scope="module")
def source():
    return build_d2r_source()


def test_d2r_derives_complete_exact_training_bundle(source):
    receipt = source.provenance_receipt
    bundle = source.training_bundle
    assert receipt.training_bundle_hash == bundle.artifact_hash
    assert receipt.feature_artifact_hash == source.train_features.artifact_hash
    assert receipt.label_artifact_hash == source.train_labels.artifact_hash
    assert bundle.manifest.sample_count == source.raw_source.sample_count
    assert bundle.manifest.feature_artifact_hash == source.train_features.artifact_hash
    assert bundle.manifest.label_artifact_hash == source.train_labels.artifact_hash
    assert receipt.feature_causal_prefix_enforced is True
    assert receipt.feature_future_values_used is False
    assert receipt.label_future_values_used is True
    assert receipt.label_future_values_confined_to_explicit_horizon is True
    assert receipt.label_horizon_bars == 1
    assert receipt.label_horizon_crossed_train_boundary is False
    assert receipt.exact_feature_label_keyset is True
    assert receipt.development_values_loaded is False
    assert receipt.final_holdout_values_loaded is False
    assert receipt.execution_authorized is False
    assert receipt.capital_authority == "NONE"


def test_schema_and_research_universe_identity_are_material_independent(source):
    assert source.train_features.manifest.feature_schema_hash == source.development_features.manifest.feature_schema_hash
    assert source.training_bundle.manifest.source_universe_hash == source.raw_source.universe_identity_hash
    assert source.development_features.manifest.source_universe_hash == source.raw_source.universe_identity_hash
    assert source.raw_source.universe_identity_hash == research_universe_identity_hash(source.training_universe)
    # Material universe hash contains bars and therefore must be a separate identity.
    assert source.training_universe.universe_hash != source.raw_source.universe_identity_hash


def test_label_formula_is_exact_one_bar_forward_simple_close_return(source):
    formula = canonical_oss3d2r_label_formula()
    assert formula.expression == "close_t_plus_1 / close_t - 1"
    assert formula.horizon_bars == 1
    first_label = source.train_labels.rows[0]
    symbol = first_label.symbol
    raw = source.training_universe.dataset(symbol)
    expected = float(raw.bars[1].close / raw.bars[0].close - Decimal("1"))
    assert first_label.value == pytest.approx(expected, abs=1e-15)
    assert first_label.horizon_end == raw.bars[1].ended_at.isoformat()
    assert first_label.available_at == first_label.horizon_end


def test_terminal_train_bar_is_label_endpoint_not_sample_origin(source):
    raw = source.training_universe
    last_origin = raw.datasets[0].bars[-2].ended_at.isoformat()
    terminal_end = raw.datasets[0].bars[-1].ended_at.isoformat()
    assert source.train_features.rows[-len(raw.symbols)].as_of == last_origin
    assert source.train_labels.rows[-len(raw.symbols)].label_as_of == last_origin
    assert source.train_labels.rows[-len(raw.symbols)].horizon_end == terminal_end
    assert all(row.as_of != terminal_end for row in source.train_features.rows)
    assert all(row.label_as_of != terminal_end for row in source.train_labels.rows)
    assert raw.datasets[0].bars[-1].ended_at < source.raw_source.partition_end


def test_mutating_terminal_bar_changes_labels_but_not_feature_row_payload(source):
    altered_training = _mutate_close(
        source.training_universe,
        symbol=source.training_universe.symbols[0],
        index=source.training_universe.bar_count - 1,
        multiplier=Decimal("1.20"),
    )
    altered_raw = RawTrainingMarketSource.build(
        warmup_universe=source.warmup_universe,
        training_universe=altered_training,
    )
    altered_features, altered_labels, _, _ = derive_raw_training_bundle(
        raw_source=altered_raw,
        campaign_id=CAMPAIGN_ID,
        research_split_hash=SPLIT_HASH,
    )
    assert altered_raw.universe_identity_hash == source.raw_source.universe_identity_hash
    assert altered_training.universe_hash != source.training_universe.universe_hash
    assert altered_features.manifest.feature_schema_hash == source.train_features.manifest.feature_schema_hash
    assert altered_features.manifest.row_payload_hash == source.train_features.manifest.row_payload_hash
    assert altered_labels.manifest.row_payload_hash != source.train_labels.manifest.row_payload_hash


def test_mutating_future_train_bar_cannot_change_prior_feature_rows(source):
    mutation_index = 12
    altered_training = _mutate_close(
        source.training_universe,
        symbol=source.training_universe.symbols[1],
        index=mutation_index,
        multiplier=Decimal("1.10"),
    )
    altered_raw = RawTrainingMarketSource.build(
        warmup_universe=source.warmup_universe,
        training_universe=altered_training,
    )
    altered_features, altered_labels, _, _ = derive_raw_training_bundle(
        raw_source=altered_raw,
        campaign_id=CAMPAIGN_ID,
        research_split_hash=SPLIT_HASH,
    )
    width = len(source.training_universe.symbols)
    prior_rows = mutation_index * width
    assert altered_features.rows[:prior_rows] == source.train_features.rows[:prior_rows]
    # The label at origin index 11 for the mutated symbol legitimately observes
    # bar 12 as its explicit one-bar target, so labels may change one origin earlier.
    prior_label_rows = (mutation_index - 1) * width
    assert altered_labels.rows[:prior_label_rows] == source.train_labels.rows[:prior_label_rows]


def test_exact_twenty_bar_warmup_is_required(source):
    shortened = AlignedMarketUniverse.from_datasets(
        datasets=tuple(
            MarketDataset(
                instrument=dataset.instrument,
                bars=dataset.bars[1:],
                source=dataset.source + "#short",
            )
            for dataset in source.warmup_universe.datasets
        ),
        universe_name="oss3d2r-short-warmup",
    )
    assert shortened.bar_count == CANONICAL_LOOKBACK_BARS - 1
    with pytest.raises(RawTrainingBundleProvenanceGovernanceError, match="twenty"):
        RawTrainingMarketSource.build(
            warmup_universe=shortened,
            training_universe=source.training_universe,
        )


def test_instrument_semantic_drift_changes_research_universe_identity(source):
    training = source.training_universe
    dataset = training.datasets[0]
    drifted_instrument = replace(dataset.instrument, price_tick=Decimal("0.10"))
    drifted_dataset = MarketDataset(
        instrument=drifted_instrument,
        bars=dataset.bars,
        source=dataset.source + "#tick-drift",
    )
    drifted = AlignedMarketUniverse.from_datasets(
        datasets=(drifted_dataset, *training.datasets[1:]),
        universe_name="oss3d2r-training-universe#drift",
    )
    assert research_universe_identity_hash(drifted) != source.raw_source.universe_identity_hash
    with pytest.raises(RawTrainingBundleProvenanceIntegrityError, match="identity"):
        RawTrainingMarketSource.build(
            warmup_universe=source.warmup_universe,
            training_universe=drifted,
        )


def test_raw_verifier_rejects_feature_and_label_tampering(source):
    bad_feature = replace(
        source.train_features.rows[0],
        values=(source.train_features.rows[0].values[0] + 0.5, source.train_features.rows[0].values[1]),
    )
    from autotrade.research.oss3_factor_matrix_artifact import FactorMatrixArtifact, FactorMatrixPartition
    fm = source.train_features.manifest
    tampered_features = FactorMatrixArtifact.build(
        campaign_id=fm.campaign_id,
        research_split_hash=fm.research_split_hash,
        partition=FactorMatrixPartition.TRAIN,
        partition_start=source.raw_source.partition_start,
        partition_end=source.raw_source.partition_end,
        producer_code_hash=fm.producer_code_hash,
        source_dataset_hash=fm.source_dataset_hash,
        source_universe_hash=fm.source_universe_hash,
        features=source.train_features.features,
        rows=(bad_feature, *source.train_features.rows[1:]),
    )
    with pytest.raises(RawTrainingBundleProvenanceIntegrityError, match="feature artifact"):
        verify_raw_training_bundle_provenance(
            raw_source=source.raw_source,
            campaign_id=CAMPAIGN_ID,
            research_split_hash=SPLIT_HASH,
            features=tampered_features,
            labels=source.train_labels,
            bundle=source.training_bundle,
            receipt=source.provenance_receipt,
        )

    bad_label = replace(source.train_labels.rows[0], value=source.train_labels.rows[0].value + 0.25)
    from autotrade.research.oss3_supervised_label_artifact import LabelPartition, SupervisedLabelArtifact
    lm = source.train_labels.manifest
    tampered_labels = SupervisedLabelArtifact.build(
        campaign_id=lm.campaign_id,
        research_split_hash=lm.research_split_hash,
        partition=LabelPartition.TRAIN,
        partition_start=source.raw_source.partition_start,
        partition_end=source.raw_source.partition_end,
        producer_code_hash=lm.producer_code_hash,
        source_dataset_hash=lm.source_dataset_hash,
        source_universe_hash=lm.source_universe_hash,
        label=source.train_labels.label,
        rows=(bad_label, *source.train_labels.rows[1:]),
    )
    with pytest.raises(RawTrainingBundleProvenanceIntegrityError, match="label artifact"):
        verify_raw_training_bundle_provenance(
            raw_source=source.raw_source,
            campaign_id=CAMPAIGN_ID,
            research_split_hash=SPLIT_HASH,
            features=source.train_features,
            labels=tampered_labels,
            bundle=source.training_bundle,
            receipt=source.provenance_receipt,
        )


def test_receipt_cannot_be_mutated_into_authority(source):
    with pytest.raises(RawTrainingBundleProvenanceGovernanceError):
        replace(source.provenance_receipt, promotion_authorized=True)
    with pytest.raises(RawTrainingBundleProvenanceGovernanceError):
        replace(source.provenance_receipt, execution_authorized=True)
    with pytest.raises(RawTrainingBundleProvenanceGovernanceError):
        replace(source.provenance_receipt, capital_authority="PAPER")


def test_raw_derived_bundle_builds_exact_six_candidate_request_set(source):
    plan, requests = build_concrete_model_request_set(
        training_bundle=source.training_bundle,
        development_features=source.development_features,
        shared_runner_code_hash=family_runner_code_hash(),
    )
    assert len(plan.candidates) == 6
    assert len(requests.bindings) == 6
    assert requests.training_bundle_hash == source.training_bundle.artifact_hash
    assert all(binding.request.manifest.feature_schema_hash == source.training_bundle.manifest.feature_schema_hash for binding in requests.bindings)
    assert all(binding.request.manifest.source_universe_hash == source.raw_source.universe_identity_hash for binding in requests.bindings)


def test_raw_derived_bundle_executes_all_six_frozen_qlib_candidates(source, tmp_path):
    _, requests = build_concrete_model_request_set(
        training_bundle=source.training_bundle,
        development_features=source.development_features,
        shared_runner_code_hash=family_runner_code_hash(),
    )
    evidence = []
    for binding in requests.bindings:
        root = tmp_path / binding.candidate_id
        paths = write_d2g_inputs(source, root, binding.request)
        run = run_isolated_qlib_family_candidate(
            request_path=paths["request"],
            training_bundle_path=paths["bundle"],
            train_features_path=paths["features"],
            train_labels_path=paths["labels"],
            development_features_path=paths["development"],
            prediction_output_path=paths["prediction"],
            receipt_output_path=paths["receipt"],
            environment_attestation_output_path=paths["attestation"],
            runtime_identity_output_path=paths["runtime"],
            run_evidence_output_path=paths["evidence"],
        )
        evidence.append(run)
        assert run.request_hash == binding.request.request_hash
        assert run.development_labels_loaded is False
        assert run.final_holdout_loaded is False
        assert run.execution_authorized is False
        assert paths["prediction"].is_file()
    assert len(evidence) == 6
    assert len({item.model_config_hash for item in evidence}) == 6


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
                source=dataset.source + "#mutated",
            )
        )
    return AlignedMarketUniverse.from_datasets(
        datasets=tuple(datasets),
        universe_name=universe.universe_name + "#mutated",
    )
