from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3

from autotrade.paper_close_plan import PaperCryptoClosePlan
from autotrade.persistence import SQLiteRuntime


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_GENESIS = "0" * 64
_OPEN_STATUSES = {"accepted", "pending_new", "new", "partially_filled"}
_TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected"}


class PaperCloseLifecycleError(RuntimeError):
    pass


class PaperCloseLifecycleConflict(PaperCloseLifecycleError):
    pass


class PaperCloseLifecycleIntegrityError(PaperCloseLifecycleError):
    pass


class PaperCloseLifecycleBlocked(PaperCloseLifecycleError):
    pass


class PaperCloseLifecycleStatus(StrEnum):
    PREPARED = "PREPARED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    TERMINAL_RECONCILED = "TERMINAL_RECONCILED"
    FLAT_RECONCILED = "FLAT_RECONCILED"
    HALTED_RECONCILIATION_REQUIRED = "HALTED_RECONCILIATION_REQUIRED"


class PaperCloseLifecycleEventType(StrEnum):
    PREPARED = "PREPARED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    RECONCILED = "RECONCILED"
    HALTED = "HALTED"


@dataclass(frozen=True, slots=True)
class PaperCloseLifecycleState:
    attempt_id: str
    plan_hash: str
    symbol: str
    requested_quantity: Decimal
    status: PaperCloseLifecycleStatus
    submission_attempt_count: int
    broker_order_id: str | None
    broker_order_status: str | None
    broker_filled_quantity: Decimal
    confirmed_remaining_position: Decimal
    event_sequence: int
    event_head_hash: str
    updated_at: datetime
    control_hash: str

    @property
    def retry_post(self) -> bool:
        return False

    @property
    def restart_action(self) -> str:
        if self.status in {
            PaperCloseLifecycleStatus.SUBMISSION_UNKNOWN,
            PaperCloseLifecycleStatus.HALTED_RECONCILIATION_REQUIRED,
        }:
            return "RECONCILE_ONLY"
        if self.status in {PaperCloseLifecycleStatus.ACKNOWLEDGED, PaperCloseLifecycleStatus.PARTIALLY_FILLED}:
            return "MONITOR_AND_RECONCILE"
        if self.status is PaperCloseLifecycleStatus.PREPARED:
            return "CONTINUE_SAME_ATTEMPT_ONLY"
        return "IDLE"

    def to_dict(self) -> dict[str, object]:
        return _state_payload(self, include_control=True)


@dataclass(frozen=True, slots=True)
class PaperCloseLifecycleEvent:
    attempt_id: str
    sequence: int
    event_type: PaperCloseLifecycleEventType
    occurred_at: datetime
    payload: dict[str, object]
    previous_event_hash: str
    event_hash: str


@dataclass(frozen=True, slots=True)
class PaperCloseLifecycleSnapshot:
    state: PaperCloseLifecycleState
    events: tuple[PaperCloseLifecycleEvent, ...]


