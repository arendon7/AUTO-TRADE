from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from autotrade.research.oss3_concrete_model_family import MODEL_FAMILY, QLIB_VERSION, build_concrete_model_request_set
from autotrade.research.oss3_factor_matrix_artifact import (
    FactorDefinition,
    FactorMatrixArtifact,
    FactorMatrixPartition,
    FactorMatrixRow,
)
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from autotrade.research.oss3_supervised_label_artifact import (
    LabelDefinition,
    LabelPartition,
    SupervisedLabelArtifact,
    SupervisedLabelRow,
)
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact
from autotrade.research.trials import SQLiteTrialLedger
from labs.oss3_qlib.environment_attestation import InstalledDistribution
from labs.oss3_qlib.family_environment_attestation import CandidateEnvironmentAttestation
from labs.oss3_qlib.family_evaluation_batch import (
    OSS3D2G_RUN_EVIDENCE_VERSION,
    FrozenCandidateOutput,
    evaluate_preregistered_family,
    prepare_family_evaluation_preregistration,
    preregister_family_evaluation,
)
from labs.oss3_qlib.family_model_contract import family_runner_code_hash


UTC = timezone.utc
BASE = datetime(2026, 5, 1, tzinfo=UTC)
TRAIN_END = BASE + timedelta(days=7)
DEV_START = TRAIN_END
DEV_END = DEV_START + timedelta(days=4)
CAMPAIGN = "oss3d2i-source-campaign-001"
SPLIT = "1" * 64
UNIVERSE = "2" * 64
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@dataclass(frozen=True, slots=True)
class D2IRuntimeFreeRunEvidence:
    evidence_version: str
    candidate_id: str
    model_config_hash: str
    shared_runner_code_hash: str
    request_hash: str
    prediction_artifact_hash: str
    prediction_receipt_hash: str
    environment_attestation_hash: str
    runtime_environment_hash: str
    development_labels_loaded: bool = False
    final_holdout_loaded: bool = False
    broker_credentials_present: bool = False
    network_allowed: bool = False
    adaptive_search: bool = False
    hyperparameter_optimization: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        return sha256(raw).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_version": self.evidence_version,
            "candidate_id": self.candidate_id,
            "model_config_hash": self.model_config_hash,
            "shared_runner_code_hash": self.shared_runner_code_hash,
            "request_hash": self.request_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "prediction_receipt_hash": self.prediction_receipt_hash,
            "environment_attestation_hash": self.environment_attestation_hash,
            "runtime_environment_hash": self.runtime_environment_hash,
            "development_labels_loaded": self.development_labels_loaded,
            "final_holdout_loaded": self.final_holdout_loaded,
            "broker_credentials_present": self.broker_credentials_present,
            "network_allowed": self.network_allowed,
            "adaptive_search": self.adaptive_search,
            "hyperparameter_optimization": self.hyperparameter_optimization,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


def build_completed_d2h_evidence(tmp_path):
    train_features = _train_features()
    train_labels = _train_labels(train_features)
    bundle = TrainingBundleArtifact.build(features=train_features, labels=train_labels)
    development_features = _development_features()
    development_labels = _development_labels(development_features)
    d2f_plan, request_set = build_concrete_model_request_set(
        training_bundle=bundle,
        development_features=development_features,
        shared_runner_code_hash=family_runner_code_hash(),
    )
    outputs = tuple(
        _candidate_output(
            index=index,
            binding=binding,
            bundle=bundle,
            development_features=development_features,
        )
        for index, binding in enumerate(request_set.bindings)
    )
    preregistration = prepare_family_evaluation_preregistration(
        d2f_plan=d2f_plan,
        d2f_request_set=request_set,
        outputs=outputs,
        development_labels=development_labels,
        tournament_campaign_id="oss3d2i-tournament-campaign-001",
        tournament_id="oss3d2i-tournament-001",
    )
    ledger = SQLiteTrialLedger(tmp_path / "d2i.sqlite3")
    start = datetime(2026, 6, 1, tzinfo=UTC)
    preregister_family_evaluation(ledger, preregistration, now=start)
    evidence = evaluate_preregistered_family(
        ledger,
        preregistration,
        outputs=outputs,
        development_labels=development_labels,
        now=start + timedelta(minutes=1),
    )
    return preregistration, evidence


def _feature_defs():
    return (
        FactorDefinition(
            name="momentum_20",
            dtype="float64",
            role="FEATURE",
            formula_hash="3" * 64,
            source_id="synthetic-bars-v1",
            source_hash="4" * 64,
            lookback_bars=20,
        ),
        FactorDefinition(
            name="volatility_20",
            dtype="float64",
            role="FEATURE",
            formula_hash="5" * 64,
            source_id="synthetic-bars-v1",
            source_hash="6" * 64,
            lookback_bars=20,
        ),
    )


def _label_def():
    return LabelDefinition(
        name="forward_return",
        dtype="float64",
        role="LABEL",
        formula_hash="7" * 64,
        source_id="synthetic-bars-v1",
        source_hash="8" * 64,
    )


def _train_features():
    rows = []
    for day in range(5):
        timestamp = BASE + timedelta(days=day + 1)
        for idx, symbol in enumerate(SYMBOLS, start=1):
            rows.append(
                FactorMatrixRow(
                    as_of=timestamp.isoformat(),
                    available_at=(timestamp - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(1.0 + 0.4 * day + 0.2 * idx, 0.7 + 0.03 * idx - 0.02 * day),
                )
            )
    return FactorMatrixArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=FactorMatrixPartition.TRAIN,
        partition_start=BASE,
        partition_end=TRAIN_END,
        producer_code_hash="9" * 64,
        source_dataset_hash="a" * 64,
        source_universe_hash=UNIVERSE,
        features=_feature_defs(),
        rows=tuple(rows),
    )


