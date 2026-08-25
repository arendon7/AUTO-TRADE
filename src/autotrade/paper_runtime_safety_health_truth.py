from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

import autotrade.paper_runtime_candidate_identity as candidate_module
from autotrade.paper_runtime_candidate_identity import PaperRuntimeCandidateIdentityProof


PAPER_RUNTIME_SAFETY_HEALTH_TRUTH_VERSION = "W86_PAPER_RUNTIME_SAFETY_HEALTH_TRUTH_V1"
PORTFOLIO_HEALTH_ENTITY_ID = "R6_CRYPTO_PAPER_PORTFOLIO"
COMMISSIONING_EVENT = "R6_HEALTH_R4_CORE_COMMISSIONED"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_REQUIRED_TABLES = frozenset(
    {
        "safety_state",
        "ledger_events",
        "health_state_v2",
        "health_recovery_acks_v3",
        "health_bridge_state",
    }
)
_SAFETY_EVENTS = frozenset(
    {
        "KILL_SWITCH_ACTIVATED",
        "KILL_SWITCH_RESET",
        "CIRCUIT_ACTIVATED",
        "CIRCUIT_ACKNOWLEDGED",
        "HEALTH_BRIDGE_APPLIED",
        "HEALTH_BRIDGE_RECOVERY_ACKNOWLEDGED",
    }
)


class PaperRuntimeSafetyHealthTruthError(RuntimeError):
    pass


class PaperRuntimeSafetyHealthTruthIntegrityError(PaperRuntimeSafetyHealthTruthError):
    pass


@dataclass(frozen=True, slots=True)
class PaperRuntimeSafetyHealthTruthPolicy:
    max_health_state_age_seconds: int = 3600

    def __post_init__(self) -> None:
        value = self.max_health_state_age_seconds
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
            raise ValueError("max_health_state_age_seconds must be integer seconds in [1, 3600]")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "max_health_state_age_seconds": self.max_health_state_age_seconds,
                "portfolio_health_entity_id": PORTFOLIO_HEALTH_ENTITY_ID,
            }
        )


@dataclass(frozen=True, slots=True)
class _SafetyObserved:
    kill_switch_active: bool
    kill_switch_reason: str
    circuit_active: bool
    circuit_reason: str
    version: int
    updated_at: datetime | None

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "kill_switch_active": self.kill_switch_active,
                "kill_switch_reason": self.kill_switch_reason,
                "circuit_active": self.circuit_active,
                "circuit_reason": self.circuit_reason,
                "version": self.version,
                "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            }
        )


@dataclass(frozen=True, slots=True)
class _HealthObserved:
    entity_kind: str
    entity_id: str
    state: str
    version: int
    distinct_quarantine_count: int
    baseline_fingerprint: str
    policy_fingerprint: str
    last_assessment_fingerprint: str
    updated_at: datetime
    recovery_ack_head: str
    fingerprint: str
    recovery_ack_count: int


@dataclass(frozen=True, slots=True)
class _BridgeObserved:
    entity_kind: str
    entity_id: str
    mode: str
    risk_multiplier: Decimal
    health_state_version: int
    health_state_fingerprint: str
    baseline_fingerprint: str
    policy_fingerprint: str
    bridge_version: int
    updated_at: datetime
    fingerprint: str


@dataclass(frozen=True, slots=True)
class _LedgerProjection:
    event_count: int
    head_hash: str
    safety: _SafetyObserved


