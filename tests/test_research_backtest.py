from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from math import isinf

import pytest

from autotrade.research.backtest import (
    BacktestConfig,
    BacktestEngine,
    InvalidBacktestConfig,
)
from autotrade.research.costs import ExecutionCostModel, InvalidCostModel
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.strategy import ResearchSignal, StrategyContext


def make_dataset(now, *, count=6, volume="10000"):
    instrument = InstrumentMetadata(
        symbol="TEST-USD",
        venue="TEST",
        quote_currency="USD",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )
    prices = [Decimal("100") + Decimal(index * 2) for index in range(count)]
    bars = tuple(
        Bar(
            symbol="TEST-USD",
            started_at=now + timedelta(minutes=index),
            timeframe_seconds=60,
            open=price,
            high=price + Decimal("1"),
            low=price - Decimal("1"),
            close=price,
            volume=Decimal(volume),
        )
        for index, price in enumerate(prices)
    )
    return MarketDataset(instrument=instrument, bars=bars, source="research-fixture-v1")


def cost_model():
    return ExecutionCostModel(
        fee_bps=Decimal("10"),
        half_spread_bps=Decimal("5"),
        slippage_bps=Decimal("5"),
    )


def config(**changes):
    values = {
        "initial_cash": Decimal("100000"),
        "cost_model": cost_model(),
        "execution_delay_bars": 1,
        "annualization_factor": Decimal("252"),
        "max_leverage": Decimal("2"),
        "max_volume_participation": Decimal("0.10"),
        "allow_short": True,
    }
    values.update(changes)
    return BacktestConfig(**values)


class RoundTripStrategy:
    strategy_id = "round-trip"
    strategy_version = "1.0.0"
    parameters = {"quantity": 10}

    def __init__(self):
        self.history_lengths = []

    def on_bar(self, context: StrategyContext):
        self.history_lengths.append(len(context.history))
        assert context.current_bar is context.history[-1]
        assert len(context.history) == context.index + 1
        if context.index == 0:
            return ResearchSignal(
                signal_id="buy-1",
                symbol=context.symbol,
                generated_at=context.current_bar.ended_at,
                quantity_delta=Decimal("10"),
                reason="open",
            )
        if context.index == 2:
            return ResearchSignal(
                signal_id="sell-1",
                symbol=context.symbol,
                generated_at=context.current_bar.ended_at,
                quantity_delta=Decimal("-10"),
                reason="close",
            )
        return None


def test_backtest_is_next_bar_only_and_reproducible(now):
    dataset = make_dataset(now)
    first_strategy = RoundTripStrategy()
    first = BacktestEngine().run(
        dataset=dataset,
        strategy=first_strategy,
        config=config(),
    )
    second = BacktestEngine().run(
        dataset=dataset,
        strategy=RoundTripStrategy(),
        config=config(),
    )

    assert [fill.bar_index for fill in first.fills] == [1, 3]
    assert first.fills[0].occurred_at == dataset.bars[1].started_at
    assert first.fills[0].occurred_at >= dataset.bars[0].ended_at
    assert first.fills[1].realized_pnl > 0
    assert first_strategy.history_lengths == [1, 2, 3, 4, 5, 6]
    assert first.metrics.fills == 2
    assert first.metrics.hit_rate == 1.0
    assert isinf(first.metrics.profit_factor)
    assert first.metrics.total_fees > 0
    assert first.metrics.turnover > 0
    assert first.metrics.max_gross_exposure > 0
    assert first.result_hash == second.result_hash
    assert first.dataset_hash == dataset.dataset_hash
    assert first.config_hash == config().config_hash


def test_explicit_costs_reduce_net_result(now):
    dataset = make_dataset(now)
    costly = BacktestEngine().run(
        dataset=dataset,
        strategy=RoundTripStrategy(),
        config=config(),
    )
    zero = ExecutionCostModel(
        fee_bps=Decimal("0"),
        half_spread_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        allow_zero_total_costs=True,
    )
    frictionless = BacktestEngine().run(
        dataset=dataset,
        strategy=RoundTripStrategy(),
        config=config(cost_model=zero),
    )
    assert frictionless.metrics.net_return > costly.metrics.net_return
    assert frictionless.metrics.total_fees == 0


