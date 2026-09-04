from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import Side
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.cross_sectional import CrossSectionalMomentumConfig
from autotrade.research.cross_sectional_backtest import (
    CrossSectionalBacktestConfig,
    CrossSectionalBacktestEngine,
    InvalidCrossSectionalBacktestConfig,
)
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.portfolio_dependence import CalibrationPhase
from autotrade.research.universe import AlignedMarketUniverse


def dataset(now, *, symbol, closes, volume=100000):
    instrument = InstrumentMetadata(
        symbol=symbol,
        venue="TEST",
        quote_currency="USD",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )
    bars = tuple(
        Bar(
            symbol=symbol,
            started_at=now + timedelta(minutes=index),
            timeframe_seconds=60,
            open=Decimal(str(close)),
            high=Decimal(str(close)) + Decimal("1"),
            low=max(Decimal(str(close)) - Decimal("1"), Decimal("0.01")),
            close=Decimal(str(close)),
            volume=Decimal(str(volume)),
        )
        for index, close in enumerate(closes)
    )
    return MarketDataset(
        instrument=instrument,
        bars=bars,
        source=f"oss2-backtest:{symbol}",
    )


def universe(now, *, a=None, b=None, c=None, volume=100000):
    a = a or [100, 101, 103, 106, 110, 114, 118, 121, 124, 127, 130, 133]
    b = b or [100, 100, 101, 102, 103, 104, 105, 106, 108, 110, 112, 114]
    c = c or [100, 99, 98, 97, 96, 95, 96, 97, 98, 99, 100, 101]
    return AlignedMarketUniverse.from_datasets(
        datasets=(
            dataset(now, symbol="AAA-USD", closes=a, volume=volume),
            dataset(now, symbol="BBB-USD", closes=b, volume=volume),
            dataset(now, symbol="CCC-USD", closes=c, volume=volume),
        ),
        universe_name="oss2-backtest-universe",
    )


def ranking_config(**overrides):
    values = {
        "lookback_bars": 3,
        "top_n": 2,
        "min_average_dollar_volume": Decimal("0"),
        "max_weight_per_asset": Decimal("0.45"),
        "require_positive_momentum": True,
    }
    values.update(overrides)
    return CrossSectionalMomentumConfig(**values)


def backtest_config(*, costs=True, volume_participation="0.10", rebalance=2, **overrides):
    cost_model = (
        ExecutionCostModel(
            fee_bps=Decimal("10"),
            half_spread_bps=Decimal("5"),
            slippage_bps=Decimal("5"),
        )
        if costs
        else ExecutionCostModel(
            fee_bps=Decimal("0"),
            half_spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            allow_zero_total_costs=True,
        )
    )
    values = {
        "initial_cash": Decimal("100000"),
        "ranking": ranking_config(),
        "cost_model": cost_model,
        "rebalance_every_bars": rebalance,
        "annualization_factor": Decimal("365"),
        "gross_target": Decimal("0.95"),
        "max_volume_participation": Decimal(volume_participation),
        "min_trade_notional": Decimal("1"),
    }
    values.update(overrides)
    return CrossSectionalBacktestConfig(**values)


def test_backtest_executes_every_ranking_only_on_next_bar(now):
    result = CrossSectionalBacktestEngine().run(
        universe=universe(now),
        config=backtest_config(rebalance=1),
    )

    assert result.ranking_evidence
    assert result.fills
    ranking_by_fingerprint = {
        ranking.fingerprint: ranking for ranking in result.ranking_evidence
    }
    for fill in result.fills:
        ranking = ranking_by_fingerprint[fill.ranking_fingerprint]
        assert fill.signal_bar_index == ranking.as_of_bar_index
        assert fill.execution_bar_index == fill.signal_bar_index + 1
        # In contiguous bars, close(t) and open(t+1) share one boundary timestamp.
        # Future-bar isolation is established by the index transition, not by
        # requiring an artificial time gap between adjacent bars.
        assert fill.occurred_at == ranking.as_of
    assert result.metrics.rebalances == len(result.ranking_evidence)


def test_explicit_costs_reduce_result_and_are_accounted(now):
    market = universe(now)
    with_costs = CrossSectionalBacktestEngine().run(
        universe=market,
        config=backtest_config(costs=True),
    )
    zero_costs = CrossSectionalBacktestEngine().run(
        universe=market,
        config=backtest_config(costs=False),
    )

    assert with_costs.metrics.total_fees > 0
    assert zero_costs.metrics.total_fees == 0
    assert with_costs.equity_curve[-1].equity < zero_costs.equity_curve[-1].equity
    assert with_costs.metrics.net_return < zero_costs.metrics.net_return


def test_volume_participation_caps_fills_and_exposes_tracking_error(now):
    market = universe(now, volume=Decimal("1"))
    result = CrossSectionalBacktestEngine().run(
        universe=market,
        config=backtest_config(
            costs=False,
            volume_participation="0.10",
            min_trade_notional=Decimal("0"),
        ),
    )

    assert result.fills
    assert all(fill.volume_participation <= Decimal("0.10") for fill in result.fills)
    assert result.metrics.max_volume_participation <= 0.10
    assert result.metrics.average_target_tracking_error > 0


