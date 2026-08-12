from __future__ import annotations

import ast
from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_mac_safety_rehearsal_boundary.py"


def test_current_mac_safety_rehearsal_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Capital Safety rehearsal boundary: PASS" in result.stdout


def test_boundary_helper_identifies_network_and_write_calls() -> None:
    ns = runpy.run_path(str(CHECKER))
    assert ns["_call_name"](ast.parse("client.send(x)").body[0].value.func) == "send"
    assert ns["_call_name"](ast.parse("Request(x)").body[0].value.func) == "Request"


def test_boundary_forbidden_surface_contract_includes_manual_risk_and_limit_escalation() -> None:
    ns = runpy.run_path(str(CHECKER))
    forbidden = ns["FORBIDDEN_TEXT"]
    assert "--max-order-notional" in forbidden
    assert "--max-leverage" in forbidden
    assert "AlpacaPaperSingleShotWriter" in forbidden
    assert "PaperCanaryExecutionBridge" in forbidden
    assert "stage_external_submission" in forbidden
