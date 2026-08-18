from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_crypto_operator_decision_boundary.py"


def test_crypto_operator_decision_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "crypto operator decision boundary: PASS" in result.stdout
    assert "HUMAN_OPERATOR only" in result.stdout
    assert "tamper-evident ISSUED->CONSUMED" in result.stdout
    assert "isolated UAT issuer plus exact first-canary execution issuer" in result.stdout
    assert "approval issuers have no credentials/network/writer/POST/consumption authority" in result.stdout
