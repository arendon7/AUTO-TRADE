from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3
from typing import Mapping

from autotrade.oms import ExternalSubmissionHandoff
from autotrade.persistence import SQLiteRuntime

from .alpaca_paper_bracket import AlpacaEquityBracketRequest
from .alpaca_paper_canary import PaperCanaryApproval
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation
from .alpaca_paper_submission import PaperSubmissionBinding


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_GENESIS_HASH = "0" * 64
_OPERATOR_SOURCE = "HUMAN_OPERATOR"
_OPERATOR_ACTION = "APPROVE_SINGLE_PAPER_CANARY"
_MAX_DECISION_TTL = timedelta(minutes=2)


class PaperOperatorDecisionError(RuntimeError):
    pass


class PaperOperatorDecisionIntegrityError(PaperOperatorDecisionError):
    pass


class PaperOperatorDecisionConflict(PaperOperatorDecisionError):
    pass


class PaperOperatorDecisionExpired(PaperOperatorDecisionError):
    pass


class PaperOperatorDecisionStatus(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"


class PaperOperatorDecisionEventType(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"


@dataclass(frozen=True, slots=True)
class PaperOperatorDecisionContext:
    environment: str
    account_attestation_fingerprint: str
    order_id: str
    client_order_id: str
    binding_hash: str
    bracket_payload_hash: str
    canary_approval_hash: str
    oms_handoff_hash: str
    notional: Decimal
    attempt_id: str
    preparation_hash: str

    def __post_init__(self) -> None:
        if self.environment != "PAPER":
            raise ValueError("operator decision context is PAPER-only")
        _validate_hash(self.account_attestation_fingerprint, "account_attestation_fingerprint")
        _validate_id(self.order_id, "order_id")
        _validate_id(self.client_order_id, "client_order_id")
        _validate_hash(self.binding_hash, "binding_hash")
        _validate_hash(self.bracket_payload_hash, "bracket_payload_hash")
        _validate_hash(self.canary_approval_hash, "canary_approval_hash")
        _validate_hash(self.oms_handoff_hash, "oms_handoff_hash")
        _validate_id(self.attempt_id, "attempt_id")
        if not self.notional.is_finite() or self.notional <= 0:
            raise ValueError("operator decision notional must be finite and positive")
        _validate_hash(self.preparation_hash, "preparation_hash")
        if self.preparation_hash != _hash_json(_context_payload_without_hash(self)):
            raise ValueError("operator decision preparation_hash mismatch")

    @classmethod
    def from_evidence(
        cls,
        *,
        account_attestation: AlpacaPaperAccountAttestation,
        expected_bracket: AlpacaEquityBracketRequest,
        approval: PaperCanaryApproval,
        binding: PaperSubmissionBinding,
        external_handoff: ExternalSubmissionHandoff,
        attempt_id: str,
    ) -> "PaperOperatorDecisionContext":
        _validate_id(attempt_id, "attempt_id")
        if account_attestation.status != "ACTIVE" or account_attestation.currency != "USD":
            raise ValueError("operator decision requires ACTIVE USD PAPER account attestation")
        account_fp = account_attestation.fingerprint
        if expected_bracket.order_id != binding.order_id:
            raise ValueError("operator context bracket/binding order_id mismatch")
        if expected_bracket.client_order_id != binding.client_order_id:
            raise ValueError("operator context bracket/binding client_order_id mismatch")
        if expected_bracket.payload_hash != binding.order_payload_hash:
            raise ValueError("operator context bracket/binding payload hash mismatch")
        if binding.account_attestation_fingerprint != account_fp:
            raise ValueError("operator context binding/account mismatch")
        if approval.order_id != binding.order_id:
            raise ValueError("operator context approval order_id mismatch")
        if approval.client_order_id != binding.client_order_id:
            raise ValueError("operator context approval client_order_id mismatch")
        if approval.binding_hash != binding.fingerprint:
            raise ValueError("operator context approval binding mismatch")
        if approval.account_attestation_fingerprint != account_fp:
            raise ValueError("operator context approval account mismatch")
        if approval.risk_decision_id != binding.risk_decision_id:
            raise ValueError("operator context approval risk decision mismatch")
        if external_handoff.order_id != binding.order_id:
            raise ValueError("operator context OMS handoff order mismatch")
        if external_handoff.intent_fingerprint != binding.intent_fingerprint:
            raise ValueError("operator context OMS handoff intent mismatch")
        if external_handoff.risk_decision_id != binding.risk_decision_id:
            raise ValueError("operator context OMS handoff risk decision mismatch")
        if external_handoff.handoff_id != approval.approval_hash:
            raise ValueError("operator context OMS handoff/canary approval mismatch")
        if not approval.issued_at <= external_handoff.authorized_at < approval.expires_at:
            raise ValueError("operator context OMS handoff outside canary approval window")

        raw = {
            "environment": "PAPER",
            "account_attestation_fingerprint": account_fp,
            "order_id": binding.order_id,
            "client_order_id": binding.client_order_id,
            "binding_hash": binding.fingerprint,
            "bracket_payload_hash": expected_bracket.payload_hash,
            "canary_approval_hash": approval.approval_hash,
            "oms_handoff_hash": external_handoff.handoff_hash,
            "notional": str(approval.notional),
            "attempt_id": attempt_id,
        }
        return cls(
            environment="PAPER",
            account_attestation_fingerprint=account_fp,
            order_id=binding.order_id,
            client_order_id=binding.client_order_id,
            binding_hash=binding.fingerprint,
            bracket_payload_hash=expected_bracket.payload_hash,
            canary_approval_hash=approval.approval_hash,
            oms_handoff_hash=external_handoff.handoff_hash,
            notional=approval.notional,
            attempt_id=attempt_id,
            preparation_hash=_hash_json(raw),
        )

    def to_dict(self) -> dict[str, object]:
        payload = _context_payload_without_hash(self)
        payload["preparation_hash"] = self.preparation_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "PaperOperatorDecisionContext":
        expected = {
            "environment",
            "account_attestation_fingerprint",
            "order_id",
            "client_order_id",
            "binding_hash",
            "bracket_payload_hash",
            "canary_approval_hash",
            "oms_handoff_hash",
            "notional",
            "attempt_id",
            "preparation_hash",
        }
        if set(payload) != expected:
            raise ValueError("operator decision context payload is non-canonical")
        return cls(
            environment=_required_str(payload, "environment"),
            account_attestation_fingerprint=_required_str(payload, "account_attestation_fingerprint"),
            order_id=_required_str(payload, "order_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            binding_hash=_required_str(payload, "binding_hash"),
            bracket_payload_hash=_required_str(payload, "bracket_payload_hash"),
            canary_approval_hash=_required_str(payload, "canary_approval_hash"),
            oms_handoff_hash=_required_str(payload, "oms_handoff_hash"),
            notional=_decimal(payload.get("notional"), "notional"),
            attempt_id=_required_str(payload, "attempt_id"),
            preparation_hash=_required_str(payload, "preparation_hash"),
        )


@dataclass(frozen=True, slots=True)
class PaperOperatorDecision:
    context: PaperOperatorDecisionContext
    operator_id: str
    source: str
    action: str
    issued_at: datetime
    expires_at: datetime
    decision_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.context, PaperOperatorDecisionContext):
            raise ValueError("operator decision context is required")
        _validate_id(self.operator_id, "operator_id")
        if self.source != _OPERATOR_SOURCE:
            raise ValueError("operator decision source must be HUMAN_OPERATOR")
        if self.action != _OPERATOR_ACTION:
            raise ValueError("operator decision action is not the exact PAPER canary approval action")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        issued = self.issued_at.astimezone(timezone.utc)
        expires = self.expires_at.astimezone(timezone.utc)
        if expires <= issued or expires - issued > _MAX_DECISION_TTL:
            raise ValueError("operator decision validity window must be >0 and <=2 minutes")
        _validate_hash(self.decision_hash, "decision_hash")
        if self.decision_hash != _hash_json(_decision_payload_without_hash(self)):
            raise ValueError("operator decision hash mismatch")

    def is_valid_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        return self.issued_at.astimezone(timezone.utc) <= instant < self.expires_at.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class PaperOperatorDecisionState:
    decision: PaperOperatorDecision
    status: PaperOperatorDecisionStatus
    consumed_at: datetime | None
    consumed_attempt_id: str | None
    event_sequence: int
    event_hash: str


@dataclass(frozen=True, slots=True)
class _DecisionEvent:
    sequence: int
    event_type: PaperOperatorDecisionEventType
    preparation_hash: str
    occurred_at: datetime
    payload: Mapping[str, object]
    previous_event_hash: str
    event_hash: str


class SQLitePaperOperatorDecisionRegistry:
    """Tamper-evident one-shot registry for explicit human PAPER decisions.

    This class has no network, OMS or broker APIs. Production issuance is
    intentionally separated from the writer; the permanent R6 checker permits
    record_operator_approval calls only from the dedicated interactive operator
    script. Tests may call the method directly to verify invariants.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpaca_paper_operator_decision_events (
                    sequence INTEGER PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    preparation_hash TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS alpaca_paper_operator_decision_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    event_sequence INTEGER NOT NULL CHECK(event_sequence >= 0),
                    event_head_hash TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                """
            )
            row = conn.execute(
                "SELECT event_sequence, event_head_hash, control_hash FROM alpaca_paper_operator_decision_control WHERE singleton = 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO alpaca_paper_operator_decision_control(singleton, event_sequence, event_head_hash, control_hash) VALUES (1, 0, ?, ?)",
                    (_GENESIS_HASH, _control_hash(0, _GENESIS_HASH)),
                )
        finally:
            conn.close()

    def record_operator_approval(
        self,
        *,
        context: PaperOperatorDecisionContext,
        operator_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> PaperOperatorDecisionState:
        if not isinstance(context, PaperOperatorDecisionContext):
            raise TypeError("operator decision context is required")
        _validate_id(operator_id, "operator_id")
        decision = _build_decision(
            context=context,
            operator_id=operator_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            states, sequence, head = self._verify_locked(conn)
            existing = states.get(context.preparation_hash)
            if existing is not None:
                if existing.decision == decision:
                    conn.execute("COMMIT")
                    return existing
                raise PaperOperatorDecisionConflict(
                    "prepared PAPER canary already has different operator-decision evidence"
                )
            event = _make_event(
                sequence=sequence + 1,
                event_type=PaperOperatorDecisionEventType.ISSUED,
                preparation_hash=context.preparation_hash,
                occurred_at=decision.issued_at,
                payload=_decision_payload(decision),
                previous_event_hash=head,
            )
            _insert_event(conn, event)
            _update_control(conn, event.sequence, event.event_hash)
            states, _, _ = self._verify_locked(conn)
            state = states[context.preparation_hash]
            conn.execute("COMMIT")
            return state
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def consume(
        self,
        *,
        decision: PaperOperatorDecision,
        attempt_id: str,
        now: datetime,
    ) -> PaperOperatorDecisionState:
        if not isinstance(decision, PaperOperatorDecision):
            raise TypeError("operator decision is required")
        _validate_id(attempt_id, "attempt_id")
        _require_aware(now, "now")
        if decision.context.attempt_id != attempt_id:
            raise PaperOperatorDecisionConflict("operator decision is bound to another attempt")
        instant = now.astimezone(timezone.utc)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            states, sequence, head = self._verify_locked(conn)
            state = states.get(decision.context.preparation_hash)
            if state is None:
                raise KeyError(decision.context.preparation_hash)
            if state.decision != decision:
                raise PaperOperatorDecisionConflict("durable operator decision does not match supplied decision")
            if state.status is PaperOperatorDecisionStatus.CONSUMED:
                if state.consumed_attempt_id == attempt_id:
                    conn.execute("COMMIT")
                    return state
                raise PaperOperatorDecisionConflict("operator decision already consumed by another attempt")
            if not decision.is_valid_at(instant):
                raise PaperOperatorDecisionExpired("operator decision is expired or not yet valid")
            event = _make_event(
                sequence=sequence + 1,
                event_type=PaperOperatorDecisionEventType.CONSUMED,
                preparation_hash=decision.context.preparation_hash,
                occurred_at=instant,
                payload={
                    "decision_hash": decision.decision_hash,
                    "attempt_id": attempt_id,
                },
                previous_event_hash=head,
            )
            _insert_event(conn, event)
            _update_control(conn, event.sequence, event.event_hash)
            states, _, _ = self._verify_locked(conn)
            consumed = states[decision.context.preparation_hash]
            conn.execute("COMMIT")
            return consumed
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, preparation_hash: str) -> PaperOperatorDecisionState:
        _validate_hash(preparation_hash, "preparation_hash")
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            if preparation_hash not in states:
                raise KeyError(preparation_hash)
            return states[preparation_hash]
        finally:
            conn.close()

    def _verify_locked(
        self, conn: sqlite3.Connection
    ) -> tuple[dict[str, PaperOperatorDecisionState], int, str]:
        control = conn.execute(
            "SELECT event_sequence, event_head_hash, control_hash FROM alpaca_paper_operator_decision_control WHERE singleton = 1"
        ).fetchone()
        if control is None:
            raise PaperOperatorDecisionIntegrityError("operator decision control anchor is missing")
        sequence = _strict_int(control["event_sequence"], "event_sequence")
        head = str(control["event_head_hash"])
        _validate_hash(head, "event_head_hash")
        if str(control["control_hash"]) != _control_hash(sequence, head):
            raise PaperOperatorDecisionIntegrityError("operator decision control hash mismatch")
        rows = conn.execute(
            "SELECT sequence, event_type, preparation_hash, occurred_at, payload_json, previous_event_hash, event_hash FROM alpaca_paper_operator_decision_events ORDER BY sequence"
        ).fetchall()
        if len(rows) != sequence:
            raise PaperOperatorDecisionIntegrityError("operator decision event count does not match anchor")

        states: dict[str, PaperOperatorDecisionState] = {}
        previous = _GENESIS_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            event = _event_from_row(row)
            if event.sequence != expected_sequence:
                raise PaperOperatorDecisionIntegrityError("operator decision event sequence gap/reordering")
            if event.previous_event_hash != previous:
                raise PaperOperatorDecisionIntegrityError("operator decision previous-hash mismatch")
            if event.event_type is PaperOperatorDecisionEventType.ISSUED:
                if event.preparation_hash in states:
                    raise PaperOperatorDecisionIntegrityError("operator decision issued more than once")
                decision = _decision_from_payload(event.payload)
                if decision.context.preparation_hash != event.preparation_hash:
                    raise PaperOperatorDecisionIntegrityError("operator decision preparation hash mismatch")
                if decision.issued_at != event.occurred_at:
                    raise PaperOperatorDecisionIntegrityError("operator decision issue timestamp mismatch")
                states[event.preparation_hash] = PaperOperatorDecisionState(
                    decision=decision,
                    status=PaperOperatorDecisionStatus.ISSUED,
                    consumed_at=None,
                    consumed_attempt_id=None,
                    event_sequence=event.sequence,
                    event_hash=event.event_hash,
                )
            else:
                state = states.get(event.preparation_hash)
                if state is None:
                    raise PaperOperatorDecisionIntegrityError("operator decision consumption precedes issuance")
                if state.status is PaperOperatorDecisionStatus.CONSUMED:
                    raise PaperOperatorDecisionIntegrityError("operator decision consumed more than once")
                if set(event.payload) != {"decision_hash", "attempt_id"}:
                    raise PaperOperatorDecisionIntegrityError("operator decision consumption payload is non-canonical")
                if event.payload.get("decision_hash") != state.decision.decision_hash:
                    raise PaperOperatorDecisionIntegrityError("operator decision consumption hash mismatch")
                attempt_id = _required_str(event.payload, "attempt_id")
                if attempt_id != state.decision.context.attempt_id:
                    raise PaperOperatorDecisionIntegrityError("operator decision consumed by unexpected attempt")
                if not state.decision.is_valid_at(event.occurred_at):
                    raise PaperOperatorDecisionIntegrityError("persisted operator decision consumption outside validity")
                states[event.preparation_hash] = PaperOperatorDecisionState(
                    decision=state.decision,
                    status=PaperOperatorDecisionStatus.CONSUMED,
                    consumed_at=event.occurred_at,
                    consumed_attempt_id=attempt_id,
                    event_sequence=event.sequence,
                    event_hash=event.event_hash,
                )
            previous = event.event_hash
        if sequence == 0:
            if head != _GENESIS_HASH:
                raise PaperOperatorDecisionIntegrityError("empty operator decision ledger has non-genesis head")
        elif previous != head:
            raise PaperOperatorDecisionIntegrityError("operator decision anchored head mismatch")
        return states, sequence, head


def operator_confirmation_challenge(context: PaperOperatorDecisionContext) -> str:
    if not isinstance(context, PaperOperatorDecisionContext):
        raise TypeError("operator decision context is required")
    return f"APPROVE PAPER {context.preparation_hash[:12]}"


def _build_decision(
    *,
    context: PaperOperatorDecisionContext,
    operator_id: str,
    issued_at: datetime,
    expires_at: datetime,
) -> PaperOperatorDecision:
    _require_aware(issued_at, "issued_at")
    _require_aware(expires_at, "expires_at")
    provisional = {
        "context": context.to_dict(),
        "operator_id": operator_id,
        "source": _OPERATOR_SOURCE,
        "action": _OPERATOR_ACTION,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
    }
    return PaperOperatorDecision(
        context=context,
        operator_id=operator_id,
        source=_OPERATOR_SOURCE,
        action=_OPERATOR_ACTION,
        issued_at=issued_at.astimezone(timezone.utc),
        expires_at=expires_at.astimezone(timezone.utc),
        decision_hash=_hash_json(provisional),
    )


def _context_payload_without_hash(context: PaperOperatorDecisionContext) -> dict[str, object]:
    return {
        "environment": context.environment,
        "account_attestation_fingerprint": context.account_attestation_fingerprint,
        "order_id": context.order_id,
        "client_order_id": context.client_order_id,
        "binding_hash": context.binding_hash,
        "bracket_payload_hash": context.bracket_payload_hash,
        "canary_approval_hash": context.canary_approval_hash,
        "oms_handoff_hash": context.oms_handoff_hash,
        "notional": str(context.notional),
        "attempt_id": context.attempt_id,
    }


def _decision_payload_without_hash(decision: PaperOperatorDecision) -> dict[str, object]:
    return {
        "context": decision.context.to_dict(),
        "operator_id": decision.operator_id,
        "source": decision.source,
        "action": decision.action,
        "issued_at": _iso(decision.issued_at),
        "expires_at": _iso(decision.expires_at),
    }


def _decision_payload(decision: PaperOperatorDecision) -> dict[str, object]:
    payload = _decision_payload_without_hash(decision)
    payload["decision_hash"] = decision.decision_hash
    return payload


def _decision_from_payload(payload: Mapping[str, object]) -> PaperOperatorDecision:
    expected = {"context", "operator_id", "source", "action", "issued_at", "expires_at", "decision_hash"}
    if set(payload) != expected:
        raise PaperOperatorDecisionIntegrityError("operator decision issuance payload is non-canonical")
    context_raw = payload.get("context")
    if not isinstance(context_raw, dict):
        raise PaperOperatorDecisionIntegrityError("operator decision context payload must be object")
    try:
        return PaperOperatorDecision(
            context=PaperOperatorDecisionContext.from_dict(context_raw),
            operator_id=_required_str(payload, "operator_id"),
            source=_required_str(payload, "source"),
            action=_required_str(payload, "action"),
            issued_at=_datetime(payload.get("issued_at"), "issued_at"),
            expires_at=_datetime(payload.get("expires_at"), "expires_at"),
            decision_hash=_required_str(payload, "decision_hash"),
        )
    except (TypeError, ValueError) as exc:
        raise PaperOperatorDecisionIntegrityError("invalid persisted operator decision") from exc


def _make_event(
    *,
    sequence: int,
    event_type: PaperOperatorDecisionEventType,
    preparation_hash: str,
    occurred_at: datetime,
    payload: Mapping[str, object],
    previous_event_hash: str,
) -> _DecisionEvent:
    if sequence <= 0:
        raise ValueError("operator decision event sequence must be positive")
    _validate_hash(preparation_hash, "preparation_hash")
    _validate_hash(previous_event_hash, "previous_event_hash")
    _require_aware(occurred_at, "occurred_at")
    canonical = _strict_mapping(payload)
    event_hash = _hash_json(
        {
            "sequence": sequence,
            "event_type": event_type.value,
            "preparation_hash": preparation_hash,
            "occurred_at": _iso(occurred_at),
            "payload": canonical,
            "previous_event_hash": previous_event_hash,
        }
    )
    return _DecisionEvent(
        sequence=sequence,
        event_type=event_type,
        preparation_hash=preparation_hash,
        occurred_at=occurred_at.astimezone(timezone.utc),
        payload=canonical,
        previous_event_hash=previous_event_hash,
        event_hash=event_hash,
    )


def _insert_event(conn: sqlite3.Connection, event: _DecisionEvent) -> None:
    conn.execute(
        "INSERT INTO alpaca_paper_operator_decision_events(sequence, event_type, preparation_hash, occurred_at, payload_json, previous_event_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event.sequence,
            event.event_type.value,
            event.preparation_hash,
            _iso(event.occurred_at),
            _canonical_json(event.payload),
            event.previous_event_hash,
            event.event_hash,
        ),
    )


def _event_from_row(row: sqlite3.Row) -> _DecisionEvent:
    try:
        expected = _make_event(
            sequence=_strict_int(row["sequence"], "sequence"),
            event_type=PaperOperatorDecisionEventType(str(row["event_type"])),
            preparation_hash=str(row["preparation_hash"]),
            occurred_at=_datetime(row["occurred_at"], "occurred_at"),
            payload=_strict_json_object(str(row["payload_json"])),
            previous_event_hash=str(row["previous_event_hash"]),
        )
    except (TypeError, ValueError) as exc:
        raise PaperOperatorDecisionIntegrityError("invalid persisted operator decision event") from exc
    if str(row["event_hash"]) != expected.event_hash:
        raise PaperOperatorDecisionIntegrityError("operator decision event hash mismatch")
    if str(row["occurred_at"]) != _iso(expected.occurred_at):
        raise PaperOperatorDecisionIntegrityError("operator decision timestamp is non-canonical")
    if str(row["payload_json"]) != _canonical_json(expected.payload):
        raise PaperOperatorDecisionIntegrityError("operator decision payload is non-canonical")
    return expected


def _update_control(conn: sqlite3.Connection, sequence: int, head: str) -> None:
    cursor = conn.execute(
        "UPDATE alpaca_paper_operator_decision_control SET event_sequence = ?, event_head_hash = ?, control_hash = ? WHERE singleton = 1",
        (sequence, head, _control_hash(sequence, head)),
    )
    if cursor.rowcount != 1:
        raise PaperOperatorDecisionIntegrityError("operator decision control update failed")


def _control_hash(sequence: int, head: str) -> str:
    return _hash_json({"event_sequence": sequence, "event_head_hash": head})


def _strict_mapping(payload: Mapping[str, object]) -> Mapping[str, object]:
    return _strict_json_object(_canonical_json(payload))


def _strict_json_object(raw: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw, parse_constant=lambda token: _raise_constant(token))
    except (json.JSONDecodeError, ValueError) as exc:
        raise PaperOperatorDecisionIntegrityError("operator decision JSON is invalid") from exc
    if not isinstance(value, dict):
        raise PaperOperatorDecisionIntegrityError("operator decision JSON root must be object")
    return value


def _raise_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    return parsed


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    _require_aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be integer")
    return value


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be canonical text <=128 chars")


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _iso(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_json(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
