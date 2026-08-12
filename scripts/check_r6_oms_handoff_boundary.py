from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BROKER_DIR = ROOT / "src/autotrade/brokers"
OMS = ROOT / "src/autotrade/oms.py"
WRITER = BROKER_DIR / "alpaca_paper_writer.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_oms_handoff_boundary.py"
SELF_TEST = "tests/test_r6_oms_handoff_boundary.py"

OMS_REQUIRED = (
    "class ExternalSubmissionHandoff:",
    "def validate_for_external_submission(",
    "def stage_external_submission(",
    "def verify_external_submission_handoff(",
    'event_type="EXTERNAL_ORDER_HANDOFF_AUTHORIZED"',
    "if current.status is OrderStatus.VALIDATED:",
    "status=OrderStatus.SUBMITTING",
    "self._append_idempotent(",
    "self._orders.update(staged)",
)
WRITER_REQUIRED = (
    "oms: OrderManagementSystem",
    "external_handoff: ExternalSubmissionHandoff",
    "external_handoff.handoff_id != approval.approval_hash",
    "oms.verify_external_submission_handoff(external_handoff)",
)


def main() -> int:
    errors: list[str] = []
    if not OMS.is_file():
        errors.append("OMS source missing")
    else:
        source = OMS.read_text(encoding="utf-8")
        for anchor in OMS_REQUIRED:
            if anchor not in source:
                errors.append(f"OMS handoff anchor missing: {anchor}")
        errors.extend(_validate_oms_ordering(source))

    if not WRITER.is_file():
        errors.append("R6 PAPER writer missing")
    else:
        writer = WRITER.read_text(encoding="utf-8")
        for anchor in WRITER_REQUIRED:
            if anchor not in writer:
                errors.append(f"writer OMS-handoff anchor missing: {anchor}")

    for path in sorted(BROKER_DIR.glob("alpaca_paper_*.py")):
        errors.extend(_scan_broker_source(path.read_text(encoding="utf-8"), path))

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file():
            errors.append(f"{label} workflow missing")
            continue
        text = workflow.read_text(encoding="utf-8")
        if SELF_COMMAND not in text:
            errors.append(f"{label}: OMS external-handoff checker is not wired into CI")
    if R6.is_file() and SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: OMS handoff adversarial tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 OMS external-handoff boundary: PASS "
        "(OMS-owned VALIDATED->SUBMITTING; durable handoff required; direct R6 mutation denied)"
    )
    return 0


def _validate_oms_ordering(source: str) -> list[str]:
    errors: list[str] = []
    start = source.find("    def stage_external_submission(")
    end = source.find("    def verify_external_submission_handoff(", start)
    if start < 0 or end < 0:
        return ["OMS stage/verify method boundaries missing"]
    method = source[start:end]
    event_pos = method.find('event_type="EXTERNAL_ORDER_HANDOFF_AUTHORIZED"')
    update_pos = method.find("self._orders.update(staged)")
    if event_pos < 0 or update_pos < 0 or event_pos > update_pos:
        errors.append(
            "OMS external handoff must durably append authorization event before SUBMITTING update"
        )
    if "self._broker.submit" in method:
        errors.append("OMS external handoff staging must never invoke broker.submit")
    return errors


def _scan_broker_source(source: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _call_name(node.func) == "ExternalSubmissionHandoff":
                errors.append(
                    f"{path}:{node.lineno}: R6 broker modules may not construct OMS handoff attestations"
                )
            if _is_direct_order_store_update(node):
                errors.append(
                    f"{path}:{node.lineno}: direct R6 OrderStore.update is forbidden; use OMS handoff"
                )
            for keyword in node.keywords:
                if keyword.arg == "status" and _is_order_status_submitting(keyword.value):
                    errors.append(
                        f"{path}:{node.lineno}: R6 broker modules may not synthesize SUBMITTING status"
                    )
    return errors


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_direct_order_store_update(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "update":
        return False
    base = func.value
    if isinstance(base, ast.Name):
        return base.id in {"order_store", "orders"}
    if isinstance(base, ast.Attribute):
        return base.attr in {"_orders", "order_store"}
    return False


def _is_order_status_submitting(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "SUBMITTING"
        and isinstance(node.value, ast.Name)
        and node.value.id == "OrderStatus"
    )


if __name__ == "__main__":
    raise SystemExit(main())
