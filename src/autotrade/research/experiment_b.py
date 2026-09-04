from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from itertools import product
import json
from math import prod
from typing import Mapping, Sequence

from .strategy import ResearchSignal, ResearchStrategy, StrategyContext


Primitive = str | int | float | bool
_POSITION_MODES = {"long_flat", "long_short"}


class ExperimentBSpecError(ValueError):
    pass


class ExperimentBSpaceError(ValueError):
    pass


def _hash_payload(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _canonical_value(value: Primitive) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ExperimentBSpaceError("search-space values must be finite JSON primitives") from exc


def _ordered_dimensions(
    dimensions: Mapping[str, Sequence[Primitive]],
) -> dict[str, list[Primitive]]:
    return {
        name: sorted(dimensions[name], key=_canonical_value)
        for name in sorted(dimensions)
    }


def _decimal(value: object, *, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ExperimentBSpecError(f"{name} must be decimal-compatible")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExperimentBSpecError(f"{name} must be decimal-compatible") from exc
    if not result.is_finite():
        raise ExperimentBSpecError(f"{name} must be finite")
    return result


def _integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExperimentBSpecError(f"{name} must be an integer")
    return value


def _string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentBSpecError(f"{name} must be a non-empty string")
    return value


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ValueError("values cannot be empty")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _population_std(values: Sequence[Decimal]) -> Decimal:
    average = _mean(values)
    variance = sum(((value - average) ** 2 for value in values), Decimal("0")) / Decimal(
        len(values)
    )
    return variance.sqrt()


def _target_signal(
    *,
    context: StrategyContext,
    target: Decimal,
    strategy_id: str,
    strategy_version: str,
    reason: str,
) -> ResearchSignal | None:
    if not target.is_finite():
        raise ValueError("target must be finite")
    delta = target - context.current_position_quantity
    if delta == 0:
        return None
    seed = (
        f"{strategy_id}:{strategy_version}:{context.symbol}:"
        f"{context.current_bar.ended_at.isoformat()}:{target}:{reason}"
    )
    return ResearchSignal(
        signal_id=f"experiment-b:{sha256(seed.encode('utf-8')).hexdigest()[:24]}",
        symbol=context.symbol,
        generated_at=context.current_bar.ended_at,
        quantity_delta=delta,
        reason=reason,
    )


def _direction_target(direction: int, quantity: Decimal, position_mode: str) -> Decimal:
    if direction > 0:
        return quantity
    if direction < 0 and position_mode == "long_short":
        return -quantity
    return Decimal("0")


@dataclass(frozen=True, slots=True)
class DualMovingAverageTrendStrategy:
    fast_bars: int
    lookback_bars: int
    band_bps: Decimal
    order_quantity: Decimal
    position_mode: str = "long_flat"
    strategy_id: str = "b-dma-trend"
    strategy_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.fast_bars < 2:
            raise ValueError("fast_bars must be >= 2")
        if self.lookback_bars <= self.fast_bars:
            raise ValueError("lookback_bars must be greater than fast_bars")
        if not self.band_bps.is_finite() or self.band_bps < 0:
            raise ValueError("band_bps must be finite and >= 0")
        if not self.order_quantity.is_finite() or self.order_quantity <= 0:
            raise ValueError("order_quantity must be finite and > 0")
        if self.position_mode not in _POSITION_MODES:
            raise ValueError("invalid position_mode")

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]:
        return {
            "fast_bars": self.fast_bars,
            "lookback_bars": self.lookback_bars,
            "band_bps": str(self.band_bps),
            "order_quantity": str(self.order_quantity),
            "position_mode": self.position_mode,
        }

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        if len(context.history) < self.lookback_bars:
            return None
        closes = [bar.close for bar in context.history[-self.lookback_bars :]]
        slow = _mean(closes)
        fast = _mean(closes[-self.fast_bars :])
        band = self.band_bps / Decimal("10000")
        relative = fast / slow - Decimal("1")
        if relative > band:
            direction = 1
        elif relative < -band:
            direction = -1
        else:
            direction = 0
        target = _direction_target(direction, self.order_quantity, self.position_mode)
        return _target_signal(
            context=context,
            target=target,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            reason=f"dma:fast={fast};slow={slow};relative={relative}",
        )


@dataclass(frozen=True, slots=True)
class ATRImpulseBreakoutStrategy:
    lookback_bars: int
    atr_multiplier: Decimal
    exit_atr_multiplier: Decimal
    order_quantity: Decimal
    position_mode: str = "long_flat"
    strategy_id: str = "b-atr-impulse"
    strategy_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.lookback_bars < 3:
            raise ValueError("lookback_bars must be >= 3")
        if not self.atr_multiplier.is_finite() or self.atr_multiplier <= 0:
            raise ValueError("atr_multiplier must be finite and > 0")
        if not self.exit_atr_multiplier.is_finite() or self.exit_atr_multiplier < 0:
            raise ValueError("exit_atr_multiplier must be finite and >= 0")
        if self.exit_atr_multiplier >= self.atr_multiplier:
            raise ValueError("exit_atr_multiplier must be < atr_multiplier")
        if not self.order_quantity.is_finite() or self.order_quantity <= 0:
            raise ValueError("order_quantity must be finite and > 0")
        if self.position_mode not in _POSITION_MODES:
            raise ValueError("invalid position_mode")

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]:
        return {
            "lookback_bars": self.lookback_bars,
            "atr_multiplier": str(self.atr_multiplier),
            "exit_atr_multiplier": str(self.exit_atr_multiplier),
            "order_quantity": str(self.order_quantity),
            "position_mode": self.position_mode,
        }

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        if len(context.history) < self.lookback_bars + 2:
            return None
        reference = context.history[-self.lookback_bars - 2 : -1]
        true_ranges: list[Decimal] = []
        for previous, bar in zip(reference, reference[1:], strict=False):
            true_ranges.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous.close),
                    abs(bar.low - previous.close),
                )
            )
        atr = _mean(true_ranges[-self.lookback_bars :])
        prior_close = context.history[-2].close
        current = context.current_bar.close
        change = current - prior_close
        upper = self.atr_multiplier * atr
        exit_band = self.exit_atr_multiplier * atr
        if change > upper:
            target = self.order_quantity
            action = "up-impulse"
        elif change < -upper:
            target = (
                -self.order_quantity
                if self.position_mode == "long_short"
                else Decimal("0")
            )
            action = "down-impulse"
        elif abs(change) <= exit_band:
            target = Decimal("0")
            action = "impulse-decay"
        else:
            return None
        return _target_signal(
            context=context,
            target=target,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            reason=f"atr-impulse:{action};change={change};atr={atr}",
        )


