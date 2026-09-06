from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "labs" / "oss3_qlib" / "environment_attestation.py"
ROOT_PYPROJECT = ROOT / "pyproject.toml"

FORBIDDEN_IMPORT_PREFIXES = (
    "os",
    "socket",
    "subprocess",
    "requests",
    "aiohttp",
    "urllib",
    "http.client",
    "ftplib",
    "paramiko",
    "psutil",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
)
FORBIDDEN_CALL_NAMES = {
    "getenv",
    "environ",
    "gethostname",
    "getfqdn",
    "uname",
    "system",
    "popen",
    "Popen",
    "eval",
    "exec",
    "__import__",
    "import_module",
}
FORBIDDEN_AUTHORITY_NAMES = {"OrderIntent", "RiskDecision", "CapitalSafetyKernel"}
FORBIDDEN_SERIALIZED_KEYS = {
    "timestamp",
    "created_at",
    "hostname",
    "username",
    "home",
    "cwd",
    "path",
    "environment",
    "env",
    "credential",
    "secret",
    "token",
    "api_key",
    "ip_address",
    "mac_address",
    "node",
    "processor",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        errors.append("missing OSS-3D2C environment attestation module")
        return _finish(errors)
    errors.extend(_scan_source())
    errors.extend(_check_root_isolation())
    errors.extend(_runtime_probe())
    return _finish(errors)


def _scan_source() -> list[str]:
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET.relative_to(ROOT)))
    errors: list[str] = []
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

    serialized_keys = _literal_dict_keys(tree)
    bad_keys = sorted(serialized_keys & FORBIDDEN_SERIALIZED_KEYS)
    if bad_keys:
        errors.append(f"forbidden serialized metadata key(s): {bad_keys!r}")

    required = (
        "metadata.distributions()",
        "sys.implementation.name.lower()",
        "platform.python_version()",
        "platform.system().lower()",
        "platform.machine().lower()",
        "platform.libc_ver()",
        'qlib_distribution="pyqlib"',
        "model_config_hash=model_config_hash()",
        "runner_code_hash=runner_code_hash()",
        'capital_authority: str = "NONE"',
        'live_trading: str = "BLOCKED"',
    )
    for snippet in required:
        if snippet not in source:
            errors.append(f"missing required sanitized binding: {snippet}")
    return errors


def _check_root_isolation() -> list[str]:
    text = ROOT_PYPROJECT.read_text(encoding="utf-8").lower()
    for token in ("pyqlib", "qlib==", "mlflow", "redis=="):
        if token in text:
            return [f"external ML runtime leaked into core pyproject: {token}"]
    return []


def _runtime_probe() -> list[str]:
    sys.path.insert(0, str(ROOT))
    try:
        from labs.oss3_qlib.environment_attestation import (
            EnvironmentAttestation,
            InstalledDistribution,
        )
        from labs.oss3_qlib.model_contract import QLIB_VERSION

        artifact = EnvironmentAttestation.build(
            distributions=(
                InstalledDistribution("numpy", "2.5.2"),
                InstalledDistribution("pyqlib", QLIB_VERSION),
            ),
            python_implementation="cpython",
            python_version="3.12.14",
            platform_system="linux",
            platform_machine="x86_64",
            libc_name="glibc",
            libc_version="2.39",
        )
    except Exception as exc:  # checker must surface fail-closed construction errors
        return [f"runtime sanitized attestation probe failed: {exc}"]
    finally:
        if sys.path and sys.path[0] == str(ROOT):
            sys.path.pop(0)

    payload = artifact.to_dict()
    rendered = repr(payload).lower()
    for token in ("hostname", "username", "credential", "secret", "api_key", "ip_address"):
        if token in rendered:
            return [f"sanitized runtime payload contains forbidden token: {token}"]
    if payload["manifest"]["execution_authorized"] is not False:
        return ["environment attestation unexpectedly authorizes execution"]
    if payload["manifest"]["paper_execution_authorized"] is not False:
        return ["environment attestation unexpectedly authorizes PAPER"]
    if payload["manifest"]["capital_authority"] != "NONE":
        return ["environment attestation unexpectedly grants capital authority"]
    if payload["manifest"]["live_trading"] != "BLOCKED":
        return ["environment attestation unexpectedly enables LIVE"]
    return []


def _finish(errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2C environment attestation boundary: PASS "
        "(installed-distribution metadata only; no env/path/host/secret/network/broker/OMS/"
        "Safety authority; exact Qlib/model/runner binding; PAPER/capital/LIVE denied)"
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
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value.lower())
    return keys


if __name__ == "__main__":
    raise SystemExit(main())
