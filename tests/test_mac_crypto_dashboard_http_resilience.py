from __future__ import annotations

import http.client
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts/mac_dashboard.py"
SPEC = importlib.util.spec_from_file_location("primary_mac_dashboard_http_under_test", MODULE)
assert SPEC is not None and SPEC.loader is not None
dashboard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dashboard
SPEC.loader.exec_module(dashboard)


def _post(port: int, path: str, token: str, payload: dict[str, object]):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(payload)
    conn.request("POST", path, body=body, headers={
        "Content-Type": "application/json", "Content-Length": str(len(body.encode())),
        "X-CSRF-Token": token, "Origin": f"http://127.0.0.1:{port}",
    })
    response = conn.getresponse(); data = json.loads(response.read()); headers = dict(response.getheaders()); conn.close()
    return response.status, data, headers


def _get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path); response = conn.getresponse(); data = json.loads(response.read()); conn.close()
    return response.status, data


def test_primary_control_center_owns_crypto_preview_and_same_attempt_recovery(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "status":"CRYPTO_PAPER_QUALIFICATION_PREVIEW_PASS", "broker_write_performed":False,
            "external_post_authorized":False, "capital_authority":"NONE", "live_trading":"BLOCKED",
        }), stderr="")
    monkeypatch.setattr(dashboard.subprocess, "run", fake_run)
    server = dashboard._start_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        port = server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5); conn.request("GET", "/crypto")
        response = conn.getresponse(); assert response.status == 200; response.read(); conn.close()
        request_id = "a" * 32
        payload = {"workspace":str(workspace), "symbol":"BTC/USD", "paper_key":"paper-key", "paper_secret":"paper-secret", "preview_request_id":request_id}
        status, direct, headers = _post(port, "/api/canary-preview", server.csrf_token, payload)
        assert status == 200 and direct["ok"] is True
        assert headers["Content-Length"] and headers["Connection"].lower() == "close"
        status, recovered = _get(port, f"/api/canary-preview-result?request_id={request_id}")
        assert status == 200 and recovered["state"] == "COMPLETE" and recovered["result"]["ok"] is True
        assert len(calls) == 1
        status, replay, _ = _post(port, "/api/canary-preview", server.csrf_token, payload)
        assert status == 400 and "no replay" in replay["error"]
        assert len(calls) == 1
        assert recovered["broker_write_performed"] is False
        assert recovered["external_post_authorized"] is False
        assert recovered["capital_authority"] == "NONE" and recovered["live_trading"] == "BLOCKED"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
