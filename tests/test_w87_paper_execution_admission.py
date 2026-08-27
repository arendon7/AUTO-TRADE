from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect

import pytest

import autotrade.paper_execution_admission as admission_module
from autotrade.paper_execution_admission import (
    PaperExecutionAdmissionBlocked,
    PaperExecutionAdmissionConflict,
    PaperExecutionAdmissionIntegrityError,
    PaperExecutionAdmissionStatus,
    SQLitePaperExecutionAdmissionRegistry,
    W87_MAX_CANARY_NOTIONAL_USD,
    W87_MIN_CANARY_NOTIONAL_USD,
    capture_paper_execution_admission,
)
from autotrade.paper_runtime_readiness_seal import (
    PaperRuntimeReadinessSealStatus,
    seal_paper_runtime_readiness_after_collection,
)
from autotrade.persistence import SQLiteEventLedger, SQLiteRuntime
from test_w86_paper_runtime_read_only_pipeline import _collect, _source_candidate
from test_w86_paper_runtime_readiness_seal import _install_postcheck, _post_proof


def _sealed(monkeypatch, *, buying_power=Decimal("1000")):
    result, _, _, _ = _collect(monkeypatch, buying_power=buying_power)
    source, candidate = _source_candidate()
    observed = result.funding_capacity.observed_at
    post = _post_proof(source, observed_at=observed)
    _install_postcheck(monkeypatch, post)
    sealed = seal_paper_runtime_readiness_after_collection(
        seal_id="w87-source-seal",
        pipeline_result=result,
        source_snapshot=source,
        candidate_identity=candidate,
        core_path="/read-only/core.sqlite3",
    )
    return sealed


def _capture(monkeypatch, *, admission_id="w87-admission", buying_power=Decimal("1000")):
    sealed = _sealed(monkeypatch, buying_power=buying_power)
    monkeypatch.setattr(admission_module, "_now_utc", lambda: sealed.seal.observed_at)
    receipt = capture_paper_execution_admission(
        admission_id=admission_id,
        sealed_result=sealed,
    )
    return sealed, receipt


def test_w87_captures_exact_usd1_to5_canary_envelope_without_execution_authority(monkeypatch):
    sealed, receipt = _capture(monkeypatch)

    assert sealed.seal.status is PaperRuntimeReadinessSealStatus.READY
    assert receipt.status is PaperExecutionAdmissionStatus.ADMITTED
    assert receipt.readiness_seal_hash == sealed.seal.receipt_hash
    assert receipt.pipeline_receipt_hash == sealed.pipeline.receipt.receipt_hash
    assert receipt.final_readiness_hash == sealed.pipeline.final_readiness.receipt_hash
    assert receipt.funding_capacity_hash == sealed.pipeline.funding_capacity.proof_hash
    assert receipt.canary_quantity == Decimal("0.010")
    assert receipt.conservative_limit_price == Decimal("101")
    assert receipt.canary_notional_usd == Decimal("1.010")
    assert W87_MIN_CANARY_NOTIONAL_USD <= receipt.canary_notional_usd <= W87_MAX_CANARY_NOTIONAL_USD
    assert receipt.canary_quantity % receipt.broker_trade_increment == 0
    assert receipt.probation_order_cap == 1
    assert receipt.order_intent_creation_permitted is True
    assert receipt.separate_risk_decision_required is True
    assert receipt.separate_human_execution_approval_required is True
    assert receipt.oms_handoff_permitted is False
    assert receipt.capital_reserved is False
    assert receipt.broker_write_performed is False
    assert receipt.paper_execution_authorized is False
    assert receipt.external_execution_authorized is False
    assert receipt.runtime_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"


def test_w87_uses_internal_clock_and_refuses_expired_w86_seal(monkeypatch):
    sealed = _sealed(monkeypatch)
    monkeypatch.setattr(
        admission_module,
        "_now_utc",
        lambda: sealed.seal.valid_until + timedelta(microseconds=1),
    )
    with pytest.raises(PaperExecutionAdmissionBlocked, match="expired"):
        capture_paper_execution_admission(
            admission_id="w87-expired",
            sealed_result=sealed,
        )


def test_w87_entrypoint_exposes_no_caller_timestamp():
    parameters = inspect.signature(capture_paper_execution_admission).parameters
    assert "now" not in parameters
    assert "observed_at" not in parameters
    assert "captured_at" not in parameters


def test_w87_rejects_blocked_upstream_pipeline(monkeypatch):
    sealed = _sealed(monkeypatch, buying_power=Decimal("0.05"))
    assert sealed.seal.status is PaperRuntimeReadinessSealStatus.BLOCKED
    monkeypatch.setattr(admission_module, "_now_utc", lambda: sealed.seal.observed_at)
    with pytest.raises(PaperExecutionAdmissionBlocked, match="not READY"):
        capture_paper_execution_admission(
            admission_id="w87-upstream-blocked",
            sealed_result=sealed,
        )


