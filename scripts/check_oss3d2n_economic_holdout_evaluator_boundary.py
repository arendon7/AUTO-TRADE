from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "labs" / "oss3_qlib" / "economic_holdout_evaluator.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "qlib",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "subprocess",
)
FORBIDDEN_NAMES = {
    "OrderIntent",
    "RiskDecision",
    "CapitalSafetyKernel",
}
FORBIDDEN_CALLS = {
    "submit_order",
    "place_order",
    "execute_order",
    "send_order",
    "urlopen",
    "eval",
    "exec",
    "__import__",
    "import_module",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        return _finish(["missing OSS-3D2N evaluator"])
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
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                errors.append(f"forbidden call: {name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"forbidden authority symbol: {node.id}")

    evaluate = _method(tree, "SQLiteOSS3EconomicHoldoutEvaluationRegistry", "evaluate")
    if evaluate is None:
        errors.append("missing SQLiteOSS3EconomicHoldoutEvaluationRegistry.evaluate")
    else:
        params = {arg.arg for arg in evaluate.args.args + evaluate.args.kwonlyargs}
        required = {
            "evaluation_id",
            "economic_protocol",
            "d2l_receipt",
            "d2j_protocol",
            "holdout",
            "now",
        }
        if not required.issubset(params):
            errors.append("D2N evaluate surface is missing frozen inputs")
        for forbidden in (
            "initial_cash",
            "cost_policy",
            "decision_policy",
            "thresholds",
            "retune",
            "fallback_candidate",
            "broker",
            "order_intent",
        ):
            if forbidden in params:
                errors.append(f"D2N evaluate exposes forbidden configurable input: {forbidden}")

        calls = [
            (_call_name(node.func), getattr(node, "lineno", 0))
            for node in ast.walk(evaluate)
            if isinstance(node, ast.Call)
        ]
        lines: dict[str, int] = {}
        for name, lineno in calls:
            lines.setdefault(name, lineno)
        required_calls = (
            "_verify_protocol_chain",
            "_verify_temporal_separation",
            "_reject_broker_credentials",
            "_require_d2k_pass",
            "_record_start",
            "_checkout",
            "project_prediction_artifact",
            "_simulate",
            "_record_terminal",
        )
        for name in required_calls:
            if name not in lines:
                errors.append(f"missing D2N sequencing call: {name}")
        ordered = (
            "_verify_protocol_chain",
            "_verify_temporal_separation",
            "_reject_broker_credentials",
            "_require_d2k_pass",
            "_record_start",
            "_checkout",
            "project_prediction_artifact",
            "_simulate",
        )
        if all(name in lines for name in ordered):
            if tuple(lines[name] for name in ordered) != tuple(sorted(lines[name] for name in ordered)):
                errors.append(
                    "D2N must verify -> temporal -> reject creds -> D2K PASS -> durable start -> checkout -> project -> simulate"
                )

    required_text = (
        'OSS3D2N_MATERIAL_VERSION = "OSS3D2N_PROTECTED_ECONOMIC_HOLDOUT_MATERIAL_V1"',
        'OSS3D2N_START_VERSION = "OSS3D2N_ECONOMIC_HOLDOUT_START_V1"',
        'OSS3D2N_METRICS_VERSION = "OSS3D2N_ECONOMIC_HOLDOUT_METRICS_V1"',
        'OSS3D2N_RECEIPT_VERSION = "OSS3D2N_ECONOMIC_HOLDOUT_EVALUATION_V1"',
        'INITIAL_CASH = Decimal("100000")',
        'TEMPORAL_POLICY = "ECONOMIC_HOLDOUT_STRICTLY_AFTER_PREDICTIVE_HOLDOUT_V1"',
        'PROFIT_FACTOR_POLICY = "REALIZED_SELL_PNL_AVERAGE_COST_V1"',
        'NO_TERMINAL_LIQUIDATION_POLICY = "MARK_TO_MARKET_ONLY_NO_FORCED_FINAL_TRADE_V1"',
        'economic holdout must start strictly after predictive FINAL_HOLDOUT',
        'D2N requires predictive D2K PASS',
        'BEGIN IMMEDIATE',
        'oss3_predictive_economic_protocols',
        'oss3_final_holdout_evaluations',
        'oss3_economic_holdout_evaluation_starts',
        'oss3_economic_holdout_evaluations',
        'OSS-3D2N start registry is append-only',
        'OSS-3D2N terminal registry is append-only',
        'execution_index != self.signal_index + 1',
        'cost_policy.execution_cost_model',
        'cost_policy.max_volume_participation',
        'cost_policy.min_trade_notional',
        'average_cost',
        'realized_pnl',
        'ECONOMIC_NET_RETURN_POSITIVE',
        'ECONOMIC_SHARPE_MIN',
        'ECONOMIC_PROFIT_FACTOR_MIN',
        'ECONOMIC_MAX_DRAWDOWN_MAX',
        'ECONOMIC_FILLS_MIN',
        'ECONOMIC_REBALANCES_MIN',
        '"profitability_claim_authorized": False',
        '"promotion_authorized": False',
        '"execution_authorized": False',
        '"paper_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    )
    for marker in required_text:
        if marker not in source:
            errors.append(f"missing D2N invariant marker: {marker}")

    forbidden_text = (
        "import qlib",
        "from qlib",
        "OrderIntent(",
        "submit_order(",
        "place_order(",
        "execute_order(",
        "paper_execution_authorized=True",
        "execution_authorized=True",
        "promotion_authorized=True",
        'capital_authority="PAPER"',
        'live_trading="ENABLED"',
        "second_attempt_allowed=True",
        "retuning_allowed=True",
        "reselection_allowed=True",
    )
    for marker in forbidden_text:
        if marker in source:
            errors.append(f"forbidden D2N surface: {marker}")

    return _finish(errors)


def _method(tree: ast.AST, class_name: str, method_name: str):
    for node in getattr(tree, "body", ()):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                    return child
    return None


def _forbidden_module(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


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
        "AUTO-TRADE OSS-3D2N ECONOMIC HOLDOUT BOUNDARY: PASS — exact D2M/D2L/D2J + "
        "durable D2K PASS; strict later economic holdout; fixed NAV; next-bar long-only "
        "cash-bounded cost-aware simulation; one-shot terminal PASS/FAIL; no broker/OMS/"
        "Safety/OrderIntent/PAPER/capital/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