@dataclass(frozen=True, slots=True)
class PaperRuntimeSafetyHealthTruthProof:
    proof_id: str
    contract_version: str
    candidate_identity_hash: str
    policy_hash: str
    authority_key: str
    admission_hash: str
    selected_strategy_id: str
    portfolio_health_entity_id: str
    sqlite_data_version: int
    ledger_event_count: int
    ledger_head_hash: str
    safety_version: int
    safety_state_fingerprint: str
    kill_switch_active: bool
    kill_switch_reason: str
    circuit_active: bool
    circuit_reason: str
    safety_updated_at: datetime | None
    strategy_health_version: int
    strategy_health_fingerprint: str
    strategy_health_updated_at: datetime
    strategy_recovery_ack_head: str
    strategy_recovery_ack_count: int
    strategy_bridge_version: int
    strategy_bridge_fingerprint: str
    strategy_bridge_updated_at: datetime
    portfolio_health_version: int
    portfolio_health_fingerprint: str
    portfolio_health_updated_at: datetime
    portfolio_recovery_ack_head: str
    portfolio_recovery_ack_count: int
    portfolio_bridge_version: int
    portfolio_bridge_fingerprint: str
    portfolio_bridge_updated_at: datetime
    observed_at: datetime
    safety_health_valid_until: datetime
    ledger_integrity_verified: bool
    safety_projection_verified: bool
    strategy_health_verified: bool
    portfolio_health_verified: bool
    read_only_core_truth: bool
    sqlite_snapshot_consistent: bool
    concurrent_durable_change_detected: bool
    paper_runtime_ready: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    proof_hash: str

    def __post_init__(self) -> None:
        _require_id(self.proof_id, "proof_id")
        _require_id(self.selected_strategy_id, "selected_strategy_id")
        if self.portfolio_health_entity_id != PORTFOLIO_HEALTH_ENTITY_ID:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "portfolio Health identity is not canonical R6 PAPER portfolio"
            )
        if self.contract_version != PAPER_RUNTIME_SAFETY_HEALTH_TRUTH_VERSION:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "W86 Safety/Health truth version is not canonical"
            )
        for label, value in (
            ("candidate_identity_hash", self.candidate_identity_hash),
            ("policy_hash", self.policy_hash),
            ("authority_key", self.authority_key),
            ("admission_hash", self.admission_hash),
            ("ledger_head_hash", self.ledger_head_hash),
            ("safety_state_fingerprint", self.safety_state_fingerprint),
            ("strategy_health_fingerprint", self.strategy_health_fingerprint),
            ("strategy_bridge_fingerprint", self.strategy_bridge_fingerprint),
            ("portfolio_health_fingerprint", self.portfolio_health_fingerprint),
            ("portfolio_bridge_fingerprint", self.portfolio_bridge_fingerprint),
            ("proof_hash", self.proof_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("strategy_recovery_ack_head", self.strategy_recovery_ack_head),
            ("portfolio_recovery_ack_head", self.portfolio_recovery_ack_head),
        ):
            if value != "GENESIS":
                _require_hash(value, label)
        for label, value, minimum in (
            ("sqlite_data_version", self.sqlite_data_version, 0),
            ("ledger_event_count", self.ledger_event_count, 0),
            ("safety_version", self.safety_version, 0),
            ("strategy_health_version", self.strategy_health_version, 1),
            ("strategy_recovery_ack_count", self.strategy_recovery_ack_count, 0),
            ("strategy_bridge_version", self.strategy_bridge_version, 1),
            ("portfolio_health_version", self.portfolio_health_version, 1),
            ("portfolio_recovery_ack_count", self.portfolio_recovery_ack_count, 0),
            ("portfolio_bridge_version", self.portfolio_bridge_version, 1),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise PaperRuntimeSafetyHealthTruthIntegrityError(
                    f"{label} must be integer >= {minimum}"
                )
        for label, value in (
            ("strategy_health_updated_at", self.strategy_health_updated_at),
            ("strategy_bridge_updated_at", self.strategy_bridge_updated_at),
            ("portfolio_health_updated_at", self.portfolio_health_updated_at),
            ("portfolio_bridge_updated_at", self.portfolio_bridge_updated_at),
            ("observed_at", self.observed_at),
            ("safety_health_valid_until", self.safety_health_valid_until),
        ):
            _require_aware(value, label)
        if self.safety_updated_at is not None:
            _require_aware(self.safety_updated_at, "safety_updated_at")
            if _utc(self.safety_updated_at) > _utc(self.observed_at):
                raise PaperRuntimeSafetyHealthTruthIntegrityError(
                    "Safety timestamp is in W86 process future"
                )
        if self.kill_switch_active is not False or self.kill_switch_reason != "":
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "W86 candidate runtime requires kill switch inactive with empty reason"
            )
        if self.circuit_active is not False or self.circuit_reason != "":
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "W86 candidate runtime requires circuit breaker inactive with empty reason"
            )
        if _utc(self.safety_health_valid_until) < _utc(self.observed_at):
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "Safety/Health truth is already stale at observation time"
            )
        if (
            self.ledger_integrity_verified is not True
            or self.safety_projection_verified is not True
            or self.strategy_health_verified is not True
            or self.portfolio_health_verified is not True
            or self.read_only_core_truth is not True
            or self.sqlite_snapshot_consistent is not True
            or self.concurrent_durable_change_detected is not False
        ):
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "W86 Safety/Health proof requires complete read-only durable verification"
            )
        _require_no_authority(
            paper_runtime_ready=self.paper_runtime_ready,
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
        )
        if self.proof_hash != _hash(_proof_payload(self, include_hash=False)):
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "W86 Safety/Health truth proof hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _proof_payload(self, include_hash=True)