def test_w87_rejects_tampered_w86_seal_before_minting_admission(monkeypatch):
    sealed = _sealed(monkeypatch)
    object.__setattr__(sealed.seal, "receipt_hash", "0" * 64)
    monkeypatch.setattr(admission_module, "_now_utc", lambda: sealed.seal.observed_at)
    with pytest.raises(Exception, match="hash mismatch"):
        capture_paper_execution_admission(
            admission_id="w87-tampered-upstream",
            sealed_result=sealed,
        )


@pytest.mark.parametrize(
    "field,value,match",
    (
        ("paper_execution_authorized", True, "may only permit"),
        ("external_execution_authorized", True, "may only permit"),
        ("runtime_execution_authorized", True, "may only permit"),
        ("oms_handoff_permitted", True, "may only permit"),
        ("capital_reserved", True, "may only permit"),
        ("broker_write_performed", True, "may only permit"),
        ("capital_authority", "RESERVED", "may only permit"),
        ("live_trading", "ENABLED", "may only permit"),
        ("order_intent_creation_permitted", False, "may only permit"),
        ("separate_risk_decision_required", False, "may only permit"),
        ("separate_human_execution_approval_required", False, "may only permit"),
    ),
)
def test_w87_receipt_rejects_every_authority_escalation_or_boundary_weakening(
    monkeypatch, field, value, match
):
    _, receipt = _capture(monkeypatch)
    with pytest.raises(PaperExecutionAdmissionIntegrityError, match=match):
        replace(receipt, **{field: value})


def test_w87_receipt_rejects_hash_and_canary_term_tamper(monkeypatch):
    _, receipt = _capture(monkeypatch)
    with pytest.raises(PaperExecutionAdmissionIntegrityError, match="hash mismatch"):
        replace(receipt, receipt_hash="0" * 64)
    with pytest.raises(PaperExecutionAdmissionIntegrityError, match="canonical W87 quantity"):
        replace(receipt, canary_quantity=receipt.canary_quantity + receipt.broker_trade_increment)
    with pytest.raises(PaperExecutionAdmissionIntegrityError, match="notional is inconsistent"):
        replace(receipt, canary_notional_usd=receipt.canary_notional_usd + Decimal("0.01"))


def test_w87_registry_is_durable_idempotent_and_hash_chained(tmp_path, monkeypatch):
    _, receipt = _capture(monkeypatch)
    runtime = SQLiteRuntime(tmp_path / "core.sqlite3")
    registry = SQLitePaperExecutionAdmissionRegistry(runtime)

    assert registry.capture(receipt) is receipt
    assert registry.capture(receipt) is receipt
    loaded = registry.get(receipt.admission_id)

    assert loaded == receipt
    assert SQLiteEventLedger(runtime).verify_integrity() is True

    conn = runtime.connect()
    try:
        rows = conn.execute(
            "SELECT event_type,payload_json FROM ledger_events "
            "WHERE event_type='W87_PAPER_EXECUTION_ADMISSION_CAPTURED'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert '"paper_execution_authorized":false' in rows[0]["payload_json"]
    assert '"external_execution_authorized":false' in rows[0]["payload_json"]
    assert '"live_trading":"BLOCKED"' in rows[0]["payload_json"]


def test_w87_registry_rejects_second_admission_for_same_readiness_seal(tmp_path, monkeypatch):
    sealed = _sealed(monkeypatch)
    monkeypatch.setattr(admission_module, "_now_utc", lambda: sealed.seal.observed_at)
    first = capture_paper_execution_admission(
        admission_id="w87-first",
        sealed_result=sealed,
    )
    second = capture_paper_execution_admission(
        admission_id="w87-second",
        sealed_result=sealed,
    )
    runtime = SQLiteRuntime(tmp_path / "core.sqlite3")
    registry = SQLitePaperExecutionAdmissionRegistry(runtime)
    registry.capture(first)
    with pytest.raises(PaperExecutionAdmissionConflict, match="already bound"):
        registry.capture(second)


def test_w87_registry_detects_missing_ledger_half_of_atomic_contract(tmp_path, monkeypatch):
    _, receipt = _capture(monkeypatch)
    runtime = SQLiteRuntime(tmp_path / "core.sqlite3")
    registry = SQLitePaperExecutionAdmissionRegistry(runtime)
    registry.capture(receipt)
    conn = runtime.connect()
    try:
        conn.execute(
            "DELETE FROM ledger_events WHERE event_id = ?",
            (f"w87-admission:{receipt.receipt_hash}",),
        )
    finally:
        conn.close()

    with pytest.raises(PaperExecutionAdmissionConflict, match="without exact durable ledger event"):
        registry.capture(receipt)


def test_w87_receipt_round_trip_revalidates_all_invariants(tmp_path, monkeypatch):
    _, receipt = _capture(monkeypatch)
    runtime = SQLiteRuntime(tmp_path / "core.sqlite3")
    registry = SQLitePaperExecutionAdmissionRegistry(runtime)
    registry.capture(receipt)

    loaded = registry.get(receipt.admission_id)
    loaded.__post_init__()
    assert loaded.to_dict() == receipt.to_dict()
