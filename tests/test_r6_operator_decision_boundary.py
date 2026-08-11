from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_operator_decision_boundary.py"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def test_current_operator_decision_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "human operator decision boundary: PASS" in result.stdout


def test_checker_rejects_network_dependency(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "rogue.py"
    path.write_text("import websockets\n", encoding="utf-8")
    errors = ns["_scan_forbidden_authority"](path)
    assert any("forbidden authority/network import" in error for error in errors)


def test_checker_rejects_ai_research_dependency(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "rogue.py"
    path.write_text(
        "from autotrade.research.forward import ForwardEvaluator\n"
        "from openai import OpenAI\n",
        encoding="utf-8",
    )
    errors = ns["_scan_forbidden_authority"](path)
    assert sum("forbidden authority/network import" in error for error in errors) == 2


def test_checker_can_detect_rogue_operator_authority_call(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "rogue.py"
    path.write_text(
        "def rogue(registry, context, now):\n"
        "    return registry.record_operator_approval(context=context, operator_id='agent', issued_at=now, expires_at=now)\n",
        encoding="utf-8",
    )
    calls = ns["_named_calls"](path, "record_operator_approval")
    assert calls == [(2, "record_operator_approval")]


def test_checker_rejects_socket_send_call_without_relying_only_on_import(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "rogue.py"
    path.write_text("def rogue(sock):\n    sock.send(b'x')\n", encoding="utf-8")
    errors = ns["_scan_forbidden_authority"](path)
    assert any("network call forbidden" in error for error in errors)
