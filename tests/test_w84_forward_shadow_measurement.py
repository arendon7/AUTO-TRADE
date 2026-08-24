from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

import pytest

import autotrade.forward_shadow_measurement as measurement
from autotrade.forward_shadow_measurement import (
    ForwardShadowMeasurementIntegrityError,
    build_forward_measurement_plan,
    build_forward_measurement_runtime_identity,
    build_forward_shadow_measurements,
    measurement_receipts_hash,
    verify_shadow_measurement_binding,
)
from autotrade.research.backtest import BacktestConfig
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.market import Bar, MarketDataset
from autotrade.research.shadow import (
    FrozenShadowConfig,
    SQLitePortfolioShadowRegistry,
    StrategyShadowObservation,
)
from test_w83_promotion_strategy_version_resolution import _runtime_chain, _resolve


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _w83(limits, market, empty_portfolio, market_buy_intent):
    chain, binding = _runtime_chain(
        limits, market, empty_portfolio, market_buy_intent
    )
    resolution = _resolve(chain, binding, market_buy_intent)
    return chain, binding, resolution


def _bar(symbol, start, close, *, open_value=None):
    close = Decimal(str(close))
    open_value = close if open_value is None else Decimal(str(open_value))
    return Bar(
        symbol=symbol,
        started_at=start,
        timeframe_seconds=60,
        open=open_value,
        high=max(open_value, close) + Decimal("1"),
        low=min(open_value, close) - Decimal("1"),
        close=close,
        volume=Decimal("1000"),
    )


def _measurement_inputs(chain, resolution):
    activation = resolution.resolved_at + timedelta(minutes=10)
    planned_at = activation - timedelta(minutes=1)
    instrument = chain["dataset"].instrument
    source = "w84-forward-source"
    history = MarketDataset(
        instrument=instrument,
        bars=(
            _bar(instrument.symbol, planned_at - timedelta(minutes=3), "10"),
            _bar(instrument.symbol, planned_at - timedelta(minutes=2), "10"),
            _bar(instrument.symbol, planned_at - timedelta(minutes=1), "10"),
        ),
        source=source,
    )
    post_freeze = MarketDataset(
        instrument=instrument,
        bars=(
            # Bridge bar: occurs after the policy freeze and establishes state,
            # but is deliberately not qualification evidence.
            _bar(instrument.symbol, planned_at, "11", open_value="10"),
            _bar(instrument.symbol, activation, "12", open_value="11"),
            _bar(
                instrument.symbol,
                activation + timedelta(minutes=1),
                "12",
                open_value="12",
            ),
        ),
        source=source,
    )
    config = BacktestConfig(
        initial_cash=Decimal("100000"),
        cost_model=ExecutionCostModel(
            fee_bps=Decimal("1"),
            half_spread_bps=Decimal("1"),
            slippage_bps=Decimal("1"),
        ),
        execution_delay_bars=1,
        annualization_factor=Decimal("525600"),
        max_leverage=Decimal("1"),
        max_volume_participation=Decimal("1"),
        allow_short=False,
    )
    return planned_at, activation, history, post_freeze, config


def _plan(chain, binding, resolution, history, config, planned_at, activation):
    return build_forward_measurement_plan(
        plan_id="w84-measurement-plan",
        w83_resolution=resolution,
        binding_evidence=binding,
        strategy_spec=chain["spec"],
        backtest_config=config,
        history_dataset=history,
        planned_at=planned_at,
        forward_activated_at=activation,
    )


def _receipts(
    chain,
    binding,
    resolution,
    plan,
    history,
    post_freeze,
    config,
    *,
    captured_at=None,
):
    return build_forward_shadow_measurements(
        plan=plan,
        policy_hash=h("w84-policy"),
        w83_resolution=resolution,
        binding_evidence=binding,
        strategy_spec=chain["spec"],
        backtest_config=config,
        history_dataset=history,
        post_freeze_dataset=post_freeze,
        captured_at=captured_at or post_freeze.ended_at + timedelta(seconds=1),
    )


def test_w84_measurement_runtime_binds_w83_backtest_costs_domain_and_exact_python():
    first = build_forward_measurement_runtime_identity()
    second = build_forward_measurement_runtime_identity()

    assert first == second
    assert len(first.identity_hash) == 64
    assert len(first.w83_runtime_hash) == 64
    assert len(first.backtest_source_hash) == 64
    assert len(first.costs_source_hash) == 64
    assert len(first.domain_source_hash) == 64
    assert first.python_runtime.count(".") == 2
    assert first.to_dict()["identity_hash"] == first.identity_hash