class PaperRuntimeSafetyHealthTruthReader:
    """Read one atomic core-SQLite Safety + Health truth snapshot.

    The reader has no writer/store/network/broker surface. It refuses symlinked
    databases, enforces SQLite ``mode=ro`` + ``query_only``, validates the full
    core ledger hash chain, replays every recognized Safety-version transition,
    verifies authoritative strategy + canonical portfolio Health state including
    recovery ACK chains, and requires exact NORMAL Health bridges. It never
    grants PAPER readiness or execution authority by itself.
    """

    def __init__(self, core_path: str | Path) -> None:
        raw = Path(core_path).expanduser()
        if raw.is_symlink():
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "W86 refuses symlinked authoritative core database"
            )
        resolved = raw.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "W86 requires existing authoritative core database"
            )
        self._core_path = resolved

    def verify_current(
        self,
        *,
        proof_id: str,
        candidate_identity: PaperRuntimeCandidateIdentityProof,
        observed_at: datetime,
        policy: PaperRuntimeSafetyHealthTruthPolicy | None = None,
    ) -> PaperRuntimeSafetyHealthTruthProof:
        _require_id(proof_id, "proof_id")
        _require_aware(observed_at, "observed_at")
        _validate_candidate(candidate_identity)
        effective_policy = policy or PaperRuntimeSafetyHealthTruthPolicy()
        if not isinstance(effective_policy, PaperRuntimeSafetyHealthTruthPolicy):
            raise TypeError("policy must be PaperRuntimeSafetyHealthTruthPolicy")

        instant = _utc(observed_at)
        conn = self._connect_read_only()
        try:
            data_version_before = _data_version(conn)
            conn.execute("BEGIN")
            try:
                _require_schema(conn)
                safety = _read_safety(conn, observed_at=instant)
                ledger = _read_verify_and_project_ledger(conn)
                _require_safety_projection_match(safety, ledger.safety)
                strategy_health = _read_health(
                    conn,
                    entity_kind="STRATEGY",
                    entity_id=candidate_identity.selected_strategy_id,
                    observed_at=instant,
                    max_age_seconds=effective_policy.max_health_state_age_seconds,
                )
                strategy_bridge = _read_bridge(
                    conn,
                    health=strategy_health,
                    observed_at=instant,
                    max_age_seconds=effective_policy.max_health_state_age_seconds,
                )
                portfolio_health = _read_health(
                    conn,
                    entity_kind="PORTFOLIO",
                    entity_id=PORTFOLIO_HEALTH_ENTITY_ID,
                    observed_at=instant,
                    max_age_seconds=effective_policy.max_health_state_age_seconds,
                )
                portfolio_bridge = _read_bridge(
                    conn,
                    health=portfolio_health,
                    observed_at=instant,
                    max_age_seconds=effective_policy.max_health_state_age_seconds,
                )
                snapshot_identity = _identity_tuple(
                    safety=safety,
                    ledger=ledger,
                    strategy_health=strategy_health,
                    strategy_bridge=strategy_bridge,
                    portfolio_health=portfolio_health,
                    portfolio_bridge=portfolio_bridge,
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

            data_version_after_snapshot = _data_version(conn)
            post_identity = _read_current_identity(
                conn,
                strategy_id=candidate_identity.selected_strategy_id,
            )
            data_version_after_postcheck = _data_version(conn)
            if (
                data_version_before != data_version_after_snapshot
                or data_version_after_snapshot != data_version_after_postcheck
                or snapshot_identity != post_identity
            ):
                raise PaperRuntimeSafetyHealthTruthIntegrityError(
                    "durable Safety/Health authority changed during W86 atomic snapshot"
                )
        except PaperRuntimeSafetyHealthTruthError:
            raise
        except (sqlite3.Error, KeyError, TypeError, ValueError, ArithmeticError, InvalidOperation) as exc:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "durable Safety/Health source failed W86 integrity validation"
            ) from exc
        finally:
            conn.close()

        valid_until = min(
            strategy_health.updated_at
            + timedelta(seconds=effective_policy.max_health_state_age_seconds),
            strategy_bridge.updated_at
            + timedelta(seconds=effective_policy.max_health_state_age_seconds),
            portfolio_health.updated_at
            + timedelta(seconds=effective_policy.max_health_state_age_seconds),
            portfolio_bridge.updated_at
            + timedelta(seconds=effective_policy.max_health_state_age_seconds),
        )
        if valid_until < instant:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "Safety/Health truth became stale before proof publication"
            )

        values = {
            "proof_id": proof_id,
            "contract_version": PAPER_RUNTIME_SAFETY_HEALTH_TRUTH_VERSION,
            "candidate_identity_hash": candidate_identity.proof_hash,
            "policy_hash": effective_policy.fingerprint,
            "authority_key": candidate_identity.authority_key,
            "admission_hash": candidate_identity.admission_hash,
            "selected_strategy_id": candidate_identity.selected_strategy_id,
            "portfolio_health_entity_id": PORTFOLIO_HEALTH_ENTITY_ID,
            "sqlite_data_version": data_version_after_postcheck,
            "ledger_event_count": ledger.event_count,
            "ledger_head_hash": ledger.head_hash,
            "safety_version": safety.version,
            "safety_state_fingerprint": safety.fingerprint,
            "kill_switch_active": safety.kill_switch_active,
            "kill_switch_reason": safety.kill_switch_reason,
            "circuit_active": safety.circuit_active,
            "circuit_reason": safety.circuit_reason,
            "safety_updated_at": safety.updated_at,
            "strategy_health_version": strategy_health.version,
            "strategy_health_fingerprint": strategy_health.fingerprint,
            "strategy_health_updated_at": strategy_health.updated_at,
            "strategy_recovery_ack_head": strategy_health.recovery_ack_head,
            "strategy_recovery_ack_count": strategy_health.recovery_ack_count,
            "strategy_bridge_version": strategy_bridge.bridge_version,
            "strategy_bridge_fingerprint": strategy_bridge.fingerprint,
            "strategy_bridge_updated_at": strategy_bridge.updated_at,
            "portfolio_health_version": portfolio_health.version,
            "portfolio_health_fingerprint": portfolio_health.fingerprint,
            "portfolio_health_updated_at": portfolio_health.updated_at,
            "portfolio_recovery_ack_head": portfolio_health.recovery_ack_head,
            "portfolio_recovery_ack_count": portfolio_health.recovery_ack_count,
            "portfolio_bridge_version": portfolio_bridge.bridge_version,
            "portfolio_bridge_fingerprint": portfolio_bridge.fingerprint,
            "portfolio_bridge_updated_at": portfolio_bridge.updated_at,
            "observed_at": instant,
            "safety_health_valid_until": valid_until,
            "ledger_integrity_verified": True,
            "safety_projection_verified": True,
            "strategy_health_verified": True,
            "portfolio_health_verified": True,
            "read_only_core_truth": True,
            "sqlite_snapshot_consistent": True,
            "concurrent_durable_change_detected": False,
            "paper_runtime_ready": False,
            "paper_execution_authorized": False,
            "external_execution_authorized": False,
            "runtime_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        return PaperRuntimeSafetyHealthTruthProof(
            **values,
            proof_hash=_hash(_proof_payload_from_values(values)),
        )

    def _connect_read_only(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            f"file:{self._core_path}?mode=ro",
            uri=True,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute("PRAGMA query_only").fetchone()
        if row is None or int(row[0]) != 1:
            conn.close()
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "W86 could not enforce SQLite query_only mode"
            )
        return conn


