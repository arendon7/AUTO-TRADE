from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_writer_human_gate.py"


def namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def test_current_writer_human_gate_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "writer human-execution gate: PASS" in result.stdout


def test_writer_cannot_mint_human_decision_or_stage_oms(tmp_path) -> None:
    ns = namespace()
    scan = ns["_scan_writer"]
    path = tmp_path / "alpaca_paper_writer.py"
    source = '''
def rogue(registry, oms):
    registry.record_operator_approval(context=None)
    oms.stage_external_submission(order_id="x")
'''
    path.write_text(source, encoding="utf-8")
    errors = "\n".join(scan(source, path))
    assert "record_operator_approval" in errors
    assert "stage_external_submission" in errors


def test_writer_ordering_checker_rejects_post_before_human_consume() -> None:
    ns = namespace()
    validate = ns["_validate_ordering"]
    bad = '''
response = self._transport.write(request)
durable_operator = operator_registry.get(
durable_operator.status is not PaperOperatorDecisionStatus.CONSUMED
if execution_stage.package_hash != prepared_package.package_hash:
permit = permit_registry.get(approval.approval_hash)
phase=PaperFinalWritePhase.PRE_CONSUME
permit_registry.consume(
submission_registry.mark_submit_attempt_unknown(
request = _build_request(
phase=PaperFinalWritePhase.PRE_IO
'''
    errors = validate(bad)
    assert any("writer ordering" in error for error in errors)
