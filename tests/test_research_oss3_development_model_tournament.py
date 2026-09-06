from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from statistics import median

import pytest

from autotrade.research.oss3_development_evaluation import (
    CrossSectionalIC,
    DevelopmentEvaluationArtifact,
    DevelopmentEvaluationManifest,
    DevelopmentPredictionMetrics,
    KEY_POLICY_ID,
    METRIC_POLICY_ID,
    OSS3D2D_EVALUATION_VERSION,
    OSS3D2D_PRODUCER_ID,
)
from autotrade.research.oss3_development_inference import (
    DevelopmentPredictionReceipt,
    OSS3D2A_RECEIPT_VERSION,
)
from autotrade.research.oss3_development_model_tournament import (
    COMMON_SUPPORT_POLICY,
    MULTIPLE_TESTING_POLICY,
    OSS3D2E_EVIDENCE_VERSION,
    OSS3D2E_FAMILY_ID,
    OSS3D2E_PLAN_VERSION,
    PRIMARY_METRIC,
    DevelopmentDatasetBinding,
    DevelopmentModelCandidate,
    DevelopmentModelTournamentCompatibilityError,
    DevelopmentModelTournamentGovernanceError,
    DevelopmentModelTournamentIntegrityError,
    OSS3D2ETournamentEvidence,
    _one_sided_exact_sign_test,
    build_oss3d2e_plan,
    evaluate_oss3d2e_tournament,
    preregister_oss3d2e_plan,
    record_oss3d2e_evaluation,
    record_oss3d2e_failure,
)
from autotrade.research.trials import SQLiteTrialLedger, TrialGovernanceError, TrialPhase


UTC = timezone.utc
TRAIN_START = datetime(2026, 1, 1, tzinfo=UTC)
TRAIN_END = datetime(2026, 1, 10, tzinfo=UTC)
DEV_START = datetime(2026, 1, 11, tzinfo=UTC)
DEV_END = datetime(2026, 1, 15, tzinfo=UTC)
NOW = datetime(2026, 2, 1, tzinfo=UTC)
SOURCE_CAMPAIGN = "oss3-source-campaign-001"
TOURNAMENT_CAMPAIGN = "oss3d2e-campaign-001"
TOURNAMENT_ID = "oss3d2e-tournament-001"
QLIB_VERSION = "0.9.7"
RUNNER = sha256(b"runner").hexdigest()
ENVIRONMENT = sha256(b"environment").hexdigest()
CODE_VERSION = sha256(b"d2e-code").hexdigest()


