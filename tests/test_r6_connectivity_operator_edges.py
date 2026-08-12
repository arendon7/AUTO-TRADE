from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import sqlite3

import pytest

import autotrade.connectivity_operator_decision as cod
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.connectivity_operator_decision import (
    ConnectivityOperatorBridge,
    ConnectivityOperatorDecisionConflict,
    ConnectivityOperatorDecisionContext,
    ConnectivityOperatorDecisionIntegrityError,
    ConnectivityOperatorDecisionRejected,
    SQLiteConnectivityOperatorDecisionRegistry,
    connectivity_operator_confirmation_challenge,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_connectivity_candidate import NOW
from test_r6_connectivity_operator_decision import evidence, issue


def test_bridge_requires_operational_workspace() -> None:
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        ConnectivityOperatorBridge(object())  # type: ignore[arg-type]


def test_bridge_requires_timezone_aware_now(tmp_path) -> None:
    ws = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    bridge = ConnectivityOperatorBridge(ws)
    with pytest.raises(ValueError, match="timezone-aware"):
        bridge.prepare_context(now=NOW.replace(tzinfo=None))


def test_bridge_requires_preparation_artifact(tmp_path) -> None:
    ws = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    with pytest.raises(ConnectivityOperatorDecisionRejected, match="connectivity preparation"):
        ConnectivityOperatorBridge(ws).prepare_context(now=NOW)


def test_preparation_hash_tamper_fails_closed(tmp_path) -> None:
    ws, _, bridge, _ = evidence(tmp_path)
    path = ws.root / "connectivity_preparation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["preparation_hash"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorDecisionRejected, match="preparation hash mismatch"):
        bridge.prepare_context(now=NOW + timedelta(seconds=21))


def test_claimed_core_hash_tamper_fails_closed(tmp_path) -> None:
    ws, _, bridge, _ = evidence(tmp_path)
    path = ws.root / "connectivity_preparation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["core_db_sha256_after_preparation"] = "f" * 64
    without_hash = dict(payload)
    without_hash.pop("preparation_hash", None)
    payload["preparation_hash"] = cod._hash(without_hash)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorDecisionRejected, match="core.sqlite3 changed"):
        bridge.prepare_context(now=NOW + timedelta(seconds=21))


def test_bracket_hash_relationship_tamper_fails_closed(tmp_path) -> None:
    ws, _, bridge, _ = evidence(tmp_path)
    path = ws.root / "connectivity_preparation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["expected_bracket_payload_hash"] = "f" * 64
    without_hash = dict(payload)
    without_hash.pop("preparation_hash", None)
    payload["preparation_hash"] = cod._hash(without_hash)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorDecisionRejected, match="prepared bracket hash mismatch"):
        bridge.prepare_context(now=NOW + timedelta(seconds=21))


def test_context_file_conflict_blocks_reverification(tmp_path) -> None:
    ws, _, bridge, context = evidence(tmp_path)
    bridge.context_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConnectivityOperatorDecisionConflict, match="refusing to overwrite"):
        bridge.verify_context(expected=context, now=NOW + timedelta(seconds=21))


def test_decision_file_conflict_blocks_reissue(tmp_path) -> None:
    ws, _, bridge, context = evidence(tmp_path)
    issue(bridge, context)
    bridge.decision_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConnectivityOperatorDecisionConflict, match="refusing to overwrite"):
        issue(bridge, context)


def test_normal_strategy_operator_db_blocks_connectivity(tmp_path) -> None:
    ws, _, bridge, _ = evidence(tmp_path)
    ws.operator_db_path.write_bytes(b"not-used")
    with pytest.raises(ConnectivityOperatorDecisionRejected, match="normal strategy operator artifacts"):
        bridge.prepare_context(now=NOW + timedelta(seconds=21))


def test_confirmation_challenge_requires_exact_context_type() -> None:
    with pytest.raises(TypeError, match="ConnectivityOperatorDecisionContext"):
        connectivity_operator_confirmation_challenge(object())  # type: ignore[arg-type]


