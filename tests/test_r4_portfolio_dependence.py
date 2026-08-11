from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.portfolio_dependence import (
    AllocationBudgetEvidence,
    CalibrationPhase,
    DependenceSpec,
    DiversificationBudgetPolicy,
    InsufficientDependenceEvidence,
    PortfolioBudgetViolation,
    PortfolioDependenceError,
    ReturnObservation,
    StrategyReturnSeries,
    build_dependence_evidence,
    validate_allocation_budget,
)


D = Decimal


def series(now, strategy_id, values, *, offset=0, phase=CalibrationPhase.TRAIN, source_char="a"):
    observations = tuple(
        ReturnObservation(
            occurred_at=now + timedelta(minutes=index + offset),
            value=D(str(value)),
        )
        for index, value in enumerate(values)
    )
    return StrategyReturnSeries(
        strategy_id=strategy_id,
        strategy_version="1",
        phase=phase,
        source_hash=source_char * 64,
        observations=observations,
    )


def spec(*, phase=CalibrationPhase.TRAIN, minimum=3, threshold="0.8"):
    return DependenceSpec(
        phase=phase,
        min_common_observations=minimum,
        cluster_abs_correlation=D(threshold),
    )


def test_phase_enum_structurally_excludes_final_holdout():
    assert {value.value for value in CalibrationPhase} == {"TRAIN", "DEVELOPMENT"}
    with pytest.raises(ValueError):
        CalibrationPhase("FINAL_HOLDOUT")


def test_observations_require_aware_time_and_finite_decimal(now):
    with pytest.raises(ValueError, match="timezone-aware"):
        ReturnObservation(now.replace(tzinfo=None), D("0.01"))
    for bad in (D("NaN"), D("Infinity"), D("-Infinity")):
        with pytest.raises(ValueError, match="finite Decimal"):
            ReturnObservation(now, bad)


def test_series_requires_canonical_identity_hash_and_strict_time_order(now):
    base = series(now, "alpha", [1, 2, 3])
    assert base.strategy_key == "alpha@1"
    assert len(base.fingerprint) == 64

    with pytest.raises(ValueError, match="surrounding whitespace"):
        StrategyReturnSeries(
            strategy_id=" alpha",
            strategy_version="1",
            phase=CalibrationPhase.TRAIN,
            source_hash="a" * 64,
            observations=base.observations,
        )
    with pytest.raises(ValueError, match="source_hash"):
        StrategyReturnSeries(
            strategy_id="alpha",
            strategy_version="1",
            phase=CalibrationPhase.TRAIN,
            source_hash="not-hash",
            observations=base.observations,
        )
    duplicate = (base.observations[0], base.observations[0], base.observations[2])
    with pytest.raises(ValueError, match="strictly increasing"):
        StrategyReturnSeries(
            strategy_id="alpha",
            strategy_version="1",
            phase=CalibrationPhase.TRAIN,
            source_hash="a" * 64,
            observations=duplicate,
        )


def test_dependence_spec_boundaries_are_strict():
    DependenceSpec(CalibrationPhase.TRAIN, 2, D("0"))
    DependenceSpec(CalibrationPhase.TRAIN, 2, D("1"))
    with pytest.raises(ValueError, match=">= 2"):
        DependenceSpec(CalibrationPhase.TRAIN, 1, D("0.5"))
    for bad in (D("-0.0001"), D("1.0001"), D("NaN")):
        with pytest.raises(ValueError, match="cluster_abs_correlation"):
            DependenceSpec(CalibrationPhase.TRAIN, 2, bad)


def test_known_pearson_correlations_are_plus_one_minus_one_and_zero(now):
    alpha = series(now, "alpha", [-1, 0, 1], source_char="a")
    beta = series(now, "beta", [-2, 0, 2], source_char="b")
    gamma = series(now, "gamma", [1, 0, -1], source_char="c")
    delta = series(now, "delta", [1, -2, 1], source_char="d")
    evidence = build_dependence_evidence(
        (delta, gamma, beta, alpha),
        spec(threshold="0.9"),
    )
    assert evidence.correlation("alpha@1", "beta@1") == D("1")
    assert evidence.correlation("alpha@1", "gamma@1") == D("-1")
    assert evidence.correlation("alpha@1", "delta@1") == D("0")
    assert evidence.correlation("alpha@1", "alpha@1") == D("1")


def test_all_pairs_use_one_common_timestamp_intersection(now):
    alpha = series(now, "alpha", [1, 2, 3, 4], offset=0, source_char="a")
    beta = series(now, "beta", [2, 4, 6, 8], offset=1, source_char="b")
    gamma = series(now, "gamma", [8, 6, 4, 2], offset=0, source_char="c")
    evidence = build_dependence_evidence(
        (alpha, beta, gamma),
        spec(minimum=3, threshold="0.9"),
    )
    assert evidence.common_observation_count == 3
    assert evidence.common_timestamps == tuple(
        now + timedelta(minutes=value) for value in (1, 2, 3)
    )
    assert len(evidence.pairs) == 3


