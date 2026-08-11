from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OMS = ROOT / "src/autotrade/oms.py"
WRITER = ROOT / "src/autotrade/brokers/alpaca_paper_writer.py"
WRITER_TEST = ROOT / "tests/test_r6_paper_writer.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{label}: expected one anchor, found {text.count(old)}")
    return text.replace(old, new, 1)


def patch_oms() -> None:
    text = OMS.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "    risk_decision_id: str\n    authorized_at: datetime\n",
        "    risk_decision_id: str\n    safety_state_version: int\n    market_fingerprint: str\n    decision_valid_until: datetime\n    authorized_at: datetime\n",
        "handoff control-plane fields",
    )
    text = replace_once(
        text,
        '''        _require_external_identity(self.risk_decision_id, "risk_decision_id")\n        _require_sha256(self.handoff_id, "handoff_id")\n        _require_sha256(self.intent_fingerprint, "intent_fingerprint")\n        if self.authorized_at.tzinfo is None or self.authorized_at.utcoffset() is None:\n            raise ValueError("authorized_at must be timezone-aware")\n''',
        '''        _require_external_identity(self.risk_decision_id, "risk_decision_id")\n        _require_sha256(self.handoff_id, "handoff_id")\n        _require_sha256(self.intent_fingerprint, "intent_fingerprint")\n        _require_sha256(self.market_fingerprint, "market_fingerprint")\n        if isinstance(self.safety_state_version, bool) or not isinstance(self.safety_state_version, int) or self.safety_state_version < 0:\n            raise ValueError("safety_state_version must be integer >= 0")\n        if self.decision_valid_until.tzinfo is None or self.decision_valid_until.utcoffset() is None:\n            raise ValueError("decision_valid_until must be timezone-aware")\n        if self.authorized_at.tzinfo is None or self.authorized_at.utcoffset() is None:\n            raise ValueError("authorized_at must be timezone-aware")\n        if self.authorized_at > self.decision_valid_until:\n            raise ValueError("external handoff cannot outlive RiskDecision")\n''',
        "handoff control-plane validation",
    )
    text = replace_once(
        text,
        '''            risk_decision_id=self.risk_decision_id,\n            authorized_at=self.authorized_at,\n            event_id=self.event_id,\n''',
        '''            risk_decision_id=self.risk_decision_id,\n            safety_state_version=self.safety_state_version,\n            market_fingerprint_value=self.market_fingerprint,\n            decision_valid_until=self.decision_valid_until,\n            authorized_at=self.authorized_at,\n            event_id=self.event_id,\n''',
        "handoff hash control-plane args",
    )
    text = replace_once(
        text,
        '''            "risk_decision_id": self.risk_decision_id,\n            "authorized_at": self.authorized_at.isoformat(),\n''',
        '''            "risk_decision_id": self.risk_decision_id,\n            "safety_state_version": str(self.safety_state_version),\n            "market_fingerprint": self.market_fingerprint,\n            "decision_valid_until": self.decision_valid_until.isoformat(),\n            "authorized_at": self.authorized_at.isoformat(),\n''',
        "handoff event control-plane fields",
    )

    text = replace_once(
        text,
        '''        handoff_id: str,\n        expected_intent_fingerprint: str,\n        expected_risk_decision_id: str,\n        now: datetime,\n''',
        '''        handoff_id: str,\n        decision: RiskDecision,\n        market: MarketSnapshot,\n        now: datetime,\n''',
        "stage control-plane signature",
    )
    text = replace_once(
        text,
        '''        _require_external_identity(order_id, "order_id")\n        _require_sha256(handoff_id, "handoff_id")\n        _require_sha256(expected_intent_fingerprint, "expected_intent_fingerprint")\n        _require_external_identity(expected_risk_decision_id, "expected_risk_decision_id")\n\n        current = self._orders.get_by_order_id(order_id)\n        if current is None:\n            raise KeyError(order_id)\n        current_fingerprint = intent_fingerprint(current.intent)\n        if current_fingerprint != expected_intent_fingerprint:\n            raise ExternalSubmissionHandoffConflict("external handoff intent fingerprint mismatch")\n        if current.risk_decision_id != expected_risk_decision_id:\n            raise ExternalSubmissionHandoffConflict("external handoff risk decision mismatch")\n        self._validate_external_stage_controls(order=current, now=now)\n''',
        '''        _require_external_identity(order_id, "order_id")\n        _require_sha256(handoff_id, "handoff_id")\n        if not isinstance(decision, RiskDecision):\n            raise TypeError("external handoff requires RiskDecision")\n        if not isinstance(market, MarketSnapshot):\n            raise TypeError("external handoff requires MarketSnapshot")\n\n        current = self._orders.get_by_order_id(order_id)\n        if current is None:\n            raise KeyError(order_id)\n        current_fingerprint = intent_fingerprint(current.intent)\n        if current.risk_decision_id != decision.decision_id:\n            raise ExternalSubmissionHandoffConflict("external handoff risk decision mismatch")\n        # Re-run the normal OMS control-plane validation at the staging instant.\n        # This binds the handoff to the original RiskDecision/MarketSnapshot and\n        # rejects any intervening Safety-state version change, even if a kill or\n        # circuit was activated and later reset.\n        self._validate_control_plane(\n            intent=current.intent,\n            decision=decision,\n            market=market,\n            now=now,\n            fingerprint=current_fingerprint,\n        )\n        self._validate_external_stage_controls(order=current, now=now)\n''',
        "stage control-plane revalidation",
    )
    text = replace_once(
        text,
        '''                or handoff.handoff_id != handoff_id\n                or handoff.intent_fingerprint != expected_intent_fingerprint\n                or handoff.risk_decision_id != expected_risk_decision_id\n''',
        '''                or handoff.handoff_id != handoff_id\n                or handoff.intent_fingerprint != current_fingerprint\n                or handoff.risk_decision_id != decision.decision_id\n                or handoff.safety_state_version != decision.safety_state_version\n                or handoff.market_fingerprint != decision.market_fingerprint\n                or handoff.decision_valid_until != decision.valid_until\n''',
        "stage durable control-plane comparison",
    )
    text = replace_once(
        text,
        '''                intent_fingerprint_value=expected_intent_fingerprint,\n                risk_decision_id=expected_risk_decision_id,\n                authorized_at=now,\n''',
        '''                intent_fingerprint_value=current_fingerprint,\n                risk_decision_id=decision.decision_id,\n                safety_state_version=decision.safety_state_version,\n                market_fingerprint_value=decision.market_fingerprint,\n                decision_valid_until=decision.valid_until,\n                authorized_at=now,\n''',
        "stage handoff build control-plane args",
    )

    text = replace_once(
        text,
        '''    risk_decision_id: str,\n    authorized_at: datetime,\n    event_id: str,\n''',
        '''    risk_decision_id: str,\n    safety_state_version: int,\n    market_fingerprint_value: str,\n    decision_valid_until: datetime,\n    authorized_at: datetime,\n    event_id: str,\n''',
        "hash helper signature",
    )
    text = replace_once(
        text,
        '''        "risk_decision_id": risk_decision_id,\n    }\n''',
        '''        "risk_decision_id": risk_decision_id,\n        "safety_state_version": safety_state_version,\n        "market_fingerprint": market_fingerprint_value,\n        "decision_valid_until": decision_valid_until.isoformat(),\n    }\n''',
        "hash helper payload",
    )
    text = replace_once(
        text,
        '''    risk_decision_id: str,\n    authorized_at: datetime,\n) -> ExternalSubmissionHandoff:\n''',
        '''    risk_decision_id: str,\n    safety_state_version: int,\n    market_fingerprint_value: str,\n    decision_valid_until: datetime,\n    authorized_at: datetime,\n) -> ExternalSubmissionHandoff:\n''',
        "build helper signature",
    )
    text = replace_once(
        text,
        '''        risk_decision_id=risk_decision_id,\n        authorized_at=authorized_at,\n        event_id=event_id,\n''',
        '''        risk_decision_id=risk_decision_id,\n        safety_state_version=safety_state_version,\n        market_fingerprint_value=market_fingerprint_value,\n        decision_valid_until=decision_valid_until,\n        authorized_at=authorized_at,\n        event_id=event_id,\n''',
        "build hash call",
    )
    text = replace_once(
        text,
        '''        risk_decision_id=risk_decision_id,\n        authorized_at=authorized_at,\n        event_id=event_id,\n        handoff_hash=handoff_hash,\n''',
        '''        risk_decision_id=risk_decision_id,\n        safety_state_version=safety_state_version,\n        market_fingerprint=market_fingerprint_value,\n        decision_valid_until=decision_valid_until,\n        authorized_at=authorized_at,\n        event_id=event_id,\n        handoff_hash=handoff_hash,\n''',
        "build object control-plane fields",
    )
    text = replace_once(
        text,
        '''        "risk_decision_id",\n        "authorized_at",\n''',
        '''        "risk_decision_id",\n        "safety_state_version",\n        "market_fingerprint",\n        "decision_valid_until",\n        "authorized_at",\n''',
        "event expected control-plane keys",
    )
    text = replace_once(
        text,
        '''            risk_decision_id=payload["risk_decision_id"],\n            authorized_at=authorized_at,\n''',
        '''            risk_decision_id=payload["risk_decision_id"],\n            safety_state_version=int(payload["safety_state_version"]),\n            market_fingerprint=payload["market_fingerprint"],\n            decision_valid_until=datetime.fromisoformat(payload["decision_valid_until"]),\n            authorized_at=authorized_at,\n''',
        "event object control-plane fields",
    )
    OMS.write_text(text, encoding="utf-8")


