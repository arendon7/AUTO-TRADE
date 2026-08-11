from __future__ import annotations

from pathlib import Path
import runpy


CHECKER = Path(__file__).resolve().parents[1] / "scripts" / "check_r5_authority.py"


def scan(tmp_path: Path, source: str, *, filename: str = "forward.py") -> list[str]:
    namespace = runpy.run_path(str(CHECKER))
    path = tmp_path / filename
    path.write_text(source)
    return namespace["_scan_path"](path)


def test_checker_rejects_execution_imports_and_calls(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "from autotrade.oms import OMS\n"
        "def f(client):\n"
        "    client.submit_order()\n",
    )
    assert any("forbidden R5 import" in error for error in errors)
    assert any("submit_order" in error for error in errors)


def test_checker_rejects_operational_portfolio_state_and_domain_symbols(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "from autotrade.state import InMemoryPortfolioStore\n"
        "from autotrade.domain import PortfolioSnapshot\n",
        filename="shadow.py",
    )
    assert any("autotrade.state" in error for error in errors)
    assert any("autotrade.domain" in error for error in errors)
    assert any("PortfolioSnapshot" in error for error in errors)


def test_checker_rejects_holdout_selection_modules_and_literal(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "from .splits import SplitName\n"
        "PHASE = 'FINAL_HOLDOUT'\n",
    )
    assert any("holdout/research-selection" in error for error in errors)
    assert any("forbidden authority/holdout literal" in error for error in errors)


def test_checker_rejects_network_access_from_shadow_or_forward(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "import socket\n"
        "ENDPOINT = 'https://example.com'\n",
        filename="shadow.py",
    )
    assert any("cannot import network module socket" in error for error in errors)
    assert any("cannot contain network endpoints" in error for error in errors)


def test_checker_allows_market_stream_network_surface_only_in_stream_modules(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "from urllib.parse import urlsplit\n"
        "ENDPOINT = 'wss://data-stream.binance.vision/ws/example'\n"
        "def validate(value):\n"
        "    return urlsplit(value)\n",
        filename="streaming.py",
    )
    assert errors == []

    errors = scan(
        tmp_path,
        "from websockets.sync.client import connect\n"
        "def open_only(url):\n"
        "    return connect(url)\n",
        filename="stream_transport.py",
    )
    assert errors == []


def test_checker_rejects_any_outbound_send_call_even_in_stream_transport(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def bad(connection):\n"
        "    connection.send('order-like outbound payload')\n",
        filename="stream_transport.py",
    )
    assert any("outbound method send" in error for error in errors)
