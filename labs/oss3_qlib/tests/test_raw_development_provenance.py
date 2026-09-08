from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import sqlite3

import pytest

from autotrade.research.market import Bar, MarketDataset
from autotrade.research.universe import AlignedMarketUniverse
from labs.oss3_qlib.family_evaluation_batch import (
    evaluate_preregistered_family,
    preregister_family_evaluation,
)
from labs.oss3_qlib.raw_development_provenance import (
    COMMON_SUPPORT_POLICY,
    MULTIPLE_TESTING_POLICY,
    PRIMARY_METRIC,
    RawDevelopmentMarketSource,
    RawDevelopmentProvenanceGovernanceError,
    RawDevelopmentProvenanceIntegrityError,
    SQLiteOSS3RawDevelopmentPreregistrationRegistry,
    bind_completed_raw_development_evidence,
    derive_raw_development_features,
    materialize_development_labels_after_preregistration,
    prepare_d2h_from_d2s_reveal,
    prepare_raw_development_preregistration,
)
from labs.oss3_qlib.tests.d2s_fixture import (
    D2S_PREREG_ID,
    D2S_TOURNAMENT_CAMPAIGN,
    D2S_TOURNAMENT_ID,
    build_d2s_source,
    run_real_d2g_outputs,
)


UTC = timezone.utc
NOW = datetime(2026, 7, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def source():
    return build_d2s_source()


@pytest.fixture(scope="module")
def outputs(source, tmp_path_factory):
    root = tmp_path_factory.mktemp("d2s-real-d2g")
    return run_real_d2g_outputs(source, root)


def _prereg(source, outputs):
    return prepare_raw_development_preregistration(
        preregistration_id=D2S_PREREG_ID,
        tournament_campaign_id=D2S_TOURNAMENT_CAMPAIGN,
        tournament_id=D2S_TOURNAMENT_ID,
        d2f_plan=source.d2f_plan,
        d2f_request_set=source.d2f_request_set,
        outputs=outputs,
        raw_source=source.raw_source,
        development_features=source.development_features,
    )


def test_d2s_features_are_raw_derived_causal_and_schema_compatible(source):
    features = source.development_features
    assert features.manifest.partition == "DEVELOPMENT"
    assert features.manifest.row_count == source.raw_source.sample_count
    assert features.manifest.source_dataset_hash == source.raw_source.source_hash
    assert features.manifest.source_universe_hash == source.raw_source.universe_identity_hash
    assert features.manifest.feature_schema_hash == source.train_source.training_bundle.manifest.feature_schema_hash
    assert tuple((row.as_of, row.symbol) for row in features.rows) == source.raw_source.evaluation_keys
    assert source.raw_source.full_development_path_loaded is True
    assert source.raw_source.supervised_label_artifact_materialized is False


def test_d2s_preregistration_contains_no_label_artifact_or_label_values(source, outputs):
    prereg = _prereg(source, outputs)
    payload = prereg.to_dict()
    assert "label_artifact_hash" not in payload
    assert "development_label_artifact_hash" not in payload
    # `label_values_used=False` is an intentional governance proof.  What D2S
    # forbids is any field containing actual label values or an artifact hash.
    assert "label_values_used" in payload and payload["label_values_used"] is False
    assert not any(key in payload for key in ("labels", "label_rows", "label_payload", "label_value_array"))
    assert prereg.label_artifact_materialized is False
    assert prereg.label_values_used is False
    assert prereg.development_metrics_computed is False
    assert prereg.primary_metric == PRIMARY_METRIC
    assert prereg.multiple_testing_policy == MULTIPLE_TESTING_POLICY
    assert prereg.common_support_policy == COMMON_SUPPORT_POLICY
    assert len(prereg.candidate_bindings) == 6
    assert len({binding.prediction_artifact_hash for binding in prereg.candidate_bindings}) == 6


def test_label_materialization_fails_before_durable_d2s_preregistration(source, outputs, tmp_path):
    prereg = _prereg(source, outputs)
    registry = SQLiteOSS3RawDevelopmentPreregistrationRegistry(tmp_path / "d2s.sqlite3")
    with pytest.raises(RawDevelopmentProvenanceGovernanceError, match="durable D2S preregistration"):
        materialize_development_labels_after_preregistration(
            registry=registry,
            preregistration=prereg,
            raw_source=source.raw_source,
            development_features=source.development_features,
        )


def test_d2s_registry_is_idempotent_exact_and_append_only(source, outputs, tmp_path):
    prereg = _prereg(source, outputs)
    path = tmp_path / "d2s.sqlite3"
    registry = SQLiteOSS3RawDevelopmentPreregistrationRegistry(path)
    registry.preregister(prereg, now=NOW)
    registry.preregister(prereg, now=NOW)
    registry.require_exact(prereg)

    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="APPEND_ONLY"):
            conn.execute(
                "UPDATE oss3d2s_raw_development_preregistrations SET fingerprint = ? WHERE preregistration_id = ?",
                ("f" * 64, prereg.preregistration_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="APPEND_ONLY"):
            conn.execute(
                "DELETE FROM oss3d2s_raw_development_preregistrations WHERE preregistration_id = ?",
                (prereg.preregistration_id,),
            )


def test_label_reveal_after_durable_preregistration_is_exact_one_bar_forward(source, outputs, tmp_path):
    prereg = _prereg(source, outputs)
    registry = SQLiteOSS3RawDevelopmentPreregistrationRegistry(tmp_path / "d2s.sqlite3")
    registry.preregister(prereg, now=NOW)
    labels, reveal = materialize_development_labels_after_preregistration(
        registry=registry,
        preregistration=prereg,
        raw_source=source.raw_source,
        development_features=source.development_features,
    )
    assert labels.manifest.partition == "DEVELOPMENT"
    assert labels.manifest.row_count == prereg.observation_count
    assert labels.artifact_hash == reveal.label_artifact_hash
    assert reveal.durable_d2s_preregistration_verified is True
    assert reveal.predictions_frozen_before_label_materialization is True
    assert reveal.label_values_materialized_after_durable_preregistration is True
    assert reveal.development_metrics_computed is False

    first = labels.rows[0]
    raw = source.development_universe.dataset(first.symbol)
    expected = float(raw.bars[1].close / raw.bars[0].close - Decimal("1"))
    assert first.value == pytest.approx(expected, abs=1e-15)
    assert first.label_as_of == raw.bars[0].ended_at.isoformat()
    assert first.horizon_end == raw.bars[1].ended_at.isoformat()
    assert first.available_at == first.horizon_end


def test_d2s_end_to_end_extends_into_d2h_d2e_without_early_labels(source, outputs, tmp_path):
    prereg = _prereg(source, outputs)
    registry = SQLiteOSS3RawDevelopmentPreregistrationRegistry(tmp_path / "d2s.sqlite3")
    registry.preregister(prereg, now=NOW)
    labels, reveal = materialize_development_labels_after_preregistration(
        registry=registry,
        preregistration=prereg,
        raw_source=source.raw_source,
        development_features=source.development_features,
    )
    d2h = prepare_d2h_from_d2s_reveal(
        registry=registry,
        preregistration=prereg,
        reveal=reveal,
        labels=labels,
        d2f_plan=source.d2f_plan,
        d2f_request_set=source.d2f_request_set,
        outputs=outputs,
    )
    assert d2h.d2e_plan.campaign.campaign_id == D2S_TOURNAMENT_CAMPAIGN
    assert d2h.d2e_plan.tournament.tournament_id == D2S_TOURNAMENT_ID
    assert d2h.d2e_plan.dataset.development_label_artifact_hash == labels.artifact_hash
    assert d2h.d2e_plan.dataset.evaluation_keyset_hash == prereg.evaluation_keyset_hash
    assert d2h.d2e_plan.runtime_environment.fingerprint == prereg.runtime_environment_hash

    from autotrade.research.trials import SQLiteTrialLedger

    ledger = SQLiteTrialLedger(tmp_path / "d2h.sqlite3")
    preregister_family_evaluation(ledger, d2h, now=NOW)
    batch = evaluate_preregistered_family(
        ledger,
        d2h,
        outputs=outputs,
        development_labels=labels,
        now=NOW,
    )
    completed = bind_completed_raw_development_evidence(
        preregistration=prereg,
        reveal=reveal,
        d2h_preregistration=d2h,
        batch_evidence=batch,
    )
    assert completed.label_values_materialized_after_d2s_preregistration is True
    assert completed.metrics_computed_after_d2h_preregistration is True
    assert completed.label_artifact_hash == labels.artifact_hash
    assert completed.winner_trial_id in {output.candidate_id for output in outputs}
    assert batch.tournament_evidence.family_size == 6
    assert batch.final_holdout_observed is False
    assert batch.promotion_authorized is False
    assert batch.execution_authorized is False


def test_mutating_future_development_bar_cannot_change_prior_features(source):
    mutation_index = 5
    altered_universe = _mutate_close(
        source.development_universe,
        symbol=source.development_universe.symbols[1],
        index=mutation_index,
        multiplier=Decimal("1.15"),
    )
    altered_raw = RawDevelopmentMarketSource.build(
        warmup_universe=source.warmup_universe,
        development_universe=altered_universe,
    )
    altered_features = derive_raw_development_features(
        raw_source=altered_raw,
        campaign_id=source.development_features.manifest.campaign_id,
        research_split_hash=source.development_features.manifest.research_split_hash,
    )
    width = len(source.development_universe.symbols)
    assert altered_features.rows[: mutation_index * width] == source.development_features.rows[: mutation_index * width]
    assert altered_features.manifest.row_payload_hash != source.development_features.manifest.row_payload_hash


def test_raw_source_drift_after_preregistration_blocks_label_reveal(source, outputs, tmp_path):
    prereg = _prereg(source, outputs)
    registry = SQLiteOSS3RawDevelopmentPreregistrationRegistry(tmp_path / "d2s.sqlite3")
    registry.preregister(prereg, now=NOW)
    altered = _mutate_close(
        source.development_universe,
        symbol=source.development_universe.symbols[0],
        index=source.development_universe.bar_count - 1,
        multiplier=Decimal("1.25"),
    )
    altered_raw = RawDevelopmentMarketSource.build(
        warmup_universe=source.warmup_universe,
        development_universe=altered,
    )
    with pytest.raises(RawDevelopmentProvenanceIntegrityError, match="raw DEVELOPMENT source changed"):
        materialize_development_labels_after_preregistration(
            registry=registry,
            preregistration=prereg,
            raw_source=altered_raw,
            development_features=source.development_features,
        )


def test_d2s_preregistration_and_reveal_cannot_be_mutated_into_authority(source, outputs, tmp_path):
    prereg = _prereg(source, outputs)
    with pytest.raises(RawDevelopmentProvenanceGovernanceError):
        replace(prereg, promotion_authorized=True)
    with pytest.raises(RawDevelopmentProvenanceGovernanceError):
        replace(prereg, execution_authorized=True)
    with pytest.raises(RawDevelopmentProvenanceGovernanceError):
        replace(prereg, capital_authority="PAPER")

    registry = SQLiteOSS3RawDevelopmentPreregistrationRegistry(tmp_path / "d2s.sqlite3")
    registry.preregister(prereg, now=NOW)
    _, reveal = materialize_development_labels_after_preregistration(
        registry=registry,
        preregistration=prereg,
        raw_source=source.raw_source,
        development_features=source.development_features,
    )
    with pytest.raises(RawDevelopmentProvenanceGovernanceError):
        replace(reveal, promotion_authorized=True)
    with pytest.raises(RawDevelopmentProvenanceGovernanceError):
        replace(reveal, execution_authorized=True)


def test_statistical_policy_cannot_drift_after_predictions(source, outputs):
    prereg = _prereg(source, outputs)
    with pytest.raises(RawDevelopmentProvenanceGovernanceError, match="primary metric"):
        replace(prereg, primary_metric="sharpe")
    with pytest.raises(RawDevelopmentProvenanceGovernanceError, match="multiple-testing"):
        replace(prereg, multiple_testing_policy="NONE")
    with pytest.raises(RawDevelopmentProvenanceGovernanceError, match="common-support"):
        replace(prereg, common_support_policy="ANY")


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
