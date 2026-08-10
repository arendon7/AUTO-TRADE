from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Mapping

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
_ALLOWED_MA_PARAMETERS = {
    "short_window",
    "long_window",
    "order_quantity",
    "position_mode",
}


def _decimal(value: object, *, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidStrategySpec(f"{name} must be decimal-compatible") from exc
    if not result.is_finite():
        raise InvalidStrategySpec(f"{name} must be finite")
    return result


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
        if self.kind != "moving_average_cross":
            raise InvalidStrategySpec(f"unsupported strategy kind: {self.kind}")
        if not self.initial_stop_pct.is_finite() or not Decimal("0") < self.initial_stop_pct < Decimal("1"):
            raise InvalidStrategySpec("initial_stop_pct must be finite and between 0 and 1")

        params = dict(self.parameters)
        unknown = set(params) - _ALLOWED_MA_PARAMETERS
        missing = _ALLOWED_MA_PARAMETERS - set(params)
        if unknown:
            raise InvalidStrategySpec(f"unknown strategy parameters: {sorted(unknown)}")
        if missing:
            raise InvalidStrategySpec(f"missing strategy parameters: {sorted(missing)}")

        short_window = params["short_window"]
        long_window = params["long_window"]
        if isinstance(short_window, bool) or not isinstance(short_window, int) or short_window <= 0:
            raise InvalidStrategySpec("short_window must be an integer > 0")
        if isinstance(long_window, bool) or not isinstance(long_window, int) or long_window <= 1:
            raise InvalidStrategySpec("long_window must be an integer > 1")
        if short_window >= long_window:
            raise InvalidStrategySpec("short_window must be < long_window")

        quantity = _decimal(params["order_quantity"], name="order_quantity")
        if quantity <= 0:
            raise InvalidStrategySpec("order_quantity must be > 0")

        if params["position_mode"] not in {"long_flat", "long_short"}:
            raise InvalidStrategySpec("position_mode must be long_flat or long_short")

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
            raise InvalidStrategySpec(f"unknown top-level fields: {sorted(unknown)}")
        if missing:
            raise InvalidStrategySpec(f"missing top-level fields: {sorted(missing)}")
        if not isinstance(payload["parameters"], dict):
            raise InvalidStrategySpec("parameters must be an object")
        return cls(
            strategy_id=str(payload["strategy_id"]),
            strategy_version=str(payload["strategy_version"]),
            kind=str(payload["kind"]),
            parameters=payload["parameters"],
            initial_stop_pct=_decimal(payload["initial_stop_pct"], name="initial_stop_pct"),
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

    def build(self) -> "MovingAverageCrossStrategy":
        return MovingAverageCrossStrategy(self)


class MovingAverageCrossStrategy:
    """Deterministic research-only executor for the safe declarative DSL.

    The spec contains no import path, callable, network target, broker reference,
    OMS reference or risk-policy mutation. It produces ResearchSignal only.
    `initial_stop_pct` is mandatory research metadata and is deliberately not
    interpreted as broker-side protection.
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
        params = dict(self.spec.parameters)
        short_window = int(params["short_window"])
        long_window = int(params["long_window"])
        if len(context.history) < long_window + 1:
            return None

        closes = [bar.close for bar in context.history]
        previous_fast = sum(closes[-short_window - 1 : -1], Decimal("0")) / Decimal(short_window)
        current_fast = sum(closes[-short_window:], Decimal("0")) / Decimal(short_window)
        previous_slow = sum(closes[-long_window - 1 : -1], Decimal("0")) / Decimal(long_window)
        current_slow = sum(closes[-long_window:], Decimal("0")) / Decimal(long_window)

        crossed_up = previous_fast <= previous_slow and current_fast > current_slow
        crossed_down = previous_fast >= previous_slow and current_fast < current_slow
        if not crossed_up and not crossed_down:
            return None

        order_quantity = _decimal(params["order_quantity"], name="order_quantity")
        position_mode = str(params["position_mode"])
        if crossed_up:
            target = order_quantity
            direction = "up"
        elif position_mode == "long_short":
            target = -order_quantity
            direction = "down-short"
        else:
            target = Decimal("0")
            direction = "down-flat"

        delta = target - context.current_position_quantity
        if delta == 0:
            return None

        signal_key = (
            f"{self.spec.canonical_hash}:{context.symbol}:"
            f"{context.current_bar.ended_at.isoformat()}:{direction}"
        )
        signal_id = f"dsl:{sha256(signal_key.encode('utf-8')).hexdigest()[:24]}"
        return ResearchSignal(
            signal_id=signal_id,
            symbol=context.symbol,
            generated_at=context.current_bar.ended_at,
            quantity_delta=delta,
            reason=(
                f"moving_average_cross:{direction};"
                f"initial_stop_pct={self.spec.initial_stop_pct}"
            ),
        )
