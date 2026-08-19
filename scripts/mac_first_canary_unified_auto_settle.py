from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import threading
import time
import webbrowser

import mac_first_canary_unified_dashboard as base
import mac_first_canary_unified_queue as queue

from autotrade.brokers.alpaca_paper_crypto_account_status import attest_active_crypto_account
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


AUTO_SETTLE_DELAYS_SECONDS = (0.0, 1.0, 2.0, 4.0)
SAFETY_INVARIANTS = {"retry_post": False, "live_trading": "BLOCKED"}


class AutoSettlementSession(queue.QueuedRecoverySession):
    """Unified operator session with bounded GET-only post-execution settlement.

    The single POST remains owned by the already-certified base execution path.
    This layer may only repeat the certified recovery child by GET after POST
    authority has already been consumed/burned. It never mints, restores or
    retries POST authority.
    """

    def connect(self, payload: dict[str, object]) -> dict[str, object]:
        result = super().connect(payload)
        credentials = self._require_credentials()
        attestation = attest_active_crypto_account(
            credentials=AlpacaPaperCredentials(
                key_id=credentials[0],
                secret_key=credentials[1],
            ),
            expected_account_id=_workspace_account_id(self.workspace),
            now=datetime.now(timezone.utc),
        )
        result["crypto_status"] = attestation.crypto_status
        result["crypto_ready"] = True
        result["crypto_status_fingerprint"] = attestation.fingerprint
        result.update(SAFETY_INVARIANTS)
        return result

    def _auto_recover_if_needed(self, status: dict[str, object]) -> dict[str, object] | None:
        if status.get("recovery_get_only") is not True:
            return None

        credentials = self._require_credentials()
        attempt_id = self._require_active_attempt()
        last: dict[str, object] | None = None

        for index, delay in enumerate(AUTO_SETTLE_DELAYS_SECONDS, start=1):
            if delay:
                time.sleep(delay)

            result = base.safe._recover(
                {
                    "workspace": str(self.workspace),
                    "attempt_id": attempt_id,
                    "paper_key": credentials[0],
                    "paper_secret": credentials[1],
                }
            )
            sanitized = base._sanitize_recovery(result)
            sanitized.update(SAFETY_INVARIANTS)
            sanitized["auto_settlement_attempts"] = index

            after = base.safe._attempt_status(workspace=self.workspace, attempt_id=attempt_id)
            resolved = after.get("phase") == "RESOLVED" or after.get("resolved") is True

            parsed = result.get("json") if isinstance(result, dict) else None
            status_text = ""
            if isinstance(parsed, dict) and isinstance(parsed.get("status"), str):
                status_text = str(parsed["status"])
            manual_review = "MANUAL_REVIEW" in status_text or "HALTED" in status_text

            sanitized["auto_settlement_resolved"] = resolved
            sanitized["manual_review_required"] = manual_review
            sanitized["auto_settlement_exhausted"] = False
            last = sanitized

            if resolved or manual_review:
                return sanitized

        if last is None:
            return None
        # A non-terminal GET result must not be presented by the inherited
        # operator outcome mapper as a terminal reconciliation merely because
        # the transport call itself returned successfully.
        last["ok"] = False
        last["auto_settlement_resolved"] = False
        last["auto_settlement_exhausted"] = True
        last.update(SAFETY_INVARIANTS)
        return last

    def execute(self, payload: dict[str, object]) -> dict[str, object]:
        result = super().execute(payload)
        result.update(SAFETY_INVARIANTS)
        result["auto_settlement"] = True

        recovery = result.get("recovery")
        if isinstance(recovery, dict):
            attempts = int(recovery.get("auto_settlement_attempts") or 0)
            if recovery.get("manual_review_required") is True:
                result.update(
                    {
                        "phase": "RECOVERY_ONLY",
                        "headline": "Canary PAPER requiere revisión",
                        "detail": (
                            "AUTO-TRADE obtuvo broker truth fail-closed durante la reconciliación automática. "
                            "No existe retry POST; conserva este intento para revisión GET-only."
                        ),
                    }
                )
                return result

            if recovery.get("auto_settlement_resolved") is True:
                rejection = _broker_rejection(result)
                if rejection is not None:
                    result.update(
                        {
                            "ok": True,
                            "phase": "SETTLED_REJECTED",
                            "headline": "Alpaca rechazó la orden · sin exposición",
                            "detail": (
                                f"{rejection} La reconciliación confirmó posición BTC/USD igual a cero. "
                                "El intento quedó cerrado y no existe retry POST."
                            ),
                        }
                    )
                    return result
                phase, headline, detail = _terminal_operator_result(recovery)
                result.update({"ok": True, "phase": phase, "headline": headline, "detail": detail})
                return result

            if recovery.get("auto_settlement_exhausted") is True:
                result.update(
                    {
                        "phase": "RECOVERY_ONLY",
                        "headline": "Broker aún no confirma · no reintentar",
                        "detail": (
                            f"AUTO-TRADE hizo {attempts} reconciliaciones GET-only automáticas y todavía no existe "
                            "verdad terminal. El POST quedó quemado; la única acción posible es continuar "
                            "reconciliando este mismo intento."
                        ),
                    }
                )
                return result

        execution = result.get("execution")
        if isinstance(execution, dict):
            parsed = execution.get("json")
            if isinstance(parsed, dict) and parsed.get("status") == "RECONCILED_FINAL":
                result.update(
                    {
                        "phase": "SETTLED",
                        "headline": "Canary PAPER terminado",
                        "detail": (
                            "El único POST PAPER terminó con reconciliación final en la misma ejecución. "
                            "Retry POST permanece bloqueado."
                        ),
                    }
                )
        return result


