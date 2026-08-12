from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_authority.py"
CORE = ROOT / "scripts/check_r6_authority_core.py"
CURRENT_PHASE = (
    "PAPER_SINGLE_SHOT_FLAT_ACCOUNT_AND_MARKET_DATA_GET_RECONCILIATION_"
    "AND_TRADE_UPDATES_CONTROL_STREAM"
)


def test_r6_authority_cli_cannot_be_a_silent_noop() -> None:
    source = CHECKER.read_text(encoding="utf-8")
    assert 'with_name("check_r6_authority_core.py")' in source
    assert 'if __name__ == "__main__":' in source
    assert "code = main()" in source
    assert CORE.is_file()

    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert CURRENT_PHASE in result.stdout
    assert "AUTO-TRADE R6 PAPER authority boundary: PASS" in result.stdout
