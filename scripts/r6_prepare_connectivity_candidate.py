from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_connectivity_prepare import PaperConnectivityPreparationBridge
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace

_WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
_KEY_ENV = "APCA_API_KEY_ID"
_SECRET_ENV = "APCA_API_SECRET_KEY"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Credential-free/network-free CONNECTIVITY_CANARY preparation; stops before operator/POST.")
    parser.add_argument("--workspace", required=True, type=Path)
    return parser


def _workspace(path: Path) -> PaperOperationalWorkspace:
    expanded = path.expanduser()
    if expanded.is_symlink() or not expanded.is_dir():
        raise SystemExit("connectivity workspace must be an existing non-symlink directory")
    return PaperOperationalWorkspace(root=expanded.resolve())


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.environ.get(_WRITE_ENV) == "ENABLED":
        raise SystemExit("Refusing connectivity preparation while R6_EXTERNAL_PAPER_WRITE=ENABLED; disable the write gate first.")
    if os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV):
        raise SystemExit("Refusing connectivity preparation while Alpaca credentials are present; this phase is deliberately credential-free.")
    workspace = _workspace(args.workspace)
    result = PaperConnectivityPreparationBridge(workspace).prepare(now=datetime.now(timezone.utc))
    output = {
        "status": "CONNECTIVITY_CANARY_PREPARED", "workspace": str(workspace.root), "artifact": str(result.artifact_path),
        "order_id": result.order_id, "attempt_id": result.attempt_id,
        "connectivity_authority_id": result.connectivity_authority_id,
        "connectivity_binding_id": result.connectivity_binding_id,
        "standard_package_hash": result.standard_package_hash,
        "expected_bracket_payload_hash": result.bracket_payload_hash,
        "preparation_hash": result.preparation_hash,
        "core_db_sha256_after_preparation": result.core_db_sha256_after_preparation,
        "network_used": False, "credentials_used": False, "strategy_health_required": False,
        "strategy_health_created": False, "strategy_trading_authorized": False,
        "operator_authority_created": False, "external_post_authorized": False,
        "external_order_submitted": False, "capital_authority": "NONE", "profitability_claim": False,
        "live_trading": "BLOCKED", "next_action": "CONNECTIVITY_OPERATOR_BRIDGE_REQUIRED",
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
