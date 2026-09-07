from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from autotrade.research.market import Bar, MarketDataset
from autotrade.research.universe import AlignedMarketUniverse
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
    OSS3EconomicPredictionProvenanceReceipt,
    run_economic_prediction_provenance,
)
from labs.oss3_qlib.economic_prediction_provenance_admission import (
    OSS3EconomicPredictionProvenanceAdmissionReceipt,
    SQLiteOSS3EconomicPredictionProvenanceAdmissionRegistry,
)
from labs.oss3_qlib.economic_raw_market_feature_provenance import (
    CANONICAL_LOOKBACK_BARS,
    OSS3RawMarketFeatureProvenanceReceipt,
    RawMarketFeatureSource,
    canonical_oss3d2q_factor_definitions,
    derive_economic_feature_artifact_from_raw_market,
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
from labs.oss3_qlib.tests import d2i_fixture, d2k_fixture
from labs.oss3_qlib.tests.d2n_fixture import (
    _economic_prediction,
    _economic_universe,
    build_runtime_bound_d2n_lineage,
)


UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class D2QPreparedSource:
    lineage: object
    shared_sqlite_path: Path
    predictive_material: object
    d2j_protocol: object
    d2l_binding: object
    d2l_receipt: object
    d2m_protocol: object
    warmup_universe: AlignedMarketUniverse
    economic_universe: AlignedMarketUniverse
    raw_source: RawMarketFeatureSource
    economic_features: object
    d2q_receipt: OSS3RawMarketFeatureProvenanceReceipt
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


def build_d2q_pre_d2k_source(
    tmp_path,
    *,
    market_mode: str = "flat",
    admit: bool = True,
) -> D2QPreparedSource:
    if market_mode not in {"favorable", "adverse", "flat"}:
        raise ValueError("unsupported market_mode")

    def canonical_defs():
        return canonical_oss3d2q_factor_definitions(
            source_id="synthetic-bars-v1",
            source_hash="4" * 64,
        )

    # Build the existing D2F->D2I lineage while replacing only the synthetic
    # fixture's factor declarations with exact D2Q formula identities.  The
    # historical row values remain synthetic on purpose: D2Q proves economic
    # raw-bar derivation and TRAIN formula declaration identity, not historical
    # TRAIN-value derivation.
    with patch.object(d2i_fixture, "_feature_defs", canonical_defs):
        lineage = build_runtime_bound_d2n_lineage(tmp_path)

    shared = tmp_path / f"d2q-shared-{market_mode}.sqlite3"
    predictive_material = d2k_fixture.build_final_holdout_material(
        source_request=lineage.winner_output.request,
        train_features=lineage.train_features,
        label_mode="aligned",
    )
    d2j_protocol = SQLiteOSS3FinalHoldoutProtocolRegistry(shared).preregister_and_record(
        protocol_id=f"oss3d2q-predictive-protocol-{market_mode}",
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
    economic_universe = _economic_universe(start=economic_start, market_mode=market_mode)
    placeholder_prediction = _economic_prediction(
        lineage=lineage,
        binding=d2l_binding,
        universe=economic_universe,
    )
    placeholder_material = EconomicHoldoutMaterial(
        material_version=OSS3D2N_MATERIAL_VERSION,
        commitment_id=f"oss3d2q-economic-holdout-{market_mode}",
        universe=economic_universe,
        prediction=placeholder_prediction,
    )
    d2m_protocol = SQLiteOSS3PredictiveEconomicProtocolRegistry(shared).preregister(
        economic_protocol_id=f"oss3d2q-economic-protocol-{market_mode}",
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        economic_holdout_commitment=placeholder_material.commitment,
        now=datetime(2026, 6, 16, tzinfo=UTC),
    )

    warmup_universe = build_warmup_universe(economic_universe)
    raw_source = RawMarketFeatureSource.build(
        warmup_universe=warmup_universe,
        economic_universe=economic_universe,
    )
    economic_features, d2q_receipt = derive_economic_feature_artifact_from_raw_market(
        economic_protocol=d2m_protocol,
        d2l_receipt=d2l_receipt,
        train_features=lineage.train_features,
        raw_source=raw_source,
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

    # Install D2N + D2P DB-level gates before any prediction precommit/D2K state.
    d2p_registry = SQLiteOSS3EconomicPredictionProvenanceAdmissionRegistry(shared)
    prediction_precommit = SQLiteOSS3EconomicPredictionPrecommitRegistry(shared).preregister(
        precommit_id=f"oss3d2q-prediction-precommit-{market_mode}",
        economic_protocol=d2m_protocol,
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        prediction=prediction,
        now=datetime(2026, 6, 16, 12, tzinfo=UTC),
    )

    provenance_admission = None
    if admit:
        provenance_admission = d2p_registry.admit_by_replay(
            admission_id=f"oss3d2q-d2p-admission-{market_mode}",
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

    return D2QPreparedSource(
        lineage=lineage,
        shared_sqlite_path=shared,
        predictive_material=predictive_material,
        d2j_protocol=d2j_protocol,
        d2l_binding=d2l_binding,
        d2l_receipt=d2l_receipt,
        d2m_protocol=d2m_protocol,
        warmup_universe=warmup_universe,
        economic_universe=economic_universe,
        raw_source=raw_source,
        economic_features=economic_features,
        d2q_receipt=d2q_receipt,
        prediction=prediction,
        d2o_attestation=d2o_attestation,
        d2o_receipt=d2o_receipt,
        prediction_precommit=prediction_precommit,
        provenance_admission=provenance_admission,
    )


def build_warmup_universe(economic_universe: AlignedMarketUniverse) -> AlignedMarketUniverse:
    start = economic_universe.datasets[0].bars[0].started_at
    step = timedelta(seconds=economic_universe.timeframe_seconds)
    datasets = []
    for economic_dataset in economic_universe.datasets:
        anchor = economic_dataset.bars[0].open
        bars = tuple(
            Bar(
                symbol=economic_dataset.instrument.symbol,
                started_at=start - step * (CANONICAL_LOOKBACK_BARS - index),
                timeframe_seconds=economic_universe.timeframe_seconds,
                open=anchor,
                high=anchor * Decimal("1.001"),
                low=anchor * Decimal("0.999"),
                close=anchor,
                volume=Decimal("1000000"),
            )
            for index in range(CANONICAL_LOOKBACK_BARS)
        )
        datasets.append(
            MarketDataset(
                instrument=economic_dataset.instrument,
                bars=bars,
                source="oss3d2q-canonical-warmup-synthetic-v1",
            )
        )
    return AlignedMarketUniverse.from_datasets(
        datasets=tuple(datasets),
        universe_name="oss3d2q-canonical-warmup-v1",
    )


def run_d2k(source: D2QPreparedSource):
    return SQLiteOSS3FinalHoldoutEvaluationRegistry(source.shared_sqlite_path).evaluate(
        evaluation_id=f"oss3d2q-d2k-{source.d2m_protocol.economic_protocol_id}",
        protocol=source.d2j_protocol,
        source_request=source.lineage.winner_output.request,
        training_bundle=source.lineage.training_bundle,
        train_features=source.lineage.train_features,
        train_labels=source.lineage.train_labels,
        holdout=ProtectedOSS3FinalHoldout(source.predictive_material),
        now=datetime(2026, 6, 17, tzinfo=UTC),
    )


def protected_economic_holdout(source: D2QPreparedSource) -> ProtectedEconomicHoldout:
    return ProtectedEconomicHoldout(source.economic_material)
