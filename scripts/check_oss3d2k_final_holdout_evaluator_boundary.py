from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "labs" / "oss3_qlib" / "final_holdout_evaluator.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
    "requests",
    "aiohttp",
    "httpx",
    "socket",
    "subprocess",
)
FORBIDDEN_AUTHORITY_NAMES = {
    "OrderIntent",
    "RiskDecision",
    "CapitalSafetyKernel",
}
FORBIDDEN_CALL_NAMES = {
    "submit_order",
    "place_order",
    "send_order",
    "execute_order",
    "eval",
    "exec",
    "__import__",
    "import_module",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        return _finish(["missing OSS-3D2K evaluator"])

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

    evaluate_node = _method(tree, "SQLiteOSS3FinalHoldoutEvaluationRegistry", "evaluate")
    if evaluate_node is None:
        errors.append("missing SQLiteOSS3FinalHoldoutEvaluationRegistry.evaluate")
    else:
        params = tuple(arg.arg for arg in evaluate_node.args.args + evaluate_node.args.kwonlyargs)
        required_params = {
            "evaluation_id",
            "protocol",
            "source_request",
            "training_bundle",
            "train_features",
            "train_labels",
            "holdout",
            "now",
        }
        if not required_params.issubset(params):
            errors.append("D2K evaluate surface is missing frozen replay inputs")
        if "development_features" in params or "development_labels" in params:
            errors.append("D2K evaluate surface may not accept DEVELOPMENT refit data")

        calls = [
            (_call_name(node.func), getattr(node, "lineno", 0))
            for node in ast.walk(evaluate_node)
            if isinstance(node, ast.Call)
        ]
        line_by_name: dict[str, int] = {}
        for name, lineno in calls:
            line_by_name.setdefault(name, lineno)
        for required in (
            "_reject_broker_credentials",
            "_verify_exact_final_runtime",
            "_consume_and_record_start",
            "_checkout",
        ):
            if required not in line_by_name:
                errors.append(f"missing D2K sequencing call: {required}")
        ordered = (
            "_reject_broker_credentials",
            "_verify_exact_final_runtime",
            "_consume_and_record_start",
            "_checkout",
        )
        if all(name in line_by_name for name in ordered):
            if tuple(line_by_name[name] for name in ordered) != tuple(
                sorted(line_by_name[name] for name in ordered)
            ):
                errors.append(
                    "D2K must reject credentials, verify exact runtime, burn permit, then checkout holdout"
                )

    checkout_node = _method(tree, "ProtectedOSS3FinalHoldout", "_checkout")
    if checkout_node is None:
        errors.append("missing ProtectedOSS3FinalHoldout._checkout")
    else:
        checkout_source = ast.get_source_segment(source, checkout_node) or ""
        for marker in (
            "start_receipt",
            "registry_path",
            "_verify_durable_checkout_authorization(",
            "start_receipt.holdout_authorization_id",
            "start_receipt.holdout_commitment_fingerprint",
        ):
            if marker not in checkout_source:
                errors.append(f"protected checkout lacks durable proof marker: {marker}")

    runtime_node = _function(tree, "_verify_exact_final_runtime")
    if runtime_node is None:
        errors.append("missing _verify_exact_final_runtime")
    else:
        runtime_source = ast.get_source_segment(source, runtime_node) or ""
        for marker in (
            "collect_candidate_environment_attestation(",
            "attestation.artifact_hash != winner.environment_attestation_hash",
            "attestation.runtime_environment.fingerprint != winner.runtime_environment_hash",
            "attestation.manifest.runner_code_hash != winner.shared_runner_code_hash",
        ):
            if marker not in runtime_source:
                errors.append(f"D2K exact runtime proof missing: {marker}")

    durable_node = _function(tree, "_verify_durable_checkout_authorization")
    if durable_node is None:
        errors.append("missing durable checkout authorization verifier")
    else:
        durable_source = ast.get_source_segment(source, durable_node) or ""
        for marker in (
            "mode=ro",
            "PRAGMA query_only = ON",
            "oss3_final_holdout_evaluation_starts",
            "holdout_permits",
            "_start_from_row(row)",
            "_verify_permit_row(permit_row, durable_start)",
        ):
            if marker not in durable_source:
                errors.append(f"durable checkout proof missing: {marker}")

    qlib_imports = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(module == "qlib" or module.startswith("qlib.") for module in modules):
                qlib_imports.append(node)
    runner = _function(tree, "_run_frozen_final_model")
    if runner is None:
        errors.append("missing _run_frozen_final_model")
    else:
        runner_start = runner.lineno
        runner_end = getattr(runner, "end_lineno", runner_start)
        if any(not runner_start <= node.lineno <= runner_end for node in qlib_imports):
            errors.append("Qlib imports must remain inside isolated D2K model execution")
        runner_source = ast.get_source_segment(source, runner) or ""
        for snippet in (
            "with deny_network():",
            "model.fit(dataset)",
            "model.predict(dataset",
            "training_bundle.artifact_hash != request.training_bundle_hash",
        ):
            if snippet not in runner_source:
                errors.append(f"missing frozen-runtime protection: {snippet}")

    required_text = (
        'OSS3D2K_MATERIAL_VERSION = "OSS3D2K_PROTECTED_FINAL_HOLDOUT_MATERIAL_V1"',
        'OSS3D2K_START_VERSION = "OSS3D2K_FINAL_HOLDOUT_START_V1"',
        'OSS3D2K_RECEIPT_VERSION = "OSS3D2K_FINAL_HOLDOUT_EVALUATION_V1"',
        "class ProtectedOSS3FinalHoldout:",
        "class SQLiteOSS3FinalHoldoutEvaluationRegistry:",
        "SQLiteExperimentRegistry(self.path)",
        "INSERT INTO holdout_permits",
        "BEGIN IMMEDIATE",
        "FINAL_NONZERO_RANK_IC_CROSS_SECTIONS_MIN",
        "FINAL_MEAN_CROSS_SECTIONAL_RANK_IC_MIN",
        "FINAL_ONE_SIDED_EXACT_SIGN_TEST_P_MAX",
        "protocol.policy.min_holdout_cross_sections",
        "D2K may not refit on DEVELOPMENT",
        "D2K must replay exact original TRAIN bundle",
        "source_environment_attestation_hash",
        "final_environment_attestation_hash",
        "source_runtime_environment_hash",
        "final_runtime_environment_hash",
        '"retuning_allowed": False',
        '"reselection_allowed": False',
        '"fallback_candidate_allowed": False',
        '"second_attempt_allowed": False',
        '"profitability_claim_authorized": False',
        '"promotion_authorized": False',
        '"execution_authorized": False',
        '"paper_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    )
    for snippet in required_text:
        if snippet not in source:
            errors.append(f"missing required D2K invariant: {snippet}")

    forbidden_text = (
        "development_labels=",
        "development_features=",
        "submit_order(",
        "place_order(",
        "OrderIntent",
        "RiskDecision",
        "CapitalSafetyKernel",
        "paper_execution_authorized=True",
        "execution_authorized=True",
        "promotion_authorized=True",
        'capital_authority="PAPER"',
        'live_trading="ENABLED"',
        "Sharpe",
        "max_drawdown",
        "net_return",
    )
    for snippet in forbidden_text:
        if snippet in source:
            errors.append(f"forbidden D2K surface: {snippet}")

    # D2K's protected checkout is intentionally internal.  Prevent accidental
    # production bypasses from being added elsewhere in the repository. Tests
    # may call it only to prove that forged permits fail closed.
    for root in (ROOT / "src", ROOT / "labs"):
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if path == TARGET or "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "ProtectedOSS3FinalHoldout" in text and "._checkout(" in text:
                errors.append(
                    f"production D2K protected checkout bypass: {path.relative_to(ROOT)}"
                )

    return _finish(errors)


def _method(tree: ast.AST, class_name: str, method_name: str):
    for node in getattr(tree, "body", ()):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                    return child
    return None


def _function(tree: ast.AST, name: str):
    for node in getattr(tree, "body", ()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


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
        "AUTO-TRADE OSS-3D2K FINAL_HOLDOUT evaluator boundary: PASS "
        "(credential reject -> exact D2G environment -> durable permit/start proof -> protected checkout; "
        "original TRAIN replay; preregistered predictive gates; terminal no-retry; no execution authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
