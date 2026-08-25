from __future__ import annotations

import ast
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/paper_runtime_safety_health_truth.py"
WORKFLOW = ROOT / ".github/workflows/w86-paper-runtime-safety-health-truth.yml"
CORE = ROOT / ".github/workflows/core-tests.yml"
SELF_COMMAND = "python scripts/check_w86_paper_runtime_safety_health_truth_boundary.py"
SELF_TEST = "tests/test_w86_paper_runtime_safety_health_truth.py"
WRITE_SQL = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|VACUUM|ATTACH|DETACH)\b",
    re.IGNORECASE,
)
FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "http",
    "socket",
    "urllib",
    "websocket",
    "websockets",
    "openai",
    "anthropic",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.risk_state",
    "autotrade.health_bridge",
    "autotrade.persistence",
    "autotrade.research",
    "autotrade.execution",
    "autotrade.brokers",
)
FORBIDDEN_CALLS = {
    "SQLiteRuntime",
    "SQLiteR2SafetyStateStore",
    "SQLiteSafetyStateStore",
    "SQLiteHealthStateStore",
    "SQLiteHealthBridgeStore",
    "OrderIntent",
    "submit",
    "submit_once",
    "place_order",
    "cancel_order",
    "replace_order",
    "stage_external_submission",
    "reserve",
    "reserve_capital",
    "activate",
    "activate_kill_switch",
    "activate_circuit",
    "reset",
    "reset_kill_switch",
    "acknowledge_circuit",
    "acknowledge_recovery",
    "sync_from_health",
    "write_text",
    "write_bytes",
    "unlink",
    "replace",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        print("ERROR: missing W86 PAPER runtime Safety/Health truth module", file=sys.stderr)
        return 1

    source = TARGET.read_text(encoding="utf-8")
    required = (
        'PAPER_RUNTIME_SAFETY_HEALTH_TRUTH_VERSION = "W86_PAPER_RUNTIME_SAFETY_HEALTH_TRUTH_V1"',
        'PORTFOLIO_HEALTH_ENTITY_ID = "R6_CRYPTO_PAPER_PORTFOLIO"',
        "class PaperRuntimeSafetyHealthTruthPolicy:",
        "class PaperRuntimeSafetyHealthTruthProof:",
        "class PaperRuntimeSafetyHealthTruthReader:",
        'f"file:{self._core_path}?mode=ro"',
        'conn.execute("PRAGMA query_only=ON")',
        'conn.execute("BEGIN")',
        'conn.execute("COMMIT")',
        'conn.execute("ROLLBACK")',
        'conn.execute("PRAGMA data_version")',
        '"safety_state"',
        '"ledger_events"',
        '"health_state_v2"',
        '"health_recovery_acks_v3"',
        '"health_bridge_state"',
        '"kill_switch_active"',
        '"circuit_active"',
        '"KILL_SWITCH_ACTIVATED"',
        '"KILL_SWITCH_RESET"',
        '"CIRCUIT_ACTIVATED"',
        '"CIRCUIT_ACKNOWLEDGED"',
        '"HEALTH_BRIDGE_APPLIED"',
        '"HEALTH_BRIDGE_RECOVERY_ACKNOWLEDGED"',
        'COMMISSIONING_EVENT = "R6_HEALTH_R4_CORE_COMMISSIONED"',
        'running = "GENESIS"',
        "expected_hash = _ledger_hash(",
        'if state != "HEALTHY":',
        'if mode != "NORMAL":',
        'if raw_multiplier != "1":',
        "if health_version != health.version:",
        "if health_fingerprint != health.fingerprint:",
        "if running != expected_head:",
        "max_health_state_age_seconds: int = 3600",
        "not 1 <= value <= 3600",
        '"ledger_integrity_verified": True',
        '"safety_projection_verified": True',
        '"strategy_health_verified": True',
        '"portfolio_health_verified": True',
        '"read_only_core_truth": True',
        '"sqlite_snapshot_consistent": True',
        '"concurrent_durable_change_detected": False',
        '"paper_runtime_ready": False',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "candidate_module._payload(candidate, include_hash=False)",
    )
    for anchor in required:
        if anchor not in source:
            errors.append(f"W86 Safety/Health truth contract missing: {anchor}")

    for forbidden in (
        "OrderIntent(",
        "paper-api.alpaca.markets",
        "data.alpaca.markets",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "R6_EXTERNAL_PAPER_WRITE",
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
        "SQLiteRuntime(",
        "SQLiteR2SafetyStateStore(",
        "SQLiteSafetyStateStore(",
        "SQLiteHealthStateStore(",
        "SQLiteHealthBridgeStore(",
    ):
        if forbidden in source:
            errors.append(f"W86 Safety/Health truth contains forbidden surface: {forbidden}")

    try:
        tree = ast.parse(source, filename=str(TARGET))
    except SyntaxError as exc:
        errors.append(f"W86 Safety/Health truth syntax error: {exc}")
        tree = None

    if tree is not None:
        for node in ast.walk(tree):
            for module in _import_modules(node):
                if any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in FORBIDDEN_IMPORT_PREFIXES
                ):
                    errors.append(
                        f"W86 Safety/Health truth imports forbidden authority/network surface "
                        f"at line {node.lineno}: {module}"
                    )
            if isinstance(node, ast.Call):
                call = _call_name(node.func)
                if call in FORBIDDEN_CALLS:
                    errors.append(
                        f"W86 Safety/Health truth contains forbidden mutating call "
                        f"at line {node.lineno}: {call}"
                    )
                if call in {"execute", "executescript"} and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if WRITE_SQL.search(arg.value):
                            errors.append(
                                f"W86 Safety/Health truth contains write-capable SQL at line {node.lineno}"
                            )
                if call == "open" and len(node.args) > 1:
                    mode = node.args[1]
                    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                        if any(flag in mode.value for flag in ("w", "a", "+", "x")):
                            errors.append(
                                f"W86 Safety/Health truth contains writable file open at line {node.lineno}"
                            )

    if source.count("sqlite3.connect(") != 1:
        errors.append("W86 Safety/Health truth must expose exactly one read-only SQLite connect site")
    if source.count('conn.execute("BEGIN")') != 1:
        errors.append("W86 Safety/Health truth must pin one explicit SQLite read transaction")

    for workflow, label in ((WORKFLOW, "W86 Safety/Health"), (CORE, "Core Safety")):
        if not workflow.is_file():
            errors.append(f"{label}: required workflow missing")
            continue
        text = workflow.read_text(encoding="utf-8")
        if SELF_COMMAND not in text:
            errors.append(f"{label}: Safety/Health boundary is not wired into CI")
        if SELF_TEST not in text:
            errors.append(f"{label}: Safety/Health adversarial tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "AUTO-TRADE W86 PAPER runtime Safety + Health truth boundary: PASS "
        "(atomic SQLite mode=ro/query_only snapshot; independent kill+circuit replay; "
        "ledger hash-chain; exact strategy + canonical portfolio HEALTHY/NORMAL proof; "
        "full recovery ACK chains; no writer/network/readiness/execution/capital/LIVE authority)"
    )
    return 0


def _import_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        modules = [base]
        modules.extend(
            f"{base}.{alias.name}" if base else alias.name for alias in node.names
        )
        return modules
    return []


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