def test_context_validation_rejects_live_and_wrong_purpose(tmp_path) -> None:
    _, _, _, context = evidence(tmp_path)
    with pytest.raises(ValueError, match="PAPER-only"):
        replace(context, environment="LIVE")
    with pytest.raises(ValueError, match="purpose"):
        replace(context, purpose="STRATEGY")


def test_context_validation_rejects_invalid_identity_notional_and_hash(tmp_path) -> None:
    _, _, _, context = evidence(tmp_path)
    with pytest.raises(ValueError, match="order_id"):
        replace(context, order_id=" bad ")
    with pytest.raises(ValueError, match="notional"):
        replace(context, notional=Decimal("0"))
    with pytest.raises(ValueError, match="context hash mismatch"):
        replace(context, context_hash="f" * 64)


def test_decision_validation_rejects_wrong_source_action_and_window(tmp_path) -> None:
    _, _, bridge, context = evidence(tmp_path)
    state = issue(bridge, context)
    with pytest.raises(ValueError, match="HUMAN_OPERATOR"):
        replace(state.decision, source="AGENT")
    with pytest.raises(ValueError, match="action"):
        replace(state.decision, action="APPROVE_SINGLE_PAPER_CANARY")
    with pytest.raises(ValueError, match="<=2 minutes"):
        replace(state.decision, expires_at=state.decision.issued_at)
    assert state.decision.is_valid_at(state.decision.issued_at - timedelta(microseconds=1)) is False
    assert state.decision.is_valid_at(state.decision.expires_at) is False


def test_registry_rejects_wrong_context_type(tmp_path) -> None:
    registry = SQLiteConnectivityOperatorDecisionRegistry(SQLiteRuntime(tmp_path / "operator.sqlite3"))
    with pytest.raises(TypeError, match="ConnectivityOperatorDecisionContext"):
        registry.issue(
            context=object(),  # type: ignore[arg-type]
            operator_id="operator:test",
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=1),
        )


def test_registry_get_missing_and_list_states(tmp_path) -> None:
    ws, _, bridge, context = evidence(tmp_path)
    registry = SQLiteConnectivityOperatorDecisionRegistry(SQLiteRuntime(bridge.registry_path))
    with pytest.raises(KeyError):
        registry.get("f" * 64)
    state = issue(bridge, context)
    assert registry.list_states() == (state,)


def test_registry_tail_deletion_is_detected(tmp_path) -> None:
    ws, _, bridge, context = evidence(tmp_path)
    issue(bridge, context)
    registry = SQLiteConnectivityOperatorDecisionRegistry(SQLiteRuntime(bridge.registry_path))
    conn = sqlite3.connect(bridge.registry_path)
    try:
        conn.execute("DELETE FROM connectivity_operator_events WHERE sequence=1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityOperatorDecisionIntegrityError, match="count mismatch"):
        registry.get(context.context_hash)


def test_empty_registry_non_genesis_head_is_detected(tmp_path) -> None:
    path = tmp_path / "operator.sqlite3"
    registry = SQLiteConnectivityOperatorDecisionRegistry(SQLiteRuntime(path))
    fake_head = "f" * 64
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE connectivity_operator_control SET event_head_hash=?,control_hash=? WHERE singleton=1",
            (fake_head, cod._control_hash(0, fake_head)),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityOperatorDecisionIntegrityError, match="empty connectivity operator ledger"):
        registry.list_states()


def test_context_from_payload_rejects_noncanonical_surface(tmp_path) -> None:
    _, _, _, context = evidence(tmp_path)
    payload = context.payload()
    payload["unexpected"] = True
    with pytest.raises(ConnectivityOperatorDecisionIntegrityError, match="non-canonical"):
        cod._context_from_payload(payload)


def test_decision_from_payload_rejects_noncanonical_surface(tmp_path) -> None:
    _, _, bridge, context = evidence(tmp_path)
    state = issue(bridge, context)
    payload = state.decision.payload()
    payload["unexpected"] = True
    with pytest.raises(ConnectivityOperatorDecisionIntegrityError, match="non-canonical"):
        cod._decision_from_payload(payload)
