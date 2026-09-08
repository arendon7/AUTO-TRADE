from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from autotrade.research.market import Bar, MarketDataset
from autotrade.research.oss3_concrete_model_family import build_concrete_model_request_set
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact
from autotrade.research.trials import SQLiteTrialLedger
from autotrade.research.universe import AlignedMarketUniverse
from labs.oss3_qlib.family_environment_attestation import CandidateEnvironmentAttestation
from labs.oss3_qlib.family_evaluation_batch import FrozenCandidateOutput
from labs.oss3_qlib.family_model_contract import family_runner_code_hash
from labs.oss3_qlib.family_runner import run_isolated_qlib_family_candidate
from labs.oss3_qlib.raw_development_provenance import (
    RawDevelopmentMarketSource,
    SQLiteOSS3RawDevelopmentPreregistrationRegistry,
    derive_raw_development_features,
)
from labs.oss3_qlib.raw_training_bundle_provenance import (
    RawTrainingMarketSource,
    derive_raw_training_bundle,
)
from labs.oss3_qlib.tests.d2r_fixture import (
    CAMPAIGN_ID,
    SPLIT_HASH,
    TIMEFRAME_SECONDS,
    build_d2r_source,
)


UTC = timezone.utc
DEVELOPMENT_BARS = 9
STRONG_TRAIN_BARS = 48
D2S_PREREG_ID = "oss3d2s-raw-development-prereg-001"
D2S_TOURNAMENT_CAMPAIGN = "oss3d2s-development-tournament-campaign-001"
D2S_TOURNAMENT_ID = "oss3d2s-development-tournament-001"


@dataclass(frozen=True, slots=True)
class D2STrainSource:
    warmup_universe: AlignedMarketUniverse
    training_universe: AlignedMarketUniverse
    raw_source: RawTrainingMarketSource
    train_features: object
    train_labels: object
    training_bundle: object
    provenance_receipt: object


@dataclass(frozen=True, slots=True)
class D2SSource:
    train_source: D2STrainSource
    warmup_universe: AlignedMarketUniverse
    development_universe: AlignedMarketUniverse
    raw_source: RawDevelopmentMarketSource
    development_features: object
    d2f_plan: object
    d2f_request_set: object


def build_d2s_source() -> D2SSource:
    train = _strong_raw_derived_training_source()
    warmup = _development_warmup(train)
    development = _development_universe(train)
    raw = RawDevelopmentMarketSource.build(
        warmup_universe=warmup,
        development_universe=development,
    )
    features = derive_raw_development_features(
        raw_source=raw,
        campaign_id=CAMPAIGN_ID,
        research_split_hash=SPLIT_HASH,
    )
    assert features.manifest.feature_schema_hash == train.training_bundle.manifest.feature_schema_hash
    assert features.manifest.source_universe_hash == train.training_bundle.manifest.source_universe_hash
    plan, requests = build_concrete_model_request_set(
        training_bundle=train.training_bundle,
        development_features=features,
        shared_runner_code_hash=family_runner_code_hash(),
    )
    return D2SSource(
        train_source=train,
        warmup_universe=warmup,
        development_universe=development,
        raw_source=raw,
        development_features=features,
        d2f_plan=plan,
        d2f_request_set=requests,
    )


def run_real_d2g_outputs(source: D2SSource, root: Path) -> tuple[FrozenCandidateOutput, ...]:
    outputs = []
    for binding in source.d2f_request_set.bindings:
        candidate_root = root / binding.candidate_id
        candidate_root.mkdir(parents=True, exist_ok=True)
        paths = _write_inputs(source, candidate_root, binding.request)
        run_evidence = run_isolated_qlib_family_candidate(
            request_path=paths["request"],
            training_bundle_path=paths["bundle"],
            train_features_path=paths["train_features"],
            train_labels_path=paths["train_labels"],
            development_features_path=paths["development_features"],
            prediction_output_path=paths["prediction"],
            receipt_output_path=paths["receipt"],
            environment_attestation_output_path=paths["attestation"],
            runtime_identity_output_path=paths["runtime"],
            run_evidence_output_path=paths["evidence"],
        )
        prediction = QlibPredictionArtifact.read(paths["prediction"])
        receipt = binding.request.bind_prediction(
            prediction=prediction,
            training_bundle=source.train_source.training_bundle,
            development_features=source.development_features,
        )
        attestation = CandidateEnvironmentAttestation.read(paths["attestation"])
        outputs.append(
            FrozenCandidateOutput(
                candidate_id=binding.candidate_id,
                request=binding.request,
                prediction=prediction,
                receipt=receipt,
                attestation=attestation,
                run_evidence=run_evidence,
            )
        )
    return tuple(outputs)


def build_registries(root: Path):
    return (
        SQLiteOSS3RawDevelopmentPreregistrationRegistry(root / "d2s.sqlite3"),
        SQLiteTrialLedger(root / "d2h-trials.sqlite3"),
    )


