from datetime import timedelta
from decimal import Decimal
import json

import pytest

from autotrade.research.backtest import BacktestConfig, BacktestEngine
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.dsl import InvalidStrategySpec, StrategySpec
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.strategy import StrategyContext


def make_bars(now, closes):
    result = []
    for index, raw in enumerate(closes):
        close = Decimal(str(raw))
        result.append(
            Bar(
                symbol="TEST-USD",
                started_at=now + timedelta(minutes=index),
                timeframe_seconds=60,
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("10000"),
            )
        )
    return tuple(result)


def context(now, closes, *, position="0"):
    history = make_bars(now, closes)
    return StrategyContext(
        symbol="TEST-USD",
        index=len(history) - 1,
        history=history,
        current_position_quantity=Decimal(position),
        current_equity=Decimal("100000"),
    )


def payload(kind, parameters):
    return {
        "strategy_id": f"oss-{kind}",
        "strategy_version": "1.0.0",
        "kind": kind,
        "parameters": parameters,
        "initial_stop_pct": "0.03",
    }


def build(kind, parameters):
    return StrategySpec.from_json(json.dumps(payload(kind, parameters))).build()


def standard_position_params():
    return {"order_quantity": "2", "position_mode": "long_short"}


@pytest.mark.parametrize(
    "kind,parameters",
    [
        (
            "trend_ema_atr",
            {
                "fast_span": 2,
                "slow_span": 4,
                "atr_window": 2,
                "min_atr_pct": "0",
                **standard_position_params(),
            },
        ),
        (
            "time_series_momentum",
            {
                "fast_horizon": 1,
                "slow_horizon": 3,
                "threshold": "0.01",
                **standard_position_params(),
            },
        ),
        (
            "mean_reversion_zscore",
            {
                "lookback": 4,
                "entry_z": "1",
                "exit_z": "0.25",
                **standard_position_params(),
            },
        ),
        (
            "donchian_breakout",
            {
                "lookback": 3,
                "atr_window": 2,
                "min_atr_pct": "0",
                **standard_position_params(),
            },
        ),
        (
            "volatility_regime",
            {
                "short_vol_window": 2,
                "long_vol_window": 4,
                "trend_window": 3,
                "vol_ratio_threshold": "1",
                **standard_position_params(),
            },
        ),
    ],
)
def test_oss_dsl_kinds_are_canonical_research_only(kind, parameters):
    spec = StrategySpec.from_json(json.dumps(payload(kind, parameters)))
    strategy = spec.build()

    assert strategy.strategy_id == f"oss-{kind}"
    assert strategy.parameters["spec_hash"] == spec.canonical_hash
    assert strategy.parameters["initial_stop_pct"] == "0.03"
    assert spec.canonical_payload["kind"] == kind
    assert not any(
        key in spec.canonical_payload
        for key in ("module", "callable", "broker", "network", "oms", "import")
    )


@pytest.mark.parametrize(
    "kind,parameters,mutation,message",
    [
        (
            "trend_ema_atr",
            {
                "fast_span": 2,
                "slow_span": 4,
                "atr_window": 2,
                "min_atr_pct": "0",
                **standard_position_params(),
            },
            ("slow_span", 2),
            "fast_span must be < slow_span|slow_span must be an integer >= 3",
        ),
        (
            "time_series_momentum",
            {
                "fast_horizon": 1,
                "slow_horizon": 3,
                "threshold": "0.01",
                **standard_position_params(),
            },
            ("threshold", "1"),
            "threshold must be >= 0 and < 1",
        ),
        (
            "mean_reversion_zscore",
            {
                "lookback": 4,
                "entry_z": "1",
                "exit_z": "0.25",
                **standard_position_params(),
            },
            ("exit_z", "1"),
            "exit_z must be >= 0 and < entry_z",
        ),
        (
            "donchian_breakout",
            {
                "lookback": 3,
                "atr_window": 2,
                "min_atr_pct": "0",
                **standard_position_params(),
            },
            ("lookback", 1),
            "lookback must be an integer >= 2",
        ),
        (
            "volatility_regime",
            {
                "short_vol_window": 2,
                "long_vol_window": 4,
                "trend_window": 3,
                "vol_ratio_threshold": "1",
                **standard_position_params(),
            },
            ("short_vol_window", 4),
            "short_vol_window must be < long_vol_window",
        ),
    ],
)
def test_oss_dsl_fails_closed_on_invalid_parameters(
    kind, parameters, mutation, message
):
    parameters = dict(parameters)
    parameters[mutation[0]] = mutation[1]
    with pytest.raises(InvalidStrategySpec, match=message):
        StrategySpec.from_json(json.dumps(payload(kind, parameters)))


