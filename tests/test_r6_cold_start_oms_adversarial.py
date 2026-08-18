from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from autotrade.cold_start_oms import (
    COLD_START_OMS_KILL_REASON,
    COLD_START_OMS_SCOPE,
    ColdStartExternalSubmissionConflict,
    ColdStartExternalSubmissionHandoff,
    ColdStartOmsStageAuthorization,
    ColdStartOmsStageAuthority,
    ColdStartOrderManagementSystem,
)
from autotrade.domain import OrderStatus, RiskDecisionStatus, intent_fingerprint, market_fingerprint
from autotrade.ledger import InMemoryEventLedger, LedgerEvent
from test_r6_paper_crypto_canary_coordinator import NOW, _NoBroker, _decision, _intent, _market
from test_r6_paper_crypto_cold_start_final_guard import _setup


class _ValidAuthority(ColdStartOmsStageAuthority):
    def __init__(self, *, authorization_id="a" * 64, mutate=None, authorized_at=None):
        self.authorization_id = authorization_id
        self.mutate = mutate
        self.authorized_at = authorized_at
        self.calls = 0

    def authorize_oms_stage(self, *, order, decision, market, now, context):
        self.calls += 1
        authorization = self._issue_authorization(
            authorization_id=self.authorization_id,
            package_hash="b" * 64,
            operator_decision_hash="c" * 64,
            checkpoint_hash="d" * 64,
            authority_state_fingerprint="e" * 64,
            attempt_id="cold-start-attempt-001",
            order_id=order.order_id,
            client_order_id="atr6c-entry-oms-adversarial",
            intent_fingerprint_value=intent_fingerprint(order.intent),
            risk_decision_id=decision.decision_id,
            market_fingerprint_value=market_fingerprint(market),
            safety_state_version=context.safety_version,
            authorized_at=self.authorized_at or now,
        )
        if self.mutate is not None:
            return self.mutate(authorization)
        return authorization


class _WrongReturnAuthority(ColdStartOmsStageAuthority):
    def authorize_oms_stage(self, **_kwargs):
        return "not-an-authorization"


class _Context:
    def __init__(self, safety_version):
        self.safety_version = safety_version


def _risk_and_market(ctx):
    attestation = _market()
    intent = _intent(quantity=ctx.package.quantity, limit_price=ctx.package.limit_price)
    decision = _decision(intent, attestation, approved_notional=ctx.package.notional)
    return decision, attestation.market


def _oms(ctx, ledger=None, *, safety=True):
    return ColdStartOrderManagementSystem(
        broker=_NoBroker(),
        ledger=ledger or InMemoryEventLedger(),
        order_store=ctx.order_store,
        safety_state_store=ctx.safety if safety else None,
    )


def _stage(ctx, *, authority=None, ledger=None, decision=None, market=None, now=None, safety=True):
    oms = _oms(ctx, ledger, safety=safety)
    risk, snapshot = _risk_and_market(ctx)
    decision = decision or risk
    market = market or snapshot
    now = now or (NOW + timedelta(seconds=4, milliseconds=200))
    authority = authority or _ValidAuthority()
    result = oms.stage_cold_start_external_submission(
        order_id=ctx.package.order_id,
        decision=decision,
        market=market,
        now=now,
        authority=authority,
        authority_context=_Context(ctx.safety.get().version),
    )
    return oms, result, risk, snapshot, authority


def test_cold_start_oms_nominal_authority_base_is_not_usable() -> None:
    with pytest.raises(NotImplementedError):
        ColdStartOmsStageAuthority().authorize_oms_stage(
            order=None, decision=None, market=None, now=NOW, context=None  # type: ignore[arg-type]
        )


def test_cold_start_oms_rejects_non_nominal_authority_missing_order_and_terminal_order(tmp_path) -> None:
    ctx = _setup(tmp_path / "non-authority")
    oms = _oms(ctx)
    risk, market = _risk_and_market(ctx)
    with pytest.raises(ColdStartExternalSubmissionConflict, match="nominal"):
        oms.stage_cold_start_external_submission(
            order_id=ctx.package.order_id,
            decision=risk,
            market=market,
            now=NOW + timedelta(seconds=4),
            authority=object(),  # type: ignore[arg-type]
            authority_context=None,
        )
    with pytest.raises(KeyError):
        oms.stage_cold_start_external_submission(
            order_id="missing-order",
            decision=risk,
            market=market,
            now=NOW + timedelta(seconds=4),
            authority=_ValidAuthority(),
            authority_context=_Context(ctx.safety.get().version),
        )

    current = ctx.order_store.get_by_order_id(ctx.package.order_id)
    assert current is not None
    ctx.order_store.update(replace(current, status=OrderStatus.CANCELLED))
    with pytest.raises(ColdStartExternalSubmissionConflict, match="cannot resume"):
        oms.stage_cold_start_external_submission(
            order_id=ctx.package.order_id,
            decision=risk,
            market=market,
            now=NOW + timedelta(seconds=4),
            authority=_ValidAuthority(),
            authority_context=_Context(ctx.safety.get().version),
        )