def _train_labels(features):
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=row.as_of,
            horizon_end=(datetime.fromisoformat(row.as_of) + timedelta(hours=1)).isoformat(),
            available_at=(datetime.fromisoformat(row.as_of) + timedelta(hours=1, minutes=1)).isoformat(),
            symbol=row.symbol,
            value=0.02 * float(row.values[0]) - 0.01 * float(row.values[1]),
        )
        for row in features.rows
    )
    return SupervisedLabelArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=LabelPartition.TRAIN,
        partition_start=BASE,
        partition_end=TRAIN_END,
        producer_code_hash="b" * 64,
        source_dataset_hash="c" * 64,
        source_universe_hash=UNIVERSE,
        label=_label_def(),
        rows=rows,
    )


def _development_features():
    rows = []
    for day in range(3):
        timestamp = DEV_START + timedelta(days=day, hours=2)
        for idx, symbol in enumerate(SYMBOLS, start=1):
            rows.append(
                FactorMatrixRow(
                    as_of=timestamp.isoformat(),
                    available_at=(timestamp - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(5.0 + 0.2 * idx + 0.1 * day, 0.4 + 0.02 * idx + 0.01 * day),
                )
            )
    return FactorMatrixArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=FactorMatrixPartition.DEVELOPMENT,
        partition_start=DEV_START,
        partition_end=DEV_END,
        producer_code_hash="9" * 64,
        source_dataset_hash="d" * 64,
        source_universe_hash=UNIVERSE,
        features=_feature_defs(),
        rows=tuple(rows),
    )


def _development_labels(features):
    target = {"BTCUSDT": 0.01, "ETHUSDT": 0.02, "SOLUSDT": 0.03}
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=row.as_of,
            horizon_end=(datetime.fromisoformat(row.as_of) + timedelta(hours=1)).isoformat(),
            available_at=(datetime.fromisoformat(row.as_of) + timedelta(hours=1, minutes=1)).isoformat(),
            symbol=row.symbol,
            value=target[row.symbol],
        )
        for row in features.rows
    )
    return SupervisedLabelArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=LabelPartition.DEVELOPMENT,
        partition_start=DEV_START,
        partition_end=DEV_END,
        producer_code_hash="b" * 64,
        source_dataset_hash="e" * 64,
        source_universe_hash=UNIVERSE,
        label=_label_def(),
        rows=rows,
    )


def _candidate_output(*, index, binding, bundle, development_features):
    permutations = (
        (1.0, 2.0, 3.0),
        (1.0, 3.0, 2.0),
        (2.0, 1.0, 3.0),
        (3.0, 2.0, 1.0),
        (2.0, 3.0, 1.0),
        (3.0, 1.0, 2.0),
    )
    scores = dict(zip(SYMBOLS, permutations[index], strict=True))
    rows = tuple(
        QlibPredictionRow(
            timestamp=row.as_of,
            symbol=row.symbol,
            score=scores[row.symbol] + 0.01 * (datetime.fromisoformat(row.as_of) - DEV_START).days,
        )
        for row in development_features.rows
    )
    manifest = binding.request.manifest
    prediction = QlibPredictionArtifact.build(
        qlib_version=QLIB_VERSION,
        model_family=MODEL_FAMILY,
        model_config_hash=binding.model_config_hash,
        training_dataset_hash=bundle.artifact_hash,
        feature_schema_hash=manifest.feature_schema_hash,
        producer_code_hash=family_runner_code_hash(),
        train_start=datetime.fromisoformat(manifest.train_start),
        train_end=datetime.fromisoformat(manifest.train_end),
        inference_start=datetime.fromisoformat(manifest.inference_start),
        inference_end=datetime.fromisoformat(manifest.inference_end),
        rows=rows,
    )
    receipt = binding.request.bind_prediction(
        prediction=prediction,
        training_bundle=bundle,
        development_features=development_features,
    )
    attestation = CandidateEnvironmentAttestation.build(
        model_config_hash=binding.model_config_hash,
        distributions=(
            InstalledDistribution(name="numpy", version="2.0.0"),
            InstalledDistribution(name="pyqlib", version=QLIB_VERSION),
        ),
        python_implementation="cpython",
        python_version="3.12.10",
        platform_system="linux",
        platform_machine="x86_64",
        libc_name="glibc",
        libc_version="2.39",
    )
    run_evidence = D2IRuntimeFreeRunEvidence(
        evidence_version=OSS3D2G_RUN_EVIDENCE_VERSION,
        candidate_id=binding.candidate_id,
        model_config_hash=binding.model_config_hash,
        shared_runner_code_hash=family_runner_code_hash(),
        request_hash=binding.request.request_hash,
        prediction_artifact_hash=prediction.artifact_hash,
        prediction_receipt_hash=receipt.fingerprint,
        environment_attestation_hash=attestation.artifact_hash,
        runtime_environment_hash=attestation.runtime_environment.fingerprint,
    )
    return FrozenCandidateOutput(
        candidate_id=binding.candidate_id,
        request=binding.request,
        prediction=prediction,
        receipt=receipt,
        attestation=attestation,
        run_evidence=run_evidence,
    )