def test_rotation_sells_before_buying_and_never_uses_negative_cash(now):
    market = universe(
        now,
        a=[100, 105, 110, 120, 125, 120, 110, 100, 90, 85, 82, 80],
        b=[100, 100, 100, 101, 102, 105, 110, 120, 130, 140, 150, 160],
        c=[100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89],
    )
    result = CrossSectionalBacktestEngine().run(
        universe=market,
        config=backtest_config(
            costs=True,
            rebalance=1,
            ranking=ranking_config(top_n=1, max_weight_per_asset=Decimal("0.90")),
        ),
    )

    assert all(point.cash >= 0 for point in result.equity_curve)
    grouped = {}
    for fill in result.fills:
        grouped.setdefault(fill.execution_bar_index, []).append(fill)
    rotations = [
        fills
        for fills in grouped.values()
        if any(fill.side is Side.SELL for fill in fills)
        and any(fill.side is Side.BUY for fill in fills)
    ]
    assert rotations
    for fills in rotations:
        first_buy = next(i for i, fill in enumerate(fills) if fill.side is Side.BUY)
        last_sell = max(i for i, fill in enumerate(fills) if fill.side is Side.SELL)
        assert last_sell < first_buy


def test_first_decisions_are_invariant_to_unseen_future_tail(now):
    first = universe(now)
    second = universe(
        now,
        a=[100, 101, 103, 106, 110, 114, 999, 800, 700, 600, 500, 400],
        b=[100, 100, 101, 102, 103, 104, 1, 2, 3, 4, 5, 6],
        c=[100, 99, 98, 97, 96, 95, 900, 1000, 1100, 1200, 1300, 1400],
    )
    config = backtest_config(rebalance=1)
    first_result = CrossSectionalBacktestEngine().run(universe=first, config=config)
    second_result = CrossSectionalBacktestEngine().run(universe=second, config=config)

    # Prefix through bar 5 is identical. Therefore the first rankings/fills,
    # which can only consume that prefix and execute at the next open, are exact.
    first_rankings = first_result.ranking_evidence[:2]
    second_rankings = second_result.ranking_evidence[:2]
    assert first_rankings == second_rankings

    cutoff_execution_index = 5
    first_fills = tuple(
        fill for fill in first_result.fills if fill.execution_bar_index <= cutoff_execution_index
    )
    second_fills = tuple(
        fill for fill in second_result.fills if fill.execution_bar_index <= cutoff_execution_index
    )
    assert first_fills == second_fills


def test_result_converts_to_existing_dependence_return_contract(now):
    result = CrossSectionalBacktestEngine().run(
        universe=universe(now),
        config=backtest_config(),
    )
    series = result.to_strategy_return_series(
        strategy_id="oss2-cross-sectional-momentum",
        strategy_version="1.0.0",
        phase=CalibrationPhase.DEVELOPMENT,
    )

    assert series.strategy_id == "oss2-cross-sectional-momentum"
    assert series.source_hash == result.result_hash
    assert len(series.observations) == len(result.period_returns)
    assert len(series.observations) >= 2


def test_backtest_result_is_deterministic(now):
    market = universe(now)
    config = backtest_config()
    first = CrossSectionalBacktestEngine().run(universe=market, config=config)
    second = CrossSectionalBacktestEngine().run(universe=market, config=config)

    assert first == second
    assert first.result_hash == second.result_hash
    assert len(first.result_hash) == 64


def test_backtest_config_and_short_dataset_fail_closed(now):
    base = backtest_config()
    with pytest.raises(InvalidCrossSectionalBacktestConfig, match="initial_cash"):
        CrossSectionalBacktestConfig(
            initial_cash=Decimal("0"),
            ranking=base.ranking,
            cost_model=base.cost_model,
            rebalance_every_bars=1,
            annualization_factor=Decimal("365"),
            gross_target=Decimal("0.9"),
            max_volume_participation=Decimal("0.1"),
            min_trade_notional=Decimal("0"),
        )
    with pytest.raises(InvalidCrossSectionalBacktestConfig, match="gross_target"):
        CrossSectionalBacktestConfig(
            initial_cash=Decimal("1000"),
            ranking=base.ranking,
            cost_model=base.cost_model,
            rebalance_every_bars=1,
            annualization_factor=Decimal("365"),
            gross_target=Decimal("1.1"),
            max_volume_participation=Decimal("0.1"),
            min_trade_notional=Decimal("0"),
        )

    short = AlignedMarketUniverse.from_datasets(
        datasets=(
            dataset(now, symbol="AAA-USD", closes=[100, 101, 102, 103]),
            dataset(now, symbol="BBB-USD", closes=[100, 101, 102, 103]),
        ),
        universe_name="short",
    )
    with pytest.raises(InvalidCrossSectionalBacktestConfig, match="enough bars"):
        CrossSectionalBacktestEngine().run(universe=short, config=base)


def test_backtest_outputs_are_research_evidence_not_authority(now):
    result = CrossSectionalBacktestEngine().run(
        universe=universe(now),
        config=backtest_config(),
    )
    assert not hasattr(result, "paper_execution_authorized")
    assert not hasattr(result, "live_authority")
    assert not hasattr(result, "capital_authority")
    assert not hasattr(result, "order_intent")
