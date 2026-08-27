from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/autotrade/paper_execution_canary_preparation.py"
GUARD = ROOT / "src/autotrade/paper_execution_canary_preparation_guard.py"
TEST = ROOT / "tests/test_w87_paper_execution_canary_preparation.py"
WORKFLOW = ROOT / ".github/workflows/w87-paper-execution-admission.yml"
SELF_COMMAND = "python scripts/check_w87_paper_execution_canary_preparation_boundary.py"
TEST_COMMAND = "pytest -q tests/test_w87_paper_execution_canary_preparation.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "socket",
    "urllib",
    "websocket",
    "websockets",
    "autotrade.first_canary_real_paper_execution",
    "autotrade.first_canary_execution_gate",
    "autotrade.first_canary_external_post_consent",
    "autotrade.brokers.alpaca_paper_crypto_writer",
    "autotrade.brokers.alpaca_paper_crypto_reconciliation",
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
    "stage_external_submission",
    "consume_external_post_consent",
    "execute_first_canary_once",
    "execute_real_paper",
}


def main() -> int:
    errors: list[str] = []
    for path, label in (
        (SOURCE, "W87-C preparation source"),
        (GUARD, "W87-C preparation guard"),
        (TEST, "W87-C adversarial tests"),
        (WORKFLOW, "W87 dedicated workflow"),
    ):
        if not path.is_file():
            errors.append(f"missing {label}: {path}")

    source = SOURCE.read_text(encoding="utf-8") if SOURCE.is_file() else ""
    guard = GUARD.read_text(encoding="utf-8") if GUARD.is_file() else ""
    combined = source + "\n" + guard

    for marker in (
        'PAPER_EXECUTION_CANARY_PREPARATION_VERSION = "W87_PAPER_EXECUTION_CANARY_PREPARATION_V1"',
        "class PaperExecutionCanaryPreparationReceipt:",
        "def prepare_paper_execution_canary(",
        "CryptoPaperCanaryCoordinator",
        "coordinator.prepare_entry(",
        "SQLiteCryptoPaperLifecycle(runtime)",
        "_count_unresolved_local_unknown(runtime)",
        'certified_tracks=_CERTIFIED_R6_TRACKS',
        "reconciliation_clean=True",
        'package.next_action != "OPERATOR_DECISION_REQUIRED"',
        "package.execution_deadline) > _utc(risk_result.receipt.valid_until)",
        '"network_write_authorized": package.network_write_authorized',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "def prepare_guarded_paper_execution_canary(",
        "before_safety = safety.state_store.get()",
        "before_portfolio = portfolio_store.get()",
        "after_safety = safety.state_store.get()",
        "after_portfolio = portfolio_store.get()",
        "after_safety != before_safety",
        "after_portfolio != before_portfolio",
    ):
        if marker not in combined:
            errors.append(f"W87-C missing permanent boundary marker: {marker}")

    for forbidden in (
        '"network_write_authorized": True',
        '"paper_execution_authorized": True',
        '"external_execution_authorized": True',
        '"runtime_execution_authorized": True',
        '"live_trading": "ENABLED"',
        '"next_action": "POST_ALLOWED"',
        "HttpsAlpacaPaperCryptoWriteTransport(",
        "AlpacaPaperCryptoWriter(",
        "consume_external_post_consent(",
        "execute_first_canary_once(",
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
    ):
        if forbidden in combined:
            errors.append(f"W87-C contains forbidden execution surface: {forbidden}")

    _check_ast(SOURCE, source, errors)
    _check_ast(GUARD, guard, errors)
    _check_entrypoint_parameters(source, guard, errors)

    if TEST.is_file():
        tests = TEST.read_text(encoding="utf-8")
        for marker in (
            "test_w87_canary_preparation_reuses_r6_and_stops_at_operator_decision",
            "test_w87_canary_preparation_entrypoint_exposes_no_clock_flags_or_execution_inputs",
            "test_w87_canary_preparation_refuses_stale_w86_or_risk_before_oms",
            "test_w87_canary_preparation_refuses_kill_switch_or_safety_version_drift",
            "test_w87_canary_preparation_refuses_portfolio_drift_before_oms",
            "test_w87_canary_preparation_detects_safety_race_after_local_preparation",
            "test_w87_canary_preparation_refuses_local_unknown_state",
            "test_w87_canary_preparation_is_idempotent_for_same_exact_contract_and_instant",
            "test_w87_canary_preparation_rejects_evidence_binding_tamper",
            "test_w87_canary_preparation_receipt_rejects_every_authority_escalation",
        ):
            if marker not in tests:
                errors.append(f"W87-C tests missing adversarial contract: {marker}")

    if WORKFLOW.is_file():
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            SELF_COMMAND,
            TEST_COMMAND,
            "python scripts/check_w87_paper_execution_risk_contract_boundary.py",
            "python scripts/check_w86_paper_runtime_readiness_seal_boundary.py",
            "python scripts/check_r6_authority.py",
            "python scripts/check_r6_live_deny_boundary.py",
        ):
            if required not in workflow:
                errors.append(f"W87 workflow missing preparation re-proof: {required}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "AUTO-TRADE W87-C PAPER canary preparation boundary: PASS "
        "(exact W86/W87 bindings; authoritative Safety/Portfolio pre/post guard; "
        "existing R6 coordinator only; OMS VALIDATED + ENTRY_PREPARED; local UNKNOWN=0; "
        "deadline cannot outlive W87 risk/seal window; OPERATOR_DECISION_REQUIRED; "
        "no credentials, writer, broker POST, capital, external execution or LIVE authority)"
    )
    return 0


def _check_ast(path: Path, source: str, errors: list[str]) -> None:
    if not source:
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        errors.append(f"{path.name} syntax error: {exc}")
        return

    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        for module in modules:
            if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                errors.append(f"{path.name} imports forbidden execution/network module: {module}")

        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALL_NAMES:
                errors.append(f"{path.name} calls forbidden execution method: {name}")


def _check_entrypoint_parameters(source: str, guard: str, errors: list[str]) -> None:
    forbidden = {
        "now",
        "observed_at",
        "prepared_at",
        "certified_tracks",
        "reconciliation_clean",
        "unresolved_unknown_orders",
        "relevant_open_orders",
        "confirmed_pair_position_quantity",
        "credentials",
        "writer",
        "transport",
        "environment",
        "live",
    }
    for text, name in (
        (source, "prepare_paper_execution_canary"),
        (guard, "prepare_guarded_paper_execution_canary"),
    ):
        if not text:
            continue
        tree = ast.parse(text)
        function = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
            ),
            None,
        )
        if function is None:
            errors.append(f"W87-C entrypoint missing: {name}")
            continue
        names = {
            arg.arg
            for arg in (
                list(function.args.posonlyargs)
                + list(function.args.args)
                + list(function.args.kwonlyargs)
            )
        }
        for value in sorted(names & forbidden):
            errors.append(f"W87-C entrypoint {name} exposes caller-controlled authority input: {value}")


def _call_name(value: ast.expr) -> str:
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
