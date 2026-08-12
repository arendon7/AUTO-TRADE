from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace, _file_sha256
from autotrade.brokers.alpaca_paper_submission import PaperSubmissionStatus, SQLitePaperSubmissionRegistry
from autotrade.connectivity_execution_intent import (
    ConnectivityExecutionIntentDecision,
    ConnectivityExecutionIntentStatus,
    SQLiteConnectivityExecutionIntentRegistry,
)
from autotrade.connectivity_final_freshness import (
    ConnectivityFinalFreshnessGuard,
    ConnectivityFinalFreshnessResult,
    ConnectivityFinalFreshnessStatus,
    SQLiteConnectivityFinalFreshnessRegistry,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_GENESIS_HASH = "0" * 64
_EXECUTION_INTENT_DB = "connectivity_execution_intent.sqlite3"
_EXECUTION_INTENT_ARTIFACT = "connectivity_execution_intent.json"
_FINAL_FRESHNESS_DB = "connectivity_final_freshness.sqlite3"
_FINAL_FRESHNESS_ARTIFACT = "connectivity_final_freshness.json"
_BINDING_DB = "connectivity_execution_freshness_binding.sqlite3"
_BINDING_ARTIFACT = "connectivity_execution_freshness_binding.json"


class ConnectivityExecutionFreshnessError(RuntimeError):
    pass


class ConnectivityExecutionFreshnessRejected(ConnectivityExecutionFreshnessError):
    pass


class ConnectivityExecutionFreshnessConflict(ConnectivityExecutionFreshnessError):
    pass


class ConnectivityExecutionFreshnessIntegrityError(ConnectivityExecutionFreshnessError):
    pass


@dataclass(frozen=True, slots=True)
class ConnectivityExecutionFreshnessBinding:
    order_id: str
    client_order_id: str
    attempt_id: str
    execution_intent_context_hash: str
    execution_intent_decision_hash: str
    execution_intent_event_hash: str
    execution_intent_artifact_sha256: str
    operator_context_hash: str
    operator_decision_hash: str
    preparation_hash: str
    final_freshness_permit_hash: str
    final_freshness_event_hash: str
    final_freshness_artifact_sha256: str
    fresh_risk_decision_id: str
    fresh_risk_decision_fingerprint: str
    fresh_market_fingerprint: str
    safety_state_version: int
    core_db_sha256_after_fresh_safety: str
    issued_at: datetime
    expires_at: datetime
    binding_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("order_id", self.order_id),
            ("client_order_id", self.client_order_id),
            ("attempt_id", self.attempt_id),
            ("fresh_risk_decision_id", self.fresh_risk_decision_id),
        ):
            _validate_id(value, label)
        for label, value in (
            ("execution_intent_context_hash", self.execution_intent_context_hash),
            ("execution_intent_decision_hash", self.execution_intent_decision_hash),
            ("execution_intent_event_hash", self.execution_intent_event_hash),
            ("execution_intent_artifact_sha256", self.execution_intent_artifact_sha256),
            ("operator_context_hash", self.operator_context_hash),
            ("operator_decision_hash", self.operator_decision_hash),
            ("preparation_hash", self.preparation_hash),
            ("final_freshness_permit_hash", self.final_freshness_permit_hash),
            ("final_freshness_event_hash", self.final_freshness_event_hash),
            ("final_freshness_artifact_sha256", self.final_freshness_artifact_sha256),
            ("fresh_risk_decision_fingerprint", self.fresh_risk_decision_fingerprint),
            ("fresh_market_fingerprint", self.fresh_market_fingerprint),
            ("core_db_sha256_after_fresh_safety", self.core_db_sha256_after_fresh_safety),
            ("binding_hash", self.binding_hash),
        ):
            _validate_hash(value, label)
        if isinstance(self.safety_state_version, bool) or not isinstance(self.safety_state_version, int) or self.safety_state_version < 0:
            raise ValueError("safety_state_version must be non-negative integer")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("execution/freshness binding must expire after issue")
        if self.binding_hash != _hash(_binding_payload(self, include_hash=False)):
            raise ValueError("execution/freshness binding hash mismatch")

    def is_valid_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        return self.issued_at.astimezone(timezone.utc) <= instant < self.expires_at.astimezone(timezone.utc)

    def payload(self) -> dict[str, object]:
        return _binding_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class ConnectivityExecutionFreshnessState:
    binding: ConnectivityExecutionFreshnessBinding
    event_hash: str