def test_exact_minimum_common_sample_passes_and_one_fewer_fails(now):
    alpha = series(now, "alpha", [1, 2, 3, 4], offset=0, source_char="a")
    beta = series(now, "beta", [2, 4, 6, 8], offset=1, source_char="b")
    evidence = build_dependence_evidence((alpha, beta), spec(minimum=3))
    assert evidence.common_observation_count == 3
    with pytest.raises(InsufficientDependenceEvidence, match="below required 4"):
        build_dependence_evidence((alpha, beta), spec(minimum=4))


def test_zero_variance_common_sample_fails_closed(now):
    constant = series(now, "constant", [1, 1, 1], source_char="a")
    moving = series(now, "moving", [1, 2, 3], source_char="b")
    with pytest.raises(InsufficientDependenceEvidence, match="zero-variance"):
        build_dependence_evidence((constant, moving), spec())


def test_calibration_phase_mismatch_fails_before_correlation(now):
    train = series(now, "train", [1, 2, 3], source_char="a")
    development = series(
        now,
        "development",
        [1, 2, 3],
        phase=CalibrationPhase.DEVELOPMENT,
        source_char="b",
    )
    with pytest.raises(PortfolioDependenceError, match="does not match calibration phase"):
        build_dependence_evidence((train, development), spec())


def test_duplicate_strategy_key_fails_closed(now):
    first = series(now, "alpha", [1, 2, 3], source_char="a")
    duplicate = series(now, "alpha", [3, 2, 1], source_char="b")
    with pytest.raises(PortfolioDependenceError, match="duplicate strategy key"):
        build_dependence_evidence((first, duplicate), spec())


def test_dependence_requires_at_least_two_strategies(now):
    only = series(now, "alpha", [1, 2, 3], source_char="a")
    with pytest.raises(InsufficientDependenceEvidence, match="at least two"):
        build_dependence_evidence((only,), spec())


def test_correlation_clusters_are_connected_components_not_pair_buckets(now):
    # Centered vectors: A=(1,-1,0,0), C=(0,0,1,-1), B=A+C.
    # corr(A,B)=corr(B,C)=sqrt(1/2) > 0.70 while corr(A,C)=0.
    alpha = series(now, "alpha", [1, -1, 0, 0], source_char="a")
    beta = series(now, "beta", [1, -1, 1, -1], source_char="b")
    gamma = series(now, "gamma", [0, 0, 1, -1], source_char="c")
    evidence = build_dependence_evidence(
        (gamma, alpha, beta),
        spec(minimum=4, threshold="0.70"),
    )
    assert abs(evidence.correlation("alpha@1", "beta@1")) > D("0.70")
    assert abs(evidence.correlation("beta@1", "gamma@1")) > D("0.70")
    assert evidence.correlation("alpha@1", "gamma@1") == D("0")
    assert evidence.clusters == (("alpha@1", "beta@1", "gamma@1"),)


def test_threshold_exact_boundary_uses_greater_or_equal(now):
    alpha = series(now, "alpha", [-1, 0, 1], source_char="a")
    beta = series(now, "beta", [-2, 0, 2], source_char="b")
    evidence = build_dependence_evidence((alpha, beta), spec(threshold="1"))
    assert evidence.clusters == (("alpha@1", "beta@1"),)


def test_evidence_is_deterministic_and_input_mutation_changes_fingerprint(now):
    alpha = series(now, "alpha", [1, 2, 4], source_char="a")
    beta = series(now, "beta", [2, 5, 8], source_char="b")
    first = build_dependence_evidence((beta, alpha), spec(threshold="0.5"))
    second = build_dependence_evidence((alpha, beta), spec(threshold="0.5"))
    assert first.fingerprint == second.fingerprint
    assert first.to_payload()["fingerprint"] == first.fingerprint

    changed = series(now, "beta", [2, 5, 9], source_char="b")
    third = build_dependence_evidence((alpha, changed), spec(threshold="0.5"))
    assert third.fingerprint != first.fingerprint


def simple_budget_evidence(now):
    alpha = series(now, "alpha", [1, -1, 0, 0], source_char="a")
    beta = series(now, "beta", [1, -1, 1, -1], source_char="b")
    gamma = series(now, "gamma", [0, 0, 1, -1], source_char="c")
    return build_dependence_evidence(
        (alpha, beta, gamma),
        spec(minimum=4, threshold="0.70"),
    )


def test_budget_policy_requires_nested_conservative_limits():
    policy = DiversificationBudgetPolicy(D("0.4"), D("0.7"), D("1"))
    assert len(policy.fingerprint) == 64
    with pytest.raises(ValueError, match="max_strategy_weight cannot exceed"):
        DiversificationBudgetPolicy(D("0.8"), D("0.7"), D("1"))
    with pytest.raises(ValueError, match="max_cluster_weight cannot exceed"):
        DiversificationBudgetPolicy(D("0.4"), D("0.9"), D("0.8"))
    for bad in (D("0"), D("1.0001"), D("NaN")):
        with pytest.raises(ValueError):
            DiversificationBudgetPolicy(bad, D("1"), D("1"))


