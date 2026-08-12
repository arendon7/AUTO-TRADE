from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

import autotrade.connectivity_execution_freshness_binding as cefb
import autotrade.connectivity_operator_review as review
from autotrade.connectivity_execution_freshness_binding import ConnectivityBoundFinalFreshnessGuard
from autotrade.connectivity_operator_review import (
    ConnectivityExecutionReviewBinding,
    ConnectivityOperatorReviewConflict,
    ConnectivityOperatorReviewReceipt,
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
from test_r6_connectivity_operator_review import reviewed_workspace


def _binding_with(binding: ConnectivityExecutionReviewBinding, **changes) -> ConnectivityExecutionReviewBinding:
    values = {
        "order_id": binding.order_id,
        "client_order_id": binding.client_order_id,
        "attempt_id": binding.attempt_id,
        "receipt_hash": binding.receipt_hash,
        "receipt_artifact_sha256": binding.receipt_artifact_sha256,
        "execution_intent_context_hash": binding.execution_intent_context_hash,
        "execution_intent_decision_hash": binding.execution_intent_decision_hash,
        "execution_intent_event_hash": binding.execution_intent_event_hash,
        "execution_intent_artifact_sha256": binding.execution_intent_artifact_sha256,
        "operator_id": binding.operator_id,
        "bound_at": binding.bound_at,
    }
    values.update(changes)
    body = review._review_binding_body_from_values(values)
    return ConnectivityExecutionReviewBinding(
        **values,
        binding_hash=review._hash(body),
    )


def _receipt_with(receipt: ConnectivityOperatorReviewReceipt, **changes) -> ConnectivityOperatorReviewReceipt:
    body = dict(receipt.body)
    body.update(changes)
    return ConnectivityOperatorReviewReceipt(body=body, receipt_hash=review._hash(body))


def _full_reviewed_freshness(tmp_path, monkeypatch):
    ws, _, _, _, receipt, _, state, review_binding = reviewed_workspace(tmp_path)
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    base = ConnectivityBoundFinalFreshnessGuard(
        ws,
        final_guard=guard(ws, clock=clock_from(offset=22)),
    )
    result = ConnectivityReviewedBoundFinalFreshnessGuard(
        ws, base_guard=base
    ).acquire(credentials=CREDS)
    return ws, receipt, state, review_binding, result


def test_receipt_and_binding_dataclasses_fail_closed_on_invalid_construction(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    valid = ConnectivityOperatorReviewReceiptBuilder(ws).build(
        now=NOW + timedelta(seconds=20, milliseconds=500)
    )
    with pytest.raises(TypeError, match="body must be mapping"):
        ConnectivityOperatorReviewReceipt(body=[], receipt_hash="a" * 64)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="receipt hash mismatch"):
        ConnectivityOperatorReviewReceipt(body=valid.body, receipt_hash="f" * 64)

    _, _, _, _, _, _, _, binding = reviewed_workspace(tmp_path / "binding")
    assert binding.binding_hash
    with pytest.raises(ValueError, match="canonical identifier"):
        _binding_with(binding, operator_id="bad operator id")
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        _binding_with(binding, receipt_hash="not-a-hash")
    with pytest.raises(ValueError, match="timezone-aware"):
        _binding_with(binding, bound_at=binding.bound_at.replace(tzinfo=None))
    with pytest.raises(ValueError, match="binding hash mismatch"):
        replace(binding, binding_hash="f" * 64)


def test_review_binding_store_empty_type_idempotent_and_conflict_paths(tmp_path) -> None:
    empty = SQLiteConnectivityExecutionReviewBindingStore(
        SQLiteRuntime(tmp_path / "empty.sqlite3")
    )
    with pytest.raises(ConnectivityOperatorReviewRejected, match="exactly one"):
        empty.get()
    with pytest.raises(TypeError, match="ConnectivityExecutionReviewBinding"):
        empty.record(object())  # type: ignore[arg-type]

    ws, _, _, _, _, _, _, binding = reviewed_workspace(tmp_path / "bound")
    store = SQLiteConnectivityExecutionReviewBindingStore(
        SQLiteRuntime(ws.root / "connectivity_execution_review_binding.sqlite3")
    )
    assert store.record(binding) == binding
    other = _binding_with(binding, operator_id="operator:other")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="different review binding"):
        store.record(other)


