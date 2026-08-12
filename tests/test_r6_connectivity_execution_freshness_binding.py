from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

import autotrade.connectivity_execution_freshness_binding as cefb
from autotrade.brokers.alpaca_paper_submission import PaperSubmissionStatus, SQLitePaperSubmissionRegistry
from autotrade.connectivity_execution_freshness_binding import (
    ConnectivityBoundFinalFreshnessGuard,
    ConnectivityExecutionFreshnessConflict,
    ConnectivityExecutionFreshnessIntegrityError,
    ConnectivityExecutionFreshnessRejected,
    SQLiteConnectivityExecutionFreshnessRegistry,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime
from test_r6_connectivity_candidate import CREDS, NOW
from test_r6_connectivity_execution_intent import prepared, issue
from test_r6_connectivity_final_freshness import Clock, clock_from, guard, ready_workspace


def bound_workspace(tmp_path, monkeypatch):
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    execution_state = issue(execution_bridge, execution_context)
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    final_guard = guard(ws, clock=clock_from(offset=22))
    result = ConnectivityBoundFinalFreshnessGuard(
        ws, final_guard=final_guard
    ).acquire(credentials=CREDS)
    return ws, execution_context, execution_state, result


def test_bound_final_freshness_happy_path_is_still_pre_staging(tmp_path, monkeypatch) -> None:
    ws, context, execution_state, result = bound_workspace(tmp_path, monkeypatch)
    binding = result.binding
    assert binding.order_id == context.order_id
    assert binding.execution_intent_context_hash == context.context_hash
    assert binding.execution_intent_decision_hash == execution_state.decision.decision_hash
    assert binding.execution_intent_event_hash == execution_state.event_hash
    assert binding.final_freshness_permit_hash == result.final_freshness.permit.permit_hash
    assert binding.final_freshness_event_hash == result.final_freshness.state.event_hash
    assert binding.fresh_risk_decision_id == result.final_freshness.permit.fresh_risk_decision_id
    assert binding.is_valid_at(binding.issued_at) is True
    assert binding.expires_at <= result.final_freshness.permit.expires_at
    assert binding.expires_at <= execution_state.decision.expires_at

    order = SQLiteOrderStore(SQLiteRuntime(ws.core_db_path)).get_by_order_id(context.order_id)
    assert order is not None and order.status is OrderStatus.VALIDATED
    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path)).get(context.order_id)
    assert submission.status is PaperSubmissionStatus.PREPARED
    assert submission.attempt_count == 0

    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["second_human_execution_intent_bound"] is True
    assert payload["final_freshness_bound"] is True
    assert payload["max_external_post_attempts"] == 1
    assert payload["oms_staging_authorized"] is False
    assert payload["external_post_authorized"] is False
    assert payload["external_order_submitted"] is False
    assert payload["strategy_health_required"] is False
    assert payload["strategy_trading_authorized"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["profitability_claim"] is False
    assert payload["live_trading"] == "BLOCKED"
    assert payload["next_action"] == "CONNECTIVITY_STAGING_BRIDGE_REQUIRED"


def test_missing_second_execution_intent_blocks_before_final_gets(tmp_path, monkeypatch) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    calls = {"acquire": 0}

    class FakeFinalGuard:
        def acquire(self, *, credentials):
            calls["acquire"] += 1
            raise AssertionError("Final Freshness must not run")

    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="execution-intent registry is missing"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=FakeFinalGuard()).acquire(
            credentials=CREDS
        )
    assert calls["acquire"] == 0


def test_expired_second_execution_intent_blocks_before_final_gets(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    at = NOW + timedelta(seconds=21)
    execution_bridge.issue(
        context=execution_context,
        operator_id="operator:arendon7",
        issued_at=at,
        expires_at=at + timedelta(seconds=1),
    )
    calls = {"acquire": 0}

    class FakeFinalGuard:
        def acquire(self, *, credentials):
            calls["acquire"] += 1
            raise AssertionError("Final Freshness must not run")

    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22, milliseconds=1))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="execution intent is expired"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=FakeFinalGuard()).acquire(
            credentials=CREDS
        )
    assert calls["acquire"] == 0


def test_execution_intent_artifact_tamper_blocks_before_final_gets(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    issue(execution_bridge, execution_context)
    path = ws.root / "connectivity_execution_intent.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["external_post_authorized"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    calls = {"acquire": 0}

    class FakeFinalGuard:
        def acquire(self, *, credentials):
            calls["acquire"] += 1
            raise AssertionError("Final Freshness must not run")

    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="external_post_authorized"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=FakeFinalGuard()).acquire(
            credentials=CREDS
        )
    assert calls["acquire"] == 0


def test_execution_intent_expiry_during_gets_leaves_no_binding(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    at = NOW + timedelta(seconds=21)
    execution_bridge.issue(
        context=execution_context,
        operator_id="operator:arendon7",
        issued_at=at,
        expires_at=at + timedelta(seconds=2),
    )
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=21, milliseconds=100))
    times = [
        NOW + timedelta(seconds=21, milliseconds=200),
        NOW + timedelta(seconds=21, milliseconds=400),
        NOW + timedelta(seconds=21, milliseconds=600),
        NOW + timedelta(seconds=21, milliseconds=800),
        NOW + timedelta(seconds=22),
        NOW + timedelta(seconds=23, milliseconds=100),
    ]
    final_guard = guard(ws, clock=Clock(*times))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="expired during"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=final_guard).acquire(
            credentials=CREDS
        )
    assert (ws.root / "connectivity_final_freshness.json").exists()
    assert (ws.root / "connectivity_final_freshness.sqlite3").exists()
    assert not (ws.root / "connectivity_execution_freshness_binding.json").exists()
    assert not (ws.root / "connectivity_execution_freshness_binding.sqlite3").exists()


def test_binding_hash_is_self_detecting(tmp_path, monkeypatch) -> None:
    _, _, _, result = bound_workspace(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="binding hash mismatch"):
        replace(result.binding, binding_hash="f" * 64)
    assert result.binding.is_valid_at(result.binding.expires_at) is False


def test_binding_registry_is_idempotent_and_conflict_fails(tmp_path, monkeypatch) -> None:
    ws, _, _, result = bound_workspace(tmp_path, monkeypatch)
    registry = SQLiteConnectivityExecutionFreshnessRegistry(
        SQLiteRuntime(ws.root / "connectivity_execution_freshness_binding.sqlite3")
    )
    assert registry.issue(result.binding) == result.state
    with pytest.raises(ValueError, match="binding hash mismatch"):
        replace(result.binding, fresh_market_fingerprint="f" * 64)


def test_binding_registry_detects_control_tamper(tmp_path, monkeypatch) -> None:
    ws, _, _, result = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_execution_freshness_binding.sqlite3"
    registry = SQLiteConnectivityExecutionFreshnessRegistry(SQLiteRuntime(path))
    assert registry.get(result.binding.binding_hash) == result.state
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE connectivity_execution_freshness_control SET event_head_hash=? WHERE singleton=1",
            ("f" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityExecutionFreshnessIntegrityError, match="control hash"):
        registry.get(result.binding.binding_hash)


def test_binding_cannot_be_refreshed_in_place(tmp_path, monkeypatch) -> None:
    ws, _, _, _ = bound_workspace(tmp_path, monkeypatch)
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="never refresh in-place"):
        ConnectivityBoundFinalFreshnessGuard(ws).acquire(credentials=CREDS)
