from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json
import re

from autotrade.persistence import SQLiteRuntime

from .alpaca_paper_crypto_protection_coordinator import PreparedCryptoProtectionPackage


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
DEFAULT_PROTECTION_OPERATOR_DECISION_TTL = timedelta(seconds=30)


class CryptoProtectionOperatorDecisionError(RuntimeError):
    pass


class CryptoProtectionOperatorDecisionConflict(CryptoProtectionOperatorDecisionError):
    pass


class CryptoProtectionOperatorDecisionIntegrityError(CryptoProtectionOperatorDecisionError):
    pass


class CryptoProtectionOperatorDecisionStatus(str, Enum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"


@dataclass(frozen=True, slots=True)
class CryptoProtectionOperatorDecisionContext:
    preparation_hash: str
    prepared_package_hash: str
    lifecycle_id: str
    order_id: str
    client_order_id: str
    entry_reconciliation_fingerprint: str
    risk_decision_fingerprint: str
    lifecycle_binding_hash: str
    lifecycle_control_hash: str
    lifecycle_event_head_hash: str
    quantity: str
    stop_price: str
    limit_price: str
    attempt_id: str
    context_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("preparation_hash", self.preparation_hash),
            ("prepared_package_hash", self.prepared_package_hash),
            ("entry_reconciliation_fingerprint", self.entry_reconciliation_fingerprint),
            ("risk_decision_fingerprint", self.risk_decision_fingerprint),
            ("lifecycle_binding_hash", self.lifecycle_binding_hash),
            ("lifecycle_control_hash", self.lifecycle_control_hash),
            ("lifecycle_event_head_hash", self.lifecycle_event_head_hash),
            ("context_hash", self.context_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("lifecycle_id", self.lifecycle_id),
            ("order_id", self.order_id),
            ("client_order_id", self.client_order_id),
            ("attempt_id", self.attempt_id),
        ):
            _require_id(value, label)
        for label, value in (
            ("quantity", self.quantity),
            ("stop_price", self.stop_price),
            ("limit_price", self.limit_price),
        ):
            if not isinstance(value, str) or not value or value.startswith("-"):
                raise ValueError(f"{label} must be canonical positive decimal text")
        expected = _hash_json(
            {
                "kind": "R6_CRYPTO_PROTECTION_OPERATOR_CONTEXT",
                **_context_payload(self, include_hash=False),
            }
        )
        if self.context_hash != expected:
            raise ValueError("protection operator context hash mismatch")

    @classmethod
    def from_prepared_package(
        cls,
        package: PreparedCryptoProtectionPackage,
        *,
        attempt_id: str,
    ) -> "CryptoProtectionOperatorDecisionContext":
        if not isinstance(package, PreparedCryptoProtectionPackage):
            raise TypeError("prepared crypto protection package is required")
        if package.network_write_authorized is not False:
            raise ValueError("prepared protection package cannot carry network authority")
        if package.next_action != "OPERATOR_DECISION_REQUIRED":
            raise ValueError("prepared protection package is not operator-gated")
        _require_id(attempt_id, "attempt_id")
        values = {
            "prepared_package_hash": package.package_hash,
            "lifecycle_id": package.lifecycle_id,
            "order_id": package.order_id,
            "client_order_id": package.client_order_id,
            "entry_reconciliation_fingerprint": package.entry_reconciliation_fingerprint,
            "risk_decision_fingerprint": package.risk_decision_fingerprint,
            "lifecycle_binding_hash": package.lifecycle_binding_hash,
            "lifecycle_control_hash": package.lifecycle_control_hash,
            "lifecycle_event_head_hash": package.lifecycle_event_head_hash,
            "quantity": format(package.quantity, "f"),
            "stop_price": format(package.stop_price, "f"),
            "limit_price": format(package.limit_price, "f"),
            "attempt_id": attempt_id,
        }
        preparation_hash = _hash_json({"kind": "R6_CRYPTO_PROTECTION_PREPARATION", **values})
        values["preparation_hash"] = preparation_hash
        context_hash = _hash_json({"kind": "R6_CRYPTO_PROTECTION_OPERATOR_CONTEXT", **values})
        return cls(**values, context_hash=context_hash)

    def canonical_payload(self) -> dict[str, object]:
        return _context_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class CryptoProtectionOperatorDecision:
    decision_id: str
    context: CryptoProtectionOperatorDecisionContext
    operator_id: str
    note: str
    issued_at: datetime
    valid_until: datetime
    decision_hash: str

    def __post_init__(self) -> None:
        _require_id(self.decision_id, "decision_id")
        if not isinstance(self.context, CryptoProtectionOperatorDecisionContext):
            raise TypeError("protection operator decision requires exact context")
        _require_id(self.operator_id, "operator_id")
        if not isinstance(self.note, str) or len(self.note) > 512:
            raise ValueError("operator note must be <= 512 characters")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.valid_until, "valid_until")
        if self.issued_at >= self.valid_until:
            raise ValueError("protection operator decision must expire after issuance")
        _require_hash(self.decision_hash, "decision_hash")
        if self.decision_hash != _hash_json(_decision_payload(self, include_hash=False)):
            raise ValueError("protection operator decision hash mismatch")

    def is_valid_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        return self.issued_at <= instant < self.valid_until

    def canonical_payload(self) -> dict[str, object]:
        return _decision_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class CryptoProtectionOperatorDecisionState:
    decision: CryptoProtectionOperatorDecision
    status: CryptoProtectionOperatorDecisionStatus
    consumed_attempt_id: str | None
    consumed_at: datetime | None
    event_sequence: int
    event_head_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, CryptoProtectionOperatorDecisionStatus):
            raise TypeError("protection operator state status is invalid")
        if self.status is CryptoProtectionOperatorDecisionStatus.ISSUED:
            if self.consumed_attempt_id is not None or self.consumed_at is not None:
                raise ValueError("ISSUED protection decision cannot be consumed")
        else:
            if self.consumed_attempt_id is None or self.consumed_at is None:
                raise ValueError("CONSUMED protection decision requires attempt/time")
            _require_id(self.consumed_attempt_id, "consumed_attempt_id")
            _require_aware(self.consumed_at, "consumed_at")
        if self.event_sequence < 1:
            raise ValueError("protection decision event sequence must be positive")
        _require_hash(self.event_head_hash, "event_head_hash")


