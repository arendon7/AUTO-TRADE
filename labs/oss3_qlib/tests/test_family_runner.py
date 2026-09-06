from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from autotrade.research.oss3_concrete_model_family import (
    CANONICAL_CANDIDATES,
    MODEL_FAMILY,
    QLIB_VERSION,
    build_concrete_model_request_set,
)
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
from labs.oss3_qlib.environment_attestation import InstalledDistribution
from labs.oss3_qlib.family_environment_attestation import (
    CandidateEnvironmentAttestation,
    FamilyEnvironmentAttestationIntegrityError,
)
from labs.oss3_qlib.family_model_contract import (
    FamilyModelContractError,
    assert_family_request_contract,
    candidate_from_config_hash,
    family_runner_code_hash,
    public_family_runtime_contract,
)
from labs.oss3_qlib.family_runner import (
    OSS3D2G_RUN_EVIDENCE_VERSION,
    FamilyCandidateRunEvidence,
    QlibFamilyLabGovernanceError,
    QlibFamilyLabIntegrityError,
    run_isolated_qlib_family_candidate,
    verify_family_candidate_outputs,
)


UTC = timezone.utc
BASE = datetime(2026, 2, 1, tzinfo=UTC)
TRAIN_END = BASE + timedelta(days=8)
DEV_START = TRAIN_END
DEV_END = DEV_START + timedelta(days=4)
CAMPAIGN = "oss3d2g-campaign-001"
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
    rows = []
    for day in range(6):
        timestamp = BASE + timedelta(days=day, hours=1)
        for symbol_index, symbol in enumerate(("BTCUSDT", "ETHUSDT"), start=1):
            x1 = 1.0 + day * 0.7 + symbol_index * 0.15
            x2 = 0.8 - day * 0.05 + symbol_index * 0.03
            rows.append(
                FactorMatrixRow(
                    as_of=timestamp.isoformat(),
                    available_at=(timestamp - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(x1, x2),
                )
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
        rows=tuple(rows),
    )


def _train_labels():
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=row.as_of,
            horizon_end=(datetime.fromisoformat(row.as_of) + timedelta(minutes=30)).isoformat(),
            available_at=(datetime.fromisoformat(row.as_of) + timedelta(minutes=31)).isoformat(),
            symbol=row.symbol,
            value=0.04 * float(row.values[0]) - 0.015 * float(row.values[1]) + 0.002,
        )
        for row in _train_features().rows
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