def _validate_candidate(candidate: PaperRuntimeCandidateIdentityProof) -> None:
    if not isinstance(candidate, PaperRuntimeCandidateIdentityProof):
        raise TypeError("candidate_identity must be PaperRuntimeCandidateIdentityProof")
    expected = candidate_module._hash(candidate_module._payload(candidate, include_hash=False))
    if candidate.proof_hash != expected:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "W86 candidate identity proof hash mismatch"
        )
    if (
        candidate.product_identity_verified is not True
        or candidate.strategy_runtime_identity_verified is not True
    ):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "W86 candidate identity is not fully verified"
        )
    if (
        candidate.paper_execution_authorized is not False
        or candidate.external_execution_authorized is not False
        or candidate.runtime_execution_authorized is not False
        or candidate.capital_authority != "NONE"
        or candidate.live_trading != "BLOCKED"
    ):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "W86 candidate identity authority boundary is not intact"
        )


def _require_schema(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    tables = {str(row[0]) for row in rows}
    missing = sorted(_REQUIRED_TABLES - tables)
    if missing:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "W86 Safety/Health schema is incomplete: " + ",".join(missing)
        )
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(safety_state)").fetchall()
    }
    required_safety = {
        "singleton_id",
        "kill_switch_active",
        "kill_switch_reason",
        "circuit_active",
        "circuit_reason",
        "version",
        "updated_at",
    }
    if not required_safety.issubset(columns):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "W86 requires R2 Safety schema with independent circuit breaker"
        )


def _read_safety(conn: sqlite3.Connection, *, observed_at: datetime) -> _SafetyObserved:
    row = conn.execute(
        "SELECT kill_switch_active, kill_switch_reason, circuit_active, circuit_reason, version, updated_at "
        "FROM safety_state WHERE singleton_id=1"
    ).fetchone()
    if row is None:
        raise PaperRuntimeSafetyHealthTruthIntegrityError("durable Safety state is missing")
    kill = _strict_bool_int(row["kill_switch_active"], "kill_switch_active")
    circuit = _strict_bool_int(row["circuit_active"], "circuit_active")
    kill_reason = _strict_text(row["kill_switch_reason"], "kill_switch_reason")
    circuit_reason = _strict_text(row["circuit_reason"], "circuit_reason")
    version = _nonnegative_int(row["version"], "Safety version")
    updated_at = _optional_aware_timestamp(row["updated_at"], "Safety updated_at")
    if updated_at is not None and _utc(updated_at) > observed_at:
        raise PaperRuntimeSafetyHealthTruthIntegrityError("Safety state is from the future")
    if version == 0 and updated_at is not None:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "initial Safety version cannot have mutation timestamp"
        )
    if version > 0 and updated_at is None:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "mutated Safety state requires timestamp"
        )
    if kill and not kill_reason:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "engaged kill switch requires durable reason"
        )
    if not kill and kill_reason:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "inactive kill switch must not retain reason"
        )
    if circuit and not circuit_reason:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "engaged circuit breaker requires durable reason"
        )
    if not circuit and circuit_reason:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "inactive circuit breaker must not retain reason"
        )
    if kill:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "durable kill switch is engaged; W86 new-risk candidate is blocked"
        )
    if circuit:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "durable circuit breaker is engaged; W86 new-risk candidate is blocked"
        )
    return _SafetyObserved(
        kill_switch_active=kill,
        kill_switch_reason=kill_reason,
        circuit_active=circuit,
        circuit_reason=circuit_reason,
        version=version,
        updated_at=updated_at,
    )


