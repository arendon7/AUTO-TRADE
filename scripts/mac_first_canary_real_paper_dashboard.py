from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

from autotrade.first_canary_external_post_consent import external_post_challenge
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    ATTEMPT_ID_RE,
    EXECUTION_DIR,
    FirstCanaryAttemptWorkspace,
)
from mac_first_canary_dashboard import (
    DEFAULT_WORKSPACE,
    KEY_ENV,
    MAX_BODY_BYTES,
    PYTHON,
    ROOT,
    SECRET_ENV,
    WRITE_ENV,
    FirstCanaryDashboardError,
    _attempt_id,
    _credentials,
    _extract_json,
    _open_browser,
    _recover,
    _redact,
    _safe_document,
    _safe_env,
    _workspace_value,
)


HTML = ROOT / "web/mac_first_canary_real_paper.html"
EXECUTE_SCRIPT = ROOT / "scripts/mac_crypto_first_canary_execute_real_paper.py"
PREPARED_EVIDENCE_FILENAME = "prepared_evidence.json"
CONSENT_FILENAME = "external_post_consent.json"


class FirstCanaryRealPaperDashboardError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Separate localhost execution-only surface for one prepared BTC/USD Alpaca PAPER technical canary. "
            "It cannot prepare arbitrary orders and LIVE remains blocked."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def _require_runtime() -> None:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise FirstCanaryRealPaperDashboardError(
            "real first-canary dashboard refuses generic R6_EXTERNAL_PAPER_WRITE=ENABLED"
        )
    for path in (PYTHON, HTML, EXECUTE_SCRIPT, ROOT / "scripts/mac_crypto_first_canary_reconcile.py"):
        if not path.is_file():
            raise FirstCanaryRealPaperDashboardError(
                f"real first-canary runtime is incomplete: missing {path.name}"
            )


def _decimal_text(raw: object, label: str) -> Decimal:
    if not isinstance(raw, str):
        raise FirstCanaryRealPaperDashboardError(f"{label} is missing")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise FirstCanaryRealPaperDashboardError(f"{label} is invalid") from exc
    if not value.is_finite():
        raise FirstCanaryRealPaperDashboardError(f"{label} must be finite")
    return value


