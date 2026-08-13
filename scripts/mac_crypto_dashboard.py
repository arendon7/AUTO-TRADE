from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib.parse import urlparse
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
HTML = ROOT / "web/mac_crypto_dashboard.html"
DEFAULT_WORKSPACE = Path.home() / "AUTO-TRADE-R6/workspace-001"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
MAX_BODY_BYTES = 32 * 1024


class CryptoDashboardError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local BTC/USD PAPER rehearsal dashboard; no broker write surface.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def _require_runtime() -> None:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoDashboardError("refusing Crypto PAPER Lab while R6_EXTERNAL_PAPER_WRITE=ENABLED")
    if not PYTHON.is_file() or not HTML.is_file():
        raise CryptoDashboardError("AUTO-TRADE FULL runtime is incomplete; reinstall the current Mac package")


def _workspace(payload: dict[str, object]) -> str:
    raw = str(payload.get("workspace") or DEFAULT_WORKSPACE).strip()
    if not raw or "\x00" in raw or len(raw) > 1024:
        raise CryptoDashboardError("invalid workspace path")
    path = Path(raw).expanduser()
    if path.is_symlink() or not path.is_dir():
        raise CryptoDashboardError("workspace not found; verify the PAPER account once in the main Control Center first")
    return str(path.resolve())


def _credentials(payload: dict[str, object]) -> tuple[str, str]:
    key = str(payload.get("paper_key") or "").strip()
    secret = str(payload.get("paper_secret") or "").strip()
    if not key or not secret:
        raise CryptoDashboardError("PAPER Key ID and Secret are required")
    if len(key) > 512 or len(secret) > 1024:
        raise CryptoDashboardError("credential input is unexpectedly long")
    return key, secret


def _safe_env(credentials: tuple[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env[WRITE_ENV] = "DISABLED"
    env.pop(KEY_ENV, None)
    env.pop(SECRET_ENV, None)
    env[KEY_ENV], env[SECRET_ENV] = credentials
    return env


def _redact(text: str, credentials: tuple[str, str]) -> str:
    result = text
    for value in credentials:
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


def _run(payload: dict[str, object]) -> dict[str, object]:
    workspace = _workspace(payload)
    credentials = _credentials(payload)
    command = [
        str(PYTHON),
        "scripts/mac_crypto_paper_rehearsal.py",
        "--workspace", workspace,
        "--allow-paper-crypto-read",
    ]
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=_safe_env(credentials),
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    stdout = _redact(completed.stdout, credentials)
    stderr = _redact(completed.stderr, credentials)
    return {
        "ok": completed.returncode == 0,
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


def _meta() -> dict[str, object]:
    build = _read_key_values(ROOT / "MAC_BUILD_INFO.txt")
    return {
        "source_head": build.get("source_head", "UNKNOWN"),
        "default_workspace": str(DEFAULT_WORKSPACE),
        "symbol": "BTC/USD",
        "paper_write": "DISABLED",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "broker_order_surface": False,
    }


class CryptoHandler(BaseHTTPRequestHandler):
    server_version = "AUTO-TRADE-R6-CryptoLab"

    @property
    def crypto_server(self) -> "CryptoServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: object) -> None:
        return

    def _headers(self, status: HTTPStatus, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()

    def _json(self, status: HTTPStatus, value: dict[str, object]) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        self._headers(status, "application/json; charset=utf-8")
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = HTML.read_text(encoding="utf-8").replace("__CSRF_TOKEN__", self.crypto_server.csrf_token)
            self._headers(HTTPStatus.OK, "text/html; charset=utf-8")
            self.wfile.write(page.encode())
            return
        if parsed.path == "/api/meta":
            self._json(HTTPStatus.OK, {"ok": True, "meta": _meta()})
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/rehearsal":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        expected_origin = f"http://127.0.0.1:{self.crypto_server.server_port}"
        if self.headers.get("Origin") not in (None, expected_origin):
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "origin rejected"})
            return
        if self.headers.get("X-CSRF-Token") != self.crypto_server.csrf_token:
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "csrf rejected"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                raise CryptoDashboardError("invalid request size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise CryptoDashboardError("request must be a JSON object")
            result = _run(payload)
        except subprocess.TimeoutExpired:
            self._json(HTTPStatus.REQUEST_TIMEOUT, {"ok": False, "error": "BTC/USD rehearsal timed out"})
            return
        except (CryptoDashboardError, OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        self._json(HTTPStatus.OK, result)


class CryptoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        self.csrf_token = csrf_token
        super().__init__(address, CryptoHandler)


def _start_server(host: str, port: int) -> CryptoServer:
    if host != "127.0.0.1":
        raise CryptoDashboardError("Crypto PAPER Lab may bind only to 127.0.0.1")
    if not 0 <= port <= 65535:
        raise CryptoDashboardError("invalid port")
    return CryptoServer((host, port), secrets.token_urlsafe(32))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _require_runtime()
    server = _start_server(args.host, args.port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"AUTO-TRADE Crypto PAPER Lab: {url}")
    print("BTC/USD 24/7 read + Capital Safety + local OMS only. Broker POST: DISABLED. LIVE: BLOCKED.")
    if not args.no_browser:
        webbrowser.open(url, new=1, autoraise=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
