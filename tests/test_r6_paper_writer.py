from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from autotrade.brokers.alpaca_paper_bracket import (
    AlpacaEquityBracketBuilder,
    PaperEquityVenueRules,
)
from autotrade.brokers.alpaca_paper_canary import (
    PaperCanaryContext,
    PaperCanaryGate,
    PaperCanaryPolicy,
)
from autotrade.brokers.alpaca_paper_canary_permit import (
    PaperCanaryPermitStatus,
    SQLitePaperCanaryPermitRegistry,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.alpaca_paper_final_guard import PaperFinalWriteGuard
from autotrade.health_bridge import EffectiveHealthControl, HealthRiskMode
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionBinding,
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.brokers.alpaca_paper_writer import (
    AlpacaPaperSingleShotWriter,
    AlpacaPaperWriteResponse,
    AlpacaPaperWriterConfig,
    PaperWriterAmbiguous,
    PaperWriterBlocked,
    PaperWriterDisabled,
    PaperWriterPolicyError,
)
from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.state import (
    InMemoryOrderStore,
    InMemoryPortfolioStore,
    InMemorySafetyStateStore,
    SafetyControlState,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 11, 9, 30, tzinfo=UTC)
TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(key_id="PAPERKEY123", secret_key="PAPERSECRET456")


def attestation() -> AlpacaPaperAccountAttestation:
    creds = credentials()
    return AlpacaPaperAccountAttestation(
        account_id="e6fe16f3-64a4-4921-8928-cadf02f92f98",
        account_reference=h("writer-paper-account"),
        credential_reference=creds.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=True,
        attested_at=NOW,
        request_id="writer-account-request-001",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def order() -> OrderRecord:
    intent = OrderIntent(
        intent_id="writer-intent-001",
        strategy_id="writer-strategy",
        symbol="AAPL",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("10"),
        idempotency_key="writer-idempotency-001",
        created_at=NOW - timedelta(seconds=2),
    )
    return OrderRecord(
        order_id="writer-order-001",
        intent=intent,
        status=OrderStatus.VALIDATED,
        risk_decision_id="writer-risk-001",
        created_at=NOW - timedelta(seconds=1),
    )



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


def bracket():
    return AlpacaEquityBracketBuilder().build(
        order=order(),
        venue_rules=PaperEquityVenueRules(
            symbol="AAPL",
            asset_class="us_equity",
            price_tick=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            instrument_master_fingerprint=h("writer-instrument-master"),
        ),
        take_profit_price=Decimal("10.50"),
        stop_loss_price=Decimal("9.50"),
    )


class NeverCalledBroker:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, *, order, market, now):
        del order, market, now
        self.calls += 1
        raise AssertionError("OMS external handoff must never invoke internal broker")


class FakeWriteTransport:
    def __init__(self, *, response: AlpacaPaperWriteResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests = []

    def write(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("fake writer transport missing response")
        return self.response


def success_response(expected=None) -> AlpacaPaperWriteResponse:
    import json

    expected = expected or bracket()
    body = json.dumps(
        {
            "id": "broker-parent-001",
            "client_order_id": expected.client_order_id,
            "status": "accepted",
        },
        separators=(",", ":"),
    ).encode()
    return AlpacaPaperWriteResponse(
        status_code=200,
        body=body,
        final_url="https://paper-api.alpaca.markets/v2/orders",
        headers={
            "content-type": "application/json; charset=utf-8",
            "x-request-id": "writer-submit-request-001",
        },
    )


def error_response(status: int = 422) -> AlpacaPaperWriteResponse:
    import json

    return AlpacaPaperWriteResponse(
        status_code=status,
        body=json.dumps({"message": "paper validation rejected"}, separators=(",", ":")).encode(),
        final_url="https://paper-api.alpaca.markets/v2/orders",
        headers={
            "content-type": "application/json",
            "x-request-id": "writer-error-request-001",
        },
    )


class HealthyBridge:
    def effective_control(self, *, strategy_id, portfolio_entity_id, now):
        del strategy_id, portfolio_entity_id, now
        return EffectiveHealthControl(
            mode=HealthRiskMode.NORMAL,
            order_multiplier=Decimal("1"),
            strategy_multiplier=Decimal("1"),
            portfolio_multiplier=Decimal("1"),
            reason="R6_PAPER_CANARY",
            strategy_state_fingerprint=h("writer-strategy-health"),
            portfolio_state_fingerprint=h("writer-portfolio-health"),
        )


class FlipToKillSwitchStore:
    def __init__(self):
        self.calls = 0

    def get(self):
        self.calls += 1
        # stage_external_submission performs two authoritative Safety reads;
        # PRE_CONSUME performs the third. Flip only at PRE_IO so this fixture
        # continues to prove the post-permit, pre-network race.
        if self.calls <= 3:
            return SafetyControlState(version=0, updated_at=NOW)
        return SafetyControlState(
            kill_switch_active=True,
            kill_switch_reason="test-final-recheck",
            version=2,
            updated_at=NOW + timedelta(seconds=1),
        )


def stack(tmp_path, *, safety_store=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    current_order = order()
    current_attestation = attestation()
    expected = bracket()

    submission_registry = SQLitePaperSubmissionRegistry(
        SQLiteRuntime(tmp_path / "submission.sqlite")
    )
    binding = PaperSubmissionBinding.from_order(
        order=current_order,
        account_attestation_fingerprint=current_attestation.fingerprint,
        order_payload_hash=expected.payload_hash,
        created_at=NOW - timedelta(milliseconds=500),
    )
    submission_state = submission_registry.prepare(binding)

    approval = PaperCanaryGate(
        PaperCanaryPolicy(
            enabled=True,
            max_notional=Decimal("10"),
            max_account_fraction=Decimal("0.001"),
            max_attestation_age_seconds=30,
            approval_ttl_seconds=5,
        )
    ).approve(
        PaperCanaryContext(
            order=current_order,
            binding=binding,
            submission_state=submission_state,
            account_attestation=current_attestation,
            now=NOW,
            certified_tracks=TRACKS,
            reconciliation_clean=True,
            unresolved_unknown_orders=0,
            kill_switch_engaged=False,
            health_allows_new_exposure=True,
            prior_canary_submissions=0,
        )
    )

    permit_registry = SQLitePaperCanaryPermitRegistry(
        SQLiteRuntime(tmp_path / "permit.sqlite")
    )
    permit_registry.issue(approval)

    order_store = InMemoryOrderStore()
    order_store.create_if_absent(current_order)
    safety_store = safety_store or InMemorySafetyStateStore()
    health_bridge = HealthyBridge()
    oms = OrderManagementSystem(
        broker=NeverCalledBroker(),
        ledger=InMemoryEventLedger(),
        order_store=order_store,
        safety_state_store=safety_store,
        health_bridge=health_bridge,
        portfolio_health_entity_id="portfolio-r6-canary",
    )
    _, external_handoff = oms.stage_external_submission(
        order_id=current_order.order_id,
        handoff_id=approval.approval_hash,
        decision=risk_decision(current_order),
        market=market(),
        now=NOW + timedelta(milliseconds=100),
    )
    portfolio_store = InMemoryPortfolioStore()
    portfolio_store.initialize(
        PortfolioSnapshot(
            snapshot_id="writer-portfolio-snapshot-001",
            equity=Decimal("100000"),
            gross_exposure=Decimal("0"),
            net_exposure=Decimal("0"),
            daily_pnl=Decimal("0"),
            drawdown=Decimal("0"),
            open_orders=0,
            signed_position_notional_by_symbol={},
            strategy_gross_exposure={},
            strategy_signed_position_notional_by_symbol={},
            reconciliation_ok=True,
            broker_state_known=True,
        ),
        now=NOW,
    )
    final_guard = PaperFinalWriteGuard(
        order_store=order_store,
        safety_state_store=safety_store,
        portfolio_store=portfolio_store,
        health_bridge=health_bridge,
        portfolio_health_entity_id="portfolio-r6-canary",
    )
    return {
        "order": current_order,
        "attestation": current_attestation,
        "expected": expected,
        "submission_registry": submission_registry,
        "binding": binding,
        "approval": approval,
        "permit_registry": permit_registry,
        "order_store": order_store,
        "safety_store": safety_store,
        "portfolio_store": portfolio_store,
        "oms": oms,
        "handoff": external_handoff,
        "final_guard": final_guard,
    }


def writer(transport: FakeWriteTransport, *, enabled: bool = True, base_url: str = "https://paper-api.alpaca.markets"):
    return AlpacaPaperSingleShotWriter(
        config=AlpacaPaperWriterConfig(enabled=enabled, base_url=base_url),
        transport=transport,
    )


def submit(instance, values, *, now=NOW + timedelta(seconds=1), attempt_id="writer-attempt-001"):
    return instance.submit_once(
        credentials=credentials(),
        account_attestation=values["attestation"],
        expected_bracket=values["expected"],
        approval=values["approval"],
        permit_registry=values["permit_registry"],
        submission_registry=values["submission_registry"],
        oms=values["oms"],
        external_handoff=values["handoff"],
        final_guard=values["final_guard"],
        attempt_id=attempt_id,
        now=now,
    )


def test_writer_disabled_by_default_causes_zero_io_and_zero_state_change(tmp_path) -> None:
    values = stack(tmp_path)
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    instance = writer(transport, enabled=False)

    with pytest.raises(PaperWriterDisabled):
        submit(instance, values)

    assert transport.requests == []
    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.PREPARED
    assert values["permit_registry"].get(values["approval"].approval_hash).status is PaperCanaryPermitStatus.ISSUED


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.alpaca.markets",
        "https://example.com",
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets:8443",
        "https://user:pass@paper-api.alpaca.markets",
    ],
)
def test_nonexact_or_live_origin_rejects_before_state_mutation_or_io(tmp_path, base_url) -> None:
    values = stack(tmp_path / h(base_url)[:8])
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    instance = writer(transport, base_url=base_url)

    with pytest.raises(PaperWriterPolicyError):
        submit(instance, values)

    assert transport.requests == []
    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.PREPARED
    assert values["permit_registry"].get(values["approval"].approval_hash).status is PaperCanaryPermitStatus.ISSUED


def test_expired_approval_rejects_before_state_mutation_or_io(tmp_path) -> None:
    values = stack(tmp_path)
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    instance = writer(transport)

    with pytest.raises(PaperWriterBlocked, match="expired"):
        submit(instance, values, now=NOW + timedelta(seconds=5))

    assert transport.requests == []
    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.PREPARED
    assert values["permit_registry"].get(values["approval"].approval_hash).status is PaperCanaryPermitStatus.ISSUED


def test_initial_unknown_blocks_writer_before_permit_consumption_and_io(tmp_path) -> None:
    values = stack(tmp_path)
    values["submission_registry"].mark_submit_attempt_unknown(
        order_id=values["binding"].order_id,
        attempt_id="preexisting-attempt",
        now=NOW,
    )
    transport = FakeWriteTransport(response=success_response(values["expected"]))

    with pytest.raises(PaperWriterBlocked, match="reconciliation-only"):
        submit(writer(transport), values)

    assert transport.requests == []
    assert values["permit_registry"].get(values["approval"].approval_hash).status is PaperCanaryPermitStatus.ISSUED


def test_valid_2xx_makes_exactly_one_post_but_durable_state_stays_unknown(tmp_path) -> None:
    values = stack(tmp_path)
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    result = submit(writer(transport), values)

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url == "https://paper-api.alpaca.markets/v2/orders"
    assert request.body == values["expected"].payload_json.encode()
    assert result.provisionally_accepted is True
    assert result.broker_order_id == "broker-parent-001"
    assert result.durable_status is PaperSubmissionStatus.UNKNOWN
    assert result.reconciliation_required is True

    durable = values["submission_registry"].get(values["binding"].order_id)
    permit = values["permit_registry"].get(values["approval"].approval_hash)
    assert durable.status is PaperSubmissionStatus.UNKNOWN
    assert durable.attempt_count == 1
    assert permit.status is PaperCanaryPermitStatus.CONSUMED
    assert permit.attempt_id == "writer-attempt-001"


def test_second_writer_call_after_unknown_never_performs_second_post(tmp_path) -> None:
    values = stack(tmp_path)
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    instance = writer(transport)
    submit(instance, values)

    with pytest.raises(PaperWriterBlocked, match="reconciliation-only"):
        submit(instance, values, now=NOW + timedelta(seconds=2))

    assert len(transport.requests) == 1
    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.UNKNOWN


def test_ambiguous_transport_failure_occurs_only_after_permit_consumed_and_unknown_persisted(tmp_path) -> None:
    values = stack(tmp_path)
    transport = FakeWriteTransport(error=PaperWriterAmbiguous("simulated timeout"))

    with pytest.raises(PaperWriterAmbiguous, match="simulated timeout"):
        submit(writer(transport), values)

    assert len(transport.requests) == 1
    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.UNKNOWN
    assert values["permit_registry"].get(values["approval"].approval_hash).status is PaperCanaryPermitStatus.CONSUMED


def test_explicit_4xx_is_not_provisional_success_and_still_requires_reconciliation(tmp_path) -> None:
    values = stack(tmp_path)
    transport = FakeWriteTransport(response=error_response())
    result = submit(writer(transport), values)

    assert len(transport.requests) == 1
    assert result.http_status == 422
    assert result.provisionally_accepted is False
    assert result.broker_order_id is None
    assert result.durable_status is PaperSubmissionStatus.UNKNOWN
    assert result.reconciliation_required is True
    assert values["submission_registry"].get(values["binding"].order_id).submit_allowed is False


@pytest.mark.parametrize(
    "response",
    [
        AlpacaPaperWriteResponse(
            status_code=200,
            body=b"not-json",
            final_url="https://paper-api.alpaca.markets/v2/orders",
            headers={"content-type": "application/json", "x-request-id": "request-1"},
        ),
        AlpacaPaperWriteResponse(
            status_code=200,
            body=b'{"id":"broker-parent-001","client_order_id":"wrong-client"}',
            final_url="https://paper-api.alpaca.markets/v2/orders",
            headers={"content-type": "application/json", "x-request-id": "request-1"},
        ),
        AlpacaPaperWriteResponse(
            status_code=200,
            body=b'{"id":"broker-parent-001","client_order_id":"placeholder"}',
            final_url="https://example.com/v2/orders",
            headers={"content-type": "application/json", "x-request-id": "request-1"},
        ),
        AlpacaPaperWriteResponse(
            status_code=200,
            body=b'{"id":"broker-parent-001","client_order_id":"placeholder"}',
            final_url="https://paper-api.alpaca.markets/v2/orders",
            headers={"content-type": "application/json"},
        ),
    ],
)
def test_non_authoritative_post_response_is_ambiguous_and_never_acks(tmp_path, response) -> None:
    values = stack(tmp_path)
    if b"placeholder" in response.body:
        response = replace(
            response,
            body=response.body.replace(b"placeholder", values["expected"].client_order_id.encode()),
        )
    transport = FakeWriteTransport(response=response)

    with pytest.raises(PaperWriterAmbiguous):
        submit(writer(transport), values)

    assert len(transport.requests) == 1
    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.UNKNOWN
    assert values["permit_registry"].get(values["approval"].approval_hash).status is PaperCanaryPermitStatus.CONSUMED


def test_changed_bracket_or_approval_blocks_before_permit_consumption_or_io(tmp_path) -> None:
    values = stack(tmp_path)
    changed = AlpacaEquityBracketBuilder().build(
        order=order(),
        venue_rules=PaperEquityVenueRules(
            symbol="AAPL",
            asset_class="us_equity",
            price_tick=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            instrument_master_fingerprint=h("writer-instrument-master"),
        ),
        take_profit_price=Decimal("10.60"),
        stop_loss_price=Decimal("9.50"),
    )
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    instance = writer(transport)

    changed_values = dict(values)
    changed_values["expected"] = changed
    with pytest.raises(PaperWriterBlocked, match="payload hash"):
        submit(instance, changed_values)
    assert transport.requests == []

    bad_approval = replace(values["approval"], client_order_id="different-client")
    changed_values = dict(values)
    changed_values["approval"] = bad_approval
    with pytest.raises(PaperWriterBlocked, match="client_order_id mismatch"):
        submit(instance, changed_values)
    assert transport.requests == []
    assert values["permit_registry"].get(values["approval"].approval_hash).status is PaperCanaryPermitStatus.ISSUED


def test_preconsumed_permit_by_different_attempt_is_fail_closed_and_does_not_resume_post(tmp_path) -> None:
    values = stack(tmp_path)
    values["permit_registry"].consume(
        approval=values["approval"],
        attempt_id="writer-attempt-other",
        now=NOW + timedelta(milliseconds=500),
    )
    transport = FakeWriteTransport(response=success_response(values["expected"]))

    with pytest.raises(PaperWriterBlocked, match="another attempt"):
        submit(writer(transport), values, attempt_id="writer-attempt-001")

    assert transport.requests == []
    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.PREPARED
    permit = values["permit_registry"].get(values["approval"].approval_hash)
    assert permit.status is PaperCanaryPermitStatus.CONSUMED
    assert permit.attempt_id == "writer-attempt-other"


def test_writer_has_no_retry_cancel_replace_or_self_ack_surface(tmp_path) -> None:
    transport = FakeWriteTransport(response=success_response())
    instance = writer(transport)
    forbidden = {
        "retry",
        "retry_submit",
        "cancel",
        "cancel_order",
        "replace",
        "replace_order",
        "reconcile_acknowledged",
    }
    assert not (forbidden & set(dir(instance)))


def test_final_pre_io_recheck_blocks_changed_kill_switch_after_permit_consumption(tmp_path) -> None:
    flipping = FlipToKillSwitchStore()
    values = stack(tmp_path, safety_store=flipping)
    transport = FakeWriteTransport(response=success_response(values["expected"]))

    with pytest.raises(PaperWriterBlocked, match="PRE_IO"):
        submit(writer(transport), values)

    assert flipping.calls == 4
    assert transport.requests == []
    assert values["submission_registry"].get(values["binding"].order_id).status is PaperSubmissionStatus.UNKNOWN
    assert values["permit_registry"].get(values["approval"].approval_hash).status is PaperCanaryPermitStatus.CONSUMED


def test_success_result_binds_both_just_in_time_guard_hashes(tmp_path) -> None:
    values = stack(tmp_path)
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    result = submit(writer(transport), values)
    assert len(result.pre_consume_guard_hash) == 64
    assert len(result.pre_io_guard_hash) == 64
    assert result.pre_consume_guard_hash != result.pre_io_guard_hash
