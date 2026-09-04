from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.costs import ExecutionCostModel
from autotrade.research.cross_sectional_backtest import CrossSectionalBacktestEngine
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.oss2_campaign import (
    backtest_config_from_oss2_trial,
    build_oss2_development_campaign,
    evaluate_oss2_common_window,
    oss2_candidate_count,
)
from autotrade.research.tournament import (
    RankingDirection,
    evaluate_strategy_tournament,
)
from autotrade.research.trials import SQLiteTrialLedger, TrialPhase
from autotrade.research.universe import AlignedMarketUniverse


D = Decimal


def make_dataset(now, *, symbol, closes, volume="100000"):
    instrument = InstrumentMetadata(
        symbol=symbol,
        venue="TEST",
        quote_currency="USD",
        price_tick=D("0.01"),
        quantity_step=D("0.001"),
    )
    bars = tuple(
        Bar(
            symbol=symbol,
            started_at=now + timedelta(minutes=index),
            timeframe_seconds=60,
            open=D(str(close)),
            high=D(str(close)) + D("1"),
            low=D(str(close)) - D("1"),
            close=D(str(close)),
            volume=D(volume),
        )
        for index, close in enumerate(closes)
    )
    return MarketDataset(
        instrument=instrument,
        bars=bars,
        source=f"oss2c:{symbol}",
    )


