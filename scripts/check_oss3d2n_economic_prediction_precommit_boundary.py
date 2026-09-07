from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "labs" / "oss3_qlib" / "economic_prediction_precommit.py"
LAB_ROOT = ROOT / "labs" / "oss3_qlib"

FORBIDDEN_IMPORT_PREFIXES = (
    "autotrade.research.market",
    "autotrade.research.universe",
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
    "MarketDataset",
    "AlignedMarketUniverse",
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
        return _finish(["missing OSS-3D2N economic prediction precommit module"])
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
        elif isinstance(node, ast.Attribute) and node.attr == "score":
            errors.append("prediction precommit may not inspect row.score")

    prereg = _method(tree, "SQLiteOSS3EconomicPredictionPrecommitRegistry", "preregister")
    if prereg is None:
        errors.append("missing prediction precommit preregister method")
    else:
        params = {arg.arg for arg in prereg.args.args + prereg.args.kwonlyargs}
        required = {
            "precommit_id",
            "economic_protocol",
            "d2l_receipt",
            "d2j_protocol",
            "prediction",
            "now",
        }
        if not required.issubset(params):
            errors.append("prediction precommit surface missing frozen identity inputs")
        forbidden_params = {
            "market",
            "universe",
            "bars",
            "prices",
            "returns",
            "economic_outcomes",
            "labels",
            "thresholds",
            "broker",
            "initial_cash",
        }
        if params & forbidden_params:
            errors.append("prediction precommit accepts outcome-bearing/configurable inputs")
        calls = [
            (_call_name(node.func), getattr(node, "lineno", 0))
            for node in ast.walk(prereg)
            if isinstance(node, ast.Call)
        ]
        lines: dict[str, int] = {}
        for name, lineno in calls:
            lines.setdefault(name, lineno)
        for required_call in (
            "_verify_chain",
            "_verify_prediction_identity_and_support",
            "_require_exact_durable_inputs",
            "_require_no_d2k_state",
            "_build_receipt",
        ):
            if required_call not in lines:
                errors.append(f"missing prediction precommit sequencing call: {required_call}")
        ordered = (
            "_verify_chain",
            "_verify_prediction_identity_and_support",
            "_build_receipt",
            "_require_exact_durable_inputs",
            "_require_no_d2k_state",
        )
        if all(name in lines for name in ordered):
            if tuple(lines[name] for name in ordered) != tuple(sorted(lines[name] for name in ordered)):
                errors.append(
                    "prediction precommit must verify lineage/support, build candidate, prove durable D2M/D2L, then prove no D2K"
                )

    required_text = (
        'OSS3D2N_PREDICTION_PRECOMMIT_VERSION = "OSS3D2N_ECONOMIC_PREDICTION_PRECOMMIT_V1"',
        'OSS3D2N_PREDICTION_ORDERING_CONTRACT = "OSS3D2N_D2M_PREDICTION_D2K_SHARED_SQLITE_ORDERING_V1"',
        "oss3_economic_prediction_precommits",
        "oss3_d2n_start_requires_prediction_precommit",
        "prediction_artifact_hash = NEW.economic_prediction_artifact_hash",
        "economic prediction must be precommitted before D2K start",
        "prediction_values_frozen\": True",
        "economic_market_values_used\": False",
        "economic_outcomes_used\": False",
        "execution_authorized\": False",
        "paper_execution_authorized\": False",
        "capital_authority\": \"NONE\"",
        "live_trading\": \"BLOCKED\"",
        "prediction precommit registry is append-only",
        "BEGIN IMMEDIATE",
    )
    for marker in required_text:
        if marker not in source:
            errors.append(f"missing prediction precommit invariant: {marker}")

    # The internal evaluator is intentionally implementation-level. Future
    # non-test lab code may not instantiate it directly except the canonical
    # precommit installer, which adds the DB admission trigger first.
    for path in LAB_ROOT.glob("*.py"):
        if path.name in {"economic_holdout_evaluator.py", "economic_prediction_precommit.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "SQLiteOSS3EconomicHoldoutEvaluationRegistry(" in text:
            errors.append(
                f"noncanonical direct D2N evaluator instantiation outside admission layer: {path.name}"
            )

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
        "AUTO-TRADE OSS-3D2N PREDICTION PRECOMMIT BOUNDARY: PASS — exact prediction "
        "artifact/support/model identity frozen after D2M and before D2K; DB trigger binds "
        "D2N start to exact artifact; no market values/outcomes/row scores/broker/OMS/Safety/"
        "OrderIntent/PAPER/capital/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