def test_execution_delay_bars_moves_fill_further_into_future(now):
    dataset = make_dataset(now)
    result = BacktestEngine().run(
        dataset=dataset,
        strategy=RoundTripStrategy(),
        config=config(execution_delay_bars=2),
    )
    assert [fill.bar_index for fill in result.fills] == [2, 4]


class SingleSignalStrategy:
    strategy_id = "single"
    strategy_version = "1"
    parameters = {}

    def __init__(self, *, quantity, index=0, symbol="TEST-USD", timestamp_offset=0):
        self.quantity = Decimal(str(quantity))
        self.index = index
        self.symbol = symbol
        self.timestamp_offset = timestamp_offset

    def on_bar(self, context):
        if context.index != self.index:
            return None
        return ResearchSignal(
            signal_id="single-signal",
            symbol=self.symbol,
            generated_at=context.current_bar.ended_at
            + timedelta(seconds=self.timestamp_offset),
            quantity_delta=self.quantity,
        )


def test_volume_participation_rejects_impossible_fill(now):
    dataset = make_dataset(now, volume="100")
    result = BacktestEngine().run(
        dataset=dataset,
        strategy=SingleSignalStrategy(quantity="20"),
        config=config(max_volume_participation=Decimal("0.10")),
    )
    assert result.fills == ()
    assert result.rejected_signals[0].reason_code == "MAX_VOLUME_PARTICIPATION"


def test_zero_volume_rejects_fill(now):
    dataset = make_dataset(now, volume="0")
    result = BacktestEngine().run(
        dataset=dataset,
        strategy=SingleSignalStrategy(quantity="1"),
        config=config(),
    )
    assert result.rejected_signals[0].reason_code == "NO_EXECUTABLE_VOLUME"


def test_short_can_be_disabled(now):
    result = BacktestEngine().run(
        dataset=make_dataset(now),
        strategy=SingleSignalStrategy(quantity="-1"),
        config=config(allow_short=False),
    )
    assert result.fills == ()
    assert result.rejected_signals[0].reason_code == "SHORT_NOT_ALLOWED"


def test_leverage_gate_rejects_oversized_signal(now):
    result = BacktestEngine().run(
        dataset=make_dataset(now, volume="1000000"),
        strategy=SingleSignalStrategy(quantity="500"),
        config=config(
            initial_cash=Decimal("1000"),
            max_leverage=Decimal("1"),
            max_volume_participation=Decimal("1"),
        ),
    )
    assert result.fills == ()
    assert result.rejected_signals[0].reason_code in {
        "MAX_LEVERAGE",
        "NON_POSITIVE_EQUITY",
    }


def test_end_of_data_signal_is_not_magically_filled(now):
    dataset = make_dataset(now, count=3)
    result = BacktestEngine().run(
        dataset=dataset,
        strategy=SingleSignalStrategy(quantity="1", index=2),
        config=config(),
    )
    assert result.fills == ()
    assert result.rejected_signals[0].reason_code == "END_OF_DATA"


def test_bad_symbol_and_timestamp_are_rejected_before_scheduling(now):
    dataset = make_dataset(now)
    bad_symbol = BacktestEngine().run(
        dataset=dataset,
        strategy=SingleSignalStrategy(quantity="1", symbol="OTHER"),
        config=config(),
    )
    assert bad_symbol.rejected_signals[0].reason_code == "SIGNAL_SYMBOL_MISMATCH"

    bad_time = BacktestEngine().run(
        dataset=dataset,
        strategy=SingleSignalStrategy(quantity="1", timestamp_offset=1),
        config=config(),
    )
    assert bad_time.rejected_signals[0].reason_code == "SIGNAL_TIMESTAMP_MISMATCH"


