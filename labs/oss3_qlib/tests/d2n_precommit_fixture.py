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
from labs.oss3_qlib.final_holdout_evaluator import (
    ProtectedOSS3FinalHoldout,
    SQLiteOSS3FinalHoldoutEvaluationRegistry,
)
from labs.oss3_qlib.final_holdout_protocol import SQLiteOSS3FinalHoldoutProtocolRegistry
from labs.oss3_qlib.predictive_economic_protocol import (
    SQLiteOSS3PredictiveEconomicProtocolRegistry,
)
from labs.oss3_qlib.predictive_strategy_contract import (
    SQLiteOSS3PredictiveStrategyRegistry,
    build_predictive_strategy_binding,
)
from labs.oss3_qlib.tests import d2k_fixture
from labs.oss3_qlib.tests.d2n_fixture import (
    RuntimeBoundD2NLineage,
    _economic_prediction,
    _economic_universe,
    build_runtime_bound_d2n_lineage,
)


UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class HardenedD2NSource:
    lineage: RuntimeBoundD2NLineage
    shared_sqlite_path: Path
    d2j_protocol: object
    d2l_binding: object
    d2l_receipt: object
    d2m_protocol: object
    prediction_precommit: OSS3EconomicPredictionPrecommitReceipt
    d2k_receipt: object
    economic_material: EconomicHoldoutMaterial
    economic_holdout: ProtectedEconomicHoldout


def build_hardened_d2n_source(
    tmp_path,
    *,
    market_mode: str = "favorable",
) -> HardenedD2NSource:
    """Build D2M -> prediction precommit -> D2K PASS -> D2N-ready lineage."""
    if market_mode not in {"favorable", "adverse", "flat"}:
        raise ValueError("unsupported market_mode")

    lineage = build_runtime_bound_d2n_lineage(tmp_path)
    shared = tmp_path / f"d2n-precommit-shared-{market_mode}.sqlite3"

    predictive_material = d2k_fixture.build_final_holdout_material(
        source_request=lineage.winner_output.request,
        train_features=lineage.train_features,
        label_mode="aligned",
    )
    d2j_registry = SQLiteOSS3FinalHoldoutProtocolRegistry(shared)
    d2j_protocol = d2j_registry.preregister_and_record(
        protocol_id=f"oss3d2n-precommit-predictive-{market_mode}",
        seal=lineage.winner_seal,
        preregistration=lineage.preregistration,
        batch_evidence=lineage.batch_evidence,
        holdout_commitment=predictive_material.commitment,
    )

    d2l_binding = build_predictive_strategy_binding(
        protocol=d2j_protocol,
        winner_output=lineage.winner_output,
    )
    d2l_registry = SQLiteOSS3PredictiveStrategyRegistry(shared)
    d2l_receipt = d2l_registry.preregister(
        protocol=d2j_protocol,
        binding=d2l_binding,
        now=datetime(2026, 6, 15, tzinfo=UTC),
    )

    economic_start = (
        datetime.fromisoformat(d2j_protocol.holdout_commitment.partition_end)
        + timedelta(days=1)
    )
    universe = _economic_universe(start=economic_start, market_mode=market_mode)
    prediction = _economic_prediction(
        lineage=lineage,
        binding=d2l_binding,
        universe=universe,
    )
    economic_material = EconomicHoldoutMaterial(
        material_version=OSS3D2N_MATERIAL_VERSION,
        commitment_id=f"oss3d2n-precommit-economic-holdout-{market_mode}",
        universe=universe,
        prediction=prediction,
    )

    d2m_registry = SQLiteOSS3PredictiveEconomicProtocolRegistry(shared)
    d2m_protocol = d2m_registry.preregister(
        economic_protocol_id=f"oss3d2n-precommit-economic-{market_mode}",
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        economic_holdout_commitment=economic_material.commitment,
        now=datetime(2026, 6, 16, tzinfo=UTC),
    )

    precommit_registry = SQLiteOSS3EconomicPredictionPrecommitRegistry(shared)
    prediction_precommit = precommit_registry.preregister(
        precommit_id=f"oss3d2n-prediction-precommit-{market_mode}",
        economic_protocol=d2m_protocol,
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        prediction=prediction,
        now=datetime(2026, 6, 16, 12, tzinfo=UTC),
    )

    d2k_registry = SQLiteOSS3FinalHoldoutEvaluationRegistry(shared)
    d2k_receipt = d2k_registry.evaluate(
        evaluation_id=f"oss3d2n-precommit-d2k-{market_mode}",
        protocol=d2j_protocol,
        source_request=lineage.winner_output.request,
        training_bundle=lineage.training_bundle,
        train_features=lineage.train_features,
        train_labels=lineage.train_labels,
        holdout=ProtectedOSS3FinalHoldout(predictive_material),
        now=datetime(2026, 6, 17, tzinfo=UTC),
    )

    return HardenedD2NSource(
        lineage=lineage,
        shared_sqlite_path=shared,
        d2j_protocol=d2j_protocol,
        d2l_binding=d2l_binding,
        d2l_receipt=d2l_receipt,
        d2m_protocol=d2m_protocol,
        prediction_precommit=prediction_precommit,
        d2k_receipt=d2k_receipt,
        economic_material=economic_material,
        economic_holdout=ProtectedEconomicHoldout(economic_material),
    )