def test_w84_measurement_plan_freezes_only_preoutcome_history_and_exact_candidate(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, _, config = _measurement_inputs(chain, resolution)
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )

    assert history.ended_at == planned_at
    assert planned_at < activation
    assert plan.w83_resolution_hash == resolution.resolution_hash
    assert plan.w83_binding_hash == binding.evidence_hash
    assert plan.strategy_spec_hash == chain["spec"].canonical_hash
    assert plan.w83_runtime_hash == resolution.loaded_runtime_code_hash
    assert plan.backtest_config_hash == config.config_hash
    assert plan.history_dataset_hash == history.dataset_hash
    assert plan.dataset_source == history.source
    assert plan.timeframe_seconds == 60
    assert plan.history_bars == 3
    assert plan.planned_at == planned_at
    assert plan.forward_activated_at == activation
    assert plan.paper_candidate_authorized is False
    assert plan.capital_authority == "NONE"
    assert plan.live_trading == "BLOCKED"


def test_w84_measurement_plan_rejects_posthoc_history_or_candidate_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, _, config = _measurement_inputs(chain, resolution)

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="StrategySpec"):
        build_forward_measurement_plan(
            plan_id="bad-spec",
            w83_resolution=resolution,
            binding_evidence=binding,
            strategy_spec=replace(chain["spec"], initial_stop_pct=Decimal("0.06")),
            backtest_config=config,
            history_dataset=history,
            planned_at=planned_at,
            forward_activated_at=activation,
        )

    different_instrument = replace(history.instrument, venue="other-venue")
    wrong_market = MarketDataset(
        instrument=different_instrument,
        bars=history.bars,
        source=history.source,
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="market identity"):
        build_forward_measurement_plan(
            plan_id="bad-market",
            w83_resolution=resolution,
            binding_evidence=binding,
            strategy_spec=chain["spec"],
            backtest_config=config,
            history_dataset=wrong_market,
            planned_at=planned_at,
            forward_activated_at=activation,
        )

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="end exactly"):
        build_forward_measurement_plan(
            plan_id="bad-freeze",
            w83_resolution=resolution,
            binding_evidence=binding,
            strategy_spec=chain["spec"],
            backtest_config=config,
            history_dataset=history,
            planned_at=planned_at + timedelta(minutes=1),
            forward_activated_at=activation,
        )

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="strictly predate"):
        build_forward_measurement_plan(
            plan_id="late-freeze",
            w83_resolution=resolution,
            binding_evidence=binding,
            strategy_spec=chain["spec"],
            backtest_config=config,
            history_dataset=history,
            planned_at=planned_at,
            forward_activated_at=planned_at,
        )


def test_w84_bridge_bar_is_processed_but_excluded_from_qualification(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )
    receipts = _receipts(
        chain, binding, resolution, plan, history, post_freeze, config
    )

    assert len(post_freeze.bars) == 3
    assert len(receipts) == 2
    assert post_freeze.bars[0].started_at == planned_at
    assert post_freeze.bars[0].ended_at == activation
    assert receipts[0].period_started_at == activation
    assert receipts[0].return_fraction > 0


def test_w84_forward_measurement_is_prefix_only_and_future_bar_cannot_change_first_hash(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )
    full = _receipts(
        chain, binding, resolution, plan, history, post_freeze, config
    )

    first_only_dataset = MarketDataset(
        instrument=post_freeze.instrument,
        bars=post_freeze.bars[:2],
        source=post_freeze.source,
    )
    first_only = _receipts(
        chain,
        binding,
        resolution,
        plan,
        history,
        first_only_dataset,
        config,
        captured_at=first_only_dataset.ended_at + timedelta(seconds=1),
    )

    changed_future = MarketDataset(
        instrument=post_freeze.instrument,
        bars=(
            post_freeze.bars[0],
            post_freeze.bars[1],
            _bar(
                post_freeze.instrument.symbol,
                activation + timedelta(minutes=1),
                "40",
                open_value="12",
            ),
        ),
        source=post_freeze.source,
    )
    changed = _receipts(
        chain, binding, resolution, plan, history, changed_future, config
    )

    assert full[0].measurement_hash == first_only[0].measurement_hash
    assert full[0].measurement_hash == changed[0].measurement_hash
    assert full[0].prefix_dataset_hash == first_only[0].prefix_dataset_hash
    assert full[1].measurement_hash != changed[1].measurement_hash
    assert full[0].previous_measurement_hash == measurement.GENESIS_MEASUREMENT_HASH
    assert full[1].previous_measurement_hash == full[0].measurement_hash


def test_w84_measurement_recomputes_return_and_builds_exact_shadow_observation(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )
    receipts = _receipts(
        chain, binding, resolution, plan, history, post_freeze, config
    )

    first = receipts[0]
    assert first.return_fraction == first.equity_after / first.equity_before - Decimal("1")
    observation = first.to_shadow_observation()
    assert observation.strategy_id == resolution.selected_strategy_id
    assert observation.return_fraction == first.return_fraction
    assert observation.source_fingerprint == first.measurement_hash
    assert measurement_receipts_hash(receipts) == measurement._hash(
        [value.measurement_hash for value in receipts]
    )


