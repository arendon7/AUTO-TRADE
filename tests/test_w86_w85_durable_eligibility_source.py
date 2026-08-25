from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta
from pathlib import Path

import pytest

import autotrade.paper_candidate_admission_final_verification as admission_final
import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_candidate_eligibility_final as eligibility_final
import autotrade.paper_runtime_readiness_source as source
from autotrade.persistence import SQLiteRuntime
from test_w85_final_admission_and_eligibility import (
    _bundle_with_final_admission,
    _rehash_final_verification,
)


def _active_source_bundle(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, lifecycle_registry = _bundle_with_final_admission(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    observed_at = verified.process_verified_at + timedelta(seconds=1)
    monkeypatch.setattr(eligibility_final, "_now_utc", lambda: observed_at)
    eligibility = eligibility_final.project_final_paper_candidate_eligibility(
        projection_id="w86-source-active",
        final_verification=verified,
        admission_receipt=admitted,
        lifecycle_registry=lifecycle_registry,
    )
    reproved_at = observed_at + timedelta(seconds=1)
    monkeypatch.setattr(source, "_now_utc", lambda: reproved_at)
    return bundle, admitted, verified, lifecycle_registry, eligibility, reproved_at


def _rehash_eligibility(value, **changes):
    values = {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.name != "projection_hash"
    }
    values.update(changes)
    return type(value)(
        **values,
        projection_hash=eligibility_final._hash(
            eligibility_final._payload_from_values(values)
        ),
    )


def test_w86_reproves_current_active_w85_from_query_only_sqlite(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, _, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    proof = source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
        proof_id="w86-w85-source-proof",
        eligibility=eligibility,
        final_verification=verified,
    )

    assert proof.admission_id == admitted.admission_id
    assert proof.admission_hash == admitted.admission_hash
    assert proof.policy_hash == admitted.policy_hash
    assert proof.w84_admission_source_proof_hash == admitted.w84_admission_source_proof_hash
    assert proof.current_state is lifecycle.PaperCandidateEligibilityState.ACTIVE
    assert proof.candidate_currently_eligible is True
    assert proof.probation_notional_cap_usd == admitted.probation_notional_cap_usd
    assert proof.probation_order_cap == admitted.probation_order_cap
    assert proof.reproved_at == reproved_at
    assert proof.durable_admission_verified is True
    assert proof.durable_lifecycle_verified is True
    assert proof.sqlite_read_only is True
    assert proof.paper_execution_authorized is False
    assert proof.external_execution_authorized is False
    assert proof.runtime_execution_authorized is False
    assert proof.capital_authority == "NONE"
    assert proof.live_trading == "BLOCKED"
    assert proof.to_dict()["proof_hash"] == proof.proof_hash


def test_w86_source_reader_rejects_missing_or_symlinked_core(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="existing authoritative core",
    ):
        source.W85DurableEligibilitySourceReader(missing)
    assert not missing.exists()

    real = tmp_path / "real.sqlite3"
    real.write_bytes(b"")
    link = tmp_path / "core-link.sqlite3"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink unavailable on this platform")
    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="symlinked",
    ):
        source.W85DurableEligibilitySourceReader(link)


def test_w86_source_reader_requires_complete_w85_schema(tmp_path):
    path = tmp_path / "empty.sqlite3"
    conn = __import__("sqlite3").connect(path)
    conn.close()
    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="complete durable W85",
    ):
        source.W85DurableEligibilitySourceReader(path).verify_current(
            proof_id="w86-empty",
            eligibility=object(),
            final_verification=object(),
        )


def test_w86_rejects_stale_supplied_eligibility_after_durable_suspend(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: reproved_at)
    registry.append(
        event_id="w86-source-suspend",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="W86_REVIEW",
    )
    monkeypatch.setattr(source, "_now_utc", lambda: reproved_at + timedelta(seconds=1))

    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="stale or differs from durable lifecycle",
    ):
        source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
            proof_id="w86-stale-after-suspend",
            eligibility=eligibility,
            final_verification=verified,
        )


def test_w86_reproves_current_suspended_w85_without_execution_authority(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, _, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: reproved_at)
    event = registry.append(
        event_id="w86-source-current-suspend",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="W86_REVIEW",
    )
    observed = reproved_at + timedelta(seconds=1)
    monkeypatch.setattr(eligibility_final, "_now_utc", lambda: observed)
    suspended = eligibility_final.project_final_paper_candidate_eligibility(
        projection_id="w86-source-suspended",
        final_verification=verified,
        admission_receipt=admitted,
        lifecycle_registry=registry,
    )
    monkeypatch.setattr(source, "_now_utc", lambda: observed + timedelta(seconds=1))
    proof = source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
        proof_id="w86-suspended-proof",
        eligibility=suspended,
        final_verification=verified,
    )
    assert proof.current_state is lifecycle.PaperCandidateEligibilityState.SUSPENDED
    assert proof.lifecycle_head_hash == event.event_hash
    assert proof.candidate_currently_eligible is False
    assert proof.paper_execution_authorized is False
    assert proof.capital_authority == "NONE"


def test_w86_rejects_active_eligibility_that_has_now_expired(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, _, eligibility, _ = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    assert admitted.valid_until is not None
    monkeypatch.setattr(
        source,
        "_now_utc",
        lambda: admitted.valid_until + timedelta(microseconds=1),
    )
    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="current lifecycle state",
    ):
        source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
            proof_id="w86-expired-stale",
            eligibility=eligibility,
            final_verification=verified,
        )


