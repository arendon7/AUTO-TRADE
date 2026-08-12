from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace, _file_sha256
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.connectivity_canary_authority import CONNECTIVITY_CANARY_STRATEGY_ID
from autotrade.connectivity_operator_decision import (
    ConnectivityOperatorDecisionStatus,
    SQLiteConnectivityOperatorDecisionRegistry,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_GENESIS_HASH = "0" * 64
_OPERATOR_DB = "connectivity_operator.sqlite3"
_OPERATOR_ARTIFACT = "connectivity_operator_decision.json"
_PREPARATION_ARTIFACT = "connectivity_preparation.json"
_EXECUTION_INTENT_DB = "connectivity_execution_intent.sqlite3"
_EXECUTION_INTENT_CONTEXT = "connectivity_execution_intent_context.json"
_EXECUTION_INTENT_ARTIFACT = "connectivity_execution_intent.json"
_FINAL_FRESHNESS_ARTIFACT = "connectivity_final_freshness.json"
_FINAL_FRESHNESS_DB = "connectivity_final_freshness.sqlite3"
_ACTION = "CONFIRM_CONNECTIVITY_EXECUTION_INTENT"
_SOURCE = "HUMAN_OPERATOR"
_MAX_TTL = timedelta(seconds=90)


class ConnectivityExecutionIntentError(RuntimeError):
    pass


class ConnectivityExecutionIntentRejected(ConnectivityExecutionIntentError):
    pass


class ConnectivityExecutionIntentConflict(ConnectivityExecutionIntentError):
    pass


class ConnectivityExecutionIntentIntegrityError(ConnectivityExecutionIntentError):
    pass


class ConnectivityExecutionIntentStatus(StrEnum):
    ISSUED = "ISSUED"


@dataclass(frozen=True, slots=True)
class ConnectivityExecutionIntentContext:
    environment: str
    purpose: str
    order_id: str
    client_order_id: str
    attempt_id: str
    operator_context_hash: str
    operator_decision_hash: str
    operator_event_hash: str
    preparation_hash: str
    connectivity_binding_hash: str
    standard_package_hash: str
    canary_approval_hash: str
    permit_event_hash: str
    submission_binding_hash: str
    bracket_payload_hash: str
    initial_account_fingerprint: str
    notional: Decimal
    core_db_sha256_after_preparation: str
    context_hash: str

    def __post_init__(self) -> None:
        if self.environment != "PAPER":
            raise ValueError("execution intent is PAPER-only")
        if self.purpose != "CONNECTIVITY_CANARY":
            raise ValueError("execution intent purpose must be CONNECTIVITY_CANARY")
        for label, value in (
            ("order_id", self.order_id),
            ("client_order_id", self.client_order_id),
            ("attempt_id", self.attempt_id),
        ):
            _validate_id(value, label)
        for label, value in (
            ("operator_context_hash", self.operator_context_hash),
            ("operator_decision_hash", self.operator_decision_hash),
            ("operator_event_hash", self.operator_event_hash),
            ("preparation_hash", self.preparation_hash),
            ("connectivity_binding_hash", self.connectivity_binding_hash),
            ("standard_package_hash", self.standard_package_hash),
            ("canary_approval_hash", self.canary_approval_hash),
            ("permit_event_hash", self.permit_event_hash),
            ("submission_binding_hash", self.submission_binding_hash),
            ("bracket_payload_hash", self.bracket_payload_hash),
            ("initial_account_fingerprint", self.initial_account_fingerprint),
            ("core_db_sha256_after_preparation", self.core_db_sha256_after_preparation),
            ("context_hash", self.context_hash),
        ):
            _validate_hash(value, label)
        if not isinstance(self.notional, Decimal) or not self.notional.is_finite() or self.notional <= 0:
            raise ValueError("execution intent notional must be finite and positive")
        if self.context_hash != _hash(_context_payload(self, include_hash=False)):
            raise ValueError("execution intent context hash mismatch")

    def payload(self) -> dict[str, object]:
        return _context_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class ConnectivityExecutionIntentDecision:
    context: ConnectivityExecutionIntentContext
    operator_id: str
    source: str
    action: str
    issued_at: datetime
    expires_at: datetime
    max_external_post_attempts: int
    decision_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.context, ConnectivityExecutionIntentContext):
            raise ValueError("execution intent context is required")
        _validate_id(self.operator_id, "operator_id")
        if self.source != _SOURCE:
            raise ValueError("execution intent source must be HUMAN_OPERATOR")
        if self.action != _ACTION:
            raise ValueError("execution intent action is not exact")
        if self.max_external_post_attempts != 1:
            raise ValueError("execution intent budget must be exactly one external POST attempt")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        issued = self.issued_at.astimezone(timezone.utc)
        expires = self.expires_at.astimezone(timezone.utc)
        if expires <= issued or expires - issued > _MAX_TTL:
            raise ValueError("execution intent validity must be >0 and <=90 seconds")
        _validate_hash(self.decision_hash, "decision_hash")
        if self.decision_hash != _hash(_decision_payload(self, include_hash=False)):
            raise ValueError("execution intent decision hash mismatch")

    def is_valid_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        return self.issued_at.astimezone(timezone.utc) <= instant < self.expires_at.astimezone(timezone.utc)

    def payload(self) -> dict[str, object]:
        return _decision_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class ConnectivityExecutionIntentState:
    decision: ConnectivityExecutionIntentDecision
    status: ConnectivityExecutionIntentStatus
    event_hash: str