def _timestamp(raw: object, label: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise FirstCanaryRealPaperDashboardError(f"{label} is missing")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FirstCanaryRealPaperDashboardError(f"{label} is invalid") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise FirstCanaryRealPaperDashboardError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _status(*, workspace: Path, attempt_id: str) -> dict[str, object]:
    attempt_root = workspace / EXECUTION_DIR / attempt_id
    if not attempt_root.exists():
        return {
            "attempt_id": attempt_id,
            "phase": "PREPARE_IN_SAFE_GATE_FIRST",
            "ready_for_real_post": False,
            "retry_post": False,
            "live_trading": "BLOCKED",
        }
    if attempt_root.is_symlink() or not attempt_root.is_dir():
        raise FirstCanaryRealPaperDashboardError("unsafe first-canary attempt directory")
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=attempt_id,
    )
    preparation = _safe_document(attempt.preparation_path)
    prepared_evidence = _safe_document(attempt.attempt_root / PREPARED_EVIDENCE_FILENAME)
    approval = _safe_document(attempt.approval_receipt_path)
    consent = _safe_document(attempt.attempt_root / CONSENT_FILENAME)
    started = _safe_document(attempt.execution_started_path)
    result = _safe_document(attempt.execution_result_path)
    pending = _safe_document(attempt.reconciliation_pending_path)
    final = _safe_document(attempt.reconciliation_path)
    recovery = _safe_document(attempt.recovery_resolution_path)

    challenge = None
    safe_preparation = None
    if preparation is not None:
        package = preparation.get("prepared_package")
        if not isinstance(package, dict):
            raise FirstCanaryRealPaperDashboardError("prepared package is missing")
        symbol = package.get("symbol")
        if symbol != "BTC/USD":
            raise FirstCanaryRealPaperDashboardError("real first-canary surface accepts BTC/USD only")
        notional = _decimal_text(package.get("notional"), "prepared notional")
        if not Decimal("1") <= notional <= Decimal("5"):
            raise FirstCanaryRealPaperDashboardError("prepared notional is outside USD 1-5")
        client_order_id = package.get("client_order_id")
        if not isinstance(client_order_id, str) or not client_order_id:
            raise FirstCanaryRealPaperDashboardError("prepared client_order_id is missing")
        challenge = external_post_challenge(
            attempt_id=attempt_id,
            client_order_id=client_order_id,
            notional=notional,
        )
        safe_preparation = {
            "symbol": symbol,
            "notional": format(notional, "f"),
            "quantity": package.get("quantity"),
            "limit_price": package.get("limit_price"),
            "order_type": package.get("broker_order_type"),
            "time_in_force": package.get("time_in_force"),
            "client_order_id": client_order_id,
            "execution_deadline": package.get("execution_deadline"),
            "package_hash": package.get("package_hash"),
        }

    if final is not None or recovery is not None:
        phase = "RESOLVED_NO_MORE_POST"
    elif consent is not None or started is not None:
        phase = "POST_CONSENT_OR_ATTEMPT_BURNED_RECOVERY_ONLY"
    elif preparation is None or prepared_evidence is None:
        phase = "RESTART_SAFE_PREPARATION_REQUIRED"
    elif approval is None:
        phase = "EXECUTION_APPROVAL_REQUIRED_IN_SAFE_GATE"
    else:
        phase = "READY_FOR_SECOND_EXACT_POST_CONFIRMATION"

    ready = (
        phase == "READY_FOR_SECOND_EXACT_POST_CONFIRMATION"
        and challenge is not None
    )
    return {
        "attempt_id": attempt_id,
        "phase": phase,
        "ready_for_real_post": ready,
        "preparation": safe_preparation,
        "external_post_challenge": challenge if ready else None,
        "prepared_evidence_present": prepared_evidence is not None,
        "approval_present": approval is not None,
        "external_post_consent_present": consent is not None,
        "execution_started": started is not None,
        "execution_status": None if result is None else result.get("status"),
        "reconciliation_pending_status": None if pending is None else pending.get("status"),
        "reconciliation_final_status": None if final is None else final.get("status"),
        "recovery_status": None if recovery is None else recovery.get("status"),
        "retry_post": False,
        "recovery_get_only": consent is not None or started is not None,
        "credentials_persisted": False,
        "generic_control_center_write_enabled": False,
        "live_trading": "BLOCKED",
    }


def _discover_ready_attempt(*, workspace: Path) -> dict[str, object]:
    execution_root = workspace / EXECUTION_DIR
    if not execution_root.exists():
        return {
            "selection_status": "NO_READY_ATTEMPT",
            "attempt_id": None,
            "ready_count": 0,
            "expired_count": 0,
            "invalid_count": 0,
            "auto_selected": False,
        }
    if execution_root.is_symlink() or not execution_root.is_dir():
        raise FirstCanaryRealPaperDashboardError("unsafe first-canary execution directory")

    now = datetime.now(timezone.utc)
    ready_attempts: list[str] = []
    expired_attempts: list[str] = []
    invalid_attempts: list[str] = []

    for child in sorted(execution_root.iterdir(), key=lambda value: value.name):
        if not ATTEMPT_ID_RE.fullmatch(child.name):
            continue
        if child.is_symlink() or not child.is_dir():
            invalid_attempts.append(child.name)
            continue
        try:
            value = _status(workspace=workspace, attempt_id=child.name)
            if value.get("ready_for_real_post") is not True:
                continue
            preparation = value.get("preparation")
            if not isinstance(preparation, dict):
                raise FirstCanaryRealPaperDashboardError("ready attempt is missing preparation summary")
            package_deadline = _timestamp(
                preparation.get("execution_deadline"),
                "execution deadline",
            )
            approval = _safe_document(child / "approval.json")
            if not isinstance(approval, dict):
                raise FirstCanaryRealPaperDashboardError("ready attempt is missing approval receipt")
            approval_deadline = _timestamp(approval.get("expires_at"), "approval expiry")
            if package_deadline <= now or approval_deadline <= now:
                expired_attempts.append(child.name)
                continue
            ready_attempts.append(child.name)
        except Exception:
            invalid_attempts.append(child.name)

    if len(ready_attempts) == 1:
        return {
            "selection_status": "EXACT_ONE_READY",
            "attempt_id": ready_attempts[0],
            "ready_count": 1,
            "expired_count": len(expired_attempts),
            "invalid_count": len(invalid_attempts),
            "auto_selected": True,
        }
    if len(ready_attempts) > 1:
        return {
            "selection_status": "AMBIGUOUS_MULTIPLE_READY",
            "attempt_id": None,
            "ready_count": len(ready_attempts),
            "expired_count": len(expired_attempts),
            "invalid_count": len(invalid_attempts),
            "auto_selected": False,
        }
    return {
        "selection_status": "NO_READY_ATTEMPT",
        "attempt_id": None,
        "ready_count": 0,
        "expired_count": len(expired_attempts),
        "invalid_count": len(invalid_attempts),
        "auto_selected": False,
    }


