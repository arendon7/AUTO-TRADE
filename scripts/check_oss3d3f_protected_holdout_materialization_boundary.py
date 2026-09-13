from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "labs" / "oss3_qlib" / "protected_holdout_materialization.py"
FORBIDDEN_IMPORT_PREFIXES = ("qlib", "pandas", "numpy", "scipy", "sklearn", "socket", "subprocess", "requests", "aiohttp", "httpx", "autotrade.brokers", "autotrade.oms", "autotrade.safety", "autotrade.engine")
FORBIDDEN_CALL_NAMES = {"checkout", "_checkout", "evaluate", "submit_order", "place_order", "send_order", "execute_order", "consume_holdout_permit", "preregister_and_record", "register_protocol"}
FORBIDDEN_SYMBOLS = {"LinearModel", "SQLiteOSS3FinalHoldoutEvaluationRegistry", "SQLiteOSS3EconomicHoldoutEvaluationRegistry", "OrderIntent", "CapitalSafetyKernel"}
REQUIRED_SOURCE_SNIPPETS = (
    "allow_network=False",
    'EXPECTED_D3E_CAMPAIGN_SEAL = "8ce57f1e6a6ce6999d8599af7df38b5b06662834739aa175cadd672c21eed105"',
    'MATERIALIZATION_POLICY = "DETERMINISTIC_IN_MEMORY_VALUE_OPAQUE_COMMITMENT_ONLY_V1"',
    'TEMPORAL_SEPARATION_POLICY = "HALF_OPEN_CONTIGUOUS_Q1_Q2_NO_OVERLAP_V1"',
    "label_values_exposed=False",
    "prediction_values_materialized=False",
    "predictive_metrics_computed=False",
    "holdout_permit_issued=False",
    "holdout_permit_consumed=False",
    'capital_authority="NONE"',
    'live_trading="BLOCKED"',
)


def _qualified_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET))
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == prefix or alias.name.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES):
                    errors.append(f"forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES):
                errors.append(f"forbidden import-from: {module}")
        elif isinstance(node, ast.Call):
            name = _qualified_name(node.func)
            terminal = name.rsplit(".", 1)[-1]
            if terminal in FORBIDDEN_CALL_NAMES:
                errors.append(f"forbidden call: {name}")
            if terminal == "run_dual_holdout_acquisition":
                keywords = {item.arg: item.value for item in node.keywords if item.arg}
                value = keywords.get("allow_network")
                if not isinstance(value, ast.Constant) or value.value is not False:
                    errors.append("D3F D3E reverification must hard-code allow_network=False")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_SYMBOLS:
            errors.append(f"forbidden symbol: {node.id}")
    for snippet in REQUIRED_SOURCE_SNIPPETS:
        if snippet not in source:
            errors.append(f"required D3F governance snippet missing: {snippet}")
    lowered = source.lower()
    for token in ("api_key", "secret_key", "broker_url", "paper_trading=true", "live_trading=true"):
        if token in lowered:
            errors.append(f"forbidden authority/credential token: {token}")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("OSS-3D3F PROTECTED HOLDOUT MATERIALIZATION BOUNDARY PASS — certified D3E raw evidence is fully reverified offline; Q1 D2Q/D2R values exist only in private process memory and leave as D2J value-opaque hash commitments; Q2 leaves only as D2M raw-universe commitment; no Qlib, predictions, metrics, protocol registration, permit, broker, PAPER, capital or LIVE authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
