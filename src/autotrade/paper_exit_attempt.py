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
from autotrade.paper_exit_order import PaperExitOrder
from autotrade.persistence import SQLiteRuntime


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_GENESIS_HASH = "0" * 64
_OPEN_STATUSES = {"accepted", "pending_new", "new", "partially_filled"}
_TERMINAL_STATUSES = {"filled", "canceled", "expired", "rejected"}


class PaperExitAttemptError(RuntimeError):
    pass


class PaperExitAttemptConflict(PaperExitAttemptError):
    pass


class PaperExitAttemptIntegrityError(PaperExitAttemptError):
    pass


class PaperExitAttemptBlocked(PaperExitAttemptError):
    pass


class PaperExitStatus(StrEnum):
    PREPARED = "PREPARED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ORDER_ABSENT_UNKNOWN = "ORDER_ABSENT_UNKNOWN"
    RECONCILED_FLAT = "RECONCILED_FLAT"
    RECONCILED_PARTIAL = "RECONCILED_PARTIAL"
    RECONCILED_NO_FILL = "RECONCILED_NO_FILL"
    HALTED_RECONCILIATION_REQUIRED = "HALTED_RECONCILIATION_REQUIRED"


class PaperExitEventType(StrEnum):
    PREPARED = "PREPARED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    ORDER_RECONCILED = "ORDER_RECONCILED"
    ORDER_ABSENT_OBSERVED = "ORDER_ABSENT_OBSERVED"
    HALTED = "HALTED"


@dataclass(frozen=True, slots=True)
class PaperExitBinding:
    attempt_id: str
    plan_hash: str
    order_hash: str
    payload_hash: str
    client_order_id: str
    account_reference: str
    credential_reference: str
    portfolio_fingerprint: str
    symbol: str
    broker_symbol: str
    close_quantity: Decimal
    initial_position_quantity: Decimal
    created_at: datetime

    def __post_init__(self) -> None:
        _id(self.attempt_id, "attempt_id")
        _id(self.client_order_id, "client_order_id")
        for label, value in (
            ("plan_hash", self.plan_hash),
            ("order_hash", self.order_hash),
            ("payload_hash", self.payload_hash),
            ("account_reference", self.account_reference),
            ("credential_reference", self.credential_reference),
            ("portfolio_fingerprint", self.portfolio_fingerprint),
        ):
            _hash_value(value, label)
        _positive(self.close_quantity, "close_quantity")
        _positive(self.initial_position_quantity, "initial_position_quantity")
        if self.close_quantity > self.initial_position_quantity:
            raise ValueError("exit close quantity exceeds initial position")
        _aware(self.created_at, "created_at")

    @property
    def fingerprint(self) -> str:
        return _hash_json(_binding_payload(self))


@dataclass(frozen=True, slots=True)
class PaperExitState:
    attempt_id: str
    binding_hash: str
    status: PaperExitStatus
    attempt_count: int
    broker_order_id: str | None
    broker_status: str | None
    broker_filled_quantity: Decimal
    remaining_position_quantity: Decimal
    event_sequence: int
    event_head_hash: str
    updated_at: datetime
    control_hash: str

    @property
    def restart_action(self) -> str:
        if self.status is PaperExitStatus.PREPARED:
            return "EXECUTE_ONCE_IF_FRESH"
        if self.status in {
            PaperExitStatus.SUBMISSION_UNKNOWN,
            PaperExitStatus.ACKNOWLEDGED,
            PaperExitStatus.ORDER_ABSENT_UNKNOWN,
            PaperExitStatus.HALTED_RECONCILIATION_REQUIRED,
        }:
            return "RECONCILE_ONLY"
        return "IDLE"

    @property
    def terminal(self) -> bool:
        return self.status in {
            PaperExitStatus.RECONCILED_FLAT,
            PaperExitStatus.RECONCILED_PARTIAL,
            PaperExitStatus.RECONCILED_NO_FILL,
        }


