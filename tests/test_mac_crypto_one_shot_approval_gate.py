from __future__ import annotations

from datetime import timedelta
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import threading

import pytest

from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    crypto_operator_confirmation_challenge,
)
from test_r6_paper_crypto_canary_coordinator import NOW, _prepare


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/mac_dashboard_one_shot.py"
SPEC = importlib.util.spec_from_file_location("mac_dashboard_one_shot_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def _context(tmp_path: Path) -> CryptoOperatorDecisionContext:
    prepared, _lifecycle = _prepare(tmp_path / "prepare")
    return CryptoOperatorDecisionContext.from_prepared_package(
        prepared.package,
        attempt_id=f"approval-uat-{prepared.package.package_hash[:24]}",
    )


def _material(tmp_path: Path, *, context: CryptoOperatorDecisionContext | None = None) -> dict[str, object]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    context = context or _context(tmp_path)
    challenge = crypto_operator_confirmation_challenge(context)
    return {
        "state": "COMPLETE",
        "stored_at": gate.time.monotonic(),
        "workspace": str(workspace),
        "record_request_id": None,
        "result": {
            "ok": True,
            "json": {
                "status": "CRYPTO_PAPER_ONE_SHOT_APPROVAL_PREPARED",
                "operator": {
                    "approval_context": context.to_dict(),
                    "approval_challenge": challenge,
                },
            },
            "broker_write_performed": False,
            "external_post_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        },
    }


def test_crypto_page_injects_one_shot_gate_but_keeps_execution_absent() -> None:
    html = gate._crypto_page("csrf-token").decode("utf-8")
    for anchor in (
        "4 · Aprobación humana de un solo uso · UAT",
        "Preparar aprobación fresca · NO POST",
        "Registrar aprobación · NO POST",
        "UAT APPROVAL · RECORDED / NOT EXECUTABLE",
        'fetch("/api/canary-approval-prepare"',
        'fetch("/api/canary-approval-record"',
        "NO puede consumir esa aprobación",
        "LIVE · BLOCKED",
    ):
        assert anchor in html
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "r6_execute_paper_canary.py",
        "alpaca_paper_writer",
        "FinalGuardedCryptoEntryTransport",
        "stage_external_submission",
    ):
        assert forbidden not in html


def test_wrapper_meta_exposes_uat_approval_without_execution_authority() -> None:
    meta = gate._build_meta()
    assert meta["one_shot_human_approval_uat"] is True
    assert meta["one_shot_human_approval_consumption"] is False
    assert meta["one_shot_human_approval_execution"] is False
    assert meta["one_shot_human_approval_write_authority"] is False
    assert meta["external_paper_write"] == "DISABLED"
    assert meta["order_execution_from_dashboard"] is False
    assert meta["live_trading"] == "BLOCKED"


def test_exact_human_challenge_records_durable_issued_but_unconsumed_uat_approval(tmp_path) -> None:
    context = _context(tmp_path)
    material = _material(tmp_path, context=context)
    challenge = crypto_operator_confirmation_challenge(context)
    receipt = gate._record_operator_approval(
        material,
        operator_id="operator-001",
        confirmation=challenge,
        now=NOW + timedelta(seconds=3),
    )
    assert receipt["status"] == "CRYPTO_PAPER_ONE_SHOT_APPROVAL_RECORDED_UAT"
    assert receipt["decision_status"] == "ISSUED"
    assert receipt["decision_consumed"] is False
    assert receipt["uat_only"] is True
    assert receipt["reusable_for_real_execution"] is False
    assert receipt["execution_authority"] == "NONE"
    assert receipt["broker_write_performed"] is False
    assert receipt["external_post_authorized"] is False
    assert receipt["capital_authority"] == "NONE"
    assert receipt["live_trading"] == "BLOCKED"
    assert (Path(material["workspace"]) / receipt["approval_database"]).is_file()


def test_wrong_challenge_records_nothing_and_can_be_corrected(tmp_path) -> None:
    context = _context(tmp_path)
    material = _material(tmp_path, context=context)
    with pytest.raises(gate.DashboardError, match="does not exactly match"):
        gate._validate_confirmation(
            material,
            operator_id="operator-001",
            confirmation="APPROVE THE WRONG THING",
        )
    assert not (Path(material["workspace"]) / gate.APPROVAL_DB_DIR / gate.APPROVAL_DB_NAME).exists()
    gate._validate_confirmation(
        material,
        operator_id="operator-001",
        confirmation=crypto_operator_confirmation_challenge(context),
    )


def test_expiring_package_requires_fresh_preparation_before_approval(tmp_path) -> None:
    context = _context(tmp_path)
    material = _material(tmp_path, context=context)
    with pytest.raises(gate.DashboardError, match="too close to expiry"):
        gate._record_operator_approval(
            material,
            operator_id="operator-001",
            confirmation=crypto_operator_confirmation_challenge(context),
            now=context.execution_deadline - timedelta(seconds=4),
        )


def test_material_may_be_claimed_for_recording_only_once(tmp_path) -> None:
    server = gate._start_server("127.0.0.1", 0)
    try:
        approval_id = "a" * 32
        server._approval_prepares[approval_id] = _material(tmp_path)
        first = server.begin_approval_record("b" * 32, approval_id=approval_id)
        assert first["record_request_id"] == "b" * 32
        with pytest.raises(gate.DashboardError, match="already been claimed"):
            server.begin_approval_record("c" * 32, approval_id=approval_id)
    finally:
        server.server_close()


def test_primary_control_center_http_prepare_and_record_recover_same_attempt_without_replay(monkeypatch, tmp_path) -> None:
    context = _context(tmp_path)
    challenge = crypto_operator_confirmation_challenge(context)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prepare_calls = {"count": 0}
    record_calls = {"count": 0}

    prepared_outer = {
        "ok": True,
        "json": {
            "status": "CRYPTO_PAPER_ONE_SHOT_APPROVAL_PREPARED",
            "entry": {"payload": {"symbol": "BTC/USD"}},
            "operator": {
                "approval_context": context.to_dict(),
                "approval_challenge": challenge,
            },
        },
        "broker_write_performed": False,
        "external_post_authorized": False,
        "operator_approval_authority": "PREPARED_NOT_RECORDED",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }

    def fake_prepare(_payload):
        prepare_calls["count"] += 1
        return prepared_outer

    def fake_record(_material, *, operator_id, confirmation, now):
        del now
        record_calls["count"] += 1
        assert operator_id == "operator-001"
        assert confirmation == challenge
        return {
            "status": "CRYPTO_PAPER_ONE_SHOT_APPROVAL_RECORDED_UAT",
            "decision_status": "ISSUED",
            "operator_id": operator_id,
            "preparation_hash": context.preparation_hash,
            "decision_hash": "d" * 64,
            "event_hash": "e" * 64,
            "issued_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(seconds=10)).isoformat(),
            "decision_consumed": False,
            "reusable_for_real_execution": False,
        }

    monkeypatch.setattr(gate, "_run_approval_prepare", fake_prepare)
    monkeypatch.setattr(gate, "_record_operator_approval", fake_record)
    server = gate._start_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/crypto")
        page = conn.getresponse()
        assert page.status == 200
        assert b"Aprobaci" in page.read()
        conn.close()

        approval_id = "1" * 32
        body = json.dumps(
            {
                "approval_request_id": approval_id,
                "workspace": str(workspace),
                "symbol": "BTC/USD",
                "paper_key": "secret-paper-key",
                "paper_secret": "secret-paper-secret",
            }
        )
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request(
            "POST",
            "/api/canary-approval-prepare",
            body=body,
            headers={"Content-Type": "application/json", "X-CSRF-Token": server.csrf_token},
        )
        response = conn.getresponse()
        assert response.status == 200
        direct = json.loads(response.read())
        assert direct["ok"] is True
        conn.close()
        assert prepare_calls["count"] == 1

        cached = server.approval_prepare_status(approval_id)
        serialized = json.dumps(cached, sort_keys=True)
        assert "secret-paper-key" not in serialized
        assert "secret-paper-secret" not in serialized

        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", f"/api/canary-approval-prepare-result?request_id={approval_id}")
        recovered_response = conn.getresponse()
        assert recovered_response.status == 200
        recovered = json.loads(recovered_response.read())
        assert recovered["state"] == "COMPLETE"
        assert recovered["result"] == prepared_outer
        conn.close()
        assert prepare_calls["count"] == 1

        record_id = "2" * 32
        record_body = json.dumps(
            {
                "approval_request_id": approval_id,
                "record_request_id": record_id,
                "operator_id": "operator-001",
                "confirmation": challenge,
            }
        )
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request(
            "POST",
            "/api/canary-approval-record",
            body=record_body,
            headers={"Content-Type": "application/json", "X-CSRF-Token": server.csrf_token},
        )
        record_response = conn.getresponse()
        assert record_response.status == 200
        record_direct = json.loads(record_response.read())
        assert record_direct["receipt"]["decision_status"] == "ISSUED"
        conn.close()
        assert record_calls["count"] == 1

        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", f"/api/canary-approval-record-result?request_id={record_id}")
        record_recovery_response = conn.getresponse()
        assert record_recovery_response.status == 200
        record_recovered = json.loads(record_recovery_response.read())
        assert record_recovered["state"] == "COMPLETE"
        assert record_recovered["result"]["receipt"]["decision_status"] == "ISSUED"
        conn.close()
        assert record_calls["count"] == 1
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
