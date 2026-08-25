from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta

import pytest

import autotrade.paper_candidate_admission as admission
import autotrade.paper_candidate_admission_final_verification as admission_final
import autotrade.paper_candidate_eligibility_final as eligibility_final
import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.promotion_shadow_forward_final_verification as w84
from autotrade.persistence import SQLiteRuntime
from test_w85_paper_candidate_admission import _admit, _full_context


def _bundle_with_final_admission(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    admitted = _admit(bundle, monkeypatch)
    verification_time = admitted.admitted_at + timedelta(seconds=1)
    monkeypatch.setattr(admission_final, "_now_utc", lambda: verification_time)
    verified = admission_final.finalize_paper_candidate_admission(
        verification_id="w85-final-admission",
        admission_registry=bundle["registry"],
        admission_id=admitted.admission_id,
        promotion_policy=bundle["promotion_policy"],
        w80_assessment=bundle["assessment"],
        w81_resolution=bundle["w81"],
        w82_resolution=bundle["w82"],
        w83_resolution=bundle["w83"],
        w84_finalization=bundle["final"],
    )
    lifecycle_registry = lifecycle.SQLitePaperCandidateLifecycleRegistry(bundle["core"])
    return bundle, admitted, verified, lifecycle_registry


def _rehash_receipt(value, **changes):
    values = {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.name != "admission_hash"
    }
    values.update(changes)
    return type(value)(
        **values,
        admission_hash=admission._hash(admission._receipt_payload_from_values(values)),
    )


def _rehash_final_verification(value, **changes):
    values = {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.name != "verification_hash"
    }
    values.update(changes)
    return type(value)(
        **values,
        verification_hash=admission_final._hash(
            admission_final._payload_from_values(values)
        ),
    )


def test_w85_final_admission_explicitly_binds_w84_provenance_and_no_execution(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, _ = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )

    assert verified.admission_source_truth_verified is True
    assert verified.w84_source_truth_verified is True
    assert verified.w84_admission_source_proof_bound is True
    assert verified.historical_w84_timestamp_used_for_freshness is False
    assert verified.paper_candidate_was_admitted is True
    assert verified.admission_hash == admitted.admission_hash
    assert verified.w83_binding_hash == bundle["w83"].binding_evidence_hash
    assert verified.w84_finalization_hash == bundle["final"].finalization_hash
    assert verified.w84_source_verification_hash == bundle["final"].source_verification_hash
    assert verified.w84_policy_hash == bundle["final"].policy_hash
    assert verified.w84_evidence_hash == bundle["final"].evidence_hash
    assert verified.w84_measurement_plan_hash == bundle["final"].measurement_plan_hash
    assert verified.w84_measurement_runtime_hash == bundle["final"].measurement_runtime_hash
    assert verified.w84_admission_source_proof_hash == admitted.w84_admission_source_proof_hash
    assert (
        verified.w84_admission_source_verification_hash
        == admitted.w84_admission_source_verification_hash
    )
    assert verified.w84_admission_source_capture_at == admitted.w84_admission_source_capture_at
    assert verified.w84_admission_source_verified_at == admitted.admitted_at
    assert verified.paper_execution_authorized is False
    assert verified.external_execution_authorized is False
    assert verified.runtime_execution_authorized is False
    assert verified.capital_authority == "NONE"
    assert verified.live_trading == "BLOCKED"
    assert verified.to_dict()["verification_hash"] == verified.verification_hash


def test_w85_final_admission_rejects_validly_rehashed_wrong_w83_binding(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    admitted = _admit(bundle, monkeypatch)
    values = {
        field.name: getattr(bundle["final"], field.name)
        for field in fields(bundle["final"])
        if field.name != "finalization_hash"
    }
    values["w83_binding_hash"] = "f" * 64
    forged = w84.PromotionShadowForwardFinalVerification(
        **values,
        finalization_hash=w84._hash(w84._payload_from_values(values)),
    )
    monkeypatch.setattr(
        admission_final,
        "_now_utc",
        lambda: admitted.admitted_at + timedelta(seconds=1),
    )
    with pytest.raises(
        admission_final.PaperCandidateAdmissionFinalVerificationIntegrityError,
        match="exact W83 execution binding",
    ):
        admission_final.finalize_paper_candidate_admission(
            verification_id="w85-wrong-binding",
            admission_registry=bundle["registry"],
            admission_id=admitted.admission_id,
            promotion_policy=bundle["promotion_policy"],
            w80_assessment=bundle["assessment"],
            w81_resolution=bundle["w81"],
            w82_resolution=bundle["w82"],
            w83_resolution=bundle["w83"],
            w84_finalization=forged,
        )


def test_w85_final_admission_freshness_uses_durable_capture_not_historical_w84_clock(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    admitted = _admit(bundle, monkeypatch)
    stale_capture = admitted.admitted_at - timedelta(
        seconds=bundle["policy"].max_w84_finalization_age_seconds + 1
    )
    forged = _rehash_receipt(
        admitted,
        w84_admission_source_capture_at=stale_capture,
    )

    with pytest.raises(
        admission_final.PaperCandidateAdmissionFinalVerificationIntegrityError,
        match="durable-source freshness budget",
    ):
        admission_final._validate_exact_chain(
            receipt=forged,
            registration=bundle["registration"],
            promotion_policy=bundle["promotion_policy"],
            w80_assessment=bundle["assessment"],
            w81_resolution=bundle["w81"],
            w82_resolution=bundle["w82"],
            w83_resolution=bundle["w83"],
            w84_finalization=bundle["final"],
        )


def test_w85_final_admission_rejects_durable_admission_side_column_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    admitted = _admit(bundle, monkeypatch)
    conn = SQLiteRuntime(bundle["core"]).connect()
    try:
        conn.execute(
            "UPDATE paper_candidate_admissions SET status = 'BLOCKED' WHERE admission_id = ?",
            (admitted.admission_id,),
        )
    finally:
        conn.close()
    monkeypatch.setattr(
        admission_final,
        "_now_utc",
        lambda: admitted.admitted_at + timedelta(seconds=1),
    )
    with pytest.raises(Exception, match="SQLite column mismatch"):
        admission_final.finalize_paper_candidate_admission(
            verification_id="w85-durable-tamper",
            admission_registry=bundle["registry"],
            admission_id=admitted.admission_id,
            promotion_policy=bundle["promotion_policy"],
            w80_assessment=bundle["assessment"],
            w81_resolution=bundle["w81"],
            w82_resolution=bundle["w82"],
            w83_resolution=bundle["w83"],
            w84_finalization=bundle["final"],
        )


def test_w85_final_eligibility_active_then_suspended_without_execution_authority(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admitted, verified, registry = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    active_time = verified.process_verified_at + timedelta(seconds=1)
    monkeypatch.setattr(eligibility_final, "_now_utc", lambda: active_time)
    active = eligibility_final.project_final_paper_candidate_eligibility(
        projection_id="w85-current-active",
        final_verification=verified,
        admission_receipt=admitted,
        lifecycle_registry=registry,
    )
    assert active.state is lifecycle.PaperCandidateEligibilityState.ACTIVE
    assert active.paper_candidate_currently_eligible is True
    assert active.w84_admission_source_proof_hash == admitted.w84_admission_source_proof_hash
    assert active.paper_execution_authorized is False
    assert active.capital_authority == "NONE"

    monkeypatch.setattr(lifecycle, "_now_utc", lambda: active_time + timedelta(seconds=1))
    event = registry.append(
        event_id="w85-final-suspend",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="RISK_REVIEW",
    )
    monkeypatch.setattr(
        eligibility_final,
        "_now_utc",
        lambda: active_time + timedelta(seconds=2),
    )
    suspended = eligibility_final.project_final_paper_candidate_eligibility(
        projection_id="w85-current-suspended",
        final_verification=verified,
        admission_receipt=admitted,
        lifecycle_registry=registry,
    )
    assert suspended.state is lifecycle.PaperCandidateEligibilityState.SUSPENDED
    assert suspended.paper_candidate_currently_eligible is False
    assert suspended.lifecycle_head_hash == event.event_hash
    assert suspended.paper_execution_authorized is False
    assert suspended.capital_authority == "NONE"


def test_w85_final_eligibility_expiry_precedes_historical_revocation(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admitted, verified, registry = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    revoke_time = verified.process_verified_at + timedelta(seconds=1)
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: revoke_time)
    revoked = registry.append(
        event_id="w85-final-revoke",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.REVOKE,
        reason_code="GOVERNANCE_REVOKED",
    )
    assert admitted.valid_until is not None
    monkeypatch.setattr(
        eligibility_final,
        "_now_utc",
        lambda: admitted.valid_until + timedelta(microseconds=1),
    )
    projection = eligibility_final.project_final_paper_candidate_eligibility(
        projection_id="w85-revoked-then-expired",
        final_verification=verified,
        admission_receipt=admitted,
        lifecycle_registry=registry,
    )
    assert projection.state is lifecycle.PaperCandidateEligibilityState.EXPIRED
    assert projection.paper_candidate_currently_eligible is False
    assert projection.lifecycle_events_count == 1
    assert projection.lifecycle_head_hash == revoked.event_hash
    assert registry.list_for_admission(admitted) == (revoked,)


def test_w85_final_eligibility_rejects_validly_rehashed_source_proof_drift(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admitted, verified, registry = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    forged = _rehash_final_verification(
        verified,
        w84_admission_source_proof_hash="f" * 64,
    )
    monkeypatch.setattr(
        eligibility_final,
        "_now_utc",
        lambda: verified.process_verified_at + timedelta(seconds=1),
    )
    with pytest.raises(
        eligibility_final.PaperCandidateFinalEligibilityIntegrityError,
        match="does not match canonical W85 V2 verification",
    ):
        eligibility_final.project_final_paper_candidate_eligibility(
            projection_id="w85-source-proof-drift",
            final_verification=forged,
            admission_receipt=admitted,
            lifecycle_registry=registry,
        )


def test_w85_final_eligibility_rejects_verification_and_projection_authority_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admitted, verified, registry = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(
        admission_final.PaperCandidateAdmissionFinalVerificationIntegrityError
    ):
        replace(verified, paper_execution_authorized=True)
    with pytest.raises(
        admission_final.PaperCandidateAdmissionFinalVerificationIntegrityError,
        match="hash mismatch",
    ):
        replace(verified, verification_hash="0" * 64)

    monkeypatch.setattr(
        eligibility_final,
        "_now_utc",
        lambda: verified.process_verified_at + timedelta(seconds=1),
    )
    projection = eligibility_final.project_final_paper_candidate_eligibility(
        projection_id="w85-projection-tamper",
        final_verification=verified,
        admission_receipt=admitted,
        lifecycle_registry=registry,
    )
    with pytest.raises(eligibility_final.PaperCandidateFinalEligibilityIntegrityError):
        replace(projection, paper_execution_authorized=True)
    with pytest.raises(
        eligibility_final.PaperCandidateFinalEligibilityIntegrityError,
        match="hash mismatch",
    ):
        replace(projection, projection_hash="0" * 64)


def test_w85_final_eligibility_process_clock_cannot_precede_admission_verification(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admitted, verified, registry = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    monkeypatch.setattr(
        eligibility_final,
        "_now_utc",
        lambda: verified.process_verified_at - timedelta(microseconds=1),
    )
    with pytest.raises(
        eligibility_final.PaperCandidateFinalEligibilityIntegrityError,
        match="predates admission verification",
    ):
        eligibility_final.project_final_paper_candidate_eligibility(
            projection_id="w85-clock-regression",
            final_verification=verified,
            admission_receipt=admitted,
            lifecycle_registry=registry,
        )