def test_w84_arbitrary_shadow_source_fingerprint_is_rejected(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )
    receipts = _receipts(
        chain, binding, resolution, plan, history, post_freeze, config
    )
    policy_hash = h("w84-policy")
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(
        FrozenShadowConfig(
            config_id="w84-shadow",
            activated_at=activation,
            initial_nav=Decimal("100000"),
            strategy_weights={resolution.selected_strategy_id: Decimal("1")},
            source_config_hash=policy_hash,
        )
    )
    first = receipts[0]
    shadow.append_period(
        (
            StrategyShadowObservation(
                strategy_id=resolution.selected_strategy_id,
                period_started_at=first.period_started_at,
                period_ended_at=first.period_ended_at,
                return_fraction=first.return_fraction,
                source_fingerprint=h("opaque-legacy-source"),
            ),
        )
    )

    with pytest.raises(
        ForwardShadowMeasurementIntegrityError,
        match="not the exact deterministic W84 measurement",
    ):
        verify_shadow_measurement_binding(
            plan=plan,
            policy_hash=policy_hash,
            selected_strategy_id=resolution.selected_strategy_id,
            shadow_records=shadow.list_records(),
            receipts=(first,),
            assessed_at=first.captured_at + timedelta(seconds=1),
        )


def test_w84_exact_measurement_fingerprint_binds_shadow_record(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )
    receipts = _receipts(
        chain, binding, resolution, plan, history, post_freeze, config
    )
    policy_hash = h("w84-policy")
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(
        FrozenShadowConfig(
            config_id="w84-shadow",
            activated_at=activation,
            initial_nav=Decimal("100000"),
            strategy_weights={resolution.selected_strategy_id: Decimal("1")},
            source_config_hash=policy_hash,
        )
    )
    for receipt in receipts:
        shadow.append_period((receipt.to_shadow_observation(),))

    head = verify_shadow_measurement_binding(
        plan=plan,
        policy_hash=policy_hash,
        selected_strategy_id=resolution.selected_strategy_id,
        shadow_records=shadow.list_records(),
        receipts=receipts,
        assessed_at=receipts[-1].captured_at + timedelta(seconds=1),
    )
    assert head == receipts[-1].measurement_hash


def test_w84_measurement_rejects_config_source_activation_and_capture_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )
    changed_config = replace(
        config,
        cost_model=ExecutionCostModel(
            fee_bps=Decimal("2"),
            half_spread_bps=Decimal("1"),
            slippage_bps=Decimal("1"),
        ),
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="frozen W84 measurement plan"):
        _receipts(
            chain,
            binding,
            resolution,
            plan,
            history,
            post_freeze,
            changed_config,
        )

    wrong_source = MarketDataset(
        instrument=post_freeze.instrument,
        bars=post_freeze.bars,
        source="changed-source",
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="source differs"):
        _receipts(
            chain, binding, resolution, plan, history, wrong_source, config
        )

    late_start = MarketDataset(
        instrument=post_freeze.instrument,
        bars=post_freeze.bars[1:],
        source=post_freeze.source,
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="start exactly"):
        _receipts(
            chain, binding, resolution, plan, history, late_start, config
        )

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="before dataset end"):
        _receipts(
            chain,
            binding,
            resolution,
            plan,
            history,
            post_freeze,
            config,
            captured_at=post_freeze.ended_at - timedelta(seconds=1),
        )


def test_w84_measurement_rejects_loaded_measurement_runtime_drift(
    monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )
    current = build_forward_measurement_runtime_identity()
    monkeypatch.setattr(
        measurement,
        "build_forward_measurement_runtime_identity",
        lambda: SimpleNamespace(
            identity_hash=h("different-measurement-runtime"),
            python_runtime=current.python_runtime,
            backtest_source_hash=current.backtest_source_hash,
            costs_source_hash=current.costs_source_hash,
            domain_source_hash=current.domain_source_hash,
        ),
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="frozen W84 measurement plan"):
        _receipts(
            chain, binding, resolution, plan, history, post_freeze, config
        )


def test_w84_measurement_receipt_tamper_and_authority_escalation_fail_closed(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )
    receipt = _receipts(
        chain, binding, resolution, plan, history, post_freeze, config
    )[0]

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="return is not reproducible"):
        replace(receipt, return_fraction=receipt.return_fraction + Decimal("0.01"))
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="may not grant"):
        replace(receipt, paper_candidate_authorized=True)
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="hash mismatch"):
        replace(receipt, measurement_hash="0" * 64)
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="hash mismatch"):
        replace(plan, plan_hash="0" * 64)
