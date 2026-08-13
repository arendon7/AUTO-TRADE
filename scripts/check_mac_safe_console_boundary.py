from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "scripts/mac_safe_console.py"
START = ROOT / "scripts/mac_start.sh"
WORKSPACE_INIT = ROOT / "scripts/mac_create_workspace.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_mac_safe_console_boundary.py"

_ALLOWED_TARGETS = {
    "scripts/mac_create_workspace.py",
    "scripts/mac_doctor.py",
    "scripts/mac_rehearsal.sh",
    "scripts/mac_safety_rehearsal.py",
    "scripts/r6_inspect_paper_readiness.py",
    "scripts/r6_precanary_status.py",
    "scripts/r6_external_paper_account_discovery.py",
    "scripts/r6_external_paper_preflight.py",
    "scripts/r6_external_paper_asset_preflight.py",
    "scripts/r6_external_paper_flat_account_preflight.py",
    "scripts/r6_external_paper_market_preflight.py",
    "scripts/r6_build_connectivity_candidate.py",
    "scripts/r6_prepare_connectivity_candidate.py",
    "scripts/r6_connectivity_review_receipt.py",
}
_FORBIDDEN = (
    "r6_execute_paper_canary.py",
    "r6_connectivity_bound_final_freshness.py",
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "stage_external_submission",
    "submit_once",
    "--execute-paper-canary",
)


def main() -> int:
    errors: list[str] = []
    for path in (CONSOLE, START, WORKSPACE_INIT):
        if not path.is_file():
            errors.append(f"missing Mac safe entrypoint: {path.relative_to(ROOT)}")
            continue
        source = path.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN:
            if forbidden in source:
                errors.append(f"{path.name} contains forbidden execution surface: {forbidden}")

    if CONSOLE.is_file():
        source = CONSOLE.read_text(encoding="utf-8")
        for anchor in (
            'env[WRITE_ENV] = "DISABLED"',
            "env.pop(KEY_ENV, None)",
            "env.pop(SECRET_ENV, None)",
            '"account-discovery"',
            '"--allow-paper-account-discovery-read"',
            '"pre-canary-status"',
            '"scripts/r6_precanary_status.py"',
            '"build-connectivity-candidate"',
            '"prepare-connectivity-candidate"',
            '"review-receipt"',
            "credential_free=True",
        ):
            if anchor not in source:
                errors.append(f"Mac safe console anchor missing: {anchor}")
        for target in _ALLOWED_TARGETS:
            if target not in source:
                errors.append(f"Mac safe console audited target missing: {target}")
        tree = ast.parse(source, filename=str(CONSOLE))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"system", "popen"}:
                    errors.append("Mac safe console may not use shell system/popen")

    if START.is_file():
        source = START.read_text(encoding="utf-8")
        for anchor in (
            "export R6_EXTERNAL_PAPER_WRITE=DISABLED",
            "pre-canary-status",
            "build-connectivity-candidate",
            "prepare-connectivity-candidate",
            "review-receipt",
            "CapitalSafetyKernel RiskDecision + OMS VALIDATED",
            "NO Strategy Health",
            "NO external POST authority",
            "account -> asset -> flat account -> market -> connectivity candidate",
        ):
            if anchor not in source:
                errors.append(f"mac_start.sh connectivity anchor missing: {anchor}")

    if WORKSPACE_INIT.is_file():
        source = WORKSPACE_INIT.read_text(encoding="utf-8")
        for forbidden in ("urllib", "requests", "websockets", "AlpacaPaperCredentials"):
            if forbidden in source:
                errors.append(f"workspace initializer contains forbidden network/credential surface: {forbidden}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: Mac safe-console checker is not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac safe console boundary: PASS "
        "(write-disabled; credential stripping on local phases; GET-only account discovery/preflights; "
        "read-only pre-canary status + offline preparation/review; no order execution command)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
