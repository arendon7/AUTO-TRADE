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
from labs.oss3_qlib.tests.d2r_fixture import (
    CAMPAIGN_ID,
    SPLIT_HASH,
    TIMEFRAME_SECONDS,
    build_d2r_source,
)


UTC = timezone.utc
DEVELOPMENT_BARS = 9
D2S_PREREG_ID = "oss3d2s-raw-development-prereg-001"
D2S_TOURNAMENT_CAMPAIGN = "oss3d2s-development-tournament-campaign-001"
D2S_TOURNAMENT_ID = "oss3d2s-development-tournament-001"


@dataclass(frozen=True, slots=True)
class D2SSource:
    train_source: object
    warmup_universe: AlignedMarketUniverse
    development_universe: AlignedMarketUniverse
    raw_source: RawDevelopmentMarketSource
    development_features: object
    d2f_plan: object
    d2f_request_set: object


def build_d2s_source() -> D2SSource:
    train = build_d2r_source()
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


def _development_warmup(train) -> AlignedMarketUniverse:
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


def _development_universe(train) -> AlignedMarketUniverse:
    step = timedelta(seconds=TIMEFRAME_SECONDS)
    start = train.training_universe.datasets[0].bars[-1].ended_at
    datasets = []
    for symbol_index, train_dataset in enumerate(train.training_universe.datasets):
        symbol = train_dataset.instrument.symbol
        previous = train_dataset.bars[-1].close
        bars = []
        for index in range(DEVELOPMENT_BARS):
            # Deterministic but non-collinear cross-sectional dynamics.  The
            # terminal bar is a target endpoint only, mirroring D2R semantics.
            base = Decimal("0.0007") + Decimal(symbol_index + 1) * Decimal("0.00065")
            wave = Decimal(((index + 2 * symbol_index) % 5) - 2) * Decimal("0.00055")
            interaction = Decimal(index * (symbol_index + 1)) * Decimal("0.000025")
            close = previous * (Decimal("1") + base + wave + interaction)
            bars.append(
                Bar(
                    symbol=symbol,
                    started_at=start + step * index,
                    timeframe_seconds=TIMEFRAME_SECONDS,
                    open=previous,
                    high=max(previous, close) * Decimal("1.002"),
                    low=min(previous, close) * Decimal("0.998"),
                    close=close,
                    volume=Decimal("1200000") + Decimal(index * 1700 + symbol_index * 300),
                )
            )
            previous = close
        datasets.append(
            MarketDataset(
                instrument=train_dataset.instrument,
                bars=tuple(bars),
                source="oss3d2s-development-v1",
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