def patch_writer() -> None:
    text = WRITER.read_text(encoding="utf-8")
    anchor = (
        "        if external_handoff.risk_decision_id != binding.risk_decision_id:\n"
        "            raise PaperWriterBlocked(\"OMS external handoff risk decision mismatch\")\n"
    )
    value = anchor + (
        "        if external_handoff.authorized_at < approval.issued_at or external_handoff.authorized_at >= approval.expires_at:\n"
        "            raise PaperWriterBlocked(\"OMS external handoff is outside canary approval window\")\n"
        "        if now > external_handoff.decision_valid_until:\n"
        "            raise PaperWriterBlocked(\"OMS external handoff RiskDecision has expired\")\n"
    )
    text = replace_once(text, anchor, value, "writer handoff timing")

    guard_anchor = (
        "        except PaperFinalWriteBlocked as exc:\n"
        "            raise PaperWriterBlocked(f\"final PRE_CONSUME guard rejected: {exc}\") from exc\n\n"
        "        # Crash-safety order is deliberate:\n"
    )
    guard_value = (
        "        except PaperFinalWriteBlocked as exc:\n"
        "            raise PaperWriterBlocked(f\"final PRE_CONSUME guard rejected: {exc}\") from exc\n"
        "        if pre_consume_guard.safety_state_version != external_handoff.safety_state_version:\n"
        "            raise PaperWriterBlocked(\"Safety version changed after OMS external handoff\")\n\n"
        "        # Crash-safety order is deliberate:\n"
    )
    text = replace_once(text, guard_anchor, guard_value, "writer safety-version chain")
    WRITER.write_text(text, encoding="utf-8")