def test_w86_can_reprove_expired_truth_but_never_marks_candidate_eligible(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, _, _ = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    assert admitted.valid_until is not None
    expired_at = admitted.valid_until + timedelta(microseconds=1)
    monkeypatch.setattr(eligibility_final, "_now_utc", lambda: expired_at)
    expired = eligibility_final.project_final_paper_candidate_eligibility(
        projection_id="w86-source-expired",
        final_verification=verified,
        admission_receipt=admitted,
        lifecycle_registry=registry,
    )
    monkeypatch.setattr(source, "_now_utc", lambda: expired_at + timedelta(seconds=1))
    proof = source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
        proof_id="w86-expired-proof",
        eligibility=expired,
        final_verification=verified,
    )
    assert proof.current_state is lifecycle.PaperCandidateEligibilityState.EXPIRED
    assert proof.candidate_currently_eligible is False
    assert proof.runtime_execution_authorized is False


def test_w86_rejects_durable_admission_side_column_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, _, eligibility, _ = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    conn = SQLiteRuntime(bundle["core"]).connect()
    try:
        conn.execute(
            "UPDATE paper_candidate_admissions SET status='BLOCKED' WHERE admission_id=?",
            (admitted.admission_id,),
        )
    finally:
        conn.close()
    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="admission receipt failed integrity",
    ):
        source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
            proof_id="w86-admission-tamper",
            eligibility=eligibility,
            final_verification=verified,
        )


def test_w86_rejects_durable_policy_side_column_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, _, eligibility, _ = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    conn = SQLiteRuntime(bundle["core"]).connect()
    try:
        conn.execute(
            "UPDATE paper_candidate_admission_policies SET authority_key=? WHERE policy_id=?",
            ("f" * 64, admitted.policy_id),
        )
    finally:
        conn.close()
    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="policy registration failed integrity",
    ):
        source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
            proof_id="w86-policy-tamper",
            eligibility=eligibility,
            final_verification=verified,
        )


def test_w86_rejects_durable_lifecycle_side_column_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, _, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: reproved_at)
    event = registry.append(
        event_id="w86-tamper-event",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="RISK_REVIEW",
    )
    observed = reproved_at + timedelta(seconds=1)
    monkeypatch.setattr(eligibility_final, "_now_utc", lambda: observed)
    suspended = eligibility_final.project_final_paper_candidate_eligibility(
        projection_id="w86-tamper-suspended",
        final_verification=verified,
        admission_receipt=admitted,
        lifecycle_registry=registry,
    )
    conn = SQLiteRuntime(bundle["core"]).connect()
    try:
        conn.execute(
            "UPDATE paper_candidate_admission_events SET action='REVOKE' WHERE event_id=?",
            (event.event_id,),
        )
    finally:
        conn.close()
    monkeypatch.setattr(source, "_now_utc", lambda: observed + timedelta(seconds=1))
    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="lifecycle failed integrity",
    ):
        source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
            proof_id="w86-lifecycle-tamper",
            eligibility=suspended,
            final_verification=verified,
        )


def test_w86_rejects_validly_rehashed_final_verification_provenance_drift(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, verified, _, eligibility, _ = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    forged_verification = _rehash_final_verification(
        verified,
        intent_fingerprint="f" * 64,
    )
    forged_eligibility = _rehash_eligibility(
        eligibility,
        final_admission_verification_hash=forged_verification.verification_hash,
    )
    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="durable intent fingerprint",
    ):
        source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
            proof_id="w86-forged-final-verification",
            eligibility=forged_eligibility,
            final_verification=forged_verification,
        )


def test_w86_rejects_eligibility_source_proof_drift_even_when_rehashed(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, verified, _, eligibility, _ = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    forged = _rehash_eligibility(
        eligibility,
        w84_admission_source_proof_hash="f" * 64,
    )
    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="source-proof mismatch",
    ):
        source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
            proof_id="w86-source-proof-drift",
            eligibility=forged,
            final_verification=verified,
        )


def test_w86_rejects_future_supplied_eligibility_clock(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, _, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    assert admitted.valid_until is not None
    future = reproved_at + timedelta(seconds=5)
    assert future < admitted.valid_until
    forged = _rehash_eligibility(eligibility, observed_at=future)
    monkeypatch.setattr(source, "_now_utc", lambda: reproved_at)
    with pytest.raises(
        source.PaperRuntimeReadinessSourceIntegrityError,
        match="process clock predates",
    ):
        source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
            proof_id="w86-future-eligibility",
            eligibility=forged,
            final_verification=verified,
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"paper_execution_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"sqlite_read_only": False},
        {"durable_admission_verified": False},
        {"durable_lifecycle_verified": False},
    ),
)
def test_w86_source_proof_constructor_rejects_authority_or_truth_downgrade(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    changes,
):
    bundle, _, verified, _, eligibility, _ = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    proof = source.W85DurableEligibilitySourceReader(bundle["core"]).verify_current(
        proof_id="w86-proof-guard",
        eligibility=eligibility,
        final_verification=verified,
    )
    with pytest.raises(source.PaperRuntimeReadinessSourceIntegrityError):
        replace(proof, **changes)


def test_w86_source_reader_type_guards(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, verified, _, eligibility, _ = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    reader = source.W85DurableEligibilitySourceReader(bundle["core"])
    with pytest.raises(TypeError, match="eligibility"):
        reader.verify_current(
            proof_id="w86-type-eligibility",
            eligibility=object(),
            final_verification=verified,
        )
    with pytest.raises(TypeError, match="final_verification"):
        reader.verify_current(
            proof_id="w86-type-final",
            eligibility=eligibility,
            final_verification=object(),
        )
