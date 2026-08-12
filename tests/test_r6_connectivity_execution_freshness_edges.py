from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

import autotrade.connectivity_execution_freshness_binding as cefb
from autotrade.connectivity_execution_freshness_binding import (
    ConnectivityBoundFinalFreshnessGuard,
    ConnectivityExecutionFreshnessBinding,
    ConnectivityExecutionFreshnessConflict,
    ConnectivityExecutionFreshnessIntegrityError,
    ConnectivityExecutionFreshnessRejected,
    SQLiteConnectivityExecutionFreshnessRegistry,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime
from test_r6_connectivity_candidate import CREDS, NOW
from test_r6_connectivity_execution_freshness_binding import bound_workspace
from test_r6_connectivity_execution_intent import prepared, issue
from test_r6_connectivity_final_freshness import clock_from, guard


def test_bound_guard_requires_workspace_type() -> None:
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        ConnectivityBoundFinalFreshnessGuard(object())  # type: ignore[arg-type]


def test_bound_guard_requires_credentials_type(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    issue(execution_bridge, execution_context)
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    with pytest.raises(TypeError, match="AlpacaPaperCredentials"):
        ConnectivityBoundFinalFreshnessGuard(ws).acquire(credentials=object())  # type: ignore[arg-type]


def test_missing_execution_intent_artifact_blocks_before_final_guard(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    issue(execution_bridge, execution_context)
    (ws.root / "connectivity_execution_intent.json").unlink()
    calls = {"count": 0}

    class FakeFinalGuard:
        def acquire(self, *, credentials):
            calls["count"] += 1
            raise AssertionError("must not run")

    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="artifact is missing"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=FakeFinalGuard()).acquire(credentials=CREDS)
    assert calls["count"] == 0


def test_execution_intent_decision_artifact_drift_blocks_before_final_guard(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    issue(execution_bridge, execution_context)
    path = ws.root / "connectivity_execution_intent.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["decision"]["operator_id"] = "operator:tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    calls = {"count": 0}

    class FakeFinalGuard:
        def acquire(self, *, credentials):
            calls["count"] += 1
            raise AssertionError("must not run")

    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="artifact/registry decision mismatch"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=FakeFinalGuard()).acquire(credentials=CREDS)
    assert calls["count"] == 0


def test_execution_intent_event_artifact_drift_blocks_before_final_guard(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    issue(execution_bridge, execution_context)
    path = ws.root / "connectivity_execution_intent.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["event_hash"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="artifact/registry event mismatch"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=object()).acquire(credentials=CREDS)  # type: ignore[arg-type]


def test_unsafe_execution_intent_live_marker_blocks(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    issue(execution_bridge, execution_context)
    path = ws.root / "connectivity_execution_intent.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["live_trading"] = "ENABLED"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="live_trading"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=object()).acquire(credentials=CREDS)  # type: ignore[arg-type]


def test_nonvalidated_oms_order_blocks_before_final_guard(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    issue(execution_bridge, execution_context)
    store = SQLiteOrderStore(SQLiteRuntime(ws.core_db_path))
    order = store.get_by_order_id(execution_context.order_id)
    assert order is not None
    store.update(replace(order, status=OrderStatus.SUBMITTING, submitted_at=NOW + timedelta(seconds=22)))
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="OMS order must remain VALIDATED"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=object()).acquire(credentials=CREDS)  # type: ignore[arg-type]


def test_core_drift_blocks_before_final_guard(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    issue(execution_bridge, execution_context)
    conn = sqlite3.connect(ws.core_db_path)
    try:
        conn.execute("CREATE TABLE binding_core_drift(x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="core.sqlite3 changed before"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=object()).acquire(credentials=CREDS)  # type: ignore[arg-type]


def test_submission_unknown_blocks_before_final_guard(tmp_path, monkeypatch) -> None:
    ws, _, _, _, execution_bridge, execution_context = prepared(tmp_path)
    issue(execution_bridge, execution_context)
    from autotrade.brokers.alpaca_paper_submission import SQLitePaperSubmissionRegistry

    registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path))
    registry.mark_submit_attempt_unknown(
        order_id=execution_context.order_id,
        attempt_id="premature-binding-attempt",
        now=NOW + timedelta(seconds=22),
    )
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    with pytest.raises(ConnectivityExecutionFreshnessRejected, match="pristine PREPARED"):
        ConnectivityBoundFinalFreshnessGuard(ws, final_guard=object()).acquire(credentials=CREDS)  # type: ignore[arg-type]


def test_binding_validation_rejects_identity_hash_version_window(tmp_path, monkeypatch) -> None:
    _, _, _, result = bound_workspace(tmp_path, monkeypatch)
    binding = result.binding
    with pytest.raises(ValueError, match="order_id"):
        replace(binding, order_id=" bad ")
    with pytest.raises(ValueError, match="execution_intent_decision_hash"):
        replace(binding, execution_intent_decision_hash="not-hash")
    with pytest.raises(ValueError, match="safety_state_version"):
        replace(binding, safety_state_version=-1)
    with pytest.raises(ValueError, match="expire after issue"):
        replace(binding, expires_at=binding.issued_at)


def test_registry_requires_binding_type_get_missing_and_lists_empty(tmp_path) -> None:
    registry = SQLiteConnectivityExecutionFreshnessRegistry(SQLiteRuntime(tmp_path / "binding.sqlite3"))
    assert registry.list_states() == ()
    with pytest.raises(TypeError, match="ConnectivityExecutionFreshnessBinding"):
        registry.issue(object())  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        registry.get("f" * 64)


def test_registry_rejects_valid_different_binding(tmp_path, monkeypatch) -> None:
    ws, _, _, result = bound_workspace(tmp_path, monkeypatch)
    registry = SQLiteConnectivityExecutionFreshnessRegistry(
        SQLiteRuntime(ws.root / "connectivity_execution_freshness_binding.sqlite3")
    )
    assert registry.issue(result.binding) == result.state
    other_ws, _, _, other_result = bound_workspace(tmp_path / "other", monkeypatch)
    assert other_result.binding.binding_hash != result.binding.binding_hash
    with pytest.raises(ConnectivityExecutionFreshnessConflict, match="different execution/freshness binding"):
        registry.issue(other_result.binding)


def test_registry_detects_event_count_tamper(tmp_path, monkeypatch) -> None:
    ws, _, _, _ = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_execution_freshness_binding.sqlite3"
    registry = SQLiteConnectivityExecutionFreshnessRegistry(SQLiteRuntime(path))
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM connectivity_execution_freshness_events WHERE sequence=1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityExecutionFreshnessIntegrityError, match="event count mismatch"):
        registry.list_states()


def test_registry_detects_payload_tamper(tmp_path, monkeypatch) -> None:
    ws, _, _, result = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_execution_freshness_binding.sqlite3"
    registry = SQLiteConnectivityExecutionFreshnessRegistry(SQLiteRuntime(path))
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE connectivity_execution_freshness_events SET payload_json=? WHERE sequence=1",
            (json.dumps({"tampered": True}),),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityExecutionFreshnessIntegrityError):
        registry.get(result.binding.binding_hash)


def test_registry_detects_event_hash_tamper(tmp_path, monkeypatch) -> None:
    ws, _, _, result = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_execution_freshness_binding.sqlite3"
    registry = SQLiteConnectivityExecutionFreshnessRegistry(SQLiteRuntime(path))
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE connectivity_execution_freshness_events SET event_hash=? WHERE sequence=1",
            ("f" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityExecutionFreshnessIntegrityError, match="event hash mismatch"):
        registry.get(result.binding.binding_hash)


def test_empty_registry_non_genesis_head_is_detected(tmp_path) -> None:
    path = tmp_path / "binding.sqlite3"
    registry = SQLiteConnectivityExecutionFreshnessRegistry(SQLiteRuntime(path))
    fake = "f" * 64
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE connectivity_execution_freshness_control SET event_head_hash=?,control_hash=? WHERE singleton=1",
            (fake, cefb._control_hash(0, fake)),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityExecutionFreshnessIntegrityError, match="non-genesis"):
        registry.list_states()


def test_binding_parser_rejects_noncanonical_and_unsafe_fields(tmp_path, monkeypatch) -> None:
    _, _, _, result = bound_workspace(tmp_path, monkeypatch)
    payload = result.binding.payload()
    payload["unexpected"] = True
    with pytest.raises(ConnectivityExecutionFreshnessIntegrityError, match="non-canonical"):
        cefb._binding_from_payload(payload)
    payload = result.binding.payload()
    payload["external_post_authorized"] = True
    with pytest.raises(ConnectivityExecutionFreshnessIntegrityError, match="external_post_authorized"):
        cefb._binding_from_payload(payload)


def test_json_helpers_reject_invalid_shapes() -> None:
    with pytest.raises(ConnectivityExecutionFreshnessIntegrityError, match="JSON is invalid"):
        cefb._json_object("{", "x")
    with pytest.raises(ConnectivityExecutionFreshnessIntegrityError, match="must be object"):
        cefb._json_object("[]", "x")
    with pytest.raises(ValueError, match="datetime value"):
        cefb._iso("not-a-datetime")
