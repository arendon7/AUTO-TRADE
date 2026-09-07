from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from autotrade.research.universe import AlignedMarketUniverse
from labs.oss3_qlib.economic_holdout_evaluator import (
    OSS3D2N_MATERIAL_VERSION,
    EconomicHoldoutMaterial,
    ProtectedEconomicHoldout,
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
from labs.oss3_qlib.tests.d2l_fixture import D2LSource, build_d2l_source
from labs.oss3_qlib.tests import d2k_fixture


UTC = timezone.utc
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME_SECONDS = 86_400


@dataclass(frozen=True, slots=True)
class D2NSource:
    lineage: D2LSource
    shared_sqlite_path: Path
    d2j_protocol: object
    d2l_binding: object
    d2l_receipt: object
    d2m_protocol: object
    d2k_receipt: object
    economic_material: EconomicHoldoutMaterial
    economic_holdout: ProtectedEconomicHoldout


def build_d2n_source(tmp_path, *, market_mode: str = "favorable") -> D2NSource:
    if market_mode not in {"favorable", "adverse", "flat"}:
        raise ValueError("unsupported market_mode")

    lineage = build_d2l_source(tmp_path)
    shared = tmp_path / f"d2n-shared-{market_mode}.sqlite3"

    predictive_material = d2k_fixture.build_final_holdout_material(
        source_request=lineage.winner_output.request,
        train_features=lineage.train_features,
        label_mode="aligned",
    )
    d2j_registry = SQLiteOSS3FinalHoldoutProtocolRegistry(shared)
    d2j_protocol = d2j_registry.preregister_and_record(
        protocol_id=f"oss3d2n-predictive-protocol-{market_mode}",
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

    economic_start = datetime.fromisoformat(
        d2j_protocol.holdout_commitment.partition_end
    ) + timedelta(days=1)
    universe = _economic_universe(start=economic_start, market_mode=market_mode)
    prediction = _economic_prediction(
        lineage=lineage,
        binding=d2l_binding,
        universe=universe,
    )
    economic_material = EconomicHoldoutMaterial(
        material_version=OSS3D2N_MATERIAL_VERSION,
        commitment_id=f"oss3d2n-economic-holdout-{market_mode}",
        universe=universe,
        prediction=prediction,
    )

    d2m_registry = SQLiteOSS3PredictiveEconomicProtocolRegistry(shared)
    d2m_protocol = d2m_registry.preregister(
        economic_protocol_id=f"oss3d2n-economic-protocol-{market_mode}",
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        economic_holdout_commitment=economic_material.commitment,
        now=datetime(2026, 6, 16, tzinfo=UTC),
    )

    d2k_registry = SQLiteOSS3FinalHoldoutEvaluationRegistry(shared)
    d2k_receipt = d2k_registry.evaluate(
        evaluation_id=f"oss3d2n-predictive-evaluation-{market_mode}",
        protocol=d2j_protocol,
        source_request=lineage.winner_output.request,
        training_bundle=lineage.training_bundle,
        train_features=lineage.train_features,
        train_labels=lineage.train_labels,
        holdout=ProtectedOSS3FinalHoldout(predictive_material),
        now=datetime(2026, 6, 17, tzinfo=UTC),
    )

    return D2NSource(
        lineage=lineage,
        shared_sqlite_path=shared,
        d2j_protocol=d2j_protocol,
        d2l_binding=d2l_binding,
        d2l_receipt=d2l_receipt,
        d2m_protocol=d2m_protocol,
        d2k_receipt=d2k_receipt,
        economic_material=economic_material,
        economic_holdout=ProtectedEconomicHoldout(economic_material),
    )


def _economic_universe(*, start: datetime, market_mode: str) -> AlignedMarketUniverse:
    selected_for_execution = {
        index: SYMBOLS[index % len(SYMBOLS)]
        for index in range(1, 90)
    }
    datasets = []
    for symbol in SYMBOLS:
        price = Decimal("100")
        bars = []
        for index in range(90):
            opened = price
            selected = selected_for_execution.get(index)
            if selected == symbol and market_mode == "favorable":
                closed = opened * Decimal("1.03")
            elif selected == symbol and market_mode == "adverse":
                closed = opened * Decimal("0.97")
            else:
                closed = opened
            high = max(opened, closed) * Decimal("1.001")
            low = min(opened, closed) * Decimal("0.999")
            bars.append(
                Bar(
                    symbol=symbol,
                    started_at=start + timedelta(days=index),
                    timeframe_seconds=TIMEFRAME_SECONDS,
                    open=opened,
                    high=high,
                    low=low,
                    close=closed,
                    volume=Decimal("1000000"),
                )
            )
            price = closed
        datasets.append(
            MarketDataset(
                instrument=InstrumentMetadata(
                    symbol=symbol,
                    venue="SYNTHETIC",
                    quote_currency="USDT",
                    price_tick=Decimal("0.01"),
                    quantity_step=Decimal("0.0001"),
                ),
                bars=tuple(bars),
                source=f"oss3d2n-{market_mode}-synthetic-v1",
            )
        )
    return AlignedMarketUniverse.from_datasets(
        datasets=tuple(datasets),
        universe_name=f"oss3d2n-{market_mode}-universe-v1",
    )


def _economic_prediction(*, lineage, binding, universe) -> QlibPredictionArtifact:
    original = lineage.winner_output.prediction.manifest
    rows = []
    for signal_index, signal_bar in enumerate(universe.datasets[0].bars[:-1]):
        selected = SYMBOLS[(signal_index + 1) % len(SYMBOLS)]
        for symbol in SYMBOLS:
            if symbol == selected:
                score = 3.0
            else:
                # Stable deterministic ordering for the two nonselected assets.
                score = 2.0 if symbol < selected else 1.0
            rows.append(
                QlibPredictionRow(
                    timestamp=signal_bar.ended_at.astimezone(UTC).isoformat(),
                    symbol=symbol,
                    score=score,
                )
            )
    first_signal = universe.datasets[0].bars[0].ended_at
    inference_end = universe.datasets[0].bars[-1].ended_at
    return QlibPredictionArtifact.build(
        qlib_version=binding.qlib_version,
        model_family=binding.model_family,
        model_config_hash=binding.model_config_hash,
        training_dataset_hash=binding.training_dataset_hash,
        feature_schema_hash=binding.feature_schema_hash,
        producer_code_hash=binding.shared_runner_code_hash,
        train_start=datetime.fromisoformat(original.train_start),
        train_end=datetime.fromisoformat(original.train_end),
        inference_start=first_signal,
        inference_end=inference_end,
        rows=tuple(rows),
    )
