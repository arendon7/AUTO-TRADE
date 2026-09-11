from __future__ import annotations

import ast
from pathlib import Path

from labs.oss3_qlib.dual_holdout_acquisition_preregistration import (
    ECONOMIC_END,
    ECONOMIC_START,
    PREDICTIVE_END,
    PREDICTIVE_START,
    SOURCE_D3C_ARTIFACT_HASH,
    SOURCE_D3C_CERTIFIED_HEAD,
    SOURCE_D3C_SCIENTIFIC_OUTCOME_HASH,
    canonical_oss3d3d_dual_holdout_plan,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ROOT / "labs/oss3_qlib/dual_holdout_acquisition_preregistration.py",
    ROOT / "scripts/run_oss3d3d_dual_holdout_preregistration.py",
)

FORBIDDEN_IMPORT_FRAGMENTS = (
    "requests",
    "httpx",
    "urllib",
    "socket",
    "aiohttp",
    "pandas",
    "numpy",
    "scipy",
    "sklearn",
    "final_holdout_evaluator",
    "economic_holdout_evaluator",
    "broker",
    "oms",
    "execution_engine",
    "order_intent",
    "holdout_permit",
)

FORBIDDEN_CALLS = {
    "urlopen",
    "create_connection",
    "run_isolated_qlib_family_candidate",
    "evaluate_final_holdout",
    "evaluate_economic_holdout",
    "evaluate_development_predictions",
    "materialize_development_labels_after_preregistration",
    "consume_holdout_permit",
    "submit_order",
    "place_order",
    "execute_order",
}

FORBIDDEN_TEXT = (
    "requests.get(",
    "requests.post(",
    "httpx.get(",
    "httpx.post(",
    "urllib.request",
    "socket.socket(",
    "qlib.init(",
    "zipfile.zipfile(",
    "archive_bytes",
    "checksum_bytes",
    "label_values_exposed = true",
    "market_values_exposed = true",
)


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _external_qlib_import(module: str) -> bool:
    lowered = module.lower()
    return lowered == "qlib" or lowered.startswith("qlib.")


def main() -> int:
    for path in SOURCES:
        if not path.is_file():
            raise SystemExit(f"D3D boundary source missing: {path.relative_to(ROOT)}")
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                modules = []
            for module in modules:
                lowered = module.lower()
                if _external_qlib_import(module) or any(
                    marker in lowered for marker in FORBIDDEN_IMPORT_FRAGMENTS
                ):
                    raise SystemExit(f"D3D forbidden import {module!r} in {path.relative_to(ROOT)}")
            if isinstance(node, ast.Call) and _call_name(node) in FORBIDDEN_CALLS:
                raise SystemExit(f"D3D forbidden call {_call_name(node)!r} in {path.relative_to(ROOT)}")
        lowered_text = text.lower()
        for marker in FORBIDDEN_TEXT:
            if marker in lowered_text:
                raise SystemExit(f"D3D forbidden runtime/value marker {marker!r} in {path.relative_to(ROOT)}")

    plan = canonical_oss3d3d_dual_holdout_plan()
    assert plan.source_d3c_certified_head == SOURCE_D3C_CERTIFIED_HEAD
    assert plan.source_d3c_artifact_hash == SOURCE_D3C_ARTIFACT_HASH
    assert plan.source_d3c_scientific_outcome_hash == SOURCE_D3C_SCIENTIFIC_OUTCOME_HASH
    assert plan.development_end == PREDICTIVE_START
    assert plan.predictive.partition_start == PREDICTIVE_START
    assert plan.predictive.partition_end == PREDICTIVE_END
    assert plan.economic.partition_start == ECONOMIC_START == PREDICTIVE_END
    assert plan.economic.partition_end == ECONOMIC_END
    assert len(plan.predictive.descriptors) == 9
    assert len(plan.economic.descriptors) == 9
    assert not set(plan.predictive.descriptor_fingerprints) & set(plan.economic.descriptor_fingerprints)
    assert plan.predictive.market_values_exposed is False
    assert plan.predictive.label_artifacts_materialized is False
    assert plan.predictive.prediction_values_materialized is False
    assert plan.predictive.metrics_computed is False
    assert plan.economic.market_values_exposed is False
    assert plan.economic.metrics_computed is False
    assert plan.network_acquisition_performed is False
    assert plan.final_holdout_observed is False
    assert plan.economic_holdout_observed is False
    assert plan.holdout_permit_issued is False
    assert plan.holdout_permit_consumed is False
    assert plan.execution_authorized is False
    assert plan.paper_execution_authorized is False
    assert plan.capital_authority == "NONE"
    assert plan.live_trading == "BLOCKED"

    print(
        "OSS-3D3D DUAL HOLDOUT PREREGISTRATION BOUNDARY PASS — "
        "Q1 predictive + Q2 economic windows frozen before network; "
        "18 structural descriptors only; no market bytes, labels, predictions, metrics, "
        "holdout permit, PAPER, capital or LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
