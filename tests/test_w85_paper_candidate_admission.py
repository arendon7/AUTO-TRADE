from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal
import json

import pytest

import autotrade.paper_candidate_admission as w85
import autotrade.promotion_cost_continuity as w81_module
import autotrade.promotion_shadow_forward_final_verification as final_verification
from autotrade.execution_cost_continuity import CONTINUITY_BLOCKER
from autotrade.persistence import SQLiteRuntime
from autotrade.promotion_cost_continuity import (
    PromotionCostContinuityResolution,
    PromotionCostContinuityStatus,
)
from autotrade.strategy_lab_promotion import SQLiteStrategyPromotionPolicyRegistry
from autotrade.strategy_promotion_assessment import SQLiteStrategyPromotionAssessmentRegistry
from test_w83_execution_strategy_binding import _assessment
from test_w84_shadow_forward_source_verification import _context


def _w80_w81(chain, market):
    w82 = chain["w82"]
    assessment = _assessment(
        policy=chain["policy"],
        measurement_hash=w82.continuity_measurement_hash,
    )
    execution_gate = next(
        gate for gate in assessment.gates if gate.gate_id == "EXECUTION_SENSITIVITY"
    )
    values = {
        "resolution_id": w82.w81_resolution_id,
        "contract_version": w81_module.RESOLUTION_CONTRACT_VERSION,
        "promotion_assessment_id": assessment.assessment_id,
        "promotion_assessment_hash": assessment.assessment_hash,
        "promotion_policy_id": assessment.policy_id,
        "promotion_policy_hash": assessment.policy_hash,
        "selected_strategy_id": assessment.selected_strategy_id,
        "selected_strategy_version": assessment.selected_strategy_version,
        "execution_gate_evidence_hashes": tuple(sorted(execution_gate.evidence_hashes)),
        "continuity_evidence_hash": w82.continuity_evidence_hash,
        "continuity_measurement_hash": w82.continuity_measurement_hash,
        "intent_fingerprint": w82.intent_fingerprint,
        "promotion_assessed_at": assessment.assessed_at,
        "continuity_assessed_at": market.observed_at,
        "status": PromotionCostContinuityStatus.PASS,
        "reason_codes": (),
        "resolved_promotion_blockers": (CONTINUITY_BLOCKER,),
        "remaining_promotion_blockers": tuple(
            sorted(set(assessment.promotion_blockers) - {CONTINUITY_BLOCKER})
        ),
        "fee_accounting_complete": False,
        "strategy_version_execution_bound": False,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "resolved_at": w82.w81_resolved_at,
    }
    resolution = PromotionCostContinuityResolution(
        **values,
        resolution_hash=w81_module._hash(w81_module._payload_from_values(values)),
    )
    assert resolution.resolution_hash == w82.w81_resolution_hash
    return assessment, resolution