def test_review_binding_store_detects_row_hash_mismatch_during_record(tmp_path) -> None:
    ws, _, _, _, _, _, _, binding = reviewed_workspace(tmp_path)
    path = ws.root / "connectivity_execution_review_binding.sqlite3"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE connectivity_execution_review_binding SET binding_hash=? WHERE singleton=1",
            ("f" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    store = SQLiteConnectivityExecutionReviewBindingStore(SQLiteRuntime(path))
    with pytest.raises(ConnectivityOperatorReviewConflict, match="row hash mismatch"):
        store.record(binding)


def test_receipt_builder_type_and_expiry_guards(tmp_path, monkeypatch) -> None:
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        ConnectivityOperatorReviewReceiptBuilder(object())  # type: ignore[arg-type]
    ws, _, operator_context, state = ready_workspace(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        ConnectivityOperatorReviewReceiptBuilder(ws).build(
            now=(NOW + timedelta(seconds=20)).replace(tzinfo=None)
        )

    expired_decision = replace(
        state.decision,
        expires_at=NOW + timedelta(seconds=20, milliseconds=250),
    )
    expired_state = replace(state, decision=expired_decision)

    class FakeOperatorBridge:
        def __init__(self, workspace):
            self.workspace = workspace
        def prepare_context(self, *, now):
            return operator_context

    monkeypatch.setattr(review, "ConnectivityOperatorBridge", FakeOperatorBridge)
    monkeypatch.setattr(review, "_load_operator_state", lambda workspace: expired_state)
    with pytest.raises(ConnectivityOperatorReviewRejected, match="expired before review"):
        ConnectivityOperatorReviewReceiptBuilder(ws).build(
            now=NOW + timedelta(seconds=20, milliseconds=500)
        )


def test_receipt_builder_detects_rehashed_preparation_binding_tamper(tmp_path, monkeypatch) -> None:
    ws, _, operator_context, _ = ready_workspace(tmp_path)

    class FakeOperatorBridge:
        def __init__(self, workspace):
            self.workspace = workspace
        def prepare_context(self, *, now):
            return operator_context

    monkeypatch.setattr(review, "ConnectivityOperatorBridge", FakeOperatorBridge)
    path = ws.root / "connectivity_preparation.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["standard_prepared_package"]["order_id"] = "other-order"
    unsigned = dict(raw)
    unsigned.pop("preparation_hash", None)
    raw["preparation_hash"] = review._hash(unsigned)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(
        ConnectivityOperatorReviewConflict,
        match="operator decision/preparation binding mismatch",
    ):
        ConnectivityOperatorReviewReceiptBuilder(ws).build(
            now=NOW + timedelta(seconds=20, milliseconds=500)
        )


def test_receipt_loader_missing_symlink_and_preparation_drift(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    with pytest.raises(ConnectivityOperatorReviewRejected, match="canonical operator review receipt"):
        load_operator_review_receipt(ws)

    receipt = ConnectivityOperatorReviewReceiptBuilder(ws).build(
        now=NOW + timedelta(seconds=20, milliseconds=500)
    )
    path = ws.root / "connectivity_operator_review_receipt.json"
    copy = ws.root / "review-copy.json"
    copy.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(copy.name)
    with pytest.raises(ConnectivityOperatorReviewRejected, match="canonical operator review receipt"):
        load_operator_review_receipt(ws)

    path.unlink()
    path.write_text(json.dumps(receipt.document()), encoding="utf-8")
    prep = ws.root / "connectivity_preparation.json"
    prep_raw = json.loads(prep.read_text(encoding="utf-8"))
    prep_raw["preparation_hash"] = "f" * 64
    prep.write_text(json.dumps(prep_raw), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="preparation hash changed"):
        load_operator_review_receipt(ws)


def test_receipt_loader_detects_validly_rehashed_operator_binding_tamper(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    receipt = ConnectivityOperatorReviewReceiptBuilder(ws).build(
        now=NOW + timedelta(seconds=20, milliseconds=500)
    )
    forged = _receipt_with(receipt, operator_context_hash="f" * 64)
    (ws.root / "connectivity_operator_review_receipt.json").write_text(
        json.dumps(forged.document()), encoding="utf-8"
    )
    with pytest.raises(ConnectivityOperatorReviewConflict, match="operator context changed"):
        load_operator_review_receipt(ws)


def test_reviewed_intent_bridge_and_challenge_type_guards(tmp_path) -> None:
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        ConnectivityReviewedExecutionIntentBridge(object())  # type: ignore[arg-type]
    ws, _, _, _ = ready_workspace(tmp_path)
    receipt = ConnectivityOperatorReviewReceiptBuilder(ws).build(
        now=NOW + timedelta(seconds=20, milliseconds=500)
    )
    bridge = ConnectivityReviewedExecutionIntentBridge(ws)
    assert bridge.receipt_path.name == "connectivity_operator_review_receipt.json"
    assert bridge.binding_path.name == "connectivity_execution_review_binding.json"
    assert bridge.registry_path.name == "connectivity_execution_review_binding.sqlite3"
    context, _ = bridge.prepare(now=NOW + timedelta(seconds=21))
    with pytest.raises(TypeError, match="ConnectivityExecutionIntentContext"):
        reviewed_execution_intent_challenge(object(), receipt)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ConnectivityOperatorReviewReceipt"):
        reviewed_execution_intent_challenge(context, object())  # type: ignore[arg-type]


def test_verify_execution_review_binding_requires_canonical_artifact_and_registry(tmp_path) -> None:
    ws, _, _, _, _, _, _, _ = reviewed_workspace(tmp_path)
    artifact = ws.root / "connectivity_execution_review_binding.json"
    artifact.unlink()
    with pytest.raises(ConnectivityOperatorReviewRejected, match="binding artifact"):
        verify_execution_review_binding(ws)

    ws2, _, _, _, _, _, _, _ = reviewed_workspace(tmp_path / "db")
    db2 = ws2.root / "connectivity_execution_review_binding.sqlite3"
    db2.unlink()
    with pytest.raises(ConnectivityOperatorReviewRejected, match="binding registry"):
        verify_execution_review_binding(ws2)

    ws3, _, _, _, _, _, _, _ = reviewed_workspace(tmp_path / "symlink")
    art3 = ws3.root / "connectivity_execution_review_binding.json"
    copy = ws3.root / "binding-copy.json"
    copy.write_bytes(art3.read_bytes())
    art3.unlink()
    art3.symlink_to(copy.name)
    with pytest.raises(ConnectivityOperatorReviewRejected, match="binding artifact"):
        verify_execution_review_binding(ws3)


def test_verify_execution_review_binding_rejects_unsafe_top_level_and_registry_divergence(tmp_path) -> None:
    ws, _, _, _, _, _, _, _ = reviewed_workspace(tmp_path)
    path = ws.root / "connectivity_execution_review_binding.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["external_post_authorized"] = True
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="unsafe execution review binding field"):
        verify_execution_review_binding(ws)

    ws2, _, _, _, _, _, _, binding2 = reviewed_workspace(tmp_path / "divergence")
    path2 = ws2.root / "connectivity_execution_review_binding.json"
    raw2 = json.loads(path2.read_text(encoding="utf-8"))
    raw2["binding"] = _binding_with(binding2, operator_id="operator:other").payload()
    path2.write_text(json.dumps(raw2), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="artifact/registry mismatch"):
        verify_execution_review_binding(ws2)


def test_verify_execution_review_binding_detects_receipt_file_and_intent_artifact_drift(tmp_path) -> None:
    ws, _, _, _, _, _, _, _ = reviewed_workspace(tmp_path)
    receipt_path = ws.root / "connectivity_operator_review_receipt.json"
    receipt_path.write_text(receipt_path.read_text(encoding="utf-8") + " \n", encoding="utf-8")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="review receipt file changed"):
        verify_execution_review_binding(ws)

    ws2, _, _, _, _, _, _, _ = reviewed_workspace(tmp_path / "intent")
    intent_path = ws2.root / "connectivity_execution_intent.json"
    intent_path.write_text(intent_path.read_text(encoding="utf-8") + " \n", encoding="utf-8")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="execution intent changed"):
        verify_execution_review_binding(ws2)


def test_verify_execution_review_binding_rejects_missing_intent_state(tmp_path) -> None:
    ws, _, _, _, _, _, _, _ = reviewed_workspace(tmp_path)
    db = ws.root / "connectivity_execution_intent.sqlite3"
    db.unlink()
    with pytest.raises(ConnectivityOperatorReviewRejected, match="exactly one second execution intent"):
        verify_execution_review_binding(ws)


def test_reviewed_final_guard_type_existing_artifact_and_review_race_guards(tmp_path, monkeypatch) -> None:
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        ConnectivityReviewedBoundFinalFreshnessGuard(object())  # type: ignore[arg-type]
    ws, _, _, _, _, _, _, binding = reviewed_workspace(tmp_path)
    guard_obj = ConnectivityReviewedBoundFinalFreshnessGuard(ws, base_guard=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="AlpacaPaperCredentials"):
        guard_obj.acquire(credentials=object())  # type: ignore[arg-type]

    guard_obj.artifact_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConnectivityOperatorReviewRejected, match="never refresh in-place"):
        guard_obj.acquire(credentials=CREDS)
    guard_obj.artifact_path.unlink()

    other = _binding_with(binding, operator_id="operator:other")
    sequence = iter((binding, other))
    monkeypatch.setattr(review, "verify_execution_review_binding", lambda workspace: next(sequence))

    class FakeBase:
        def acquire(self, *, credentials):
            return object()

    with pytest.raises(ConnectivityOperatorReviewConflict, match="changed during Final Freshness"):
        ConnectivityReviewedBoundFinalFreshnessGuard(
            ws, base_guard=FakeBase()
        ).acquire(credentials=CREDS)


