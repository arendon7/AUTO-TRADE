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
FULL = ROOT / ".github/workflows/mac-standalone-full.yml"

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
    for path in (SERVER, HTML, OPEN, INSTALL, CORE, R6, FULL):
        if not path.is_file():
            errors.append(f"missing Mac Control Center contract file: {path.relative_to(ROOT)}")

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
            "MAC_STANDALONE_MANIFEST.txt",
            "scripts/mac_bootstrap.sh",
            "scripts/check_mac_standalone_boundary.py",
            "scripts/check_mac_dashboard_boundary.py",
            "R6_EXTERNAL_PAPER_WRITE=DISABLED",
        ):
            if anchor not in text:
                errors.append(f"standalone installer missing safety anchor: {anchor}")
        if "curl " in text or "wget " in text:
            errors.append("user-facing installer must not download runtime/dependencies")
        for forbidden in FORBIDDEN:
            if forbidden in text:
                errors.append(f"installer contains forbidden execution surface: {forbidden}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if workflow.is_file() and "python scripts/check_mac_dashboard_boundary.py" not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: dashboard boundary not wired")
    if R6.is_file():
        text = R6.read_text(encoding="utf-8")
        for test in ("tests/test_mac_dashboard.py", "tests/test_mac_doctor_provenance.py"):
            if test not in text:
                errors.append(f"R6 Authority missing functional test: {test}")

    if FULL.is_file():
        text = FULL.read_text(encoding="utf-8")
        for anchor in (
            "ABRIR_AUTO_TRADE.command",
            "INSTALAR_AUTO_TRADE.command",
            "web/**",
            "scripts/check_mac_dashboard_boundary.py",
            "tests/test_mac_dashboard.py",
            "AUTO-TRADE-R6-MAC-FULL/web/mac_dashboard.html",
            "AUTO-TRADE-R6-MAC-FULL/scripts/mac_dashboard.py",
        ):
            if anchor not in text:
                errors.append(f"FULL standalone workflow missing dashboard contract: {anchor}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac Control Center boundary: PASS "
        "(localhost-only; ephemeral PAPER credentials; safe allowlist; FULL standalone integration; no Final Freshness/staging/POST/LIVE surface)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
