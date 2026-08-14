from __future__ import annotations

import http.client
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import threading


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "scripts/mac_crypto_dashboard.py"
SPEC = importlib.util.spec_from_file_location("mac_crypto_dashboard_http_under_test", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
dashboard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dashboard
SPEC.loader.exec_module(dashboard)


def _payload(workspace: Path) -> dict[str, str]:
    return {
        "workspace": str(workspace),
        "symbol": "BTC/USD",
        "paper_key": "paper-key",
        "paper_secret": "paper-secret",
    }


def test_preview_http_unexpected_exception_returns_complete_fail_closed_json(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def explode(_payload):
        raise RuntimeError("synthetic localhost preview failure")

    monkeypatch.setattr(dashboard, "_run_canary_preview", explode)
    server = dashboard._start_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    port = server.server_port
    body = json.dumps(_payload(workspace)).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/canary-preview",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Origin": f"http://127.0.0.1:{port}",
                "X-CSRF-Token": server.csrf_token,
            },
        )
        response = connection.getresponse()
        raw = response.read()
        assert response.status == 500
        assert response.getheader("Content-Type") == "application/json; charset=utf-8"
        assert int(response.getheader("Content-Length")) == len(raw)
        assert response.getheader("Connection") == "close"
        decoded = json.loads(raw.decode("utf-8"))
        assert decoded["ok"] is False
        assert decoded["error_type"] == "RuntimeError"
        assert "local qualification service failed closed" in decoded["error"]
        assert len(decoded["diagnostic_id"]) == 16
        assert decoded["broker_write_performed"] is False
        assert decoded["external_post_authorized"] is False
        assert decoded["operator_approval_authority"] == "NONE"
        assert decoded["capital_authority"] == "NONE"
        assert decoded["live_trading"] == "BLOCKED"
        assert "paper-key" not in raw.decode("utf-8")
        assert "paper-secret" not in raw.decode("utf-8")
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_preview_child_nonzero_without_json_gets_structured_fallback_reason(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr(
        dashboard.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="preview child failed without JSON\n",
        ),
    )
    result = dashboard._run_canary_preview(_payload(workspace))
    assert result["ok"] is False
    assert result["error"] == "preview child failed without JSON"
    assert result["json"] is None
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["operator_approval_authority"] == "NONE"
    assert result["capital_authority"] == "NONE"
    assert result["live_trading"] == "BLOCKED"
