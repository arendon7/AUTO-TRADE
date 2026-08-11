from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "src/autotrade/brokers/alpaca_paper_canary_coordinator.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_canary_coordinator_boundary.py"
SELF_TEST = "tests/test_r6_canary_coordinator_boundary.py"

REQUIRED = (
    "class PaperCanaryCoordinator:",
    "class PreparedPaperCanaryPackage:",
    "network_write_authorized: bool",
    'next_action: str',
    '"OPERATOR_DECISION_REQUIRED"',
    "self._oms.validate_for_external_submission(",
    "submission_registry.prepare(binding)",
    "permit_registry.issue(approval)",
    "deterministic_canary_attempt_id(",
    "risk_decision_fingerprint",
    '"risk_decision_fingerprint": risk_decision_fingerprint(decision)',
)
FORBIDDEN_IMPORT_FRAGMENTS = (
    "alpaca_paper_writer",
    "urllib",
    "http.client",
    "requests",
    "socket",
    "websockets",
)
FORBIDDEN_CALL_NAMES = {
    "submit_once",
    "stage_external_submission",
    "post",
    "send",
    "write",
    "urlopen",
    "create_connection",
    "connect",
    "record_operator_approval",
    "consume",
}


def main() -> int:
    errors: list[str] = []
    if not COORDINATOR.is_file():
        errors.append("R6 offline canary coordinator source missing")
    else:
        source = COORDINATOR.read_text(encoding="utf-8")
        for anchor in REQUIRED:
            if anchor not in source:
                errors.append(f"coordinator safety anchor missing: {anchor}")
        errors.extend(_scan_coordinator_source(source, COORDINATOR))
        errors.extend(_validate_ordering(source))

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file():
            errors.append(f"{label} workflow missing")
            continue
        text = workflow.read_text(encoding="utf-8")
        if SELF_COMMAND not in text:
            errors.append(f"{label}: canary coordinator checker is not wired into CI")
    if R6.is_file() and SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: canary coordinator adversarial test is not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 offline canary coordinator boundary: PASS "
        "(VALIDATED-only preparation; no writer/network/operator minting)"
    )
    return 0


def _scan_coordinator_source(source: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                names = [node.module or ""]
            for name in names:
                if any(fragment in name for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                    errors.append(f"{path}:{node.lineno}: forbidden coordinator import {name}")
        if isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in FORBIDDEN_CALL_NAMES:
                errors.append(f"{path}:{node.lineno}: forbidden coordinator call {call_name}")
            if call_name == "ExternalSubmissionHandoff":
                errors.append(f"{path}:{node.lineno}: coordinator may not construct OMS handoff")
        if isinstance(node, ast.Attribute) and node.attr == "SUBMITTING":
            if isinstance(node.value, ast.Name) and node.value.id == "OrderStatus":
                errors.append(f"{path}:{node.lineno}: coordinator may not reference SUBMITTING status")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if "paper-api.alpaca.markets/v2/orders" in lowered or "api.alpaca.markets/v2/orders" in lowered:
                errors.append(f"{path}:{node.lineno}: coordinator may not embed order-write endpoint")
    return errors


def _validate_ordering(source: str) -> list[str]:
    errors: list[str] = []
    issue = source.find("permit = permit_registry.issue(approval)")
    replay = source.find("replay = self._oms.validate_for_external_submission(", issue)
    package = source.find("package = _build_package(", replay)
    if issue < 0 or replay < 0 or package < 0 or not issue < replay < package:
        errors.append("coordinator must issue permit, revalidate OMS brokerlessly, then build package")
    if '"network_write_authorized": False' not in source:
        errors.append("coordinator package must hard-code network_write_authorized=False")
    if '"next_action": "OPERATOR_DECISION_REQUIRED"' not in source:
        errors.append("coordinator package must hard-code OPERATOR_DECISION_REQUIRED")
    return errors


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
