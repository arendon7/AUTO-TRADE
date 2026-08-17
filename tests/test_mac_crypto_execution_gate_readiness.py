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
MODULE_PATH = SCRIPTS / "mac_dashboard_execution_gate.py"
SPEC = importlib.util.spec_from_file_location("mac_dashboard_execution_gate_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def test_crypto_page_adds_health_readiness_without_execution_controls() -> None:
    html = gate._crypto_page("csrf-token").decode("utf-8")
    for anchor in (
        "5 · Execution Gate Readiness · Health R4",
        "Comprobar Health R4 · SOLO LECTURA",
        "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION",
        'fetch("/api/execution-health-readiness"',
        "approval_consumed = false",
        "oms_submitting = false",
        "lifecycle_unknown = false",
        "broker_post = false",
        "LIVE = BLOCKED",
        "nunca se fabricará un estado NORMAL",
    ):
        assert anchor in html
    for forbidden in (
        "Consumir aprobación",
        "Enviar orden",
        "FinalGuardedCryptoEntryTransport",
        "submit_once(",
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
    ):
        assert forbidden not in html


def test_meta_exposes_read_only_gate_and_no_execution_authority() -> None:
    meta = gate._build_meta()
    assert meta["crypto_execution_health_readiness"] is True
    assert meta["crypto_execution_health_readiness_read_only"] is True
    assert meta["crypto_execution_strategy_id"] == "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION"
    assert meta["crypto_execution_final_guard_uat"] is False
    assert meta["crypto_execution_approval_consumption"] is False
    assert meta["crypto_execution_oms_staging"] is False
    assert meta["crypto_execution_lifecycle_unknown"] is False
    assert meta["crypto_execution_broker_post"] is False
    assert meta["external_paper_write"] == "DISABLED"
    assert meta["live_trading"] == "BLOCKED"


def test_loopback_health_readiness_missing_core_is_structured_and_no_post(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = gate._start_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        body = json.dumps({"workspace": str(workspace)})
        conn.request(
            "POST",
            "/api/execution-health-readiness",
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
        readiness = payload["readiness"]
        assert readiness["status"] == "HEALTH_R4_EXECUTION_READINESS_BLOCKED"
        assert readiness["blockers"] == ["CORE_DB_MISSING"]
        assert readiness["credentials_read"] is False
        assert readiness["broker_network_used"] is False
        assert readiness["broker_write_performed"] is False
        assert readiness["approval_consumed"] is False
        assert readiness["oms_submitting"] is False
        assert readiness["lifecycle_unknown"] is False
        assert readiness["external_post_authorized"] is False
        assert readiness["capital_authority"] == "NONE"
        assert readiness["live_trading"] == "BLOCKED"
        assert not (workspace / "core.sqlite3").exists()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_readiness_endpoint_never_needs_paper_credentials(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = gate.inspect_health_readiness(
        workspace_path=workspace,
        now=gate.datetime.now(gate.timezone.utc),
        strategy_id=gate.EXECUTION_STRATEGY_ID,
    )
    assert result["credentials_read"] is False
    assert result["broker_network_used"] is False
    assert result["broker_write_performed"] is False
