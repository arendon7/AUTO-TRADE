from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import threading
import webbrowser

import mac_first_canary_unified_auto_settle as r6

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.first_canary_paper_policy import (
    FIRST_CANARY_PAPER_MAX_NOTIONAL,
    FIRST_CANARY_PAPER_MIN_NOTIONAL,
    FIRST_CANARY_PAPER_SYMBOL,
    FIRST_CANARY_PAPER_TARGET_NOTIONAL,
)
from autotrade.paper_close_operator import (
    CLOSE_WRITE_ENV,
    PaperCloseOperator,
    PreparedPaperCloseOperatorSession,
)
from autotrade.paper_operations_read_model import PaperOperationsReadModel


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web/mac_r7_paper_operations.html"
SAFETY_INVARIANTS = {
    "broker_write_authorized": False,
    "retry_post": False,
    "credentials_persisted": False,
    "live_trading": "BLOCKED",
}


class PaperOperationsSession(r6.AutoSettlementSession):
    """R7 Portfolio surface plus a tokenized one-shot risk-reducing close facade.

    Historical entry approve/execute/recover remains inherited from the certified
    R6 session. R7 owns no low-level writer or transport here: all close authority
    is delegated to ``PaperCloseOperator``. A fresh broker exposure blocks a new
    BUY; a burned close attempt blocks every new close and may only reconcile.
    """

    def __init__(self) -> None:
        super().__init__()
        self.close_operator: PaperCloseOperator | None = None
        self.close_prepared: PreparedPaperCloseOperatorSession | None = None
        self.close_review_token: str | None = None
        self.close_execute_token: str | None = None

    def connect(self, payload: dict[str, object]) -> dict[str, object]:
        result = super().connect(payload)
        self._clear_close_ephemeral()
        self.close_operator = PaperCloseOperator(workspace_path=self.workspace)
        pending = self.close_operator.pending_recovery_attempt()
        result["close_recovery_resumed"] = pending is not None
        result["close_write_gate_enabled"] = _close_write_enabled()
        if pending is not None:
            self.review_token = None
            self.execute_token = None
            result["phase"] = "CLOSE_RECOVERY_ONLY"
        return result

    def _operations_snapshot(self):
        credentials = self._paper_credentials()
        return PaperOperationsReadModel(workspace_path=self.workspace).snapshot(
            credentials=credentials,
            now=datetime.now(timezone.utc),
        )

    def operations(self) -> dict[str, object]:
        snapshot = self._operations_snapshot()
        pending_close = self._close_operator().pending_recovery_attempt()
        result = snapshot.to_dict()
        result.update(
            {
                "ok": True,
                "surface": "R7_PAPER_OPERATIONS",
                "entry_preparation_allowed": (
                    pending_close is None
                    and len(snapshot.portfolio.positions) == 0
                    and len(snapshot.portfolio.open_orders) == 0
                ),
                "first_canary_policy": _policy_meta(),
                "close_preparation_allowed": (
                    pending_close is None and snapshot.ready_for_close_preparation
                ),
                "close_recovery_pending": pending_close is not None,
                "close_write_gate_enabled": _close_write_enabled(),
                "close_execution_authorized": False,
            }
        )
        result.update(SAFETY_INVARIANTS)
        return result

    def close_prepare(self) -> dict[str, object]:
        with self._exclusive_action("close_prepare"):
            operator = self._close_operator()
            if operator.pending_recovery_attempt() is not None:
                raise r6.base.UnifiedCanaryError(
                    "a burned PAPER close attempt is unresolved; use only close reconciliation"
                )
            prepared = operator.prepare_full_close(credentials=self._paper_credentials())
            self.close_prepared = prepared
            self.close_review_token = secrets.token_urlsafe(24)
            self.close_execute_token = None
            return {
                "ok": True,
                "phase": "CLOSE_REVIEW_READY",
                "summary": _sanitize_close_summary(prepared.summary()),
                "review_token": self.close_review_token,
                "broker_write_performed": False,
                "close_write_gate_enabled": _close_write_enabled(),
                "retry_post": False,
                "credentials_persisted": False,
                "live_trading": "BLOCKED",
            }

    def close_approve(self, payload: dict[str, object]) -> dict[str, object]:
        if payload.get("close_review_confirmed") is not True:
            raise r6.base.UnifiedCanaryError("explicit close review confirmation is required")
        supplied = str(payload.get("close_review_token") or "")
        if not self.close_review_token or not secrets.compare_digest(supplied, self.close_review_token):
            raise r6.base.UnifiedCanaryError("this close review is stale; prepare a fresh close plan")
        with self._exclusive_action("close_approve"):
            prepared = self._require_close_prepared()
            self._close_operator().approve(prepared=prepared)
            self.close_review_token = None
            self.close_execute_token = secrets.token_urlsafe(24)
            return {
                "ok": True,
                "phase": "CLOSE_FINAL_CONFIRMATION_READY",
                "summary": _sanitize_close_summary(prepared.summary()),
                "execute_token": self.close_execute_token,
                "broker_write_performed": False,
                "close_write_gate_enabled": _close_write_enabled(),
                "retry_post": False,
                "credentials_persisted": False,
                "live_trading": "BLOCKED",
            }

    def close_execute(self, payload: dict[str, object]) -> dict[str, object]:
        if payload.get("close_execute_confirmed") is not True:
            raise r6.base.UnifiedCanaryError("explicit final PAPER close confirmation is required")
        supplied = str(payload.get("close_execute_token") or "")
        if not self.close_execute_token or not secrets.compare_digest(supplied, self.close_execute_token):
            raise r6.base.UnifiedCanaryError("this final close confirmation is stale or already consumed")
        with self._exclusive_action("close_execute"):
            prepared = self._require_close_prepared()
            self.close_execute_token = None
            result = self._close_operator().execute_once(
                prepared=prepared,
                credentials=self._paper_credentials(),
            )
            self.close_prepared = None
            self.close_review_token = None
            return _sanitize_close_result(result)

    def close_recover(self) -> dict[str, object]:
        with self._exclusive_action("close_recover"):
            self.close_execute_token = None
            self.close_review_token = None
            self.close_prepared = None
            result = self._close_operator().recover(credentials=self._paper_credentials())
            return _sanitize_close_result(result)

    def _assert_no_existing_broker_exposure(self) -> None:
        if self._close_operator().pending_recovery_attempt() is not None:
            raise r6.base.UnifiedCanaryError(
                "R7 entry blocked: a burned close attempt is pending GET-only reconciliation"
            )
        snapshot = self._operations_snapshot()
        if snapshot.portfolio.positions or snapshot.portfolio.open_orders:
            raise r6.base.UnifiedCanaryError(
                "R7 entry blocked: fresh Alpaca PAPER broker truth shows an existing position "
                "or open order; AUTO-TRADE will not prepare another BUY while exposure exists"
            )

    def _assert_no_unresolved_recovery(self) -> None:
        super()._assert_no_unresolved_recovery()
        self._assert_no_existing_broker_exposure()

    def _close_operator(self) -> PaperCloseOperator:
        if self.close_operator is None:
            if self.credentials is None:
                raise r6.base.UnifiedCanaryError("connect Alpaca PAPER credentials first")
            self.close_operator = PaperCloseOperator(workspace_path=self.workspace)
        return self.close_operator

    def _paper_credentials(self) -> AlpacaPaperCredentials:
        key_id, secret_key = self._require_credentials()
        return AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)

    def _require_close_prepared(self) -> PreparedPaperCloseOperatorSession:
        if not isinstance(self.close_prepared, PreparedPaperCloseOperatorSession):
            raise r6.base.UnifiedCanaryError("prepare a fresh risk-reducing PAPER close first")
        return self.close_prepared

    def _clear_close_ephemeral(self) -> None:
        self.close_prepared = None
        self.close_review_token = None
        self.close_execute_token = None


