from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "research" / "oss3_development_evaluation.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "qlib",
    "pandas",
    "numpy",
    "scipy",
    "sklearn",
    "os",
    "socket",
    "subprocess",
    "requests",
    "aiohttp",
    "urllib",
    "http.client",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
)
FORBIDDEN_AUTHORITY_NAMES = {"OrderIntent", "RiskDecision", "CapitalSafetyKernel"}
FORBIDDEN_CALL_NAMES = {
    "eval",
    "exec",
    "__import__",
    "import_module",
    "system",
    "popen",
    "Popen",
    "urlopen",
    "submit_order",
    "place_order",
    "send_order",
    "execute_order",
}
FORBIDDEN_METRIC_NAMES = {
    "sharpe",
    "sortino",
    "profit",
    "pnl",
    "return_on_capital",
    "cagr",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        return _finish(["missing OSS-3D2D module"])
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET.relative_to(ROOT)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_module(alias.name):
                    errors.append(f"forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_module(module):
                errors.append(f"forbidden import: {module}")
            for alias in node.names:
                if alias.name in FORBIDDEN_AUTHORITY_NAMES:
                    errors.append(f"forbidden authority symbol: {alias.name}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALL_NAMES:
                errors.append(f"forbidden call: {name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_AUTHORITY_NAMES:
            errors.append(f"forbidden authority symbol: {node.id}")

    literal_keys = _literal_dict_keys(tree)
    metric_leaks = sorted(literal_keys & FORBIDDEN_METRIC_NAMES)
    if metric_leaks:
        errors.append(f"execution/PnL metric leaked into D2D artifact: {metric_leaks!r}")

    required = (
        'METRIC_POLICY_ID = "PREDICTIVE_QUALITY_NO_PNL_V1"',
        'KEY_POLICY_ID = "EXACT_TIMESTAMP_SYMBOL_LABEL_KEYSET_V1"',
        'if lm.partition != "DEVELOPMENT":',
        '("prediction_artifact_hash", receipt.prediction_artifact_hash, prediction.artifact_hash)',
        'prediction_keys != label_keys',
        'keyset_hash != receipt.inference_keyset_hash',
        'environment_attestation_hash=environment_attestation_hash',
        'execution_authorized: bool = False',
        'paper_execution_authorized: bool = False',
        'capital_authority: str = "NONE"',
        'live_trading: str = "BLOCKED"',
    )
    for snippet in required:
        if snippet not in source:
            errors.append(f"missing required D2D binding: {snippet}")

    if "FINAL_HOLDOUT" not in source:
        errors.append("D2D source must document FINAL_HOLDOUT exclusion")
    return _finish(errors)


def _finish(errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2D DEVELOPMENT evaluation boundary: PASS "
        "(immutable D2A receipt + exact OSS-3A prediction + DEVELOPMENT OSS-3C labels; "
        "exact keyset/provenance; predictive metrics only; no Qlib runtime/PnL/broker/OMS/"
        "Safety/PAPER/capital/LIVE authority)"
    )
    return 0


def _forbidden_module(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES)


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _literal_dict_keys(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                result.add(key.value.lower())
    return result


if __name__ == "__main__":
    raise SystemExit(main())
