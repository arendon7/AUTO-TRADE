from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.research.backtest import BacktestConfig
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.robustness import (
    CandidateRobustnessEvidence,
    RobustnessEvaluator,
    RobustnessPolicy,
    StressScenario,
    WalkForwardConfig,
    default_stress_scenarios,
    robust_rank_key,
)
from autotrade.research.strategy_catalog import LibraryStrategySpec


def _dataset(*, count: int = 180) -> MarketDataset:
    instrument = InstrumentMetadata(
        symbol="TESTUSDT",
        venue="TEST",
        quote_currency="USDT",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(count):
        # Persistent trend with small deterministic alternation so Sharpe remains finite.
        close = Decimal("100") + Decimal(index) * Decimal("0.35") + Decimal(index % 2) * Decimal("0.03")
        bars.append(
            Bar(
                symbol="TESTUSDT",
                started_at=started + timedelta(hours=index),
                timeframe_seconds=3600,
                open=close - Decimal("0.05"),
                high=close + Decimal("0.10"),
                low=close - Decimal("0.10"),
                close=close,
                volume=Decimal("100000"),
            )
        )
    return MarketDataset(
        instrument=instrument,
        bars=tuple(bars),
        source="robustness-fixture-v1",
    )


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash=Decimal("10000"),
        cost_model=ExecutionCostModel(
            fee_bps=Decimal("1"),
            half_spread_bps=Decimal("1"),
            slippage_bps=Decimal("1"),
        ),
        execution_delay_bars=1,
        annualization_factor=Decimal("8766"),
        max_leverage=Decimal("1"),
        max_volume_participation=Decimal("0.10"),
        allow_short=False,
    )


def _candidate(strategy_id: str = "momentum") -> LibraryStrategySpec:
    return LibraryStrategySpec(
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        kind="time_series_momentum",
        parameters={
            "lookback_bars": 6,
            "order_quantity": "1",
            "entry_threshold": "0",
            "position_mode": "long_flat",
        },
    )


def _walk_forward() -> WalkForwardConfig:
    return WalkForwardConfig(
        train_bars=48,
        evaluation_bars=24,
        step_bars=24,
        min_folds=3,
    )


def test_stress_scenario_only_worsens_execution_assumptions() -> None:
    base = _config()
    scenario = StressScenario(
        scenario_id="hard",
        fee_multiplier=Decimal("2"),
        spread_multiplier=Decimal("3"),
        slippage_multiplier=Decimal("4"),
        execution_delay_bars=3,
        volume_participation_multiplier=Decimal("0.5"),
        leverage_multiplier=Decimal("0.5"),
    )
    stressed = scenario.apply(base)

    assert stressed.cost_model.fee_bps == Decimal("2")
    assert stressed.cost_model.half_spread_bps == Decimal("3")
    assert stressed.cost_model.slippage_bps == Decimal("4")
    assert stressed.execution_delay_bars == 3
    assert stressed.max_volume_participation == Decimal("0.05")
    assert stressed.max_leverage == Decimal("0.5")
    assert scenario.fingerprint == scenario.fingerprint


def test_robustness_is_deterministic_and_uses_chronological_folds() -> None:
    evaluator = RobustnessEvaluator()
    kwargs = dict(
        candidate=_candidate(),
        development_dataset=_dataset(),
        base_config=_config(),
        walk_forward_config=_walk_forward(),
        stress_scenarios=default_stress_scenarios(),
        policy=RobustnessPolicy(
            min_positive_fold_ratio=0.5,
            min_median_fold_sharpe=-10,
            min_worst_fold_net_return=-0.10,
            max_worst_fold_drawdown=0.50,
            min_stress_pass_ratio=0.0,
            min_worst_stress_net_return=-0.50,
            max_worst_stress_drawdown=0.75,
        ),
    )

    first = evaluator.evaluate(**kwargs)
    second = evaluator.evaluate(**kwargs)

    assert len(first.walk_forward) >= 3
    assert [item.fold_index for item in first.walk_forward] == list(
        range(len(first.walk_forward))
    )
    assert all(item.completed for item in first.walk_forward)
    assert first.positive_fold_ratio > 0
    assert len(first.stress) == 3
    assert first.fingerprint == second.fingerprint
    assert [item.result_hash for item in first.walk_forward] == [
        item.result_hash for item in second.walk_forward
    ]


def test_robustness_policy_fails_closed_when_threshold_is_unreachable() -> None:
    evidence = RobustnessEvaluator().evaluate(
        candidate=_candidate(),
        development_dataset=_dataset(),
        base_config=_config(),
        walk_forward_config=_walk_forward(),
        stress_scenarios=(),
        policy=RobustnessPolicy(
            min_positive_fold_ratio=1.0,
            min_median_fold_sharpe=1_000_000,
            min_worst_fold_net_return=0.0,
            max_worst_fold_drawdown=0.10,
        ),
    )

    assert evidence.passed is False
    assert evidence.stress_pass_ratio == 1.0


def test_duplicate_stress_ids_are_rejected() -> None:
    duplicate = StressScenario(scenario_id="same")
    with pytest.raises(ValueError, match="unique"):
        RobustnessEvaluator().evaluate(
            candidate=_candidate(),
            development_dataset=_dataset(),
            base_config=_config(),
            walk_forward_config=_walk_forward(),
            stress_scenarios=(duplicate, duplicate),
            policy=RobustnessPolicy(),
        )


def test_invalid_robustness_inputs_fail_closed() -> None:
    with pytest.raises(ValueError, match="evaluation_bars"):
        WalkForwardConfig(train_bars=10, evaluation_bars=1)
    with pytest.raises(ValueError, match="fee_multiplier"):
        StressScenario(scenario_id="bad", fee_multiplier=Decimal("0.9"))
    with pytest.raises(ValueError, match="min_stress_pass_ratio"):
        RobustnessPolicy(min_stress_pass_ratio=1.1)


def test_robust_rank_prioritizes_passed_then_quality() -> None:
    def evidence(strategy_id: str, *, passed: bool, median_sharpe: float):
        return CandidateRobustnessEvidence(
            strategy_id=strategy_id,
            strategy_version="1",
            walk_forward=(),
            stress=(),
            positive_fold_ratio=1.0,
            median_fold_sharpe=median_sharpe,
            worst_fold_net_return=0.01,
            worst_fold_drawdown=0.01,
            stress_pass_ratio=1.0,
            worst_stress_net_return=0.01,
            worst_stress_drawdown=0.01,
            passed=passed,
        )

    failed_high = evidence("failed", passed=False, median_sharpe=99.0)
    passed_low = evidence("passed-low", passed=True, median_sharpe=0.5)
    passed_high = evidence("passed-high", passed=True, median_sharpe=1.0)

    ordered = sorted((failed_high, passed_low, passed_high), key=robust_rank_key)
    assert [item.strategy_id for item in ordered] == [
        "passed-high",
        "passed-low",
        "failed",
    ]