@dataclass(frozen=True, slots=True)
class TrendPullbackStrategy:
    lookback_bars: int
    pullback_bars: int
    min_trend_return: Decimal
    entry_z: Decimal
    exit_z: Decimal
    order_quantity: Decimal
    position_mode: str = "long_flat"
    strategy_id: str = "b-trend-pullback"
    strategy_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.lookback_bars < 4:
            raise ValueError("lookback_bars must be >= 4")
        if self.pullback_bars < 3 or self.pullback_bars >= self.lookback_bars:
            raise ValueError("pullback_bars must be >=3 and < lookback_bars")
        if not self.min_trend_return.is_finite() or self.min_trend_return < 0:
            raise ValueError("min_trend_return must be finite and >= 0")
        if not self.entry_z.is_finite() or self.entry_z <= 0:
            raise ValueError("entry_z must be finite and > 0")
        if not self.exit_z.is_finite() or self.exit_z < 0 or self.exit_z >= self.entry_z:
            raise ValueError("exit_z must satisfy 0 <= exit_z < entry_z")
        if not self.order_quantity.is_finite() or self.order_quantity <= 0:
            raise ValueError("order_quantity must be finite and > 0")
        if self.position_mode not in _POSITION_MODES:
            raise ValueError("invalid position_mode")

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]:
        return {
            "lookback_bars": self.lookback_bars,
            "pullback_bars": self.pullback_bars,
            "min_trend_return": str(self.min_trend_return),
            "entry_z": str(self.entry_z),
            "exit_z": str(self.exit_z),
            "order_quantity": str(self.order_quantity),
            "position_mode": self.position_mode,
        }

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        if len(context.history) < self.lookback_bars + 1:
            return None
        current = context.current_bar.close
        trend_reference = context.history[-self.lookback_bars - 1].close
        trend_return = current / trend_reference - Decimal("1")
        prior = [bar.close for bar in context.history[-self.pullback_bars - 1 : -1]]
        standard_deviation = _population_std(prior)
        if standard_deviation == 0:
            return None
        z_score = (current - _mean(prior)) / standard_deviation

        if trend_return >= self.min_trend_return and z_score <= -self.entry_z:
            target = self.order_quantity
            action = "trend-pullback-entry"
        elif context.current_position_quantity > 0 and (
            z_score >= self.exit_z or trend_return <= Decimal("0")
        ):
            target = Decimal("0")
            action = "trend-pullback-exit"
        elif (
            self.position_mode == "long_short"
            and trend_return <= -self.min_trend_return
            and z_score >= self.entry_z
        ):
            target = -self.order_quantity
            action = "downtrend-rally-entry"
        elif context.current_position_quantity < 0 and self.position_mode == "long_short" and (
            z_score <= -self.exit_z or trend_return >= Decimal("0")
        ):
            target = Decimal("0")
            action = "downtrend-rally-exit"
        else:
            return None
        return _target_signal(
            context=context,
            target=target,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            reason=f"trend-pullback:{action};trend={trend_return};z={z_score}",
        )