def _finalize_w84(ctx, monkeypatch):
    capture = ctx["receipts"][-1].period_ended_at + timedelta(
        seconds=ctx["evidence"].capture_lag_seconds
    )
    process_time = max(
        capture + timedelta(seconds=2),
        ctx["base"].resolved_at + timedelta(seconds=1),
    )
    monkeypatch.setattr(final_verification, "_now_utc", lambda: process_time)
    result = final_verification.finalize_promotion_shadow_forward_resolution(
        finalization_id="w84-final-for-w85",
        source_verification_id="w84-source-for-w85",
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
    return result


def _seed_w79_schema(path, promotion_policy):
    SQLiteStrategyPromotionPolicyRegistry(path)
    SQLiteStrategyPromotionAssessmentRegistry(path)
    runtime = SQLiteRuntime(path)
    conn = runtime.connect()
    try:
        conn.execute(
            """
            INSERT INTO strategy_promotion_threshold_policies(
                threshold_policy_id, threshold_policy_hash,
                development_campaign_id, holdout_campaign_id,
                registered_at, policy_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                promotion_policy.threshold_policy_id,
                promotion_policy.threshold_policy_hash,
                promotion_policy.development_campaign_id,
                promotion_policy.holdout_campaign_id,
                "2026-08-24T00:00:00+00:00",
                "{}",
            ),
        )
        conn.execute(
            """
            INSERT INTO strategy_promotion_policies(
                policy_id, policy_hash, threshold_policy_id,
                threshold_policy_hash, development_campaign_id,
                holdout_campaign_id, registered_at, policy_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                promotion_policy.policy_id,
                promotion_policy.policy_hash,
                promotion_policy.threshold_policy_id,
                promotion_policy.threshold_policy_hash,
                promotion_policy.development_campaign_id,
                promotion_policy.holdout_campaign_id,
                "2026-08-24T00:00:01+00:00",
                "{}",
            ),
        )
    finally:
        conn.close()


def _full_context(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    ctx = _context(tmp_path / "w84", limits, market, empty_portfolio, market_buy_intent)
    final = _finalize_w84(ctx, monkeypatch)
    assessment, w81 = _w80_w81(ctx["chain"], market)
    core = tmp_path / "core.sqlite3"
    _seed_w79_schema(core, ctx["chain"]["policy"])
    registry = w85.SQLitePaperCandidateAdmissionRegistry(core)
    policy = w85.build_paper_candidate_admission_policy(
        policy_id="w85-admission-policy",
        promotion_policy=ctx["chain"]["policy"],
        w83_resolution=ctx["w83"],
    )
    registered_at = final.process_verified_at + timedelta(seconds=1)
    monkeypatch.setattr(w85, "_now_utc", lambda: registered_at)
    registration = registry.register_policy(
        policy,
        promotion_policy=ctx["chain"]["policy"],
        w83_resolution=ctx["w83"],
    )
    return {
        "ctx": ctx,
        "final": final,
        "assessment": assessment,
        "w81": w81,
        "w82": ctx["chain"]["w82"],
        "w83": ctx["w83"],
        "promotion_policy": ctx["chain"]["policy"],
        "registry": registry,
        "policy": policy,
        "registration": registration,
        "registered_at": registered_at,
        "core": core,
    }


def _admit(bundle, monkeypatch, *, admission_id="w85-admission", final=None, seconds=1):
    process_time = bundle["registered_at"] + timedelta(seconds=seconds)
    monkeypatch.setattr(w85, "_now_utc", lambda: process_time)
    return bundle["registry"].assess_and_record(
        admission_id=admission_id,
        policy_id=bundle["policy"].policy_id,
        promotion_policy=bundle["promotion_policy"],
        w80_assessment=bundle["assessment"],
        w81_resolution=bundle["w81"],
        w82_resolution=bundle["w82"],
        w83_resolution=bundle["w83"],
        w84_finalization=bundle["final"] if final is None else final,
    )


def test_w85_pass_admits_candidate_but_never_execution_or_capital(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    result = _admit(bundle, monkeypatch)

    assert result.status is w85.PaperCandidateAdmissionStatus.PASS
    assert result.reason_codes == ()
    assert result.paper_candidate_authorized is True
    assert result.paper_execution_authorized is False
    assert result.external_execution_authorized is False
    assert result.runtime_execution_authorized is False
    assert result.probation_budget_is_execution_authority is False
    assert result.probation_notional_cap_usd == Decimal("5")
    assert result.probation_order_cap == 1
    assert result.capital_authority == "NONE"
    assert result.live_trading == "BLOCKED"
    assert result.w84_finalization_hash == bundle["final"].finalization_hash
    assert result.valid_until == result.admitted_at + timedelta(
        seconds=bundle["policy"].candidate_validity_seconds
    )
    assert bundle["registry"].get(result.admission_id) == result
    assert bundle["registry"].list_for_authority(result.authority_key) == (result,)


def test_w85_missing_w84_is_incomplete_and_cannot_mint_candidate(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    process_time = bundle["registered_at"] + timedelta(seconds=1)
    monkeypatch.setattr(w85, "_now_utc", lambda: process_time)
    result = bundle["registry"].assess_and_record(
        admission_id="w85-missing-w84",
        policy_id=bundle["policy"].policy_id,
        promotion_policy=bundle["promotion_policy"],
        w80_assessment=bundle["assessment"],
        w81_resolution=bundle["w81"],
        w82_resolution=bundle["w82"],
        w83_resolution=bundle["w83"],
        w84_finalization=None,
    )
    assert result.status is w85.PaperCandidateAdmissionStatus.INCOMPLETE
    assert result.reason_codes == ("W84_FINAL_VERIFICATION_MISSING",)
    assert result.paper_candidate_authorized is False
    assert result.valid_until is None
    assert result.paper_execution_authorized is False
    assert result.capital_authority == "NONE"


def test_w85_stale_w84_is_blocked_by_internal_process_clock(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    stale_time = bundle["final"].process_verified_at + timedelta(
        seconds=bundle["policy"].max_w84_finalization_age_seconds + 1
    )
    assert stale_time > bundle["registered_at"]
    monkeypatch.setattr(w85, "_now_utc", lambda: stale_time)
    result = bundle["registry"].assess_and_record(
        admission_id="w85-stale",
        policy_id=bundle["policy"].policy_id,
        promotion_policy=bundle["promotion_policy"],
        w80_assessment=bundle["assessment"],
        w81_resolution=bundle["w81"],
        w82_resolution=bundle["w82"],
        w83_resolution=bundle["w83"],
        w84_finalization=bundle["final"],
    )
    assert result.status is w85.PaperCandidateAdmissionStatus.BLOCKED
    assert result.reason_codes == ("W84_FINALIZATION_STALE",)
    assert result.paper_candidate_authorized is False
    assert result.valid_until is None


def test_w85_policy_is_bounded_and_cannot_become_execution_authority(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError):
        w85.build_paper_candidate_admission_policy(
            policy_id="too-old",
            promotion_policy=bundle["promotion_policy"],
            w83_resolution=bundle["w83"],
            max_w84_finalization_age_seconds=w85.MAX_FINALIZATION_AGE_SECONDS + 1,
        )
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError):
        w85.build_paper_candidate_admission_policy(
            policy_id="too-large",
            promotion_policy=bundle["promotion_policy"],
            w83_resolution=bundle["w83"],
            probation_notional_cap_usd=w85.MAX_PROBATION_NOTIONAL_USD + Decimal("0.01"),
        )
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError):
        replace(bundle["policy"], paper_execution_authorized=True)
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError):
        replace(bundle["policy"], capital_authority="PAPER")
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError):
        replace(bundle["policy"], live_trading="ENABLED")


def test_w85_same_admission_id_is_idempotent_but_conflicting_identity_fails(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    first = _admit(bundle, monkeypatch)
    later = bundle["registered_at"] + timedelta(seconds=2)
    monkeypatch.setattr(w85, "_now_utc", lambda: later)
    same = bundle["registry"].assess_and_record(
        admission_id=first.admission_id,
        policy_id=bundle["policy"].policy_id,
        promotion_policy=bundle["promotion_policy"],
        w80_assessment=bundle["assessment"],
        w81_resolution=bundle["w81"],
        w82_resolution=bundle["w82"],
        w83_resolution=bundle["w83"],
        w84_finalization=bundle["final"],
    )
    assert same == first

    forged_w83 = object.__new__(type(bundle["w83"]))
    for field in fields(bundle["w83"]):
        object.__setattr__(
            forged_w83,
            field.name,
            "0" * 64 if field.name == "resolution_hash" else getattr(bundle["w83"], field.name),
        )
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError, match="W83 resolution hash"):
        bundle["registry"].assess_and_record(
            admission_id=first.admission_id,
            policy_id=bundle["policy"].policy_id,
            promotion_policy=bundle["promotion_policy"],
            w80_assessment=bundle["assessment"],
            w81_resolution=bundle["w81"],
            w82_resolution=bundle["w82"],
            w83_resolution=forged_w83,
            w84_finalization=bundle["final"],
        )


def test_w85_active_pass_cannot_be_duplicated_under_new_id(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    _admit(bundle, monkeypatch)
    with pytest.raises(w85.PaperCandidateAdmissionConflict, match="active W85 admission"):
        _admit(bundle, monkeypatch, admission_id="w85-duplicate-active", seconds=2)


def test_w85_receipt_rejects_candidate_execution_and_hash_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    result = _admit(bundle, monkeypatch)
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError):
        replace(result, paper_execution_authorized=True)
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError):
        replace(result, external_execution_authorized=True)
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError):
        replace(result, runtime_execution_authorized=True)
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError):
        replace(result, capital_authority="PAPER")
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError):
        replace(result, live_trading="ENABLED")
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError, match="hash mismatch"):
        replace(result, admission_hash="0" * 64)


