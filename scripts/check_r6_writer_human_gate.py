from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "src/autotrade/brokers/alpaca_paper_writer.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_writer_human_gate.py"
SELF_TEST = "tests/test_r6_writer_human_gate_boundary.py"

REQUIRED = (
    "prepared_package: PreparedPaperCanaryPackage",
    "operator_decision: PaperOperatorDecision",
    "operator_registry: SQLitePaperOperatorDecisionRegistry",
    "execution_stage: PaperExecutionStageResult",
    "PaperOperatorDecisionContext.from_prepared_package(",
    "durable_operator = operator_registry.get(",
    "durable_operator.status is not PaperOperatorDecisionStatus.CONSUMED",
    "durable_operator.consumed_attempt_id != attempt_id",
    "execution_stage.package_hash != prepared_package.package_hash",
    "execution_stage.operator_decision_hash != operator_decision.decision_hash",
    "execution_stage.handoff != external_handoff",
    "permit_registry.get_issued_event_hash(approval.approval_hash)",
    "prepared_package_hash=prepared_package.package_hash",
    "operator_decision_hash=operator_decision.decision_hash",
)
FORBIDDEN_WRITER_CALLS = {
    "record_operator_approval",
    "stage_external_submission",
}


def main() -> int:
    errors: list[str] = []
    if not WRITER.is_file():
        errors.append("R6 PAPER writer source missing")
    else:
        source = WRITER.read_text(encoding="utf-8")
        for anchor in REQUIRED:
            if anchor not in source:
                errors.append(f"writer human gate anchor missing: {anchor}")
        errors.extend(_scan_writer(source, WRITER))
        errors.extend(_validate_ordering(source))

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: writer human gate checker is not wired into permanent CI")
    if not R6.is_file() or SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: writer human-gate adversarial tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 writer human-execution gate: PASS "
        "(CONSUMED human decision + exact execution stage before permit/UNKNOWN/POST)"
    )
    return 0


def _scan_writer(source: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call = _call_name(node.func)
            if call in FORBIDDEN_WRITER_CALLS:
                errors.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden writer call {call}")
    return errors


def _validate_ordering(source: str) -> list[str]:
    errors: list[str] = []
    ordered = (
        ("operator_get", "durable_operator = operator_registry.get("),
        ("operator_consumed", "durable_operator.status is not PaperOperatorDecisionStatus.CONSUMED"),
        ("execution_stage", "if execution_stage.package_hash != prepared_package.package_hash:"),
        ("permit_get", "permit = permit_registry.get(approval.approval_hash)"),
        ("pre_consume", "phase=PaperFinalWritePhase.PRE_CONSUME"),
        ("permit_consume", "permit_registry.consume("),
        ("unknown", "submission_registry.mark_submit_attempt_unknown("),
        ("request", "request = AlpacaPaperWriteRequest("),
        ("pre_io", "phase=PaperFinalWritePhase.PRE_IO"),
        ("transport_write", "response = self._transport.write(request)"),
    )
    positions: list[tuple[str, int]] = [(name, source.find(anchor)) for name, anchor in ordered]
    missing = [name for name, pos in positions if pos < 0]
    if missing:
        errors.append(f"writer human gate ordering anchors missing: {', '.join(missing)}")
    elif any(left[1] >= right[1] for left, right in zip(positions, positions[1:])):
        errors.append(
            "writer ordering must be human durable get/CONSUMED/execution-stage, permit, PRE_CONSUME, permit consume, UNKNOWN, request, PRE_IO, single write"
        )
    if source.count("self._transport.write(request)") != 1:
        errors.append("writer must contain exactly one transport.write(request) call")
    if "record_operator_approval(" in source:
        errors.append("writer must never mint human operator approval")
    if "stage_external_submission(" in source:
        errors.append("writer must never stage OMS; execution bridge owns SUBMITTING")
    return errors


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