@dataclass(frozen=True, slots=True)
class PaperExitSnapshot:
    binding: PaperExitBinding
    state: PaperExitState


class SQLitePaperExitAttempt:
    """Durable R7 PAPER exit state machine.

    The transition to SUBMISSION_UNKNOWN is committed before broker I/O. From
    that point, all nonterminal restart states expose RECONCILE_ONLY and no
    method can create a second submission attempt.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        if not isinstance(runtime, SQLiteRuntime):
            raise TypeError("paper exit lifecycle requires SQLiteRuntime")
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_exit_bindings (
                    attempt_id TEXT PRIMARY KEY,
                    plan_hash TEXT NOT NULL UNIQUE,
                    order_hash TEXT NOT NULL UNIQUE,
                    binding_json TEXT NOT NULL,
                    binding_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS paper_exit_control (
                    attempt_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    control_hash TEXT NOT NULL,
                    FOREIGN KEY(attempt_id) REFERENCES paper_exit_bindings(attempt_id)
                );
                CREATE TABLE IF NOT EXISTS paper_exit_events (
                    attempt_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    PRIMARY KEY(attempt_id, sequence),
                    UNIQUE(event_hash),
                    FOREIGN KEY(attempt_id) REFERENCES paper_exit_bindings(attempt_id)
                );
                """
            )
        finally:
            conn.close()

    def prepare(
        self,
        *,
        plan: PaperCryptoClosePlan,
        order: PaperExitOrder,
        at: datetime,
    ) -> PaperExitSnapshot:
        _aware(at, "at")
        instant = at.astimezone(timezone.utc)
        if not isinstance(plan, PaperCryptoClosePlan) or not isinstance(order, PaperExitOrder):
            raise PaperExitAttemptBlocked("exact close plan and exit order are required")
        if instant < plan.prepared_at.astimezone(timezone.utc) or instant >= plan.expires_at.astimezone(timezone.utc):
            raise PaperExitAttemptBlocked("exit preparation evidence is expired or not yet valid")
        if order.plan_hash != plan.plan_hash:
            raise PaperExitAttemptBlocked("exit order is not bound to exact close plan")
        if order.quantity != plan.quantity or order.limit_price != plan.limit_price:
            raise PaperExitAttemptBlocked("exit order quantity/price differs from close plan")
        if order.symbol != plan.symbol or order.broker_symbol != plan.broker_symbol:
            raise PaperExitAttemptBlocked("exit order symbol differs from close plan")
        if order.retry_post is not False or order.live_trading != "BLOCKED":
            raise PaperExitAttemptBlocked("exit order retry/LIVE invariants are invalid")
        binding = PaperExitBinding(
            attempt_id=order.attempt_id,
            plan_hash=plan.plan_hash,
            order_hash=order.order_hash,
            payload_hash=order.payload_hash,
            client_order_id=order.client_order_id,
            account_reference=plan.account_reference,
            credential_reference=plan.credential_reference,
            portfolio_fingerprint=plan.portfolio_fingerprint,
            symbol=plan.symbol,
            broker_symbol=plan.broker_symbol,
            close_quantity=plan.quantity,
            initial_position_quantity=plan.observed_position_quantity,
            created_at=instant,
        )
        event = _event(
            attempt_id=binding.attempt_id,
            sequence=1,
            event_type=PaperExitEventType.PREPARED,
            occurred_at=instant,
            payload={"plan_hash": plan.plan_hash, "order_hash": order.order_hash},
            previous_event_hash=_GENESIS_HASH,
        )
        state = PaperExitState(
            attempt_id=binding.attempt_id,
            binding_hash=binding.fingerprint,
            status=PaperExitStatus.PREPARED,
            attempt_count=0,
            broker_order_id=None,
            broker_status=None,
            broker_filled_quantity=Decimal("0"),
            remaining_position_quantity=binding.initial_position_quantity,
            event_sequence=1,
            event_head_hash=event["event_hash"],
            updated_at=instant,
            control_hash="",
        )
        state = _with_control_hash(state)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT attempt_id FROM paper_exit_bindings WHERE attempt_id=? OR plan_hash=? OR order_hash=?",
                (binding.attempt_id, binding.plan_hash, binding.order_hash),
            ).fetchall()
            if existing:
                conn.execute("ROLLBACK")
                snapshot = self.snapshot(binding.attempt_id)
                if snapshot.binding == binding and snapshot.state.status is PaperExitStatus.PREPARED:
                    return snapshot
                raise PaperExitAttemptConflict("exit attempt identity already belongs to different durable evidence")
            conn.execute(
                "INSERT INTO paper_exit_bindings VALUES (?, ?, ?, ?, ?)",
                (binding.attempt_id, binding.plan_hash, binding.order_hash, _canonical(_binding_payload(binding)), binding.fingerprint),
            )
            conn.execute(
                "INSERT INTO paper_exit_control VALUES (?, ?, ?)",
                (state.attempt_id, _canonical(_state_payload(state)), state.control_hash),
            )
            conn.execute(
                "INSERT INTO paper_exit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    binding.attempt_id,
                    1,
                    PaperExitEventType.PREPARED.value,
                    _iso(instant),
                    _canonical(event["payload"]),
                    _GENESIS_HASH,
                    event["event_hash"],
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.snapshot(binding.attempt_id)

    def mark_submission_unknown(self, attempt_id: str, *, at: datetime) -> PaperExitState:
        def transition(binding: PaperExitBinding, state: PaperExitState) -> PaperExitState:
            if state.status is not PaperExitStatus.PREPARED or state.attempt_count != 0:
                raise PaperExitAttemptBlocked("exit POST authority is already consumed or unavailable")
            return replace(state, status=PaperExitStatus.SUBMISSION_UNKNOWN, attempt_count=1)

        return self._mutate(
            attempt_id,
            at=at,
            event_type=PaperExitEventType.SUBMISSION_UNKNOWN,
            payload={"attempt_count": 1, "retry_post": False},
            transition=transition,
        )

    def reconcile_order(
        self,
        attempt_id: str,
        *,
        broker_order_id: str,
        broker_status: str,
        filled_quantity: Decimal,
        remaining_position_quantity: Decimal,
        at: datetime,
    ) -> PaperExitState:
        _id(broker_order_id, "broker_order_id")
        status = broker_status.strip().lower()
        if status not in _OPEN_STATUSES | _TERMINAL_STATUSES:
            raise ValueError("broker exit status is unsupported")
        _nonnegative(filled_quantity, "filled_quantity")
        _nonnegative(remaining_position_quantity, "remaining_position_quantity")

        def transition(binding: PaperExitBinding, state: PaperExitState) -> PaperExitState:
            if state.status not in {
                PaperExitStatus.SUBMISSION_UNKNOWN,
                PaperExitStatus.ACKNOWLEDGED,
                PaperExitStatus.ORDER_ABSENT_UNKNOWN,
            }:
                raise PaperExitAttemptBlocked("exit reconciliation requires a burned POST attempt")
            if state.attempt_count != 1:
                raise PaperExitAttemptIntegrityError("exit reconciliation requires exactly one POST attempt")
            if filled_quantity > binding.close_quantity:
                raise PaperExitAttemptIntegrityError("broker exit fill exceeds requested close quantity")
            if remaining_position_quantity > binding.initial_position_quantity:
                raise PaperExitAttemptIntegrityError("broker position increased during risk-reducing exit")
            if status in _OPEN_STATUSES:
                next_status = PaperExitStatus.ACKNOWLEDGED
            elif remaining_position_quantity == 0:
                next_status = PaperExitStatus.RECONCILED_FLAT
            elif filled_quantity > 0 or remaining_position_quantity < binding.initial_position_quantity:
                next_status = PaperExitStatus.RECONCILED_PARTIAL
            else:
                next_status = PaperExitStatus.RECONCILED_NO_FILL
            return replace(
                state,
                status=next_status,
                broker_order_id=broker_order_id,
                broker_status=status,
                broker_filled_quantity=filled_quantity,
                remaining_position_quantity=remaining_position_quantity,
            )

        return self._mutate(
            attempt_id,
            at=at,
            event_type=PaperExitEventType.ORDER_RECONCILED,
            payload={
                "broker_order_id": broker_order_id,
                "broker_status": status,
                "filled_quantity": _decimal(filled_quantity),
                "remaining_position_quantity": _decimal(remaining_position_quantity),
            },
            transition=transition,
        )

    def reconcile_order_absent(
        self,
        attempt_id: str,
        *,
        remaining_position_quantity: Decimal,
        at: datetime,
    ) -> PaperExitState:
        _nonnegative(remaining_position_quantity, "remaining_position_quantity")

        def transition(binding: PaperExitBinding, state: PaperExitState) -> PaperExitState:
            if state.status not in {
                PaperExitStatus.SUBMISSION_UNKNOWN,
                PaperExitStatus.ACKNOWLEDGED,
                PaperExitStatus.ORDER_ABSENT_UNKNOWN,
            }:
                raise PaperExitAttemptBlocked("order-absence evidence requires a burned POST attempt")
            if state.attempt_count != 1:
                raise PaperExitAttemptIntegrityError("order-absence evidence requires exactly one POST attempt")
            if remaining_position_quantity > binding.initial_position_quantity:
                raise PaperExitAttemptIntegrityError("position increased while exit order identity is absent")
            return replace(
                state,
                status=PaperExitStatus.ORDER_ABSENT_UNKNOWN,
                broker_order_id=None,
                broker_status="not_found",
                remaining_position_quantity=remaining_position_quantity,
            )

        return self._mutate(
            attempt_id,
            at=at,
            event_type=PaperExitEventType.ORDER_ABSENT_OBSERVED,
            payload={
                "remaining_position_quantity": _decimal(remaining_position_quantity),
                "retry_post": False,
            },
            transition=transition,
        )

    def halt(self, attempt_id: str, *, reason: str, at: datetime) -> PaperExitState:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
            raise ValueError("halt reason is required and bounded")

        def transition(_binding: PaperExitBinding, state: PaperExitState) -> PaperExitState:
            if state.terminal:
                raise PaperExitAttemptBlocked("terminal exit does not require halt")
            return replace(state, status=PaperExitStatus.HALTED_RECONCILIATION_REQUIRED)

        return self._mutate(
            attempt_id,
            at=at,
            event_type=PaperExitEventType.HALTED,
            payload={"reason": reason.strip()},
            transition=transition,
        )

    def snapshot(self, attempt_id: str) -> PaperExitSnapshot:
        _id(attempt_id, "attempt_id")
        conn = self._runtime.connect()
        try:
            binding_row = conn.execute("SELECT * FROM paper_exit_bindings WHERE attempt_id=?", (attempt_id,)).fetchone()
            state_row = conn.execute("SELECT * FROM paper_exit_control WHERE attempt_id=?", (attempt_id,)).fetchone()
            if binding_row is None or state_row is None:
                raise KeyError(attempt_id)
            binding = _binding_from_json(str(binding_row["binding_json"]))
            if binding.fingerprint != str(binding_row["binding_hash"]):
                raise PaperExitAttemptIntegrityError("durable exit binding hash mismatch")
            state = _state_from_json(str(state_row["state_json"]), control_hash=str(state_row["control_hash"]))
            if state.binding_hash != binding.fingerprint or state.control_hash != _state_hash(replace(state, control_hash="")):
                raise PaperExitAttemptIntegrityError("durable exit control hash mismatch")
            rows = conn.execute(
                "SELECT * FROM paper_exit_events WHERE attempt_id=? ORDER BY sequence",
                (attempt_id,),
            ).fetchall()
            if len(rows) != state.event_sequence:
                raise PaperExitAttemptIntegrityError("durable exit event sequence mismatch")
            previous = _GENESIS_HASH
            for expected, row in enumerate(rows, start=1):
                payload = json.loads(str(row["payload_json"]))
                event_hash = _event_hash(
                    attempt_id=attempt_id,
                    sequence=expected,
                    event_type=str(row["event_type"]),
                    occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
                    payload=payload,
                    previous_event_hash=previous,
                )
                if int(row["sequence"]) != expected or str(row["previous_event_hash"]) != previous or str(row["event_hash"]) != event_hash:
                    raise PaperExitAttemptIntegrityError("durable exit event chain mismatch")
                previous = event_hash
            if previous != state.event_head_hash:
                raise PaperExitAttemptIntegrityError("durable exit event head mismatch")
            return PaperExitSnapshot(binding=binding, state=state)
        except (json.JSONDecodeError, ValueError, InvalidOperation) as exc:
            raise PaperExitAttemptIntegrityError("invalid durable exit evidence") from exc
        finally:
            conn.close()

    def _mutate(self, attempt_id: str, *, at: datetime, event_type: PaperExitEventType, payload: dict[str, object], transition) -> PaperExitState:
        _id(attempt_id, "attempt_id")
        _aware(at, "at")
        instant = at.astimezone(timezone.utc)
        snapshot = self.snapshot(attempt_id)
        next_state = transition(snapshot.binding, snapshot.state)
        event = _event(
            attempt_id=attempt_id,
            sequence=snapshot.state.event_sequence + 1,
            event_type=event_type,
            occurred_at=instant,
            payload=payload,
            previous_event_hash=snapshot.state.event_head_hash,
        )
        next_state = replace(
            next_state,
            event_sequence=event["sequence"],
            event_head_hash=event["event_hash"],
            updated_at=instant,
            control_hash="",
        )
        next_state = _with_control_hash(next_state)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT control_hash FROM paper_exit_control WHERE attempt_id=?", (attempt_id,)).fetchone()
            if current is None or str(current["control_hash"]) != snapshot.state.control_hash:
                raise PaperExitAttemptConflict("exit lifecycle changed concurrently")
            conn.execute(
                "INSERT INTO paper_exit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    event["sequence"],
                    event_type.value,
                    _iso(instant),
                    _canonical(payload),
                    event["previous_event_hash"],
                    event["event_hash"],
                ),
            )
            conn.execute(
                "UPDATE paper_exit_control SET state_json=?, control_hash=? WHERE attempt_id=?",
                (_canonical(_state_payload(next_state)), next_state.control_hash, attempt_id),
            )
            conn.execute("COMMIT")
            return next_state
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def _binding_payload(value: PaperExitBinding) -> dict[str, object]:
    return {
        "attempt_id": value.attempt_id,
        "plan_hash": value.plan_hash,
        "order_hash": value.order_hash,
        "payload_hash": value.payload_hash,
        "client_order_id": value.client_order_id,
        "account_reference": value.account_reference,
        "credential_reference": value.credential_reference,
        "portfolio_fingerprint": value.portfolio_fingerprint,
        "symbol": value.symbol,
        "broker_symbol": value.broker_symbol,
        "close_quantity": _decimal(value.close_quantity),
        "initial_position_quantity": _decimal(value.initial_position_quantity),
        "created_at": _iso(value.created_at),
    }


