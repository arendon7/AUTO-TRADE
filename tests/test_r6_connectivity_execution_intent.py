from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_canary_permit import (
    PaperCanaryPermitStatus,
    SQLitePaperCanaryPermitRegistry,
)
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.connectivity_execution_intent import (
    ConnectivityExecutionIntentBridge,
    ConnectivityExecutionIntentConflict,
    ConnectivityExecutionIntentIntegrityError,
    ConnectivityExecutionIntentRejected,
    ConnectivityExecutionIntentStatus,
    SQLiteConnectivityExecutionIntentRegistry,
    connectivity_execution_intent_challenge,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime
from test_r6_connectivity_candidate import NOW
from test_r6_connectivity_final_freshness import ready_workspace


def prepared(tmp_path):
    ws, preparation, operator_context, operator_state = ready_workspace(tmp_path)
    bridge = ConnectivityExecutionIntentBridge(ws)
    context = bridge.prepare_context(now=NOW + timedelta(seconds=21))
    return ws, preparation, operator_context, operator_state, bridge, context


def issue(bridge, context, *, operator_id="operator:arendon7", at=None):
    at = at or NOW + timedelta(seconds=21)
    return bridge.issue(
        context=context,
        operator_id=operator_id,
        issued_at=at,
        expires_at=at + timedelta(seconds=30),
    )


def test_execution_intent_context_binds_operator_and_preparation(tmp_path) -> None:
    ws, preparation, operator_context, operator_state, bridge, context = prepared(tmp_path)
    assert context.environment == "PAPER"
    assert context.purpose == "CONNECTIVITY_CANARY"
    assert context.order_id == operator_context.order_id
    assert context.operator_context_hash == operator_context.context_hash
    assert context.operator_decision_hash == operator_state.decision.decision_hash
    assert context.preparation_hash == preparation.preparation_hash
    assert connectivity_execution_intent_challenge(context) == (
        f"CONFIRM PAPER EXECUTION {context.context_hash[:12]}"
    )
    payload = json.loads(bridge.context_path.read_text(encoding="utf-8"))
    assert payload == context.payload()
    assert not (ws.root / "connectivity_final_freshness.json").exists()
    assert not (ws.root / "connectivity_final_freshness.sqlite3").exists()


def test_execution_intent_happy_path_never_stages_or_posts(tmp_path) -> None:
    ws, _, _, _, bridge, context = prepared(tmp_path)
    state = issue(bridge, context)
    assert state.status is ConnectivityExecutionIntentStatus.ISSUED
    assert state.decision.source == "HUMAN_OPERATOR"
    assert state.decision.action == "CONFIRM_CONNECTIVITY_EXECUTION_INTENT"
    assert state.decision.max_external_post_attempts == 1
    assert state.decision.is_valid_at(NOW + timedelta(seconds=22)) is True

    order = SQLiteOrderStore(SQLiteRuntime(ws.core_db_path)).get_by_order_id(context.order_id)
    assert order is not None and order.status is OrderStatus.VALIDATED
    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path)).get(context.order_id)
    assert submission.status is PaperSubmissionStatus.PREPARED
    assert submission.attempt_count == 0
    original = SQLitePaperCanaryPermitRegistry(SQLiteRuntime(ws.permit_db_path)).get(
        context.canary_approval_hash
    )
    assert original.status is PaperCanaryPermitStatus.ISSUED
    assert original.attempt_id is None

    artifact = json.loads(bridge.artifact_path.read_text(encoding="utf-8"))
    assert artifact["human_execution_intent_recorded"] is True
    assert artifact["max_external_post_attempts"] == 1
    assert artifact["final_freshness_required"] is True
    assert artifact["oms_staging_authorized"] is False
    assert artifact["external_post_authorized"] is False
    assert artifact["external_order_submitted"] is False
    assert artifact["strategy_health_required"] is False
    assert artifact["strategy_trading_authorized"] is False
    assert artifact["capital_authority"] == "NONE"
    assert artifact["profitability_claim"] is False
    assert artifact["live_trading"] == "BLOCKED"
    assert artifact["next_action"] == "INLINE_FINAL_FRESHNESS_REQUIRED"


def test_execution_intent_must_precede_final_freshness(tmp_path) -> None:
    ws, _, _, _, bridge, _ = prepared(tmp_path)
    (ws.root / "connectivity_final_freshness.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConnectivityExecutionIntentRejected, match="must precede final freshness"):
        bridge.prepare_context(now=NOW + timedelta(seconds=22))


def test_execution_intent_refuses_expired_first_operator(tmp_path) -> None:
    ws, _, _, _, bridge, _ = prepared(tmp_path)
    with pytest.raises(ConnectivityExecutionIntentRejected, match="operator decision is expired"):
        bridge.prepare_context(now=NOW + timedelta(seconds=81))


def test_execution_intent_refuses_core_drift(tmp_path) -> None:
    ws, _, _, _, bridge, _ = prepared(tmp_path)
    conn = sqlite3.connect(ws.core_db_path)
    try:
        conn.execute("CREATE TABLE execution_intent_drift(x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityExecutionIntentRejected, match="core.sqlite3 changed"):
        bridge.prepare_context(now=NOW + timedelta(seconds=22))


def test_execution_intent_refuses_operator_artifact_tamper(tmp_path) -> None:
    ws, _, _, _, bridge, _ = prepared(tmp_path)
    path = ws.root / "connectivity_operator_decision.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["external_post_authorized"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityExecutionIntentRejected, match="external_post_authorized"):
        bridge.prepare_context(now=NOW + timedelta(seconds=22))


def test_execution_intent_context_tamper_is_self_detecting(tmp_path) -> None:
    _, _, _, _, _, context = prepared(tmp_path)
    with pytest.raises(ValueError, match="context hash mismatch"):
        replace(context, standard_package_hash="f" * 64)


def test_execution_intent_decision_is_one_attempt_and_short_lived(tmp_path) -> None:
    _, _, _, _, bridge, context = prepared(tmp_path)
    state = issue(bridge, context)
    with pytest.raises(ValueError, match="exactly one"):
        replace(state.decision, max_external_post_attempts=2)
    with pytest.raises(ValueError, match="<=90 seconds"):
        replace(
            state.decision,
            expires_at=state.decision.issued_at + timedelta(seconds=91),
        )
    with pytest.raises(ValueError, match="decision hash mismatch"):
        replace(state.decision, decision_hash="f" * 64)
    assert state.decision.is_valid_at(state.decision.expires_at) is False


def test_execution_intent_registry_is_idempotent_and_conflict_fails(tmp_path) -> None:
    _, _, _, _, bridge, context = prepared(tmp_path)
    first = issue(bridge, context)
    assert issue(bridge, context) == first
    with pytest.raises(ConnectivityExecutionIntentConflict, match="different execution intent"):
        issue(bridge, context, operator_id="operator:other")


def test_execution_intent_registry_detects_control_tamper(tmp_path) -> None:
    _, _, _, _, bridge, context = prepared(tmp_path)
    issue(bridge, context)
    registry = SQLiteConnectivityExecutionIntentRegistry(SQLiteRuntime(bridge.registry_path))
    conn = sqlite3.connect(bridge.registry_path)
    try:
        conn.execute(
            "UPDATE connectivity_execution_intent_control SET event_head_hash=? WHERE singleton=1",
            ("f" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityExecutionIntentIntegrityError, match="control hash"):
        registry.list_states()
