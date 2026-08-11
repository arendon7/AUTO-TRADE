from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3
from typing import Mapping

from autotrade.domain import OrderRecord, OrderStatus, intent_fingerprint
from autotrade.persistence import SQLiteRuntime


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CLIENT_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_BROKER_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_GENESIS_HASH = "0" * 64


class PaperSubmissionError(RuntimeError):
    pass


class PaperSubmissionIntegrityError(PaperSubmissionError):
    pass


class PaperSubmissionConflict(PaperSubmissionError):
    pass


class PaperSubmissionBlocked(PaperSubmissionError):
    pass


class PaperSubmissionStatus(StrEnum):
    PREPARED = "PREPARED"
    UNKNOWN = "UNKNOWN"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class PaperSubmissionEventType(StrEnum):
    PREPARED = "PREPARED"
    SUBMIT_ATTEMPT_UNKNOWN = "SUBMIT_ATTEMPT_UNKNOWN"
    RECONCILIATION_ABSENT = "RECONCILIATION_ABSENT"
    RECONCILED_ACKNOWLEDGED = "RECONCILED_ACKNOWLEDGED"


@dataclass(frozen=True, slots=True)
class PaperSubmissionBinding:
    order_id: str
    client_order_id: str
    intent_id: str
    intent_fingerprint: str
    risk_decision_id: str
    account_attestation_fingerprint: str
    order_payload_hash: str
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_id(self.order_id, "order_id")
        _validate_client_order_id(self.client_order_id)
        _validate_id(self.intent_id, "intent_id")
        _validate_hash(self.intent_fingerprint, "intent_fingerprint")
        _validate_id(self.risk_decision_id, "risk_decision_id")
        _validate_hash(
            self.account_attestation_fingerprint,
            "account_attestation_fingerprint",
        )
        _validate_hash(self.order_payload_hash, "order_payload_hash")
        _require_aware(self.created_at, "created_at")

    @classmethod
    def from_order(
        cls,
        *,
        order: OrderRecord,
        account_attestation_fingerprint: str,
        order_payload_hash: str,
        created_at: datetime,
    ) -> "PaperSubmissionBinding":
        if order.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTING}:
            raise ValueError(
                "external PAPER binding requires a VALIDATED or SUBMITTING OMS order"
            )
        return cls(
            order_id=order.order_id,
            client_order_id=deterministic_client_order_id(order),
            intent_id=order.intent.intent_id,
            intent_fingerprint=intent_fingerprint(order.intent),
            risk_decision_id=order.risk_decision_id,
            account_attestation_fingerprint=account_attestation_fingerprint,
            order_payload_hash=order_payload_hash,
            created_at=created_at,
        )

    @property
    def fingerprint(self) -> str:
        return _hash_payload(_binding_payload(self))


@dataclass(frozen=True, slots=True)
class PaperSubmissionEvent:
    order_id: str
    sequence: int
    event_type: PaperSubmissionEventType
    occurred_at: datetime
    payload: Mapping[str, object]
    previous_event_hash: str
    event_hash: str


@dataclass(frozen=True, slots=True)
class PaperSubmissionState:
    order_id: str
    client_order_id: str
    binding_hash: str
    status: PaperSubmissionStatus
    event_sequence: int
    event_head_hash: str
    attempt_count: int
    absence_observation_count: int
    broker_order_id: str | None
    broker_client_order_id: str | None
    updated_at: datetime
    control_hash: str

    @property
    def submit_allowed(self) -> bool:
        return self.status is PaperSubmissionStatus.PREPARED

    @property
    def reconciliation_required(self) -> bool:
        return self.status is PaperSubmissionStatus.UNKNOWN


