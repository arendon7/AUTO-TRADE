"""Deterministic aligned multi-asset research universe.

The first OSS-2 universe contract is intentionally strict: all assets must use
one quote currency, one timeframe, and the exact same closed-bar timestamps.
This makes cross-sectional comparisons explicit and prevents silent forward
fills, stale-asset ranking, or mixed-clock leakage.

Research only: this module has no broker, OMS, Safety writer, credentials,
network, OrderIntent, PAPER execution or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json

from .market import MarketDataset


class InvalidAlignedUniverse(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AlignedMarketUniverse:
    datasets: tuple[MarketDataset, ...]
    universe_name: str

    def __post_init__(self) -> None:
        if not self.universe_name.strip():
            raise InvalidAlignedUniverse("universe_name is required")
        if len(self.datasets) < 2:
            raise InvalidAlignedUniverse("aligned universe requires at least two assets")
        if any(not isinstance(dataset, MarketDataset) for dataset in self.datasets):
            raise InvalidAlignedUniverse("datasets must contain MarketDataset")

        symbols = tuple(dataset.instrument.symbol for dataset in self.datasets)
        if symbols != tuple(sorted(symbols)):
            raise InvalidAlignedUniverse("datasets must be in canonical symbol order")
        if len(set(symbols)) != len(symbols):
            raise InvalidAlignedUniverse("universe symbols must be unique")

        quote_currency = self.datasets[0].instrument.quote_currency
        timeframe_seconds = self.datasets[0].timeframe_seconds
        bar_count = len(self.datasets[0].bars)
        timestamps = tuple(bar.started_at for bar in self.datasets[0].bars)

        for dataset in self.datasets:
            if dataset.instrument.quote_currency != quote_currency:
                raise InvalidAlignedUniverse("mixed quote currencies are not allowed")
            if dataset.timeframe_seconds != timeframe_seconds:
                raise InvalidAlignedUniverse("mixed timeframes are not allowed")
            if len(dataset.bars) != bar_count:
                raise InvalidAlignedUniverse("all assets must have identical bar counts")
            current_timestamps = tuple(bar.started_at for bar in dataset.bars)
            if current_timestamps != timestamps:
                raise InvalidAlignedUniverse(
                    "all assets must have exact aligned bar timestamps"
                )
            if dataset.gap_indexes():
                raise InvalidAlignedUniverse("gapped datasets are not allowed in OSS-2")

    @classmethod
    def from_datasets(
        cls,
        *,
        datasets: tuple[MarketDataset, ...],
        universe_name: str,
    ) -> "AlignedMarketUniverse":
        ordered = tuple(sorted(datasets, key=lambda item: item.instrument.symbol))
        return cls(datasets=ordered, universe_name=universe_name)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(dataset.instrument.symbol for dataset in self.datasets)

    @property
    def quote_currency(self) -> str:
        return self.datasets[0].instrument.quote_currency

    @property
    def timeframe_seconds(self) -> int:
        return self.datasets[0].timeframe_seconds

    @property
    def bar_count(self) -> int:
        return len(self.datasets[0].bars)

    @property
    def timestamps(self) -> tuple[datetime, ...]:
        return tuple(bar.started_at for bar in self.datasets[0].bars)

    @property
    def universe_hash(self) -> str:
        payload = {
            "universe_name": self.universe_name,
            "quote_currency": self.quote_currency,
            "timeframe_seconds": self.timeframe_seconds,
            "datasets": [
                {
                    "symbol": dataset.instrument.symbol,
                    "dataset_hash": dataset.dataset_hash,
                }
                for dataset in self.datasets
            ],
        }
        raw = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(raw).hexdigest()

    def dataset(self, symbol: str) -> MarketDataset:
        for dataset in self.datasets:
            if dataset.instrument.symbol == symbol:
                return dataset
        raise KeyError(symbol)

    def prefix(self, stop: int) -> "AlignedMarketUniverse":
        if isinstance(stop, bool) or not isinstance(stop, int):
            raise InvalidAlignedUniverse("prefix stop must be integer")
        if stop <= 0 or stop > self.bar_count:
            raise InvalidAlignedUniverse("prefix stop outside universe range")
        return AlignedMarketUniverse.from_datasets(
            datasets=tuple(dataset.slice(0, stop) for dataset in self.datasets),
            universe_name=f"{self.universe_name}#prefix={stop}",
        )
