from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW_TEST = ROOT / "tests/test_r6_writer_same_attempt_resume.py"
WRITER_TEST = ROOT / "tests/test_r6_paper_writer.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = NEW_TEST.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "    assert state_after.attempt_count == 1\n"
        "    assert state_after.attempt_id == attempt_id\n",
        "    assert state_after.attempt_count == 1\n"
        "    events = values[\"submission_registry\"].events(values[\"binding\"].order_id)\n"
        "    unknown_events = [event for event in events if event.event_type.value == \"SUBMIT_ATTEMPT_UNKNOWN\"]\n"
        "    assert len(unknown_events) == 1\n"
        "    assert unknown_events[0].payload[\"attempt_id\"] == attempt_id\n",
        "new resume durable attempt assertion",
    )
    NEW_TEST.write_text(text, encoding="utf-8")

    writer = WRITER_TEST.read_text(encoding="utf-8")
    old = '''def test_preconsumed_permit_with_prepared_submission_is_fail_closed_and_does_not_resume_post(tmp_path) -> None:\n    values = stack(tmp_path)\n    values["permit_registry"].consume(\n        approval=values["approval"],\n        attempt_id="writer-attempt-001",\n        now=NOW + timedelta(milliseconds=500),\n    )\n    transport = FakeWriteTransport(response=success_response(values["expected"]))\n\n    with pytest.raises(PaperWriterBlocked, match="ISSUED"):\n        submit(writer(transport), values)\n\n    assert transport.requests == []\n    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.PREPARED\n    permit = values["permit_registry"].get(values["approval"].approval_hash)\n    assert permit.status is PaperCanaryPermitStatus.CONSUMED\n'''
    new = '''def test_preconsumed_permit_by_different_attempt_is_fail_closed_and_does_not_resume_post(tmp_path) -> None:\n    values = stack(tmp_path)\n    values["permit_registry"].consume(\n        approval=values["approval"],\n        attempt_id="writer-attempt-other",\n        now=NOW + timedelta(milliseconds=500),\n    )\n    transport = FakeWriteTransport(response=success_response(values["expected"]))\n\n    with pytest.raises(PaperWriterBlocked, match="another attempt"):\n        submit(writer(transport), values, attempt_id="writer-attempt-001")\n\n    assert transport.requests == []\n    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.PREPARED\n    permit = values["permit_registry"].get(values["approval"].approval_hash)\n    assert permit.status is PaperCanaryPermitStatus.CONSUMED\n    assert permit.attempt_id == "writer-attempt-other"\n'''
    writer = replace_once(writer, old, new, "legacy preconsumed writer test")
    WRITER_TEST.write_text(writer, encoding="utf-8")
    print("TD-R6-012 tests aligned to same-attempt resume contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