def _run_execute(payload: dict[str, object]) -> dict[str, object]:
    workspace = _workspace_value(payload.get("workspace"))
    attempt_id = _attempt_id(payload.get("attempt_id"))
    confirmation = payload.get("confirmation")
    if not isinstance(confirmation, str) or not confirmation or len(confirmation) > 4096:
        raise FirstCanaryRealPaperDashboardError("exact second POST confirmation is required")

    discovery = _discover_ready_attempt(workspace=workspace)
    if (
        discovery.get("selection_status") != "EXACT_ONE_READY"
        or discovery.get("attempt_id") != attempt_id
    ):
        raise FirstCanaryRealPaperDashboardError(
            "execution requires exactly one fresh approved unstarted attempt and the selected Attempt ID must match it"
        )

    status = _status(workspace=workspace, attempt_id=attempt_id)
    if status.get("ready_for_real_post") is not True:
        raise FirstCanaryRealPaperDashboardError(
            "attempt is not ready for one-shot real PAPER POST; do not bypass preparation/approval/recovery state"
        )
    if confirmation != status.get("external_post_challenge"):
        raise FirstCanaryRealPaperDashboardError("second POST confirmation does not match exact challenge")
    credentials = _credentials(payload)

    started_at = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            [
                str(PYTHON),
                "scripts/mac_crypto_first_canary_execute_real_paper.py",
                "--workspace",
                str(workspace),
                "--attempt-id",
                attempt_id,
                "--allow-exact-paper-post",
            ],
            cwd=ROOT,
            env=_safe_env(credentials),
            input=json.dumps({"confirmation": confirmation}, separators=(",", ":")),
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FirstCanaryRealPaperDashboardError(
            "execution process timed out; POST outcome is treated as ambiguous: never retry POST, use GET-only recovery"
        ) from exc
    except OSError as exc:
        raise FirstCanaryRealPaperDashboardError("execution process could not start") from exc

    stdout = _redact(completed.stdout, credentials)
    stderr = _redact(completed.stderr, credentials)
    parsed = _extract_json(stdout)
    reason = ""
    if completed.returncode != 0:
        if isinstance(parsed, dict) and isinstance(parsed.get("reason"), str):
            reason = str(parsed["reason"])[:1000]
        if not reason:
            lines = [line.strip() for line in stderr.splitlines() if line.strip()]
            reason = lines[-1][:1000] if lines else f"child returncode={completed.returncode}"
    broker_write = bool(isinstance(parsed, dict) and parsed.get("broker_write_performed") is True)
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "error": reason,
        "json": parsed,
        "stdout": stdout,
        "stderr": stderr,
        "broker_write_performed": broker_write,
        "external_post_authorized": True,
        "real_execution_enabled": True,
        "retry_post": False,
        "recovery_get_only_after_attempt": True,
        "credentials_persisted": False,
        "live_trading": "BLOCKED",
        "at": started_at.isoformat(),
    }


