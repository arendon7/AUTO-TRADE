from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3
from typing import Mapping

from autotrade.persistence import SQLiteRuntime

from .alpaca_paper_canary import PaperCanaryApproval


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_GENESIS_HASH = "0" * 64


class PaperCanaryPermitError(RuntimeError):
    pass


class PaperCanaryPermitIntegrityError(PaperCanaryPermitError):
    pass


class PaperCanaryPermitConflict(PaperCanaryPermitError):
    pass


class PaperCanaryPermitExpired(PaperCanaryPermitError):
    pass


class PaperCanaryPermitStatus(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"


class PaperCanaryPermitEventType(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"


@dataclass(frozen=True, slots=True)
class PaperCanaryPermitState:
    approval_hash: str
    order_id: str
    client_order_id: str
    binding_hash: str
    status: PaperCanaryPermitStatus
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    attempt_id: str | None
    event_sequence: int
    event_hash: str


@dataclass(frozen=True, slots=True)
class _PermitEvent:
    sequence: int
    event_type: PaperCanaryPermitEventType
    approval_hash: str
    occurred_at: datetime
    payload: Mapping[str, object]
    previous_event_hash: str
    event_hash: str


class SQLitePaperCanaryPermitRegistry:
    """Append-only global ledger for one-shot external PAPER canary approvals.

    The approval itself is short-lived and hash-bound. Issuance and consumption
    are persisted as a single global SHA-256 event chain with a separate control
    anchor. A consumed approval can never be re-used with a different attempt.
    Tail deletion, event mutation, sequence gaps or control mutation fail closed.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpaca_paper_canary_permit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    approval_hash TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS alpaca_paper_canary_permit_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    event_sequence INTEGER NOT NULL CHECK(event_sequence >= 0),
                    event_head_hash TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                """
            )
            row = conn.execute(
                "SELECT event_sequence, event_head_hash, control_hash FROM alpaca_paper_canary_permit_control WHERE singleton = 1"
            ).fetchone()
            if row is None:
                control_hash = _control_hash(0, _GENESIS_HASH)
                conn.execute(
                    """
                    INSERT INTO alpaca_paper_canary_permit_control(
                        singleton, event_sequence, event_head_hash, control_hash
                    ) VALUES (1, 0, ?, ?)
                    """,
                    (_GENESIS_HASH, control_hash),
                )
        finally:
            conn.close()

    def issue(self, approval: PaperCanaryApproval) -> PaperCanaryPermitState:
        _validate_approval(approval)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            states, sequence, head = self._verify_locked(conn)
            existing = states.get(approval.approval_hash)
            if existing is not None:
                if _state_matches_approval(existing, approval):
                    conn.execute("COMMIT")
                    return existing
                raise PaperCanaryPermitConflict(
                    "approval_hash is already bound to different permit evidence"
                )

            event = _make_event(
                sequence=sequence + 1,
                event_type=PaperCanaryPermitEventType.ISSUED,
                approval_hash=approval.approval_hash,
                occurred_at=approval.issued_at,
                payload=_approval_payload(approval),
                previous_event_hash=head,
            )
            _insert_event(conn, event)
            _update_control(conn, event.sequence, event.event_hash)
            states, _, _ = self._verify_locked(conn)
            state = states[approval.approval_hash]
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
        approval: PaperCanaryApproval,
        attempt_id: str,
        now: datetime,
    ) -> PaperCanaryPermitState:
        _validate_approval(approval)
        _validate_id(attempt_id, "attempt_id")
        _require_aware(now, "now")
        now_utc = now.astimezone(timezone.utc)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            states, sequence, head = self._verify_locked(conn)
            state = states.get(approval.approval_hash)
            if state is None:
                raise KeyError(approval.approval_hash)
            if not _state_matches_approval(state, approval):
                raise PaperCanaryPermitConflict(
                    "durable permit no longer matches supplied approval"
                )
            if state.status is PaperCanaryPermitStatus.CONSUMED:
                if state.attempt_id == attempt_id:
                    conn.execute("COMMIT")
                    return state
                raise PaperCanaryPermitConflict(
                    "PAPER canary permit is already consumed by another attempt"
                )
            if now_utc < approval.issued_at.astimezone(timezone.utc):
                raise PaperCanaryPermitConflict("PAPER canary permit cannot be consumed before issuance")
            if now_utc >= approval.expires_at.astimezone(timezone.utc):
                raise PaperCanaryPermitExpired("PAPER canary permit has expired")

            event = _make_event(
                sequence=sequence + 1,
                event_type=PaperCanaryPermitEventType.CONSUMED,
                approval_hash=approval.approval_hash,
                occurred_at=now_utc,
                payload={
                    "approval_hash": approval.approval_hash,
                    "attempt_id": attempt_id,
                },
                previous_event_hash=head,
            )
            _insert_event(conn, event)
            _update_control(conn, event.sequence, event.event_hash)
            states, _, _ = self._verify_locked(conn)
            consumed = states[approval.approval_hash]
            conn.execute("COMMIT")
            return consumed
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, approval_hash: str) -> PaperCanaryPermitState:
        _validate_hash(approval_hash, "approval_hash")
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            if approval_hash not in states:
                raise KeyError(approval_hash)
            return states[approval_hash]
        finally:
            conn.close()

    def get_issued_event_hash(self, approval_hash: str) -> str:
        """Return the immutable, verified ISSUED-event hash for one permit.

        PaperCanaryPermitState.event_hash tracks the latest event and therefore
        changes from ISSUED to CONSUMED. Prepared canary packages must bind the
        immutable issuance evidence so the same-attempt crash-safe resume can
        verify the original permit without mistaking the later CONSUMED event
        for tampering. The entire ledger/control chain is verified first.
        """
        _validate_hash(approval_hash, "approval_hash")
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            if approval_hash not in states:
                raise KeyError(approval_hash)
            rows = conn.execute(
                """
                SELECT sequence, event_type, approval_hash, occurred_at,
                       payload_json, previous_event_hash, event_hash
                FROM alpaca_paper_canary_permit_events
                WHERE approval_hash = ? AND event_type = ?
                ORDER BY sequence
                """,
                (approval_hash, PaperCanaryPermitEventType.ISSUED.value),
            ).fetchall()
            if len(rows) != 1:
                raise PaperCanaryPermitIntegrityError(
                    "canary permit must have exactly one verified issuance event"
                )
            event = _event_from_row(rows[0])
            if (
                event.event_type is not PaperCanaryPermitEventType.ISSUED
                or event.approval_hash != approval_hash
            ):
                raise PaperCanaryPermitIntegrityError(
                    "canary permit issuance event identity mismatch"
                )
            return event.event_hash
        finally:
            conn.close()

    def list_states(self) -> tuple[PaperCanaryPermitState, ...]:
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            return tuple(states[key] for key in sorted(states))
        finally:
            conn.close()

    def _verify_locked(
        self,
        conn: sqlite3.Connection,
    ) -> tuple[dict[str, PaperCanaryPermitState], int, str]:
        control = conn.execute(
            """
            SELECT event_sequence, event_head_hash, control_hash
            FROM alpaca_paper_canary_permit_control WHERE singleton = 1
            """
        ).fetchone()
        if control is None:
            raise PaperCanaryPermitIntegrityError("canary permit control anchor is missing")
        sequence = _strict_int(control["event_sequence"], "event_sequence")
        head = str(control["event_head_hash"])
        _validate_hash(head, "event_head_hash")
        if str(control["control_hash"]) != _control_hash(sequence, head):
            raise PaperCanaryPermitIntegrityError("canary permit control hash mismatch")

        rows = conn.execute(
            """
            SELECT sequence, event_type, approval_hash, occurred_at,
                   payload_json, previous_event_hash, event_hash
            FROM alpaca_paper_canary_permit_events ORDER BY sequence
            """
        ).fetchall()
        if len(rows) != sequence:
            raise PaperCanaryPermitIntegrityError(
                "canary permit event count does not match anchored sequence"
            )

        states: dict[str, PaperCanaryPermitState] = {}
        previous = _GENESIS_HASH
        expected_sequence = 1
        for row in rows:
            event = _event_from_row(row)
            if event.sequence != expected_sequence:
                raise PaperCanaryPermitIntegrityError(
                    "canary permit event sequence gap or reordering detected"
                )
            if event.previous_event_hash != previous:
                raise PaperCanaryPermitIntegrityError(
                    "canary permit previous-hash linkage mismatch"
                )
            if event.event_type is PaperCanaryPermitEventType.ISSUED:
                if event.approval_hash in states:
                    raise PaperCanaryPermitIntegrityError(
                        "canary permit approval was issued more than once"
                    )
                approval = _approval_from_payload(event.payload)
                if approval.approval_hash != event.approval_hash:
                    raise PaperCanaryPermitIntegrityError(
                        "canary permit event approval hash mismatch"
                    )
                if event.occurred_at != approval.issued_at:
                    raise PaperCanaryPermitIntegrityError(
                        "canary permit issuance timestamp mismatch"
                    )
                states[event.approval_hash] = PaperCanaryPermitState(
                    approval_hash=approval.approval_hash,
                    order_id=approval.order_id,
                    client_order_id=approval.client_order_id,
                    binding_hash=approval.binding_hash,
                    status=PaperCanaryPermitStatus.ISSUED,
                    issued_at=approval.issued_at,
                    expires_at=approval.expires_at,
                    consumed_at=None,
                    attempt_id=None,
                    event_sequence=event.sequence,
                    event_hash=event.event_hash,
                )
            elif event.event_type is PaperCanaryPermitEventType.CONSUMED:
                state = states.get(event.approval_hash)
                if state is None:
                    raise PaperCanaryPermitIntegrityError(
                        "canary permit consumption precedes issuance"
                    )
                if state.status is PaperCanaryPermitStatus.CONSUMED:
                    raise PaperCanaryPermitIntegrityError(
                        "canary permit was consumed more than once"
                    )
                attempt_id = _required_str(event.payload, "attempt_id")
                _validate_id(attempt_id, "attempt_id")
                if event.payload.get("approval_hash") != state.approval_hash:
                    raise PaperCanaryPermitIntegrityError(
                        "canary permit consumption approval hash mismatch"
                    )
                if set(event.payload) != {"approval_hash", "attempt_id"}:
                    raise PaperCanaryPermitIntegrityError(
                        "canary permit consumption payload is non-canonical"
                    )
                if event.occurred_at < state.issued_at or event.occurred_at >= state.expires_at:
                    raise PaperCanaryPermitIntegrityError(
                        "persisted canary permit consumption is outside validity window"
                    )
                states[event.approval_hash] = PaperCanaryPermitState(
                    approval_hash=state.approval_hash,
                    order_id=state.order_id,
                    client_order_id=state.client_order_id,
                    binding_hash=state.binding_hash,
                    status=PaperCanaryPermitStatus.CONSUMED,
                    issued_at=state.issued_at,
                    expires_at=state.expires_at,
                    consumed_at=event.occurred_at,
                    attempt_id=attempt_id,
                    event_sequence=event.sequence,
                    event_hash=event.event_hash,
                )
            expected_sequence += 1
            previous = event.event_hash

        if sequence == 0:
            if head != _GENESIS_HASH:
                raise PaperCanaryPermitIntegrityError(
                    "empty canary permit ledger has non-genesis head"
                )
        elif previous != head:
            raise PaperCanaryPermitIntegrityError(
                "canary permit anchored head does not match event chain tail"
            )
        return states, sequence, head


