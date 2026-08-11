from __future__ import annotations

import ast
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = ROOT / "src/autotrade/brokers/alpaca_paper_core_provenance.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_core_provenance_boundary.py"
SELF_TEST = "tests/test_r6_core_provenance_boundary.py"

FORBIDDEN_IMPORTS = (
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "alpaca_paper_operator_decision",
    "alpaca_paper_canary_permit",
    "alpaca_paper_canary_coordinator",
    "alpaca_paper_gateway",
    "alpaca_paper_reconciliation_gateway",
    "alpaca_paper_trade_updates_transport",
    "autotrade.oms",
    "autotrade.research",
    "openai",
    "anthropic",
)
FORBIDDEN_CALLS = {
    "submit",
    "submit_once",
    "stage_external_submission",
    "record_operator_approval",
    "consume",
    "mark_submit_attempt_unknown",
    "SQLiteRuntime",
    "SQLiteOrderStore",
    "SQLitePortfolioStore",
    "SQLiteHealthStateStore",
    "SQLiteHealthBridgeStore",
}
WRITE_SQL = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|VACUUM|ATTACH|DETACH)\b",
    re.IGNORECASE,
)
REQUIRED = (
    "class PaperOperationalCoreProvenanceReader:",
    'uri = f"{db_path.resolve().as_uri()}?mode=ro"',
    "sqlite3.connect(uri, uri=True, isolation_level=None)",
    'conn.execute("PRAGMA query_only=ON")',
    "before_hash = _file_sha256(db_path)",
    "after_hash = _file_sha256(db_path)",
    '"core database bytes changed during read-only provenance verification"',
    'order.status is not OrderStatus.VALIDATED',
    "risk_decision_fingerprint(decision) != package.risk_decision_fingerprint",
    "state.version != package.risk_decision_safety_state_version",
    "snapshot.reconciliation_ok",
    "snapshot.broker_state_known",
    "state != _HEALTHY",
    "bridge.mode is not HealthRiskMode.NORMAL",
    'bridge.risk_multiplier != Decimal("1")',
    "bridge.health_state_fingerprint != health.fingerprint",
)


def main() -> int:
    errors: list[str] = []
    if not PROVENANCE.is_file():
        errors.append("R6 core provenance reader missing")
    else:
        source = PROVENANCE.read_text(encoding="utf-8")
        for anchor in REQUIRED:
            if anchor not in source:
                errors.append(f"core provenance safety anchor missing: {anchor}")
        errors.extend(_scan(source, PROVENANCE))
        if source.count("sqlite3.connect(") != 1:
            errors.append("core provenance reader must contain exactly one SQLite connect call")
        for forbidden in (
            "/v2/orders",
            "paper-api.alpaca.markets",
            "wss://",
            "APCA_API_SECRET_KEY",
        ):
            if forbidden in source:
                errors.append(f"core provenance reader contains forbidden network/secret surface: {forbidden}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: core provenance checker is not wired into CI")
    if not R6.is_file() or SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: core provenance adversarial tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 core provenance boundary: PASS "
        "(SQLite mode=ro/query_only; no store initialization, research, mutation, network, or execution authority)"
    )
    return 0


def _scan(source: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]
    rel = _relative(path)
    for node in ast.walk(tree):
        for module in _import_modules(node):
            if any(fragment in module for fragment in FORBIDDEN_IMPORTS):
                errors.append(f"{rel}:{node.lineno}: forbidden core provenance import {module}")
        if isinstance(node, ast.Call):
            call = _call_name(node.func)
            if call in FORBIDDEN_CALLS:
                errors.append(f"{rel}:{node.lineno}: forbidden core provenance call {call}")
            if call in {"execute", "executescript"} and node.args:
                sql_arg = node.args[0]
                if isinstance(sql_arg, ast.Constant) and isinstance(sql_arg.value, str):
                    if WRITE_SQL.search(sql_arg.value):
                        errors.append(
                            f"{rel}:{node.lineno}: write-capable SQL is forbidden in provenance reader"
                        )
    return errors


def _import_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        modules = [base]
        modules.extend(
            f"{base}.{alias.name}" if base else alias.name
            for alias in node.names
        )
        return modules
    return []


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
