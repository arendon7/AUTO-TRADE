from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

from autotrade.connectivity_canary_authority import CONNECTIVITY_CANARY_STRATEGY_ID
from autotrade.domain import (
    MarketSnapshot,
    OrderStatus,
    RiskDecision,
    RiskDecisionStatus,
    intent_fingerprint,
    market_fingerprint,
    risk_decision_fingerprint,
)
from autotrade.ledger import DuplicateLedgerEvent, EventLedger, LedgerEvent
from autotrade.state import OrderStore, SafetyStateStore

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_EVENT_TYPE = "CONNECTIVITY_EXTERNAL_HANDOFF_AUTHORIZED"


class ConnectivityOmsStageError(RuntimeError):
    pass


class ConnectivityOmsStageRejected(ConnectivityOmsStageError):
    pass


class ConnectivityOmsStageConflict(ConnectivityOmsStageError):
    pass


@dataclass(frozen=True, slots=True)
class ConnectivitySubmissionHandoff:
    handoff_id: str
    purpose: str
    order_id: str
    attempt_id: str
    original_risk_decision_id: str
    fresh_risk_decision_id: str
    fresh_risk_decision_fingerprint: str
    safety_state_version: int
    market_fingerprint: str
    execution_freshness_binding_hash: str
    final_freshness_permit_hash: str
    authorized_at: datetime
    valid_until: datetime
    event_id: str
    handoff_hash: str

    def __post_init__(self) -> None:
        if self.purpose != "CONNECTIVITY_CANARY":
            raise ValueError("connectivity handoff purpose must be exact")
        for label, value in (
            ("order_id", self.order_id),
            ("attempt_id", self.attempt_id),
            ("original_risk_decision_id", self.original_risk_decision_id),
            ("fresh_risk_decision_id", self.fresh_risk_decision_id),
        ):
            _validate_id(value, label)
        for label, value in (
            ("handoff_id", self.handoff_id),
            ("fresh_risk_decision_fingerprint", self.fresh_risk_decision_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("execution_freshness_binding_hash", self.execution_freshness_binding_hash),
            ("final_freshness_permit_hash", self.final_freshness_permit_hash),
            ("handoff_hash", self.handoff_hash),
        ):
            _validate_hash(value, label)
        if isinstance(self.safety_state_version, bool) or not isinstance(self.safety_state_version, int) or self.safety_state_version < 0:
            raise ValueError("safety_state_version must be non-negative integer")
        _require_aware(self.authorized_at, "authorized_at")
        _require_aware(self.valid_until, "valid_until")
        if self.valid_until <= self.authorized_at:
            raise ValueError("connectivity handoff must retain a positive validity window")
        expected_event_id = f"connectivity-handoff:{self.order_id}:{self.attempt_id}"
        if self.event_id != expected_event_id:
            raise ValueError("connectivity handoff event_id mismatch")
        expected_id = _hash(
            {
                "order_id": self.order_id,
                "attempt_id": self.attempt_id,
                "fresh_risk_decision_id": self.fresh_risk_decision_id,
                "execution_freshness_binding_hash": self.execution_freshness_binding_hash,
                "authorized_at": _iso(self.authorized_at),
            }
        )
        if self.handoff_id != expected_id:
            raise ValueError("connectivity handoff_id mismatch")
        if self.handoff_hash != _hash(self.payload(include_hash=False)):
            raise ValueError("connectivity handoff hash mismatch")

    def is_valid_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        return self.authorized_at.astimezone(timezone.utc) <= instant < self.valid_until.astimezone(timezone.utc)

    def payload(self, *, include_hash: bool = True) -> dict[str, str]:
        payload = {
            "handoff_id": self.handoff_id,
            "purpose": self.purpose,
            "order_id": self.order_id,
            "attempt_id": self.attempt_id,
            "original_risk_decision_id": self.original_risk_decision_id,
            "fresh_risk_decision_id": self.fresh_risk_decision_id,
            "fresh_risk_decision_fingerprint": self.fresh_risk_decision_fingerprint,
            "safety_state_version": str(self.safety_state_version),
            "market_fingerprint": self.market_fingerprint,
            "execution_freshness_binding_hash": self.execution_freshness_binding_hash,
            "final_freshness_permit_hash": self.final_freshness_permit_hash,
            "authorized_at": _iso(self.authorized_at),
            "valid_until": _iso(self.valid_until),
            "event_id": self.event_id,
        }
        if include_hash:
            payload["handoff_hash"] = self.handoff_hash
        return payload


class ConnectivityOmsStager:
    """Purpose-specific OMS extension. It has no ExecutionBroker and no network API."""

    def __init__(
        self,
        *,
        order_store: OrderStore,
        ledger: EventLedger,
        safety_state_store: SafetyStateStore,
    ) -> None:
        self._orders = order_store
        self._ledger = ledger
        self._safety = safety_state_store

    def stage(
        self,
        *,
        order_id: str,
        attempt_id: str,
        execution_freshness_binding_hash: str,
        final_freshness_permit_hash: str,
        decision: RiskDecision,
        market: MarketSnapshot,
        now: datetime,
        valid_until: datetime,
    ) -> tuple[object, ConnectivitySubmissionHandoff]:
        _validate_id(order_id, "order_id")
        _validate_id(attempt_id, "attempt_id")
        _validate_hash(execution_freshness_binding_hash, "execution_freshness_binding_hash")
        _validate_hash(final_freshness_permit_hash, "final_freshness_permit_hash")
        if not isinstance(decision, RiskDecision):
            raise TypeError("fresh RiskDecision is required")
        if not isinstance(market, MarketSnapshot):
            raise TypeError("fresh MarketSnapshot is required")
        _require_aware(now, "now")
        _require_aware(valid_until, "valid_until")
        now_utc = now.astimezone(timezone.utc)
        if valid_until.astimezone(timezone.utc) <= now_utc:
            raise ConnectivityOmsStageRejected("execution/freshness binding is expired")

        current = self._orders.get_by_order_id(order_id)
        if current is None:
            raise KeyError(order_id)
        if current.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTING}:
            raise ConnectivityOmsStageRejected(
                f"connectivity staging requires VALIDATED/SUBMITTING, found {current.status.value}"
            )
        if current.intent.strategy_id != CONNECTIVITY_CANARY_STRATEGY_ID:
            raise ConnectivityOmsStageRejected("OMS strategy is not reserved connectivity canary")
        if (
            current.intent.quantity != Decimal("1")
            or current.intent.side.value != "BUY"
            or current.intent.order_type.value != "LIMIT"
            or current.intent.limit_price is None
        ):
            raise ConnectivityOmsStageRejected("connectivity OMS order shape is not exact BUY LIMIT x1")
        fingerprint = intent_fingerprint(current.intent)
        if decision.status is not RiskDecisionStatus.APPROVED:
            raise ConnectivityOmsStageRejected("fresh RiskDecision is not APPROVED")
        if decision.intent_id != current.intent.intent_id or decision.intent_fingerprint != fingerprint:
            raise ConnectivityOmsStageRejected("fresh RiskDecision does not bind current OMS intent")
        market_fp = market_fingerprint(market)
        if decision.market_fingerprint != market_fp:
            raise ConnectivityOmsStageRejected("fresh RiskDecision/market fingerprint mismatch")
        if now_utc < decision.evaluated_at.astimezone(timezone.utc):
            raise ConnectivityOmsStageRejected("cannot stage before fresh RiskDecision evaluation")
        if now_utc >= decision.valid_until.astimezone(timezone.utc):
            raise ConnectivityOmsStageRejected("fresh RiskDecision expired before staging")
        if valid_until.astimezone(timezone.utc) > decision.valid_until.astimezone(timezone.utc):
            raise ConnectivityOmsStageRejected("staging authority outlives fresh RiskDecision")
        notional = current.intent.quantity * current.intent.limit_price
        if decision.approved_notional != notional:
            raise ConnectivityOmsStageRejected("fresh RiskDecision approved notional mismatch")

        safety = self._safety.get()
        if safety.version != decision.safety_state_version:
            raise ConnectivityOmsStageRejected("Safety state version changed after Final Freshness")
        if safety.kill_switch_active:
            raise ConnectivityOmsStageRejected("connectivity staging blocked by kill switch")
        if safety.circuit_active:
            raise ConnectivityOmsStageRejected("connectivity staging blocked by safety circuit")

        handoff = _build_handoff(
            order_id=order_id,
            attempt_id=attempt_id,
            original_risk_decision_id=current.risk_decision_id,
            decision=decision,
            market_fingerprint_value=market_fp,
            execution_freshness_binding_hash=execution_freshness_binding_hash,
            final_freshness_permit_hash=final_freshness_permit_hash,
            authorized_at=now_utc,
            valid_until=valid_until.astimezone(timezone.utc),
        )
        event = LedgerEvent(
            event_id=handoff.event_id,
            event_type=_EVENT_TYPE,
            occurred_at=handoff.authorized_at,
            payload=handoff.payload(),
        )
        existing = tuple(
            item for item in self._ledger.all_events() if item.event_id == event.event_id
        )
        if len(existing) > 1:
            raise ConnectivityOmsStageConflict("duplicate connectivity handoff ledger identity")
        if existing:
            if existing[0] != event:
                raise ConnectivityOmsStageConflict("connectivity handoff ledger binding mismatch")
        else:
            if current.status is not OrderStatus.VALIDATED:
                raise ConnectivityOmsStageConflict(
                    "SUBMITTING without durable connectivity handoff event is forbidden"
                )
            try:
                self._ledger.append(event)
            except DuplicateLedgerEvent as exc:
                raise ConnectivityOmsStageConflict("connectivity handoff append raced") from exc

        if current.status is OrderStatus.VALIDATED:
            staged = replace(
                current,
                risk_decision_id=decision.decision_id,
                status=OrderStatus.SUBMITTING,
                submitted_at=handoff.authorized_at,
            )
            self._orders.update(staged)
        else:
            if (
                current.risk_decision_id != decision.decision_id
                or current.submitted_at != handoff.authorized_at
            ):
                raise ConnectivityOmsStageConflict("existing SUBMITTING order does not match handoff")
            staged = current
        return staged, handoff

    def verify_handoff(self, handoff: ConnectivitySubmissionHandoff):
        if not isinstance(handoff, ConnectivitySubmissionHandoff):
            raise TypeError("ConnectivitySubmissionHandoff is required")
        matches = tuple(
            item for item in self._ledger.all_events() if item.event_id == handoff.event_id
        )
        if len(matches) != 1:
            raise ConnectivityOmsStageConflict("connectivity handoff ledger event is missing or duplicated")
        if matches[0].event_type != _EVENT_TYPE or dict(matches[0].payload) != handoff.payload():
            raise ConnectivityOmsStageConflict("connectivity handoff does not match durable ledger")
        current = self._orders.get_by_order_id(handoff.order_id)
        if current is None:
            raise ConnectivityOmsStageConflict("connectivity OMS order is missing")
        if current.status is not OrderStatus.SUBMITTING:
            raise ConnectivityOmsStageConflict("connectivity handoff requires OMS SUBMITTING")
        if current.risk_decision_id != handoff.fresh_risk_decision_id:
            raise ConnectivityOmsStageConflict("connectivity OMS fresh RiskDecision binding changed")
        if current.submitted_at != handoff.authorized_at:
            raise ConnectivityOmsStageConflict("connectivity OMS staging timestamp changed")
        return current


