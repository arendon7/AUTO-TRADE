from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BROKER_DIR = ROOT / "src/autotrade/brokers"
BRIDGE = BROKER_DIR / "alpaca_paper_execution_bridge.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_execution_bridge_boundary.py"
SELF_TEST = "tests/test_r6_execution_bridge_boundary.py"

FORBIDDEN_BRIDGE_IMPORTS = (
    "alpaca_paper_writer",
    "urllib",
    "http.client",
    "requests",
    "socket",
    "websockets",
    "openai",
    "anthropic",
    "autotrade.research",
)
FORBIDDEN_BRIDGE_CALLS = {
    "record_operator_approval",
    "submit_once",
    "write",
    "send",
    "urlopen",
    "Request",
}
REQUIRED_BRIDGE_ANCHORS = (
    "class PaperCanaryExecutionBridge:",
    "package: PreparedPaperCanaryPackage",
    "operator_decision: PaperOperatorDecision",
    "operator_registry: SQLitePaperOperatorDecisionRegistry",
    "PaperOperatorDecisionContext.from_prepared_package(package)",
    "operator_registry.get(expected_context.preparation_hash)",
    "operator_registry.consume(",
    "self._oms.stage_external_submission(",
    "risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint",
    "package.risk_decision_safety_state_version",
    "package.market_fingerprint",
    "consumed.status is not PaperOperatorDecisionStatus.CONSUMED",
)


def main() -> int:
    errors: list[str] = []
    if not BRIDGE.is_file():
        errors.append("R6 execution bridge source missing")
    else:
        source = BRIDGE.read_text(encoding="utf-8")
        for anchor in REQUIRED_BRIDGE_ANCHORS:
            if anchor not in source:
                errors.append(f"execution bridge anchor missing: {anchor}")
        errors.extend(_scan_bridge(source, BRIDGE))
        errors.extend(_validate_bridge_ordering(source))

    # No other R6 broker production module may transition OMS into SUBMITTING.
    for path in sorted(BROKER_DIR.glob("alpaca_paper_*.py")):
        if path.resolve() == BRIDGE.resolve():
            continue
        for lineno, call in _named_calls(path, "stage_external_submission"):
            errors.append(
                f"{_relative(path)}:{lineno}: OMS external staging is execution-bridge-only ({call})"
            )

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: execution bridge checker is not wired into permanent CI")
    if not R6.is_file() or SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: execution bridge adversarial tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 execution bridge boundary: PASS "
        "(human decision consumed before OMS staging; bridge-only SUBMITTING; no network/AI authority)"
    )
    return 0


def _scan_bridge(source: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]
    rel = _relative(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules = [base]
            modules.extend(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
            )
        else:
            modules = []
        for module in modules:
            if any(fragment in module for fragment in FORBIDDEN_BRIDGE_IMPORTS):
                errors.append(f"{rel}:{node.lineno}: forbidden bridge import {module}")
        if isinstance(node, ast.Call):
            call = _call_name(node.func)
            if call in FORBIDDEN_BRIDGE_CALLS:
                errors.append(f"{rel}:{node.lineno}: forbidden bridge call {call}")
    return errors


def _validate_bridge_ordering(source: str) -> list[str]:
    errors: list[str] = []
    get_pos = source.find("durable = operator_registry.get(")
    consume_pos = source.find("consumed = operator_registry.consume(")
    stage_pos = source.find("staged, handoff = self._oms.stage_external_submission(")
    if get_pos < 0 or consume_pos < 0 or stage_pos < 0 or not get_pos < consume_pos < stage_pos:
        errors.append("execution bridge must verify durable decision, consume it, then stage OMS")
    if "network_write_authorized is not False" not in source:
        errors.append("execution bridge must reject any package claiming network authority")
    if 'next_action != "OPERATOR_DECISION_REQUIRED"' not in source:
        errors.append("execution bridge must require OPERATOR_DECISION_REQUIRED package")
    return errors


def _named_calls(path: Path, name: str) -> list[tuple[int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError):
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) == name:
            found.append((node.lineno, name))
    return found


def _relative(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
