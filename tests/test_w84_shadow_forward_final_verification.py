from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect

import pytest

import autotrade.promotion_shadow_forward_final_verification as final_verification
from autotrade.promotion_shadow_forward_final_verification import (
    PromotionShadowForwardFinalVerification,
    ShadowForwardFinalVerificationIntegrityError,
    finalize_promotion_shadow_forward_resolution,
)
from test_w84_shadow_forward_promotion_binding import _rehash_evidence
from test_w84_shadow_forward_source_verification import (
    _context,
    _rehash_resolution,
)


def _capture_at(ctx):
    return ctx["receipts"][-1].period_ended_at + timedelta(
        seconds=ctx["evidence"].capture_lag_seconds
    )


def _finalize(ctx):
    return finalize_promotion_shadow_forward_resolution(
        finalization_id="w84-final-verification",
        source_verification_id="w84-source-verification-final",
        base_resolution=ctx["base"],
        evidence=ctx["evidence"],
        policy=ctx["policy"],
        measurement_plan=ctx["plan"],
        w83_resolution=ctx["w83"],
        binding_evidence=ctx["binding"],
        shadow_registry=ctx["shadow"],
        forward_registry=ctx["forward"],
        measurement_receipts=ctx["receipts"],
    )


def test_w84_final_verification_uses_internal_process_clock_and_preserves_no_authority(
    monkeypatch, tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    observed = _capture_at(ctx) + timedelta(seconds=4)
    monkeypatch.setattr(final_verification, "_now_utc", lambda: observed)

    result = _finalize(ctx)

    assert "verified_at" not in inspect.signature(
        finalize_promotion_shadow_forward_resolution
    ).parameters
    assert result.source_truth_verified is True
    assert result.process_clock_freshness_verified is True
    assert result.measurement_capture_at == _capture_at(ctx)
    assert result.process_verified_at == observed
    assert result.decision_delay_seconds == 4
    assert result.source_verification_hash
    assert result.resolved_promotion_blockers == (
        final_verification.SHADOW_FORWARD_BLOCKER,
    )
    assert result.paper_candidate_authorized is False
    assert result.external_execution_authorized is False
    assert result.runtime_execution_authorized is False
    assert result.capital_authority == "NONE"
    assert result.live_trading == "BLOCKED"
    assert result.to_dict()["finalization_hash"] == result.finalization_hash


def test_w84_final_verification_rejects_late_clock_even_if_evidence_claims_fresh(
    monkeypatch, tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    observed = _capture_at(ctx) + timedelta(
        seconds=ctx["policy"].max_assessment_delay_seconds + 1
    )
    monkeypatch.setattr(final_verification, "_now_utc", lambda: observed)

    with pytest.raises(
        ShadowForwardFinalVerificationIntegrityError,
        match="process-clock freshness budget",
    ):
        _finalize(ctx)


def test_w84_final_verification_rejects_process_clock_before_measurement_capture(
    monkeypatch, tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    monkeypatch.setattr(
        final_verification,
        "_now_utc",
        lambda: _capture_at(ctx) - timedelta(seconds=1),
    )

    with pytest.raises(ShadowForwardFinalVerificationIntegrityError):
        _finalize(ctx)


def test_w84_final_verification_rejects_coherently_rehashed_assessment_time_lie(
    monkeypatch, tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    forged_assessed_at = ctx["evidence"].measurement_captured_at + timedelta(seconds=1)
    forged_evidence = _rehash_evidence(
        ctx["evidence"],
        assessed_at=forged_assessed_at,
        assessment_delay_seconds=1,
    )
    forged_base = _rehash_resolution(
        ctx["base"],
        evidence_hash=forged_evidence.evidence_hash,
        resolved_at=forged_assessed_at + timedelta(seconds=1),
    )
    monkeypatch.setattr(
        final_verification,
        "_now_utc",
        lambda: _capture_at(ctx)
        + timedelta(seconds=ctx["policy"].max_assessment_delay_seconds + 1),
    )

    with pytest.raises(ShadowForwardFinalVerificationIntegrityError):
        finalize_promotion_shadow_forward_resolution(
            finalization_id="w84-forged-time",
            source_verification_id="w84-source-forged-time",
            base_resolution=forged_base,
            evidence=forged_evidence,
            policy=ctx["policy"],
            measurement_plan=ctx["plan"],
            w83_resolution=ctx["w83"],
            binding_evidence=ctx["binding"],
            shadow_registry=ctx["shadow"],
            forward_registry=ctx["forward"],
            measurement_receipts=ctx["receipts"],
        )


def test_w84_final_verification_rejects_rehash_valid_metric_lie_before_clock_admission(
    monkeypatch, tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    forged_evidence = _rehash_evidence(
        ctx["evidence"], cumulative_return=Decimal("0.999")
    )
    forged_base = _rehash_resolution(
        ctx["base"], evidence_hash=forged_evidence.evidence_hash
    )
    monkeypatch.setattr(
        final_verification,
        "_now_utc",
        lambda: _capture_at(ctx) + timedelta(seconds=1),
    )

    with pytest.raises(
        ShadowForwardFinalVerificationIntegrityError,
        match="durable source truth",
    ):
        finalize_promotion_shadow_forward_resolution(
            finalization_id="w84-forged-metric",
            source_verification_id="w84-source-forged-metric",
            base_resolution=forged_base,
            evidence=forged_evidence,
            policy=ctx["policy"],
            measurement_plan=ctx["plan"],
            w83_resolution=ctx["w83"],
            binding_evidence=ctx["binding"],
            shadow_registry=ctx["shadow"],
            forward_registry=ctx["forward"],
            measurement_receipts=ctx["receipts"],
        )


def test_w84_final_verification_receipt_constructor_fail_closed(
    monkeypatch, tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    monkeypatch.setattr(
        final_verification,
        "_now_utc",
        lambda: _capture_at(ctx) + timedelta(seconds=4),
    )
    result = _finalize(ctx)

    cases = (
        {"contract_version": "wrong"},
        {"finalization_id": ""},
        {"source_verification_hash": "bad"},
        {"decision_delay_seconds": -1},
        {"source_truth_verified": False},
        {"process_clock_freshness_verified": False},
        {"resolved_promotion_blockers": ()},
        {"remaining_promotion_blockers": (final_verification.SHADOW_FORWARD_BLOCKER,)},
        {"strategy_version_execution_bound": False},
        {"shadow_forward_promotion_bound": False},
        {"paper_candidate_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"process_verified_at": result.process_verified_at + timedelta(seconds=1)},
        {"finalization_hash": "0" * 64},
    )
    for changes in cases:
        with pytest.raises(ShadowForwardFinalVerificationIntegrityError):
            replace(result, **changes)


def test_w84_final_verification_hash_reproducible_for_same_process_clock(
    monkeypatch, tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    ctx = _context(tmp_path, limits, market, empty_portfolio, market_buy_intent)
    observed = _capture_at(ctx) + timedelta(seconds=3)
    monkeypatch.setattr(final_verification, "_now_utc", lambda: observed)

    first = _finalize(ctx)
    second = _finalize(ctx)
    assert first == second
    assert first.finalization_hash == second.finalization_hash


def test_w84_final_verification_helpers_fail_closed():
    with pytest.raises(ShadowForwardFinalVerificationIntegrityError):
        final_verification._utc_iso("not-a-date")
    with pytest.raises(ShadowForwardFinalVerificationIntegrityError):
        final_verification._require_hash("bad", "hash")
    with pytest.raises(ShadowForwardFinalVerificationIntegrityError):
        final_verification._require_id("", "id")
