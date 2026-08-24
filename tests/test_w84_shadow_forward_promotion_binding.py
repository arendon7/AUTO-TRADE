from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import sqlite3

import pytest

import autotrade.promotion_shadow_forward_binding as w84
from autotrade.promotion_fee_accounting import SHADOW_FORWARD_BLOCKER
from autotrade.research.forward import FrozenForwardPolicy, SQLiteForwardEvidenceRegistry
from autotrade.research.shadow import (
    FrozenShadowConfig,
    ShadowIntegrityError,
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


def _policy(binding, resolution, **overrides):
    frozen_at = resolution.resolved_at + timedelta(seconds=1)
    values = {
        "policy_id": "w84-policy",
        "w83_resolution": resolution,
        "binding_evidence": binding,
        "shadow_config_id": "w84-shadow",
        "shadow_initial_nav": Decimal("100000"),
        "shadow_activated_at": frozen_at + timedelta(seconds=1),
        "forward_campaign_id": "w84-forward-campaign",
        "forward_activated_at": frozen_at + timedelta(seconds=2),
        "required_forward_periods": 2,
        "minimum_forward_duration_seconds": 120,
        "min_cumulative_return": Decimal("0"),
        "max_peak_to_trough_drawdown": Decimal("0.10"),
        "frozen_at": frozen_at,
    }
    values.update(overrides)
    return w84.build_shadow_forward_promotion_policy(**values)


def _candidate_config(binding, resolution, policy):
    return w84.build_candidate_shadow_config(
        policy=policy,
        w83_resolution=resolution,
        binding_evidence=binding,
    )


def _observation(strategy_id, start, value):
    return StrategyShadowObservation(
        strategy_id=strategy_id,
        period_started_at=start,
        period_ended_at=start + timedelta(minutes=1),
        return_fraction=Decimal(value),
        source_fingerprint=h(f"{strategy_id}:{start.isoformat()}:{value}"),
    )


def _registries(
    tmp_path,
    binding,
    resolution,
    *,
    returns=("0.01", "-0.002"),
    policy_overrides=None,
    start_offset_minutes=0,
):
    policy = _policy(binding, resolution, **(policy_overrides or {}))
    config = _candidate_config(binding, resolution, policy)
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(config)
    forward = SQLiteForwardEvidenceRegistry(tmp_path / "forward.sqlite")
    forward.register_policy(
        w84.build_bound_forward_policy(
            policy=policy,
            shadow_config=config,
            w83_resolution=resolution,
            binding_evidence=binding,
        )
    )
    for index, value in enumerate(returns):
        start = policy.forward_activated_at + timedelta(
            minutes=start_offset_minutes + index
        )
        record = shadow.append_period(
            (_observation(resolution.selected_strategy_id, start, value),)
        )
        forward.append_shadow_record(
            shadow_registry=shadow,
            shadow_record_hash=record.record_hash,
        )
    return config, policy, shadow, forward


def _assess(binding, resolution, policy, shadow, forward, *, seconds=1):
    records = forward.list_records()
    end = records[-1].period_ended_at if records else policy.forward_activated_at
    return w84.assess_shadow_forward_promotion(
        evidence_id="w84-evidence",
        policy=policy,
        w83_resolution=resolution,
        binding_evidence=binding,
        shadow_registry=shadow,
        forward_registry=forward,
        assessed_at=end + timedelta(seconds=seconds),
    )


def _resolve_w84(binding, resolution, policy, evidence, *, seconds=1):
    return w84.resolve_promotion_shadow_forward_binding(
        resolution_id="w84-resolution",
        evidence=evidence,
        policy=policy,
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


def test_w84_happy_path_fixed_window_only_removes_shadow_forward_blocker(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    config, policy, shadow, forward = _registries(
        tmp_path,
        binding,
        resolution,
        returns=("0.01", "-0.002"),
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)

    assert evidence.status is w84.ShadowForwardPromotionStatus.PASS
    assert evidence.reason_codes == ()
    assert evidence.forward_sequence == 2
    assert evidence.required_forward_periods == 2
    assert evidence.qualification_periods_used == 2
    assert evidence.qualification_duration_seconds == 120
    assert evidence.cumulative_return == Decimal("0.00798")
    assert evidence.peak_to_trough_drawdown == Decimal("0.002")
    assert evidence.qualification_head_hash == forward.list_records()[1].evidence_hash
    assert evidence.shadow_config_fingerprint == config.fingerprint
    assert evidence.shadow_policy_commitment_hash == policy.policy_hash
    assert evidence.exact_candidate_shadow_bound is True
    assert evidence.policy_preregistered_in_shadow_config is True
    assert evidence.forward_policy_committed is True
    assert evidence.full_observed_forward_tail_bound is True
    assert evidence.fixed_forward_window_bound is True
    assert evidence.paper_candidate_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"

    result = _resolve_w84(binding, resolution, policy, evidence)
    assert result.resolved_promotion_blockers == (SHADOW_FORWARD_BLOCKER,)
    assert SHADOW_FORWARD_BLOCKER not in result.remaining_promotion_blockers
    assert result.strategy_version_execution_bound is True
    assert result.shadow_forward_promotion_bound is True
    assert result.paper_candidate_authorized is False
    assert result.external_execution_authorized is False
    assert result.runtime_execution_authorized is False
    assert result.capital_authority == "NONE"
    assert result.live_trading == "BLOCKED"
    assert result.to_dict()["resolution_hash"] == result.resolution_hash


def test_w84_known_post_horizon_outcome_blocks_late_promotion_decision(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path,
        binding,
        resolution,
        returns=("0.01", "-0.002", "0.50"),
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)

    assert evidence.status is w84.ShadowForwardPromotionStatus.FAIL
    assert "FORWARD_WINDOW_OVERRUN" in evidence.reason_codes
    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="without PASS",
    ):
        _resolve_w84(binding, resolution, policy, evidence)


def test_w84_policy_is_cryptographically_committed_before_any_shadow_outcome(
    limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    policy = _policy(binding, resolution)
    config = _candidate_config(binding, resolution, policy)

    assert config.source_config_hash == policy.policy_hash
    assert config.config_id == policy.shadow_config_id
    assert config.initial_nav == policy.shadow_initial_nav
    assert config.activated_at == policy.shadow_activated_at
    assert config.strategy_weights == {
        resolution.selected_strategy_id: Decimal("1")
    }

    stricter = _policy(
        binding,
        resolution,
        max_peak_to_trough_drawdown=Decimal("0.01"),
    )
    assert stricter.policy_hash != policy.policy_hash
    assert config.source_config_hash != stricter.policy_hash


def test_bound_forward_policy_commits_policy_runtime_and_exact_candidate_config(
    limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    policy = _policy(binding, resolution)
    config = _candidate_config(binding, resolution, policy)
    forward_policy = w84.build_bound_forward_policy(
        policy=policy,
        shadow_config=config,
        w83_resolution=resolution,
        binding_evidence=binding,
    )

    assert forward_policy.campaign_id == policy.forward_campaign_id
    assert forward_policy.activated_at == policy.forward_activated_at
    assert forward_policy.shadow_config_fingerprint == config.fingerprint
    assert forward_policy.frozen_parameters_hash == policy.policy_hash
    assert forward_policy.source_code_hash == resolution.loaded_runtime_code_hash


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        (
            {"frozen_at_offset": -1},
            "freeze cannot predate exact W83 proof",
        ),
        (
            {"campaign_same": True},
            "distinct from DEVELOPMENT",
        ),
        (
            {"shadow_before_freeze": True},
            "chronology",
        ),
        (
            {"forward_before_shadow": True},
            "chronology",
        ),
    ),
)
def test_w84_policy_rejects_invalid_freeze_and_campaign_chronology(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    overrides,
    message,
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    frozen = resolution.resolved_at + timedelta(seconds=1)
    kwargs = {}
    if overrides.get("frozen_at_offset") == -1:
        kwargs["frozen_at"] = resolution.resolved_at - timedelta(microseconds=1)
    if overrides.get("campaign_same"):
        kwargs["forward_campaign_id"] = binding.development_campaign_id
    if overrides.get("shadow_before_freeze"):
        kwargs["shadow_activated_at"] = frozen - timedelta(microseconds=1)
    if overrides.get("forward_before_shadow"):
        kwargs["forward_activated_at"] = frozen + timedelta(microseconds=1)
        kwargs["shadow_activated_at"] = frozen + timedelta(seconds=1)

    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match=message):
        _policy(binding, resolution, **kwargs)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"shadow_initial_nav": Decimal("0")}, "shadow_initial_nav"),
        ({"required_forward_periods": 0}, "required_forward_periods"),
        ({"minimum_forward_duration_seconds": 0}, "minimum_forward_duration_seconds"),
        ({"min_cumulative_return": Decimal("-1")}, "greater than -1"),
        ({"max_peak_to_trough_drawdown": Decimal("1.01")}, "within \\[0,1\\]"),
        ({"min_cumulative_return": Decimal("NaN")}, "finite Decimal"),
    ),
)
def test_w84_policy_rejects_invalid_economic_and_horizon_parameters(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    overrides,
    message,
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match=message):
        _policy(binding, resolution, **overrides)


