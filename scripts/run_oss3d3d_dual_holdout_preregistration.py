from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from labs.oss3_qlib.dual_holdout_acquisition_preregistration import (
    OSS3D3D_REGISTRY_TABLE,
    SQLiteDualHoldoutAcquisitionPlanRegistry,
    canonical_oss3d3d_dual_holdout_plan,
    write_dual_holdout_plan,
)


def require_exact_read_only(registry_path: str | Path) -> None:
    """Verify canonical D3D durable state without allowing SQLite mutation."""
    plan = canonical_oss3d3d_dual_holdout_plan()
    resolved = Path(registry_path).resolve()
    if not resolved.is_file():
        raise RuntimeError("D3D durable registry is missing")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute(
            f"SELECT fingerprint, canonical_json FROM {OSS3D3D_REGISTRY_TABLE} WHERE plan_id = ?",
            (plan.plan_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("canonical D3D plan is absent from durable registry")
        import json

        expected = json.dumps(
            plan.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        if str(row["fingerprint"]) != plan.fingerprint or str(row["canonical_json"]) != expected:
            raise RuntimeError("durable D3D registry differs from canonical plan")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Durably preregister exact Q1 predictive + Q2 economic holdout acquisition geometry"
    )
    parser.add_argument("--registry", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    plan = canonical_oss3d3d_dual_holdout_plan()
    registry = SQLiteDualHoldoutAcquisitionPlanRegistry(args.registry)
    registry.preregister(plan, now=datetime.now(timezone.utc))
    registry.require_exact(plan)
    require_exact_read_only(args.registry)
    write_dual_holdout_plan(plan, args.output)

    print("OSS-3D3D plan fingerprint:", plan.fingerprint)
    print("D3C certified head:", plan.source_d3c_certified_head)
    print("D3C artifact:", plan.source_d3c_artifact_hash)
    print("stable scientific outcome:", plan.source_d3c_scientific_outcome_hash)
    print("predictive FINAL_HOLDOUT:", plan.predictive.partition_start, "->", plan.predictive.partition_end)
    print("economic holdout reservation:", plan.economic.partition_start, "->", plan.economic.partition_end)
    print("predictive descriptors:", len(plan.predictive.descriptors))
    print("economic descriptors:", len(plan.economic.descriptors))
    print("network acquisition performed:", plan.network_acquisition_performed)
    print("FINAL_HOLDOUT observed:", plan.final_holdout_observed)
    print("ECONOMIC_HOLDOUT observed:", plan.economic_holdout_observed)
    print("capital authority:", plan.capital_authority)
    print("LIVE:", plan.live_trading)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
