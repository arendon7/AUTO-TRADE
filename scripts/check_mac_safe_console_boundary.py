from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "scripts/mac_safe_console.py"
START = ROOT / "scripts/mac_start.sh"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_mac_safe_console_boundary.py"
SELF_TEST = "tests/test_mac_safe_console.py"

ALLOWED_SCRIPT_TARGETS = {
    "scripts/mac_doctor.py",
    "scripts/mac_rehearsal.sh",
    "scripts/r6_inspect_paper_readiness.py",
    "scripts/r6_external_paper_preflight.py",
    "scripts/r6_external_paper_market_preflight.py",
}
FORBIDDEN_TEXT = (
    "r6_execute_paper_canary.py",
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "stage_external_submission",
    "submit_once",
    "--execute-paper-canary",
    "APCA_API_SECRET_KEY=",
    "APCA_API_KEY_ID=",
)


def main() -> int:
    errors: list[str] = []
    for path in (CONSOLE, START):
        if not path.is_file():
            errors.append(f"missing Mac safe entrypoint: {path.relative_to(ROOT)}")
            continue
        source = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in source:
                errors.append(
                    f"{path.relative_to(ROOT)} contains forbidden execution/secret surface: {forbidden}"
                )
        if "R6_EXTERNAL_PAPER_WRITE=ENABLED" in source and "BLOCKED" not in source:
            errors.append(
                f"{path.relative_to(ROOT)} references enabled write gate without explicit refusal semantics"
            )

    if CONSOLE.is_file():
        source = CONSOLE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(CONSOLE))
        if 'env[WRITE_ENV] = "DISABLED"' not in source:
            errors.append("safe console must force the child write gate to DISABLED")
        if 'os.environ.get(WRITE_ENV) == WRITE_ENABLED' not in source:
            errors.append("safe console must refuse an inherited ENABLED write gate")
        for target in ALLOWED_SCRIPT_TARGETS:
            if target not in source:
                errors.append(f"safe console expected audited command target is missing: {target}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"system", "popen"}:
                    errors.append("safe console may not use shell system/popen execution")

    if START.is_file():
        source = START.read_text(encoding="utf-8")
        if "export R6_EXTERNAL_PAPER_WRITE=DISABLED" not in source:
            errors.append("mac_start.sh must force R6_EXTERNAL_PAPER_WRITE=DISABLED")
        if "scripts/mac_bootstrap.sh" not in source:
            errors.append("mac_start.sh must bootstrap safely when .venv is absent")
        if "mac_safe_console.py" not in source:
            errors.append("mac_start.sh must delegate all operator actions to the safe console")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: Mac safe-console checker is not wired into CI")
    if R6.is_file() and SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: Mac safe-console tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac safe console boundary: PASS "
        "(no execution command; write gate forced disabled; only local/read-only/GET-only surfaces)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
