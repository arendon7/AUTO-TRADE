from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "tests/test_r6_paper_writer.py"


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    old = '''class FlipToKillSwitchStore:\n    def __init__(self):\n        self.calls = 0\n\n    def get(self):\n        self.calls += 1\n        if self.calls == 1:\n            return SafetyControlState(version=1, updated_at=NOW)\n        return SafetyControlState(\n            kill_switch_active=True,\n            kill_switch_reason="test-final-recheck",\n            version=2,\n            updated_at=NOW + timedelta(seconds=1),\n        )\n'''
    new = '''class FlipToKillSwitchStore:\n    def __init__(self):\n        self.calls = 0\n\n    def get(self):\n        self.calls += 1\n        # OMS staging performs two authoritative Safety reads and PRE_CONSUME\n        # performs a third. Keep the RiskDecision-bound version stable for all\n        # three, then flip exactly at PRE_IO after permit consumption/UNKNOWN.\n        if self.calls <= 3:\n            return SafetyControlState(version=0, updated_at=NOW)\n        return SafetyControlState(\n            kill_switch_active=True,\n            kill_switch_reason="test-final-recheck",\n            version=1,\n            updated_at=NOW + timedelta(seconds=1),\n        )\n'''
    if text.count(old) != 1:
        raise SystemExit(f"kill-switch fixture anchor count={text.count(old)}")
    TARGET.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("R6 writer kill-switch fixture aligned with OMS handoff staging")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
