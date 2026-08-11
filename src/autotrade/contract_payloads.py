from __future__ import annotations

from typing import Any

from .brokers.base import BrokerExecution
from .domain import Fill, OrderIntent, OrderRecord, RiskDecision
from .ledger import LedgerEvent
from .risk_state import RiskTelemetryState
from .state import RiskReservation, SafetyControlState


def contract_payload(value: Any) -> tuple[str, dict[str, object]]:
    """Return the canonical R2 contract id and JSON-safe payload for a domain object.

    This is deliberately explicit rather than reflective: adding a new field to a
    capital-sensitive object requires a conscious contract/serializer change.
    """
    if isinstance(value, OrderIntent):
        return "OrderIntent@1", _order_intent(value)
    if isinstance(value, RiskDecision):
        return "RiskDecision@1", _risk_decision(value)
    if isinstance(value, Fill):
        return "Fill@1", _fill(value)
    if isinstance(value, BrokerExecution):
        return "BrokerExecution@1", {
            "status": value.status.value,
            "fills": [_fill(fill) for fill in value.fills],
        }
    if isinstance(value, OrderRecord):
        return "OrderRecord@1", {
            "order_id": value.order_id,
            "intent": _order_intent(value.intent),
            "risk_decision_id": value.risk_decision_id,
            "status": value.status.value,
            "created_at": value.created_at.isoformat(),
            "submitted_at": value.submitted_at.isoformat() if value.submitted_at else None,
            "filled_quantity": str(value.filled_quantity),
            "average_fill_price": (
                str(value.average_fill_price) if value.average_fill_price is not None else None
            ),
        }
    if isinstance(value, RiskReservation):
        return "RiskReservation@1", {
            "reservation_id": value.reservation_id,
            "idempotency_key": value.idempotency_key,
            "intent_fingerprint": value.intent_fingerprint,
            "strategy_id": value.strategy_id,
            "symbol": value.symbol,
            "signed_notional": value.signed_notional,
            "status": value.status.value,
            "portfolio_version": value.portfolio_version,
            "created_at": value.created_at.isoformat(),
            "updated_at": value.updated_at.isoformat(),
        }
    if isinstance(value, SafetyControlState):
        return "SafetyControlState@1", {
            "kill_switch_active": value.kill_switch_active,
            "kill_switch_reason": value.kill_switch_reason,
            "circuit_active": value.circuit_active,
            "circuit_reason": value.circuit_reason,
            "version": value.version,
            "updated_at": value.updated_at.isoformat() if value.updated_at else None,
        }
    if isinstance(value, RiskTelemetryState):
        return "RiskTelemetryState@1", {
            "session_date": value.session_date,
            "day_start_equity": str(value.day_start_equity),
            "peak_equity": str(value.peak_equity),
            "current_equity": str(value.current_equity),
            "daily_pnl": str(value.daily_pnl),
            "drawdown": str(value.drawdown),
            "version": value.version,
            "updated_at": value.updated_at.isoformat(),
        }
    if isinstance(value, LedgerEvent):
        return "LedgerEvent@1", {
            "event_id": value.event_id,
            "event_type": value.event_type,
            "occurred_at": value.occurred_at.isoformat(),
            "payload": dict(value.payload),
        }
    raise TypeError(f"no machine-readable contract binding for {type(value).__name__}")


def _order_intent(value: OrderIntent) -> dict[str, object]:
    return {
        "intent_id": value.intent_id,
        "idempotency_key": value.idempotency_key,
        "strategy_id": value.strategy_id,
        "symbol": value.symbol,
        "side": value.side.value,
        "quantity": str(value.quantity),
        "order_type": value.order_type.value,
        "created_at": value.created_at.isoformat(),
        "limit_price": str(value.limit_price) if value.limit_price is not None else None,
    }


def _risk_decision(value: RiskDecision) -> dict[str, object]:
    return {
        "decision_id": value.decision_id,
        "intent_id": value.intent_id,
        "status": value.status.value,
        "reason_code": value.reason_code,
        "reason_detail": value.reason_detail,
        "evaluated_at": value.evaluated_at.isoformat(),
        "valid_until": value.valid_until.isoformat(),
        "limits_version": value.limits_version,
        "intent_fingerprint": value.intent_fingerprint,
        "market_fingerprint": value.market_fingerprint,
        "approved_notional": (
            str(value.approved_notional) if value.approved_notional is not None else None
        ),
        "risk_reducing": value.risk_reducing,
        "safety_state_version": value.safety_state_version,
    }


def _fill(value: Fill) -> dict[str, object]:
    return {
        "fill_id": value.fill_id,
        "order_id": value.order_id,
        "symbol": value.symbol,
        "side": value.side.value,
        "quantity": str(value.quantity),
        "price": str(value.price),
        "occurred_at": value.occurred_at.isoformat(),
    }