_ALLOWED_PARAMETERS: dict[str, frozenset[str]] = {
    "dual_moving_average_trend": frozenset(
        {"fast_bars", "lookback_bars", "band_bps", "order_quantity", "position_mode"}
    ),
    "atr_impulse_breakout": frozenset(
        {
            "lookback_bars",
            "atr_multiplier",
            "exit_atr_multiplier",
            "order_quantity",
            "position_mode",
        }
    ),
    "trend_pullback": frozenset(
        {
            "lookback_bars",
            "pullback_bars",
            "min_trend_return",
            "entry_z",
            "exit_z",
            "order_quantity",
            "position_mode",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class ExperimentBStrategySpec:
    strategy_id: str
    strategy_version: str
    kind: str
    parameters: Mapping[str, Primitive]

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ExperimentBSpecError("strategy identity is required")
        if self.kind not in _ALLOWED_PARAMETERS:
            raise ExperimentBSpecError(f"unsupported Experiment B kind: {self.kind}")
        params = dict(self.parameters)
        expected = _ALLOWED_PARAMETERS[self.kind]
        missing = expected - set(params)
        unknown = set(params) - expected
        if missing or unknown:
            raise ExperimentBSpecError(
                f"invalid parameters; missing={sorted(missing)} unknown={sorted(unknown)}"
            )
        self._validate(params)

    def _validate(self, params: Mapping[str, Primitive]) -> None:
        position_mode = _string(params["position_mode"], name="position_mode")
        if position_mode not in _POSITION_MODES:
            raise ExperimentBSpecError("position_mode must be long_flat or long_short")
        quantity = _decimal(params["order_quantity"], name="order_quantity")
        if quantity <= 0:
            raise ExperimentBSpecError("order_quantity must be > 0")
        lookback = _integer(params["lookback_bars"], name="lookback_bars")
        if self.kind == "dual_moving_average_trend":
            fast = _integer(params["fast_bars"], name="fast_bars")
            if fast < 2 or lookback <= fast:
                raise ExperimentBSpecError("DMA requires 2 <= fast_bars < lookback_bars")
            if _decimal(params["band_bps"], name="band_bps") < 0:
                raise ExperimentBSpecError("band_bps must be >= 0")
        elif self.kind == "atr_impulse_breakout":
            entry = _decimal(params["atr_multiplier"], name="atr_multiplier")
            exit_value = _decimal(params["exit_atr_multiplier"], name="exit_atr_multiplier")
            if lookback < 3 or entry <= 0 or exit_value < 0 or exit_value >= entry:
                raise ExperimentBSpecError("invalid ATR impulse parameters")
        elif self.kind == "trend_pullback":
            pullback = _integer(params["pullback_bars"], name="pullback_bars")
            entry_z = _decimal(params["entry_z"], name="entry_z")
            exit_z = _decimal(params["exit_z"], name="exit_z")
            trend = _decimal(params["min_trend_return"], name="min_trend_return")
            if lookback < 4 or pullback < 3 or pullback >= lookback:
                raise ExperimentBSpecError("invalid trend-pullback windows")
            if trend < 0 or entry_z <= 0 or exit_z < 0 or exit_z >= entry_z:
                raise ExperimentBSpecError("invalid trend-pullback thresholds")

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "kind": self.kind,
            "parameters": dict(self.parameters),
        }

    @property
    def canonical_hash(self) -> str:
        return _hash_payload(self.canonical_payload)

    def build(self) -> ResearchStrategy:
        p = dict(self.parameters)
        common = {
            "order_quantity": _decimal(p["order_quantity"], name="order_quantity"),
            "position_mode": _string(p["position_mode"], name="position_mode"),
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
        }
        if self.kind == "dual_moving_average_trend":
            return DualMovingAverageTrendStrategy(
                fast_bars=_integer(p["fast_bars"], name="fast_bars"),
                lookback_bars=_integer(p["lookback_bars"], name="lookback_bars"),
                band_bps=_decimal(p["band_bps"], name="band_bps"),
                **common,
            )
        if self.kind == "atr_impulse_breakout":
            return ATRImpulseBreakoutStrategy(
                lookback_bars=_integer(p["lookback_bars"], name="lookback_bars"),
                atr_multiplier=_decimal(p["atr_multiplier"], name="atr_multiplier"),
                exit_atr_multiplier=_decimal(
                    p["exit_atr_multiplier"], name="exit_atr_multiplier"
                ),
                **common,
            )
        if self.kind == "trend_pullback":
            return TrendPullbackStrategy(
                lookback_bars=_integer(p["lookback_bars"], name="lookback_bars"),
                pullback_bars=_integer(p["pullback_bars"], name="pullback_bars"),
                min_trend_return=_decimal(
                    p["min_trend_return"], name="min_trend_return"
                ),
                entry_z=_decimal(p["entry_z"], name="entry_z"),
                exit_z=_decimal(p["exit_z"], name="exit_z"),
                **common,
            )
        raise ExperimentBSpecError(f"unsupported Experiment B kind: {self.kind}")


@dataclass(frozen=True, slots=True)
class ExperimentBSearchSpace:
    family_id: str
    strategy_version: str
    kind: str
    dimensions: Mapping[str, Sequence[Primitive]]
    max_candidates: int = 64

    def __post_init__(self) -> None:
        if not self.family_id.strip() or not self.strategy_version.strip():
            raise ExperimentBSpaceError("family identity is required")
        if self.max_candidates <= 0 or not self.dimensions:
            raise ExperimentBSpaceError("search space must be bounded and non-empty")
        for name, values in self.dimensions.items():
            if not name.strip() or isinstance(values, (str, bytes)) or not values:
                raise ExperimentBSpaceError(f"invalid dimension: {name}")
            encoded = tuple(_canonical_value(value) for value in values)
            if len(encoded) != len(set(encoded)):
                raise ExperimentBSpaceError(f"dimension {name} contains duplicates")
        if self.candidate_count > self.max_candidates:
            raise ExperimentBSpaceError("candidate count exceeds max_candidates")
        self.candidates()

    @property
    def candidate_count(self) -> int:
        return prod(len(tuple(values)) for values in self.dimensions.values())

    @property
    def canonical_hash(self) -> str:
        return _hash_payload(
            {
                "family_id": self.family_id,
                "strategy_version": self.strategy_version,
                "kind": self.kind,
                "dimensions": _ordered_dimensions(self.dimensions),
                "max_candidates": self.max_candidates,
            }
        )

    def candidates(self) -> tuple[ExperimentBStrategySpec, ...]:
        ordered = _ordered_dimensions(self.dimensions)
        names = tuple(ordered)
        result: list[ExperimentBStrategySpec] = []
        for values in product(*(ordered[name] for name in names)):
            parameters = dict(zip(names, values, strict=True))
            identity = _hash_payload(
                {
                    "family_id": self.family_id,
                    "version": self.strategy_version,
                    "kind": self.kind,
                    "parameters": parameters,
                }
            )[:16]
            result.append(
                ExperimentBStrategySpec(
                    strategy_id=f"{self.family_id}-{identity}",
                    strategy_version=self.strategy_version,
                    kind=self.kind,
                    parameters=parameters,
                )
            )
        result.sort(key=lambda item: item.strategy_id)
        if len(result) != self.candidate_count:
            raise ExperimentBSpaceError("candidate accounting mismatch")
        return tuple(result)


@dataclass(frozen=True, slots=True)
class ExperimentBProgram:
    program_id: str
    spaces: tuple[ExperimentBSearchSpace, ...]
    max_total_candidates: int = 96

    def __post_init__(self) -> None:
        if not self.program_id.strip() or not self.spaces:
            raise ExperimentBSpaceError("program identity and spaces are required")
        family_ids = tuple(space.family_id for space in self.spaces)
        if len(family_ids) != len(set(family_ids)):
            raise ExperimentBSpaceError("family ids must be unique")
        if self.candidate_count > self.max_total_candidates:
            raise ExperimentBSpaceError("program exceeds max_total_candidates")
        candidates = self.candidates()
        if len({item.strategy_id for item in candidates}) != len(candidates):
            raise ExperimentBSpaceError("strategy identifiers must be unique")

    @property
    def candidate_count(self) -> int:
        return sum(space.candidate_count for space in self.spaces)

    @property
    def canonical_hash(self) -> str:
        return _hash_payload(
            {
                "program_id": self.program_id,
                "space_hashes": sorted(space.canonical_hash for space in self.spaces),
                "max_total_candidates": self.max_total_candidates,
            }
        )

    def candidates(self) -> tuple[ExperimentBStrategySpec, ...]:
        candidates = [candidate for space in self.spaces for candidate in space.candidates()]
        candidates.sort(key=lambda item: item.strategy_id)
        return tuple(candidates)

    def trial_id_for(self, candidate: ExperimentBStrategySpec) -> str:
        known = {item.canonical_hash for item in self.candidates()}
        if candidate.canonical_hash not in known:
            raise ExperimentBSpaceError("candidate is outside frozen Experiment B program")
        suffix = _hash_payload(
            {
                "program_hash": self.canonical_hash,
                "candidate_hash": candidate.canonical_hash,
            }
        )[:20]
        return f"{self.program_id}-{suffix}"

    @property
    def expected_trial_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.trial_id_for(item) for item in self.candidates()))


