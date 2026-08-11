from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_oms_handoff_boundary.py"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def scan(tmp_path: Path, source: str, filename: str = "alpaca_paper_fake.py") -> list[str]:
    ns = namespace()
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    return ns["_scan_broker_source"](source, path)


def test_current_oms_handoff_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OMS external-handoff boundary: PASS" in result.stdout


def test_r6_broker_module_cannot_construct_handoff_attestation(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def rogue():\n"
        "    return ExternalSubmissionHandoff(handoff_id='a')\n",
    )
    assert any("may not construct OMS handoff" in error for error in errors)


def test_r6_broker_module_cannot_mutate_order_store_directly(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def rogue(self, order):\n"
        "    self._orders.update(order)\n",
    )
    assert any("OrderStore.update is forbidden" in error for error in errors)


def test_r6_broker_module_cannot_synthesize_submitting_status(tmp_path) -> None:
    errors = scan(
        tmp_path,
        "def rogue(order):\n"
        "    return replace(order, status=OrderStatus.SUBMITTING)\n",
    )
    assert any("may not synthesize SUBMITTING" in error for error in errors)
