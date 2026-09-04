from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Mapping, Sequence

from .strategy import ResearchSignal, StrategyContext


_POSITION_MODES = {"long_flat", "long_short"}


def _require_positive(value: Decimal, *, name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")


def _require_nonnegative(value: Decimal, *, name: str) -> None:
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be finite and >= 0")


def _validate_position_mode(value: str) -> None:
    if value not in _POSITION_MODES:
        raise ValueError("position_mode must be long_flat or long_short")


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ValueError("values cannot be empty")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _population_std(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ValueError("values cannot be empty")
    average = _mean(values)
    variance = sum(((value - average) ** 2 for value in values), Decimal("0")) / Decimal(
        len(values)
    )
    return variance.sqrt()


def _signal_to_target(
    *,
    context: StrategyContext,
    target_quantity: Decimal,
    strategy_id: str,
    strategy_version: str,
    reason: str,
) -> ResearchSignal | None:
    if not target_quantity.is_finite():
        raise ValueError("target_quantity must be finite")
    delta = target_quantity - context.current_position_quantity
    if delta == 0:
        return None
    key = (
        f"{strategy_id}:{strategy_version}:{context.symbol}:"
        f"{context.current_bar.ended_at.isoformat()}:{target_quantity}:{reason}"
    )
    signal_id = f"library:{sha256(key.encode('utf-8')).hexdigest()[:24]}"
    return ResearchSignal(
        signal_id=signal_id,
        symbol=context.symbol,
        generated_at=context.current_bar.ended_at,
        quantity_delta=delta,
        reason=reason,
    )


def _directional_target(
    *,
    direction: int,
    quantity: Decimal,
    position_mode: str,
) -> Decimal:
    if direction > 0:
        return quantity
    if direction < 0 and position_mode == "long_short":
        return -quantity
    return Decimal("0")


@dataclass(frozen=True, slots=True)
class TimeSeriesMomentumStrategy:
    """Research-only time-series momentum with deterministic target positions.

    The strategy compares the latest fully closed bar with a historical close.
    The backtester remains responsible for next-bar execution, costs and risk.
    """

    lookback_bars: int = 96
    order_quantity: Decimal = Decimal("1")
    entry_threshold: Decimal = Decimal("0")
    position_mode: str = "long_short"
    strategy_id: str = "time_series_momentum"
    strategy_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.lookback_bars <= 0:
            raise ValueError("lookback_bars must be > 0")
        _require_positive(self.order_quantity, name="order_quantity")
        _require_nonnegative(self.entry_threshold, name="entry_threshold")
        _validate_position_mode(self.position_mode)
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ValueError("strategy identity is required")

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]:
        payload: dict[str, str | int | float | bool] = {
            "lookback_bars": self.lookback_bars,
            "order_quantity": str(self.order_quantity),
            "entry_threshold": str(self.entry_threshold),
            "position_mode": self.position_mode,
        }
        return payload

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        if len(context.history) < self.lookback_bars + 1:
            return None
        current = context.current_bar.close
        previous = context.history[-self.lookback_bars - 1].close
        momentum = current / previous - Decimal("1")
        if momentum > self.entry_threshold:
            direction = 1
        elif momentum < -self.entry_threshold:
            direction = -1
        else:
            return None
        target = _directional_target(
            direction=direction,
            quantity=self.order_quantity,
            position_mode=self.position_mode,
        )
        return _signal_to_target(
            context=context,
            target_quantity=target,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            reason=f"time_series_momentum:return={momentum}",
        )


@dataclass(frozen=True, slots=True)
class DonchianBreakoutStrategy:
    """Close-confirmed Donchian breakout using only prior-bar channel levels."""

    lookback_bars: int = 48
    order_quantity: Decimal = Decimal("1")
    position_mode: str = "long_short"
    strategy_id: str = "donchian_breakout"
    strategy_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.lookback_bars <= 1:
            raise ValueError("lookback_bars must be > 1")
        _require_positive(self.order_quantity, name="order_quantity")
        _validate_position_mode(self.position_mode)
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ValueError("strategy identity is required")

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]:
        payload: dict[str, str | int | float | bool] = {
            "lookback_bars": self.lookback_bars,
            "order_quantity": str(self.order_quantity),
            "position_mode": self.position_mode,
        }
        return payload

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        if len(context.history) < self.lookback_bars + 1:
            return None
        prior = context.history[-self.lookback_bars - 1 : -1]
        upper = max(bar.high for bar in prior)
        lower = min(bar.low for bar in prior)
        close = context.current_bar.close
        if close > upper:
            direction = 1
            side = "upper"
        elif close < lower:
            direction = -1
            side = "lower"
        else:
            return None
        target = _directional_target(
            direction=direction,
            quantity=self.order_quantity,
            position_mode=self.position_mode,
        )
        return _signal_to_target(
            context=context,
            target_quantity=target,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            reason=f"donchian_breakout:{side};upper={upper};lower={lower}",
        )