def test_cold_start_oms_requires_authoritative_commissioning_safety_and_no_circuit(tmp_path) -> None:
    no_store = _setup(tmp_path / "no-store")
    with pytest.raises(ColdStartExternalSubmissionConflict, match="Safety store"):
        _stage(no_store, safety=False)

    wrong_kill = _setup(tmp_path / "wrong-kill")
    wrong_kill.safety.reset(now=NOW + timedelta(seconds=3))
    wrong_kill.safety.activate(reason="MANUAL_KILL", now=NOW + timedelta(seconds=3, milliseconds=100))
    with pytest.raises(ColdStartExternalSubmissionConflict, match="commissioning kill"):
        _stage(wrong_kill)

    no_kill = _setup(tmp_path / "no-kill")
    no_kill.safety.reset(now=NOW + timedelta(seconds=3))
    with pytest.raises(ColdStartExternalSubmissionConflict, match="commissioning kill"):
        _stage(no_kill)

    circuit = _setup(tmp_path / "circuit")
    circuit.safety.activate_circuit(reason="RISK_BREACH", now=NOW + timedelta(seconds=3))
    with pytest.raises(ColdStartExternalSubmissionConflict, match="safety circuit"):
        _stage(circuit)


def test_cold_start_oms_rejects_every_risk_decision_identity_drift(tmp_path) -> None:
    cases = []
    base = _setup(tmp_path / "base")
    risk, market = _risk_and_market(base)
    cases.append(("not APPROVED", replace(risk, status=RiskDecisionStatus.DENIED), market))
    cases.append(("intent mismatch", replace(risk, intent_id="different-intent"), market))
    cases.append(("fingerprint mismatch", replace(risk, intent_fingerprint="0" * 64), market))
    cases.append(("market changed", risk, replace(market, last=market.last + 1)))
    cases.append(("expired", replace(risk, valid_until=NOW + timedelta(seconds=1)), market))
    cases.append(("RiskDecision id mismatch", replace(risk, decision_id="different-decision"), market))

    for index, (message, changed_decision, changed_market) in enumerate(cases):
        ctx = _setup(tmp_path / f"case-{index}")
        # Rebuild modifications from this context's exact decision where needed.
        fresh_risk, fresh_market = _risk_and_market(ctx)
        if message == "not APPROVED":
            changed_decision = replace(fresh_risk, status=RiskDecisionStatus.DENIED)
            changed_market = fresh_market
        elif message == "intent mismatch":
            changed_decision = replace(fresh_risk, intent_id="different-intent")
            changed_market = fresh_market
        elif message == "fingerprint mismatch":
            changed_decision = replace(fresh_risk, intent_fingerprint="0" * 64)
            changed_market = fresh_market
        elif message == "market changed":
            changed_decision = fresh_risk
            changed_market = replace(fresh_market, last=fresh_market.last + 1)
        elif message == "expired":
            changed_decision = replace(fresh_risk, valid_until=NOW + timedelta(seconds=1))
            changed_market = fresh_market
        else:
            changed_decision = replace(fresh_risk, decision_id="different-decision")
            changed_market = fresh_market
        with pytest.raises(ColdStartExternalSubmissionConflict, match=message):
            _stage(
                ctx,
                decision=changed_decision,
                market=changed_market,
                now=NOW + timedelta(seconds=4),
            )


def test_cold_start_oms_rejects_wrong_authorization_type_binding_and_time(tmp_path) -> None:
    wrong_type = _setup(tmp_path / "wrong-type")
    with pytest.raises(ColdStartExternalSubmissionConflict, match="invalid.*authorization type"):
        _stage(wrong_type, authority=_WrongReturnAuthority())

    binding = _setup(tmp_path / "binding")
    bad_binding = _ValidAuthority(mutate=lambda value: replace(value, order_id="different-order"))
    with pytest.raises(ColdStartExternalSubmissionConflict, match="authorization binding mismatch"):
        _stage(binding, authority=bad_binding)

    future = _setup(tmp_path / "future")
    future_time = NOW + timedelta(seconds=5)
    with pytest.raises(ColdStartExternalSubmissionConflict, match="future-dated"):
        _stage(
            future,
            authority=_ValidAuthority(authorized_at=future_time),
            now=NOW + timedelta(seconds=4),
        )

    outlives = _setup(tmp_path / "outlives")
    risk, market = _risk_and_market(outlives)
    authorized = risk.valid_until + timedelta(milliseconds=1)
    with pytest.raises(ColdStartExternalSubmissionConflict, match="outlives RiskDecision"):
        _stage(
            outlives,
            authority=_ValidAuthority(authorized_at=authorized),
            decision=risk,
            market=market,
            now=authorized,
        )