def _h(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _dataset(**overrides) -> DevelopmentDatasetBinding:
    values = {
        "source_campaign_id": SOURCE_CAMPAIGN,
        "research_split_hash": _h("split"),
        "source_universe_hash": _h("universe"),
        "label_definition_hash": _h("label-definition"),
        "development_label_artifact_hash": _h("development-label-artifact"),
        "evaluation_keyset_hash": _h("evaluation-keyset"),
        "evaluation_start": DEV_START.isoformat(),
        "evaluation_end": DEV_END.isoformat(),
    }
    values.update(overrides)
    return DevelopmentDatasetBinding(**values)


def _candidate(index: int, **overrides) -> DevelopmentModelCandidate:
    values = {
        "trial_id": f"model-{index:02d}",
        "hypothesis_id": f"oss3d2e-model-{index:02d}",
        "model_family": f"model_family_{index:02d}",
        "model_config_hash": _h(f"model-config-{index}"),
        "request_hash": _h(f"request-{index}"),
        "qlib_version": QLIB_VERSION,
        "expected_runner_code_hash": RUNNER,
        "environment_attestation_hash": ENVIRONMENT,
    }
    values.update(overrides)
    return DevelopmentModelCandidate(**values)


def _plan(*, candidates=None, dataset=None):
    if candidates is None:
        candidates = (_candidate(1), _candidate(2))
    return build_oss3d2e_plan(
        tournament_campaign_id=TOURNAMENT_CAMPAIGN,
        tournament_id=TOURNAMENT_ID,
        dataset=_dataset() if dataset is None else dataset,
        candidates=candidates,
        code_version=CODE_VERSION,
    )


def _receipt(candidate: DevelopmentModelCandidate, dataset: DevelopmentDatasetBinding, **overrides):
    values = {
        "receipt_version": OSS3D2A_RECEIPT_VERSION,
        "request_hash": candidate.request_hash,
        "request_manifest_hash": _h(f"request-manifest:{candidate.trial_id}"),
        "prediction_artifact_hash": _h(f"prediction-artifact:{candidate.trial_id}"),
        "prediction_manifest_hash": _h(f"prediction-manifest:{candidate.trial_id}"),
        "campaign_id": dataset.source_campaign_id,
        "research_split_hash": dataset.research_split_hash,
        "training_bundle_hash": _h("training-bundle"),
        "development_feature_artifact_hash": _h("development-features"),
        "source_universe_hash": dataset.source_universe_hash,
        "feature_schema_hash": _h("feature-schema"),
        "label_definition_hash": dataset.label_definition_hash,
        "inference_keyset_hash": dataset.evaluation_keyset_hash,
        "prediction_count": 12,
        "model_family": candidate.model_family,
        "model_config_hash": candidate.model_config_hash,
        "qlib_version": candidate.qlib_version,
        "producer_code_hash": candidate.expected_runner_code_hash,
        "train_start": TRAIN_START.isoformat(),
        "train_end": TRAIN_END.isoformat(),
        "inference_start": dataset.evaluation_start,
        "inference_end": dataset.evaluation_end,
    }
    values.update(overrides)
    return DevelopmentPredictionReceipt(**values)


def _artifact_hash(manifest, metrics, cross_sections) -> str:
    payload = {
        "evaluation_version": OSS3D2D_EVALUATION_VERSION,
        "manifest": manifest.to_dict(),
        "metrics": metrics.to_dict(),
        "cross_sections": [item.to_dict() for item in cross_sections],
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _evaluation(
    candidate: DevelopmentModelCandidate,
    dataset: DevelopmentDatasetBinding,
    receipt: DevelopmentPredictionReceipt,
    *,
    rank_ics=(0.8, 0.6, 0.4, 0.2),
    timestamps=None,
    primary=None,
    manifest_overrides=None,
    metric_overrides=None,
) -> DevelopmentEvaluationArtifact:
    if timestamps is None:
        timestamps = tuple((DEV_START + timedelta(days=i)).isoformat() for i in range(len(rank_ics)))
    cross_sections = tuple(
        CrossSectionalIC(
            timestamp=timestamp,
            observation_count=3,
            pearson_ic=float(rank_ic),
            spearman_ic=float(rank_ic),
        )
        for timestamp, rank_ic in zip(timestamps, rank_ics, strict=True)
    )
    rank_values = tuple(float(value) for value in rank_ics)
    mean_rank = sum(rank_values) / len(rank_values)
    positive_ratio = sum(1 for value in rank_values if value > 0.0) / len(rank_values)
    metrics_values = {
        "observation_count": receipt.prediction_count,
        "pearson_ic": mean_rank,
        "spearman_ic": mean_rank,
        "mae": 0.1,
        "rmse": 0.2,
        "sign_accuracy": 0.75,
        "cross_section_count": len(cross_sections),
        "mean_cross_sectional_ic": mean_rank,
        "median_cross_sectional_ic": float(median(rank_values)),
        "positive_cross_sectional_ic_ratio": positive_ratio,
        "mean_cross_sectional_rank_ic": mean_rank if primary is None else primary,
        "median_cross_sectional_rank_ic": float(median(rank_values)),
        "positive_cross_sectional_rank_ic_ratio": positive_ratio,
    }
    if metric_overrides:
        metrics_values.update(metric_overrides)
    metrics = DevelopmentPredictionMetrics(**metrics_values)
    manifest_values = {
        "producer_id": OSS3D2D_PRODUCER_ID,
        "metric_policy_id": METRIC_POLICY_ID,
        "key_policy_id": KEY_POLICY_ID,
        "campaign_id": dataset.source_campaign_id,
        "research_split_hash": dataset.research_split_hash,
        "source_universe_hash": dataset.source_universe_hash,
        "label_definition_hash": dataset.label_definition_hash,
        "prediction_receipt_hash": receipt.fingerprint,
        "prediction_artifact_hash": receipt.prediction_artifact_hash,
        "prediction_manifest_hash": receipt.prediction_manifest_hash,
        "prediction_payload_hash": _h(f"prediction-payload:{candidate.trial_id}"),
        "development_label_artifact_hash": dataset.development_label_artifact_hash,
        "development_label_manifest_hash": _h("development-label-manifest"),
        "development_label_payload_hash": _h("development-label-payload"),
        "evaluation_keyset_hash": dataset.evaluation_keyset_hash,
        "environment_attestation_hash": candidate.environment_attestation_hash,
        "model_family": candidate.model_family,
        "model_config_hash": candidate.model_config_hash,
        "qlib_version": candidate.qlib_version,
        "producer_code_hash": candidate.expected_runner_code_hash,
        "evaluation_start": dataset.evaluation_start,
        "evaluation_end": dataset.evaluation_end,
        "observation_count": metrics.observation_count,
        "cross_section_count": metrics.cross_section_count,
    }
    if manifest_overrides:
        manifest_values.update(manifest_overrides)
    manifest = DevelopmentEvaluationManifest(**manifest_values)
    return DevelopmentEvaluationArtifact(
        evaluation_version=OSS3D2D_EVALUATION_VERSION,
        manifest=manifest,
        metrics=metrics,
        cross_sections=cross_sections,
        artifact_hash=_artifact_hash(manifest, metrics, cross_sections),
    )


def _ledger(tmp_path) -> SQLiteTrialLedger:
    return SQLiteTrialLedger(tmp_path / "research.sqlite3")


def _record_pair(tmp_path, *, first_rank=(0.8, 0.6, 0.4, 0.2), second_rank=(0.4, 0.2, -0.1, 0.1)):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    for offset, (candidate, ranks) in enumerate(zip(plan.candidates, (first_rank, second_rank), strict=True), start=1):
        receipt = _receipt(candidate, plan.dataset)
        evaluation = _evaluation(candidate, plan.dataset, receipt, rank_ics=ranks)
        record_oss3d2e_evaluation(
            ledger,
            plan,
            trial_id=candidate.trial_id,
            evaluation=evaluation,
            receipt=receipt,
            now=NOW + timedelta(minutes=offset),
        )
    return ledger, plan


def test_plan_is_deterministic_sorted_and_development_only():
    c1, c2 = _candidate(1), _candidate(2)
    plan = _plan(candidates=(c2, c1))
    again = _plan(candidates=(c1, c2))
    assert plan.plan_version == OSS3D2E_PLAN_VERSION
    assert plan.fingerprint == again.fingerprint
    assert plan.campaign.family_id == OSS3D2E_FAMILY_ID
    assert plan.campaign.expected_trial_ids == ("model-01", "model-02")
    assert plan.tournament.metric_name == PRIMARY_METRIC
    assert plan.primary_metric == PRIMARY_METRIC
    assert plan.multiple_testing_policy == MULTIPLE_TESTING_POLICY
    assert plan.common_support_policy == COMMON_SUPPORT_POLICY
    assert all(trial.phase is TrialPhase.DEVELOPMENT for trial in plan.trials)
    assert all(trial.split_name == "DEVELOPMENT" for trial in plan.trials)
    assert all(not trial.holdout_authorization_id for trial in plan.trials)
    assert plan.final_holdout_observable is False
    assert plan.execution_authorized is False
    assert plan.paper_execution_authorized is False
    assert plan.capital_authority == "NONE"
    assert plan.live_trading == "BLOCKED"


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-01-11T00:00:00", DEV_END.isoformat()),
        ("2026-01-11T00:00:00Z", DEV_END.isoformat()),
        ("2026-01-11T01:00:00+01:00", DEV_END.isoformat()),
        (DEV_END.isoformat(), DEV_START.isoformat()),
    ],
)
def test_dataset_requires_positive_canonical_utc_window(start, end):
    with pytest.raises(ValueError):
        _dataset(evaluation_start=start, evaluation_end=end)


