from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OMS = ROOT / "src/autotrade/oms.py"
WRITER = ROOT / "src/autotrade/brokers/alpaca_paper_writer.py"
WRITER_TEST = ROOT / "tests/test_r6_paper_writer.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{label}: expected one anchor, found {text.count(old)}")
    return text.replace(old, new, 1)


def patch_oms() -> None:
    text = OMS.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from dataclasses import replace",
        "from dataclasses import dataclass, replace",
        "oms dataclass import",
    )
    text = replace_once(
        text,
        "from .health_bridge import HealthBridgeControlProvider, HealthBridgeError",
        "from .health_bridge import HealthBridgeControlProvider, HealthBridgeError, HealthRiskMode",
        "oms health import",
    )
    class_anchor = "\n\nclass OrderManagementSystem:\n"
    classes = r'''

class ExternalSubmissionHandoffError(RuntimeError):
    pass


class ExternalSubmissionHandoffConflict(ExternalSubmissionHandoffError):
    pass


@dataclass(frozen=True, slots=True)
class ExternalSubmissionHandoff:
    handoff_id: str
    order_id: str
    intent_fingerprint: str
    risk_decision_id: str
    authorized_at: datetime
    event_id: str
    handoff_hash: str

    def __post_init__(self) -> None:
        _require_external_identity(self.order_id, "order_id")
        _require_external_identity(self.risk_decision_id, "risk_decision_id")
        _require_sha256(self.handoff_id, "handoff_id")
        _require_sha256(self.intent_fingerprint, "intent_fingerprint")
        if self.authorized_at.tzinfo is None or self.authorized_at.utcoffset() is None:
            raise ValueError("authorized_at must be timezone-aware")
        expected_event_id = f"external-handoff:{self.order_id}:{self.handoff_id}"
        if self.event_id != expected_event_id:
            raise ValueError("external handoff event_id mismatch")
        _require_sha256(self.handoff_hash, "handoff_hash")
        expected_hash = _external_handoff_hash(
            handoff_id=self.handoff_id,
            order_id=self.order_id,
            intent_fingerprint_value=self.intent_fingerprint,
            risk_decision_id=self.risk_decision_id,
            authorized_at=self.authorized_at,
            event_id=self.event_id,
        )
        if self.handoff_hash != expected_hash:
            raise ValueError("external handoff hash mismatch")

    def to_event_payload(self) -> dict[str, str]:
        return {
            "handoff_id": self.handoff_id,
            "order_id": self.order_id,
            "intent_fingerprint": self.intent_fingerprint,
            "risk_decision_id": self.risk_decision_id,
            "authorized_at": self.authorized_at.isoformat(),
            "event_id": self.event_id,
            "handoff_hash": self.handoff_hash,
        }
'''
    text = replace_once(text, class_anchor, classes + class_anchor, "oms handoff classes")

    method_anchor = "    def submit(\n"
    methods = r'''    def validate_for_external_submission(
        self,
        *,
        intent: OrderIntent,
        decision: RiskDecision,
        market: MarketSnapshot,
        now: datetime,
    ) -> OrderRecord:
        """Create/recover a durable VALIDATED order without invoking any broker.

        This is the only OMS-owned entry point for the R6 external PAPER path.
        It reuses the normal deterministic control-plane validation but stops
        before SUBMITTING and before broker I/O.
        """
        fingerprint = intent_fingerprint(intent)
        self._validate_control_plane(
            intent=intent,
            decision=decision,
            market=market,
            now=now,
            fingerprint=fingerprint,
        )
        candidate = OrderRecord(
            order_id=str(uuid4()),
            intent=intent,
            risk_decision_id=decision.decision_id,
            status=OrderStatus.VALIDATED,
            created_at=now,
        )
        created, stored = self._orders.create_if_absent(candidate)
        if not created:
            if intent_fingerprint(stored.intent) != fingerprint:
                raise IdempotencyConflict(intent.idempotency_key)
            if stored.risk_decision_id != decision.decision_id:
                raise ExternalSubmissionHandoffConflict(
                    "existing external order is bound to a different risk decision"
                )
            if stored.status is not OrderStatus.VALIDATED:
                raise ExternalSubmissionHandoffConflict(
                    f"external validation requires VALIDATED state, found {stored.status.value}"
                )

        # Always repair/verify the durable validation ledger event. If a prior
        # process crashed after order-store commit but before ledger append, the
        # same idempotent retry restores evidence without broker I/O.
        self._append_idempotent(
            LedgerEvent(
                event_id=f"order-validated:{stored.order_id}",
                event_type="ORDER_VALIDATED",
                occurred_at=stored.created_at,
                payload={
                    "order_id": stored.order_id,
                    "intent_id": stored.intent.intent_id,
                    "risk_decision_id": stored.risk_decision_id,
                    "idempotency_key": stored.intent.idempotency_key,
                },
            )
        )
        return stored

    def stage_external_submission(
        self,
        *,
        order_id: str,
        handoff_id: str,
        expected_intent_fingerprint: str,
        expected_risk_decision_id: str,
        now: datetime,
    ) -> tuple[OrderRecord, ExternalSubmissionHandoff]:
        """Authorize one external handoff and durably enter SUBMITTING.

        The handoff ledger event is committed before the order-status update.
        A crash between the two is therefore safely replayable: retry rechecks
        Safety/Health, verifies the same event, and completes the transition.
        SUBMITTING without the exact handoff event fails closed.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("external handoff time must be timezone-aware")
        _require_external_identity(order_id, "order_id")
        _require_sha256(handoff_id, "handoff_id")
        _require_sha256(expected_intent_fingerprint, "expected_intent_fingerprint")
        _require_external_identity(expected_risk_decision_id, "expected_risk_decision_id")

        current = self._orders.get_by_order_id(order_id)
        if current is None:
            raise KeyError(order_id)
        current_fingerprint = intent_fingerprint(current.intent)
        if current_fingerprint != expected_intent_fingerprint:
            raise ExternalSubmissionHandoffConflict("external handoff intent fingerprint mismatch")
        if current.risk_decision_id != expected_risk_decision_id:
            raise ExternalSubmissionHandoffConflict("external handoff risk decision mismatch")
        self._validate_external_stage_controls(order=current, now=now)

        event_id = f"external-handoff:{order_id}:{handoff_id}"
        existing_events = tuple(
            event for event in self._ledger.all_events() if event.event_id == event_id
        )
        if len(existing_events) > 1:
            raise ExternalSubmissionHandoffConflict("duplicate external handoff ledger identity")
        if existing_events:
            handoff = _external_handoff_from_event(existing_events[0])
            if (
                handoff.order_id != order_id
                or handoff.handoff_id != handoff_id
                or handoff.intent_fingerprint != expected_intent_fingerprint
                or handoff.risk_decision_id != expected_risk_decision_id
            ):
                raise ExternalSubmissionHandoffConflict("external handoff ledger binding mismatch")
        else:
            if current.status is not OrderStatus.VALIDATED:
                raise ExternalSubmissionHandoffConflict(
                    "SUBMITTING without a durable OMS external-handoff event is forbidden"
                )
            handoff = _build_external_handoff(
                handoff_id=handoff_id,
                order_id=order_id,
                intent_fingerprint_value=expected_intent_fingerprint,
                risk_decision_id=expected_risk_decision_id,
                authorized_at=now,
            )
            self._append_idempotent(
                LedgerEvent(
                    event_id=handoff.event_id,
                    event_type="EXTERNAL_ORDER_HANDOFF_AUTHORIZED",
                    occurred_at=handoff.authorized_at,
                    payload=handoff.to_event_payload(),
                )
            )

        if current.status is OrderStatus.VALIDATED:
            staged = replace(
                current,
                status=OrderStatus.SUBMITTING,
                submitted_at=handoff.authorized_at,
            )
            self._orders.update(staged)
        elif current.status is OrderStatus.SUBMITTING:
            if current.submitted_at != handoff.authorized_at:
                raise ExternalSubmissionHandoffConflict(
                    "SUBMITTING timestamp is not bound to external handoff"
                )
            staged = current
        else:
            raise ExternalSubmissionHandoffConflict(
                f"external handoff cannot stage from {current.status.value}"
            )
        return staged, handoff

    def verify_external_submission_handoff(
        self,
        handoff: ExternalSubmissionHandoff,
    ) -> OrderRecord:
        """Verify durable OMS ownership of a handoff before external I/O."""
        if not isinstance(handoff, ExternalSubmissionHandoff):
            raise ExternalSubmissionHandoffConflict("external handoff object is required")
        matches = tuple(
            event for event in self._ledger.all_events() if event.event_id == handoff.event_id
        )
        if len(matches) != 1:
            raise ExternalSubmissionHandoffConflict("external handoff ledger event is missing or duplicated")
        durable = _external_handoff_from_event(matches[0])
        if durable != handoff:
            raise ExternalSubmissionHandoffConflict("external handoff object does not match durable ledger")
        current = self._orders.get_by_order_id(handoff.order_id)
        if current is None:
            raise ExternalSubmissionHandoffConflict("external handoff OMS order is missing")
        if current.status is not OrderStatus.SUBMITTING:
            raise ExternalSubmissionHandoffConflict("external handoff requires OMS SUBMITTING state")
        if current.submitted_at != handoff.authorized_at:
            raise ExternalSubmissionHandoffConflict("external handoff SUBMITTING timestamp mismatch")
        if intent_fingerprint(current.intent) != handoff.intent_fingerprint:
            raise ExternalSubmissionHandoffConflict("external handoff OMS intent changed")
        if current.risk_decision_id != handoff.risk_decision_id:
            raise ExternalSubmissionHandoffConflict("external handoff OMS risk decision changed")
        return current

    def _validate_external_stage_controls(self, *, order: OrderRecord, now: datetime) -> None:
        if order.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTING}:
            raise ExternalSubmissionHandoffConflict(
                f"external handoff requires VALIDATED/SUBMITTING, found {order.status.value}"
            )
        if self._safety_state_store is None:
            raise ExternalSubmissionHandoffConflict(
                "external handoff requires authoritative Safety state store"
            )
        safety = self._safety_state_store.get()
        if safety.kill_switch_active:
            raise ExternalSubmissionHandoffConflict("external handoff blocked by kill switch")
        if safety.circuit_active:
            raise ExternalSubmissionHandoffConflict("external handoff blocked by safety circuit")
        if self._health_bridge is None or not self._portfolio_health_entity_id:
            raise ExternalSubmissionHandoffConflict(
                "external handoff requires authoritative Health bridge"
            )
        try:
            control = self._health_bridge.effective_control(
                strategy_id=order.intent.strategy_id,
                portfolio_entity_id=self._portfolio_health_entity_id,
                now=now,
            )
        except Exception as exc:  # fail closed at the capital boundary
            raise ExternalSubmissionHandoffConflict(
                "external handoff Health control is unavailable"
            ) from exc
        if control.mode is not HealthRiskMode.NORMAL:
            raise ExternalSubmissionHandoffConflict("external handoff Health mode is not NORMAL")
        if (
            control.order_multiplier != Decimal("1")
            or control.strategy_multiplier != Decimal("1")
            or control.portfolio_multiplier != Decimal("1")
        ):
            raise ExternalSubmissionHandoffConflict(
                "external handoff Health multipliers must be exactly 1"
            )

'''
    text = replace_once(text, method_anchor, methods + method_anchor, "oms external methods")

    helpers = r'''


def _require_external_identity(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be canonical non-empty text")


def _require_sha256(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _external_handoff_hash(
    *,
    handoff_id: str,
    order_id: str,
    intent_fingerprint_value: str,
    risk_decision_id: str,
    authorized_at: datetime,
    event_id: str,
) -> str:
    payload = {
        "authorized_at": authorized_at.isoformat(),
        "event_id": event_id,
        "handoff_id": handoff_id,
        "intent_fingerprint": intent_fingerprint_value,
        "order_id": order_id,
        "risk_decision_id": risk_decision_id,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _build_external_handoff(
    *,
    handoff_id: str,
    order_id: str,
    intent_fingerprint_value: str,
    risk_decision_id: str,
    authorized_at: datetime,
) -> ExternalSubmissionHandoff:
    event_id = f"external-handoff:{order_id}:{handoff_id}"
    handoff_hash = _external_handoff_hash(
        handoff_id=handoff_id,
        order_id=order_id,
        intent_fingerprint_value=intent_fingerprint_value,
        risk_decision_id=risk_decision_id,
        authorized_at=authorized_at,
        event_id=event_id,
    )
    return ExternalSubmissionHandoff(
        handoff_id=handoff_id,
        order_id=order_id,
        intent_fingerprint=intent_fingerprint_value,
        risk_decision_id=risk_decision_id,
        authorized_at=authorized_at,
        event_id=event_id,
        handoff_hash=handoff_hash,
    )


def _external_handoff_from_event(event: LedgerEvent) -> ExternalSubmissionHandoff:
    if event.event_type != "EXTERNAL_ORDER_HANDOFF_AUTHORIZED":
        raise ExternalSubmissionHandoffConflict("external handoff ledger event type mismatch")
    payload = dict(event.payload)
    expected_keys = {
        "handoff_id",
        "order_id",
        "intent_fingerprint",
        "risk_decision_id",
        "authorized_at",
        "event_id",
        "handoff_hash",
    }
    if set(payload) != expected_keys:
        raise ExternalSubmissionHandoffConflict("external handoff ledger payload surface mismatch")
    try:
        authorized_at = datetime.fromisoformat(payload["authorized_at"])
        handoff = ExternalSubmissionHandoff(
            handoff_id=payload["handoff_id"],
            order_id=payload["order_id"],
            intent_fingerprint=payload["intent_fingerprint"],
            risk_decision_id=payload["risk_decision_id"],
            authorized_at=authorized_at,
            event_id=payload["event_id"],
            handoff_hash=payload["handoff_hash"],
        )
    except (TypeError, ValueError) as exc:
        raise ExternalSubmissionHandoffConflict("external handoff ledger payload is invalid") from exc
    if event.event_id != handoff.event_id or event.occurred_at != handoff.authorized_at:
        raise ExternalSubmissionHandoffConflict("external handoff ledger timestamp/identity mismatch")
    return handoff
'''
    text = text.rstrip() + helpers + "\n"
    OMS.write_text(text, encoding="utf-8")


