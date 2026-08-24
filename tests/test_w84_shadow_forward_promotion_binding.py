from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import sqlite3

import pytest

import autotrade.forward_shadow_measurement as measurement
import autotrade.promotion_shadow_forward_binding as w84
from autotrade.forward_shadow_measurement import build_forward_shadow_measurements
from autotrade.promotion_fee_accounting import SHADOW_FORWARD_BLOCKER
from autotrade.research.forward import FrozenForwardPolicy, SQLiteForwardEvidenceRegistry
from autotrade.research.market import MarketDataset
from autotrade.research.shadow import (
    FrozenShadowConfig,
    SQLitePortfolioShadowRegistry,
    StrategyShadowObservation,
)
from test_w84_forward_shadow_measurement import (
    _bar,
    _measurement_inputs,
    _plan,
    _w83,
)


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _policy(binding, resolution, plan, **overrides):
    values = {
        "policy_id": "w84-policy",
        "w83_resolution": resolution,
        "binding_evidence": binding,
        "measurement_plan": plan,
        "shadow_config_id": "w84-shadow",
        "shadow_initial_nav": plan.initial_cash,
        "shadow_activated_at": plan.planned_at,
        "forward_campaign_id": "w84-forward-campaign",
        "required_forward_periods": 2,
        "minimum_forward_duration_seconds": 120,
        "min_cumulative_return": Decimal("0"),
        "max_peak_to_trough_drawdown": Decimal("0.10"),
        "max_capture_lag_seconds": 5,
        "max_assessment_delay_seconds": 5,
    }
    values.update(overrides)
    return w84.build_shadow_forward_promotion_policy(**values)


def _candidate_config(binding, resolution, plan, policy):
    return w84.build_candidate_shadow_config(
        policy=policy,
        measurement_plan=plan,
        w83_resolution=resolution,
        binding_evidence=binding,
    )


def _build_registries(
    tmp_path,
    *,
    chain,
    binding,
    resolution,
    plan,
    policy,
    history,
    post_freeze,
    config,
    append_shadow_count=None,
    append_forward_count=None,
    observation_factory=None,
    capture_offset_seconds=2,
):
    shadow_config = _candidate_config(binding, resolution, plan, policy)
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(shadow_config)
    forward = SQLiteForwardEvidenceRegistry(tmp_path / "forward.sqlite")
    forward.register_policy(
        w84.build_bound_forward_policy(
            policy=policy,
            measurement_plan=plan,
            shadow_config=shadow_config,
            w83_resolution=resolution,
            binding_evidence=binding,
        )
    )
    captured_at = post_freeze.ended_at + timedelta(seconds=capture_offset_seconds)
    receipts = build_forward_shadow_measurements(
        plan=plan,
        policy_hash=policy.policy_hash,
        w83_resolution=resolution,
        binding_evidence=binding,
        strategy_spec=chain["spec"],
        backtest_config=config,
        history_dataset=history,
        post_freeze_dataset=post_freeze,
        captured_at=captured_at,
    )
    shadow_count = len(receipts) if append_shadow_count is None else append_shadow_count
    forward_count = shadow_count if append_forward_count is None else append_forward_count
    shadow_records = []
    for receipt in receipts[:shadow_count]:
        observation = (
            observation_factory(receipt)
            if observation_factory is not None
            else receipt.to_shadow_observation()
        )
        shadow_records.append(shadow.append_period((observation,)))
    for record in shadow_records[:forward_count]:
        forward.append_shadow_record(
            shadow_registry=shadow,
            shadow_record_hash=record.record_hash,
        )
    return shadow_config, shadow, forward, receipts, captured_at


def _assess(
    *,
    chain,
    binding,
    resolution,
    plan,
    policy,
    history,
    post_freeze,
    config,
    shadow,
    forward,
    captured_at,
    assessment_offset_seconds=2,
):
    return w84.assess_shadow_forward_promotion(
        evidence_id="w84-evidence",
        policy=policy,
        measurement_plan=plan,
        w83_resolution=resolution,
        binding_evidence=binding,
        strategy_spec=chain["spec"],
        backtest_config=config,
        history_dataset=history,
        post_freeze_dataset=post_freeze,
        measurement_captured_at=captured_at,
        shadow_registry=shadow,
        forward_registry=forward,
        assessed_at=captured_at + timedelta(seconds=assessment_offset_seconds),
    )


