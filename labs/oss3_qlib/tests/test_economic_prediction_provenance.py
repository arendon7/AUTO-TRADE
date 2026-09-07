from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pytest

from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from labs.oss3_qlib.economic_holdout_evaluator import (
    OSS3D2N_MATERIAL_VERSION,
    EconomicHoldoutMaterial,
)
from labs.oss3_qlib.economic_prediction_provenance import (
    EconomicFeatureRow,
    EconomicPredictionFeatureArtifact,
    EconomicPredictionProvenanceGovernanceError,
    EconomicPredictionProvenanceIntegrityError,
    OSS3D2O_PROVENANCE_VERSION,
    economic_prediction_provenance_semantic_hash,
    run_economic_prediction_provenance,
    verify_economic_prediction_provenance,
)
from labs.oss3_qlib.final_holdout_protocol import SQLiteOSS3FinalHoldoutProtocolRegistry
from labs.oss3_qlib.predictive_economic_protocol import SQLiteOSS3PredictiveEconomicProtocolRegistry
from labs.oss3_qlib.predictive_strategy_contract import (
    SQLiteOSS3PredictiveStrategyRegistry,
    build_predictive_strategy_binding,
)
from labs.oss3_qlib.tests import d2k_fixture
from labs.oss3_qlib.tests.d2n_fixture import (
    _economic_prediction,
    _economic_universe,
    build_runtime_bound_d2n_lineage,
)


UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class D2OSource:
    lineage: object
    d2j_protocol: object
    d2l_receipt: object
    d2m_protocol: object
    features: EconomicPredictionFeatureArtifact
    prediction: QlibPredictionArtifact
    attestation: object
    receipt: object


