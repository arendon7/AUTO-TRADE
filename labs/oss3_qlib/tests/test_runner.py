from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket

import pytest

from autotrade.research.oss3_development_inference import DevelopmentInferenceRequest
from autotrade.research.oss3_factor_matrix_artifact import (
    FactorDefinition,
    FactorMatrixArtifact,
    FactorMatrixPartition,
    FactorMatrixRow,
)
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact
from autotrade.research.oss3_supervised_label_artifact import (
    LabelDefinition,
    LabelPartition,
    SupervisedLabelArtifact,
    SupervisedLabelRow,
)
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact
from labs.oss3_qlib.dataset_adapter import QlibArtifactDatasetAdapter, QlibDatasetAdapterError
from labs.oss3_qlib.model_contract import (
    MODEL_CONFIG,
    MODEL_FAMILY,
    QLIB_VERSION,
    model_config_hash,
    runner_code_hash,
)
from labs.oss3_qlib.network_guard import QlibLabNetworkDenied, deny_network
from labs.oss3_qlib.runner import (
    QlibLabGovernanceError,
    run_isolated_qlib_lab,
)


UTC = timezone.utc
BASE = datetime(2026, 1, 1, tzinfo=UTC)
TRAIN_END = BASE + timedelta(days=4)
DEV_START = TRAIN_END
DEV_END = DEV_START + timedelta(days=3)
CAMPAIGN = "oss3d2b-campaign-001"
SPLIT = "1" * 64
UNIVERSE = "2" * 64
FEATURE_CODE = "3" * 64
LABEL_CODE = "4" * 64


def _definitions():
    return (
        FactorDefinition(
            name="momentum_20",
            dtype="float64",
            role="FEATURE",
            formula_hash="5" * 64,
            source_id="synthetic-bars-v1",
            source_hash="6" * 64,
            lookback_bars=20,
        ),
        FactorDefinition(
            name="volatility_20",
            dtype="float64",
            role="FEATURE",
            formula_hash="7" * 64,
            source_id="synthetic-bars-v1",
            source_hash="8" * 64,
            lookback_bars=20,
        ),
    )


def _train_features():
    identities = (
        (BASE + timedelta(hours=1), "BTCUSDT", (1.0, 0.5)),
        (BASE + timedelta(hours=1), "ETHUSDT", (2.0, 0.4)),
        (BASE + timedelta(days=1), "BTCUSDT", (3.0, 0.3)),
        (BASE + timedelta(days=1), "ETHUSDT", (4.0, 0.2)),
        (BASE + timedelta(days=2), "BTCUSDT", (5.0, 0.1)),
        (BASE + timedelta(days=2), "ETHUSDT", (6.0, 0.05)),
    )
    rows = tuple(
        FactorMatrixRow(
            as_of=timestamp.isoformat(),
            available_at=(timestamp - timedelta(minutes=1)).isoformat(),
            symbol=symbol,
            values=values,
        )
        for timestamp, symbol, values in identities
    )
    return FactorMatrixArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=FactorMatrixPartition.TRAIN,
        partition_start=BASE,
        partition_end=TRAIN_END,
        producer_code_hash=FEATURE_CODE,
        source_dataset_hash="9" * 64,
        source_universe_hash=UNIVERSE,
        features=_definitions(),
        rows=rows,
    )


def _train_labels():
    feature_rows = _train_features().rows
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=row.as_of,
            horizon_end=(datetime.fromisoformat(row.as_of) + timedelta(minutes=30)).isoformat(),
            available_at=(datetime.fromisoformat(row.as_of) + timedelta(minutes=31)).isoformat(),
            symbol=row.symbol,
            value=0.02 * float(index) - 0.01 * float(row.values[1]),
        )
        for index, row in enumerate(feature_rows, start=1)
    )
    return SupervisedLabelArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=LabelPartition.TRAIN,
        partition_start=BASE,
        partition_end=TRAIN_END,
        producer_code_hash=LABEL_CODE,
        source_dataset_hash="a" * 64,
        source_universe_hash=UNIVERSE,
        label=LabelDefinition(
            name="forward_return",
            dtype="float64",
            role="LABEL",
            formula_hash="b" * 64,
            source_id="synthetic-bars-v1",
            source_hash="c" * 64,
        ),
        rows=rows,
    )


