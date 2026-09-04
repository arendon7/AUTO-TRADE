from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.research.costs import ExecutionCostModel
from autotrade.research.cross_sectional_backtest import CrossSectionalBacktestEngine
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.oss2_campaign import (
    CommonWindowMetricsEvidence,
    backtest_config_from_oss2_trial,
    build_oss2_development_campaign,
    evaluate_oss2_common_window,
)
from autotrade.research.trials import TrialPhase
from autotrade.research.universe import AlignedMarketUniverse


D = Decimal


def _dataset(now, *, symbol, closes):
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
            volume=D("100000"),
        )
        for index, close in enumerate(closes)
    )
    return MarketDataset(
        instrument=instrument,
        bars=bars,
        source=f"oss2c-adversarial:{symbol}",
    )


def _market(now, *, name="OSS2C-ADVERSARIAL", count=130):
    return AlignedMarketUniverse.from_datasets(
        universe_name=name,
        datasets=(
            _dataset(now, symbol="AAA-USD", closes=[100 + i for i in range(count)]),
            _dataset(now, symbol="BBB-USD", closes=[100 + i // 2 for i in range(count)]),
            _dataset(now, symbol="CCC-USD", closes=[160 - i // 4 for i in range(count)]),
        ),
    )


def _costs():
    return ExecutionCostModel(
        fee_bps=D("1"),
        half_spread_bps=D("1"),
        slippage_bps=D("1"),
    )


def _campaign_kwargs(market):
    return {
        "universe_hash": market.universe_hash,
        "code_version": "oss2c-adversarial-v1",
        "initial_cash": D("100000"),
        "annualization_factor": D("365"),
        "cost_model": _costs(),
        "top_n": 2,
        "min_average_dollar_volume": D("0"),
        "max_weight_per_asset": D("0.45"),
        "gross_target": D("0.90"),
        "max_volume_participation": D("0.10"),
        "min_trade_notional": D("1"),
        "require_positive_momentum": True,
    }


def _plan(market):
    return build_oss2_development_campaign(**_campaign_kwargs(market))


@pytest.mark.parametrize(
    "mutation,message",
    [
        ({"universe_hash": ""}, "universe_hash is required"),
        ({"code_version": ""}, "code_version is required"),
        ({"split_name": ""}, "cannot be HOLDOUT"),
    ],
)
def test_campaign_identity_inputs_fail_closed(now, mutation, message):
    market = _market(now)
    kwargs = _campaign_kwargs(market)
    kwargs.update(mutation)
    with pytest.raises(ValueError, match=message):
        build_oss2_development_campaign(**kwargs)


def test_campaign_rejects_wrong_cost_model_type(now):
    market = _market(now)
    kwargs = _campaign_kwargs(market)
    kwargs["cost_model"] = object()
    with pytest.raises(TypeError, match="ExecutionCostModel"):
        build_oss2_development_campaign(**kwargs)


def test_trial_config_rejects_non_development_phase(now):
    trial = _plan(_market(now)).trials[0]
    with pytest.raises(ValueError, match="must be DEVELOPMENT"):
        backtest_config_from_oss2_trial(replace(trial, phase=TrialPhase.TRAIN))


def test_trial_config_rejects_wrong_hypothesis(now):
    trial = _plan(_market(now)).trials[0]
    with pytest.raises(ValueError, match="not an OSS-2"):
        backtest_config_from_oss2_trial(replace(trial, hypothesis_id="other"))


def test_trial_config_rejects_parameter_surface_expansion(now):
    trial = _plan(_market(now)).trials[0]
    mutated = replace(
        trial,
        parameters={**dict(trial.parameters), "broker": "should-never-be-accepted"},
    )
    with pytest.raises(ValueError, match="parameter contract mismatch"):
        backtest_config_from_oss2_trial(mutated)


def test_trial_config_rejects_ranking_fingerprint_tamper(now):
    trial = _plan(_market(now)).trials[0]
    mutated = replace(
        trial,
        parameters={**dict(trial.parameters), "ranking_fingerprint": "0" * 64},
    )
    with pytest.raises(ValueError, match="ranking fingerprint mismatch"):
        backtest_config_from_oss2_trial(mutated)


def test_trial_config_rejects_backtest_hash_tamper(now):
    trial = _plan(_market(now)).trials[0]
    mutated = replace(
        trial,
        parameters={**dict(trial.parameters), "backtest_config_hash": "0" * 64},
    )
    with pytest.raises(ValueError, match="backtest config hash mismatch"):
        backtest_config_from_oss2_trial(mutated)


def test_trial_config_rejects_invalid_scalar_types(now):
    trial = _plan(_market(now)).trials[0]

    bad_int = replace(
        trial,
        parameters={**dict(trial.parameters), "ranking_lookback_bars": True},
    )
    with pytest.raises(ValueError, match="invalid integer parameter"):
        backtest_config_from_oss2_trial(bad_int)

    bad_bool = replace(
        trial,
        parameters={**dict(trial.parameters), "ranking_require_positive_momentum": "true"},
    )
    with pytest.raises(ValueError, match="invalid bool parameter"):
        backtest_config_from_oss2_trial(bad_bool)

    bad_decimal = replace(
        trial,
        parameters={**dict(trial.parameters), "initial_cash": "NaN"},
    )
    with pytest.raises(ValueError, match="invalid Decimal parameter"):
        backtest_config_from_oss2_trial(bad_decimal)


def test_common_window_rejects_universe_identity_substitution(now):
    market = _market(now)
    trial = _plan(market).trials[0]
    result = CrossSectionalBacktestEngine().run(
        universe=market,
        config=backtest_config_from_oss2_trial(trial),
    )
    other = AlignedMarketUniverse.from_datasets(
        universe_name="OTHER-UNIVERSE",
        datasets=market.datasets,
    )
    with pytest.raises(ValueError, match="universe identity mismatch"):
        evaluate_oss2_common_window(result=result, universe=other, trial=trial)


def test_common_window_rejects_start_outside_universe(now):
    market = _market(now)
    trial = _plan(market).trials[0]
    result = CrossSectionalBacktestEngine().run(
        universe=market,
        config=backtest_config_from_oss2_trial(trial),
    )
    mutated = replace(
        trial,
        parameters={
            **dict(trial.parameters),
            "common_window_start_bar_index": market.bar_count,
        },
    )
    with pytest.raises(ValueError, match="starts outside universe"):
        evaluate_oss2_common_window(result=result, universe=market, trial=mutated)


def _valid_evidence():
    start = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    return CommonWindowMetricsEvidence(
        source_result_hash="a" * 64,
        universe_hash="b" * 64,
        start_bar_index=97,
        start_at=start,
        end_at=start + timedelta(minutes=1),
        observation_count=2,
        net_return=0.01,
        annualized_volatility=0.2,
        sharpe=1.0,
        sortino=1.1,
        max_drawdown=0.05,
    )


@pytest.mark.parametrize(
    "mutation,message",
    [
        ({"source_result_hash": "bad"}, "SHA-256"),
        ({"start_bar_index": -1}, "start_bar_index"),
        ({"start_at": datetime(2026, 9, 4, 12, 0)}, "start_at"),
        ({"end_at": datetime(2026, 9, 4, 12, 1)}, "end_at"),
        ({"observation_count": 1}, "at least 2 observations"),
        ({"sharpe": float("nan")}, "sharpe must be finite"),
        ({"annualized_volatility": -0.1}, "invalid common-window risk metric"),
        ({"max_drawdown": 1.1}, "invalid common-window risk metric"),
    ],
)
def test_common_window_evidence_rejects_invalid_provenance_and_metrics(mutation, message):
    with pytest.raises(ValueError, match=message):
        replace(_valid_evidence(), **mutation)


def test_common_window_evidence_rejects_reverse_time():
    evidence = _valid_evidence()
    with pytest.raises(ValueError, match="cannot precede"):
        replace(evidence, end_at=evidence.start_at - timedelta(minutes=1))
