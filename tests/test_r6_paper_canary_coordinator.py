from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from autotrade.brokers.alpaca_paper_bracket import PaperEquityVenueRules
from autotrade.brokers.alpaca_paper_canary import PaperCanaryGate, PaperCanaryPolicy, PaperCanaryRejected
from autotrade.brokers.alpaca_paper_canary_coordinator import (
    PaperCanaryCoordinator,
    PaperCanaryPreparationBlocked,
)
from autotrade.brokers.alpaca_paper_canary_permit import (
    PaperCanaryPermitStatus,
    SQLitePaperCanaryPermitRegistry,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderStatus,
    OrderType,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
)
from autotrade.health_bridge import EffectiveHealthControl, HealthRiskMode
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.persistence import SQLiteRuntime
from autotrade.state import InMemoryOrderStore, InMemorySafetyStateStore


UTC = timezone.utc
NOW = datetime(2026, 8, 11, 17, 45, tzinfo=UTC)
TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")


def h(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class NeverCalledBroker:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, *, order, market, now):
        del order, market, now
        self.calls += 1
        raise AssertionError("offline canary coordinator must never call broker.submit")


class HealthyBridge:
    def effective_control(self, *, strategy_id, portfolio_entity_id, now):
        del strategy_id, portfolio_entity_id, now
        return EffectiveHealthControl(
            mode=HealthRiskMode.NORMAL,
            order_multiplier=Decimal("1"),
            strategy_multiplier=Decimal("1"),
            portfolio_multiplier=Decimal("1"),
            reason="R6_COORDINATOR_HEALTHY",
            strategy_state_fingerprint=h("strategy-health"),
            portfolio_state_fingerprint=h("portfolio-health"),
        )


class FlipSafetyAfterIssue:
    def __init__(self, inner, safety):
        self.inner = inner
        self.safety = safety

    def issue(self, approval):
        state = self.inner.issue(approval)
        self.safety.activate(reason="race-after-permit", now=NOW + timedelta(milliseconds=1))
        return state


def intent() -> OrderIntent:
    return OrderIntent(
        intent_id="coordinator-intent-001",
        idempotency_key="coordinator-idem-001",
        strategy_id="coordinator-strategy",
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.LIMIT,
        created_at=NOW - timedelta(seconds=2),
        limit_price=Decimal("10"),
    )


def market() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="AAPL",
        bid=Decimal("9.99"),
        ask=Decimal("10.01"),
        last=Decimal("10"),
        observed_at=NOW - timedelta(milliseconds=100),
    )


def decision(current_intent: OrderIntent | None = None) -> RiskDecision:
    current_intent = current_intent or intent()
    current_market = market()
    return RiskDecision(
        decision_id="coordinator-risk-001",
        intent_id=current_intent.intent_id,
        status=RiskDecisionStatus.APPROVED,
        reason_code="APPROVED",
        reason_detail="bounded offline R6 canary preparation",
        evaluated_at=NOW - timedelta(milliseconds=50),
        valid_until=NOW + timedelta(seconds=12),
        limits_version="r6-coordinator-test",
        intent_fingerprint=intent_fingerprint(current_intent),
        market_fingerprint=market_fingerprint(current_market),
        approved_notional=Decimal("10"),
        risk_reducing=False,
        safety_state_version=0,
    )


def attestation(*, at: datetime = NOW) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="e6fe16f3-64a4-4921-8928-cadf02f92f98",
        account_reference=h("coordinator-paper-account"),
        credential_reference=h("coordinator-paper-key"),
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=True,
        attested_at=at,
        request_id="coordinator-account-request-001",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def venue_rules() -> PaperEquityVenueRules:
    return PaperEquityVenueRules(
        symbol="AAPL",
        asset_class="us_equity",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("1"),
        minimum_quantity=Decimal("1"),
        instrument_master_fingerprint=h("coordinator-instrument-master"),
    )


def stack(tmp_path, *, gate=None):
    broker = NeverCalledBroker()
    safety = InMemorySafetyStateStore()
    oms = OrderManagementSystem(
        broker=broker,
        ledger=InMemoryEventLedger(),
        order_store=InMemoryOrderStore(),
        safety_state_store=safety,
        health_bridge=HealthyBridge(),
        portfolio_health_entity_id="portfolio-r6-canary",
    )
    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(tmp_path / "submission.sqlite"))
    permit = SQLitePaperCanaryPermitRegistry(SQLiteRuntime(tmp_path / "permit.sqlite"))
    gate = gate or PaperCanaryGate(
        PaperCanaryPolicy(
            enabled=True,
            max_notional=Decimal("10"),
            max_account_fraction=Decimal("0.001"),
            max_attestation_age_seconds=30,
            approval_ttl_seconds=5,
        )
    )
    coordinator = PaperCanaryCoordinator(oms=oms, canary_gate=gate)
    return coordinator, broker, safety, submission, permit


def prepare(coordinator, submission, permit, **overrides):
    values = {
        "intent": intent(),
        "decision": decision(),
        "market": market(),
        "account_attestation": attestation(),
        "venue_rules": venue_rules(),
        "take_profit_price": Decimal("11"),
        "stop_loss_price": Decimal("9"),
        "submission_registry": submission,
        "permit_registry": permit,
        "now": NOW,
        "certified_tracks": TRACKS,
        "reconciliation_clean": True,
        "unresolved_unknown_orders": 0,
        "kill_switch_engaged": False,
        "health_allows_new_exposure": True,
        "prior_canary_submissions": 0,
    }
    values.update(overrides)
    return coordinator.prepare(**values)


