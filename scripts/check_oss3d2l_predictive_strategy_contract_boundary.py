from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "labs" / "oss3_qlib" / "predictive_strategy_contract.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "qlib",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
    "autotrade.domain",
    "autotrade.research.oss3_supervised_label_artifact",
    "autotrade.research.oss3_development_evaluation",
    "requests",
    "aiohttp",
    "httpx",
    "socket",
    "subprocess",
)
FORBIDDEN_NAMES = {
    "OrderIntent",
    "RiskDecision",
    "CapitalSafetyKernel",
    "OrderManagementSystem",
}
FORBIDDEN_CALLS = {
    "submit",
    "submit_order",
    "place_order",
    "send_order",
    "execute_order",
    "cancel_order",
    "replace_order",
    "eval",
    "exec",
    "__import__",
    "import_module",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        return _finish(["missing OSS-3D2L predictive strategy contract"])

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
                if alias.name in FORBIDDEN_NAMES:
                    errors.append(f"forbidden authority symbol: {alias.name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"forbidden authority symbol: {node.id}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                errors.append(f"forbidden authority/process call: {name}")

    for function_name, expected, forbidden in (
        (
            "build_predictive_strategy_binding",
            {"protocol", "winner_output", "policy"},
            {"development_labels", "labels", "final_holdout", "holdout", "d2k_receipt"},
        ),
        (
            "project_prediction_artifact",
            {"binding", "prediction"},
            {"labels", "final_holdout", "holdout", "execution_intent", "broker"},
        ),
    ):
        node = _function(tree, function_name)
        if node is None:
            errors.append(f"missing public D2L function: {function_name}")
            continue
        params = {arg.arg for arg in node.args.args + node.args.kwonlyargs}
        if not expected.issubset(params):
            errors.append(f"{function_name} missing canonical arguments")
        leaked = params & forbidden
        if leaked:
            errors.append(f"{function_name} exposes forbidden arguments: {sorted(leaked)}")

    prereg = _method(tree, "SQLiteOSS3PredictiveStrategyRegistry", "preregister")
    if prereg is None:
        errors.append("missing SQLiteOSS3PredictiveStrategyRegistry.preregister")
    else:
        calls = [
            (_call_name(node.func), getattr(node, "lineno", 0))
            for node in ast.walk(prereg)
            if isinstance(node, ast.Call)
        ]
        lines: dict[str, int] = {}
        for name, lineno in calls:
            lines.setdefault(name, lineno)
        for required in ("execute", "_require_no_d2k_start", "_build_preregistration_receipt"):
            if required not in lines:
                errors.append(f"D2L preregistration missing sequencing call: {required}")
        prereg_source = ast.get_source_segment(source, prereg) or ""
        for marker in (
            'conn.execute("BEGIN IMMEDIATE")',
            "_require_no_d2k_start(conn=conn, protocol=protocol)",
            "INSERT INTO oss3_predictive_strategy_preregistrations(",
        ):
            if marker not in prereg_source:
                errors.append(f"missing D2L durable-ordering marker: {marker}")
        positions = [prereg_source.find(marker) for marker in (
            'conn.execute("BEGIN IMMEDIATE")',
            "_require_no_d2k_start(conn=conn, protocol=protocol)",
            "INSERT INTO oss3_predictive_strategy_preregistrations(",
        )]
        if all(position >= 0 for position in positions) and positions != sorted(positions):
            errors.append("D2L new preregistration must lock, verify no D2K start, then insert")

    required_text = (
        'OSS3D2L_POLICY_VERSION = "OSS3D2L_PREDICTIVE_PORTFOLIO_POLICY_V1"',
        'SHARED_SQLITE_ORDERING_CONTRACT = "OSS3D2L_D2K_SHARED_SQLITE_ORDERING_V1"',
        'STRATEGY_ID = "oss3-qlib-cross-sectional-long-only"',
        'SCORE_DIRECTION = "DESCENDING"',
        'SELECTION_MODE = "TOP_FRACTION"',
        'WEIGHTING_MODE = "EQUAL_WEIGHT"',
        'TIE_BREAK_POLICY = "SYMBOL_ASC"',
        "EXECUTION_DELAY_BARS = 1",
        'selection_fraction=Decimal("0.25")',
        'gross_target=Decimal("0.75")',
        'max_weight_per_asset=Decimal("0.25")',
        'reserve_cash_min=Decimal("0.25")',
        "rank_score_only=True",
        "adaptive_portfolio_search=False",
        "hyperparameter_optimization=False",
        "score_sign_flip_allowed=False",
        "shorting_allowed=False",
        "leverage_allowed=False",
        "same_bar_execution_allowed=False",
        "sorted(rows, key=lambda row: (-float(row.score), row.symbol))",
        '"development_labels_used": False',
        '"policy_frozen_before_final_holdout": True',
        '"policy_selected_after_final_holdout": False',
        '"final_holdout_observed": False',
        '"final_holdout_consumed": False',
        '"profitability_claim_authorized": False',
        '"promotion_authorized": False',
        '"execution_authorized": False',
        '"paper_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "oss3_final_holdout_evaluation_starts",
        "holdout_permits",
        "D2L cannot be preregistered after D2K start",
        "D2L cannot be preregistered after holdout permit consumption",
        "CREATE TRIGGER IF NOT EXISTS oss3_predictive_strategy_prereg_no_update",
        "CREATE TRIGGER IF NOT EXISTS oss3_predictive_strategy_prereg_no_delete",
    )
    for marker in required_text:
        if marker not in source:
            errors.append(f"missing D2L invariant: {marker}")

    forbidden_text = (
        "from .final_holdout_evaluator",
        "import final_holdout_evaluator",
        "_checkout(",
        "SQLiteOSS3FinalHoldoutEvaluationRegistry",
        "evaluate_development_predictions",
        "SupervisedLabelArtifact",
        "development_labels=",
        "final_holdout=",
        "OrderIntent(",
        "submit_order(",
        "place_order(",
        "score_threshold",
        "min_score",
        "score >=",
        "score <=",
        '"execution_authorized": True',
        '"paper_execution_authorized": True',
        '"capital_authority": "PAPER"',
        '"live_trading": "ENABLED"',
    )
    for marker in forbidden_text:
        if marker in source:
            errors.append(f"forbidden D2L surface: {marker}")

    semantic_block = _assignment_text(source, tree, "SEMANTIC_FILES")
    if "final_holdout_evaluator.py" in semantic_block:
        errors.append("D2L semantic identity may not depend on D2K evaluator outcomes/runtime")

    return _finish(errors)


def _function(tree: ast.AST, name: str):
    for node in getattr(tree, "body", ()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _method(tree: ast.AST, class_name: str, method_name: str):
    for node in getattr(tree, "body", ()):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                    return child
    return None


def _assignment_text(source: str, tree: ast.AST, name: str) -> str:
    for node in getattr(tree, "body", ()):
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.get_source_segment(source, node) or ""
    return ""


def _forbidden_module(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES)


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _finish(errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2L PREDICTIVE STRATEGY CONTRACT: PASS "
        "(D2J winner + DEVELOPMENT scores only; deterministic long-only rank targets; "
        "shared-SQLite preregistration before D2K start/permit; one-bar delay; "
        "no labels/Qlib runtime/broker/OMS/Safety/OrderIntent/PAPER/capital/LIVE)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
