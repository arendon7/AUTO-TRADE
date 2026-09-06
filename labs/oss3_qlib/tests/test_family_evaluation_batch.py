from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest

from autotrade.research.oss3_concrete_model_family import build_concrete_model_request_set
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
from autotrade.research.trials import SQLiteTrialLedger
from labs.oss3_qlib.family_environment_attestation import CandidateEnvironmentAttestation
from labs.oss3_qlib.family_evaluation_batch import (
    OSS3D2H_BATCH_EVIDENCE_VERSION,
    OSS3D2H_PREREGISTRATION_VERSION,
    FamilyEvaluationBatchGovernanceError,
    FamilyEvaluationBatchIntegrityError,
    FrozenCandidateOutput,
    evaluate_preregistered_family,
    family_evaluation_code_hash,
    prepare_family_evaluation_preregistration,
    preregister_family_evaluation,
)
from labs.oss3_qlib.family_model_contract import family_runner_code_hash
from labs.oss3_qlib.family_runner import run_isolated_qlib_family_candidate


UTC = timezone.utc
BASE = datetime(2026, 3, 1, tzinfo=UTC)
TRAIN_END = BASE + timedelta(days=10)
DEV_START = TRAIN_END
DEV_END = DEV_START + timedelta(days=5)
CAMPAIGN = "oss3d2h-campaign-001"
SPLIT = "1" * 64
UNIVERSE = "2" * 64
FEATURE_CODE = "3" * 64
LABEL_CODE = "4" * 64
SYMBOLS = ("ADAUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")
LABEL_DEF = LabelDefinition(
    name="forward_return",
    dtype="float64",
    role="LABEL",
    formula_hash="b" * 64,
    source_id="synthetic-bars-v1",
    source_hash="c" * 64,
)


def _defs():
    return (
        FactorDefinition(
            name="momentum_20", dtype="float64", role="FEATURE",
            formula_hash="5" * 64, source_id="synthetic-bars-v1",
            source_hash="6" * 64, lookback_bars=20,
        ),
        FactorDefinition(
            name="volatility_20", dtype="float64", role="FEATURE",
            formula_hash="7" * 64, source_id="synthetic-bars-v1",
            source_hash="8" * 64, lookback_bars=20,
        ),
    )


def _train_features():
    rows = []
    for day in range(8):
        timestamp = BASE + timedelta(days=day, hours=1)
        for idx, symbol in enumerate(SYMBOLS, start=1):
            rows.append(
                FactorMatrixRow(
                    as_of=timestamp.isoformat(),
                    available_at=(timestamp - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(
                        0.8 + day * 0.55 + idx * 0.19,
                        1.3 - day * 0.07 + idx * 0.04 + ((day + idx) % 3) * 0.03,
                    ),
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
        features=_defs(),
        rows=tuple(rows),
    )


def _train_labels(features):
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=row.as_of,
            horizon_end=(datetime.fromisoformat(row.as_of) + timedelta(hours=1)).isoformat(),
            available_at=(datetime.fromisoformat(row.as_of) + timedelta(hours=1, minutes=1)).isoformat(),
            symbol=row.symbol,
            value=0.031 * float(row.values[0]) - 0.017 * float(row.values[1]) + 0.004,
        )
        for row in features.rows
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
        label=LABEL_DEF,
        rows=rows,
    )


def _development_features():
    rows = []
    for day in range(4):
        timestamp = DEV_START + timedelta(days=day)
        for idx, symbol in enumerate(SYMBOLS, start=1):
            rows.append(
                FactorMatrixRow(
                    as_of=timestamp.isoformat(),
                    available_at=(timestamp - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(5.0 + day * 0.37 + idx * 0.23, 0.42 + day * 0.06 + idx * 0.025),
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
        features=_defs(),
        rows=tuple(rows),
    )


def _development_labels(features, *, value_shift=0.0):
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=row.as_of,
            horizon_end=(datetime.fromisoformat(row.as_of) + timedelta(hours=2)).isoformat(),
            available_at=(datetime.fromisoformat(row.as_of) + timedelta(hours=2, minutes=1)).isoformat(),
            symbol=row.symbol,
            value=0.028 * float(row.values[0]) - 0.021 * float(row.values[1]) + 0.003 + value_shift,
        )
        for row in features.rows
    )
    return SupervisedLabelArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=LabelPartition.DEVELOPMENT,
        partition_start=DEV_START,
        partition_end=DEV_END,
        producer_code_hash=LABEL_CODE,
        source_dataset_hash="e" * 64,
        source_universe_hash=UNIVERSE,
        label=LABEL_DEF,
        rows=rows,
    )


