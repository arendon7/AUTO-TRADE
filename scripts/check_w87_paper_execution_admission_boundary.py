from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/autotrade/paper_execution_admission.py"
TEST = ROOT / "tests/test_w87_paper_execution_admission.py"
DEDICATED = ROOT / ".github/workflows/w87-paper-execution-admission.yml"
CORE = ROOT / ".github/workflows/core-tests.yml"
SELF_COMMAND = "python scripts/check_w87_paper_execution_admission_boundary.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "socket",
    "urllib",
    "websocket",
    "websockets",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.health_bridge",
    "autotrade.execution",
    "autotrade.paper_close",
    "autotrade.brokers",
)
FORBIDDEN_CALL_NAMES = {
    "post",
    "put",
    "patch",
    "delete",
    "submit",
    "submit_once",
    "submit_order",
    "place_order",
    "cancel_order",
    "replace_order",
    "reserve",
    "reserve_capital",
    "stage_external_submission",
    "stage_external_handoff",
    "validate_for_external_submission",
}


def main() -> int:
    errors: list[str] = []
    for path, label in (
        (SOURCE, "W87 execution admission source"),
        (TEST, "W87 execution admission tests"),
        (DEDICATED, "W87 dedicated workflow"),
        (CORE, "Core Safety workflow"),
    ):
        if not path.is_file():
            errors.append(f"missing {label}: {path}")

    source = SOURCE.read_text(encoding="utf-8") if SOURCE.is_file() else ""
    for marker in (
        'PAPER_EXECUTION_ADMISSION_VERSION = "W87_PAPER_EXECUTION_ADMISSION_V1"',
        'W87_MIN_CANARY_NOTIONAL_USD = Decimal("1")',
        'W87_MAX_CANARY_NOTIONAL_USD = Decimal("5")',
        "W87_PROBATION_ORDER_CAP = 1",
        "class PaperExecutionAdmissionReceipt:",
        "class SQLitePaperExecutionAdmissionRegistry:",
        "def capture_paper_execution_admission(",
        "_validate_w86(sealed_result)",
        "_now_utc()",
        "PaperRuntimeReadinessSealStatus.READY",
        "final.minimum_executable_quantity",
        "asset.min_trade_increment",
        "final.conservative_unit_price",
        "funding.buying_power_usd < notional",
        '"order_intent_creation_permitted": True',
        '"separate_risk_decision_required": True',
        '"separate_human_execution_approval_required": True',
        '"oms_handoff_permitted": False',
        '"capital_reserved": False',
        '"broker_write_performed": False',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        'conn.execute("BEGIN IMMEDIATE")',
        "W87_PAPER_EXECUTION_ADMISSION_CAPTURED",
        "INSERT INTO ledger_events",
    ):
        if marker not in source:
            errors.append(f"W87 source missing permanent marker: {marker}")

    for forbidden in (
        '"paper_execution_authorized": True',
        '"external_execution_authorized": True',
        '"runtime_execution_authorized": True',
        '"oms_handoff_permitted": True',
        '"capital_reserved": True',
        '"broker_write_performed": True',
        '"live_trading": "ENABLED"',
        "OrderManagementSystem(",
        "CapitalSafetyKernel(",
        "AlpacaPaperCryptoWriter(",
        "HttpsAlpacaPaperCryptoWriteTransport(",
        "INSERT INTO orders",
        "UPDATE orders",
        "INSERT INTO risk_reservations",
        "UPDATE risk_reservations",
        "UPDATE portfolio_state",
        "UPDATE safety_state",
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
    ):
        if forbidden in source:
            errors.append(f"W87 admission contains forbidden authority/write surface: {forbidden}")

    _check_ast(source, errors)

    if TEST.is_file():
        test_source = TEST.read_text(encoding="utf-8")
        for marker in (
            "test_w87_captures_exact_usd1_to5_canary_envelope_without_execution_authority",
            "test_w87_uses_internal_clock_and_refuses_expired_w86_seal",
            "test_w87_rejects_blocked_upstream_pipeline",
            "test_w87_rejects_tampered_w86_seal_before_minting_admission",
            "test_w87_receipt_rejects_every_authority_escalation_or_boundary_weakening",
            "test_w87_registry_is_durable_idempotent_and_hash_chained",
            "test_w87_registry_rejects_second_admission_for_same_readiness_seal",
            "test_w87_registry_detects_missing_ledger_half_of_atomic_contract",
        ):
            if marker not in test_source:
                errors.append(f"W87 tests missing adversarial contract: {marker}")

    if DEDICATED.is_file():
        workflow = DEDICATED.read_text(encoding="utf-8")
        for required in (
            SELF_COMMAND,
            "pytest -q tests/test_w87_paper_execution_admission.py",
            "python scripts/check_w86_paper_runtime_readiness_seal_boundary.py",
            "python scripts/check_w86_paper_runtime_read_only_pipeline_boundary.py",
            "python scripts/check_w86_paper_runtime_funding_capacity_boundary.py",
            "python scripts/check_w85_paper_candidate_admission_boundary.py",
            "python scripts/check_r6_authority.py",
            "python scripts/check_r6_live_deny_boundary.py",
        ):
            if required not in workflow:
                errors.append(f"W87 dedicated workflow missing re-proof: {required}")

    if CORE.is_file():
        core = CORE.read_text(encoding="utf-8")
        if SELF_COMMAND not in core:
            errors.append("Core Safety does not enforce W87 admission boundary")
        if "pytest -q tests/test_w87_paper_execution_admission.py" not in core:
            errors.append("Core Safety does not run explicit W87 admission adversarial tests")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "AUTO-TRADE W87 PAPER execution admission boundary: PASS "
        "(fresh W86 READY seal only; exact USD 1..5 broker-increment-valid canary envelope; "
        "one readiness seal -> one durable admission; internal clock; hash-chain ledger; "
        "OrderIntent construction only; RiskDecision + human approval still mandatory; "
        "no OMS handoff, capital reservation, broker write, execution authority or LIVE authority)"
    )
    return 0


def _check_ast(source: str, errors: list[str]) -> None:
    if not source:
        return
    try:
        tree = ast.parse(source, filename=str(SOURCE))
    except SyntaxError as exc:
        errors.append(f"W87 source syntax error: {exc}")
        return

    capture = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "capture_paper_execution_admission"
        ),
        None,
    )
    if capture is None:
        errors.append("W87 capture entrypoint missing from AST")
    else:
        parameter_names = {
            arg.arg
            for arg in (
                list(capture.args.posonlyargs)
                + list(capture.args.args)
                + list(capture.args.kwonlyargs)
            )
        }
        for forbidden_parameter in ("now", "observed_at", "captured_at"):
            if forbidden_parameter in parameter_names:
                errors.append(
                    f"W87 capture entrypoint exposes caller-controlled clock: {forbidden_parameter}"
                )

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
                errors.append(
                    f"W87 admission imports forbidden execution/network surface at line "
                    f"{node.lineno}: {module}"
                )
        if isinstance(node, ast.Call):
            name = _call_name(node.func).lower()
            if name in FORBIDDEN_CALL_NAMES:
                errors.append(
                    f"W87 admission invokes forbidden execution/mutation call at line "
                    f"{node.lineno}: {name}"
                )


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
