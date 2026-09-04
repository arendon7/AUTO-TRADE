from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.cross_sectional import (
    CrossSectionalMomentumConfig,
    CrossSectionalResearchError,
    rank_cross_sectional_momentum,
)
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.universe import AlignedMarketUniverse, InvalidAlignedUniverse


def make_dataset(
    now,
    *,
    symbol,
    closes,
    volumes=None,
    quote_currency="USD",
    timeframe_seconds=60,
    shift_seconds=0,
):
    if volumes is None:
        volumes = [1000] * len(closes)
    instrument = InstrumentMetadata(
        symbol=symbol,
        venue="TEST",
        quote_currency=quote_currency,
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )
    bars = []
    for index, (raw_close, raw_volume) in enumerate(zip(closes, volumes, strict=True)):
        close = Decimal(str(raw_close))
        bars.append(
            Bar(
                symbol=symbol,
                started_at=(
                    now
                    + timedelta(seconds=shift_seconds)
                    + timedelta(seconds=timeframe_seconds * index)
                ),
                timeframe_seconds=timeframe_seconds,
                open=close,
                high=close + Decimal("1"),
                low=max(close - Decimal("1"), Decimal("0.01")),
                close=close,
                volume=Decimal(str(raw_volume)),
            )
        )
    return MarketDataset(
        instrument=instrument,
        bars=tuple(bars),
        source=f"oss2-fixture:{symbol}",
    )


def make_universe(now, *, tails=None):
    tails = tails or {
        "AAA-USD": [120, 125],
        "BBB-USD": [108, 109],
        "CCC-USD": [90, 85],
    }
    prefixes = {
        "AAA-USD": [100, 101, 104, 108, 115],
        "BBB-USD": [100, 100, 102, 104, 106],
        "CCC-USD": [100, 99, 97, 95, 92],
    }
    datasets = tuple(
        make_dataset(
            now,
            symbol=symbol,
            closes=prefixes[symbol] + tails[symbol],
        )
        for symbol in ("CCC-USD", "AAA-USD", "BBB-USD")
    )
    return AlignedMarketUniverse.from_datasets(
        datasets=datasets,
        universe_name="oss2-test-universe",
    )


def config(**overrides):
    values = {
        "lookback_bars": 3,
        "top_n": 2,
        "min_average_dollar_volume": Decimal("1000"),
        "max_weight_per_asset": Decimal("0.5"),
        "require_positive_momentum": True,
    }
    values.update(overrides)
    return CrossSectionalMomentumConfig(**values)


def test_aligned_universe_canonicalizes_symbol_order_and_hashes_identity(now):
    universe = make_universe(now)

    assert universe.symbols == ("AAA-USD", "BBB-USD", "CCC-USD")
    assert universe.bar_count == 7
    assert universe.timeframe_seconds == 60
    assert universe.quote_currency == "USD"
    assert len(universe.universe_hash) == 64
    assert universe.dataset("BBB-USD").instrument.symbol == "BBB-USD"
    with pytest.raises(KeyError):
        universe.dataset("MISSING")


def test_aligned_universe_rejects_clock_quote_and_length_mismatch(now):
    a = make_dataset(now, symbol="AAA-USD", closes=[100, 101, 102])
    shifted = make_dataset(
        now,
        symbol="BBB-USD",
        closes=[100, 101, 102],
        shift_seconds=1,
    )
    with pytest.raises(InvalidAlignedUniverse, match="aligned bar timestamps"):
        AlignedMarketUniverse.from_datasets(
            datasets=(a, shifted), universe_name="bad-clock"
        )

    eur = make_dataset(
        now,
        symbol="BBB-EUR",
        closes=[100, 101, 102],
        quote_currency="EUR",
    )
    with pytest.raises(InvalidAlignedUniverse, match="quote currencies"):
        AlignedMarketUniverse.from_datasets(datasets=(a, eur), universe_name="bad-fx")

    short = make_dataset(now, symbol="BBB-USD", closes=[100, 101])
    with pytest.raises(InvalidAlignedUniverse, match="bar counts"):
        AlignedMarketUniverse.from_datasets(
            datasets=(a, short), universe_name="bad-length"
        )


def test_aligned_universe_requires_multiple_assets_and_valid_prefix(now):
    a = make_dataset(now, symbol="AAA-USD", closes=[100, 101, 102])
    with pytest.raises(InvalidAlignedUniverse, match="at least two assets"):
        AlignedMarketUniverse.from_datasets(datasets=(a,), universe_name="one")

    universe = make_universe(now)
    prefix = universe.prefix(5)
    assert prefix.bar_count == 5
    assert prefix.symbols == universe.symbols
    with pytest.raises(InvalidAlignedUniverse, match="outside universe range"):
        universe.prefix(0)
    with pytest.raises(InvalidAlignedUniverse, match="outside universe range"):
        universe.prefix(999)


