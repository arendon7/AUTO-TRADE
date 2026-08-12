from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_canary import PaperCanaryContext
from autotrade.brokers.alpaca_paper_connectivity_candidate import PaperConnectivityCandidateBuilder
from autotrade.brokers.alpaca_paper_connectivity_gate import (
    CERTIFIED_TRACKS,
    ConnectivityCanaryGate,
    PaperCanaryGateRejected,
)
from autotrade.brokers.alpaca_paper_connectivity_prepare import PaperConnectivityPreparationBridge
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionBinding,
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.connectivity_canary_authority import SQLiteConnectivityCanaryAuthorityStore
from autotrade.connectivity_preparation_binding import (
    ConnectivityPreparationBinding,
    ConnectivityPreparationBindingConflict,
    SQLiteConnectivityPreparationBindingStore,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime
from test_r6_connectivity_candidate import NOW, account, h, workspace


def _gate_fixture(tmp_path):
    ws = workspace(tmp_path)
    built = PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    runtime = SQLiteRuntime(ws.core_db_path)
    order = SQLiteOrderStore(runtime).get_by_order_id(built.order_id)
    assert order is not None
    authority = SQLiteConnectivityCanaryAuthorityStore(runtime).get_for_order(order.order_id)
    assert authority is not None
    current_account = account()
    binding = PaperSubmissionBinding.from_order(
        order=order,
        account_attestation_fingerprint=current_account.fingerprint,
        order_payload_hash=h("connectivity-gate-payload"),
        created_at=order.created_at,
    )
    submission = SQLitePaperSubmissionRegistry(
        SQLiteRuntime(tmp_path / "gate-submission.sqlite3")
    ).prepare(binding)
    context = PaperCanaryContext(
        order=order,
        binding=binding,
        submission_state=submission,
        account_attestation=current_account,
        now=NOW + timedelta(seconds=1),
        certified_tracks=CERTIFIED_TRACKS,
        reconciliation_clean=True,
        unresolved_unknown_orders=0,
        kill_switch_engaged=False,
        health_allows_new_exposure=False,
        prior_canary_submissions=0,
    )
    return ConnectivityCanaryGate(authority), context


def _reject(gate, context, text: str) -> None:
    with pytest.raises(PaperCanaryGateRejected, match=text):
        gate.approve(context)


def test_connectivity_gate_rejects_non_context() -> None:
    with pytest.raises(TypeError, match="ConnectivityCanaryAuthority"):
        ConnectivityCanaryGate(object())  # type: ignore[arg-type]


def test_connectivity_gate_requires_health_false(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    _reject(gate, replace(ctx, health_allows_new_exposure=True), "Strategy Health")


def test_connectivity_gate_rejects_expired_authority(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    _reject(gate, replace(ctx, now=NOW + timedelta(seconds=16)), "expired")


def test_connectivity_gate_requires_validated_order(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    _reject(gate, replace(ctx, order=replace(ctx.order, status=OrderStatus.SUBMITTING)), "VALIDATED")


def test_connectivity_gate_requires_reserved_strategy(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    bad_intent = replace(ctx.order.intent, strategy_id="not-connectivity")
    _reject(gate, replace(ctx, order=replace(ctx.order, intent=bad_intent)), "strategy_id")


def test_connectivity_gate_rejects_order_identity_drift(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    _reject(gate, replace(ctx, order=replace(ctx.order, order_id="different-order")), "order mismatch")


def test_connectivity_gate_rejects_intent_fingerprint_drift(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    bad_intent = replace(ctx.order.intent, idempotency_key="different-idempotency")
    _reject(gate, replace(ctx, order=replace(ctx.order, intent=bad_intent)), "intent fingerprint")


def test_connectivity_gate_rejects_risk_decision_drift(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    _reject(gate, replace(ctx, order=replace(ctx.order, risk_decision_id="different-risk")), "RiskDecision")


def test_connectivity_gate_rejects_submission_binding_order_drift(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    bad = replace(ctx.binding, order_id="different-order")
    _reject(gate, replace(ctx, binding=bad), "binding/order")


def test_connectivity_gate_rejects_submission_binding_risk_drift(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    bad = replace(ctx.binding, risk_decision_id="different-risk")
    _reject(gate, replace(ctx, binding=bad), "binding/RiskDecision")


def test_connectivity_gate_rejects_submission_state_binding_drift(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    bad = replace(ctx.submission_state, binding_hash="0" * 64)
    _reject(gate, replace(ctx, submission_state=bad), "state/binding")


def test_connectivity_gate_requires_fresh_prepared_submission(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    bad = replace(ctx.submission_state, status=PaperSubmissionStatus.UNKNOWN, attempt_count=1)
    _reject(gate, replace(ctx, submission_state=bad), "fresh PREPARED")


def test_connectivity_gate_requires_exact_tracks(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    _reject(gate, replace(ctx, certified_tracks=("R0", "R1")), "R0-R5")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"reconciliation_clean": False}, "clean reconciliation"),
        ({"unresolved_unknown_orders": 1}, "no UNKNOWN"),
        ({"kill_switch_engaged": True}, "kill switch"),
        ({"prior_canary_submissions": 1}, "no prior"),
    ],
)
def test_connectivity_gate_requires_clean_global_state(tmp_path, changes, message) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    _reject(gate, replace(ctx, **changes), message)


def test_connectivity_gate_requires_exact_paper_endpoint(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    bad_account = replace(ctx.account_attestation, source_host="api.alpaca.markets")
    _reject(gate, replace(ctx, account_attestation=bad_account), "exact PAPER")


def test_connectivity_gate_requires_active_usd_account(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    bad_account = replace(ctx.account_attestation, status="INACTIVE")
    _reject(gate, replace(ctx, account_attestation=bad_account), "ACTIVE USD")


def test_connectivity_gate_rejects_account_fingerprint_drift(tmp_path) -> None:
    gate, ctx = _gate_fixture(tmp_path)
    bad_account = replace(ctx.account_attestation, buying_power=ctx.account_attestation.buying_power - 1)
    _reject(gate, replace(ctx, account_attestation=bad_account), "authority/account")


def _prepared_binding(tmp_path):
    ws = workspace(tmp_path)
    PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    prepared = PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=1))
    store = SQLiteConnectivityPreparationBindingStore(SQLiteRuntime(ws.core_db_path))
    binding = store.get_for_order(prepared.order_id)
    assert binding is not None
    return ws, store, binding


def test_connectivity_binding_store_rejects_wrong_type(tmp_path) -> None:
    ws = workspace(tmp_path)
    PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    store = SQLiteConnectivityPreparationBindingStore(SQLiteRuntime(ws.core_db_path))
    with pytest.raises(TypeError, match="ConnectivityPreparationBinding"):
        store.record(object())  # type: ignore[arg-type]


def test_connectivity_binding_lookup_missing_returns_none(tmp_path) -> None:
    ws = workspace(tmp_path)
    PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    store = SQLiteConnectivityPreparationBindingStore(SQLiteRuntime(ws.core_db_path))
    assert store.get_for_order("missing-order") is None


def test_connectivity_binding_record_is_idempotent(tmp_path) -> None:
    _, store, binding = _prepared_binding(tmp_path)
    assert store.record(binding) == binding


def test_connectivity_binding_rejects_different_existing_binding(tmp_path) -> None:
    _, store, binding = _prepared_binding(tmp_path)
    other = ConnectivityPreparationBinding.create(
        order_id=binding.order_id,
        connectivity_authority_id=binding.connectivity_authority_id,
        connectivity_authority_hash=binding.connectivity_authority_hash,
        candidate_hash=h("different-candidate"),
        standard_package_hash=binding.standard_package_hash,
        canary_approval_hash=binding.canary_approval_hash,
        permit_event_hash=binding.permit_event_hash,
        submission_binding_hash=binding.submission_binding_hash,
        bracket_payload_hash=binding.bracket_payload_hash,
        instrument_master_fingerprint=binding.instrument_master_fingerprint,
        prepared_at=binding.prepared_at,
    )
    with pytest.raises(ConnectivityPreparationBindingConflict, match="different"):
        store.record(other)


def test_connectivity_binding_detects_row_hash_tamper(tmp_path) -> None:
    ws, store, binding = _prepared_binding(tmp_path)
    conn = sqlite3.connect(ws.core_db_path)
    try:
        conn.execute(
            "UPDATE connectivity_preparation_binding SET binding_hash=? WHERE order_id=?",
            ("0" * 64, binding.order_id),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityPreparationBindingConflict, match="row hash mismatch"):
        store.get_for_order(binding.order_id)


def test_connectivity_binding_detects_missing_ledger_event(tmp_path) -> None:
    ws, store, binding = _prepared_binding(tmp_path)
    conn = sqlite3.connect(ws.core_db_path)
    try:
        conn.execute(
            "DELETE FROM ledger_events WHERE event_id=?",
            (f"connectivity-prepared:{binding.binding_id}",),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityPreparationBindingConflict, match="missing or duplicated"):
        store.get_for_order(binding.order_id)


def test_connectivity_binding_detects_unsafe_payload_field(tmp_path) -> None:
    ws, store, binding = _prepared_binding(tmp_path)
    conn = sqlite3.connect(ws.core_db_path)
    try:
        row = conn.execute(
            "SELECT payload_json FROM connectivity_preparation_binding WHERE order_id=?",
            (binding.order_id,),
        ).fetchone()
        payload = json.loads(row[0])
        payload["external_post_authorized"] = True
        conn.execute(
            "UPDATE connectivity_preparation_binding SET payload_json=? WHERE order_id=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), binding.order_id),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityPreparationBindingConflict, match="external_post_authorized"):
        store.get_for_order(binding.order_id)
