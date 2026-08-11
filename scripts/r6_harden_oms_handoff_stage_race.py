from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OMS = ROOT / "src/autotrade/oms.py"
WRITER_TEST = ROOT / "tests/test_r6_paper_writer.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    oms = OMS.read_text(encoding="utf-8")
    oms = replace_once(
        oms,
        "        self._validate_external_stage_controls(order=current, now=now)\n",
        "        self._validate_external_stage_controls(\n"
        "            order=current,\n"
        "            expected_safety_state_version=decision.safety_state_version,\n"
        "            now=now,\n"
        "        )\n",
        "stage second Safety version binding",
    )
    oms = replace_once(
        oms,
        "    def _validate_external_stage_controls(self, *, order: OrderRecord, now: datetime) -> None:\n",
        "    def _validate_external_stage_controls(\n"
        "        self,\n"
        "        *,\n"
        "        order: OrderRecord,\n"
        "        expected_safety_state_version: int,\n"
        "        now: datetime,\n"
        "    ) -> None:\n",
        "stage control signature",
    )
    oms = replace_once(
        oms,
        "        safety = self._safety_state_store.get()\n"
        "        if safety.kill_switch_active:\n",
        "        safety = self._safety_state_store.get()\n"
        "        if safety.version != expected_safety_state_version:\n"
        "            raise ExternalSubmissionHandoffConflict(\n"
        "                \"Safety state version changed during external handoff staging\"\n"
        "            )\n"
        "        if safety.kill_switch_active:\n",
        "stage second Safety version check",
    )
    OMS.write_text(oms, encoding="utf-8")

    writer_test = WRITER_TEST.read_text(encoding="utf-8")
    writer_test = replace_once(
        writer_test,
        "        if self.calls == 1:\n"
        "            return SafetyControlState(version=1, updated_at=NOW)\n",
        "        # stage_external_submission performs two authoritative Safety reads;\n"
        "        # PRE_CONSUME performs the third. Flip only at PRE_IO so this fixture\n"
        "        # continues to prove the post-permit, pre-network race.\n"
        "        if self.calls <= 3:\n"
        "            return SafetyControlState(version=0, updated_at=NOW)\n",
        "writer PRE_IO race fixture",
    )
    WRITER_TEST.write_text(writer_test, encoding="utf-8")
    print("TD-R6-010 stage Safety race hardened and PRE_IO fixture realigned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