def patch_writer() -> None:
    text = WRITER.read_text(encoding="utf-8")
    import_anchor = "from .alpaca_paper_bracket import AlpacaEquityBracketRequest\n"
    import_value = (
        "from autotrade.oms import ExternalSubmissionHandoff, OrderManagementSystem\n\n"
        + import_anchor
    )
    text = replace_once(text, import_anchor, import_value, "writer oms import")

    sig_anchor = (
        "        submission_registry: SQLitePaperSubmissionRegistry,\n"
        "        final_guard: PaperFinalWriteGuard,\n"
    )
    sig_value = (
        "        submission_registry: SQLitePaperSubmissionRegistry,\n"
        "        oms: OrderManagementSystem,\n"
        "        external_handoff: ExternalSubmissionHandoff,\n"
        "        final_guard: PaperFinalWriteGuard,\n"
    )
    text = replace_once(text, sig_anchor, sig_value, "writer signature")

    type_anchor = (
        "        if not isinstance(final_guard, PaperFinalWriteGuard):\n"
        "            raise PaperWriterBlocked(\"writer requires authoritative PaperFinalWriteGuard\")\n"
    )
    type_value = type_anchor + (
        "        if not isinstance(oms, OrderManagementSystem):\n"
        "            raise PaperWriterBlocked(\"writer requires authoritative OrderManagementSystem\")\n"
        "        if not isinstance(external_handoff, ExternalSubmissionHandoff):\n"
        "            raise PaperWriterBlocked(\"writer requires durable OMS external handoff\")\n"
    )
    text = replace_once(text, type_anchor, type_value, "writer handoff types")

    verify_anchor = (
        "        if approval.account_attestation_fingerprint != account_attestation.fingerprint:\n"
        "            raise PaperWriterBlocked(\"canary approval account attestation mismatch\")\n\n"
    )
    verify_value = verify_anchor + (
        "        if external_handoff.handoff_id != approval.approval_hash:\n"
        "            raise PaperWriterBlocked(\"OMS external handoff is not bound to canary approval\")\n"
        "        if external_handoff.order_id != binding.order_id:\n"
        "            raise PaperWriterBlocked(\"OMS external handoff order_id mismatch\")\n"
        "        if external_handoff.intent_fingerprint != binding.intent_fingerprint:\n"
        "            raise PaperWriterBlocked(\"OMS external handoff intent fingerprint mismatch\")\n"
        "        if external_handoff.risk_decision_id != binding.risk_decision_id:\n"
        "            raise PaperWriterBlocked(\"OMS external handoff risk decision mismatch\")\n"
        "        try:\n"
        "            staged_order = oms.verify_external_submission_handoff(external_handoff)\n"
        "        except Exception as exc:  # OMS/ledger/order evidence must fail closed\n"
        "            raise PaperWriterBlocked(\"durable OMS external handoff verification failed\") from exc\n"
        "        if staged_order.order_id != binding.order_id:\n"
        "            raise PaperWriterBlocked(\"verified OMS external handoff order mismatch\")\n\n"
    )
    text = replace_once(text, verify_anchor, verify_value, "writer handoff verification")
    WRITER.write_text(text, encoding="utf-8")


