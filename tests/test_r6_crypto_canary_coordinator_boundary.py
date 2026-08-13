from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_crypto_canary_coordinator_boundary.py"


def test_crypto_canary_coordinator_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "crypto canary coordinator boundary: PASS" in result.stdout
    assert "bounded LIMIT IOC" in result.stdout
    assert "operator decision required" in result.stdout
    assert "no credentials/network/writer/POST authority" in result.stdout
