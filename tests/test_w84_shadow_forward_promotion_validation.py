from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

import autotrade.promotion_shadow_forward_binding as w84
from autotrade.promotion_fee_accounting import SHADOW_FORWARD_BLOCKER
from test_w84_shadow_forward_promotion_binding import _assess, _base, _resolve


def _valid_chain(tmp_path, limits, market, empty_portfolio, market_buy_intent):
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
    resolved = _resolve(binding, resolution, plan, policy, evidence)
    return binding, resolution, plan, policy, evidence, resolved


def test_w84_policy_constructor_rejects_semantic_and_authority_drift(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, _, _, policy, _, _ = _valid_chain(
        tmp_path, limits, market, empty_portfolio, market_buy_intent
    )

    cases = (
        {"contract_version": "wrong"},
        {"dataset_source": ""},
        {
            "minimum_forward_duration_seconds":
                policy.required_forward_periods * policy.timeframe_seconds + 1
        },
        {"min_cumulative_return": Decimal("-1")},
        {"max_peak_to_trough_drawdown": Decimal("1.01")},
        {"max_capture_lag_seconds": -1},
        {"max_assessment_delay_seconds": -1},
        {"shadow_activated_at": policy.forward_activated_at},
        {"paper_candidate_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"policy_hash": "0" * 64},
    )
    for changes in cases:
        with pytest.raises(w84.ShadowForwardPromotionIntegrityError):
            replace(policy, **changes)


def test_w84_evidence_constructor_rejects_causality_window_and_status_drift(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, _, _, _, evidence, _ = _valid_chain(
        tmp_path, limits, market, empty_portfolio, market_buy_intent
    )

    cases = (
        {"contract_version": "wrong"},
        {"measurement_receipts_count": -1},
        {"capture_lag_seconds": -1},
        {"assessment_delay_seconds": -1},
        {"qualification_periods_used": evidence.required_forward_periods + 1},
        {
            "measurement_captured_at":
                evidence.measurement_data_cutoff_at - timedelta(seconds=1)
        },
        {"assessed_at": evidence.measurement_captured_at - timedelta(seconds=1)},
        {"qualification_started_at": None},
        {
            "qualification_started_at": None,
            "qualification_ended_at": None,
        },
        {"qualification_ended_at": evidence.qualification_started_at},
        {"cumulative_return": Decimal("-1")},
        {"peak_to_trough_drawdown": Decimal("1.01")},
        {"status": "PASS"},
        {"reason_codes": ("Z_REASON", "A_REASON")},
        {"reason_codes": ("UNEXPECTED_REASON",)},
        {"status": w84.ShadowForwardPromotionStatus.FAIL, "reason_codes": ()},
        {"exact_candidate_shadow_bound": False},
        {"measurement_plan_preregistered": False},
        {"per_observation_measurement_bound": False},
        {"prefix_only_measurement_bound": False},
        {"measurement_freshness_bound": False},
        {"full_observed_forward_tail_bound": False},
        {"fixed_forward_window_bound": False},
        {"paper_candidate_authorized": True},
        {"evidence_hash": "0" * 64},
    )
    for changes in cases:
        with pytest.raises(w84.ShadowForwardPromotionIntegrityError):
            replace(evidence, **changes)


def test_w84_resolution_constructor_rejects_blocker_binding_and_authority_drift(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, _, _, _, _, resolved = _valid_chain(
        tmp_path, limits, market, empty_portfolio, market_buy_intent
    )

    duplicated_remaining = resolved.remaining_promotion_blockers + (
        resolved.remaining_promotion_blockers[0],
    ) if resolved.remaining_promotion_blockers else ("DUP", "DUP")

    cases = (
        {"contract_version": "wrong"},
        {"resolved_promotion_blockers": ()},
        {"resolved_promotion_blockers": ("OTHER",)},
        {"remaining_promotion_blockers": duplicated_remaining},
        {
            "remaining_promotion_blockers": tuple(
                sorted(resolved.remaining_promotion_blockers + (SHADOW_FORWARD_BLOCKER,))
            )
        },
        {"strategy_version_execution_bound": False},
        {"shadow_forward_promotion_bound": False},
        {"paper_candidate_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"resolution_hash": "0" * 64},
    )
    for changes in cases:
        with pytest.raises(w84.ShadowForwardPromotionIntegrityError):
            replace(resolved, **changes)


def test_w84_valid_receipts_remain_no_authority_through_resolution(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    _, _, plan, policy, evidence, resolved = _valid_chain(
        tmp_path, limits, market, empty_portfolio, market_buy_intent
    )

    assert policy.measurement_plan_hash == plan.plan_hash
    assert evidence.measurement_plan_hash == plan.plan_hash
    assert resolved.measurement_plan_hash == plan.plan_hash
    assert evidence.status is w84.ShadowForwardPromotionStatus.PASS
    assert resolved.resolved_promotion_blockers == (SHADOW_FORWARD_BLOCKER,)
    assert resolved.paper_candidate_authorized is False
    assert resolved.external_execution_authorized is False
    assert resolved.runtime_execution_authorized is False
    assert resolved.capital_authority == "NONE"
    assert resolved.live_trading == "BLOCKED"