class DuplicateSignalStrategy:
    strategy_id = "duplicate"
    strategy_version = "1"
    parameters = {}

    def on_bar(self, context):
        if context.index > 1:
            return None
        return ResearchSignal(
            signal_id="same-id",
            symbol=context.symbol,
            generated_at=context.current_bar.ended_at,
            quantity_delta=Decimal("1"),
        )


def test_duplicate_signal_ids_are_rejected(now):
    result = BacktestEngine().run(
        dataset=make_dataset(now),
        strategy=DuplicateSignalStrategy(),
        config=config(),
    )
    assert result.metrics.fills == 1
    assert "DUPLICATE_SIGNAL_ID" in {
        rejection.reason_code for rejection in result.rejected_signals
    }


def test_cost_model_requires_explicit_nonzero_assumptions():
    with pytest.raises(InvalidCostModel, match="zero-cost"):
        ExecutionCostModel(
            fee_bps=Decimal("0"),
            half_spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        )
    with pytest.raises(InvalidCostModel, match="fee_bps"):
        ExecutionCostModel(
            fee_bps=Decimal("-1"),
            half_spread_bps=Decimal("1"),
            slippage_bps=Decimal("1"),
        )

    model = cost_model()
    buy = model.execution_price(side=__import__("autotrade.domain", fromlist=["Side"]).Side.BUY, reference_price=Decimal("100"))
    sell = model.execution_price(side=__import__("autotrade.domain", fromlist=["Side"]).Side.SELL, reference_price=Decimal("100"))
    assert buy > Decimal("100")
    assert sell < Decimal("100")
    assert model.fee(quantity=Decimal("10"), execution_price=buy) > 0
    with pytest.raises(InvalidCostModel, match="reference_price"):
        model.execution_price(side=__import__("autotrade.domain", fromlist=["Side"]).Side.BUY, reference_price=Decimal("0"))
    with pytest.raises(InvalidCostModel, match="quantity"):
        model.fee(quantity=Decimal("0"), execution_price=Decimal("100"))


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"initial_cash": Decimal("0")}, "initial_cash"),
        ({"execution_delay_bars": 0}, "execution_delay_bars"),
        ({"annualization_factor": Decimal("0")}, "annualization_factor"),
        ({"max_leverage": Decimal("0")}, "max_leverage"),
        ({"max_volume_participation": Decimal("0")}, "max_volume_participation"),
        ({"max_volume_participation": Decimal("1.1")}, "cannot exceed"),
    ],
)
def test_backtest_config_is_fail_fast(changes, message):
    with pytest.raises(InvalidBacktestConfig, match=message):
        config(**changes)


def test_research_signal_validation(now):
    with pytest.raises(ValueError, match="signal_id"):
        ResearchSignal("", "TEST-USD", now, Decimal("1"))
    with pytest.raises(ValueError, match="symbol"):
        ResearchSignal("x", "", now, Decimal("1"))
    with pytest.raises(ValueError, match="timezone-aware"):
        ResearchSignal("x", "TEST-USD", now.replace(tzinfo=None), Decimal("1"))
    with pytest.raises(ValueError, match="non-zero"):
        ResearchSignal("x", "TEST-USD", now, Decimal("0"))


def test_strategy_metadata_is_required_and_json_serializable(now):
    class MissingId:
        strategy_id = ""
        strategy_version = "1"
        parameters = {}

        def on_bar(self, context):
            return None

    with pytest.raises(ValueError, match="strategy_id"):
        BacktestEngine().run(dataset=make_dataset(now), strategy=MissingId(), config=config())

    class MissingVersion:
        strategy_id = "x"
        strategy_version = ""
        parameters = {}

        def on_bar(self, context):
            return None

    with pytest.raises(ValueError, match="strategy_version"):
        BacktestEngine().run(dataset=make_dataset(now), strategy=MissingVersion(), config=config())

    class BadParameters:
        strategy_id = "x"
        strategy_version = "1"
        parameters = {"bad": object()}

        def on_bar(self, context):
            return None

    with pytest.raises(TypeError):
        BacktestEngine().run(dataset=make_dataset(now), strategy=BadParameters(), config=config())