def _build_handoff(
    *,
    order_id: str,
    attempt_id: str,
    original_risk_decision_id: str,
    decision: RiskDecision,
    market_fingerprint_value: str,
    execution_freshness_binding_hash: str,
    final_freshness_permit_hash: str,
    authorized_at: datetime,
    valid_until: datetime,
) -> ConnectivitySubmissionHandoff:
    handoff_id = _hash(
        {
            "order_id": order_id,
            "attempt_id": attempt_id,
            "fresh_risk_decision_id": decision.decision_id,
            "execution_freshness_binding_hash": execution_freshness_binding_hash,
            "authorized_at": _iso(authorized_at),
        }
    )
    event_id = f"connectivity-handoff:{order_id}:{attempt_id}"
    values = {
        "handoff_id": handoff_id,
        "purpose": "CONNECTIVITY_CANARY",
        "order_id": order_id,
        "attempt_id": attempt_id,
        "original_risk_decision_id": original_risk_decision_id,
        "fresh_risk_decision_id": decision.decision_id,
        "fresh_risk_decision_fingerprint": risk_decision_fingerprint(decision),
        "safety_state_version": decision.safety_state_version,
        "market_fingerprint": market_fingerprint_value,
        "execution_freshness_binding_hash": execution_freshness_binding_hash,
        "final_freshness_permit_hash": final_freshness_permit_hash,
        "authorized_at": authorized_at,
        "valid_until": valid_until,
        "event_id": event_id,
    }
    provisional = ConnectivitySubmissionHandoff(
        **values,
        handoff_hash="0" * 64,
    )
    # Build the immutable hash without recursively including itself.
    handoff_hash = _hash(provisional.payload(include_hash=False))
    return ConnectivitySubmissionHandoff(**values, handoff_hash=handoff_hash)


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be canonical identifier")


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _iso(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _hash(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ConnectivityOmsStageConflict",
    "ConnectivityOmsStageError",
    "ConnectivityOmsStageRejected",
    "ConnectivityOmsStager",
    "ConnectivitySubmissionHandoff",
]
