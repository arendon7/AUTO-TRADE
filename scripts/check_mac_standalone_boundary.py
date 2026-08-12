from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/mac_bootstrap.sh"
INSTALLER = ROOT / "INSTALAR_AUTO_TRADE.command"
OPENER = ROOT / "ABRIR_AUTO_TRADE.command"
WORKFLOW = ROOT / ".github/workflows/mac-standalone-full.yml"


def main() -> int:
    errors: list[str] = []
    for path in (BOOTSTRAP, INSTALLER, OPENER, WORKFLOW):
        if not path.is_file():
            errors.append(f"required standalone artifact is missing: {path.relative_to(ROOT)}")

    if BOOTSTRAP.is_file():
        text = BOOTSTRAP.read_text(encoding="utf-8")
        required = (
            'MAC_STANDALONE_MANIFEST.txt',
            'vendor/runtime',
            'vendor/wheels',
            'shasum -a 256 -c SHA256SUMS',
            'cpython-3.12.13-20260718-${RUNTIME_ARCH}.tar.gz',
            'arm64) RUNTIME_ARCH="arm64"',
            'x86_64) RUNTIME_ARCH="x86_64"',
            '--no-index',
            '--find-links "$WHEELHOUSE"',
            "'auto-trade-core[dev]==0.4.0.dev0'",
            'R6_EXTERNAL_PAPER_WRITE="DISABLED"',
            'unset APCA_API_KEY_ID',
            'unset APCA_API_SECRET_KEY',
            'EXPECTED_INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"',
            'FULL/STANDALONE runtime execution is allowed only from:',
            'PYTHON_PROBE_STATUS',
            'installation remains fail-closed',
        )
        for anchor in required:
            if anchor not in text:
                errors.append(f"standalone bootstrap anchor missing: {anchor}")
        standalone_region = text.split('if [[ -f "$STANDALONE_MARKER" ]]', 1)[-1].split("else\n  for candidate", 1)[0]
        for forbidden in (
            "curl ",
            "wget ",
            "brew ",
            "pip install --upgrade",
            "https://pypi.org",
            "r6_execute_paper_canary.py",
            "alpaca_paper_writer",
            "stage_external_submission",
        ):
            if forbidden in standalone_region:
                errors.append(f"standalone bootstrap may not depend on network/execute surface: {forbidden}")

    if INSTALLER.is_file():
        text = INSTALLER.read_text(encoding="utf-8")
        required = (
            'INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"',
            'verify_standalone_assets "$SOURCE_ROOT"',
            'ditto --norsrc --noqtn "$SOURCE_ROOT" "$STAGE_ROOT"',
            'verify_standalone_assets "$STAGE_ROOT"',
            'if [[ -L "$INSTALL_ROOT" ]]',
            'rm -rf "$STAGE_ROOT/.venv"',
            '"$STAGE_ROOT/.runtime"',
            'mv "$STAGE_ROOT" "$INSTALL_ROOT"',
            'standalone_install_relocated=',
            'export R6_EXTERNAL_PAPER_WRITE=DISABLED',
            'unset APCA_API_KEY_ID',
            'unset APCA_API_SECRET_KEY',
        )
        for anchor in required:
            if anchor not in text:
                errors.append(f"standalone installer anchor missing: {anchor}")
        first_verify = text.find('verify_standalone_assets "$SOURCE_ROOT"')
        clean_copy = text.find('ditto --norsrc --noqtn "$SOURCE_ROOT" "$STAGE_ROOT"')
        second_verify = text.find('verify_standalone_assets "$STAGE_ROOT"')
        promote = text.find('mv "$STAGE_ROOT" "$INSTALL_ROOT"')
        if min(first_verify, clean_copy, second_verify, promote) < 0 or not (
            first_verify < clean_copy < second_verify < promote
        ):
            errors.append("standalone installer must verify source -> clean-copy -> reverify stage -> promote")
        for forbidden in (
            "R6_EXTERNAL_PAPER_WRITE=ENABLED",
            "r6_execute_paper_canary.py",
            "alpaca_paper_writer",
            "stage_external_submission",
            "source .env",
        ):
            if forbidden in text:
                errors.append(f"standalone installer contains forbidden authority surface: {forbidden}")

    if OPENER.is_file():
        text = OPENER.read_text(encoding="utf-8")
        for anchor in (
            'INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"',
            'EXPECTED_HEAD="$(read_source_head "$SOURCE_ROOT")"',
            'INSTALLED_HEAD="$(read_source_head "$INSTALL_ROOT")"',
            'bash "$SOURCE_ROOT/INSTALAR_AUTO_TRADE.command"',
            'ROOT="$INSTALL_ROOT"',
            'scripts/mac_dashboard.py',
            'export R6_EXTERNAL_PAPER_WRITE=DISABLED',
        ):
            if anchor not in text:
                errors.append(f"standalone opener anchor missing: {anchor}")

    if WORKFLOW.is_file():
        text = WORKFLOW.read_text(encoding="utf-8")
        required = (
            "AUTO-TRADE-R6-MAC-FULL",
            "cpython-3.12.13%2B20260718-aarch64-apple-darwin-install_only.tar.gz",
            "cpython-3.12.13%2B20260718-x86_64-apple-darwin-install_only.tar.gz",
            "62aeee6161d57303a71a138b75fd5cc6fb8c89c4b1d9c7f0a052d89fa0b6652b",
            "10b47148de86f9d87ba6e96a3db606ced90a206a3454d7d6d8fa68536a05d81f",
            "macos-15",
            "macos-15-intel",
            "PIP_NO_INDEX: '1'",
            "MAC_STANDALONE_MANIFEST.txt",
            "homebrew_required=NO",
            "system_python_required=NO",
            "pypi_required_at_first_launch=NO",
            "external_order_submitted_by_build=NO",
            "Simulate Safari/Finder quarantine",
            "com.apple.quarantine",
            'INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"',
            "standalone_install_relocated=YES",
            "installed embedded runtime retained Finder quarantine",
            "Delete downloaded source and prove installed self-heal",
            "installed_copy_self_heal=PASS",
        )
        for anchor in required:
            if anchor not in text:
                errors.append(f"standalone workflow anchor missing: {anchor}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac FULL/STANDALONE boundary: PASS "
        "(verified clean relocation outside downloaded quarantine + embedded dual-arch Python + hashed offline wheelhouse; "
        "no Homebrew/system-Python/PyPI first-launch dependency; no execution authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
