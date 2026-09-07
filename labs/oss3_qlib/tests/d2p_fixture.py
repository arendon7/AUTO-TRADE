from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from labs.oss3_qlib.economic_holdout_evaluator import (
    OSS3D2N_MATERIAL_VERSION,
    EconomicHoldoutMaterial,
    ProtectedEconomicHoldout,
)
from labs.oss3_qlib.economic_prediction_precommit import (
    OSS3EconomicPredictionPrecommitReceipt,
    SQLiteOSS3EconomicPredictionPrecommitRegistry,
)
from labs.oss3_qlib.economic_prediction_provenance import (
    EconomicFeatureRow,
    EconomicPredictionFeatureArtifact,
    OSS3EconomicPredictionProvenanceReceipt,
    run_economic_prediction_provenance,
)
from labs.oss3_qlib.economic_prediction_provenance_admission import (
    OSS3EconomicPredictionProvenanceAdmissionReceipt,
    SQLiteOSS3EconomicPredictionProvenanceAdmissionRegistry,
)
from labs.oss3_qlib.final_holdout_evaluator import (
    ProtectedOSS3FinalHoldout,
    SQLiteOSS3FinalHoldoutEvaluationRegistry,
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
class D2PPreparedSource:
    lineage: object
    shared_sqlite_path: Path
    predictive_material: object
    d2j_protocol: object
    d2l_binding: object
    d2l_receipt: object
    d2m_protocol: object
    economic_universe: object
    economic_features: EconomicPredictionFeatureArtifact
    prediction: object
    d2o_attestation: object
    d2o_receipt: OSS3EconomicPredictionProvenanceReceipt
    prediction_precommit: OSS3EconomicPredictionPrecommitReceipt
    provenance_admission: OSS3EconomicPredictionProvenanceAdmissionReceipt | None

    @property
    def economic_material(self) -> EconomicHoldoutMaterial:
        return EconomicHoldoutMaterial(
            material_version=OSS3D2N_MATERIAL_VERSION,
            commitment_id=self.d2m_protocol.economic_holdout_commitment.commitment_id,
            universe=self.economic_universe,
            prediction=self.prediction,
        )


def build_d2p_pre_d2k_source(
    tmp_path,
    *,
    market_mode: str = "flat",
    admit: bool = True,
) -> D2PPreparedSource:
    if market_mode not in {"favorable", "adverse", "flat"}:
        raise ValueError("unsupported market_mode")

    lineage = build_runtime_bound_d2n_lineage(tmp_path)
    shared = tmp_path / f"d2p-shared-{market_mode}.sqlite3"

    predictive_material = d2k_fixture.build_final_holdout_material(
        source_request=lineage.winner_output.request,
        train_features=lineage.train_features,
        label_mode="aligned",
    )
    d2j_protocol = SQLiteOSS3FinalHoldoutProtocolRegistry(shared).preregister_and_record(
        protocol_id=f"oss3d2p-predictive-protocol-{market_mode}",
        seal=lineage.winner_seal,
        preregistration=lineage.preregistration,
        batch_evidence=lineage.batch_evidence,
        holdout_commitment=predictive_material.commitment,
    )

    d2l_binding = build_predictive_strategy_binding(
        protocol=d2j_protocol,
        winner_output=lineage.winner_output,
    )
    d2l_receipt = SQLiteOSS3PredictiveStrategyRegistry(shared).preregister(
        protocol=d2j_protocol,
        binding=d2l_binding,
        now=datetime(2026, 6, 15, tzinfo=UTC),
    )

    economic_start = (
        datetime.fromisoformat(d2j_protocol.holdout_commitment.partition_end)
        + timedelta(days=1)
    )
    universe = _economic_universe(start=economic_start, market_mode=market_mode)
    placeholder_prediction = _economic_prediction(
        lineage=lineage,
        binding=d2l_binding,
        universe=universe,
    )
    placeholder_material = EconomicHoldoutMaterial(
        material_version=OSS3D2N_MATERIAL_VERSION,
        commitment_id=f"oss3d2p-economic-holdout-{market_mode}",
        universe=universe,
        prediction=placeholder_prediction,
    )
    d2m_protocol = SQLiteOSS3PredictiveEconomicProtocolRegistry(shared).preregister(
        economic_protocol_id=f"oss3d2p-economic-protocol-{market_mode}",
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        economic_holdout_commitment=placeholder_material.commitment,
        now=datetime(2026, 6, 16, tzinfo=UTC),
    )

    # Install both the D2N exact-prediction gate and the D2P provenance gate
    # before creating any precommit or D2K state.
    d2p_registry = SQLiteOSS3EconomicPredictionProvenanceAdmissionRegistry(shared)

    feature_names = tuple(feature.name for feature in lineage.train_features.features)
    commitment = d2m_protocol.economic_holdout_commitment
    start = datetime.fromisoformat(commitment.partition_start)
    step = timedelta(seconds=commitment.timeframe_seconds)
    rows = []
    for bar_index in range(1, commitment.bar_count):
        as_of = start + step * bar_index
        for symbol_index, symbol in enumerate(commitment.symbols, start=1):
            values = tuple(
                0.6 * feature_index
                + 0.11 * symbol_index
                + 0.001 * bar_index
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
    economic_features = EconomicPredictionFeatureArtifact.build(
        economic_protocol=d2m_protocol,
        d2l_receipt=d2l_receipt,
        feature_source_hash="c" * 64,
        feature_producer_code_hash="d" * 64,
        feature_names=feature_names,
        rows=tuple(rows),
    )

    prediction, d2o_attestation, d2o_receipt = run_economic_prediction_provenance(
        economic_protocol=d2m_protocol,
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        source_request=lineage.winner_output.request,
        training_bundle=lineage.training_bundle,
        train_features=lineage.train_features,
        train_labels=lineage.train_labels,
        economic_features=economic_features,
    )

    precommit_registry = SQLiteOSS3EconomicPredictionPrecommitRegistry(shared)
    prediction_precommit = precommit_registry.preregister(
        precommit_id=f"oss3d2p-prediction-precommit-{market_mode}",
        economic_protocol=d2m_protocol,
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        prediction=prediction,
        now=datetime(2026, 6, 16, 12, tzinfo=UTC),
    )

    provenance_admission = None
    if admit:
        provenance_admission = d2p_registry.admit_by_replay(
            admission_id=f"oss3d2p-admission-{market_mode}",
            economic_protocol=d2m_protocol,
            d2l_receipt=d2l_receipt,
            d2j_protocol=d2j_protocol,
            source_request=lineage.winner_output.request,
            training_bundle=lineage.training_bundle,
            train_features=lineage.train_features,
            train_labels=lineage.train_labels,
            economic_features=economic_features,
            now=datetime(2026, 6, 16, 13, tzinfo=UTC),
        )

    return D2PPreparedSource(
        lineage=lineage,
        shared_sqlite_path=shared,
        predictive_material=predictive_material,
        d2j_protocol=d2j_protocol,
        d2l_binding=d2l_binding,
        d2l_receipt=d2l_receipt,
        d2m_protocol=d2m_protocol,
        economic_universe=universe,
        economic_features=economic_features,
        prediction=prediction,
        d2o_attestation=d2o_attestation,
        d2o_receipt=d2o_receipt,
        prediction_precommit=prediction_precommit,
        provenance_admission=provenance_admission,
    )


def run_d2k(source: D2PPreparedSource):
    return SQLiteOSS3FinalHoldoutEvaluationRegistry(source.shared_sqlite_path).evaluate(
        evaluation_id=f"oss3d2p-d2k-{source.d2m_protocol.economic_protocol_id}",
        protocol=source.d2j_protocol,
        source_request=source.lineage.winner_output.request,
        training_bundle=source.lineage.training_bundle,
        train_features=source.lineage.train_features,
        train_labels=source.lineage.train_labels,
        holdout=ProtectedOSS3FinalHoldout(source.predictive_material),
        now=datetime(2026, 6, 17, tzinfo=UTC),
    )


def protected_economic_holdout(source: D2PPreparedSource) -> ProtectedEconomicHoldout:
    return ProtectedEconomicHoldout(source.economic_material)
