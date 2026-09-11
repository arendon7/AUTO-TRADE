#!/usr/bin/env python3
"""Operator entry point for OSS-3D3E raw dual-holdout acquisition."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from labs.oss3_market_data.dual_holdout_acquisition import (
    SQLiteD3EAcquisitionLedger,
    require_exact_d3d_registry_read_only,
    run_dual_holdout_acquisition,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify/acquire exactly the D3D-preregistered Q1 predictive + Q2 economic "
            "Binance monthly archives. Default mode is offline verification/status."
        )
    )
    parser.add_argument("--d3d-registry", required=True, help="Certified D3D preregistration SQLite file")
    parser.add_argument("--evidence-root", required=True, help="Evidence directory outside the AUTO-TRADE git repository")
    parser.add_argument(
        "--execute-public-get",
        action="store_true",
        help="Enable exact bounded public GET acquisition for missing D3D descriptors",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    require_exact_d3d_registry_read_only(args.d3d_registry)
    result = run_dual_holdout_acquisition(
        d3d_registry_path=args.d3d_registry,
        evidence_root=Path(args.evidence_root),
        now=datetime.now(timezone.utc),
        allow_network=bool(args.execute_public_get),
    )
    ledger = SQLiteD3EAcquisitionLedger(Path(args.evidence_root) / "oss3d3e-dual-holdout-acquisition.sqlite3")
    seals = ledger.descriptor_seals()
    payload = {
        "result_version": result.result_version,
        "d3d_plan_fingerprint": result.d3d_plan_fingerprint,
        "descriptor_count": result.descriptor_count,
        "sealed_descriptor_count": len(seals),
        "predictive_sealed_count": sum(item.purpose == "PREDICTIVE_FINAL_HOLDOUT" for item in seals),
        "economic_sealed_count": sum(item.purpose == "ECONOMIC_HOLDOUT" for item in seals),
        "acquired_from_network": result.acquired_from_network,
        "reused_after_full_reverification": result.reused_after_full_reverification,
        "reconciled_unsealed_final_material": result.reconciled_unsealed_final_material,
        "missing_without_network_authority": result.missing_without_network_authority,
        "complete": result.complete,
        "campaign_seal_fingerprint": result.campaign_seal_fingerprint,
        "network_enabled": result.network_enabled,
        "expected_get_count_if_fresh": result.expected_get_count_if_fresh,
        "scientific_holdout_evaluation_observed": result.scientific_holdout_evaluation_observed,
        "feature_values_materialized": False,
        "label_values_materialized": False,
        "prediction_values_materialized": False,
        "metrics_computed": False,
        "d2j_or_d2m_commitment_created": False,
        "holdout_permit_issued": False,
        "holdout_permit_consumed": False,
        "capital_authority": result.capital_authority,
        "live_trading": result.live_trading,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if result.complete or not args.execute_public_get else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"OSS-3D3E dual holdout acquisition failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