def test_cold_start_authorization_and_handoff_dataclasses_detect_tamper(tmp_path) -> None:
    ctx = _setup(tmp_path)
    oms, (order, handoff), _, _, authority = _stage(ctx)
    assert order.status is OrderStatus.SUBMITTING
    assert authority.calls == 1

    with pytest.raises(ValueError, match="hash mismatch"):
        replace(
            authority._issue_authorization(
                authorization_id="1" * 64,
                package_hash="2" * 64,
                operator_decision_hash="3" * 64,
                checkpoint_hash="4" * 64,
                authority_state_fingerprint="5" * 64,
                attempt_id="attempt",
                order_id=ctx.package.order_id,
                client_order_id="client",
                intent_fingerprint_value=intent_fingerprint(order.intent),
                risk_decision_id=order.risk_decision_id or "risk",
                market_fingerprint_value="6" * 64,
                safety_state_version=ctx.safety.get().version,
                authorized_at=NOW,
            ),
            package_hash="7" * 64,
        )
    with pytest.raises(ValueError, match="handoff hash mismatch"):
        replace(handoff, package_hash="8" * 64)
    with pytest.raises(ValueError, match="event_id mismatch"):
        replace(handoff, event_id="wrong-event")
    assert oms.resolve_cold_start_external_submission_handoff(
        order_id=ctx.package.order_id,
        authorization_id=handoff.authorization_id,
    ) == handoff


def test_cold_start_oms_resolver_rejects_missing_status_timestamp_intent_risk_and_safety_drift(tmp_path) -> None:
    missing = _setup(tmp_path / "missing")
    oms = _oms(missing)
    with pytest.raises(ColdStartExternalSubmissionConflict, match="missing or duplicated"):
        oms.resolve_cold_start_external_submission_handoff(
            order_id=missing.package.order_id,
            authorization_id="a" * 64,
        )

    status_ctx = _setup(tmp_path / "status")
    status_oms, (_, status_handoff), _, _, _ = _stage(status_ctx)
    order = status_ctx.order_store.get_by_order_id(status_ctx.package.order_id)
    assert order is not None
    status_ctx.order_store.update(replace(order, status=OrderStatus.CANCELLED))
    with pytest.raises(ColdStartExternalSubmissionConflict, match="requires SUBMITTING"):
        status_oms.resolve_cold_start_external_submission_handoff(
            order_id=status_ctx.package.order_id,
            authorization_id=status_handoff.authorization_id,
        )

    time_ctx = _setup(tmp_path / "time")
    time_oms, (_, time_handoff), _, _, _ = _stage(time_ctx)
    order = time_ctx.order_store.get_by_order_id(time_ctx.package.order_id)
    assert order is not None
    time_ctx.order_store.update(replace(order, submitted_at=order.submitted_at + timedelta(milliseconds=1)))
    with pytest.raises(ColdStartExternalSubmissionConflict, match="timestamp mismatch"):
        time_oms.resolve_cold_start_external_submission_handoff(
            order_id=time_ctx.package.order_id,
            authorization_id=time_handoff.authorization_id,
        )

    intent_ctx = _setup(tmp_path / "intent")
    intent_oms, (_, intent_handoff), _, _, _ = _stage(intent_ctx)
    order = intent_ctx.order_store.get_by_order_id(intent_ctx.package.order_id)
    assert order is not None
    intent_ctx.order_store.update(replace(order, intent=replace(order.intent, intent_id="changed-intent")))
    with pytest.raises(ColdStartExternalSubmissionConflict, match="intent changed"):
        intent_oms.resolve_cold_start_external_submission_handoff(
            order_id=intent_ctx.package.order_id,
            authorization_id=intent_handoff.authorization_id,
        )

    risk_ctx = _setup(tmp_path / "risk")
    risk_oms, (_, risk_handoff), _, _, _ = _stage(risk_ctx)
    order = risk_ctx.order_store.get_by_order_id(risk_ctx.package.order_id)
    assert order is not None
    risk_ctx.order_store.update(replace(order, risk_decision_id="changed-risk"))
    with pytest.raises(ColdStartExternalSubmissionConflict, match="RiskDecision changed"):
        risk_oms.resolve_cold_start_external_submission_handoff(
            order_id=risk_ctx.package.order_id,
            authorization_id=risk_handoff.authorization_id,
        )

    safety_ctx = _setup(tmp_path / "safety")
    safety_oms, (_, safety_handoff), _, _, _ = _stage(safety_ctx)
    safety_ctx.safety.activate_circuit(reason="LATE_BREAKER", now=NOW + timedelta(seconds=5))
    with pytest.raises(ColdStartExternalSubmissionConflict, match="Safety version changed|safety circuit"):
        safety_oms.resolve_cold_start_external_submission_handoff(
            order_id=safety_ctx.package.order_id,
            authorization_id=safety_handoff.authorization_id,
        )


