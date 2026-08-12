from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.connectivity_final_freshness import (
    ConnectivityFinalFreshnessError,
    ConnectivityFinalFreshnessGuard,
)

_KEY_ENV = "APCA_API_KEY_ID"
_SECRET_ENV = "APCA_API_SECRET_KEY"
_WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reacquire the exact GET-only PAPER evidence after CONNECTIVITY_CANARY human approval, "
            "run fresh Capital Safety and issue a <=5s execution-eligibility attestation. "
            "This command cannot stage OMS or POST an order."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--allow-paper-final-freshness-read",
        action="store_true",
        help=(
            "Explicitly allow exactly five GET requests: account, asset, positions, open orders and IEX snapshot."
        ),
    )
    return parser


def _credentials() -> AlpacaPaperCredentials:
    key_id = os.environ.get(_KEY_ENV)
    secret_key = os.environ.get(_SECRET_ENV)
    if not key_id or not secret_key:
        raise SystemExit(
            f"Alpaca PAPER credentials must exist only in {_KEY_ENV}/{_SECRET_ENV}; CLI credential arguments are forbidden"
        )
    return AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)


def _workspace(path: Path) -> PaperOperationalWorkspace:
    root = path.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise SystemExit("connectivity workspace must be an existing non-symlink directory")
    return PaperOperationalWorkspace(root=root)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_final_freshness_read:
        raise SystemExit(
            "final freshness network access is disabled unless --allow-paper-final-freshness-read is explicit"
        )
    if os.environ.get(_WRITE_ENV) == "ENABLED":
        raise SystemExit(
            "final freshness refuses R6_EXTERNAL_PAPER_WRITE=ENABLED; this phase is GET-only"
        )
    workspace = _workspace(args.workspace)
    credentials = _credentials()
    try:
        result = ConnectivityFinalFreshnessGuard(workspace).acquire(credentials=credentials)
    except (ConnectivityFinalFreshnessError, TypeError, ValueError) as exc:
        raise SystemExit(f"connectivity final freshness rejected: {exc}") from exc
    print(
        json.dumps(
            {
                "status": result.state.status.value,
                "purpose": "CONNECTIVITY_CANARY",
                "order_id": result.permit.order_id,
                "permit_hash": result.permit.permit_hash,
                "fresh_risk_decision_id": result.permit.fresh_risk_decision_id,
                "fresh_risk_decision_fingerprint": result.permit.fresh_risk_decision_fingerprint,
                "issued_at": result.permit.issued_at.isoformat(),
                "expires_at": result.permit.expires_at.isoformat(),
                "network_methods": ["GET", "GET", "GET", "GET", "GET"],
                "network_read_count": 5,
                "credentials_persisted": False,
                "oms_staging_authorized": False,
                "external_post_authorized": False,
                "external_order_submitted": False,
                "strategy_trading_authorized": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
                "next_action": "EXPLICIT_CONNECTIVITY_EXECUTION_DECISION_REQUIRED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