def make_universe(now, count=130):
    a = [100 + index for index in range(count)]
    b = [100 + index // 2 for index in range(count)]
    c = [160 - index // 4 for index in range(count)]
    return AlignedMarketUniverse.from_datasets(
        universe_name="OSS2C-TEST",
        datasets=(
            make_dataset(now, symbol="AAA-USD", closes=a),
            make_dataset(now, symbol="BBB-USD", closes=b),
            make_dataset(now, symbol="CCC-USD", closes=c),
        ),
    )


def cost_model():
    return ExecutionCostModel(
        fee_bps=D("1"),
        half_spread_bps=D("1"),
        slippage_bps=D("1"),
    )


def build_plan(market):
    return build_oss2_development_campaign(
        universe_hash=market.universe_hash,
        code_version="oss2c-test-v1",
        initial_cash=D("100000"),
        annualization_factor=D("365"),
        cost_model=cost_model(),
        top_n=2,
        min_average_dollar_volume=D("0"),
        max_weight_per_asset=D("0.45"),
        gross_target=D("0.90"),
        max_volume_participation=D("0.10"),
        min_trade_notional=D("1"),
        require_positive_momentum=True,
    )


def test_oss2_campaign_freezes_complete_common_window_universe(now):
    market = make_universe(now)
    plan = build_plan(market)

    assert oss2_candidate_count() == 12
    assert len(plan.trials) == 12
    assert plan.common_window_start_bar_index == 97
    assert plan.campaign.expected_trial_ids == tuple(
        trial.trial_id for trial in plan.trials
    )
    assert plan.tournament.candidate_trial_ids == plan.campaign.expected_trial_ids
    assert plan.tournament.metric_name == "common_window_sharpe"
    assert plan.tournament.direction is RankingDirection.MAXIMIZE
    assert all(trial.phase is TrialPhase.DEVELOPMENT for trial in plan.trials)
    assert all(not trial.holdout_authorization_id for trial in plan.trials)
    assert {
        trial.parameters["ranking_lookback_bars"] for trial in plan.trials
    } == {12, 24, 48, 96}
    assert {
        trial.parameters["rebalance_every_bars"] for trial in plan.trials
    } == {1, 4, 12}
    assert all(
        trial.parameters["common_window_start_bar_index"] == 97
        for trial in plan.trials
    )


def test_oss2_trial_reconstructs_exact_backtest_identity(now):
    market = make_universe(now)
    plan = build_plan(market)

    for trial in plan.trials:
        config = backtest_config_from_oss2_trial(trial)
        assert config.config_hash == trial.parameters["backtest_config_hash"]
        assert config.ranking.fingerprint == trial.parameters["ranking_fingerprint"]
        assert config.ranking.lookback_bars == trial.parameters["ranking_lookback_bars"]
        assert config.rebalance_every_bars == trial.parameters["rebalance_every_bars"]


def test_common_window_removes_warmup_advantage_across_lookbacks(now):
    market = make_universe(now)
    plan = build_plan(market)
    short = next(
        trial
        for trial in plan.trials
        if trial.parameters["ranking_lookback_bars"] == 12
        and trial.parameters["rebalance_every_bars"] == 1
    )
    long = next(
        trial
        for trial in plan.trials
        if trial.parameters["ranking_lookback_bars"] == 96
        and trial.parameters["rebalance_every_bars"] == 1
    )

    engine = CrossSectionalBacktestEngine()
    short_result = engine.run(
        universe=market,
        config=backtest_config_from_oss2_trial(short),
    )
    long_result = engine.run(
        universe=market,
        config=backtest_config_from_oss2_trial(long),
    )
    assert len(short_result.period_returns) > len(long_result.period_returns)

    short_evidence = evaluate_oss2_common_window(
        result=short_result,
        universe=market,
        trial=short,
    )
    long_evidence = evaluate_oss2_common_window(
        result=long_result,
        universe=market,
        trial=long,
    )

    assert short_evidence.start_bar_index == long_evidence.start_bar_index == 97
    assert short_evidence.start_at == long_evidence.start_at
    assert short_evidence.end_at == long_evidence.end_at
    assert short_evidence.observation_count == long_evidence.observation_count == 33
    assert short_evidence.ledger_metrics()["common_window_sharpe"] == short_evidence.sharpe


def test_common_window_rejects_result_from_different_trial_config(now):
    market = make_universe(now)
    plan = build_plan(market)
    first, second = plan.trials[0], plan.trials[1]
    result = CrossSectionalBacktestEngine().run(
        universe=market,
        config=backtest_config_from_oss2_trial(first),
    )

    with pytest.raises(ValueError, match="config"):
        evaluate_oss2_common_window(
            result=result,
            universe=market,
            trial=second,
        )


def test_common_window_rejects_truncated_result(now):
    market = make_universe(now)
    plan = build_plan(market)
    trial = plan.trials[0]
    result = CrossSectionalBacktestEngine().run(
        universe=market,
        config=backtest_config_from_oss2_trial(trial),
    )
    truncated = replace(result, period_returns=result.period_returns[:-1])

    with pytest.raises(ValueError, match="exact common evaluation window"):
        evaluate_oss2_common_window(
            result=truncated,
            universe=market,
            trial=trial,
        )


def test_oss2_campaign_rejects_holdout_and_does_not_smuggle_authority(now):
    market = make_universe(now)
    with pytest.raises(ValueError, match="cannot be HOLDOUT"):
        build_oss2_development_campaign(
            universe_hash=market.universe_hash,
            code_version="v1",
            initial_cash=D("100000"),
            annualization_factor=D("365"),
            cost_model=cost_model(),
            top_n=2,
            min_average_dollar_volume=D("0"),
            max_weight_per_asset=D("0.45"),
            gross_target=D("0.90"),
            max_volume_participation=D("0.10"),
            min_trade_notional=D("1"),
            split_name="final_holdout",
        )

    plan = build_plan(market)
    forbidden = {
        "broker",
        "credentials",
        "oms",
        "order_intent",
        "paper_execution_authorized",
        "live_authority",
        "capital_authority",
        "network",
    }
    for trial in plan.trials:
        assert forbidden.isdisjoint(set(trial.parameters))


def test_oss2_campaign_runs_end_to_end_through_existing_trial_tournament(now, tmp_path):
    market = make_universe(now)
    plan = build_plan(market)
    ledger = SQLiteTrialLedger(tmp_path / "oss2c.sqlite")
    ledger.create_campaign(plan.campaign, now=now)
    engine = CrossSectionalBacktestEngine()

    for offset, trial in enumerate(plan.trials):
        ledger.preregister(trial, now=now + timedelta(seconds=offset + 1))
        result = engine.run(
            universe=market,
            config=backtest_config_from_oss2_trial(trial),
        )
        evidence = evaluate_oss2_common_window(
            result=result,
            universe=market,
            trial=trial,
        )
        ledger.record_completed(
            trial_id=trial.trial_id,
            metrics=evidence.ledger_metrics(),
            p_value=None,
            now=now + timedelta(minutes=1, seconds=offset),
        )

    tournament = evaluate_strategy_tournament(ledger, plan.tournament)

    assert tournament.metric_name == "common_window_sharpe"
    assert tournament.completed_count == 12
    assert tournament.failed_count == 0
    assert tournament.winner_trial_id
    assert len(tournament.entries) == 12
    assert all(entry.eligible for entry in tournament.entries)