class SQLiteConnectivityExecutionFreshnessRegistry:
    """One immutable binding event. No consume, OMS, writer or broker API."""

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        conn = runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS connectivity_execution_freshness_events (
                    sequence INTEGER PRIMARY KEY,
                    binding_hash TEXT NOT NULL UNIQUE,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS connectivity_execution_freshness_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    event_sequence INTEGER NOT NULL CHECK(event_sequence>=0),
                    event_head_hash TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                """
            )
            if conn.execute(
                "SELECT 1 FROM connectivity_execution_freshness_control WHERE singleton=1"
            ).fetchone() is None:
                conn.execute(
                    "INSERT INTO connectivity_execution_freshness_control(singleton,event_sequence,event_head_hash,control_hash) VALUES(1,0,?,?)",
                    (_GENESIS_HASH, _control_hash(0, _GENESIS_HASH)),
                )
        finally:
            conn.close()

    def issue(self, binding: ConnectivityExecutionFreshnessBinding) -> ConnectivityExecutionFreshnessState:
        if not isinstance(binding, ConnectivityExecutionFreshnessBinding):
            raise TypeError("ConnectivityExecutionFreshnessBinding is required")
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            states, sequence, head = self._verify_locked(conn)
            if states:
                existing = next(iter(states.values()))
                if existing.binding == binding:
                    conn.execute("COMMIT")
                    return existing
                raise ConnectivityExecutionFreshnessConflict(
                    "workspace already contains a different execution/freshness binding"
                )
            payload_json = _canonical(binding.payload())
            event_hash = _event_hash(
                sequence=sequence + 1,
                binding_hash=binding.binding_hash,
                occurred_at=binding.issued_at,
                payload_json=payload_json,
                previous_event_hash=head,
            )
            conn.execute(
                "INSERT INTO connectivity_execution_freshness_events(sequence,binding_hash,occurred_at,payload_json,previous_event_hash,event_hash) VALUES(?,?,?,?,?,?)",
                (
                    sequence + 1,
                    binding.binding_hash,
                    _iso(binding.issued_at),
                    payload_json,
                    head,
                    event_hash,
                ),
            )
            conn.execute(
                "UPDATE connectivity_execution_freshness_control SET event_sequence=?,event_head_hash=?,control_hash=? WHERE singleton=1",
                (sequence + 1, event_hash, _control_hash(sequence + 1, event_hash)),
            )
            states, _, _ = self._verify_locked(conn)
            state = states[binding.binding_hash]
            conn.execute("COMMIT")
            return state
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, binding_hash: str) -> ConnectivityExecutionFreshnessState:
        _validate_hash(binding_hash, "binding_hash")
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            if binding_hash not in states:
                raise KeyError(binding_hash)
            return states[binding_hash]
        finally:
            conn.close()

    def list_states(self) -> tuple[ConnectivityExecutionFreshnessState, ...]:
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            return tuple(states[key] for key in sorted(states))
        finally:
            conn.close()

    def _verify_locked(
        self, conn: sqlite3.Connection
    ) -> tuple[dict[str, ConnectivityExecutionFreshnessState], int, str]:
        control = conn.execute(
            "SELECT event_sequence,event_head_hash,control_hash FROM connectivity_execution_freshness_control WHERE singleton=1"
        ).fetchone()
        if control is None:
            raise ConnectivityExecutionFreshnessIntegrityError("execution/freshness control anchor is missing")
        sequence = _strict_int(control["event_sequence"], "event_sequence")
        head = str(control["event_head_hash"])
        _validate_hash(head, "event_head_hash")
        if str(control["control_hash"]) != _control_hash(sequence, head):
            raise ConnectivityExecutionFreshnessIntegrityError("execution/freshness control hash mismatch")
        rows = conn.execute(
            "SELECT sequence,binding_hash,occurred_at,payload_json,previous_event_hash,event_hash FROM connectivity_execution_freshness_events ORDER BY sequence"
        ).fetchall()
        if len(rows) != sequence:
            raise ConnectivityExecutionFreshnessIntegrityError("execution/freshness event count mismatch")
        states: dict[str, ConnectivityExecutionFreshnessState] = {}
        previous = _GENESIS_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            current_sequence = _strict_int(row["sequence"], "sequence")
            if current_sequence != expected_sequence:
                raise ConnectivityExecutionFreshnessIntegrityError("execution/freshness event sequence gap")
            binding_hash = str(row["binding_hash"])
            _validate_hash(binding_hash, "binding_hash")
            occurred_at = _datetime(row["occurred_at"], "occurred_at")
            payload_json = str(row["payload_json"])
            parsed = _json_object(payload_json, "execution/freshness event")
            if payload_json != _canonical(parsed):
                raise ConnectivityExecutionFreshnessIntegrityError("execution/freshness payload is non-canonical")
            if str(row["previous_event_hash"]) != previous:
                raise ConnectivityExecutionFreshnessIntegrityError("execution/freshness previous-hash mismatch")
            calculated = _event_hash(
                sequence=current_sequence,
                binding_hash=binding_hash,
                occurred_at=occurred_at,
                payload_json=payload_json,
                previous_event_hash=previous,
            )
            if str(row["event_hash"]) != calculated:
                raise ConnectivityExecutionFreshnessIntegrityError("execution/freshness event hash mismatch")
            binding = _binding_from_payload(parsed)
            if binding.binding_hash != binding_hash or binding.issued_at != occurred_at:
                raise ConnectivityExecutionFreshnessIntegrityError("execution/freshness event identity mismatch")
            if binding_hash in states:
                raise ConnectivityExecutionFreshnessIntegrityError("execution/freshness binding issued more than once")
            states[binding_hash] = ConnectivityExecutionFreshnessState(
                binding=binding,
                event_hash=calculated,
            )
            previous = calculated
        if sequence == 0:
            if head != _GENESIS_HASH:
                raise ConnectivityExecutionFreshnessIntegrityError(
                    "empty execution/freshness registry has non-genesis head"
                )
        elif previous != head:
            raise ConnectivityExecutionFreshnessIntegrityError("execution/freshness anchored head mismatch")
        return states, sequence, head


@dataclass(frozen=True, slots=True)
class ConnectivityBoundFinalFreshnessResult:
    final_freshness: ConnectivityFinalFreshnessResult
    binding: ConnectivityExecutionFreshnessBinding
    state: ConnectivityExecutionFreshnessState
    artifact_path: Path


class ConnectivityBoundFinalFreshnessGuard:
    """Bind the second human intent to a newly acquired Final Freshness permit."""

    def __init__(
        self,
        workspace: PaperOperationalWorkspace,
        *,
        final_guard: ConnectivityFinalFreshnessGuard | None = None,
    ) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace
        self._final_guard = final_guard or ConnectivityFinalFreshnessGuard(workspace)

    @property
    def registry_path(self) -> Path:
        return self._workspace.root / _BINDING_DB

    @property
    def artifact_path(self) -> Path:
        return self._workspace.root / _BINDING_ARTIFACT

    def acquire(
        self,
        *,
        credentials: AlpacaPaperCredentials,
    ) -> ConnectivityBoundFinalFreshnessResult:
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("AlpacaPaperCredentials are required")
        if self.registry_path.exists() or self.artifact_path.exists():
            raise ConnectivityExecutionFreshnessRejected(
                "execution/freshness binding already exists; never refresh in-place"
            )
        intent_state = self._load_execution_intent()
        intent = intent_state.decision
        started_at = _utc_now()
        if not intent.is_valid_at(started_at):
            raise ConnectivityExecutionFreshnessRejected("second human execution intent is expired")
        self._verify_pre_execution_state(intent)
        execution_intent_artifact_hash = _file_sha256(
            self._workspace.root / _EXECUTION_INTENT_ARTIFACT
        )

        final_result = self._final_guard.acquire(credentials=credentials)
        completed_at = final_result.permit.issued_at.astimezone(timezone.utc)
        if not intent.is_valid_at(completed_at):
            raise ConnectivityExecutionFreshnessRejected(
                "second human execution intent expired during Final Freshness acquisition"
            )
        self._verify_pre_execution_state(intent)
        self._verify_final_freshness(final_result)

        expires_at = min(
            intent.expires_at.astimezone(timezone.utc),
            final_result.permit.expires_at.astimezone(timezone.utc),
        )
        if expires_at <= completed_at:
            raise ConnectivityExecutionFreshnessRejected(
                "no execution/freshness binding window remains"
            )
        final_artifact_hash = _file_sha256(
            self._workspace.root / _FINAL_FRESHNESS_ARTIFACT
        )
        binding = _build_binding(
            intent=intent,
            intent_event_hash=intent_state.event_hash,
            execution_intent_artifact_sha256=execution_intent_artifact_hash,
            final_result=final_result,
            final_artifact_sha256=final_artifact_hash,
            issued_at=completed_at,
            expires_at=expires_at,
        )
        state = SQLiteConnectivityExecutionFreshnessRegistry(
            SQLiteRuntime(self.registry_path)
        ).issue(binding)
        artifact = {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "binding": binding.payload(),
            "registry_event_hash": state.event_hash,
            "second_human_execution_intent_bound": True,
            "final_freshness_bound": True,
            "max_external_post_attempts": 1,
            "oms_staging_authorized": False,
            "external_post_authorized": False,
            "external_order_submitted": False,
            "strategy_health_required": False,
            "strategy_trading_authorized": False,
            "capital_authority": "NONE",
            "profitability_claim": False,
            "live_trading": "BLOCKED",
            "next_action": "CONNECTIVITY_STAGING_BRIDGE_REQUIRED",
        }
        _write_json_exclusive(self.artifact_path, artifact)
        return ConnectivityBoundFinalFreshnessResult(
            final_freshness=final_result,
            binding=binding,
            state=state,
            artifact_path=self.artifact_path,
        )

    def _load_execution_intent(self):
        registry_path = self._workspace.root / _EXECUTION_INTENT_DB
        artifact_path = self._workspace.root / _EXECUTION_INTENT_ARTIFACT
        if not registry_path.is_file() or registry_path.is_symlink():
            raise ConnectivityExecutionFreshnessRejected("second execution-intent registry is missing")
        if not artifact_path.is_file() or artifact_path.is_symlink():
            raise ConnectivityExecutionFreshnessRejected("second execution-intent artifact is missing")
        states = SQLiteConnectivityExecutionIntentRegistry(
            SQLiteRuntime(registry_path)
        ).list_states()
        if len(states) != 1:
            raise ConnectivityExecutionFreshnessRejected("exactly one second execution intent is required")
        state = states[0]
        if state.status is not ConnectivityExecutionIntentStatus.ISSUED:
            raise ConnectivityExecutionFreshnessRejected("second execution intent is not ISSUED")
        artifact = _read_json(artifact_path, "second execution-intent artifact")
        if artifact.get("decision") != state.decision.payload():
            raise ConnectivityExecutionFreshnessRejected("execution-intent artifact/registry decision mismatch")
        if artifact.get("event_hash") != state.event_hash:
            raise ConnectivityExecutionFreshnessRejected("execution-intent artifact/registry event mismatch")
        for key, expected in (
            ("environment", "PAPER"),
            ("purpose", "CONNECTIVITY_CANARY"),
            ("human_execution_intent_recorded", True),
            ("max_external_post_attempts", 1),
            ("final_freshness_required", True),
            ("oms_staging_authorized", False),
            ("external_post_authorized", False),
            ("external_order_submitted", False),
            ("capital_authority", "NONE"),
            ("live_trading", "BLOCKED"),
            ("next_action", "INLINE_FINAL_FRESHNESS_REQUIRED"),
        ):
            if artifact.get(key) != expected:
                raise ConnectivityExecutionFreshnessRejected(
                    f"unsafe execution-intent artifact field: {key}"
                )
        return state

    def _verify_pre_execution_state(self, intent: ConnectivityExecutionIntentDecision) -> None:
        context = intent.context
        runtime = SQLiteRuntime(self._workspace.core_db_path)
        order = SQLiteOrderStore(runtime).get_by_order_id(context.order_id)
        if order is None or order.status is not OrderStatus.VALIDATED:
            raise ConnectivityExecutionFreshnessRejected("OMS order must remain VALIDATED before binding")
        if _file_sha256(self._workspace.core_db_path) != context.core_db_sha256_after_preparation:
            # Final Freshness itself creates a fresh Safety decision in core. After
            # acquisition this check is intentionally replaced by the permit's
            # post-Safety core hash in _verify_final_freshness.
            if not (self._workspace.root / _FINAL_FRESHNESS_ARTIFACT).exists():
                raise ConnectivityExecutionFreshnessRejected(
                    "core.sqlite3 changed before Final Freshness acquisition"
                )
        submission = SQLitePaperSubmissionRegistry(
            SQLiteRuntime(self._workspace.submission_db_path)
        ).get(context.order_id)
        if submission.status is not PaperSubmissionStatus.PREPARED or submission.attempt_count != 0:
            raise ConnectivityExecutionFreshnessRejected(
                "submission must remain pristine PREPARED before staging"
            )
        if submission.binding_hash != context.submission_binding_hash:
            raise ConnectivityExecutionFreshnessRejected("submission binding drifted")

    def _verify_final_freshness(self, result: ConnectivityFinalFreshnessResult) -> None:
        if result.state.status is not ConnectivityFinalFreshnessStatus.ISSUED:
            raise ConnectivityExecutionFreshnessRejected("Final Freshness is not ISSUED")
        permit = result.permit
        if not permit.is_valid_at(permit.issued_at):
            raise ConnectivityExecutionFreshnessRejected("Final Freshness permit is invalid at issue")
        if _file_sha256(self._workspace.core_db_path) != permit.core_db_sha256_after_fresh_safety:
            raise ConnectivityExecutionFreshnessRejected(
                "core.sqlite3 changed after Final Freshness Safety decision"
            )
        registry_states = SQLiteConnectivityFinalFreshnessRegistry(
            SQLiteRuntime(self._workspace.root / _FINAL_FRESHNESS_DB)
        ).list_states()
        if len(registry_states) != 1 or registry_states[0] != result.state:
            raise ConnectivityExecutionFreshnessRejected(
                "Final Freshness result/registry mismatch"
            )
        artifact = _read_json(
            self._workspace.root / _FINAL_FRESHNESS_ARTIFACT,
            "Final Freshness artifact",
        )
        if artifact.get("permit") != permit.payload():
            raise ConnectivityExecutionFreshnessRejected(
                "Final Freshness artifact/permit mismatch"
            )
        if artifact.get("registry_event_hash") != result.state.event_hash:
            raise ConnectivityExecutionFreshnessRejected(
                "Final Freshness artifact/registry event mismatch"
            )
        if artifact.get("external_post_authorized") is not False:
            raise ConnectivityExecutionFreshnessRejected(
                "Final Freshness unexpectedly claims POST authority"
            )


def _build_binding(
    *,
    intent: ConnectivityExecutionIntentDecision,
    intent_event_hash: str,
    execution_intent_artifact_sha256: str,
    final_result: ConnectivityFinalFreshnessResult,
    final_artifact_sha256: str,
    issued_at: datetime,
    expires_at: datetime,
) -> ConnectivityExecutionFreshnessBinding:
    permit = final_result.permit
    values: dict[str, object] = {
        "order_id": intent.context.order_id,
        "client_order_id": intent.context.client_order_id,
        "attempt_id": intent.context.attempt_id,
        "execution_intent_context_hash": intent.context.context_hash,
        "execution_intent_decision_hash": intent.decision_hash,
        "execution_intent_event_hash": intent_event_hash,
        "execution_intent_artifact_sha256": execution_intent_artifact_sha256,
        "operator_context_hash": intent.context.operator_context_hash,
        "operator_decision_hash": intent.context.operator_decision_hash,
        "preparation_hash": intent.context.preparation_hash,
        "final_freshness_permit_hash": permit.permit_hash,
        "final_freshness_event_hash": final_result.state.event_hash,
        "final_freshness_artifact_sha256": final_artifact_sha256,
        "fresh_risk_decision_id": permit.fresh_risk_decision_id,
        "fresh_risk_decision_fingerprint": permit.fresh_risk_decision_fingerprint,
        "fresh_market_fingerprint": permit.fresh_market_fingerprint,
        "safety_state_version": permit.safety_state_version,
        "core_db_sha256_after_fresh_safety": permit.core_db_sha256_after_fresh_safety,
        "issued_at": issued_at.astimezone(timezone.utc),
        "expires_at": expires_at.astimezone(timezone.utc),
    }
    values["binding_hash"] = _hash(_binding_payload_from_values(values))
    return ConnectivityExecutionFreshnessBinding(**values)  # type: ignore[arg-type]


def _binding_payload(
    binding: ConnectivityExecutionFreshnessBinding,
    *,
    include_hash: bool,
) -> dict[str, object]:
    values = {
        "order_id": binding.order_id,
        "client_order_id": binding.client_order_id,
        "attempt_id": binding.attempt_id,
        "execution_intent_context_hash": binding.execution_intent_context_hash,
        "execution_intent_decision_hash": binding.execution_intent_decision_hash,
        "execution_intent_event_hash": binding.execution_intent_event_hash,
        "execution_intent_artifact_sha256": binding.execution_intent_artifact_sha256,
        "operator_context_hash": binding.operator_context_hash,
        "operator_decision_hash": binding.operator_decision_hash,
        "preparation_hash": binding.preparation_hash,
        "final_freshness_permit_hash": binding.final_freshness_permit_hash,
        "final_freshness_event_hash": binding.final_freshness_event_hash,
        "final_freshness_artifact_sha256": binding.final_freshness_artifact_sha256,
        "fresh_risk_decision_id": binding.fresh_risk_decision_id,
        "fresh_risk_decision_fingerprint": binding.fresh_risk_decision_fingerprint,
        "fresh_market_fingerprint": binding.fresh_market_fingerprint,
        "safety_state_version": binding.safety_state_version,
        "core_db_sha256_after_fresh_safety": binding.core_db_sha256_after_fresh_safety,
        "issued_at": binding.issued_at,
        "expires_at": binding.expires_at,
    }
    payload = _binding_payload_from_values(values)
    if include_hash:
        payload["binding_hash"] = binding.binding_hash
    return payload


def _binding_payload_from_values(values: Mapping[str, object]) -> dict[str, object]:
    return {
        "order_id": values["order_id"],
        "client_order_id": values["client_order_id"],
        "attempt_id": values["attempt_id"],
        "execution_intent_context_hash": values["execution_intent_context_hash"],
        "execution_intent_decision_hash": values["execution_intent_decision_hash"],
        "execution_intent_event_hash": values["execution_intent_event_hash"],
        "execution_intent_artifact_sha256": values["execution_intent_artifact_sha256"],
        "operator_context_hash": values["operator_context_hash"],
        "operator_decision_hash": values["operator_decision_hash"],
        "preparation_hash": values["preparation_hash"],
        "final_freshness_permit_hash": values["final_freshness_permit_hash"],
        "final_freshness_event_hash": values["final_freshness_event_hash"],
        "final_freshness_artifact_sha256": values["final_freshness_artifact_sha256"],
        "fresh_risk_decision_id": values["fresh_risk_decision_id"],
        "fresh_risk_decision_fingerprint": values["fresh_risk_decision_fingerprint"],
        "fresh_market_fingerprint": values["fresh_market_fingerprint"],
        "safety_state_version": values["safety_state_version"],
        "core_db_sha256_after_fresh_safety": values["core_db_sha256_after_fresh_safety"],
        "issued_at": _iso(values["issued_at"]),
        "expires_at": _iso(values["expires_at"]),
        "max_external_post_attempts": 1,
        "oms_staging_authorized": False,
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def _binding_from_payload(payload: Mapping[str, object]) -> ConnectivityExecutionFreshnessBinding:
    expected = {
        "order_id", "client_order_id", "attempt_id", "execution_intent_context_hash",
        "execution_intent_decision_hash", "execution_intent_event_hash",
        "execution_intent_artifact_sha256", "operator_context_hash", "operator_decision_hash",
        "preparation_hash", "final_freshness_permit_hash", "final_freshness_event_hash",
        "final_freshness_artifact_sha256", "fresh_risk_decision_id",
        "fresh_risk_decision_fingerprint", "fresh_market_fingerprint", "safety_state_version",
        "core_db_sha256_after_fresh_safety", "issued_at", "expires_at",
        "max_external_post_attempts", "oms_staging_authorized", "external_post_authorized",
        "capital_authority", "live_trading", "binding_hash",
    }
    if set(payload) != expected:
        raise ConnectivityExecutionFreshnessIntegrityError(
            "execution/freshness binding payload is non-canonical"
        )
    for key, expected_value in (
        ("max_external_post_attempts", 1),
        ("oms_staging_authorized", False),
        ("external_post_authorized", False),
        ("capital_authority", "NONE"),
        ("live_trading", "BLOCKED"),
    ):
        if payload.get(key) != expected_value:
            raise ConnectivityExecutionFreshnessIntegrityError(
                f"unsafe execution/freshness binding field: {key}"
            )
    try:
        return ConnectivityExecutionFreshnessBinding(
            order_id=_required_str(payload, "order_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            attempt_id=_required_str(payload, "attempt_id"),
            execution_intent_context_hash=_required_hash(payload, "execution_intent_context_hash"),
            execution_intent_decision_hash=_required_hash(payload, "execution_intent_decision_hash"),
            execution_intent_event_hash=_required_hash(payload, "execution_intent_event_hash"),
            execution_intent_artifact_sha256=_required_hash(payload, "execution_intent_artifact_sha256"),
            operator_context_hash=_required_hash(payload, "operator_context_hash"),
            operator_decision_hash=_required_hash(payload, "operator_decision_hash"),
            preparation_hash=_required_hash(payload, "preparation_hash"),
            final_freshness_permit_hash=_required_hash(payload, "final_freshness_permit_hash"),
            final_freshness_event_hash=_required_hash(payload, "final_freshness_event_hash"),
            final_freshness_artifact_sha256=_required_hash(payload, "final_freshness_artifact_sha256"),
            fresh_risk_decision_id=_required_str(payload, "fresh_risk_decision_id"),
            fresh_risk_decision_fingerprint=_required_hash(payload, "fresh_risk_decision_fingerprint"),
            fresh_market_fingerprint=_required_hash(payload, "fresh_market_fingerprint"),
            safety_state_version=_required_int(payload, "safety_state_version"),
            core_db_sha256_after_fresh_safety=_required_hash(payload, "core_db_sha256_after_fresh_safety"),
            issued_at=_datetime(payload.get("issued_at"), "issued_at"),
            expires_at=_datetime(payload.get("expires_at"), "expires_at"),
            binding_hash=_required_hash(payload, "binding_hash"),
        )
    except (TypeError, ValueError) as exc:
        raise ConnectivityExecutionFreshnessIntegrityError(
            "invalid execution/freshness binding"
        ) from exc


def _read_json(path: Path, label: str) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ConnectivityExecutionFreshnessRejected(f"{label} must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectivityExecutionFreshnessRejected(f"cannot read {label}") from exc
    if not isinstance(payload, dict):
        raise ConnectivityExecutionFreshnessRejected(f"{label} root must be object")
    return payload


def _write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise ConnectivityExecutionFreshnessConflict(f"refusing to overwrite {path.name}")
    raw = json.dumps(dict(payload), sort_keys=True, indent=2, allow_nan=False) + "\n"
    path.write_text(raw, encoding="utf-8")
    path.chmod(0o600)


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


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be datetime string")
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConnectivityExecutionFreshnessIntegrityError(f"{label} must be integer")
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


def _iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise ValueError("datetime value is required")
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_object(raw: str, label: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectivityExecutionFreshnessIntegrityError(f"{label} JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ConnectivityExecutionFreshnessIntegrityError(f"{label} must be object")
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
    binding_hash: str,
    occurred_at: datetime,
    payload_json: str,
    previous_event_hash: str,
) -> str:
    return _hash(
        {
            "sequence": sequence,
            "binding_hash": binding_hash,
            "occurred_at": _iso(occurred_at),
            "payload_json": payload_json,
            "previous_event_hash": previous_event_hash,
        }
    )


__all__ = [
    "ConnectivityBoundFinalFreshnessGuard",
    "ConnectivityBoundFinalFreshnessResult",
    "ConnectivityExecutionFreshnessBinding",
    "ConnectivityExecutionFreshnessConflict",
    "ConnectivityExecutionFreshnessError",
    "ConnectivityExecutionFreshnessIntegrityError",
    "ConnectivityExecutionFreshnessRejected",
    "ConnectivityExecutionFreshnessState",
    "SQLiteConnectivityExecutionFreshnessRegistry",
]
