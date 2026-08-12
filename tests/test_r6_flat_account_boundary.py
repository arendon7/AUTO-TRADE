from __future__ import annotations

import ast
from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_flat_account_boundary.py"


def test_current_flat_account_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "flat-account boundary: PASS" in result.stdout


def test_flat_account_boundary_rejects_writer_and_mutation_authority(tmp_path) -> None:
    ns = runpy.run_path(str(CHECKER))
    path = tmp_path / "bad_flat.py"
    source = (
        "from autotrade.brokers.alpaca_paper_writer import AlpacaPaperSingleShotWriter\n"
        "def bad(x):\n"
        "    x.stage_external_submission()\n"
        "    x.cancel_all()\n"
    )
    tree = ast.parse(source)
    errors = ns["_scan_ast"](tree, path)
    assert any("alpaca_paper_writer" in error for error in errors)
    assert any("stage_external_submission" in error for error in errors)
    assert any("cancel_all" in error for error in errors)


def test_flat_account_boundary_rejects_raw_http_request_construction(tmp_path) -> None:
    ns = runpy.run_path(str(CHECKER))
    path = tmp_path / "bad_http.py"
    tree = ast.parse('Request("https://paper-api.alpaca.markets/v2/orders")')
    errors = ns["_scan_ast"](tree, path)
    assert any("raw HTTP Request" in error for error in errors)
