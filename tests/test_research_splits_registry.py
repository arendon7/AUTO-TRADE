from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.backtest import BacktestConfig, BacktestEngine
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.registry import (
    ExperimentConflict,
    ExperimentSpec,
    HoldoutPermit,
    HoldoutPermitConsumed,
    SQLiteExperimentRegistry,
)
from autotrade.research.splits import (
    InvalidTemporalSplit,
    create_temporal_split,
    generate_walk_forward_folds,
)
from autotrade.research.strategy import ResearchSignal


def dataset(now, count=12):
    instrument = InstrumentMetadata(
        symbol="TEST-USD",
        venue="TEST",
        quote_currency="USD",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )
    bars = tuple(
        Bar(
            symbol="TEST-USD",
            started_at=now + timedelta(minutes=index),
            timeframe_seconds=60,
            open=Decimal("100") + index,
            high=Decimal("101") + index,
            low=Decimal("99") + index,
            close=Decimal("100") + index,
            volume=Decimal("10000"),
        )
        for index in range(count)
    )
    return MarketDataset(instrument=instrument, bars=bars, source="split-fixture")


def config():
    return BacktestConfig(
        initial_cash=Decimal("100000"),
        cost_model=ExecutionCostModel(
            fee_bps=Decimal("5"),
            half_spread_bps=Decimal("5"),
            slippage_bps=Decimal("5"),
        ),
        execution_delay_bars=1,
        annualization_factor=Decimal("252"),
        max_leverage=Decimal("2"),
        max_volume_participation=Decimal("0.10"),
    )


class RoundTrip:
    strategy_id = "registry-round-trip"
    strategy_version = "1.0.0"
    parameters = {"quantity": 10}

    def on_bar(self, context):
        if context.index == 0:
            return ResearchSignal(
                "open",
                context.symbol,
                context.current_bar.ended_at,
                Decimal("10"),
            )
        if context.index == 2:
            return ResearchSignal(
                "close",
                context.symbol,
                context.current_bar.ended_at,
                Decimal("-10"),
            )
        return None


def test_temporal_split_keeps_holdout_behind_permit(tmp_path, now):
    original = dataset(now)
    split = create_temporal_split(original, train_bars=5, development_bars=4)
    assert len(split.train.bars) == 5
    assert len(split.development.bars) == 4
    assert split.protected_holdout.bar_count == 3
    assert split.train.ended_at <= split.development.started_at
    assert split.development.ended_at <= split.protected_holdout.started_at

    registry_path = tmp_path / "experiments.db"
    registry = SQLiteExperimentRegistry(registry_path)
    permit = HoldoutPermit(permit_id="final-001", issued_by="human-reviewer")
    checked_out = split.protected_holdout.checkout(
        permit=permit,
        registry=registry,
        now=now,
    )
    assert checked_out.dataset_hash == split.protected_holdout.dataset_hash

    restarted_registry = SQLiteExperimentRegistry(registry_path)
    with pytest.raises(HoldoutPermitConsumed, match="final-001"):
        split.protected_holdout.checkout(
            permit=permit,
            registry=restarted_registry,
            now=now,
        )


def test_holdout_permit_requires_final_validation():
    with pytest.raises(ValueError, match="permit_id"):
        HoldoutPermit("", "reviewer")
    with pytest.raises(ValueError, match="issued_by"):
        HoldoutPermit("p1", "")
    with pytest.raises(ValueError, match="final_validation"):
        HoldoutPermit("p1", "reviewer", purpose="tuning")


@pytest.mark.parametrize(
    "train_bars, development_bars",
    [(0, 2), (2, 0), (8, 4)],
)
def test_invalid_temporal_split_is_rejected(now, train_bars, development_bars):
    with pytest.raises(InvalidTemporalSplit):
        create_temporal_split(
            dataset(now, count=10),
            train_bars=train_bars,
            development_bars=development_bars,
        )


