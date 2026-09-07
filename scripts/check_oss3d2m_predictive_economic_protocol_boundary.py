from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "labs" / "oss3_qlib" / "predictive_economic_protocol.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "qlib",
    "pandas",
    "numpy",
    "sklearn",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
    "autotrade.domain",
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
    "MarketDataset",
    "AlignedMarketUniverse",
    "QlibPredictionArtifact",
    "SupervisedLabelArtifact",
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
        return _finish(["missing OSS-3D2M protocol module"])

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
                    errors.append(f"forbidden symbol: {alias.name}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                errors.append(f"forbidden call: {name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"forbidden symbol: {node.id}")

    prereg = _method(tree, "SQLiteOSS3PredictiveEconomicProtocolRegistry", "preregister")
    if prereg is None:
        errors.append("missing SQLiteOSS3PredictiveEconomicProtocolRegistry.preregister")
    else:
        params = {arg.arg for arg in prereg.args.args + prereg.args.kwonlyargs}
        required = {
            "economic_protocol_id",
            "d2l_receipt",
            "d2j_protocol",
            "economic_holdout_commitment",
            "now",
        }
        if not required.issubset(params):
            errors.append("D2M preregistration surface is missing frozen protocol inputs")
        forbidden_params = {
            "market",
            "market_dataset",
            "universe",
            "bars",
            "labels",
            "development_labels",
            "final_holdout_labels",
            "prediction",
            "predictions",
            "economic_result",
        }
        if params & forbidden_params:
            errors.append("D2M preregistration may not accept outcome-bearing data")

        calls = [
            (_call_name(node.func), getattr(node, "lineno", 0))
            for node in ast.walk(prereg)
            if isinstance(node, ast.Call)
        ]
        lines: dict[str, int] = {}
        for name, lineno in calls:
            lines.setdefault(name, lineno)
        for required_call in (
            "_verify_d2l_d2j_binding",
            "_require_exact_d2l_durable_state",
            "_require_no_d2k_state",
            "_build_protocol_receipt",
        ):
            if required_call not in lines:
                errors.append(f"missing D2M sequencing call: {required_call}")
        if all(
            item in lines
            for item in (
                "_require_exact_d2l_durable_state",
                "_require_no_d2k_state",
                "_build_protocol_receipt",
            )
        ):
            if not (
                lines["_require_exact_d2l_durable_state"]
                < lines["_require_no_d2k_state"]
                < lines["_build_protocol_receipt"]
            ):
                errors.append(
                    "D2M must prove D2L durable state, prove no D2K state, then build receipt"
                )

    required_text = (
        'OSS3D2M_COST_POLICY_VERSION = "OSS3D2M_ECONOMIC_COST_POLICY_V1"',
        'OSS3D2M_DECISION_POLICY_VERSION = "OSS3D2M_ECONOMIC_DECISION_POLICY_V1"',
        'OSS3D2M_HOLDOUT_COMMITMENT_VERSION = "OSS3D2M_ECONOMIC_HOLDOUT_COMMITMENT_V1"',
        'OSS3D2M_PROTOCOL_VERSION = "OSS3D2M_PREREGISTERED_ECONOMIC_PROTOCOL_V1"',
        'fee_bps=Decimal("10")',
        'half_spread_bps=Decimal("5")',
        'slippage_bps=Decimal("5")',
        'max_volume_participation=Decimal("0.10")',
        'min_trade_notional=Decimal("10")',
        'min_sharpe=Decimal("1.5")',
        'min_profit_factor=Decimal("1.3")',
        'max_drawdown=Decimal("0.15")',
        'min_fills=10',
        'min_rebalances=10',
        'max_evaluations=1',
        'requires_w81_w82_continuity=True',
        'broker_authoritative_costs_claimed=False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        'BEGIN IMMEDIATE',
        'oss3_predictive_strategy_preregistrations',
        'oss3_final_holdout_evaluation_starts',
        'holdout_permits',
        'OSS-3D2M registry is append-only',
    )
    for marker in required_text:
        if marker not in source:
            errors.append(f"missing D2M invariant marker: {marker}")

    forbidden_text = (
        "import qlib",
        "from qlib",
        "development_labels=",
        "final_holdout_labels=",
        "OrderIntent(",
        "submit_order(",
        "place_order(",
        "execute_order(",
        "paper_execution_authorized=True",
        "execution_authorized=True",
        "promotion_authorized=True",
        'capital_authority="PAPER"',
        'live_trading="ENABLED"',
        "allow_short=True",
        "allow_leverage=True",
        "allow_margin=True",
    )
    for marker in forbidden_text:
        if marker in source:
            errors.append(f"forbidden D2M surface: {marker}")

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
        "AUTO-TRADE OSS-3D2M ECONOMIC PROTOCOL BOUNDARY: PASS — exact durable D2L "
        "binding; no D2K start/permit; value-opaque economic holdout; nonzero frozen "
        "costs; preregistered Sharpe/PF/DD/net-return gates; no Qlib/market values/labels/"
        "broker/OMS/Safety/OrderIntent/PAPER/capital/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
