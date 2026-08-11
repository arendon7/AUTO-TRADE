from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.brokers.base import BrokerExecution
from autotrade.contract_payloads import contract_payload
from autotrade.contract_registry import ContractRegistry
from autotrade.domain import Fill, OrderRecord, OrderStatus
from autotrade.ledger import LedgerEvent
from autotrade.risk_state import RiskTelemetryState
from autotrade.state import ReservationStatus, RiskReservation, SafetyControlState


def assert_valid(registry, value):
    contract_id, payload = contract_payload(value)
    registry.validate(contract_id, payload)
    return contract_id, payload


def test_real_domain_objects_validate_against_current_contracts(
    limits, market, market_buy_intent, empty_portfolio
):
    from autotrade.ledger import InMemoryEventLedger
    from autotrade.safety import CapitalSafetyKernel

    registry = ContractRegistry.load_default()
    ledger = InMemoryEventLedger()
    decision = CapitalSafetyKernel(limits, ledger).evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    order = OrderRecord(
        order_id="o-1",
        intent=market_buy_intent,
        risk_decision_id=decision.decision_id,
        status=OrderStatus.PARTIALLY_FILLED,
        created_at=market.observed_at,
        submitted_at=market.observed_at,
        filled_quantity=Decimal("4"),
        average_fill_price=Decimal("101"),
    )
    fill = Fill(
        fill_id="f-1",
        order_id=order.order_id,
        symbol=order.intent.symbol,
        side=order.intent.side,
        quantity=Decimal("4"),
        price=Decimal("101"),
        occurred_at=market.observed_at,
    )
    reservation = RiskReservation(
        reservation_id="r-1",
        idempotency_key=order.intent.idempotency_key,
        intent_fingerprint="fp",
        strategy_id=order.intent.strategy_id,
        symbol=order.intent.symbol,
        signed_notional="1010",
        status=ReservationStatus.OPEN,
        portfolio_version=1,
        created_at=market.observed_at,
        updated_at=market.observed_at,
    )
    safety = SafetyControlState(
        kill_switch_active=False,
        circuit_active=True,
        circuit_reason="test",
        version=3,
        updated_at=market.observed_at,
    )
    telemetry = RiskTelemetryState(
        session_date=market.observed_at.date().isoformat(),
        day_start_equity=Decimal("100000"),
        peak_equity=Decimal("101000"),
        current_equity=Decimal("100500"),
        daily_pnl=Decimal("500"),
        drawdown=Decimal("0.004950495049504950495049504950"),
        version=2,
        updated_at=market.observed_at,
    )
    event = LedgerEvent(
        event_id="e-1",
        event_type="TEST",
        occurred_at=market.observed_at,
        payload={"x": "1"},
    )

    assert assert_valid(registry, market_buy_intent)[0] == "OrderIntent@1"
    assert assert_valid(registry, decision)[0] == "RiskDecision@1"
    assert assert_valid(registry, fill)[0] == "Fill@1"
    assert assert_valid(registry, BrokerExecution(OrderStatus.PARTIALLY_FILLED, (fill,)))[0] == "BrokerExecution@1"
    assert assert_valid(registry, order)[0] == "OrderRecord@1"
    assert assert_valid(registry, reservation)[0] == "RiskReservation@1"
    assert assert_valid(registry, safety)[0] == "SafetyControlState@1"
    assert assert_valid(registry, telemetry)[0] == "RiskTelemetryState@1"
    assert assert_valid(registry, event)[0] == "LedgerEvent@1"


def test_nullable_domain_fields_remain_schema_valid(market_buy_intent, now):
    registry = ContractRegistry.load_default()
    order = OrderRecord(
        order_id="o-null",
        intent=market_buy_intent,
        risk_decision_id="d",
        status=OrderStatus.VALIDATED,
        created_at=now,
    )
    safety = SafetyControlState()
    assert_valid(registry, order)
    assert_valid(registry, safety)


def test_contract_binding_is_explicit_not_reflective():
    with pytest.raises(TypeError, match="no machine-readable contract binding"):
        contract_payload(object())


def test_contract_payload_drift_is_detected_by_registry(
    limits, market, market_buy_intent, empty_portfolio
):
    from autotrade.ledger import InMemoryEventLedger
    from autotrade.safety import CapitalSafetyKernel

    registry = ContractRegistry.load_default()
    decision = CapitalSafetyKernel(limits, InMemoryEventLedger()).evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    contract_id, payload = contract_payload(decision)
    payload["unexpected_new_runtime_field"] = "drift"
    from autotrade.contract_registry import ContractValidationError

    with pytest.raises(ContractValidationError, match="unknown fields"):
        registry.validate(contract_id, payload)
