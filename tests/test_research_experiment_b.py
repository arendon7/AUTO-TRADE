from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.research.experiment_b import (
    ATRImpulseBreakoutStrategy,
    DualMovingAverageTrendStrategy,
    ExperimentBSearchSpace,
    ExperimentBSpecError,
    ExperimentBStrategySpec,
    TrendPullbackStrategy,
    build_experiment_b_program,
)
from autotrade.research.market import Bar
from autotrade.research.strategy import StrategyContext


def _bars(closes: list[str]) -> tuple[Bar, ...]:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result: list[Bar] = []
    for index, raw_close in enumerate(closes):
        close = Decimal(raw_close)
        result.append(
            Bar(
                symbol="BTCUSDT",
                started_at=started + timedelta(hours=index),
                timeframe_seconds=3600,
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("100000"),
            )
        )
    return tuple(result)


def _context(closes: list[str], *, position: str = "0") -> StrategyContext:
    history = _bars(closes)
    return StrategyContext(
        symbol="BTCUSDT",
        index=len(history) - 1,
        history=history,
        current_position_quantity=Decimal(position),
        current_equity=Decimal("100000"),
    )


def test_dma_trend_enters_when_fast_average_exceeds_slow_band() -> None:
    strategy = DualMovingAverageTrendStrategy(
        fast_bars=2,
        lookback_bars=4,
        band_bps=Decimal("0"),
        order_quantity=Decimal("2"),
    )
    signal = strategy.on_bar(_context(["100", "100", "104", "106"]))
    assert signal is not None
    assert signal.quantity_delta == Decimal("2")
    assert "dma:" in signal.reason


def test_dma_long_flat_exits_when_trend_reverses() -> None:
    strategy = DualMovingAverageTrendStrategy(
        fast_bars=2,
        lookback_bars=4,
        band_bps=Decimal("0"),
        order_quantity=Decimal("2"),
    )
    signal = strategy.on_bar(_context(["106", "104", "100", "99"], position="2"))
    assert signal is not None
    assert signal.quantity_delta == Decimal("-2")


def test_atr_impulse_uses_prior_bars_and_targets_long_on_large_up_move() -> None:
    strategy = ATRImpulseBreakoutStrategy(
        lookback_bars=3,
        atr_multiplier=Decimal("1"),
        exit_atr_multiplier=Decimal("0.25"),
        order_quantity=Decimal("1"),
    )
    # Prior bars have true range around 2; final closed bar jumps by 5.
    signal = strategy.on_bar(_context(["100", "100", "100", "100", "100", "105"]))
    assert signal is not None
    assert signal.quantity_delta == Decimal("1")
    assert "up-impulse" in signal.reason


def test_atr_impulse_long_flat_exits_when_impulse_decays() -> None:
    strategy = ATRImpulseBreakoutStrategy(
        lookback_bars=3,
        atr_multiplier=Decimal("2"),
        exit_atr_multiplier=Decimal("0.5"),
        order_quantity=Decimal("1"),
    )
    signal = strategy.on_bar(
        _context(["100", "100", "100", "100", "100", "100.2"], position="1")
    )
    assert signal is not None
    assert signal.quantity_delta == Decimal("-1")
    assert "impulse-decay" in signal.reason


def test_trend_pullback_enters_only_inside_positive_long_term_trend() -> None:
    strategy = TrendPullbackStrategy(
        lookback_bars=6,
        pullback_bars=3,
        min_trend_return=Decimal("0.01"),
        entry_z=Decimal("1"),
        exit_z=Decimal("0.25"),
        order_quantity=Decimal("3"),
    )
    signal = strategy.on_bar(
        _context(["90", "100", "102", "104", "106", "108", "101"])
    )
    assert signal is not None
    assert signal.quantity_delta == Decimal("3")
    assert "trend-pullback-entry" in signal.reason


def test_trend_pullback_does_not_enter_without_positive_trend() -> None:
    strategy = TrendPullbackStrategy(
        lookback_bars=6,
        pullback_bars=3,
        min_trend_return=Decimal("0.01"),
        entry_z=Decimal("1"),
        exit_z=Decimal("0.25"),
        order_quantity=Decimal("3"),
    )
    assert strategy.on_bar(
        _context(["110", "108", "106", "104", "102", "100", "95"])
    ) is None


def test_experiment_b_spec_rejects_unknown_parameters() -> None:
    with pytest.raises(ExperimentBSpecError, match="unknown"):
        ExperimentBStrategySpec(
            strategy_id="b-dma-test",
            strategy_version="1.0.0",
            kind="dual_moving_average_trend",
            parameters={
                "fast_bars": 12,
                "lookback_bars": 72,
                "band_bps": "0",
                "order_quantity": "1",
                "position_mode": "long_flat",
                "broker": "forbidden",
            },
        )


def test_experiment_b_spec_builds_only_finite_catalog() -> None:
    spec = ExperimentBStrategySpec(
        strategy_id="b-atr-test",
        strategy_version="1.0.0",
        kind="atr_impulse_breakout",
        parameters={
            "lookback_bars": 24,
            "atr_multiplier": "1.5",
            "exit_atr_multiplier": "0.25",
            "order_quantity": "2",
            "position_mode": "long_flat",
        },
    )
    assert isinstance(spec.build(), ATRImpulseBreakoutStrategy)


def test_search_space_is_deterministic_and_bounded() -> None:
    space = ExperimentBSearchSpace(
        family_id="b-test",
        strategy_version="1.0.0",
        kind="dual_moving_average_trend",
        dimensions={
            "fast_bars": (12, 24),
            "lookback_bars": (72,),
            "band_bps": ("0", "10"),
            "order_quantity": ("1",),
            "position_mode": ("long_flat",),
        },
        max_candidates=4,
    )
    first = space.candidates()
    second = space.candidates()
    assert space.candidate_count == 4
    assert first == second
    assert len({item.strategy_id for item in first}) == 4


def test_experiment_b_program_is_frozen_at_thirty_candidates() -> None:
    program = build_experiment_b_program(quantity=Decimal("1"))
    assert program.program_id == "r7-experiment-b"
    assert program.candidate_count == 30
    assert len(program.expected_trial_ids) == 30
    assert len(set(program.expected_trial_ids)) == 30
    assert len(program.canonical_hash) == 64
    assert all(trial.startswith("r7-experiment-b-") for trial in program.expected_trial_ids)


def test_program_identity_changes_with_research_quantity() -> None:
    one = build_experiment_b_program(quantity=Decimal("1"))
    two = build_experiment_b_program(quantity=Decimal("2"))
    assert one.canonical_hash != two.canonical_hash
    assert one.expected_trial_ids != two.expected_trial_ids
