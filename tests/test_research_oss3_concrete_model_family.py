from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from autotrade.research.oss3_concrete_model_family import (
    CANONICAL_CANDIDATES,
    FAMILY_ID,
    MODEL_FAMILY,
    OSS3D2F_EVIDENCE_VERSION,
    OSS3D2F_FAMILY_VERSION,
    QLIB_VERSION,
    ConcreteModelFamilyGovernanceError,
    ConcreteModelFamilyIntegrityError,
    ConcreteModelFamilyPlan,
    all_candidate_config_hashes,
    build_concrete_model_request_set,
    canonical_candidate_config,
    verify_concrete_model_request_set,
)
from autotrade.research.oss3_factor_matrix_artifact import (
    FactorDefinition,
    FactorMatrixArtifact,
    FactorMatrixPartition,
    FactorMatrixRow,
)
from autotrade.research.oss3_supervised_label_artifact import (
    LabelDefinition,
    LabelPartition,
    SupervisedLabelArtifact,
    SupervisedLabelRow,
)
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
TRAIN_END = BASE + timedelta(days=10)
DEV_END = TRAIN_END + timedelta(days=3)
CAMPAIGN = "oss3d2f-campaign-001"
SPLIT = "1" * 64
UNIVERSE = "2" * 64
RUNNER = "8" * 64


def _feature_defs():
    return (
        FactorDefinition(
            name="momentum_20",
            dtype="float64",
            role="FEATURE",
            formula_hash="a" * 64,
            source_id="aligned-bars-v1",
            source_hash="b" * 64,
            lookback_bars=20,
        ),
        FactorDefinition(
            name="volatility_20",
            dtype="float64",
            role="FEATURE",
            formula_hash="c" * 64,
            source_id="aligned-bars-v1",
            source_hash="d" * 64,
            lookback_bars=20,
        ),
    )


def _factor_rows(start: datetime):
    identities = (
        (start, "BTCUSDT"),
        (start, "ETHUSDT"),
        (start + timedelta(days=1), "BTCUSDT"),
    )
    return tuple(
        FactorMatrixRow(
            as_of=timestamp.isoformat(),
            available_at=(timestamp - timedelta(minutes=1)).isoformat(),
            symbol=symbol,
            values=(float(index), float(index) / 10),
        )
        for index, (timestamp, symbol) in enumerate(identities, start=1)
    )


def _features(*, partition: FactorMatrixPartition, start: datetime, end: datetime, source_hash: str):
    row_start = BASE + timedelta(days=1) if partition is FactorMatrixPartition.TRAIN else start
    return FactorMatrixArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=partition,
        partition_start=start,
        partition_end=end,
        producer_code_hash="6" * 64,
        source_dataset_hash=source_hash,
        source_universe_hash=UNIVERSE,
        features=_feature_defs(),
        rows=_factor_rows(row_start),
    )


def _labels():
    identities = (
        (BASE + timedelta(days=1), "BTCUSDT"),
        (BASE + timedelta(days=1), "ETHUSDT"),
        (BASE + timedelta(days=2), "BTCUSDT"),
    )
    rows = tuple(
        SupervisedLabelRow(
            label_as_of=timestamp.isoformat(),
            horizon_end=(timestamp + timedelta(hours=1)).isoformat(),
            available_at=(timestamp + timedelta(hours=1, minutes=1)).isoformat(),
            symbol=symbol,
            value=float(index) / 100,
        )
        for index, (timestamp, symbol) in enumerate(identities, start=1)
    )
    return SupervisedLabelArtifact.build(
        campaign_id=CAMPAIGN,
        research_split_hash=SPLIT,
        partition=LabelPartition.TRAIN,
        partition_start=BASE,
        partition_end=TRAIN_END,
        producer_code_hash="7" * 64,
        source_dataset_hash="4" * 64,
        source_universe_hash=UNIVERSE,
        label=LabelDefinition(
            name="forward_return",
            dtype="float64",
            role="LABEL",
            formula_hash="e" * 64,
            source_id="aligned-bars-v1",
            source_hash="f" * 64,
        ),
        rows=rows,
    )


def _bundle():
    return TrainingBundleArtifact.build(
        features=_features(
            partition=FactorMatrixPartition.TRAIN,
            start=BASE,
            end=TRAIN_END,
            source_hash="3" * 64,
        ),
        labels=_labels(),
    )


def _development_features():
    return _features(
        partition=FactorMatrixPartition.DEVELOPMENT,
        start=TRAIN_END,
        end=DEV_END,
        source_hash="5" * 64,
    )


def _plan():
    return ConcreteModelFamilyPlan(
        family_version=OSS3D2F_FAMILY_VERSION,
        family_id=FAMILY_ID,
        shared_runner_code_hash=RUNNER,
    )


def test_canonical_family_is_exact_finite_and_deterministic():
    assert tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES) == (
        "linear-lasso-a0p001",
        "linear-lasso-a0p01",
        "linear-ols",
        "linear-ridge-a0p1",
        "linear-ridge-a1",
        "linear-ridge-a10",
    )
    assert len(CANONICAL_CANDIDATES) == 6
    assert len(set(all_candidate_config_hashes())) == 6
    assert _plan().fingerprint == _plan().fingerprint


def test_configs_match_qlib_linear_model_contract_without_search_controls():
    for candidate in CANONICAL_CANDIDATES:
        config = canonical_candidate_config(candidate.candidate_id)
        assert config["implementation"] == "qlib.contrib.model.linear.LinearModel"
        assert config["estimator"] in {"ols", "ridge", "lasso"}
        assert config["fit_intercept"] is True
        assert config["include_valid"] is False
        assert config["prediction_segment"] == "test"
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="outside frozen"):
        canonical_candidate_config("linear-ridge-a100-after-looking-at-results")