def test_trend_ema_atr_emits_long_only_after_closed_bar(now):
    strategy = build(
        "trend_ema_atr",
        {
            "fast_span": 2,
            "slow_span": 4,
            "atr_window": 2,
            "min_atr_pct": "0",
            **standard_position_params(),
        },
    )
    ctx = context(now, [100, 100, 100, 100, 105])
    signal = strategy.on_bar(ctx)

    assert signal is not None
    assert signal.quantity_delta == Decimal("2")
    assert signal.generated_at == ctx.current_bar.ended_at
    assert "ema-cross-up" in signal.reason


def test_time_series_momentum_supports_long_and_short_targets(now):
    strategy = build(
        "time_series_momentum",
        {
            "fast_horizon": 1,
            "slow_horizon": 3,
            "threshold": "0.01",
            **standard_position_params(),
        },
    )
    up = strategy.on_bar(context(now, [100, 100, 100, 104]))
    down = strategy.on_bar(context(now, [104, 104, 104, 100]))

    assert up is not None and up.quantity_delta == Decimal("2")
    assert down is not None and down.quantity_delta == Decimal("-2")


def test_mean_reversion_zscore_enters_against_extreme(now):
    strategy = build(
        "mean_reversion_zscore",
        {
            "lookback": 4,
            "entry_z": "1",
            "exit_z": "0.25",
            **standard_position_params(),
        },
    )
    low = strategy.on_bar(context(now, [100, 100, 100, 90]))
    high = strategy.on_bar(context(now, [100, 100, 100, 110]))

    assert low is not None and low.quantity_delta == Decimal("2")
    assert high is not None and high.quantity_delta == Decimal("-2")


def test_donchian_breakout_uses_prior_channel_not_current_bar(now):
    strategy = build(
        "donchian_breakout",
        {
            "lookback": 3,
            "atr_window": 2,
            "min_atr_pct": "0",
            **standard_position_params(),
        },
    )
    ctx = context(now, [100, 100, 100, 105])
    signal = strategy.on_bar(ctx)

    assert signal is not None
    assert signal.quantity_delta == Decimal("2")
    assert "donchian-up" in signal.reason


def test_volatility_regime_switch_requires_active_volatility(now):
    strategy = build(
        "volatility_regime",
        {
            "short_vol_window": 2,
            "long_vol_window": 4,
            "trend_window": 3,
            "vol_ratio_threshold": "1",
            **standard_position_params(),
        },
    )
    active = strategy.on_bar(context(now, [100, 100, 100, 100, 105]))
    calm = strategy.on_bar(context(now, [100, 100, 100, 100, 100]))

    assert active is not None and active.quantity_delta == Decimal("2")
    assert calm is None


def test_oss_strategy_runs_through_existing_future_bar_backtester(now):
    instrument = InstrumentMetadata(
        symbol="TEST-USD",
        venue="TEST",
        quote_currency="USD",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("1"),
    )
    dataset = MarketDataset(
        instrument=instrument,
        bars=make_bars(now, [100, 100, 100, 104, 106, 108, 100, 98, 96]),
        source="oss-1-future-bar-fixture",
    )
    strategy = build(
        "time_series_momentum",
        {
            "fast_horizon": 1,
            "slow_horizon": 3,
            "threshold": "0.01",
            **standard_position_params(),
        },
    )
    config = BacktestConfig(
        initial_cash=Decimal("100000"),
        cost_model=ExecutionCostModel(
            fee_bps=Decimal("1"),
            half_spread_bps=Decimal("1"),
            slippage_bps=Decimal("1"),
        ),
        execution_delay_bars=1,
        annualization_factor=Decimal("252"),
        max_leverage=Decimal("2"),
        max_volume_participation=Decimal("0.1"),
        allow_short=True,
    )

    result = BacktestEngine().run(dataset=dataset, strategy=strategy, config=config)

    assert result.strategy_id == "oss-time_series_momentum"
    assert result.fills
    for fill in result.fills:
        signal_bar_index = fill.bar_index - config.execution_delay_bars
        assert fill.occurred_at >= dataset.bars[signal_bar_index].ended_at
    assert result.metrics.total_fees > 0


def test_long_flat_mode_never_requests_negative_target(now):
    strategy = build(
        "time_series_momentum",
        {
            "fast_horizon": 1,
            "slow_horizon": 3,
            "threshold": "0.01",
            "order_quantity": "2",
            "position_mode": "long_flat",
        },
    )
    signal = strategy.on_bar(context(now, [104, 104, 104, 100], position="2"))

    assert signal is not None
    assert signal.quantity_delta == Decimal("-2")
    assert "momentum-down" in signal.reason


def test_new_dsl_still_rejects_dynamic_injection():
    injected = payload(
        "time_series_momentum",
        {
            "fast_horizon": 1,
            "slow_horizon": 3,
            "threshold": "0.01",
            **standard_position_params(),
            "callable": "os.system",
        },
    )
    with pytest.raises(InvalidStrategySpec, match="unknown strategy parameters"):
        StrategySpec.from_json(json.dumps(injected))