def _close_write_enabled() -> bool:
    return os.environ.get(CLOSE_WRITE_ENV, "DISABLED") == "ENABLED"


def _policy_meta() -> dict[str, object]:
    return {
        "environment": "PAPER",
        "symbol": FIRST_CANARY_PAPER_SYMBOL,
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "IOC",
        "min_notional_usd": str(FIRST_CANARY_PAPER_MIN_NOTIONAL),
        "target_notional_usd": str(FIRST_CANARY_PAPER_TARGET_NOTIONAL),
        "hard_max_notional_usd": str(FIRST_CANARY_PAPER_MAX_NOTIONAL),
        "operations_get_only": True,
        "close_mode": "FULL_ONLY_FIRST_OPERATION",
        "close_side": "SELL",
        "close_order_type": "LIMIT",
        "close_time_in_force": "IOC",
        "close_max_slippage_bps": "25",
        "close_write_gate_enabled": _close_write_enabled(),
        "retry_post": False,
        "credentials_persisted": False,
        "live_trading": "BLOCKED",
    }


def _sanitize_close_summary(summary: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in summary.items()
        if key not in {"attempt_id_internal", "source_attempt_id"}
    }


def _sanitize_close_result(result: dict[str, object]) -> dict[str, object]:
    return {
        "ok": result.get("ok"),
        "phase": result.get("phase"),
        "broker_write_performed": result.get("broker_write_performed"),
        "broker_post_attempt_burned": result.get("broker_post_attempt_burned"),
        "broker_post_status": result.get("broker_post_status"),
        "settlement": result.get("settlement"),
        "retry_post": False,
        "credentials_persisted": False,
        "live_trading": "BLOCKED",
    }


