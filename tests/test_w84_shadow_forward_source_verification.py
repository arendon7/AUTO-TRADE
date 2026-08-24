from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal

import pytest

import autotrade.forward_shadow_measurement as measurement
import autotrade.promotion_shadow_forward_binding as w84
import autotrade.promotion_shadow_forward_source_verification as source_verification
from autotrade.forward_shadow_measurement import ForwardShadowMeasurementReceipt
from autotrade.promotion_shadow_forward_source_verification import (
    PromotionShadowForwardSourceVerification,
    ShadowForwardSourceVerificationIntegrityError,
    verify_promotion_shadow_forward_resolution_sources,
)
from test_w84_shadow_forward_promotion_binding import (
    _assess,
    _base,
    _rehash_evidence,
    _resolve,
)


def _context(tmp_path, limits, market, empty_portfolio, market_buy_intent):
    (
        chain,
        binding,
        w83_resolution,
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
        resolution=w83_resolution,
        plan=plan,
        policy=policy,
        history=history,
        post_freeze=post_freeze,
        config=config,
        shadow=shadow,
        forward=forward,
        captured_at=captured_at,
    )
    base_resolution = _resolve(binding, w83_resolution, plan, policy, evidence)
    return {
        "chain": chain,
        "binding": binding,
        "w83": w83_resolution,
        "plan": plan,
        "policy": policy,
        "history": history,
        "post_freeze": post_freeze,
        "config": config,
        "shadow": shadow,
        "forward": forward,
        "receipts": receipts,
        "captured_at": captured_at,
        "evidence": evidence,
        "base": base_resolution,
    }


def _verify(ctx, *, evidence=None, base=None, receipts=None, seconds=1):
    evidence = evidence or ctx["evidence"]
    base = base or ctx["base"]
    receipts = ctx["receipts"] if receipts is None else receipts
    return verify_promotion_shadow_forward_resolution_sources(
        verification_id="w84-source-verification",
        base_resolution=base,
        evidence=evidence,
        policy=ctx["policy"],
        measurement_plan=ctx["plan"],
        w83_resolution=ctx["w83"],
        binding_evidence=ctx["binding"],
        shadow_registry=ctx["shadow"],
        forward_registry=ctx["forward"],
        measurement_receipts=receipts,
        verified_at=base.resolved_at + timedelta(seconds=seconds),
    )


def _rehash_resolution(base, **changes):
    values = {
        field.name: getattr(base, field.name)
        for field in fields(base)
        if field.name != "resolution_hash"
    }
    values.update(changes)
    values["resolution_hash"] = w84._hash(w84._resolution_payload_from_values(values))
    forged = object.__new__(w84.PromotionShadowForwardResolution)
    for field in fields(w84.PromotionShadowForwardResolution):
        object.__setattr__(forged, field.name, values[field.name])
    return forged


def _forge_measurement_receipt(receipt, **changes):
    values = {
        field.name: getattr(receipt, field.name)
        for field in fields(receipt)
        if field.name not in {"measurement_hash", "receipt_hash"}
    }
    values.update(changes)
    measurement_hash = measurement._hash(
        measurement._measurement_payload_from_values(values)
    )
    receipt_values = {
        **values,
        "measurement_hash": measurement_hash,
        "captured_at": changes.get("captured_at", receipt.captured_at),
    }
    forged = object.__new__(ForwardShadowMeasurementReceipt)
    receipt_hash = measurement._hash(
        measurement._measurement_payload_from_values(
            receipt_values,
            include_capture=True,
        )
    )
    for field in fields(ForwardShadowMeasurementReceipt):
        if field.name == "measurement_hash":
            value = measurement_hash
        elif field.name == "receipt_hash":
            value = receipt_hash
        else:
            value = receipt_values[field.name]
        object.__setattr__(forged, field.name, value)
    return forged


