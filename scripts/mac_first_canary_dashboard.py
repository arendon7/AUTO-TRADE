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
from urllib.parse import parse_qs, urlparse
import webbrowser

from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    ATTEMPT_ID_RE,
    EXECUTION_DIR,
    FirstCanaryAttemptWorkspace,
)


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
HTML = ROOT / "web/mac_first_canary.html"
DEFAULT_WORKSPACE = Path.home() / "AUTO-TRADE-R6/workspace-001"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
MAX_BODY_BYTES = 32 * 1024
_ATTEMPT_QUERY_RE = re.compile(r"^first-canary-[0-9a-f]{32}$")


class FirstCanaryDashboardError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Separate localhost control surface for the first BTC/USD PAPER canary. "
            "Preparation, human approval and GET-only recovery are enabled; broker POST execution is not yet exposed."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def _require_runtime() -> None:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise FirstCanaryDashboardError(
            "first-canary dashboard refuses to start while broker-write environment is enabled"
        )
    required = (
        PYTHON,
        HTML,
        ROOT / "scripts/mac_crypto_first_canary_prepare.py",
        ROOT / "scripts/mac_crypto_first_canary_approval.py",
        ROOT / "scripts/mac_crypto_first_canary_reconcile.py",
    )
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise FirstCanaryDashboardError(
            "AUTO-TRADE first-canary runtime is incomplete: " + ", ".join(missing)
        )


def _workspace_value(raw: object) -> Path:
    text = str(raw or DEFAULT_WORKSPACE).strip()
    if not text or "\x00" in text or len(text) > 1024:
        raise FirstCanaryDashboardError("invalid workspace path")
    path = Path(text).expanduser()
    if path.is_symlink() or not path.is_dir():
        raise FirstCanaryDashboardError(
            "workspace not found; verify the PAPER account once in the main Control Center first"
        )
    return path.resolve()


