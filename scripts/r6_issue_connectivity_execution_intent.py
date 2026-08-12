from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.connectivity_execution_intent import (
    ConnectivityExecutionIntentBridge,
    ConnectivityExecutionIntentError,
    connectivity_execution_intent_challenge,
)

_WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
_KEY_ENV = "APCA_API_KEY_ID"
_SECRET_ENV = "APCA_API_SECRET_KEY"
DEFAULT_TTL_SECONDS = 45
MAX_TTL_SECONDS = 90


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record the second explicit human intent for one bounded CONNECTIVITY_CANARY attempt. "
            "This command is credential-free, performs no broker I/O, does not stage OMS and cannot POST."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    return parser


def _workspace(path: Path) -> PaperOperationalWorkspace:
    root = path.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise SystemExit("connectivity workspace must be an existing non-symlink directory")
    return PaperOperationalWorkspace(root=root)


def _validate_local_only_environment() -> None:
    if os.environ.get(_WRITE_ENV) == "ENABLED":
        raise SystemExit("execution intent refuses R6_EXTERNAL_PAPER_WRITE=ENABLED")
    if os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV):
        raise SystemExit("execution intent refuses Alpaca credentials; this step is local-only")


def _ttl(value: int) -> int:
    if isinstance(value, bool) or value <= 0 or value > MAX_TTL_SECONDS:
        raise SystemExit(f"--ttl-seconds must be between 1 and {MAX_TTL_SECONDS}")
    return value


def _summary(context) -> str:
    return "\n".join(
        (
            "R6 CONNECTIVITY_CANARY — SECOND HUMAN EXECUTION INTENT",
            "Environment: PAPER",
            "Purpose: CONNECTIVITY_CANARY (NOT strategy trading)",
            f"Order ID: {context.order_id}",
            f"Client order ID: {context.client_order_id}",
            f"Notional: USD {context.notional}",
            "Budget: at most ONE future external POST attempt, only if all later gates pass.",
            "This confirmation itself does NOT stage OMS and does NOT authorize POST.",
            "After confirmation, inline Final Freshness (5 GETs + real Safety) is still mandatory.",
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_local_only_environment()
    ttl_seconds = _ttl(args.ttl_seconds)
    workspace = _workspace(args.workspace)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("execution intent requires an interactive TTY")

    bridge = ConnectivityExecutionIntentBridge(workspace)
    first_checked_at = datetime.now(timezone.utc)
    try:
        context = bridge.prepare_context(now=first_checked_at)
    except (ConnectivityExecutionIntentError, TypeError, ValueError) as exc:
        raise SystemExit(f"execution intent context is not eligible: {exc}") from exc

    print(_summary(context))
    challenge = connectivity_execution_intent_challenge(context)
    print(f"Type exactly: {challenge}")
    if input("> ") != challenge:
        raise SystemExit("execution intent challenge did not match; no authority recorded")

    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    try:
        state = bridge.issue(
            context=context,
            operator_id=args.operator_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
    except (ConnectivityExecutionIntentError, TypeError, ValueError) as exc:
        raise SystemExit(f"execution intent was not recorded: {exc}") from exc

    print(
        json.dumps(
            {
                "status": state.status.value,
                "purpose": "CONNECTIVITY_CANARY",
                "decision_hash": state.decision.decision_hash,
                "operator_id": state.decision.operator_id,
                "issued_at": state.decision.issued_at.isoformat(),
                "expires_at": state.decision.expires_at.isoformat(),
                "max_external_post_attempts": 1,
                "final_freshness_required": True,
                "oms_staging_authorized": False,
                "external_post_authorized": False,
                "external_order_submitted": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
                "next_action": "INLINE_FINAL_FRESHNESS_REQUIRED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
