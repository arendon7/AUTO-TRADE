from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_authority.py"


def scan(tmp_path: Path, source: str, *, filename: str) -> list[str]:
    namespace = runpy.run_path(str(CHECKER))
    fake_root = tmp_path / "root"
    path = fake_root / "src/autotrade/brokers" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    namespace["_scan"].__globals__["ROOT"] = fake_root
    return namespace["_scan"](path)


def test_current_r6_authority_checker_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ATTESTATION_AND_AMBIGUITY_ONLY" in result.stdout


def test_checker_rejects_research_or_ai_authority_imports(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "from autotrade.research.portfolio_dependence import PortfolioDependence\n"
        "from openai import OpenAI\n",
        filename="alpaca_paper_fake.py",
    )
    assert any("autotrade.research" in error for error in errors)
    assert any("openai" in error.lower() for error in errors)


def test_checker_rejects_networking_outside_approved_gateway(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "import socket\nfrom urllib.request import Request\n",
        filename="alpaca_paper_submission.py",
    )
    assert any("networking is forbidden" in error for error in errors)


def test_checker_rejects_external_post_send_or_submit_calls(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def bad(client):\n"
        "    client.post('/v2/orders')\n"
        "    client.send('payload')\n"
        "    client.submit_order()\n",
        filename="alpaca_paper_writer.py",
    )
    assert sum("external write call" in error for error in errors) == 3


def test_checker_rejects_live_host_and_endpoint_outside_gateway(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "LIVE = 'api.alpaca.markets'\n"
        "URL = 'https://api.alpaca.markets/v2/orders'\n",
        filename="alpaca_paper_writer.py",
    )
    assert any("LIVE Trading API host literal" in error for error in errors)
    assert any("endpoint literal forbidden" in error for error in errors)


def test_checker_allows_nonnetwork_durable_state_machine(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "import sqlite3\n"
        "from hashlib import sha256\n"
        "def bind(value):\n"
        "    return sha256(value.encode()).hexdigest()\n",
        filename="alpaca_paper_submission.py",
    )
    assert errors == []