class SQLiteCryptoProtectionOperatorDecisionRegistry:
    """Append-only, tamper-evident human authority for protective exits only.

    Recording approval never sends an order and never imports broker credentials.
    One decision is bound to one immutable protection package and one attempt_id.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        if not isinstance(runtime, SQLiteRuntime):
            raise TypeError("protection operator registry requires SQLiteRuntime")
        self._runtime = runtime
        self._ensure_schema()

    def record_operator_approval(
        self,
        *,
        context: CryptoProtectionOperatorDecisionContext,
        operator_id: str,
        note: str,
        now: datetime,
        ttl: timedelta = DEFAULT_PROTECTION_OPERATOR_DECISION_TTL,
    ) -> CryptoProtectionOperatorDecision:
        if not isinstance(context, CryptoProtectionOperatorDecisionContext):
            raise TypeError("exact protection operator context is required")
        _require_id(operator_id, "operator_id")
        if not isinstance(note, str) or len(note) > 512:
            raise ValueError("operator note must be <= 512 characters")
        _require_aware(now, "now")
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0) or ttl > timedelta(minutes=2):
            raise ValueError("protection operator decision ttl must be >0 and <=2 minutes")
        instant = now.astimezone(timezone.utc)
        valid_until = instant + ttl
        decision_id = "r6c-protect-approval-" + sha256(
            f"{context.context_hash}|{operator_id}|{instant.isoformat()}".encode("utf-8")
        ).hexdigest()[:32]
        values = {
            "decision_id": decision_id,
            "context": context,
            "operator_id": operator_id,
            "note": note,
            "issued_at": instant,
            "valid_until": valid_until,
        }
        decision = CryptoProtectionOperatorDecision(
            **values,
            decision_hash=_hash_json(_decision_payload_from_values(values)),
        )
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT COUNT(*) AS n FROM alpaca_crypto_protection_operator_events WHERE preparation_hash = ?",
                (context.preparation_hash,),
            ).fetchone()
            if existing is not None and int(existing["n"]) != 0:
                raise CryptoProtectionOperatorDecisionConflict("protection preparation already has human authority")
            event_json = _event_json(
                decision=decision,
                status=CryptoProtectionOperatorDecisionStatus.ISSUED,
                attempt_id=None,
                event_at=instant,
                sequence=1,
            )
            event_hash = _event_hash(
                preparation_hash=context.preparation_hash,
                sequence=1,
                event_json=event_json,
                previous_event_hash=None,
            )
            conn.execute(
                """
                INSERT INTO alpaca_crypto_protection_operator_events(
                    preparation_hash, event_sequence, event_json,
                    previous_event_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (context.preparation_hash, 1, event_json, None, event_hash),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return decision

    def consume(
        self,
        *,
        decision: CryptoProtectionOperatorDecision,
        attempt_id: str,
        now: datetime,
    ) -> CryptoProtectionOperatorDecisionState:
        if not isinstance(decision, CryptoProtectionOperatorDecision):
            raise TypeError("exact protection operator decision is required")
        _require_id(attempt_id, "attempt_id")
        _require_aware(now, "now")
        if attempt_id != decision.context.attempt_id:
            raise CryptoProtectionOperatorDecisionConflict("protection decision attempt binding mismatch")
        instant = now.astimezone(timezone.utc)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            state = self._read_state(conn, decision.context.preparation_hash)
            if state.decision != decision:
                raise CryptoProtectionOperatorDecisionIntegrityError("durable protection decision differs from supplied decision")
            if state.status is CryptoProtectionOperatorDecisionStatus.CONSUMED:
                if state.consumed_attempt_id != attempt_id:
                    raise CryptoProtectionOperatorDecisionConflict("protection decision was consumed by another attempt")
                conn.rollback()
                return state
            if not decision.is_valid_at(instant):
                raise CryptoProtectionOperatorDecisionConflict("protection operator decision is expired or not yet valid")
            sequence = state.event_sequence + 1
            event_json = _event_json(
                decision=decision,
                status=CryptoProtectionOperatorDecisionStatus.CONSUMED,
                attempt_id=attempt_id,
                event_at=instant,
                sequence=sequence,
            )
            event_hash = _event_hash(
                preparation_hash=decision.context.preparation_hash,
                sequence=sequence,
                event_json=event_json,
                previous_event_hash=state.event_head_hash,
            )
            conn.execute(
                """
                INSERT INTO alpaca_crypto_protection_operator_events(
                    preparation_hash, event_sequence, event_json,
                    previous_event_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    decision.context.preparation_hash,
                    sequence,
                    event_json,
                    state.event_head_hash,
                    event_hash,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get(decision.context.preparation_hash)

    def get(self, preparation_hash: str) -> CryptoProtectionOperatorDecisionState:
        _require_hash(preparation_hash, "preparation_hash")
        conn = self._runtime.connect()
        try:
            return self._read_state(conn, preparation_hash)
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alpaca_crypto_protection_operator_events(
                    preparation_hash TEXT NOT NULL,
                    event_sequence INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    previous_event_hash TEXT,
                    event_hash TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(preparation_hash, event_sequence)
                )
                """
            )
        finally:
            conn.close()

    def _read_state(self, conn, preparation_hash: str) -> CryptoProtectionOperatorDecisionState:
        rows = conn.execute(
            """
            SELECT event_sequence, event_json, previous_event_hash, event_hash
            FROM alpaca_crypto_protection_operator_events
            WHERE preparation_hash = ?
            ORDER BY event_sequence ASC
            """,
            (preparation_hash,),
        ).fetchall()
        if not rows:
            raise KeyError(preparation_hash)
        previous_hash: str | None = None
        decision: CryptoProtectionOperatorDecision | None = None
        status = CryptoProtectionOperatorDecisionStatus.ISSUED
        consumed_attempt_id: str | None = None
        consumed_at: datetime | None = None
        for expected_sequence, row in enumerate(rows, start=1):
            sequence = int(row["event_sequence"])
            if sequence != expected_sequence:
                raise CryptoProtectionOperatorDecisionIntegrityError("protection operator event sequence gap")
            stored_previous = row["previous_event_hash"]
            if stored_previous != previous_hash:
                raise CryptoProtectionOperatorDecisionIntegrityError("protection operator event chain mismatch")
            event_json = str(row["event_json"])
            expected_hash = _event_hash(
                preparation_hash=preparation_hash,
                sequence=sequence,
                event_json=event_json,
                previous_event_hash=previous_hash,
            )
            if str(row["event_hash"]) != expected_hash:
                raise CryptoProtectionOperatorDecisionIntegrityError("protection operator event hash mismatch")
            try:
                payload = json.loads(event_json)
                parsed_decision = _decision_from_payload(payload["decision"])
                parsed_status = CryptoProtectionOperatorDecisionStatus(payload["status"])
                event_at = _datetime(payload["event_at"], "event_at")
                parsed_attempt = payload["attempt_id"]
            except Exception as exc:
                raise CryptoProtectionOperatorDecisionIntegrityError("invalid durable protection operator event") from exc
            if payload.get("event_sequence") != sequence:
                raise CryptoProtectionOperatorDecisionIntegrityError("protection operator event payload sequence mismatch")
            if decision is None:
                if parsed_status is not CryptoProtectionOperatorDecisionStatus.ISSUED or parsed_attempt is not None:
                    raise CryptoProtectionOperatorDecisionIntegrityError("first protection operator event must be ISSUED")
                decision = parsed_decision
            elif parsed_decision != decision:
                raise CryptoProtectionOperatorDecisionIntegrityError("protection operator decision changed across event chain")
            if parsed_status is CryptoProtectionOperatorDecisionStatus.CONSUMED:
                if sequence != 2 or not isinstance(parsed_attempt, str):
                    raise CryptoProtectionOperatorDecisionIntegrityError("invalid protection decision consumption event")
                _require_id(parsed_attempt, "attempt_id")
                status = parsed_status
                consumed_attempt_id = parsed_attempt
                consumed_at = event_at
            elif sequence != 1:
                raise CryptoProtectionOperatorDecisionIntegrityError("duplicate ISSUED protection operator event")
            previous_hash = str(row["event_hash"])
        assert decision is not None
        return CryptoProtectionOperatorDecisionState(
            decision=decision,
            status=status,
            consumed_attempt_id=consumed_attempt_id,
            consumed_at=consumed_at,
            event_sequence=len(rows),
            event_head_hash=previous_hash or "0" * 64,
        )


def _context_payload(
    context: CryptoProtectionOperatorDecisionContext,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload = {
        "preparation_hash": context.preparation_hash,
        "prepared_package_hash": context.prepared_package_hash,
        "lifecycle_id": context.lifecycle_id,
        "order_id": context.order_id,
        "client_order_id": context.client_order_id,
        "entry_reconciliation_fingerprint": context.entry_reconciliation_fingerprint,
        "risk_decision_fingerprint": context.risk_decision_fingerprint,
        "lifecycle_binding_hash": context.lifecycle_binding_hash,
        "lifecycle_control_hash": context.lifecycle_control_hash,
        "lifecycle_event_head_hash": context.lifecycle_event_head_hash,
        "quantity": context.quantity,
        "stop_price": context.stop_price,
        "limit_price": context.limit_price,
        "attempt_id": context.attempt_id,
    }
    if include_hash:
        payload["context_hash"] = context.context_hash
    return payload


def _context_from_payload(payload: object) -> CryptoProtectionOperatorDecisionContext:
    if not isinstance(payload, dict):
        raise ValueError("protection operator context payload must be object")
    expected = {
        "preparation_hash",
        "prepared_package_hash",
        "lifecycle_id",
        "order_id",
        "client_order_id",
        "entry_reconciliation_fingerprint",
        "risk_decision_fingerprint",
        "lifecycle_binding_hash",
        "lifecycle_control_hash",
        "lifecycle_event_head_hash",
        "quantity",
        "stop_price",
        "limit_price",
        "attempt_id",
        "context_hash",
    }
    if set(payload) != expected:
        raise ValueError("protection operator context payload keys mismatch")
    return CryptoProtectionOperatorDecisionContext(**payload)


def _decision_payload(
    decision: CryptoProtectionOperatorDecision,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload = {
        "decision_id": decision.decision_id,
        "context": decision.context.canonical_payload(),
        "operator_id": decision.operator_id,
        "note": decision.note,
        "issued_at": decision.issued_at.astimezone(timezone.utc).isoformat(),
        "valid_until": decision.valid_until.astimezone(timezone.utc).isoformat(),
    }
    if include_hash:
        payload["decision_hash"] = decision.decision_hash
    return payload


def _decision_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    context = values["context"]
    assert isinstance(context, CryptoProtectionOperatorDecisionContext)
    issued_at = values["issued_at"]
    valid_until = values["valid_until"]
    assert isinstance(issued_at, datetime) and isinstance(valid_until, datetime)
    return {
        "decision_id": values["decision_id"],
        "context": context.canonical_payload(),
        "operator_id": values["operator_id"],
        "note": values["note"],
        "issued_at": issued_at.astimezone(timezone.utc).isoformat(),
        "valid_until": valid_until.astimezone(timezone.utc).isoformat(),
    }


def _decision_from_payload(payload: object) -> CryptoProtectionOperatorDecision:
    if not isinstance(payload, dict):
        raise ValueError("protection operator decision payload must be object")
    expected = {"decision_id", "context", "operator_id", "note", "issued_at", "valid_until", "decision_hash"}
    if set(payload) != expected:
        raise ValueError("protection operator decision payload keys mismatch")
    return CryptoProtectionOperatorDecision(
        decision_id=payload["decision_id"],
        context=_context_from_payload(payload["context"]),
        operator_id=payload["operator_id"],
        note=payload["note"],
        issued_at=_datetime(payload["issued_at"], "issued_at"),
        valid_until=_datetime(payload["valid_until"], "valid_until"),
        decision_hash=payload["decision_hash"],
    )


def _event_json(
    *,
    decision: CryptoProtectionOperatorDecision,
    status: CryptoProtectionOperatorDecisionStatus,
    attempt_id: str | None,
    event_at: datetime,
    sequence: int,
) -> str:
    return json.dumps(
        {
            "decision": decision.canonical_payload(),
            "status": status.value,
            "attempt_id": attempt_id,
            "event_at": event_at.astimezone(timezone.utc).isoformat(),
            "event_sequence": sequence,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _event_hash(
    *,
    preparation_hash: str,
    sequence: int,
    event_json: str,
    previous_event_hash: str | None,
) -> str:
    return _hash_json(
        {
            "preparation_hash": preparation_hash,
            "event_sequence": sequence,
            "event_json": event_json,
            "previous_event_hash": previous_event_hash,
        }
    )


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be ISO datetime")
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, label)
    if parsed.astimezone(timezone.utc).isoformat() != value:
        raise ValueError(f"{label} must be canonical UTC ISO datetime")
    return parsed


def _require_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _require_hash(value: object, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _hash_json(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CryptoProtectionOperatorDecision",
    "CryptoProtectionOperatorDecisionConflict",
    "CryptoProtectionOperatorDecisionContext",
    "CryptoProtectionOperatorDecisionError",
    "CryptoProtectionOperatorDecisionIntegrityError",
    "CryptoProtectionOperatorDecisionState",
    "CryptoProtectionOperatorDecisionStatus",
    "DEFAULT_PROTECTION_OPERATOR_DECISION_TTL",
    "SQLiteCryptoProtectionOperatorDecisionRegistry",
]
