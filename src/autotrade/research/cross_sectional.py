"""Leakage-safe cross-sectional ranking and long-only research allocation.

A ranking is computed from an exact aligned universe prefix. Current-bar close
and volume may be used only after that bar is closed; no future bar is read.
The output is hash-bound research evidence, not an order or execution permit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import re

from .universe import AlignedMarketUniverse


_ZERO = Decimal("0")
_ONE = Decimal("1")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CrossSectionalResearchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CrossSectionalMomentumConfig:
    lookback_bars: int
    top_n: int
    min_average_dollar_volume: Decimal
    max_weight_per_asset: Decimal
    require_positive_momentum: bool = True

    def __post_init__(self) -> None:
        if (
            isinstance(self.lookback_bars, bool)
            or not isinstance(self.lookback_bars, int)
            or self.lookback_bars < 2
        ):
            raise ValueError("lookback_bars must be integer >= 2")
        if isinstance(self.top_n, bool) or not isinstance(self.top_n, int) or self.top_n < 1:
            raise ValueError("top_n must be integer >= 1")
        if (
            not isinstance(self.min_average_dollar_volume, Decimal)
            or not self.min_average_dollar_volume.is_finite()
            or self.min_average_dollar_volume < _ZERO
        ):
            raise ValueError("min_average_dollar_volume must be finite Decimal >= 0")
        if (
            not isinstance(self.max_weight_per_asset, Decimal)
            or not self.max_weight_per_asset.is_finite()
            or self.max_weight_per_asset <= _ZERO
            or self.max_weight_per_asset > _ONE
        ):
            raise ValueError("max_weight_per_asset must be finite Decimal in (0,1]")
        if not isinstance(self.require_positive_momentum, bool):
            raise ValueError("require_positive_momentum must be bool")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "lookback_bars": self.lookback_bars,
                "top_n": self.top_n,
                "min_average_dollar_volume": str(self.min_average_dollar_volume),
                "max_weight_per_asset": str(self.max_weight_per_asset),
                "require_positive_momentum": self.require_positive_momentum,
            }
        )


@dataclass(frozen=True, slots=True)
class AssetRanking:
    rank: int
    symbol: str
    momentum: Decimal
    average_dollar_volume: Decimal
    eligible: bool
    selected: bool
    exclusion_reason: str

    def __post_init__(self) -> None:
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError("rank must be integer >= 1")
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        for value in (self.momentum, self.average_dollar_volume):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError("ranking metrics must be finite Decimal")
        if self.average_dollar_volume < _ZERO:
            raise ValueError("average_dollar_volume cannot be negative")
        if self.selected and not self.eligible:
            raise ValueError("selected asset must be eligible")
        if self.eligible and self.exclusion_reason:
            raise ValueError("eligible asset cannot have exclusion_reason")
        if not self.eligible and not self.exclusion_reason.strip():
            raise ValueError("ineligible asset requires exclusion_reason")


@dataclass(frozen=True, slots=True)
class CrossSectionalRankingEvidence:
    universe_hash: str
    config_fingerprint: str
    as_of_bar_index: int
    as_of: datetime
    rankings: tuple[AssetRanking, ...]
    target_weights: tuple[tuple[str, Decimal], ...]

    def __post_init__(self) -> None:
        if not _SHA256_RE.fullmatch(self.universe_hash):
            raise ValueError("universe_hash must be SHA-256 hex")
        if not _SHA256_RE.fullmatch(self.config_fingerprint):
            raise ValueError("config_fingerprint must be SHA-256 hex")
        if (
            isinstance(self.as_of_bar_index, bool)
            or not isinstance(self.as_of_bar_index, int)
            or self.as_of_bar_index < 0
        ):
            raise ValueError("as_of_bar_index must be integer >= 0")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if not self.rankings:
            raise ValueError("rankings cannot be empty")
        ranks = tuple(item.rank for item in self.rankings)
        if ranks != tuple(range(1, len(self.rankings) + 1)):
            raise ValueError("rankings must use contiguous canonical ranks")
        symbols = tuple(item.symbol for item in self.rankings)
        if len(set(symbols)) != len(symbols):
            raise ValueError("ranking symbols must be unique")

        weight_symbols = tuple(symbol for symbol, _ in self.target_weights)
        if weight_symbols != tuple(sorted(weight_symbols)):
            raise ValueError("target weights must be canonical sorted symbols")
        if len(set(weight_symbols)) != len(weight_symbols):
            raise ValueError("target weight symbols must be unique")
        ranking_symbol_set = set(symbols)
        if set(weight_symbols) != ranking_symbol_set:
            raise ValueError("target weights must cover complete ranking universe")
        total = _ZERO
        selected = {item.symbol for item in self.rankings if item.selected}
        for symbol, weight in self.target_weights:
            if not isinstance(weight, Decimal) or not weight.is_finite() or weight < _ZERO:
                raise ValueError("target weights must be finite Decimal >= 0")
            if weight > _ZERO and symbol not in selected:
                raise ValueError("positive weight requires selected asset")
            if symbol in selected and weight <= _ZERO:
                raise ValueError("selected asset requires positive weight")
            total += weight
        if total > _ONE:
            raise ValueError("target weights cannot exceed 1")

    @property
    def selected_symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.rankings if item.selected)

    @property
    def invested_weight(self) -> Decimal:
        return sum((weight for _, weight in self.target_weights), _ZERO)

    @property
    def cash_weight(self) -> Decimal:
        return _ONE - self.invested_weight

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_payload(include_fingerprint=False))

    def to_payload(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "universe_hash": self.universe_hash,
            "config_fingerprint": self.config_fingerprint,
            "as_of_bar_index": self.as_of_bar_index,
            "as_of": self.as_of.isoformat(),
            "rankings": [
                {
                    "rank": item.rank,
                    "symbol": item.symbol,
                    "momentum": str(item.momentum),
                    "average_dollar_volume": str(item.average_dollar_volume),
                    "eligible": item.eligible,
                    "selected": item.selected,
                    "exclusion_reason": item.exclusion_reason,
                }
                for item in self.rankings
            ],
            "target_weights": [[symbol, str(weight)] for symbol, weight in self.target_weights],
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload


def rank_cross_sectional_momentum(
    universe: AlignedMarketUniverse,
    config: CrossSectionalMomentumConfig,
    *,
    as_of_bar_index: int,
) -> CrossSectionalRankingEvidence:
    """Rank assets using only closed bars through ``as_of_bar_index``.

    Momentum = close[t] / close[t-lookback] - 1.
    Liquidity = mean(close * volume) over the trailing lookback bars ending at t.
    Long-only equal weights are assigned to at most top_n eligible assets,
    bounded by max_weight_per_asset. Unused allocation remains research cash.
    """
    if not isinstance(universe, AlignedMarketUniverse):
        raise TypeError("universe must be AlignedMarketUniverse")
    if not isinstance(config, CrossSectionalMomentumConfig):
        raise TypeError("config must be CrossSectionalMomentumConfig")
    if (
        isinstance(as_of_bar_index, bool)
        or not isinstance(as_of_bar_index, int)
        or as_of_bar_index < config.lookback_bars
        or as_of_bar_index >= universe.bar_count
    ):
        raise CrossSectionalResearchError(
            "as_of_bar_index must leave a complete historical lookback inside universe"
        )

    historical_universe = universe.prefix(as_of_bar_index + 1)
    raw: list[tuple[str, Decimal, Decimal, bool, str]] = []
    for dataset in historical_universe.datasets:
        current = dataset.bars[as_of_bar_index]
        past = dataset.bars[as_of_bar_index - config.lookback_bars]
        momentum = current.close / past.close - _ONE
        trailing = dataset.bars[
            as_of_bar_index - config.lookback_bars + 1 : as_of_bar_index + 1
        ]
        average_dollar_volume = sum(
            (bar.close * bar.volume for bar in trailing), _ZERO
        ) / Decimal(len(trailing))

        reason = ""
        eligible = True
        if average_dollar_volume < config.min_average_dollar_volume:
            eligible = False
            reason = "MIN_AVERAGE_DOLLAR_VOLUME"
        elif config.require_positive_momentum and momentum <= _ZERO:
            eligible = False
            reason = "NON_POSITIVE_MOMENTUM"
        raw.append((dataset.instrument.symbol, momentum, average_dollar_volume, eligible, reason))

    ordered = sorted(raw, key=lambda item: (-item[1], item[0]))
    eligible_symbols = [item[0] for item in ordered if item[3]]
    selected_symbols = tuple(eligible_symbols[: config.top_n])
    selected_set = set(selected_symbols)

    rankings = tuple(
        AssetRanking(
            rank=index + 1,
            symbol=symbol,
            momentum=momentum,
            average_dollar_volume=average_dollar_volume,
            eligible=eligible,
            selected=symbol in selected_set,
            exclusion_reason=reason,
        )
        for index, (symbol, momentum, average_dollar_volume, eligible, reason) in enumerate(ordered)
    )

    if selected_symbols:
        equal_weight = _ONE / Decimal(len(selected_symbols))
        selected_weight = min(equal_weight, config.max_weight_per_asset)
    else:
        selected_weight = _ZERO
    weights = tuple(
        sorted(
            (
                (symbol, selected_weight if symbol in selected_set else _ZERO)
                for symbol in historical_universe.symbols
            ),
            key=lambda item: item[0],
        )
    )
    return CrossSectionalRankingEvidence(
        universe_hash=historical_universe.universe_hash,
        config_fingerprint=config.fingerprint,
        as_of_bar_index=as_of_bar_index,
        as_of=historical_universe.datasets[0].bars[as_of_bar_index].ended_at,
        rankings=rankings,
        target_weights=weights,
    )


def _hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()
