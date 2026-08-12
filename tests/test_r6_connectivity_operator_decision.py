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
from autotrade.brokers.alpaca_paper_connectivity_candidate import PaperConnectivityCandidateBuilder
from autotrade.brokers.alpaca_paper_connectivity_prepare import PaperConnectivityPreparationBridge
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.connectivity_operator_decision import (
    ConnectivityOperatorBridge,
    ConnectivityOperatorDecisionConflict,
    ConnectivityOperatorDecisionIntegrityError,
    ConnectivityOperatorDecisionRejected,
    ConnectivityOperatorDecisionStatus,
    SQLiteConnectivityOperatorDecisionRegistry,
    connectivity_operator_confirmation_challenge,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime
from test_r6_connectivity_candidate import NOW, workspace


def evidence(tmp_path):
    ws = workspace(tmp_path)
    PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    prepared = PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=1))
    bridge = ConnectivityOperatorBridge(ws)
    context = bridge.prepare_context(now=NOW + timedelta(seconds=20))
    return ws, prepared, bridge, context


def issue(bridge, context, *, operator_id="operator:arendon7", issued_at=None):
    issued_at = issued_at or NOW + timedelta(seconds=20)
    return bridge.issue(
        context=context,
        operator_id=operator_id,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=60),
    )


def test_context_is_connectivity_specific_and_challenge_is_deterministic(tmp_path) -> None:
    ws, prepared, _, context = evidence(tmp_path)
    assert context.environment == "PAPER"
    assert context.purpose == "CONNECTIVITY_CANARY"
    assert context.order_id == prepared.order_id
    assert context.connectivity_preparation_hash == prepared.preparation_hash
    assert context.standard_package_hash == prepared.standard_package_hash
    assert context.bracket_payload_hash == prepared.bracket_payload_hash
    assert context.core_db_sha256_after_preparation == prepared.core_db_sha256_after_preparation
    assert connectivity_operator_confirmation_challenge(context) == (
        f"APPROVE CONNECTIVITY {context.context_hash[:12]}"
    )
    payload = json.loads((ws.root / "connectivity_operator_context.json").read_text(encoding="utf-8"))
    assert payload == context.payload()
    assert not ws.operator_context_path.exists()
    assert not ws.operator_db_path.exists()