def _resolve(binding, resolution, plan, policy, evidence, *, seconds=1):
    return w84.resolve_promotion_shadow_forward_binding(
        resolution_id="w84-resolution",
        evidence=evidence,
        policy=policy,
        measurement_plan=plan,
        w83_resolution=resolution,
        binding_evidence=binding,
        resolved_at=evidence.assessed_at + timedelta(seconds=seconds),
    )


def _rehash_policy(policy, **changes):
    values = {
        field.name: getattr(policy, field.name)
        for field in fields(policy)
        if field.name != "policy_hash"
    }
    values.update(changes)
    return w84.ShadowForwardPromotionPolicy(
        **values,
        policy_hash=w84._hash(w84._policy_payload_from_values(values)),
    )


def _rehash_evidence(evidence, **changes):
    values = {
        field.name: getattr(evidence, field.name)
        for field in fields(evidence)
        if field.name != "evidence_hash"
    }
    values.update(changes)
    return w84.ShadowForwardPromotionEvidence(
        **values,
        evidence_hash=w84._hash(w84._evidence_payload_from_values(values)),
    )


def _base(tmp_path, limits, market, empty_portfolio, market_buy_intent, **policy_overrides):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )
    policy = _policy(binding, resolution, plan, **policy_overrides)
    built = _build_registries(
        tmp_path,
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
    )
    return chain, binding, resolution, plan, policy, history, post_freeze, config, built


def test_w84_happy_path_measures_every_shadow_observation_and_only_removes_blocker(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    (
        chain,
        binding,
        resolution,
        plan,
        policy,
        history,
        post_freeze,
        config,
        (_, shadow, forward, receipts, captured_at),
    ) = _base(tmp_path, limits, market, empty_portfolio, market_buy_intent)

    evidence = _assess(
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        shadow=shadow,
        forward=forward,
        captured_at=captured_at,
    )
    assert evidence.status is w84.ShadowForwardPromotionStatus.PASS
    assert evidence.reason_codes == ()
    assert evidence.measurement_plan_hash == plan.plan_hash
    assert evidence.measurement_runtime_hash == plan.measurement_runtime_hash
    assert evidence.measurement_receipts_count == 2
    assert evidence.measurement_head_hash == receipts[-1].measurement_hash
    assert evidence.qualification_measurement_head_hash == receipts[-1].measurement_hash
    assert evidence.measurement_receipts_hash == measurement.measurement_receipts_hash(receipts)
    assert evidence.per_observation_measurement_bound is True
    assert evidence.prefix_only_measurement_bound is True
    assert evidence.measurement_freshness_bound is True
    assert evidence.qualification_periods_used == 2
    assert evidence.qualification_duration_seconds == 120
    assert evidence.cumulative_return > Decimal("0")
    assert evidence.paper_candidate_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"

    result = _resolve(binding, resolution, plan, policy, evidence)
    assert result.resolved_promotion_blockers == (SHADOW_FORWARD_BLOCKER,)
    assert SHADOW_FORWARD_BLOCKER not in result.remaining_promotion_blockers
    assert result.strategy_version_execution_bound is True
    assert result.shadow_forward_promotion_bound is True
    assert result.measurement_plan_hash == plan.plan_hash
    assert result.paper_candidate_authorized is False
    assert result.external_execution_authorized is False
    assert result.runtime_execution_authorized is False
    assert result.capital_authority == "NONE"
    assert result.live_trading == "BLOCKED"


def test_w84_policy_hash_commits_measurement_plan_before_outcomes(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, _, config = _measurement_inputs(chain, resolution)
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)
    shadow_config = _candidate_config(binding, resolution, plan, policy)

    assert policy.measurement_plan_hash == plan.plan_hash
    assert policy.measurement_runtime_hash == plan.measurement_runtime_hash
    assert policy.backtest_config_hash == plan.backtest_config_hash
    assert policy.history_dataset_hash == plan.history_dataset_hash
    assert policy.frozen_at == plan.planned_at
    assert shadow_config.source_config_hash == policy.policy_hash
    assert shadow_config.strategy_weights == {
        resolution.selected_strategy_id: Decimal("1")
    }


def test_w84_forward_policy_commits_full_measurement_runtime_not_only_strategy_runtime(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, _, config = _measurement_inputs(chain, resolution)
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)
    shadow_config = _candidate_config(binding, resolution, plan, policy)
    forward_policy = w84.build_bound_forward_policy(
        policy=policy,
        measurement_plan=plan,
        shadow_config=shadow_config,
        w83_resolution=resolution,
        binding_evidence=binding,
    )

    assert forward_policy.frozen_parameters_hash == policy.policy_hash
    assert forward_policy.source_code_hash == plan.measurement_runtime_hash
    assert forward_policy.source_code_hash != resolution.loaded_runtime_code_hash
    assert forward_policy.shadow_config_fingerprint == shadow_config.fingerprint


def test_w84_same_strategy_id_with_fabricated_source_fingerprint_cannot_pass(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)

    def forged(receipt):
        return StrategyShadowObservation(
            strategy_id=resolution.selected_strategy_id,
            period_started_at=receipt.period_started_at,
            period_ended_at=receipt.period_ended_at,
            return_fraction=receipt.return_fraction,
            source_fingerprint=h("same-id-different-runtime-or-opaque-source"),
        )

    _, shadow, forward, _, captured_at = _build_registries(
        tmp_path,
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        observation_factory=forged,
    )
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="not exact W84"):
        _assess(
            chain=chain,
            binding=binding,
            resolution=resolution,
            plan=plan,
            policy=policy,
            history=history,
            post_freeze=post_freeze,
            config=config,
            shadow=shadow,
            forward=forward,
            captured_at=captured_at,
        )


