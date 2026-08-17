from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
INSPECTOR = ROOT / "scripts/mac_crypto_execution_health_readiness.py"
WRAPPER = ROOT / "scripts/mac_dashboard_execution_gate.py"
LAUNCHER = ROOT / "ABRIR_AUTO_TRADE.command"
TEST_INSPECTOR = ROOT / "tests/test_mac_crypto_execution_health_readiness.py"
TEST_WRAPPER = ROOT / "tests/test_mac_crypto_execution_gate_readiness.py"
WORKFLOW = ROOT / ".github/workflows/r6-crypto-execution-gate-readiness.yml"

FORBIDDEN_AUTHORITY = (
    "alpaca_paper_writer",
    "alpaca_paper_crypto_writer",
    "FinalGuardedCryptoEntryTransport",
    "FinalGuardedCryptoProtectionTransport",
    "CryptoPaperExecutionBridge",
    "CryptoPaperFinalWriteGuard",
    "stage_external_submission",
    "mark_entry_submission_unknown",
    "submit_once(",
    ".consume(",
    "record_operator_approval(",
    'env[WRITE_ENV] = "ENABLED"',
    "r6_execute_paper_canary.py",
)
FORBIDDEN_INSPECTOR_IMPORT_ROOTS = {
    "http",
    "urllib",
    "socket",
    "requests",
    "httpx",
    "websocket",
    "websockets",
    "subprocess",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def main() -> int:
    errors: list[str] = []
    for path in (INSPECTOR, WRAPPER, LAUNCHER, TEST_INSPECTOR, TEST_WRAPPER, WORKFLOW):
        if not path.is_file():
            errors.append(f"missing execution readiness contract file: {path.relative_to(ROOT)}")

    inspector = INSPECTOR.read_text(encoding="utf-8") if INSPECTOR.is_file() else ""
    for anchor in (
        'EXECUTION_STRATEGY_ID = "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION"',
        'MAX_HEALTH_AGE_SECONDS = 3600',
        '?mode=ro',
        'conn.execute("PRAGMA query_only=ON")',
        '"health_state_v2"',
        '"health_bridge_state"',
        "HealthControlState",
        "HealthBridgeState",
        "_verify_ack_chain",
        '"CORE_DB_MISSING"',
        '"STRATEGY_HEALTH_MISSING"',
        '"PORTFOLIO_HEALTH_MISSING"',
        '"PORTFOLIO_HEALTH_IDENTITY_AMBIGUOUS"',
        '"HEALTH_INTEGRITY_FAILURE"',
        '"HEALTH_R4_EXECUTION_READINESS_PASS"',
        '"read_only": True',
        '"credentials_read": False',
        '"broker_network_used": False',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"approval_consumed": False',
        '"oms_submitting": False',
        '"lifecycle_unknown": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    ):
        if anchor not in inspector:
            errors.append(f"Health readiness inspector missing anchor: {anchor}")
    if INSPECTOR.is_file():
        for module in _imports(INSPECTOR):
            if module.split(".", 1)[0] in FORBIDDEN_INSPECTOR_IMPORT_ROOTS:
                errors.append(f"Health readiness inspector imports forbidden network/process authority: {module}")
    for forbidden in FORBIDDEN_AUTHORITY + (
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
        "sqlite3.connect(str(",
    ):
        if forbidden in inspector:
            errors.append(f"Health readiness inspector contains forbidden authority/write surface: {forbidden}")

    wrapper = WRAPPER.read_text(encoding="utf-8") if WRAPPER.is_file() else ""
    for anchor in (
        '"/api/execution-health-readiness"',
        "5 · Execution Gate Readiness · Health R4",
        "Comprobar Health R4 · SOLO LECTURA",
        "inspect_health_readiness(",
        '"crypto_execution_health_readiness_read_only": True',
        '"crypto_execution_final_guard_uat": False',
        '"crypto_execution_approval_consumption": False',
        '"crypto_execution_oms_staging": False',
        '"crypto_execution_lifecycle_unknown": False',
        '"crypto_execution_broker_post": False',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"approval_consumed": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "nunca se fabricará un estado NORMAL",
    ):
        if anchor not in wrapper:
            errors.append(f"Mac execution readiness wrapper missing anchor: {anchor}")
    for forbidden in FORBIDDEN_AUTHORITY + ("R6_EXTERNAL_PAPER_WRITE=ENABLED",):
        if forbidden in wrapper:
            errors.append(f"Mac execution readiness wrapper contains forbidden execution authority: {forbidden}")

    launcher = LAUNCHER.read_text(encoding="utf-8") if LAUNCHER.is_file() else ""
    for anchor in (
        "scripts/mac_dashboard_execution_gate.py",
        "scripts/mac_dashboard_one_shot.py",
        "scripts/mac_dashboard.py",
        "export R6_EXTERNAL_PAPER_WRITE=DISABLED",
        "unset APCA_API_KEY_ID",
        "unset APCA_API_SECRET_KEY",
    ):
        if anchor not in launcher:
            errors.append(f"launcher missing execution-readiness safe anchor: {anchor}")
    for forbidden in FORBIDDEN_AUTHORITY + ("export R6_EXTERNAL_PAPER_WRITE=ENABLED",):
        if forbidden in launcher:
            errors.append(f"launcher contains forbidden execution authority: {forbidden}")

    tests = ""
    for path in (TEST_INSPECTOR, TEST_WRAPPER):
        if path.is_file():
            tests += path.read_text(encoding="utf-8") + "\n"
    for anchor in (
        "test_missing_core_db_blocks_without_creating_database",
        "test_exact_healthy_strategy_and_single_portfolio_are_ready",
        "test_execution_strategy_health_is_exact_not_any_strategy",
        "test_ambiguous_portfolio_health_blocks",
        "test_stale_authoritative_health_and_bridge_block",
        "test_tampered_health_hash_fails_closed",
        "test_tampered_bridge_hash_fails_closed",
        "test_loopback_health_readiness_missing_core_is_structured_and_no_post",
        "assert before == after",
        'assert readiness["approval_consumed"] is False',
        'assert readiness["lifecycle_unknown"] is False',
    ):
        if anchor not in tests:
            errors.append(f"execution readiness tests missing regression anchor: {anchor}")

    workflow = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.is_file() else ""
    for anchor in (
        "python scripts/check_mac_crypto_execution_gate_readiness.py",
        "tests/test_mac_crypto_execution_health_readiness.py",
        "tests/test_mac_crypto_execution_gate_readiness.py",
        "python scripts/check_r6_authority.py",
        "python scripts/check_r6_crypto_execution_boundary.py",
        "python scripts/check_mac_dashboard_boundary.py",
    ):
        if anchor not in workflow:
            errors.append(f"execution readiness workflow missing permanent check: {anchor}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac crypto execution-gate Health R4 readiness boundary: PASS "
        "(workspace core.sqlite3 mode=ro; authoritative strategy+portfolio Health and bridge integrity; "
        "no credentials/network/write/approval-consume/OMS-SUBMITTING/lifecycle-UNKNOWN/Final-Guard/POST/LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