@dataclass(frozen=True, slots=True)
class MeanReversionZScoreStrategy:
    """Research-only z-score mean reversion against a prior-bar reference window."""

    lookback_bars: int = 48
    entry_z: Decimal = Decimal("2")
    exit_z: Decimal = Decimal("0.5")
    order_quantity: Decimal = Decimal("1")
    position_mode: str = "long_short"
    strategy_id: str = "mean_reversion_zscore"
    strategy_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.lookback_bars <= 2:
            raise ValueError("lookback_bars must be > 2")
        _require_positive(self.entry_z, name="entry_z")
        _require_nonnegative(self.exit_z, name="exit_z")
        if self.exit_z >= self.entry_z:
            raise ValueError("exit_z must be < entry_z")
        _require_positive(self.order_quantity, name="order_quantity")
        _validate_position_mode(self.position_mode)
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ValueError("strategy identity is required")

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]:
        payload: dict[str, str | int | float | bool] = {
            "lookback_bars": self.lookback_bars,
            "entry_z": str(self.entry_z),
            "exit_z": str(self.exit_z),
            "order_quantity": str(self.order_quantity),
            "position_mode": self.position_mode,
        }
        return payload

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        if len(context.history) < self.lookback_bars + 1:
            return None
        reference = [bar.close for bar in context.history[-self.lookback_bars - 1 : -1]]
        average = _mean(reference)
        standard_deviation = _population_std(reference)
        if standard_deviation == 0:
            return None
        z_score = (context.current_bar.close - average) / standard_deviation
        if z_score <= -self.entry_z:
            target = self.order_quantity
            action = "long"
        elif z_score >= self.entry_z:
            target = (
                -self.order_quantity
                if self.position_mode == "long_short"
                else Decimal("0")
            )
            action = "short" if self.position_mode == "long_short" else "flat-high"
        elif abs(z_score) <= self.exit_z:
            target = Decimal("0")
            action = "mean-exit"
        else:
            return None
        return _signal_to_target(
            context=context,
            target_quantity=target,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            reason=f"mean_reversion_zscore:{action};z={z_score}",
        )


@dataclass(frozen=True, slots=True)
class VolatilityManagedMomentumStrategy:
    """Time-series momentum whose target quantity contracts as volatility rises.

    `target_bar_volatility` is expressed in the same per-bar return units as the
    input series. Scaling is deliberately bounded and remains research-only.
    """

    momentum_lookback_bars: int = 96
    volatility_window_bars: int = 48
    base_quantity: Decimal = Decimal("1")
    target_bar_volatility: Decimal = Decimal("0.01")
    min_scale: Decimal = Decimal("0.10")
    max_scale: Decimal = Decimal("2")
    entry_threshold: Decimal = Decimal("0")
    position_mode: str = "long_short"
    strategy_id: str = "volatility_managed_momentum"
    strategy_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.momentum_lookback_bars <= 0:
            raise ValueError("momentum_lookback_bars must be > 0")
        if self.volatility_window_bars <= 1:
            raise ValueError("volatility_window_bars must be > 1")
        _require_positive(self.base_quantity, name="base_quantity")
        _require_positive(self.target_bar_volatility, name="target_bar_volatility")
        _require_nonnegative(self.min_scale, name="min_scale")
        _require_positive(self.max_scale, name="max_scale")
        if self.min_scale > self.max_scale:
            raise ValueError("min_scale must be <= max_scale")
        _require_nonnegative(self.entry_threshold, name="entry_threshold")
        _validate_position_mode(self.position_mode)
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ValueError("strategy identity is required")

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]:
        payload: dict[str, str | int | float | bool] = {
            "momentum_lookback_bars": self.momentum_lookback_bars,
            "volatility_window_bars": self.volatility_window_bars,
            "base_quantity": str(self.base_quantity),
            "target_bar_volatility": str(self.target_bar_volatility),
            "min_scale": str(self.min_scale),
            "max_scale": str(self.max_scale),
            "entry_threshold": str(self.entry_threshold),
            "position_mode": self.position_mode,
        }
        return payload

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        required = max(self.momentum_lookback_bars, self.volatility_window_bars) + 1
        if len(context.history) < required:
            return None

        current = context.current_bar.close
        previous = context.history[-self.momentum_lookback_bars - 1].close
        momentum = current / previous - Decimal("1")
        if momentum > self.entry_threshold:
            direction = 1
        elif momentum < -self.entry_threshold:
            direction = -1
        else:
            return None

        closes = [
            bar.close
            for bar in context.history[-self.volatility_window_bars - 1 :]
        ]
        returns = [
            closes[index] / closes[index - 1] - Decimal("1")
            for index in range(1, len(closes))
        ]
        realized_volatility = _population_std(returns)
        if realized_volatility == 0:
            return None

        scale = self.target_bar_volatility / realized_volatility
        scale = min(self.max_scale, max(self.min_scale, scale))
        quantity = self.base_quantity * scale
        target = _directional_target(
            direction=direction,
            quantity=quantity,
            position_mode=self.position_mode,
        )
        return _signal_to_target(
            context=context,
            target_quantity=target,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            reason=(
                "volatility_managed_momentum:"
                f"return={momentum};realized_vol={realized_volatility};scale={scale}"
            ),
        )


__all__ = [
    "DonchianBreakoutStrategy",
    "MeanReversionZScoreStrategy",
    "TimeSeriesMomentumStrategy",
    "VolatilityManagedMomentumStrategy",
]
