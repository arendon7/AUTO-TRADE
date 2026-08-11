from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.allocation_robustness import (
    AllocationRobustnessError,
    AllocationRobustnessPolicy,
    AllocationRobustnessSpec,
    FragileAllocation,
    ScenarioKind,
    evaluate_allocation_robustness,
    require_robust_allocation,
)
from autotrade.research.portfolio_dependence import (
    CalibrationPhase,
    DependenceSpec,
    DiversificationBudgetPolicy,
    PortfolioBudgetViolation,
    ReturnObservation,
    StrategyReturnSeries,
    build_dependence_evidence,
)


D = Decimal


def series(now, strategy_id, values, source_char):
    return StrategyReturnSeries(
        strategy_id=strategy_id,
        strategy_version="1",
        phase=CalibrationPhase.TRAIN,
        source_hash=source_char * 64,
        observations=tuple(
            ReturnObservation(
                occurred_at=now + timedelta(minutes=index),
                value=D(str(value)),
            )
            for index, value in enumerate(values)
        ),
    )


def dependence(now, values=None):
    values = values or {
        "alpha": ["0.010", "0.020", "0.015", "0.025"],
        "beta": ["0.012", "0.018", "0.017", "0.023"],
        "gamma": ["0.009", "0.021", "0.014", "0.026"],
    }
    items = tuple(
        series(now, key, value, chr(ord("a") + index))
        for index, (key, value) in enumerate(sorted(values.items()))
    )
    return build_dependence_evidence(
        items,
        DependenceSpec(
            phase=CalibrationPhase.TRAIN,
            min_common_observations=4,
            cluster_abs_correlation=D("0.95"),
        ),
    )


def budget_policy():
    return DiversificationBudgetPolicy(
        max_strategy_weight=D("0.6"),
        max_cluster_weight=D("1"),
        max_total_weight=D("1"),
    )


def weights():
    return {"alpha@1": D("0.34"), "beta@1": D("0.33"), "gamma@1": D("0.33")}


def robust_spec():
    return AllocationRobustnessSpec(perturbation_weight=D("0.05"))


def loose_policy():
    return AllocationRobustnessPolicy(
        max_mean_degradation_fraction=D("1"),
        max_volatility_increase_fraction=D("1"),
    )


def test_robustness_spec_and_policy_validate_exact_domains():
    assert len(robust_spec().fingerprint) == 64
    assert len(loose_policy().fingerprint) == 64
    for bad in (D("0"), D("1"), D("NaN")):
        with pytest.raises(ValueError, match="perturbation_weight"):
            AllocationRobustnessSpec(bad)
    for bad in (D("-0.01"), D("1.01"), D("NaN")):
        with pytest.raises(ValueError):
            AllocationRobustnessPolicy(bad, D("1"))


def test_complete_scenario_universe_is_deterministic(now):
    dep = dependence(now)
    first = evaluate_allocation_robustness(
        dep, budget_policy(), weights(), robust_spec(), loose_policy()
    )
    second = evaluate_allocation_robustness(
        dep, budget_policy(), dict(reversed(list(weights().items()))), robust_spec(), loose_policy()
    )
    assert first.fingerprint == second.fingerprint
    assert len(first.scenarios) == 9
    assert sum(s.kind is ScenarioKind.LEAVE_ONE_OUT for s in first.scenarios) == 3
    assert sum(s.kind is ScenarioKind.PERTURBATION for s in first.scenarios) == 6
    assert tuple(s.scenario_id for s in first.scenarios) == tuple(
        sorted(s.scenario_id for s in first.scenarios)
    )
    assert all(sum((weight for _, weight in s.weights), D("0")) == D("1") for s in first.scenarios)
    assert first.to_payload()["fingerprint"] == first.fingerprint


def test_loose_policy_accepts_stable_positive_allocation(now):
    evidence = evaluate_allocation_robustness(
        dependence(now), budget_policy(), weights(), robust_spec(), loose_policy()
    )
    assert evidence.baseline_mean_return > 0
    assert evidence.baseline_volatility > 0
    assert evidence.robust is True
    require_robust_allocation(evidence)


