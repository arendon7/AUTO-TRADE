from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from autotrade.brokers.alpaca_paper_market_readiness import (
    PaperReadinessError,
    inspect_market_aware_readiness,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect one local R6 PAPER workspace read-only. This command never loads "
            "broker credentials, never performs network I/O, never mints human authority, "
            "and never submits an order."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    return parser


def _workspace(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink() or not expanded.is_dir():
        raise ValueError("workspace must be an existing non-symlink directory")
    return expanded.resolve()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = _workspace(args.workspace)
        report = inspect_market_aware_readiness(
            root=root,
            now=datetime.now(timezone.utc),
        )
    except (PaperReadinessError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": str(exc),
                    "network_used": False,
                    "broker_write_performed": False,
                    "execution_authorized": False,
                    "capital_authority": "NONE",
                    "profitability_claim": False,
                    "live_trading": "BLOCKED",
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
