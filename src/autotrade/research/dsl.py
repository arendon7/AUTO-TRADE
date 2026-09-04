from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Mapping

from .market import Bar
from .strategy import ResearchSignal, StrategyContext


class InvalidStrategySpec(ValueError):
    pass


_ALLOWED_TOP_LEVEL = {
    "strategy_id",
    "strategy_version",
    "kind",
    "parameters",
    "initial_stop_pct",
}

_ALLOWED_PARAMETERS_BY_KIND = {
    "moving_average_cross": {
        "short_window",
        "long_window",
        "order_quantity",
        "position_mode",
    },
    "trend_ema_atr": {
        "fast_span",
        "slow_span",
        "atr_window",
        "min_atr_pct",
        "order_quantity",
        "position_mode",
    },
    "time_series_momentum": {
        "fast_horizon",
        "slow_horizon",
        "threshold",
        "order_quantity",
        "position_mode",
    },
    "mean_reversion_zscore": {
        "lookback",
        "entry_z",
        "exit_z",
        "order_quantity",
        "position_mode",
    },
    "donchian_breakout": {
        "lookback",
        "atr_window",
        "min_atr_pct",
        "order_quantity",
        "position_mode",
    },
    "volatility_regime": {
        "short_vol_window",
        "long_vol_window",
        "trend_window",
        "vol_ratio_threshold",
        "order_quantity",
        "position_mode",
    },
}