class SQLitePaperCloseLifecycle:
    """Durable one-shot lifecycle for R7 risk-reducing PAPER exits.

    This class performs no broker I/O and receives no credentials. The future
    writer must call mark_submission_unknown immediately before its single POST.
    Once UNKNOWN is crossed, this attempt can never regain POST authority;
    restart_action becomes RECONCILE_ONLY until broker truth resolves it.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        if not isinstance(runtime, SQLiteRuntime):
            raise TypeError("R7 close lifecycle requires SQLiteRuntime")
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS r7_paper_close_control (
                    attempt_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS r7_paper_close_events (
                    attempt_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    PRIMARY KEY(attempt_id, sequence)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS r7_paper_close_plan_once
                    ON r7_paper_close_control(json_extract(state_json, '$.plan_hash'));
                """
            )
        finally:
            conn.close()

    def prepare(self, *, attempt_id: str, plan: PaperCryptoClosePlan, at: datetime) -> PaperCloseLifecycleState:
        _require_id(attempt_id, "attempt_id")
        _require_aware(at, "at")
        if not isinstance(plan, PaperCryptoClosePlan):
            raise TypeError("R7 close lifecycle requires PaperCryptoClosePlan")
        instant = at.astimezone(timezone.utc)
        if instant < plan.prepared_at.astimezone(timezone.utc) or instant >= plan.expires_at.astimezone(timezone.utc):
            raise PaperCloseLifecycleBlocked("close plan is not fresh at lifecycle preparation")
        initial = PaperCloseLifecycleState(
            attempt_id=attempt_id,
            plan_hash=plan.plan_hash,
            symbol=plan.symbol,
            requested_quantity=plan.quantity,
            status=PaperCloseLifecycleStatus.PREPARED,
            submission_attempt_count=0,
            broker_order_id=None,
            broker_order_status=None,
            broker_filled_quantity=Decimal("0"),
            confirmed_remaining_position=plan.observed_position_quantity,
            event_sequence=1,
            event_head_hash="",
            updated_at=instant,
            control_hash="",
        )
        event = _event(
            attempt_id=attempt_id,
            sequence=1,
            event_type=PaperCloseLifecycleEventType.PREPARED,
            occurred_at=instant,
            payload={
                "plan_hash": plan.plan_hash,
                "symbol": plan.symbol,
                "requested_quantity": _decimal(plan.quantity),
                "retry_post": False,
                "live_trading": "BLOCKED",
            },
            previous_event_hash=_GENESIS,
        )
        initial = replace(initial, event_head_hash=event.event_hash)
        initial = _with_control_hash(initial)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT state_json, control_hash FROM r7_paper_close_control WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if existing is not None:
                current = _state_from_row(existing)
                if current == initial:
                    conn.execute("COMMIT")
                    return current
                raise PaperCloseLifecycleConflict("close attempt_id is already bound to different evidence")
            plan_row = conn.execute(
                "SELECT state_json, control_hash FROM r7_paper_close_control WHERE json_extract(state_json, '$.plan_hash') = ?",
                (plan.plan_hash,),
            ).fetchone()
            if plan_row is not None:
                raise PaperCloseLifecycleConflict("close plan is already bound to another attempt")
            _insert_event(conn, event)
            _write_state(conn, initial)
            conn.execute("COMMIT")
            return initial
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise PaperCloseLifecycleConflict("close lifecycle uniqueness conflict") from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def mark_submission_unknown(self, attempt_id: str, *, at: datetime) -> PaperCloseLifecycleState:
        return self._mutate(
            attempt_id,
            at=at,
            event_type=PaperCloseLifecycleEventType.SUBMISSION_UNKNOWN,
            payload={"retry_post": False, "next_action": "RECONCILE_ONLY"},
            transition=self._to_unknown,
        )

    @staticmethod
    def _to_unknown(state: PaperCloseLifecycleState, _payload: dict[str, object]) -> PaperCloseLifecycleState:
        if state.status is not PaperCloseLifecycleStatus.PREPARED:
            raise PaperCloseLifecycleBlocked("only PREPARED close attempt may cross SUBMISSION_UNKNOWN")
        if state.submission_attempt_count != 0:
            raise PaperCloseLifecycleIntegrityError("prepared close attempt count is not zero")
        return replace(
            state,
            status=PaperCloseLifecycleStatus.SUBMISSION_UNKNOWN,
            submission_attempt_count=1,
        )

    def reconcile(
        self,
        attempt_id: str,
        *,
        broker_order_id: str,
        broker_status: str,
        filled_quantity: Decimal,
        remaining_position: Decimal,
        at: datetime,
    ) -> PaperCloseLifecycleState:
        _require_id(broker_order_id, "broker_order_id")
        status = broker_status.strip().lower() if isinstance(broker_status, str) else ""
        if status not in _OPEN_STATUSES | _TERMINAL_STATUSES:
            raise ValueError("broker close status is unsupported")
        _nonnegative(filled_quantity, "filled_quantity")
        _nonnegative(remaining_position, "remaining_position")
        payload = {
            "broker_order_id": broker_order_id,
            "broker_status": status,
            "filled_quantity": _decimal(filled_quantity),
            "remaining_position": _decimal(remaining_position),
        }

        def transition(state: PaperCloseLifecycleState, _payload: dict[str, object]) -> PaperCloseLifecycleState:
            if state.submission_attempt_count != 1:
                raise PaperCloseLifecycleBlocked("close reconciliation requires exactly one burned POST attempt")
            if state.status not in {
                PaperCloseLifecycleStatus.SUBMISSION_UNKNOWN,
                PaperCloseLifecycleStatus.ACKNOWLEDGED,
                PaperCloseLifecycleStatus.PARTIALLY_FILLED,
                PaperCloseLifecycleStatus.HALTED_RECONCILIATION_REQUIRED,
            }:
                raise PaperCloseLifecycleBlocked("close lifecycle state is not reconcilable")
            if filled_quantity > state.requested_quantity:
                raise PaperCloseLifecycleIntegrityError("broker close fill exceeds requested quantity")
            if remaining_position > state.confirmed_remaining_position:
                raise PaperCloseLifecycleIntegrityError("broker remaining position increased during risk-reducing close")
            if status == "partially_filled" and (filled_quantity <= 0 or filled_quantity >= state.requested_quantity):
                raise PaperCloseLifecycleIntegrityError("partial close status has inconsistent filled quantity")
            if status in _OPEN_STATUSES and status != "partially_filled" and filled_quantity != 0:
                raise PaperCloseLifecycleIntegrityError("open unfilled close status reports fills")
            if remaining_position == 0:
                next_status = PaperCloseLifecycleStatus.FLAT_RECONCILED
            elif status in _TERMINAL_STATUSES:
                next_status = PaperCloseLifecycleStatus.TERMINAL_RECONCILED
            elif status == "partially_filled":
                next_status = PaperCloseLifecycleStatus.PARTIALLY_FILLED
            else:
                next_status = PaperCloseLifecycleStatus.ACKNOWLEDGED
            return replace(
                state,
                status=next_status,
                broker_order_id=broker_order_id,
                broker_order_status=status,
                broker_filled_quantity=filled_quantity,
                confirmed_remaining_position=remaining_position,
            )

        return self._mutate(
            attempt_id,
            at=at,
            event_type=PaperCloseLifecycleEventType.RECONCILED,
            payload=payload,
            transition=transition,
        )

    def halt(self, attempt_id: str, *, reason: str, at: datetime) -> PaperCloseLifecycleState:
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 512:
            raise ValueError("close lifecycle halt reason is required and bounded")

        def transition(state: PaperCloseLifecycleState, _payload: dict[str, object]) -> PaperCloseLifecycleState:
            if state.status in {PaperCloseLifecycleStatus.FLAT_RECONCILED, PaperCloseLifecycleStatus.TERMINAL_RECONCILED}:
                raise PaperCloseLifecycleBlocked("terminal close lifecycle cannot be halted")
            return replace(state, status=PaperCloseLifecycleStatus.HALTED_RECONCILIATION_REQUIRED)

        return self._mutate(
            attempt_id,
            at=at,
            event_type=PaperCloseLifecycleEventType.HALTED,
            payload={"reason": reason.strip(), "retry_post": False},
            transition=transition,
        )

    def snapshot(self, attempt_id: str) -> PaperCloseLifecycleSnapshot:
        _require_id(attempt_id, "attempt_id")
        conn = self._runtime.connect()
        try:
            state = self._read_state(conn, attempt_id)
            rows = conn.execute(
                "SELECT * FROM r7_paper_close_events WHERE attempt_id = ? ORDER BY sequence",
                (attempt_id,),
            ).fetchall()
            events = tuple(_event_from_row(row) for row in rows)
            _verify_event_chain(state, events)
            return PaperCloseLifecycleSnapshot(state=state, events=events)
        finally:
            conn.close()

    def _mutate(self, attempt_id: str, *, at: datetime, event_type: PaperCloseLifecycleEventType, payload: dict[str, object], transition) -> PaperCloseLifecycleState:
        _require_id(attempt_id, "attempt_id")
        _require_aware(at, "at")
        instant = at.astimezone(timezone.utc)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            state = self._read_state(conn, attempt_id)
            rows = conn.execute(
                "SELECT * FROM r7_paper_close_events WHERE attempt_id = ? ORDER BY sequence",
                (attempt_id,),
            ).fetchall()
            events = tuple(_event_from_row(row) for row in rows)
            _verify_event_chain(state, events)
            if instant < state.updated_at.astimezone(timezone.utc):
                raise PaperCloseLifecycleBlocked("close lifecycle time may not move backwards")
            next_state = transition(state, payload)
            event = _event(
                attempt_id=attempt_id,
                sequence=state.event_sequence + 1,
                event_type=event_type,
                occurred_at=instant,
                payload=payload,
                previous_event_hash=state.event_head_hash,
            )
            _insert_event(conn, event)
            next_state = replace(
                next_state,
                event_sequence=event.sequence,
                event_head_hash=event.event_hash,
                updated_at=instant,
                control_hash="",
            )
            next_state = _with_control_hash(next_state)
            _write_state(conn, next_state)
            conn.execute("COMMIT")
            return next_state
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @staticmethod
    def _read_state(conn: sqlite3.Connection, attempt_id: str) -> PaperCloseLifecycleState:
        row = conn.execute(
            "SELECT state_json, control_hash FROM r7_paper_close_control WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise PaperCloseLifecycleIntegrityError("close lifecycle attempt is missing")
        return _state_from_row(row)


def _state_payload(state: PaperCloseLifecycleState, *, include_control: bool) -> dict[str, object]:
    payload = {
        "attempt_id": state.attempt_id,
        "plan_hash": state.plan_hash,
        "symbol": state.symbol,
        "requested_quantity": _decimal(state.requested_quantity),
        "status": state.status.value,
        "submission_attempt_count": state.submission_attempt_count,
        "broker_order_id": state.broker_order_id,
        "broker_order_status": state.broker_order_status,
        "broker_filled_quantity": _decimal(state.broker_filled_quantity),
        "confirmed_remaining_position": _decimal(state.confirmed_remaining_position),
        "event_sequence": state.event_sequence,
        "event_head_hash": state.event_head_hash,
        "updated_at": state.updated_at.astimezone(timezone.utc).isoformat(),
        "retry_post": False,
        "live_trading": "BLOCKED",
    }
    if include_control:
        payload["control_hash"] = state.control_hash
    return payload


def _with_control_hash(state: PaperCloseLifecycleState) -> PaperCloseLifecycleState:
    return replace(state, control_hash=_hash(_state_payload(state, include_control=False)))


def _write_state(conn: sqlite3.Connection, state: PaperCloseLifecycleState) -> None:
    conn.execute(
        "INSERT INTO r7_paper_close_control(attempt_id, state_json, control_hash) VALUES (?, ?, ?) "
        "ON CONFLICT(attempt_id) DO UPDATE SET state_json=excluded.state_json, control_hash=excluded.control_hash",
        (state.attempt_id, _canonical(_state_payload(state, include_control=False)), state.control_hash),
    )


def _state_from_row(row: sqlite3.Row) -> PaperCloseLifecycleState:
    try:
        payload = json.loads(str(row["state_json"]))
        state = PaperCloseLifecycleState(
            attempt_id=str(payload["attempt_id"]),
            plan_hash=str(payload["plan_hash"]),
            symbol=str(payload["symbol"]),
            requested_quantity=Decimal(str(payload["requested_quantity"])),
            status=PaperCloseLifecycleStatus(str(payload["status"])),
            submission_attempt_count=int(payload["submission_attempt_count"]),
            broker_order_id=payload.get("broker_order_id"),
            broker_order_status=payload.get("broker_order_status"),
            broker_filled_quantity=Decimal(str(payload["broker_filled_quantity"])),
            confirmed_remaining_position=Decimal(str(payload["confirmed_remaining_position"])),
            event_sequence=int(payload["event_sequence"]),
            event_head_hash=str(payload["event_head_hash"]),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
            control_hash=str(row["control_hash"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as exc:
        raise PaperCloseLifecycleIntegrityError("close lifecycle state is corrupt") from exc
    if state.control_hash != _hash(_state_payload(state, include_control=False)):
        raise PaperCloseLifecycleIntegrityError("close lifecycle control hash mismatch")
    return state


def _event(*, attempt_id: str, sequence: int, event_type: PaperCloseLifecycleEventType, occurred_at: datetime, payload: dict[str, object], previous_event_hash: str) -> PaperCloseLifecycleEvent:
    values = {
        "attempt_id": attempt_id,
        "sequence": sequence,
        "event_type": event_type.value,
        "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
        "payload": payload,
        "previous_event_hash": previous_event_hash,
    }
    return PaperCloseLifecycleEvent(
        attempt_id=attempt_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload,
        previous_event_hash=previous_event_hash,
        event_hash=_hash(values),
    )


def _insert_event(conn: sqlite3.Connection, event: PaperCloseLifecycleEvent) -> None:
    conn.execute(
        "INSERT INTO r7_paper_close_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event.attempt_id,
            event.sequence,
            event.event_type.value,
            event.occurred_at.astimezone(timezone.utc).isoformat(),
            _canonical(event.payload),
            event.previous_event_hash,
            event.event_hash,
        ),
    )


def _event_from_row(row: sqlite3.Row) -> PaperCloseLifecycleEvent:
    try:
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise TypeError("event payload must be object")
        return PaperCloseLifecycleEvent(
            attempt_id=str(row["attempt_id"]),
            sequence=int(row["sequence"]),
            event_type=PaperCloseLifecycleEventType(str(row["event_type"])),
            occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
            payload=payload,
            previous_event_hash=str(row["previous_event_hash"]),
            event_hash=str(row["event_hash"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PaperCloseLifecycleIntegrityError("close lifecycle event is corrupt") from exc


def _verify_event_chain(state: PaperCloseLifecycleState, events: tuple[PaperCloseLifecycleEvent, ...]) -> None:
    if not events or len(events) != state.event_sequence:
        raise PaperCloseLifecycleIntegrityError("close lifecycle event sequence mismatch")
    previous = _GENESIS
    for expected, event in enumerate(events, start=1):
        if event.attempt_id != state.attempt_id or event.sequence != expected or event.previous_event_hash != previous:
            raise PaperCloseLifecycleIntegrityError("close lifecycle event chain linkage mismatch")
        expected_hash = _hash(
            {
                "attempt_id": event.attempt_id,
                "sequence": event.sequence,
                "event_type": event.event_type.value,
                "occurred_at": event.occurred_at.astimezone(timezone.utc).isoformat(),
                "payload": event.payload,
                "previous_event_hash": event.previous_event_hash,
            }
        )
        if event.event_hash != expected_hash:
            raise PaperCloseLifecycleIntegrityError("close lifecycle event hash mismatch")
        previous = event.event_hash
    if state.event_head_hash != previous:
        raise PaperCloseLifecycleIntegrityError("close lifecycle control head differs from event chain")


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _nonnegative(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{label} must be finite non-negative Decimal")


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


__all__ = [
    "PaperCloseLifecycleBlocked",
    "PaperCloseLifecycleConflict",
    "PaperCloseLifecycleError",
    "PaperCloseLifecycleEvent",
    "PaperCloseLifecycleEventType",
    "PaperCloseLifecycleIntegrityError",
    "PaperCloseLifecycleSnapshot",
    "PaperCloseLifecycleState",
    "PaperCloseLifecycleStatus",
    "SQLitePaperCloseLifecycle",
]
