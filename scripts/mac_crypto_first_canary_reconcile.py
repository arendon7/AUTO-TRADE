from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from autotrade.first_canary_recovery import recover_first_canary
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials


WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"


class CryptoFirstCanaryRecoveryCliError(RuntimeError):
    pass


def _credentials() -> AlpacaPaperCredentials:
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        raise CryptoFirstCanaryRecoveryCliError(
            "PAPER Key + Secret are required for GET-only first-canary recovery"
        )
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "GET-only recovery/reconciliation for a burned BTC/USD PAPER first-canary attempt. "
            "This command has no writer/POST surface and can never authorize retry."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--allow-paper-recovery-read", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_recovery_read:
        raise SystemExit(
            "first-canary recovery requires explicit --allow-paper-recovery-read"
        )
    try:
        if os.environ.get(WRITE_ENV) == "ENABLED":
            raise CryptoFirstCanaryRecoveryCliError(
                "GET-only recovery refuses broker-write enabled environment"
            )
        result = recover_first_canary(
            workspace_path=args.workspace,
            attempt_id=str(args.attempt_id),
            credentials=_credentials(),
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        result = {
            "status": "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECOVERY_BLOCKED",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "retry_post": False,
            "recovery_get_only": True,
            "credentials_persisted": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