def _approval_payload(approval: PaperCanaryApproval) -> dict[str, object]:
    return {
        "account_attestation_fingerprint": approval.account_attestation_fingerprint,
        "approval_hash": approval.approval_hash,
        "binding_hash": approval.binding_hash,
        "client_order_id": approval.client_order_id,
        "effective_notional_cap": str(approval.effective_notional_cap),
        "expires_at": _iso(approval.expires_at),
        "issued_at": _iso(approval.issued_at),
        "notional": str(approval.notional),
        "order_id": approval.order_id,
        "risk_decision_id": approval.risk_decision_id,
    }


def _approval_from_payload(payload: Mapping[str, object]) -> PaperCanaryApproval:
    expected_keys = {
        "account_attestation_fingerprint",
        "approval_hash",
        "binding_hash",
        "client_order_id",
        "effective_notional_cap",
        "expires_at",
        "issued_at",
        "notional",
        "order_id",
        "risk_decision_id",
    }
    if set(payload) != expected_keys:
        raise PaperCanaryPermitIntegrityError("canary approval payload is non-canonical")
    try:
        approval = PaperCanaryApproval(
            order_id=_required_str(payload, "order_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            binding_hash=_required_str(payload, "binding_hash"),
            account_attestation_fingerprint=_required_str(
                payload, "account_attestation_fingerprint"
            ),
            risk_decision_id=_required_str(payload, "risk_decision_id"),
            notional=_decimal(payload.get("notional"), "notional"),
            effective_notional_cap=_decimal(
                payload.get("effective_notional_cap"), "effective_notional_cap"
            ),
            issued_at=_datetime(payload.get("issued_at"), "issued_at"),
            expires_at=_datetime(payload.get("expires_at"), "expires_at"),
            approval_hash=_required_str(payload, "approval_hash"),
        )
    except ValueError as exc:
        raise PaperCanaryPermitIntegrityError("invalid persisted canary approval") from exc
    _validate_approval(approval)
    return approval


def _validate_approval(approval: PaperCanaryApproval) -> None:
    _validate_id(approval.order_id, "order_id")
    _validate_id(approval.client_order_id, "client_order_id")
    _validate_hash(approval.binding_hash, "binding_hash")
    _validate_hash(
        approval.account_attestation_fingerprint,
        "account_attestation_fingerprint",
    )
    _validate_id(approval.risk_decision_id, "risk_decision_id")
    _validate_hash(approval.approval_hash, "approval_hash")
    _require_aware(approval.issued_at, "issued_at")
    _require_aware(approval.expires_at, "expires_at")
    if approval.expires_at <= approval.issued_at:
        raise ValueError("canary approval expires_at must be after issued_at")
    if not approval.notional.is_finite() or approval.notional <= 0:
        raise ValueError("canary approval notional must be finite and positive")
    if (
        not approval.effective_notional_cap.is_finite()
        or approval.effective_notional_cap <= 0
        or approval.notional > approval.effective_notional_cap
    ):
        raise ValueError("canary approval effective cap is invalid")
    expected = sha256(
        json.dumps(
            {
                "account_attestation_fingerprint": approval.account_attestation_fingerprint,
                "binding_hash": approval.binding_hash,
                "client_order_id": approval.client_order_id,
                "effective_notional_cap": str(approval.effective_notional_cap),
                "expires_at": _iso(approval.expires_at),
                "issued_at": _iso(approval.issued_at),
                "notional": str(approval.notional),
                "order_id": approval.order_id,
                "risk_decision_id": approval.risk_decision_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if approval.approval_hash != expected:
        raise ValueError("canary approval hash does not match canonical approval evidence")


def _state_matches_approval(
    state: PaperCanaryPermitState, approval: PaperCanaryApproval
) -> bool:
    return (
        state.approval_hash == approval.approval_hash
        and state.order_id == approval.order_id
        and state.client_order_id == approval.client_order_id
        and state.binding_hash == approval.binding_hash
        and state.issued_at == approval.issued_at.astimezone(timezone.utc)
        and state.expires_at == approval.expires_at.astimezone(timezone.utc)
    )


def _make_event(
    *,
    sequence: int,
    event_type: PaperCanaryPermitEventType,
    approval_hash: str,
    occurred_at: datetime,
    payload: Mapping[str, object],
    previous_event_hash: str,
) -> _PermitEvent:
    _require_aware(occurred_at, "occurred_at")
    if sequence <= 0:
        raise ValueError("event sequence must be positive")
    _validate_hash(approval_hash, "approval_hash")
    _validate_hash(previous_event_hash, "previous_event_hash")
    canonical = _strict_mapping(payload)
    event_hash = _hash_json(
        {
            "approval_hash": approval_hash,
            "event_type": event_type.value,
            "occurred_at": _iso(occurred_at),
            "payload": canonical,
            "previous_event_hash": previous_event_hash,
            "sequence": sequence,
        }
    )
    return _PermitEvent(
        sequence=sequence,
        event_type=event_type,
        approval_hash=approval_hash,
        occurred_at=occurred_at.astimezone(timezone.utc),
        payload=canonical,
        previous_event_hash=previous_event_hash,
        event_hash=event_hash,
    )


def _insert_event(conn: sqlite3.Connection, event: _PermitEvent) -> None:
    conn.execute(
        """
        INSERT INTO alpaca_paper_canary_permit_events(
            sequence, event_type, approval_hash, occurred_at, payload_json,
            previous_event_hash, event_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.sequence,
            event.event_type.value,
            event.approval_hash,
            _iso(event.occurred_at),
            _canonical_json(event.payload),
            event.previous_event_hash,
            event.event_hash,
        ),
    )


def _event_from_row(row: sqlite3.Row) -> _PermitEvent:
    try:
        event_type = PaperCanaryPermitEventType(str(row["event_type"]))
        sequence = _strict_int(row["sequence"], "sequence")
        approval_hash = str(row["approval_hash"])
        occurred_at = _datetime(row["occurred_at"], "occurred_at")
        payload = _strict_json_object(str(row["payload_json"]))
        previous_event_hash = str(row["previous_event_hash"])
        event_hash = str(row["event_hash"])
        expected = _make_event(
            sequence=sequence,
            event_type=event_type,
            approval_hash=approval_hash,
            occurred_at=occurred_at,
            payload=payload,
            previous_event_hash=previous_event_hash,
        )
    except (ValueError, TypeError) as exc:
        raise PaperCanaryPermitIntegrityError("invalid persisted canary permit event") from exc
    if event_hash != expected.event_hash:
        raise PaperCanaryPermitIntegrityError("canary permit event hash mismatch")
    if str(row["occurred_at"]) != _iso(expected.occurred_at):
        raise PaperCanaryPermitIntegrityError("canary permit event timestamp is non-canonical")
    if str(row["payload_json"]) != _canonical_json(expected.payload):
        raise PaperCanaryPermitIntegrityError("canary permit event payload is non-canonical")
    return expected


def _update_control(conn: sqlite3.Connection, sequence: int, head: str) -> None:
    cursor = conn.execute(
        """
        UPDATE alpaca_paper_canary_permit_control
        SET event_sequence = ?, event_head_hash = ?, control_hash = ?
        WHERE singleton = 1
        """,
        (sequence, head, _control_hash(sequence, head)),
    )
    if cursor.rowcount != 1:
        raise PaperCanaryPermitIntegrityError("canary permit control update failed")


def _control_hash(sequence: int, head: str) -> str:
    return _hash_json({"event_head_hash": head, "event_sequence": sequence})


def _strict_mapping(payload: Mapping[str, object]) -> Mapping[str, object]:
    return _strict_json_object(_canonical_json(payload))


def _strict_json_object(raw: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw, parse_constant=lambda token: _raise_json_constant(token))
    except (json.JSONDecodeError, ValueError) as exc:
        raise PaperCanaryPermitIntegrityError("canary permit JSON is invalid") from exc
    if not isinstance(value, dict):
        raise PaperCanaryPermitIntegrityError("canary permit JSON root must be object")
    return value


def _raise_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _decimal(value: object, label: str):
    from decimal import Decimal, InvalidOperation

    if not isinstance(value, str):
        raise ValueError(f"{label} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} is invalid") from exc
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


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_json(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be canonical text <=128 characters")


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256 hex")