def patch_writer_test() -> None:
    text = WRITER_TEST.read_text(encoding="utf-8")
    domain_old = '''    OrderIntent,\n    OrderRecord,\n    OrderStatus,\n    OrderType,\n    PortfolioSnapshot,\n    Side,\n)\n'''
    domain_new = '''    MarketSnapshot,\n    OrderIntent,\n    OrderRecord,\n    OrderStatus,\n    OrderType,\n    PortfolioSnapshot,\n    RiskDecision,\n    RiskDecisionStatus,\n    Side,\n    intent_fingerprint,\n    market_fingerprint,\n)\n'''
    text = replace_once(text, domain_old, domain_new, "writer test domain imports")

    bracket_anchor = "\n\ndef bracket():\n"
    helpers = r'''


def market() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="AAPL",
        bid=Decimal("9.99"),
        ask=Decimal("10.01"),
        last=Decimal("10"),
        observed_at=NOW - timedelta(milliseconds=200),
    )


def risk_decision(current_order=None) -> RiskDecision:
    current_order = current_order or order()
    current_market = market()
    return RiskDecision(
        decision_id=current_order.risk_decision_id,
        intent_id=current_order.intent.intent_id,
        status=RiskDecisionStatus.APPROVED,
        reason_code="APPROVED",
        reason_detail="R6 writer fixture",
        evaluated_at=NOW - timedelta(milliseconds=150),
        valid_until=NOW + timedelta(seconds=10),
        limits_version="r6-writer-test",
        intent_fingerprint=intent_fingerprint(current_order.intent),
        market_fingerprint=market_fingerprint(current_market),
        approved_notional=Decimal("10"),
        risk_reducing=False,
        safety_state_version=0,
    )
'''
    text = replace_once(text, bracket_anchor, helpers + bracket_anchor, "writer test control-plane helpers")

    stage_old = '''        handoff_id=approval.approval_hash,\n        expected_intent_fingerprint=binding.intent_fingerprint,\n        expected_risk_decision_id=binding.risk_decision_id,\n        now=NOW + timedelta(milliseconds=100),\n'''
    stage_new = '''        handoff_id=approval.approval_hash,\n        decision=risk_decision(current_order),\n        market=market(),\n        now=NOW + timedelta(milliseconds=100),\n'''
    text = replace_once(text, stage_old, stage_new, "writer test stage control plane")
    WRITER_TEST.write_text(text, encoding="utf-8")


def main() -> int:
    patch_oms()
    patch_writer()
    patch_writer_test()
    print("TD-R6-010 control-plane refinement applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