def _development_features(*, shift=0.0):
    identities = (
        (DEV_START, "BTCUSDT", (7.0 + shift, 0.15)),
        (DEV_START, "ETHUSDT", (8.0 + shift, 0.20)),
        (DEV_START + timedelta(days=1), "BTCUSDT", (9.0 + shift, 0.25)),
        (DEV_START + timedelta(days=1), "ETHUSDT", (10.0 + shift, 0.30)),
    )
    rows = tuple(
        FactorMatrixRow(
            as_of=timestamp.isoformat(),
            available_at=(timestamp - timedelta(minutes=1)).isoformat(),
            symbol=symbol,
            values=values,
        )
        for timestamp, symbol, values in identities
    )
    return FactorMatrixArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=FactorMatrixPartition.DEVELOPMENT,
        partition_start=DEV_START,
        partition_end=DEV_END,
        producer_code_hash=FEATURE_CODE,
        source_dataset_hash="d" * 64,
        source_universe_hash=UNIVERSE,
        features=_definitions(),
        rows=rows,
    )


def _write_inputs(tmp_path: Path, *, runner_hash: str | None = None):
    train_features = _train_features()
    train_labels = _train_labels()
    bundle = TrainingBundleArtifact.build(features=train_features, labels=train_labels)
    development_features = _development_features()
    request = DevelopmentInferenceRequest.build(
        training_bundle=bundle,
        development_features=development_features,
        model_family=MODEL_FAMILY,
        model_config_hash=model_config_hash(),
        required_qlib_version=QLIB_VERSION,
        expected_runner_code_hash=runner_code_hash() if runner_hash is None else runner_hash,
    )
    paths = {
        "request": tmp_path / "request.json",
        "bundle": tmp_path / "bundle.json",
        "train_features": tmp_path / "train-features.json",
        "train_labels": tmp_path / "train-labels.json",
        "development_features": tmp_path / "development-features.json",
        "prediction": tmp_path / "prediction.json",
        "receipt": tmp_path / "receipt.json",
    }
    request.write(paths["request"])
    bundle.write(paths["bundle"])
    train_features.write(paths["train_features"])
    train_labels.write(paths["train_labels"])
    development_features.write(paths["development_features"])
    return request, bundle, train_features, train_labels, development_features, paths


def _clear_broker_env(monkeypatch):
    prefixes = ("APCA_", "ALPACA_", "IBKR_", "BINANCE_", "BROKER_")
    for key in tuple(os.environ):
        if any(key.startswith(prefix) for prefix in prefixes):
            monkeypatch.delenv(key, raising=False)


def test_frozen_model_contract_is_single_nonadaptive_ridge():
    assert QLIB_VERSION == "0.9.7"
    assert MODEL_FAMILY == "qlib_linear_ridge_v1"
    assert MODEL_CONFIG == {
        "implementation": "qlib.contrib.model.linear.LinearModel",
        "estimator": "ridge",
        "alpha": 1.0,
        "fit_intercept": True,
        "include_valid": False,
        "prediction_segment": "test",
    }
    assert len(model_config_hash()) == 64
    assert len(runner_code_hash()) == 64


def test_dataset_adapter_exposes_only_linear_model_prepare_contract():
    adapter = QlibArtifactDatasetAdapter(
        train_features=_train_features(),
        train_labels=_train_labels(),
        development_features=_development_features(),
    )
    train = adapter.prepare("train", col_set=["feature", "label"], data_key="learn")
    test = adapter.prepare("test", col_set="feature", data_key="infer")
    assert list(train.columns.get_level_values(0).unique()) == ["feature", "label"]
    assert list(test.columns) == ["momentum_20", "volatility_20"]
    assert train.index.names == ["datetime", "instrument"]
    assert test.index.names == ["datetime", "instrument"]
    with pytest.raises(QlibDatasetAdapterError):
        adapter.prepare("valid", col_set="feature", data_key="infer")
    with pytest.raises(QlibDatasetAdapterError):
        adapter.prepare("train", col_set=["feature"], data_key="learn")
    with pytest.raises(QlibDatasetAdapterError):
        adapter.prepare("test", col_set="feature", data_key="learn")


def test_network_guard_blocks_and_restores_socket_entry_points():
    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo
    with deny_network():
        with pytest.raises(QlibLabNetworkDenied):
            socket.getaddrinfo("example.com", 443)
    assert socket.socket.connect is original_connect
    assert socket.getaddrinfo is original_getaddrinfo


