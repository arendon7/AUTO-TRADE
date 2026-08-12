from __future__ import annotations

from datetime import datetime, timedelta
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_operator_decision import (
    PaperOperatorDecisionContext,
    SQLitePaperOperatorDecisionRegistry,
)
from autotrade.brokers.alpaca_paper_readiness import (
    PaperOperationalReadinessInspector,
    PaperReadinessIntegrityError,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_operational_prepare import NOW, build, run_prepare
from test_r6_paper_canary_coordinator import attestation


def _mutate_order_json(workspace, order_id: str, value: str) -> None:
    conn = sqlite3.connect(workspace.core_db_path)
    try:
        conn.execute("UPDATE orders SET record_json=? WHERE order_id=?", (value, order_id))
        conn.commit()
    finally:
        conn.close()


def test_readiness_constructor_and_clock_are_strict(tmp_path) -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        PaperOperationalReadinessInspector(str(tmp_path))  # type: ignore[arg-type]

    _, workspace, _, _, _ = build(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        PaperOperationalReadinessInspector(workspace.root).inspect(
            now=datetime(2026, 8, 11, 19, 0, 0)
        )


def test_readiness_rejects_non_directory_workspace(tmp_path) -> None:
    target = tmp_path / "not-a-workspace"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(PaperReadinessIntegrityError, match="regular directory"):
        PaperOperationalReadinessInspector(target).inspect(now=NOW)


def test_readiness_rejects_account_evidence_that_does_not_keep_production_blocked(tmp_path) -> None:
    _, workspace, _, _, _ = build(tmp_path)
    workspace.write_account_attestation(attestation())
    raw = json.loads(workspace.account_attestation_path.read_text(encoding="utf-8"))
    raw["live_trading"] = "ENABLED"
    workspace.account_attestation_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(PaperReadinessIntegrityError, match="keep LIVE blocked"):
        PaperOperationalReadinessInspector(workspace.root).inspect(now=NOW)


def test_readiness_rejects_missing_and_malformed_durable_oms_record(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    prepared = run_prepare(preparer, submission, permit)
    order_id = prepared.result.package.order_id

    conn = sqlite3.connect(workspace.core_db_path)
    try:
        conn.execute("DELETE FROM orders WHERE order_id=?", (order_id,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperReadinessIntegrityError, match="OMS order is missing or duplicated"):
        PaperOperationalReadinessInspector(workspace.root).inspect(
            now=NOW + timedelta(seconds=1)
        )

    # Rebuild a clean workspace and corrupt only its serialized OMS record.
    preparer2, workspace2, _, submission2, permit2 = build(tmp_path / "second")
    prepared2 = run_prepare(preparer2, submission2, permit2)
    _mutate_order_json(workspace2, prepared2.result.package.order_id, "{")
    with pytest.raises(PaperReadinessIntegrityError, match="OMS order JSON is invalid"):
        PaperOperationalReadinessInspector(workspace2.root).inspect(
            now=NOW + timedelta(seconds=1)
        )


def test_readiness_rejects_mismatched_oms_identity_and_invalid_status_type(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    prepared = run_prepare(preparer, submission, permit)
    order_id = prepared.result.package.order_id

    conn = sqlite3.connect(workspace.core_db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT record_json FROM orders WHERE order_id=?", (order_id,)).fetchone()
        assert row is not None
        payload = json.loads(row["record_json"])
        payload["order_id"] = "different-order"
        conn.execute(
            "UPDATE orders SET record_json=? WHERE order_id=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), order_id),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperReadinessIntegrityError, match="OMS order identity mismatch"):
        PaperOperationalReadinessInspector(workspace.root).inspect(now=NOW + timedelta(seconds=1))

    preparer2, workspace2, _, submission2, permit2 = build(tmp_path / "third")
    prepared2 = run_prepare(preparer2, submission2, permit2)
    conn = sqlite3.connect(workspace2.core_db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT record_json FROM orders WHERE order_id=?",
            (prepared2.result.package.order_id,),
        ).fetchone()
        assert row is not None
        payload = json.loads(row["record_json"])
        payload["status"] = 7
        conn.execute(
            "UPDATE orders SET record_json=? WHERE order_id=?",
            (
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                prepared2.result.package.order_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperReadinessIntegrityError, match="OMS order status is invalid"):
        PaperOperationalReadinessInspector(workspace2.root).inspect(now=NOW + timedelta(seconds=1))


def test_readiness_rejects_operator_event_cardinality_corruption(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    prepared = run_prepare(preparer, submission, permit)
    context = PaperOperatorDecisionContext.from_prepared_package(prepared.result.package)
    registry = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(workspace.operator_db_path))
    registry.record_operator_approval(
        context=context,
        operator_id="operator:cardinality",
        issued_at=NOW + timedelta(milliseconds=100),
        expires_at=NOW + timedelta(seconds=4),
    )

    conn = sqlite3.connect(workspace.operator_db_path)
    try:
        rows = conn.execute(
            "SELECT MAX(sequence) FROM alpaca_paper_operator_decision_events"
        ).fetchone()
        sequence = int(rows[0]) + 1
        conn.execute(
            "INSERT INTO alpaca_paper_operator_decision_events(sequence,event_type,preparation_hash,occurred_at,payload_json,previous_event_hash,event_hash) VALUES(?,?,?,?,?,?,?)",
            (
                sequence,
                "CONSUMED",
                context.preparation_hash,
                (NOW + timedelta(seconds=1)).isoformat(),
                json.dumps({"attempt_id": context.attempt_id}),
                "0" * 64,
                "1" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO alpaca_paper_operator_decision_events(sequence,event_type,preparation_hash,occurred_at,payload_json,previous_event_hash,event_hash) VALUES(?,?,?,?,?,?,?)",
            (
                sequence + 1,
                "CONSUMED",
                context.preparation_hash,
                (NOW + timedelta(seconds=1)).isoformat(),
                json.dumps({"attempt_id": context.attempt_id}),
                "1" * 64,
                "2" * 64,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(PaperReadinessIntegrityError, match="operator decision event cardinality"):
        PaperOperationalReadinessInspector(workspace.root).inspect(now=NOW + timedelta(seconds=1))


def test_readiness_detects_expired_canary_permit_without_authorizing(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    prepared = run_prepare(preparer, submission, permit)
    context = PaperOperatorDecisionContext.from_prepared_package(prepared.result.package)
    registry = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(workspace.operator_db_path))
    registry.record_operator_approval(
        context=context,
        operator_id="operator:permit-expiry",
        issued_at=NOW + timedelta(milliseconds=100),
        expires_at=NOW + timedelta(seconds=20),
    )

    report = PaperOperationalReadinessInspector(workspace.root).inspect(
        now=NOW + timedelta(seconds=6)
    )
    assert report.permit_status == "EXPIRED"
    assert report.execution_authorized is False
    assert report.next_action == "STOP_AND_REFRESH_OR_REPREPARE_EXPIRED_OR_INCONSISTENT_AUTHORITY"


def test_readiness_rejects_unsafe_qualification_claims(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    run_prepare(preparer, submission, permit)
    conn = sqlite3.connect(workspace.submission_db_path)
    try:
        conn.execute(
            "UPDATE alpaca_paper_submission_control SET status='ACKNOWLEDGED', attempt_count=1"
        )
        conn.commit()
    finally:
        conn.close()

    workspace.qualification_report_path.write_text(
        json.dumps({"live_trading": "BLOCKED", "profitability_claim": True}),
        encoding="utf-8",
    )
    with pytest.raises(PaperReadinessIntegrityError, match="cannot claim profitability"):
        PaperOperationalReadinessInspector(workspace.root).inspect(now=NOW + timedelta(seconds=1))

    workspace.qualification_report_path.write_text(
        json.dumps({"live_trading": "ENABLED", "profitability_claim": False}),
        encoding="utf-8",
    )
    with pytest.raises(PaperReadinessIntegrityError, match="keep LIVE blocked"):
        PaperOperationalReadinessInspector(workspace.root).inspect(now=NOW + timedelta(seconds=1))
