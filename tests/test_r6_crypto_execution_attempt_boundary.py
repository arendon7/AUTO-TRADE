from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_crypto_execution_attempt_boundary.py"


def test_crypto_execution_attempt_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "crypto execution attempt boundary: PASS" in result.stdout
    assert "durable PRE_CONSUME checkpoint" in result.stdout
    assert "single-attempt binding" in result.stdout
    assert "no credentials/network/writer authority" in result.stdout
    assert "Mac remains disconnected" in result.stdout