def _development_features():
    rows = []
    for day in range(3):
        timestamp = DEV_START + timedelta(days=day)
        for symbol_index, symbol in enumerate(("BTCUSDT", "ETHUSDT"), start=1):
            rows.append(
                FactorMatrixRow(
                    as_of=timestamp.isoformat(),
                    available_at=(timestamp - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(5.0 + day * 0.4 + symbol_index * 0.1, 0.45 + day * 0.04),
                )
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
        rows=tuple(rows),
    )


def _artifacts():
    train_features = _train_features()
    train_labels = _train_labels()
    bundle = TrainingBundleArtifact.build(features=train_features, labels=train_labels)
    development_features = _development_features()
    plan, request_set = build_concrete_model_request_set(
        training_bundle=bundle,
        development_features=development_features,
        shared_runner_code_hash=family_runner_code_hash(),
    )
    return plan, request_set, bundle, train_features, train_labels, development_features


def _write_common(tmp_path: Path):
    plan, request_set, bundle, train_features, train_labels, development_features = _artifacts()
    paths = {
        "bundle": tmp_path / "bundle.json",
        "train_features": tmp_path / "train-features.json",
        "train_labels": tmp_path / "train-labels.json",
        "development_features": tmp_path / "development-features.json",
    }
    bundle.write(paths["bundle"])
    train_features.write(paths["train_features"])
    train_labels.write(paths["train_labels"])
    development_features.write(paths["development_features"])
    return plan, request_set, bundle, development_features, paths


def _candidate_paths(tmp_path: Path, candidate_id: str):
    root = tmp_path / candidate_id
    root.mkdir(parents=True, exist_ok=True)
    return {
        "request": root / "request.json",
        "prediction": root / "prediction.json",
        "receipt": root / "receipt.json",
        "attestation": root / "environment-attestation.json",
        "runtime": root / "runtime-identity.json",
        "evidence": root / "run-evidence.json",
    }


def _clear_broker_env(monkeypatch):
    prefixes = (
        "APCA_", "ALPACA_", "IBKR_", "BINANCE_", "COINBASE_", "KRAKEN_",
        "BYBIT_", "OKX_", "BITGET_", "KUCOIN_", "BROKER_",
    )
    for key in tuple(os.environ):
        if any(key.startswith(prefix) for prefix in prefixes):
            monkeypatch.delenv(key, raising=False)


def _synthetic_distributions():
    return (
        InstalledDistribution(name="numpy", version="2.0.0"),
        InstalledDistribution(name="pyqlib", version=QLIB_VERSION),
    )


def test_family_runtime_contract_is_exactly_d2f_and_nonadaptive():
    contract = public_family_runtime_contract()
    assert contract["model_family"] == MODEL_FAMILY
    assert contract["qlib_version"] == QLIB_VERSION == "0.9.7"
    assert contract["candidate_count"] == 6
    assert [item["candidate_id"] for item in contract["candidates"]] == [
        candidate.candidate_id for candidate in CANONICAL_CANDIDATES
    ]
    assert contract["adaptive_search"] is False
    assert contract["hyperparameter_optimization"] is False
    assert contract["development_labels_observable"] is False
    assert contract["final_holdout_observable"] is False
    assert contract["capital_authority"] == "NONE"
    assert contract["live_trading"] == "BLOCKED"
    assert len(contract["runner_code_hash"]) == 64


def test_config_hash_resolution_is_exact_and_unknown_hash_fails_closed():
    for candidate in CANONICAL_CANDIDATES:
        assert candidate_from_config_hash(candidate.model_config_hash) == candidate
    with pytest.raises(FamilyModelContractError, match="outside the frozen"):
        candidate_from_config_hash("f" * 64)


def test_all_six_preregistered_candidates_execute_real_qlib_with_one_runtime_identity(
    tmp_path, monkeypatch
):
    _clear_broker_env(monkeypatch)
    _, request_set, bundle, development_features, common = _write_common(tmp_path)
    runtime_hashes = set()
    runner_hashes = set()
    attestation_hashes = set()
    config_hashes = set()

    for binding in request_set.bindings:
        paths = _candidate_paths(tmp_path, binding.candidate_id)
        binding.request.write(paths["request"])
        evidence = run_isolated_qlib_family_candidate(
            request_path=paths["request"],
            training_bundle_path=common["bundle"],
            train_features_path=common["train_features"],
            train_labels_path=common["train_labels"],
            development_features_path=common["development_features"],
            prediction_output_path=paths["prediction"],
            receipt_output_path=paths["receipt"],
            environment_attestation_output_path=paths["attestation"],
            runtime_identity_output_path=paths["runtime"],
            run_evidence_output_path=paths["evidence"],
        )
        prediction = QlibPredictionArtifact.read(paths["prediction"])
        attestation = CandidateEnvironmentAttestation.read(paths["attestation"])
        receipt = binding.request.bind_prediction(
            prediction=prediction,
            training_bundle=bundle,
            development_features=development_features,
        )
        verify_family_candidate_outputs(
            request=binding.request,
            prediction=prediction,
            receipt=receipt,
            attestation=attestation,
            evidence=evidence,
        )
        serialized_receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
        serialized_runtime = json.loads(paths["runtime"].read_text(encoding="utf-8"))
        serialized_evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))

        assert prediction.manifest.qlib_version == QLIB_VERSION
        assert prediction.manifest.model_family == MODEL_FAMILY
        assert prediction.manifest.model_config_hash == binding.model_config_hash
        assert prediction.manifest.producer_code_hash == family_runner_code_hash()
        assert tuple((row.timestamp, row.symbol) for row in prediction.rows) == tuple(
            (row.as_of, row.symbol) for row in development_features.rows
        )
        assert serialized_receipt["receipt_fingerprint"] == receipt.fingerprint
        assert serialized_runtime["runtime_environment_hash"] == attestation.runtime_environment.fingerprint
        assert serialized_evidence["evidence_fingerprint"] == evidence.fingerprint
        assert evidence.candidate_id == binding.candidate_id
        assert evidence.model_config_hash == binding.model_config_hash
        assert evidence.request_hash == binding.request.request_hash
        assert evidence.environment_attestation_hash == attestation.artifact_hash
        assert evidence.development_labels_loaded is False
        assert evidence.final_holdout_loaded is False
        assert evidence.network_allowed is False
        assert evidence.execution_authorized is False
        assert evidence.paper_execution_authorized is False
        assert evidence.capital_authority == "NONE"
        assert evidence.live_trading == "BLOCKED"

        runtime_hashes.add(evidence.runtime_environment_hash)
        runner_hashes.add(evidence.shared_runner_code_hash)
        attestation_hashes.add(evidence.environment_attestation_hash)
        config_hashes.add(evidence.model_config_hash)

    assert len(config_hashes) == 6
    assert len(attestation_hashes) == 6
    assert runtime_hashes and len(runtime_hashes) == 1
    assert runner_hashes == {family_runner_code_hash()}