def _meta() -> dict[str, object]:
    return {
        "default_workspace": str(DEFAULT_WORKSPACE),
        "environment": "PAPER",
        "symbol": "BTC/USD",
        "surface": "SEPARATE_EXECUTION_ONLY",
        "preparation_here": False,
        "approval_here": False,
        "real_execution_enabled": True,
        "one_shot_only": True,
        "hard_max_notional_usd": "5",
        "automatic_attempt_discovery": "EXACTLY_ONE_FRESH_READY_ONLY",
        "generic_control_center_write_enabled": False,
        "credentials_persisted": False,
        "retry_post": False,
        "live_trading": "BLOCKED",
        "warning": (
            "This surface may submit exactly one already-prepared BTC/USD PAPER order after the exact second challenge. "
            "After consent or execution starts, never press execute again; use GET-only recovery."
        ),
    }


def _fail_closed(message: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": message,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "real_execution_enabled": True,
        "retry_post": False,
        "recovery_get_only": True,
        "credentials_persisted": False,
        "live_trading": "BLOCKED",
    }


class RealPaperHandler(BaseHTTPRequestHandler):
    server_version = "AUTO-TRADE-R6-FirstCanary-RealPAPER"

    @property
    def canary_server(self) -> "RealPaperServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: object) -> None:
        return

    def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()

    def _write(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self._headers(status, content_type, len(body))
        self.wfile.write(body)
        self.wfile.flush()

    def _json(self, status: HTTPStatus, value: dict[str, object]) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        self._write(status, "application/json; charset=utf-8", body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = HTML.read_text(encoding="utf-8").replace("__CSRF_TOKEN__", self.canary_server.csrf_token)
            self._write(HTTPStatus.OK, "text/html; charset=utf-8", page.encode("utf-8"))
            return
        if parsed.path == "/api/meta":
            self._json(HTTPStatus.OK, {"ok": True, "meta": _meta()})
            return
        if parsed.path == "/api/discover":
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                workspace = _workspace_value(query.get("workspace", [str(DEFAULT_WORKSPACE)])[0])
                value = _discover_ready_attempt(workspace=workspace)
            except Exception as exc:
                self._json(HTTPStatus.BAD_REQUEST, _fail_closed(str(exc)))
                return
            self._json(HTTPStatus.OK, {"ok": True, "discovery": value})
            return
        if parsed.path == "/api/status":
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                workspace = _workspace_value(query.get("workspace", [str(DEFAULT_WORKSPACE)])[0])
                attempt_id = _attempt_id(query.get("attempt_id", [""])[0])
                value = _status(workspace=workspace, attempt_id=attempt_id)
            except Exception as exc:
                self._json(HTTPStatus.BAD_REQUEST, _fail_closed(str(exc)))
                return
            self._json(HTTPStatus.OK, {"ok": True, "status": value})
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/api/execute", "/api/recover"}:
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
                raise FirstCanaryRealPaperDashboardError("invalid request size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise FirstCanaryRealPaperDashboardError("request must be JSON object")
            result = _run_execute(payload) if path == "/api/execute" else _recover(payload)
        except (FirstCanaryDashboardError, FirstCanaryRealPaperDashboardError, OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, _fail_closed(str(exc)))
            return
        except Exception as exc:
            diagnostic_id = secrets.token_hex(8)
            print(
                f"AUTO-TRADE real-PAPER canary diagnostic {diagnostic_id}: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
            value = _fail_closed(f"real-PAPER canary service failed closed [{diagnostic_id}]")
            value["diagnostic_id"] = diagnostic_id
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, value)
            return
        self._json(HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT, result)


class RealPaperServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], csrf_token: str) -> None:
        self.csrf_token = csrf_token
        super().__init__(address, RealPaperHandler)


def _start_server(host: str, port: int) -> RealPaperServer:
    if host != "127.0.0.1":
        raise FirstCanaryRealPaperDashboardError("real first-canary dashboard may bind only to 127.0.0.1")
    if not 0 <= port <= 65535:
        raise FirstCanaryRealPaperDashboardError("invalid port")
    return RealPaperServer((host, port), secrets.token_urlsafe(32))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _require_runtime()
    server = _start_server(args.host, args.port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"AUTO-TRADE · PRIMER CANARY REAL PAPER (execution-only): {url}")
    print(
        "This surface can cross one exact Alpaca PAPER POST only after restart-safe preparation, new human approval and a second exact challenge. "
        "Generic Control Center WRITE remains disabled. LIVE remains BLOCKED. Never retry POST after consent/start."
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
