from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from math import prod
from itertools import product
from typing import Mapping, Sequence

from .strategy import ResearchSignal, ResearchStrategy, StrategyContext


Primitive = str | int | float | bool
_POSITION_MODES = {"long_flat", "long_short"}


class ExperimentCError(ValueError):
    pass


def _hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ExperimentCError(f"{name} must be decimal-compatible")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExperimentCError(f"{name} must be decimal-compatible") from exc
    if not result.is_finite():
        raise ExperimentCError(f"{name} must be finite")
    return result


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExperimentCError(f"{name} must be integer")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentCError(f"{name} must be non-empty string")
    return value


def _canonical(value: Primitive) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ExperimentCError("dimensions must contain finite JSON primitives") from exc


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


def _ema(values: Sequence[Decimal], span: int) -> Decimal:
    if span < 2 or not values:
        raise ValueError("EMA requires non-empty values and span >=2")
    alpha = Decimal("2") / Decimal(span + 1)
    current = values[0]
    for value in values[1:]:
        current = alpha * value + (Decimal("1") - alpha) * current
    return current


def _returns(closes: Sequence[Decimal]) -> tuple[Decimal, ...]:
    result: list[Decimal] = []
    for prior, current in zip(closes, closes[1:], strict=False):
        if prior <= 0:
            raise ValueError("close must be positive")
        result.append(current / prior - Decimal("1"))
    return tuple(result)


def _target_signal(
    *,
    context: StrategyContext,
    target: Decimal,
    strategy_id: str,
    strategy_version: str,
    reason: str,
) -> ResearchSignal | None:
    delta = target - context.current_position_quantity
    if delta == 0:
        return None
    seed = (
        f"{strategy_id}:{strategy_version}:{context.symbol}:"
        f"{context.current_bar.ended_at.isoformat()}:{target}:{reason}"
    )
    return ResearchSignal(
        signal_id=f"experiment-c:{sha256(seed.encode('utf-8')).hexdigest()[:24]}",
        symbol=context.symbol,
        generated_at=context.current_bar.ended_at,
        quantity_delta=delta,
        reason=reason,
    )


def _direction_target(direction: int, quantity: Decimal, mode: str) -> Decimal:
    if direction > 0:
        return quantity
    if direction < 0 and mode == "long_short":
        return -quantity
    return Decimal("0")


@dataclass(frozen=True, slots=True)
class NormalizedEWMACStrategy:
    fast_bars: int
    slow_bars: int
    volatility_window_bars: int
    entry_score: Decimal
    exit_score: Decimal
    order_quantity: Decimal
    position_mode: str = "long_flat"
    strategy_id: str = "c-ewmac"
    strategy_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.fast_bars < 2 or self.slow_bars <= self.fast_bars:
            raise ValueError("requires 2 <= fast_bars < slow_bars")
        if self.volatility_window_bars < 3:
            raise ValueError("volatility_window_bars must be >=3")
        if not self.entry_score.is_finite() or self.entry_score <= 0:
            raise ValueError("entry_score must be finite and >0")
        if not self.exit_score.is_finite() or not 0 <= self.exit_score < self.entry_score:
            raise ValueError("exit_score must satisfy 0 <= exit < entry")
        if not self.order_quantity.is_finite() or self.order_quantity <= 0:
            raise ValueError("order_quantity must be finite and >0")
        if self.position_mode not in _POSITION_MODES:
            raise ValueError("invalid position_mode")

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]:
        return {
            "fast_bars": self.fast_bars,
            "slow_bars": self.slow_bars,
            "volatility_window_bars": self.volatility_window_bars,
            "entry_score": str(self.entry_score),
            "exit_score": str(self.exit_score),
            "order_quantity": str(self.order_quantity),
            "position_mode": self.position_mode,
        }

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        required = max(self.slow_bars, self.volatility_window_bars + 1)
        if len(context.history) < required:
            return None
        closes = [bar.close for bar in context.history[-required:]]
        fast = _ema(closes[-self.slow_bars :], self.fast_bars)
        slow = _ema(closes[-self.slow_bars :], self.slow_bars)
        recent_returns = _returns(closes[-self.volatility_window_bars - 1 :])
        volatility = _population_std(recent_returns)
        current = closes[-1]
        if volatility == 0 or current <= 0:
            return None
        score = ((fast - slow) / current) / volatility

        current_position = context.current_position_quantity
        if score >= self.entry_score:
            target = self.order_quantity
            action = "long-entry"
        elif self.position_mode == "long_short" and score <= -self.entry_score:
            target = -self.order_quantity
            action = "short-entry"
        elif current_position > 0 and score <= self.exit_score:
            target = Decimal("0")
            action = "long-exit"
        elif current_position < 0 and score >= -self.exit_score:
            target = Decimal("0")
            action = "short-exit"
        else:
            return None
        return _target_signal(
            context=context,
            target=target,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            reason=f"normalized-ewmac:{action};score={score};vol={volatility}",
        )


