from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
HUB_HTML_PATH = ROOT / "web/mac_multi_asset.html"
HTML_PATH = ROOT / "web/mac_dashboard.html"
CRYPTO_HTML_PATH = ROOT / "web/mac_crypto_dashboard.html"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
MAX_BODY_BYTES = 64 * 1024
DEFAULT_WORKSPACE = Path.home() / "AUTO-TRADE-R6/workspace-001"
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,16}$")
CRYPTO_PAIR_RE = re.compile(r"^[A-Z0-9]{2,16}/[A-Z0-9]{2,16}$")
ACCOUNT_ID_RE = re.compile(r"^[0-9a-fA-F-]{16,64}$")
PREVIEW_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PREVIEW_RESULT_TTL_SECONDS = 120
MAX_PREVIEW_RESULTS = 16


class DashboardError(RuntimeError):
    pass


class ActionSpec(NamedTuple):
    credential_mode: str
    timeout_seconds: int = 180


SAFE_ACTIONS: dict[str, ActionSpec] = {
    "init_workspace": ActionSpec("none"),
    "doctor": ActionSpec("none"),
    "rehearsal": ActionSpec("none", 600),
    "safety_rehearsal": ActionSpec("none"),
    "readiness": ActionSpec("none"),
    "status": ActionSpec("none"),
    "account_discovery": ActionSpec("paper"),
    "account_preflight": ActionSpec("paper"),
    "asset_preflight": ActionSpec("paper"),
    "flat_account_preflight": ActionSpec("paper"),
    "market_preflight": ActionSpec("paper"),
    "build_candidate": ActionSpec("none"),
    "prepare_candidate": ActionSpec("none"),
    "review_receipt": ActionSpec("none"),
    "crypto_rehearsal": ActionSpec("paper", 60),
    "crypto_preview": ActionSpec("paper", 60),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Local native multi-asset Control Center for AUTO-TRADE R6 safe/PAPER-read operations. "
            "No staging, Final Freshness, order POST or LIVE action is exposed."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def _require_safe_runtime() -> None:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise DashboardError("Refusing dashboard while R6_EXTERNAL_PAPER_WRITE=ENABLED")
    if not PYTHON.is_file():
        raise DashboardError("AUTO-TRADE runtime is not installed; run INSTALAR_AUTO_TRADE.command")
    for path in (HUB_HTML_PATH, HTML_PATH, CRYPTO_HTML_PATH):
        if not path.is_file():
            raise DashboardError(f"Missing local dashboard asset: {path.relative_to(ROOT)}")


def _workspace(payload: dict[str, object], *, allow_missing: bool = False) -> str:
    raw = str(payload.get("workspace") or "").strip() or str(DEFAULT_WORKSPACE)
    if "\x00" in raw or len(raw) > 1024:
        raise DashboardError("Invalid workspace path")
    path = Path(raw).expanduser()
    if path.is_symlink():
        raise DashboardError("Workspace may not be a symlink")
    if not allow_missing and not path.is_dir():
        raise DashboardError("Workspace does not exist yet")
    return str(path)


def _symbol(payload: dict[str, object]) -> str:
    value = str(payload.get("symbol") or "AAPL").strip().upper()
    if not SYMBOL_RE.fullmatch(value):
        raise DashboardError("Symbol must be 1-16 uppercase market characters")
    return value


def _crypto_symbol(payload: dict[str, object]) -> str:
    value = str(payload.get("symbol") or "BTC/USD").strip().upper()
    if not CRYPTO_PAIR_RE.fullmatch(value):
        raise DashboardError("Crypto pair must use strict BASE/QUOTE form")
    base, quote = value.split("/", 1)
    if base == quote:
        raise DashboardError("Crypto base and quote currency must differ")
    return value


def _preview_request_id(payload: dict[str, object]) -> str:
    value = str(payload.get("preview_request_id") or "").strip().lower()
    if not PREVIEW_ID_RE.fullmatch(value):
        raise DashboardError("invalid preview request id")
    return value


def _paper_credentials(payload: dict[str, object]) -> tuple[str, str]:
    key = str(payload.get("paper_key") or "").strip()
    secret = str(payload.get("paper_secret") or "").strip()
    if not key or not secret:
        raise DashboardError("PAPER key and secret are required for this GET-only action")
    if len(key) > 512 or len(secret) > 1024:
        raise DashboardError("Credential input is unexpectedly long")
    return key, secret


def _safe_env(*, paper_credentials: tuple[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env[WRITE_ENV] = "DISABLED"
    env.pop(KEY_ENV, None)
    env.pop(SECRET_ENV, None)
    if paper_credentials is not None:
        env[KEY_ENV], env[SECRET_ENV] = paper_credentials
    return env


def _command(action: str, payload: dict[str, object]) -> tuple[list[str], tuple[str, str] | None]:
    if action not in SAFE_ACTIONS:
        raise DashboardError("Action is not in the certified dashboard allowlist")

    if action in {"crypto_rehearsal", "crypto_preview"}:
        workspace = _workspace(payload)
        credentials = _paper_credentials(payload)
        symbol = _crypto_symbol(payload)
        if action == "crypto_preview" and symbol != "BTC/USD":
            raise DashboardError("first TD-R6-017 qualification preview is fixed to BTC/USD")
        script = (
            "scripts/mac_crypto_canary_preview.py"
            if action == "crypto_preview"
            else "scripts/mac_crypto_paper_rehearsal.py"
        )
        return [
            str(PYTHON),
            script,
            "--workspace", workspace,
            "--symbol", symbol,
            "--allow-paper-crypto-read",
        ], credentials

    base = [str(PYTHON), "scripts/mac_safe_console.py"]
    if action == "rehearsal":
        return base + ["rehearsal"], None
    if action == "safety_rehearsal":
        return base + [
            "safety-rehearsal",
            "--symbol", _symbol(payload),
            "--quantity", str(payload.get("quantity") or "0.25").strip(),
            "--limit-price", str(payload.get("limit_price") or "100").strip(),
        ], None

    workspace = _workspace(payload, allow_missing=action in {"init_workspace", "doctor"})
    if action == "init_workspace":
        return base + ["init-workspace", "--workspace", workspace], None
    if action == "doctor":
        command = base + ["doctor"]
        if Path(workspace).is_dir():
            command += ["--workspace", workspace]
        return command, None
    if action == "readiness":
        return base + ["readiness", "--workspace", workspace], None
    if action == "status":
        return base + ["pre-canary-status", "--workspace", workspace], None

    credentials = _paper_credentials(payload) if SAFE_ACTIONS[action].credential_mode == "paper" else None
    if action == "account_discovery":
        return base + [
            "account-discovery", "--workspace", workspace,
            "--allow-paper-account-discovery-read",
        ], credentials
    if action == "account_preflight":
        account_id = str(payload.get("account_id") or "").strip()
        if not ACCOUNT_ID_RE.fullmatch(account_id):
            raise DashboardError(
                "Expected Alpaca PAPER account ID must be the UUID-like internal account id, not an email or login"
            )
        return base + [
            "account-preflight", "--workspace", workspace,
            "--expected-account-id", account_id,
            "--allow-paper-account-read",
        ], credentials
    if action == "asset_preflight":
        return base + [
            "asset-preflight", "--workspace", workspace,
            "--symbol", _symbol(payload), "--allow-paper-asset-read",
        ], credentials
    if action == "flat_account_preflight":
        return base + [
            "flat-account-preflight", "--workspace", workspace,
            "--allow-paper-flat-account-read",
        ], credentials
    if action == "market_preflight":
        return base + [
            "market-preflight", "--workspace", workspace,
            "--symbol", _symbol(payload), "--allow-paper-market-read",
        ], credentials
    if action == "build_candidate":
        return base + ["build-connectivity-candidate", "--workspace", workspace], None
    if action == "prepare_candidate":
        return base + ["prepare-connectivity-candidate", "--workspace", workspace], None
    if action == "review_receipt":
        return base + ["review-receipt", "--workspace", workspace], None
    raise DashboardError("Unsupported safe action")


def _redact(text: str, secrets_to_remove: tuple[str, str] | None) -> str:
    result = text
    if secrets_to_remove:
        for value in secrets_to_remove:
            if value:
                result = result.replace(value, "[REDACTED]")
    return result


def _extract_json(text: str) -> dict[str, object] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _run_action(action: str, payload: dict[str, object]) -> dict[str, object]:
    argv, credentials = _command(action, payload)
    started = time.monotonic()
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        env=_safe_env(paper_credentials=credentials),
        text=True,
        capture_output=True,
        timeout=SAFE_ACTIONS[action].timeout_seconds,
        check=False,
    )
    stdout = _redact(completed.stdout, credentials)
    stderr = _redact(completed.stderr, credentials)
    return {
        "ok": completed.returncode == 0,
        "action": action,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "json": _extract_json(stdout),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "broker_write_performed": False,
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "at": datetime.now(timezone.utc).isoformat(),
    }


def _read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file() and not path.is_symlink():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def _build_meta() -> dict[str, object]:
    build = _read_key_values(ROOT / "MAC_BUILD_INFO.txt")
    standalone = _read_key_values(ROOT / "MAC_STANDALONE_MANIFEST.txt")
    return {
        "source_head": build.get("source_head") or standalone.get("source_head") or "UNKNOWN",
        "bundle_mode": "FULL_STANDALONE" if standalone else "SOURCE_CHECKOUT",
        "supported_architectures": standalone.get("supported_architectures") or "host",
        "embedded_python": standalone.get("python_version") or "host",
        "installed": PYTHON.is_file(),
        "default_workspace": str(DEFAULT_WORKSPACE),
        "native_multi_asset_control_center": True,
        "asset_classes": ["US_EQUITY", "CRYPTO"],
        "equity_route": "/equities",
        "crypto_route": "/crypto",
        "crypto_default_symbol": "BTC/USD",
        "crypto_pair_input": "BASE/QUOTE",
        "crypto_rehearsal_available": True,
        "qualification_preview_available": True,
        "qualification_preview_symbol": "BTC/USD",
        "qualification_preview_max_notional_usd": "5",
        "qualification_preview_target_notional_usd": "2",
        "qualification_preview_write_authority": False,
        "qualification_preview_response_recovery": "PRIMARY_CONTROL_CENTER_SAME_ATTEMPT_GET",
        "equity_execution_from_dashboard": False,
        "crypto_execution_from_dashboard": False,
        "external_paper_write": "DISABLED",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "order_execution_from_dashboard": False,
    }


def _page(path: Path, token: str) -> bytes:
    return path.read_text(encoding="utf-8").replace("__CSRF_TOKEN__", token).encode("utf-8")


def _fail_closed_preview_value(message: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": message,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "operator_approval_authority": "NONE",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AUTO-TRADE-R6-MultiAssetDashboard"

    @property
    def dashboard_server(self) -> "DashboardServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: object) -> None:
        return

    def _headers(self, status: HTTPStatus, content_type: str, content_length: int) -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()

    def _write_body(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self._headers(status, content_type, len(body))
        self.wfile.write(body)
        self.wfile.flush()

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._write_body(status, "application/json; charset=utf-8", body)

    def _require_local_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin == f"http://127.0.0.1:{self.dashboard_server.server_port}"

    def _read_payload(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise DashboardError("Invalid request length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise DashboardError("Invalid request body size")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DashboardError("Request must be JSON") from exc
        if not isinstance(payload, dict):
            raise DashboardError("Request JSON root must be an object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        pages = {
            "/": HUB_HTML_PATH,
            "/equities": HTML_PATH,
            "/crypto": CRYPTO_HTML_PATH,
        }
        if parsed.path in pages:
            self._write_body(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                _page(pages[parsed.path], self.dashboard_server.csrf_token),
            )
            return
        if parsed.path == "/api/meta":
            self._json(HTTPStatus.OK, {"ok": True, "meta": _build_meta()})
            return
        if parsed.path == "/runbook":
            runbook = ROOT / "docs/MAC_PAPER_RUNBOOK.md"
            if not runbook.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "runbook missing"})
                return
            escaped = runbook.read_text(encoding="utf-8").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            body = (
                "<!doctype html><meta charset=\"utf-8\"><title>AUTO-TRADE R6 Runbook</title>"
                "<style>body{font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;max-width:1100px;margin:40px auto;padding:0 24px;background:#08100e;color:#dbeae4}pre{white-space:pre-wrap}</style>"
                f"<pre>{escaped}</pre>"
            )
            self._write_body(HTTPStatus.OK, "text/html; charset=utf-8", body.encode("utf-8"))
            return
        if parsed.path == "/api/canary-preview-result":
            raw = parse_qs(parsed.query, keep_blank_values=True).get("request_id", [""])[0].strip().lower()
            if not PREVIEW_ID_RE.fullmatch(raw):
                self._json(HTTPStatus.BAD_REQUEST, _fail_closed_preview_value("invalid preview request id"))
                return
            record = self.dashboard_server.preview_status(raw)
            if record is None:
                value = _fail_closed_preview_value("preview request id is unknown or expired")
                value.update({"state": "UNKNOWN", "preview_request_id": raw})
                self._json(HTTPStatus.NOT_FOUND, value)
                return
            if record["state"] == "IN_PROGRESS":
                value = _fail_closed_preview_value("preview still in progress")
                value.update({"ok": True, "state": "IN_PROGRESS", "preview_request_id": raw})
                self._json(HTTPStatus.ACCEPTED, value)
                return
            value = {
                "ok": True,
                "state": "COMPLETE",
                "preview_request_id": raw,
                "result": record["result"],
                "broker_write_performed": False,
                "external_post_authorized": False,
                "operator_approval_authority": "NONE",
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            }
            self._json(HTTPStatus.OK, value)
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed_path = urlparse(self.path).path
        if parsed_path not in {"/api/action", "/api/rehearsal", "/api/canary-preview"}:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        if not self._require_local_origin():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "origin rejected"})
            return
        if self.headers.get("X-CSRF-Token") != self.dashboard_server.csrf_token:
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "csrf rejected"})
            return
        preview_request_id: str | None = None
        try:
            payload = self._read_payload()
            if parsed_path == "/api/canary-preview":
                preview_request_id = _preview_request_id(payload)
                self.dashboard_server.begin_preview(preview_request_id)
                action = "crypto_preview"
            elif parsed_path == "/api/rehearsal":
                action = "crypto_rehearsal"
            else:
                action = str(payload.get("action") or "")
            result = _run_action(action, payload)
            if preview_request_id is not None:
                self.dashboard_server.finish_preview(preview_request_id, result)
        except subprocess.TimeoutExpired:
            value = _fail_closed_preview_value("safe action timed out") if preview_request_id else {"ok": False, "error": "safe action timed out"}
            if preview_request_id is not None:
                self.dashboard_server.finish_preview(preview_request_id, value)
            self._json(HTTPStatus.REQUEST_TIMEOUT, value)
            return
        except (DashboardError, OSError, ValueError) as exc:
            value = _fail_closed_preview_value(str(exc)) if parsed_path == "/api/canary-preview" else {"ok": False, "error": str(exc)}
            if preview_request_id is not None:
                self.dashboard_server.finish_preview(preview_request_id, value)
            self._json(HTTPStatus.BAD_REQUEST, value)
            return
        except Exception as exc:
            diagnostic_id = secrets.token_hex(8)
            print(
                f"AUTO-TRADE Control Center preview diagnostic {diagnostic_id}: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
            value = _fail_closed_preview_value(f"local qualification service failed closed [{diagnostic_id}]")
            value.update({"error_type": type(exc).__name__, "diagnostic_id": diagnostic_id})
            if preview_request_id is not None:
                self.dashboard_server.finish_preview(preview_request_id, value)
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, value)
            return
        self._json(HTTPStatus.OK, result)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        self.csrf_token = csrf_token
        self._preview_lock = threading.RLock()
        self._preview_results: dict[str, dict[str, object]] = {}
        super().__init__(address, DashboardHandler)

    def _prune_preview_results_locked(self) -> None:
        cutoff = time.monotonic() - PREVIEW_RESULT_TTL_SECONDS
        expired = [
            key
            for key, value in self._preview_results.items()
            if float(value["stored_at"]) < cutoff
        ]
        for key in expired:
            self._preview_results.pop(key, None)
        if len(self._preview_results) > MAX_PREVIEW_RESULTS:
            ordered = sorted(
                self._preview_results,
                key=lambda key: float(self._preview_results[key]["stored_at"]),
            )
            for key in ordered[: len(self._preview_results) - MAX_PREVIEW_RESULTS]:
                self._preview_results.pop(key, None)

    def begin_preview(self, request_id: str) -> None:
        with self._preview_lock:
            self._prune_preview_results_locked()
            if request_id in self._preview_results:
                raise DashboardError("preview request id already exists; no replay permitted")
            self._preview_results[request_id] = {
                "state": "IN_PROGRESS",
                "stored_at": time.monotonic(),
            }

    def finish_preview(self, request_id: str, result: dict[str, object]) -> None:
        with self._preview_lock:
            if request_id not in self._preview_results:
                return
            self._preview_results[request_id] = {
                "state": "COMPLETE",
                "stored_at": time.monotonic(),
                "result": result,
            }
            self._prune_preview_results_locked()

    def preview_status(self, request_id: str) -> dict[str, object] | None:
        with self._preview_lock:
            self._prune_preview_results_locked()
            record = self._preview_results.get(request_id)
            return dict(record) if record is not None else None