def test_real_qlib_runner_emits_exact_oss3a_and_d2a_receipt(tmp_path, monkeypatch):
    _clear_broker_env(monkeypatch)
    request, bundle, _, _, development_features, paths = _write_inputs(tmp_path)
    receipt = run_isolated_qlib_lab(
        request_path=paths["request"],
        training_bundle_path=paths["bundle"],
        train_features_path=paths["train_features"],
        train_labels_path=paths["train_labels"],
        development_features_path=paths["development_features"],
        prediction_output_path=paths["prediction"],
        receipt_output_path=paths["receipt"],
    )
    prediction = QlibPredictionArtifact.read(paths["prediction"])
    assert prediction.manifest.qlib_version == QLIB_VERSION
    assert prediction.manifest.model_family == MODEL_FAMILY
    assert prediction.manifest.model_config_hash == model_config_hash()
    assert prediction.manifest.training_dataset_hash == bundle.artifact_hash
    assert prediction.manifest.producer_code_hash == runner_code_hash()
    assert tuple((row.timestamp, row.symbol) for row in prediction.rows) == tuple(
        (row.as_of, row.symbol) for row in development_features.rows
    )
    assert all(isinstance(row.score, float) for row in prediction.rows)
    assert receipt.request_hash == request.request_hash
    assert receipt.prediction_artifact_hash == prediction.artifact_hash
    assert receipt.execution_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    serialized_receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    assert serialized_receipt["receipt_fingerprint"] == receipt.fingerprint
    assert serialized_receipt["receipt"] == receipt.to_dict()


def test_runner_is_deterministic_for_same_frozen_inputs(tmp_path, monkeypatch):
    _clear_broker_env(monkeypatch)
    request, _, _, _, _, paths = _write_inputs(tmp_path)
    first_receipt = run_isolated_qlib_lab(
        request_path=paths["request"],
        training_bundle_path=paths["bundle"],
        train_features_path=paths["train_features"],
        train_labels_path=paths["train_labels"],
        development_features_path=paths["development_features"],
        prediction_output_path=paths["prediction"],
    )
    first_prediction = QlibPredictionArtifact.read(paths["prediction"])
    second_path = tmp_path / "prediction-2.json"
    second_receipt = run_isolated_qlib_lab(
        request_path=paths["request"],
        training_bundle_path=paths["bundle"],
        train_features_path=paths["train_features"],
        train_labels_path=paths["train_labels"],
        development_features_path=paths["development_features"],
        prediction_output_path=second_path,
    )
    second_prediction = QlibPredictionArtifact.read(second_path)
    assert request.request_hash == DevelopmentInferenceRequest.read(paths["request"]).request_hash
    assert first_prediction.artifact_hash == second_prediction.artifact_hash
    assert first_receipt.fingerprint == second_receipt.fingerprint


def test_runner_rejects_broker_credentials_before_model_execution(tmp_path, monkeypatch):
    _, _, _, _, _, paths = _write_inputs(tmp_path)
    monkeypatch.setenv("BROKER_API_KEY", "must-not-enter-research-lab")
    with pytest.raises(QlibLabGovernanceError, match="credential variables"):
        run_isolated_qlib_lab(
            request_path=paths["request"],
            training_bundle_path=paths["bundle"],
            train_features_path=paths["train_features"],
            train_labels_path=paths["train_labels"],
            development_features_path=paths["development_features"],
            prediction_output_path=paths["prediction"],
        )


def test_runner_rejects_nonpreregistered_runner_hash(tmp_path, monkeypatch):
    _clear_broker_env(monkeypatch)
    _, _, _, _, _, paths = _write_inputs(tmp_path, runner_hash="f" * 64)
    with pytest.raises(RuntimeError, match="expected_runner_code_hash mismatch"):
        run_isolated_qlib_lab(
            request_path=paths["request"],
            training_bundle_path=paths["bundle"],
            train_features_path=paths["train_features"],
            train_labels_path=paths["train_labels"],
            development_features_path=paths["development_features"],
            prediction_output_path=paths["prediction"],
        )


def test_runner_never_accepts_development_label_path_in_cli_contract():
    source = (Path(__file__).resolve().parents[1] / "runner.py").read_text(encoding="utf-8")
    assert "--development-labels" not in source
    assert "development_labels_path" not in source