@dataclass(frozen=True, slots=True)
class EfficiencyRatioTrendStrategy:
    lookback_bars: int
    entry_efficiency: Decimal
    exit_efficiency: Decimal
    order_quantity: Decimal
    position_mode: str = "long_flat"
    strategy_id: str = "c-efficiency"
    strategy_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.lookback_bars < 3:
            raise ValueError("lookback_bars must be >=3")
        if not self.entry_efficiency.is_finite() or not 0 < self.entry_efficiency <= 1:
            raise ValueError("entry_efficiency must be in (0,1]")
        if not self.exit_efficiency.is_finite() or not 0 <= self.exit_efficiency < self.entry_efficiency:
            raise ValueError("exit_efficiency must satisfy 0 <= exit < entry")
        if not self.order_quantity.is_finite() or self.order_quantity <= 0:
            raise ValueError("order_quantity must be finite and >0")
        if self.position_mode not in _POSITION_MODES:
            raise ValueError("invalid position_mode")

    @property
    def parameters(self) -> Mapping[str, str | int | float | bool]:
        return {
            "lookback_bars": self.lookback_bars,
            "entry_efficiency": str(self.entry_efficiency),
            "exit_efficiency": str(self.exit_efficiency),
            "order_quantity": str(self.order_quantity),
            "position_mode": self.position_mode,
        }

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        if len(context.history) < self.lookback_bars + 1:
            return None
        closes = [bar.close for bar in context.history[-self.lookback_bars - 1 :]]
        net_change = closes[-1] - closes[0]
        path = sum(
            (abs(current - prior) for prior, current in zip(closes, closes[1:], strict=False)),
            Decimal("0"),
        )
        if path == 0:
            return None
        efficiency = abs(net_change) / path
        direction = 1 if net_change > 0 else -1 if net_change < 0 else 0
        current_position = context.current_position_quantity

        if efficiency >= self.entry_efficiency and direction > 0:
            target = self.order_quantity
            action = "positive-efficient-trend"
        elif (
            efficiency >= self.entry_efficiency
            and direction < 0
            and self.position_mode == "long_short"
        ):
            target = -self.order_quantity
            action = "negative-efficient-trend"
        elif current_position != 0 and efficiency <= self.exit_efficiency:
            target = Decimal("0")
            action = "efficiency-decay"
        elif current_position > 0 and direction < 0:
            target = Decimal("0")
            action = "direction-reversal"
        elif current_position < 0 and direction > 0:
            target = Decimal("0")
            action = "direction-reversal"
        else:
            return None
        return _target_signal(
            context=context,
            target=target,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            reason=f"efficiency-trend:{action};efficiency={efficiency};change={net_change}",
        )