class SQLiteConnectivityExecutionIntentRegistry:
    """Single human execution intent; no consume, staging, broker or network API."""

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        conn = runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS connectivity_execution_intent_events (
                    sequence INTEGER PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    context_hash TEXT NOT NULL UNIQUE,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS connectivity_execution_intent_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    event_sequence INTEGER NOT NULL CHECK(event_sequence>=0),
                    event_head_hash TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                """
            )
            if conn.execute(
                "SELECT 1 FROM connectivity_execution_intent_control WHERE singleton=1"
            ).fetchone() is None:
                conn.execute(
                    "INSERT INTO connectivity_execution_intent_control(singleton,event_sequence,event_head_hash,control_hash) VALUES(1,0,?,?)",
                    (_GENESIS_HASH, _control_hash(0, _GENESIS_HASH)),
                )
        finally:
            conn.close()

    def issue(
        self,
        *,
        context: ConnectivityExecutionIntentContext,
        operator_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> ConnectivityExecutionIntentState:
        if not isinstance(context, ConnectivityExecutionIntentContext):
            raise TypeError("ConnectivityExecutionIntentContext is required")
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
            if states:
                existing = next(iter(states.values()))
                if existing.decision == decision:
                    conn.execute("COMMIT")
                    return existing
                raise ConnectivityExecutionIntentConflict(
                    "workspace already contains a different execution intent"
                )
            payload_json = _canonical(decision.payload())
            event_hash = _event_hash(
                sequence=sequence + 1,
                context_hash=context.context_hash,
                occurred_at=decision.issued_at,
                payload_json=payload_json,
                previous_event_hash=head,
            )
            conn.execute(
                "INSERT INTO connectivity_execution_intent_events(sequence,event_type,context_hash,occurred_at,payload_json,previous_event_hash,event_hash) VALUES(?,?,?,?,?,?,?)",
                (
                    sequence + 1,
                    ConnectivityExecutionIntentStatus.ISSUED.value,
                    context.context_hash,
                    _iso(decision.issued_at),
                    payload_json,
                    head,
                    event_hash,
                ),
            )
            conn.execute(
                "UPDATE connectivity_execution_intent_control SET event_sequence=?,event_head_hash=?,control_hash=? WHERE singleton=1",
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

    def list_states(self) -> tuple[ConnectivityExecutionIntentState, ...]:
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            return tuple(states[key] for key in sorted(states))
        finally:
            conn.close()

    def _verify_locked(
        self, conn: sqlite3.Connection
    ) -> tuple[dict[str, ConnectivityExecutionIntentState], int, str]:
        control = conn.execute(
            "SELECT event_sequence,event_head_hash,control_hash FROM connectivity_execution_intent_control WHERE singleton=1"
        ).fetchone()
        if control is None:
            raise ConnectivityExecutionIntentIntegrityError("execution intent control anchor is missing")
        sequence = _strict_int(control["event_sequence"], "event_sequence")
        head = str(control["event_head_hash"])
        _validate_hash(head, "event_head_hash")
        if str(control["control_hash"]) != _control_hash(sequence, head):
            raise ConnectivityExecutionIntentIntegrityError("execution intent control hash mismatch")
        rows = conn.execute(
            "SELECT sequence,event_type,context_hash,occurred_at,payload_json,previous_event_hash,event_hash FROM connectivity_execution_intent_events ORDER BY sequence"
        ).fetchall()
        if len(rows) != sequence:
            raise ConnectivityExecutionIntentIntegrityError("execution intent event count mismatch")
        states: dict[str, ConnectivityExecutionIntentState] = {}
        previous = _GENESIS_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            current_sequence = _strict_int(row["sequence"], "sequence")
            if current_sequence != expected_sequence:
                raise ConnectivityExecutionIntentIntegrityError("execution intent event sequence gap")
            if str(row["event_type"]) != ConnectivityExecutionIntentStatus.ISSUED.value:
                raise ConnectivityExecutionIntentIntegrityError("execution intent event type is invalid")
            context_hash = str(row["context_hash"])
            _validate_hash(context_hash, "context_hash")
            occurred_at = _datetime(row["occurred_at"], "occurred_at")
            payload_json = str(row["payload_json"])
            parsed = _json_object(payload_json, "execution intent event")
            if payload_json != _canonical(parsed):
                raise ConnectivityExecutionIntentIntegrityError("execution intent payload is non-canonical")
            if str(row["previous_event_hash"]) != previous:
                raise ConnectivityExecutionIntentIntegrityError("execution intent previous-hash mismatch")
            calculated = _event_hash(
                sequence=current_sequence,
                context_hash=context_hash,
                occurred_at=occurred_at,
                payload_json=payload_json,
                previous_event_hash=previous,
            )
            if str(row["event_hash"]) != calculated:
                raise ConnectivityExecutionIntentIntegrityError("execution intent event hash mismatch")
            decision = _decision_from_payload(parsed)
            if decision.context.context_hash != context_hash or decision.issued_at != occurred_at:
                raise ConnectivityExecutionIntentIntegrityError("execution intent event identity mismatch")
            if context_hash in states:
                raise ConnectivityExecutionIntentIntegrityError("execution intent issued more than once")
            states[context_hash] = ConnectivityExecutionIntentState(
                decision=decision,
                status=ConnectivityExecutionIntentStatus.ISSUED,
                event_hash=calculated,
            )
            previous = calculated
        if sequence == 0:
            if head != _GENESIS_HASH:
                raise ConnectivityExecutionIntentIntegrityError("empty execution intent registry has non-genesis head")
        elif previous != head:
            raise ConnectivityExecutionIntentIntegrityError("execution intent anchored head mismatch")
        return states, sequence, head


class ConnectivityExecutionIntentBridge:
    """Prepare/record the second human decision without crossing into execution."""

    def __init__(self, workspace: PaperOperationalWorkspace) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace

    @property
    def registry_path(self) -> Path:
        return self._workspace.root / _EXECUTION_INTENT_DB

    @property
    def context_path(self) -> Path:
        return self._workspace.root / _EXECUTION_INTENT_CONTEXT

    @property
    def artifact_path(self) -> Path:
        return self._workspace.root / _EXECUTION_INTENT_ARTIFACT

    def prepare_context(self, *, now: datetime) -> ConnectivityExecutionIntentContext:
        _require_aware(now, "now")
        if (self._workspace.root / _FINAL_FRESHNESS_ARTIFACT).exists() or (
            self._workspace.root / _FINAL_FRESHNESS_DB
        ).exists():
            raise ConnectivityExecutionIntentRejected(
                "execution intent must precede final freshness; never authorize an old freshness permit"
            )
        operator_registry_path = self._workspace.root / _OPERATOR_DB
        if not operator_registry_path.is_file() or operator_registry_path.is_symlink():
            raise ConnectivityExecutionIntentRejected("connectivity operator registry is missing")
        operator_states = SQLiteConnectivityOperatorDecisionRegistry(
            SQLiteRuntime(operator_registry_path)
        ).list_states()
        if len(operator_states) != 1:
            raise ConnectivityExecutionIntentRejected("exactly one connectivity operator decision is required")
        operator_state = operator_states[0]
        if operator_state.status is not ConnectivityOperatorDecisionStatus.ISSUED:
            raise ConnectivityExecutionIntentRejected("connectivity operator decision is not ISSUED")
        operator = operator_state.decision
        if not operator.is_valid_at(now):
            raise ConnectivityExecutionIntentRejected("connectivity operator decision is expired")
        operator_artifact = _read_json(
            self._workspace.root / _OPERATOR_ARTIFACT,
            "connectivity operator decision",
        )
        if operator_artifact.get("event_hash") != operator_state.event_hash:
            raise ConnectivityExecutionIntentRejected("operator artifact event hash mismatch")
        if operator_artifact.get("decision") != operator.payload():
            raise ConnectivityExecutionIntentRejected("operator artifact decision mismatch")
        for key, expected in (
            ("environment", "PAPER"),
            ("purpose", "CONNECTIVITY_CANARY"),
            ("oms_staging_authorized", False),
            ("external_post_authorized", False),
            ("external_order_submitted", False),
            ("strategy_trading_authorized", False),
            ("capital_authority", "NONE"),
            ("live_trading", "BLOCKED"),
            ("next_action", "CONNECTIVITY_FINAL_FRESHNESS_REQUIRED"),
        ):
            if operator_artifact.get(key) != expected:
                raise ConnectivityExecutionIntentRejected(f"unsafe operator artifact field: {key}")

        context0 = operator.context
        if _file_sha256(self._workspace.core_db_path) != context0.core_db_sha256_after_preparation:
            raise ConnectivityExecutionIntentRejected("core.sqlite3 changed after connectivity preparation")
        runtime = SQLiteRuntime(self._workspace.core_db_path)
        order = SQLiteOrderStore(runtime).get_by_order_id(context0.order_id)
        if order is None or order.status is not OrderStatus.VALIDATED:
            raise ConnectivityExecutionIntentRejected("OMS order must remain VALIDATED")
        if order.intent.strategy_id != CONNECTIVITY_CANARY_STRATEGY_ID:
            raise ConnectivityExecutionIntentRejected("OMS connectivity purpose drifted")
        if order.intent.quantity != Decimal("1") or order.intent.side.value != "BUY" or order.intent.order_type.value != "LIMIT":
            raise ConnectivityExecutionIntentRejected("OMS connectivity order shape drifted")
        submission = SQLitePaperSubmissionRegistry(
            SQLiteRuntime(self._workspace.submission_db_path)
        ).get(context0.order_id)
        if (
            submission.status is not PaperSubmissionStatus.PREPARED
            or submission.attempt_count != 0
            or submission.binding_hash != context0.submission_binding_hash
            or submission.broker_order_id is not None
            or submission.broker_client_order_id is not None
        ):
            raise ConnectivityExecutionIntentRejected("submission is no longer pristine PREPARED")
        permit = SQLitePaperCanaryPermitRegistry(
            SQLiteRuntime(self._workspace.permit_db_path)
        ).get(context0.canary_approval_hash)
        if (
            permit.status is not PaperCanaryPermitStatus.ISSUED
            or permit.event_hash != context0.permit_event_hash
            or permit.attempt_id is not None
        ):
            raise ConnectivityExecutionIntentRejected("original canary permit drifted")
        preparation = _read_json(
            self._workspace.root / _PREPARATION_ARTIFACT,
            "connectivity preparation",
        )
        if preparation.get("preparation_hash") != context0.connectivity_preparation_hash:
            raise ConnectivityExecutionIntentRejected("preparation/operator hash mismatch")
        body = dict(preparation)
        body.pop("preparation_hash", None)
        if _hash(body) != context0.connectivity_preparation_hash:
            raise ConnectivityExecutionIntentRejected("connectivity preparation hash mismatch")

        base = {
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "order_id": context0.order_id,
            "client_order_id": context0.client_order_id,
            "attempt_id": context0.attempt_id,
            "operator_context_hash": context0.context_hash,
            "operator_decision_hash": operator.decision_hash,
            "operator_event_hash": operator_state.event_hash,
            "preparation_hash": context0.connectivity_preparation_hash,
            "connectivity_binding_hash": context0.connectivity_binding_hash,
            "standard_package_hash": context0.standard_package_hash,
            "canary_approval_hash": context0.canary_approval_hash,
            "permit_event_hash": context0.permit_event_hash,
            "submission_binding_hash": context0.submission_binding_hash,
            "bracket_payload_hash": context0.bracket_payload_hash,
            "initial_account_fingerprint": context0.account_attestation_fingerprint,
            "notional": str(context0.notional),
            "core_db_sha256_after_preparation": context0.core_db_sha256_after_preparation,
        }
        context = ConnectivityExecutionIntentContext(
            environment="PAPER",
            purpose="CONNECTIVITY_CANARY",
            order_id=context0.order_id,
            client_order_id=context0.client_order_id,
            attempt_id=context0.attempt_id,
            operator_context_hash=context0.context_hash,
            operator_decision_hash=operator.decision_hash,
            operator_event_hash=operator_state.event_hash,
            preparation_hash=context0.connectivity_preparation_hash,
            connectivity_binding_hash=context0.connectivity_binding_hash,
            standard_package_hash=context0.standard_package_hash,
            canary_approval_hash=context0.canary_approval_hash,
            permit_event_hash=context0.permit_event_hash,
            submission_binding_hash=context0.submission_binding_hash,
            bracket_payload_hash=context0.bracket_payload_hash,
            initial_account_fingerprint=context0.account_attestation_fingerprint,
            notional=context0.notional,
            core_db_sha256_after_preparation=context0.core_db_sha256_after_preparation,
            context_hash=_hash(base),
        )
        _write_json_idempotent(self.context_path, context.payload())
        return context

    def issue(
        self,
        *,
        context: ConnectivityExecutionIntentContext,
        operator_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> ConnectivityExecutionIntentState:
        observed = self.prepare_context(now=issued_at)
        if observed != context:
            raise ConnectivityExecutionIntentRejected("execution intent context changed during human confirmation")
        registry = SQLiteConnectivityExecutionIntentRegistry(SQLiteRuntime(self.registry_path))
        state = registry.issue(
            context=context,
            operator_id=operator_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        artifact = {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "decision": state.decision.payload(),
            "status": state.status.value,
            "event_hash": state.event_hash,
            "human_execution_intent_recorded": True,
            "max_external_post_attempts": 1,
            "final_freshness_required": True,
            "oms_staging_authorized": False,
            "external_post_authorized": False,
            "external_order_submitted": False,
            "strategy_health_required": False,
            "strategy_trading_authorized": False,
            "capital_authority": "NONE",
            "profitability_claim": False,
            "live_trading": "BLOCKED",
            "next_action": "INLINE_FINAL_FRESHNESS_REQUIRED",
        }
        _write_json_idempotent(self.artifact_path, artifact)
        return state


def connectivity_execution_intent_challenge(context: ConnectivityExecutionIntentContext) -> str:
    if not isinstance(context, ConnectivityExecutionIntentContext):
        raise TypeError("ConnectivityExecutionIntentContext is required")
    return f"CONFIRM PAPER EXECUTION {context.context_hash[:12]}"


def _build_decision(
    *,
    context: ConnectivityExecutionIntentContext,
    operator_id: str,
    issued_at: datetime,
    expires_at: datetime,
) -> ConnectivityExecutionIntentDecision:
    _validate_id(operator_id, "operator_id")
    base = {
        "context": context.payload(),
        "operator_id": operator_id,
        "source": _SOURCE,
        "action": _ACTION,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
        "max_external_post_attempts": 1,
    }
    return ConnectivityExecutionIntentDecision(
        context=context,
        operator_id=operator_id,
        source=_SOURCE,
        action=_ACTION,
        issued_at=issued_at.astimezone(timezone.utc),
        expires_at=expires_at.astimezone(timezone.utc),
        max_external_post_attempts=1,
        decision_hash=_hash(base),
    )


def _context_payload(
    context: ConnectivityExecutionIntentContext, *, include_hash: bool
) -> dict[str, object]:
    payload = {
        "environment": context.environment,
        "purpose": context.purpose,
        "order_id": context.order_id,
        "client_order_id": context.client_order_id,
        "attempt_id": context.attempt_id,
        "operator_context_hash": context.operator_context_hash,
        "operator_decision_hash": context.operator_decision_hash,
        "operator_event_hash": context.operator_event_hash,
        "preparation_hash": context.preparation_hash,
        "connectivity_binding_hash": context.connectivity_binding_hash,
        "standard_package_hash": context.standard_package_hash,
        "canary_approval_hash": context.canary_approval_hash,
        "permit_event_hash": context.permit_event_hash,
        "submission_binding_hash": context.submission_binding_hash,
        "bracket_payload_hash": context.bracket_payload_hash,
        "initial_account_fingerprint": context.initial_account_fingerprint,
        "notional": str(context.notional),
        "core_db_sha256_after_preparation": context.core_db_sha256_after_preparation,
    }
    if include_hash:
        payload["context_hash"] = context.context_hash
    return payload


def _decision_payload(
    decision: ConnectivityExecutionIntentDecision, *, include_hash: bool
) -> dict[str, object]:
    payload = {
        "context": decision.context.payload(),
        "operator_id": decision.operator_id,
        "source": decision.source,
        "action": decision.action,
        "issued_at": _iso(decision.issued_at),
        "expires_at": _iso(decision.expires_at),
        "max_external_post_attempts": decision.max_external_post_attempts,
    }
    if include_hash:
        payload["decision_hash"] = decision.decision_hash
    return payload


def _decision_from_payload(payload: Mapping[str, object]) -> ConnectivityExecutionIntentDecision:
    if set(payload) != {
        "context", "operator_id", "source", "action", "issued_at", "expires_at",
        "max_external_post_attempts", "decision_hash",
    }:
        raise ConnectivityExecutionIntentIntegrityError("execution intent decision payload is non-canonical")
    context = _context_from_payload(_mapping(payload, "context"))
    try:
        return ConnectivityExecutionIntentDecision(
            context=context,
            operator_id=_required_str(payload, "operator_id"),
            source=_required_str(payload, "source"),
            action=_required_str(payload, "action"),
            issued_at=_datetime(payload.get("issued_at"), "issued_at"),
            expires_at=_datetime(payload.get("expires_at"), "expires_at"),
            max_external_post_attempts=_required_int(payload, "max_external_post_attempts"),
            decision_hash=_required_hash(payload, "decision_hash"),
        )
    except (TypeError, ValueError) as exc:
        raise ConnectivityExecutionIntentIntegrityError("invalid execution intent decision") from exc


def _context_from_payload(payload: Mapping[str, object]) -> ConnectivityExecutionIntentContext:
    expected = {
        "environment", "purpose", "order_id", "client_order_id", "attempt_id",
        "operator_context_hash", "operator_decision_hash", "operator_event_hash",
        "preparation_hash", "connectivity_binding_hash", "standard_package_hash",
        "canary_approval_hash", "permit_event_hash", "submission_binding_hash",
        "bracket_payload_hash", "initial_account_fingerprint", "notional",
        "core_db_sha256_after_preparation", "context_hash",
    }
    if set(payload) != expected:
        raise ConnectivityExecutionIntentIntegrityError("execution intent context payload is non-canonical")
    try:
        return ConnectivityExecutionIntentContext(
            environment=_required_str(payload, "environment"),
            purpose=_required_str(payload, "purpose"),
            order_id=_required_str(payload, "order_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            attempt_id=_required_str(payload, "attempt_id"),
            operator_context_hash=_required_hash(payload, "operator_context_hash"),
            operator_decision_hash=_required_hash(payload, "operator_decision_hash"),
            operator_event_hash=_required_hash(payload, "operator_event_hash"),
            preparation_hash=_required_hash(payload, "preparation_hash"),
            connectivity_binding_hash=_required_hash(payload, "connectivity_binding_hash"),
            standard_package_hash=_required_hash(payload, "standard_package_hash"),
            canary_approval_hash=_required_hash(payload, "canary_approval_hash"),
            permit_event_hash=_required_hash(payload, "permit_event_hash"),
            submission_binding_hash=_required_hash(payload, "submission_binding_hash"),
            bracket_payload_hash=_required_hash(payload, "bracket_payload_hash"),
            initial_account_fingerprint=_required_hash(payload, "initial_account_fingerprint"),
            notional=_positive_decimal(payload.get("notional"), "notional"),
            core_db_sha256_after_preparation=_required_hash(payload, "core_db_sha256_after_preparation"),
            context_hash=_required_hash(payload, "context_hash"),
        )
    except (TypeError, ValueError) as exc:
        raise ConnectivityExecutionIntentIntegrityError("invalid execution intent context") from exc


def _read_json(path: Path, label: str) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ConnectivityExecutionIntentRejected(f"{label} must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectivityExecutionIntentRejected(f"cannot read {label}") from exc
    if not isinstance(payload, dict):
        raise ConnectivityExecutionIntentRejected(f"{label} root must be object")
    return payload


def _write_json_idempotent(path: Path, payload: Mapping[str, object]) -> None:
    raw = json.dumps(dict(payload), sort_keys=True, indent=2, allow_nan=False) + "\n"
    if path.exists() and path.is_symlink():
        raise ConnectivityExecutionIntentConflict(f"{path.name} cannot be symlink")
    if path.exists():
        if path.read_text(encoding="utf-8") != raw:
            raise ConnectivityExecutionIntentConflict(f"refusing to overwrite different {path.name}")
        return
    path.write_text(raw, encoding="utf-8")
    path.chmod(0o600)


def _mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConnectivityExecutionIntentIntegrityError(f"{key} must be object")
    return value


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty string")
    return value


def _required_hash(payload: Mapping[str, object], key: str) -> str:
    value = _required_str(payload, key)
    _validate_hash(value, key)
    return value


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be integer")
    return value


def _positive_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return parsed


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be datetime string")
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConnectivityExecutionIntentIntegrityError(f"{label} must be integer")
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
        raise ConnectivityExecutionIntentIntegrityError(f"{label} JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ConnectivityExecutionIntentIntegrityError(f"{label} must be object")
    return payload


def _canonical(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


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
            "event_type": ConnectivityExecutionIntentStatus.ISSUED.value,
            "context_hash": context_hash,
            "occurred_at": _iso(occurred_at),
            "payload_json": payload_json,
            "previous_event_hash": previous_event_hash,
        }
    )


__all__ = [
    "ConnectivityExecutionIntentBridge",
    "ConnectivityExecutionIntentConflict",
    "ConnectivityExecutionIntentContext",
    "ConnectivityExecutionIntentDecision",
    "ConnectivityExecutionIntentError",
    "ConnectivityExecutionIntentIntegrityError",
    "ConnectivityExecutionIntentRejected",
    "ConnectivityExecutionIntentState",
    "ConnectivityExecutionIntentStatus",
    "SQLiteConnectivityExecutionIntentRegistry",
    "connectivity_execution_intent_challenge",
]
