from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Mapping

from autotrade.connectivity_canary_authority import CONNECTIVITY_CANARY_STRATEGY_ID
from autotrade.domain import (
    MarketSnapshot,
    OrderRecord,
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
        if (
            isinstance(self.safety_state_version, bool)
            or not isinstance(self.safety_state_version, int)
            or self.safety_state_version < 0
        ):
            raise ValueError("safety_state_version must be non-negative integer")
        _require_aware(self.authorized_at, "authorized_at")
        _require_aware(self.valid_until, "valid_until")
        if self.valid_until <= self.authorized_at:
            raise ValueError("connectivity handoff must retain a positive validity window")
        if self.event_id != f"connectivity-handoff:{self.order_id}:{self.attempt_id}":
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
        return (
            self.authorized_at.astimezone(timezone.utc)
            <= instant
            < self.valid_until.astimezone(timezone.utc)
        )

    def payload(self, *, include_hash: bool = True) -> dict[str, str]:
        payload = _handoff_payload_from_values(
            handoff_id=self.handoff_id,
            purpose=self.purpose,
            order_id=self.order_id,
            attempt_id=self.attempt_id,
            original_risk_decision_id=self.original_risk_decision_id,
            fresh_risk_decision_id=self.fresh_risk_decision_id,
            fresh_risk_decision_fingerprint=self.fresh_risk_decision_fingerprint,
            safety_state_version=self.safety_state_version,
            market_fingerprint_value=self.market_fingerprint,
            execution_freshness_binding_hash=self.execution_freshness_binding_hash,
            final_freshness_permit_hash=self.final_freshness_permit_hash,
            authorized_at=self.authorized_at,
            valid_until=self.valid_until,
            event_id=self.event_id,
        )
        if include_hash:
            payload["handoff_hash"] = self.handoff_hash
        return payload


class ConnectivityOmsStager:
    """Connectivity-only deterministic OMS staging. No broker or network surface."""

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
    ) -> tuple[OrderRecord, ConnectivitySubmissionHandoff]:
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
        valid_until_utc = valid_until.astimezone(timezone.utc)
        if valid_until_utc <= now_utc:
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
            raise ConnectivityOmsStageRejected(
                "connectivity OMS order shape is not exact BUY LIMIT x1"
            )
        if market.symbol != current.intent.symbol:
            raise ConnectivityOmsStageRejected("fresh market symbol does not match OMS intent")

        fingerprint = intent_fingerprint(current.intent)
        if decision.status is not RiskDecisionStatus.APPROVED:
            raise ConnectivityOmsStageRejected("fresh RiskDecision is not APPROVED")
        if (
            decision.intent_id != current.intent.intent_id
            or decision.intent_fingerprint != fingerprint
        ):
            raise ConnectivityOmsStageRejected(
                "fresh RiskDecision does not bind current OMS intent"
            )
        market_fp = market_fingerprint(market)
        if decision.market_fingerprint != market_fp:
            raise ConnectivityOmsStageRejected(
                "fresh RiskDecision/market fingerprint mismatch"
            )
        if now_utc < decision.evaluated_at.astimezone(timezone.utc):
            raise ConnectivityOmsStageRejected(
                "cannot stage before fresh RiskDecision evaluation"
            )
        if now_utc >= decision.valid_until.astimezone(timezone.utc):
            raise ConnectivityOmsStageRejected("fresh RiskDecision expired before staging")
        if valid_until_utc > decision.valid_until.astimezone(timezone.utc):
            raise ConnectivityOmsStageRejected(
                "staging authority outlives fresh RiskDecision"
            )
        notional = current.intent.quantity * current.intent.limit_price
        if decision.approved_notional != notional:
            raise ConnectivityOmsStageRejected(
                "fresh RiskDecision approved notional mismatch"
            )

        safety = self._safety.get()
        if safety.version != decision.safety_state_version:
            raise ConnectivityOmsStageRejected(
                "Safety state version changed after Final Freshness"
            )
        if safety.kill_switch_active:
            raise ConnectivityOmsStageRejected(
                "connectivity staging blocked by kill switch"
            )
        if safety.circuit_active:
            raise ConnectivityOmsStageRejected(
                "connectivity staging blocked by safety circuit"
            )

        event_id = f"connectivity-handoff:{order_id}:{attempt_id}"
        existing = tuple(
            event for event in self._ledger.all_events() if event.event_id == event_id
        )
        if len(existing) > 1:
            raise ConnectivityOmsStageConflict(
                "duplicate connectivity handoff ledger identity"
            )
        if existing:
            if existing[0].event_type != _EVENT_TYPE:
                raise ConnectivityOmsStageConflict(
                    "connectivity handoff ledger event type mismatch"
                )
            handoff = _handoff_from_payload(existing[0].payload)
            self._verify_requested_replay(
                handoff=handoff,
                current=current,
                attempt_id=attempt_id,
                binding_hash=execution_freshness_binding_hash,
                permit_hash=final_freshness_permit_hash,
                decision=decision,
                market_fingerprint_value=market_fp,
            )
            if not handoff.is_valid_at(now_utc):
                raise ConnectivityOmsStageRejected(
                    "durable connectivity handoff expired before replay"
                )
            return current, handoff

        if current.status is not OrderStatus.VALIDATED:
            raise ConnectivityOmsStageConflict(
                "SUBMITTING without durable connectivity handoff event is forbidden"
            )
        handoff = _build_handoff(
            order_id=order_id,
            attempt_id=attempt_id,
            original_risk_decision_id=current.risk_decision_id,
            decision=decision,
            market_fingerprint_value=market_fp,
            execution_freshness_binding_hash=execution_freshness_binding_hash,
            final_freshness_permit_hash=final_freshness_permit_hash,
            authorized_at=now_utc,
            valid_until=valid_until_utc,
        )
        try:
            self._ledger.append(
                LedgerEvent(
                    event_id=handoff.event_id,
                    event_type=_EVENT_TYPE,
                    occurred_at=handoff.authorized_at,
                    payload=handoff.payload(),
                )
            )
        except DuplicateLedgerEvent as exc:
            raise ConnectivityOmsStageConflict(
                "connectivity handoff append raced"
            ) from exc

        staged = replace(
            current,
            risk_decision_id=decision.decision_id,
            status=OrderStatus.SUBMITTING,
            submitted_at=handoff.authorized_at,
        )
        self._orders.update(staged)
        return staged, handoff

    def verify_handoff(
        self, handoff: ConnectivitySubmissionHandoff
    ) -> OrderRecord:
        if not isinstance(handoff, ConnectivitySubmissionHandoff):
            raise TypeError("ConnectivitySubmissionHandoff is required")
        matches = tuple(
            event
            for event in self._ledger.all_events()
            if event.event_id == handoff.event_id
        )
        if len(matches) != 1:
            raise ConnectivityOmsStageConflict(
                "connectivity handoff ledger event is missing or duplicated"
            )
        if (
            matches[0].event_type != _EVENT_TYPE
            or dict(matches[0].payload) != handoff.payload()
        ):
            raise ConnectivityOmsStageConflict(
                "connectivity handoff does not match durable ledger"
            )
        current = self._orders.get_by_order_id(handoff.order_id)
        if current is None:
            raise ConnectivityOmsStageConflict("connectivity OMS order is missing")
        if current.status is not OrderStatus.SUBMITTING:
            raise ConnectivityOmsStageConflict(
                "connectivity handoff requires OMS SUBMITTING"
            )
        if current.risk_decision_id != handoff.fresh_risk_decision_id:
            raise ConnectivityOmsStageConflict(
                "connectivity OMS fresh RiskDecision binding changed"
            )
        if current.submitted_at != handoff.authorized_at:
            raise ConnectivityOmsStageConflict(
                "connectivity OMS staging timestamp changed"
            )
        return current

    @staticmethod
    def _verify_requested_replay(
        *,
        handoff: ConnectivitySubmissionHandoff,
        current: OrderRecord,
        attempt_id: str,
        binding_hash: str,
        permit_hash: str,
        decision: RiskDecision,
        market_fingerprint_value: str,
    ) -> None:
        if current.status is not OrderStatus.SUBMITTING:
            raise ConnectivityOmsStageConflict(
                "durable handoff exists but OMS is not SUBMITTING"
            )
        if (
            handoff.order_id != current.order_id
            or handoff.attempt_id != attempt_id
            or handoff.execution_freshness_binding_hash != binding_hash
            or handoff.final_freshness_permit_hash != permit_hash
            or handoff.fresh_risk_decision_id != decision.decision_id
            or handoff.fresh_risk_decision_fingerprint
            != risk_decision_fingerprint(decision)
            or handoff.safety_state_version != decision.safety_state_version
            or handoff.market_fingerprint != market_fingerprint_value
        ):
            raise ConnectivityOmsStageConflict(
                "connectivity handoff replay binding mismatch"
            )
        if current.risk_decision_id != handoff.fresh_risk_decision_id:
            raise ConnectivityOmsStageConflict(
                "SUBMITTING fresh RiskDecision changed"
            )
        if current.submitted_at != handoff.authorized_at:
            raise ConnectivityOmsStageConflict("SUBMITTING timestamp changed")


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
    payload = _handoff_payload_from_values(
        handoff_id=handoff_id,
        purpose="CONNECTIVITY_CANARY",
        order_id=order_id,
        attempt_id=attempt_id,
        original_risk_decision_id=original_risk_decision_id,
        fresh_risk_decision_id=decision.decision_id,
        fresh_risk_decision_fingerprint=risk_decision_fingerprint(decision),
        safety_state_version=decision.safety_state_version,
        market_fingerprint_value=market_fingerprint_value,
        execution_freshness_binding_hash=execution_freshness_binding_hash,
        final_freshness_permit_hash=final_freshness_permit_hash,
        authorized_at=authorized_at,
        valid_until=valid_until,
        event_id=event_id,
    )
    return ConnectivitySubmissionHandoff(
        handoff_id=handoff_id,
        purpose="CONNECTIVITY_CANARY",
        order_id=order_id,
        attempt_id=attempt_id,
        original_risk_decision_id=original_risk_decision_id,
        fresh_risk_decision_id=decision.decision_id,
        fresh_risk_decision_fingerprint=risk_decision_fingerprint(decision),
        safety_state_version=decision.safety_state_version,
        market_fingerprint=market_fingerprint_value,
        execution_freshness_binding_hash=execution_freshness_binding_hash,
        final_freshness_permit_hash=final_freshness_permit_hash,
        authorized_at=authorized_at,
        valid_until=valid_until,
        event_id=event_id,
        handoff_hash=_hash(payload),
    )


