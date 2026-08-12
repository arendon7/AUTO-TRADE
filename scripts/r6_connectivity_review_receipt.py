from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.connectivity_operator_review import (
    ConnectivityOperatorReviewError,
    ConnectivityOperatorReviewReceiptBuilder,
)

_WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
_KEY_ENV = "APCA_API_KEY_ID"
_SECRET_ENV = "APCA_API_SECRET_KEY"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze the exact credential-free human review receipt for one prepared CONNECTIVITY_CANARY. "
            "No broker I/O, OMS staging or POST is possible from this command."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    return parser


def _workspace(path: Path) -> PaperOperationalWorkspace:
    root = path.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise SystemExit("connectivity workspace must be an existing non-symlink directory")
    return PaperOperationalWorkspace(root=root)


def _validate_offline_environment() -> None:
    if os.environ.get(_WRITE_ENV) == "ENABLED":
        raise SystemExit("operator review receipt refuses R6_EXTERNAL_PAPER_WRITE=ENABLED")
    if os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV):
        raise SystemExit("operator review receipt refuses Alpaca credentials; this step is offline")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_offline_environment()
    workspace = _workspace(args.workspace)
    try:
        receipt = ConnectivityOperatorReviewReceiptBuilder(workspace).build(
            now=datetime.now(timezone.utc)
        )
    except (ConnectivityOperatorReviewError, TypeError, ValueError) as exc:
        raise SystemExit(f"operator review receipt rejected: {exc}") from exc

    body = receipt.body
    print(
        json.dumps(
            {
                "status": "REVIEW_RECEIPT_FROZEN",
                "purpose": "CONNECTIVITY_CANARY",
                "order_id": receipt.order_id,
                "client_order_id": receipt.client_order_id,
                "attempt_id": receipt.attempt_id,
                "symbol": body["symbol"],
                "side": body["side"],
                "quantity": body["quantity"],
                "order_type": body["order_type"],
                "limit_price": body["limit_price"],
                "take_profit_price": body["take_profit_price"],
                "stop_loss_price": body["stop_loss_price"],
                "notional": body["notional"],
                "effective_notional_cap": body["effective_notional_cap"],
                "market_bid": body["market_bid"],
                "market_ask": body["market_ask"],
                "market_last": body["market_last"],
                "flat_position_count": body["flat_position_count"],
                "flat_open_order_count": body["flat_open_order_count"],
                "receipt_hash": receipt.receipt_hash,
                "credentials_used": False,
                "network_used": False,
                "oms_staging_authorized": False,
                "external_post_authorized": False,
                "external_order_submitted": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
                "next_action": "SECOND_HUMAN_EXECUTION_INTENT_REQUIRED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