class SQLitePaperSubmissionRegistry:
    """Durable pre-submit ambiguity barrier for external Alpaca PAPER orders.

    No network I/O is implemented here. A future writer must persist
    `SUBMIT_ATTEMPT_UNKNOWN` before issuing POST. Once that durable boundary is
    crossed, this registry refuses a second attempt and requires reconciliation
    by deterministic `client_order_id`.

    Bindings are immutable. Per-order events are append-only SHA-256 chains and
    a separately hashed control row anchors the current head, detecting mutation,
    reordering, sequence gaps and tail deletion on every read/restart.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpaca_paper_submission_bindings (
                    order_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    binding_json TEXT NOT NULL,
                    binding_hash TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS alpaca_paper_submission_events (
                    order_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(order_id, sequence),
                    FOREIGN KEY(order_id)
                        REFERENCES alpaca_paper_submission_bindings(order_id)
                        ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS alpaca_paper_submission_control (
                    order_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    binding_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    event_sequence INTEGER NOT NULL CHECK(event_sequence > 0),
                    event_head_hash TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL CHECK(attempt_count >= 0),
                    absence_observation_count INTEGER NOT NULL CHECK(absence_observation_count >= 0),
                    broker_order_id TEXT,
                    broker_client_order_id TEXT,
                    updated_at TEXT NOT NULL,
                    control_hash TEXT NOT NULL,
                    FOREIGN KEY(order_id)
                        REFERENCES alpaca_paper_submission_bindings(order_id)
                        ON DELETE RESTRICT
                );
                """
            )
        finally:
            conn.close()

    def prepare(self, binding: PaperSubmissionBinding) -> PaperSubmissionState:
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            by_order = conn.execute(
                "SELECT * FROM alpaca_paper_submission_bindings WHERE order_id = ?",
                (binding.order_id,),
            ).fetchone()
            by_client = conn.execute(
                "SELECT * FROM alpaca_paper_submission_bindings WHERE client_order_id = ?",
                (binding.client_order_id,),
            ).fetchone()

            if by_order is None and by_client is not None:
                raise PaperSubmissionConflict(
                    "client_order_id is already bound to another local order"
                )
            if by_order is not None:
                existing = _binding_from_row(by_order)
                if existing.client_order_id != binding.client_order_id:
                    raise PaperSubmissionConflict(
                        "local order is already bound to a different client_order_id"
                    )
                if by_client is None or str(by_client["order_id"]) != existing.order_id:
                    raise PaperSubmissionIntegrityError("submission binding indexes disagree")
                if existing.fingerprint != binding.fingerprint:
                    raise PaperSubmissionConflict(
                        "order/client_order_id is already bound to different immutable data"
                    )
                _, state, _ = self._verify_locked(conn, binding.order_id)
                conn.execute("COMMIT")
                return state

            _assert_no_orphan_state(conn, binding.order_id)
            conn.execute(
                """
                INSERT INTO alpaca_paper_submission_bindings(
                    order_id, client_order_id, binding_json, binding_hash
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    binding.order_id,
                    binding.client_order_id,
                    _canonical_json(_binding_payload(binding)),
                    binding.fingerprint,
                ),
            )
            event = _make_event(
                order_id=binding.order_id,
                sequence=1,
                event_type=PaperSubmissionEventType.PREPARED,
                occurred_at=binding.created_at,
                payload={"binding_hash": binding.fingerprint},
                previous_event_hash=_GENESIS_HASH,
            )
            _insert_event(conn, event)
            state = _make_state(
                order_id=binding.order_id,
                client_order_id=binding.client_order_id,
                binding_hash=binding.fingerprint,
                status=PaperSubmissionStatus.PREPARED,
                event_sequence=1,
                event_head_hash=event.event_hash,
                attempt_count=0,
                absence_observation_count=0,
                broker_order_id=None,
                broker_client_order_id=None,
                updated_at=binding.created_at,
            )
            _insert_control(conn, state)
            conn.execute("COMMIT")
            return state
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, order_id: str) -> PaperSubmissionState:
        _validate_id(order_id, "order_id")
        conn = self._runtime.connect()
        try:
            _, state, _ = self._verify_locked(conn, order_id)
            return state
        finally:
            conn.close()

    def get_by_client_order_id(self, client_order_id: str) -> PaperSubmissionState:
        _validate_client_order_id(client_order_id)
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                """
                SELECT order_id FROM alpaca_paper_submission_bindings
                WHERE client_order_id = ?
                """,
                (client_order_id,),
            ).fetchone()
            if row is None:
                raise KeyError(client_order_id)
            _, state, _ = self._verify_locked(conn, str(row["order_id"]))
            return state
        finally:
            conn.close()

    def events(self, order_id: str) -> tuple[PaperSubmissionEvent, ...]:
        _validate_id(order_id, "order_id")
        conn = self._runtime.connect()
        try:
            _, _, events = self._verify_locked(conn, order_id)
            return events
        finally:
            conn.close()

    def mark_submit_attempt_unknown(
        self,
        *,
        order_id: str,
        attempt_id: str,
        now: datetime,
    ) -> PaperSubmissionState:
        _validate_id(order_id, "order_id")
        _validate_id(attempt_id, "attempt_id")
        _require_aware(now, "now")
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            binding, state, events = self._verify_locked(conn, order_id)
            if _event_by_payload_value(
                events,
                event_type=PaperSubmissionEventType.SUBMIT_ATTEMPT_UNKNOWN,
                key="attempt_id",
                value=attempt_id,
            ) is not None:
                conn.execute("COMMIT")
                return state
            if now.astimezone(timezone.utc) < state.updated_at.astimezone(timezone.utc):
                raise PaperSubmissionIntegrityError(
                    "submission event time cannot move backwards"
                )
            if state.status is not PaperSubmissionStatus.PREPARED:
                raise PaperSubmissionBlocked(
                    f"external PAPER submit blocked from {state.status.value}; reconcile first"
                )

            event = _make_event(
                order_id=order_id,
                sequence=state.event_sequence + 1,
                event_type=PaperSubmissionEventType.SUBMIT_ATTEMPT_UNKNOWN,
                occurred_at=now,
                payload={
                    "attempt_id": attempt_id,
                    "binding_hash": binding.fingerprint,
                    "client_order_id": binding.client_order_id,
                },
                previous_event_hash=state.event_head_hash,
            )
            _insert_event(conn, event)
            updated = _make_state(
                order_id=state.order_id,
                client_order_id=state.client_order_id,
                binding_hash=state.binding_hash,
                status=PaperSubmissionStatus.UNKNOWN,
                event_sequence=event.sequence,
                event_head_hash=event.event_hash,
                attempt_count=state.attempt_count + 1,
                absence_observation_count=state.absence_observation_count,
                broker_order_id=None,
                broker_client_order_id=None,
                updated_at=now,
            )
            _update_control(conn, updated)
            conn.execute("COMMIT")
            return updated
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def record_reconciliation_absent(
        self,
        *,
        order_id: str,
        request_id: str,
        now: datetime,
    ) -> PaperSubmissionState:
        _validate_id(order_id, "order_id")
        _validate_request_id(request_id)
        _require_aware(now, "now")
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            binding, state, events = self._verify_locked(conn, order_id)
            if _event_by_payload_value(
                events,
                event_type=PaperSubmissionEventType.RECONCILIATION_ABSENT,
                key="request_id",
                value=request_id,
            ) is not None:
                conn.execute("COMMIT")
                return state
            _reject_reused_request_id(events, request_id)
            if state.status is not PaperSubmissionStatus.UNKNOWN:
                raise PaperSubmissionBlocked(
                    "absence evidence is accepted only while submission state is UNKNOWN"
                )

            event = _make_event(
                order_id=order_id,
                sequence=state.event_sequence + 1,
                event_type=PaperSubmissionEventType.RECONCILIATION_ABSENT,
                occurred_at=now,
                payload={
                    "binding_hash": binding.fingerprint,
                    "client_order_id": binding.client_order_id,
                    "lookup": "client_order_id",
                    "request_id": request_id,
                },
                previous_event_hash=state.event_head_hash,
            )
            _insert_event(conn, event)
            updated = _make_state(
                order_id=state.order_id,
                client_order_id=state.client_order_id,
                binding_hash=state.binding_hash,
                status=PaperSubmissionStatus.UNKNOWN,
                event_sequence=event.sequence,
                event_head_hash=event.event_hash,
                attempt_count=state.attempt_count,
                absence_observation_count=state.absence_observation_count + 1,
                broker_order_id=None,
                broker_client_order_id=None,
                updated_at=now,
            )
            _update_control(conn, updated)
            conn.execute("COMMIT")
            return updated
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def reconcile_acknowledged(
        self,
        *,
        order_id: str,
        broker_order_id: str,
        broker_client_order_id: str,
        broker_order_payload_hash: str,
        request_id: str,
        now: datetime,
    ) -> PaperSubmissionState:
        _validate_id(order_id, "order_id")
        _validate_broker_order_id(broker_order_id)
        _validate_client_order_id(broker_client_order_id)
        _validate_hash(broker_order_payload_hash, "broker_order_payload_hash")
        _validate_request_id(request_id)
        _require_aware(now, "now")

        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            binding, state, events = self._verify_locked(conn, order_id)
            if broker_client_order_id != binding.client_order_id:
                raise PaperSubmissionConflict(
                    "reconciled broker client_order_id does not match frozen binding"
                )
            if broker_order_payload_hash != binding.order_payload_hash:
                raise PaperSubmissionConflict(
                    "reconciled broker payload does not match frozen order payload"
                )
            if state.status is PaperSubmissionStatus.ACKNOWLEDGED:
                if (
                    state.broker_order_id == broker_order_id
                    and state.broker_client_order_id == broker_client_order_id
                ):
                    conn.execute("COMMIT")
                    return state
                raise PaperSubmissionConflict(
                    "submission already acknowledged to a different broker order"
                )
            if state.status is not PaperSubmissionStatus.UNKNOWN:
                raise PaperSubmissionBlocked(
                    "broker acknowledgement is accepted only after UNKNOWN submit attempt"
                )
            _reject_reused_request_id(events, request_id)

            event = _make_event(
                order_id=order_id,
                sequence=state.event_sequence + 1,
                event_type=PaperSubmissionEventType.RECONCILED_ACKNOWLEDGED,
                occurred_at=now,
                payload={
                    "binding_hash": binding.fingerprint,
                    "broker_client_order_id": broker_client_order_id,
                    "broker_order_id": broker_order_id,
                    "broker_order_payload_hash": broker_order_payload_hash,
                    "request_id": request_id,
                },
                previous_event_hash=state.event_head_hash,
            )
            _insert_event(conn, event)
            updated = _make_state(
                order_id=state.order_id,
                client_order_id=state.client_order_id,
                binding_hash=state.binding_hash,
                status=PaperSubmissionStatus.ACKNOWLEDGED,
                event_sequence=event.sequence,
                event_head_hash=event.event_hash,
                attempt_count=state.attempt_count,
                absence_observation_count=state.absence_observation_count,
                broker_order_id=broker_order_id,
                broker_client_order_id=broker_client_order_id,
                updated_at=now,
            )
            _update_control(conn, updated)
            conn.execute("COMMIT")
            return updated
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _verify_locked(
        self,
        conn: sqlite3.Connection,
        order_id: str,
    ) -> tuple[PaperSubmissionBinding, PaperSubmissionState, tuple[PaperSubmissionEvent, ...]]:
        binding_row = conn.execute(
            "SELECT * FROM alpaca_paper_submission_bindings WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if binding_row is None:
            _assert_no_orphan_state(conn, order_id)
            raise KeyError(order_id)
        binding = _binding_from_row(binding_row)

        rows = conn.execute(
            """
            SELECT * FROM alpaca_paper_submission_events
            WHERE order_id = ? ORDER BY sequence
            """,
            (order_id,),
        ).fetchall()
        if not rows:
            raise PaperSubmissionIntegrityError("submission binding has no event history")
        events = tuple(_event_from_row(row) for row in rows)
        derived = _derive_state(binding=binding, events=events)

        control_row = conn.execute(
            "SELECT * FROM alpaca_paper_submission_control WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if control_row is None:
            raise PaperSubmissionIntegrityError("submission control anchor is missing")
        control = _control_from_row(control_row)
        if control != derived:
            raise PaperSubmissionIntegrityError(
                "submission control anchor does not match verified event history"
            )
        return binding, control, events


def deterministic_client_order_id(order: OrderRecord) -> str:
    if not order.order_id or not order.intent.idempotency_key:
        raise ValueError("order_id and idempotency_key are required")
    digest = sha256(
        f"{order.order_id}:{order.intent.idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"autotrade-{digest}"


def _binding_payload(binding: PaperSubmissionBinding) -> dict[str, object]:
    return {
        "account_attestation_fingerprint": binding.account_attestation_fingerprint,
        "client_order_id": binding.client_order_id,
        "created_at": _iso(binding.created_at),
        "intent_fingerprint": binding.intent_fingerprint,
        "intent_id": binding.intent_id,
        "order_id": binding.order_id,
        "order_payload_hash": binding.order_payload_hash,
        "risk_decision_id": binding.risk_decision_id,
    }


def _binding_from_row(row: sqlite3.Row) -> PaperSubmissionBinding:
    payload = _strict_json_object(str(row["binding_json"]), "submission binding")
    try:
        binding = PaperSubmissionBinding(
            order_id=_required_str(payload, "order_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            intent_id=_required_str(payload, "intent_id"),
            intent_fingerprint=_required_str(payload, "intent_fingerprint"),
            risk_decision_id=_required_str(payload, "risk_decision_id"),
            account_attestation_fingerprint=_required_str(
                payload, "account_attestation_fingerprint"
            ),
            order_payload_hash=_required_str(payload, "order_payload_hash"),
            created_at=_parse_datetime(payload.get("created_at"), "created_at"),
        )
    except ValueError as exc:
        raise PaperSubmissionIntegrityError("invalid persisted submission binding") from exc
    if str(row["order_id"]) != binding.order_id:
        raise PaperSubmissionIntegrityError("submission binding order_id column mismatch")
    if str(row["client_order_id"]) != binding.client_order_id:
        raise PaperSubmissionIntegrityError(
            "submission binding client_order_id column mismatch"
        )
    if str(row["binding_hash"]) != binding.fingerprint:
        raise PaperSubmissionIntegrityError("submission binding hash mismatch")
    if str(row["binding_json"]) != _canonical_json(_binding_payload(binding)):
        raise PaperSubmissionIntegrityError("submission binding JSON is not canonical")
    return binding


def _make_event(
    *,
    order_id: str,
    sequence: int,
    event_type: PaperSubmissionEventType,
    occurred_at: datetime,
    payload: Mapping[str, object],
    previous_event_hash: str,
) -> PaperSubmissionEvent:
    if sequence <= 0:
        raise PaperSubmissionIntegrityError("submission event sequence must be positive")
    _require_aware(occurred_at, "occurred_at")
    _validate_hash(previous_event_hash, "previous_event_hash")
    canonical_payload = _strict_mapping(payload, "event payload")
    event_hash = _event_hash(
        order_id=order_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=canonical_payload,
        previous_event_hash=previous_event_hash,
    )
    return PaperSubmissionEvent(
        order_id=order_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=canonical_payload,
        previous_event_hash=previous_event_hash,
        event_hash=event_hash,
    )


def _event_hash(
    *,
    order_id: str,
    sequence: int,
    event_type: PaperSubmissionEventType,
    occurred_at: datetime,
    payload: Mapping[str, object],
    previous_event_hash: str,
) -> str:
    return _hash_payload(
        {
            "event_type": event_type.value,
            "occurred_at": _iso(occurred_at),
            "order_id": order_id,
            "payload": payload,
            "previous_event_hash": previous_event_hash,
            "sequence": sequence,
        }
    )


def _insert_event(conn: sqlite3.Connection, event: PaperSubmissionEvent) -> None:
    conn.execute(
        """
        INSERT INTO alpaca_paper_submission_events(
            order_id, sequence, event_type, occurred_at, payload_json,
            previous_event_hash, event_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.order_id,
            event.sequence,
            event.event_type.value,
            _iso(event.occurred_at),
            _canonical_json(event.payload),
            event.previous_event_hash,
            event.event_hash,
        ),
    )


