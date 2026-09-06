from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "research" / "oss3_development_model_tournament.py"

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
FORBIDDEN_RESULT_KEYS = {
    "pnl",
    "profit",
    "sharpe",
    "sortino",
    "cagr",
    "portfolio_return",
    "return_on_capital",
    "paper_candidate",
    "execution_candidate",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        return _finish(["missing OSS-3D2E module"])
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

    leaked_keys = sorted(_literal_dict_keys(tree) & FORBIDDEN_RESULT_KEYS)
    if leaked_keys:
        errors.append(f"trading-economics/authority key leaked into D2E: {leaked_keys!r}")

    required = (
        'PRIMARY_METRIC = "mean_cross_sectional_rank_ic"',
        'MULTIPLE_TESTING_POLICY = "EXACT_SIGN_TEST_PLUS_HOLM_V1"',
        'COMMON_SUPPORT_POLICY = "EXACT_CROSS_SECTION_TIMESTAMP_SUPPORT_V1"',
        'phase=TrialPhase.DEVELOPMENT',
        'split_name="DEVELOPMENT"',
        'candidate_trial_ids=trial_ids',
        'p_value=Decimal(str(raw_p))',
        'campaign_holm_evidence(ledger, plan.campaign.campaign_id)',
        '("receipt keyset", d.evaluation_keyset_hash, receipt.inference_keyset_hash)',
        '("evaluation prediction artifact", receipt.prediction_artifact_hash, m.prediction_artifact_hash)',
        'recomputed_primary = sum(rank_ics) / len(rank_ics)',
        'final_holdout_observable: bool = False',
        'promotion_authorized: bool = False',
        'execution_authorized: bool = False',
        'paper_execution_authorized: bool = False',
        'capital_authority: str = "NONE"',
        'live_trading: str = "BLOCKED"',
    )
    for snippet in required:
        if snippet not in source:
            errors.append(f"missing required D2E binding: {snippet}")

    if "FINAL_HOLDOUT" not in source:
        errors.append("D2E source must document FINAL_HOLDOUT exclusion")
    return _finish(errors)


def _finish(errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2E DEVELOPMENT model tournament boundary: PASS "
        "(frozen finite family; exact D2D/D2A provenance; primary mean rank IC only; "
        "exact sign-test + Holm; common timestamp support; FINAL_HOLDOUT/promotion/"
        "broker/OMS/Safety/PAPER/capital/LIVE authority denied)"
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
