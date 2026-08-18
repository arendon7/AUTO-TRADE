from __future__ import annotations

import http.client
import importlib.util
import json
from pathlib import Path
import sys
import threading


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "mac_dashboard_health_commissioning.py"
SPEC = importlib.util.spec_from_file_location("mac_dashboard_health_commissioning_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def test_crypto_page_adds_schema_only_commissioning_and_keeps_execution_absent() -> None:
    html = gate._crypto_page("csrf-token").decode("utf-8")
    for anchor in (
        "6 · Health R4 Commissioning · Core durable",
        "Commissionar core R4 · LOCAL ONLY / NO POST",
        "No crea Strategy Health, Portfolio Health ni bridge NORMAL",
        'fetch("/api/health-r4-commission-core"',
        "fabricated_health",
        "broker_write_performed",
        "LIVE = ",
    ):
        assert anchor in html
    for forbidden in (
        "FinalGuardedCryptoEntryTransport",
        "FinalGuardedCryptoProtectionTransport",
        "r6_execute_paper_canary.py",
        "stage_external_submission",
        "mark_entry_submission_unknown",
    ):
        assert forbidden not in html


def test_meta_exposes_schema_only_commissioning_without_execution_authority() -> None:
    meta = gate._build_meta()
    assert meta["crypto_health_r4_core_commissioning"] is True
    assert meta["crypto_health_r4_schema_only"] is True
    assert meta["crypto_health_r4_fabricated_health"] is False
    assert meta["crypto_execution_final_guard_uat"] is False
    assert meta["crypto_execution_approval_consumption"] is False
    assert meta["crypto_execution_oms_staging"] is False
    assert meta["crypto_execution_lifecycle_unknown"] is False
    assert meta["crypto_execution_broker_post"] is False


def test_loopback_commissioning_endpoint_is_local_state_only(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls = {"count": 0}

    def fake_commission(*, workspace_path, now):
        del now
        calls["count"] += 1
        assert workspace_path == workspace
        return {
            "status": "HEALTH_R4_CORE_COMMISSIONED_EVIDENCE_REQUIRED",
            "workspace": str(workspace),
            "core_database": str(workspace / "core.sqlite3"),
            "manifest": str(workspace / "health_r4_commissioning_manifest.json"),
            "manifest_hash": "a" * 64,
            "strategy_id": "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION",
            "portfolio_health_entity_id": "R6_CRYPTO_PAPER_PORTFOLIO",
            "kill_switch_active": True,
            "kill_switch_reason": "R6_HEALTH_R4_EVIDENCE_REQUIRED",
            "safety_state_version": 1,
            "health_state_rows_created": 0,
            "health_bridge_rows_created": 0,
            "fabricated_health": False,
            "readiness_blockers": ["STRATEGY_HEALTH_MISSING", "PORTFOLIO_HEALTH_MISSING"],
            "broker_network_used": False,
            "credentials_read": False,
            "local_state_write_performed": True,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "approval_consumed": False,
            "oms_submitting": False,
            "lifecycle_unknown": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }

    monkeypatch.setattr(gate, "commission_health_core", fake_commission)
    server = gate._start_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        body = json.dumps({"workspace": str(workspace)})
        conn.request(
            "POST",
            "/api/health-r4-commission-core",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": server.csrf_token,
                "Origin": f"http://127.0.0.1:{server.server_port}",
            },
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        conn.close()
        assert response.status == 200
        assert payload["ok"] is True
        assert payload["commissioning"]["fabricated_health"] is False
        assert payload["broker_write_performed"] is False
        assert payload["external_post_authorized"] is False
        assert payload["approval_consumed"] is False
        assert payload["capital_authority"] == "NONE"
        assert payload["live_trading"] == "BLOCKED"
        assert calls["count"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
