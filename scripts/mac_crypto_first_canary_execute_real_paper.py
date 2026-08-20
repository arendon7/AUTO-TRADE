from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import autotrade.first_canary_execution_gate as first_canary_execution_gate
import autotrade.first_canary_external_post_consent as first_canary_external_post_consent
import autotrade.brokers.alpaca_paper_crypto_cold_start_execution_bridge as cold_start_execution_bridge
import autotrade.brokers.alpaca_paper_crypto_cold_start_final_guard as cold_start_final_guard
from autotrade.first_canary_paper_policy import (
    FIRST_CANARY_PAPER_MAX_NOTIONAL,
    FIRST_CANARY_PAPER_MIN_NOTIONAL,
)
from autotrade.first_canary_real_paper_execution import (
    collect_fresh_final_evidence,
    execute_real_paper_first_canary_once,
)
from autotrade.brokers.alpaca_paper_crypto_account_status import attest_active_crypto_account
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import FirstCanaryAttemptWorkspace
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"


class MacFirstCanaryRealPaperExecutionError(RuntimeError):
    pass


def _credentials() -> AlpacaPaperCredentials:
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        raise MacFirstCanaryRealPaperExecutionError(
            "PAPER Key + Secret are required for the exact first-canary execution"
        )
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def _confirmation_from_stdin() -> str:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        raise MacFirstCanaryRealPaperExecutionError(
            "execution requires a JSON stdin body containing the exact confirmation"
        ) from exc
    if not isinstance(payload, dict):
        raise MacFirstCanaryRealPaperExecutionError("execution stdin root must be an object")
    confirmation = payload.get("confirmation")
    if not isinstance(confirmation, str) or not confirmation:
        raise MacFirstCanaryRealPaperExecutionError("exact execution confirmation is required")
    return confirmation