def patch_writer_test() -> None:
    text = WRITER_TEST.read_text(encoding="utf-8")
    import_anchor = "from autotrade.health_bridge import EffectiveHealthControl, HealthRiskMode\n"
    import_value = (
        "from autotrade.health_bridge import EffectiveHealthControl, HealthRiskMode\n"
        "from autotrade.ledger import InMemoryEventLedger\n"
        "from autotrade.oms import OrderManagementSystem\n"
    )
    text = replace_once(text, import_anchor, import_value, "writer test imports")

    fake_anchor = "\n\nclass FakeWriteTransport:\n"
    fake_broker = r'''

class NeverCalledBroker:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, *, order, market, now):
        del order, market, now
        self.calls += 1
        raise AssertionError("OMS external handoff must never invoke internal broker")
'''
    text = replace_once(text, fake_anchor, fake_broker + fake_anchor, "writer test fake broker")

    old_stack = '''    order_store = InMemoryOrderStore()\n    order_store.create_if_absent(current_order)\n    order_store.update(\n        replace(\n            current_order,\n            status=OrderStatus.SUBMITTING,\n            submitted_at=NOW + timedelta(milliseconds=100),\n        )\n    )\n    safety_store = safety_store or InMemorySafetyStateStore()\n    portfolio_store = InMemoryPortfolioStore()\n'''
    new_stack = '''    order_store = InMemoryOrderStore()\n    order_store.create_if_absent(current_order)\n    safety_store = safety_store or InMemorySafetyStateStore()\n    health_bridge = HealthyBridge()\n    oms = OrderManagementSystem(\n        broker=NeverCalledBroker(),\n        ledger=InMemoryEventLedger(),\n        order_store=order_store,\n        safety_state_store=safety_store,\n        health_bridge=health_bridge,\n        portfolio_health_entity_id="portfolio-r6-canary",\n    )\n    _, external_handoff = oms.stage_external_submission(\n        order_id=current_order.order_id,\n        handoff_id=approval.approval_hash,\n        expected_intent_fingerprint=binding.intent_fingerprint,\n        expected_risk_decision_id=binding.risk_decision_id,\n        now=NOW + timedelta(milliseconds=100),\n    )\n    portfolio_store = InMemoryPortfolioStore()\n'''
    text = replace_once(text, old_stack, new_stack, "writer test stack handoff")

    guard_anchor = '''        health_bridge=HealthyBridge(),\n        portfolio_health_entity_id="portfolio-r6-canary",\n'''
    guard_value = '''        health_bridge=health_bridge,\n        portfolio_health_entity_id="portfolio-r6-canary",\n'''
    text = replace_once(text, guard_anchor, guard_value, "writer test common bridge")

    return_anchor = '''        "portfolio_store": portfolio_store,\n        "final_guard": final_guard,\n'''
    return_value = '''        "portfolio_store": portfolio_store,\n        "oms": oms,\n        "handoff": external_handoff,\n        "final_guard": final_guard,\n'''
    text = replace_once(text, return_anchor, return_value, "writer test return handoff")

    submit_anchor = '''        submission_registry=values["submission_registry"],\n        final_guard=values["final_guard"],\n'''
    submit_value = '''        submission_registry=values["submission_registry"],\n        oms=values["oms"],\n        external_handoff=values["handoff"],\n        final_guard=values["final_guard"],\n'''
    text = replace_once(text, submit_anchor, submit_value, "writer test submit handoff")
    WRITER_TEST.write_text(text, encoding="utf-8")


