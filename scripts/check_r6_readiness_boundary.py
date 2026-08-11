from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/brokers/alpaca_paper_readiness.py"
CLI = ROOT / "scripts/r6_inspect_paper_readiness.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_readiness_boundary.py"
SELF_TEST = "tests/test_r6_readiness_boundary.py"
FUNCTIONAL_TEST = "tests/test_r6_paper_readiness.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "urllib",
    "http",
    "socket",
    "websockets",
    "requests",
    "openai",
    "anthropic",
    "autotrade.research",
)
FORBIDDEN_IMPORT_FRAGMENTS = (
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "alpaca_paper_reconciliation_gateway",
    "alpaca_paper_trade_updates_transport",
)
FORBIDDEN_CALLS = {
    "submit_once",
    "stage_external_submission",
    "record_operator_approval",
    "attest_account",
    "connect_and_listen",
    "write_text",
    "write_bytes",
    "unlink",
    "replace",
}
REQUIRED_MODULE = (
    "class PaperOperationalReadinessInspector:",
    "mode=ro",
    'conn.execute("PRAGMA query_only=ON")',
    'network_used: bool = False',
    'broker_write_performed: bool = False',
    'execution_authorized: bool = False',
    'capital_authority: str = "NONE"',
    'profitability_claim: bool = False',
    'live_trading: str = "BLOCKED"',
    'PaperReadinessPhase.EXPLICIT_EXECUTION_DECISION_REQUIRED',
    '"SEPARATE_EXPLICIT_OPERATOR_DECISION_REQUIRED_BEFORE_REAL_PAPER_EXECUTION"',
    'PaperReadinessPhase.RECONCILIATION_REQUIRED',
    '"RUN_SEPARATE_GET_ONLY_RECONCILIATION_AND_EVIDENCE_CAPTURE"',
)
REQUIRED_CLI = (
    "PaperOperationalReadinessInspector(args.workspace).inspect(",
    '"network_used": False',
    '"broker_write_performed": False',
    '"execution_authorized": False',
    '"capital_authority": "NONE"',
    '"profitability_claim": False',
    '"live_trading": "BLOCKED"',
)


def main() -> int:
    errors: list[str] = []
    for path, label in ((MODULE, "readiness module"), (CLI, "readiness CLI")):
        if not path.is_file():
            errors.append(f"required {label} missing: {_relative(path)}")
            continue
        errors.extend(_scan(path))

    if MODULE.is_file():
        source = MODULE.read_text(encoding="utf-8")
        for anchor in REQUIRED_MODULE:
            if anchor not in source:
                errors.append(f"readiness fail-closed anchor missing: {anchor}")
        if "SQLiteRuntime" in source:
            errors.append("readiness inspector must not instantiate SQLiteRuntime because it can initialize schema")
        if source.count("sqlite3.connect(") == 0:
            errors.append("readiness inspector must explicitly use read-only SQLite connections")
        if "?mode=ro" not in source:
            errors.append("readiness inspector SQLite URI is not mode=ro")

    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        for anchor in REQUIRED_CLI:
            if anchor not in source:
                errors.append(f"readiness CLI anchor missing: {anchor}")
        for forbidden in (
            "APCA_API_KEY_ID",
            "APCA_API_SECRET_KEY",
            "R6_EXTERNAL_PAPER_WRITE",
            "--execute-paper-canary",
            "AlpacaPaperCredentials",
            "AlpacaPaperSingleShotWriter",
        ):
            if forbidden in source:
                errors.append(f"readiness CLI contains forbidden authority/credential surface: {forbidden}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: readiness checker is not wired into permanent CI")
    if R6.is_file():
        source = R6.read_text(encoding="utf-8")
        if SELF_TEST not in source:
            errors.append("R6 Authority: readiness adversarial checker tests are not wired into CI")
        if FUNCTIONAL_TEST not in source:
            errors.append("R6 Authority: readiness functional tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 readiness boundary: PASS "
        "(local read-only inspection; no credentials/network/write/execution authority; explicit next-step reporting only)"
    )
    return 0


def _scan(path: Path) -> list[str]:
    errors: list[str] = []
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{_relative(path)}: syntax error: {exc}"]
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        for module in modules:
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in FORBIDDEN_IMPORT_PREFIXES
            ):
                errors.append(f"{_relative(path)}:{node.lineno}: forbidden readiness import {module}")
            if any(fragment in module for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                errors.append(f"{_relative(path)}:{node.lineno}: forbidden readiness authority import {module}")
        if isinstance(node, ast.Call):
            call = _call_name(node.func)
            if call in FORBIDDEN_CALLS:
                errors.append(f"{_relative(path)}:{node.lineno}: forbidden readiness call {call}")
            if call == "open" and node.args:
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                    if any(flag in mode for flag in ("w", "a", "+", "x")):
                        errors.append(f"{_relative(path)}:{node.lineno}: writable file open forbidden")
    return errors


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _relative(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


if __name__ == "__main__":
    raise SystemExit(main())
