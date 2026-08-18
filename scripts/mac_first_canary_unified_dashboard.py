from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import webbrowser

import mac_first_canary_dashboard as safe
import mac_first_canary_real_paper_dashboard as real


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web/mac_first_canary_unified.html"
PYTHON = ROOT / ".venv/bin/python"
PREPARE_SCRIPT = ROOT / "scripts/mac_crypto_first_canary_prepare_restart_safe.py"
APPROVAL_SCRIPT = ROOT / "scripts/mac_crypto_first_canary_approval.py"
EXECUTE_SCRIPT = ROOT / "scripts/mac_crypto_first_canary_execute_real_paper.py"
RECOVERY_SCRIPT = ROOT / "scripts/mac_crypto_first_canary_reconcile.py"
DEFAULT_OPERATOR = "operator-001"
MAX_BODY_BYTES = 32 * 1024


class UnifiedCanaryError(RuntimeError):
    pass


class UnifiedCanarySession:
    """Ephemeral operator session.

    Credentials live only in this Python process. Attempt identity and authority
    continue to live in the existing durable first-canary stores. The session
    intentionally contains no broker transport or writer implementation.
    """

    def __init__(self) -> None:
        self.workspace: Path = safe.DEFAULT_WORKSPACE
        self.credentials: tuple[str, str] | None = None
        self.active_attempt_id: str | None = None
        self._action_lock = threading.Lock()

    def connect(self, payload: dict[str, object]) -> dict[str, object]:
        workspace = safe._workspace_value(payload.get("workspace"))
        credentials = safe._credentials(payload)
        self.workspace = workspace
        self.credentials = credentials
        resumed = self._resume_exact_ready_attempt()
        return {
            "ok": True,
            "connected": True,
            "workspace": str(workspace),
            "credentials_persisted": False,
            "active_attempt_resumed": resumed,
            "active_attempt_id": self.active_attempt_id,
            "live_trading": "BLOCKED",
        }

    def prepare(self) -> dict[str, object]:
        with self._exclusive_action("prepare"):
            credentials = self._require_credentials()
            attempt_id = "first-canary-" + secrets.token_hex(16)
            result = safe._run_child(
                [
                    "scripts/mac_crypto_first_canary_prepare_restart_safe.py",
                    "--workspace",
                    str(self.workspace),
                    "--attempt-id",
                    attempt_id,
                    "--allow-paper-crypto-read",
                ],
                credentials=credentials,
                timeout=75,
            )
            if result.get("ok") is not True:
                raise UnifiedCanaryError(str(result.get("error") or "PAPER preparation failed"))
            self.active_attempt_id = attempt_id
            status = safe._attempt_status(workspace=self.workspace, attempt_id=attempt_id)
            preparation = status.get("preparation")
            if not isinstance(preparation, dict):
                raise UnifiedCanaryError("prepared attempt did not expose a safe preparation summary")
            if status.get("phase") != "APPROVAL_REQUIRED":
                raise UnifiedCanaryError("prepared attempt did not enter APPROVAL_REQUIRED")
            return {
                "ok": True,
                "phase": "REVIEW_READY",
                "summary": _summary(preparation),
                "attempt_id": attempt_id,
                "credentials_persisted": False,
                "broker_write_performed": False,
                "live_trading": "BLOCKED",
            }

    def approve(self, payload: dict[str, object]) -> dict[str, object]:
        if payload.get("review_confirmed") is not True:
            raise UnifiedCanaryError("review confirmation is required")
        with self._exclusive_action("approve"):
            attempt_id = self._require_active_attempt()
            status = safe._attempt_status(workspace=self.workspace, attempt_id=attempt_id)
            context = status.get("operator_context")
            challenge = status.get("operator_challenge")
            if not isinstance(context, dict) or not isinstance(challenge, str) or not challenge:
                raise UnifiedCanaryError("fresh prepared operator authority is unavailable; prepare again")
            if status.get("phase") != "APPROVAL_REQUIRED":
                raise UnifiedCanaryError("attempt is not in APPROVAL_REQUIRED")
            result = safe._run_child(
                [
                    "scripts/mac_crypto_first_canary_approval.py",
                    "--workspace",
                    str(self.workspace),
                    "--attempt-id",
                    attempt_id,
                ],
                credentials=None,
                stdin_payload={
                    "context": context,
                    "operator_id": DEFAULT_OPERATOR,
                    "confirmation": challenge,
                },
                timeout=20,
            )
            if result.get("ok") is not True:
                raise UnifiedCanaryError(str(result.get("error") or "human approval failed"))
            ready = real._status(workspace=self.workspace, attempt_id=attempt_id)
            if ready.get("ready_for_real_post") is not True:
                raise UnifiedCanaryError(
                    "approval completed but the exact restart-safe attempt is not ready for final PAPER confirmation"
                )
            return {
                "ok": True,
                "phase": "FINAL_CONFIRMATION_READY",
                "summary": _summary_from_real(ready),
                "credentials_persisted": False,
                "broker_write_performed": False,
                "live_trading": "BLOCKED",
            }

    def execute(self, payload: dict[str, object]) -> dict[str, object]:
        if payload.get("execute_confirmed") is not True:
            raise UnifiedCanaryError("explicit final PAPER execution confirmation is required")
        with self._exclusive_action("execute"):
            credentials = self._require_credentials()
            attempt_id = self._require_active_attempt()

            discovery = real._discover_ready_attempt(workspace=self.workspace)
            if (
                discovery.get("selection_status") != "EXACT_ONE_READY"
                or discovery.get("attempt_id") != attempt_id
            ):
                raise UnifiedCanaryError(
                    "the approved package is no longer the unique fresh executable attempt; prepare again"
                )
            status = real._status(workspace=self.workspace, attempt_id=attempt_id)
            challenge = status.get("external_post_challenge")
            if status.get("ready_for_real_post") is not True or not isinstance(challenge, str) or not challenge:
                raise UnifiedCanaryError("final PAPER guard is not ready; no POST was sent")

            try:
                execution = real._run_execute(
                    {
                        "workspace": str(self.workspace),
                        "attempt_id": attempt_id,
                        "paper_key": credentials[0],
                        "paper_secret": credentials[1],
                        "confirmation": challenge,
                    }
                )
            except Exception as exc:
                post_status = self._safe_real_status(attempt_id)
                recovery = self._auto_recover_if_needed(post_status)
                return {
                    "ok": False,
                    "phase": "RECOVERY_ONLY" if recovery is not None else "EXECUTION_BLOCKED",
                    "execution_error": str(exc),
                    "execution": None,
                    "recovery": recovery,
                    "retry_post": False,
                    "credentials_persisted": False,
                    "live_trading": "BLOCKED",
                }

            post_status = self._safe_real_status(attempt_id)
            recovery = self._auto_recover_if_needed(post_status)
            outcome = _operator_outcome(execution=execution, status=post_status, recovery=recovery)
            return {
                "ok": bool(execution.get("ok")),
                "phase": outcome["phase"],
                "headline": outcome["headline"],
                "detail": outcome["detail"],
                "execution": _sanitize_execution(execution),
                "recovery": recovery,
                "retry_post": False,
                "credentials_persisted": False,
                "live_trading": "BLOCKED",
            }

    def state(self) -> dict[str, object]:
        attempt_id = self.active_attempt_id
        if attempt_id is None:
            return {
                "connected": self.credentials is not None,
                "phase": "CONNECTED_READY_TO_PREPARE" if self.credentials is not None else "CONNECT_REQUIRED",
                "summary": None,
                "retry_post": False,
                "live_trading": "BLOCKED",
            }
        safe_status = safe._attempt_status(workspace=self.workspace, attempt_id=attempt_id)
        real_status = self._safe_real_status(attempt_id)
        if real_status.get("recovery_get_only") is True:
            phase = "RECOVERY_ONLY"
        elif real_status.get("ready_for_real_post") is True:
            phase = "FINAL_CONFIRMATION_READY"
        elif safe_status.get("phase") == "APPROVAL_REQUIRED":
            phase = "REVIEW_READY"
        elif safe_status.get("phase") == "RESOLVED":
            phase = "RESOLVED"
        else:
            phase = str(safe_status.get("phase") or "UNKNOWN")
        preparation = safe_status.get("preparation")
        return {
            "connected": self.credentials is not None,
            "phase": phase,
            "summary": _summary(preparation) if isinstance(preparation, dict) else None,
            "retry_post": False,
            "live_trading": "BLOCKED",
        }

    def reset(self) -> dict[str, object]:
        if self._action_lock.locked():
            raise UnifiedCanaryError("an operation is in progress")
        self.active_attempt_id = None
        return {
            "ok": True,
            "connected": self.credentials is not None,
            "phase": "CONNECTED_READY_TO_PREPARE" if self.credentials is not None else "CONNECT_REQUIRED",
            "retry_post": False,
            "live_trading": "BLOCKED",
        }

    def _resume_exact_ready_attempt(self) -> bool:
        try:
            discovery = real._discover_ready_attempt(workspace=self.workspace)
        except Exception:
            return False
        if discovery.get("selection_status") == "EXACT_ONE_READY":
            value = discovery.get("attempt_id")
            if isinstance(value, str):
                self.active_attempt_id = value
                return True
        return False

    def _require_credentials(self) -> tuple[str, str]:
        if self.credentials is None:
            raise UnifiedCanaryError("connect Alpaca PAPER credentials first")
        return self.credentials

    def _require_active_attempt(self) -> str:
        if not isinstance(self.active_attempt_id, str):
            raise UnifiedCanaryError("prepare a fresh first-canary attempt first")
        return self.active_attempt_id

    def _safe_real_status(self, attempt_id: str) -> dict[str, object]:
        try:
            return real._status(workspace=self.workspace, attempt_id=attempt_id)
        except Exception as exc:
            return {
                "phase": "STATUS_UNAVAILABLE",
                "ready_for_real_post": False,
                "recovery_get_only": True,
                "status_error": str(exc),
                "retry_post": False,
                "live_trading": "BLOCKED",
            }

    def _auto_recover_if_needed(self, status: dict[str, object]) -> dict[str, object] | None:
        if status.get("recovery_get_only") is not True:
            return None
        credentials = self._require_credentials()
        attempt_id = self._require_active_attempt()
        result = safe._recover(
            {
                "workspace": str(self.workspace),
                "attempt_id": attempt_id,
                "paper_key": credentials[0],
                "paper_secret": credentials[1],
            }
        )
        return _sanitize_recovery(result)

    class _Action:
        def __init__(self, lock: threading.Lock, label: str) -> None:
            self.lock = lock
            self.label = label

        def __enter__(self):
            if not self.lock.acquire(blocking=False):
                raise UnifiedCanaryError(
                    f"another first-canary action is already running; {self.label} blocked"
                )
            return self

        def __exit__(self, exc_type, exc, tb):
            self.lock.release()
            return False

    def _exclusive_action(self, label: str) -> "UnifiedCanarySession._Action":
        return UnifiedCanarySession._Action(self._action_lock, label)


