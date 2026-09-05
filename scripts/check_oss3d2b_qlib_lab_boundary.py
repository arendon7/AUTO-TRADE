from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "labs" / "oss3_qlib"
RUNNER = LAB / "runner.py"
ADAPTER = LAB / "dataset_adapter.py"
GUARD = LAB / "network_guard.py"
MODEL = LAB / "model_contract.py"
REQUIREMENTS = LAB / "requirements.txt"
PYPROJECT = ROOT / "pyproject.toml"

FORBIDDEN_IMPORT_PREFIXES = (
    "mlflow",
    "redis",
    "subprocess",
    "requests",
    "aiohttp",
    "urllib",
    "http.client",
    "ftplib",
    "paramiko",
    "qlib.workflow",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
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
    "create_connection",
    "qrun",
    "submit_order",
    "place_order",
    "send_order",
    "execute_order",
}
FORBIDDEN_AUTHORITY_NAMES = {
    "OrderIntent",
    "RiskDecision",
    "CapitalSafetyKernel",
}
FORBIDDEN_RUNTIME_NAMES = {"qrun"}


def main() -> int:
    errors: list[str] = []
    for path in (RUNNER, ADAPTER, GUARD, MODEL, REQUIREMENTS):
        if not path.is_file():
            errors.append(f"missing OSS-3D2B lab file: {path.relative_to(ROOT)}")
    if errors:
        return _finish(errors)

    errors.extend(_check_root_dependencies())
    errors.extend(_check_requirements())
    errors.extend(_scan_python(RUNNER, allow_socket=False, require_qlib_guard=True))
    errors.extend(_scan_python(ADAPTER, allow_socket=False, require_qlib_guard=False))
    errors.extend(_scan_python(MODEL, allow_socket=False, require_qlib_guard=False))
    errors.extend(_scan_network_guard())
    errors.extend(_check_source_contracts())
    errors.extend(_runtime_contract_probe())
    return _finish(errors)


def _finish(errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2B isolated Qlib lab boundary: PASS "
        "(pyqlib pinned outside core; one frozen ridge model; Qlib imports only under "
        "fail-closed network guard; no qlib.init/qrun/workflow/MLflow/Redis; no "
        "DEVELOPMENT-label CLI input; broker credentials rejected; no broker/OMS/Safety/"
        "PAPER/capital/LIVE authority)"
    )
    return 0


def _check_root_dependencies() -> list[str]:
    text = PYPROJECT.read_text(encoding="utf-8").lower()
    errors: list[str] = []
    for token in ("pyqlib", "qlib==", "mlflow", "redis=="):
        if token in text:
            errors.append(f"external ML runtime leaked into root pyproject: {token}")
    return errors