def _preregistration(plan, request_set, outputs, labels):
    return prepare_family_evaluation_preregistration(
        d2f_plan=plan,
        d2f_request_set=request_set,
        outputs=outputs,
        development_labels=labels,
        tournament_campaign_id="oss3d2h-tournament-campaign-001",
        tournament_id="oss3d2h-tournament-001",
    )


@pytest.fixture(scope="module")
def real_family(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("oss3d2h-real-family")
    broker_prefixes = (
        "APCA_", "ALPACA_", "IBKR_", "BINANCE_", "COINBASE_", "KRAKEN_",
        "BYBIT_", "OKX_", "BITGET_", "KUCOIN_", "BROKER_",
    )
    removed = {key: value for key, value in os.environ.items() if any(key.startswith(p) for p in broker_prefixes)}
    for key in removed:
        os.environ.pop(key, None)
    try:
        train_features = _train_features()
        train_labels = _train_labels(train_features)
        bundle = TrainingBundleArtifact.build(features=train_features, labels=train_labels)
        dev_features = _development_features()
        dev_labels = _development_labels(dev_features)
        plan, request_set = build_concrete_model_request_set(
            training_bundle=bundle,
            development_features=dev_features,
            shared_runner_code_hash=family_runner_code_hash(),
        )
        common = {
            "bundle": tmp_path / "bundle.json",
            "train_features": tmp_path / "train-features.json",
            "train_labels": tmp_path / "train-labels.json",
            "dev_features": tmp_path / "dev-features.json",
        }
        bundle.write(common["bundle"])
        train_features.write(common["train_features"])
        train_labels.write(common["train_labels"])
        dev_features.write(common["dev_features"])

        outputs = []
        for binding in request_set.bindings:
            root = tmp_path / binding.candidate_id
            root.mkdir()
            request_path = root / "request.json"
            prediction_path = root / "prediction.json"
            attestation_path = root / "attestation.json"
            binding.request.write(request_path)
            run_evidence = run_isolated_qlib_family_candidate(
                request_path=request_path,
                training_bundle_path=common["bundle"],
                train_features_path=common["train_features"],
                train_labels_path=common["train_labels"],
                development_features_path=common["dev_features"],
                prediction_output_path=prediction_path,
                receipt_output_path=root / "receipt.json",
                environment_attestation_output_path=attestation_path,
                runtime_identity_output_path=root / "runtime.json",
                run_evidence_output_path=root / "evidence.json",
            )
            prediction = QlibPredictionArtifact.read(prediction_path)
            attestation = CandidateEnvironmentAttestation.read(attestation_path)
            receipt = binding.request.bind_prediction(
                prediction=prediction,
                training_bundle=bundle,
                development_features=dev_features,
            )
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
        yield plan, request_set, tuple(outputs), dev_features, dev_labels
    finally:
        os.environ.update(removed)


def test_end_to_end_six_real_models_preregister_then_evaluate_and_tournament(real_family, tmp_path):
    plan, request_set, outputs, _, labels = real_family
    prereg = _preregistration(plan, request_set, outputs, labels)
    assert prereg.preregistration_version == OSS3D2H_PREREGISTRATION_VERSION
    assert prereg.label_values_used is False
    assert prereg.development_metrics_computed is False
    assert prereg.final_holdout_observed is False
    assert len(prereg.candidate_output_bindings) == 6
    assert prereg.d2h_code_version == family_evaluation_code_hash()

    ledger = SQLiteTrialLedger(tmp_path / "trials.sqlite")
    now = datetime(2026, 3, 20, tzinfo=UTC)
    preregister_family_evaluation(ledger, prereg, now=now)
    evidence = evaluate_preregistered_family(
        ledger, prereg, outputs=outputs, development_labels=labels, now=now + timedelta(seconds=1)
    )
    assert evidence.evidence_version == OSS3D2H_BATCH_EVIDENCE_VERSION
    assert len(evidence.evaluations) == 6
    assert evidence.label_values_used_after_preregistration is True
    assert evidence.development_metrics_computed is True
    assert evidence.final_holdout_observed is False
    assert evidence.promotion_authorized is False
    assert evidence.execution_authorized is False
    assert evidence.paper_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"
    assert evidence.tournament_evidence.plan_fingerprint == prereg.d2e_plan.fingerprint
    assert evidence.tournament_evidence.winner_trial_id in {output.candidate_id for output in outputs}
    assert evidence.tournament_evidence.holm_evidence.family_size == 6
    assert len({item.d2d_evaluation_artifact_hash for item in evidence.evaluations}) == 6


def test_prepare_is_deterministic_and_canonicalizes_output_order(real_family):
    plan, request_set, outputs, _, labels = real_family
    left = _preregistration(plan, request_set, outputs, labels)
    right = _preregistration(plan, request_set, tuple(reversed(outputs)), labels)
    assert left.fingerprint == right.fingerprint
    assert left.development_label_artifact_hash == labels.artifact_hash
    assert left.label_values_used is False
    assert left.development_metrics_computed is False


def test_evaluation_requires_durable_preregistration(real_family, tmp_path):
    plan, request_set, outputs, _, labels = real_family
    prereg = _preregistration(plan, request_set, outputs, labels)
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="durable"):
        evaluate_preregistered_family(
            SQLiteTrialLedger(tmp_path / "missing.sqlite"), prereg,
            outputs=outputs, development_labels=labels,
            now=datetime(2026, 3, 20, tzinfo=UTC),
        )