def _workspace_account_id(workspace_path) -> str:
    workspace = PaperOperationalWorkspace(root=workspace_path.expanduser().resolve())
    path = workspace.account_attestation_path
    if path.is_symlink() or not path.is_file():
        raise base.UnifiedCanaryError("verified PAPER account evidence is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise base.UnifiedCanaryError("verified PAPER account evidence is unreadable") from exc
    account_id = payload.get("account_id") if isinstance(payload, dict) else None
    if not isinstance(account_id, str) or not account_id.strip():
        raise base.UnifiedCanaryError("workspace PAPER account ID is missing")
    return account_id.strip()


def _broker_rejection(result: dict[str, object]) -> str | None:
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return None
    parsed = execution.get("json")
    if not isinstance(parsed, dict):
        return None
    diagnostic = parsed.get("broker_diagnostic")
    if not isinstance(diagnostic, dict):
        return None
    message = diagnostic.get("writer_error")
    if not isinstance(message, str):
        return None
    marker = "Alpaca PAPER order response rejected;"
    if marker not in message:
        return None
    safe = " ".join(message.strip().split())[:600]
    return safe


def _terminal_operator_result(recovery: dict[str, object]) -> tuple[str, str, str]:
    parsed = recovery.get("json")
    if not isinstance(parsed, dict):
        return (
            "SETTLED",
            "Canary PAPER reconciliado",
            "AUTO-TRADE obtuvo una resolución durable por GET. Retry POST permanece bloqueado.",
        )

    status = str(parsed.get("status") or "").upper()
    lifecycle = str(
        parsed.get("resulting_lifecycle_status") or parsed.get("lifecycle_status") or ""
    ).upper()

    if "FLAT" in status or lifecycle == "FLAT_RECONCILED":
        return (
            "SETTLED_FLAT",
            "Canary PAPER cerrado · sin exposición",
            "La reconciliación confirmó posición BTC/USD igual a cero y el intento quedó cerrado sin retry POST.",
        )
    if "FILLED" in status or "FILLED" in lifecycle:
        return (
            "SETTLED_FILLED",
            "Canary PAPER ejecutado",
            "Broker truth confirmó la ejecución del canary PAPER. Retry POST permanece bloqueado.",
        )
    if any(token in status or token in lifecycle for token in ("CANCELED", "CANCELLED", "EXPIRED")):
        return (
            "SETTLED_CANCELED",
            "Canary PAPER cerrado sin fill",
            "El broker confirmó un resultado terminal cancelado/expirado. No existe retry POST.",
        )
    return (
        "SETTLED",
        "Canary PAPER reconciliado",
        "AUTO-TRADE obtuvo broker truth terminal por GET. Retry POST permanece bloqueado.",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-window Mac first-canary app with queue recovery and bounded GET-only automatic settlement."
        )
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
    app = AutoSettlementSession()
    server = base.UnifiedServer((args.host, args.port), app)
    url = f"http://{args.host}:{args.port}/"
    print("AUTO-TRADE · Primer Canary PAPER · UNA SOLA APP")
    print(f"Abrir: {url}")
    print(
        "PAPER ONLY · BTC/USD BUY LIMIT IOC · USD 1-5 · CRYPTO STATUS ACTIVE REQUIRED · "
        "AUTO SETTLEMENT GET-ONLY · LIVE BLOCKED · RETRY POST FALSE"
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
