from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/brokers/alpaca_paper_canary_coordinator.py"
TEST = ROOT / "tests/test_r6_paper_canary_coordinator.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = MODULE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "    risk_decision_id: str\n    risk_decision_valid_until: datetime\n",
        "    risk_decision_id: str\n    risk_decision_safety_state_version: int\n    market_fingerprint: str\n    risk_decision_valid_until: datetime\n",
        "package control-plane fields",
    )
    text = replace_once(
        text,
        '            ("intent_fingerprint", self.intent_fingerprint),\n            ("account_attestation_fingerprint", self.account_attestation_fingerprint),\n',
        '            ("intent_fingerprint", self.intent_fingerprint),\n            ("market_fingerprint", self.market_fingerprint),\n            ("account_attestation_fingerprint", self.account_attestation_fingerprint),\n',
        "package market hash validation",
    )
    text = replace_once(
        text,
        '        if not _finite_positive(self.notional):\n',
        '        if (\n            isinstance(self.risk_decision_safety_state_version, bool)\n            or not isinstance(self.risk_decision_safety_state_version, int)\n            or self.risk_decision_safety_state_version < 0\n        ):\n            raise ValueError("risk_decision_safety_state_version must be a non-negative integer")\n        if not _finite_positive(self.notional):\n',
        "package safety version validation",
    )
    text = replace_once(
        text,
        '        attempt_id = deterministic_canary_attempt_id(\n            order=order,\n            binding=binding,\n            bracket=bracket,\n            approval=approval,\n        )\n',
        '        attempt_id = deterministic_canary_attempt_id(\n            order=order,\n            decision=decision,\n            binding=binding,\n            bracket=bracket,\n            approval=approval,\n        )\n',
        "attempt call RiskDecision binding",
    )
    text = replace_once(
        text,
        '    order: OrderRecord,\n    binding: PaperSubmissionBinding,\n    bracket: AlpacaEquityBracketRequest,\n    approval: PaperCanaryApproval,\n) -> str:\n',
        '    order: OrderRecord,\n    decision: RiskDecision,\n    binding: PaperSubmissionBinding,\n    bracket: AlpacaEquityBracketRequest,\n    approval: PaperCanaryApproval,\n) -> str:\n',
        "attempt signature RiskDecision binding",
    )
    text = replace_once(
        text,
        '        "risk_decision_id": order.risk_decision_id,\n    }\n    return f"r6-paper-attempt-{_hash_json(payload)[:48]}"\n',
        '        "risk_decision_id": order.risk_decision_id,\n        "risk_decision_safety_state_version": decision.safety_state_version,\n        "market_fingerprint": decision.market_fingerprint,\n    }\n    return f"r6-paper-attempt-{_hash_json(payload)[:48]}"\n',
        "attempt control-plane binding",
    )
    text = replace_once(
        text,
        '        "risk_decision_id": decision.decision_id,\n        "risk_decision_valid_until": decision.valid_until,\n',
        '        "risk_decision_id": decision.decision_id,\n        "risk_decision_safety_state_version": decision.safety_state_version,\n        "market_fingerprint": decision.market_fingerprint,\n        "risk_decision_valid_until": decision.valid_until,\n',
        "build package control-plane values",
    )
    text = replace_once(
        text,
        '        "risk_decision_id": package.risk_decision_id,\n        "risk_decision_valid_until": package.risk_decision_valid_until,\n',
        '        "risk_decision_id": package.risk_decision_id,\n        "risk_decision_safety_state_version": package.risk_decision_safety_state_version,\n        "market_fingerprint": package.market_fingerprint,\n        "risk_decision_valid_until": package.risk_decision_valid_until,\n',
        "serialize package control-plane values",
    )
    text = replace_once(
        text,
        '        "risk_decision_id": values["risk_decision_id"],\n        "risk_decision_valid_until": _iso(values["risk_decision_valid_until"]),\n',
        '        "risk_decision_id": values["risk_decision_id"],\n        "risk_decision_safety_state_version": values["risk_decision_safety_state_version"],\n        "market_fingerprint": values["market_fingerprint"],\n        "risk_decision_valid_until": _iso(values["risk_decision_valid_until"]),\n',
        "canonical payload control-plane values",
    )
    MODULE.write_text(text, encoding="utf-8")

    test = TEST.read_text(encoding="utf-8")
    test = replace_once(
        test,
        '    assert result.package.next_action == "OPERATOR_DECISION_REQUIRED"\n',
        '    assert result.package.next_action == "OPERATOR_DECISION_REQUIRED"\n    assert result.package.risk_decision_safety_state_version == 0\n    assert result.package.market_fingerprint == market_fingerprint(market())\n',
        "coordinator package control-plane assertions",
    )
    test = replace_once(
        test,
        '    with pytest.raises(ValueError, match="hash mismatch"):\n        replace(package, package_hash="0" * 64)\n',
        '    with pytest.raises(ValueError, match="hash mismatch"):\n        replace(package, package_hash="0" * 64)\n    with pytest.raises(ValueError, match="hash mismatch"):\n        replace(package, market_fingerprint="f" * 64)\n    with pytest.raises(ValueError, match="non-negative integer"):\n        replace(package, risk_decision_safety_state_version=-1)\n',
        "package tamper/control assertions",
    )
    TEST.write_text(test, encoding="utf-8")
    print("prepared PAPER canary package now binds RiskDecision market fingerprint and Safety version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
