from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

import autotrade.paper_candidate_admission_lifecycle as lifecycle
from autotrade.persistence import SQLiteRuntime
from test_w85_paper_candidate_admission import _admit, _full_context


def _admitted_bundle(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle = _full_context(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    admission = _admit(bundle, monkeypatch)
    registry = lifecycle.SQLitePaperCandidateLifecycleRegistry(bundle["core"])
    return bundle, admission, registry


def _clock(monkeypatch, when):
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: when)


def test_w85_admitted_candidate_projects_active_but_never_execution_authority(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admission, registry = _admitted_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    _clock(monkeypatch, admission.admitted_at + timedelta(seconds=1))
    projection = registry.current_projection(admission)

    assert projection.state is lifecycle.PaperCandidateEligibilityState.ACTIVE
    assert projection.paper_candidate_currently_eligible is True
    assert projection.lifecycle_events_count == 0
    assert projection.lifecycle_head_hash == lifecycle.ZERO_EVENT_HASH
    assert projection.paper_execution_authorized is False
    assert projection.external_execution_authorized is False
    assert projection.runtime_execution_authorized is False
    assert projection.capital_authority == "NONE"
    assert projection.live_trading == "BLOCKED"


def test_w85_suspend_reinstate_revoke_is_append_only_and_revocation_terminal(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admission, registry = _admitted_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    t1 = admission.admitted_at + timedelta(seconds=1)
    _clock(monkeypatch, t1)
    suspended = registry.append(
        event_id="w85-suspend",
        admission_receipt=admission,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="OPERATOR_RISK_REVIEW",
    )
    assert suspended.ordinal == 1
    assert suspended.candidate_eligible_after_event is False

    _clock(monkeypatch, t1 + timedelta(seconds=1))
    projection = registry.current_projection(admission)
    assert projection.state is lifecycle.PaperCandidateEligibilityState.SUSPENDED
    assert projection.paper_candidate_currently_eligible is False

    _clock(monkeypatch, t1 + timedelta(seconds=2))
    reinstated = registry.append(
        event_id="w85-reinstate",
        admission_receipt=admission,
        action=lifecycle.PaperCandidateLifecycleAction.REINSTATE,
        reason_code="RISK_REVIEW_CLEARED",
    )
    assert reinstated.ordinal == 2
    assert reinstated.previous_event_hash == suspended.event_hash
    assert reinstated.candidate_eligible_after_event is True

    _clock(monkeypatch, t1 + timedelta(seconds=3))
    revoked = registry.append(
        event_id="w85-revoke",
        admission_receipt=admission,
        action=lifecycle.PaperCandidateLifecycleAction.REVOKE,
        reason_code="GOVERNANCE_REVOKED",
    )
    assert revoked.ordinal == 3
    assert revoked.previous_event_hash == reinstated.event_hash
    assert revoked.candidate_eligible_after_event is False

    _clock(monkeypatch, t1 + timedelta(seconds=4))
    projection = registry.current_projection(admission)
    assert projection.state is lifecycle.PaperCandidateEligibilityState.REVOKED
    assert projection.paper_candidate_currently_eligible is False
    assert projection.lifecycle_events_count == 3
    assert projection.lifecycle_head_hash == revoked.event_hash

    with pytest.raises(lifecycle.PaperCandidateLifecycleConflict, match="terminal"):
        registry.append(
            event_id="w85-after-revoke",
            admission_receipt=admission,
            action=lifecycle.PaperCandidateLifecycleAction.REINSTATE,
            reason_code="INVALID_REINSTATE",
        )


def test_w85_invalid_transition_fails_closed(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admission, registry = _admitted_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    _clock(monkeypatch, admission.admitted_at + timedelta(seconds=1))
    with pytest.raises(lifecycle.PaperCandidateLifecycleConflict, match="SUSPENDED"):
        registry.append(
            event_id="bad-reinstate",
            admission_receipt=admission,
            action=lifecycle.PaperCandidateLifecycleAction.REINSTATE,
            reason_code="NO_PRIOR_SUSPENSION",
        )


def test_w85_candidate_expiry_is_process_clock_projection_not_history_mutation(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admission, registry = _admitted_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    assert admission.valid_until is not None
    _clock(monkeypatch, admission.valid_until + timedelta(microseconds=1))
    projection = registry.current_projection(admission)
    assert projection.state is lifecycle.PaperCandidateEligibilityState.EXPIRED
    assert projection.paper_candidate_currently_eligible is False
    assert projection.lifecycle_events_count == 0
    assert projection.paper_execution_authorized is False
    assert projection.capital_authority == "NONE"

    with pytest.raises(lifecycle.PaperCandidateLifecycleConflict, match="expired"):
        registry.append(
            event_id="post-expiry-suspend",
            admission_receipt=admission,
            action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
            reason_code="TOO_LATE",
        )


def test_w85_lifecycle_event_idempotency_and_conflict(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admission, registry = _admitted_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    when = admission.admitted_at + timedelta(seconds=1)
    _clock(monkeypatch, when)
    first = registry.append(
        event_id="stable-event-id",
        admission_receipt=admission,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="RISK_PAUSE",
    )
    _clock(monkeypatch, when + timedelta(seconds=1))
    same = registry.append(
        event_id="stable-event-id",
        admission_receipt=admission,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="RISK_PAUSE",
    )
    assert same == first

    with pytest.raises(lifecycle.PaperCandidateLifecycleConflict, match="identity conflict"):
        registry.append(
            event_id="stable-event-id",
            admission_receipt=admission,
            action=lifecycle.PaperCandidateLifecycleAction.REVOKE,
            reason_code="RISK_PAUSE",
        )


def test_w85_lifecycle_receipts_and_projection_reject_authority_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    _, admission, registry = _admitted_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    _clock(monkeypatch, admission.admitted_at + timedelta(seconds=1))
    event = registry.append(
        event_id="tamper-source-event",
        admission_receipt=admission,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="RISK_PAUSE",
    )
    with pytest.raises(lifecycle.PaperCandidateLifecycleIntegrityError):
        replace(event, paper_execution_authorized=True)
    with pytest.raises(lifecycle.PaperCandidateLifecycleIntegrityError):
        replace(event, capital_authority="PAPER")
    with pytest.raises(lifecycle.PaperCandidateLifecycleIntegrityError):
        replace(event, live_trading="ENABLED")
    with pytest.raises(lifecycle.PaperCandidateLifecycleIntegrityError, match="hash mismatch"):
        replace(event, event_hash="0" * 64)

    _clock(monkeypatch, admission.admitted_at + timedelta(seconds=2))
    projection = registry.current_projection(admission)
    with pytest.raises(lifecycle.PaperCandidateLifecycleIntegrityError):
        replace(projection, paper_candidate_currently_eligible=True)
    with pytest.raises(lifecycle.PaperCandidateLifecycleIntegrityError):
        replace(projection, paper_execution_authorized=True)
    with pytest.raises(lifecycle.PaperCandidateLifecycleIntegrityError):
        replace(projection, capital_authority="PAPER")


def test_w85_lifecycle_sqlite_side_column_tamper_is_detected(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admission, registry = _admitted_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    _clock(monkeypatch, admission.admitted_at + timedelta(seconds=1))
    event = registry.append(
        event_id="side-column-event",
        admission_receipt=admission,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="RISK_PAUSE",
    )
    conn = SQLiteRuntime(bundle["core"]).connect()
    try:
        conn.execute(
            "UPDATE paper_candidate_admission_events SET action = 'REVOKE' WHERE event_id = ?",
            (event.event_id,),
        )
    finally:
        conn.close()
    with pytest.raises(lifecycle.PaperCandidateLifecycleIntegrityError, match="SQLite column mismatch"):
        registry.list_for_admission(admission)


def test_w85_lifecycle_requires_durable_pass_admission(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admission, registry = _admitted_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    forged = object.__new__(type(admission))
    for field_name in admission.__dataclass_fields__:
        value = getattr(admission, field_name)
        if field_name == "admission_hash":
            value = "0" * 64
        object.__setattr__(forged, field_name, value)
    _clock(monkeypatch, admission.admitted_at + timedelta(seconds=1))
    with pytest.raises(lifecycle.PaperCandidateLifecycleIntegrityError, match="admission hash"):
        registry.current_projection(forged)

    conn = SQLiteRuntime(bundle["core"]).connect()
    try:
        conn.execute(
            "DELETE FROM paper_candidate_admissions WHERE admission_id = ?",
            (admission.admission_id,),
        )
    finally:
        conn.close()
    with pytest.raises(lifecycle.PaperCandidateLifecycleIntegrityError, match="absent"):
        registry.current_projection(admission)