def _summary(preparation: dict[str, object]) -> dict[str, object]:
    return {
        "symbol": preparation.get("symbol") or "BTC/USD",
        "notional_usd": preparation.get("prepared_notional"),
        "quantity_btc": preparation.get("prepared_quantity"),
        "limit_price": preparation.get("prepared_limit_price"),
        "execution_deadline": preparation.get("execution_deadline"),
        "environment": "PAPER",
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "IOC",
        "hard_max_notional_usd": "5",
        "live_trading": "BLOCKED",
    }


def _summary_from_real(status: dict[str, object]) -> dict[str, object]:
    preparation = status.get("preparation")
    if not isinstance(preparation, dict):
        return {
            "symbol": "BTC/USD",
            "environment": "PAPER",
            "hard_max_notional_usd": "5",
            "live_trading": "BLOCKED",
        }
    return {
        "symbol": preparation.get("symbol"),
        "notional_usd": preparation.get("notional"),
        "quantity_btc": preparation.get("quantity"),
        "limit_price": preparation.get("limit_price"),
        "execution_deadline": preparation.get("execution_deadline"),
        "environment": "PAPER",
        "side": "BUY",
        "order_type": preparation.get("order_type") or "LIMIT",
        "time_in_force": preparation.get("time_in_force") or "IOC",
        "hard_max_notional_usd": "5",
        "live_trading": "BLOCKED",
    }


