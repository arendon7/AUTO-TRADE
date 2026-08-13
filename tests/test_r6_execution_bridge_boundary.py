from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_execution_bridge_boundary.py"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def scan(tmp_path: Path, source: str, *, filename: str = "alpaca_paper_execution_bridge.py") -> list[str]:
    ns = namespace()
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return ns["_scan_bridge"](source, path)


def test_current_execution_bridge_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "execution bridge boundary: PASS" in result.stdout
    assert "checkpoint-bound crypto" in result.stdout


def test_execution_bridge_cannot_import_writer_network_or_ai(tmp_path) -> None:
    for source in (
        "from .alpaca_paper_writer import AlpacaPaperSingleShotWriter\n",
        "from .alpaca_paper_crypto_writer import AlpacaPaperCryptoWriter\n",
        "from .alpaca_paper_crypto_pre_io import FinalGuardedCryptoEntryTransport\n",
        "import requests\n",
        "import socket\n",
        "import websockets\n",
        "import openai\n",
        "from autotrade import research\n",
    ):
        errors = scan(tmp_path, source, filename="alpaca_paper_crypto_execution_bridge.py")
        assert any("forbidden bridge import" in error for error in errors)


def test_execution_bridge_cannot_mint_human_decision_or_post(tmp_path) -> None:
    source = (
        "def rogue(registry, writer, transport):\n"
        "    registry.record_operator_approval(context=None)\n"
        "    writer.submit_once()\n"
        "    transport.write(None)\n"
        "    transport.send(b'x')\n"
        "    transport.post('/v2/orders')\n"
    )
    errors = "\n".join(scan(tmp_path, source))
    assert "record_operator_approval" in errors
    assert "submit_once" in errors
    assert "write" in errors
    assert "send" in errors
    assert "post" in errors


def test_ordering_checker_requires_decision_consume_before_oms_stage() -> None:
    ns = namespace()
    validate = ns["_validate_bridge_ordering"]
    bad = '''
network_write_authorized is not False
next_action != "OPERATOR_DECISION_REQUIRED"
staged, handoff = self._oms.stage_external_submission(
durable = operator_registry.get(
consumed = operator_registry.consume(
'''
    errors = validate(bad)
    assert any("verify durable decision, consume it, then stage OMS" in error for error in errors)


def test_crypto_ordering_checker_requires_checkpoint_then_consume_then_stage() -> None:
    ns = namespace()
    validate = ns["_validate_crypto_bridge_ordering"]
    good = '''
network_write_authorized is not False
next_action != "OPERATOR_DECISION_REQUIRED"
consume_instant > stage_instant
checkpoint.package_hash != package.package_hash
durable = operator_registry.get(
consumed = operator_registry.consume(
handoff_id = crypto_execution_handoff_id(
staged, handoff = self._oms.stage_external_submission(
'''
    assert validate(good) == []

    bad = '''
network_write_authorized is not False
next_action != "OPERATOR_DECISION_REQUIRED"
consume_instant > stage_instant
staged, handoff = self._oms.stage_external_submission(
checkpoint.package_hash != package.package_hash
durable = operator_registry.get(
consumed = operator_registry.consume(
handoff_id = crypto_execution_handoff_id(
'''
    errors = validate(bad)
    assert any("verify checkpoint" in error for error in errors)


def test_crypto_bridge_time_order_is_mandatory() -> None:
    ns = namespace()
    validate = ns["_validate_crypto_bridge_ordering"]
    source = '''
network_write_authorized is not False
next_action != "OPERATOR_DECISION_REQUIRED"
checkpoint.package_hash != package.package_hash
durable = operator_registry.get(
consumed = operator_registry.consume(
handoff_id = crypto_execution_handoff_id(
staged, handoff = self._oms.stage_external_submission(
'''
    errors = validate(source)
    assert any("consume-after-stage" in error for error in errors)
