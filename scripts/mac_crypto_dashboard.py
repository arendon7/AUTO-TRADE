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
from threading import RLock
import time
from urllib.parse import parse_qs, urlparse
import webbrowser

from autotrade.brokers.alpaca_paper_crypto_asset import CRYPTO_PAIR, normalize_crypto_pair


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
HTML = ROOT / "web/mac_crypto_dashboard.html"
DEFAULT_WORKSPACE = Path.home() / "AUTO-TRADE-R6/workspace-001"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
MAX_BODY_BYTES = 32 * 1024
PREVIEW_RESULT_TTL_SECONDS = 120
MAX_PREVIEW_RESULTS = 16
_PREVIEW_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class CryptoDashboardError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local crypto PAPER rehearsal + qualification preview dashboard; no broker write surface."
    )
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


def _symbol(payload: dict[str, object]) -> str:
    try:
        return normalize_crypto_pair(str(payload.get("symbol") or CRYPTO_PAIR))
    except (TypeError, ValueError) as exc:
        raise CryptoDashboardError(str(exc)) from exc


def _credentials(payload: dict[str, object]) -> tuple[str, str]:
    key = str(payload.get("paper_key") or "").strip()
    secret = str(payload.get("paper_secret") or "").strip()
    if not key or not secret:
        raise CryptoDashboardError("PAPER Key ID and Secret are required")
    if len(key) > 512 or len(secret) > 1024:
        raise CryptoDashboardError("credential input is unexpectedly long")
    return key, secret


def _preview_request_id(payload: dict[str, object]) -> str:
    value = str(payload.get("preview_request_id") or "").strip().lower()
    if not _PREVIEW_ID_RE.fullmatch(value):
        raise CryptoDashboardError("invalid preview request id")
    return value


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


def _stderr_reason(stderr: str, returncode: int) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if lines:
        return lines[-1][:1000]
    return f"crypto child exited with returncode={returncode} without structured reason"


def _run_child(
    payload: dict[str, object],
    *,
    script: str,
    timeout: int,
) -> dict[str, object]:
    workspace = _workspace(payload)
    symbol = _symbol(payload)
    credentials = _credentials(payload)
    command = [
        str(PYTHON),
        script,
        "--workspace", workspace,
        "--symbol", symbol,
        "--allow-paper-crypto-read",
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=_safe_env(credentials),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise
    except Exception as exc:
        raise CryptoDashboardError(
            f"crypto read-only child launch failed closed: {type(exc).__name__}"
        ) from exc
    stdout = _redact(completed.stdout, credentials)
    stderr = _redact(completed.stderr, credentials)
    parsed = _extract_json(stdout)
    structured_reason = ""
    if completed.returncode != 0:
        if isinstance(parsed, dict):
            raw_reason = parsed.get("reason")
            if isinstance(raw_reason, str) and raw_reason.strip():
                structured_reason = raw_reason.strip()[:1000]
        if not structured_reason:
            structured_reason = _stderr_reason(stderr, completed.returncode)
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "error": structured_reason,
        "json": parsed,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "broker_write_performed": False,
        "external_post_authorized": False,
        "operator_approval_authority": "NONE",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "at": datetime.now(timezone.utc).isoformat(),
    }


def _run(payload: dict[str, object]) -> dict[str, object]:
    return _run_child(payload, script="scripts/mac_crypto_paper_rehearsal.py", timeout=45)


def _run_canary_preview(payload: dict[str, object]) -> dict[str, object]:
    if _symbol(payload) != CRYPTO_PAIR:
        raise CryptoDashboardError("first TD-R6-017 qualification preview is fixed to BTC/USD")
    return _run_child(payload, script="scripts/mac_crypto_canary_preview.py", timeout=45)


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
        "symbol": CRYPTO_PAIR,
        "pair_input": "BASE/QUOTE",
        "paper_write": "DISABLED",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "broker_order_surface": False,
        "qualification_preview_available": True,
        "qualification_preview_symbol": CRYPTO_PAIR,
        "qualification_preview_max_notional_usd": "5",
        "qualification_preview_target_notional_usd": "2",
        "qualification_preview_write_authority": False,
        "qualification_preview_response_recovery": "IN_MEMORY_SAME_ATTEMPT_GET",
    }


