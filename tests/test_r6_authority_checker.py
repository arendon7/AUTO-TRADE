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
    assert "AUTO-TRADE R6 PAPER authority boundary: PASS" in result.stdout
    assert (
        "PAPER_MULTI_ASSET_SINGLE_SHOT_RECONCILIATION_AND_PROTECTION_STRUCTURAL_CERTIFICATION"
        in result.stdout
    )


def test_checker_rejects_research_or_ai_authority_imports(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "from autotrade.research.portfolio_dependence import PortfolioDependence\n"
        "from openai import OpenAI\n",
        filename="alpaca_paper_fake.py",
    )
    assert any("autotrade.research" in error for error in errors)
    assert any("openai" in error.lower() for error in errors)


def test_checker_rejects_networking_outside_exact_approved_modules(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "import socket\nfrom urllib.request import Request\n",
        filename="alpaca_paper_submission.py",
    )
    assert any("networking is forbidden" in error for error in errors)


def test_checker_allows_network_imports_only_in_audited_gateway_roles(tmp_path) -> None:
    source = "import socket\nfrom urllib.request import Request\n"
    for filename in (
        "alpaca_paper_gateway.py",
        "alpaca_paper_flat_account.py",
        "alpaca_paper_market_data.py",
        "alpaca_paper_reconciliation_gateway.py",
        "alpaca_paper_writer.py",
        "alpaca_paper_trade_updates_transport.py",
        "alpaca_paper_crypto_writer.py",
    ):
        assert not any(
            "networking is forbidden" in error
            for error in scan(tmp_path / filename, source, filename=filename)
        )


def test_checker_rejects_crypto_networking_outside_dedicated_writer(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "import http.client\n",
        filename="alpaca_paper_crypto_reconciliation.py",
    )
    assert any("networking is forbidden" in error for error in errors)


def test_checker_rejects_networking_inside_crypto_preio_interlock(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "import http.client\n"
        "def go(self, **kwargs):\n"
        "    return self._delegate.post(**kwargs)\n",
        filename="alpaca_paper_crypto_pre_io.py",
    )
    assert any("networking is forbidden" in error for error in errors)
    assert not any("unaudited external write call post" in error for error in errors)


def test_checker_rejects_unaudited_post_send_or_submit_calls_even_in_writer(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def bad(client):\n"
        "    client.post('/v2/orders')\n"
        "    client.send('payload')\n"
        "    client.submit_order()\n",
        filename="alpaca_paper_writer.py",
    )
    assert sum("unaudited external write call" in error for error in errors) == 3


def test_checker_allows_exactly_one_named_crypto_preio_delegate_post(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def go(self, **kwargs):\n"
        "    return self._delegate.post(**kwargs)\n",
        filename="alpaca_paper_crypto_pre_io.py",
    )
    assert not any("unaudited external write call post" in error for error in errors)
    assert not any("exactly one self._delegate.post" in error for error in errors)


def test_checker_rejects_second_or_arbitrary_post_in_crypto_preio_interlock(tmp_path) -> None:
    duplicate = scan(
        tmp_path / "duplicate",
        "def go(self, **kwargs):\n"
        "    self._delegate.post(**kwargs)\n"
        "    return self._delegate.post(**kwargs)\n",
        filename="alpaca_paper_crypto_pre_io.py",
    )
    assert any("exactly one self._delegate.post" in error for error in duplicate)

    arbitrary = scan(
        tmp_path / "arbitrary",
        "def go(self, client, **kwargs):\n"
        "    self._delegate.post(**kwargs)\n"
        "    return client.post('/v2/orders')\n",
        filename="alpaca_paper_crypto_pre_io.py",
    )
    assert any("unaudited external write call post" in error for error in arbitrary)


def test_checker_rejects_crypto_preio_delegate_post_inside_loop(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def go(self, **kwargs):\n"
        "    while True:\n"
        "        return self._delegate.post(**kwargs)\n",
        filename="alpaca_paper_crypto_pre_io.py",
    )
    assert any("PRE_IO delegated POST cannot execute inside a loop" in error for error in errors)


def test_checker_rejects_post_in_crypto_execution_simulation(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def go(client):\n"
        "    return client.post('/v2/orders')\n",
        filename="alpaca_paper_crypto_execution_simulation.py",
    )
    assert any("unaudited external write call post" in error for error in errors)


def test_checker_allows_only_named_socket_control_send_in_trade_updates_module(tmp_path) -> None:
    exact = scan(
        tmp_path / "exact",
        "def go(socket, a, b):\n    socket.send(a)\n    socket.send(b)\n",
        filename="alpaca_paper_trade_updates_transport.py",
    )
    assert not any("unaudited external write call send" in error for error in exact)
    assert not any("exactly two socket control sends" in error for error in exact)

    arbitrary = scan(
        tmp_path / "arbitrary",
        "def go(client):\n    client.send('order')\n",
        filename="alpaca_paper_trade_updates_transport.py",
    )
    assert any("unaudited external write call send" in error for error in arbitrary)


def test_checker_rejects_trade_update_control_send_inside_loop_or_wrong_count(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def go(socket, payload):\n"
        "    while True:\n"
        "        socket.send(payload)\n",
        filename="alpaca_paper_trade_updates_transport.py",
    )
    assert any("cannot execute inside a loop" in error for error in errors)
    assert any("exactly two socket control sends" in error for error in errors)


def test_checker_allows_exactly_one_transport_write_only_in_writer(tmp_path) -> None:
    one = scan(
        tmp_path / "one",
        "def go(self, request):\n    return self._transport.write(request)\n",
        filename="alpaca_paper_writer.py",
    )
    assert not any("transport write" in error for error in one)

    wrong_file = scan(
        tmp_path / "wrong",
        "def go(self, request):\n    return self._transport.write(request)\n",
        filename="alpaca_paper_submission.py",
    )
    assert any("transport write is allowed only" in error for error in wrong_file)

    duplicate = scan(
        tmp_path / "duplicate",
        "def go(self, request):\n"
        "    self._transport.write(request)\n"
        "    return self._transport.write(request)\n",
        filename="alpaca_paper_writer.py",
    )
    assert any("exactly one transport write" in error for error in duplicate)


def test_checker_rejects_writer_transport_write_inside_loop(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def go(self, request):\n"
        "    while True:\n"
        "        return self._transport.write(request)\n",
        filename="alpaca_paper_writer.py",
    )
    assert any("cannot execute inside a loop" in error for error in errors)


def test_checker_rejects_live_host_and_unapproved_endpoint_authority(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "LIVE = 'api.alpaca.markets'\n"
        "URL = 'https://api.alpaca.markets/v2/orders'\n",
        filename="alpaca_paper_submission.py",
    )
    assert any("LIVE Trading API host literal" in error for error in errors)
    assert any("endpoint literal forbidden" in error for error in errors)


def test_checker_rejects_websocket_authority_outside_exact_trade_updates_module(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "URL = 'wss://paper-api.alpaca.markets/stream'\n",
        filename="alpaca_paper_gateway.py",
    )
    assert any("websocket endpoint authority" in error for error in errors)


def test_checker_rejects_any_other_wss_literal_even_in_trade_updates_module(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "URL = 'wss://streaming.alpaca.markets/v2/account'\n"
        "def go(socket, a, b):\n    socket.send(a)\n    socket.send(b)\n",
        filename="alpaca_paper_trade_updates_transport.py",
    )
    assert any("websocket endpoint authority" in error for error in errors)


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
