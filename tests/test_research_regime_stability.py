from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.research.backtest import BacktestConfig
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.regime_stability import (
    RegimeStabilityConfig,
    RegimeStabilityEvaluator,
    RegimeStabilityPolicy,
)
from autotrade.research.strategy_catalog import LibraryStrategySpec


def _datasets() -> tuple[MarketDataset, MarketDataset]:
    instrument = InstrumentMetadata(
        symbol="TESTUSDT",
        venue="TEST",
        quote_currency="USDT",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars: list[Bar] = []
    price = Decimal("100")
    for index in range(240):
        phase = (index // 20) % 3
        if phase == 0:
            change = Decimal("0.08") + Decimal(index % 2) * Decimal("0.01")
        elif phase == 1:
            change = Decimal("0.35") if index % 2 else Decimal("-0.20")
        else:
            change = Decimal("1.20") if index % 2 else Decimal("-0.85")
        price = max(Decimal("10"), price + change)
        bars.append(
            Bar(
                symbol=instrument.symbol,
                started_at=started + timedelta(hours=index),
                timeframe_seconds=3600,
                open=price - Decimal("0.02"),
                high=price + Decimal("0.10"),
                low=price - Decimal("0.10"),
                close=price,
                volume=Decimal("100000"),
            )
        )
    total = MarketDataset(
        instrument=instrument,
        bars=tuple(bars),
        source="regime-stability-fixture-v1",
    )
    return total.slice(0, 120), total.slice(120, 240)


def _candidate() -> LibraryStrategySpec:
    return LibraryStrategySpec(
        strategy_id="regime-momentum",
        strategy_version="1.0.0",
        kind="time_series_momentum",
        parameters={
            "lookback_bars": 4,
            "order_quantity": "1",
            "entry_threshold": "0",
            "position_mode": "long_flat",
        },
    )


def _backtest_config() -> BacktestConfig:
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


def _config() -> RegimeStabilityConfig:
    return RegimeStabilityConfig(
        volatility_window_bars=8,
        low_quantile=Decimal("0.33"),
        high_quantile=Decimal("0.67"),
        min_calibration_observations=50,
    )


def test_regime_stability_is_deterministic_and_train_calibrated() -> None:
    train, development = _datasets()
    evaluator = RegimeStabilityEvaluator()
    policy = RegimeStabilityPolicy(
        min_observed_states=2,
        min_observations_per_state=1,
        min_worst_state_compounded_return=-1.0,
        min_worst_state_sharpe=-1_000.0,
    )

    first = evaluator.evaluate(
        candidate=_candidate(),
        train_dataset=train,
        development_dataset=development,
        backtest_config=_backtest_config(),
        config=_config(),
        policy=policy,
    )
    second = evaluator.evaluate(
        candidate=_candidate(),
        train_dataset=train,
        development_dataset=development,
        backtest_config=_backtest_config(),
        config=_config(),
        policy=policy,
    )

    assert first == second
    assert first.model_fingerprint == second.model_fingerprint
    assert first.low_threshold < first.high_threshold
    assert len(first.observed_states) >= 2
    assert sum(item.observations for item in first.buckets) > 0
    assert first.passed is True


def test_regime_model_fingerprint_does_not_depend_on_development_prices() -> None:
    train, development = _datasets()
    altered_bars = list(development.bars)
    original = altered_bars[-1]
    altered_bars[-1] = Bar(
        symbol=original.symbol,
        started_at=original.started_at,
        timeframe_seconds=original.timeframe_seconds,
        open=original.open,
        high=original.high + Decimal("5"),
        low=original.low,
        close=original.close + Decimal("4"),
        volume=original.volume,
    )
    altered = MarketDataset(
        instrument=development.instrument,
        bars=tuple(altered_bars),
        source="altered-development",
    )
    evaluator = RegimeStabilityEvaluator()
    policy = RegimeStabilityPolicy(
        min_observed_states=1,
        min_observations_per_state=1,
        min_worst_state_compounded_return=-1.0,
        min_worst_state_sharpe=-1_000.0,
    )

    base = evaluator.evaluate(
        candidate=_candidate(),
        train_dataset=train,
        development_dataset=development,
        backtest_config=_backtest_config(),
        config=_config(),
        policy=policy,
    )
    changed = evaluator.evaluate(
        candidate=_candidate(),
        train_dataset=train,
        development_dataset=altered,
        backtest_config=_backtest_config(),
        config=_config(),
        policy=policy,
    )

    assert base.model_fingerprint == changed.model_fingerprint
    assert base.evaluation_fingerprint != changed.evaluation_fingerprint


def test_regime_policy_can_fail_closed_without_affecting_model_evidence() -> None:
    train, development = _datasets()
    evidence = RegimeStabilityEvaluator().evaluate(
        candidate=_candidate(),
        train_dataset=train,
        development_dataset=development,
        backtest_config=_backtest_config(),
        config=_config(),
        policy=RegimeStabilityPolicy(
            min_observed_states=3,
            min_observations_per_state=1000,
            min_worst_state_compounded_return=0.99,
            min_worst_state_sharpe=1000.0,
        ),
    )

    assert evidence.passed is False
    assert evidence.reasons
    assert evidence.model_fingerprint
    assert evidence.evaluation_fingerprint


def test_regime_stability_rejects_invalid_boundaries_and_configs() -> None:
    train, development = _datasets()
    with pytest.raises(ValueError, match="volatility_window_bars"):
        RegimeStabilityConfig(volatility_window_bars=1)
    with pytest.raises(ValueError, match="min_observed_states"):
        RegimeStabilityPolicy(min_observed_states=4)

    with pytest.raises(ValueError, match="TRAIN cannot overlap DEVELOPMENT"):
        RegimeStabilityEvaluator().evaluate(
            candidate=_candidate(),
            train_dataset=development,
            development_dataset=train,
            backtest_config=_backtest_config(),
            config=_config(),
            policy=RegimeStabilityPolicy(),
        )
