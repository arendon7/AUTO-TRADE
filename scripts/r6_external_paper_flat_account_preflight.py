from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_flat_account import AlpacaPaperFlatAccountGateway
from autotrade.brokers.alpaca_paper_flat_account_evidence import PaperFlatAccountEvidenceStore
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalWorkspace,
    _read_json_object,
)


KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
WRITE_ENABLED = "ENABLED"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Perform exactly two GET-only Alpaca PAPER reads for first-canary flatness: "
            "open positions and open orders. No cancellation, liquidation or order write exists."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--allow-paper-flat-account-read",
        action="store_true",
        help="Required explicit opt-in to the two PAPER GET requests.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_flat_account_read:
        raise SystemExit("ERROR: explicit --allow-paper-flat-account-read is required")
    if os.environ.get(WRITE_ENV) == WRITE_ENABLED:
        raise SystemExit(
            "ERROR: flat-account preflight refuses to run while R6_EXTERNAL_PAPER_WRITE=ENABLED"
        )
    key_id = os.environ.get(KEY_ENV)
    secret_key = os.environ.get(SECRET_ENV)
    if not key_id or not secret_key:
        raise SystemExit("ERROR: Alpaca PAPER credentials must be supplied only through environment variables")

    workspace = PaperOperationalWorkspace(root=args.workspace.expanduser().resolve())
    if not workspace.root.is_dir():
        raise SystemExit("ERROR: workspace does not exist; create it with Mac Safe Start first")
    if not workspace.account_attestation_path.is_file():
        raise SystemExit("ERROR: account_attestation.json is required before flat-account preflight")
    account = _read_json_object(workspace.account_attestation_path)
    if account.get("environment") != "PAPER" or account.get("credentials_persisted") is not False:
        raise SystemExit("ERROR: persisted account evidence is not safe PAPER evidence")
    account_fingerprint = account.get("attestation_fingerprint")
    credential_reference = account.get("credential_reference")
    if not isinstance(account_fingerprint, str) or not isinstance(credential_reference, str):
        raise SystemExit("ERROR: persisted account evidence identifiers are invalid")

    credentials = AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)
    gateway = AlpacaPaperFlatAccountGateway(config=AlpacaPaperGatewayConfig(enabled=True))
    attestation = gateway.attest_flatness(
        credentials=credentials,
        account_attestation_fingerprint=account_fingerprint,
        expected_credential_reference=credential_reference,
        now=datetime.now(timezone.utc),
    )
    artifact = PaperFlatAccountEvidenceStore(workspace).write(attestation)
    report = {
        "workspace": str(workspace.root),
        "artifact": str(artifact),
        "environment": "PAPER",
        "network_methods": ["GET", "GET"],
        "network_paths": [attestation.positions_path, attestation.orders_path],
        "position_count": attestation.position_count,
        "open_order_count": attestation.open_order_count,
        "clean_for_first_canary": attestation.clean_for_first_canary,
        "credentials_persisted": False,
        "broker_mutation_performed": False,
        "execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "next_action": (
            "RUN_SEPARATE_GET_ONLY_IEX_MARKET_PREFLIGHT"
            if attestation.clean_for_first_canary
            else "STOP_AND_REVIEW_EXISTING_PAPER_EXPOSURE_MANUALLY"
        ),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if attestation.clean_for_first_canary else 3


if __name__ == "__main__":
    raise SystemExit(main())
