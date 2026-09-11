from __future__ import annotations

import ast
from pathlib import Path

from labs.oss3_market_data.dual_holdout_acquisition import (
    D3D_PLAN_FINGERPRINT,
    EXPECTED_DESCRIPTOR_COUNT,
    EXPECTED_ECONOMIC_DESCRIPTORS,
    EXPECTED_PREDICTIVE_DESCRIPTORS,
    ECONOMIC_PURPOSE,
    PREDICTIVE_PURPOSE,
)
from labs.oss3_qlib.dual_holdout_acquisition_preregistration import canonical_oss3d3d_dual_holdout_plan


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ROOT / "labs/oss3_market_data/dual_holdout_acquisition.py",
    ROOT / "scripts/run_oss3d3e_dual_holdout_acquisition.py",
)

FORBIDDEN_EXACT_IMPORTS = {
    "qlib",
    "pandas",
    "numpy",
    "scipy",
    "sklearn",
}
FORBIDDEN_IMPORT_FRAGMENTS = (
    "final_holdout_evaluator",
    "economic_holdout_evaluator",
    "predictive_economic_protocol",
    "predictive_strategy_contract",
    "broker",
    "oms",
    "execution_engine",
    "order_intent",
)
FORBIDDEN_CALLS = {
    "evaluate_final_holdout",
    "evaluate_economic_holdout",
    "evaluate_development_predictions",
    "materialize_development_labels_after_preregistration",
    "consume_holdout_permit",
    "issue_holdout_permit",
    "submit_order",
    "place_order",
    "execute_order",
}
FORBIDDEN_TEXT = (
    "qlib.init(",
    "retuning_allowed=true",
    "reselection_allowed=true",
    "scientific_holdout_evaluation_observed=true",
    "feature_values_materialized=true",
    "label_values_materialized=true",
    "prediction_values_materialized=true",
    "metrics_computed=true",
    "paper_execution_authorized=true",
    'capital_authority="live"',
    'live_trading="enabled"',
)


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def main() -> int:
    for path in SOURCES:
        if not path.is_file():
            raise SystemExit(f"D3E boundary source missing: {path.relative_to(ROOT)}")
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for module in modules:
                lowered = module.lower()
                root_name = lowered.split(".", 1)[0]
                if root_name in FORBIDDEN_EXACT_IMPORTS or any(fragment in lowered for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                    raise SystemExit(f"D3E forbidden import {module!r} in {path.relative_to(ROOT)}")
            if isinstance(node, ast.Call) and _call_name(node) in FORBIDDEN_CALLS:
                raise SystemExit(f"D3E forbidden call {_call_name(node)!r} in {path.relative_to(ROOT)}")
        lowered_text = text.lower().replace(" ", "")
        for marker in FORBIDDEN_TEXT:
            if marker in lowered_text:
                raise SystemExit(f"D3E forbidden authority/value marker {marker!r} in {path.relative_to(ROOT)}")

    plan = canonical_oss3d3d_dual_holdout_plan()
    assert plan.fingerprint == D3D_PLAN_FINGERPRINT
    assert len(plan.predictive.descriptors) == EXPECTED_PREDICTIVE_DESCRIPTORS
    assert len(plan.economic.descriptors) == EXPECTED_ECONOMIC_DESCRIPTORS
    assert len(plan.predictive.descriptors) + len(plan.economic.descriptors) == EXPECTED_DESCRIPTOR_COUNT
    assert plan.predictive.purpose == PREDICTIVE_PURPOSE
    assert plan.economic.purpose == ECONOMIC_PURPOSE
    assert not set(plan.predictive.descriptor_fingerprints) & set(plan.economic.descriptor_fingerprints)
    assert plan.final_holdout_observed is False
    assert plan.economic_holdout_observed is False
    assert plan.holdout_permit_issued is False
    assert plan.holdout_permit_consumed is False
    assert plan.capital_authority == "NONE"
    assert plan.live_trading == "BLOCKED"

    print(
        "OSS-3D3E DUAL HOLDOUT RAW ACQUISITION BOUNDARY PASS — exact certified D3D 18-descriptor family; "
        "public GET + D2T integrity only; raw bytes may be sealed but no features, labels, predictions, metrics, "
        "D2J/D2M, D2K/D2N, holdout permit, PAPER, capital or LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