def test_w84_rejects_shadow_config_not_committing_exact_policy_or_candidate(
    limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    policy = _policy(binding, resolution)
    config = _candidate_config(binding, resolution, policy)

    unbound = FrozenShadowConfig(
        config_id=config.config_id,
        activated_at=config.activated_at,
        initial_nav=config.initial_nav,
        strategy_weights=config.strategy_weights,
        source_config_hash=h("not-policy"),
    )
    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="does not exactly commit",
    ):
        w84.build_bound_forward_policy(
            policy=policy,
            shadow_config=unbound,
            w83_resolution=resolution,
            binding_evidence=binding,
        )

    mixed = FrozenShadowConfig(
        config_id=config.config_id,
        activated_at=config.activated_at,
        initial_nav=config.initial_nav,
        strategy_weights={
            resolution.selected_strategy_id: Decimal("0.5"),
            "other": Decimal("0.5"),
        },
        source_config_hash=policy.policy_hash,
    )
    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="exclusive 100%",
    ):
        w84.build_bound_forward_policy(
            policy=policy,
            shadow_config=mixed,
            w83_resolution=resolution,
            binding_evidence=binding,
        )


@pytest.mark.parametrize(
    ("field_name", "new_value"),
    (
        ("source_code_hash", "a" * 64),
        ("frozen_parameters_hash", "b" * 64),
        ("campaign_id", "wrong-forward-campaign"),
        ("shadow_config_fingerprint", "c" * 64),
    ),
)
def test_w84_assessment_rejects_forward_policy_identity_drift(
    tmp_path,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    field_name,
    new_value,
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    policy = _policy(binding, resolution)
    config = _candidate_config(binding, resolution, policy)
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(config)
    valid = w84.build_bound_forward_policy(
        policy=policy,
        shadow_config=config,
        w83_resolution=resolution,
        binding_evidence=binding,
    )
    values = {
        "campaign_id": valid.campaign_id,
        "activated_at": valid.activated_at,
        "shadow_config_fingerprint": valid.shadow_config_fingerprint,
        "frozen_parameters_hash": valid.frozen_parameters_hash,
        "source_code_hash": valid.source_code_hash,
    }
    values[field_name] = new_value
    forward = SQLiteForwardEvidenceRegistry(tmp_path / "forward.sqlite")
    forward.register_policy(FrozenForwardPolicy(**values))

    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="does not exactly commit",
    ):
        _assess(binding, resolution, policy, shadow, forward)