def _attempt_id(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if not ATTEMPT_ID_RE.fullmatch(value) or not _ATTEMPT_QUERY_RE.fullmatch(value):
        raise FirstCanaryDashboardError("invalid first-canary attempt id")
    return value


def _credentials(payload: dict[str, object]) -> tuple[str, str]:
    key = str(payload.get("paper_key") or "").strip()
    secret = str(payload.get("paper_secret") or "").strip()
    if not key or not secret:
        raise FirstCanaryDashboardError("PAPER Key ID and Secret are required")
    if len(key) > 512 or len(secret) > 1024:
        raise FirstCanaryDashboardError("credential input is unexpectedly long")
    return key, secret


def _safe_env(credentials: tuple[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env[WRITE_ENV] = "DISABLED"
    env.pop(KEY_ENV, None)
    env.pop(SECRET_ENV, None)
    if credentials is not None:
        env[KEY_ENV], env[SECRET_ENV] = credentials
    return env


def _redact(text: str, credentials: tuple[str, str] | None) -> str:
    result = text
    for value in credentials or ():
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


def _run_child(
    command: list[str],
    *,
    credentials: tuple[str, str] | None,
    stdin_payload: dict[str, object] | None = None,
    timeout: int = 60,
) -> dict[str, object]:
    started = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            [str(PYTHON), *command],
            cwd=ROOT,
            env=_safe_env(credentials),
            input=(
                None
                if stdin_payload is None
                else json.dumps(stdin_payload, separators=(",", ":"), allow_nan=False)
            ),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FirstCanaryDashboardError("first-canary child operation timed out") from exc
    except OSError as exc:
        raise FirstCanaryDashboardError("first-canary child process could not start") from exc
    stdout = _redact(completed.stdout, credentials)
    stderr = _redact(completed.stderr, credentials)
    parsed = _extract_json(stdout)
    reason = ""
    if completed.returncode != 0:
        if isinstance(parsed, dict) and isinstance(parsed.get("reason"), str):
            reason = str(parsed["reason"]).strip()[:1000]
        if not reason:
            lines = [line.strip() for line in stderr.splitlines() if line.strip()]
            reason = lines[-1][:1000] if lines else f"child returncode={completed.returncode}"
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "error": reason,
        "json": parsed,
        "stdout": stdout,
        "stderr": stderr,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "real_execution_enabled": False,
        "live_trading": "BLOCKED",
        "at": started.isoformat(),
    }


def _prepare(payload: dict[str, object]) -> dict[str, object]:
    workspace = _workspace_value(payload.get("workspace"))
    attempt_id = _attempt_id(payload.get("attempt_id"))
    credentials = _credentials(payload)
    return _run_child(
        [
            "scripts/mac_crypto_first_canary_prepare.py",
            "--workspace",
            str(workspace),
            "--attempt-id",
            attempt_id,
            "--allow-paper-crypto-read",
        ],
        credentials=credentials,
        timeout=60,
    )


def _approve(payload: dict[str, object]) -> dict[str, object]:
    workspace = _workspace_value(payload.get("workspace"))
    attempt_id = _attempt_id(payload.get("attempt_id"))
    operator_id = str(payload.get("operator_id") or "").strip()
    confirmation = str(payload.get("confirmation") or "")
    if not operator_id or len(operator_id) > 128:
        raise FirstCanaryDashboardError("Operator ID is required and bounded")
    if not confirmation or len(confirmation) > 4096:
        raise FirstCanaryDashboardError("exact human confirmation is required")
    status = _attempt_status(workspace=workspace, attempt_id=attempt_id)
    context = status.get("operator_context")
    if not isinstance(context, dict):
        raise FirstCanaryDashboardError(
            "prepared operator context is missing; prepare a fresh attempt first"
        )
    return _run_child(
        [
            "scripts/mac_crypto_first_canary_approval.py",
            "--workspace",
            str(workspace),
            "--attempt-id",
            attempt_id,
        ],
        credentials=None,
        stdin_payload={
            "context": context,
            "operator_id": operator_id,
            "confirmation": confirmation,
        },
        timeout=20,
    )


def _recover(payload: dict[str, object]) -> dict[str, object]:
    workspace = _workspace_value(payload.get("workspace"))
    attempt_id = _attempt_id(payload.get("attempt_id"))
    credentials = _credentials(payload)
    return _run_child(
        [
            "scripts/mac_crypto_first_canary_reconcile.py",
            "--workspace",
            str(workspace),
            "--attempt-id",
            attempt_id,
            "--allow-paper-recovery-read",
        ],
        credentials=credentials,
        timeout=60,
    )


def _safe_document(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise FirstCanaryDashboardError(f"unsafe attempt artifact: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FirstCanaryDashboardError(f"unreadable attempt artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise FirstCanaryDashboardError(f"invalid attempt artifact root: {path.name}")
    return value


def _attempt_status(*, workspace: Path, attempt_id: str) -> dict[str, object]:
    attempt_root = workspace / EXECUTION_DIR / attempt_id
    if not attempt_root.exists():
        return {
            "attempt_id": attempt_id,
            "phase": "READY_TO_PREPARE",
            "prepared": False,
            "approved": False,
            "execution_started": False,
            "real_execution_enabled": False,
            "recovery_only": False,
            "resolved": False,
            "live_trading": "BLOCKED",
        }
    if attempt_root.is_symlink() or not attempt_root.is_dir():
        raise FirstCanaryDashboardError("unsafe first-canary attempt directory")
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=attempt_id,
    )
    preparation = _safe_document(attempt.preparation_path)
    approval = _safe_document(attempt.approval_receipt_path)
    started = _safe_document(attempt.execution_started_path)
    result = _safe_document(attempt.execution_result_path)
    failure = _safe_document(attempt.reconciliation_failure_path)
    pending = _safe_document(attempt.reconciliation_pending_path)
    final = _safe_document(attempt.reconciliation_path)
    recovery = _safe_document(attempt.recovery_resolution_path)

    if recovery is not None or final is not None:
        phase = "RESOLVED"
    elif started is not None:
        phase = "RECOVERY_ONLY"
    elif approval is not None:
        phase = "APPROVED_WAITING_FOR_CERTIFIED_EXECUTION"
    elif preparation is not None:
        phase = "APPROVAL_REQUIRED"
    else:
        phase = "READY_TO_PREPARE"

    safe_preparation = None
    operator_context = None
    operator_challenge = None
    if preparation is not None:
        operator_context = preparation.get("operator_context")
        operator_challenge = preparation.get("operator_challenge")
        safe_preparation = {
            "status": preparation.get("status"),
            "symbol": preparation.get("symbol"),
            "scope": preparation.get("scope"),
            "prepared_at": preparation.get("prepared_at"),
            "execution_deadline": preparation.get("execution_deadline"),
            "prepared_notional": preparation.get("prepared_notional"),
            "prepared_quantity": preparation.get("prepared_quantity"),
            "prepared_limit_price": preparation.get("prepared_limit_price"),
            "client_order_id": (
                preparation.get("prepared_package", {}).get("client_order_id")
                if isinstance(preparation.get("prepared_package"), dict)
                else None
            ),
            "preparation_hash": preparation.get("preparation_hash"),
        }

    return {
        "attempt_id": attempt_id,
        "phase": phase,
        "prepared": preparation is not None,
        "approved": approval is not None,
        "execution_started": started is not None,
        "real_execution_enabled": False,
        "recovery_only": started is not None,
        "resolved": recovery is not None or final is not None,
        "preparation": safe_preparation,
        "operator_context": operator_context if isinstance(operator_context, dict) else None,
        "operator_challenge": operator_challenge if isinstance(operator_challenge, str) else None,
        "approval_status": None if approval is None else approval.get("status"),
        "execution_status": None if result is None else result.get("status"),
        "reconciliation_failure_status": None if failure is None else failure.get("status"),
        "reconciliation_pending_status": None if pending is None else pending.get("status"),
        "reconciliation_final_status": None if final is None else final.get("status"),
        "recovery_status": None if recovery is None else recovery.get("status"),
        "retry_post": False,
        "credentials_persisted": False,
        "live_trading": "BLOCKED",
    }


def _meta() -> dict[str, object]:
    return {
        "default_workspace": str(DEFAULT_WORKSPACE),
        "environment": "PAPER",
        "symbol": "BTC/USD",
        "target_notional_usd": "2",
        "hard_max_notional_usd": "5",
        "steps": ["PREPARE", "APPROVE", "EXECUTE_ONCE", "RECONCILE_RECOVER"],
        "prepare_enabled": True,
        "approval_enabled": True,
        "real_execution_enabled": False,
        "recovery_enabled": True,
        "generic_control_center_write_enabled": False,
        "credentials_persisted": False,
        "retry_post": False,
        "live_trading": "BLOCKED",
        "execution_message": (
            "Real PAPER POST is intentionally disabled in this dashboard build until the separate delegate injector is certified."
        ),
    }


def _fail_closed(message: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": message,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "real_execution_enabled": False,
        "retry_post": False,
        "live_trading": "BLOCKED",
    }


class FirstCanaryHandler(BaseHTTPRequestHandler):
    server_version = "AUTO-TRADE-R6-FirstCanary"

    @property
    def canary_server(self) -> "FirstCanaryServer":
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
            page = HTML.read_text(encoding="utf-8").replace(
                "__CSRF_TOKEN__", self.canary_server.csrf_token
            )
            self._write_body(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                page.encode("utf-8"),
            )
            return
        if parsed.path == "/api/meta":
            self._json(HTTPStatus.OK, {"ok": True, "meta": _meta()})
            return
        if parsed.path == "/api/status":
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                workspace = _workspace_value(query.get("workspace", [str(DEFAULT_WORKSPACE)])[0])
                attempt_id = _attempt_id(query.get("attempt_id", [""])[0])
                value = _attempt_status(workspace=workspace, attempt_id=attempt_id)
            except Exception as exc:
                self._json(HTTPStatus.BAD_REQUEST, _fail_closed(str(exc)))
                return
            self._json(HTTPStatus.OK, {"ok": True, "status": value})
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed_path = urlparse(self.path).path
        if parsed_path not in {"/api/prepare", "/api/approve", "/api/recover"}:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        expected_origin = f"http://127.0.0.1:{self.canary_server.server_port}"
        if self.headers.get("Origin") not in (None, expected_origin):
            self._json(HTTPStatus.FORBIDDEN, _fail_closed("origin rejected"))
            return
        if self.headers.get("X-CSRF-Token") != self.canary_server.csrf_token:
            self._json(HTTPStatus.FORBIDDEN, _fail_closed("csrf rejected"))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                raise FirstCanaryDashboardError("invalid request size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise FirstCanaryDashboardError("request must be a JSON object")
            if parsed_path == "/api/prepare":
                result = _prepare(payload)
            elif parsed_path == "/api/approve":
                result = _approve(payload)
            else:
                result = _recover(payload)
        except (FirstCanaryDashboardError, OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, _fail_closed(str(exc)))
            return
        except Exception as exc:
            diagnostic_id = secrets.token_hex(8)
            print(
                f"AUTO-TRADE first-canary dashboard diagnostic {diagnostic_id}: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
            value = _fail_closed(
                f"local first-canary service failed closed [{diagnostic_id}]"
            )
            value["diagnostic_id"] = diagnostic_id
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, value)
            return
        self._json(HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT, result)


class FirstCanaryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        self.csrf_token = csrf_token
        super().__init__(address, FirstCanaryHandler)


def _start_server(host: str, port: int) -> FirstCanaryServer:
    if host != "127.0.0.1":
        raise FirstCanaryDashboardError(
            "first-canary dashboard may bind only to 127.0.0.1"
        )
    if not 0 <= port <= 65535:
        raise FirstCanaryDashboardError("invalid port")
    return FirstCanaryServer((host, port), secrets.token_urlsafe(32))


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
    print(f"AUTO-TRADE Primer Canary PAPER: {url}")
    print(
        "BTC/USD PAPER only. Prepare + new human approval + GET-only recovery enabled. "
        "Real broker POST: NOT YET EXPOSED. Generic Control Center: WRITE DISABLED. LIVE: BLOCKED."
    )
    if not args.no_browser and not _open_browser(url):
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