def _check_requirements() -> list[str]:
    active = [
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if active != ["pyqlib==0.9.7"]:
        return [f"OSS-3D2B requirements must be exactly pyqlib==0.9.7, got {active!r}"]
    return []


def _scan_python(path: Path, *, allow_socket: bool, require_qlib_guard: bool) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path.relative_to(ROOT)))
    errors: list[str] = []
    guarded_qlib_import_lines = _qlib_imports_under_network_guard(tree) if require_qlib_guard else set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                if _forbidden_module(module):
                    errors.append(f"{path.name}:{node.lineno}: forbidden import {module}")
                if module == "socket" and not allow_socket:
                    errors.append(f"{path.name}:{node.lineno}: socket import outside network_guard")
                if require_qlib_guard and (module == "qlib" or module.startswith("qlib.")):
                    if node.lineno not in guarded_qlib_import_lines:
                        errors.append(f"{path.name}:{node.lineno}: Qlib import is outside deny_network scope")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_module(module):
                errors.append(f"{path.name}:{node.lineno}: forbidden import {module}")
            if module == "socket" and not allow_socket:
                errors.append(f"{path.name}:{node.lineno}: socket import outside network_guard")
            if require_qlib_guard and (module == "qlib" or module.startswith("qlib.")):
                if node.lineno not in guarded_qlib_import_lines:
                    errors.append(f"{path.name}:{node.lineno}: Qlib import is outside deny_network scope")
            for alias in node.names:
                if alias.name in FORBIDDEN_AUTHORITY_NAMES:
                    errors.append(f"{path.name}:{node.lineno}: forbidden authority symbol {alias.name}")
                if alias.name in FORBIDDEN_RUNTIME_NAMES:
                    errors.append(f"{path.name}:{node.lineno}: forbidden runtime symbol {alias.name}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALL_NAMES:
                errors.append(f"{path.name}:{node.lineno}: forbidden call {name}")
            if _is_qlib_init(node.func):
                errors.append(f"{path.name}:{node.lineno}: qlib.init() is forbidden")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_AUTHORITY_NAMES:
                errors.append(f"{path.name}:{node.lineno}: forbidden authority symbol {node.id}")
            if node.id in FORBIDDEN_RUNTIME_NAMES:
                errors.append(f"{path.name}:{node.lineno}: forbidden runtime symbol {node.id}")
    return errors


def _qlib_imports_under_network_guard(tree: ast.AST) -> set[int]:
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        if not any(_is_deny_network_context(item.context_expr) for item in node.items):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                if any(alias.name == "qlib" or alias.name.startswith("qlib.") for alias in child.names):
                    guarded.add(child.lineno)
            elif isinstance(child, ast.ImportFrom):
                module = child.module or ""
                if module == "qlib" or module.startswith("qlib."):
                    guarded.add(child.lineno)
    return guarded


def _is_deny_network_context(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and _call_name(node.func) == "deny_network"


def _scan_network_guard() -> list[str]:
    source = GUARD.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(GUARD.relative_to(ROOT)))
    imports_socket = any(
        isinstance(node, ast.Import) and any(alias.name == "socket" for alias in node.names)
        for node in ast.walk(tree)
    )
    errors: list[str] = []
    if not imports_socket:
        errors.append("network_guard must import socket to enforce denial")
    for snippet in (
        "socket.socket.connect = _blocked",
        "socket.socket.connect_ex = _blocked",
        "socket.create_connection = _blocked",
        "socket.getaddrinfo = _blocked",
    ):
        if snippet not in source:
            errors.append(f"network_guard missing fail-closed patch: {snippet}")
    return errors


def _check_source_contracts() -> list[str]:
    runner = RUNNER.read_text(encoding="utf-8")
    model = MODEL.read_text(encoding="utf-8")
    errors: list[str] = []
    for forbidden in (
        "--development-labels",
        "development_labels_path",
    ):
        if forbidden in runner:
            errors.append(f"runner contains forbidden CLI/label surface: {forbidden}")
    for required in (
        "_reject_broker_credentials()",
        "request.verify_inputs(",
        "TrainingBundleArtifact.build(",
        "assert_request_model_contract(request.manifest)",
        "with deny_network():",
        "model.fit(dataset)",
        "model.predict(dataset",
        "QlibPredictionArtifact.build(",
        "request.bind_prediction(",
    ):
        if required not in runner:
            errors.append(f"runner missing required binding: {required}")
    for required in (
        'QLIB_VERSION = "0.9.7"',
        'MODEL_FAMILY = "qlib_linear_ridge_v1"',
        '"estimator": "ridge"',
        '"alpha": 1.0',
        '"include_valid": False',
        '"prediction_segment": "test"',
    ):
        if required not in model:
            errors.append(f"model contract missing frozen value: {required}")
    return errors


def _runtime_contract_probe() -> list[str]:
    sys.path.insert(0, str(ROOT))
    try:
        from labs.oss3_qlib.model_contract import (
            MODEL_CONFIG,
            MODEL_FAMILY,
            QLIB_VERSION,
            model_config_hash,
            runner_code_hash,
        )
    finally:
        if sys.path and sys.path[0] == str(ROOT):
            sys.path.pop(0)
    errors: list[str] = []
    if QLIB_VERSION != "0.9.7":
        errors.append("runtime Qlib pin drifted")
    if MODEL_FAMILY != "qlib_linear_ridge_v1":
        errors.append("runtime model family drifted")
    if MODEL_CONFIG.get("estimator") != "ridge" or MODEL_CONFIG.get("alpha") != 1.0:
        errors.append("runtime model config is not the frozen ridge canary")
    if MODEL_CONFIG.get("include_valid") is not False:
        errors.append("runtime model unexpectedly includes a validation segment")
    if len(model_config_hash()) != 64 or len(runner_code_hash()) != 64:
        errors.append("runtime model/runner fingerprints are not sha256 identities")
    return errors


def _forbidden_module(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES)


def _is_qlib_init(func: ast.expr) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "init"
        and isinstance(func.value, ast.Name)
        and func.value.id == "qlib"
    )


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
