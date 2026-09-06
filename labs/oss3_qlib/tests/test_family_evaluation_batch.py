from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autotrade.research.oss3_concrete_model_family import (
    CANONICAL_CANDIDATES,
    MODEL_FAMILY,
    QLIB_VERSION,
    build_concrete_model_request_set,
)
from autotrade.research.oss3_development_evaluation import DevelopmentEvaluationGovernanceError
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
from autotrade.research.trials import SQLiteTrialLedger, TrialStatus
from labs.oss3_qlib.environment_attestation import InstalledDistribution
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
from labs.oss3_qlib.family_runner import (
    OSS3D2G_RUN_EVIDENCE_VERSION,
    FamilyCandidateRunEvidence,
)


UTC = timezone.utc
BASE = datetime(2026, 3, 1, tzinfo=UTC)
TRAIN_END = BASE + timedelta(days=7)
DEV_START = TRAIN_END
DEV_END = DEV_START + timedelta(days=4)
CAMPAIGN = "oss3d2h-source-campaign-001"
TOURNAMENT_CAMPAIGN = "oss3d2h-tournament-campaign-001"
TOURNAMENT_ID = "oss3d2h-tournament-001"
SPLIT = "1" * 64
UNIVERSE = "2" * 64
FEATURE_CODE = "3" * 64
LABEL_CODE = "4" * 64
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _factor_definitions():
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


def _label_definition():
    return LabelDefinition(
        name="forward_return",
        dtype="float64",
        role="LABEL",
        formula_hash="9" * 64,
        source_id="synthetic-bars-v1",
        source_hash="a" * 64,
    )


def _train_features():
    rows = []
    for day in range(5):
        timestamp = BASE + timedelta(days=day + 1)
        for index, symbol in enumerate(SYMBOLS, start=1):
            rows.append(
                FactorMatrixRow(
                    as_of=timestamp.isoformat(),
                    available_at=(timestamp - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(1.0 + day * 0.5 + index * 0.2, 0.9 - day * 0.04 + index * 0.03),
                )
            )
    return FactorMatrixArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=FactorMatrixPartition.TRAIN,
        partition_start=BASE,
        partition_end=TRAIN_END,
        producer_code_hash=FEATURE_CODE,
        source_dataset_hash="b" * 64,
        source_universe_hash=UNIVERSE,
        features=_factor_definitions(),
        rows=tuple(rows),
    )


def _train_labels(train_features: FactorMatrixArtifact):
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=row.as_of,
            horizon_end=(datetime.fromisoformat(row.as_of) + timedelta(hours=1)).isoformat(),
            available_at=(datetime.fromisoformat(row.as_of) + timedelta(hours=1, minutes=1)).isoformat(),
            symbol=row.symbol,
            value=0.03 * float(row.values[0]) - 0.01 * float(row.values[1]) + 0.002,
        )
        for row in train_features.rows
    )
    return SupervisedLabelArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=LabelPartition.TRAIN,
        partition_start=BASE,
        partition_end=TRAIN_END,
        producer_code_hash=LABEL_CODE,
        source_dataset_hash="c" * 64,
        source_universe_hash=UNIVERSE,
        label=_label_definition(),
        rows=rows,
    )


def _development_features():
    rows = []
    for day in range(3):
        timestamp = DEV_START + timedelta(days=day, hours=2)
        for index, symbol in enumerate(SYMBOLS, start=1):
            rows.append(
                FactorMatrixRow(
                    as_of=timestamp.isoformat(),
                    available_at=(timestamp - timedelta(minutes=1)).isoformat(),
                    symbol=symbol,
                    values=(5.0 + day * 0.3 + index * 0.2, 0.5 + day * 0.05 + index * 0.01),
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
        features=_factor_definitions(),
        rows=tuple(rows),
    )


def _development_labels(dev_features: FactorMatrixArtifact, *, value_shift: float = 0.0):
    target_by_symbol = {"BTCUSDT": 0.01, "ETHUSDT": 0.02, "SOLUSDT": 0.03}
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=row.as_of,
            horizon_end=(datetime.fromisoformat(row.as_of) + timedelta(hours=1)).isoformat(),
            available_at=(datetime.fromisoformat(row.as_of) + timedelta(hours=1, minutes=1)).isoformat(),
            symbol=row.symbol,
            value=target_by_symbol[row.symbol] + value_shift,
        )
        for row in dev_features.rows
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
        label=_label_definition(),
        rows=rows,
    )