def _handoff_from_payload(
    raw: Mapping[str, str],
) -> ConnectivitySubmissionHandoff:
    expected = {
        "handoff_id",
        "purpose",
        "order_id",
        "attempt_id",
        "original_risk_decision_id",
        "fresh_risk_decision_id",
        "fresh_risk_decision_fingerprint",
        "safety_state_version",
        "market_fingerprint",
        "execution_freshness_binding_hash",
        "final_freshness_permit_hash",
        "authorized_at",
        "valid_until",
        "event_id",
        "handoff_hash",
    }
    if set(raw) != expected:
        raise ConnectivityOmsStageConflict(
            "connectivity handoff payload is non-canonical"
        )
    try:
        return ConnectivitySubmissionHandoff(
            handoff_id=str(raw["handoff_id"]),
            purpose=str(raw["purpose"]),
            order_id=str(raw["order_id"]),
            attempt_id=str(raw["attempt_id"]),
            original_risk_decision_id=str(raw["original_risk_decision_id"]),
            fresh_risk_decision_id=str(raw["fresh_risk_decision_id"]),
            fresh_risk_decision_fingerprint=str(
                raw["fresh_risk_decision_fingerprint"]
            ),
            safety_state_version=int(raw["safety_state_version"]),
            market_fingerprint=str(raw["market_fingerprint"]),
            execution_freshness_binding_hash=str(
                raw["execution_freshness_binding_hash"]
            ),
            final_freshness_permit_hash=str(raw["final_freshness_permit_hash"]),
            authorized_at=_datetime(str(raw["authorized_at"]), "authorized_at"),
            valid_until=_datetime(str(raw["valid_until"]), "valid_until"),
            event_id=str(raw["event_id"]),
            handoff_hash=str(raw["handoff_hash"]),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ConnectivityOmsStageConflict(
            "invalid durable connectivity handoff"
        ) from exc


def _handoff_payload_from_values(
    *,
    handoff_id: str,
    purpose: str,
    order_id: str,
    attempt_id: str,
    original_risk_decision_id: str,
    fresh_risk_decision_id: str,
    fresh_risk_decision_fingerprint: str,
    safety_state_version: int,
    market_fingerprint_value: str,
    execution_freshness_binding_hash: str,
    final_freshness_permit_hash: str,
    authorized_at: datetime,
    valid_until: datetime,
    event_id: str,
) -> dict[str, str]:
    return {
        "handoff_id": handoff_id,
        "purpose": purpose,
        "order_id": order_id,
        "attempt_id": attempt_id,
        "original_risk_decision_id": original_risk_decision_id,
        "fresh_risk_decision_id": fresh_risk_decision_id,
        "fresh_risk_decision_fingerprint": fresh_risk_decision_fingerprint,
        "safety_state_version": str(safety_state_version),
        "market_fingerprint": market_fingerprint_value,
        "execution_freshness_binding_hash": execution_freshness_binding_hash,
        "final_freshness_permit_hash": final_freshness_permit_hash,
        "authorized_at": _iso(authorized_at),
        "valid_until": _iso(valid_until),
        "event_id": event_id,
    }


def _datetime(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is invalid datetime") from exc
    _require_aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be canonical identifier")


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must be timezone-aware")


def _iso(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _hash(payload: object) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ConnectivityOmsStageConflict",
    "ConnectivityOmsStageError",
    "ConnectivityOmsStageRejected",
    "ConnectivityOmsStager",
    "ConnectivitySubmissionHandoff",
]
