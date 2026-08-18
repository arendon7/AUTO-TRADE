from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from autotrade.first_canary_real_paper_execution import (
    execute_real_paper_first_canary_once,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials


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
        consent, outcome = execute_real_paper_first_canary_once(
            workspace_path=args.workspace,
            attempt_id=str(args.attempt_id),
            credentials=_credentials(),
            confirmation=_confirmation_from_stdin(),
            now=datetime.now(timezone.utc),
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
