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

from autotrade.persistence import SQLiteRuntime

from .alpaca_paper_crypto_canary_coordinator import PreparedCryptoPaperCanaryPackage


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_GENESIS_HASH = "0" * 64
_OPERATOR_SOURCE = "HUMAN_OPERATOR"
_OPERATOR_ACTION = "APPROVE_SINGLE_CRYPTO_PAPER_CANARY_ENTRY"
_MAX_DECISION_TTL = timedelta(minutes=2)


class CryptoOperatorDecisionError(RuntimeError):
    pass


class CryptoOperatorDecisionIntegrityError(CryptoOperatorDecisionError):
    pass


class CryptoOperatorDecisionConflict(CryptoOperatorDecisionError):
    pass


class CryptoOperatorDecisionExpired(CryptoOperatorDecisionError):
    pass


class CryptoOperatorDecisionStatus(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"


class CryptoOperatorDecisionEventType(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"


@dataclass(frozen=True, slots=True)
class CryptoOperatorDecisionContext:
    environment: str
    prepared_package_hash: str
    lifecycle_id: str
    order_id: str
    client_order_id: str
    symbol: str
    account_attestation_fingerprint: str
    asset_attestation_fingerprint: str
    product_profile_fingerprint: str
    risk_decision_id: str
    risk_decision_fingerprint: str
    risk_decision_safety_state_version: int
    risk_decision_valid_until: datetime
    crypto_order_fingerprint: str
    crypto_order_payload_hash: str
    lifecycle_binding_hash: str
    lifecycle_control_hash: str
    lifecycle_event_head_hash: str
    quantity: Decimal
    limit_price: Decimal
    notional: Decimal
    prepared_at: datetime
    execution_deadline: datetime
    attempt_id: str
    preparation_hash: str

    def __post_init__(self) -> None:
        if self.environment != "PAPER":
            raise ValueError("crypto operator decision context is PAPER-only")
        for label, value in (
            ("prepared_package_hash", self.prepared_package_hash),
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("asset_attestation_fingerprint", self.asset_attestation_fingerprint),
            ("product_profile_fingerprint", self.product_profile_fingerprint),
            ("risk_decision_fingerprint", self.risk_decision_fingerprint),
            ("crypto_order_fingerprint", self.crypto_order_fingerprint),
            ("crypto_order_payload_hash", self.crypto_order_payload_hash),
            ("lifecycle_binding_hash", self.lifecycle_binding_hash),
            ("lifecycle_control_hash", self.lifecycle_control_hash),
            ("lifecycle_event_head_hash", self.lifecycle_event_head_hash),
            ("preparation_hash", self.preparation_hash),
        ):
            _validate_hash(value, label)
        for label, value in (
            ("lifecycle_id", self.lifecycle_id),
            ("order_id", self.order_id),
            ("client_order_id", self.client_order_id),
            ("risk_decision_id", self.risk_decision_id),
            ("attempt_id", self.attempt_id),
        ):
            _validate_id(value, label)
        if self.symbol.count("/") != 1 or self.symbol != self.symbol.upper():
            raise ValueError("crypto operator symbol must be canonical BASE/QUOTE")
        if (
            isinstance(self.risk_decision_safety_state_version, bool)
            or not isinstance(self.risk_decision_safety_state_version, int)
            or self.risk_decision_safety_state_version < 0
        ):
            raise ValueError("crypto operator Safety state version must be non-negative integer")
        for label, value in (
            ("risk_decision_valid_until", self.risk_decision_valid_until),
            ("prepared_at", self.prepared_at),
            ("execution_deadline", self.execution_deadline),
        ):
            _require_aware(value, label)
        for label, value in (
            ("quantity", self.quantity),
            ("limit_price", self.limit_price),
            ("notional", self.notional),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{label} must be finite and positive")
        if self.notional != self.quantity * self.limit_price:
            raise ValueError("crypto operator notional must equal quantity * limit_price")
        prepared = self.prepared_at.astimezone(timezone.utc)
        deadline = self.execution_deadline.astimezone(timezone.utc)
        decision_deadline = self.risk_decision_valid_until.astimezone(timezone.utc)
        if not prepared < deadline <= decision_deadline:
            raise ValueError("crypto operator preparation/deadline ordering is invalid")
        expected = _hash_json(_context_payload_without_hash(self))
        if self.preparation_hash != expected:
            raise ValueError("crypto operator decision preparation_hash mismatch")

    @classmethod
    def from_prepared_package(
        cls,
        package: PreparedCryptoPaperCanaryPackage,
        *,
        attempt_id: str,
    ) -> "CryptoOperatorDecisionContext":
        if not isinstance(package, PreparedCryptoPaperCanaryPackage):
            raise TypeError("prepared crypto PAPER canary package is required")
        _validate_id(attempt_id, "attempt_id")
        if package.network_write_authorized is not False:
            raise ValueError("crypto operator decision requires non-authorizing prepared package")
        if package.next_action != "OPERATOR_DECISION_REQUIRED":
            raise ValueError("prepared crypto package does not require operator decision")
        if package.order_status != "VALIDATED":
            raise ValueError("crypto operator decision requires OMS VALIDATED package")
        if package.broker_order_type != "limit" or package.time_in_force != "ioc":
            raise ValueError("crypto first-canary operator context requires LIMIT IOC")
        if package.opening_short is not False or package.uses_margin is not False:
            raise ValueError("crypto first-canary operator context must be long-only and non-margin")
        raw = {
            "environment": "PAPER",
            "prepared_package_hash": package.package_hash,
            "lifecycle_id": package.lifecycle_id,
            "order_id": package.order_id,
            "client_order_id": package.client_order_id,
            "symbol": package.symbol,
            "account_attestation_fingerprint": package.account_attestation_fingerprint,
            "asset_attestation_fingerprint": package.asset_attestation_fingerprint,
            "product_profile_fingerprint": package.product_profile_fingerprint,
            "risk_decision_id": package.risk_decision_id,
            "risk_decision_fingerprint": package.risk_decision_fingerprint,
            "risk_decision_safety_state_version": package.risk_decision_safety_state_version,
            "risk_decision_valid_until": package.risk_decision_valid_until,
            "crypto_order_fingerprint": package.crypto_order_fingerprint,
            "crypto_order_payload_hash": package.crypto_order_payload_hash,
            "lifecycle_binding_hash": package.lifecycle_binding_hash,
            "lifecycle_control_hash": package.lifecycle_control_hash,
            "lifecycle_event_head_hash": package.lifecycle_event_head_hash,
            "quantity": package.quantity,
            "limit_price": package.limit_price,
            "notional": package.notional,
            "prepared_at": package.prepared_at,
            "execution_deadline": package.execution_deadline,
            "attempt_id": attempt_id,
        }
        return cls(
            **raw,
            preparation_hash=_hash_json(_context_payload_from_values(raw)),
        )

    def to_dict(self) -> dict[str, object]:
        payload = _context_payload_without_hash(self)
        payload["preparation_hash"] = self.preparation_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CryptoOperatorDecisionContext":
        expected = {
            "environment",
            "prepared_package_hash",
            "lifecycle_id",
            "order_id",
            "client_order_id",
            "symbol",
            "account_attestation_fingerprint",
            "asset_attestation_fingerprint",
            "product_profile_fingerprint",
            "risk_decision_id",
            "risk_decision_fingerprint",
            "risk_decision_safety_state_version",
            "risk_decision_valid_until",
            "crypto_order_fingerprint",
            "crypto_order_payload_hash",
            "lifecycle_binding_hash",
            "lifecycle_control_hash",
            "lifecycle_event_head_hash",
            "quantity",
            "limit_price",
            "notional",
            "prepared_at",
            "execution_deadline",
            "attempt_id",
            "preparation_hash",
        }
        if set(payload) != expected:
            raise ValueError("crypto operator context payload is non-canonical")
        return cls(
            environment=_required_str(payload, "environment"),
            prepared_package_hash=_required_str(payload, "prepared_package_hash"),
            lifecycle_id=_required_str(payload, "lifecycle_id"),
            order_id=_required_str(payload, "order_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            symbol=_required_str(payload, "symbol"),
            account_attestation_fingerprint=_required_str(payload, "account_attestation_fingerprint"),
            asset_attestation_fingerprint=_required_str(payload, "asset_attestation_fingerprint"),
            product_profile_fingerprint=_required_str(payload, "product_profile_fingerprint"),
            risk_decision_id=_required_str(payload, "risk_decision_id"),
            risk_decision_fingerprint=_required_str(payload, "risk_decision_fingerprint"),
            risk_decision_safety_state_version=_strict_int(payload.get("risk_decision_safety_state_version"), "risk_decision_safety_state_version"),
            risk_decision_valid_until=_datetime(payload.get("risk_decision_valid_until"), "risk_decision_valid_until"),
            crypto_order_fingerprint=_required_str(payload, "crypto_order_fingerprint"),
            crypto_order_payload_hash=_required_str(payload, "crypto_order_payload_hash"),
            lifecycle_binding_hash=_required_str(payload, "lifecycle_binding_hash"),
            lifecycle_control_hash=_required_str(payload, "lifecycle_control_hash"),
            lifecycle_event_head_hash=_required_str(payload, "lifecycle_event_head_hash"),
            quantity=_decimal(payload.get("quantity"), "quantity"),
            limit_price=_decimal(payload.get("limit_price"), "limit_price"),
            notional=_decimal(payload.get("notional"), "notional"),
            prepared_at=_datetime(payload.get("prepared_at"), "prepared_at"),
            execution_deadline=_datetime(payload.get("execution_deadline"), "execution_deadline"),
            attempt_id=_required_str(payload, "attempt_id"),
            preparation_hash=_required_str(payload, "preparation_hash"),
        )


@dataclass(frozen=True, slots=True)
class CryptoOperatorDecision:
    context: CryptoOperatorDecisionContext
    operator_id: str
    source: str
    action: str
    issued_at: datetime
    expires_at: datetime
    decision_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.context, CryptoOperatorDecisionContext):
            raise ValueError("crypto operator decision context is required")
        _validate_id(self.operator_id, "operator_id")
        if self.source != _OPERATOR_SOURCE:
            raise ValueError("crypto operator decision source must be HUMAN_OPERATOR")
        if self.action != _OPERATOR_ACTION:
            raise ValueError("crypto operator decision action is not exact first-canary approval")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        issued = self.issued_at.astimezone(timezone.utc)
        expires = self.expires_at.astimezone(timezone.utc)
        prepared = self.context.prepared_at.astimezone(timezone.utc)
        package_deadline = self.context.execution_deadline.astimezone(timezone.utc)
        if issued < prepared:
            raise ValueError("crypto operator decision may not predate prepared package")
        if expires <= issued or expires - issued > _MAX_DECISION_TTL:
            raise ValueError("crypto operator decision validity window must be >0 and <=2 minutes")
        if expires > package_deadline:
            raise ValueError("crypto operator decision may not outlive prepared package")
        _validate_hash(self.decision_hash, "decision_hash")
        if self.decision_hash != _hash_json(_decision_payload_without_hash(self)):
            raise ValueError("crypto operator decision hash mismatch")

    def is_valid_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        return (
            self.issued_at.astimezone(timezone.utc)
            <= instant
            < self.expires_at.astimezone(timezone.utc)
            <= self.context.execution_deadline.astimezone(timezone.utc)
        )


@dataclass(frozen=True, slots=True)
class CryptoOperatorDecisionState:
    decision: CryptoOperatorDecision
    status: CryptoOperatorDecisionStatus
    consumed_at: datetime | None
    consumed_attempt_id: str | None
    event_sequence: int
    event_hash: str


@dataclass(frozen=True, slots=True)
class _DecisionEvent:
    sequence: int
    event_type: CryptoOperatorDecisionEventType
    preparation_hash: str
    occurred_at: datetime
    payload: Mapping[str, object]
    previous_event_hash: str
    event_hash: str


class SQLiteCryptoOperatorDecisionRegistry:
    """Tamper-evident one-shot human authority for one exact crypto PAPER package.

    This registry contains no credentials, network, OMS staging or broker APIs.
    Issuance and consumption are durable and bound to the exact attempt id.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpaca_crypto_operator_decision_events (
                    sequence INTEGER PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    preparation_hash TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS alpaca_crypto_operator_decision_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    event_sequence INTEGER NOT NULL CHECK(event_sequence >= 0),
                    event_head_hash TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                """
            )
            row = conn.execute(
                "SELECT event_sequence, event_head_hash, control_hash FROM alpaca_crypto_operator_decision_control WHERE singleton = 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO alpaca_crypto_operator_decision_control(singleton, event_sequence, event_head_hash, control_hash) VALUES (1, 0, ?, ?)",
                    (_GENESIS_HASH, _control_hash(0, _GENESIS_HASH)),
                )
        finally:
            conn.close()

    def record_operator_approval(
        self,
        *,
        context: CryptoOperatorDecisionContext,
        operator_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> CryptoOperatorDecisionState:
        if not isinstance(context, CryptoOperatorDecisionContext):
            raise TypeError("crypto operator decision context is required")
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
                raise CryptoOperatorDecisionConflict(
                    "prepared crypto canary already has different operator-decision evidence"
                )
            event = _make_event(
                sequence=sequence + 1,
                event_type=CryptoOperatorDecisionEventType.ISSUED,
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
        decision: CryptoOperatorDecision,
        attempt_id: str,
        now: datetime,
    ) -> CryptoOperatorDecisionState:
        if not isinstance(decision, CryptoOperatorDecision):
            raise TypeError("crypto operator decision is required")
        _validate_id(attempt_id, "attempt_id")
        _require_aware(now, "now")
        if decision.context.attempt_id != attempt_id:
            raise CryptoOperatorDecisionConflict("crypto operator decision is bound to another attempt")
        instant = now.astimezone(timezone.utc)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            states, sequence, head = self._verify_locked(conn)
            state = states.get(decision.context.preparation_hash)
            if state is None:
                raise KeyError(decision.context.preparation_hash)
            if state.decision != decision:
                raise CryptoOperatorDecisionConflict("durable crypto operator decision does not match supplied decision")
            if state.status is CryptoOperatorDecisionStatus.CONSUMED:
                if state.consumed_attempt_id == attempt_id:
                    conn.execute("COMMIT")
                    return state
                raise CryptoOperatorDecisionConflict("crypto operator decision already consumed by another attempt")
            if not decision.is_valid_at(instant):
                raise CryptoOperatorDecisionExpired("crypto operator decision is expired or not yet valid")
            event = _make_event(
                sequence=sequence + 1,
                event_type=CryptoOperatorDecisionEventType.CONSUMED,
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

    def get(self, preparation_hash: str) -> CryptoOperatorDecisionState:
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
        self,
        conn: sqlite3.Connection,
    ) -> tuple[dict[str, CryptoOperatorDecisionState], int, str]:
        control = conn.execute(
            "SELECT event_sequence, event_head_hash, control_hash FROM alpaca_crypto_operator_decision_control WHERE singleton = 1"
        ).fetchone()
        if control is None:
            raise CryptoOperatorDecisionIntegrityError("crypto operator decision control anchor is missing")
        sequence = _strict_int(control["event_sequence"], "event_sequence")
        head = str(control["event_head_hash"])
        _validate_hash(head, "event_head_hash")
        if str(control["control_hash"]) != _control_hash(sequence, head):
            raise CryptoOperatorDecisionIntegrityError("crypto operator decision control hash mismatch")
        rows = conn.execute(
            "SELECT sequence, event_type, preparation_hash, occurred_at, payload_json, previous_event_hash, event_hash FROM alpaca_crypto_operator_decision_events ORDER BY sequence"
        ).fetchall()
        if len(rows) != sequence:
            raise CryptoOperatorDecisionIntegrityError("crypto operator decision event count does not match anchor")

        states: dict[str, CryptoOperatorDecisionState] = {}
        previous = _GENESIS_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            event = _event_from_row(row)
            if event.sequence != expected_sequence:
                raise CryptoOperatorDecisionIntegrityError("crypto operator decision event sequence gap/reordering")
            if event.previous_event_hash != previous:
                raise CryptoOperatorDecisionIntegrityError("crypto operator decision previous-hash mismatch")
            if event.event_type is CryptoOperatorDecisionEventType.ISSUED:
                if event.preparation_hash in states:
                    raise CryptoOperatorDecisionIntegrityError("crypto operator decision issued more than once")
                decision = _decision_from_payload(event.payload)
                if decision.context.preparation_hash != event.preparation_hash:
                    raise CryptoOperatorDecisionIntegrityError("crypto operator decision preparation hash mismatch")
                if decision.issued_at != event.occurred_at:
                    raise CryptoOperatorDecisionIntegrityError("crypto operator decision issue timestamp mismatch")
                states[event.preparation_hash] = CryptoOperatorDecisionState(
                    decision=decision,
                    status=CryptoOperatorDecisionStatus.ISSUED,
                    consumed_at=None,
                    consumed_attempt_id=None,
                    event_sequence=event.sequence,
                    event_hash=event.event_hash,
                )
            else:
                state = states.get(event.preparation_hash)
                if state is None:
                    raise CryptoOperatorDecisionIntegrityError("crypto operator decision consumption precedes issuance")
                if state.status is CryptoOperatorDecisionStatus.CONSUMED:
                    raise CryptoOperatorDecisionIntegrityError("crypto operator decision consumed more than once")
                if set(event.payload) != {"decision_hash", "attempt_id"}:
                    raise CryptoOperatorDecisionIntegrityError("crypto operator consumption payload is non-canonical")
                if event.payload.get("decision_hash") != state.decision.decision_hash:
                    raise CryptoOperatorDecisionIntegrityError("crypto operator consumption hash mismatch")
                attempt_id = _required_str(event.payload, "attempt_id")
                if attempt_id != state.decision.context.attempt_id:
                    raise CryptoOperatorDecisionIntegrityError("crypto operator decision consumed by unexpected attempt")
                if not state.decision.is_valid_at(event.occurred_at):
                    raise CryptoOperatorDecisionIntegrityError("persisted crypto operator consumption outside validity")
                states[event.preparation_hash] = CryptoOperatorDecisionState(
                    decision=state.decision,
                    status=CryptoOperatorDecisionStatus.CONSUMED,
                    consumed_at=event.occurred_at,
                    consumed_attempt_id=attempt_id,
                    event_sequence=event.sequence,
                    event_hash=event.event_hash,
                )
            previous = event.event_hash
        if sequence == 0:
            if head != _GENESIS_HASH:
                raise CryptoOperatorDecisionIntegrityError("empty crypto operator decision ledger has non-genesis head")
        elif previous != head:
            raise CryptoOperatorDecisionIntegrityError("crypto operator decision anchored head mismatch")
        return states, sequence, head


def crypto_operator_confirmation_challenge(context: CryptoOperatorDecisionContext) -> str:
    if not isinstance(context, CryptoOperatorDecisionContext):
        raise TypeError("crypto operator decision context is required")
    return f"APPROVE CRYPTO PAPER {context.symbol} {context.preparation_hash[:12]}"


def _build_decision(
    *,
    context: CryptoOperatorDecisionContext,
    operator_id: str,
    issued_at: datetime,
    expires_at: datetime,
) -> CryptoOperatorDecision:
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
    return CryptoOperatorDecision(
        context=context,
        operator_id=operator_id,
        source=_OPERATOR_SOURCE,
        action=_OPERATOR_ACTION,
        issued_at=issued_at.astimezone(timezone.utc),
        expires_at=expires_at.astimezone(timezone.utc),
        decision_hash=_hash_json(provisional),
    )


def _context_payload_without_hash(context: CryptoOperatorDecisionContext) -> dict[str, object]:
    return _context_payload_from_values(
        {
            "environment": context.environment,
            "prepared_package_hash": context.prepared_package_hash,
            "lifecycle_id": context.lifecycle_id,
            "order_id": context.order_id,
            "client_order_id": context.client_order_id,
            "symbol": context.symbol,
            "account_attestation_fingerprint": context.account_attestation_fingerprint,
            "asset_attestation_fingerprint": context.asset_attestation_fingerprint,
            "product_profile_fingerprint": context.product_profile_fingerprint,
            "risk_decision_id": context.risk_decision_id,
            "risk_decision_fingerprint": context.risk_decision_fingerprint,
            "risk_decision_safety_state_version": context.risk_decision_safety_state_version,
            "risk_decision_valid_until": context.risk_decision_valid_until,
            "crypto_order_fingerprint": context.crypto_order_fingerprint,
            "crypto_order_payload_hash": context.crypto_order_payload_hash,
            "lifecycle_binding_hash": context.lifecycle_binding_hash,
            "lifecycle_control_hash": context.lifecycle_control_hash,
            "lifecycle_event_head_hash": context.lifecycle_event_head_hash,
            "quantity": context.quantity,
            "limit_price": context.limit_price,
            "notional": context.notional,
            "prepared_at": context.prepared_at,
            "execution_deadline": context.execution_deadline,
            "attempt_id": context.attempt_id,
        }
    )


def _context_payload_from_values(values: Mapping[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            payload[key] = _iso(value)
        elif isinstance(value, Decimal):
            payload[key] = format(value, "f")
        else:
            payload[key] = value
    return payload


def _decision_payload_without_hash(decision: CryptoOperatorDecision) -> dict[str, object]:
    return {
        "context": decision.context.to_dict(),
        "operator_id": decision.operator_id,
        "source": decision.source,
        "action": decision.action,
        "issued_at": _iso(decision.issued_at),
        "expires_at": _iso(decision.expires_at),
    }


def _decision_payload(decision: CryptoOperatorDecision) -> dict[str, object]:
    payload = _decision_payload_without_hash(decision)
    payload["decision_hash"] = decision.decision_hash
    return payload


def _decision_from_payload(payload: Mapping[str, object]) -> CryptoOperatorDecision:
    expected = {"context", "operator_id", "source", "action", "issued_at", "expires_at", "decision_hash"}
    if set(payload) != expected:
        raise CryptoOperatorDecisionIntegrityError("crypto operator issuance payload is non-canonical")
    context_raw = payload.get("context")
    if not isinstance(context_raw, dict):
        raise CryptoOperatorDecisionIntegrityError("crypto operator context payload must be object")
    try:
        return CryptoOperatorDecision(
            context=CryptoOperatorDecisionContext.from_dict(context_raw),
            operator_id=_required_str(payload, "operator_id"),
            source=_required_str(payload, "source"),
            action=_required_str(payload, "action"),
            issued_at=_datetime(payload.get("issued_at"), "issued_at"),
            expires_at=_datetime(payload.get("expires_at"), "expires_at"),
            decision_hash=_required_str(payload, "decision_hash"),
        )
    except (TypeError, ValueError) as exc:
        raise CryptoOperatorDecisionIntegrityError("invalid persisted crypto operator decision") from exc


def _make_event(
    *,
    sequence: int,
    event_type: CryptoOperatorDecisionEventType,
    preparation_hash: str,
    occurred_at: datetime,
    payload: Mapping[str, object],
    previous_event_hash: str,
) -> _DecisionEvent:
    if sequence <= 0:
        raise ValueError("crypto operator event sequence must be positive")
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
        "INSERT INTO alpaca_crypto_operator_decision_events(sequence, event_type, preparation_hash, occurred_at, payload_json, previous_event_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
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
            event_type=CryptoOperatorDecisionEventType(str(row["event_type"])),
            preparation_hash=str(row["preparation_hash"]),
            occurred_at=_datetime(row["occurred_at"], "occurred_at"),
            payload=_strict_json_object(str(row["payload_json"])),
            previous_event_hash=str(row["previous_event_hash"]),
        )
    except (TypeError, ValueError) as exc:
        raise CryptoOperatorDecisionIntegrityError("invalid persisted crypto operator decision event") from exc
    if str(row["event_hash"]) != expected.event_hash:
        raise CryptoOperatorDecisionIntegrityError("crypto operator decision event hash mismatch")
    if str(row["occurred_at"]) != _iso(expected.occurred_at):
        raise CryptoOperatorDecisionIntegrityError("crypto operator decision timestamp is non-canonical")
    if str(row["payload_json"]) != _canonical_json(expected.payload):
        raise CryptoOperatorDecisionIntegrityError("crypto operator decision payload is non-canonical")
    return expected


def _update_control(conn: sqlite3.Connection, sequence: int, head: str) -> None:
    cursor = conn.execute(
        "UPDATE alpaca_crypto_operator_decision_control SET event_sequence = ?, event_head_hash = ?, control_hash = ? WHERE singleton = 1",
        (sequence, head, _control_hash(sequence, head)),
    )
    if cursor.rowcount != 1:
        raise CryptoOperatorDecisionIntegrityError("crypto operator decision control update failed")


def _control_hash(sequence: int, head: str) -> str:
    return _hash_json({"event_sequence": sequence, "event_head_hash": head})


def _strict_mapping(payload: Mapping[str, object]) -> dict[str, object]:
    encoded = _canonical_json(payload)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("crypto operator event payload must be object")
    return decoded


def _strict_json_object(text: str) -> dict[str, object]:
    try:
        value = json.loads(text, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (json.JSONDecodeError, ValueError) as exc:
        raise CryptoOperatorDecisionIntegrityError("crypto operator persisted payload is invalid JSON") from exc
    if not isinstance(value, dict):
        raise CryptoOperatorDecisionIntegrityError("crypto operator persisted payload must be object")
    return value


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty string")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return parsed


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be integer")
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be ISO datetime") from exc
    _require_aware(parsed, label)
    return parsed


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_json(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "CryptoOperatorDecision",
    "CryptoOperatorDecisionConflict",
    "CryptoOperatorDecisionContext",
    "CryptoOperatorDecisionError",
    "CryptoOperatorDecisionExpired",
    "CryptoOperatorDecisionIntegrityError",
    "CryptoOperatorDecisionState",
    "CryptoOperatorDecisionStatus",
    "SQLiteCryptoOperatorDecisionRegistry",
    "crypto_operator_confirmation_challenge",
]