def patch_workflows() -> None:
    core = CORE.read_text(encoding="utf-8")
    core_anchor = (
        "      - name: R6 permanent LIVE-deny authority boundary\n"
        "        run: python scripts/check_r6_live_deny_boundary.py\n\n"
    )
    core_value = core_anchor + (
        "      - name: R6 OMS external-handoff boundary\n"
        "        run: python scripts/check_r6_oms_handoff_boundary.py\n\n"
    )
    core = replace_once(core, core_anchor, core_value, "core OMS handoff gate")
    CORE.write_text(core, encoding="utf-8")

    r6 = R6.read_text(encoding="utf-8")
    r6_anchor = (
        "      - name: R6 permanent LIVE-deny authority boundary\n"
        "        run: python scripts/check_r6_live_deny_boundary.py\n"
    )
    r6_value = r6_anchor + (
        "      - name: R6 OMS external-handoff boundary\n"
        "        run: python scripts/check_r6_oms_handoff_boundary.py\n"
    )
    r6 = replace_once(r6, r6_anchor, r6_value, "r6 OMS handoff gate")
    old_tests = (
        "pytest -q tests/test_r6_authority_checker.py tests/test_r6_live_deny_boundary.py "
        "tests/test_r6_unsupported_products_boundary.py"
    )
    new_tests = (
        "pytest -q tests/test_r6_authority_checker.py tests/test_r6_live_deny_boundary.py "
        "tests/test_r6_oms_handoff_boundary.py tests/test_r6_unsupported_products_boundary.py"
    )
    r6 = replace_once(r6, old_tests, new_tests, "r6 adversarial test wiring")
    R6.write_text(r6, encoding="utf-8")


def main() -> int:
    patch_oms()
    patch_writer()
    patch_writer_test()
    patch_workflows()
    print("TD-R6-010 implementation patch applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
