from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "src/autotrade/brokers/alpaca_paper_writer.py"
TEST = ROOT / "tests/test_r6_writer_same_attempt_resume.py"

OLD = '''        permit = permit_registry.get(approval.approval_hash)\n        if permit.status is not PaperCanaryPermitStatus.ISSUED:\n            raise PaperWriterBlocked("canary permit must be ISSUED before writer starts")\n        if (\n            permit.order_id != binding.order_id\n            or permit.client_order_id != binding.client_order_id\n            or permit.binding_hash != binding.fingerprint\n        ):\n            raise PaperWriterBlocked("durable canary permit does not match frozen submission")\n'''

NEW = '''        permit = permit_registry.get(approval.approval_hash)\n        if permit.status is PaperCanaryPermitStatus.CONSUMED:\n            # The only resumable consumed-permit state is PREPARED + zero submit\n            # attempts + the exact same attempt_id. By construction the writer\n            # persists UNKNOWN before it can construct/execute the POST, so a\n            # PREPARED submission proves no external write could have happened.\n            if permit.attempt_id != attempt_id:\n                raise PaperWriterBlocked(\n                    "canary permit is consumed by another attempt; reconciliation/manual recovery only"\n                )\n            if permit.consumed_at is None:\n                raise PaperWriterBlocked("consumed canary permit is missing consumed_at")\n        elif permit.status is not PaperCanaryPermitStatus.ISSUED:\n            raise PaperWriterBlocked("canary permit state is not resumable")\n        if (\n            permit.order_id != binding.order_id\n            or permit.client_order_id != binding.client_order_id\n            or permit.binding_hash != binding.fingerprint\n        ):\n            raise PaperWriterBlocked("durable canary permit does not match frozen submission")\n'''

TEST_CONTENT = r'''from __future__ import annotations

from datetime import timedelta

import pytest

from autotrade.brokers.alpaca_paper_canary_permit import PaperCanaryPermitStatus
from autotrade.brokers.alpaca_paper_submission import PaperSubmissionStatus, SQLitePaperSubmissionRegistry
from autotrade.brokers.alpaca_paper_writer import PaperWriterBlocked
from test_r6_paper_writer import (
    NOW,
    FakeWriteTransport,
    stack,
    submit,
    success_response,
    writer,
)


class CrashBeforeUnknownRegistry(SQLitePaperSubmissionRegistry):
    def __init__(self, runtime) -> None:
        super().__init__(runtime)
        self.crash_once = True

    def mark_submit_attempt_unknown(self, *, order_id, attempt_id, now):
        if self.crash_once:
            self.crash_once = False
            raise SystemExit("synthetic crash after permit consumption before UNKNOWN")
        return super().mark_submit_attempt_unknown(
            order_id=order_id,
            attempt_id=attempt_id,
            now=now,
        )


def crashable_values(tmp_path):
    values = stack(tmp_path)
    original = values["submission_registry"]
    crashable = CrashBeforeUnknownRegistry(original._runtime)
    values["submission_registry"] = crashable
    return values


def test_same_attempt_can_resume_only_from_prepared_consumed_before_unknown(tmp_path) -> None:
    values = crashable_values(tmp_path)
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    instance = writer(transport)
    attempt_id = "writer-attempt-resume-001"

    with pytest.raises(SystemExit, match="synthetic crash"):
        submit(instance, values, attempt_id=attempt_id)

    permit = values["permit_registry"].get(values["approval"].approval_hash)
    state = values["submission_registry"].get(values["binding"].order_id)
    assert permit.status is PaperCanaryPermitStatus.CONSUMED
    assert permit.attempt_id == attempt_id
    assert state.status is PaperSubmissionStatus.PREPARED
    assert state.attempt_count == 0
    assert transport.requests == []

    result = submit(
        instance,
        values,
        attempt_id=attempt_id,
        now=NOW + timedelta(seconds=2),
    )
    assert result.durable_status is PaperSubmissionStatus.UNKNOWN
    assert result.reconciliation_required is True
    assert len(transport.requests) == 1
    permit_after = values["permit_registry"].get(values["approval"].approval_hash)
    assert permit_after == permit
    state_after = values["submission_registry"].get(values["binding"].order_id)
    assert state_after.status is PaperSubmissionStatus.UNKNOWN
    assert state_after.attempt_count == 1
    assert state_after.attempt_id == attempt_id


def test_different_attempt_cannot_resume_consumed_prepared_permit(tmp_path) -> None:
    values = crashable_values(tmp_path)
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    instance = writer(transport)

    with pytest.raises(SystemExit):
        submit(instance, values, attempt_id="writer-attempt-original")

    with pytest.raises(PaperWriterBlocked, match="another attempt"):
        submit(
            instance,
            values,
            attempt_id="writer-attempt-different",
            now=NOW + timedelta(seconds=2),
        )
    assert transport.requests == []
    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.PREPARED


def test_consumed_permit_does_not_override_approval_expiry(tmp_path) -> None:
    values = crashable_values(tmp_path)
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    instance = writer(transport)
    attempt_id = "writer-attempt-expired-resume"

    with pytest.raises(SystemExit):
        submit(instance, values, attempt_id=attempt_id)

    with pytest.raises(PaperWriterBlocked, match="expired"):
        submit(
            instance,
            values,
            attempt_id=attempt_id,
            now=values["approval"].expires_at,
        )
    assert transport.requests == []
    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.PREPARED


def test_unknown_is_never_resume_write_even_for_same_consumed_attempt(tmp_path) -> None:
    values = stack(tmp_path)
    attempt_id = "writer-attempt-unknown"
    values["permit_registry"].consume(
        approval=values["approval"],
        attempt_id=attempt_id,
        now=NOW + timedelta(seconds=1),
    )
    values["submission_registry"].mark_submit_attempt_unknown(
        order_id=values["binding"].order_id,
        attempt_id=attempt_id,
        now=NOW + timedelta(seconds=1),
    )
    transport = FakeWriteTransport(response=success_response(values["expected"]))

    with pytest.raises(PaperWriterBlocked, match="reconciliation-only"):
        submit(
            writer(transport),
            values,
            attempt_id=attempt_id,
            now=NOW + timedelta(seconds=2),
        )
    assert transport.requests == []
'''


def main() -> int:
    text = WRITER.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise SystemExit(f"writer permit anchor count={text.count(OLD)}")
    WRITER.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    if TEST.exists():
        raise SystemExit("same-attempt resume test file already exists")
    TEST.write_text(TEST_CONTENT, encoding="utf-8")
    print("TD-R6-012 same-attempt resume patch applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
