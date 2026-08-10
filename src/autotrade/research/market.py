from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable


class InvalidMarketDataset(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InstrumentMetadata:
    symbol: str
    venue: str
    quote_currency: str
    price_tick: Decimal
    quantity_step: Decimal

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise InvalidMarketDataset("instrument symbol is required")
        if not self.venue.strip():
            raise InvalidMarketDataset("instrument venue is required")
        if not self.quote_currency.strip():
            raise InvalidMarketDataset("quote_currency is required")
        if not _finite_positive(self.price_tick):
            raise InvalidMarketDataset("price_tick must be finite and > 0")
        if not _finite_positive(self.quantity_step):
            raise InvalidMarketDataset("quantity_step must be finite and > 0")


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    started_at: datetime
    timeframe_seconds: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise InvalidMarketDataset("bar symbol is required")
        if not _aware(self.started_at):
            raise InvalidMarketDataset("bar timestamp must be timezone-aware")
        if self.timeframe_seconds <= 0:
            raise InvalidMarketDataset("timeframe_seconds must be > 0")
        for name, value in (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
        ):
            if not _finite_positive(value):
                raise InvalidMarketDataset(f"{name} must be finite and > 0")
        if not _finite_nonnegative(self.volume):
            raise InvalidMarketDataset("volume must be finite and >= 0")
        if self.low > self.high:
            raise InvalidMarketDataset("bar low cannot exceed high")
        if self.high < max(self.open, self.close):
            raise InvalidMarketDataset("bar high below open/close")
        if self.low > min(self.open, self.close):
            raise InvalidMarketDataset("bar low above open/close")

    @property
    def ended_at(self) -> datetime:
        return self.started_at + timedelta(seconds=self.timeframe_seconds)


@dataclass(frozen=True, slots=True)
class MarketDataset:
    instrument: InstrumentMetadata
    bars: tuple[Bar, ...]
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise InvalidMarketDataset("dataset source is required")
        if not self.bars:
            raise InvalidMarketDataset("dataset cannot be empty")

        expected_timeframe = self.bars[0].timeframe_seconds
        previous: Bar | None = None
        for index, bar in enumerate(self.bars):
            if bar.symbol != self.instrument.symbol:
                raise InvalidMarketDataset(
                    f"bar {index} symbol {bar.symbol} != {self.instrument.symbol}"
                )
            if bar.timeframe_seconds != expected_timeframe:
                raise InvalidMarketDataset("mixed bar timeframes are not allowed")
            if previous is not None and bar.started_at <= previous.started_at:
                raise InvalidMarketDataset("bars must be strictly increasing and unique")
            previous = bar

    @classmethod
    def from_iterable(
        cls,
        *,
        instrument: InstrumentMetadata,
        bars: Iterable[Bar],
        source: str,
    ) -> "MarketDataset":
        return cls(instrument=instrument, bars=tuple(bars), source=source)

    @property
    def timeframe_seconds(self) -> int:
        return self.bars[0].timeframe_seconds

    @property
    def started_at(self) -> datetime:
        return self.bars[0].started_at

    @property
    def ended_at(self) -> datetime:
        return self.bars[-1].ended_at

    @property
    def dataset_hash(self) -> str:
        payload = {
            "instrument": {
                "symbol": self.instrument.symbol,
                "venue": self.instrument.venue,
                "quote_currency": self.instrument.quote_currency,
                "price_tick": str(self.instrument.price_tick),
                "quantity_step": str(self.instrument.quantity_step),
            },
            "source": self.source,
            "bars": [
                {
                    "started_at": bar.started_at.isoformat(),
                    "timeframe_seconds": bar.timeframe_seconds,
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": str(bar.volume),
                }
                for bar in self.bars
            ],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(raw).hexdigest()

    def gap_indexes(self) -> tuple[int, ...]:
        expected = timedelta(seconds=self.timeframe_seconds)
        gaps: list[int] = []
        for index in range(1, len(self.bars)):
            if self.bars[index].started_at - self.bars[index - 1].started_at != expected:
                gaps.append(index)
        return tuple(gaps)

    def slice(self, start: int, stop: int) -> "MarketDataset":
        selected = self.bars[start:stop]
        if not selected:
            raise InvalidMarketDataset("dataset slice cannot be empty")
        return MarketDataset(
            instrument=self.instrument,
            bars=selected,
            source=f"{self.source}#bars={start}:{stop}",
        )


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _finite_positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _finite_nonnegative(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0