def test_cross_sectional_momentum_ranks_and_allocates_top_positive_assets(now):
    universe = make_universe(now)
    evidence = rank_cross_sectional_momentum(
        universe,
        config(),
        as_of_bar_index=4,
    )

    assert [item.symbol for item in evidence.rankings] == [
        "AAA-USD",
        "BBB-USD",
        "CCC-USD",
    ]
    assert evidence.selected_symbols == ("AAA-USD", "BBB-USD")
    assert dict(evidence.target_weights) == {
        "AAA-USD": Decimal("0.5"),
        "BBB-USD": Decimal("0.5"),
        "CCC-USD": Decimal("0"),
    }
    assert evidence.invested_weight == Decimal("1.0")
    assert evidence.cash_weight == Decimal("0.0")
    assert evidence.rankings[-1].exclusion_reason == "NON_POSITIVE_MOMENTUM"
    assert len(evidence.fingerprint) == 64


def test_liquidity_filter_excludes_fast_but_illiquid_asset(now):
    a = make_dataset(
        now,
        symbol="AAA-USD",
        closes=[100, 102, 105, 110, 120],
        volumes=[1, 1, 1, 1, 1],
    )
    b = make_dataset(
        now,
        symbol="BBB-USD",
        closes=[100, 101, 102, 104, 108],
        volumes=[1000, 1000, 1000, 1000, 1000],
    )
    universe = AlignedMarketUniverse.from_datasets(
        datasets=(a, b), universe_name="liquidity"
    )
    evidence = rank_cross_sectional_momentum(
        universe,
        config(
            top_n=1,
            min_average_dollar_volume=Decimal("10000"),
            max_weight_per_asset=Decimal("1"),
        ),
        as_of_bar_index=4,
    )

    aaa = next(item for item in evidence.rankings if item.symbol == "AAA-USD")
    bbb = next(item for item in evidence.rankings if item.symbol == "BBB-USD")
    assert aaa.rank == 1
    assert not aaa.eligible
    assert aaa.exclusion_reason == "MIN_AVERAGE_DOLLAR_VOLUME"
    assert bbb.selected
    assert dict(evidence.target_weights)["BBB-USD"] == Decimal("1")


def test_weight_cap_leaves_explicit_cash_instead_of_overallocating(now):
    universe = make_universe(now)
    evidence = rank_cross_sectional_momentum(
        universe,
        config(max_weight_per_asset=Decimal("0.30")),
        as_of_bar_index=4,
    )

    assert evidence.selected_symbols == ("AAA-USD", "BBB-USD")
    assert evidence.invested_weight == Decimal("0.60")
    assert evidence.cash_weight == Decimal("0.40")
    assert all(weight <= Decimal("0.30") for _, weight in evidence.target_weights)


def test_ranking_is_strictly_prefix_only_even_when_future_tail_changes(now):
    first = make_universe(now)
    second = make_universe(
        now,
        tails={
            "AAA-USD": [1, 1],
            "BBB-USD": [1000, 2000],
            "CCC-USD": [5000, 9000],
        },
    )
    assert first.universe_hash != second.universe_hash

    first_evidence = rank_cross_sectional_momentum(
        first, config(), as_of_bar_index=4
    )
    second_evidence = rank_cross_sectional_momentum(
        second, config(), as_of_bar_index=4
    )

    assert first_evidence.universe_hash == second_evidence.universe_hash
    assert first_evidence.rankings == second_evidence.rankings
    assert first_evidence.target_weights == second_evidence.target_weights
    assert first_evidence.fingerprint == second_evidence.fingerprint


def test_later_as_of_may_change_ranking_only_when_new_closed_bars_exist(now):
    universe = make_universe(now)
    early = rank_cross_sectional_momentum(universe, config(), as_of_bar_index=4)
    later = rank_cross_sectional_momentum(universe, config(), as_of_bar_index=6)

    assert early.as_of < later.as_of
    assert early.universe_hash != later.universe_hash
    assert early.fingerprint != later.fingerprint


def test_config_and_as_of_fail_closed(now):
    with pytest.raises(ValueError, match="lookback_bars"):
        config(lookback_bars=1)
    with pytest.raises(ValueError, match="top_n"):
        config(top_n=0)
    with pytest.raises(ValueError, match="min_average_dollar_volume"):
        config(min_average_dollar_volume=Decimal("-1"))
    with pytest.raises(ValueError, match="max_weight_per_asset"):
        config(max_weight_per_asset=Decimal("1.1"))

    universe = make_universe(now)
    with pytest.raises(CrossSectionalResearchError, match="historical lookback"):
        rank_cross_sectional_momentum(universe, config(), as_of_bar_index=2)
    with pytest.raises(CrossSectionalResearchError, match="historical lookback"):
        rank_cross_sectional_momentum(universe, config(), as_of_bar_index=7)


def test_cross_sectional_evidence_contains_no_execution_authority_fields(now):
    evidence = rank_cross_sectional_momentum(
        make_universe(now), config(), as_of_bar_index=4
    )
    payload = evidence.to_payload()
    forbidden = {
        "broker",
        "credentials",
        "oms",
        "order_intent",
        "paper_execution_authorized",
        "live_authority",
        "capital_authority",
    }
    assert forbidden.isdisjoint(set(payload))