def _read_verify_and_project_ledger(conn: sqlite3.Connection) -> _LedgerProjection:
    rows = conn.execute(
        "SELECT seq,event_id,event_type,occurred_at,payload_json,prev_hash,event_hash "
        "FROM ledger_events ORDER BY seq"
    ).fetchall()
    running = "GENESIS"
    safety = _SafetyObserved(False, "", False, "", 0, None)
    for row in rows:
        event_id = _strict_text(row["event_id"], "ledger event_id")
        event_type = _strict_text(row["event_type"], "ledger event_type")
        occurred_raw = _strict_text(row["occurred_at"], "ledger occurred_at")
        payload_raw = _strict_text(row["payload_json"], "ledger payload_json")
        previous = _strict_text(row["prev_hash"], "ledger prev_hash")
        actual_hash = _strict_text(row["event_hash"], "ledger event_hash")
        if previous != running:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                f"core ledger previous hash mismatch before {event_id}"
            )
        expected_hash = _ledger_hash(
            prev_hash=running,
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_raw,
            payload_json=payload_raw,
        )
        if actual_hash != expected_hash:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                f"core ledger event hash mismatch: {event_id}"
            )
        try:
            occurred_at = datetime.fromisoformat(occurred_raw)
            payload = json.loads(payload_raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                f"core ledger event is malformed: {event_id}"
            ) from exc
        _require_aware(occurred_at, "ledger occurred_at")
        if not isinstance(payload, dict):
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                f"core ledger payload must be object: {event_id}"
            )
        if event_type == COMMISSIONING_EVENT:
            safety = _project_commissioning(safety, payload, occurred_at)
        elif event_type in _SAFETY_EVENTS:
            safety = _project_safety_event(safety, event_type, payload, occurred_at)
        running = actual_hash
    return _LedgerProjection(event_count=len(rows), head_hash=running, safety=safety)


def _project_commissioning(
    current: _SafetyObserved,
    payload: dict[str, object],
    occurred_at: datetime,
) -> _SafetyObserved:
    reason = payload.get("kill_switch_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "R6 Health commissioning event lacks canonical kill-switch reason"
        )
    if current.version != 0 or current.kill_switch_active or current.circuit_active:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "R6 Health commissioning event is not initial Safety anchor"
        )
    return _SafetyObserved(True, reason, False, "", 1, occurred_at)


def _project_safety_event(
    current: _SafetyObserved,
    event_type: str,
    payload: dict[str, object],
    occurred_at: datetime,
) -> _SafetyObserved:
    raw_version = payload.get("safety_state_version")
    try:
        version = int(raw_version) if isinstance(raw_version, str) else raw_version
    except ValueError as exc:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{event_type} safety_state_version is invalid"
        ) from exc
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{event_type} safety_state_version is invalid"
        )

    kill = current.kill_switch_active
    kill_reason = current.kill_switch_reason
    circuit = current.circuit_active
    circuit_reason = current.circuit_reason
    if event_type == "KILL_SWITCH_ACTIVATED":
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "KILL_SWITCH_ACTIVATED lacks reason"
            )
        kill = True
        kill_reason = reason
    elif event_type == "KILL_SWITCH_RESET":
        kill = False
        kill_reason = ""
    elif event_type == "CIRCUIT_ACTIVATED":
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "CIRCUIT_ACTIVATED lacks reason"
            )
        circuit = True
        circuit_reason = reason
    elif event_type == "CIRCUIT_ACKNOWLEDGED":
        circuit = False
        circuit_reason = ""

    changed = (
        kill != current.kill_switch_active
        or kill_reason != current.kill_switch_reason
        or circuit != current.circuit_active
        or circuit_reason != current.circuit_reason
    )
    if version == current.version:
        if changed or event_type.startswith("HEALTH_BRIDGE_"):
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                f"{event_type} failed to advance Safety version"
            )
        return current
    if version != current.version + 1:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{event_type} Safety version sequence is not contiguous"
        )
    return _SafetyObserved(
        kill_switch_active=kill,
        kill_switch_reason=kill_reason,
        circuit_active=circuit,
        circuit_reason=circuit_reason,
        version=version,
        updated_at=occurred_at,
    )


def _require_safety_projection_match(
    observed: _SafetyObserved,
    projected: _SafetyObserved,
) -> None:
    if observed != projected:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "durable Safety row differs from ledger-projected kill/circuit/version state"
        )


