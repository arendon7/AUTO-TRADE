from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts/mac_dashboard.py"
HTML = ROOT / "web/mac_dashboard.html"
OPEN = ROOT / "ABRIR_AUTO_TRADE.command"
INSTALL = ROOT / "INSTALAR_AUTO_TRADE.command"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
MAC = ROOT / ".github/workflows/mac-rehearsal-artifact.yml"

FORBIDDEN = (
    "r6_execute_paper_canary.py",
    "r6_connectivity_bound_final_freshness.py",
    "connectivity_workspace_post",
    "connectivity_workspace_stage",
    "alpaca_paper_writer",
    "stage_external_submission",
    "submit_once",
    "shell=True",
)


def main() -> int:
    errors: list[str] = []
    for path in (SERVER, HTML, OPEN, INSTALL, CORE, R6, MAC):
        if not path.is_file():
            errors.append(f"missing dashboard/full-Mac contract file: {path.relative_to(ROOT)}")

    if SERVER.is_file():
        text = SERVER.read_text(encoding="utf-8")
        for anchor in (
            '"127.0.0.1"',
            "SAFE_ACTIONS",
            "secrets.token_urlsafe",
            "X-CSRF-Token",
            "Cache-Control",
            "Content-Security-Policy",
            "scripts/mac_safe_console.py",
            'env[WRITE_ENV] = "DISABLED"',
            '"order_execution_from_dashboard": False',
            "subprocess.run(",
        ):
            if anchor not in text:
                errors.append(f"dashboard safety anchor missing: {anchor}")
        for forbidden in FORBIDDEN:
            if forbidden in text:
                errors.append(f"dashboard contains forbidden execution surface: {forbidden}")
        if 'if host != "127.0.0.1"' not in text:
            errors.append("dashboard must reject non-loopback bind")
        if 'env[WRITE_ENV] = "ENABLED"' in text:
            errors.append("dashboard may never enable external PAPER write")

    if HTML.is_file():
        html = HTML.read_text(encoding="utf-8")
        for anchor in (
            "AUTO-TRADE R6 · Control Center",
            "PAPER WRITE · DISABLED",
            "LIVE · BLOCKED",
            'type="password"',
            "Las credenciales no se guardan",
            "fuera del dashboard",
            "NO DISPONIBLE",
        ):
            if anchor not in html:
                errors.append(f"dashboard UI safety/UX anchor missing: {anchor}")
        for forbidden in (
            "localStorage",
            "sessionStorage",
            '<script src=',
            '<link rel="stylesheet" href=',
            "r6_execute_paper_canary",
            "r6_connectivity_bound_final_freshness",
        ):
            if forbidden in html:
                errors.append(f"dashboard UI contains forbidden persistence/execution surface: {forbidden}")

    if OPEN.is_file():
        text = OPEN.read_text(encoding="utf-8")
        for anchor in (
            "R6_EXTERNAL_PAPER_WRITE=DISABLED",
            "unset APCA_API_KEY_ID",
            "unset APCA_API_SECRET_KEY",
            "scripts/mac_dashboard.py",
            "INSTALAR_AUTO_TRADE.command",
        ):
            if anchor not in text:
                errors.append(f"dashboard opener missing safe anchor: {anchor}")
        for forbidden in FORBIDDEN:
            if forbidden in text:
                errors.append(f"dashboard opener contains forbidden execution surface: {forbidden}")

    if INSTALL.is_file():
        text = INSTALL.read_text(encoding="utf-8")
        for anchor in (
            "runtime/python-3.12.10-macos11.pkg",
            "--no-index",
            "runtime/wheels",
            "R6_EXTERNAL_PAPER_WRITE=DISABLED",
            "scripts/check_mac_dashboard_boundary.py",
            "MAC_FULL_INSTALL_RECEIPT.json",
        ):
            if anchor not in text:
                errors.append(f"FULL installer missing offline/safety anchor: {anchor}")
        if "curl " in text or "wget " in text:
            errors.append("FULL installer must be offline and may not download runtime")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if workflow.is_file():
            text = workflow.read_text(encoding="utf-8")
            if "python scripts/check_mac_dashboard_boundary.py" not in text:
                errors.append(f"{label}: dashboard boundary not wired")
    if R6.is_file() and "tests/test_mac_dashboard.py" not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: dashboard functional tests not wired")

    if MAC.is_file():
        text = MAC.read_text(encoding="utf-8")
        for anchor in (
            "AUTO-TRADE-R6-MAC-FULL",
            "python-3.12.10-macos11.pkg",
            "websockets==16.1.1",
            "tests/test_mac_dashboard.py",
            "scripts/check_mac_dashboard_boundary.py",
        ):
            if anchor not in text:
                errors.append(f"Mac FULL artifact workflow missing: {anchor}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac dashboard/FULL installer boundary: PASS "
        "(localhost-only UI; ephemeral PAPER credentials; safe allowlist; offline runtime bundle; no Final Freshness/staging/POST/LIVE surface)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