def test_missing_candidate_fails_before_preregistration(real_family):
    plan, request_set, outputs, _, labels = real_family
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="exact complete"):
        _preregistration(plan, request_set, outputs[:-1], labels)


def test_candidate_identity_mutation_fails_at_construction(real_family):
    _, _, outputs, _, _ = real_family
    with pytest.raises(FamilyEvaluationBatchIntegrityError, match="candidate id"):
        replace(outputs[0], candidate_id=outputs[1].candidate_id)


def test_different_label_artifact_after_preregistration_fails(real_family, tmp_path):
    plan, request_set, outputs, dev_features, labels = real_family
    prereg = _preregistration(plan, request_set, outputs, labels)
    ledger = SQLiteTrialLedger(tmp_path / "trials.sqlite")
    preregister_family_evaluation(ledger, prereg, now=datetime(2026, 3, 20, tzinfo=UTC))
    altered = _development_labels(dev_features, value_shift=0.001)
    with pytest.raises(FamilyEvaluationBatchIntegrityError, match="label artifact"):
        evaluate_preregistered_family(
            ledger, prereg, outputs=outputs, development_labels=altered,
            now=datetime(2026, 3, 20, 0, 0, 1, tzinfo=UTC),
        )


def test_train_labels_are_rejected_before_preregistration(real_family):
    plan, request_set, outputs, _, _ = real_family
    train_features = _train_features()
    train_labels = _train_labels(train_features)
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="DEVELOPMENT"):
        _preregistration(plan, request_set, outputs, train_labels)


def test_evaluating_twice_is_rejected_by_untouched_ledger_rule(real_family, tmp_path):
    plan, request_set, outputs, _, labels = real_family
    prereg = _preregistration(plan, request_set, outputs, labels)
    ledger = SQLiteTrialLedger(tmp_path / "trials.sqlite")
    now = datetime(2026, 3, 20, tzinfo=UTC)
    preregister_family_evaluation(ledger, prereg, now=now)
    evaluate_preregistered_family(
        ledger, prereg, outputs=outputs, development_labels=labels, now=now + timedelta(seconds=1)
    )
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="untouched"):
        evaluate_preregistered_family(
            ledger, prereg, outputs=outputs, development_labels=labels,
            now=now + timedelta(seconds=2),
        )


def test_authority_mutations_are_denied(real_family, tmp_path):
    plan, request_set, outputs, _, labels = real_family
    prereg = _preregistration(plan, request_set, outputs, labels)
    with pytest.raises(FamilyEvaluationBatchGovernanceError):
        replace(prereg, final_holdout_observed=True)
    with pytest.raises(FamilyEvaluationBatchGovernanceError):
        replace(prereg, execution_authorized=True)

    ledger = SQLiteTrialLedger(tmp_path / "trials.sqlite")
    now = datetime(2026, 3, 20, tzinfo=UTC)
    preregister_family_evaluation(ledger, prereg, now=now)
    evidence = evaluate_preregistered_family(
        ledger, prereg, outputs=outputs, development_labels=labels, now=now + timedelta(seconds=1)
    )
    with pytest.raises(FamilyEvaluationBatchGovernanceError):
        replace(evidence, promotion_authorized=True)
    with pytest.raises(FamilyEvaluationBatchGovernanceError):
        replace(evidence, paper_execution_authorized=True)
    with pytest.raises(FamilyEvaluationBatchGovernanceError):
        replace(evidence, capital_authority="LIMITED")


def test_family_evaluation_code_hash_fails_if_semantic_file_missing(tmp_path):
    with pytest.raises(FamilyEvaluationBatchIntegrityError, match="semantic file missing"):
        family_evaluation_code_hash(repo_root=tmp_path)