def _train_partition_labels(dev_features: FactorMatrixArtifact):
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=(BASE + timedelta(days=1, minutes=index)).isoformat(),
            horizon_end=(BASE + timedelta(days=1, hours=1, minutes=index)).isoformat(),
            available_at=(BASE + timedelta(days=1, hours=1, minutes=index + 1)).isoformat(),
            symbol=row.symbol,
            value=0.01 + index * 0.001,
        )
        for index, row in enumerate(dev_features.rows)
    )
    return SupervisedLabelArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=LabelPartition.TRAIN,
        partition_start=BASE,
        partition_end=TRAIN_END,
        producer_code_hash=LABEL_CODE,
        source_dataset_hash="f" * 64,
        source_universe_hash=UNIVERSE,
        label=_label_definition(),
        rows=rows,
    )


def _base_contract():
    train_features = _train_features()
    train_labels = _train_labels(train_features)
    bundle = TrainingBundleArtifact.build(features=train_features, labels=train_labels)
    dev_features = _development_features()
    plan, request_set = build_concrete_model_request_set(
        training_bundle=bundle,
        development_features=dev_features,
        shared_runner_code_hash=family_runner_code_hash(),
    )
    return plan, request_set, bundle, dev_features


def _prediction_scores(candidate_index: int, *, constant: bool = False):
    if constant:
        return {symbol: 1.0 for symbol in SYMBOLS}
    permutations = (
        (1.0, 2.0, 3.0),
        (1.0, 3.0, 2.0),
        (2.0, 1.0, 3.0),
        (3.0, 2.0, 1.0),
        (2.0, 3.0, 1.0),
        (3.0, 1.0, 2.0),
    )
    return dict(zip(SYMBOLS, permutations[candidate_index], strict=True))


def _candidate_output(
    *,
    candidate_index: int,
    binding,
    bundle: TrainingBundleArtifact,
    dev_features: FactorMatrixArtifact,
    constant: bool = False,
):
    scores = _prediction_scores(candidate_index, constant=constant)
    rows = tuple(
        QlibPredictionRow(
            timestamp=row.as_of,
            symbol=row.symbol,
            score=scores[row.symbol]
            if constant
            else scores[row.symbol] + (datetime.fromisoformat(row.as_of) - DEV_START).days * 0.01,
        )
        for row in dev_features.rows
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
        development_features=dev_features,
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
    run_evidence = FamilyCandidateRunEvidence(
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


def _family_outputs(*, constant_candidate: int | None = None):
    plan, request_set, bundle, dev_features = _base_contract()
    outputs = tuple(
        _candidate_output(
            candidate_index=index,
            binding=binding,
            bundle=bundle,
            dev_features=dev_features,
            constant=index == constant_candidate,
        )
        for index, binding in enumerate(request_set.bindings)
    )
    return plan, request_set, bundle, dev_features, outputs


def _preregistration(*, outputs=None, labels=None):
    plan, request_set, _, dev_features, generated_outputs = _family_outputs()
    actual_outputs = generated_outputs if outputs is None else outputs
    actual_labels = _development_labels(dev_features) if labels is None else labels
    preregistration = prepare_family_evaluation_preregistration(
        d2f_plan=plan,
        d2f_request_set=request_set,
        outputs=actual_outputs,
        development_labels=actual_labels,
        tournament_campaign_id=TOURNAMENT_CAMPAIGN,
        tournament_id=TOURNAMENT_ID,
    )
    return plan, request_set, dev_features, actual_outputs, actual_labels, preregistration


def test_prepare_preregistration_freezes_exact_six_candidate_universe_before_metrics():
    _, _, _, outputs, labels, preregistration = _preregistration()
    assert preregistration.preregistration_version == OSS3D2H_PREREGISTRATION_VERSION
    assert preregistration.label_values_used is False
    assert preregistration.development_metrics_computed is False
    assert preregistration.final_holdout_observed is False
    assert preregistration.execution_authorized is False
    assert preregistration.paper_execution_authorized is False
    assert preregistration.capital_authority == "NONE"
    assert preregistration.live_trading == "BLOCKED"
    assert preregistration.development_label_artifact_hash == labels.artifact_hash
    assert tuple(candidate.trial_id for candidate in preregistration.d2e_plan.candidates) == tuple(
        candidate.candidate_id for candidate in CANONICAL_CANDIDATES
    )
    assert len(outputs) == len(preregistration.d2e_plan.candidates) == 6
    assert {candidate.expected_runner_code_hash for candidate in preregistration.d2e_plan.candidates} == {
        family_runner_code_hash()
    }
    assert preregistration.d2e_plan.final_holdout_observable is False


def test_prepare_is_deterministic_for_same_frozen_inputs():
    plan, request_set, _, dev_features, outputs = _family_outputs()
    labels = _development_labels(dev_features)
    first = prepare_family_evaluation_preregistration(
        d2f_plan=plan,
        d2f_request_set=request_set,
        outputs=reversed(outputs),
        development_labels=labels,
        tournament_campaign_id=TOURNAMENT_CAMPAIGN,
        tournament_id=TOURNAMENT_ID,
    )
    second = prepare_family_evaluation_preregistration(
        d2f_plan=plan,
        d2f_request_set=request_set,
        outputs=outputs,
        development_labels=labels,
        tournament_campaign_id=TOURNAMENT_CAMPAIGN,
        tournament_id=TOURNAMENT_ID,
    )
    assert first.fingerprint == second.fingerprint
    assert first.d2e_plan.fingerprint == second.d2e_plan.fingerprint


def test_prepare_rejects_incomplete_family():
    plan, request_set, _, dev_features, outputs = _family_outputs()
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="exact complete"):
        prepare_family_evaluation_preregistration(
            d2f_plan=plan,
            d2f_request_set=request_set,
            outputs=outputs[:-1],
            development_labels=_development_labels(dev_features),
            tournament_campaign_id=TOURNAMENT_CAMPAIGN,
            tournament_id=TOURNAMENT_ID,
        )


def test_prepare_rejects_non_development_labels():
    plan, request_set, _, dev_features, outputs = _family_outputs()
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="DEVELOPMENT labels only"):
        prepare_family_evaluation_preregistration(
            d2f_plan=plan,
            d2f_request_set=request_set,
            outputs=outputs,
            development_labels=_train_partition_labels(dev_features),
            tournament_campaign_id=TOURNAMENT_CAMPAIGN,
            tournament_id=TOURNAMENT_ID,
        )


