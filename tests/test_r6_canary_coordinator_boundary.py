from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_canary_coordinator_boundary.py"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def scan(tmp_path: Path, source: str) -> list[str]:
    ns = namespace()
    path = tmp_path / "alpaca_paper_canary_coordinator.py"
    path.write_text(source, encoding="utf-8")
    return ns["_scan_coordinator_source"](source, path)


def test_current_offline_canary_coordinator_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "offline canary coordinator boundary: PASS" in result.stdout


def test_coordinator_cannot_import_writer_or_network_stack(tmp_path) -> None:
    for source in (
        "from .alpaca_paper_writer import AlpacaPaperOrderWriter\n",
        "import requests\n",
        "import socket\n",
        "import websockets\n",
    ):
        errors = scan(tmp_path, source)
        assert any("forbidden coordinator import" in error for error in errors)


def test_coordinator_cannot_stage_submit_send_or_mint_human_authority(tmp_path) -> None:
    source = (
        "def rogue(self, registry):\n"
        "    self._oms.stage_external_submission(order_id='x')\n"
        "    self.submit_once()\n"
        "    self.send(b'x')\n"
        "    registry.record_operator_approval(context=None)\n"
    )
    errors = scan(tmp_path, source)
    joined = "\n".join(errors)
    assert "stage_external_submission" in joined
    assert "submit_once" in joined
    assert "send" in joined
    assert "record_operator_approval" in joined


def test_coordinator_cannot_reference_submitting_or_construct_handoff(tmp_path) -> None:
    source = (
        "def rogue():\n"
        "    x = OrderStatus.SUBMITTING\n"
        "    return ExternalSubmissionHandoff(handoff_id='a')\n"
    )
    errors = scan(tmp_path, source)
    joined = "\n".join(errors)
    assert "may not reference SUBMITTING" in joined
    assert "may not construct OMS handoff" in joined


def test_coordinator_cannot_embed_order_write_endpoint(tmp_path) -> None:
    errors = scan(tmp_path, 'URL = "https://paper-api.alpaca.markets/v2/orders"\n')
    assert any("order-write endpoint" in error for error in errors)
