from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/autotrade/paper_execution_risk_contract.py"
TEST = ROOT / "tests/test_w87_paper_execution_risk_contract.py"
DEDICATED = ROOT / ".github/workflows/w87-paper-execution-admission.yml"
CORE = ROOT / ".github/workflows/core-tests.yml"
SELF_COMMAND = "python scripts/check_w87_paper_execution_risk_contract_boundary.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "socket",
    "urllib",
    "websocket",
    "websockets",
    "autotrade.oms",
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
    "connect",
    "execute",
    "executemany",
    "executescript",
}


def main() -> int:
    errors: list[str] = []
    for path, label in (
        (SOURCE, "W87 risk-contract source"),
        (TEST, "W87 risk-contract tests"),
        (DEDICATED, "W87 dedicated workflow"),
        (CORE, "Core Safety workflow"),
    ):
        if not path.is_file():
            errors.append(f"missing {label}: {path}")

    source = SOURCE.read_text(encoding="utf-8") if SOURCE.is_file() else ""
    for marker in (
        'PAPER_EXECUTION_RISK_CONTRACT_VERSION = "W87_PAPER_EXECUTION_RISK_CONTRACT_V1"',
        "class PaperExecutionRiskContractReceipt:",
        "class PaperExecutionRiskContractResult:",
        "def evaluate_paper_execution_risk_contract(",
        "PaperRuntimeReadinessSealStatus.READY",
        "admission.__post_init__()",
        "_validate_w86_source(sealed_result)",
        "_validate_admission_binding(admission=admission, sealed_result=sealed_result)",
        "before_portfolio = portfolio_store.get()",
        "before_safety = safety.state_store.get()",
        "decision = safety.evaluate(",
        "after_safety = safety.state_store.get()",
        "after_portfolio = portfolio_store.get()",
        "decision.safety_state_version != before_safety.version",
        "decision.status is not RiskDecisionStatus.APPROVED",
        "market_fingerprint(market)",
        "intent_fingerprint(intent)",
        "risk_decision_fingerprint(decision)",
        '"separate_human_execution_approval_required": True',
        '"oms_handoff_permitted": False',
        '"capital_reserved": False',
        '"broker_write_performed": False',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        '"next_action": "CANARY_PREPARATION_REQUIRED"',
    ):
        if marker not in source:
            errors.append(f"W87 risk-contract source missing permanent marker: {marker}")

    for forbidden in (
        '"paper_execution_authorized": True',
        '"external_execution_authorized": True',
        '"runtime_execution_authorized": True',
        '"oms_handoff_permitted": True',
        '"capital_reserved": True',
        '"broker_write_performed": True',
        '"live_trading": "ENABLED"',
        "OrderManagementSystem(",
        "AlpacaPaperCryptoWriter(",
        "HttpsAlpacaPaperCryptoWriteTransport(",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
    ):
        if forbidden in source:
            errors.append(
                f"W87 risk contract contains forbidden persistence/execution surface: {forbidden}"
            )

    _check_ast(source, errors)

    if TEST.is_file():
        test_source = TEST.read_text(encoding="utf-8")
        for marker in (
            "test_w87_risk_contract_uses_authoritative_safety_and_grants_zero_execution_authority",
            "test_w87_risk_contract_intent_identity_is_deterministic_from_admission",
            "test_w87_risk_contract_refuses_stale_w86_seal",
            "test_w87_risk_contract_refuses_safety_version_drift_even_when_controls_are_clear",
            "test_w87_risk_contract_refuses_nonflat_or_unreconciled_local_portfolio",
            "test_w87_risk_contract_fails_closed_if_portfolio_changes_during_safety",
            "test_w87_risk_contract_fails_closed_if_safety_changes_after_approval",
            "test_w87_risk_contract_rejects_market_binding_tamper_before_safety",
            "test_w87_risk_contract_receipt_rejects_every_authority_escalation",
        ):
            if marker not in test_source:
                errors.append(f"W87 risk-contract tests missing adversarial contract: {marker}")

    if DEDICATED.is_file():
        workflow = DEDICATED.read_text(encoding="utf-8")
        for required in (
            SELF_COMMAND,
            "pytest -q tests/test_w87_paper_execution_risk_contract.py",
            "python scripts/check_w86_paper_runtime_readiness_seal_boundary.py",
            "python scripts/check_r6_authority.py",
            "python scripts/check_r6_live_deny_boundary.py",
        ):
            if required not in workflow:
                errors.append(f"W87 dedicated workflow missing risk re-proof: {required}")

    if CORE.is_file():
        core = CORE.read_text(encoding="utf-8")
        if SELF_COMMAND not in core:
            errors.append("Core Safety does not enforce W87 risk-contract boundary")
        if "pytest -q tests/test_w87_paper_execution_risk_contract.py" not in core:
            errors.append("Core Safety does not run W87 risk-contract adversarial tests")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "AUTO-TRADE W87 PAPER risk-contract boundary: PASS "
        "(fresh exact W86 seal + exact W87 admission; deterministic BUY LIMIT intent; "
        "authoritative CapitalSafetyKernel only; exact attested market; flat durable portfolio; "
        "Safety/portfolio TOCTOU postcheck; finite authority window; human approval still mandatory; "
        "no persistence, OMS, capital reservation, broker/network write, execution or LIVE authority)"
    )
    return 0


def _check_ast(source: str, errors: list[str]) -> None:
    if not source:
        return
    try:
        tree = ast.parse(source, filename=str(SOURCE))
    except SyntaxError as exc:
        errors.append(f"W87 risk-contract source syntax error: {exc}")
        return

    entrypoint = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "evaluate_paper_execution_risk_contract"
        ),
        None,
    )
    if entrypoint is None:
        errors.append("W87 risk-contract entrypoint missing from AST")
    else:
        parameter_names = {
            arg.arg
            for arg in (
                list(entrypoint.args.posonlyargs)
                + list(entrypoint.args.args)
                + list(entrypoint.args.kwonlyargs)
            )
        }
        for forbidden_parameter in (
            "now",
            "observed_at",
            "evaluated_at",
            "market",
            "intent",
            "decision",
        ):
            if forbidden_parameter in parameter_names:
                errors.append(
                    "W87 risk-contract entrypoint exposes caller-controlled authority input: "
                    f"{forbidden_parameter}"
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
                    "W87 risk contract imports forbidden OMS/execution/network surface at line "
                    f"{node.lineno}: {module}"
                )
        if isinstance(node, ast.Call):
            name = _call_name(node.func).lower()
            if name in FORBIDDEN_CALL_NAMES:
                errors.append(
                    "W87 risk contract invokes forbidden persistence/execution call at line "
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
