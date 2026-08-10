from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.market import (
    Bar,
    InstrumentMetadata,
    InvalidMarketDataset,
    MarketDataset,
)


def instrument():
    return InstrumentMetadata(
        symbol="TEST-USD",
        venue="TEST",
        quote_currency="USD",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )


def bar(now, index=0, *, symbol="TEST-USD", timeframe=60, price="100", volume="1000"):
    p = Decimal(price)
    return Bar(
        symbol=symbol,
        started_at=now + timedelta(seconds=index * timeframe),
        timeframe_seconds=timeframe,
        open=p,
        high=p + Decimal("1"),
        low=p - Decimal("1"),
        close=p + Decimal("0.5"),
        volume=Decimal(volume),
    )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"symbol": ""}, "symbol"),
        ({"venue": ""}, "venue"),
        ({"quote_currency": ""}, "quote_currency"),
        ({"price_tick": Decimal("0")}, "price_tick"),
        ({"quantity_step": Decimal("NaN")}, "quantity_step"),
    ],
)
def test_instrument_metadata_is_strict(kwargs, message):
    values = {
        "symbol": "TEST-USD",
        "venue": "TEST",
        "quote_currency": "USD",
        "price_tick": Decimal("0.01"),
        "quantity_step": Decimal("0.001"),
    }
    values.update(kwargs)
    with pytest.raises(InvalidMarketDataset, match=message):
        InstrumentMetadata(**values)


def test_bar_rejects_naive_invalid_prices_volume_and_ohlc(now):
    good = bar(now)
    with pytest.raises(InvalidMarketDataset, match="timezone-aware"):
        replace(good, started_at=now.replace(tzinfo=None))
    with pytest.raises(InvalidMarketDataset, match="timeframe"):
        replace(good, timeframe_seconds=0)
    with pytest.raises(InvalidMarketDataset, match="open"):
        replace(good, open=Decimal("NaN"))
    with pytest.raises(InvalidMarketDataset, match="volume"):
        replace(good, volume=Decimal("-1"))
    with pytest.raises(InvalidMarketDataset, match="low cannot exceed"):
        replace(good, low=Decimal("102"), high=Decimal("101"))
    with pytest.raises(InvalidMarketDataset, match="high below"):
        replace(good, high=Decimal("100.1"))
    with pytest.raises(InvalidMarketDataset, match="low above"):
        replace(good, low=Decimal("100.2"))
    with pytest.raises(InvalidMarketDataset, match="symbol"):
        replace(good, symbol="")


def test_dataset_hash_is_deterministic_and_slice_changes_identity(now):
    bars = tuple(bar(now, i, price=str(100 + i)) for i in range(5))
    first = MarketDataset(instrument=instrument(), bars=bars, source="fixture-v1")
    second = MarketDataset.from_iterable(
        instrument=instrument(), bars=list(bars), source="fixture-v1"
    )
    assert first.dataset_hash == second.dataset_hash
    assert first.timeframe_seconds == 60
    assert first.started_at == bars[0].started_at
    assert first.ended_at == bars[-1].ended_at
    assert first.gap_indexes() == ()

    sliced = first.slice(1, 4)
    assert len(sliced.bars) == 3
    assert sliced.dataset_hash != first.dataset_hash
    with pytest.raises(InvalidMarketDataset, match="slice cannot be empty"):
        first.slice(2, 2)


def test_dataset_detects_gaps(now):
    bars = [bar(now, 0), bar(now, 1), bar(now, 3)]
    dataset = MarketDataset(instrument=instrument(), bars=tuple(bars), source="gapped")
    assert dataset.gap_indexes() == (2,)


def test_dataset_rejects_empty_source_ordering_symbol_and_timeframe(now):
    good = bar(now)
    with pytest.raises(InvalidMarketDataset, match="source"):
        MarketDataset(instrument=instrument(), bars=(good,), source="")
    with pytest.raises(InvalidMarketDataset, match="cannot be empty"):
        MarketDataset(instrument=instrument(), bars=(), source="x")
    with pytest.raises(InvalidMarketDataset, match="strictly increasing"):
        MarketDataset(instrument=instrument(), bars=(good, good), source="x")
    with pytest.raises(InvalidMarketDataset, match="symbol"):
        MarketDataset(
            instrument=instrument(),
            bars=(replace(good, symbol="OTHER"),),
            source="x",
        )
    with pytest.raises(InvalidMarketDataset, match="mixed bar timeframes"):
        MarketDataset(
            instrument=instrument(),
            bars=(good, bar(now, 1, timeframe=120)),
            source="x",
        )
