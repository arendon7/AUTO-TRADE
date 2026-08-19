from __future__ import annotations

import argparse
import threading
import webbrowser

import mac_first_canary_unified_dashboard as base


class QueuedRecoverySession(base.UnifiedCanarySession):
    """One-window session that drains multiple burned attempts by GET only.

    Multiple unresolved attempts are never eligible for POST authority. They are
    ordered internally and reconciled serially using the existing certified
    recovery child. No attempt identifier is exposed to the operator UI.
    """

    def __init__(self) -> None:
        super().__init__()
        self.pending_recovery_count = 0

    def connect(self, payload: dict[str, object]) -> dict[str, object]:
        workspace = base.safe._workspace_value(payload.get("workspace"))
        credentials = base.safe._credentials(payload)
        self.workspace = workspace
        self.credentials = credentials
        self.active_attempt_id = None
        self.review_token = None
        self.execute_token = None

        candidates = self._ordered_recovery_candidates()
        if candidates:
            self.active_attempt_id = candidates[0]
            self.pending_recovery_count = len(candidates)
            return {
                "ok": True,
                "connected": True,
                "workspace": str(workspace),
                "credentials_persisted": False,
                "active_attempt_resumed": False,
                "active_recovery_resumed": True,
                "pending_recovery_count": len(candidates),
                "phase": "RECOVERY_ONLY",
                "retry_post": False,
                "live_trading": "BLOCKED",
            }

        self.pending_recovery_count = 0
        ready_resumed = self._resume_exact_ready_attempt()
        return {
            "ok": True,
            "connected": True,
            "workspace": str(workspace),
            "credentials_persisted": False,
            "active_attempt_resumed": ready_resumed,
            "active_recovery_resumed": False,
            "pending_recovery_count": 0,
            "phase": "FINAL_CONFIRMATION_READY" if ready_resumed else "CONNECTED_READY_TO_PREPARE",
            "retry_post": False,
            "live_trading": "BLOCKED",
        }

    def recover(self) -> dict[str, object]:
        """Drain resolvable burned attempts using only the existing GET path.

        The batch stops immediately if an attempt remains pending, is blocked,
        or yields a manual-review/halted resolution. A later click may repeat
        GET reconciliation, but no code path here can mint or consume POST
        authority.
        """
        with self._exclusive_action("recover-queue"):
            credentials = self._require_credentials()
            candidates = self._ordered_recovery_candidates()
            if not candidates:
                self.pending_recovery_count = 0
                return {
                    "ok": True,
                    "phase": "RECOVERED_GET_ONLY",
                    "headline": "No hay canaries pendientes",
                    "detail": "No existe ningún intento quemado que requiera reconciliación. Retry POST permanece bloqueado para intentos anteriores.",
                    "recovery": None,
                    "reconciled_count": 0,
                    "pending_recovery_count": 0,
                    "retry_post": False,
                    "credentials_persisted": False,
                    "live_trading": "BLOCKED",
                }

            reconciled_count = 0
            last_recovery: dict[str, object] | None = None
            for attempt_id in candidates:
                self.active_attempt_id = attempt_id
                self.review_token = None
                self.execute_token = None
                before = base.safe._attempt_status(workspace=self.workspace, attempt_id=attempt_id)
                if before.get("phase") != "RECOVERY_ONLY" or before.get("resolved") is True:
                    continue

                result = base.safe._recover(
                    {
                        "workspace": str(self.workspace),
                        "attempt_id": attempt_id,
                        "paper_key": credentials[0],
                        "paper_secret": credentials[1],
                    }
                )
                last_recovery = base._sanitize_recovery(result)
                after = base.safe._attempt_status(workspace=self.workspace, attempt_id=attempt_id)
                resolved = after.get("phase") == "RESOLVED" or after.get("resolved") is True
                parsed = result.get("json") if isinstance(result, dict) else None
                status_text = ""
                if isinstance(parsed, dict) and isinstance(parsed.get("status"), str):
                    status_text = str(parsed["status"])

                if not resolved or result.get("ok") is not True:
                    remaining = self._ordered_recovery_candidates()
                    self.pending_recovery_count = len(remaining)
                    if remaining:
                        self.active_attempt_id = remaining[0]
                    return {
                        "ok": True,
                        "phase": "RECOVERY_ONLY",
                        "headline": "Reconciliación GET-only pendiente",
                        "detail": "AUTO-TRADE se detuvo en el primer intento que todavía no tiene verdad final del broker. Puedes volver a reconciliar; nunca se repetirá el POST.",
                        "recovery": last_recovery,
                        "reconciled_count": reconciled_count,
                        "pending_recovery_count": len(remaining),
                        "retry_post": False,
                        "credentials_persisted": False,
                        "live_trading": "BLOCKED",
                    }

                reconciled_count += 1
                if "MANUAL_REVIEW" in status_text or "HALTED" in status_text:
                    remaining = self._ordered_recovery_candidates()
                    self.pending_recovery_count = len(remaining)
                    if remaining:
                        self.active_attempt_id = remaining[0]
                    return {
                        "ok": True,
                        "phase": "RECOVERY_ONLY" if remaining else "RECOVERED_GET_ONLY",
                        "headline": "Reconciliación requiere revisión",
                        "detail": "AUTO-TRADE obtuvo una resolución fail-closed que requiere revisión antes de continuar. No existe retry POST.",
                        "recovery": last_recovery,
                        "reconciled_count": reconciled_count,
                        "pending_recovery_count": len(remaining),
                        "manual_review_required": True,
                        "retry_post": False,
                        "credentials_persisted": False,
                        "live_trading": "BLOCKED",
                    }

            remaining = self._ordered_recovery_candidates()
            self.pending_recovery_count = len(remaining)
            if remaining:
                self.active_attempt_id = remaining[0]
                return {
                    "ok": True,
                    "phase": "RECOVERY_ONLY",
                    "headline": "Quedan canaries por reconciliar",
                    "detail": "AUTO-TRADE resolvió los que pudo y conserva la cola restante solo para GET.",
                    "recovery": last_recovery,
                    "reconciled_count": reconciled_count,
                    "pending_recovery_count": len(remaining),
                    "retry_post": False,
                    "credentials_persisted": False,
                    "live_trading": "BLOCKED",
                }

            self.active_attempt_id = None
            return {
                "ok": True,
                "phase": "RECOVERED_GET_ONLY",
                "headline": "Canaries pendientes reconciliados",
                "detail": "AUTO-TRADE terminó la cola usando únicamente broker truth por GET. No hubo un segundo POST.",
                "recovery": last_recovery,
                "reconciled_count": reconciled_count,
                "pending_recovery_count": 0,
                "retry_post": False,
                "credentials_persisted": False,
                "live_trading": "BLOCKED",
            }

    def _ordered_recovery_candidates(self) -> list[str]:
        candidates = self._recovery_candidates()

        def priority(attempt_id: str) -> tuple[int, float, str]:
            status = base.safe._attempt_status(workspace=self.workspace, attempt_id=attempt_id)
            has_reconciliation_evidence = any(
                status.get(key) is not None
                for key in ("reconciliation_failure_status", "reconciliation_pending_status")
            )
            has_execution_evidence = status.get("execution_status") is not None
            rank = 0 if has_reconciliation_evidence else 1 if has_execution_evidence else 2
            path = self.workspace / base.safe.EXECUTION_DIR / attempt_id
            try:
                modified = -path.stat().st_mtime
            except OSError:
                modified = 0.0
            return rank, modified, attempt_id

        return sorted(candidates, key=priority)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-window guided Mac app with GET-only queue recovery for the first BTC/USD Alpaca PAPER canary."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base._require_runtime()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise base.UnifiedCanaryError("unified first-canary app may bind to localhost only")
    app = QueuedRecoverySession()
    server = base.UnifiedServer((args.host, args.port), app)
    url = f"http://{args.host}:{args.port}/"
    print("AUTO-TRADE · Primer Canary PAPER · UNA SOLA APP")
    print(f"Abrir: {url}")
    print("PAPER ONLY · BTC/USD BUY LIMIT IOC · USD 1-5 · RECOVERY QUEUE GET-ONLY · LIVE BLOCKED · RETRY POST FALSE")
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
