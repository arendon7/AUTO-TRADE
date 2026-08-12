from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import sqlite3

import pytest

import autotrade.connectivity_execution_intent as cei
from autotrade.brokers.alpaca_paper_submission import SQLitePaperSubmissionRegistry
from autotrade.connectivity_execution_intent import (
    ConnectivityExecutionIntentBridge,
    ConnectivityExecutionIntentConflict,
    ConnectivityExecutionIntentIntegrityError,
    ConnectivityExecutionIntentRejected,
    SQLiteConnectivityExecutionIntentRegistry,
    connectivity_execution_intent_challenge,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_connectivity_candidate import NOW
from test_r6_connectivity_execution_intent import prepared, issue
from test_r6_connectivity_final_freshness import ready_workspace


def test_bridge_requires_operational_workspace() -> None:
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        ConnectivityExecutionIntentBridge(object())  # type: ignore[arg-type]


def test_bridge_requires_timezone_aware_now(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        ConnectivityExecutionIntentBridge(ws).prepare_context(now=NOW.replace(tzinfo=None))


def test_final_freshness_registry_alone_also_blocks_intent(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    (ws.root / "connectivity_final_freshness.sqlite3").write_bytes(b"reserved")
    with pytest.raises(ConnectivityExecutionIntentRejected, match="must precede final freshness"):
        ConnectivityExecutionIntentBridge(ws).prepare_context(now=NOW + timedelta(seconds=21))


def test_missing_operator_registry_blocks_intent(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    (ws.root / "connectivity_operator.sqlite3").unlink()
    with pytest.raises(ConnectivityExecutionIntentRejected, match="operator registry is missing"):
        ConnectivityExecutionIntentBridge(ws).prepare_context(now=NOW + timedelta(seconds=21))


def test_operator_artifact_event_hash_tamper_blocks_intent(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    path = ws.root / "connectivity_operator_decision.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["event_hash"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityExecutionIntentRejected, match="event hash mismatch"):
        ConnectivityExecutionIntentBridge(ws).prepare_context(now=NOW + timedelta(seconds=21))


def test_operator_artifact_decision_tamper_blocks_intent(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    path = ws.root / "connectivity_operator_decision.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["decision"]["operator_id"] = "operator:tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityExecutionIntentRejected, match="decision mismatch"):
        ConnectivityExecutionIntentBridge(ws).prepare_context(now=NOW + timedelta(seconds=21))


def test_operator_artifact_live_marker_blocks_intent(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    path = ws.root / "connectivity_operator_decision.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["live_trading"] = "ENABLED"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityExecutionIntentRejected, match="live_trading"):
        ConnectivityExecutionIntentBridge(ws).prepare_context(now=NOW + timedelta(seconds=21))


def test_submission_unknown_blocks_second_intent(tmp_path) -> None:
    ws, _, operator_context, _ = ready_workspace(tmp_path)
    registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path))
    registry.mark_submit_attempt_unknown(
        order_id=operator_context.order_id,
        attempt_id="premature-attempt",
        now=NOW + timedelta(seconds=21),
    )
    with pytest.raises(ConnectivityExecutionIntentRejected, match="pristine PREPARED"):
        ConnectivityExecutionIntentBridge(ws).prepare_context(now=NOW + timedelta(seconds=22))


def test_preparation_hash_tamper_blocks_second_intent(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    path = ws.root / "connectivity_preparation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["preparation_hash"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityExecutionIntentRejected, match="preparation/operator hash mismatch"):
        ConnectivityExecutionIntentBridge(ws).prepare_context(now=NOW + timedelta(seconds=21))


def test_context_validation_rejects_wrong_environment_purpose_notional_and_hash(tmp_path) -> None:
    _, _, _, _, _, context = prepared(tmp_path)
    with pytest.raises(ValueError, match="PAPER-only"):
        replace(context, environment="LIVE")
    with pytest.raises(ValueError, match="purpose"):
        replace(context, purpose="STRATEGY")
    with pytest.raises(ValueError, match="notional"):
        replace(context, notional=Decimal("0"))
    with pytest.raises(ValueError, match="context hash mismatch"):
        replace(context, context_hash="f" * 64)


def test_execution_intent_challenge_requires_exact_context_type() -> None:
    with pytest.raises(TypeError, match="ConnectivityExecutionIntentContext"):
        connectivity_execution_intent_challenge(object())  # type: ignore[arg-type]


def test_decision_validation_rejects_source_action_and_zero_window(tmp_path) -> None:
    _, _, _, _, bridge, context = prepared(tmp_path)
    state = issue(bridge, context)
    with pytest.raises(ValueError, match="HUMAN_OPERATOR"):
        replace(state.decision, source="AGENT")
    with pytest.raises(ValueError, match="action"):
        replace(state.decision, action="EXECUTE_NOW")
    with pytest.raises(ValueError, match="<=90 seconds"):
        replace(state.decision, expires_at=state.decision.issued_at)
    assert state.decision.is_valid_at(state.decision.issued_at - timedelta(microseconds=1)) is False


def test_registry_requires_context_type_and_lists_empty(tmp_path) -> None:
    registry = SQLiteConnectivityExecutionIntentRegistry(SQLiteRuntime(tmp_path / "intent.sqlite3"))
    assert registry.list_states() == ()
    with pytest.raises(TypeError, match="ConnectivityExecutionIntentContext"):
        registry.issue(
            context=object(),  # type: ignore[arg-type]
            operator_id="operator:test",
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=1),
        )


def test_registry_detects_event_count_tamper(tmp_path) -> None:
    _, _, _, _, bridge, context = prepared(tmp_path)
    issue(bridge, context)
    registry = SQLiteConnectivityExecutionIntentRegistry(SQLiteRuntime(bridge.registry_path))
    conn = sqlite3.connect(bridge.registry_path)
    try:
        conn.execute("DELETE FROM connectivity_execution_intent_events WHERE sequence=1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityExecutionIntentIntegrityError, match="count mismatch"):
        registry.list_states()


def test_registry_detects_payload_tamper(tmp_path) -> None:
    _, _, _, _, bridge, context = prepared(tmp_path)
    issue(bridge, context)
    registry = SQLiteConnectivityExecutionIntentRegistry(SQLiteRuntime(bridge.registry_path))
    conn = sqlite3.connect(bridge.registry_path)
    try:
        conn.execute(
            "UPDATE connectivity_execution_intent_events SET payload_json=? WHERE sequence=1",
            (json.dumps({"tampered": True}),),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityExecutionIntentIntegrityError):
        registry.list_states()


def test_empty_registry_non_genesis_head_is_detected(tmp_path) -> None:
    path = tmp_path / "intent.sqlite3"
    registry = SQLiteConnectivityExecutionIntentRegistry(SQLiteRuntime(path))
    fake = "f" * 64
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE connectivity_execution_intent_control SET event_head_hash=?,control_hash=? WHERE singleton=1",
            (fake, cei._control_hash(0, fake)),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityExecutionIntentIntegrityError, match="non-genesis"):
        registry.list_states()


def test_context_and_decision_parsers_reject_noncanonical_payloads(tmp_path) -> None:
    _, _, _, _, bridge, context = prepared(tmp_path)
    context_payload = context.payload()
    context_payload["unexpected"] = True
    with pytest.raises(ConnectivityExecutionIntentIntegrityError, match="non-canonical"):
        cei._context_from_payload(context_payload)
    state = issue(bridge, context)
    decision_payload = state.decision.payload()
    decision_payload["unexpected"] = True
    with pytest.raises(ConnectivityExecutionIntentIntegrityError, match="non-canonical"):
        cei._decision_from_payload(decision_payload)


def test_json_helpers_reject_invalid_shapes() -> None:
    with pytest.raises(ConnectivityExecutionIntentIntegrityError, match="JSON is invalid"):
        cei._json_object("{", "x")
    with pytest.raises(ConnectivityExecutionIntentIntegrityError, match="must be object"):
        cei._json_object("[]", "x")


def test_context_file_conflict_fails_closed(tmp_path) -> None:
    _, _, _, _, bridge, _ = prepared(tmp_path)
    bridge.context_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConnectivityExecutionIntentConflict, match="refusing to overwrite"):
        bridge.prepare_context(now=NOW + timedelta(seconds=22))
