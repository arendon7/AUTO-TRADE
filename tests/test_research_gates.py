from dataclasses import replace

import pytest

from autotrade.research.backtest import BacktestMetrics, BacktestResult
from autotrade.research.gates import (
    RobustnessPolicy,
    evaluate_walk_forward_robustness,
)


def result(dataset_hash, *, net_return, drawdown, fills):
    metrics = BacktestMetrics(
        net_return=net_return,
        annualized_volatility=0.1,
        sharpe=1.0,
        sortino=1.2,
        max_drawdown=drawdown,
        turnover=1.0,
        hit_rate=0.5,
        profit_factor=1.2,
        average_gross_exposure=0.4,
        max_gross_exposure=1000.0,
        max_volume_participation=0.01,
        total_fees=10.0,
        fills=fills,
        rejected_signals=0,
    )
    return BacktestResult(
        dataset_hash=dataset_hash,
        strategy_id="s",
        strategy_version="1",
        strategy_parameters={},
        config_hash="c",
        fills=(),
        rejected_signals=(),
        equity_curve=(),
        metrics=metrics,
    )


def policy(**changes):
    values = {
        "min_folds": 3,
        "min_total_fills": 20,
        "min_positive_fold_fraction": 0.66,
        "min_median_net_return": 0.005,
        "min_worst_fold_return": -0.01,
        "max_worst_drawdown": 0.15,
    }
    values.update(changes)
    return RobustnessPolicy(**values)


def test_walk_forward_gate_passes_only_against_explicit_policy():
    decision = evaluate_walk_forward_robustness(
        (
            result("d1", net_return=0.02, drawdown=0.05, fills=10),
            result("d2", net_return=0.01, drawdown=0.08, fills=8),
            result("d3", net_return=0.03, drawdown=0.04, fills=9),
        ),
        policy=policy(),
    )
    assert decision.passed is True
    assert decision.reason_codes == ()
    assert decision.folds == 3
    assert decision.total_fills == 27
    assert decision.positive_fold_fraction == 1.0
    assert decision.worst_fold_return == 0.01
    assert decision.worst_drawdown == 0.08


def test_walk_forward_gate_reports_all_failed_dimensions():
    decision = evaluate_walk_forward_robustness(
        (
            result("d1", net_return=-0.02, drawdown=0.25, fills=2),
            result("d2", net_return=0.001, drawdown=0.20, fills=2),
        ),
        policy=policy(),
    )
    assert decision.passed is False
    assert set(decision.reason_codes) == {
        "INSUFFICIENT_FOLDS",
        "INSUFFICIENT_FILLS",
        "LOW_POSITIVE_FOLD_FRACTION",
        "LOW_MEDIAN_NET_RETURN",
        "WORST_FOLD_RETURN",
        "MAX_DRAWDOWN",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"min_folds": 0},
        {"min_total_fills": -1},
        {"min_positive_fold_fraction": -0.1},
        {"min_positive_fold_fraction": 1.1},
        {"min_median_net_return": float("inf")},
        {"min_worst_fold_return": float("nan")},
        {"max_worst_drawdown": -0.1},
        {"max_worst_drawdown": 1.1},
    ],
)
def test_robustness_policy_validation(changes):
    with pytest.raises(ValueError):
        policy(**changes)


def test_walk_forward_gate_rejects_invalid_result_sets():
    with pytest.raises(ValueError, match="cannot be empty"):
        evaluate_walk_forward_robustness((), policy=policy())

    duplicate = result("same", net_return=0.01, drawdown=0.1, fills=10)
    with pytest.raises(ValueError, match="distinct"):
        evaluate_walk_forward_robustness((duplicate, duplicate), policy=policy())

    invalid = replace(
        result("d1", net_return=0.01, drawdown=0.1, fills=10),
        metrics=replace(
            result("tmp", net_return=0.01, drawdown=0.1, fills=10).metrics,
            net_return=float("nan"),
        ),
    )
    with pytest.raises(ValueError, match="finite"):
        evaluate_walk_forward_robustness((invalid,), policy=policy(min_folds=1))
