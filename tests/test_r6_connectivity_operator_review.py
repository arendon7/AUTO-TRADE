from __future__ import annotations

from datetime import timedelta
import json
import sqlite3

import pytest

import autotrade.connectivity_execution_freshness_binding as cefb
from autotrade.connectivity_execution_freshness_binding import ConnectivityBoundFinalFreshnessGuard
from autotrade.connectivity_operator_review import (
    ConnectivityOperatorReviewConflict,
    ConnectivityOperatorReviewReceiptBuilder,
    ConnectivityOperatorReviewRejected,
    ConnectivityReviewedBoundFinalFreshnessGuard,
    ConnectivityReviewedExecutionIntentBridge,
    SQLiteConnectivityExecutionReviewBindingStore,
    load_operator_review_receipt,
    reviewed_execution_intent_challenge,
    verify_execution_review_binding,
    verify_reviewed_final_freshness_binding,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_connectivity_candidate import CREDS, NOW
from test_r6_connectivity_final_freshness import clock_from, guard, ready_workspace


def reviewed_workspace(tmp_path):
    ws, prepared, operator_context, operator_state = ready_workspace(tmp_path)
    receipt = ConnectivityOperatorReviewReceiptBuilder(ws).build(
        now=NOW + timedelta(seconds=20, milliseconds=500)
    )
    bridge = ConnectivityReviewedExecutionIntentBridge(ws)
    context, observed_receipt = bridge.prepare(now=NOW + timedelta(seconds=21))
    assert observed_receipt == receipt
    state, review_binding = bridge.issue(
        context=context,
        receipt_hash=receipt.receipt_hash,
        operator_id="operator:arendon7",
        issued_at=NOW + timedelta(seconds=21),
        expires_at=NOW + timedelta(seconds=51),
    )
    return ws, prepared, operator_context, operator_state, receipt, context, state, review_binding


def test_review_receipt_freezes_exact_human_order_snapshot(tmp_path) -> None:
    ws, prepared, operator_context, operator_state = ready_workspace(tmp_path)
    receipt = ConnectivityOperatorReviewReceiptBuilder(ws).build(
        now=NOW + timedelta(seconds=20, milliseconds=500)
    )
    body = receipt.body
    assert receipt.order_id == operator_context.order_id
    assert receipt.client_order_id == operator_context.client_order_id
    assert receipt.attempt_id == operator_context.attempt_id
    assert body["operator_context_hash"] == operator_context.context_hash
    assert body["operator_decision_hash"] == operator_state.decision.decision_hash
    assert body["preparation_hash"] == prepared.preparation_hash
    assert body["symbol"] == "FIVE"
    assert body["side"] == "buy"
    assert body["quantity"] == "1"
    assert body["order_type"] == "limit"
    assert body["order_class"] == "bracket"
    assert body["limit_price"] == "5.01"
    assert body["take_profit_price"] == "5.12"
    assert body["stop_loss_price"] == "4.95"
    assert body["notional"] == "5.01"
    assert body["flat_position_count"] == 0
    assert body["flat_open_order_count"] == 0
    assert body["final_freshness_reacquisition_required"] is True
    assert body["max_external_post_attempts"] == 1
    assert body["human_execution_intent_recorded"] is False
    assert body["external_post_authorized"] is False
    assert body["capital_authority"] == "NONE"
    assert body["live_trading"] == "BLOCKED"
    assert load_operator_review_receipt(ws) == receipt
    raw = json.loads((ws.root / "connectivity_operator_review_receipt.json").read_text())
    assert raw["receipt_hash"] == receipt.receipt_hash


def test_review_receipt_is_idempotent_before_second_intent(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    builder = ConnectivityOperatorReviewReceiptBuilder(ws)
    first = builder.build(now=NOW + timedelta(seconds=20, milliseconds=500))
    second = builder.build(now=NOW + timedelta(seconds=20, milliseconds=500))
    assert second == first


def test_review_receipt_rejects_after_second_intent_exists(tmp_path) -> None:
    ws, _, _, _, receipt, _, _, _ = reviewed_workspace(tmp_path)
    assert receipt.receipt_hash
    with pytest.raises(ConnectivityOperatorReviewRejected, match="must be frozen before"):
        ConnectivityOperatorReviewReceiptBuilder(ws).build(
            now=NOW + timedelta(seconds=22)
        )


def test_review_receipt_detects_reviewed_evidence_tamper(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    ConnectivityOperatorReviewReceiptBuilder(ws).build(
        now=NOW + timedelta(seconds=20, milliseconds=500)
    )
    path = ws.root / "market_snapshot.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["ask"] = "5.02"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="reviewed evidence changed"):
        load_operator_review_receipt(ws)


def test_reviewed_second_intent_challenge_binds_receipt_and_decision(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    receipt = ConnectivityOperatorReviewReceiptBuilder(ws).build(
        now=NOW + timedelta(seconds=20, milliseconds=500)
    )
    bridge = ConnectivityReviewedExecutionIntentBridge(ws)
    context, observed = bridge.prepare(now=NOW + timedelta(seconds=21))
    challenge = reviewed_execution_intent_challenge(context, observed)
    assert context.context_hash[:12] in challenge
    assert receipt.receipt_hash[:12] in challenge

    state, binding = bridge.issue(
        context=context,
        receipt_hash=receipt.receipt_hash,
        operator_id="operator:arendon7",
        issued_at=NOW + timedelta(seconds=21),
        expires_at=NOW + timedelta(seconds=51),
    )
    assert binding.receipt_hash == receipt.receipt_hash
    assert binding.execution_intent_context_hash == context.context_hash
    assert binding.execution_intent_decision_hash == state.decision.decision_hash
    assert binding.execution_intent_event_hash == state.event_hash
    assert binding.operator_id == "operator:arendon7"
    assert verify_execution_review_binding(ws) == binding
    artifact = json.loads(bridge.binding_path.read_text(encoding="utf-8"))
    assert artifact["operator_review_receipt_bound"] is True
    assert artifact["second_human_execution_intent_bound"] is True
    assert artifact["external_post_authorized"] is False
    assert artifact["next_action"] == "REVIEWED_BOUND_FINAL_FRESHNESS_REQUIRED"


def test_wrong_review_hash_cannot_issue_second_intent(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    ConnectivityOperatorReviewReceiptBuilder(ws).build(
        now=NOW + timedelta(seconds=20, milliseconds=500)
    )
    bridge = ConnectivityReviewedExecutionIntentBridge(ws)
    context, _ = bridge.prepare(now=NOW + timedelta(seconds=21))
    with pytest.raises(ConnectivityOperatorReviewRejected, match="receipt hash changed"):
        bridge.issue(
            context=context,
            receipt_hash="f" * 64,
            operator_id="operator:arendon7",
            issued_at=NOW + timedelta(seconds=21),
            expires_at=NOW + timedelta(seconds=51),
        )
    assert not (ws.root / "connectivity_execution_intent.sqlite3").exists()


def test_review_binding_registry_detects_tamper(tmp_path) -> None:
    ws, _, _, _, _, _, _, binding = reviewed_workspace(tmp_path)
    registry = SQLiteConnectivityExecutionReviewBindingStore(
        SQLiteRuntime(ws.root / "connectivity_execution_review_binding.sqlite3")
    )
    assert registry.get() == binding
    conn = sqlite3.connect(ws.root / "connectivity_execution_review_binding.sqlite3")
    try:
        conn.execute(
            "UPDATE connectivity_execution_review_binding SET binding_hash=? WHERE singleton=1",
            ("f" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityOperatorReviewConflict, match="durable hash mismatch"):
        registry.get()


def test_reviewed_final_freshness_binds_receipt_intent_and_fresh_authority(tmp_path, monkeypatch) -> None:
    ws, _, _, _, receipt, _, state, review_binding = reviewed_workspace(tmp_path)
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    base = ConnectivityBoundFinalFreshnessGuard(
        ws,
        final_guard=guard(ws, clock=clock_from(offset=22)),
    )
    result = ConnectivityReviewedBoundFinalFreshnessGuard(
        ws, base_guard=base
    ).acquire(credentials=CREDS)
    chain_hash = verify_reviewed_final_freshness_binding(ws, result)
    raw = json.loads(
        (ws.root / "connectivity_review_final_freshness_binding.json").read_text()
    )
    assert raw["operator_review_receipt_hash"] == receipt.receipt_hash
    assert raw["execution_review_binding_hash"] == review_binding.binding_hash
    assert raw["execution_freshness_binding_hash"] == result.binding.binding_hash
    assert raw["final_freshness_permit_hash"] == result.binding.final_freshness_permit_hash
    assert raw["reviewed_human_intent_freshness_chain_bound"] is True
    assert raw["binding_hash"] == chain_hash
    assert result.binding.execution_intent_decision_hash == state.decision.decision_hash
    assert raw["external_post_authorized"] is False


def test_missing_review_binding_blocks_before_any_final_freshness_acquisition(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    calls = {"acquire": 0}

    class FakeBaseGuard:
        def acquire(self, *, credentials):
            calls["acquire"] += 1
            raise AssertionError("must not acquire Final Freshness")

    with pytest.raises(ConnectivityOperatorReviewRejected, match="review receipt"):
        ConnectivityReviewedBoundFinalFreshnessGuard(
            ws, base_guard=FakeBaseGuard()
        ).acquire(credentials=CREDS)
    assert calls["acquire"] == 0


def test_receipt_tamper_after_second_intent_blocks_before_final_gets(tmp_path) -> None:
    ws, _, _, _, _, _, _, _ = reviewed_workspace(tmp_path)
    path = ws.root / "connectivity_operator_review_receipt.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["take_profit_price"] = "99.99"
    path.write_text(json.dumps(raw), encoding="utf-8")
    calls = {"acquire": 0}

    class FakeBaseGuard:
        def acquire(self, *, credentials):
            calls["acquire"] += 1
            raise AssertionError("must not acquire Final Freshness")

    with pytest.raises((ValueError, ConnectivityOperatorReviewConflict)):
        ConnectivityReviewedBoundFinalFreshnessGuard(
            ws, base_guard=FakeBaseGuard()
        ).acquire(credentials=CREDS)
    assert calls["acquire"] == 0


def test_review_freshness_binding_tamper_is_self_detecting(tmp_path, monkeypatch) -> None:
    ws, _, _, _, _, _, _, _ = reviewed_workspace(tmp_path)
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    result = ConnectivityReviewedBoundFinalFreshnessGuard(
        ws,
        base_guard=ConnectivityBoundFinalFreshnessGuard(
            ws, final_guard=guard(ws, clock=clock_from(offset=22))
        ),
    ).acquire(credentials=CREDS)
    path = ws.root / "connectivity_review_final_freshness_binding.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["external_post_authorized"] = True
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorReviewConflict):
        verify_reviewed_final_freshness_binding(ws, result)