def test_allocation_budget_accepts_exact_strategy_cluster_and_total_boundaries(now):
    evidence = simple_budget_evidence(now)
    policy = DiversificationBudgetPolicy(D("0.4"), D("0.7"), D("0.7"))
    approved = validate_allocation_budget(
        evidence,
        policy,
        {"alpha@1": D("0.4"), "beta@1": D("0.3"), "gamma@1": D("0")},
    )
    assert isinstance(approved, AllocationBudgetEvidence)
    assert approved.total_weight == D("0.7")
    assert approved.cluster_weights == (
        (("alpha@1", "beta@1", "gamma@1"), D("0.7")),
    )
    assert len(approved.fingerprint) == 64


def test_strategy_weight_exact_boundary_passes_plus_epsilon_fails(now):
    evidence = simple_budget_evidence(now)
    policy = DiversificationBudgetPolicy(D("0.4"), D("1"), D("1"))
    validate_allocation_budget(
        evidence,
        policy,
        {"alpha@1": D("0.4"), "beta@1": D("0"), "gamma@1": D("0")},
    )
    with pytest.raises(PortfolioBudgetViolation, match="max_strategy_weight"):
        validate_allocation_budget(
            evidence,
            policy,
            {"alpha@1": D("0.4000001"), "beta@1": D("0"), "gamma@1": D("0")},
        )


def test_cluster_weight_exact_boundary_passes_plus_epsilon_fails(now):
    evidence = simple_budget_evidence(now)
    policy = DiversificationBudgetPolicy(D("0.5"), D("0.7"), D("1"))
    validate_allocation_budget(
        evidence,
        policy,
        {"alpha@1": D("0.4"), "beta@1": D("0.3"), "gamma@1": D("0")},
    )
    with pytest.raises(PortfolioBudgetViolation, match="max_cluster_weight"):
        validate_allocation_budget(
            evidence,
            policy,
            {"alpha@1": D("0.4"), "beta@1": D("0.3000001"), "gamma@1": D("0")},
        )


def test_total_weight_exact_boundary_passes_plus_epsilon_fails(now):
    alpha = series(now, "alpha", [-1, 0, 1], source_char="a")
    beta = series(now, "beta", [1, -2, 1], source_char="b")
    evidence = build_dependence_evidence((alpha, beta), spec(threshold="0.9"))
    assert evidence.clusters == (("alpha@1",), ("beta@1",))
    policy = DiversificationBudgetPolicy(D("0.6"), D("0.6"), D("1"))
    validate_allocation_budget(
        evidence,
        policy,
        {"alpha@1": D("0.5"), "beta@1": D("0.5")},
    )
    with pytest.raises(PortfolioBudgetViolation, match="max_total_weight"):
        validate_allocation_budget(
            evidence,
            policy,
            {"alpha@1": D("0.5000001"), "beta@1": D("0.5")},
        )


def test_allocation_universe_must_exactly_match_dependence_evidence(now):
    evidence = simple_budget_evidence(now)
    policy = DiversificationBudgetPolicy(D("0.5"), D("1"), D("1"))
    with pytest.raises(PortfolioBudgetViolation, match="universe mismatch"):
        validate_allocation_budget(
            evidence,
            policy,
            {"alpha@1": D("0.2"), "beta@1": D("0.2")},
        )
    with pytest.raises(PortfolioBudgetViolation, match="universe mismatch"):
        validate_allocation_budget(
            evidence,
            policy,
            {
                "alpha@1": D("0.2"),
                "beta@1": D("0.2"),
                "gamma@1": D("0.2"),
                "unknown@1": D("0"),
            },
        )


def test_negative_nonfinite_or_non_decimal_weights_fail_closed(now):
    evidence = simple_budget_evidence(now)
    policy = DiversificationBudgetPolicy(D("0.5"), D("1"), D("1"))
    for bad in (D("-0.01"), D("NaN"), D("Infinity"), 0.2):
        with pytest.raises(PortfolioBudgetViolation, match="finite Decimal >= 0"):
            validate_allocation_budget(
                evidence,
                policy,
                {"alpha@1": bad, "beta@1": D("0"), "gamma@1": D("0")},
            )


def test_no_budget_evidence_can_be_created_when_dependence_is_insufficient(now):
    constant = series(now, "constant", [1, 1, 1], source_char="a")
    moving = series(now, "moving", [1, 2, 3], source_char="b")
    with pytest.raises(InsufficientDependenceEvidence):
        evidence = build_dependence_evidence((constant, moving), spec())
        validate_allocation_budget(  # pragma: no cover - structurally unreachable
            evidence,
            DiversificationBudgetPolicy(D("0.5"), D("1"), D("1")),
            {"constant@1": D("0.5"), "moving@1": D("0.5")},
        )
