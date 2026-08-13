from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_crypto_execution_simulation_boundary.py"


def test_crypto_execution_simulation_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "crypto execution simulation boundary: PASS" in result.stdout
    assert "UNKNOWN -> PRE_IO -> delegated transport" in result.stdout
    assert "deterministic in-memory delegate only" in result.stdout
    assert "Mac remains disconnected" in result.stdout
