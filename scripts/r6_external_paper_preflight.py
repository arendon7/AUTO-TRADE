from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Perform the R6 exact Alpaca PAPER account attestation GET and write a "
            "sanitized durable workspace artifact. This command has no order-write API."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--expected-account-id", required=True)
    parser.add_argument(
        "--allow-paper-account-read",
        action="store_true",
        help="Explicitly permit the single GET /v2/account PAPER attestation.",
    )
    return parser


def _credentials_from_environment() -> AlpacaPaperCredentials:
    key_id = os.environ.get(KEY_ENV, "")
    secret_key = os.environ.get(SECRET_ENV, "")
    if not key_id or not secret_key:
        raise SystemExit(
            f"PAPER credentials must exist only in environment variables {KEY_ENV} and {SECRET_ENV}"
        )
    return AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)


def run_account_preflight(
    *,
    workspace: Path,
    expected_account_id: str,
    credentials: AlpacaPaperCredentials,
    gateway: AlpacaPaperAccountGateway,
    now: datetime,
) -> dict[str, object]:
    operational = PaperOperationalWorkspace.initialize(workspace)
    attestation = gateway.attest_account(
        credentials=credentials,
        expected_account_id=expected_account_id,
        now=now,
    )
    artifact_path = operational.write_account_attestation(attestation)
    return {
        "status": "PAPER_ACCOUNT_ATTESTED",
        "environment": "PAPER",
        "account_id": attestation.account_id,
        "account_attestation_fingerprint": attestation.fingerprint,
        "credential_reference": attestation.credential_reference,
        "artifact": str(artifact_path),
        "network_method": "GET",
        "network_path": "/v2/account",
        "order_write_authorized": False,
        "external_order_submitted": False,
        "live_trading": "BLOCKED",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_account_read:
        raise SystemExit(
            "PAPER account network read is disabled unless --allow-paper-account-read is explicit"
        )
    credentials = _credentials_from_environment()
    gateway = AlpacaPaperAccountGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
    )
    result = run_account_preflight(
        workspace=args.workspace,
        expected_account_id=args.expected_account_id,
        credentials=credentials,
        gateway=gateway,
        now=datetime.now(timezone.utc),
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
