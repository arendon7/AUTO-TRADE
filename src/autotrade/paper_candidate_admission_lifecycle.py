from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3

import autotrade.paper_candidate_admission as admission
from autotrade.paper_candidate_admission import (
    PaperCandidateAdmissionReceipt,
    PaperCandidateAdmissionStatus,
)
from autotrade.persistence import SQLiteRuntime


LIFECYCLE_EVENT_VERSION = "W85_PAPER_CANDIDATE_LIFECYCLE_EVENT_V1"
ELIGIBILITY_PROJECTION_VERSION = "W85_PAPER_CANDIDATE_ELIGIBILITY_PROJECTION_V1"
ZERO_EVENT_HASH = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class PaperCandidateLifecycleError(RuntimeError):
    pass


class PaperCandidateLifecycleIntegrityError(PaperCandidateLifecycleError):
    pass


class PaperCandidateLifecycleConflict(PaperCandidateLifecycleError):
    pass


class PaperCandidateLifecycleAction(StrEnum):
    SUSPEND = "SUSPEND"
    REINSTATE = "REINSTATE"
    REVOKE = "REVOKE"


class PaperCandidateEligibilityState(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class PaperCandidateLifecycleEvent:
    event_id: str
    contract_version: str
    ordinal: int
    authority_key: str
    admission_id: str
    admission_hash: str
    action: PaperCandidateLifecycleAction
    reason_code: str
    previous_event_hash: str
    occurred_at: datetime
    candidate_eligible_after_event: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    event_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("event_id", self.event_id),
            ("admission_id", self.admission_id),
            ("reason_code", self.reason_code),
        ):
            _require_id(value, label)
        if self.contract_version != LIFECYCLE_EVENT_VERSION:
            raise PaperCandidateLifecycleIntegrityError(
                "candidate lifecycle event version is not canonical W85"
            )
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise PaperCandidateLifecycleIntegrityError(
                "candidate lifecycle ordinal must be integer >=1"
            )
        for label, value in (
            ("authority_key", self.authority_key),
            ("admission_hash", self.admission_hash),
            ("previous_event_hash", self.previous_event_hash),
            ("event_hash", self.event_hash),
        ):
            _require_hash(value, label)
        if not isinstance(self.action, PaperCandidateLifecycleAction):
            raise PaperCandidateLifecycleIntegrityError("invalid lifecycle action")
        _require_aware(self.occurred_at, "occurred_at")
        expected_eligible = self.action is PaperCandidateLifecycleAction.REINSTATE
        if self.candidate_eligible_after_event is not expected_eligible:
            raise PaperCandidateLifecycleIntegrityError(
                "candidate eligibility flag does not match lifecycle action"
            )
        _require_no_execution_authority(
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
            label="W85 lifecycle event",
        )
        if self.event_hash != _hash(_event_payload(self, include_hash=False)):
            raise PaperCandidateLifecycleIntegrityError("lifecycle event hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _event_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PaperCandidateEligibilityProjection:
    contract_version: str
    authority_key: str
    admission_id: str
    admission_hash: str
    admission_valid_until: datetime
    lifecycle_head_hash: str
    lifecycle_events_count: int
    state: PaperCandidateEligibilityState
    observed_at: datetime
    paper_candidate_currently_eligible: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    projection_hash: str

    def __post_init__(self) -> None:
        if self.contract_version != ELIGIBILITY_PROJECTION_VERSION:
            raise PaperCandidateLifecycleIntegrityError(
                "eligibility projection version is not canonical W85"
            )
        _require_hash(self.authority_key, "authority_key")
        _require_id(self.admission_id, "admission_id")
        _require_hash(self.admission_hash, "admission_hash")
        _require_hash(self.lifecycle_head_hash, "lifecycle_head_hash")
        _require_aware(self.admission_valid_until, "admission_valid_until")
        _require_aware(self.observed_at, "observed_at")
        if (
            isinstance(self.lifecycle_events_count, bool)
            or not isinstance(self.lifecycle_events_count, int)
            or self.lifecycle_events_count < 0
        ):
            raise PaperCandidateLifecycleIntegrityError(
                "lifecycle_events_count must be integer >=0"
            )
        if not isinstance(self.state, PaperCandidateEligibilityState):
            raise PaperCandidateLifecycleIntegrityError("invalid candidate eligibility state")
        expected_eligible = self.state is PaperCandidateEligibilityState.ACTIVE
        if self.paper_candidate_currently_eligible is not expected_eligible:
            raise PaperCandidateLifecycleIntegrityError(
                "current candidate eligibility does not match projected state"
            )
        if self.state is PaperCandidateEligibilityState.EXPIRED:
            if _utc(self.observed_at) <= _utc(self.admission_valid_until):
                raise PaperCandidateLifecycleIntegrityError(
                    "EXPIRED projection requires process time after candidate validity"
                )
        elif _utc(self.observed_at) > _utc(self.admission_valid_until):
            raise PaperCandidateLifecycleIntegrityError(
                "non-expired projection cannot outlive candidate validity"
            )
        _require_no_execution_authority(
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
            label="W85 eligibility projection",
        )
        if self.projection_hash != _hash(_projection_payload(self, include_hash=False)):
            raise PaperCandidateLifecycleIntegrityError("eligibility projection hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _projection_payload(self, include_hash=True)


class SQLitePaperCandidateLifecycleRegistry:
    """Append-only suspension/reinstatement/revocation journal for W85 admission."""

    def __init__(self, runtime: SQLiteRuntime | str) -> None:
        self._runtime = runtime if isinstance(runtime, SQLiteRuntime) else SQLiteRuntime(runtime)
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "paper_candidate_admissions" not in tables:
                raise PaperCandidateLifecycleIntegrityError(
                    "W85 lifecycle requires initialized paper_candidate_admissions"
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_candidate_admission_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_hash TEXT NOT NULL UNIQUE,
                    authority_key TEXT NOT NULL,
                    admission_id TEXT NOT NULL,
                    admission_hash TEXT NOT NULL,
                    action TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    FOREIGN KEY(admission_id)
                        REFERENCES paper_candidate_admissions(admission_id)
                );
                CREATE INDEX IF NOT EXISTS idx_candidate_lifecycle_admission_sequence
                    ON paper_candidate_admission_events(admission_id, sequence);
                """
            )
        finally:
            conn.close()

    def append(
        self,
        *,
        event_id: str,
        admission_receipt: PaperCandidateAdmissionReceipt,
        action: PaperCandidateLifecycleAction,
        reason_code: str,
    ) -> PaperCandidateLifecycleEvent:
        _require_id(event_id, "event_id")
        _require_id(reason_code, "reason_code")
        if not isinstance(action, PaperCandidateLifecycleAction):
            raise TypeError("action must be PaperCandidateLifecycleAction")
        _validate_admission(admission_receipt)
        now = _now_utc()
        _require_aware(now, "lifecycle process clock")

        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _require_admission_matches_db(conn, admission_receipt)
            existing_row = conn.execute(
                "SELECT * FROM paper_candidate_admission_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing_row is not None:
                existing = _event_from_row(existing_row)
                if (
                    existing.admission_hash != admission_receipt.admission_hash
                    or existing.action is not action
                    or existing.reason_code != reason_code
                ):
                    raise PaperCandidateLifecycleConflict(
                        f"candidate lifecycle event identity conflict: {event_id}"
                    )
                conn.execute("COMMIT")
                return existing

            rows = conn.execute(
                """
                SELECT * FROM paper_candidate_admission_events
                WHERE admission_id = ?
                ORDER BY sequence
                """,
                (admission_receipt.admission_id,),
            ).fetchall()
            events = tuple(_event_from_row(row) for row in rows)
            _validate_event_chain(events, admission_receipt)
            current = _project_state(admission_receipt, events, now)
            if current is PaperCandidateEligibilityState.EXPIRED:
                raise PaperCandidateLifecycleConflict(
                    "expired candidate admission cannot accept lifecycle events"
                )
            if current is PaperCandidateEligibilityState.REVOKED:
                raise PaperCandidateLifecycleConflict(
                    "revoked candidate admission is terminal"
                )
            if action is PaperCandidateLifecycleAction.SUSPEND:
                if current is not PaperCandidateEligibilityState.ACTIVE:
                    raise PaperCandidateLifecycleConflict(
                        "SUSPEND requires ACTIVE candidate state"
                    )
            elif action is PaperCandidateLifecycleAction.REINSTATE:
                if current is not PaperCandidateEligibilityState.SUSPENDED:
                    raise PaperCandidateLifecycleConflict(
                        "REINSTATE requires SUSPENDED candidate state"
                    )
            elif action is PaperCandidateLifecycleAction.REVOKE:
                if current not in {
                    PaperCandidateEligibilityState.ACTIVE,
                    PaperCandidateEligibilityState.SUSPENDED,
                }:
                    raise PaperCandidateLifecycleConflict(
                        "REVOKE requires ACTIVE or SUSPENDED candidate state"
                    )

            previous = events[-1] if events else None
            if previous is not None and _utc(now) <= _utc(previous.occurred_at):
                raise PaperCandidateLifecycleIntegrityError(
                    "candidate lifecycle timestamps must advance monotonically"
                )
            values = {
                "event_id": event_id,
                "contract_version": LIFECYCLE_EVENT_VERSION,
                "ordinal": previous.ordinal + 1 if previous is not None else 1,
                "authority_key": admission_receipt.authority_key,
                "admission_id": admission_receipt.admission_id,
                "admission_hash": admission_receipt.admission_hash,
                "action": action,
                "reason_code": reason_code,
                "previous_event_hash": previous.event_hash if previous is not None else ZERO_EVENT_HASH,
                "occurred_at": now,
                "candidate_eligible_after_event": action is PaperCandidateLifecycleAction.REINSTATE,
                "paper_execution_authorized": False,
                "external_execution_authorized": False,
                "runtime_execution_authorized": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            }
            event = PaperCandidateLifecycleEvent(
                **values,
                event_hash=_hash(_event_payload_from_values(values)),
            )
            conn.execute(
                """
                INSERT INTO paper_candidate_admission_events(
                    event_id, event_hash, authority_key, admission_id,
                    admission_hash, action, previous_event_hash,
                    occurred_at, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_hash,
                    event.authority_key,
                    event.admission_id,
                    event.admission_hash,
                    event.action.value,
                    event.previous_event_hash,
                    _utc(event.occurred_at).isoformat(),
                    _canonical_json(event.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return event
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def list_for_admission(
        self, admission_receipt: PaperCandidateAdmissionReceipt
    ) -> tuple[PaperCandidateLifecycleEvent, ...]:
        _validate_admission(admission_receipt)
        conn = self._runtime.connect()
        try:
            _require_admission_matches_db(conn, admission_receipt)
            rows = conn.execute(
                """
                SELECT * FROM paper_candidate_admission_events
                WHERE admission_id = ?
                ORDER BY sequence
                """,
                (admission_receipt.admission_id,),
            ).fetchall()
        finally:
            conn.close()
        events = tuple(_event_from_row(row) for row in rows)
        _validate_event_chain(events, admission_receipt)
        return events

    def current_projection(
        self, admission_receipt: PaperCandidateAdmissionReceipt
    ) -> PaperCandidateEligibilityProjection:
        _validate_admission(admission_receipt)
        now = _now_utc()
        _require_aware(now, "eligibility process clock")
        events = self.list_for_admission(admission_receipt)
        state = _project_state(admission_receipt, events, now)
        head_hash = events[-1].event_hash if events else ZERO_EVENT_HASH
        values = {
            "contract_version": ELIGIBILITY_PROJECTION_VERSION,
            "authority_key": admission_receipt.authority_key,
            "admission_id": admission_receipt.admission_id,
            "admission_hash": admission_receipt.admission_hash,
            "admission_valid_until": admission_receipt.valid_until,
            "lifecycle_head_hash": head_hash,
            "lifecycle_events_count": len(events),
            "state": state,
            "observed_at": now,
            "paper_candidate_currently_eligible": state is PaperCandidateEligibilityState.ACTIVE,
            "paper_execution_authorized": False,
            "external_execution_authorized": False,
            "runtime_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        return PaperCandidateEligibilityProjection(
            **values,
            projection_hash=_hash(_projection_payload_from_values(values)),
        )


def _project_state(
    admission_receipt: PaperCandidateAdmissionReceipt,
    events: tuple[PaperCandidateLifecycleEvent, ...],
    now: datetime,
) -> PaperCandidateEligibilityState:
    if admission_receipt.valid_until is None:
        raise PaperCandidateLifecycleIntegrityError(
            "candidate lifecycle requires finite PASS admission validity"
        )
    if events and events[-1].action is PaperCandidateLifecycleAction.REVOKE:
        return PaperCandidateEligibilityState.REVOKED
    if _utc(now) > _utc(admission_receipt.valid_until):
        return PaperCandidateEligibilityState.EXPIRED
    if not events or events[-1].action is PaperCandidateLifecycleAction.REINSTATE:
        return PaperCandidateEligibilityState.ACTIVE
    if events[-1].action is PaperCandidateLifecycleAction.SUSPEND:
        return PaperCandidateEligibilityState.SUSPENDED
    raise PaperCandidateLifecycleIntegrityError("unprojectable candidate lifecycle state")


def _validate_admission(value: PaperCandidateAdmissionReceipt) -> None:
    if not isinstance(value, PaperCandidateAdmissionReceipt):
        raise TypeError("admission_receipt must be PaperCandidateAdmissionReceipt")
    expected = admission._hash(admission._receipt_payload(value, include_hash=False))
    if value.admission_hash != expected:
        raise PaperCandidateLifecycleIntegrityError("candidate admission hash mismatch")
    if (
        value.status is not PaperCandidateAdmissionStatus.PASS
        or value.paper_candidate_authorized is not True
        or value.valid_until is None
    ):
        raise PaperCandidateLifecycleIntegrityError(
            "candidate lifecycle requires a finite PASS W85 admission"
        )
    _require_no_execution_authority(
        paper_execution=value.paper_execution_authorized,
        external=value.external_execution_authorized,
        runtime=value.runtime_execution_authorized,
        capital=value.capital_authority,
        live=value.live_trading,
        label="source admission",
    )


def _require_admission_matches_db(
    conn: sqlite3.Connection, receipt: PaperCandidateAdmissionReceipt
) -> None:
    row = conn.execute(
        "SELECT * FROM paper_candidate_admissions WHERE admission_id = ?",
        (receipt.admission_id,),
    ).fetchone()
    if row is None:
        raise PaperCandidateLifecycleIntegrityError(
            "candidate lifecycle source admission is absent from durable W85 journal"
        )
    durable = admission._receipt_from_row(row)
    if durable != receipt:
        raise PaperCandidateLifecycleIntegrityError(
            "candidate lifecycle source differs from durable W85 admission"
        )


def _validate_event_chain(
    events: tuple[PaperCandidateLifecycleEvent, ...],
    admission_receipt: PaperCandidateAdmissionReceipt,
) -> None:
    previous: PaperCandidateLifecycleEvent | None = None
    state = PaperCandidateEligibilityState.ACTIVE
    for expected_ordinal, event in enumerate(events, start=1):
        if event.ordinal != expected_ordinal:
            raise PaperCandidateLifecycleIntegrityError(
                "candidate lifecycle ordinal discontinuity"
            )
        if (
            event.authority_key != admission_receipt.authority_key
            or event.admission_id != admission_receipt.admission_id
            or event.admission_hash != admission_receipt.admission_hash
        ):
            raise PaperCandidateLifecycleIntegrityError(
                "candidate lifecycle changed source admission identity"
            )
        expected_previous = previous.event_hash if previous is not None else ZERO_EVENT_HASH
        if event.previous_event_hash != expected_previous:
            raise PaperCandidateLifecycleIntegrityError(
                "candidate lifecycle predecessor hash discontinuity"
            )
        if previous is not None and _utc(event.occurred_at) <= _utc(previous.occurred_at):
            raise PaperCandidateLifecycleIntegrityError(
                "candidate lifecycle timestamp regression"
            )
        if admission_receipt.valid_until is None or _utc(event.occurred_at) > _utc(
            admission_receipt.valid_until
        ):
            raise PaperCandidateLifecycleIntegrityError(
                "candidate lifecycle event occurred after admission expiry"
            )
        if event.action is PaperCandidateLifecycleAction.SUSPEND:
            if state is not PaperCandidateEligibilityState.ACTIVE:
                raise PaperCandidateLifecycleIntegrityError(
                    "invalid historical SUSPEND transition"
                )
            state = PaperCandidateEligibilityState.SUSPENDED
        elif event.action is PaperCandidateLifecycleAction.REINSTATE:
            if state is not PaperCandidateEligibilityState.SUSPENDED:
                raise PaperCandidateLifecycleIntegrityError(
                    "invalid historical REINSTATE transition"
                )
            state = PaperCandidateEligibilityState.ACTIVE
        elif event.action is PaperCandidateLifecycleAction.REVOKE:
            if state not in {
                PaperCandidateEligibilityState.ACTIVE,
                PaperCandidateEligibilityState.SUSPENDED,
            }:
                raise PaperCandidateLifecycleIntegrityError(
                    "invalid historical REVOKE transition"
                )
            state = PaperCandidateEligibilityState.REVOKED
        if previous is not None and previous.action is PaperCandidateLifecycleAction.REVOKE:
            raise PaperCandidateLifecycleIntegrityError(
                "candidate lifecycle contains event after terminal revocation"
            )
        previous = event


def _event_from_row(row: sqlite3.Row) -> PaperCandidateLifecycleEvent:
    raw = row["event_json"]
    if not isinstance(raw, str):
        raise PaperCandidateLifecycleIntegrityError("lifecycle event JSON must be text")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PaperCandidateLifecycleIntegrityError("lifecycle event JSON invalid") from exc
    event = _event_from_dict(payload)
    expected = {
        "event_id": event.event_id,
        "event_hash": event.event_hash,
        "authority_key": event.authority_key,
        "admission_id": event.admission_id,
        "admission_hash": event.admission_hash,
        "action": event.action.value,
        "previous_event_hash": event.previous_event_hash,
        "occurred_at": _utc(event.occurred_at).isoformat(),
        "event_json": _canonical_json(event.to_dict()),
    }
    for key, expected_value in expected.items():
        if str(row[key]) != expected_value:
            raise PaperCandidateLifecycleIntegrityError(
                f"candidate lifecycle SQLite column mismatch: {key}"
            )
    return event


def _event_from_dict(value: object) -> PaperCandidateLifecycleEvent:
    if not isinstance(value, dict):
        raise PaperCandidateLifecycleIntegrityError("lifecycle event must be object")
    try:
        action = PaperCandidateLifecycleAction(_string(value, "action"))
    except ValueError as exc:
        raise PaperCandidateLifecycleIntegrityError("lifecycle action invalid") from exc
    occurred_raw = _string(value, "occurred_at")
    try:
        occurred_at = datetime.fromisoformat(occurred_raw)
    except ValueError as exc:
        raise PaperCandidateLifecycleIntegrityError("occurred_at invalid") from exc
    return PaperCandidateLifecycleEvent(
        event_id=_string(value, "event_id"),
        contract_version=_string(value, "contract_version"),
        ordinal=_integer(value, "ordinal"),
        authority_key=_string(value, "authority_key"),
        admission_id=_string(value, "admission_id"),
        admission_hash=_string(value, "admission_hash"),
        action=action,
        reason_code=_string(value, "reason_code"),
        previous_event_hash=_string(value, "previous_event_hash"),
        occurred_at=occurred_at,
        candidate_eligible_after_event=_boolean(value, "candidate_eligible_after_event"),
        paper_execution_authorized=_boolean(value, "paper_execution_authorized"),
        external_execution_authorized=_boolean(value, "external_execution_authorized"),
        runtime_execution_authorized=_boolean(value, "runtime_execution_authorized"),
        capital_authority=_string(value, "capital_authority"),
        live_trading=_string(value, "live_trading"),
        event_hash=_string(value, "event_hash"),
    )


def _event_payload(value: PaperCandidateLifecycleEvent, *, include_hash: bool) -> dict[str, object]:
    names = tuple(
        field
        for field in PaperCandidateLifecycleEvent.__dataclass_fields__
        if field != "event_hash"
    )
    payload = _event_payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["event_hash"] = value.event_hash
    return payload


def _event_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    action = payload["action"]
    occurred_at = payload["occurred_at"]
    if not isinstance(action, PaperCandidateLifecycleAction):
        raise PaperCandidateLifecycleIntegrityError("event action type invalid")
    if not isinstance(occurred_at, datetime):
        raise PaperCandidateLifecycleIntegrityError("event occurred_at type invalid")
    payload["action"] = action.value
    payload["occurred_at"] = _utc(occurred_at).isoformat()
    return payload


def _projection_payload(
    value: PaperCandidateEligibilityProjection, *, include_hash: bool
) -> dict[str, object]:
    names = tuple(
        field
        for field in PaperCandidateEligibilityProjection.__dataclass_fields__
        if field != "projection_hash"
    )
    payload = _projection_payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["projection_hash"] = value.projection_hash
    return payload


def _projection_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    state = payload["state"]
    admission_valid_until = payload["admission_valid_until"]
    observed_at = payload["observed_at"]
    if not isinstance(state, PaperCandidateEligibilityState):
        raise PaperCandidateLifecycleIntegrityError("projection state type invalid")
    if not isinstance(admission_valid_until, datetime) or not isinstance(observed_at, datetime):
        raise PaperCandidateLifecycleIntegrityError("projection datetime type invalid")
    payload["state"] = state.value
    payload["admission_valid_until"] = _utc(admission_valid_until).isoformat()
    payload["observed_at"] = _utc(observed_at).isoformat()
    return payload


def _require_no_execution_authority(
    *, paper_execution: bool, external: bool, runtime: bool, capital: str, live: str, label: str
) -> None:
    if (
        paper_execution is not False
        or external is not False
        or runtime is not False
        or capital != "NONE"
        or live != "BLOCKED"
    ):
        raise PaperCandidateLifecycleIntegrityError(
            f"{label} may not grant PAPER execution, external/runtime execution, capital, or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperCandidateLifecycleIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperCandidateLifecycleIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperCandidateLifecycleIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _string(value: dict[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise PaperCandidateLifecycleIntegrityError(f"{key} must be string")
    return raw


def _integer(value: dict[str, object], key: str) -> int:
    raw = value.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise PaperCandidateLifecycleIntegrityError(f"{key} must be integer")
    return raw


def _boolean(value: dict[str, object], key: str) -> bool:
    raw = value.get(key)
    if not isinstance(raw, bool):
        raise PaperCandidateLifecycleIntegrityError(f"{key} must be boolean")
    return raw


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "ELIGIBILITY_PROJECTION_VERSION",
    "LIFECYCLE_EVENT_VERSION",
    "ZERO_EVENT_HASH",
    "PaperCandidateEligibilityProjection",
    "PaperCandidateEligibilityState",
    "PaperCandidateLifecycleAction",
    "PaperCandidateLifecycleConflict",
    "PaperCandidateLifecycleError",
    "PaperCandidateLifecycleEvent",
    "PaperCandidateLifecycleIntegrityError",
    "SQLitePaperCandidateLifecycleRegistry",
]