def test_w84_fails_closed_on_omitted_observed_shadow_tail(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    policy = _policy(binding, resolution)
    config = _candidate_config(binding, resolution, policy)
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(config)
    forward = SQLiteForwardEvidenceRegistry(tmp_path / "forward.sqlite")
    forward.register_policy(
        w84.build_bound_forward_policy(
            policy=policy,
            shadow_config=config,
            w83_resolution=resolution,
            binding_evidence=binding,
        )
    )
    first = shadow.append_period(
        (_observation(resolution.selected_strategy_id, policy.forward_activated_at, "0.01"),)
    )
    shadow.append_period(
        (
            _observation(
                resolution.selected_strategy_id,
                policy.forward_activated_at + timedelta(minutes=1),
                "-0.50",
            ),
        )
    )
    forward.append_shadow_record(
        shadow_registry=shadow,
        shadow_record_hash=first.record_hash,
    )

    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="complete observed eligible shadow tail",
    ):
        _assess(binding, resolution, policy, shadow, forward)


def test_w84_incomplete_fixed_window_is_pending_not_failed_performance(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path,
        binding,
        resolution,
        returns=("-0.90",),
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)

    assert evidence.status is w84.ShadowForwardPromotionStatus.PENDING
    assert evidence.reason_codes == ("FORWARD_WINDOW_INCOMPLETE",)
    assert evidence.qualification_periods_used == 1
    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="without PASS",
    ):
        _resolve_w84(binding, resolution, policy, evidence)


