from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "labs" / "oss3_qlib"
ATTESTATION = LAB / "environment_attestation.py"
D2B_SEMANTIC_FILES = (
    LAB / "model_contract.py",
    LAB / "dataset_adapter.py",
    LAB / "network_guard.py",
    LAB / "runner.py",
    LAB / "requirements.txt",
)

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
    "qlib",
    "mlflow",
    "redis",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
)
FORBIDDEN_CALLS = {
    "getenv",
    "getlogin",
    "gethostname",
    "getfqdn",
    "node",
    "cwd",
    "getcwd",
    "chdir",
    "popen",
    "Popen",
    "urlopen",
    "create_connection",
    "eval",
    "exec",
    "__import__",
    "import_module",
}
FORBIDDEN_AUTHORITY_NAMES = {
    "OrderIntent",
    "RiskDecision",
    "CapitalSafetyKernel",
}


def main() -> int:
    errors: list[str] = []
    if not ATTESTATION.is_file():
        errors.append("missing OSS-3D2C environment_attestation.py")
        return _finish(errors)
    for path in D2B_SEMANTIC_FILES:
        if not path.is_file():
            errors.append(f"missing certified D2B semantic file: {path.name}")
    errors.extend(_scan_attestation())
    errors.extend(_check_source_contract())
    errors.extend(_runtime_probe())
    return _finish(errors)


def _finish(errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2C Qlib environment boundary: PASS "
        "(effective Python/platform/distribution manifest only; D2B runner/model/"
        "requirements hash-bound; no env-var values, hostname/user/path evidence, "
        "network/process/Qlib/MLflow/Redis runtime imports, broker/OMS/Safety, "
        "PAPER/capital/LIVE authority)"
    )
    return 0


def _scan_attestation() -> list[str]:
    source = ATTESTATION.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ATTESTATION.relative_to(ROOT)))
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_module(alias.name):
                    errors.append(f"line {node.lineno}: forbidden import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_module(module):
                errors.append(f"line {node.lineno}: forbidden import {module}")
            for alias in node.names:
                if alias.name in FORBIDDEN_AUTHORITY_NAMES:
                    errors.append(
                        f"line {node.lineno}: forbidden authority symbol {alias.name}"
                    )
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                errors.append(f"line {node.lineno}: forbidden call {name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_AUTHORITY_NAMES:
            errors.append(f"line {node.lineno}: forbidden authority symbol {node.id}")
    return errors


def _check_source_contract() -> list[str]:
    source = ATTESTATION.read_text(encoding="utf-8")
    errors: list[str] = []
    required = (
        'OSS3D2C_ATTESTATION_VERSION = "OSS3D2C_QLIB_ENVIRONMENT_ATTESTATION_V1"',
        "runner_code_hash(lab_root=root)",
        "model_config_hash()",
        'requirements = root / "requirements.txt"',
        "metadata.distributions()",
        '"execution_authorized": False',
        '"paper_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        '"mlflow"',
        '"redis"',
    )
    for snippet in required:
        if snippet not in source:
            errors.append(f"attestation missing required contract: {snippet}")
    forbidden = (
        "os.environ",
        "os.getenv",
        "os.system",
        "os.popen",
        "platform.node(",
        "socket.",
        "subprocess.",
        "qlib.",
        "mlflow.",
        "redis.",
        "HOME",
        "USERPROFILE",
        "API_KEY",
        "SECRET_KEY",
    )
    for snippet in forbidden:
        if snippet in source:
            errors.append(f"attestation contains forbidden evidence/runtime surface: {snippet}")
    return errors


def _runtime_probe() -> list[str]:
    sys.path.insert(0, str(ROOT))
    try:
        from labs.oss3_qlib.environment_attestation import (
            InstalledDistribution,
            OSS3D2C_ATTESTATION_VERSION,
            collect_environment_attestation,
        )
    finally:
        if sys.path and sys.path[0] == str(ROOT):
            sys.path.pop(0)

    fake = (
        InstalledDistribution("joblib", "1.0"),
        InstalledDistribution("numpy", "2.0"),
        InstalledDistribution("pandas", "3.0"),
        InstalledDistribution("pyqlib", "0.9.7"),
        InstalledDistribution("scikit-learn", "1.0"),
        InstalledDistribution("scipy", "1.0"),
    )
    errors: list[str] = []
    try:
        attestation = collect_environment_attestation(distributions=fake)
    except Exception as exc:
        return [f"runtime attestation probe failed: {type(exc).__name__}: {exc}"]
    if OSS3D2C_ATTESTATION_VERSION != "OSS3D2C_QLIB_ENVIRONMENT_ATTESTATION_V1":
        errors.append("attestation version drifted")
    if attestation.qlib_version != "0.9.7":
        errors.append("attestation no longer binds certified pyqlib 0.9.7")
    if attestation.execution_authorized or attestation.paper_execution_authorized:
        errors.append("environment evidence unexpectedly authorizes execution")
    if attestation.capital_authority != "NONE" or attestation.live_trading != "BLOCKED":
        errors.append("environment evidence unexpectedly grants capital/LIVE")
    if len(attestation.attestation_hash) != 64 or len(attestation.installed_manifest_hash) != 64:
        errors.append("environment evidence hashes are not sha256 identities")
    return errors


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


if __name__ == "__main__":
    raise SystemExit(main())