def test_w84_fabricated_return_fraction_cannot_be_hidden_inside_valid_r5_chain(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)

    def forged(receipt):
        return StrategyShadowObservation(
            strategy_id=resolution.selected_strategy_id,
            period_started_at=receipt.period_started_at,
            period_ended_at=receipt.period_ended_at,
            return_fraction=receipt.return_fraction + Decimal("0.10"),
            source_fingerprint=h("forged-return"),
        )

    _, shadow, forward, _, captured_at = _build_registries(
        tmp_path,
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        observation_factory=forged,
    )
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="not exact W84"):
        _assess(
            chain=chain,
            binding=binding,
            resolution=resolution,
            plan=plan,
            policy=policy,
            history=history,
            post_freeze=post_freeze,
            config=config,
            shadow=shadow,
            forward=forward,
            captured_at=captured_at,
        )


def test_w84_missing_shadow_period_is_detected_against_complete_market_measurement(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)
    _, shadow, forward, _, captured_at = _build_registries(
        tmp_path,
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        append_shadow_count=1,
    )

    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="not exact W84"):
        _assess(
            chain=chain,
            binding=binding,
            resolution=resolution,
            plan=plan,
            policy=policy,
            history=history,
            post_freeze=post_freeze,
            config=config,
            shadow=shadow,
            forward=forward,
            captured_at=captured_at,
        )


def test_w84_forward_registry_cannot_omit_bad_shadow_tail(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)
    _, shadow, forward, _, captured_at = _build_registries(
        tmp_path,
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        append_forward_count=1,
    )

    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="complete observed"):
        _assess(
            chain=chain,
            binding=binding,
            resolution=resolution,
            plan=plan,
            policy=policy,
            history=history,
            post_freeze=post_freeze,
            config=config,
            shadow=shadow,
            forward=forward,
            captured_at=captured_at,
        )


def test_w84_incomplete_fixed_window_is_pending_and_cannot_resolve(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    partial = MarketDataset(
        instrument=post_freeze.instrument,
        bars=post_freeze.bars[:2],
        source=post_freeze.source,
    )
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)
    _, shadow, forward, receipts, captured_at = _build_registries(
        tmp_path,
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=partial,
        config=config,
    )
    assert len(receipts) == 1
    evidence = _assess(
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=partial,
        config=config,
        shadow=shadow,
        forward=forward,
        captured_at=captured_at,
    )
    assert evidence.status is w84.ShadowForwardPromotionStatus.PENDING
    assert evidence.reason_codes == ("FORWARD_WINDOW_INCOMPLETE",)
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="without PASS"):
        _resolve(binding, resolution, plan, policy, evidence)


def test_w84_post_horizon_period_causes_overrun_fail_not_optional_stopping(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    extended = MarketDataset(
        instrument=post_freeze.instrument,
        bars=post_freeze.bars
        + (
            _bar(
                post_freeze.instrument.symbol,
                activation + timedelta(minutes=2),
                "20",
                open_value="12",
            ),
        ),
        source=post_freeze.source,
    )
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)
    _, shadow, forward, receipts, captured_at = _build_registries(
        tmp_path,
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=extended,
        config=config,
    )
    assert len(receipts) == 3
    evidence = _assess(
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=extended,
        config=config,
        shadow=shadow,
        forward=forward,
        captured_at=captured_at,
    )
    assert evidence.status is w84.ShadowForwardPromotionStatus.FAIL
    assert "FORWARD_WINDOW_OVERRUN" in evidence.reason_codes


