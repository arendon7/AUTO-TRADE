#!/usr/bin/env python3
"""Operator entry point for the frozen OSS-3D2V historical-data campaign."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from labs.oss3_market_data.real_acquisition_campaign import run_canonical_campaign


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify or execute the frozen D2U BTCUSDT/ETHUSDT/SOLUSDT 1h "
            "2023-01..2025-12 public archive campaign. Default mode is offline status/verification."
        )
    )
    parser.add_argument(
        "--evidence-root",
        required=True,
        help="Absolute/local evidence directory outside the AUTO-TRADE git repository.",
    )
    parser.add_argument(
        "--execute-public-get",
        action="store_true",
        help="Enable bounded public GET acquisition for descriptors that are not already verified locally.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.evidence_root).expanduser()
    result, material = run_canonical_campaign(
        evidence_root=root,
        now=datetime.now(timezone.utc),
        allow_network=bool(args.execute_public_get),
    )
    payload = {
        "result_version": result.result_version,
        "collection_id": result.collection_id,
        "plan_fingerprint": result.plan_fingerprint,
        "descriptor_count": result.descriptor_count,
        "acquired_from_network": result.acquired_from_network,
        "reused_after_seal_reverification": result.reused_after_seal_reverification,
        "reconciled_unsealed_final_material": result.reconciled_unsealed_final_material,
        "missing_without_network_authority": result.missing_without_network_authority,
        "complete": result.complete,
        "campaign_seal_fingerprint": result.campaign_seal_fingerprint,
        "network_enabled": bool(args.execute_public_get),
        "final_holdout_values_loaded": result.final_holdout_values_loaded,
        "capital_authority": result.capital_authority,
        "live_trading": result.live_trading,
    }
    if material is not None:
        payload["d2u_partition_material_fingerprint"] = material.fingerprint
        payload["training_universe_hash"] = material.training.universe_hash
        payload["development_universe_hash"] = material.development.universe_hash
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if result.complete or not args.execute_public_get else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"OSS-3D2V real acquisition failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