def _event_from_row(row: sqlite3.Row) -> PaperSubmissionEvent:
    try:
        event_type = PaperSubmissionEventType(str(row["event_type"]))
        occurred_at = _parse_datetime(row["occurred_at"], "occurred_at")
        payload = _strict_json_object(str(row["payload_json"]), "submission event")
        event = PaperSubmissionEvent(
            order_id=str(row["order_id"]),
            sequence=_strict_int(row["sequence"], "sequence"),
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
            previous_event_hash=str(row["previous_event_hash"]),
            event_hash=str(row["event_hash"]),
        )
    except (ValueError, TypeError) as exc:
        raise PaperSubmissionIntegrityError("invalid persisted submission event") from exc
    _validate_id(event.order_id, "event order_id")
    _validate_hash(event.previous_event_hash, "event previous_event_hash")
    _validate_hash(event.event_hash, "event_hash")
    expected = _event_hash(
        order_id=event.order_id,
        sequence=event.sequence,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        payload=event.payload,
        previous_event_hash=event.previous_event_hash,
    )
    if event.event_hash != expected:
        raise PaperSubmissionIntegrityError("submission event hash mismatch")
    if str(row["occurred_at"]) != _iso(event.occurred_at):
        raise PaperSubmissionIntegrityError("submission event timestamp is not canonical")
    if str(row["payload_json"]) != _canonical_json(event.payload):
        raise PaperSubmissionIntegrityError("submission event payload is not canonical")
    return event