@pytest.fixture(scope="module")
def source(tmp_path_factory) -> D2OSource:
    tmp_path = tmp_path_factory.mktemp("oss3d2o")
    lineage = build_runtime_bound_d2n_lineage(tmp_path)
    shared = tmp_path / "d2o-shared.sqlite3"

    predictive_material = d2k_fixture.build_final_holdout_material(
        source_request=lineage.winner_output.request,
        train_features=lineage.train_features,
        label_mode="aligned",
    )
    d2j_protocol = SQLiteOSS3FinalHoldoutProtocolRegistry(shared).preregister_and_record(
        protocol_id="oss3d2o-predictive-protocol-001",
        seal=lineage.winner_seal,
        preregistration=lineage.preregistration,
        batch_evidence=lineage.batch_evidence,
        holdout_commitment=predictive_material.commitment,
    )
    binding = build_predictive_strategy_binding(
        protocol=d2j_protocol,
        winner_output=lineage.winner_output,
    )
    d2l_receipt = SQLiteOSS3PredictiveStrategyRegistry(shared).preregister(
        protocol=d2j_protocol,
        binding=binding,
        now=datetime(2026, 6, 15, tzinfo=UTC),
    )

    economic_start = datetime.fromisoformat(
        d2j_protocol.holdout_commitment.partition_end
    ) + timedelta(days=1)
    universe = _economic_universe(start=economic_start, market_mode="flat")
    # D2M consumes only the value-opaque commitment.  The existing D2N material
    # constructor needs a support-valid prediction to expose that commitment;
    # D2O does not consume this placeholder prediction.
    placeholder_prediction = _economic_prediction(
        lineage=lineage,
        binding=binding,
        universe=universe,
    )
    material = EconomicHoldoutMaterial(
        material_version=OSS3D2N_MATERIAL_VERSION,
        commitment_id="oss3d2o-economic-holdout-001",
        universe=universe,
        prediction=placeholder_prediction,
    )
    d2m_protocol = SQLiteOSS3PredictiveEconomicProtocolRegistry(shared).preregister(
        economic_protocol_id="oss3d2o-economic-protocol-001",
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        economic_holdout_commitment=material.commitment,
        now=datetime(2026, 6, 16, tzinfo=UTC),
    )

    feature_names = tuple(feature.name for feature in lineage.train_features.features)
    commitment = d2m_protocol.economic_holdout_commitment
    start = datetime.fromisoformat(commitment.partition_start)
    step = timedelta(seconds=commitment.timeframe_seconds)
    rows = []
    for day in range(1, commitment.bar_count):
        as_of = start + step * day
        for symbol_index, symbol in enumerate(commitment.symbols, start=1):
            # Deterministic, finite point-in-time inputs. These values exercise
            # the software boundary only and make no profitability claim.
            values = tuple(
                0.5 * feature_index
                + 0.1 * symbol_index
                + 0.001 * day
                for feature_index in range(1, len(feature_names) + 1)
            )
            rows.append(
                EconomicFeatureRow(
                    as_of=as_of.isoformat(),
                    available_at=(as_of - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=values,
                )
            )
    features = EconomicPredictionFeatureArtifact.build(
        economic_protocol=d2m_protocol,
        d2l_receipt=d2l_receipt,
        feature_source_hash="a" * 64,
        feature_producer_code_hash="b" * 64,
        feature_names=feature_names,
        rows=tuple(rows),
    )
    prediction, attestation, receipt = run_economic_prediction_provenance(
        economic_protocol=d2m_protocol,
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        source_request=lineage.winner_output.request,
        training_bundle=lineage.training_bundle,
        train_features=lineage.train_features,
        train_labels=lineage.train_labels,
        economic_features=features,
    )
    return D2OSource(
        lineage=lineage,
        d2j_protocol=d2j_protocol,
        d2l_receipt=d2l_receipt,
        d2m_protocol=d2m_protocol,
        features=features,
        prediction=prediction,
        attestation=attestation,
        receipt=receipt,
    )


def test_real_qlib_provenance_binds_exact_feature_model_runtime_and_prediction(source):
    binding = source.d2l_receipt.binding
    receipt = source.receipt
    assert receipt.receipt_version == OSS3D2O_PROVENANCE_VERSION
    assert receipt.economic_protocol_receipt_hash == source.d2m_protocol.receipt_hash
    assert receipt.source_d2l_receipt_hash == source.d2l_receipt.receipt_hash
    assert receipt.source_request_hash == source.lineage.winner_output.request.request_hash
    assert receipt.training_bundle_hash == source.lineage.training_bundle.artifact_hash
    assert receipt.economic_feature_artifact_hash == source.features.artifact_hash
    assert receipt.economic_feature_row_payload_hash == source.features.row_payload_hash
    assert receipt.economic_feature_support_hash == source.features.support_hash
    assert receipt.prediction_artifact_hash == source.prediction.artifact_hash
    assert receipt.prediction_payload_hash == source.prediction.manifest.prediction_payload_hash
    assert receipt.source_environment_attestation_hash == binding.environment_attestation_hash
    assert receipt.observed_environment_attestation_hash == binding.environment_attestation_hash
    assert receipt.source_runtime_environment_hash == binding.runtime_environment_hash
    assert receipt.observed_runtime_environment_hash == binding.runtime_environment_hash
    assert receipt.shared_model_runner_code_hash == binding.shared_runner_code_hash
    assert receipt.provenance_runner_semantic_hash == economic_prediction_provenance_semantic_hash()
    assert receipt.prediction_generated_by_qlib is True
    assert receipt.original_train_bundle_replayed is True
    assert receipt.training_labels_loaded is True
    assert receipt.market_derived_features_loaded is True
    assert receipt.economic_labels_loaded is False
    assert receipt.economic_outcomes_loaded is False
    assert receipt.network_allowed is False
    assert receipt.broker_credentials_present is False
    assert receipt.adaptive_search is False
    assert receipt.hyperparameter_optimization is False
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"

    expected_keys = tuple((row.as_of, row.symbol) for row in source.features.rows)
    actual_keys = tuple((row.timestamp, row.symbol) for row in source.prediction.rows)
    assert actual_keys == expected_keys
    assert len(source.prediction.rows) == (
        (source.d2m_protocol.economic_holdout_commitment.bar_count - 1)
        * source.d2m_protocol.economic_holdout_commitment.symbol_count
    )


def test_verifier_accepts_exact_runner_outputs(source):
    verify_economic_prediction_provenance(
        economic_protocol=source.d2m_protocol,
        d2l_receipt=source.d2l_receipt,
        d2j_protocol=source.d2j_protocol,
        source_request=source.lineage.winner_output.request,
        training_bundle=source.lineage.training_bundle,
        train_features=source.lineage.train_features,
        train_labels=source.lineage.train_labels,
        economic_features=source.features,
        prediction=source.prediction,
        attestation=source.attestation,
        receipt=source.receipt,
    )


def test_tampered_prediction_scores_are_not_covered_by_original_receipt(source):
    manifest = source.prediction.manifest
    changed_rows = list(source.prediction.rows)
    first = changed_rows[0]
    changed_rows[0] = QlibPredictionRow(
        timestamp=first.timestamp,
        symbol=first.symbol,
        score=float(first.score) + 1.0,
    )
    changed = QlibPredictionArtifact.build(
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
        rows=tuple(changed_rows),
    )
    with pytest.raises(EconomicPredictionProvenanceIntegrityError, match="receipt does not rebind"):
        verify_economic_prediction_provenance(
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            source_request=source.lineage.winner_output.request,
            training_bundle=source.lineage.training_bundle,
            train_features=source.lineage.train_features,
            train_labels=source.lineage.train_labels,
            economic_features=source.features,
            prediction=changed,
            attestation=source.attestation,
            receipt=source.receipt,
        )


def test_nonwinner_source_request_is_rejected_before_model_execution(source):
    nonwinner = next(
        output
        for output in source.lineage.outputs
        if output.candidate_id != source.lineage.winner_seal.selected_trial_id
    )
    with pytest.raises(EconomicPredictionProvenanceIntegrityError, match="source request differs"):
        run_economic_prediction_provenance(
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            source_request=nonwinner.request,
            training_bundle=source.lineage.training_bundle,
            train_features=source.lineage.train_features,
            train_labels=source.lineage.train_labels,
            economic_features=source.features,
        )


def test_feature_support_drift_is_structurally_rejected(source):
    with pytest.raises(EconomicPredictionProvenanceIntegrityError, match="support differs"):
        replace(
            source.features,
            rows=source.features.rows[:-1],
            artifact_hash="0" * 64,
        )


def test_feature_artifact_cannot_claim_economic_outcomes_or_labels(source):
    with pytest.raises(EconomicPredictionProvenanceGovernanceError, match="outcomes/labels"):
        replace(
            source.features,
            economic_outcomes_included=True,
            artifact_hash="0" * 64,
        )
    with pytest.raises(EconomicPredictionProvenanceGovernanceError, match="outcomes/labels"):
        replace(
            source.features,
            economic_labels_included=True,
            artifact_hash="0" * 64,
        )


def test_broker_credentials_are_rejected_before_runner_execution(source, monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "forbidden-d2o-key")
    with pytest.raises(EconomicPredictionProvenanceGovernanceError, match="refuses broker"):
        run_economic_prediction_provenance(
            economic_protocol=source.d2m_protocol,
            d2l_receipt=source.d2l_receipt,
            d2j_protocol=source.d2j_protocol,
            source_request=source.lineage.winner_output.request,
            training_bundle=source.lineage.training_bundle,
            train_features=source.lineage.train_features,
            train_labels=source.lineage.train_labels,
            economic_features=source.features,
        )


def test_semantic_hash_is_stable_lowercase_sha256():
    first = economic_prediction_provenance_semantic_hash()
    second = economic_prediction_provenance_semantic_hash()
    assert first == second
    assert len(first) == 64
    assert first == first.lower()
    int(first, 16)
