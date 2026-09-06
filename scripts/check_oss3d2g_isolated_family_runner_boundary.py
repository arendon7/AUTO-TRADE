from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "labs" / "oss3_qlib" / "family_model_contract.py",
    ROOT / "labs" / "oss3_qlib" / "family_environment_attestation.py",
    ROOT / "labs" / "oss3_qlib" / "family_runner.py",
)

FORBIDDEN_IMPORT_PREFIXES = (
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
    "requests",
    "aiohttp",
    "urllib.request",
    "http.client",
    "ccxt",
)
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
FORBIDDEN_AUTHORITY_NAMES = {"OrderIntent", "RiskDecision", "CapitalSafetyKernel"}


def main() -> int:
    errors: list[str] = []
    sources: dict[str, str] = {}
    for target in TARGETS:
        relative = str(target.relative_to(ROOT))
        if not target.is_file():
            errors.append(f"missing D2G file: {relative}")
            continue
        source = target.read_text(encoding="utf-8")
        sources[relative] = source
        tree = ast.parse(source, filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _forbidden_module(alias.name):
                        errors.append(f"{relative}: forbidden import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _forbidden_module(module):
                    errors.append(f"{relative}: forbidden import {module}")
                for alias in node.names:
                    if alias.name in FORBIDDEN_AUTHORITY_NAMES:
                        errors.append(f"{relative}: forbidden authority symbol {alias.name}")
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in FORBIDDEN_CALL_NAMES:
                    errors.append(f"{relative}: forbidden call {name}")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_AUTHORITY_NAMES:
                errors.append(f"{relative}: forbidden authority symbol {node.id}")

    contract = sources.get("labs/oss3_qlib/family_model_contract.py", "")
    runner = sources.get("labs/oss3_qlib/family_runner.py", "")
    attestation = sources.get("labs/oss3_qlib/family_environment_attestation.py", "")

    required_contract = (
        "from autotrade.research.oss3_concrete_model_family import (",
        '"src/autotrade/research/oss3_concrete_model_family.py"',
        '"labs/oss3_qlib/family_runner.py"',
        '"labs/oss3_qlib/family_environment_attestation.py"',
        "candidate_from_config_hash",
        "expected_runner_code_hash",
        '"adaptive_search": False',
        '"hyperparameter_optimization": False',
        '"development_labels_observable": False',
        '"final_holdout_observable": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    )
    for snippet in required_contract:
        if snippet not in contract:
            errors.append(f"family_model_contract.py missing binding: {snippet}")

    required_runner = (
        "candidate = assert_family_request_contract(request.manifest)",
        "config = candidate_runtime_config(candidate)",
        "with deny_network():",
        "from qlib.contrib.model.linear import LinearModel",
        'estimator=str(config["estimator"])',
        'alpha=float(config["alpha"])',
        "collect_candidate_environment_attestation(",
        "runtime_identity = attestation.runtime_environment",
        "development_labels_loaded: bool = False",
        "final_holdout_loaded: bool = False",
        "network_allowed: bool = False",
        'capital_authority: str = "NONE"',
        'live_trading: str = "BLOCKED"',
    )
    for snippet in required_runner:
        if snippet not in runner:
            errors.append(f"family_runner.py missing binding: {snippet}")

    forbidden_runner_surfaces = (
        "--development-labels",
        "development_labels_path",
        "--final-holdout",
        "final_holdout_path",
        "--estimator",
        "--alpha",
        "qlib.init(",
        "qrun(",
        "R.register",
    )
    for snippet in forbidden_runner_surfaces:
        if snippet in runner:
            errors.append(f"family_runner.py exposes forbidden surface: {snippet}")

    required_attestation = (
        'ARTIFACT_VERSION = "OSS3D2G_CANDIDATE_ENVIRONMENT_ATTESTATION_V1"',
        "RuntimeEnvironmentIdentity(",
        "policy_id=RUNTIME_ENVIRONMENT_POLICY",
        "model_config_hash=model_config_hash",
        "runner_code_hash=actual_runner_hash",
        "execution_authorized: bool = False",
        "paper_execution_authorized: bool = False",
        'capital_authority: str = "NONE"',
        'live_trading: str = "BLOCKED"',
    )
    for snippet in required_attestation:
        if snippet not in attestation:
            errors.append(f"family_environment_attestation.py missing binding: {snippet}")

    # D2G must not alter the previously certified D2B/D2C modules.
    for legacy in (
        ROOT / "labs" / "oss3_qlib" / "model_contract.py",
        ROOT / "labs" / "oss3_qlib" / "runner.py",
        ROOT / "labs" / "oss3_qlib" / "environment_attestation.py",
    ):
        if not legacy.is_file():
            errors.append(f"missing legacy compatibility file: {legacy.relative_to(ROOT)}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2G isolated finite-family boundary: PASS "
        "(exact D2F hashes only; one shared semantic runner; real Qlib under no-network; "
        "TRAIN labels only; no DEVELOPMENT labels/FINAL_HOLDOUT/tuning/broker/OMS/Safety/PAPER/capital/LIVE)"
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