def _expected_account_id(workspace_path: Path) -> str:
    raw = workspace_path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise MacFirstCanaryRealPaperExecutionError("existing non-symlink PAPER workspace is required")
    workspace = PaperOperationalWorkspace(root=raw.resolve())
    path = workspace.account_attestation_path
    if path.is_symlink() or not path.is_file():
        raise MacFirstCanaryRealPaperExecutionError("verified PAPER account evidence is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MacFirstCanaryRealPaperExecutionError("verified PAPER account evidence is unreadable") from exc
    account_id = payload.get("account_id") if isinstance(payload, dict) else None
    if not isinstance(account_id, str) or not account_id.strip():
        raise MacFirstCanaryRealPaperExecutionError("workspace PAPER account ID is missing")
    return account_id.strip()


def _broker_diagnostic(workspace_path: Path, attempt_id: str) -> dict[str, object] | None:
    try:
        attempt = FirstCanaryAttemptWorkspace.open(
            workspace_path=workspace_path.expanduser().resolve(),
            attempt_id=attempt_id,
        )
        document = attempt.read(path=attempt.execution_result_path)
    except Exception:
        return None
    error_type = document.get("writer_error_type")
    error = document.get("writer_error")
    if not isinstance(error_type, str) and not isinstance(error, str):
        return None
    return {
        "writer_error_type": error_type if isinstance(error_type, str) else None,
        "writer_error": error if isinstance(error, str) else None,
        "broker_post_outcome": document.get("broker_post_outcome"),
        "durable_lifecycle_status": document.get("durable_lifecycle_status"),
    }


def _bind_isolated_execution_policy() -> None:
    # This process is the dedicated one-shot PAPER canary gate. The baseline
    # first-canary module historically encoded USD 1-5; Alpaca's current
    # crypto/USD broker floor is USD 10. Bind only this isolated PAPER process
    # to the certified USD 10-12 policy. LIVE and generic writer authority stay
    # unchanged/blocked.
    bindings = (
        (first_canary_execution_gate, "MIN_NOTIONAL", "MAX_NOTIONAL"),
        (first_canary_external_post_consent, "MIN_NOTIONAL", "MAX_NOTIONAL"),
        (cold_start_execution_bridge, "COLD_START_MIN_NOTIONAL", "COLD_START_MAX_NOTIONAL"),
        (cold_start_final_guard, "COLD_START_MIN_NOTIONAL", "COLD_START_MAX_NOTIONAL"),
    )
    for module, minimum_name, maximum_name in bindings:
        setattr(module, minimum_name, FIRST_CANARY_PAPER_MIN_NOTIONAL)
        setattr(module, maximum_name, FIRST_CANARY_PAPER_MAX_NOTIONAL)

    if any(
        getattr(module, minimum_name) != FIRST_CANARY_PAPER_MIN_NOTIONAL
        or getattr(module, maximum_name) != FIRST_CANARY_PAPER_MAX_NOTIONAL
        for module, minimum_name, maximum_name in bindings
    ):
        raise MacFirstCanaryRealPaperExecutionError(
            "isolated PAPER execution notional policy failed closed"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one already-prepared BTC/USD Alpaca PAPER technical canary. "
            "This command is intentionally separate from the generic Control Center and can cross one real PAPER POST only after a second exact human challenge."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--allow-exact-paper-post", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_exact_paper_post:
        raise SystemExit(
            "real first-canary PAPER execution requires explicit --allow-exact-paper-post"
        )
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise SystemExit(
            "generic R6_EXTERNAL_PAPER_WRITE must remain disabled; this gate uses only exact one-shot consent"
        )

    try:
        _bind_isolated_execution_policy()
        credentials = _credentials()
        confirmation = _confirmation_from_stdin()

        # Current Alpaca account truth distinguishes equity status from crypto_status.
        # Require crypto_status=ACTIVE by GET before any POST authority can be crossed.
        crypto_account = attest_active_crypto_account(
            credentials=credentials,
            expected_account_id=_expected_account_id(args.workspace),
            now=datetime.now(timezone.utc),
        )

        # Collect final broker state after the crypto-status check. A fresh wall
        # clock is taken here so the five-second Final Guard budget starts from
        # this actual final evidence sequence, not from the earlier status GET.
        preflight_at = datetime.now(timezone.utc)
        final_evidence = collect_fresh_final_evidence(
            workspace_path=args.workspace,
            credentials=credentials,
            now=preflight_at,
        )
        execution_at = datetime.now(timezone.utc)

        consent, outcome = execute_real_paper_first_canary_once(
            workspace_path=args.workspace,
            attempt_id=str(args.attempt_id),
            credentials=credentials,
            confirmation=confirmation,
            now=execution_at,
            final_evidence=final_evidence,
        )
        result = {
            "status": outcome.status,
            "attempt_id": outcome.attempt_id,
            "client_order_id": outcome.client_order_id,
            "execution_started_hash": outcome.execution_started_hash,
            "execution_result_hash": outcome.execution_result_hash,
            "reconciliation_hash": outcome.reconciliation_hash,
            "lifecycle_status": outcome.lifecycle_status,
            "broker_post_outcome": outcome.broker_post_outcome,
            "broker_diagnostic": _broker_diagnostic(args.workspace, str(args.attempt_id)),
            "crypto_status": crypto_account.crypto_status,
            "crypto_account_status_fingerprint": crypto_account.fingerprint,
            "paper_notional_min_usd": str(FIRST_CANARY_PAPER_MIN_NOTIONAL),
            "paper_notional_max_usd": str(FIRST_CANARY_PAPER_MAX_NOTIONAL),
            "retry_forbidden": outcome.retry_forbidden,
            "external_post_consent_hash": consent.receipt_hash,
            "external_post_consent_expires_at": consent.expires_at.isoformat(),
            "broker_write_performed": outcome.broker_post_outcome
            in {"BROKER_RESPONSE_RECEIVED", "UNKNOWN_RECONCILIATION_REQUIRED"},
            "external_post_authorized": True,
            "credentials_persisted": False,
            "secret_persisted": False,
            "live_trading": "BLOCKED",
            "next_action": (
                "RECONCILE_GET_ONLY_NEVER_RETRY_POST"
                if outcome.status != "RECONCILED_FINAL"
                else "REVIEW_FINAL_RECONCILIATION"
            ),
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        result = {
            "status": "CRYPTO_PAPER_FIRST_CANARY_REAL_POST_BLOCKED_OR_AMBIGUOUS",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "retry_post": False,
            "reconciliation_get_only": True,
            "credentials_persisted": False,
            "secret_persisted": False,
            "live_trading": "BLOCKED",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