def _sanitize_execution(result: dict[str, object]) -> dict[str, object]:
    parsed = result.get("json")
    safe_parsed: dict[str, object] | None = None
    if isinstance(parsed, dict):
        safe_parsed = {
            key: value
            for key, value in parsed.items()
            if key
            not in {
                "paper_key",
                "paper_secret",
                "credentials",
                "secret",
            }
        }
    return {
        "ok": result.get("ok"),
        "returncode": result.get("returncode"),
        "error": result.get("error"),
        "broker_write_performed": result.get("broker_write_performed"),
        "json": safe_parsed,
        "retry_post": False,
        "live_trading": "BLOCKED",
    }


def _sanitize_recovery(result: dict[str, object]) -> dict[str, object]:
    parsed = result.get("json")
    return {
        "ok": result.get("ok"),
        "returncode": result.get("returncode"),
        "error": result.get("error"),
        "json": parsed if isinstance(parsed, dict) else None,
        "broker_write_performed": False,
        "retry_post": False,
        "live_trading": "BLOCKED",
    }


def _operator_outcome(
    *, execution: dict[str, object], status: dict[str, object], recovery: dict[str, object] | None
) -> dict[str, str]:
    if recovery is not None:
        if recovery.get("ok") is True:
            return {
                "phase": "RECOVERED_GET_ONLY",
                "headline": "Resultado reconciliado",
                "detail": "La ejecución entró en recuperación y AUTO-TRADE consultó broker truth únicamente por GET. No existe retry POST.",
            }
        return {
            "phase": "RECOVERY_ONLY",
            "headline": "Resultado incierto · no reintentar",
            "detail": "AUTO-TRADE bloqueó cualquier segundo POST. Usa únicamente la evidencia/reconciliación GET-only.",
        }
    if execution.get("ok") is True:
        return {
            "phase": "EXECUTION_COMPLETED",
            "headline": "Canary PAPER procesado",
            "detail": "La solicitud one-shot terminó y la autoridad de repetición permanece bloqueada.",
        }
    if status.get("recovery_get_only") is True:
        return {
            "phase": "RECOVERY_ONLY",
            "headline": "Resultado incierto · no reintentar",
            "detail": "El intento quedó quemado para POST y solo admite recuperación GET-only.",
        }
    return {
        "phase": "EXECUTION_BLOCKED",
        "headline": "Ejecución bloqueada antes del POST",
        "detail": "Alguna condición final no era válida. Crea una preparación nueva; no reutilices el intento.",
    }


