from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from math import cos, sin

import pytest

from autotrade.research.costs import ExecutionCostModel
from autotrade.research.cross_sectional_backtest import CrossSectionalBacktestEngine
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.multiple_testing import campaign_pbo
from autotrade.research.oss2_campaign import (
    backtest_config_from_oss2_trial,
    build_oss2_development_campaign,
    evaluate_oss2_common_window,
)
from autotrade.research.oss2_robustness import (
    OSS2RobustnessGovernanceError,
    canonical_oss2d_policy,
    run_oss2d_robustness,
)
from autotrade.research.trials import (
    CampaignSpec,
    SQLiteTrialLedger,
    TrialPhase,
    TrialSpec,
)
from autotrade.research.universe import AlignedMarketUniverse


D = Decimal


def make_dataset(now, *, symbol, closes, volume="250000"):
    instrument = InstrumentMetadata(
        symbol=symbol,
        venue="TEST",
        quote_currency="USD",
        price_tick=D("0.000001"),
        quantity_step=D("0.001"),
    )
    bars = tuple(
        Bar(
            symbol=symbol,
            started_at=now + timedelta(minutes=index),
            timeframe_seconds=60,
            open=close,
            high=close + D("0.50"),
            low=close - D("0.50"),
            close=close,
            volume=D(volume),
        )
        for index, close in enumerate(closes)
    )
    return MarketDataset(
        instrument=instrument,
        bars=bars,
        source=f"oss2d:{symbol}",
    )


def make_universe(now, count=161):
    def series(fn):
        return [D(f"{fn(index):.6f}") for index in range(count)]

    return AlignedMarketUniverse.from_datasets(
        universe_name="OSS2D-TEST",
        datasets=(
            make_dataset(
                now,
                symbol="AAA-USD",
                closes=series(lambda i: 100 + 0.12 * i + 2.2 * sin(i / 5.0)),
            ),
            make_dataset(
                now,
                symbol="BBB-USD",
                closes=series(lambda i: 100 + 0.09 * i + 1.7 * cos(i / 7.0)),
            ),
            make_dataset(
                now,
                symbol="CCC-USD",
                closes=series(lambda i: 100 + 0.055 * i + 1.4 * sin(i / 3.0)),
            ),
            make_dataset(
                now,
                symbol="DDD-USD",
                closes=series(lambda i: 100 + 0.025 * i + 1.1 * cos(i / 4.0)),
            ),
        ),
    )


def cost_model():
    return ExecutionCostModel(
        fee_bps=D("1.0"),
        half_spread_bps=D("1.5"),
        slippage_bps=D("1.5"),
    )


def build_plan(market):
    return build_oss2_development_campaign(
        universe_hash=market.universe_hash,
        code_version="oss2d-test-v1",
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


def run_campaign(now, tmp_path):
    market = make_universe(now)
    plan = build_plan(market)
    ledger = SQLiteTrialLedger(tmp_path / "oss2d.sqlite")
    ledger.create_campaign(plan.campaign, now=now)
    engine = CrossSectionalBacktestEngine()
    results = {}
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
            now=now + timedelta(minutes=2, seconds=offset),
        )
        results[trial.trial_id] = result
    return market, plan, ledger, results


