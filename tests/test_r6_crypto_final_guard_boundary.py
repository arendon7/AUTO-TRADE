from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_crypto_final_guard_boundary.py"


def test_crypto_final_guard_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "crypto final freshness boundary: PASS" in result.stdout
    assert "offline PRE_CONSUME->PRE_IO chain" in result.stdout
    assert "UNKNOWN-before-I/O required" in result.stdout
    assert "no credentials/network/writer/mutation authority" in result.stdout