def _derive_state(
    *,
    binding: PaperSubmissionBinding,
    events: tuple[PaperSubmissionEvent, ...],
) -> PaperSubmissionState:
    expected_sequence = 1
    previous_hash = _GENESIS_HASH
    status: PaperSubmissionStatus | None = None
    attempt_count = 0
    absence_count = 0
    broker_order_id: str | None = None
    broker_client_order_id: str | None = None
    updated_at = binding.created_at
    seen_request_ids: set[str] = set()

    for event in events:
        if event.order_id != binding.order_id:
            raise PaperSubmissionIntegrityError("submission event order identity mismatch")
        if event.sequence != expected_sequence:
            raise PaperSubmissionIntegrityError(
                "submission event sequence gap or reordering detected"
            )
        if event.previous_event_hash != previous_hash:
            raise PaperSubmissionIntegrityError(
                "submission event previous-hash linkage mismatch"
            )
        if event.occurred_at.astimezone(timezone.utc) < updated_at.astimezone(timezone.utc):
            raise PaperSubmissionIntegrityError("submission event timestamps moved backwards")
        if event.payload.get("binding_hash") != binding.fingerprint:
            raise PaperSubmissionIntegrityError("submission event binding hash mismatch")

        if event.event_type is PaperSubmissionEventType.PREPARED:
            if event.sequence != 1 or status is not None:
                raise PaperSubmissionIntegrityError("PREPARED must be first event")
            if set(event.payload) != {"binding_hash"}:
                raise PaperSubmissionIntegrityError("PREPARED payload is non-canonical")
            status = PaperSubmissionStatus.PREPARED

        elif event.event_type is PaperSubmissionEventType.SUBMIT_ATTEMPT_UNKNOWN:
            if status is not PaperSubmissionStatus.PREPARED or attempt_count != 0:
                raise PaperSubmissionIntegrityError(
                    "submit-attempt event has invalid predecessor state"
                )
            _validate_id(_required_str(event.payload, "attempt_id"), "attempt_id")
            if event.payload.get("client_order_id") != binding.client_order_id:
                raise PaperSubmissionIntegrityError(
                    "submit-attempt client_order_id mismatch"
                )
            if set(event.payload) != {
                "attempt_id",
                "binding_hash",
                "client_order_id",
            }:
                raise PaperSubmissionIntegrityError(
                    "submit-attempt payload is non-canonical"
                )
            attempt_count = 1
            status = PaperSubmissionStatus.UNKNOWN

        elif event.event_type is PaperSubmissionEventType.RECONCILIATION_ABSENT:
            if status is not PaperSubmissionStatus.UNKNOWN:
                raise PaperSubmissionIntegrityError(
                    "absence reconciliation has invalid predecessor state"
                )
            request_id = _required_str(event.payload, "request_id")
            _validate_request_id(request_id)
            if request_id in seen_request_ids:
                raise PaperSubmissionIntegrityError(
                    "duplicate reconciliation request_id in event chain"
                )
            seen_request_ids.add(request_id)
            if event.payload.get("client_order_id") != binding.client_order_id:
                raise PaperSubmissionIntegrityError(
                    "absence reconciliation client_order_id mismatch"
                )
            if event.payload.get("lookup") != "client_order_id":
                raise PaperSubmissionIntegrityError(
                    "absence reconciliation lookup must be client_order_id"
                )
            if set(event.payload) != {
                "binding_hash",
                "client_order_id",
                "lookup",
                "request_id",
            }:
                raise PaperSubmissionIntegrityError(
                    "absence reconciliation payload is non-canonical"
                )
            absence_count += 1

        elif event.event_type is PaperSubmissionEventType.RECONCILED_ACKNOWLEDGED:
            if status is not PaperSubmissionStatus.UNKNOWN:
                raise PaperSubmissionIntegrityError(
                    "ack reconciliation has invalid predecessor state"
                )
            request_id = _required_str(event.payload, "request_id")
            _validate_request_id(request_id)
            if request_id in seen_request_ids:
                raise PaperSubmissionIntegrityError(
                    "duplicate reconciliation request_id in event chain"
                )
            seen_request_ids.add(request_id)
            candidate_order_id = _required_str(event.payload, "broker_order_id")
            _validate_broker_order_id(candidate_order_id)
            candidate_client_id = _required_str(
                event.payload, "broker_client_order_id"
            )
            _validate_client_order_id(candidate_client_id)
            if candidate_client_id != binding.client_order_id:
                raise PaperSubmissionIntegrityError(
                    "ack broker client_order_id mismatch"
                )
            candidate_payload_hash = _required_str(
                event.payload, "broker_order_payload_hash"
            )
            _validate_hash(candidate_payload_hash, "broker_order_payload_hash")
            if candidate_payload_hash != binding.order_payload_hash:
                raise PaperSubmissionIntegrityError("ack broker payload hash mismatch")
            if set(event.payload) != {
                "binding_hash",
                "broker_client_order_id",
                "broker_order_id",
                "broker_order_payload_hash",
                "request_id",
            }:
                raise PaperSubmissionIntegrityError(
                    "ack reconciliation payload is non-canonical"
                )
            broker_order_id = candidate_order_id
            broker_client_order_id = candidate_client_id
            status = PaperSubmissionStatus.ACKNOWLEDGED

        expected_sequence += 1
        previous_hash = event.event_hash
        updated_at = event.occurred_at

    if status is None:
        raise PaperSubmissionIntegrityError("submission event history has no state")
    return _make_state(
        order_id=binding.order_id,
        client_order_id=binding.client_order_id,
        binding_hash=binding.fingerprint,
        status=status,
        event_sequence=len(events),
        event_head_hash=events[-1].event_hash,
        attempt_count=attempt_count,
        absence_observation_count=absence_count,
        broker_order_id=broker_order_id,
        broker_client_order_id=broker_client_order_id,
        updated_at=updated_at,
    )


