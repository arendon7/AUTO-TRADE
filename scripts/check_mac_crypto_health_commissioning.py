from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
COMMISSION = ROOT / "scripts/mac_crypto_health_commissioning.py"
WRAPPER = ROOT / "scripts/mac_dashboard_health_commissioning.py"
LAUNCHER = ROOT / "ABRIR_AUTO_TRADE.command"
TEST_CORE = ROOT / "tests/test_mac_crypto_health_commissioning.py"
TEST_GATE = ROOT / "tests/test_mac_crypto_health_commissioning_gate.py"
WORKFLOW = ROOT / ".github/workflows/r6-crypto-health-commissioning.yml"

FORBIDDEN_AUTHORITY = (
    "FinalGuardedCryptoEntryTransport",
    "FinalGuardedCryptoProtectionTransport",
    "CryptoPaperFinalWriteGuard",
    "CryptoPaperExecutionBridge",
    "alpaca_paper_writer",
    "alpaca_paper_crypto_writer",
    "stage_external_submission",
    "mark_entry_submission_unknown",
    "submit_once(",
    ".consume(",
    "record_operator_approval(",
    "r6_execute_paper_canary.py",
    'env[WRITE_ENV] = "ENABLED"',
)
FORBIDDEN_COMMISSION_IMPORT_ROOTS = {
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
    for path in (COMMISSION, WRAPPER, LAUNCHER, TEST_CORE, TEST_GATE, WORKFLOW):
        if not path.is_file():
            errors.append(f"missing Health commissioning contract file: {path.relative_to(ROOT)}")

    commission = COMMISSION.read_text(encoding="utf-8") if COMMISSION.is_file() else ""
    for anchor in (
        'PORTFOLIO_HEALTH_ENTITY_ID = "R6_CRYPTO_PAPER_PORTFOLIO"',
        'COMMISSIONING_KILL_REASON = "R6_HEALTH_R4_EVIDENCE_REQUIRED"',
        "SQLiteRuntime(core)",
        "SQLiteR2SafetyStateStore(runtime)",
        "SQLiteHealthStateStore(core)",
        "SQLiteHealthBridgeStore(",
        "require_strategy_state=True",
        "require_portfolio_state=True",
        "safety_store.activate(reason=COMMISSIONING_KILL_REASON",
        'conn.execute("SELECT COUNT(*) FROM health_state_v2")',
        'conn.execute("SELECT COUNT(*) FROM health_bridge_state")',
        '"health_state_rows_created": 0',
        '"health_bridge_rows_created": 0',
        '"fabricated_health": False',
        '"broker_network_used": False',
        '"credentials_read": False',
        '"local_state_write_performed": True',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"approval_consumed": False',
        '"oms_submitting": False',
        '"lifecycle_unknown": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        '"next_action": "PRODUCE_AND_VALIDATE_REAL_STRATEGY_AND_PORTFOLIO_HEALTH_EVIDENCE"',
    ):
        if anchor not in commission:
            errors.append(f"Health commissioning script missing anchor: {anchor}")
    if COMMISSION.is_file():
        for module in _imports(COMMISSION):
            if module.split(".", 1)[0] in FORBIDDEN_COMMISSION_IMPORT_ROOTS:
                errors.append(f"Health commissioning imports forbidden network/process authority: {module}")
    for forbidden in FORBIDDEN_AUTHORITY + (
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "safety_store.reset(",
        ".acknowledge_recovery(",
        ".apply_assessment(",
        ".sync_from_health(",
    ):
        if forbidden in commission:
            errors.append(f"Health commissioning contains forbidden authority/Health fabrication surface: {forbidden}")

    wrapper = WRAPPER.read_text(encoding="utf-8") if WRAPPER.is_file() else ""
    for anchor in (
        "6 · Health R4 Commissioning · Core durable",
        "Commissionar core R4 · LOCAL ONLY / NO POST",
        '"/api/health-r4-commission-core"',
        "commission_health_core(",
        '"crypto_health_r4_schema_only": True',
        '"crypto_health_r4_fabricated_health": False',
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
    ):
        if anchor not in wrapper:
            errors.append(f"Health commissioning Mac wrapper missing anchor: {anchor}")
    for forbidden in FORBIDDEN_AUTHORITY:
        if forbidden in wrapper:
            errors.append(f"Health commissioning Mac wrapper contains forbidden execution authority: {forbidden}")

    launcher = LAUNCHER.read_text(encoding="utf-8") if LAUNCHER.is_file() else ""
    for anchor in (
        "scripts/mac_dashboard_health_commissioning.py",
        "scripts/mac_dashboard_execution_gate.py",
        "scripts/mac_dashboard_one_shot.py",
        "scripts/mac_dashboard.py",
        "export R6_EXTERNAL_PAPER_WRITE=DISABLED",
        "unset APCA_API_KEY_ID",
        "unset APCA_API_SECRET_KEY",
    ):
        if anchor not in launcher:
            errors.append(f"launcher missing Health commissioning safe anchor: {anchor}")
    if 'export R6_EXTERNAL_PAPER_WRITE=ENABLED' in launcher:
        errors.append("launcher may not enable external PAPER write")

    tests = ""
    for path in (TEST_CORE, TEST_GATE):
        if path.is_file():
            tests += path.read_text(encoding="utf-8") + "\n"
    for anchor in (
        "test_missing_core_commissions_schema_but_creates_no_health_evidence",
        "test_commissioning_is_idempotent_and_does_not_reopen_kill_switch",
        "test_commissioning_manifest_is_hash_bound_and_tamper_fails_closed",
        "test_commissioning_refuses_preexisting_authoritative_health_rows",
        "test_commissioning_refuses_symlinked_core",
        "test_crypto_page_adds_schema_only_commissioning_and_keeps_execution_absent",
        "test_loopback_commissioning_endpoint_is_local_state_only",
        'assert result["fabricated_health"] is False',
        'assert result["broker_write_performed"] is False',
        'assert result["approval_consumed"] is False',
    ):
        if anchor not in tests:
            errors.append(f"Health commissioning tests missing regression anchor: {anchor}")

    workflow = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.is_file() else ""
    for anchor in (
        "python scripts/check_mac_crypto_health_commissioning.py",
        "tests/test_mac_crypto_health_commissioning.py",
        "tests/test_mac_crypto_health_commissioning_gate.py",
        "tests/test_mac_crypto_execution_health_readiness.py",
        "python scripts/check_mac_crypto_execution_gate_readiness.py",
        "python scripts/check_r6_authority.py",
        "python scripts/check_r6_crypto_execution_boundary.py",
        "python scripts/check_mac_dashboard_boundary.py",
    ):
        if anchor not in workflow:
            errors.append(f"Health commissioning workflow missing permanent check: {anchor}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac crypto Health R4 commissioning boundary: PASS "
        "(local schema-only core commissioning; kill switch forced active; zero Health/bridge rows created; "
        "no credentials/network/approval-consume/OMS-SUBMITTING/lifecycle-UNKNOWN/Final-Guard/broker-POST/LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