def _read_health(
    conn: sqlite3.Connection,
    *,
    entity_kind: str,
    entity_id: str,
    observed_at: datetime,
    max_age_seconds: int,
) -> _HealthObserved:
    row = conn.execute(
        "SELECT * FROM health_state_v2 WHERE entity_kind=? AND entity_id=?",
        (entity_kind, entity_id),
    ).fetchone()
    if row is None:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"required authoritative Health is missing: {entity_kind}:{entity_id}"
        )
    if row["entity_kind"] != entity_kind or row["entity_id"] != entity_id:
        raise PaperRuntimeSafetyHealthTruthIntegrityError("Health row identity mismatch")
    state = _strict_text(row["state"], "Health state")
    if state != "HEALTHY":
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{entity_kind} authoritative Health is not HEALTHY"
        )
    version = _positive_int(row["version"], "Health version")
    quarantine_count = _nonnegative_int(
        row["distinct_quarantine_count"], "Health quarantine count"
    )
    baseline = _hash_text(row["baseline_fingerprint"], "Health baseline")
    policy = _hash_text(row["policy_fingerprint"], "Health policy")
    assessment_raw = row["last_assessment_fingerprint"]
    if not isinstance(assessment_raw, str) or (
        assessment_raw and not _HASH_RE.fullmatch(assessment_raw)
    ):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "Health last assessment fingerprint is invalid"
        )
    updated_at = _aware_timestamp(row["updated_at"], "Health updated_at")
    _require_fresh(updated_at, observed_at, max_age_seconds, f"{entity_kind} Health")
    recovery_head = _strict_text(row["recovery_ack_head"], "Health recovery ACK head")
    if recovery_head != "GENESIS" and not _HASH_RE.fullmatch(recovery_head):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "Health recovery ACK head is invalid"
        )
    payload = {
        "entity_id": entity_id,
        "entity_kind": entity_kind,
        "state": state,
        "version": version,
        "distinct_quarantine_count": quarantine_count,
        "baseline_fingerprint": baseline,
        "policy_fingerprint": policy,
        "last_assessment_fingerprint": assessment_raw,
        "updated_at": updated_at.isoformat(),
        "recovery_ack_head": recovery_head,
    }
    calculated = _hash(payload)
    stored = _hash_text(row["state_hash"], "Health state hash")
    if stored != calculated:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{entity_kind} authoritative Health state hash mismatch"
        )
    ack_count = _verify_recovery_ack_chain(
        conn,
        entity_kind=entity_kind,
        entity_id=entity_id,
        expected_head=recovery_head,
    )
    return _HealthObserved(
        entity_kind=entity_kind,
        entity_id=entity_id,
        state=state,
        version=version,
        distinct_quarantine_count=quarantine_count,
        baseline_fingerprint=baseline,
        policy_fingerprint=policy,
        last_assessment_fingerprint=assessment_raw,
        updated_at=updated_at,
        recovery_ack_head=recovery_head,
        fingerprint=calculated,
        recovery_ack_count=ack_count,
    )


def _verify_recovery_ack_chain(
    conn: sqlite3.Connection,
    *,
    entity_kind: str,
    entity_id: str,
    expected_head: str,
) -> int:
    rows = conn.execute(
        "SELECT ack_seq,recovery_id,request_fingerprint,confirmed_by,applied_at,previous_ack_hash,ack_hash "
        "FROM health_recovery_acks_v3 WHERE entity_kind=? AND entity_id=? ORDER BY ack_seq",
        (entity_kind, entity_id),
    ).fetchall()
    running = "GENESIS"
    expected_seq = 1
    for row in rows:
        seq = _positive_int(row["ack_seq"], "Health recovery ACK sequence")
        if seq != expected_seq:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "Health recovery ACK sequence gap/reorder detected"
            )
        recovery_id = _strict_text(row["recovery_id"], "recovery_id")
        confirmed_by = _strict_text(row["confirmed_by"], "confirmed_by")
        if not recovery_id.strip() or recovery_id != recovery_id.strip():
            raise PaperRuntimeSafetyHealthTruthIntegrityError("recovery_id is not canonical")
        if not confirmed_by.strip() or confirmed_by != confirmed_by.strip():
            raise PaperRuntimeSafetyHealthTruthIntegrityError("confirmed_by is not canonical")
        request_fp = _hash_text(
            row["request_fingerprint"], "recovery request fingerprint"
        )
        applied_raw = _strict_text(row["applied_at"], "recovery applied_at")
        applied_at = _aware_timestamp(applied_raw, "recovery applied_at")
        previous = _strict_text(row["previous_ack_hash"], "recovery previous hash")
        if previous != running:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "Health recovery ACK previous hash mismatch"
            )
        expected_hash = _hash(
            {
                "entity_kind": entity_kind,
                "entity_id": entity_id,
                "ack_seq": seq,
                "recovery_id": recovery_id,
                "request_fingerprint": request_fp,
                "confirmed_by": confirmed_by,
                "applied_at": applied_at.isoformat(),
                "previous_ack_hash": previous,
            }
        )
        actual = _hash_text(row["ack_hash"], "recovery ACK hash")
        if actual != expected_hash:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                "Health recovery ACK hash mismatch"
            )
        running = actual
        expected_seq += 1
    if running != expected_head:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "Health recovery ACK chain head does not match authoritative Health"
        )
    return len(rows)