def test_fragile_dominant_strategy_fails_leave_one_out_policy(now):
    dep = dependence(
        now,
        {
            "alpha": ["0.10", "0.12", "0.08", "0.11"],
            "beta": ["0.001", "0.002", "0.001", "0.002"],
            "gamma": ["0.002", "0.001", "0.002", "0.001"],
        },
    )
    policy = AllocationRobustnessPolicy(
        max_mean_degradation_fraction=D("0.50"),
        max_volatility_increase_fraction=D("1"),
    )
    evidence = evaluate_allocation_robustness(
        dep, budget_policy(), weights(), robust_spec(), policy
    )
    failed = [scenario for scenario in evidence.scenarios if not scenario.passes_policy]
    assert any(s.scenario_id == "loo:alpha@1" for s in failed)
    assert evidence.robust is False
    with pytest.raises(FragileAllocation, match="loo:alpha@1"):
        require_robust_allocation(evidence)


def test_policy_exact_observed_worst_boundaries_pass_and_epsilon_tighter_fails(now):
    dep = dependence(now)
    seed = evaluate_allocation_robustness(
        dep, budget_policy(), weights(), robust_spec(), loose_policy()
    )
    worst_degradation = max(s.mean_degradation_fraction for s in seed.scenarios)
    worst_volatility = max(s.volatility_increase_fraction for s in seed.scenarios)
    exact = AllocationRobustnessPolicy(worst_degradation, worst_volatility)
    exact_evidence = evaluate_allocation_robustness(
        dep, budget_policy(), weights(), robust_spec(), exact
    )
    assert exact_evidence.robust

    epsilon = D("1e-30")
    if worst_degradation > 0:
        tighter = AllocationRobustnessPolicy(
            max(D("0"), worst_degradation - epsilon),
            worst_volatility,
        )
    else:
        tighter = AllocationRobustnessPolicy(
            worst_degradation,
            max(D("0"), worst_volatility - epsilon),
        )
    tighter_evidence = evaluate_allocation_robustness(
        dep, budget_policy(), weights(), robust_spec(), tighter
    )
    assert not tighter_evidence.robust


def test_budget_violation_is_not_bypassed_by_robustness_engine(now):
    bad_weights = dict(weights())
    bad_weights["alpha@1"] = D("0.6001")
    with pytest.raises(PortfolioBudgetViolation, match="max_strategy_weight"):
        evaluate_allocation_robustness(
            dependence(now),
            budget_policy(),
            bad_weights,
            robust_spec(),
            loose_policy(),
        )


def test_robustness_requires_at_least_two_positive_weight_strategies(now):
    with pytest.raises(AllocationRobustnessError, match="at least two positive-weight"):
        evaluate_allocation_robustness(
            dependence(now),
            budget_policy(),
            {"alpha@1": D("0.5"), "beta@1": D("0"), "gamma@1": D("0")},
            robust_spec(),
            loose_policy(),
        )


def test_nonpositive_baseline_mean_fails_closed(now):
    dep = dependence(
        now,
        {
            "alpha": ["-0.02", "0.01", "-0.01", "0.00"],
            "beta": ["0.01", "-0.02", "0.00", "-0.01"],
            "gamma": ["-0.01", "0.00", "-0.02", "0.01"],
        },
    )
    with pytest.raises(AllocationRobustnessError, match="baseline mean return must be > 0"):
        evaluate_allocation_robustness(
            dep, budget_policy(), weights(), robust_spec(), loose_policy()
        )


def test_positive_but_zero_volatility_baseline_fails_closed(now):
    dep = build_dependence_evidence(
        (
            series(now, "alpha", [0, 2, 0, 2], "a"),
            series(now, "beta", [2, 0, 2, 0], "b"),
        ),
        DependenceSpec(CalibrationPhase.TRAIN, 4, D("0.9")),
    )
    with pytest.raises(AllocationRobustnessError, match="baseline volatility must be > 0"):
        evaluate_allocation_robustness(
            dep,
            DiversificationBudgetPolicy(D("0.5"), D("1"), D("1")),
            {"alpha@1": D("0.5"), "beta@1": D("0.5")},
            robust_spec(),
            loose_policy(),
        )


def test_input_mutation_changes_robustness_fingerprint(now):
    dep = dependence(now)
    baseline = evaluate_allocation_robustness(
        dep, budget_policy(), weights(), robust_spec(), loose_policy()
    )
    changed_weights = {"alpha@1": D("0.35"), "beta@1": D("0.32"), "gamma@1": D("0.33")}
    changed = evaluate_allocation_robustness(
        dep, budget_policy(), changed_weights, robust_spec(), loose_policy()
    )
    assert changed.fingerprint != baseline.fingerprint


def test_require_robust_allocation_requires_real_evidence():
    with pytest.raises(TypeError, match="AllocationRobustnessEvidence"):
        require_robust_allocation(object())  # type: ignore[arg-type]