def test_w84_source_verification_is_canonical_final_resolution(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    verified = _verify(ctx)

    assert verified.source_truth_verified is True
    assert verified.base_resolution_hash == ctx["base"].resolution_hash
    assert verified.evidence_hash == ctx["evidence"].evidence_hash
    assert verified.policy_hash == ctx["policy"].policy_hash
    assert verified.w83_resolution_hash == ctx["w83"].resolution_hash
    assert verified.w83_binding_hash == ctx["binding"].evidence_hash
    assert verified.measurement_plan_hash == ctx["plan"].plan_hash
    assert verified.shadow_sequence == len(ctx["receipts"])
    assert verified.forward_sequence == len(ctx["receipts"])
    assert verified.measurement_receipts_count == len(ctx["receipts"])
    assert verified.cumulative_return == ctx["evidence"].cumulative_return
    assert verified.peak_to_trough_drawdown == ctx["evidence"].peak_to_trough_drawdown
    assert verified.resolved_promotion_blockers == (w84.SHADOW_FORWARD_BLOCKER,)
    assert verified.remaining_promotion_blockers == ctx["base"].remaining_promotion_blockers
    assert verified.paper_candidate_authorized is False
    assert verified.external_execution_authorized is False
    assert verified.runtime_execution_authorized is False
    assert verified.capital_authority == "NONE"
    assert verified.live_trading == "BLOCKED"
    assert verified.to_dict()["verification_hash"] == verified.verification_hash


@pytest.mark.parametrize(
    "changes",
    (
        {"cumulative_return": Decimal("0.999")},
        {"peak_to_trough_drawdown": Decimal("0.099")},
        {"qualification_duration_seconds": 999},
        {"capture_lag_seconds": 0},
        {"assessment_delay_seconds": 0},
        {"forward_sequence": 1},
        {"forward_head_hash": "a" * 64},
        {"qualification_head_hash": "b" * 64},
        {"measurement_head_hash": "c" * 64},
        {"measurement_receipts_hash": "d" * 64},
        {"reason_codes": ("FORGED_PASS",)},
        {"prefix_only_measurement_bound": False},
        {"full_observed_forward_tail_bound": False},
    ),
)
def test_w84_source_verifier_rejects_rehash_valid_pass_lies(
    tmp_path,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    changes,
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    forged_evidence = _rehash_evidence(ctx["evidence"], **changes)
    forged_base = _rehash_resolution(
        ctx["base"],
        evidence_hash=forged_evidence.evidence_hash,
    )
    with pytest.raises(
        ShadowForwardSourceVerificationIntegrityError,
        match="source|PASS|durable|identity|receipt|rehash",
    ):
        _verify(ctx, evidence=forged_evidence, base=forged_base)


def test_w84_source_verifier_rejects_rehashed_window_timestamp_lies(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    evidence = ctx["evidence"]
    cases = (
        {"qualification_started_at": evidence.qualification_started_at + timedelta(seconds=60)},
        {"qualification_ended_at": evidence.qualification_ended_at + timedelta(seconds=60)},
        {"measurement_data_cutoff_at": evidence.measurement_data_cutoff_at - timedelta(seconds=60)},
        {"measurement_captured_at": evidence.measurement_captured_at + timedelta(seconds=1)},
        {"assessed_at": evidence.assessed_at + timedelta(seconds=1)},
    )
    for changes in cases:
        forged_evidence = _rehash_evidence(evidence, **changes)
        forged_base = _rehash_resolution(
            ctx["base"], evidence_hash=forged_evidence.evidence_hash
        )
        with pytest.raises(ShadowForwardSourceVerificationIntegrityError):
            _verify(ctx, evidence=forged_evidence, base=forged_base)


def test_w84_source_verifier_rejects_forged_measurement_receipt_even_when_rehashed(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    first = ctx["receipts"][0]
    forged = _forge_measurement_receipt(
        first,
        return_fraction=first.return_fraction + Decimal("0.01"),
        equity_after=first.equity_after + Decimal("100"),
    )
    with pytest.raises(
        ShadowForwardSourceVerificationIntegrityError,
        match="measurement receipts",
    ):
        _verify(ctx, receipts=(forged, *ctx["receipts"][1:]))


def test_w84_source_verifier_rejects_missing_or_extra_measurement_horizon(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    with pytest.raises(
        ShadowForwardSourceVerificationIntegrityError,
        match="measurement receipt horizon",
    ):
        _verify(ctx, receipts=ctx["receipts"][:-1])
    with pytest.raises(
        ShadowForwardSourceVerificationIntegrityError,
        match="measurement receipt horizon",
    ):
        _verify(ctx, receipts=ctx["receipts"] + (ctx["receipts"][-1],))


def test_w84_source_verifier_rejects_rehashed_base_resolution_identity_drift(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    for changes in (
        {"policy_hash": "a" * 64},
        {"measurement_plan_hash": "b" * 64},
        {"shadow_forward_promotion_bound": False},
        {"remaining_promotion_blockers": (w84.SHADOW_FORWARD_BLOCKER,)},
        {"paper_candidate_authorized": True},
    ):
        forged = _rehash_resolution(ctx["base"], **changes)
        with pytest.raises(ShadowForwardSourceVerificationIntegrityError):
            _verify(ctx, base=forged)


def test_w84_source_verifier_rejects_parent_hash_tamper_without_rehash(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    tampered = object.__new__(w84.PromotionShadowForwardResolution)
    for field in fields(w84.PromotionShadowForwardResolution):
        value = getattr(ctx["base"], field.name)
        if field.name == "resolution_hash":
            value = "0" * 64
        object.__setattr__(tampered, field.name, value)
    with pytest.raises(
        ShadowForwardSourceVerificationIntegrityError,
        match="base W84 resolution hash mismatch",
    ):
        _verify(ctx, base=tampered)


def test_w84_source_verifier_rejects_temporal_regression(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    with pytest.raises(
        ShadowForwardSourceVerificationIntegrityError,
        match="temporal causality",
    ):
        _verify(ctx, seconds=-1)


def test_w84_source_verifier_public_type_guards(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    common = dict(
        verification_id="w84-type-guard",
        base_resolution=ctx["base"],
        evidence=ctx["evidence"],
        policy=ctx["policy"],
        measurement_plan=ctx["plan"],
        w83_resolution=ctx["w83"],
        binding_evidence=ctx["binding"],
        shadow_registry=ctx["shadow"],
        forward_registry=ctx["forward"],
        measurement_receipts=ctx["receipts"],
        verified_at=ctx["base"].resolved_at + timedelta(seconds=1),
    )
    for key in (
        "base_resolution",
        "evidence",
        "policy",
        "measurement_plan",
        "shadow_registry",
        "forward_registry",
    ):
        values = dict(common)
        values[key] = object()
        with pytest.raises(TypeError):
            verify_promotion_shadow_forward_resolution_sources(**values)
    values = dict(common)
    values["measurement_receipts"] = [*ctx["receipts"]]
    with pytest.raises(TypeError):
        verify_promotion_shadow_forward_resolution_sources(**values)
    values = dict(common)
    values["measurement_receipts"] = (object(),)
    with pytest.raises(TypeError):
        verify_promotion_shadow_forward_resolution_sources(**values)


def test_w84_source_verification_receipt_constructor_is_fail_closed(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    verified = _verify(ctx)
    cases = (
        {"contract_version": "wrong"},
        {"verification_id": ""},
        {"base_resolution_hash": "not-a-hash"},
        {"shadow_sequence": -1},
        {"qualification_ended_at": verified.qualification_started_at},
        {"cumulative_return": Decimal("-1")},
        {"peak_to_trough_drawdown": Decimal("1.1")},
        {"source_truth_verified": False},
        {"resolved_promotion_blockers": ()},
        {"remaining_promotion_blockers": (w84.SHADOW_FORWARD_BLOCKER,)},
        {"strategy_version_execution_bound": False},
        {"shadow_forward_promotion_bound": False},
        {"paper_candidate_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"verification_hash": "0" * 64},
    )
    for changes in cases:
        with pytest.raises(ShadowForwardSourceVerificationIntegrityError):
            replace(verified, **changes)


def test_w84_source_verification_hash_is_reproducible(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    first = _verify(ctx)
    second = _verify(ctx)
    assert first == second
    assert first.verification_hash == second.verification_hash


def test_w84_source_verifier_helpers_fail_closed():
    with pytest.raises(ShadowForwardSourceVerificationIntegrityError):
        source_verification._utc_iso("not-a-date")
    with pytest.raises(ShadowForwardSourceVerificationIntegrityError):
        source_verification._require_hash("bad", "hash")
    with pytest.raises(ShadowForwardSourceVerificationIntegrityError):
        source_verification._require_id("", "id")
    with pytest.raises(ShadowForwardSourceVerificationIntegrityError):
        source_verification._require_nonnegative_int(True, "count")
    with pytest.raises(ShadowForwardSourceVerificationIntegrityError):
        source_verification._require_finite_decimal(Decimal("NaN"), "value")
