from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "scripts/r6_precanary_status.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
MAC = ROOT / ".github/workflows/mac-rehearsal-artifact.yml"
SELF_COMMAND = "python scripts/check_r6_precanary_status_boundary.py"
SELF_TEST = "tests/test_r6_precanary_status.py"

FORBIDDEN_IMPORTS = (
    "requests",
    "httpx",
    "urllib",
    "http.client",
    "socket",
    "ssl",
    "websockets",
    "subprocess",
    "sqlite3",
)
FORBIDDEN_TEXT = (
    "r6_execute_paper_canary.py",
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "stage_external_submission",
    "submit_once(",
    "/v2/orders",
    ".write(",
    "SQLiteRuntime(",
    "open(\"w",
    "open('w",
)


def main() -> int:
    errors: list[str] = []
    if not STATUS.is_file():
        errors.append("missing scripts/r6_precanary_status.py")
    else:
        source = STATUS.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(STATUS))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            for module in modules:
                if any(module == p or module.startswith(p + ".") for p in FORBIDDEN_IMPORTS):
                    errors.append(f"pre-canary status:{node.lineno}: forbidden import {module}")
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in source:
                errors.append(f"pre-canary status contains forbidden mutation/execution surface: {forbidden}")
        for anchor in (
            "inspect_market_aware_readiness",
            "expanded.is_symlink()",
            "child.is_symlink()",
            '"RECONCILIATION_ONLY"',
            '"execution_authorized": False',
            '"external_post_authorized": False',
            '"broker_write_performed": False',
            '"capital_authority": "NONE"',
            '"profitability_claim": False',
            '"live_trading": "BLOCKED"',
            '"READY means ready only for the named next gate; never POST authority"',
        ):
            if anchor not in source:
                errors.append(f"pre-canary status safety anchor missing: {anchor}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority"), (MAC, "Mac Artifact")):
        if not workflow.is_file():
            errors.append(f"missing workflow: {workflow.relative_to(ROOT)}")
            continue
        text = workflow.read_text(encoding="utf-8")
        if SELF_COMMAND not in text:
            errors.append(f"{label}: pre-canary checker not wired into CI")
        if workflow in (R6, MAC) and SELF_TEST not in text:
            errors.append(f"{label}: pre-canary functional test not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 pre-canary status boundary: PASS "
        "(local read-only classification; symlink fail-closed; UNKNOWN=>reconciliation-only; "
        "READY never grants POST/capital/LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
