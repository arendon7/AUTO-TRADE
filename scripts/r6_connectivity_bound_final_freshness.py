from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.connectivity_execution_freshness_binding import (
    ConnectivityBoundFinalFreshnessGuard,
    ConnectivityExecutionFreshnessError,
)

_KEY_ENV = "APCA_API_KEY_ID"
_SECRET_ENV = "APCA_API_SECRET_KEY"
_WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Acquire Final Freshness only after the second CONNECTIVITY_CANARY human intent, "
            "then bind both authorities immutably. Exactly five PAPER/IEX GETs; no OMS staging or POST."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--allow-paper-final-freshness-read",
        action="store_true",
        help="Allow exactly five GETs: account, asset, positions, open orders and IEX snapshot.",
    )
    return parser


def _workspace(path: Path) -> PaperOperationalWorkspace:
    root = path.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise SystemExit("connectivity workspace must be an existing non-symlink directory")
    return PaperOperationalWorkspace(root=root)


def _credentials() -> AlpacaPaperCredentials:
    key_id = os.environ.get(_KEY_ENV)
    secret_key = os.environ.get(_SECRET_ENV)
    if not key_id or not secret_key:
        raise SystemExit(
            "Alpaca PAPER credentials must exist only in APCA_API_KEY_ID/APCA_API_SECRET_KEY"
        )
    return AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_final_freshness_read:
        raise SystemExit(
            "bound Final Freshness is disabled unless --allow-paper-final-freshness-read is explicit"
        )
    if os.environ.get(_WRITE_ENV) == "ENABLED":
        raise SystemExit(
            "bound Final Freshness refuses R6_EXTERNAL_PAPER_WRITE=ENABLED; this phase is GET-only"
        )
    workspace = _workspace(args.workspace)
    credentials = _credentials()
    try:
        result = ConnectivityBoundFinalFreshnessGuard(workspace).acquire(
            credentials=credentials
        )
    except (ConnectivityExecutionFreshnessError, TypeError, ValueError) as exc:
        raise SystemExit(f"bound Final Freshness rejected: {exc}") from exc

    print(
        json.dumps(
            {
                "status": "ISSUED",
                "purpose": "CONNECTIVITY_CANARY",
                "order_id": result.binding.order_id,
                "execution_intent_decision_hash": result.binding.execution_intent_decision_hash,
                "final_freshness_permit_hash": result.binding.final_freshness_permit_hash,
                "binding_hash": result.binding.binding_hash,
                "issued_at": result.binding.issued_at.isoformat(),
                "expires_at": result.binding.expires_at.isoformat(),
                "network_methods": ["GET", "GET", "GET", "GET", "GET"],
                "network_read_count": 5,
                "max_external_post_attempts": 1,
                "oms_staging_authorized": False,
                "external_post_authorized": False,
                "external_order_submitted": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
                "next_action": "CONNECTIVITY_STAGING_BRIDGE_REQUIRED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