def _state_payload(value: PaperExitState) -> dict[str, object]:
    return {
        "attempt_id": value.attempt_id,
        "binding_hash": value.binding_hash,
        "status": value.status.value,
        "attempt_count": value.attempt_count,
        "broker_order_id": value.broker_order_id,
        "broker_status": value.broker_status,
        "broker_filled_quantity": _decimal(value.broker_filled_quantity),
        "remaining_position_quantity": _decimal(value.remaining_position_quantity),
        "event_sequence": value.event_sequence,
        "event_head_hash": value.event_head_hash,
        "updated_at": _iso(value.updated_at),
    }


def _binding_from_json(text: str) -> PaperExitBinding:
    raw = json.loads(text)
    return PaperExitBinding(
        attempt_id=str(raw["attempt_id"]),
        plan_hash=str(raw["plan_hash"]),
        order_hash=str(raw["order_hash"]),
        payload_hash=str(raw["payload_hash"]),
        client_order_id=str(raw["client_order_id"]),
        account_reference=str(raw["account_reference"]),
        credential_reference=str(raw["credential_reference"]),
        portfolio_fingerprint=str(raw["portfolio_fingerprint"]),
        symbol=str(raw["symbol"]),
        broker_symbol=str(raw["broker_symbol"]),
        close_quantity=Decimal(str(raw["close_quantity"])),
        initial_position_quantity=Decimal(str(raw["initial_position_quantity"])),
        created_at=datetime.fromisoformat(str(raw["created_at"])),
    )