def test_verify_reviewed_final_freshness_type_missing_hash_and_body_tamper(tmp_path, monkeypatch) -> None:
    ws, _, _, _, result = _full_reviewed_freshness(tmp_path, monkeypatch)
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        verify_reviewed_final_freshness_binding(object(), result)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ConnectivityBoundFinalFreshnessResult"):
        verify_reviewed_final_freshness_binding(ws, object())  # type: ignore[arg-type]

    path = ws.root / "connectivity_review_final_freshness_binding.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    with pytest.raises(ConnectivityOperatorReviewRejected, match="binding artifact"):
        verify_reviewed_final_freshness_binding(ws, result)

    path.write_text(json.dumps(original), encoding="utf-8")
    raw = dict(original)
    raw["binding_hash"] = "f" * 64
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="binding hash mismatch"):
        verify_reviewed_final_freshness_binding(ws, result)

    body = dict(original)
    body.pop("binding_hash")
    body["external_post_authorized"] = True
    body["binding_hash"] = review._hash(body)
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="non-canonical or changed"):
        verify_reviewed_final_freshness_binding(ws, result)


def test_strict_review_binding_payload_and_helper_parsers_fail_closed(tmp_path) -> None:
    _, _, _, _, _, _, _, binding = reviewed_workspace(tmp_path)
    with pytest.raises(ConnectivityOperatorReviewConflict, match="payload is non-canonical"):
        review._review_binding_from_payload({"binding_hash": binding.binding_hash})

    invalid_payload = binding.payload()
    invalid_payload["operator_id"] = ""
    with pytest.raises(ConnectivityOperatorReviewConflict, match="operator_id must be non-empty string"):
        review._review_binding_from_payload(invalid_payload)

    values = {
        "order_id": binding.order_id,
        "client_order_id": binding.client_order_id,
        "attempt_id": binding.attempt_id,
        "receipt_hash": binding.receipt_hash,
        "receipt_artifact_sha256": binding.receipt_artifact_sha256,
        "execution_intent_context_hash": binding.execution_intent_context_hash,
        "execution_intent_decision_hash": binding.execution_intent_decision_hash,
        "execution_intent_event_hash": binding.execution_intent_event_hash,
        "execution_intent_artifact_sha256": binding.execution_intent_artifact_sha256,
        "operator_id": binding.operator_id,
        "bound_at": "not-datetime",
    }
    with pytest.raises(ValueError, match="bound_at must be datetime"):
        review._review_binding_body_from_values(values)
    with pytest.raises(ConnectivityOperatorReviewConflict, match="JSON is invalid"):
        review._json_object("{")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="root must be object"):
        review._json_object("[]")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="must be object"):
        review._mapping({"x": []}, "x")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="non-empty string"):
        review._required_str({"x": ""}, "x")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="non-negative integer"):
        review._required_int({"x": True}, "x")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="decimal string"):
        review._decimal(1, "x")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="invalid decimal"):
        review._decimal("not-a-decimal", "x")
    with pytest.raises(ConnectivityOperatorReviewConflict, match="finite"):
        review._decimal("NaN", "x")
    with pytest.raises(ValueError, match="datetime string"):
        review._datetime(1, "x")
    with pytest.raises(ValueError, match="timezone-aware"):
        review._datetime("2026-08-12T12:00:00", "x")