def test_walk_forward_folds_are_strictly_temporal(now):
    data = dataset(now, count=10)
    folds = generate_walk_forward_folds(
        data,
        train_bars=4,
        evaluation_bars=2,
        step_bars=2,
    )
    assert len(folds) == 3
    assert [fold.fold_index for fold in folds] == [0, 1, 2]
    assert [len(fold.train.bars) for fold in folds] == [4, 4, 4]
    for fold in folds:
        assert fold.train.ended_at <= fold.evaluation.started_at

    expanding = generate_walk_forward_folds(
        data,
        train_bars=4,
        evaluation_bars=2,
        step_bars=2,
        expanding=True,
    )
    assert [len(fold.train.bars) for fold in expanding] == [4, 6, 8]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"train_bars": 0, "evaluation_bars": 2},
        {"train_bars": 4, "evaluation_bars": 0},
        {"train_bars": 4, "evaluation_bars": 2, "step_bars": 0},
        {"train_bars": 9, "evaluation_bars": 2},
    ],
)
def test_invalid_walk_forward_config_is_rejected(now, kwargs):
    with pytest.raises(InvalidTemporalSplit):
        generate_walk_forward_folds(dataset(now, count=10), **kwargs)


def test_experiment_registry_is_idempotent_and_persistent(tmp_path, now):
    data = dataset(now, count=6)
    strategy = RoundTrip()
    result = BacktestEngine().run(dataset=data, strategy=strategy, config=config())
    spec = ExperimentSpec(
        dataset_hash=data.dataset_hash,
        split_name="development",
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.strategy_version,
        strategy_parameters=strategy.parameters,
        config_hash=config().config_hash,
        code_version="research-v0.4-test",
    )
    path = tmp_path / "registry.db"
    registry = SQLiteExperimentRegistry(path)
    first = registry.record(
        spec=spec,
        result=result,
        now=now,
        artifacts={"notes": "artifact://notes"},
    )
    replay = registry.record(spec=spec, result=result, now=now)
    assert replay.run_id == first.run_id
    assert replay.result_hash == first.result_hash
    assert first.metrics["profit_factor"] == "inf"

    reopened = SQLiteExperimentRegistry(path)
    loaded = reopened.get(first.run_id)
    assert loaded == first
    assert reopened.get("missing") is None
    assert reopened.list_records() == (first,)


def test_registry_detects_spec_mismatch_and_nondeterministic_replay(tmp_path, now):
    data = dataset(now, count=6)
    strategy = RoundTrip()
    result = BacktestEngine().run(dataset=data, strategy=strategy, config=config())
    spec = ExperimentSpec(
        dataset_hash=data.dataset_hash,
        split_name="development",
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.strategy_version,
        strategy_parameters=strategy.parameters,
        config_hash=config().config_hash,
        code_version="sha-1",
    )
    registry = SQLiteExperimentRegistry(tmp_path / "registry.db")

    with pytest.raises(ExperimentConflict, match="dataset hash"):
        registry.record(
            spec=replace(spec, dataset_hash="wrong"),
            result=result,
            now=now,
        )

    registry.record(spec=spec, result=result, now=now)
    altered = replace(result, equity_curve=result.equity_curve[:-1])
    with pytest.raises(ExperimentConflict, match="different result hash"):
        registry.record(spec=spec, result=altered, now=now)


def test_experiment_spec_requires_identity_fields_and_serializable_parameters():
    base = {
        "dataset_hash": "data",
        "split_name": "dev",
        "strategy_id": "strategy",
        "strategy_version": "1",
        "strategy_parameters": {"x": 1},
        "config_hash": "config",
        "code_version": "sha",
    }
    for field in (
        "dataset_hash",
        "split_name",
        "strategy_id",
        "strategy_version",
        "config_hash",
        "code_version",
    ):
        values = dict(base)
        values[field] = ""
        with pytest.raises(ValueError, match=field):
            ExperimentSpec(**values)

    with pytest.raises(TypeError):
        ExperimentSpec(**{**base, "strategy_parameters": {"bad": object()}})
