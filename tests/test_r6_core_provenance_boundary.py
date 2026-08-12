from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_core_provenance_boundary.py"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def scan(tmp_path: Path, source: str) -> list[str]:
    ns = namespace()
    path = tmp_path / "alpaca_paper_core_provenance.py"
    path.write_text(source, encoding="utf-8")
    return ns["_scan"](source, path)


def test_current_core_provenance_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "core provenance boundary: PASS" in result.stdout


def test_provenance_cannot_import_execution_network_research_or_store_initializers(tmp_path) -> None:
    for source in (
        "from autotrade.brokers.alpaca_paper_writer import AlpacaPaperSingleShotWriter\n",
        "from autotrade.brokers.alpaca_paper_execution_bridge import PaperCanaryExecutionBridge\n",
        "from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperGateway\n",
        "from autotrade import oms\n",
        "from autotrade.research.health import HealthState\n",
        "from autotrade.research.forward import ForwardRunner\n",
        "import openai\n",
    ):
        errors = scan(tmp_path, source)
        assert any("forbidden core provenance import" in error for error in errors)

    source = '''
def rogue():
    SQLiteRuntime("x")
    SQLiteOrderStore(None)
    SQLitePortfolioStore(None)
    SQLiteHealthStateStore("x")
    SQLiteHealthBridgeStore(None)
'''
    errors = "\n".join(scan(tmp_path, source))
    for name in (
        "SQLiteRuntime",
        "SQLiteOrderStore",
        "SQLitePortfolioStore",
        "SQLiteHealthStateStore",
        "SQLiteHealthBridgeStore",
    ):
        assert name in errors


def test_provenance_cannot_contain_write_sql_or_execution_calls(tmp_path) -> None:
    for sql in (
        "UPDATE safety_state SET version=2",
        "INSERT INTO orders VALUES(1)",
        "DELETE FROM portfolio_state",
        "CREATE TABLE rogue(x)",
        "ALTER TABLE orders ADD x TEXT",
        "REPLACE INTO orders VALUES(1)",
        "VACUUM",
        "ATTACH DATABASE 'x' AS rogue",
    ):
        source = f'def rogue(conn):\n    conn.execute({sql!r})\n'
        errors = scan(tmp_path, source)
        assert any("write-capable SQL" in error for error in errors)

    source = '''
def rogue(writer, oms, registry, permit):
    writer.submit_once()
    oms.stage_external_submission(order_id="x")
    registry.record_operator_approval(context=None)
    permit.consume(attempt_id="x")
'''
    errors = "\n".join(scan(tmp_path, source))
    assert "submit_once" in errors
    assert "stage_external_submission" in errors
    assert "record_operator_approval" in errors
    assert "consume" in errors
