from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).with_name("r6_refine_oms_handoff_control_plane.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one helper anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = PATH.read_text(encoding="utf-8")

    old_hash_block = '''    text = replace_once(\n        text,\n        \'\'\'        risk_decision_id=risk_decision_id,\\n        authorized_at=authorized_at,\\n        event_id=event_id,\\n\'\'\',\n        \'\'\'        risk_decision_id=risk_decision_id,\\n        safety_state_version=safety_state_version,\\n        market_fingerprint_value=market_fingerprint_value,\\n        decision_valid_until=decision_valid_until,\\n        authorized_at=authorized_at,\\n        event_id=event_id,\\n\'\'\',\n        "build hash call",\n    )\n'''
    new_hash_block = '''    text = replace_once(\n        text,\n        \'\'\'    handoff_hash = _external_handoff_hash(\\n        handoff_id=handoff_id,\\n        order_id=order_id,\\n        intent_fingerprint_value=intent_fingerprint_value,\\n        risk_decision_id=risk_decision_id,\\n        authorized_at=authorized_at,\\n        event_id=event_id,\\n    )\\n\'\'\',\n        \'\'\'    handoff_hash = _external_handoff_hash(\\n        handoff_id=handoff_id,\\n        order_id=order_id,\\n        intent_fingerprint_value=intent_fingerprint_value,\\n        risk_decision_id=risk_decision_id,\\n        safety_state_version=safety_state_version,\\n        market_fingerprint_value=market_fingerprint_value,\\n        decision_valid_until=decision_valid_until,\\n        authorized_at=authorized_at,\\n        event_id=event_id,\\n    )\\n\'\'\',\n        "build hash call",\n    )\n'''
    text = replace_once(text, old_hash_block, new_hash_block, "hash-call patcher block")

    old_object_block = '''    text = replace_once(\n        text,\n        \'\'\'        risk_decision_id=risk_decision_id,\\n        authorized_at=authorized_at,\\n        event_id=event_id,\\n        handoff_hash=handoff_hash,\\n\'\'\',\n        \'\'\'        risk_decision_id=risk_decision_id,\\n        safety_state_version=safety_state_version,\\n        market_fingerprint=market_fingerprint_value,\\n        decision_valid_until=decision_valid_until,\\n        authorized_at=authorized_at,\\n        event_id=event_id,\\n        handoff_hash=handoff_hash,\\n\'\'\',\n        "build object control-plane fields",\n    )\n'''
    new_object_block = '''    text = replace_once(\n        text,\n        \'\'\'    return ExternalSubmissionHandoff(\\n        handoff_id=handoff_id,\\n        order_id=order_id,\\n        intent_fingerprint=intent_fingerprint_value,\\n        risk_decision_id=risk_decision_id,\\n        authorized_at=authorized_at,\\n        event_id=event_id,\\n        handoff_hash=handoff_hash,\\n    )\\n\'\'\',\n        \'\'\'    return ExternalSubmissionHandoff(\\n        handoff_id=handoff_id,\\n        order_id=order_id,\\n        intent_fingerprint=intent_fingerprint_value,\\n        risk_decision_id=risk_decision_id,\\n        safety_state_version=safety_state_version,\\n        market_fingerprint=market_fingerprint_value,\\n        decision_valid_until=decision_valid_until,\\n        authorized_at=authorized_at,\\n        event_id=event_id,\\n        handoff_hash=handoff_hash,\\n    )\\n\'\'\',\n        "build object control-plane fields",\n    )\n'''
    text = replace_once(text, old_object_block, new_object_block, "object-constructor patcher block")

    PATH.write_text(text, encoding="utf-8")
    print("R6 OMS handoff refinement helper anchors corrected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
