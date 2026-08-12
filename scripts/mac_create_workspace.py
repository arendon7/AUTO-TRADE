from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

from autotrade.brokers.alpaca_paper_market_readiness import inspect_market_aware_readiness
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
WRITE_ENABLED = "ENABLED"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create one local AUTO-TRADE R6 PAPER workspace without broker network, "
            "credentials, databases or execution authority."
        )
    )
    parser.add_argument("--workspace", type=Path, required=True)
    return parser


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    requested = args.workspace.expanduser()

    if os.environ.get(WRITE_ENV) == WRITE_ENABLED:
        raise SystemExit(
            "ERROR: workspace creation refuses to run while R6_EXTERNAL_PAPER_WRITE=ENABLED"
        )
    if os.environ.get(KEY_ENV) or os.environ.get(SECRET_ENV):
        raise SystemExit(
            "ERROR: workspace creation is credential-free; unset Alpaca credentials first"
        )

    # Resolve only after validating the requested leaf itself. Existing symlinks
    # are rejected by PaperOperationalWorkspace.initialize as well.
    resolved = requested.resolve(strict=False)
    if _is_within(resolved, repo.resolve()):
        raise SystemExit(
            "ERROR: operational workspaces must live outside the git repository"
        )

    if requested.exists():
        if requested.is_symlink() or not requested.is_dir():
            raise SystemExit("ERROR: workspace path must be a regular directory")
        if any(requested.iterdir()):
            raise SystemExit(
                "ERROR: refusing to initialize a non-empty directory; inspect the existing workspace instead"
            )

    workspace = PaperOperationalWorkspace.initialize(requested)
    mode = stat.S_IMODE(workspace.root.stat().st_mode)
    if mode & 0o077:
        raise SystemExit("ERROR: workspace permissions are not private")

    readiness = inspect_market_aware_readiness(
        root=workspace.root,
        now=datetime.now(timezone.utc),
    )
    if readiness.get("phase") != "ACCOUNT_PREFLIGHT_REQUIRED":
        raise SystemExit("ERROR: a new workspace did not start at ACCOUNT_PREFLIGHT_REQUIRED")

    report = {
        "workspace": str(workspace.root),
        "workspace_mode": f"{mode:04o}",
        "phase": readiness.get("phase"),
        "next_action": readiness.get("next_action"),
        "broker_network_used": False,
        "broker_write_performed": False,
        "execution_authorized": False,
        "credentials_used": False,
        "capital_authority": "NONE",
        "live_trading_status": "BLOCKED",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