def test_plan_rejects_family_size_outside_bounds():
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        _plan(candidates=(_candidate(1),))
    too_many = tuple(_candidate(index) for index in range(1, 34))
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        _plan(candidates=too_many)


def test_plan_rejects_duplicate_trial_model_and_request_identities():
    c1 = _candidate(1)
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        _plan(candidates=(c1, replace(_candidate(2), trial_id=c1.trial_id)))
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        _plan(
            candidates=(
                c1,
                replace(
                    _candidate(2),
                    model_family=c1.model_family,
                    model_config_hash=c1.model_config_hash,
                ),
            )
        )
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        _plan(candidates=(c1, replace(_candidate(2), request_hash=c1.request_hash)))


@pytest.mark.parametrize(
    "field,value",
    [
        ("qlib_version", "0.9.8"),
        ("expected_runner_code_hash", _h("other-runner")),
        ("environment_attestation_hash", _h("other-environment")),
    ],
)
def test_plan_requires_one_runtime_environment_across_family(field, value):
    with pytest.raises(DevelopmentModelTournamentCompatibilityError):
        _plan(candidates=(_candidate(1), replace(_candidate(2), **{field: value})))


def test_plan_lookup_fails_closed_for_unknown_trial():
    plan = _plan()
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        plan.candidate("unknown")
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        plan.trial("unknown")