class PaperOperationsHandler(r6.base.UnifiedHandler):
    server_version = "AUTO-TRADE-R7-PaperOperations"

    @property
    def operations_app(self) -> PaperOperationsSession:
        app = self.server.app  # type: ignore[attr-defined]
        if not isinstance(app, PaperOperationsSession):
            raise r6.base.UnifiedCanaryError("R7 PAPER operations session is unavailable")
        return app

    def do_GET(self) -> None:
        if self.path == "/":
            body = HTML.read_bytes()
            self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
            self.wfile.write(body)
            return
        if self.path == "/api/meta":
            self._json(
                {
                    "ok": True,
                    "surface": "R7_PAPER_OPERATIONS",
                    "first_canary_policy": _policy_meta(),
                    "close_write_gate_enabled": _close_write_enabled(),
                    **SAFETY_INVARIANTS,
                }
            )
            return
        if self.path == "/api/operations":
            try:
                self._json(self.operations_app.operations())
            except Exception as exc:
                self._json(
                    {
                        "ok": False,
                        "surface": "R7_PAPER_OPERATIONS",
                        "error": str(exc),
                        "entry_preparation_allowed": False,
                        "ready_for_close_preparation": False,
                        "close_preparation_allowed": False,
                        "close_execution_authorized": False,
                        "close_write_gate_enabled": _close_write_enabled(),
                        **SAFETY_INVARIANTS,
                    },
                    HTTPStatus.CONFLICT,
                )
            return
        super().do_GET()

    def do_POST(self) -> None:
        if not self.path.startswith("/api/close/"):
            super().do_POST()
            return
        try:
            payload = r6.base._read_json(self)
            if self.path == "/api/close/prepare":
                result = self.operations_app.close_prepare()
            elif self.path == "/api/close/approve":
                result = self.operations_app.close_approve(payload)
            elif self.path == "/api/close/execute":
                result = self.operations_app.close_execute(payload)
            elif self.path == "/api/close/recover":
                result = self.operations_app.close_recover()
            else:
                self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json(result)
        except Exception as exc:
            pending = False
            try:
                pending = self.operations_app._close_operator().pending_recovery_attempt() is not None
            except Exception:
                pending = True
            self._json(
                {
                    "ok": False,
                    "error": str(exc),
                    "phase": "RECOVERY_ONLY" if pending else "CLOSE_BLOCKED_BEFORE_POST",
                    "close_recovery_pending": pending,
                    "broker_write_performed": False if not pending else "UNKNOWN_OR_ALREADY_BURNED",
                    "retry_post": False,
                    "credentials_persisted": False,
                    "live_trading": "BLOCKED",
                },
                HTTPStatus.CONFLICT,
            )


class PaperOperationsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], app: PaperOperationsSession) -> None:
        self.app = app
        super().__init__(address, PaperOperationsHandler)


def _require_runtime() -> None:
    r6.base._require_runtime()
    if not HTML.is_file():
        raise r6.base.UnifiedCanaryError("R7 PAPER operations UI is missing")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "R7 one-window PAPER operations surface with broker-truth Portfolio/Safety reads, "
            "certified R6 entry authority and one-shot risk-reducing close orchestration."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _require_runtime()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise r6.base.UnifiedCanaryError("R7 PAPER operations app may bind to localhost only")
    app = PaperOperationsSession()
    server = PaperOperationsServer((args.host, args.port), app)
    url = f"http://{args.host}:{args.port}/"
    print("AUTO-TRADE · R7 PAPER OPERATIONS · UNA SOLA APP")
    print(f"Abrir: {url}")
    print(
        "PAPER ONLY · Portfolio/Safety GET-only · first canary entry R6 USD "
        f"{FIRST_CANARY_PAPER_MIN_NOTIONAL}-{FIRST_CANARY_PAPER_MAX_NOTIONAL} · "
        f"R7 CLOSE GATE {'ENABLED' if _close_write_enabled() else 'DISABLED'} · "
        "FULL BTC/USD SELL LIMIT IOC · NO RETRY POST · LIVE BLOCKED"
    )
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
