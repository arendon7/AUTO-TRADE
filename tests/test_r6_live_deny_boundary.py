from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_live_deny_boundary.py"
BROKER_DIR = ROOT / "src/autotrade/brokers"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def scan_source(tmp_path: Path, source: str, *, filename: str) -> list[str]:
    ns = namespace()
    fake_root = tmp_path / "root"
    path = fake_root / "src/autotrade/brokers" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    ns["_scan_ast"].__globals__["ROOT"] = fake_root
    return ns["_scan_ast"](path)


def scan_augmented(tmp_path: Path, *, filename: str, appendix: str) -> list[str]:
    ns = namespace()
    fake_root = tmp_path / "root"
    path = fake_root / "src/autotrade/brokers" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    source = (BROKER_DIR / filename).read_text(encoding="utf-8")
    path.write_text(source + "\n" + appendix + "\n", encoding="utf-8")
    ns["_scan"].__globals__["ROOT"] = fake_root
    return ns["_scan"](path)


def test_current_permanent_live_deny_checker_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "permanent LIVE-deny boundary: PASS" in result.stdout


def test_rejects_full_live_url_even_inside_approved_writer(tmp_path) -> None:
    errors = scan_augmented(
        tmp_path,
        filename="alpaca_paper_writer.py",
        appendix='ROGUE_LIVE_URL = "https://api.alpaca.markets/v2/orders"',
    )
    assert any("LIVE endpoint/host literal" in error for error in errors)


def test_rejects_dynamic_live_deny_symbol_authority_construction(tmp_path) -> None:
    errors = scan_source(
        tmp_path,
        "from autotrade.brokers.alpaca_paper_gateway import ALPACA_LIVE_TRADING_HOST\n"
        "ROGUE = f\"https://{ALPACA_LIVE_TRADING_HOST}/v2/orders\"\n",
        filename="alpaca_paper_fake.py",
    )
    assert any("may only be read in a comparison" in error for error in errors)


def test_rejects_live_promotion_identifiers(tmp_path) -> None:
    errors = scan_source(
        tmp_path,
        "def promote_live():\n"
        "    return None\n",
        filename="alpaca_paper_fake.py",
    )
    assert any("LIVE promotion/authority identifier" in error for error in errors)


def test_rejects_ai_or_research_authorization_dependencies(tmp_path) -> None:
    errors = scan_source(
        tmp_path,
        "from autotrade.research.forward import ForwardEvaluator\n"
        "from openai import OpenAI\n",
        filename="alpaca_paper_fake.py",
    )
    assert sum("AI/research authority import" in error for error in errors) == 2


def test_rejects_direct_transport_write_outside_certified_writer(tmp_path) -> None:
    errors = scan_source(
        tmp_path,
        "def rogue(transport, request):\n"
        "    return transport.write(request)\n",
        filename="alpaca_paper_submission.py",
    )
    assert any("direct transport write is forbidden" in error for error in errors)


def test_rejects_second_low_level_http_open_and_request_method_creep(tmp_path) -> None:
    errors = scan_augmented(
        tmp_path,
        filename="alpaca_paper_writer.py",
        appendix=(
            "def rogue_extra_http(self, raw, request):\n"
            "    self._opener.open(raw)\n"
            "    return Request(request.url, method=\"GET\")\n"
        ),
    )
    assert any("exactly one low-level _opener.open" in error for error in errors)
    assert any("Request method must be literal POST" in error for error in errors)
    assert any("exactly one urllib Request constructor" in error for error in errors)


def test_rejects_extra_trade_updates_connect_or_control_send(tmp_path) -> None:
    errors = scan_augmented(
        tmp_path,
        filename="alpaca_paper_trade_updates_transport.py",
        appendix=(
            "def rogue_trade_updates(socket, endpoint):\n"
            "    websocket_connect(endpoint)\n"
            "    socket.send(\"rogue\")\n"
        ),
    )
    assert any("exactly one websocket_connect" in error for error in errors)
    assert any("exactly two socket.send control frames" in error for error in errors)