def test_evaluation_requires_durable_preregistration(tmp_path):
    _, _, _, outputs, labels, preregistration = _preregistration()
    ledger = SQLiteTrialLedger(tmp_path / "d2h.sqlite3")
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="durable D2E preregistration"):
        evaluate_preregistered_family(
            ledger,
            preregistration,
            outputs=outputs,
            development_labels=labels,
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )


def test_full_six_candidate_batch_evaluates_after_preregistration(tmp_path):
    _, _, _, outputs, labels, preregistration = _preregistration()
    ledger = SQLiteTrialLedger(tmp_path / "d2h.sqlite3")
    now = datetime(2026, 4, 1, tzinfo=UTC)
    preregister_family_evaluation(ledger, preregistration, now=now)

    before = ledger.campaign_accounting(preregistration.d2e_plan.campaign.campaign_id)
    assert before.completed_trial_ids == ()
    assert before.failed_trial_ids == ()
    assert before.unterminated_trial_ids == before.expected_trial_ids

    evidence = evaluate_preregistered_family(
        ledger,
        preregistration,
        outputs=outputs,
        development_labels=labels,
        now=now + timedelta(minutes=1),
    )
    assert evidence.evidence_version == OSS3D2H_BATCH_EVIDENCE_VERSION
    assert evidence.preregistration_fingerprint == preregistration.fingerprint
    assert evidence.d2e_plan_fingerprint == preregistration.d2e_plan.fingerprint
    assert evidence.development_label_artifact_hash == labels.artifact_hash
    assert evidence.shared_runner_code_hash == family_runner_code_hash()
    assert len(evidence.evaluations) == 6
    assert evidence.label_values_used_after_preregistration is True
    assert evidence.development_metrics_computed is True
    assert evidence.final_holdout_observed is False
    assert evidence.promotion_authorized is False
    assert evidence.execution_authorized is False
    assert evidence.paper_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"
    assert evidence.tournament_evidence.winner_trial_id == CANONICAL_CANDIDATES[0].candidate_id
    assert evidence.tournament_evidence.final_holdout_observed is False
    assert evidence.tournament_evidence.promotion_authorized is False

    after = ledger.require_complete_campaign(preregistration.d2e_plan.campaign.campaign_id)
    assert after.completed_trial_ids == after.expected_trial_ids
    assert after.failed_trial_ids == ()


