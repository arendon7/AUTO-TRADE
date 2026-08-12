from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping

from autotrade.brokers.alpaca_paper_canary_permit import (
    PaperCanaryPermitStatus,
    SQLitePaperCanaryPermitRegistry,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.connectivity_canary_authority import CONNECTIVITY_CANARY_STRATEGY_ID
from autotrade.connectivity_preparation_binding import (
    ConnectivityPreparationBinding,
    SQLiteConnectivityPreparationBindingStore,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_GENESIS_HASH = "0" * 64
_SOURCE = "HUMAN_OPERATOR"
_ACTION = "APPROVE_CONNECTIVITY_CANARY"
_MAX_TTL = timedelta(minutes=2)
_PREPARATION_ARTIFACT = "connectivity_preparation.json"
_CONTEXT_ARTIFACT = "connectivity_operator_context.json"
_DECISION_ARTIFACT = "connectivity_operator_decision.json"
_OPERATOR_DB = "connectivity_operator.sqlite3"


class ConnectivityOperatorDecisionError(RuntimeError):
    pass


class ConnectivityOperatorDecisionRejected(ConnectivityOperatorDecisionError):
    pass


class ConnectivityOperatorDecisionConflict(ConnectivityOperatorDecisionError):
    pass


class ConnectivityOperatorDecisionIntegrityError(ConnectivityOperatorDecisionError):
    pass


class ConnectivityOperatorDecisionStatus(StrEnum):
    ISSUED = "ISSUED"


@dataclass(frozen=True, slots=True)
class ConnectivityOperatorDecisionContext:
    environment: str
    purpose: str
    order_id: str
    client_order_id: str
    connectivity_preparation_hash: str
    connectivity_binding_id: str
    connectivity_binding_hash: str
    standard_package_hash: str
    canary_approval_hash: str
    permit_event_hash: str
    submission_binding_hash: str
    bracket_payload_hash: str
    account_attestation_fingerprint: str
    notional: Decimal
    attempt_id: str
    core_db_sha256_after_preparation: str
    context_hash: str

    def __post_init__(self) -> None:
        if self.environment != "PAPER":
            raise ValueError("connectivity operator context is PAPER-only")
        if self.purpose != "CONNECTIVITY_CANARY":
            raise ValueError("connectivity operator context purpose is not exact")
        for label, value in (
            ("order_id", self.order_id),
            ("client_order_id", self.client_order_id),
            ("attempt_id", self.attempt_id),
        ):
            _validate_id(value, label)
        for label, value in (
            ("connectivity_preparation_hash", self.connectivity_preparation_hash),
            ("connectivity_binding_id", self.connectivity_binding_id),
            ("connectivity_binding_hash", self.connectivity_binding_hash),
            ("standard_package_hash", self.standard_package_hash),
            ("canary_approval_hash", self.canary_approval_hash),
            ("permit_event_hash", self.permit_event_hash),
            ("submission_binding_hash", self.submission_binding_hash),
            ("bracket_payload_hash", self.bracket_payload_hash),
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("core_db_sha256_after_preparation", self.core_db_sha256_after_preparation),
            ("context_hash", self.context_hash),
        ):
            _validate_hash(value, label)
        if not isinstance(self.notional, Decimal) or not self.notional.is_finite() or self.notional <= 0:
            raise ValueError("connectivity operator notional must be finite and positive")
        if self.context_hash != _hash(_context_payload(self, include_hash=False)):
            raise ValueError("connectivity operator context hash mismatch")

    def payload(self) -> dict[str, object]:
        return _context_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class ConnectivityOperatorDecision:
    context: ConnectivityOperatorDecisionContext
    operator_id: str
    source: str
    action: str
    issued_at: datetime
    expires_at: datetime
    decision_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.context, ConnectivityOperatorDecisionContext):
            raise ValueError("connectivity operator context is required")
        _validate_id(self.operator_id, "operator_id")
        if self.source != _SOURCE:
            raise ValueError("connectivity operator source must be HUMAN_OPERATOR")
        if self.action != _ACTION:
            raise ValueError("connectivity operator action is not exact")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        issued = self.issued_at.astimezone(timezone.utc)
        expires = self.expires_at.astimezone(timezone.utc)
        if expires <= issued or expires - issued > _MAX_TTL:
            raise ValueError("connectivity operator validity must be >0 and <=2 minutes")
        _validate_hash(self.decision_hash, "decision_hash")
        if self.decision_hash != _hash(_decision_payload(self, include_hash=False)):
            raise ValueError("connectivity operator decision hash mismatch")

    def is_valid_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        return self.issued_at.astimezone(timezone.utc) <= instant < self.expires_at.astimezone(timezone.utc)

    def payload(self) -> dict[str, object]:
        return _decision_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class ConnectivityOperatorDecisionState:
    decision: ConnectivityOperatorDecision
    status: ConnectivityOperatorDecisionStatus
    event_sequence: int
    event_hash: str


class SQLiteConnectivityOperatorDecisionRegistry:
    """Tamper-evident issuance registry with no broker, OMS or network capability."""

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        conn = runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS connectivity_operator_events (
                    sequence INTEGER PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    context_hash TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS connectivity_operator_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    event_sequence INTEGER NOT NULL CHECK(event_sequence >= 0),
                    event_head_hash TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                """
            )
            row = conn.execute(
                "SELECT event_sequence,event_head_hash,control_hash FROM connectivity_operator_control WHERE singleton=1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO connectivity_operator_control(singleton,event_sequence,event_head_hash,control_hash) VALUES(1,0,?,?)",
                    (_GENESIS_HASH, _control_hash(0, _GENESIS_HASH)),
                )
        finally:
            conn.close()

    def issue(
        self,
        *,
        context: ConnectivityOperatorDecisionContext,
        operator_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> ConnectivityOperatorDecisionState:
        if not isinstance(context, ConnectivityOperatorDecisionContext):
            raise TypeError("ConnectivityOperatorDecisionContext is required")
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
            existing = states.get(context.context_hash)
            if existing is not None:
                if existing.decision == decision:
                    conn.execute("COMMIT")
                    return existing
                raise ConnectivityOperatorDecisionConflict(
                    "connectivity preparation already has different human decision evidence"
                )
            payload = decision.payload()
            canonical = _canonical(payload)
            event_hash = _event_hash(
                sequence=sequence + 1,
                context_hash=context.context_hash,
                occurred_at=decision.issued_at,
                payload_json=canonical,
                previous_event_hash=head,
            )
            conn.execute(
                "INSERT INTO connectivity_operator_events(sequence,event_type,context_hash,occurred_at,payload_json,previous_event_hash,event_hash) VALUES(?,?,?,?,?,?,?)",
                (
                    sequence + 1,
                    ConnectivityOperatorDecisionStatus.ISSUED.value,
                    context.context_hash,
                    _iso(decision.issued_at),
                    canonical,
                    head,
                    event_hash,
                ),
            )
            conn.execute(
                "UPDATE connectivity_operator_control SET event_sequence=?,event_head_hash=?,control_hash=? WHERE singleton=1",
                (sequence + 1, event_hash, _control_hash(sequence + 1, event_hash)),
            )
            states, _, _ = self._verify_locked(conn)
            state = states[context.context_hash]
            conn.execute("COMMIT")
            return state
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, context_hash: str) -> ConnectivityOperatorDecisionState:
        _validate_hash(context_hash, "context_hash")
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            if context_hash not in states:
                raise KeyError(context_hash)
            return states[context_hash]
        finally:
            conn.close()

    def list_states(self) -> tuple[ConnectivityOperatorDecisionState, ...]:
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            return tuple(states[key] for key in sorted(states))
        finally:
            conn.close()

    def _verify_locked(
        self, conn: sqlite3.Connection
    ) -> tuple[dict[str, ConnectivityOperatorDecisionState], int, str]:
        control = conn.execute(
            "SELECT event_sequence,event_head_hash,control_hash FROM connectivity_operator_control WHERE singleton=1"
        ).fetchone()
        if control is None:
            raise ConnectivityOperatorDecisionIntegrityError("connectivity operator control anchor is missing")
        sequence = _strict_int(control["event_sequence"], "event_sequence")
        head = str(control["event_head_hash"])
        _validate_hash(head, "event_head_hash")
        if str(control["control_hash"]) != _control_hash(sequence, head):
            raise ConnectivityOperatorDecisionIntegrityError("connectivity operator control hash mismatch")
        rows = conn.execute(
            "SELECT sequence,event_type,context_hash,occurred_at,payload_json,previous_event_hash,event_hash FROM connectivity_operator_events ORDER BY sequence"
        ).fetchall()
        if len(rows) != sequence:
            raise ConnectivityOperatorDecisionIntegrityError("connectivity operator event count mismatch")
        states: dict[str, ConnectivityOperatorDecisionState] = {}
        previous = _GENESIS_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            current_sequence = _strict_int(row["sequence"], "sequence")
            if current_sequence != expected_sequence:
                raise ConnectivityOperatorDecisionIntegrityError("connectivity operator event sequence gap")
            if str(row["event_type"]) != ConnectivityOperatorDecisionStatus.ISSUED.value:
                raise ConnectivityOperatorDecisionIntegrityError("connectivity operator event type is invalid")
            context_hash = str(row["context_hash"])
            _validate_hash(context_hash, "context_hash")
            occurred_at = _datetime(row["occurred_at"], "occurred_at")
            payload_json = str(row["payload_json"])
            if payload_json != _canonical(_json_object(payload_json, "decision event")):
                raise ConnectivityOperatorDecisionIntegrityError("connectivity operator payload is non-canonical")
            if str(row["previous_event_hash"]) != previous:
                raise ConnectivityOperatorDecisionIntegrityError("connectivity operator previous-hash mismatch")
            calculated = _event_hash(
                sequence=current_sequence,
                context_hash=context_hash,
                occurred_at=occurred_at,
                payload_json=payload_json,
                previous_event_hash=previous,
            )
            if str(row["event_hash"]) != calculated:
                raise ConnectivityOperatorDecisionIntegrityError("connectivity operator event hash mismatch")
            decision = _decision_from_payload(_json_object(payload_json, "decision event"))
            if decision.context.context_hash != context_hash or decision.issued_at != occurred_at:
                raise ConnectivityOperatorDecisionIntegrityError("connectivity operator event identity mismatch")
            if context_hash in states:
                raise ConnectivityOperatorDecisionIntegrityError("connectivity operator decision issued more than once")
            states[context_hash] = ConnectivityOperatorDecisionState(
                decision=decision,
                status=ConnectivityOperatorDecisionStatus.ISSUED,
                event_sequence=current_sequence,
                event_hash=calculated,
            )
            previous = calculated
        if sequence == 0:
            if head != _GENESIS_HASH:
                raise ConnectivityOperatorDecisionIntegrityError("empty connectivity operator ledger has non-genesis head")
        elif previous != head:
            raise ConnectivityOperatorDecisionIntegrityError("connectivity operator anchored head mismatch")
        return states, sequence, head


class ConnectivityOperatorBridge:
    """Build and issue human connectivity authority; never stages OMS and has no network API."""

    def __init__(self, workspace: PaperOperationalWorkspace) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace

    @property
    def context_path(self) -> Path:
        return self._workspace.root / _CONTEXT_ARTIFACT

    @property
    def decision_path(self) -> Path:
        return self._workspace.root / _DECISION_ARTIFACT

    @property
    def registry_path(self) -> Path:
        return self._workspace.root / _OPERATOR_DB

    def prepare_context(self, *, now: datetime) -> ConnectivityOperatorDecisionContext:
        _require_aware(now, "now")
        self._require_normal_operator_artifacts_absent()
        preparation = _read_json(self._workspace.root / _PREPARATION_ARTIFACT, "connectivity preparation")
        _validate_preparation_envelope(preparation)
        prep_hash = _required_hash(preparation, "preparation_hash")
        without_hash = dict(preparation)
        without_hash.pop("preparation_hash", None)
        if prep_hash != _hash(without_hash):
            raise ConnectivityOperatorDecisionRejected("connectivity preparation hash mismatch")
        expected_core_hash = _required_hash(preparation, "core_db_sha256_after_preparation")
        if _file_sha256(self._workspace.core_db_path) != expected_core_hash:
            raise ConnectivityOperatorDecisionRejected("core.sqlite3 changed after connectivity preparation")

        package = _mapping(preparation, "standard_prepared_package")
        binding_payload = _mapping(preparation, "connectivity_preparation_binding")
        order_id = _required_str(package, "order_id")
        client_order_id = _required_str(package, "client_order_id")
        standard_package_hash = _required_hash(package, "package_hash")
        canary_approval_hash = _required_hash(package, "canary_approval_hash")
        permit_event_hash = _required_hash(package, "permit_event_hash")
        submission_binding_hash = _required_hash(package, "submission_binding_hash")
        bracket_payload_hash = _required_hash(package, "bracket_payload_hash")
        account_fingerprint = _required_hash(package, "account_attestation_fingerprint")
        attempt_id = _required_str(package, "attempt_id")
        notional = _decimal(package.get("notional"), "notional")
        if preparation.get("expected_bracket_payload_hash") != bracket_payload_hash:
            raise ConnectivityOperatorDecisionRejected("prepared bracket hash mismatch")

        runtime = SQLiteRuntime(self._workspace.core_db_path)
        durable_binding = SQLiteConnectivityPreparationBindingStore(runtime).get_for_order(order_id)
        if durable_binding is None:
            raise ConnectivityOperatorDecisionRejected("durable connectivity preparation binding is missing")
        if durable_binding.payload() != binding_payload:
            raise ConnectivityOperatorDecisionRejected("connectivity preparation artifact/binding mismatch")
        _validate_binding_relationships(
            durable_binding,
            standard_package_hash=standard_package_hash,
            canary_approval_hash=canary_approval_hash,
            permit_event_hash=permit_event_hash,
            submission_binding_hash=submission_binding_hash,
            bracket_payload_hash=bracket_payload_hash,
        )

        order = SQLiteOrderStore(runtime).get_by_order_id(order_id)
        if order is None or order.status is not OrderStatus.VALIDATED:
            raise ConnectivityOperatorDecisionRejected("connectivity OMS order must remain VALIDATED")
        if order.intent.strategy_id != CONNECTIVITY_CANARY_STRATEGY_ID:
            raise ConnectivityOperatorDecisionRejected("connectivity OMS strategy purpose drifted")
        if order.intent.quantity != Decimal("1") or order.intent.side.value != "BUY" or order.intent.order_type.value != "LIMIT":
            raise ConnectivityOperatorDecisionRejected("connectivity OMS order shape drifted")

        submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(self._workspace.submission_db_path)).get(order_id)
        if (
            submission.status is not PaperSubmissionStatus.PREPARED
            or submission.attempt_count != 0
            or submission.binding_hash != submission_binding_hash
            or submission.broker_order_id is not None
            or submission.broker_client_order_id is not None
        ):
            raise ConnectivityOperatorDecisionRejected("connectivity submission is no longer fresh PREPARED")
        permit = SQLitePaperCanaryPermitRegistry(SQLiteRuntime(self._workspace.permit_db_path)).get(canary_approval_hash)
        if (
            permit.status is not PaperCanaryPermitStatus.ISSUED
            or permit.order_id != order_id
            or permit.client_order_id != client_order_id
            or permit.binding_hash != submission_binding_hash
            or permit.event_hash != permit_event_hash
            or permit.attempt_id is not None
        ):
            raise ConnectivityOperatorDecisionRejected("connectivity canary permit drifted before human decision")

        base = {
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "order_id": order_id,
            "client_order_id": client_order_id,
            "connectivity_preparation_hash": prep_hash,
            "connectivity_binding_id": durable_binding.binding_id,
            "connectivity_binding_hash": durable_binding.binding_hash,
            "standard_package_hash": standard_package_hash,
            "canary_approval_hash": canary_approval_hash,
            "permit_event_hash": permit_event_hash,
            "submission_binding_hash": submission_binding_hash,
            "bracket_payload_hash": bracket_payload_hash,
            "account_attestation_fingerprint": account_fingerprint,
            "notional": str(notional),
            "attempt_id": attempt_id,
            "core_db_sha256_after_preparation": expected_core_hash,
        }
        context = ConnectivityOperatorDecisionContext(
            environment="PAPER",
            purpose="CONNECTIVITY_CANARY",
            order_id=order_id,
            client_order_id=client_order_id,
            connectivity_preparation_hash=prep_hash,
            connectivity_binding_id=durable_binding.binding_id,
            connectivity_binding_hash=durable_binding.binding_hash,
            standard_package_hash=standard_package_hash,
            canary_approval_hash=canary_approval_hash,
            permit_event_hash=permit_event_hash,
            submission_binding_hash=submission_binding_hash,
            bracket_payload_hash=bracket_payload_hash,
            account_attestation_fingerprint=account_fingerprint,
            notional=notional,
            attempt_id=attempt_id,
            core_db_sha256_after_preparation=expected_core_hash,
            context_hash=_hash(base),
        )
        _write_json_idempotent(self.context_path, context.payload())
        self._require_normal_operator_artifacts_absent()
        return context

    def verify_context(
        self,
        *,
        expected: ConnectivityOperatorDecisionContext,
        now: datetime,
    ) -> ConnectivityOperatorDecisionContext:
        if not isinstance(expected, ConnectivityOperatorDecisionContext):
            raise TypeError("ConnectivityOperatorDecisionContext is required")
        observed = self.prepare_context(now=now)
        if observed != expected:
            raise ConnectivityOperatorDecisionRejected("connectivity operator context changed during human decision")
        return observed

    def issue(
        self,
        *,
        context: ConnectivityOperatorDecisionContext,
        operator_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> ConnectivityOperatorDecisionState:
        self.verify_context(expected=context, now=issued_at)
        registry = SQLiteConnectivityOperatorDecisionRegistry(SQLiteRuntime(self.registry_path))
        state = registry.issue(
            context=context,
            operator_id=operator_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        payload = {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "decision": state.decision.payload(),
            "status": state.status.value,
            "event_sequence": state.event_sequence,
            "event_hash": state.event_hash,
            "oms_staging_authorized": False,
            "external_post_authorized": False,
            "external_order_submitted": False,
            "strategy_health_required": False,
            "strategy_trading_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
            "next_action": "CONNECTIVITY_FINAL_FRESHNESS_REQUIRED",
        }
        _write_json_idempotent(self.decision_path, payload)
        self._require_normal_operator_artifacts_absent()
        return state

    def _require_normal_operator_artifacts_absent(self) -> None:
        forbidden = (
            self._workspace.prepared_package_path,
            self._workspace.expected_bracket_path,
            self._workspace.operator_context_path,
            self._workspace.manifest_path,
            self._workspace.operator_db_path,
        )
        existing = [path.name for path in forbidden if path.exists()]
        if existing:
            raise ConnectivityOperatorDecisionRejected(
                f"normal strategy operator artifacts are forbidden on connectivity path: {existing}"
            )


def connectivity_operator_confirmation_challenge(
    context: ConnectivityOperatorDecisionContext,
) -> str:
    if not isinstance(context, ConnectivityOperatorDecisionContext):
        raise TypeError("ConnectivityOperatorDecisionContext is required")
    return f"APPROVE CONNECTIVITY {context.context_hash[:12]}"


def _validate_preparation_envelope(payload: Mapping[str, object]) -> None:
    for key, expected in (
        ("schema_version", 1),
        ("environment", "PAPER"),
        ("purpose", "CONNECTIVITY_CANARY"),
        ("operator_context_created", False),
        ("operator_authority_created", False),
        ("external_post_authorized", False),
        ("external_order_submitted", False),
        ("strategy_health_required", False),
        ("strategy_trading_authorized", False),
        ("live_trading", "BLOCKED"),
        ("next_action", "CONNECTIVITY_OPERATOR_BRIDGE_REQUIRED"),
    ):
        if payload.get(key) != expected:
            raise ConnectivityOperatorDecisionRejected(f"unsafe connectivity preparation field: {key}")


def _validate_binding_relationships(
    binding: ConnectivityPreparationBinding,
    *,
    standard_package_hash: str,
    canary_approval_hash: str,
    permit_event_hash: str,
    submission_binding_hash: str,
    bracket_payload_hash: str,
) -> None:
    expected = (
        (binding.standard_package_hash, standard_package_hash, "standard package"),
        (binding.canary_approval_hash, canary_approval_hash, "canary approval"),
        (binding.permit_event_hash, permit_event_hash, "permit event"),
        (binding.submission_binding_hash, submission_binding_hash, "submission binding"),
        (binding.bracket_payload_hash, bracket_payload_hash, "bracket payload"),
    )
    for durable, observed, label in expected:
        if durable != observed:
            raise ConnectivityOperatorDecisionRejected(f"connectivity {label} binding mismatch")


def _build_decision(
    *,
    context: ConnectivityOperatorDecisionContext,
    operator_id: str,
    issued_at: datetime,
    expires_at: datetime,
) -> ConnectivityOperatorDecision:
    _validate_id(operator_id, "operator_id")
    base = {
        "context": context.payload(),
        "operator_id": operator_id,
        "source": _SOURCE,
        "action": _ACTION,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
    }
    return ConnectivityOperatorDecision(
        context=context,
        operator_id=operator_id,
        source=_SOURCE,
        action=_ACTION,
        issued_at=issued_at.astimezone(timezone.utc),
        expires_at=expires_at.astimezone(timezone.utc),
        decision_hash=_hash(base),
    )


def _context_payload(
    context: ConnectivityOperatorDecisionContext,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "environment": context.environment,
        "purpose": context.purpose,
        "order_id": context.order_id,
        "client_order_id": context.client_order_id,
        "connectivity_preparation_hash": context.connectivity_preparation_hash,
        "connectivity_binding_id": context.connectivity_binding_id,
        "connectivity_binding_hash": context.connectivity_binding_hash,
        "standard_package_hash": context.standard_package_hash,
        "canary_approval_hash": context.canary_approval_hash,
        "permit_event_hash": context.permit_event_hash,
        "submission_binding_hash": context.submission_binding_hash,
        "bracket_payload_hash": context.bracket_payload_hash,
        "account_attestation_fingerprint": context.account_attestation_fingerprint,
        "notional": str(context.notional),
        "attempt_id": context.attempt_id,
        "core_db_sha256_after_preparation": context.core_db_sha256_after_preparation,
    }
    if include_hash:
        payload["context_hash"] = context.context_hash
    return payload


def _decision_payload(
    decision: ConnectivityOperatorDecision,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "context": decision.context.payload(),
        "operator_id": decision.operator_id,
        "source": decision.source,
        "action": decision.action,
        "issued_at": _iso(decision.issued_at),
        "expires_at": _iso(decision.expires_at),
    }
    if include_hash:
        payload["decision_hash"] = decision.decision_hash
    return payload


def _decision_from_payload(payload: Mapping[str, object]) -> ConnectivityOperatorDecision:
    expected = {"context", "operator_id", "source", "action", "issued_at", "expires_at", "decision_hash"}
    if set(payload) != expected:
        raise ConnectivityOperatorDecisionIntegrityError("connectivity operator decision payload is non-canonical")
    context = _context_from_payload(_mapping(payload, "context"))
    try:
        return ConnectivityOperatorDecision(
            context=context,
            operator_id=_required_str(payload, "operator_id"),
            source=_required_str(payload, "source"),
            action=_required_str(payload, "action"),
            issued_at=_datetime(payload.get("issued_at"), "issued_at"),
            expires_at=_datetime(payload.get("expires_at"), "expires_at"),
            decision_hash=_required_hash(payload, "decision_hash"),
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ConnectivityOperatorDecisionIntegrityError("invalid connectivity operator decision") from exc


def _context_from_payload(payload: Mapping[str, object]) -> ConnectivityOperatorDecisionContext:
    expected = {
        "environment", "purpose", "order_id", "client_order_id", "connectivity_preparation_hash",
        "connectivity_binding_id", "connectivity_binding_hash", "standard_package_hash",
        "canary_approval_hash", "permit_event_hash", "submission_binding_hash", "bracket_payload_hash",
        "account_attestation_fingerprint", "notional", "attempt_id", "core_db_sha256_after_preparation",
        "context_hash",
    }
    if set(payload) != expected:
        raise ConnectivityOperatorDecisionIntegrityError("connectivity operator context payload is non-canonical")
    try:
        return ConnectivityOperatorDecisionContext(
            environment=_required_str(payload, "environment"),
            purpose=_required_str(payload, "purpose"),
            order_id=_required_str(payload, "order_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            connectivity_preparation_hash=_required_hash(payload, "connectivity_preparation_hash"),
            connectivity_binding_id=_required_hash(payload, "connectivity_binding_id"),
            connectivity_binding_hash=_required_hash(payload, "connectivity_binding_hash"),
            standard_package_hash=_required_hash(payload, "standard_package_hash"),
            canary_approval_hash=_required_hash(payload, "canary_approval_hash"),
            permit_event_hash=_required_hash(payload, "permit_event_hash"),
            submission_binding_hash=_required_hash(payload, "submission_binding_hash"),
            bracket_payload_hash=_required_hash(payload, "bracket_payload_hash"),
            account_attestation_fingerprint=_required_hash(payload, "account_attestation_fingerprint"),
            notional=_decimal(payload.get("notional"), "notional"),
            attempt_id=_required_str(payload, "attempt_id"),
            core_db_sha256_after_preparation=_required_hash(payload, "core_db_sha256_after_preparation"),
            context_hash=_required_hash(payload, "context_hash"),
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ConnectivityOperatorDecisionIntegrityError("invalid connectivity operator context") from exc


def _control_hash(sequence: int, head: str) -> str:
    return _hash({"event_sequence": sequence, "event_head_hash": head})


def _event_hash(
    *,
    sequence: int,
    context_hash: str,
    occurred_at: datetime,
    payload_json: str,
    previous_event_hash: str,
) -> str:
    return _hash(
        {
            "sequence": sequence,
            "event_type": ConnectivityOperatorDecisionStatus.ISSUED.value,
            "context_hash": context_hash,
            "occurred_at": _iso(occurred_at),
            "payload_json": payload_json,
            "previous_event_hash": previous_event_hash,
        }
    )


def _read_json(path: Path, label: str) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ConnectivityOperatorDecisionRejected(f"{label} must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectivityOperatorDecisionRejected(f"cannot read {label}") from exc
    if not isinstance(raw, dict):
        raise ConnectivityOperatorDecisionRejected(f"{label} root must be object")
    return raw


def _write_json_idempotent(path: Path, payload: Mapping[str, object]) -> None:
    raw = json.dumps(dict(payload), sort_keys=True, indent=2, allow_nan=False) + "\n"
    if path.exists() and path.is_symlink():
        raise ConnectivityOperatorDecisionRejected(f"{path.name} cannot be symlink")
    if path.exists():
        if path.read_text(encoding="utf-8") != raw:
            raise ConnectivityOperatorDecisionConflict(f"refusing to overwrite different {path.name}")
        return
    path.write_text(raw, encoding="utf-8")
    path.chmod(0o600)


def _file_sha256(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ConnectivityOperatorDecisionRejected("core.sqlite3 must be a regular file")
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConnectivityOperatorDecisionRejected(f"{key} must be object")
    return value


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectivityOperatorDecisionRejected(f"{key} must be non-empty string")
    return value


def _required_hash(payload: Mapping[str, object], key: str) -> str:
    value = _required_str(payload, key)
    if not _HASH_RE.fullmatch(value):
        raise ConnectivityOperatorDecisionRejected(f"{key} must be lowercase SHA-256")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ConnectivityOperatorDecisionRejected(f"{label} must be decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ConnectivityOperatorDecisionRejected(f"{label} must be finite")
    return parsed


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be datetime string")
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConnectivityOperatorDecisionIntegrityError(f"{label} must be integer")
    return value


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be canonical identifier")


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _iso(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _json_object(raw: str, label: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectivityOperatorDecisionIntegrityError(f"{label} JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ConnectivityOperatorDecisionIntegrityError(f"{label} must be object")
    return payload


def _canonical(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ConnectivityOperatorBridge",
    "ConnectivityOperatorDecision",
    "ConnectivityOperatorDecisionContext",
    "ConnectivityOperatorDecisionStatus",
    "ConnectivityOperatorDecisionConflict",
    "ConnectivityOperatorDecisionError",
    "ConnectivityOperatorDecisionIntegrityError",
    "ConnectivityOperatorDecisionRejected",
    "SQLiteConnectivityOperatorDecisionRegistry",
    "connectivity_operator_confirmation_challenge",
]