def test_request_with_unknown_config_is_rejected_before_execution():
    _, _, bundle, _, _, development_features = _artifacts()
    request = DevelopmentInferenceRequest.build(
        training_bundle=bundle,
        development_features=development_features,
        model_family=MODEL_FAMILY,
        model_config_hash="f" * 64,
        required_qlib_version=QLIB_VERSION,
        expected_runner_code_hash=family_runner_code_hash(),
    )
    with pytest.raises(FamilyModelContractError, match="outside the frozen"):
        assert_family_request_contract(request.manifest)


def test_request_with_wrong_shared_runner_hash_is_rejected():
    _, _, bundle, _, _, development_features = _artifacts()
    candidate = CANONICAL_CANDIDATES[0]
    request = DevelopmentInferenceRequest.build(
        training_bundle=bundle,
        development_features=development_features,
        model_family=MODEL_FAMILY,
        model_config_hash=candidate.model_config_hash,
        required_qlib_version=QLIB_VERSION,
        expected_runner_code_hash="e" * 64,
    )
    with pytest.raises(FamilyModelContractError, match="expected_runner_code_hash mismatch"):
        assert_family_request_contract(request.manifest)


def test_candidate_attestation_rejects_stale_or_forged_runner_hash():
    with pytest.raises(FamilyEnvironmentAttestationIntegrityError, match="semantic runtime"):
        CandidateEnvironmentAttestation.build(
            model_config_hash=CANONICAL_CANDIDATES[0].model_config_hash,
            distributions=_synthetic_distributions(),
            python_implementation="cpython",
            python_version="3.12.10",
            platform_system="linux",
            platform_machine="x86_64",
            libc_name="glibc",
            libc_version="2.39",
            runner_hash="e" * 64,
        )


def test_run_evidence_rejects_candidate_config_mismatch_and_runner_drift():
    common = dict(
        evidence_version=OSS3D2G_RUN_EVIDENCE_VERSION,
        candidate_id=CANONICAL_CANDIDATES[1].candidate_id,
        model_config_hash=CANONICAL_CANDIDATES[0].model_config_hash,
        shared_runner_code_hash=family_runner_code_hash(),
        request_hash="1" * 64,
        prediction_artifact_hash="2" * 64,
        prediction_receipt_hash="3" * 64,
        environment_attestation_hash="4" * 64,
        runtime_environment_hash="5" * 64,
    )
    with pytest.raises(QlibFamilyLabIntegrityError, match="candidate/config"):
        FamilyCandidateRunEvidence(**common)

    common["candidate_id"] = CANONICAL_CANDIDATES[0].candidate_id
    common["shared_runner_code_hash"] = "e" * 64
    with pytest.raises(QlibFamilyLabIntegrityError, match="runner hash"):
        FamilyCandidateRunEvidence(**common)


def test_runner_rejects_exchange_credentials_before_reading_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "must-never-enter-research-lab")
    missing = tmp_path / "missing"
    with pytest.raises(QlibFamilyLabGovernanceError, match="credential variables"):
        run_isolated_qlib_family_candidate(
            request_path=missing,
            training_bundle_path=missing,
            train_features_path=missing,
            train_labels_path=missing,
            development_features_path=missing,
            prediction_output_path=tmp_path / "prediction.json",
            receipt_output_path=tmp_path / "receipt.json",
            environment_attestation_output_path=tmp_path / "attestation.json",
            runtime_identity_output_path=tmp_path / "runtime.json",
            run_evidence_output_path=tmp_path / "evidence.json",
        )


def test_family_runner_has_no_development_label_or_holdout_cli_surface():
    source = (Path(__file__).resolve().parents[1] / "family_runner.py").read_text(encoding="utf-8")
    assert "--development-labels" not in source
    assert "development_labels_path" not in source
    assert "--final-holdout" not in source
    assert "final_holdout_path" not in source
    assert "--estimator" not in source
    assert "--alpha" not in source


def test_candidate_attestation_is_config_specific_but_runtime_identity_is_model_neutral():
    distributions = _synthetic_distributions()
    left = CandidateEnvironmentAttestation.build(
        model_config_hash=CANONICAL_CANDIDATES[0].model_config_hash,
        distributions=distributions,
        python_implementation="cpython",
        python_version="3.12.10",
        platform_system="linux",
        platform_machine="x86_64",
        libc_name="glibc",
        libc_version="2.39",
    )
    right = CandidateEnvironmentAttestation.build(
        model_config_hash=CANONICAL_CANDIDATES[1].model_config_hash,
        distributions=distributions,
        python_implementation="cpython",
        python_version="3.12.10",
        platform_system="linux",
        platform_machine="x86_64",
        libc_name="glibc",
        libc_version="2.39",
    )
    assert left.artifact_hash != right.artifact_hash
    assert left.runtime_environment.fingerprint == right.runtime_environment.fingerprint
    assert left.manifest.runner_code_hash == right.manifest.runner_code_hash == family_runner_code_hash()