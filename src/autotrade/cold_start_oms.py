from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from .domain import (
    MarketSnapshot,
    OrderRecord,
    OrderStatus,
    RiskDecision,
    RiskDecisionStatus,
    intent_fingerprint,
    market_fingerprint,
)
from .ledger import LedgerEvent
from .oms import OrderManagementSystem


COLD_START_OMS_SCOPE = "FIRST_TECHNICAL_CANARY_ONLY"
COLD_START_OMS_KILL_REASON = "R6_HEALTH_R4_EVIDENCE_REQUIRED"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ColdStartExternalSubmissionError(RuntimeError):
    pass


class ColdStartExternalSubmissionConflict(ColdStartExternalSubmissionError):
    pass


@dataclass(frozen=True, slots=True)
class ColdStartOmsStageAuthorization:
    scope: str
    authorization_id: str
    package_hash: str
    operator_decision_hash: str
    checkpoint_hash: str
    authority_state_fingerprint: str
    attempt_id: str
    order_id: str
    client_order_id: str
    intent_fingerprint: str
    risk_decision_id: str
    market_fingerprint: str
    safety_state_version: int
    kill_switch_reason: str
    authorized_at: datetime
    authorization_hash: str

    def __post_init__(self) -> None:
        if self.scope != COLD_START_OMS_SCOPE:
            raise ValueError("cold-start OMS authorization scope mismatch")
        if self.kill_switch_reason != COLD_START_OMS_KILL_REASON:
            raise ValueError("cold-start OMS authorization kill reason mismatch")
        if isinstance(self.safety_state_version, bool) or not isinstance(self.safety_state_version, int) or self.safety_state_version < 0:
            raise ValueError("cold-start OMS Safety version is invalid")
        for label, value in (
            ("authorization_id", self.authorization_id),
            ("package_hash", self.package_hash),
            ("operator_decision_hash", self.operator_decision_hash),
            ("checkpoint_hash", self.checkpoint_hash),
            ("authority_state_fingerprint", self.authority_state_fingerprint),
            ("intent_fingerprint", self.intent_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("authorization_hash", self.authorization_hash),
        ):
            _require_hash(value, label)
        _require_aware(self.authorized_at, "authorized_at")
        if self.authorization_hash != _authorization_hash(self):
            raise ValueError("cold-start OMS authorization hash mismatch")


class ColdStartOmsStageAuthority:
    """Nominal capability for the single sanctioned bootstrap authority."""

    def authorize_oms_stage(
        self,
        *,
        order: OrderRecord,
        decision: RiskDecision,
        market: MarketSnapshot,
        now: datetime,
        context: object,
    ) -> ColdStartOmsStageAuthorization:
        raise NotImplementedError

    @staticmethod
    def _issue_authorization(
        *,
        authorization_id: str,
        package_hash: str,
        operator_decision_hash: str,
        checkpoint_hash: str,
        authority_state_fingerprint: str,
        attempt_id: str,
        order_id: str,
        client_order_id: str,
        intent_fingerprint_value: str,
        risk_decision_id: str,
        market_fingerprint_value: str,
        safety_state_version: int,
        authorized_at: datetime,
    ) -> ColdStartOmsStageAuthorization:
        values: dict[str, object] = {
            "scope": COLD_START_OMS_SCOPE,
            "authorization_id": authorization_id,
            "package_hash": package_hash,
            "operator_decision_hash": operator_decision_hash,
            "checkpoint_hash": checkpoint_hash,
            "authority_state_fingerprint": authority_state_fingerprint,
            "attempt_id": attempt_id,
            "order_id": order_id,
            "client_order_id": client_order_id,
            "intent_fingerprint": intent_fingerprint_value,
            "risk_decision_id": risk_decision_id,
            "market_fingerprint": market_fingerprint_value,
            "safety_state_version": safety_state_version,
            "kill_switch_reason": COLD_START_OMS_KILL_REASON,
            "authorized_at": authorized_at.astimezone(timezone.utc),
        }
        return ColdStartOmsStageAuthorization(
            scope=str(values["scope"]),
            authorization_id=str(values["authorization_id"]),
            package_hash=str(values["package_hash"]),
            operator_decision_hash=str(values["operator_decision_hash"]),
            checkpoint_hash=str(values["checkpoint_hash"]),
            authority_state_fingerprint=str(values["authority_state_fingerprint"]),
            attempt_id=str(values["attempt_id"]),
            order_id=str(values["order_id"]),
            client_order_id=str(values["client_order_id"]),
            intent_fingerprint=str(values["intent_fingerprint"]),
            risk_decision_id=str(values["risk_decision_id"]),
            market_fingerprint=str(values["market_fingerprint"]),
            safety_state_version=int(values["safety_state_version"]),
            kill_switch_reason=str(values["kill_switch_reason"]),
            authorized_at=values["authorized_at"],  # type: ignore[arg-type]
            authorization_hash=_authorization_hash_values(values),
        )


@dataclass(frozen=True, slots=True)
class ColdStartExternalSubmissionHandoff:
    scope: str
    authorization_id: str
    package_hash: str
    operator_decision_hash: str
    checkpoint_hash: str
    authority_state_fingerprint: str
    attempt_id: str
    order_id: str
    client_order_id: str
    intent_fingerprint: str
    risk_decision_id: str
    market_fingerprint: str
    safety_state_version: int
    kill_switch_reason: str
    authorized_at: datetime
    authorization_hash: str
    event_id: str
    handoff_hash: str

    def __post_init__(self) -> None:
        if self.scope != COLD_START_OMS_SCOPE or self.kill_switch_reason != COLD_START_OMS_KILL_REASON:
            raise ValueError("cold-start OMS handoff scope/kill reason mismatch")
        for label, value in (
            ("authorization_id", self.authorization_id),
            ("package_hash", self.package_hash),
            ("operator_decision_hash", self.operator_decision_hash),
            ("checkpoint_hash", self.checkpoint_hash),
            ("authority_state_fingerprint", self.authority_state_fingerprint),
            ("intent_fingerprint", self.intent_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("authorization_hash", self.authorization_hash),
            ("handoff_hash", self.handoff_hash),
        ):
            _require_hash(value, label)
        _require_aware(self.authorized_at, "authorized_at")
        if self.event_id != f"cold-start-external-handoff:{self.order_id}:{self.authorization_id}":
            raise ValueError("cold-start OMS handoff event_id mismatch")
        if self.handoff_hash != _handoff_hash(self):
            raise ValueError("cold-start OMS handoff hash mismatch")

    def to_event_payload(self) -> dict[str, str]:
        return {
            "scope": self.scope,
            "authorization_id": self.authorization_id,
            "package_hash": self.package_hash,
            "operator_decision_hash": self.operator_decision_hash,
            "checkpoint_hash": self.checkpoint_hash,
            "authority_state_fingerprint": self.authority_state_fingerprint,
            "attempt_id": self.attempt_id,
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "intent_fingerprint": self.intent_fingerprint,
            "risk_decision_id": self.risk_decision_id,
            "market_fingerprint": self.market_fingerprint,
            "safety_state_version": str(self.safety_state_version),
            "kill_switch_reason": self.kill_switch_reason,
            "authorized_at": _iso(self.authorized_at),
            "authorization_hash": self.authorization_hash,
            "event_id": self.event_id,
            "handoff_hash": self.handoff_hash,
        }


class ColdStartOrderManagementSystem(OrderManagementSystem):
    """OMS-owned cold-start handoff; contains no broker submission call."""

    def stage_cold_start_external_submission(
        self,
        *,
        order_id: str,
        decision: RiskDecision,
        market: MarketSnapshot,
        now: datetime,
        authority: ColdStartOmsStageAuthority,
        authority_context: object,
    ) -> tuple[OrderRecord, ColdStartExternalSubmissionHandoff]:
        _require_aware(now, "cold-start OMS stage time")
        if not isinstance(authority, ColdStartOmsStageAuthority):
            raise ColdStartExternalSubmissionConflict("nominal cold-start OMS authority is required")
        current = self._orders.get_by_order_id(order_id)
        if current is None:
            raise KeyError(order_id)
        if current.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTING}:
            raise ColdStartExternalSubmissionConflict(
                f"cold-start OMS stage cannot resume from {current.status.value}"
            )
        fingerprint = self._validate_cold_start_decision(
            order=current,
            decision=decision,
            market=market,
            now=now,
        )
        safety = self._cold_start_safety_state()
        authorization = authority.authorize_oms_stage(
            order=current,
            decision=decision,
            market=market,
            now=now,
            context=authority_context,
        )
        self._validate_authorization(
            authorization=authorization,
            order=current,
            decision=decision,
            market=market,
            intent_hash=fingerprint,
            safety_version=safety.version,
            now=now,
        )
        self._cold_start_safety_state(expected_version=authorization.safety_state_version)

        event_id = f"cold-start-external-handoff:{order_id}:{authorization.authorization_id}"
        existing = tuple(event for event in self._ledger.all_events() if event.event_id == event_id)
        if len(existing) > 1:
            raise ColdStartExternalSubmissionConflict("duplicate cold-start OMS handoff ledger identity")
        if existing:
            handoff = _handoff_from_event(existing[0])
            _require_handoff_matches_authorization(handoff, authorization)
        else:
            if current.status is not OrderStatus.VALIDATED:
                raise ColdStartExternalSubmissionConflict(
                    "SUBMITTING without durable cold-start OMS handoff is forbidden"
                )
            handoff = _build_handoff(authorization)
            self._append_idempotent(
                LedgerEvent(
                    event_id=handoff.event_id,
                    event_type="COLD_START_EXTERNAL_ORDER_HANDOFF_AUTHORIZED",
                    occurred_at=handoff.authorized_at,
                    payload=handoff.to_event_payload(),
                )
            )

        if current.status is OrderStatus.VALIDATED:
            staged = replace(current, status=OrderStatus.SUBMITTING, submitted_at=handoff.authorized_at)
            self._orders.update(staged)
        else:
            if current.submitted_at != handoff.authorized_at:
                raise ColdStartExternalSubmissionConflict(
                    "SUBMITTING timestamp differs from durable cold-start OMS handoff"
                )
            staged = current

        self._cold_start_safety_state(expected_version=authorization.safety_state_version)
        return staged, handoff

    def resolve_cold_start_external_submission_handoff(
        self,
        *,
        order_id: str,
        authorization_id: str,
    ) -> ColdStartExternalSubmissionHandoff:
        _require_hash(authorization_id, "authorization_id")
        event_id = f"cold-start-external-handoff:{order_id}:{authorization_id}"
        matches = tuple(event for event in self._ledger.all_events() if event.event_id == event_id)
        if len(matches) != 1:
            raise ColdStartExternalSubmissionConflict(
                "cold-start OMS handoff ledger event is missing or duplicated"
            )
        handoff = _handoff_from_event(matches[0])
        current = self._orders.get_by_order_id(order_id)
        if current is None or current.status is not OrderStatus.SUBMITTING:
            raise ColdStartExternalSubmissionConflict("cold-start OMS handoff requires SUBMITTING order")
        if current.submitted_at != handoff.authorized_at:
            raise ColdStartExternalSubmissionConflict("cold-start OMS handoff timestamp mismatch")
        if intent_fingerprint(current.intent) != handoff.intent_fingerprint:
            raise ColdStartExternalSubmissionConflict("cold-start OMS handoff intent changed")
        if current.risk_decision_id != handoff.risk_decision_id:
            raise ColdStartExternalSubmissionConflict("cold-start OMS handoff RiskDecision changed")
        self._cold_start_safety_state(expected_version=handoff.safety_state_version)
        return handoff

    def _validate_cold_start_decision(
        self,
        *,
        order: OrderRecord,
        decision: RiskDecision,
        market: MarketSnapshot,
        now: datetime,
    ) -> str:
        if decision.status is not RiskDecisionStatus.APPROVED:
            raise ColdStartExternalSubmissionConflict("cold-start RiskDecision is not APPROVED")
        if decision.intent_id != order.intent.intent_id:
            raise ColdStartExternalSubmissionConflict("cold-start RiskDecision intent mismatch")
        fingerprint = intent_fingerprint(order.intent)
        if decision.intent_fingerprint != fingerprint:
            raise ColdStartExternalSubmissionConflict("cold-start RiskDecision fingerprint mismatch")
        if decision.market_fingerprint != market_fingerprint(market):
            raise ColdStartExternalSubmissionConflict("cold-start market changed after RiskDecision")
        if now > decision.valid_until:
            raise ColdStartExternalSubmissionConflict("cold-start RiskDecision expired")
        if order.risk_decision_id != decision.decision_id:
            raise ColdStartExternalSubmissionConflict("cold-start order RiskDecision id mismatch")
        return fingerprint

    def _cold_start_safety_state(self, *, expected_version: int | None = None):
        if self._safety_state_store is None:
            raise ColdStartExternalSubmissionConflict("authoritative Safety store is required")
        safety = self._safety_state_store.get()
        if expected_version is not None and safety.version != expected_version:
            raise ColdStartExternalSubmissionConflict("cold-start OMS Safety version changed")
        if not safety.kill_switch_active or safety.kill_switch_reason != COLD_START_OMS_KILL_REASON:
            raise ColdStartExternalSubmissionConflict("exact commissioning kill switch is required")
        if safety.circuit_active:
            raise ColdStartExternalSubmissionConflict("cold-start OMS blocked by safety circuit")
        return safety

    @staticmethod
    def _validate_authorization(
        *,
        authorization: ColdStartOmsStageAuthorization,
        order: OrderRecord,
        decision: RiskDecision,
        market: MarketSnapshot,
        intent_hash: str,
        safety_version: int,
        now: datetime,
    ) -> None:
        if not isinstance(authorization, ColdStartOmsStageAuthorization):
            raise ColdStartExternalSubmissionConflict("invalid cold-start OMS authorization type")
        expected = {
            "order_id": order.order_id,
            "intent_fingerprint": intent_hash,
            "risk_decision_id": decision.decision_id,
            "market_fingerprint": market_fingerprint(market),
            "safety_state_version": safety_version,
            "kill_switch_reason": COLD_START_OMS_KILL_REASON,
        }
        if {key: getattr(authorization, key) for key in expected} != expected:
            raise ColdStartExternalSubmissionConflict("cold-start OMS authorization binding mismatch")
        if authorization.authorized_at > now.astimezone(timezone.utc):
            raise ColdStartExternalSubmissionConflict("cold-start OMS authorization is future-dated")
        if authorization.authorized_at > decision.valid_until.astimezone(timezone.utc):
            raise ColdStartExternalSubmissionConflict("cold-start OMS authorization outlives RiskDecision")


def _build_handoff(authorization: ColdStartOmsStageAuthorization) -> ColdStartExternalSubmissionHandoff:
    values: dict[str, object] = {
        "scope": authorization.scope,
        "authorization_id": authorization.authorization_id,
        "package_hash": authorization.package_hash,
        "operator_decision_hash": authorization.operator_decision_hash,
        "checkpoint_hash": authorization.checkpoint_hash,
        "authority_state_fingerprint": authorization.authority_state_fingerprint,
        "attempt_id": authorization.attempt_id,
        "order_id": authorization.order_id,
        "client_order_id": authorization.client_order_id,
        "intent_fingerprint": authorization.intent_fingerprint,
        "risk_decision_id": authorization.risk_decision_id,
        "market_fingerprint": authorization.market_fingerprint,
        "safety_state_version": authorization.safety_state_version,
        "kill_switch_reason": authorization.kill_switch_reason,
        "authorized_at": authorization.authorized_at,
        "authorization_hash": authorization.authorization_hash,
        "event_id": f"cold-start-external-handoff:{authorization.order_id}:{authorization.authorization_id}",
    }
    return ColdStartExternalSubmissionHandoff(
        scope=str(values["scope"]),
        authorization_id=str(values["authorization_id"]),
        package_hash=str(values["package_hash"]),
        operator_decision_hash=str(values["operator_decision_hash"]),
        checkpoint_hash=str(values["checkpoint_hash"]),
        authority_state_fingerprint=str(values["authority_state_fingerprint"]),
        attempt_id=str(values["attempt_id"]),
        order_id=str(values["order_id"]),
        client_order_id=str(values["client_order_id"]),
        intent_fingerprint=str(values["intent_fingerprint"]),
        risk_decision_id=str(values["risk_decision_id"]),
        market_fingerprint=str(values["market_fingerprint"]),
        safety_state_version=int(values["safety_state_version"]),
        kill_switch_reason=str(values["kill_switch_reason"]),
        authorized_at=values["authorized_at"],  # type: ignore[arg-type]
        authorization_hash=str(values["authorization_hash"]),
        event_id=str(values["event_id"]),
        handoff_hash=_handoff_hash_values(values),
    )


def _handoff_from_event(event: LedgerEvent) -> ColdStartExternalSubmissionHandoff:
    if event.event_type != "COLD_START_EXTERNAL_ORDER_HANDOFF_AUTHORIZED":
        raise ColdStartExternalSubmissionConflict("cold-start OMS handoff event type mismatch")
    payload = dict(event.payload)
    expected = {
        "scope", "authorization_id", "package_hash", "operator_decision_hash", "checkpoint_hash",
        "authority_state_fingerprint", "attempt_id", "order_id", "client_order_id", "intent_fingerprint",
        "risk_decision_id", "market_fingerprint", "safety_state_version", "kill_switch_reason",
        "authorized_at", "authorization_hash", "event_id", "handoff_hash",
    }
    if set(payload) != expected:
        raise ColdStartExternalSubmissionConflict("cold-start OMS handoff payload is non-canonical")
    try:
        handoff = ColdStartExternalSubmissionHandoff(
            scope=str(payload["scope"]),
            authorization_id=str(payload["authorization_id"]),
            package_hash=str(payload["package_hash"]),
            operator_decision_hash=str(payload["operator_decision_hash"]),
            checkpoint_hash=str(payload["checkpoint_hash"]),
            authority_state_fingerprint=str(payload["authority_state_fingerprint"]),
            attempt_id=str(payload["attempt_id"]),
            order_id=str(payload["order_id"]),
            client_order_id=str(payload["client_order_id"]),
            intent_fingerprint=str(payload["intent_fingerprint"]),
            risk_decision_id=str(payload["risk_decision_id"]),
            market_fingerprint=str(payload["market_fingerprint"]),
            safety_state_version=int(str(payload["safety_state_version"])),
            kill_switch_reason=str(payload["kill_switch_reason"]),
            authorized_at=datetime.fromisoformat(str(payload["authorized_at"])),
            authorization_hash=str(payload["authorization_hash"]),
            event_id=str(payload["event_id"]),
            handoff_hash=str(payload["handoff_hash"]),
        )
    except Exception as exc:
        raise ColdStartExternalSubmissionConflict("cold-start OMS handoff evidence is invalid or tampered") from exc
    if event.event_id != handoff.event_id or event.occurred_at != handoff.authorized_at:
        raise ColdStartExternalSubmissionConflict("cold-start OMS handoff ledger envelope mismatch")
    return handoff


def _require_handoff_matches_authorization(
    handoff: ColdStartExternalSubmissionHandoff,
    authorization: ColdStartOmsStageAuthorization,
) -> None:
    fields = (
        "scope", "authorization_id", "package_hash", "operator_decision_hash", "checkpoint_hash",
        "authority_state_fingerprint", "attempt_id", "order_id", "client_order_id", "intent_fingerprint",
        "risk_decision_id", "market_fingerprint", "safety_state_version", "kill_switch_reason",
        "authorized_at", "authorization_hash",
    )
    if any(getattr(handoff, field) != getattr(authorization, field) for field in fields):
        raise ColdStartExternalSubmissionConflict("cold-start OMS handoff/authorization mismatch")


def _authorization_hash(authorization: ColdStartOmsStageAuthorization) -> str:
    return _authorization_hash_values(
        {
            "scope": authorization.scope,
            "authorization_id": authorization.authorization_id,
            "package_hash": authorization.package_hash,
            "operator_decision_hash": authorization.operator_decision_hash,
            "checkpoint_hash": authorization.checkpoint_hash,
            "authority_state_fingerprint": authorization.authority_state_fingerprint,
            "attempt_id": authorization.attempt_id,
            "order_id": authorization.order_id,
            "client_order_id": authorization.client_order_id,
            "intent_fingerprint": authorization.intent_fingerprint,
            "risk_decision_id": authorization.risk_decision_id,
            "market_fingerprint": authorization.market_fingerprint,
            "safety_state_version": authorization.safety_state_version,
            "kill_switch_reason": authorization.kill_switch_reason,
            "authorized_at": authorization.authorized_at,
        }
    )


def _authorization_hash_values(values: dict[str, object]) -> str:
    return sha256(_canonical(values).encode("utf-8")).hexdigest()


def _handoff_hash(handoff: ColdStartExternalSubmissionHandoff) -> str:
    return _handoff_hash_values(
        {
            "scope": handoff.scope,
            "authorization_id": handoff.authorization_id,
            "package_hash": handoff.package_hash,
            "operator_decision_hash": handoff.operator_decision_hash,
            "checkpoint_hash": handoff.checkpoint_hash,
            "authority_state_fingerprint": handoff.authority_state_fingerprint,
            "attempt_id": handoff.attempt_id,
            "order_id": handoff.order_id,
            "client_order_id": handoff.client_order_id,
            "intent_fingerprint": handoff.intent_fingerprint,
            "risk_decision_id": handoff.risk_decision_id,
            "market_fingerprint": handoff.market_fingerprint,
            "safety_state_version": handoff.safety_state_version,
            "kill_switch_reason": handoff.kill_switch_reason,
            "authorized_at": handoff.authorized_at,
            "authorization_hash": handoff.authorization_hash,
            "event_id": handoff.event_id,
        }
    )


def _handoff_hash_values(values: dict[str, object]) -> str:
    return sha256(_canonical(values).encode("utf-8")).hexdigest()


def _canonical(values: dict[str, object]) -> str:
    payload = dict(values)
    for key, value in tuple(payload.items()):
        if isinstance(value, datetime):
            payload[key] = _iso(value)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _iso(value: datetime) -> str:
    _require_aware(value, "timestamp")
    return value.astimezone(timezone.utc).isoformat()


__all__ = [
    "COLD_START_OMS_KILL_REASON",
    "COLD_START_OMS_SCOPE",
    "ColdStartExternalSubmissionConflict",
    "ColdStartExternalSubmissionError",
    "ColdStartExternalSubmissionHandoff",
    "ColdStartOmsStageAuthorization",
    "ColdStartOmsStageAuthority",
    "ColdStartOrderManagementSystem",
]