def test_plan_mutations_cannot_weaken_metric_holdout_or_authority():
    plan = _plan()
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        replace(plan, primary_metric="rmse")
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        replace(plan, final_holdout_observable=True)
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        replace(plan, execution_authorized=True)
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        replace(plan, paper_execution_authorized=True)
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        replace(plan, capital_authority="TRADING")
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        replace(plan, live_trading="ENABLED")


def test_preregistration_freezes_entire_campaign_before_results(tmp_path):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    accounting = ledger.campaign_accounting(plan.campaign.campaign_id)
    assert accounting.expected_trial_ids == plan.campaign.expected_trial_ids
    assert accounting.preregistered_trial_ids == plan.campaign.expected_trial_ids
    assert accounting.completed_trial_ids == ()
    assert accounting.failed_trial_ids == ()
    assert accounting.unterminated_trial_ids == plan.campaign.expected_trial_ids


def test_result_cannot_be_recorded_before_preregistration(tmp_path):
    ledger = _ledger(tmp_path)
    plan = _plan()
    candidate = plan.candidates[0]
    receipt = _receipt(candidate, plan.dataset)
    evaluation = _evaluation(candidate, plan.dataset, receipt)
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        record_oss3d2e_evaluation(
            ledger,
            plan,
            trial_id=candidate.trial_id,
            evaluation=evaluation,
            receipt=receipt,
            now=NOW,
        )


def test_record_evaluation_rebinds_primary_metric_and_exact_sign_test(tmp_path):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    candidate = plan.candidates[0]
    receipt = _receipt(candidate, plan.dataset)
    evaluation = _evaluation(candidate, plan.dataset, receipt, rank_ics=(0.8, 0.6, 0.4, 0.2))
    record = record_oss3d2e_evaluation(
        ledger,
        plan,
        trial_id=candidate.trial_id,
        evaluation=evaluation,
        receipt=receipt,
        now=NOW + timedelta(minutes=1),
    )
    assert record.metrics[PRIMARY_METRIC] == pytest.approx(0.5)
    assert float(record.p_value) == pytest.approx(1 / 16)
    assert record.metrics["evaluation_artifact_hash"] == evaluation.artifact_hash
    assert record.metrics["prediction_receipt_hash"] == receipt.fingerprint
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        record_oss3d2e_evaluation(
            ledger,
            plan,
            trial_id=candidate.trial_id,
            evaluation=evaluation,
            receipt=receipt,
            now=NOW + timedelta(minutes=2),
        )