def _make_state(
    *,
    order_id: str,
    client_order_id: str,
    binding_hash: str,
    status: PaperSubmissionStatus,
    event_sequence: int,
    event_head_hash: str,
    attempt_count: int,
    absence_observation_count: int,
    broker_order_id: str | None,
    broker_client_order_id: str | None,
    updated_at: datetime,
) -> PaperSubmissionState:
    return PaperSubmissionState(
        order_id=order_id,
        client_order_id=client_order_id,
        binding_hash=binding_hash,
        status=status,
        event_sequence=event_sequence,
        event_head_hash=event_head_hash,
        attempt_count=attempt_count,
        absence_observation_count=absence_observation_count,
        broker_order_id=broker_order_id,
        broker_client_order_id=broker_client_order_id,
        updated_at=updated_at,
        control_hash=_control_hash(
            order_id=order_id,
            client_order_id=client_order_id,
            binding_hash=binding_hash,
            status=status,
            event_sequence=event_sequence,
            event_head_hash=event_head_hash,
            attempt_count=attempt_count,
            absence_observation_count=absence_observation_count,
            broker_order_id=broker_order_id,
            broker_client_order_id=broker_client_order_id,
            updated_at=updated_at,
        ),
    )


def _control_hash(
    *,
    order_id: str,
    client_order_id: str,
    binding_hash: str,
    status: PaperSubmissionStatus,
    event_sequence: int,
    event_head_hash: str,
    attempt_count: int,
    absence_observation_count: int,
    broker_order_id: str | None,
    broker_client_order_id: str | None,
    updated_at: datetime,
) -> str:
    return _hash_payload(
        {
            "absence_observation_count": absence_observation_count,
            "attempt_count": attempt_count,
            "binding_hash": binding_hash,
            "broker_client_order_id": broker_client_order_id,
            "broker_order_id": broker_order_id,
            "client_order_id": client_order_id,
            "event_head_hash": event_head_hash,
            "event_sequence": event_sequence,
            "order_id": order_id,
            "status": status.value,
            "updated_at": _iso(updated_at),
        }
    )