def _start_server(host: str, port: int) -> DashboardServer:
    if host != "127.0.0.1":
        raise DashboardError("Dashboard may bind only to 127.0.0.1")
    token = secrets.token_urlsafe(32)
    try:
        return DashboardServer((host, port), token)
    except OSError:
        if port == 0:
            raise
        return DashboardServer((host, 0), token)


def _enable_line_buffered_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(line_buffering=True)


def _open_browser(url: str) -> bool:
    """Open localhost reliably on macOS without making GUI launch a server dependency."""
    if sys.platform == "darwin":
        try:
            completed = subprocess.run(
                ["/usr/bin/open", url],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            if completed.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        return bool(webbrowser.open(url, new=1, autoraise=True))
    except (OSError, webbrowser.Error):
        return False


def main(argv: list[str] | None = None) -> int:
    _enable_line_buffered_console()
    args = _parser().parse_args(argv)
    try:
        _require_safe_runtime()
        server = _start_server(args.host, args.port)
    except DashboardError as exc:
        print(f"AUTO-TRADE DASHBOARD BLOCKED: {exc}", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{server.server_port}/"
    print("AUTO-TRADE R6 — NATIVE MULTI-ASSET CONTROL CENTER")
    print(f"Hub: {url}")
    print(f"Equities: {url}equities")
    print(f"Crypto: {url}crypto")
    print("External PAPER write: DISABLED")
    print("LIVE trading: BLOCKED")
    print("Order execution from dashboard: UNAVAILABLE")
    print("Keep this terminal open while using the dashboard. Ctrl+C closes it.")
    if not args.no_browser:
        threading.Timer(0.35, lambda: _open_browser(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
