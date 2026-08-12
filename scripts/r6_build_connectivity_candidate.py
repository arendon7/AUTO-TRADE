from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_connectivity_candidate import (
    PaperConnectivityCandidateBuilder,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


_WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
_KEY_ENV = "APCA_API_KEY_ID"
_SECRET_ENV = "APCA_API_SECRET_KEY"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build one local non-executable R6 CONNECTIVITY_CANARY candidate from "
            "already persisted GET-only PAPER evidence. No network, credentials, "
            "Strategy Health, operator authority or broker write is used."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.environ.get(_WRITE_ENV) == "ENABLED":
        raise SystemExit(
            "Refusing connectivity candidate build while R6_EXTERNAL_PAPER_WRITE=ENABLED; disable the write gate first."
        )
    if os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV):
        raise SystemExit(
            "Refusing connectivity candidate build while Alpaca credentials are present; this phase is deliberately credential-free."
        )

    root = args.workspace.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit("Workspace does not exist; create it and complete GET-only preflights first.")
    workspace = PaperOperationalWorkspace(root=root)
    result = PaperConnectivityCandidateBuilder(workspace).build(
        now=datetime.now(timezone.utc)
    )
    output = {
        "status": "CONNECTIVITY_CANDIDATE_BUILT",
        "workspace": str(root),
        "artifact": str(result.artifact_path),
        "order_id": result.order_id,
        "order_status": "VALIDATED",
        "quantity": str(result.quantity),
        "limit_price": str(result.limit_price),
        "effective_notional_cap": str(result.effective_notional_cap),
        "intent_fingerprint": result.intent_fingerprint,
        "risk_decision_fingerprint": result.risk_decision_fingerprint,
        "instrument_rules_fingerprint": result.instrument_rules_fingerprint,
        "connectivity_authority_id": result.authority_id,
        "connectivity_authority_hash": result.authority_hash,
        "candidate_hash": result.candidate_hash,
        "core_db_sha256": result.core_db_sha256,
        "network_used": False,
        "credentials_used": False,
        "strategy_health_required": False,
        "strategy_health_created": False,
        "strategy_trading_authorized": False,
        "operator_authority_created": False,
        "external_post_authorized": False,
        "external_order_submitted": False,
        "capital_authority": "NONE",
        "profitability_claim": False,
        "live_trading": "BLOCKED",
        "next_action": "CONNECTIVITY_PREPARATION_BRIDGE_REQUIRED",
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
