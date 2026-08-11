from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERMIT = ROOT / "src/autotrade/brokers/alpaca_paper_canary_permit.py"
PERMIT_TEST = ROOT / "tests/test_r6_paper_canary_permit.py"
WRITER_HELPER = ROOT / "scripts/r6_bind_writer_to_human_execution.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = PERMIT.read_text(encoding="utf-8")
    anchor = '''    def list_states(self) -> tuple[PaperCanaryPermitState, ...]:\n'''
    method = '''    def get_issued_event_hash(self, approval_hash: str) -> str:\n        """Return the immutable, verified ISSUED-event hash for one permit.\n\n        PaperCanaryPermitState.event_hash tracks the latest event and therefore\n        changes from ISSUED to CONSUMED. Prepared canary packages must bind the\n        immutable issuance evidence so the same-attempt crash-safe resume can\n        verify the original permit without mistaking the later CONSUMED event\n        for tampering. The entire ledger/control chain is verified first.\n        """\n        _validate_hash(approval_hash, "approval_hash")\n        conn = self._runtime.connect()\n        try:\n            states, _, _ = self._verify_locked(conn)\n            if approval_hash not in states:\n                raise KeyError(approval_hash)\n            rows = conn.execute(\n                """\n                SELECT sequence, event_type, approval_hash, occurred_at,\n                       payload_json, previous_event_hash, event_hash\n                FROM alpaca_paper_canary_permit_events\n                WHERE approval_hash = ? AND event_type = ?\n                ORDER BY sequence\n                """,\n                (approval_hash, PaperCanaryPermitEventType.ISSUED.value),\n            ).fetchall()\n            if len(rows) != 1:\n                raise PaperCanaryPermitIntegrityError(\n                    "canary permit must have exactly one verified issuance event"\n                )\n            event = _event_from_row(rows[0])\n            if (\n                event.event_type is not PaperCanaryPermitEventType.ISSUED\n                or event.approval_hash != approval_hash\n            ):\n                raise PaperCanaryPermitIntegrityError(\n                    "canary permit issuance event identity mismatch"\n                )\n            return event.event_hash\n        finally:\n            conn.close()\n\n'''
    text = replace_once(text, anchor, method + anchor, "permit issuance-hash method")
    PERMIT.write_text(text, encoding="utf-8")

    test = PERMIT_TEST.read_text(encoding="utf-8")
    test = replace_once(
        test,
        '''    assert first.approval_hash == approved.approval_hash\n    assert len(registry.list_states()) == 1\n''',
        '''    assert first.approval_hash == approved.approval_hash\n    assert registry.get_issued_event_hash(approved.approval_hash) == first.event_hash\n    assert len(registry.list_states()) == 1\n''',
        "permit issued-hash test",
    )
    test = replace_once(
        test,
        '''    registry.issue(approved)\n\n    consumed = registry.consume(\n''',
        '''    issued = registry.issue(approved)\n\n    consumed = registry.consume(\n''',
        "permit consume retain issuance state",
    )
    test = replace_once(
        test,
        '''    assert consumed.consumed_at == NOW + timedelta(seconds=1)\n    assert SQLitePaperCanaryPermitRegistry(rt).get(approved.approval_hash) == consumed\n''',
        '''    assert consumed.consumed_at == NOW + timedelta(seconds=1)\n    assert consumed.event_hash != issued.event_hash\n    assert registry.get_issued_event_hash(approved.approval_hash) == issued.event_hash\n    assert SQLitePaperCanaryPermitRegistry(rt).get(approved.approval_hash) == consumed\n''',
        "permit immutable issuance hash after consume",
    )
    PERMIT_TEST.write_text(test, encoding="utf-8")

    helper = WRITER_HELPER.read_text(encoding="utf-8")
    helper = replace_once(
        helper,
        '''        if prepared_package.permit_event_hash != permit.event_hash:\\n            raise PaperWriterBlocked("prepared package permit evidence mismatch")\\n''',
        '''        if (\\n            prepared_package.permit_event_hash\\n            != permit_registry.get_issued_event_hash(approval.approval_hash)\\n        ):\\n            raise PaperWriterBlocked("prepared package permit issuance evidence mismatch")\\n''',
        "writer package immutable permit issuance evidence",
    )
    WRITER_HELPER.write_text(helper, encoding="utf-8")
    print("permit registry now exposes immutable verified issuance hash; writer helper bound to it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
