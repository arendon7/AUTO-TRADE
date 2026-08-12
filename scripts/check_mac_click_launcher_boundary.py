from __future__ import annotations

from pathlib import Path
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "AUTO_TRADE_MAC.command"
FIRST_RUN = ROOT / "LEEME_PRIMERO_MAC.md"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
MAC_CI = ROOT / ".github/workflows/mac-rehearsal-artifact.yml"
SELF_COMMAND = "python scripts/check_mac_click_launcher_boundary.py"
SELF_TEST = "tests/test_mac_click_launcher.py"

FORBIDDEN = (
    "r6_execute_paper_canary.py",
    "r6_connectivity_bound_final_freshness.py",
    "--execute-paper-canary",
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "stage_external_submission",
    "submit_once",
    "source .env",
    "source \".env\"",
    "curl ",
    "wget ",
    "mac_safety_rehearsal.py",
)


def main() -> int:
    errors: list[str] = []
    if not FIRST_RUN.is_file():
        errors.append("missing Mac first-run guide: LEEME_PRIMERO_MAC.md")
    else:
        guide = FIRST_RUN.read_text(encoding="utf-8")
        for anchor in (
            "AUTO_TRADE_MAC.command",
            "Gatekeeper",
            "CapitalSafetyKernel.evaluate",
            "external_execution_authorized=false",
            "External PAPER order enviado por el proyecto: **0**",
            "LIVE trading: **BLOCKED**",
        ):
            if anchor not in guide:
                errors.append(f"Mac first-run guide safety anchor missing: {anchor}")

    if not LAUNCHER.is_file():
        errors.append("missing root Finder launcher: AUTO_TRADE_MAC.command")
    else:
        source = LAUNCHER.read_text(encoding="utf-8")
        mode = LAUNCHER.stat().st_mode
        if not mode & stat.S_IXUSR:
            errors.append("AUTO_TRADE_MAC.command must be executable")
        if "export R6_EXTERNAL_PAPER_WRITE=DISABLED" not in source:
            errors.append("Finder launcher must force external PAPER write DISABLED")
        if '"${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED"' not in source:
            errors.append("Finder launcher must refuse inherited ENABLED write authority")
        if "unset APCA_API_KEY_ID" not in source or "unset APCA_API_SECRET_KEY" not in source:
            errors.append("Finder launcher must strip Alpaca credentials from its own process")
        if "scripts/mac_bootstrap.sh" not in source:
            errors.append("Finder launcher must use the certified safe bootstrap")
        if "scripts/mac_start.sh" not in source:
            errors.append("Finder launcher must delegate operator actions to mac_start.sh")
        for anchor in (
            "init-workspace",
            "run_safe safety-rehearsal",
            "Capital Safety Kernel real",
            "run_safe pre-canary-status",
            "run_safe build-connectivity-candidate",
            "run_safe prepare-connectivity-candidate",
            "run_safe review-receipt",
            "READY en estado pre-canary",
            "account-preflight",
            "asset-preflight",
            "flat-account-preflight",
            "market-preflight",
            "account -> asset -> flat account -> market",
            "exact us_equity + active + tradable",
            "NO se emiten",
        ):
            if anchor not in source:
                errors.append(f"Finder launcher missing safe first-canary/Safety anchor: {anchor}")
        for forbidden in FORBIDDEN:
            if forbidden in source:
                errors.append(f"Finder launcher contains forbidden surface: {forbidden}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: Finder launcher boundary is not wired into CI")
    if R6.is_file() and SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: Finder launcher tests are not wired into CI")

    if not MAC_CI.is_file():
        errors.append("missing macOS rehearsal/artifact workflow")
    else:
        workflow = MAC_CI.read_text(encoding="utf-8")
        for required in (
            "runs-on: macos-14",
            "bash scripts/mac_bootstrap.sh",
            "AUTO_TRADE_MAC.command",
            "actions/upload-artifact@v4",
        ):
            if required not in workflow:
                errors.append(f"Mac artifact workflow missing required contract: {required}")
        for forbidden in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "R6_EXTERNAL_PAPER_WRITE=ENABLED"):
            if forbidden in workflow:
                errors.append(f"Mac artifact workflow may not expose broker authority: {forbidden}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac Finder launcher boundary: PASS "
        "(double-click entrypoint is broker-inert/write-disabled; pre-canary status + offline preparation/review are safe; "
        "human execution intent, Final Freshness and order execution remain absent)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
