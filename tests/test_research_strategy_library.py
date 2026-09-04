from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.research.market import Bar
from autotrade.research.strategy import StrategyContext
from autotrade.research.strategy_library import (
    DonchianBreakoutStrategy,
    MeanReversionZScoreStrategy,
    TimeSeriesMomentumStrategy,
    VolatilityManagedMomentumStrategy,
)


def _bars(closes: list[str]) -> tuple[Bar, ...]:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result: list[Bar] = []
    for index, raw_close in enumerate(closes):
        close = Decimal(raw_close)
        result.append(
            Bar(
                symbol="BTC-USDT",
                started_at=started + timedelta(hours=index),
                timeframe_seconds=3600,
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("1000"),
            )
        )
    return tuple(result)


def _context(
    closes: list[str],
    *,
    position: Decimal = Decimal("0"),
) -> StrategyContext:
    history = _bars(closes)
    return StrategyContext(
        symbol="BTC-USDT",
        index=len(history) - 1,
        history=history,
        current_position_quantity=position,
        current_equity=Decimal("100000"),
    )


def test_time_series_momentum_targets_positive_trend() -> None:
    strategy = TimeSeriesMomentumStrategy(
        lookback_bars=3,
        order_quantity=Decimal("2"),
        entry_threshold=Decimal("0.01"),
    )

    signal = strategy.on_bar(_context(["100", "101", "102", "105"]))

    assert signal is not None
    assert signal.quantity_delta == Decimal("2")
    assert signal.symbol == "BTC-USDT"
    assert "time_series_momentum" in signal.reason


def test_time_series_momentum_long_flat_exits_negative_trend() -> None:
    strategy = TimeSeriesMomentumStrategy(
        lookback_bars=3,
        order_quantity=Decimal("1"),
        position_mode="long_flat",
    )

    signal = strategy.on_bar(
        _context(["105", "103", "101", "95"], position=Decimal("1"))
    )

    assert signal is not None
    assert signal.quantity_delta == Decimal("-1")


def test_donchian_breakout_uses_prior_channel() -> None:
    strategy = DonchianBreakoutStrategy(
        lookback_bars=3,
        order_quantity=Decimal("1.5"),
    )

    signal = strategy.on_bar(_context(["100", "101", "102", "105"]))

    assert signal is not None
    assert signal.quantity_delta == Decimal("1.5")
    assert "upper" in signal.reason


def test_mean_reversion_zscore_enters_against_downside_extreme() -> None:
    strategy = MeanReversionZScoreStrategy(
        lookback_bars=5,
        entry_z=Decimal("2"),
        exit_z=Decimal("0.5"),
        order_quantity=Decimal("3"),
    )

    signal = strategy.on_bar(_context(["99", "100", "101", "100", "99", "95"]))

    assert signal is not None
    assert signal.quantity_delta == Decimal("3")
    assert "mean_reversion_zscore:long" in signal.reason


def test_mean_reversion_exits_near_reference_mean() -> None:
    strategy = MeanReversionZScoreStrategy(
        lookback_bars=5,
        entry_z=Decimal("2"),
        exit_z=Decimal("0.5"),
        order_quantity=Decimal("1"),
    )

    signal = strategy.on_bar(
        _context(["99", "100", "101", "100", "99", "100"], position=Decimal("1"))
    )

    assert signal is not None
    assert signal.quantity_delta == Decimal("-1")
    assert "mean-exit" in signal.reason


def test_volatility_managed_momentum_reduces_exposure_when_volatility_is_high() -> None:
    strategy = VolatilityManagedMomentumStrategy(
        momentum_lookback_bars=3,
        volatility_window_bars=4,
        base_quantity=Decimal("1"),
        target_bar_volatility=Decimal("0.01"),
        min_scale=Decimal("0.10"),
        max_scale=Decimal("2"),
    )

    signal = strategy.on_bar(_context(["100", "120", "90", "130", "140"]))

    assert signal is not None
    assert Decimal("0") < signal.quantity_delta < Decimal("1")
    assert "realized_vol" in signal.reason


def test_signal_identifier_is_deterministic() -> None:
    strategy = TimeSeriesMomentumStrategy(lookback_bars=3)
    context = _context(["100", "101", "102", "105"])

    first = strategy.on_bar(context)
    second = strategy.on_bar(context)

    assert first is not None
    assert second is not None
    assert first.signal_id == second.signal_id


def test_strategy_parameter_validation_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="position_mode"):
        TimeSeriesMomentumStrategy(position_mode="anything")
    with pytest.raises(ValueError, match="exit_z"):
        MeanReversionZScoreStrategy(entry_z=Decimal("1"), exit_z=Decimal("1"))
    with pytest.raises(ValueError, match="min_scale"):
        VolatilityManagedMomentumStrategy(
            min_scale=Decimal("3"),
            max_scale=Decimal("2"),
        )