def test_w85_sqlite_side_column_tamper_is_detected(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    result = _admit(bundle, monkeypatch)
    conn = SQLiteRuntime(bundle["core"]).connect()
    try:
        conn.execute(
            "UPDATE paper_candidate_admissions SET status = 'BLOCKED' WHERE admission_id = ?",
            (result.admission_id,),
        )
    finally:
        conn.close()
    with pytest.raises(w85.PaperCandidateAdmissionIntegrityError, match="SQLite column mismatch"):
        bundle["registry"].get(result.admission_id)


def test_w85_policy_registration_is_idempotent_and_candidate_specific(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    later = bundle["registered_at"] + timedelta(seconds=5)
    monkeypatch.setattr(w85, "_now_utc", lambda: later)
    same = bundle["registry"].register_policy(
        bundle["policy"],
        promotion_policy=bundle["promotion_policy"],
        w83_resolution=bundle["w83"],
    )
    assert same == bundle["registration"]

    changed_values = {
        field.name: getattr(bundle["policy"], field.name)
        for field in fields(bundle["policy"])
        if field.name != "policy_hash"
    }
    changed_values["candidate_validity_seconds"] = 60
    changed = w85.PaperCandidateAdmissionPolicy(
        **changed_values,
        policy_hash=w85._hash(w85._policy_payload_from_values(changed_values)),
    )
    with pytest.raises(w85.PaperCandidateAdmissionConflict, match="identity conflict"):
        bundle["registry"].register_policy(
            changed,
            promotion_policy=bundle["promotion_policy"],
            w83_resolution=bundle["w83"],
        )