def _decimal(value: object, *, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidStrategySpec(f"{name} must be decimal-compatible") from exc
    if not result.is_finite():
        raise InvalidStrategySpec(f"{name} must be finite")
    return result


def _positive_int(value: object, *, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidStrategySpec(f"{name} must be an integer >= {minimum}")
    return value


def _validate_position_mode(value: object) -> str:
    if value not in {"long_flat", "long_short"}:
        raise InvalidStrategySpec("position_mode must be long_flat or long_short")
    return str(value)


def _validate_quantity(value: object) -> Decimal:
    quantity = _decimal(value, name="order_quantity")
    if quantity <= 0:
        raise InvalidStrategySpec("order_quantity must be > 0")
    return quantity


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    strategy_version: str
    kind: str
    parameters: Mapping[str, str | int | float | bool]
    initial_stop_pct: Decimal

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise InvalidStrategySpec("strategy_id is required")
        if not self.strategy_version.strip():
            raise InvalidStrategySpec("strategy_version is required")
        if self.kind not in _ALLOWED_PARAMETERS_BY_KIND:
            raise InvalidStrategySpec(f"unsupported strategy kind: {self.kind}")
        if (
            not self.initial_stop_pct.is_finite()
            or not Decimal("0") < self.initial_stop_pct < Decimal("1")
        ):
            raise InvalidStrategySpec(
                "initial_stop_pct must be finite and between 0 and 1"
            )

        params = dict(self.parameters)
        allowed = _ALLOWED_PARAMETERS_BY_KIND[self.kind]
        unknown = set(params) - allowed
        missing = allowed - set(params)
        if unknown:
            raise InvalidStrategySpec(
                f"unknown strategy parameters: {sorted(unknown)}"
            )
        if missing:
            raise InvalidStrategySpec(
                f"missing strategy parameters: {sorted(missing)}"
            )
        validator = getattr(self, f"_validate_{self.kind}")
        validator(params)

    def _validate_moving_average_cross(self, params: dict[str, object]) -> None:
        short_window = _positive_int(
            params["short_window"], name="short_window", minimum=1
        )
        long_window = _positive_int(
            params["long_window"], name="long_window", minimum=2
        )
        if short_window >= long_window:
            raise InvalidStrategySpec("short_window must be < long_window")
        _validate_quantity(params["order_quantity"])
        _validate_position_mode(params["position_mode"])

    def _validate_trend_ema_atr(self, params: dict[str, object]) -> None:
        fast_span = _positive_int(params["fast_span"], name="fast_span", minimum=2)
        slow_span = _positive_int(params["slow_span"], name="slow_span", minimum=3)
        if fast_span >= slow_span:
            raise InvalidStrategySpec("fast_span must be < slow_span")
        _positive_int(params["atr_window"], name="atr_window", minimum=2)
        min_atr_pct = _decimal(params["min_atr_pct"], name="min_atr_pct")
        if min_atr_pct < 0 or min_atr_pct >= 1:
            raise InvalidStrategySpec("min_atr_pct must be >= 0 and < 1")
        _validate_quantity(params["order_quantity"])
        _validate_position_mode(params["position_mode"])

    def _validate_time_series_momentum(self, params: dict[str, object]) -> None:
        fast = _positive_int(
            params["fast_horizon"], name="fast_horizon", minimum=1
        )
        slow = _positive_int(
            params["slow_horizon"], name="slow_horizon", minimum=2
        )
        if fast >= slow:
            raise InvalidStrategySpec("fast_horizon must be < slow_horizon")
        threshold = _decimal(params["threshold"], name="threshold")
        if threshold < 0 or threshold >= 1:
            raise InvalidStrategySpec("threshold must be >= 0 and < 1")
        _validate_quantity(params["order_quantity"])
        _validate_position_mode(params["position_mode"])

    def _validate_mean_reversion_zscore(self, params: dict[str, object]) -> None:
        _positive_int(params["lookback"], name="lookback", minimum=3)
        entry_z = _decimal(params["entry_z"], name="entry_z")
        exit_z = _decimal(params["exit_z"], name="exit_z")
        if entry_z <= 0:
            raise InvalidStrategySpec("entry_z must be > 0")
        if exit_z < 0 or exit_z >= entry_z:
            raise InvalidStrategySpec("exit_z must be >= 0 and < entry_z")
        _validate_quantity(params["order_quantity"])
        _validate_position_mode(params["position_mode"])

    def _validate_donchian_breakout(self, params: dict[str, object]) -> None:
        _positive_int(params["lookback"], name="lookback", minimum=2)
        _positive_int(params["atr_window"], name="atr_window", minimum=2)
        min_atr_pct = _decimal(params["min_atr_pct"], name="min_atr_pct")
        if min_atr_pct < 0 or min_atr_pct >= 1:
            raise InvalidStrategySpec("min_atr_pct must be >= 0 and < 1")
        _validate_quantity(params["order_quantity"])
        _validate_position_mode(params["position_mode"])

    def _validate_volatility_regime(self, params: dict[str, object]) -> None:
        short_window = _positive_int(
            params["short_vol_window"], name="short_vol_window", minimum=2
        )
        long_window = _positive_int(
            params["long_vol_window"], name="long_vol_window", minimum=3
        )
        if short_window >= long_window:
            raise InvalidStrategySpec(
                "short_vol_window must be < long_vol_window"
            )
        _positive_int(params["trend_window"], name="trend_window", minimum=2)
        ratio = _decimal(params["vol_ratio_threshold"], name="vol_ratio_threshold")
        if ratio <= 0:
            raise InvalidStrategySpec("vol_ratio_threshold must be > 0")
        _validate_quantity(params["order_quantity"])
        _validate_position_mode(params["position_mode"])

    @classmethod
    def from_json(cls, raw: str) -> "StrategySpec":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidStrategySpec("strategy spec must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise InvalidStrategySpec("strategy spec root must be an object")
        unknown = set(payload) - _ALLOWED_TOP_LEVEL
        missing = _ALLOWED_TOP_LEVEL - set(payload)
        if unknown:
            raise InvalidStrategySpec(
                f"unknown top-level fields: {sorted(unknown)}"
            )
        if missing:
            raise InvalidStrategySpec(
                f"missing top-level fields: {sorted(missing)}"
            )
        if not isinstance(payload["parameters"], dict):
            raise InvalidStrategySpec("parameters must be an object")
        return cls(
            strategy_id=str(payload["strategy_id"]),
            strategy_version=str(payload["strategy_version"]),
            kind=str(payload["kind"]),
            parameters=payload["parameters"],
            initial_stop_pct=_decimal(
                payload["initial_stop_pct"], name="initial_stop_pct"
            ),
        )

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "kind": self.kind,
            "parameters": dict(self.parameters),
            "initial_stop_pct": str(self.initial_stop_pct),
        }

    @property
    def canonical_hash(self) -> str:
        raw = json.dumps(
            self.canonical_payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(raw).hexdigest()

    def build(self) -> "SafeDeclarativeStrategy":
        if self.kind == "moving_average_cross":
            return MovingAverageCrossStrategy(self)
        return SafeDeclarativeStrategy(self)


class SafeDeclarativeStrategy:
    """Deterministic research-only executor for the safe declarative DSL.

    No strategy spec contains an import path, callable, network target, broker
    reference, OMS reference or risk-policy mutation. Strategies emit only
    ResearchSignal. `initial_stop_pct` remains research metadata, not broker-side
    protection or execution authority.
    """

    def __init__(self, spec: StrategySpec) -> None:
        self.spec = spec

    @property
    def strategy_id(self) -> str:
        return self.spec.strategy_id

    @property
    def strategy_version(self) -> str:
        return self.spec.strategy_version

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]:
        payload = dict(self.spec.parameters)
        payload["initial_stop_pct"] = str(self.spec.initial_stop_pct)
        payload["spec_hash"] = self.spec.canonical_hash
        return payload

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        handler = getattr(self, f"_on_{self.spec.kind}")
        target_and_reason = handler(context)
        if target_and_reason is None:
            return None
        target, action = target_and_reason
        return self._signal_for_target(context, target=target, action=action)

    def _signal_for_target(
        self,
        context: StrategyContext,
        *,
        target: Decimal,
        action: str,
    ) -> ResearchSignal | None:
        delta = target - context.current_position_quantity
        if delta == 0:
            return None
        signal_key = (
            f"{self.spec.canonical_hash}:{context.symbol}:"
            f"{context.current_bar.ended_at.isoformat()}:{action}"
        )
        signal_id = f"dsl:{sha256(signal_key.encode('utf-8')).hexdigest()[:24]}"
        return ResearchSignal(
            signal_id=signal_id,
            symbol=context.symbol,
            generated_at=context.current_bar.ended_at,
            quantity_delta=delta,
            reason=(
                f"{self.spec.kind}:{action};"
                f"initial_stop_pct={self.spec.initial_stop_pct}"
            ),
        )

    def _quantity_and_mode(self) -> tuple[Decimal, str]:
        params = dict(self.spec.parameters)
        return (
            _validate_quantity(params["order_quantity"]),
            _validate_position_mode(params["position_mode"]),
        )

    def _directional_target(self, direction: int) -> Decimal:
        quantity, mode = self._quantity_and_mode()
        if direction > 0:
            return quantity
        if direction < 0 and mode == "long_short":
            return -quantity
        return Decimal("0")

    def _on_moving_average_cross(
        self, context: StrategyContext
    ) -> tuple[Decimal, str] | None:
        params = dict(self.spec.parameters)
        short_window = int(params["short_window"])
        long_window = int(params["long_window"])
        if len(context.history) < long_window + 1:
            return None

        closes = [bar.close for bar in context.history]
        previous_fast = (
            sum(closes[-short_window - 1 : -1], Decimal("0"))
            / Decimal(short_window)
        )
        current_fast = (
            sum(closes[-short_window:], Decimal("0")) / Decimal(short_window)
        )
        previous_slow = (
            sum(closes[-long_window - 1 : -1], Decimal("0"))
            / Decimal(long_window)
        )
        current_slow = (
            sum(closes[-long_window:], Decimal("0")) / Decimal(long_window)
        )

        crossed_up = previous_fast <= previous_slow and current_fast > current_slow
        crossed_down = previous_fast >= previous_slow and current_fast < current_slow
        if crossed_up:
            return self._directional_target(1), "cross-up"
        if crossed_down:
            return self._directional_target(-1), "cross-down"
        return None

    def _on_trend_ema_atr(
        self, context: StrategyContext
    ) -> tuple[Decimal, str] | None:
        params = dict(self.spec.parameters)
        fast_span = int(params["fast_span"])
        slow_span = int(params["slow_span"])
        atr_window = int(params["atr_window"])
        required = max(slow_span + 1, atr_window + 1)
        if len(context.history) < required:
            return None

        closes = [bar.close for bar in context.history]
        previous_fast = _ema(closes[:-1], fast_span)
        previous_slow = _ema(closes[:-1], slow_span)
        current_fast = _ema(closes, fast_span)
        current_slow = _ema(closes, slow_span)
        atr_pct = _atr(context.history, atr_window) / context.current_bar.close
        min_atr_pct = _decimal(params["min_atr_pct"], name="min_atr_pct")
        if atr_pct < min_atr_pct:
            return self._directional_target(0), "atr-filter-flat"

        if previous_fast <= previous_slow and current_fast > current_slow:
            return self._directional_target(1), "ema-cross-up"
        if previous_fast >= previous_slow and current_fast < current_slow:
            return self._directional_target(-1), "ema-cross-down"
        return None

    def _on_time_series_momentum(
        self, context: StrategyContext
    ) -> tuple[Decimal, str] | None:
        params = dict(self.spec.parameters)
        fast = int(params["fast_horizon"])
        slow = int(params["slow_horizon"])
        if len(context.history) < slow + 1:
            return None

        closes = [bar.close for bar in context.history]
        fast_return = closes[-1] / closes[-fast - 1] - Decimal("1")
        slow_return = closes[-1] / closes[-slow - 1] - Decimal("1")
        score = (fast_return + slow_return) / Decimal("2")
        threshold = _decimal(params["threshold"], name="threshold")
        if score > threshold:
            return self._directional_target(1), "momentum-up"
        if score < -threshold:
            return self._directional_target(-1), "momentum-down"
        return self._directional_target(0), "momentum-neutral"

    def _on_mean_reversion_zscore(
        self, context: StrategyContext
    ) -> tuple[Decimal, str] | None:
        params = dict(self.spec.parameters)
        lookback = int(params["lookback"])
        if len(context.history) < lookback:
            return None

        closes = [bar.close for bar in context.history[-lookback:]]
        mean = sum(closes, Decimal("0")) / Decimal(lookback)
        variance = (
            sum(((value - mean) * (value - mean) for value in closes), Decimal("0"))
            / Decimal(lookback)
        )
        if variance == 0:
            return self._directional_target(0), "zscore-zero-vol"
        std = variance.sqrt()
        zscore = (closes[-1] - mean) / std
        entry_z = _decimal(params["entry_z"], name="entry_z")
        exit_z = _decimal(params["exit_z"], name="exit_z")

        if zscore <= -entry_z:
            return self._directional_target(1), "zscore-low"
        if zscore >= entry_z:
            return self._directional_target(-1), "zscore-high"
        if abs(zscore) <= exit_z:
            return self._directional_target(0), "zscore-exit"
        return None

    def _on_donchian_breakout(
        self, context: StrategyContext
    ) -> tuple[Decimal, str] | None:
        params = dict(self.spec.parameters)
        lookback = int(params["lookback"])
        atr_window = int(params["atr_window"])
        required = max(lookback + 1, atr_window + 1)
        if len(context.history) < required:
            return None

        prior = context.history[-lookback - 1 : -1]
        upper = max(bar.high for bar in prior)
        lower = min(bar.low for bar in prior)
        atr_pct = _atr(context.history, atr_window) / context.current_bar.close
        min_atr_pct = _decimal(params["min_atr_pct"], name="min_atr_pct")
        if atr_pct < min_atr_pct:
            return self._directional_target(0), "atr-filter-flat"

        close = context.current_bar.close
        if close > upper:
            return self._directional_target(1), "donchian-up"
        if close < lower:
            return self._directional_target(-1), "donchian-down"
        return None

    def _on_volatility_regime(
        self, context: StrategyContext
    ) -> tuple[Decimal, str] | None:
        params = dict(self.spec.parameters)
        short_window = int(params["short_vol_window"])
        long_window = int(params["long_vol_window"])
        trend_window = int(params["trend_window"])
        required = max(long_window + 1, trend_window)
        if len(context.history) < required:
            return None

        closes = [bar.close for bar in context.history]
        returns = [
            closes[index] / closes[index - 1] - Decimal("1")
            for index in range(1, len(closes))
        ]
        short_vol = _population_std(returns[-short_window:])
        long_vol = _population_std(returns[-long_window:])
        if long_vol == 0:
            return self._directional_target(0), "regime-zero-vol"

        ratio = short_vol / long_vol
        threshold = _decimal(
            params["vol_ratio_threshold"], name="vol_ratio_threshold"
        )
        if ratio < threshold:
            return self._directional_target(0), "regime-calm-flat"

        trend_slice = closes[-trend_window:]
        trend_mean = sum(trend_slice, Decimal("0")) / Decimal(trend_window)
        if closes[-1] > trend_mean:
            return self._directional_target(1), "regime-active-up"
        if closes[-1] < trend_mean:
            return self._directional_target(-1), "regime-active-down"
        return self._directional_target(0), "regime-active-neutral"


class MovingAverageCrossStrategy(SafeDeclarativeStrategy):
    """Backward-compatible concrete strategy for the original R1 DSL kind."""

    def __init__(self, spec: StrategySpec) -> None:
        if spec.kind != "moving_average_cross":
            raise InvalidStrategySpec(
                "MovingAverageCrossStrategy requires moving_average_cross spec"
            )
        super().__init__(spec)


def _ema(values: list[Decimal], span: int) -> Decimal:
    if not values:
        raise InvalidStrategySpec("EMA requires at least one value")
    alpha = Decimal("2") / Decimal(span + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (Decimal("1") - alpha) * result
    return result


def _true_range(current: Bar, previous_close: Decimal) -> Decimal:
    return max(
        current.high - current.low,
        abs(current.high - previous_close),
        abs(current.low - previous_close),
    )


def _atr(history: tuple[Bar, ...], window: int) -> Decimal:
    if len(history) < window + 1:
        raise InvalidStrategySpec("ATR history is insufficient")
    selected = history[-window:]
    previous = history[-window - 1].close
    ranges: list[Decimal] = []
    for bar in selected:
        ranges.append(_true_range(bar, previous))
        previous = bar.close
    return sum(ranges, Decimal("0")) / Decimal(window)


def _population_std(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = (
        sum(((value - mean) * (value - mean) for value in values), Decimal("0"))
        / Decimal(len(values))
    )
    return variance.sqrt()
