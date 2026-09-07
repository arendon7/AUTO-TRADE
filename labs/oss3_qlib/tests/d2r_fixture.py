from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.oss3_factor_matrix_artifact import (
    FactorMatrixArtifact,
    FactorMatrixPartition,
    FactorMatrixRow,
)
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact
from autotrade.research.universe import AlignedMarketUniverse
from labs.oss3_qlib.raw_training_bundle_provenance import (
    CANONICAL_LOOKBACK_BARS,
    RawTrainingMarketSource,
    canonical_oss3d2r_feature_definitions,
    derive_raw_training_bundle,
)


UTC = timezone.utc
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME_SECONDS = 86_400
CAMPAIGN_ID = "oss3d2r-raw-training-campaign-001"
SPLIT_HASH = "2" * 64
RAW_START = datetime(2026, 1, 1, tzinfo=UTC)
TRAIN_BARS = 32


@dataclass(frozen=True, slots=True)
class D2RSource:
    warmup_universe: AlignedMarketUniverse
    training_universe: AlignedMarketUniverse
    raw_source: RawTrainingMarketSource
    train_features: object
    train_labels: object
    training_bundle: TrainingBundleArtifact
    provenance_receipt: object
    development_features: FactorMatrixArtifact


def build_d2r_source() -> D2RSource:
    warmup = _warmup_universe()
    training = _training_universe()
    raw_source = RawTrainingMarketSource.build(
        warmup_universe=warmup,
        training_universe=training,
    )
    features, labels, bundle, receipt = derive_raw_training_bundle(
        raw_source=raw_source,
        campaign_id=CAMPAIGN_ID,
        research_split_hash=SPLIT_HASH,
    )
    development = _development_features(raw_source=raw_source, bundle=bundle)
    return D2RSource(
        warmup_universe=warmup,
        training_universe=training,
        raw_source=raw_source,
        train_features=features,
        train_labels=labels,
        training_bundle=bundle,
        provenance_receipt=receipt,
        development_features=development,
    )


def _instruments() -> dict[str, InstrumentMetadata]:
    return {
        symbol: InstrumentMetadata(
            symbol=symbol,
            venue="SYNTHETIC",
            quote_currency="USDT",
            price_tick=Decimal("0.01"),
            quantity_step=Decimal("0.0001"),
        )
        for symbol in SYMBOLS
    }


def _warmup_universe() -> AlignedMarketUniverse:
    instruments = _instruments()
    step = timedelta(seconds=TIMEFRAME_SECONDS)
    datasets = []
    for symbol_index, symbol in enumerate(SYMBOLS):
        anchor = Decimal("100") + Decimal(symbol_index * 20)
        bars = tuple(
            Bar(
                symbol=symbol,
                started_at=RAW_START - step * (CANONICAL_LOOKBACK_BARS - index),
                timeframe_seconds=TIMEFRAME_SECONDS,
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
                instrument=instruments[symbol],
                bars=bars,
                source="oss3d2r-warmup-v1",
            )
        )
    return AlignedMarketUniverse.from_datasets(
        datasets=tuple(datasets),
        universe_name="oss3d2r-training-universe#warmup",
    )


def _training_universe() -> AlignedMarketUniverse:
    instruments = _instruments()
    step = timedelta(seconds=TIMEFRAME_SECONDS)
    datasets = []
    for symbol_index, symbol in enumerate(SYMBOLS):
        anchor = Decimal("100") + Decimal(symbol_index * 20)
        bars = []
        previous = anchor
        for index in range(TRAIN_BARS):
            # Different deterministic slopes/curvature make both the feature
            # matrix and one-bar labels nonconstant while remaining benign.
            drift = Decimal("0.0015") + Decimal(symbol_index) * Decimal("0.0007")
            cycle = Decimal((index % 5) - 2) * Decimal("0.00035")
            close = previous * (Decimal("1") + drift + cycle)
            bars.append(
                Bar(
                    symbol=symbol,
                    started_at=RAW_START + step * index,
                    timeframe_seconds=TIMEFRAME_SECONDS,
                    open=previous,
                    high=max(previous, close) * Decimal("1.002"),
                    low=min(previous, close) * Decimal("0.998"),
                    close=close,
                    volume=Decimal("1000000") + Decimal(index * 1000 + symbol_index * 100),
                )
            )
            previous = close
        datasets.append(
            MarketDataset(
                instrument=instruments[symbol],
                bars=tuple(bars),
                source="oss3d2r-train-v1",
            )
        )
    return AlignedMarketUniverse.from_datasets(
        datasets=tuple(datasets),
        universe_name="oss3d2r-training-universe#train",
    )


def _development_features(
    *,
    raw_source: RawTrainingMarketSource,
    bundle: TrainingBundleArtifact,
) -> FactorMatrixArtifact:
    start = raw_source.partition_end + timedelta(days=1)
    end = start + timedelta(days=4)
    rows = []
    for day in range(3):
        as_of = start + timedelta(days=day, hours=1)
        for symbol_index, symbol in enumerate(SYMBOLS):
            rows.append(
                FactorMatrixRow(
                    as_of=as_of.isoformat(),
                    available_at=(as_of - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(
                        0.025 + 0.004 * symbol_index + 0.001 * day,
                        0.003 + 0.0004 * symbol_index + 0.0001 * day,
                    ),
                )
            )
    artifact = FactorMatrixArtifact.build(
        campaign_id=CAMPAIGN_ID,
        research_split_hash=SPLIT_HASH,
        partition=FactorMatrixPartition.DEVELOPMENT,
        partition_start=start,
        partition_end=end,
        producer_code_hash="d" * 64,
        source_dataset_hash="e" * 64,
        source_universe_hash=raw_source.universe_identity_hash,
        features=canonical_oss3d2r_feature_definitions(),
        rows=tuple(rows),
    )
    assert artifact.manifest.feature_schema_hash == bundle.manifest.feature_schema_hash
    assert artifact.manifest.source_universe_hash == bundle.manifest.source_universe_hash
    return artifact


def write_d2g_inputs(source: D2RSource, root: Path, request) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "request": root / "request.json",
        "bundle": root / "training-bundle.json",
        "features": root / "train-features.json",
        "labels": root / "train-labels.json",
        "development": root / "development-features.json",
        "prediction": root / "prediction.json",
        "receipt": root / "receipt.json",
        "attestation": root / "attestation.json",
        "runtime": root / "runtime.json",
        "evidence": root / "evidence.json",
    }
    request.write(paths["request"])
    source.training_bundle.write(paths["bundle"])
    source.train_features.write(paths["features"])
    source.train_labels.write(paths["labels"])
    source.development_features.write(paths["development"])
    return paths