@pytest.mark.parametrize(
    "manifest_field,bad_value",
    [
        ("campaign_id", "other-campaign"),
        ("research_split_hash", _h("other-split")),
        ("source_universe_hash", _h("other-universe")),
        ("label_definition_hash", _h("other-label-definition")),
        ("development_label_artifact_hash", _h("other-label-artifact")),
        ("evaluation_keyset_hash", _h("other-keyset")),
        ("evaluation_start", "2026-01-12T00:00:00+00:00"),
        ("model_family", "other_model"),
        ("model_config_hash", _h("other-config")),
        ("qlib_version", "0.9.8"),
        ("producer_code_hash", _h("other-runner")),
        ("environment_attestation_hash", _h("other-environment")),
    ],
)
def test_evaluation_lineage_drift_is_rejected(tmp_path, manifest_field, bad_value):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    candidate = plan.candidates[0]
    receipt = _receipt(candidate, plan.dataset)
    evaluation = _evaluation(
        candidate,
        plan.dataset,
        receipt,
        manifest_overrides={manifest_field: bad_value},
    )
    with pytest.raises(DevelopmentModelTournamentCompatibilityError):
        record_oss3d2e_evaluation(
            ledger,
            plan,
            trial_id=candidate.trial_id,
            evaluation=evaluation,
            receipt=receipt,
            now=NOW + timedelta(minutes=1),
        )


def test_receipt_request_and_observation_drift_are_rejected(tmp_path):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    candidate = plan.candidates[0]

    bad_request_receipt = _receipt(candidate, plan.dataset, request_hash=_h("wrong-request"))
    bad_request_eval = _evaluation(candidate, plan.dataset, bad_request_receipt)
    with pytest.raises(DevelopmentModelTournamentCompatibilityError):
        record_oss3d2e_evaluation(
            ledger,
            plan,
            trial_id=candidate.trial_id,
            evaluation=bad_request_eval,
            receipt=bad_request_receipt,
            now=NOW + timedelta(minutes=1),
        )

    receipt = _receipt(candidate, plan.dataset)
    evaluation = _evaluation(
        candidate,
        plan.dataset,
        receipt,
        metric_overrides={"observation_count": 9},
        manifest_overrides={"observation_count": 9},
    )
    with pytest.raises(DevelopmentModelTournamentCompatibilityError):
        record_oss3d2e_evaluation(
            ledger,
            plan,
            trial_id=candidate.trial_id,
            evaluation=evaluation,
            receipt=receipt,
            now=NOW + timedelta(minutes=2),
        )


def test_receipt_keyset_drift_is_rejected(tmp_path):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    candidate = plan.candidates[0]
    receipt = _receipt(candidate, plan.dataset, inference_keyset_hash=_h("wrong-keyset"))
    evaluation = _evaluation(candidate, plan.dataset, receipt)
    with pytest.raises(DevelopmentModelTournamentCompatibilityError):
        record_oss3d2e_evaluation(
            ledger,
            plan,
            trial_id=candidate.trial_id,
            evaluation=evaluation,
            receipt=receipt,
            now=NOW + timedelta(minutes=1),
        )


def test_evaluation_prediction_hashes_must_match_receipt(tmp_path):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    candidate = plan.candidates[0]
    receipt = _receipt(candidate, plan.dataset)
    for field in ("prediction_artifact_hash", "prediction_manifest_hash"):
        evaluation = _evaluation(
            candidate,
            plan.dataset,
            receipt,
            manifest_overrides={field: _h(f"wrong-{field}")},
        )
        with pytest.raises(DevelopmentModelTournamentCompatibilityError):
            record_oss3d2e_evaluation(
                ledger,
                plan,
                trial_id=candidate.trial_id,
                evaluation=evaluation,
                receipt=receipt,
                now=NOW + timedelta(minutes=1),
            )


