from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/mac_bootstrap.sh"
WORKFLOW = ROOT / ".github/workflows/mac-standalone-full.yml"


def main() -> int:
    errors: list[str] = []
    for path in (BOOTSTRAP, WORKFLOW):
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
        "(embedded dual-arch Python + hashed offline wheelhouse; no Homebrew/system-Python/PyPI first-launch dependency; no execution authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
