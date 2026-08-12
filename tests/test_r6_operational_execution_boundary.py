from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_operational_execution_boundary.py"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def test_current_operational_execution_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "operational execution boundary: PASS" in result.stdout


def test_checker_rejects_research_import(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "rogue.py"
    path.write_text(
        "from autotrade.research.health import SQLiteHealthStateStore\n",
        encoding="utf-8",
    )
    errors = ns["_scan_forbidden_imports"](path)
    assert any("forbidden execution import" in error for error in errors)


def test_checker_rejects_ai_import(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "rogue.py"
    path.write_text("from openai import OpenAI\n", encoding="utf-8")
    errors = ns["_scan_forbidden_imports"](path)
    assert any("forbidden execution import" in error for error in errors)
