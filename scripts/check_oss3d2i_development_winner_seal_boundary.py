from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "labs" / "oss3_qlib" / "development_winner_seal.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "qlib",
    "pandas",
    "numpy",
    "scipy",
    "sklearn",
    "socket",
    "subprocess",
    "requests",
    "aiohttp",
    "httpx",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
    "autotrade.research.registry",
    "autotrade.research.oss2_final_holdout_evaluation",
    "autotrade.research.oss2_final_holdout_protocol",
)
FORBIDDEN_CALL_NAMES = {
    "eval",
    "exec",
    "__import__",
    "import_module",
    "submit_order",
    "place_order",
    "send_order",
    "execute_order",
    "consume_holdout_permit",
}
FORBIDDEN_AUTHORITY_NAMES = {
    "OrderIntent",
    "RiskDecision",
    "CapitalSafetyKernel",
    "HoldoutPermit",
    "ProtectedOSS2FinalHoldout",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        return _finish(["missing OSS-3D2I module"])
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

    required = (
        'OSS3D2I_CONTRACT_VERSION = "OSS3D2I_DEVELOPMENT_WINNER_SELECTION_SEAL_V1"',
        'SELECTION_SCOPE = "DEVELOPMENT_RANKING_WINNER_ONLY"',
        'NEXT_FRONTIER = "OSS3D2J_PROTOCOL_PREREGISTRATION_ONLY"',
        "class DevelopmentWinnerSelectionSeal:",
        "seal_development_winner(",
        "verify_development_winner_seal(",
        "winner_raw_p_value",
        "winner_holm_adjusted_p_value",
        "statistical_significance_claim_authorized: bool = False",
        "alpha_claim_authorized: bool = False",
        "profitability_claim_authorized: bool = False",
        "reselection_allowed: bool = False",
        "retuning_allowed: bool = False",
        "final_holdout_observed: bool = False",
        "final_holdout_authorized: bool = False",
        "holdout_permit_consumed: bool = False",
        "promotion_authorized: bool = False",
        "execution_authorized: bool = False",
        "paper_execution_authorized: bool = False",
        'capital_authority: str = "NONE"',
        'live_trading: str = "BLOCKED"',
    )
    for snippet in required:
        if snippet not in source:
            errors.append(f"missing required D2I binding: {snippet}")

    forbidden_text = (
        "HoldoutPermit",
        "consume_holdout_permit",
        "ProtectedOSS2FinalHoldout",
        "SQLiteOSS2FinalHoldoutEvaluationRegistry",
        "final_holdout_path",
        "--final-holdout",
        "paper_execution_authorized=True",
        "execution_authorized=True",
        "promotion_authorized=True",
        "final_holdout_authorized=True",
        "holdout_permit_consumed=True",
    )
    for snippet in forbidden_text:
        if snippet in source:
            errors.append(f"forbidden D2I authority surface: {snippet}")

    return _finish(errors)


def _finish(errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2I DEVELOPMENT winner seal boundary: PASS "
        "(ranking-winner identity only; p-values copied without significance/alpha/profitability claim; "
        "no reselection/retuning/FINAL_HOLDOUT permit/promotion/broker/OMS/Safety/PAPER/capital/LIVE)"
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


if __name__ == "__main__":
    raise SystemExit(main())
