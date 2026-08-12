from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.connectivity_operator_decision import (
    ConnectivityOperatorBridge,
    ConnectivityOperatorDecisionError,
    connectivity_operator_confirmation_challenge,
)

_WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
_KEY_ENV = "APCA_API_KEY_ID"
_SECRET_ENV = "APCA_API_SECRET_KEY"
DEFAULT_TTL_SECONDS = 60
MAX_TTL_SECONDS = 120


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record one explicit human decision for the exact CONNECTIVITY_CANARY preparation. "
            "This command is credential-free, does not stage OMS and cannot submit an order."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    return parser


def _workspace(path: Path) -> PaperOperationalWorkspace:
    if not path.exists() or path.is_symlink() or not path.is_dir():
        raise SystemExit("connectivity workspace must be an existing non-symlink directory")
    return PaperOperationalWorkspace(root=path.resolve())


def _validate_local_only_environment() -> None:
    if os.environ.get(_WRITE_ENV) == "ENABLED":
        raise SystemExit("connectivity operator decision refuses R6_EXTERNAL_PAPER_WRITE=ENABLED")
    if os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV):
        raise SystemExit("connectivity operator decision refuses Alpaca credentials; strip them first")


def _ttl(value: int) -> int:
    if isinstance(value, bool) or value <= 0 or value > MAX_TTL_SECONDS:
        raise SystemExit(f"--ttl-seconds must be between 1 and {MAX_TTL_SECONDS}")
    return value


def _summary(context) -> str:
    return "\n".join(
        (
            "R6 CONNECTIVITY_CANARY — HUMAN INTENT AUTHORIZATION",
            "Environment: PAPER",
            "Purpose: CONNECTIVITY_CANARY (NOT strategy trading)",
            f"Order ID: {context.order_id}",
            f"Client order ID: {context.client_order_id}",
            f"Notional: USD {context.notional}",
            f"Preparation: {context.connectivity_preparation_hash}",
            f"Connectivity binding: {context.connectivity_binding_hash}",
            f"Core after preparation: {context.core_db_sha256_after_preparation}",
            "This decision does NOT stage OMS, does NOT authorize POST and does NOT claim profitability.",
            "After approval, fresh broker/account/flat/market evidence is still required.",
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_local_only_environment()
    ttl_seconds = _ttl(args.ttl_seconds)
    workspace = _workspace(args.workspace)

    # A piped command, CI job, agent or background process must not even enter
    # the authority-building bridge. Require a real interactive terminal first.
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("connectivity human authorization requires an interactive TTY")

    bridge = ConnectivityOperatorBridge(workspace)
    first_checked_at = datetime.now(timezone.utc)
    try:
        context = bridge.prepare_context(now=first_checked_at)
    except (ConnectivityOperatorDecisionError, TypeError, ValueError) as exc:
        raise SystemExit(f"connectivity operator context is not eligible: {exc}") from exc

    print(_summary(context))
    challenge = connectivity_operator_confirmation_challenge(context)
    print(f"Type exactly: {challenge}")
    entered = input("> ")
    if entered != challenge:
        raise SystemExit("connectivity operator challenge did not match; no authority recorded")

    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    try:
        state = bridge.issue(
            context=context,
            operator_id=args.operator_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
    except (ConnectivityOperatorDecisionError, TypeError, ValueError) as exc:
        raise SystemExit(f"connectivity operator decision was not recorded: {exc}") from exc

    print(
        json.dumps(
            {
                "status": state.status.value,
                "purpose": "CONNECTIVITY_CANARY",
                "context_hash": context.context_hash,
                "decision_hash": state.decision.decision_hash,
                "operator_id": state.decision.operator_id,
                "issued_at": state.decision.issued_at.isoformat(),
                "expires_at": state.decision.expires_at.isoformat(),
                "oms_staging_authorized": False,
                "external_post_authorized": False,
                "external_order_submitted": False,
                "strategy_trading_authorized": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
                "next_action": "CONNECTIVITY_FINAL_FRESHNESS_REQUIRED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