def test_human_approval_stays_before_oms_staging_and_post(tmp_path) -> None:
    ws, _, bridge, context = evidence(tmp_path)
    state = issue(bridge, context)
    assert state.status is ConnectivityOperatorDecisionStatus.ISSUED
    assert state.decision.source == "HUMAN_OPERATOR"
    assert state.decision.action == "APPROVE_CONNECTIVITY_CANARY"
    assert state.decision.is_valid_at(NOW + timedelta(seconds=21)) is True

    order = SQLiteOrderStore(SQLiteRuntime(ws.core_db_path)).get_by_order_id(context.order_id)
    assert order is not None and order.status is OrderStatus.VALIDATED
    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path)).get(context.order_id)
    assert submission.status is PaperSubmissionStatus.PREPARED
    assert submission.attempt_count == 0
    permit = SQLitePaperCanaryPermitRegistry(SQLiteRuntime(ws.permit_db_path)).get(context.canary_approval_hash)
    assert permit.status is PaperCanaryPermitStatus.ISSUED
    assert permit.attempt_id is None

    assert not ws.prepared_package_path.exists()
    assert not ws.expected_bracket_path.exists()
    assert not ws.operator_context_path.exists()
    assert not ws.operator_db_path.exists()
    assert not ws.manifest_path.exists()
    payload = json.loads((ws.root / "connectivity_operator_decision.json").read_text(encoding="utf-8"))
    assert payload["purpose"] == "CONNECTIVITY_CANARY"
    assert payload["oms_staging_authorized"] is False
    assert payload["external_post_authorized"] is False
    assert payload["external_order_submitted"] is False
    assert payload["strategy_trading_authorized"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["live_trading"] == "BLOCKED"
    assert payload["next_action"] == "CONNECTIVITY_FINAL_FRESHNESS_REQUIRED"


def test_operator_may_authorize_intent_after_initial_market_freshness_but_not_post(tmp_path) -> None:
    _, _, bridge, context = evidence(tmp_path)
    late = NOW + timedelta(seconds=45)
    state = bridge.issue(
        context=context,
        operator_id="operator:arendon7",
        issued_at=late,
        expires_at=late + timedelta(seconds=30),
    )
    assert state.status is ConnectivityOperatorDecisionStatus.ISSUED
    assert state.decision.context.context_hash == context.context_hash


def test_core_drift_after_context_blocks_human_authority(tmp_path) -> None:
    ws, _, bridge, context = evidence(tmp_path)
    conn = sqlite3.connect(ws.core_db_path)
    try:
        conn.execute("CREATE TABLE operator_drift(x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityOperatorDecisionRejected, match="core.sqlite3 changed"):
        issue(bridge, context)
    assert not (ws.root / "connectivity_operator.sqlite3").exists()
    assert not (ws.root / "connectivity_operator_decision.json").exists()


def test_preparation_tamper_blocks_context(tmp_path) -> None:
    ws, _, bridge, _ = evidence(tmp_path)
    path = ws.root / "connectivity_preparation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["external_post_authorized"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorDecisionRejected, match="external_post_authorized"):
        bridge.prepare_context(now=NOW + timedelta(seconds=21))


def test_normal_strategy_operator_artifact_blocks_connectivity_path(tmp_path) -> None:
    ws, _, bridge, _ = evidence(tmp_path)
    ws.operator_context_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConnectivityOperatorDecisionRejected, match="normal strategy operator artifacts"):
        bridge.prepare_context(now=NOW + timedelta(seconds=21))


def test_context_tamper_is_self_detecting(tmp_path) -> None:
    _, _, _, context = evidence(tmp_path)
    with pytest.raises(ValueError, match="context hash mismatch"):
        replace(context, standard_package_hash="f" * 64)


def test_decision_hash_and_ttl_are_fail_closed(tmp_path) -> None:
    _, _, bridge, context = evidence(tmp_path)
    state = issue(bridge, context)
    with pytest.raises(ValueError, match="decision hash mismatch"):
        replace(state.decision, decision_hash="f" * 64)
    issued_at = NOW + timedelta(seconds=20)
    with pytest.raises(ValueError, match="<=2 minutes"):
        bridge.issue(
            context=context,
            operator_id="operator:arendon7",
            issued_at=issued_at,
            expires_at=issued_at + timedelta(minutes=3),
        )


def test_registry_issue_is_idempotent_and_conflict_is_rejected(tmp_path) -> None:
    _, _, bridge, context = evidence(tmp_path)
    first = issue(bridge, context)
    second = issue(bridge, context)
    assert first == second
    with pytest.raises(ConnectivityOperatorDecisionConflict, match="different human decision"):
        issue(bridge, context, operator_id="operator:someone-else")


def test_registry_event_and_control_tamper_fail_closed(tmp_path) -> None:
    ws, _, bridge, context = evidence(tmp_path)
    state = issue(bridge, context)
    registry = SQLiteConnectivityOperatorDecisionRegistry(
        SQLiteRuntime(ws.root / "connectivity_operator.sqlite3")
    )
    assert registry.get(context.context_hash) == state

    conn = sqlite3.connect(ws.root / "connectivity_operator.sqlite3")
    try:
        conn.execute(
            "UPDATE connectivity_operator_events SET payload_json=? WHERE sequence=1",
            (json.dumps({"tampered": True}),),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityOperatorDecisionIntegrityError):
        registry.get(context.context_hash)

    ws2, _, bridge2, context2 = evidence(tmp_path / "control")
    issue(bridge2, context2)
    registry2 = SQLiteConnectivityOperatorDecisionRegistry(
        SQLiteRuntime(ws2.root / "connectivity_operator.sqlite3")
    )
    conn = sqlite3.connect(ws2.root / "connectivity_operator.sqlite3")
    try:
        conn.execute(
            "UPDATE connectivity_operator_control SET event_head_hash=? WHERE singleton=1",
            ("f" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityOperatorDecisionIntegrityError, match="control hash"):
        registry2.get(context2.context_hash)