def test_receipt_body_validator_rejects_noncanonical_safety_and_geometry(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    receipt = ConnectivityOperatorReviewReceiptBuilder(ws).build(
        now=NOW + timedelta(seconds=20, milliseconds=500)
    )

    missing = dict(receipt.body)
    missing.pop("live_trading")
    with pytest.raises(ValueError, match="body is non-canonical"):
        ConnectivityOperatorReviewReceipt(body=missing, receipt_hash=review._hash(missing))

    unsafe = dict(receipt.body)
    unsafe["live_trading"] = "ALLOWED"
    with pytest.raises(ValueError, match="unsafe/non-canonical"):
        ConnectivityOperatorReviewReceipt(body=unsafe, receipt_hash=review._hash(unsafe))

    geometry = dict(receipt.body)
    geometry["take_profit_price"] = "4.00"
    with pytest.raises(ValueError, match="protection geometry"):
        ConnectivityOperatorReviewReceipt(body=geometry, receipt_hash=review._hash(geometry))

    safety = dict(receipt.body)
    safety["risk_decision_safety_state_version"] = True
    with pytest.raises(ConnectivityOperatorReviewConflict, match="non-negative integer"):
        ConnectivityOperatorReviewReceipt(body=safety, receipt_hash=review._hash(safety))

    market = dict(receipt.body)
    market["market_bid"] = 5
    with pytest.raises(ConnectivityOperatorReviewConflict, match="decimal string"):
        ConnectivityOperatorReviewReceipt(body=market, receipt_hash=review._hash(market))

    timestamp = dict(receipt.body)
    timestamp["reviewed_snapshot_at"] = "2026-08-12T12:00:00"
    with pytest.raises(ValueError, match="timezone-aware"):
        ConnectivityOperatorReviewReceipt(body=timestamp, receipt_hash=review._hash(timestamp))