def _fail_closed_value(message: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": message,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "operator_approval_authority": "NONE",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def _unexpected_failure(exc: Exception, payload: dict[str, object]) -> dict[str, object]:
    diagnostic_id = secrets.token_hex(8)
    credentials = (
        str(payload.get("paper_key") or ""),
        str(payload.get("paper_secret") or ""),
    )
    detail = _redact(str(exc), credentials).strip()[:1000]
    print(
        f"AUTO-TRADE Crypto Lab diagnostic {diagnostic_id}: {type(exc).__name__}: {detail}",
        file=sys.stderr,
        flush=True,
    )
    value = _fail_closed_value(f"local qualification service failed closed [{diagnostic_id}]")
    value.update({"error_type": type(exc).__name__, "diagnostic_id": diagnostic_id})
    return value


class CryptoHandler(BaseHTTPRequestHandler):
    server_version = "AUTO-TRADE-R6-CryptoLab"

    @property
    def crypto_server(self) -> "CryptoServer":
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
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()

    def _write_body(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self._headers(status, content_type, len(body))
        self.wfile.write(body)
        self.wfile.flush()

    def _json(self, status: HTTPStatus, value: dict[str, object]) -> None:
        body = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        self._write_body(status, "application/json; charset=utf-8", body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = HTML.read_text(encoding="utf-8").replace("__CSRF_TOKEN__", self.crypto_server.csrf_token)
            self._write_body(HTTPStatus.OK, "text/html; charset=utf-8", page.encode("utf-8"))
            return
        if parsed.path == "/api/meta":
            self._json(HTTPStatus.OK, {"ok": True, "meta": _meta()})
            return
        if parsed.path == "/api/canary-preview-result":
            raw = parse_qs(parsed.query, keep_blank_values=True).get("request_id", [""])[0].strip().lower()
            if not _PREVIEW_ID_RE.fullmatch(raw):
                self._json(HTTPStatus.BAD_REQUEST, _fail_closed_value("invalid preview request id"))
                return
            record = self.crypto_server.preview_status(raw)
            if record is None:
                value = _fail_closed_value("preview request id is unknown or expired")
                value.update({"state": "UNKNOWN", "preview_request_id": raw})
                self._json(HTTPStatus.NOT_FOUND, value)
                return
            if record["state"] == "IN_PROGRESS":
                self._json(
                    HTTPStatus.ACCEPTED,
                    {
                        "ok": True,
                        "state": "IN_PROGRESS",
                        "preview_request_id": raw,
                        "broker_write_performed": False,
                        "external_post_authorized": False,
                        "operator_approval_authority": "NONE",
                        "capital_authority": "NONE",
                        "live_trading": "BLOCKED",
                    },
                )
                return
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "state": "COMPLETE",
                    "preview_request_id": raw,
                    "result": record["result"],
                    "broker_write_performed": False,
                    "external_post_authorized": False,
                    "operator_approval_authority": "NONE",
                    "capital_authority": "NONE",
                    "live_trading": "BLOCKED",
                },
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed_path = urlparse(self.path).path
        if parsed_path not in {"/api/rehearsal", "/api/canary-preview"}:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        expected_origin = f"http://127.0.0.1:{self.crypto_server.server_port}"
        if self.headers.get("Origin") not in (None, expected_origin):
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "origin rejected"})
            return
        if self.headers.get("X-CSRF-Token") != self.crypto_server.csrf_token:
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "csrf rejected"})
            return
        payload: dict[str, object] = {}
        preview_request_id: str | None = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                raise CryptoDashboardError("invalid request size")
            decoded = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(decoded, dict):
                raise CryptoDashboardError("request must be a JSON object")
            payload = decoded
            if parsed_path == "/api/canary-preview":
                preview_request_id = _preview_request_id(payload)
                self.crypto_server.begin_preview(preview_request_id)
                result = _run_canary_preview(payload)
                self.crypto_server.finish_preview(preview_request_id, result)
            else:
                result = _run(payload)
        except subprocess.TimeoutExpired:
            value = _fail_closed_value("crypto read-only operation timed out")
            if preview_request_id is not None:
                self.crypto_server.finish_preview(preview_request_id, value)
            self._json(HTTPStatus.REQUEST_TIMEOUT, value)
            return
        except (CryptoDashboardError, OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            value = _fail_closed_value(str(exc))
            if preview_request_id is not None:
                self.crypto_server.finish_preview(preview_request_id, value)
            self._json(HTTPStatus.BAD_REQUEST, value)
            return
        except Exception as exc:
            value = _unexpected_failure(exc, payload)
            if preview_request_id is not None:
                self.crypto_server.finish_preview(preview_request_id, value)
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, value)
            return
        self._json(HTTPStatus.OK, result)


class CryptoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        self.csrf_token = csrf_token
        self._preview_lock = RLock()
        self._preview_results: dict[str, dict[str, object]] = {}
        super().__init__(address, CryptoHandler)

    def _prune_preview_results_locked(self) -> None:
        cutoff = time.monotonic() - PREVIEW_RESULT_TTL_SECONDS
        expired = [key for key, value in self._preview_results.items() if float(value["stored_at"]) < cutoff]
        for key in expired:
            self._preview_results.pop(key, None)
        if len(self._preview_results) > MAX_PREVIEW_RESULTS:
            ordered = sorted(self._preview_results, key=lambda key: float(self._preview_results[key]["stored_at"]))
            for key in ordered[: len(self._preview_results) - MAX_PREVIEW_RESULTS]:
                self._preview_results.pop(key, None)

    def begin_preview(self, request_id: str) -> None:
        with self._preview_lock:
            self._prune_preview_results_locked()
            if request_id in self._preview_results:
                raise CryptoDashboardError("preview request id already exists; no replay permitted")
            self._preview_results[request_id] = {
                "state": "IN_PROGRESS",
                "stored_at": time.monotonic(),
            }

    def finish_preview(self, request_id: str, result: dict[str, object]) -> None:
        with self._preview_lock:
            record = self._preview_results.get(request_id)
            if record is None:
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


def _start_server(host: str, port: int) -> CryptoServer:
    if host != "127.0.0.1":
        raise CryptoDashboardError("Crypto PAPER Lab may bind only to 127.0.0.1")
    if not 0 <= port <= 65535:
        raise CryptoDashboardError("invalid port")
    return CryptoServer((host, port), secrets.token_urlsafe(32))


def _open_browser(url: str) -> bool:
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
    args = _parser().parse_args(argv)
    _require_runtime()
    server = _start_server(args.host, args.port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"AUTO-TRADE Crypto PAPER Lab: {url}")
    print(
        "Crypto 24/7 read + ProductCapabilities + Capital Safety + local OMS + qualification preview only. "
        "Broker POST: DISABLED. OPERATOR AUTHORITY: NONE. LIVE: BLOCKED."
    )
    if not args.no_browser:
        if not _open_browser(url):
            print(f"Browser did not open automatically. Open this URL manually: {url}")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