def _read_bridge(
    conn: sqlite3.Connection,
    *,
    health: _HealthObserved,
    observed_at: datetime,
    max_age_seconds: int,
) -> _BridgeObserved:
    row = conn.execute(
        "SELECT * FROM health_bridge_state WHERE entity_kind=? AND entity_id=?",
        (health.entity_kind, health.entity_id),
    ).fetchone()
    if row is None:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"required Health bridge is missing: {health.entity_kind}:{health.entity_id}"
        )
    if row["entity_kind"] != health.entity_kind or row["entity_id"] != health.entity_id:
        raise PaperRuntimeSafetyHealthTruthIntegrityError("Health bridge row identity mismatch")
    mode = _strict_text(row["mode"], "Health bridge mode")
    if mode != "NORMAL":
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{health.entity_kind} Health bridge is not NORMAL"
        )
    raw_multiplier = _strict_text(row["risk_multiplier"], "Health bridge multiplier")
    if raw_multiplier != "1":
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{health.entity_kind} Health bridge multiplier is not canonical 1"
        )
    multiplier = Decimal(raw_multiplier)
    health_version = _positive_int(
        row["health_state_version"], "Health bridge health-state version"
    )
    health_fingerprint = _hash_text(
        row["health_state_fingerprint"], "Health bridge health-state fingerprint"
    )
    baseline = _hash_text(row["baseline_fingerprint"], "Health bridge baseline")
    policy = _hash_text(row["policy_fingerprint"], "Health bridge policy")
    bridge_version = _positive_int(row["bridge_version"], "Health bridge version")
    updated_at = _aware_timestamp(row["updated_at"], "Health bridge updated_at")
    _require_fresh(
        updated_at,
        observed_at,
        max_age_seconds,
        f"{health.entity_kind} Health bridge",
    )
    if health_version != health.version:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{health.entity_kind} Health bridge is not synchronized to authoritative version"
        )
    if health_fingerprint != health.fingerprint:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{health.entity_kind} Health bridge fingerprint binding mismatch"
        )
    if baseline != health.baseline_fingerprint or policy != health.policy_fingerprint:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{health.entity_kind} Health bridge baseline/policy binding mismatch"
        )
    payload = {
        "entity_id": health.entity_id,
        "entity_kind": health.entity_kind,
        "mode": mode,
        "risk_multiplier": str(multiplier),
        "health_state_version": health_version,
        "health_state_fingerprint": health_fingerprint,
        "baseline_fingerprint": baseline,
        "policy_fingerprint": policy,
        "bridge_version": bridge_version,
        "updated_at": updated_at.isoformat(),
    }
    calculated = _hash(payload)
    stored = _hash_text(row["state_hash"], "Health bridge state hash")
    if stored != calculated:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{health.entity_kind} Health bridge state hash mismatch"
        )
    return _BridgeObserved(
        entity_kind=health.entity_kind,
        entity_id=health.entity_id,
        mode=mode,
        risk_multiplier=multiplier,
        health_state_version=health_version,
        health_state_fingerprint=health_fingerprint,
        baseline_fingerprint=baseline,
        policy_fingerprint=policy,
        bridge_version=bridge_version,
        updated_at=updated_at,
        fingerprint=calculated,
    )


def _identity_tuple(
    *,
    safety: _SafetyObserved,
    ledger: _LedgerProjection,
    strategy_health: _HealthObserved,
    strategy_bridge: _BridgeObserved,
    portfolio_health: _HealthObserved,
    portfolio_bridge: _BridgeObserved,
) -> tuple[object, ...]:
    return (
        safety.fingerprint,
        ledger.event_count,
        ledger.head_hash,
        strategy_health.fingerprint,
        strategy_health.recovery_ack_head,
        strategy_health.recovery_ack_count,
        strategy_bridge.fingerprint,
        portfolio_health.fingerprint,
        portfolio_health.recovery_ack_head,
        portfolio_health.recovery_ack_count,
        portfolio_bridge.fingerprint,
    )


def _read_current_identity(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
) -> tuple[object, ...]:
    safety = _read_safety_without_gate(conn)
    ledger = _read_verify_and_project_ledger(conn)
    strategy_health = _read_health_identity(conn, "STRATEGY", strategy_id)
    strategy_bridge = _read_bridge_identity(conn, "STRATEGY", strategy_id)
    portfolio_health = _read_health_identity(
        conn, "PORTFOLIO", PORTFOLIO_HEALTH_ENTITY_ID
    )
    portfolio_bridge = _read_bridge_identity(
        conn, "PORTFOLIO", PORTFOLIO_HEALTH_ENTITY_ID
    )
    return (
        safety.fingerprint,
        ledger.event_count,
        ledger.head_hash,
        strategy_health[0],
        strategy_health[1],
        strategy_health[2],
        strategy_bridge,
        portfolio_health[0],
        portfolio_health[1],
        portfolio_health[2],
        portfolio_bridge,
    )


def _read_safety_without_gate(conn: sqlite3.Connection) -> _SafetyObserved:
    row = conn.execute(
        "SELECT kill_switch_active, kill_switch_reason, circuit_active, circuit_reason, version, updated_at "
        "FROM safety_state WHERE singleton_id=1"
    ).fetchone()
    if row is None:
        raise PaperRuntimeSafetyHealthTruthIntegrityError("durable Safety state disappeared")
    return _SafetyObserved(
        kill_switch_active=_strict_bool_int(row["kill_switch_active"], "kill_switch_active"),
        kill_switch_reason=_strict_text(row["kill_switch_reason"], "kill_switch_reason"),
        circuit_active=_strict_bool_int(row["circuit_active"], "circuit_active"),
        circuit_reason=_strict_text(row["circuit_reason"], "circuit_reason"),
        version=_nonnegative_int(row["version"], "Safety version"),
        updated_at=_optional_aware_timestamp(row["updated_at"], "Safety updated_at"),
    )


