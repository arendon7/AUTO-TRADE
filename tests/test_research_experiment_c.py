from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.research.experiment_c import (
    EfficiencyRatioTrendStrategy,
    ExperimentCError,
    ExperimentCStrategySpec,
    NormalizedEWMACStrategy,
    build_experiment_c_program,
)
from autotrade.research.market import Bar
from autotrade.research.strategy import StrategyContext


def _bars(closes: list[str]) -> tuple[Bar, ...]:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values: list[Bar] = []
    for index, raw in enumerate(closes):
        close = Decimal(raw)
        values.append(
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
    return tuple(values)


def _context(closes: list[str], *, position: str = "0") -> StrategyContext:
    history = _bars(closes)
    return StrategyContext(
        symbol="BTCUSDT",
        index=len(history) - 1,
        history=history,
        current_position_quantity=Decimal(position),
        current_equity=Decimal("100000"),
    )


def test_normalized_ewmac_enters_on_persistent_scale_free_trend() -> None:
    closes = [str(100 + index * 0.5) for index in range(80)]
    strategy = NormalizedEWMACStrategy(
        fast_bars=16,
        slow_bars=64,
        volatility_window_bars=48,
        entry_score=Decimal("0.5"),
        exit_score=Decimal("0.1"),
        order_quantity=Decimal("1"),
    )
    signal = strategy.on_bar(_context(closes))
    assert signal is not None
    assert signal.quantity_delta == Decimal("1")
    assert "normalized-ewmac" in signal.reason


def test_normalized_ewmac_exits_after_reversal() -> None:
    closes = [str(100 + index * 0.5) for index in range(64)]
    closes.extend(str(132 - index * 1.5) for index in range(20))
    strategy = NormalizedEWMACStrategy(
        fast_bars=16,
        slow_bars=64,
        volatility_window_bars=48,
        entry_score=Decimal("0.5"),
        exit_score=Decimal("0.1"),
        order_quantity=Decimal("1"),
    )
    signal = strategy.on_bar(_context(closes, position="1"))
    assert signal is not None
    assert signal.quantity_delta == Decimal("-1")


def test_efficiency_ratio_trend_enters_persistent_uptrend() -> None:
    closes = [str(100 + index) for index in range(50)]
    strategy = EfficiencyRatioTrendStrategy(
        lookback_bars=48,
        entry_efficiency=Decimal("0.4"),
        exit_efficiency=Decimal("0.1"),
        order_quantity=Decimal("2"),
    )
    signal = strategy.on_bar(_context(closes))
    assert signal is not None
    assert signal.quantity_delta == Decimal("2")
    assert "positive-efficient-trend" in signal.reason


def test_efficiency_ratio_trend_exits_on_direction_reversal() -> None:
    closes = [str(150 - index) for index in range(49)]
    strategy = EfficiencyRatioTrendStrategy(
        lookback_bars=48,
        entry_efficiency=Decimal("0.2"),
        exit_efficiency=Decimal("0.1"),
        order_quantity=Decimal("2"),
    )
    signal = strategy.on_bar(_context(closes, position="2"))
    assert signal is not None
    assert signal.quantity_delta == Decimal("-2")


def test_experiment_c_spec_is_fail_closed() -> None:
    with pytest.raises(ExperimentCError, match="unknown"):
        ExperimentCStrategySpec(
            strategy_id="c-test",
            strategy_version="1.0.0",
            kind="efficiency_ratio_trend",
            parameters={
                "lookback_bars": 48,
                "entry_efficiency": "0.2",
                "exit_efficiency": "0.1",
                "order_quantity": "1",
                "position_mode": "long_flat",
                "broker": "forbidden",
            },
        )


def test_experiment_c_program_is_exactly_twelve_candidates() -> None:
    program = build_experiment_c_program(quantity=Decimal("1"))
    assert program.program_id == "r7-experiment-c"
    assert program.candidate_count == 12
    assert len(program.expected_trial_ids) == 12
    assert len(set(program.expected_trial_ids)) == 12
    assert len(program.canonical_hash) == 64
    assert all(trial.startswith("r7-experiment-c-") for trial in program.expected_trial_ids)

    candidates = program.candidates()
    ewmac = [item for item in candidates if item.kind == "normalized_ewmac"]
    efficiency = [item for item in candidates if item.kind == "efficiency_ratio_trend"]
    assert len(ewmac) == 6
    assert len(efficiency) == 6
    pairs = {(item.parameters["fast_bars"], item.parameters["slow_bars"]) for item in ewmac}
    assert pairs == {(16, 64), (32, 128), (64, 256)}


def test_experiment_c_program_identity_changes_with_quantity() -> None:
    small = build_experiment_c_program(quantity=Decimal("0.1"))
    large = build_experiment_c_program(quantity=Decimal("2"))
    assert small.canonical_hash != large.canonical_hash
    assert small.expected_trial_ids != large.expected_trial_ids
