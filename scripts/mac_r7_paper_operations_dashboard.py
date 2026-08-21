from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
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
    """R7 read-only portfolio surface over the certified R6 one-shot session.

    This overlay does not implement approve/execute/recover and does not own a
    broker transport. Those methods remain inherited from the already-certified
    R6 session. R7 adds only fresh PAPER broker truth plus a fail-closed entry
    interlock: a new first-canary BUY cannot be prepared while any position or
    open order exists in the account.
    """

    def _operations_snapshot(self):
        key_id, secret_key = self._require_credentials()
        return PaperOperationsReadModel(workspace_path=self.workspace).snapshot(
            credentials=AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key),
            now=datetime.now(timezone.utc),
        )

    def operations(self) -> dict[str, object]:
        snapshot = self._operations_snapshot()
        result = snapshot.to_dict()
        result.update(
            {
                "ok": True,
                "surface": "R7_PAPER_OPERATIONS_READ_ONLY",
                "entry_preparation_allowed": (
                    len(snapshot.portfolio.positions) == 0
                    and len(snapshot.portfolio.open_orders) == 0
                ),
                "first_canary_policy": _policy_meta(),
                "close_execution_authorized": False,
            }
        )
        result.update(SAFETY_INVARIANTS)
        return result

    def _assert_no_existing_broker_exposure(self) -> None:
        snapshot = self._operations_snapshot()
        if snapshot.portfolio.positions or snapshot.portfolio.open_orders:
            raise r6.base.UnifiedCanaryError(
                "R7 entry blocked: fresh Alpaca PAPER broker truth shows an existing position "
                "or open order; AUTO-TRADE will not prepare another BUY while exposure exists"
            )

    def _assert_no_unresolved_recovery(self) -> None:
        # This inherited hook executes inside the certified R6 prepare action
        # lock, so the R7 broker-truth interlock is checked before any new BUY
        # preparation without modifying the historical execute path.
        super()._assert_no_unresolved_recovery()
        self._assert_no_existing_broker_exposure()


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
        "close_execution_authorized": False,
        "retry_post": False,
        "credentials_persisted": False,
        "live_trading": "BLOCKED",
    }


class PaperOperationsHandler(r6.base.UnifiedHandler):
    server_version = "AUTO-TRADE-R7-PaperOperationsReadOnly"

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
                    "surface": "R7_PAPER_OPERATIONS_READ_ONLY",
                    "first_canary_policy": _policy_meta(),
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
                        "surface": "R7_PAPER_OPERATIONS_READ_ONLY",
                        "error": str(exc),
                        "entry_preparation_allowed": False,
                        "ready_for_close_preparation": False,
                        "close_execution_authorized": False,
                        **SAFETY_INVARIANTS,
                    },
                    HTTPStatus.CONFLICT,
                )
            return
        super().do_GET()


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
            "R7 one-window PAPER operations surface with broker-truth Portfolio/Safety reads "
            "over the certified R6 first-canary authority path."
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
        "PAPER ONLY · Portfolio/Safety GET-ONLY · first canary BTC/USD BUY LIMIT IOC · "
        f"USD {FIRST_CANARY_PAPER_MIN_NOTIONAL}-{FIRST_CANARY_PAPER_MAX_NOTIONAL} · "
        "CLOSE WRITE NOT ENABLED · LIVE BLOCKED · RETRY POST FALSE"
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