def _read_health_identity(
    conn: sqlite3.Connection, entity_kind: str, entity_id: str
) -> tuple[str, str, int]:
    row = conn.execute(
        "SELECT state_hash,recovery_ack_head FROM health_state_v2 WHERE entity_kind=? AND entity_id=?",
        (entity_kind, entity_id),
    ).fetchone()
    if row is None:
        raise PaperRuntimeSafetyHealthTruthIntegrityError("Health state disappeared")
    head = _strict_text(row["recovery_ack_head"], "Health recovery ACK head")
    count_row = conn.execute(
        "SELECT COUNT(*) FROM health_recovery_acks_v3 WHERE entity_kind=? AND entity_id=?",
        (entity_kind, entity_id),
    ).fetchone()
    assert count_row is not None
    return _hash_text(row["state_hash"], "Health state hash"), head, int(count_row[0])


def _read_bridge_identity(
    conn: sqlite3.Connection, entity_kind: str, entity_id: str
) -> str:
    row = conn.execute(
        "SELECT state_hash FROM health_bridge_state WHERE entity_kind=? AND entity_id=?",
        (entity_kind, entity_id),
    ).fetchone()
    if row is None:
        raise PaperRuntimeSafetyHealthTruthIntegrityError("Health bridge disappeared")
    return _hash_text(row["state_hash"], "Health bridge state hash")


def _data_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA data_version").fetchone()
    if row is None:
        raise PaperRuntimeSafetyHealthTruthIntegrityError("SQLite data_version unavailable")
    return _nonnegative_int(row[0], "SQLite data_version")


def _ledger_hash(
    *, prev_hash: str, event_id: str, event_type: str, occurred_at: str, payload_json: str
) -> str:
    raw = "\x1f".join(
        (prev_hash, event_id, event_type, occurred_at, payload_json)
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _require_fresh(
    value: datetime,
    observed_at: datetime,
    max_age_seconds: int,
    label: str,
) -> None:
    instant = _utc(value)
    if instant > observed_at:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(f"{label} is from the future")
    if observed_at - instant > timedelta(seconds=max_age_seconds):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(f"{label} is stale")


def _strict_bool_int(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise PaperRuntimeSafetyHealthTruthIntegrityError(f"{label} must be SQLite boolean 0/1")


def _strict_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(f"{label} must be text")
    return value


def _hash_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{label} must be lowercase sha256"
        )
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{label} must be non-negative integer"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{label} must be non-negative integer"
        ) from exc
    if parsed < 0 or (isinstance(value, float) and value != parsed):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{label} must be non-negative integer"
        )
    return parsed


def _positive_int(value: object, label: str) -> int:
    parsed = _nonnegative_int(value, label)
    if parsed <= 0:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{label} must be positive integer"
        )
    return parsed


def _aware_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(f"{label} must be ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{label} must be valid ISO timestamp"
        ) from exc
    _require_aware(parsed, label)
    return parsed


def _optional_aware_timestamp(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    return _aware_timestamp(value, label)


def _proof_payload(
    value: PaperRuntimeSafetyHealthTruthProof, *, include_hash: bool
) -> dict[str, object]:
    names = tuple(
        field
        for field in PaperRuntimeSafetyHealthTruthProof.__dataclass_fields__
        if field != "proof_hash"
    )
    payload = _proof_payload_from_values(
        {name: getattr(value, name) for name in names}
    )
    if include_hash:
        payload["proof_hash"] = value.proof_hash
    return payload


def _proof_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    for key in (
        "safety_updated_at",
        "strategy_health_updated_at",
        "strategy_bridge_updated_at",
        "portfolio_health_updated_at",
        "portfolio_bridge_updated_at",
        "observed_at",
        "safety_health_valid_until",
    ):
        value = payload[key]
        if value is None:
            payload[key] = None
        elif isinstance(value, datetime):
            payload[key] = value.isoformat()
        else:
            raise PaperRuntimeSafetyHealthTruthIntegrityError(
                f"{key} must be datetime or None"
            )
    return payload


def _require_no_authority(
    *,
    paper_runtime_ready: bool,
    paper_execution: bool,
    external: bool,
    runtime: bool,
    capital: str,
    live: str,
) -> None:
    if (
        paper_runtime_ready is not False
        or paper_execution is not False
        or external is not False
        or runtime is not False
        or capital != "NONE"
        or live != "BLOCKED"
    ):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            "W86 Safety/Health truth may not grant readiness, execution, capital or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperRuntimeSafetyHealthTruthIntegrityError(
            f"{label} must be timezone-aware"
        )


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()