def test_plan_is_research_only_and_denies_runtime_family_mutation():
    plan = _plan()
    assert plan.research_only is True
    assert plan.adaptive_search is False
    assert plan.hyperparameter_optimization is False
    assert plan.development_labels_observable is False
    assert plan.final_holdout_observable is False
    assert plan.execution_authorized is False
    assert plan.paper_execution_authorized is False
    assert plan.capital_authority == "NONE"
    assert plan.live_trading == "BLOCKED"
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="immutable"):
        replace(plan, candidates=plan.candidates[:-1])
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="adaptive"):
        replace(plan, adaptive_search=True)
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="FINAL_HOLDOUT"):
        replace(plan, final_holdout_observable=True)
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="execution"):
        replace(plan, execution_authorized=True)


def test_build_request_set_covers_exact_family_and_common_support():
    bundle = _bundle()
    dev = _development_features()
    plan, evidence = build_concrete_model_request_set(
        training_bundle=bundle,
        development_features=dev,
        shared_runner_code_hash=RUNNER,
    )
    assert evidence.evidence_version == OSS3D2F_EVIDENCE_VERSION
    assert evidence.family_fingerprint == plan.fingerprint
    assert evidence.training_bundle_hash == bundle.artifact_hash
    assert evidence.development_feature_artifact_hash == dev.artifact_hash
    assert tuple(binding.candidate_id for binding in evidence.bindings) == tuple(
        candidate.candidate_id for candidate in CANONICAL_CANDIDATES
    )
    assert len({binding.request.request_hash for binding in evidence.bindings}) == 6
    manifests = tuple(binding.request.manifest for binding in evidence.bindings)
    assert {m.model_family for m in manifests} == {MODEL_FAMILY}
    assert {m.required_qlib_version for m in manifests} == {QLIB_VERSION}
    assert {m.expected_runner_code_hash for m in manifests} == {RUNNER}
    assert {m.training_bundle_hash for m in manifests} == {bundle.artifact_hash}
    assert {m.development_feature_artifact_hash for m in manifests} == {dev.artifact_hash}
    assert len({m.inference_keyset_hash for m in manifests}) == 1
    assert len({m.model_config_hash for m in manifests}) == 6


def test_request_set_rebuild_verification_is_exact():
    bundle = _bundle()
    dev = _development_features()
    plan, evidence = build_concrete_model_request_set(
        training_bundle=bundle,
        development_features=dev,
        shared_runner_code_hash=RUNNER,
    )
    verify_concrete_model_request_set(
        plan=plan,
        evidence=evidence,
        training_bundle=bundle,
        development_features=dev,
    )


def test_tampered_family_or_evidence_fails_closed():
    bundle = _bundle()
    dev = _development_features()
    plan, evidence = build_concrete_model_request_set(
        training_bundle=bundle,
        development_features=dev,
        shared_runner_code_hash=RUNNER,
    )
    with pytest.raises(ConcreteModelFamilyIntegrityError, match="family fingerprint"):
        verify_concrete_model_request_set(
            plan=plan,
            evidence=replace(evidence, family_fingerprint="0" * 64),
            training_bundle=bundle,
            development_features=dev,
        )
    with pytest.raises(ConcreteModelFamilyIntegrityError, match="runner"):
        verify_concrete_model_request_set(
            plan=plan,
            evidence=replace(evidence, shared_runner_code_hash="9" * 64),
            training_bundle=bundle,
            development_features=dev,
        )


def test_each_request_is_bound_to_candidate_config_hash():
    bundle = _bundle()
    dev = _development_features()
    _, evidence = build_concrete_model_request_set(
        training_bundle=bundle,
        development_features=dev,
        shared_runner_code_hash=RUNNER,
    )
    expected = {
        candidate.candidate_id: candidate.model_config_hash for candidate in CANONICAL_CANDIDATES
    }
    for binding in evidence.bindings:
        assert binding.model_config_hash == expected[binding.candidate_id]
        assert binding.request.manifest.model_config_hash == expected[binding.candidate_id]


def test_family_plan_rejects_invalid_runner_hash_and_authority():
    with pytest.raises(ValueError, match="shared_runner_code_hash"):
        ConcreteModelFamilyPlan(
            family_version=OSS3D2F_FAMILY_VERSION,
            family_id=FAMILY_ID,
            shared_runner_code_hash="broken",
        )
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="capital"):
        replace(_plan(), capital_authority="PAPER")
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="capital"):
        replace(_plan(), live_trading="ENABLED")


def test_candidate_lookup_is_frozen():
    plan = _plan()
    assert plan.candidate("linear-ridge-a1").estimator == "ridge"
    assert plan.candidate("linear-ridge-a1").alpha == 1.0
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="outside frozen"):
        plan.candidate("new-candidate")


def test_request_set_evidence_cannot_claim_labels_holdout_runtime_or_authority():
    bundle = _bundle()
    dev = _development_features()
    _, evidence = build_concrete_model_request_set(
        training_bundle=bundle,
        development_features=dev,
        shared_runner_code_hash=RUNNER,
    )
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="cannot load"):
        replace(evidence, development_labels_loaded=True)
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="cannot load"):
        replace(evidence, final_holdout_loaded=True)
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="cannot load"):
        replace(evidence, external_runtime_invoked=True)
    with pytest.raises(ConcreteModelFamilyGovernanceError, match="execution"):
        replace(evidence, paper_execution_authorized=True)