def _require_runtime() -> None:
    if os.environ.get(safe.WRITE_ENV) == "ENABLED":
        raise UnifiedCanaryError("unified first-canary app refuses generic external write enablement")
    for path in (PYTHON, HTML, PREPARE_SCRIPT, APPROVAL_SCRIPT, EXECUTE_SCRIPT, RECOVERY_SCRIPT):
        if not path.is_file():
            raise UnifiedCanaryError(f"unified first-canary runtime is incomplete: missing {path.name}")


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    raw_length = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise UnifiedCanaryError("invalid request length") from exc
    if length <= 0 or length > MAX_BODY_BYTES:
        raise UnifiedCanaryError("invalid request body size")
    try:
        value = json.loads(handler.rfile.read(length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UnifiedCanaryError("request body must be a JSON object") from exc
    if not isinstance(value, dict):
        raise UnifiedCanaryError("request body must be a JSON object")
    return value


class UnifiedHandler(BaseHTTPRequestHandler):
    server_version = "AUTO-TRADE-R6-UnifiedFirstCanary"

    @property
    def app(self) -> UnifiedCanarySession:
        return self.server.app  # type: ignore[attr-defined]

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
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()

    def _json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/":
            body = HTML.read_bytes()
            self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
            self.wfile.write(body)
            return
        if self.path == "/api/state":
            self._json(self.app.state())
            return
        if self.path == "/api/meta":
            self._json(
                {
                    "environment": "PAPER",
                    "symbol": "BTC/USD",
                    "side": "BUY",
                    "order_type": "LIMIT",
                    "time_in_force": "IOC",
                    "target_notional_usd": "2",
                    "hard_max_notional_usd": "5",
                    "credentials_persisted": False,
                    "one_window": True,
                    "user_visible_attempt_id": False,
                    "user_visible_hash_copying": False,
                    "retry_post": False,
                    "live_trading": "BLOCKED",
                    "default_workspace": str(safe.DEFAULT_WORKSPACE),
                }
            )
            return
        self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            payload = _read_json(self)
            if self.path == "/api/connect":
                result = self.app.connect(payload)
            elif self.path == "/api/prepare":
                result = self.app.prepare()
            elif self.path == "/api/approve":
                result = self.app.approve(payload)
            elif self.path == "/api/execute":
                result = self.app.execute(payload)
            elif self.path == "/api/reset":
                result = self.app.reset()
            else:
                self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json(result)
        except Exception as exc:
            self._json(
                {
                    "ok": False,
                    "error": str(exc),
                    "retry_post": False,
                    "credentials_persisted": False,
                    "live_trading": "BLOCKED",
                },
                HTTPStatus.CONFLICT,
            )


class UnifiedServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], app: UnifiedCanarySession) -> None:
        self.app = app
        super().__init__(address, UnifiedHandler)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-window guided Mac app for the first BTC/USD Alpaca PAPER canary."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _require_runtime()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise UnifiedCanaryError("unified first-canary app may bind to localhost only")
    app = UnifiedCanarySession()
    server = UnifiedServer((args.host, args.port), app)
    url = f"http://{args.host}:{args.port}/"
    print("AUTO-TRADE · Primer Canary PAPER · UNA SOLA APP")
    print(f"Abrir: {url}")
    print("PAPER ONLY · BTC/USD BUY LIMIT IOC · USD 1-5 · LIVE BLOCKED · RETRY POST FALSE")
    if not args.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url, new=1)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
