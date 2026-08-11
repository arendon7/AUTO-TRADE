from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).with_name("r6_bind_writer_to_human_execution.py")


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    old = '''    text = replace_once(\n        text,\n        "        safety_state_store=safety_store,\\n",\n        "        safety_state_store=oms_safety_store,\\n",\n        "writer stack OMS stable Safety",\n    )\n'''
    new = '''    text = replace_once(\n        text,\n        "    oms = OrderManagementSystem(\\n"\n        "        broker=NeverCalledBroker(),\\n"\n        "        ledger=InMemoryEventLedger(),\\n"\n        "        order_store=order_store,\\n"\n        "        safety_state_store=safety_store,\\n",\n        "    oms = OrderManagementSystem(\\n"\n        "        broker=NeverCalledBroker(),\\n"\n        "        ledger=InMemoryEventLedger(),\\n"\n        "        order_store=order_store,\\n"\n        "        safety_state_store=oms_safety_store,\\n",\n        "writer stack OMS stable Safety",\n    )\n'''
    if text.count(old) != 1:
        raise SystemExit(f"writer human-binding ambiguous Safety patch block count={text.count(old)}")
    text = text.replace(old, new, 1)
    if '"        safety_state_store=safety_store,\\n",\n        "        safety_state_store=oms_safety_store,\\n"' in text:
        raise SystemExit("generic Safety replacement remains in writer binding helper")
    PATH.write_text(text, encoding="utf-8")
    print("writer human-binding helper now targets OMS constructor contextually")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