_ALLOWED: dict[str, frozenset[str]] = {
    "normalized_ewmac": frozenset(
        {
            "fast_bars",
            "slow_bars",
            "volatility_window_bars",
            "entry_score",
            "exit_score",
            "order_quantity",
            "position_mode",
        }
    ),
    "efficiency_ratio_trend": frozenset(
        {
            "lookback_bars",
            "entry_efficiency",
            "exit_efficiency",
            "order_quantity",
            "position_mode",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class ExperimentCStrategySpec:
    strategy_id: str
    strategy_version: str
    kind: str
    parameters: Mapping[str, Primitive]

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ExperimentCError("strategy identity is required")
        if self.kind not in _ALLOWED:
            raise ExperimentCError(f"unsupported Experiment C kind: {self.kind}")
        params = dict(self.parameters)
        expected = _ALLOWED[self.kind]
        missing = expected - set(params)
        unknown = set(params) - expected
        if missing or unknown:
            raise ExperimentCError(
                f"invalid parameters; missing={sorted(missing)} unknown={sorted(unknown)}"
            )
        self.build()

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
        return _hash(self.canonical_payload)

    def build(self) -> ResearchStrategy:
        p = dict(self.parameters)
        common = {
            "order_quantity": _decimal(p["order_quantity"], "order_quantity"),
            "position_mode": _string(p["position_mode"], "position_mode"),
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
        }
        if self.kind == "normalized_ewmac":
            return NormalizedEWMACStrategy(
                fast_bars=_integer(p["fast_bars"], "fast_bars"),
                slow_bars=_integer(p["slow_bars"], "slow_bars"),
                volatility_window_bars=_integer(
                    p["volatility_window_bars"], "volatility_window_bars"
                ),
                entry_score=_decimal(p["entry_score"], "entry_score"),
                exit_score=_decimal(p["exit_score"], "exit_score"),
                **common,
            )
        if self.kind == "efficiency_ratio_trend":
            return EfficiencyRatioTrendStrategy(
                lookback_bars=_integer(p["lookback_bars"], "lookback_bars"),
                entry_efficiency=_decimal(
                    p["entry_efficiency"], "entry_efficiency"
                ),
                exit_efficiency=_decimal(p["exit_efficiency"], "exit_efficiency"),
                **common,
            )
        raise ExperimentCError(f"unsupported Experiment C kind: {self.kind}")


@dataclass(frozen=True, slots=True)
class ExperimentCSearchSpace:
    family_id: str
    strategy_version: str
    kind: str
    dimensions: Mapping[str, Sequence[Primitive]]
    max_candidates: int

    def __post_init__(self) -> None:
        if not self.family_id.strip() or not self.strategy_version.strip():
            raise ExperimentCError("family identity is required")
        if self.max_candidates <= 0 or not self.dimensions:
            raise ExperimentCError("bounded non-empty dimensions are required")
        for name, values in self.dimensions.items():
            if not name.strip() or isinstance(values, (str, bytes)) or not values:
                raise ExperimentCError(f"invalid dimension: {name}")
            encoded = tuple(_canonical(value) for value in values)
            if len(encoded) != len(set(encoded)):
                raise ExperimentCError(f"dimension {name} contains duplicates")
        if self.candidate_count > self.max_candidates:
            raise ExperimentCError("candidate count exceeds max_candidates")
        self.candidates()

    @property
    def candidate_count(self) -> int:
        return prod(len(tuple(values)) for values in self.dimensions.values())

    @property
    def canonical_hash(self) -> str:
        ordered = {
            name: sorted(self.dimensions[name], key=_canonical)
            for name in sorted(self.dimensions)
        }
        return _hash(
            {
                "family_id": self.family_id,
                "strategy_version": self.strategy_version,
                "kind": self.kind,
                "dimensions": ordered,
                "max_candidates": self.max_candidates,
            }
        )

    def candidates(self) -> tuple[ExperimentCStrategySpec, ...]:
        ordered = {
            name: sorted(self.dimensions[name], key=_canonical)
            for name in sorted(self.dimensions)
        }
        names = tuple(ordered)
        result: list[ExperimentCStrategySpec] = []
        for values in product(*(ordered[name] for name in names)):
            parameters = dict(zip(names, values, strict=True))
            identity = _hash(
                {
                    "family_id": self.family_id,
                    "version": self.strategy_version,
                    "kind": self.kind,
                    "parameters": parameters,
                }
            )[:16]
            result.append(
                ExperimentCStrategySpec(
                    strategy_id=f"{self.family_id}-{identity}",
                    strategy_version=self.strategy_version,
                    kind=self.kind,
                    parameters=parameters,
                )
            )
        result.sort(key=lambda item: item.strategy_id)
        if len(result) != self.candidate_count:
            raise ExperimentCError("candidate accounting mismatch")
        return tuple(result)


@dataclass(frozen=True, slots=True)
class ExperimentCProgram:
    program_id: str
    spaces: tuple[ExperimentCSearchSpace, ...]
    max_total_candidates: int = 12

    def __post_init__(self) -> None:
        if not self.program_id.strip() or not self.spaces:
            raise ExperimentCError("program identity and spaces are required")
        if len({space.family_id for space in self.spaces}) != len(self.spaces):
            raise ExperimentCError("family ids must be unique")
        if self.candidate_count > self.max_total_candidates:
            raise ExperimentCError("program exceeds max_total_candidates")
        candidates = self.candidates()
        if len({item.strategy_id for item in candidates}) != len(candidates):
            raise ExperimentCError("strategy ids must be unique")

    @property
    def candidate_count(self) -> int:
        return sum(space.candidate_count for space in self.spaces)

    @property
    def canonical_hash(self) -> str:
        return _hash(
            {
                "program_id": self.program_id,
                "space_hashes": sorted(space.canonical_hash for space in self.spaces),
                "max_total_candidates": self.max_total_candidates,
            }
        )

    def candidates(self) -> tuple[ExperimentCStrategySpec, ...]:
        values = [candidate for space in self.spaces for candidate in space.candidates()]
        values.sort(key=lambda item: item.strategy_id)
        return tuple(values)

    def trial_id_for(self, candidate: ExperimentCStrategySpec) -> str:
        known = {item.canonical_hash for item in self.candidates()}
        if candidate.canonical_hash not in known:
            raise ExperimentCError("candidate outside frozen program")
        suffix = _hash(
            {
                "program_hash": self.canonical_hash,
                "candidate_hash": candidate.canonical_hash,
            }
        )[:20]
        return f"{self.program_id}-{suffix}"

    @property
    def expected_trial_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.trial_id_for(item) for item in self.candidates()))


def _ewmac_space(
    *,
    family_id: str,
    fast_bars: int,
    slow_bars: int,
    quantity_text: str,
) -> ExperimentCSearchSpace:
    return ExperimentCSearchSpace(
        family_id=family_id,
        strategy_version="1.0.0",
        kind="normalized_ewmac",
        dimensions={
            "fast_bars": (fast_bars,),
            "slow_bars": (slow_bars,),
            "volatility_window_bars": (48,),
            "entry_score": ("0.5", "1.0"),
            "exit_score": ("0.10",),
            "order_quantity": (quantity_text,),
            "position_mode": ("long_flat",),
        },
        max_candidates=2,
    )


def build_experiment_c_program(*, quantity: Decimal) -> ExperimentCProgram:
    quantity_text = str(quantity)
    return ExperimentCProgram(
        program_id="r7-experiment-c",
        spaces=(
            _ewmac_space(
                family_id="c-ewmac-16-64",
                fast_bars=16,
                slow_bars=64,
                quantity_text=quantity_text,
            ),
            _ewmac_space(
                family_id="c-ewmac-32-128",
                fast_bars=32,
                slow_bars=128,
                quantity_text=quantity_text,
            ),
            _ewmac_space(
                family_id="c-ewmac-64-256",
                fast_bars=64,
                slow_bars=256,
                quantity_text=quantity_text,
            ),
            ExperimentCSearchSpace(
                family_id="c-efficiency",
                strategy_version="1.0.0",
                kind="efficiency_ratio_trend",
                dimensions={
                    "lookback_bars": (48, 168, 336),
                    "entry_efficiency": ("0.20", "0.40"),
                    "exit_efficiency": ("0.10",),
                    "order_quantity": (quantity_text,),
                    "position_mode": ("long_flat",),
                },
                max_candidates=6,
            ),
        ),
        max_total_candidates=12,
    )


__all__ = [
    "EfficiencyRatioTrendStrategy",
    "ExperimentCError",
    "ExperimentCProgram",
    "ExperimentCSearchSpace",
    "ExperimentCStrategySpec",
    "NormalizedEWMACStrategy",
    "build_experiment_c_program",
]