def build_experiment_b_program(*, quantity: Decimal) -> ExperimentBProgram:
    quantity_text = str(quantity)
    return ExperimentBProgram(
        program_id="r7-experiment-b",
        spaces=(
            ExperimentBSearchSpace(
                family_id="b-dma",
                strategy_version="1.0.0",
                kind="dual_moving_average_trend",
                dimensions={
                    "fast_bars": (12, 24),
                    "lookback_bars": (72, 168),
                    "band_bps": ("0", "10"),
                    "order_quantity": (quantity_text,),
                    "position_mode": ("long_flat",),
                },
                max_candidates=8,
            ),
            ExperimentBSearchSpace(
                family_id="b-atr",
                strategy_version="1.0.0",
                kind="atr_impulse_breakout",
                dimensions={
                    "lookback_bars": (24, 72),
                    "atr_multiplier": ("1", "1.5", "2"),
                    "exit_atr_multiplier": ("0.25",),
                    "order_quantity": (quantity_text,),
                    "position_mode": ("long_flat",),
                },
                max_candidates=6,
            ),
            ExperimentBSearchSpace(
                family_id="b-pullback",
                strategy_version="1.0.0",
                kind="trend_pullback",
                dimensions={
                    "lookback_bars": (72, 168),
                    "pullback_bars": (12, 24),
                    "min_trend_return": ("0.01", "0.03"),
                    "entry_z": ("1", "1.5"),
                    "exit_z": ("0.25",),
                    "order_quantity": (quantity_text,),
                    "position_mode": ("long_flat",),
                },
                max_candidates=16,
            ),
        ),
        max_total_candidates=30,
    )


__all__ = [
    "ATRImpulseBreakoutStrategy",
    "DualMovingAverageTrendStrategy",
    "ExperimentBProgram",
    "ExperimentBSearchSpace",
    "ExperimentBSpecError",
    "ExperimentBSpaceError",
    "ExperimentBStrategySpec",
    "TrendPullbackStrategy",
    "build_experiment_b_program",
]