def _state_from_json(text: str, *, control_hash: str) -> PaperExitState:
    raw = json.loads(text)
    return PaperExitState(
        attempt_id=str(raw["attempt_id"]),
        binding_hash=str(raw["binding_hash"]),
        status=PaperExitStatus(str(raw["status"])),
        attempt_count=int(raw["attempt_count"]),
        broker_order_id=raw.get("broker_order_id"),
        broker_status=raw.get("broker_status"),
        broker_filled_quantity=Decimal(str(raw["broker_filled_quantity"])),
        remaining_position_quantity=Decimal(str(raw["remaining_position_quantity"])),
        event_sequence=int(raw["event_sequence"]),
        event_head_hash=str(raw["event_head_hash"]),
        updated_at=datetime.fromisoformat(str(raw["updated_at"])),
        control_hash=control_hash,
    )


def _with_control_hash(state: PaperExitState) -> PaperExitState:
    return replace(state, control_hash=_state_hash(replace(state, control_hash="")))


def _state_hash(state: PaperExitState) -> str:
    return _hash_json(_state_payload(state))


def _event(*, attempt_id: str, sequence: int, event_type: PaperExitEventType, occurred_at: datetime, payload: dict[str, object], previous_event_hash: str) -> dict[str, object]:
    return {
        "attempt_id": attempt_id,
        "sequence": sequence,
        "event_type": event_type.value,
        "occurred_at": _iso(occurred_at),
        "payload": payload,
        "previous_event_hash": previous_event_hash,
        "event_hash": _event_hash(
            attempt_id=attempt_id,
            sequence=sequence,
            event_type=event_type.value,
            occurred_at=occurred_at,
            payload=payload,
            previous_event_hash=previous_event_hash,
        ),
    }


def _event_hash(*, attempt_id: str, sequence: int, event_type: str, occurred_at: datetime, payload: object, previous_event_hash: str) -> str:
    return _hash_json(
        {
            "attempt_id": attempt_id,
            "sequence": sequence,
            "event_type": event_type,
            "occurred_at": _iso(occurred_at),
            "payload": payload,
            "previous_event_hash": previous_event_hash,
        }
    )


def _id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _hash_value(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _positive(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{label} must be finite positive Decimal")


def _nonnegative(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{label} must be finite non-negative Decimal")


def _aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_json(value: object) -> str:
    return sha256(_canonical(value).encode()).hexdigest()


__all__ = [
    "PaperExitAttemptBlocked",
    "PaperExitAttemptConflict",
    "PaperExitAttemptError",
    "PaperExitAttemptIntegrityError",
    "PaperExitBinding",
    "PaperExitSnapshot",
    "PaperExitState",
    "PaperExitStatus",
    "SQLitePaperExitAttempt",
]