def test_w84_capture_lag_and_assessment_delay_fail_closed_before_next_period_can_hide(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)

    _, shadow, forward, _, captured_at = _build_registries(
        tmp_path,
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        capture_offset_seconds=6,
    )
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="capture exceeds"):
        _assess(
            chain=chain,
            binding=binding,
            resolution=resolution,
            plan=plan,
            policy=policy,
            history=history,
            post_freeze=post_freeze,
            config=config,
            shadow=shadow,
            forward=forward,
            captured_at=captured_at,
        )

    # A fresh independent set proves the post-capture decision budget.
    other = tmp_path / "delay"
    other.mkdir()
    _, shadow2, forward2, _, captured2 = _build_registries(
        other,
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
    )
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="decision budget"):
        _assess(
            chain=chain,
            binding=binding,
            resolution=resolution,
            plan=plan,
            policy=policy,
            history=history,
            post_freeze=post_freeze,
            config=config,
            shadow=shadow2,
            forward=forward2,
            captured_at=captured2,
            assessment_offset_seconds=6,
        )


def test_w84_policy_rejects_lag_budget_long_enough_to_cross_next_market_period(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, _, config = _measurement_inputs(chain, resolution)
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="shorter than one market period"):
        _policy(
            binding,
            resolution,
            plan,
            max_capture_lag_seconds=30,
            max_assessment_delay_seconds=30,
        )


def test_w84_threshold_fail_cannot_resolve_blocker(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    (
        chain,
        binding,
        resolution,
        plan,
        policy,
        history,
        post_freeze,
        config,
        (_, shadow, forward, _, captured_at),
    ) = _base(
        tmp_path,
        limits,
        market,
        empty_portfolio,
        market_buy_intent,
        min_cumulative_return=Decimal("0.01"),
    )
    evidence = _assess(
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        shadow=shadow,
        forward=forward,
        captured_at=captured_at,
    )
    assert evidence.status is w84.ShadowForwardPromotionStatus.FAIL
    assert "FORWARD_CUMULATIVE_RETURN_BELOW_MINIMUM" in evidence.reason_codes
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="without PASS"):
        _resolve(binding, resolution, plan, policy, evidence)


def test_w84_drawdown_threshold_is_evaluated_on_measured_candidate_returns(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    stressed = MarketDataset(
        instrument=post_freeze.instrument,
        bars=(
            post_freeze.bars[0],
            post_freeze.bars[1],
            _bar(
                post_freeze.instrument.symbol,
                activation + timedelta(minutes=1),
                "2",
                open_value="12",
            ),
        ),
        source=post_freeze.source,
    )
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(
        binding,
        resolution,
        plan,
        min_cumulative_return=Decimal("-0.50"),
        max_peak_to_trough_drawdown=Decimal("0.0001"),
    )
    _, shadow, forward, _, captured_at = _build_registries(
        tmp_path,
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=stressed,
        config=config,
    )
    evidence = _assess(
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=stressed,
        config=config,
        shadow=shadow,
        forward=forward,
        captured_at=captured_at,
    )
    assert evidence.peak_to_trough_drawdown > policy.max_peak_to_trough_drawdown
    assert evidence.status is w84.ShadowForwardPromotionStatus.FAIL
    assert "FORWARD_DRAWDOWN_ABOVE_MAXIMUM" in evidence.reason_codes


def test_w84_nonexclusive_shadow_config_is_rejected(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(
        FrozenShadowConfig(
            config_id=policy.shadow_config_id,
            activated_at=policy.shadow_activated_at,
            initial_nav=policy.shadow_initial_nav,
            strategy_weights={
                resolution.selected_strategy_id: Decimal("0.5"),
                "other": Decimal("0.5"),
            },
            source_config_hash=policy.policy_hash,
        )
    )
    forward = SQLiteForwardEvidenceRegistry(tmp_path / "forward.sqlite")
    forward.register_policy(
        FrozenForwardPolicy(
            campaign_id=policy.forward_campaign_id,
            activated_at=policy.forward_activated_at,
            shadow_config_fingerprint=shadow.get_config().fingerprint,
            frozen_parameters_hash=policy.policy_hash,
            source_code_hash=policy.measurement_runtime_hash,
        )
    )
    captured_at = post_freeze.ended_at + timedelta(seconds=2)
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="100% selected-candidate"):
        _assess(
            chain=chain,
            binding=binding,
            resolution=resolution,
            plan=plan,
            policy=policy,
            history=history,
            post_freeze=post_freeze,
            config=config,
            shadow=shadow,
            forward=forward,
            captured_at=captured_at,
        )


def test_w84_forward_policy_wrong_measurement_runtime_is_rejected(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    policy = _policy(binding, resolution, plan)
    shadow_config = _candidate_config(binding, resolution, plan, policy)
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(shadow_config)
    forward = SQLiteForwardEvidenceRegistry(tmp_path / "forward.sqlite")
    forward.register_policy(
        FrozenForwardPolicy(
            campaign_id=policy.forward_campaign_id,
            activated_at=policy.forward_activated_at,
            shadow_config_fingerprint=shadow_config.fingerprint,
            frozen_parameters_hash=policy.policy_hash,
            source_code_hash=h("wrong-runtime"),
        )
    )
    captured_at = post_freeze.ended_at + timedelta(seconds=2)
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="measurement runtime"):
        _assess(
            chain=chain,
            binding=binding,
            resolution=resolution,
            plan=plan,
            policy=policy,
            history=history,
            post_freeze=post_freeze,
            config=config,
            shadow=shadow,
            forward=forward,
            captured_at=captured_at,
        )


def test_w84_policy_rejects_initial_nav_or_campaign_semantic_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, _, config = _measurement_inputs(chain, resolution)
    plan = _plan(chain, binding, resolution, history, config, planned_at, activation)
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="initial NAV"):
        _policy(
            binding,
            resolution,
            plan,
            shadow_initial_nav=plan.initial_cash + Decimal("1"),
        )
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="distinct"):
        _policy(
            binding,
            resolution,
            plan,
            forward_campaign_id=binding.development_campaign_id,
        )


