from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_runtime_readiness_seal as seal_module
import autotrade.paper_runtime_readiness_source_snapshot as snapshot_module
import autotrade.paper_runtime_source_postcheck as post_module
from autotrade.paper_runtime_readiness_seal import (
    PaperRuntimeReadinessSealBlocker,
    PaperRuntimeReadinessSealIntegrityError,
    PaperRuntimeReadinessSealStatus,
    seal_paper_runtime_readiness_after_collection,
)
from autotrade.paper_runtime_source_postcheck import (
    PaperRuntimeSourcePostcheckIntegrityError,
    PaperRuntimeSourcePostcheckProof,
    PaperRuntimeSourcePostcheckReader,
)
from test_w86_paper_runtime_read_only_pipeline import _collect, _source_candidate
from test_w86_w85_durable_eligibility_source import _active_source_bundle


def _durable_snapshot(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    snap_at = reproved_at + timedelta(seconds=1)
    monkeypatch.setattr(snapshot_module, "_now_utc", lambda: snap_at)
    proof = snapshot_module.W85DurableEligibilitySnapshotReader(bundle["core"]).verify_current(
        proof_id="w86-postcheck-initial",
        eligibility=eligibility,
        final_verification=verified,
    )
    return bundle, admitted, registry, proof, snap_at


def test_post_collection_reproof_accepts_exact_unchanged_active_source(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, _, source, snap_at = _durable_snapshot(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    monkeypatch.setattr(post_module, "_now_utc", lambda: snap_at + timedelta(seconds=1))

    proof = PaperRuntimeSourcePostcheckReader(bundle["core"]).verify_after_collection(
        proof_id="w86-postcheck-active",
        source_snapshot=source,
    )

    assert proof.source_snapshot_hash == source.proof_hash
    assert proof.current_state is lifecycle.PaperCandidateEligibilityState.ACTIVE
    assert proof.current_lifecycle_head_hash == source.lifecycle_head_hash
    assert proof.current_lifecycle_events_count == source.lifecycle_events_count
    assert proof.source_unchanged is True
    assert proof.candidate_currently_eligible is True
    assert proof.post_collection_source_verified is True
    assert proof.sqlite_read_only is True
    assert proof.paper_execution_authorized is False
    assert proof.external_execution_authorized is False
    assert proof.runtime_execution_authorized is False
    assert proof.capital_authority == "NONE"
    assert proof.live_trading == "BLOCKED"


def test_post_collection_suspend_is_retained_as_fail_closed_evidence(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, registry, source, snap_at = _durable_snapshot(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    event_at = snap_at + timedelta(milliseconds=100)
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: event_at)
    event = registry.append(
        event_id="w86-postcheck-suspend",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="POST_NETWORK_RISK_REVIEW",
    )
    monkeypatch.setattr(post_module, "_now_utc", lambda: snap_at + timedelta(seconds=1))

    proof = PaperRuntimeSourcePostcheckReader(bundle["core"]).verify_after_collection(
        proof_id="w86-postcheck-suspended",
        source_snapshot=source,
    )
    assert proof.current_state is lifecycle.PaperCandidateEligibilityState.SUSPENDED
    assert proof.current_lifecycle_head_hash == event.event_hash
    assert proof.current_lifecycle_events_count == source.lifecycle_events_count + 1
    assert proof.source_unchanged is False
    assert proof.candidate_currently_eligible is False
    assert proof.post_collection_source_verified is False
    assert proof.paper_execution_authorized is False
    assert proof.capital_authority == "NONE"


def test_suspend_then_reinstate_still_invalidates_original_source_identity(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, registry, source, snap_at = _durable_snapshot(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    times = iter(
        (
            snap_at + timedelta(milliseconds=100),
            snap_at + timedelta(milliseconds=200),
        )
    )
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: next(times))
    registry.append(
        event_id="w86-postcheck-suspend-reinstate-1",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="INTERLEAVED_REVIEW",
    )
    registry.append(
        event_id="w86-postcheck-suspend-reinstate-2",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.REINSTATE,
        reason_code="INTERLEAVED_REVIEW_CLEARED",
    )
    monkeypatch.setattr(post_module, "_now_utc", lambda: snap_at + timedelta(seconds=1))

    proof = PaperRuntimeSourcePostcheckReader(bundle["core"]).verify_after_collection(
        proof_id="w86-postcheck-reinstated",
        source_snapshot=source,
    )
    assert proof.current_state is lifecycle.PaperCandidateEligibilityState.ACTIVE
    assert proof.candidate_currently_eligible is True
    assert proof.source_unchanged is False
    assert proof.post_collection_source_verified is False
    assert proof.current_lifecycle_events_count == source.lifecycle_events_count + 2


def test_postcheck_rejects_tampered_initial_snapshot_before_trusting_durable_source(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, _, source, _ = _durable_snapshot(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    object.__setattr__(source, "proof_hash", "f" * 64)
    with pytest.raises(
        PaperRuntimeSourcePostcheckIntegrityError,
        match="snapshot hash mismatch",
    ):
        PaperRuntimeSourcePostcheckReader(bundle["core"]).verify_after_collection(
            proof_id="w86-postcheck-tampered",
            source_snapshot=source,
        )


def _post_proof(source, *, observed_at, changed=False, state=None):
    state = state or lifecycle.PaperCandidateEligibilityState.ACTIVE
    current_head = "f" * 64 if changed else source.lifecycle_head_hash
    current_count = source.lifecycle_events_count + (1 if changed else 0)
    source_unchanged = not changed
    candidate_eligible = (
        state is lifecycle.PaperCandidateEligibilityState.ACTIVE
        and observed_at <= source.admission_valid_until
    )
    verified = source_unchanged and candidate_eligible
    values = {
        "proof_id": "w86-seal-postcheck",
        "contract_version": post_module.PAPER_RUNTIME_SOURCE_POSTCHECK_VERSION,
        "source_snapshot_hash": source.proof_hash,
        "authority_key": source.authority_key,
        "admission_id": source.admission_id,
        "admission_hash": source.admission_hash,
        "policy_id": source.policy_id,
        "policy_hash": source.policy_hash,
        "policy_registration_hash": source.policy_registration_hash,
        "initial_lifecycle_head_hash": source.lifecycle_head_hash,
        "initial_lifecycle_events_count": source.lifecycle_events_count,
        "current_lifecycle_head_hash": current_head,
        "current_lifecycle_events_count": current_count,
        "current_state": state,
        "admission_valid_until": source.admission_valid_until,
        "observed_at": observed_at,
        "source_unchanged": source_unchanged,
        "candidate_currently_eligible": candidate_eligible,
        "post_collection_source_verified": verified,
        "sqlite_read_only": True,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperRuntimeSourcePostcheckProof(
        **values,
        proof_hash=post_module._hash(post_module._payload_values(values)),
    )


def _install_postcheck(monkeypatch, proof):
    class Reader:
        def __init__(self, core_path):
            assert str(core_path)

        def verify_after_collection(self, *, proof_id, source_snapshot):
            assert proof_id.endswith(":source-postcheck")
            assert source_snapshot.proof_hash == proof.source_snapshot_hash
            return proof

    monkeypatch.setattr(seal_module, "PaperRuntimeSourcePostcheckReader", Reader)


def test_readiness_seal_is_finite_read_only_and_still_not_execution_authority(monkeypatch):
    result, _, _, _ = _collect(monkeypatch)
    source, candidate = _source_candidate()
    observed = result.funding_capacity.observed_at
    post = _post_proof(source, observed_at=observed)
    _install_postcheck(monkeypatch, post)

    sealed = seal_paper_runtime_readiness_after_collection(
        seal_id="w86-final-seal",
        pipeline_result=result,
        source_snapshot=source,
        candidate_identity=candidate,
        core_path="/read-only/core.sqlite3",
    )

    assert sealed.seal.status is PaperRuntimeReadinessSealStatus.READY
    assert sealed.seal.blocker_codes == ()
    assert sealed.seal.paper_runtime_ready is True
    assert sealed.seal.valid_until <= observed + timedelta(seconds=1)
    assert sealed.seal.post_collection_source_verified is True
    assert sealed.seal.source_unchanged_after_network is True
    assert sealed.seal.separate_execution_approval_required is True
    assert sealed.seal.broker_write_performed is False
    assert sealed.seal.capital_reserved is False
    assert sealed.seal.paper_execution_authorized is False
    assert sealed.seal.external_execution_authorized is False
    assert sealed.seal.runtime_execution_authorized is False
    assert sealed.seal.capital_authority == "NONE"
    assert sealed.seal.live_trading == "BLOCKED"


def test_readiness_seal_blocks_even_if_source_changed_back_to_active(monkeypatch):
    result, _, _, _ = _collect(monkeypatch)
    source, candidate = _source_candidate()
    observed = result.funding_capacity.observed_at
    post = _post_proof(source, observed_at=observed, changed=True)
    _install_postcheck(monkeypatch, post)

    sealed = seal_paper_runtime_readiness_after_collection(
        seal_id="w86-reinstated-but-changed",
        pipeline_result=result,
        source_snapshot=source,
        candidate_identity=candidate,
        core_path="/read-only/core.sqlite3",
    )
    assert sealed.post_collection_source.current_state is lifecycle.PaperCandidateEligibilityState.ACTIVE
    assert sealed.seal.status is PaperRuntimeReadinessSealStatus.BLOCKED
    assert sealed.seal.blocker_codes == (PaperRuntimeReadinessSealBlocker.W85_SOURCE_CHANGED,)
    assert sealed.seal.paper_runtime_ready is False
    assert sealed.seal.valid_until == sealed.seal.observed_at
    assert sealed.seal.paper_execution_authorized is False


def test_readiness_seal_blocks_suspended_source(monkeypatch):
    result, _, _, _ = _collect(monkeypatch)
    source, candidate = _source_candidate()
    post = _post_proof(
        source,
        observed_at=result.funding_capacity.observed_at,
        changed=True,
        state=lifecycle.PaperCandidateEligibilityState.SUSPENDED,
    )
    _install_postcheck(monkeypatch, post)

    sealed = seal_paper_runtime_readiness_after_collection(
        seal_id="w86-suspended-seal",
        pipeline_result=result,
        source_snapshot=source,
        candidate_identity=candidate,
        core_path="/read-only/core.sqlite3",
    )
    assert sealed.seal.status is PaperRuntimeReadinessSealStatus.BLOCKED
    assert sealed.seal.blocker_codes == (
        PaperRuntimeReadinessSealBlocker.W85_SOURCE_CHANGED,
        PaperRuntimeReadinessSealBlocker.W85_CANDIDATE_NOT_ACTIVE,
    )
    assert sealed.seal.paper_runtime_ready is False
    assert sealed.seal.capital_authority == "NONE"


def test_readiness_seal_rejects_upstream_receipt_tamper(monkeypatch):
    result, _, _, _ = _collect(monkeypatch)
    source, candidate = _source_candidate()
    post = _post_proof(source, observed_at=result.funding_capacity.observed_at)
    _install_postcheck(monkeypatch, post)
    object.__setattr__(result.receipt, "receipt_hash", "0" * 64)

    with pytest.raises(Exception, match="hash mismatch"):
        seal_paper_runtime_readiness_after_collection(
            seal_id="w86-tampered-upstream",
            pipeline_result=result,
            source_snapshot=source,
            candidate_identity=candidate,
            core_path="/read-only/core.sqlite3",
        )


def test_readiness_seal_receipt_hash_and_authority_guards(monkeypatch):
    result, _, _, _ = _collect(monkeypatch)
    source, candidate = _source_candidate()
    post = _post_proof(source, observed_at=result.funding_capacity.observed_at)
    _install_postcheck(monkeypatch, post)
    sealed = seal_paper_runtime_readiness_after_collection(
        seal_id="w86-seal-guards",
        pipeline_result=result,
        source_snapshot=source,
        candidate_identity=candidate,
        core_path="/read-only/core.sqlite3",
    )

    with pytest.raises(PaperRuntimeReadinessSealIntegrityError, match="hash mismatch"):
        replace(sealed.seal, receipt_hash="0" * 64)
    with pytest.raises(PaperRuntimeReadinessSealIntegrityError, match="may not grant"):
        replace(sealed.seal, paper_execution_authorized=True)