@pytest.mark.parametrize(
    ("policy_overrides", "returns", "reason"),
    (
        (
            {"minimum_forward_duration_seconds": 180},
            ("0.01", "-0.002"),
            "FORWARD_DURATION_BELOW_MINIMUM",
        ),
        (
            {"min_cumulative_return": Decimal("0.02")},
            ("0.01", "-0.002"),
            "FORWARD_CUMULATIVE_RETURN_BELOW_MINIMUM",
        ),
        (
            {"max_peak_to_trough_drawdown": Decimal("0.001")},
            ("0.01", "-0.002"),
            "FORWARD_DRAWDOWN_ABOVE_MAXIMUM",
        ),
    ),
)
def test_w84_completed_window_threshold_failures_are_fail_not_resolution(
    tmp_path,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    policy_overrides,
    returns,
    reason,
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path,
        binding,
        resolution,
        returns=returns,
        policy_overrides=policy_overrides,
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)

    assert evidence.status is w84.ShadowForwardPromotionStatus.FAIL
    assert reason in evidence.reason_codes
    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="without PASS",
    ):
        _resolve_w84(binding, resolution, policy, evidence)


def test_w84_fixed_window_rejects_late_first_forward_start(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path,
        binding,
        resolution,
        start_offset_minutes=1,
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)

    assert evidence.status is w84.ShadowForwardPromotionStatus.FAIL
    assert "FORWARD_START_MISMATCH" in evidence.reason_codes


def test_empty_forward_registry_is_pending_with_genesis_qualification_head(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    policy = _policy(binding, resolution)
    config = _candidate_config(binding, resolution, policy)
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(config)
    forward = SQLiteForwardEvidenceRegistry(tmp_path / "forward.sqlite")
    forward.register_policy(
        w84.build_bound_forward_policy(
            policy=policy,
            shadow_config=config,
            w83_resolution=resolution,
            binding_evidence=binding,
        )
    )

    evidence = _assess(binding, resolution, policy, shadow, forward)
    assert evidence.status is w84.ShadowForwardPromotionStatus.PENDING
    assert evidence.qualification_periods_used == 0
    assert evidence.qualification_head_hash == w84.GENESIS_HASH
    assert evidence.qualification_started_at is None
    assert evidence.qualification_ended_at is None


def test_w84_resolution_rejects_temporal_regression(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path, binding, resolution
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)

    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="temporal causality",
    ):
        w84.resolve_promotion_shadow_forward_binding(
            resolution_id="w84-time",
            evidence=evidence,
            policy=policy,
            w83_resolution=resolution,
            binding_evidence=binding,
            resolved_at=evidence.assessed_at - timedelta(microseconds=1),
        )


def test_w84_policy_evidence_and_resolution_cannot_mint_authority(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path, binding, resolution
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)
    result = _resolve_w84(binding, resolution, policy, evidence)

    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="may not grant PAPER",
    ):
        _rehash_policy(policy, paper_candidate_authorized=True)
    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="may not grant PAPER",
    ):
        _rehash_evidence(evidence, runtime_execution_authorized=True)
    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="may not grant PAPER",
    ):
        replace(result, external_execution_authorized=True)