def test_w84_policy_and_evidence_hash_tamper_fail_closed(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    (
        chain,
        binding,
        resolution,
        plan,
        policy,
        history,
        post_freeze,
        config,
        (_, shadow, forward, _, captured_at),
    ) = _base(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    evidence = _assess(
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        shadow=shadow,
        forward=forward,
        captured_at=captured_at,
    )

    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="policy hash mismatch"):
        replace(policy, policy_hash="0" * 64)
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="evidence hash mismatch"):
        replace(evidence, evidence_hash="0" * 64)

    forged = _rehash_evidence(evidence, paper_candidate_authorized=True)
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="may not grant"):
        _resolve(binding, resolution, plan, policy, forged)


def test_w84_resolution_rejects_measurement_plan_reuse_for_another_plan(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    (
        chain,
        binding,
        resolution,
        plan,
        policy,
        history,
        post_freeze,
        config,
        (_, shadow, forward, _, captured_at),
    ) = _base(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    evidence = _assess(
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        shadow=shadow,
        forward=forward,
        captured_at=captured_at,
    )
    values = {
        field.name: getattr(plan, field.name)
        for field in fields(plan)
        if field.name != "plan_hash"
    }
    values["plan_id"] = "w84-other-plan"
    other_plan = measurement.ForwardMeasurementPlan(
        **values,
        plan_hash=measurement._hash(measurement._plan_payload_from_values(values)),
    )
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match="policy does not match"):
        _resolve(binding, resolution, other_plan, policy, evidence)


def test_w84_underlying_r5_tamper_still_propagates_fail_closed(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    (
        chain,
        binding,
        resolution,
        plan,
        policy,
        history,
        post_freeze,
        config,
        (_, shadow, forward, _, captured_at),
    ) = _base(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    with sqlite3.connect(shadow.path) as conn:
        conn.execute("UPDATE shadow_records SET record_json = '{}' WHERE sequence = 1")
        conn.commit()
    with pytest.raises(Exception):
        _assess(
            chain=chain,
            binding=binding,
            resolution=resolution,
            plan=plan,
            policy=policy,
            history=history,
            post_freeze=post_freeze,
            config=config,
            shadow=shadow,
            forward=forward,
            captured_at=captured_at,
        )


def test_w84_resolution_hash_is_reproducible_and_never_mints_execution_authority(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    (
        chain,
        binding,
        resolution,
        plan,
        policy,
        history,
        post_freeze,
        config,
        (_, shadow, forward, _, captured_at),
    ) = _base(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    evidence = _assess(
        chain=chain,
        binding=binding,
        resolution=resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        shadow=shadow,
        forward=forward,
        captured_at=captured_at,
    )
    first = _resolve(binding, resolution, plan, policy, evidence)
    second = _resolve(binding, resolution, plan, policy, evidence)
    assert first == second
    assert first.resolution_hash == second.resolution_hash
    assert first.paper_candidate_authorized is False
    assert first.runtime_execution_authorized is False
    assert first.external_execution_authorized is False
    assert first.capital_authority == "NONE"
    assert first.live_trading == "BLOCKED"