def test_declared_primary_metric_cannot_disagree_with_cross_sections(tmp_path):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    candidate = plan.candidates[0]
    receipt = _receipt(candidate, plan.dataset)
    evaluation = _evaluation(candidate, plan.dataset, receipt, primary=0.9)
    with pytest.raises(DevelopmentModelTournamentIntegrityError):
        record_oss3d2e_evaluation(
            ledger,
            plan,
            trial_id=candidate.trial_id,
            evaluation=evaluation,
            receipt=receipt,
            now=NOW + timedelta(minutes=1),
        )


@pytest.mark.parametrize(
    "values,expected",
    [
        ((1.0, 0.8, 0.5, 0.1), 1 / 16),
        ((1.0, 0.8, 0.5, -0.1), 5 / 16),
        ((1.0, 0.8, -0.5, -0.1), 11 / 16),
        ((0.0, 0.0, 0.0), 1.0),
        ((1.0, 0.0, 0.0), 0.5),
    ],
)
def test_exact_one_sided_sign_test(values, expected):
    assert _one_sided_exact_sign_test(values) == pytest.approx(expected)
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        _one_sided_exact_sign_test(())


def test_tournament_requires_every_preregistered_candidate_terminal(tmp_path):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    first = plan.candidates[0]
    receipt = _receipt(first, plan.dataset)
    evaluation = _evaluation(first, plan.dataset, receipt)
    record_oss3d2e_evaluation(
        ledger,
        plan,
        trial_id=first.trial_id,
        evaluation=evaluation,
        receipt=receipt,
        now=NOW + timedelta(minutes=1),
    )
    with pytest.raises(TrialGovernanceError):
        evaluate_oss3d2e_tournament(ledger, plan)


def test_tournament_ranks_only_primary_metric_and_holm_adjusts_full_family(tmp_path):
    ledger, plan = _record_pair(tmp_path)
    evidence = evaluate_oss3d2e_tournament(ledger, plan)
    assert evidence.evidence_version == OSS3D2E_EVIDENCE_VERSION
    assert evidence.winner_trial_id == "model-01"
    assert evidence.winner_primary_metric == pytest.approx(0.5)
    assert evidence.winner_raw_p_value == pytest.approx(1 / 16)
    assert evidence.winner_holm_adjusted_p_value == pytest.approx(1 / 8)
    assert evidence.holm.family_size == 2
    assert evidence.family_size == 2
    assert evidence.holm.raw_p_values["model-02"] == pytest.approx(5 / 16)
    assert evidence.holm.adjusted_p_values["model-02"] == pytest.approx(5 / 16)
    assert evidence.research_only is True
    assert evidence.final_holdout_observed is False
    assert evidence.promotion_authorized is False
    assert evidence.execution_authorized is False
    assert evidence.paper_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"
    assert evidence.fingerprint == evaluate_oss3d2e_tournament(ledger, plan).fingerprint


def test_failed_candidate_remains_in_holm_family_as_p_one(tmp_path):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    first, second = plan.candidates
    receipt = _receipt(first, plan.dataset)
    evaluation = _evaluation(first, plan.dataset, receipt)
    record_oss3d2e_evaluation(
        ledger,
        plan,
        trial_id=first.trial_id,
        evaluation=evaluation,
        receipt=receipt,
        now=NOW + timedelta(minutes=1),
    )
    record_oss3d2e_failure(
        ledger,
        plan,
        trial_id=second.trial_id,
        failure_code="MODEL_FIT_FAILED",
        now=NOW + timedelta(minutes=2),
    )
    evidence = evaluate_oss3d2e_tournament(ledger, plan)
    assert evidence.winner_trial_id == first.trial_id
    assert evidence.holm.family_size == 2
    assert evidence.holm.raw_p_values[second.trial_id] == 1.0
    assert evidence.holm.failed_trial_ids == (second.trial_id,)
    with pytest.raises(DevelopmentModelTournamentGovernanceError):
        record_oss3d2e_failure(
            ledger,
            plan,
            trial_id=second.trial_id,
            failure_code="OTHER",
            now=NOW + timedelta(minutes=3),
        )


