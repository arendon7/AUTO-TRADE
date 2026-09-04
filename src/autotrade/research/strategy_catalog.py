from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Mapping

from .strategy import ResearchStrategy
from .strategy_library import (
    DonchianBreakoutStrategy,
    MeanReversionZScoreStrategy,
    TimeSeriesMomentumStrategy,
    VolatilityManagedMomentumStrategy,
)


class InvalidLibraryStrategySpec(ValueError):
    pass


_ALLOWED_TOP_LEVEL = {"strategy_id", "strategy_version", "kind", "parameters"}
_PARAMETER_FIELDS: dict[str, frozenset[str]] = {
    "time_series_momentum": frozenset(
        {"lookback_bars", "order_quantity", "entry_threshold", "position_mode"}
    ),
    "donchian_breakout": frozenset(
        {"lookback_bars", "order_quantity", "position_mode"}
    ),
    "mean_reversion_zscore": frozenset(
        {"lookback_bars", "entry_z", "exit_z", "order_quantity", "position_mode"}
    ),
    "volatility_managed_momentum": frozenset(
        {
            "momentum_lookback_bars",
            "volatility_window_bars",
            "base_quantity",
            "target_bar_volatility",
            "min_scale",
            "max_scale",
            "entry_threshold",
            "position_mode",
        }
    ),
}


def _decimal(value: object, *, name: str) -> Decimal:
    if isinstance(value, bool):
        raise InvalidLibraryStrategySpec(f"{name} must be decimal-compatible")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidLibraryStrategySpec(f"{name} must be decimal-compatible") from exc
    if not result.is_finite():
        raise InvalidLibraryStrategySpec(f"{name} must be finite")
    return result


def _integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidLibraryStrategySpec(f"{name} must be an integer")
    return value


def _string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidLibraryStrategySpec(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class LibraryStrategySpec:
    """Strict declarative strategy specification for the R7 research library.

    The catalog has no module path, callable, URL, command, broker, OMS or policy
    fields. `build()` can instantiate only the finite set of audited strategy
    classes declared in this module.
    """

    strategy_id: str
    strategy_version: str
    kind: str
    parameters: Mapping[str, str | int | float | bool]

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise InvalidLibraryStrategySpec("strategy_id is required")
        if not self.strategy_version.strip():
            raise InvalidLibraryStrategySpec("strategy_version is required")
        if self.kind not in _PARAMETER_FIELDS:
            raise InvalidLibraryStrategySpec(f"unsupported strategy kind: {self.kind}")

        params = dict(self.parameters)
        allowed = _PARAMETER_FIELDS[self.kind]
        unknown = set(params) - allowed
        missing = allowed - set(params)
        if unknown:
            raise InvalidLibraryStrategySpec(
                f"unknown parameters for {self.kind}: {sorted(unknown)}"
            )
        if missing:
            raise InvalidLibraryStrategySpec(
                f"missing parameters for {self.kind}: {sorted(missing)}"
            )
        self._validate_parameters(params)

    @classmethod
    def from_json(cls, raw: str) -> "LibraryStrategySpec":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidLibraryStrategySpec("strategy spec must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise InvalidLibraryStrategySpec("strategy spec root must be an object")
        unknown = set(payload) - _ALLOWED_TOP_LEVEL
        missing = _ALLOWED_TOP_LEVEL - set(payload)
        if unknown:
            raise InvalidLibraryStrategySpec(
                f"unknown top-level fields: {sorted(unknown)}"
            )
        if missing:
            raise InvalidLibraryStrategySpec(
                f"missing top-level fields: {sorted(missing)}"
            )
        if not isinstance(payload["parameters"], dict):
            raise InvalidLibraryStrategySpec("parameters must be an object")
        return cls(
            strategy_id=str(payload["strategy_id"]),
            strategy_version=str(payload["strategy_version"]),
            kind=str(payload["kind"]),
            parameters=payload["parameters"],
        )

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
        raw = json.dumps(
            self.canonical_payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(raw).hexdigest()

    def build(self) -> ResearchStrategy:
        params = dict(self.parameters)
        identity = {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
        }
        if self.kind == "time_series_momentum":
            return TimeSeriesMomentumStrategy(
                lookback_bars=_integer(params["lookback_bars"], name="lookback_bars"),
                order_quantity=_decimal(params["order_quantity"], name="order_quantity"),
                entry_threshold=_decimal(
                    params["entry_threshold"], name="entry_threshold"
                ),
                position_mode=_string(params["position_mode"], name="position_mode"),
                **identity,
            )
        if self.kind == "donchian_breakout":
            return DonchianBreakoutStrategy(
                lookback_bars=_integer(params["lookback_bars"], name="lookback_bars"),
                order_quantity=_decimal(params["order_quantity"], name="order_quantity"),
                position_mode=_string(params["position_mode"], name="position_mode"),
                **identity,
            )
        if self.kind == "mean_reversion_zscore":
            return MeanReversionZScoreStrategy(
                lookback_bars=_integer(params["lookback_bars"], name="lookback_bars"),
                entry_z=_decimal(params["entry_z"], name="entry_z"),
                exit_z=_decimal(params["exit_z"], name="exit_z"),
                order_quantity=_decimal(params["order_quantity"], name="order_quantity"),
                position_mode=_string(params["position_mode"], name="position_mode"),
                **identity,
            )
        if self.kind == "volatility_managed_momentum":
            return VolatilityManagedMomentumStrategy(
                momentum_lookback_bars=_integer(
                    params["momentum_lookback_bars"], name="momentum_lookback_bars"
                ),
                volatility_window_bars=_integer(
                    params["volatility_window_bars"], name="volatility_window_bars"
                ),
                base_quantity=_decimal(params["base_quantity"], name="base_quantity"),
                target_bar_volatility=_decimal(
                    params["target_bar_volatility"], name="target_bar_volatility"
                ),
                min_scale=_decimal(params["min_scale"], name="min_scale"),
                max_scale=_decimal(params["max_scale"], name="max_scale"),
                entry_threshold=_decimal(
                    params["entry_threshold"], name="entry_threshold"
                ),
                position_mode=_string(params["position_mode"], name="position_mode"),
                **identity,
            )
        raise InvalidLibraryStrategySpec(f"unsupported strategy kind: {self.kind}")

    def _validate_parameters(self, params: Mapping[str, object]) -> None:
        if self.kind in {
            "time_series_momentum",
            "donchian_breakout",
            "mean_reversion_zscore",
        }:
            _integer(params["lookback_bars"], name="lookback_bars")
            _decimal(params["order_quantity"], name="order_quantity")
            _string(params["position_mode"], name="position_mode")
        if self.kind == "time_series_momentum":
            _decimal(params["entry_threshold"], name="entry_threshold")
        elif self.kind == "mean_reversion_zscore":
            _decimal(params["entry_z"], name="entry_z")
            _decimal(params["exit_z"], name="exit_z")
        elif self.kind == "volatility_managed_momentum":
            _integer(
                params["momentum_lookback_bars"], name="momentum_lookback_bars"
            )
            _integer(
                params["volatility_window_bars"], name="volatility_window_bars"
            )
            for name in (
                "base_quantity",
                "target_bar_volatility",
                "min_scale",
                "max_scale",
                "entry_threshold",
            ):
                _decimal(params[name], name=name)
            _string(params["position_mode"], name="position_mode")

        try:
            self.build()
        except InvalidLibraryStrategySpec:
            raise
        except (TypeError, ValueError) as exc:
            raise InvalidLibraryStrategySpec(str(exc)) from exc


__all__ = ["InvalidLibraryStrategySpec", "LibraryStrategySpec"]