def _insert_control(conn: sqlite3.Connection, state: PaperSubmissionState) -> None:
    conn.execute(
        """
        INSERT INTO alpaca_paper_submission_control(
            order_id, client_order_id, binding_hash, status, event_sequence,
            event_head_hash, attempt_count, absence_observation_count,
            broker_order_id, broker_client_order_id, updated_at, control_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _control_values(state),
    )


def _update_control(conn: sqlite3.Connection, state: PaperSubmissionState) -> None:
    cursor = conn.execute(
        """
        UPDATE alpaca_paper_submission_control
        SET client_order_id = ?, binding_hash = ?, status = ?, event_sequence = ?,
            event_head_hash = ?, attempt_count = ?, absence_observation_count = ?,
            broker_order_id = ?, broker_client_order_id = ?, updated_at = ?, control_hash = ?
        WHERE order_id = ?
        """,
        (
            state.client_order_id,
            state.binding_hash,
            state.status.value,
            state.event_sequence,
            state.event_head_hash,
            state.attempt_count,
            state.absence_observation_count,
            state.broker_order_id,
            state.broker_client_order_id,
            _iso(state.updated_at),
            state.control_hash,
            state.order_id,
        ),
    )
    if cursor.rowcount != 1:
        raise PaperSubmissionIntegrityError("submission control anchor update failed")


def _control_values(state: PaperSubmissionState) -> tuple[object, ...]:
    return (
        state.order_id,
        state.client_order_id,
        state.binding_hash,
        state.status.value,
        state.event_sequence,
        state.event_head_hash,
        state.attempt_count,
        state.absence_observation_count,
        state.broker_order_id,
        state.broker_client_order_id,
        _iso(state.updated_at),
        state.control_hash,
    )


def _control_from_row(row: sqlite3.Row) -> PaperSubmissionState:
    try:
        status = PaperSubmissionStatus(str(row["status"]))
        state = _make_state(
            order_id=str(row["order_id"]),
            client_order_id=str(row["client_order_id"]),
            binding_hash=str(row["binding_hash"]),
            status=status,
            event_sequence=_strict_int(row["event_sequence"], "event_sequence"),
            event_head_hash=str(row["event_head_hash"]),
            attempt_count=_strict_int(row["attempt_count"], "attempt_count"),
            absence_observation_count=_strict_int(
                row["absence_observation_count"], "absence_observation_count"
            ),
            broker_order_id=(
                str(row["broker_order_id"])
                if row["broker_order_id"] is not None
                else None
            ),
            broker_client_order_id=(
                str(row["broker_client_order_id"])
                if row["broker_client_order_id"] is not None
                else None
            ),
            updated_at=_parse_datetime(row["updated_at"], "updated_at"),
        )
    except (ValueError, TypeError) as exc:
        raise PaperSubmissionIntegrityError("invalid submission control row") from exc
    _validate_id(state.order_id, "control order_id")
    _validate_client_order_id(state.client_order_id)
    _validate_hash(state.binding_hash, "control binding_hash")
    _validate_hash(state.event_head_hash, "control event_head_hash")
    if state.event_sequence <= 0 or state.attempt_count < 0 or state.absence_observation_count < 0:
        raise PaperSubmissionIntegrityError("submission control counters are invalid")
    if state.broker_order_id is not None:
        _validate_broker_order_id(state.broker_order_id)
    if state.broker_client_order_id is not None:
        _validate_client_order_id(state.broker_client_order_id)
    if str(row["control_hash"]) != state.control_hash:
        raise PaperSubmissionIntegrityError("submission control hash mismatch")
    if str(row["updated_at"]) != _iso(state.updated_at):
        raise PaperSubmissionIntegrityError("submission control timestamp is not canonical")
    return state


def _assert_no_orphan_state(conn: sqlite3.Connection, order_id: str) -> None:
    event = conn.execute(
        "SELECT 1 FROM alpaca_paper_submission_events WHERE order_id = ? LIMIT 1",
        (order_id,),
    ).fetchone()
    control = conn.execute(
        "SELECT 1 FROM alpaca_paper_submission_control WHERE order_id = ? LIMIT 1",
        (order_id,),
    ).fetchone()
    if event is not None or control is not None:
        raise PaperSubmissionIntegrityError(
            "submission state exists without immutable binding"
        )


def _event_by_payload_value(
    events: tuple[PaperSubmissionEvent, ...],
    *,
    event_type: PaperSubmissionEventType,
    key: str,
    value: str,
) -> PaperSubmissionEvent | None:
    for event in events:
        if event.event_type is event_type and event.payload.get(key) == value:
            return event
    return None


def _reject_reused_request_id(
    events: tuple[PaperSubmissionEvent, ...], request_id: str
) -> None:
    for event in events:
        if event.payload.get("request_id") == request_id:
            raise PaperSubmissionConflict(
                "broker reconciliation X-Request-ID was already used for different evidence"
            )


def _strict_mapping(value: Mapping[str, object], label: str) -> Mapping[str, object]:
    try:
        raw = _canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise PaperSubmissionIntegrityError(f"{label} is not canonical JSON") from exc
    return _strict_json_object(raw, label)


def _strict_json_object(raw: str, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw, parse_constant=lambda token: _raise_json_constant(token))
    except (json.JSONDecodeError, ValueError) as exc:
        raise PaperSubmissionIntegrityError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise PaperSubmissionIntegrityError(f"{label} root must be an object")
    return value


def _raise_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _parse_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    _require_aware(parsed, label)
    return parsed


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_payload(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be canonical text <= 128 characters")


def _validate_client_order_id(value: str) -> None:
    if not isinstance(value, str) or not _CLIENT_ORDER_ID_RE.fullmatch(value):
        raise ValueError(
            "client_order_id must be canonical Alpaca client text <= 128 characters"
        )


def _validate_broker_order_id(value: str) -> None:
    if not isinstance(value, str) or not _BROKER_ORDER_ID_RE.fullmatch(value):
        raise ValueError("broker_order_id must be canonical text <= 128 characters")


def _validate_request_id(value: str) -> None:
    if not isinstance(value, str) or not _REQUEST_ID_RE.fullmatch(value):
        raise ValueError("request_id must be visible ASCII <= 256 characters")


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256 hex")