def test_label_artifact_is_immutable_after_preregistration(tmp_path):
    _, _, dev_features, outputs, labels, preregistration = _preregistration()
    ledger = SQLiteTrialLedger(tmp_path / "d2h.sqlite3")
    preregister_family_evaluation(
        ledger,
        preregistration,
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    altered = _development_labels(dev_features, value_shift=0.001)
    assert altered.artifact_hash != labels.artifact_hash
    with pytest.raises(FamilyEvaluationBatchIntegrityError, match="label artifact differs"):
        evaluate_preregistered_family(
            ledger,
            preregistration,
            outputs=outputs,
            development_labels=altered,
            now=datetime(2026, 4, 1, 0, 1, tzinfo=UTC),
        )


def test_output_universe_cannot_change_after_preregistration(tmp_path):
    _, _, _, outputs, labels, preregistration = _preregistration()
    ledger = SQLiteTrialLedger(tmp_path / "d2h.sqlite3")
    preregister_family_evaluation(
        ledger,
        preregistration,
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    with pytest.raises(FamilyEvaluationBatchIntegrityError, match="output universe"):
        evaluate_preregistered_family(
            ledger,
            preregistration,
            outputs=outputs[:-1],
            development_labels=labels,
            now=datetime(2026, 4, 1, 0, 1, tzinfo=UTC),
        )


def test_all_d2d_artifacts_are_prevalidated_before_any_terminal_write(tmp_path):
    plan, request_set, bundle, dev_features = _base_contract()
    outputs = tuple(
        _candidate_output(
            candidate_index=index,
            binding=binding,
            bundle=bundle,
            dev_features=dev_features,
            constant=index == 5,
        )
        for index, binding in enumerate(request_set.bindings)
    )
    labels = _development_labels(dev_features)
    preregistration = prepare_family_evaluation_preregistration(
        d2f_plan=plan,
        d2f_request_set=request_set,
        outputs=outputs,
        development_labels=labels,
        tournament_campaign_id=TOURNAMENT_CAMPAIGN,
        tournament_id=TOURNAMENT_ID,
    )
    ledger = SQLiteTrialLedger(tmp_path / "d2h.sqlite3")
    preregister_family_evaluation(
        ledger,
        preregistration,
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    with pytest.raises(DevelopmentEvaluationGovernanceError, match="constant"):
        evaluate_preregistered_family(
            ledger,
            preregistration,
            outputs=outputs,
            development_labels=labels,
            now=datetime(2026, 4, 1, 0, 1, tzinfo=UTC),
        )
    records = ledger.list_trials(preregistration.d2e_plan.campaign.campaign_id)
    assert len(records) == 6
    assert all(record.status is TrialStatus.PREREGISTERED for record in records)


def test_preregistration_authority_flags_fail_closed():
    _, _, _, _, _, preregistration = _preregistration()
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="HOLDOUT"):
        replace(preregistration, final_holdout_observed=True)
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="execution"):
        replace(preregistration, paper_execution_authorized=True)
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="capital"):
        replace(preregistration, capital_authority="PAPER")
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="capital"):
        replace(preregistration, live_trading="ENABLED")


def test_preregistration_cannot_claim_label_values_or_metrics_already_used():
    _, _, _, _, _, preregistration = _preregistration()
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="before label-value"):
        replace(preregistration, label_values_used=True)
    with pytest.raises(FamilyEvaluationBatchGovernanceError, match="before label-value"):
        replace(preregistration, development_metrics_computed=True)


def test_d2h_code_hash_is_deterministic_and_bound_to_semantic_files(tmp_path):
    first = family_evaluation_code_hash()
    second = family_evaluation_code_hash()
    assert first == second
    assert len(first) == 64
    with pytest.raises(FamilyEvaluationBatchIntegrityError, match="semantic file missing"):
        family_evaluation_code_hash(repo_root=tmp_path)


def test_d2h_module_has_no_qlib_execution_or_final_holdout_surface():
    source = (Path(__file__).resolve().parents[1] / "family_evaluation_batch.py").read_text(
        encoding="utf-8"
    )
    assert "import qlib" not in source
    assert "from qlib" not in source
    assert "LinearModel" not in source
    assert "qlib.init(" not in source
    assert "--final-holdout" not in source
    assert "final_holdout_path" not in source
    assert "submit_order" not in source
    assert "place_order" not in source