def _strong_raw_derived_training_source() -> D2STrainSource:
    """Build a D2R-valid raw TRAIN set with enough signal for Lasso alpha=0.01.

    D2R's own certified fixture intentionally uses very small benign returns.
    That is sufficient to prove raw-bundle plumbing, but the strongest frozen
    Lasso candidate can legitimately collapse to an intercept-only model on
    those magnitudes.  D2S needs all six real candidates to reach D2D so its
    end-to-end sequencing can be tested.  We therefore construct a separate
    raw OHLCV fixture with the *same D2R formulas and governance*, but stronger
    deterministic cross-sectional momentum/forward-return signal.  No model,
    evaluator, alpha or formula is changed.
    """
    base = build_d2r_source()
    warmup = base.warmup_universe
    step = timedelta(seconds=TIMEFRAME_SECONDS)
    start = warmup.datasets[0].bars[-1].ended_at
    datasets = []
    # Persistent but non-identical trends produce large enough covariance for
    # both Lasso penalties while small deterministic waves avoid perfect
    # collinearity and constant target series.
    trend_by_symbol = (
        Decimal("0.045"),
        Decimal("0.012"),
        Decimal("-0.022"),
    )
    for symbol_index, warmup_dataset in enumerate(warmup.datasets):
        symbol = warmup_dataset.instrument.symbol
        previous = warmup_dataset.bars[-1].close
        bars = []
        for index in range(STRONG_TRAIN_BARS):
            wave = Decimal(((index + symbol_index) % 7) - 3) * Decimal("0.0025")
            interaction = Decimal((index % 5) * (symbol_index + 1)) * Decimal("0.00045")
            one_bar_return = trend_by_symbol[symbol_index] + wave + interaction
            close = previous * (Decimal("1") + one_bar_return)
            bars.append(
                Bar(
                    symbol=symbol,
                    started_at=start + step * index,
                    timeframe_seconds=TIMEFRAME_SECONDS,
                    open=previous,
                    high=max(previous, close) * Decimal("1.003"),
                    low=min(previous, close) * Decimal("0.997"),
                    close=close,
                    volume=Decimal("1500000") + Decimal(index * 2500 + symbol_index * 500),
                )
            )
            previous = close
        datasets.append(
            MarketDataset(
                instrument=warmup_dataset.instrument,
                bars=tuple(bars),
                source="oss3d2s-strong-train-v1",
            )
        )
    training = AlignedMarketUniverse.from_datasets(
        datasets=tuple(datasets),
        universe_name="oss3d2s-training-universe#strong-train",
    )
    raw = RawTrainingMarketSource.build(
        warmup_universe=warmup,
        training_universe=training,
    )
    features, labels, bundle, receipt = derive_raw_training_bundle(
        raw_source=raw,
        campaign_id=CAMPAIGN_ID,
        research_split_hash=SPLIT_HASH,
    )
    return D2STrainSource(
        warmup_universe=warmup,
        training_universe=training,
        raw_source=raw,
        train_features=features,
        train_labels=labels,
        training_bundle=bundle,
        provenance_receipt=receipt,
    )


def _development_warmup(train: D2STrainSource) -> AlignedMarketUniverse:
    datasets = []
    for dataset in train.training_universe.datasets:
        datasets.append(
            MarketDataset(
                instrument=dataset.instrument,
                bars=dataset.bars[-20:],
                source="oss3d2s-development-warmup-v1",
            )
        )
    return AlignedMarketUniverse.from_datasets(
        datasets=tuple(datasets),
        universe_name="oss3d2s-development-universe#warmup",
    )


def _development_universe(train: D2STrainSource) -> AlignedMarketUniverse:
    step = timedelta(seconds=TIMEFRAME_SECONDS)
    start = train.training_universe.datasets[0].bars[-1].ended_at
    datasets = []
    trend_by_symbol = (
        Decimal("0.038"),
        Decimal("0.010"),
        Decimal("-0.018"),
    )
    for symbol_index, train_dataset in enumerate(train.training_universe.datasets):
        symbol = train_dataset.instrument.symbol
        previous = train_dataset.bars[-1].close
        bars = []
        for index in range(DEVELOPMENT_BARS):
            # Preserve the same broad predictive relation as TRAIN while
            # varying ordering over time enough to exercise rank-IC metrics.
            wave = Decimal(((index + 2 * symbol_index) % 5) - 2) * Decimal("0.004")
            interaction = Decimal(index * (symbol_index + 1)) * Decimal("0.00035")
            one_bar_return = trend_by_symbol[symbol_index] + wave + interaction
            close = previous * (Decimal("1") + one_bar_return)
            bars.append(
                Bar(
                    symbol=symbol,
                    started_at=start + step * index,
                    timeframe_seconds=TIMEFRAME_SECONDS,
                    open=previous,
                    high=max(previous, close) * Decimal("1.003"),
                    low=min(previous, close) * Decimal("0.997"),
                    close=close,
                    volume=Decimal("1700000") + Decimal(index * 3100 + symbol_index * 700),
                )
            )
            previous = close
        datasets.append(
            MarketDataset(
                instrument=train_dataset.instrument,
                bars=tuple(bars),
                source="oss3d2s-development-v2-strong-signal",
            )
        )
    return AlignedMarketUniverse.from_datasets(
        datasets=tuple(datasets),
        universe_name="oss3d2s-development-universe#development",
    )


def _write_inputs(source: D2SSource, root: Path, request) -> dict[str, Path]:
    paths = {
        "request": root / "request.json",
        "bundle": root / "training-bundle.json",
        "train_features": root / "train-features.json",
        "train_labels": root / "train-labels.json",
        "development_features": root / "development-features.json",
        "prediction": root / "prediction.json",
        "receipt": root / "receipt.json",
        "attestation": root / "attestation.json",
        "runtime": root / "runtime.json",
        "evidence": root / "evidence.json",
    }
    request.write(paths["request"])
    source.train_source.training_bundle.write(paths["bundle"])
    source.train_source.train_features.write(paths["train_features"])
    source.train_source.train_labels.write(paths["train_labels"])
    source.development_features.write(paths["development_features"])
    return paths
