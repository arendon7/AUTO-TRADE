from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_operational_lifecycle_boundary.py"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def scan_preflight(tmp_path: Path, source: str) -> list[str]:
    ns = namespace()
    path = tmp_path / "r6_external_paper_preflight.py"
    path.write_text(source, encoding="utf-8")
    return ns["_scan_preflight"](source, path)


def scan_preparer(tmp_path: Path, source: str) -> list[str]:
    ns = namespace()
    path = tmp_path / "alpaca_paper_operational_prepare.py"
    path.write_text(source, encoding="utf-8")
    return ns["_scan_no_execution_surface"](
        source,
        path,
        "preparer",
        ns["FORBIDDEN_PREPARER_IMPORTS"],
    )


def test_current_operational_lifecycle_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "operational lifecycle boundary: PASS" in result.stdout


def test_preflight_cannot_import_execution_writer_stream_or_ai(tmp_path) -> None:
    for source in (
        "from autotrade.brokers.alpaca_paper_writer import AlpacaPaperSingleShotWriter\n",
        "from autotrade.brokers.alpaca_paper_execution_bridge import PaperCanaryExecutionBridge\n",
        "from autotrade.brokers.alpaca_paper_trade_updates_transport import AlpacaPaperTradeUpdatesTransport\n",
        "import openai\n",
        "from autotrade import research\n",
    ):
        errors = scan_preflight(tmp_path, source)
        assert any("forbidden preflight import" in error for error in errors)


def test_preflight_cannot_submit_stage_write_send_or_mint_operator_authority(tmp_path) -> None:
    source = '''
def rogue(writer, oms, transport, registry):
    writer.submit_once()
    oms.stage_external_submission(order_id="x")
    transport.write(None)
    transport.send(b"x")
    registry.record_operator_approval(context=None)
'''
    errors = "\n".join(scan_preflight(tmp_path, source))
    assert "submit_once" in errors
    assert "stage_external_submission" in errors
    assert "write" in errors
    assert "send" in errors
    assert "record_operator_approval" in errors


def test_operational_preparer_cannot_import_or_call_execution_authority(tmp_path) -> None:
    source = '''
from autotrade.brokers.alpaca_paper_writer import AlpacaPaperSingleShotWriter
from autotrade.brokers.alpaca_paper_execution_bridge import PaperCanaryExecutionBridge

def rogue(writer, oms, registry):
    writer.submit_once()
    oms.stage_external_submission(order_id="x")
    registry.record_operator_approval(context=None)
'''
    errors = "\n".join(scan_preparer(tmp_path, source))
    assert "forbidden preparer import" in errors
    assert "submit_once" in errors
    assert "stage_external_submission" in errors
    assert "record_operator_approval" in errors


def test_operational_workspace_scanner_rejects_transport_style_write(tmp_path) -> None:
    ns = namespace()
    path = tmp_path / "alpaca_paper_operational.py"
    source = '''
def rogue(transport):
    transport.write(b"payload")
'''
    path.write_text(source, encoding="utf-8")
    errors = ns["_scan_operational_workspace"](source, path)
    assert any("transport-style write authority" in error for error in errors)
