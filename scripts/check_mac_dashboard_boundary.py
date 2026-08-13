from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts/mac_dashboard.py"
HTML = ROOT / "web/mac_dashboard.html"
HUB = ROOT / "web/mac_multi_asset.html"
CRYPTO_HTML = ROOT / "web/mac_crypto_dashboard.html"
CRYPTO_REHEARSAL = ROOT / "scripts/mac_crypto_paper_rehearsal.py"
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
STORAGE_CALLS = (
    "localStorage.setItem",
    "localStorage.getItem",
    "localStorage.removeItem",
    "localStorage.clear(",
    "sessionStorage.setItem",
    "sessionStorage.getItem",
    "sessionStorage.removeItem",
    "sessionStorage.clear(",
    "localStorage[",
    "sessionStorage[",
)


def main() -> int:
    errors: list[str] = []
    for path in (SERVER, HTML, HUB, CRYPTO_HTML, CRYPTO_REHEARSAL, OPEN, INSTALL, CORE, R6, FULL):
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
            "scripts/mac_crypto_paper_rehearsal.py",
            'env[WRITE_ENV] = "DISABLED"',
            '"order_execution_from_dashboard": False',
            '"crypto_execution_from_dashboard": False',
            '"equity_execution_from_dashboard": False',
            '"native_multi_asset_control_center": True',
            '"asset_classes": ["US_EQUITY", "CRYPTO"]',
            '"/equities": HTML_PATH',
            '"/crypto": CRYPTO_HTML_PATH',
            '"/usr/bin/open"',
            "subprocess.run(",
        ):
            if anchor not in text:
                errors.append(f"dashboard safety/multi-asset anchor missing: {anchor}")
        for forbidden in FORBIDDEN:
            if forbidden in text:
                errors.append(f"dashboard contains forbidden execution surface: {forbidden}")
        if 'if host != "127.0.0.1"' not in text:
            errors.append("dashboard must reject non-loopback bind")
        if 'env[WRITE_ENV] = "ENABLED"' in text:
            errors.append("dashboard may never enable external PAPER write")
        if "[/usr/bin/open" in text or "shell=True" in text:
            errors.append("browser opener must not use shell execution")

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
                errors.append(f"equity dashboard UI safety/UX anchor missing: {anchor}")
        for forbidden in STORAGE_CALLS + (
            '<script src=',
            '<link rel="stylesheet" href=',
            "r6_execute_paper_canary",
            "r6_connectivity_bound_final_freshness",
        ):
            if forbidden in html:
                errors.append(f"equity dashboard UI contains forbidden persistence/execution surface: {forbidden}")

    if HUB.is_file():
        hub = HUB.read_text(encoding="utf-8")
        for anchor in (
            "AUTO-TRADE R6 · Multi-Asset",
            "US Equities",
            "Crypto 24/7",
            'href="/equities"',
            'href="/crypto"',
            "PAPER WRITE · DISABLED",
            "LIVE · BLOCKED",
            "Broker POST desde Hub: NO",
        ):
            if anchor not in hub:
                errors.append(f"multi-asset hub anchor missing: {anchor}")
        for forbidden in FORBIDDEN + STORAGE_CALLS + ('<script src=', '<link rel="stylesheet" href='):
            if forbidden in hub:
                errors.append(f"multi-asset hub contains forbidden surface: {forbidden}")

    if CRYPTO_HTML.is_file():
        crypto = CRYPTO_HTML.read_text(encoding="utf-8")
        for anchor in (
            "AUTO-TRADE · Crypto PAPER Lab",
            "BTC/USD",
            "PAPER WRITE · DISABLED",
            "LIVE · BLOCKED",
            "NO POST",
        ):
            if anchor not in crypto:
                errors.append(f"crypto dashboard anchor missing: {anchor}")
        for forbidden in FORBIDDEN + STORAGE_CALLS + ('<script src=', '<link rel="stylesheet" href='):
            if forbidden in crypto:
                errors.append(f"crypto dashboard contains forbidden surface: {forbidden}")

    if CRYPTO_REHEARSAL.is_file():
        text = CRYPTO_REHEARSAL.read_text(encoding="utf-8")
        for anchor in (
            "CapitalSafetyKernel",
            "OrderManagementSystem",
            '"broker_write_performed": False',
            '"external_post_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
        ):
            if anchor not in text:
                errors.append(f"crypto rehearsal boundary anchor missing: {anchor}")
        for forbidden in FORBIDDEN:
            if forbidden in text:
                errors.append(f"crypto rehearsal contains forbidden write surface: {forbidden}")

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
        "AUTO-TRADE Mac Multi-Asset Control Center boundary: PASS "
        "(localhost-only; Equities + Crypto routes; ephemeral PAPER credentials; safe allowlist; "
        "FULL standalone integration; no Final Freshness/staging/POST/LIVE surface)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