def test_w84_resolution_cannot_remove_other_or_retain_shadow_forward_blocker(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path, binding, resolution
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)
    result = _resolve_w84(binding, resolution, policy, evidence)

    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="may resolve only",
    ):
        replace(result, resolved_promotion_blockers=("SOMETHING_ELSE",))
    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="may not remain",
    ):
        replace(
            result,
            remaining_promotion_blockers=(SHADOW_FORWARD_BLOCKER,),
        )


def test_w84_detects_rehashed_policy_candidate_mismatch(
    limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    policy = _policy(binding, resolution)
    drifted = _rehash_policy(policy, selected_strategy_version="v999")

    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="does not match exact W83",
    ):
        w84.build_candidate_shadow_config(
            policy=drifted,
            w83_resolution=resolution,
            binding_evidence=binding,
        )


def test_w84_detects_evidence_hash_tamper_and_rehashed_identity_drift(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path, binding, resolution
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)

    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="evidence hash mismatch",
    ):
        replace(evidence, evidence_hash="0" * 64)

    drifted = _rehash_evidence(
        evidence,
        selected_strategy_version="v999",
    )
    with pytest.raises(
        w84.ShadowForwardPromotionIntegrityError,
        match="does not match exact frozen candidate",
    ):
        _resolve_w84(binding, resolution, policy, drifted)


def test_w84_assessment_propagates_r5_shadow_tamper_detection(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path, binding, resolution
    )
    with sqlite3.connect(shadow.path) as conn:
        conn.execute("UPDATE shadow_records SET record_json = '{}' WHERE sequence = 1")
        conn.commit()

    with pytest.raises(ShadowIntegrityError):
        _assess(binding, resolution, policy, shadow, forward)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    (
        ("w83_resolution", object(), "w83_resolution"),
        ("binding_evidence", object(), "binding_evidence"),
    ),
)
def test_w84_rejects_wrong_w83_input_types(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    field_name,
    value,
    message,
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    kwargs = {
        "policy_id": "w84-type-policy",
        "w83_resolution": resolution,
        "binding_evidence": binding,
        "shadow_config_id": "w84-shadow",
        "shadow_initial_nav": Decimal("1"),
        "shadow_activated_at": resolution.resolved_at + timedelta(seconds=2),
        "forward_campaign_id": "w84-forward",
        "forward_activated_at": resolution.resolved_at + timedelta(seconds=3),
        "required_forward_periods": 1,
        "minimum_forward_duration_seconds": 1,
        "min_cumulative_return": Decimal("0"),
        "max_peak_to_trough_drawdown": Decimal("1"),
        "frozen_at": resolution.resolved_at + timedelta(seconds=1),
    }
    kwargs[field_name] = value
    with pytest.raises(TypeError, match=message):
        w84.build_shadow_forward_promotion_policy(**kwargs)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    (
        ("shadow_registry", object(), "shadow_registry"),
        ("forward_registry", object(), "forward_registry"),
    ),
)
def test_w84_assessment_rejects_wrong_registry_types(
    tmp_path,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    field_name,
    value,
    message,
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path, binding, resolution
    )
    kwargs = {
        "evidence_id": "w84-types",
        "policy": policy,
        "w83_resolution": resolution,
        "binding_evidence": binding,
        "shadow_registry": shadow,
        "forward_registry": forward,
        "assessed_at": forward.list_records()[-1].period_ended_at
        + timedelta(seconds=1),
    }
    kwargs[field_name] = value
    with pytest.raises(TypeError, match=message):
        w84.assess_shadow_forward_promotion(**kwargs)