def test_balanced_pbo_keeps_every_common_window_observation(now, tmp_path):
    ledger = SQLiteTrialLedger(tmp_path / "balanced.sqlite")
    campaign = CampaignSpec("c", "f", ("a", "b"), "code", "balanced-pbo")
    ledger.create_campaign(campaign, now=now)
    for index, trial_id in enumerate(("a", "b")):
        ledger.preregister(
            TrialSpec(
                trial_id=trial_id,
                campaign_id="c",
                hypothesis_id="h",
                strategy_id=f"s-{trial_id}",
                strategy_version="1",
                dataset_hash="d" * 64,
                split_name="development",
                phase=TrialPhase.DEVELOPMENT,
                parameters={"x": index},
                code_version="code",
            ),
            now=now + timedelta(seconds=index + 1),
        )
        ledger.record_completed(
            trial_id=trial_id,
            metrics={"sharpe": 1.0 + index},
            p_value=None,
            now=now + timedelta(seconds=10 + index),
        )

    a = tuple(0.004 + (index % 5) * 0.0007 for index in range(33))
    b = tuple(-0.001 + (index % 7) * 0.0004 for index in range(33))
    with pytest.raises(ValueError, match="divide evenly"):
        campaign_pbo(ledger, "c", {"a": a, "b": b}, partitions=8)

    evidence = campaign_pbo(
        ledger,
        "c",
        {"a": a, "b": b},
        partitions=8,
        balanced_partitions=True,
    )
    assert evidence.balanced_partitions is True
    assert evidence.partition_sizes == (5, 4, 4, 4, 4, 4, 4, 4)
    assert sum(evidence.partition_sizes) == 33
    assert evidence.combinations_evaluated == 70
    assert 0 <= evidence.pbo <= 1


def test_oss2d_builds_complete_reproducible_development_evidence(now, tmp_path):
    market, plan, ledger, results = run_campaign(now, tmp_path)

    evidence = run_oss2d_robustness(
        ledger=ledger,
        plan=plan,
        universe=market,
        results_by_trial=results,
    )

    assert evidence.campaign_id == plan.campaign.campaign_id
    assert evidence.universe_hash == market.universe_hash
    assert evidence.policy_fingerprint == canonical_oss2d_policy().fingerprint
    assert evidence.selected_trial_id in plan.campaign.expected_trial_ids
    assert evidence.pbo.partitions == 8
    assert evidence.pbo.balanced_partitions is True
    assert evidence.pbo.combinations_evaluated == 70
    assert sum(evidence.pbo.partition_sizes) == 64
    assert evidence.deflated_sharpe.family_size == 12
    assert evidence.deflated_sharpe.sample_size == 64
    assert evidence.deflated_sharpe.selected_trial_id == evidence.selected_trial_id
    assert evidence.bootstrap.observations == 64
    assert evidence.bootstrap.iterations == 2000
    assert evidence.bootstrap.block_size == 4
    assert evidence.bootstrap.seed == 20260904
    assert 0 <= evidence.bootstrap.probability_positive <= 1
    assert tuple(item.multiplier for item in evidence.cost_stress) == (D("1.5"), D("2.0"))
    assert all(item.total_cost_bps > cost_model().total_bps for item in evidence.cost_stress)
    assert 2 <= len(evidence.local_sensitivity.neighbors) <= 4
    assert len(evidence.fingerprint) == 64


def test_oss2d_rejects_missing_or_truncated_result_universe(now, tmp_path):
    market, plan, ledger, results = run_campaign(now, tmp_path)
    missing = dict(results)
    missing.pop(plan.trials[0].trial_id)
    with pytest.raises(OSS2RobustnessGovernanceError, match="exactly match"):
        run_oss2d_robustness(
            ledger=ledger,
            plan=plan,
            universe=market,
            results_by_trial=missing,
        )

    target = plan.trials[0].trial_id
    tampered = dict(results)
    tampered[target] = replace(
        tampered[target],
        period_returns=tampered[target].period_returns[:-1],
    )
    with pytest.raises(ValueError, match="exact common evaluation window"):
        run_oss2d_robustness(
            ledger=ledger,
            plan=plan,
            universe=market,
            results_by_trial=tampered,
        )


def test_oss2d_policy_surface_is_frozen_and_research_only():
    policy = canonical_oss2d_policy()
    assert policy.pbo_partitions == 8
    assert policy.pbo_balanced_partitions is True
    assert policy.bootstrap_iterations == 2000
    assert policy.bootstrap_block_size == 4
    assert policy.bootstrap_seed == 20260904
    assert policy.cost_stress_multipliers == (D("1.5"), D("2.0"))

    forbidden = {
        "broker",
        "credentials",
        "oms",
        "order_intent",
        "paper_execution_authorized",
        "live_authority",
        "capital_authority",
        "network",
        "holdout",
    }
    assert forbidden.isdisjoint(policy.__dataclass_fields__)