def test_cold_start_oms_rejects_duplicate_tampered_and_wrong_type_handoff_events(tmp_path) -> None:
    source = _setup(tmp_path / "source")
    source_ledger = InMemoryEventLedger()
    source_oms, (_, handoff), _, _, _ = _stage(source, ledger=source_ledger)
    event = tuple(source_ledger.all_events())[0]
    assert source_oms.resolve_cold_start_external_submission_handoff(
        order_id=source.package.order_id,
        authorization_id=handoff.authorization_id,
    ) == handoff

    class DuplicateLedger:
        def all_events(self):
            return (event, event)

    duplicate_oms = ColdStartOrderManagementSystem(
        broker=_NoBroker(),
        ledger=DuplicateLedger(),
        order_store=source.order_store,
        safety_state_store=source.safety,
    )
    with pytest.raises(ColdStartExternalSubmissionConflict, match="missing or duplicated"):
        duplicate_oms.resolve_cold_start_external_submission_handoff(
            order_id=source.package.order_id,
            authorization_id=handoff.authorization_id,
        )

    wrong_type_ledger = InMemoryEventLedger()
    wrong_type_ledger.append(
        LedgerEvent(
            event_id=event.event_id,
            event_type="WRONG_TYPE",
            occurred_at=event.occurred_at,
            payload=event.payload,
        )
    )
    wrong_type_oms = ColdStartOrderManagementSystem(
        broker=_NoBroker(), ledger=wrong_type_ledger, order_store=source.order_store, safety_state_store=source.safety
    )
    with pytest.raises(ColdStartExternalSubmissionConflict, match="event type mismatch"):
        wrong_type_oms.resolve_cold_start_external_submission_handoff(
            order_id=source.package.order_id,
            authorization_id=handoff.authorization_id,
        )

    tampered_ledger = InMemoryEventLedger()
    payload = dict(event.payload)
    payload["handoff_hash"] = "0" * 64
    tampered_ledger.append(
        LedgerEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            payload=payload,
        )
    )
    tampered_oms = ColdStartOrderManagementSystem(
        broker=_NoBroker(), ledger=tampered_ledger, order_store=source.order_store, safety_state_store=source.safety
    )
    with pytest.raises(ColdStartExternalSubmissionConflict, match="invalid or tampered"):
        tampered_oms.resolve_cold_start_external_submission_handoff(
            order_id=source.package.order_id,
            authorization_id=handoff.authorization_id,
        )


def test_cold_start_authorization_rejects_scope_reason_version_and_hash_shape() -> None:
    base = ColdStartOmsStageAuthority._issue_authorization(
        authorization_id="1" * 64,
        package_hash="2" * 64,
        operator_decision_hash="3" * 64,
        checkpoint_hash="4" * 64,
        authority_state_fingerprint="5" * 64,
        attempt_id="attempt",
        order_id="order",
        client_order_id="client",
        intent_fingerprint_value="6" * 64,
        risk_decision_id="risk",
        market_fingerprint_value="7" * 64,
        safety_state_version=1,
        authorized_at=NOW,
    )
    with pytest.raises(ValueError, match="scope"):
        replace(base, scope="WRONG_SCOPE")
    with pytest.raises(ValueError, match="kill reason"):
        replace(base, kill_switch_reason="WRONG_KILL")
    with pytest.raises(ValueError, match="Safety version"):
        replace(base, safety_state_version=-1)
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        replace(base, authorization_id="not-a-hash")
    assert base.scope == COLD_START_OMS_SCOPE
    assert base.kill_switch_reason == COLD_START_OMS_KILL_REASON
