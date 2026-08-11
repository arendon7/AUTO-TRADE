from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

from autotrade.research.allocation_robustness import (
    AllocationRobustnessPolicy,
    AllocationRobustnessSpec,
    evaluate_allocation_robustness,
)
from autotrade.research.portfolio_dependence import (
    CalibrationPhase,
    DependenceSpec,
    DiversificationBudgetPolicy,
    ReturnObservation,
    StrategyReturnSeries,
    build_dependence_evidence,
)


D = Decimal


def _series(now, strategy, values):
    return StrategyReturnSeries(
        strategy_id=strategy,
        strategy_version="1",
        phase=CalibrationPhase.TRAIN,
        source_hash=sha256(strategy.encode()).hexdigest(),
        observations=tuple(
            ReturnObservation(now + timedelta(minutes=index), D(value))
            for index, value in enumerate(values)
        ),
    )


def _dependence(now):
    return build_dependence_evidence(
        (
            _series(now, "alpha", ("0.010", "0.020", "0.015", "0.025", "0.018", "0.022")),
            _series(now, "beta", ("0.012", "0.018", "0.017", "0.023", "0.019", "0.021")),
            _series(now, "gamma", ("0.009", "0.021", "0.014", "0.026", "0.017", "0.023")),
        ),
        DependenceSpec(CalibrationPhase.TRAIN, 6, D("0.95")),
    )


def test_repeating_thirds_preserve_exact_sum_in_baseline_and_every_scenario(now):
    evidence = evaluate_allocation_robustness(
        _dependence(now),
        DiversificationBudgetPolicy(D("0.40"), D("0.90"), D("0.90")),
        {"alpha@1": D("0.30"), "beta@1": D("0.30"), "gamma@1": D("0.30")},
        AllocationRobustnessSpec(D("0.05")),
        AllocationRobustnessPolicy(D("1"), D("1")),
    )
    assert sum((weight for _, weight in evidence.baseline_normalized_weights), D("0")) == D("1")
    assert len(evidence.scenarios) == 9
    assert all(
        sum((weight for _, weight in scenario.weights), D("0")) == D("1")
        for scenario in evidence.scenarios
    )
    assert evidence.robust


def test_exact_normalization_is_deterministic_for_repeating_ratios(now):
    dependence = _dependence(now)
    kwargs = dict(
        dependence=dependence,
        budget_policy=DiversificationBudgetPolicy(D("0.40"), D("0.90"), D("0.90")),
        spec=AllocationRobustnessSpec(D("0.05")),
        policy=AllocationRobustnessPolicy(D("1"), D("1")),
    )
    first = evaluate_allocation_robustness(
        strategy_weights={"alpha@1": D("0.30"), "beta@1": D("0.30"), "gamma@1": D("0.30")},
        **kwargs,
    )
    second = evaluate_allocation_robustness(
        strategy_weights={"gamma@1": D("0.30"), "alpha@1": D("0.30"), "beta@1": D("0.30")},
        **kwargs,
    )
    assert first.fingerprint == second.fingerprint