def test_tournament_rejects_different_cross_sectional_support(tmp_path):
    ledger = _ledger(tmp_path)
    plan = _plan()
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    first, second = plan.candidates
    receipt1 = _receipt(first, plan.dataset)
    receipt2 = _receipt(second, plan.dataset)
    eval1 = _evaluation(first, plan.dataset, receipt1)
    shifted = tuple((DEV_START + timedelta(days=i, hours=1)).isoformat() for i in range(4))
    eval2 = _evaluation(second, plan.dataset, receipt2, rank_ics=(0.4, 0.2, -0.1, 0.1), timestamps=shifted)
    record_oss3d2e_evaluation(
        ledger, plan, trial_id=first.trial_id, evaluation=eval1, receipt=receipt1, now=NOW + timedelta(minutes=1)
    )
    record_oss3d2e_evaluation(
        ledger, plan, trial_id=second.trial_id, evaluation=eval2, receipt=receipt2, now=NOW + timedelta(minutes=2)
    )
    with pytest.raises(DevelopmentModelTournamentCompatibilityError):
        evaluate_oss3d2e_tournament(ledger, plan)


def test_exact_primary_tie_uses_immutable_tournament_identity(tmp_path):
    c1 = _candidate(1, model_family="a_model")
    c2 = _candidate(2, model_family="z_model")
    ledger = _ledger(tmp_path)
    plan = _plan(candidates=(c2, c1))
    preregister_oss3d2e_plan(ledger, plan, now=NOW)
    for offset, candidate in enumerate(plan.candidates, start=1):
        receipt = _receipt(candidate, plan.dataset)
        evaluation = _evaluation(candidate, plan.dataset, receipt, rank_ics=(0.8, 0.6, 0.4, 0.2))
        record_oss3d2e_evaluation(
            ledger,
            plan,
            trial_id=candidate.trial_id,
            evaluation=evaluation,
            receipt=receipt,
            now=NOW + timedelta(minutes=offset),
        )
    evidence = evaluate_oss3d2e_tournament(ledger, plan)
    assert evidence.winner_trial_id == c1.trial_id


def test_evidence_mutations_cannot_grant_authority_or_rewrite_p_values(tmp_path):
    ledger, plan = _record_pair(tmp_path)
    evidence = evaluate_oss3d2e_tournament(ledger, plan)
    for kwargs in (
        {"final_holdout_observed": True},
        {"promotion_authorized": True},
        {"execution_authorized": True},
        {"paper_execution_authorized": True},
        {"capital_authority": "TRADING"},
        {"live_trading": "ENABLED"},
    ):
        with pytest.raises(DevelopmentModelTournamentGovernanceError):
            replace(evidence, **kwargs)
    with pytest.raises(DevelopmentModelTournamentIntegrityError):
        replace(evidence, winner_raw_p_value=0.99)
    with pytest.raises(DevelopmentModelTournamentIntegrityError):
        replace(evidence, winner_holm_adjusted_p_value=0.99)


def test_plan_parameter_surface_is_exact_and_hash_bound():
    plan = _plan()
    candidate = plan.candidates[0]
    trial = plan.trials[0]
    assert trial.dataset_hash == plan.dataset.dataset_hash
    assert trial.parameters["request_hash"] == candidate.request_hash
    assert trial.parameters["environment_attestation_hash"] == ENVIRONMENT
    assert trial.parameters["primary_metric"] == PRIMARY_METRIC
    assert trial.parameters["multiple_testing_policy"] == MULTIPLE_TESTING_POLICY
    assert trial.parameters["common_support_policy"] == COMMON_SUPPORT_POLICY
