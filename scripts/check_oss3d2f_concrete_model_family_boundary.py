from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "research" / "oss3_concrete_model_family.py"

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


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        return _finish(["missing OSS-3D2F module"])
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
        'OSS3D2F_FAMILY_VERSION = "OSS3D2F_CONCRETE_MODEL_FAMILY_V1"',
        'FAMILY_ID = "qlib-linear-finite-family-v1"',
        'MODEL_FAMILY = "qlib_linear_finite_v1"',
        'QLIB_VERSION = "0.9.7"',
        'MODEL_IMPLEMENTATION = "qlib.contrib.model.linear.LinearModel"',
        'ConcreteModelCandidate("linear-lasso-a0p001", "lasso", 0.001)',
        'ConcreteModelCandidate("linear-lasso-a0p01", "lasso", 0.01)',
        'ConcreteModelCandidate("linear-ols", "ols", 0.0)',
        'ConcreteModelCandidate("linear-ridge-a0p1", "ridge", 0.1)',
        'ConcreteModelCandidate("linear-ridge-a1", "ridge", 1.0)',
        'ConcreteModelCandidate("linear-ridge-a10", "ridge", 10.0)',
        'if self.candidates != CANONICAL_CANDIDATES:',
        'if self.adaptive_search or self.hyperparameter_optimization:',
        'development_labels_observable: bool = False',
        'final_holdout_observable: bool = False',
        'external_runtime_invoked: bool = False',
        'execution_authorized: bool = False',
        'paper_execution_authorized: bool = False',
        'capital_authority: str = "NONE"',
        'live_trading: str = "BLOCKED"',
        'DevelopmentInferenceRequest.build(',
        'expected_runner_code_hash=shared_runner_code_hash',
    )
    for snippet in required:
        if snippet not in source:
            errors.append(f"missing required D2F binding: {snippet}")

    forbidden_design_snippets = (
        "random_search",
        "grid_search",
        "bayesian_search",
        "hyperopt",
        "optuna",
        "FINAL_HOLDOUT feature",
        "development_labels=",
    )
    lower = source.lower()
    for snippet in forbidden_design_snippets:
        if snippet.lower() in lower:
            errors.append(f"forbidden adaptive/holdout design surface: {snippet}")

    return _finish(errors)


def _finish(errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2F concrete model family boundary: PASS "
        "(six immutable Qlib LinearModel candidates; DEVELOPMENT request generation only; "
        "no labels/FINAL_HOLDOUT/Qlib runtime/adaptive search/broker/OMS/Safety/PAPER/capital/LIVE)"
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
