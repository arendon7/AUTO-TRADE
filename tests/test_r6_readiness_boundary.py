from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_readiness_boundary.py"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def test_current_readiness_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "R6 readiness boundary: PASS" in result.stdout


def test_checker_rejects_network_import(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "rogue.py"
    path.write_text("import requests\n", encoding="utf-8")
    errors = ns["_scan"](path)
    assert any("forbidden readiness import" in error for error in errors)


def test_checker_rejects_writer_authority_import(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "rogue.py"
    path.write_text(
        "from autotrade.brokers.alpaca_paper_writer import AlpacaPaperSingleShotWriter\n",
        encoding="utf-8",
    )
    errors = ns["_scan"](path)
    assert any("forbidden readiness authority import" in error for error in errors)


def test_checker_rejects_file_mutation_call(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "rogue.py"
    path.write_text("def mutate(path):\n    path.write_text('x')\n", encoding="utf-8")
    errors = ns["_scan"](path)
    assert any("forbidden readiness call write_text" in error for error in errors)


def test_checker_rejects_execution_call(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "rogue.py"
    path.write_text("def execute(writer):\n    writer.submit_once()\n", encoding="utf-8")
    errors = ns["_scan"](path)
    assert any("forbidden readiness call submit_once" in error for error in errors)
