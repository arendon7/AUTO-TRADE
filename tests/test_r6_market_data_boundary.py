from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_market_data_boundary.py"


def test_current_market_data_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "equity market-data boundary: PASS" in result.stdout


def test_market_data_boundary_forbids_capital_authority_imports(tmp_path) -> None:
    ns = runpy.run_path(str(CHECKER))
    path = tmp_path / "bad.py"
    source = (
        "from autotrade.oms import OrderManagementSystem\n"
        "from autotrade.research.forward import ForwardEvaluator\n"
        "from autotrade.brokers.alpaca_paper_writer import AlpacaPaperSingleShotWriter\n"
    )
    path.write_text(source, encoding="utf-8")
    errors = ns["_forbidden_imports"](source, path)
    assert any("autotrade.oms" in error for error in errors)
    assert any("autotrade.research" in error for error in errors)
    assert any("alpaca_paper_writer" in error for error in errors)