def test_w84_policy_evidence_and_resolution_hashes_are_reproducible(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    policy = _policy(binding, resolution)
    policy2 = _policy(binding, resolution)
    assert policy.policy_hash == policy2.policy_hash

    config = _candidate_config(binding, resolution, policy)
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(config)
    forward = SQLiteForwardEvidenceRegistry(tmp_path / "forward.sqlite")
    forward.register_policy(
        w84.build_bound_forward_policy(
            policy=policy,
            shadow_config=config,
            w83_resolution=resolution,
            binding_evidence=binding,
        )
    )
    for index, value in enumerate(("0.01", "-0.002")):
        record = shadow.append_period(
            (
                _observation(
                    resolution.selected_strategy_id,
                    policy.forward_activated_at + timedelta(minutes=index),
                    value,
                ),
            )
        )
        forward.append_shadow_record(
            shadow_registry=shadow,
            shadow_record_hash=record.record_hash,
        )

    first = _assess(binding, resolution, policy, shadow, forward)
    second = _assess(binding, resolution, policy, shadow, forward)
    assert first == second
    assert first.evidence_hash == second.evidence_hash

    r1 = _resolve_w84(binding, resolution, policy, first)
    r2 = _resolve_w84(binding, resolution, policy, second)
    assert r1 == r2
    assert r1.resolution_hash == r2.resolution_hash


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    (
        ("contract_version", "wrong", "version"),
        ("policy_hash", "x" * 64, "lowercase sha256"),
        ("required_forward_periods", True, "integer >=1"),
        ("shadow_initial_nav", Decimal("Infinity"), "finite Decimal"),
        ("live_trading", "ENABLED", "may not grant PAPER"),
    ),
)
def test_w84_policy_dataclass_fail_closed_validation(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    field_name,
    value,
    message,
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    policy = _policy(binding, resolution)
    values = {
        field.name: getattr(policy, field.name)
        for field in fields(policy)
    }
    values[field_name] = value
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match=message):
        w84.ShadowForwardPromotionPolicy(**values)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"qualification_periods_used": 3}, "may not exceed"),
        (
            {
                "qualification_started_at": None,
                "qualification_ended_at": None,
                "qualification_periods_used": 1,
            },
            "empty qualification",
        ),
        ({"status": w84.ShadowForwardPromotionStatus.FAIL, "reason_codes": ()}, "requires reason"),
        ({"status": w84.ShadowForwardPromotionStatus.PASS, "reason_codes": ("X",)}, "may not carry"),
        ({"fixed_forward_window_bound": False}, "requires fixed_forward_window_bound"),
        ({"peak_to_trough_drawdown": Decimal("1.1")}, "within \\[0,1\\]"),
    ),
)
def test_w84_evidence_dataclass_fail_closed_validation(
    tmp_path,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    changes,
    message,
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path, binding, resolution
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)
    values = {
        field.name: getattr(evidence, field.name)
        for field in fields(evidence)
    }
    values.update(changes)
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match=message):
        w84.ShadowForwardPromotionEvidence(**values)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"contract_version": "wrong"}, "version"),
        ({"strategy_version_execution_bound": False}, "flags"),
        ({"shadow_forward_promotion_bound": False}, "flags"),
        ({"remaining_promotion_blockers": ("z", "z")}, "unique sorted"),
        ({"resolution_hash": "0" * 64}, "resolution hash mismatch"),
    ),
)
def test_w84_resolution_dataclass_fail_closed_validation(
    tmp_path,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    changes,
    message,
):
    _, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    _, policy, shadow, forward = _registries(
        tmp_path, binding, resolution
    )
    evidence = _assess(binding, resolution, policy, shadow, forward)
    result = _resolve_w84(binding, resolution, policy, evidence)
    values = {
        field.name: getattr(result, field.name)
        for field in fields(result)
    }
    values.update(changes)
    with pytest.raises(w84.ShadowForwardPromotionIntegrityError, match=message):
        w84.PromotionShadowForwardResolution(**values)


def test_w84_forward_metrics_compound_and_drawdown_exactly():
    class Record:
        def __init__(self, value):
            self.portfolio_return = Decimal(value)

    cumulative, drawdown = w84._forward_metrics(
        (Record("0.10"), Record("-0.10"), Record("0.05"))
    )
    assert cumulative == Decimal("0.0395")
    assert drawdown == Decimal("0.10")
    assert w84._forward_metrics(()) == (Decimal("0"), Decimal("0"))