def test_prepare_integrates_certified_components_but_stops_at_validated(tmp_path) -> None:
    coordinator, broker, _, submission, permit = stack(tmp_path)
    result = prepare(coordinator, submission, permit)

    assert result.order.status is OrderStatus.VALIDATED
    assert result.submission_state.status is PaperSubmissionStatus.PREPARED
    assert result.submission_state.attempt_count == 0
    assert result.permit.status is PaperCanaryPermitStatus.ISSUED
    assert result.bracket.payload_hash == result.binding.order_payload_hash
    assert result.approval.binding_hash == result.binding.fingerprint
    assert result.package.order_status == "VALIDATED"
    assert result.package.network_write_authorized is False
    assert result.package.next_action == "OPERATOR_DECISION_REQUIRED"
    assert result.package.attempt_id.startswith("r6-paper-attempt-")
    assert result.package.execution_deadline == result.approval.expires_at
    assert result.package.canonical_payload()["package_hash"] == result.package.package_hash
    assert broker.calls == 0

    forbidden = {"submit", "submit_once", "post", "write", "execute", "stage_external_submission"}
    assert not (forbidden & set(dir(coordinator)))


def test_identical_offline_replay_is_idempotent_and_package_stable(tmp_path) -> None:
    coordinator, broker, _, submission, permit = stack(tmp_path)
    first = prepare(coordinator, submission, permit)
    second = prepare(coordinator, submission, permit)

    assert second.package == first.package
    assert second.binding == first.binding
    assert second.submission_state == first.submission_state
    assert second.permit == first.permit
    assert broker.calls == 0


def test_default_gate_keeps_coordinator_disabled_without_network(tmp_path) -> None:
    coordinator, broker, _, submission, permit = stack(tmp_path, gate=PaperCanaryGate())
    with pytest.raises(PaperCanaryRejected, match="disabled by default"):
        prepare(coordinator, submission, permit)
    assert broker.calls == 0


def test_consumed_permit_cannot_be_repackaged_as_fresh_authority(tmp_path) -> None:
    coordinator, broker, _, submission, permit = stack(tmp_path)
    result = prepare(coordinator, submission, permit)
    permit.consume(
        approval=result.approval,
        attempt_id=result.package.attempt_id,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(PaperCanaryPreparationBlocked, match="already-consumed"):
        prepare(coordinator, submission, permit)
    assert broker.calls == 0


def test_unknown_submission_is_reconciliation_only_and_blocks_prepare(tmp_path) -> None:
    coordinator, broker, _, submission, permit = stack(tmp_path)
    result = prepare(coordinator, submission, permit)
    submission.mark_submit_attempt_unknown(
        order_id=result.order.order_id,
        attempt_id=result.package.attempt_id,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PaperCanaryPreparationBlocked, match="not PREPARED"):
        prepare(coordinator, submission, permit)
    assert broker.calls == 0


def test_final_offline_replay_blocks_safety_race_after_permit_issue(tmp_path) -> None:
    coordinator, broker, safety, submission, permit = stack(tmp_path)
    flipping = FlipSafetyAfterIssue(permit, safety)
    with pytest.raises(Exception, match="safety state changed|kill switch"):
        prepare(coordinator, submission, flipping)
    assert broker.calls == 0
    assert permit.list_states()[0].status is PaperCanaryPermitStatus.ISSUED


def test_package_is_self_validating_and_cannot_claim_execution_authority(tmp_path) -> None:
    coordinator, _, _, submission, permit = stack(tmp_path)
    package = prepare(coordinator, submission, permit).package
    with pytest.raises(ValueError, match="cannot authorize network write"):
        replace(package, network_write_authorized=True)
    with pytest.raises(ValueError, match="operator decision"):
        replace(package, next_action="EXECUTE")
    with pytest.raises(ValueError, match="hash mismatch"):
        replace(package, package_hash="0" * 64)


def test_global_predicates_and_exact_tracks_fail_closed(tmp_path) -> None:
    coordinator, broker, _, submission, permit = stack(tmp_path)
    with pytest.raises(PaperCanaryPreparationBlocked, match="exactly R0-R5"):
        prepare(coordinator, submission, permit, certified_tracks=TRACKS + ("R6",))
    with pytest.raises(PaperCanaryRejected, match="reconciliation"):
        prepare(
            coordinator,
            submission,
            permit,
            reconciliation_clean=False,
        )
    assert broker.calls == 0


def test_stale_account_and_bad_bracket_are_rejected_offline(tmp_path) -> None:
    coordinator, broker, _, submission, permit = stack(tmp_path)
    with pytest.raises(PaperCanaryRejected, match="stale"):
        prepare(
            coordinator,
            submission,
            permit,
            account_attestation=attestation(at=NOW - timedelta(seconds=31)),
        )

    coordinator2, broker2, _, submission2, permit2 = stack(tmp_path / "bad-bracket")
    with pytest.raises(Exception, match="geometry"):
        prepare(
            coordinator2,
            submission2,
            permit2,
            take_profit_price=Decimal("9"),
            stop_loss_price=Decimal("8"),
        )
    assert broker.calls == 0
    assert broker2.calls == 0
