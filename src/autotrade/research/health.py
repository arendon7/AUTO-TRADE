from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from .portfolio_dependence import CalibrationPhase


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ZERO = Decimal("0")
_ONE = Decimal("1")


class HealthGovernanceError(RuntimeError):
    pass


class HealthStateConflict(HealthGovernanceError):
    pass


class HealthRecoveryRejected(HealthGovernanceError):
    pass


class HealthEntityKind(StrEnum):
    STRATEGY = "STRATEGY"
    PORTFOLIO = "PORTFOLIO"


class HealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


_SEVERITY = {
    HealthState.HEALTHY: 0,
    HealthState.DEGRADED: 1,
    HealthState.QUARANTINED: 2,
    HealthState.RETIRED: 3,
}


@dataclass(frozen=True, slots=True)
class HealthReturnObservation:
    occurred_at: datetime
    available_at: datetime
    value: Decimal

    def __post_init__(self) -> None:
        if not _aware(self.occurred_at) or not _aware(self.available_at):
            raise ValueError("health observation timestamps must be timezone-aware")
        if self.available_at != self.occurred_at:
            raise HealthGovernanceError("health returns must be causally available at occurred_at")
        if not _finite(self.value):
            raise ValueError("health return must be finite Decimal")


@dataclass(frozen=True, slots=True)
class HealthBaselineSeries:
    entity_id: str
    entity_kind: HealthEntityKind
    phase: CalibrationPhase
    source_hash: str
    observations: tuple[HealthReturnObservation, ...]

    def __post_init__(self) -> None:
        _identity(self.entity_id, "entity_id")
        if not isinstance(self.entity_kind, HealthEntityKind):
            raise ValueError("entity_kind must be HealthEntityKind")
        if not isinstance(self.phase, CalibrationPhase):
            raise ValueError("baseline phase must be TRAIN or DEVELOPMENT")
        _hash_value(self.source_hash, "source_hash")
        _ordered(self.observations)

    @property
    def fingerprint(self) -> str:
        return _hash(_series_payload(self.entity_id, self.entity_kind, self.source_hash, self.observations, self.phase.value))


@dataclass(frozen=True, slots=True)
class HealthObservationSeries:
    entity_id: str
    entity_kind: HealthEntityKind
    source_hash: str
    observations: tuple[HealthReturnObservation, ...]

    def __post_init__(self) -> None:
        _identity(self.entity_id, "entity_id")
        if not isinstance(self.entity_kind, HealthEntityKind):
            raise ValueError("entity_kind must be HealthEntityKind")
        _hash_value(self.source_hash, "source_hash")
        _ordered(self.observations)

    @property
    def fingerprint(self) -> str:
        return _hash(_series_payload(self.entity_id, self.entity_kind, self.source_hash, self.observations, "OBSERVED"))


@dataclass(frozen=True, slots=True)
class HealthBaseline:
    entity_id: str
    entity_kind: HealthEntityKind
    series_fingerprint: str
    source_hash: str
    sample_count: int
    mean_return: Decimal
    volatility: Decimal
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        _identity(self.entity_id, "entity_id")
        if not isinstance(self.entity_kind, HealthEntityKind):
            raise ValueError("entity_kind must be HealthEntityKind")
        _hash_value(self.series_fingerprint, "series_fingerprint")
        _hash_value(self.source_hash, "source_hash")
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 2:
            raise ValueError("sample_count must be integer >= 2")
        if not _finite(self.mean_return) or self.mean_return <= _ZERO:
            raise HealthGovernanceError("baseline mean return must be finite and > 0")
        if not _finite(self.volatility) or self.volatility <= _ZERO:
            raise HealthGovernanceError("baseline volatility must be finite and > 0")
        if not _aware(self.start_at) or not _aware(self.end_at) or self.end_at < self.start_at:
            raise ValueError("baseline time window is invalid")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "entity_id": self.entity_id,
                "entity_kind": self.entity_kind.value,
                "series_fingerprint": self.series_fingerprint,
                "source_hash": self.source_hash,
                "sample_count": self.sample_count,
                "mean_return": str(self.mean_return),
                "volatility": str(self.volatility),
                "start_at": self.start_at.isoformat(),
                "end_at": self.end_at.isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class HealthPolicy:
    min_observations: int
    degraded_mean_loss_fraction: Decimal
    quarantined_mean_loss_fraction: Decimal
    degraded_volatility_ratio: Decimal
    quarantined_volatility_ratio: Decimal
    retire_after_distinct_quarantines: int
    max_observation_age_seconds: int = 3600

    def __post_init__(self) -> None:
        if isinstance(self.min_observations, bool) or not isinstance(self.min_observations, int) or self.min_observations < 2:
            raise ValueError("min_observations must be integer >= 2")
        if not (
            _ZERO < self.degraded_mean_loss_fraction
            < self.quarantined_mean_loss_fraction
            <= _ONE
        ):
            raise ValueError("mean-loss thresholds must satisfy 0 < degraded < quarantined <= 1")
        if (
            not _finite(self.degraded_volatility_ratio)
            or not _finite(self.quarantined_volatility_ratio)
            or not (_ONE < self.degraded_volatility_ratio < self.quarantined_volatility_ratio)
        ):
            raise ValueError("volatility ratios must satisfy 1 < degraded < quarantined")
        if (
            isinstance(self.retire_after_distinct_quarantines, bool)
            or not isinstance(self.retire_after_distinct_quarantines, int)
            or self.retire_after_distinct_quarantines < 2
        ):
            raise ValueError("retire_after_distinct_quarantines must be integer >= 2")
        if (
            isinstance(self.max_observation_age_seconds, bool)
            or not isinstance(self.max_observation_age_seconds, int)
            or self.max_observation_age_seconds <= 0
        ):
            raise ValueError("max_observation_age_seconds must be integer > 0")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "min_observations": self.min_observations,
                "degraded_mean_loss_fraction": str(self.degraded_mean_loss_fraction),
                "quarantined_mean_loss_fraction": str(self.quarantined_mean_loss_fraction),
                "degraded_volatility_ratio": str(self.degraded_volatility_ratio),
                "quarantined_volatility_ratio": str(self.quarantined_volatility_ratio),
                "retire_after_distinct_quarantines": self.retire_after_distinct_quarantines,
                "max_observation_age_seconds": self.max_observation_age_seconds,
            }
        )


@dataclass(frozen=True, slots=True)
class HealthAssessment:
    entity_id: str
    entity_kind: HealthEntityKind
    baseline_fingerprint: str
    observation_series_fingerprint: str
    policy_fingerprint: str
    sample_count: int
    current_mean_return: Decimal
    current_volatility: Decimal
    mean_loss_fraction: Decimal
    volatility_ratio: Decimal
    proposed_state: HealthState
    evaluated_at: datetime

    def __post_init__(self) -> None:
        _identity(self.entity_id, "entity_id")
        if not isinstance(self.entity_kind, HealthEntityKind):
            raise ValueError("entity_kind must be HealthEntityKind")
        for name, value in (
            ("baseline_fingerprint", self.baseline_fingerprint),
            ("observation_series_fingerprint", self.observation_series_fingerprint),
            ("policy_fingerprint", self.policy_fingerprint),
        ):
            _hash_value(value, name)
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 2:
            raise ValueError("sample_count must be integer >= 2")
        for value in (self.current_mean_return, self.current_volatility, self.mean_loss_fraction, self.volatility_ratio):
            if not _finite(value):
                raise ValueError("health assessment metrics must be finite Decimal")
        if self.current_volatility < _ZERO or self.mean_loss_fraction < _ZERO or self.volatility_ratio < _ZERO:
            raise ValueError("health assessment ratios/volatility cannot be negative")
        if not isinstance(self.proposed_state, HealthState):
            raise ValueError("proposed_state must be HealthState")
        if not _aware(self.evaluated_at):
            raise ValueError("evaluated_at must be timezone-aware")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "entity_id": self.entity_id,
                "entity_kind": self.entity_kind.value,
                "baseline_fingerprint": self.baseline_fingerprint,
                "observation_series_fingerprint": self.observation_series_fingerprint,
                "policy_fingerprint": self.policy_fingerprint,
                "sample_count": self.sample_count,
                "current_mean_return": str(self.current_mean_return),
                "current_volatility": str(self.current_volatility),
                "mean_loss_fraction": str(self.mean_loss_fraction),
                "volatility_ratio": str(self.volatility_ratio),
                "proposed_state": self.proposed_state.value,
                "evaluated_at": self.evaluated_at.isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class HealthControlState:
    entity_id: str
    entity_kind: HealthEntityKind
    state: HealthState
    version: int
    distinct_quarantine_count: int
    baseline_fingerprint: str
    policy_fingerprint: str
    last_assessment_fingerprint: str
    updated_at: datetime
    recovery_ack_head: str = "GENESIS"

    def __post_init__(self) -> None:
        _identity(self.entity_id, "entity_id")
        if not isinstance(self.entity_kind, HealthEntityKind):
            raise ValueError("entity_kind must be HealthEntityKind")
        if not isinstance(self.state, HealthState):
            raise ValueError("state must be HealthState")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version <= 0:
            raise ValueError("health state version must be integer > 0")
        if (
            isinstance(self.distinct_quarantine_count, bool)
            or not isinstance(self.distinct_quarantine_count, int)
            or self.distinct_quarantine_count < 0
        ):
            raise ValueError("distinct_quarantine_count must be integer >= 0")
        _hash_value(self.baseline_fingerprint, "baseline_fingerprint")
        _hash_value(self.policy_fingerprint, "policy_fingerprint")
        if self.last_assessment_fingerprint:
            _hash_value(self.last_assessment_fingerprint, "last_assessment_fingerprint")
        if self.recovery_ack_head != "GENESIS":
            _hash_value(self.recovery_ack_head, "recovery_ack_head")
        if not _aware(self.updated_at):
            raise ValueError("health state updated_at must be timezone-aware")

    @property
    def entity_key(self) -> str:
        return f"{self.entity_kind.value}:{self.entity_id}"

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "entity_id": self.entity_id,
                "entity_kind": self.entity_kind.value,
                "state": self.state.value,
                "version": self.version,
                "distinct_quarantine_count": self.distinct_quarantine_count,
                "baseline_fingerprint": self.baseline_fingerprint,
                "policy_fingerprint": self.policy_fingerprint,
                "last_assessment_fingerprint": self.last_assessment_fingerprint,
                "updated_at": self.updated_at.isoformat(),
                "recovery_ack_head": self.recovery_ack_head,
            }
        )


def build_health_baseline(series: HealthBaselineSeries) -> HealthBaseline:
    values = tuple(item.value for item in series.observations)
    mean, volatility = _mean_volatility(values)
    return HealthBaseline(
        entity_id=series.entity_id,
        entity_kind=series.entity_kind,
        series_fingerprint=series.fingerprint,
        source_hash=series.source_hash,
        sample_count=len(values),
        mean_return=mean,
        volatility=volatility,
        start_at=series.observations[0].occurred_at,
        end_at=series.observations[-1].occurred_at,
    )


def assess_health(
    baseline: HealthBaseline,
    observed: HealthObservationSeries,
    policy: HealthPolicy,
    *,
    now: datetime,
) -> HealthAssessment:
    if baseline.entity_id != observed.entity_id or baseline.entity_kind is not observed.entity_kind:
        raise HealthGovernanceError("health observation entity does not match baseline")
    if not _aware(now):
        raise ValueError("health evaluation time must be timezone-aware")
    if len(observed.observations) < policy.min_observations:
        raise HealthGovernanceError("insufficient health observations")
    if any(item.available_at > now for item in observed.observations):
        raise HealthGovernanceError("health assessment cannot use unavailable observations")
    latest_available = observed.observations[-1].available_at
    if now - latest_available > timedelta(seconds=policy.max_observation_age_seconds):
        raise HealthGovernanceError("health assessment observations are stale")
    values = tuple(item.value for item in observed.observations)
    mean, volatility = _mean_volatility(values)
    mean_loss = max(_ZERO, (baseline.mean_return - mean) / abs(baseline.mean_return))
    volatility_ratio = volatility / baseline.volatility

    if (
        mean_loss >= policy.quarantined_mean_loss_fraction
        or volatility_ratio >= policy.quarantined_volatility_ratio
    ):
        state = HealthState.QUARANTINED
    elif (
        mean_loss >= policy.degraded_mean_loss_fraction
        or volatility_ratio >= policy.degraded_volatility_ratio
    ):
        state = HealthState.DEGRADED
    else:
        state = HealthState.HEALTHY

    return HealthAssessment(
        entity_id=baseline.entity_id,
        entity_kind=baseline.entity_kind,
        baseline_fingerprint=baseline.fingerprint,
        observation_series_fingerprint=observed.fingerprint,
        policy_fingerprint=policy.fingerprint,
        sample_count=len(values),
        current_mean_return=mean,
        current_volatility=volatility,
        mean_loss_fraction=mean_loss,
        volatility_ratio=volatility_ratio,
        proposed_state=state,
        evaluated_at=now,
    )


class SQLiteHealthStateStore:
    """Durable monotone health state bound to immutable baseline + policy.

    Automatic assessments can only maintain or worsen severity. Replay of any
    previously applied assessment is idempotent even when it is non-consecutive.
    Recovery requires explicit acknowledgement and a freshly recomputed HEALTHY
    assessment under the exact baseline/policy bound to the state.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("health state requires filesystem SQLite path")
        Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS health_state_v2 (
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    distinct_quarantine_count INTEGER NOT NULL CHECK(distinct_quarantine_count >= 0),
                    baseline_fingerprint TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    last_assessment_fingerprint TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    recovery_ack_head TEXT NOT NULL DEFAULT 'GENESIS',
                    state_hash TEXT NOT NULL,
                    PRIMARY KEY(entity_kind, entity_id)
                );
                CREATE TABLE IF NOT EXISTS health_assessments_v2 (
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    assessment_fingerprint TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY(entity_kind, entity_id, assessment_fingerprint)
                );
                CREATE TABLE IF NOT EXISTS health_recovery_acks_v2 (
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    recovery_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY(entity_kind, entity_id, recovery_id)
                );
                CREATE TABLE IF NOT EXISTS health_recovery_acks_v3 (
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    ack_seq INTEGER NOT NULL CHECK(ack_seq > 0),
                    recovery_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    previous_ack_hash TEXT NOT NULL,
                    ack_hash TEXT NOT NULL,
                    PRIMARY KEY(entity_kind, entity_id, recovery_id),
                    UNIQUE(entity_kind, entity_id, ack_seq)
                );
                CREATE TABLE IF NOT EXISTS health_transitions_v2 (
                    transition_fingerprint TEXT PRIMARY KEY,
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    baseline_fingerprint TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    assessment_fingerprint TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                """
            )
            state_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(health_state_v2)").fetchall()
            }
            if "recovery_ack_head" not in state_columns:
                state_count = int(conn.execute("SELECT COUNT(*) FROM health_state_v2").fetchone()[0])
                if state_count:
                    raise HealthStateConflict(
                        "pre-ACK-chain health state requires explicit migration/rebaseline"
                    )
                conn.execute(
                    "ALTER TABLE health_state_v2 ADD COLUMN recovery_ack_head TEXT NOT NULL DEFAULT 'GENESIS'"
                )

            legacy_ack_count = int(
                conn.execute("SELECT COUNT(*) FROM health_recovery_acks_v2").fetchone()[0]
            )
            v3_ack_count = int(
                conn.execute("SELECT COUNT(*) FROM health_recovery_acks_v3").fetchone()[0]
            )
            if legacy_ack_count and not v3_ack_count:
                raise HealthStateConflict(
                    "pre-chain recovery acknowledgements require explicit migration/rebaseline"
                )

            legacy = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='health_state'"
            ).fetchone()
            if legacy is not None:
                legacy_count = int(conn.execute("SELECT COUNT(*) FROM health_state").fetchone()[0])
                v2_count = int(conn.execute("SELECT COUNT(*) FROM health_state_v2").fetchone()[0])
                if legacy_count and not v2_count:
                    raise HealthStateConflict(
                        "legacy health state lacks entity-kind/baseline/policy binding; explicit rebaseline required"
                    )
            conn.commit()
        finally:
            conn.close()

    def get(
        self,
        entity_id: str,
        entity_kind: HealthEntityKind,
    ) -> HealthControlState | None:
        _identity(entity_id, "entity_id")
        if not isinstance(entity_kind, HealthEntityKind):
            raise ValueError("entity_kind must be HealthEntityKind")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM health_state_v2 WHERE entity_kind=? AND entity_id=?",
                (entity_kind.value, entity_id),
            ).fetchone()
            if row is None:
                return None
            state = _state_from_row(row)
            self._verify_recovery_ack_chain(conn, state)
            return state
        finally:
            conn.close()

    def apply_assessment(
        self,
        assessment: HealthAssessment,
        policy: HealthPolicy,
        *,
        now: datetime,
    ) -> HealthControlState:
        if not isinstance(assessment, HealthAssessment):
            raise TypeError("assessment must be HealthAssessment")
        if not isinstance(policy, HealthPolicy):
            raise TypeError("policy must be HealthPolicy")
        if not _aware(now):
            raise ValueError("health state update time must be timezone-aware")
        if assessment.evaluated_at > now:
            raise HealthStateConflict("assessment cannot be from the future")
        if assessment.policy_fingerprint != policy.fingerprint:
            raise HealthStateConflict("assessment policy fingerprint mismatch")

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM health_state_v2 WHERE entity_kind=? AND entity_id=?",
                (assessment.entity_kind.value, assessment.entity_id),
            ).fetchone()
            if row is None:
                current = None
            else:
                current = _state_from_row(row)
                self._verify_recovery_ack_chain(conn, current)
                self._assert_binding(
                    current,
                    baseline_fingerprint=assessment.baseline_fingerprint,
                    policy_fingerprint=assessment.policy_fingerprint,
                )

            seen = conn.execute(
                """
                SELECT 1 FROM health_assessments_v2
                WHERE entity_kind=? AND entity_id=? AND assessment_fingerprint=?
                """,
                (
                    assessment.entity_kind.value,
                    assessment.entity_id,
                    assessment.fingerprint,
                ),
            ).fetchone()
            if seen is not None:
                if current is None:
                    conn.rollback()
                    raise HealthStateConflict("assessment replay exists without health state")
                conn.commit()
                return current

            if current is None:
                current_state = HealthState.HEALTHY
                quarantine_count = (
                    1 if assessment.proposed_state is HealthState.QUARANTINED else 0
                )
                target = assessment.proposed_state
                if quarantine_count >= policy.retire_after_distinct_quarantines:
                    target = HealthState.RETIRED
                version = 1
            else:
                current_state = current.state
                quarantine_count = current.distinct_quarantine_count
                target = current.state
                if assessment.proposed_state is HealthState.QUARANTINED:
                    quarantine_count += 1
                    if _SEVERITY[target] < _SEVERITY[HealthState.QUARANTINED]:
                        target = HealthState.QUARANTINED
                    if quarantine_count >= policy.retire_after_distinct_quarantines:
                        target = HealthState.RETIRED
                elif _SEVERITY[assessment.proposed_state] > _SEVERITY[target]:
                    target = assessment.proposed_state
                version = current.version + 1

            updated = HealthControlState(
                entity_id=assessment.entity_id,
                entity_kind=assessment.entity_kind,
                state=target,
                version=version,
                distinct_quarantine_count=quarantine_count,
                baseline_fingerprint=assessment.baseline_fingerprint,
                policy_fingerprint=assessment.policy_fingerprint,
                last_assessment_fingerprint=assessment.fingerprint,
                updated_at=now,
                recovery_ack_head=(current.recovery_ack_head if current is not None else "GENESIS"),
            )
            self._upsert_state(conn, updated)
            conn.execute(
                """
                INSERT INTO health_assessments_v2(
                    entity_kind, entity_id, assessment_fingerprint, applied_at
                ) VALUES(?,?,?,?)
                """,
                (
                    assessment.entity_kind.value,
                    assessment.entity_id,
                    assessment.fingerprint,
                    now.isoformat(),
                ),
            )
            self._append_transition(
                conn,
                entity_kind=assessment.entity_kind,
                entity_id=assessment.entity_id,
                from_state=current_state,
                to_state=updated.state,
                baseline_fingerprint=assessment.baseline_fingerprint,
                policy_fingerprint=assessment.policy_fingerprint,
                assessment_fingerprint=assessment.fingerprint,
                action="AUTOMATIC_ASSESSMENT",
                confirmed_by="",
                now=now,
            )
            conn.commit()
            return updated
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def acknowledge_recovery(
        self,
        baseline: HealthBaseline,
        observed: HealthObservationSeries,
        policy: HealthPolicy,
        *,
        recovery_id: str,
        confirmed_by: str,
        now: datetime,
    ) -> HealthControlState:
        _identity(recovery_id, "recovery_id")
        _identity(confirmed_by, "confirmed_by")
        if not _aware(now):
            raise ValueError("recovery time must be timezone-aware")
        if baseline.entity_id != observed.entity_id or baseline.entity_kind is not observed.entity_kind:
            raise HealthRecoveryRejected("recovery observation entity does not match baseline")
        request_fingerprint = _hash(
            {
                "recovery_id": recovery_id,
                "entity_id": baseline.entity_id,
                "entity_kind": baseline.entity_kind.value,
                "baseline_fingerprint": baseline.fingerprint,
                "observation_series_fingerprint": observed.fingerprint,
                "policy_fingerprint": policy.fingerprint,
                "confirmed_by": confirmed_by,
            }
        )

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM health_state_v2 WHERE entity_kind=? AND entity_id=?",
                (baseline.entity_kind.value, baseline.entity_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise HealthRecoveryRejected("no health state exists for entity")
            current = _state_from_row(row)
            self._verify_recovery_ack_chain(conn, current)
            self._assert_binding(
                current,
                baseline_fingerprint=baseline.fingerprint,
                policy_fingerprint=policy.fingerprint,
            )

            ack = conn.execute(
                """
                SELECT request_fingerprint FROM health_recovery_acks_v3
                WHERE entity_kind=? AND entity_id=? AND recovery_id=?
                """,
                (baseline.entity_kind.value, baseline.entity_id, recovery_id),
            ).fetchone()
            if ack is not None:
                if ack["request_fingerprint"] != request_fingerprint:
                    raise HealthStateConflict("recovery_id reused with conflicting request")
                conn.commit()
                return current

            assessment = assess_health(baseline, observed, policy, now=now)
            if assessment.proposed_state is not HealthState.HEALTHY:
                raise HealthRecoveryRejected("recovery requires fresh HEALTHY evidence")
            if current.state is HealthState.RETIRED:
                raise HealthRecoveryRejected("RETIRED state cannot be recovered automatically")

            if current.state is HealthState.HEALTHY:
                ack_seq, ack_hash = self._append_recovery_ack(
                    conn,
                    current=current,
                    recovery_id=recovery_id,
                    request_fingerprint=request_fingerprint,
                    confirmed_by=confirmed_by,
                    now=now,
                )
                updated = HealthControlState(
                    entity_id=current.entity_id,
                    entity_kind=current.entity_kind,
                    state=current.state,
                    version=current.version + 1,
                    distinct_quarantine_count=current.distinct_quarantine_count,
                    baseline_fingerprint=current.baseline_fingerprint,
                    policy_fingerprint=current.policy_fingerprint,
                    last_assessment_fingerprint=current.last_assessment_fingerprint,
                    updated_at=now,
                    recovery_ack_head=ack_hash,
                )
                self._upsert_state(conn, updated)
                self._verify_recovery_ack_chain(conn, updated)
                conn.commit()
                return updated

            target = (
                HealthState.HEALTHY
                if current.state is HealthState.DEGRADED
                else HealthState.DEGRADED
            )
            ack_seq, ack_hash = self._append_recovery_ack(
                conn,
                current=current,
                recovery_id=recovery_id,
                request_fingerprint=request_fingerprint,
                confirmed_by=confirmed_by,
                now=now,
            )
            updated = HealthControlState(
                entity_id=current.entity_id,
                entity_kind=current.entity_kind,
                state=target,
                version=current.version + 1,
                distinct_quarantine_count=current.distinct_quarantine_count,
                baseline_fingerprint=current.baseline_fingerprint,
                policy_fingerprint=current.policy_fingerprint,
                last_assessment_fingerprint=assessment.fingerprint,
                updated_at=now,
                recovery_ack_head=ack_hash,
            )
            self._upsert_state(conn, updated)
            self._verify_recovery_ack_chain(conn, updated)
            self._append_transition(
                conn,
                entity_kind=current.entity_kind,
                entity_id=current.entity_id,
                from_state=current.state,
                to_state=target,
                baseline_fingerprint=current.baseline_fingerprint,
                policy_fingerprint=current.policy_fingerprint,
                assessment_fingerprint=assessment.fingerprint,
                action="ACKNOWLEDGED_RECOVERY",
                confirmed_by=confirmed_by,
                now=now,
            )
            conn.commit()
            return updated
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _append_recovery_ack(
        self,
        conn: sqlite3.Connection,
        *,
        current: HealthControlState,
        recovery_id: str,
        request_fingerprint: str,
        confirmed_by: str,
        now: datetime,
    ) -> tuple[int, str]:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(ack_seq), 0) AS max_seq
            FROM health_recovery_acks_v3
            WHERE entity_kind=? AND entity_id=?
            """,
            (current.entity_kind.value, current.entity_id),
        ).fetchone()
        ack_seq = int(row["max_seq"]) + 1
        previous_ack_hash = current.recovery_ack_head
        ack_hash = _hash(
            {
                "entity_kind": current.entity_kind.value,
                "entity_id": current.entity_id,
                "ack_seq": ack_seq,
                "recovery_id": recovery_id,
                "request_fingerprint": request_fingerprint,
                "confirmed_by": confirmed_by,
                "applied_at": now.isoformat(),
                "previous_ack_hash": previous_ack_hash,
            }
        )
        conn.execute(
            """
            INSERT INTO health_recovery_acks_v3(
                entity_kind,entity_id,ack_seq,recovery_id,request_fingerprint,
                confirmed_by,applied_at,previous_ack_hash,ack_hash
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                current.entity_kind.value,
                current.entity_id,
                ack_seq,
                recovery_id,
                request_fingerprint,
                confirmed_by,
                now.isoformat(),
                previous_ack_hash,
                ack_hash,
            ),
        )
        return ack_seq, ack_hash

    def _verify_recovery_ack_chain(
        self,
        conn: sqlite3.Connection,
        state: HealthControlState,
    ) -> None:
        rows = conn.execute(
            """
            SELECT ack_seq,recovery_id,request_fingerprint,confirmed_by,
                   applied_at,previous_ack_hash,ack_hash
            FROM health_recovery_acks_v3
            WHERE entity_kind=? AND entity_id=?
            ORDER BY ack_seq ASC
            """,
            (state.entity_kind.value, state.entity_id),
        ).fetchall()
        running = "GENESIS"
        expected_seq = 1
        for row in rows:
            try:
                ack_seq = int(row["ack_seq"])
                recovery_id = str(row["recovery_id"])
                request_fingerprint = str(row["request_fingerprint"])
                confirmed_by = str(row["confirmed_by"])
                applied_at = str(row["applied_at"])
                previous_ack_hash = str(row["previous_ack_hash"])
                ack_hash = str(row["ack_hash"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HealthStateConflict("recovery ACK chain row is malformed") from exc
            if ack_seq != expected_seq:
                raise HealthStateConflict("recovery ACK chain sequence gap/reorder detected")
            _identity(recovery_id, "recovery_id")
            _identity(confirmed_by, "confirmed_by")
            _hash_value(request_fingerprint, "request_fingerprint")
            if previous_ack_hash != running:
                raise HealthStateConflict("recovery ACK chain previous hash mismatch")
            expected_hash = _hash(
                {
                    "entity_kind": state.entity_kind.value,
                    "entity_id": state.entity_id,
                    "ack_seq": ack_seq,
                    "recovery_id": recovery_id,
                    "request_fingerprint": request_fingerprint,
                    "confirmed_by": confirmed_by,
                    "applied_at": applied_at,
                    "previous_ack_hash": previous_ack_hash,
                }
            )
            if ack_hash != expected_hash:
                raise HealthStateConflict("recovery ACK chain hash mismatch")
            running = ack_hash
            expected_seq += 1
        if running != state.recovery_ack_head:
            raise HealthStateConflict("recovery ACK chain head does not match Health state")

    def _assert_binding(
        self,
        current: HealthControlState,
        *,
        baseline_fingerprint: str,
        policy_fingerprint: str,
    ) -> None:
        if current.baseline_fingerprint != baseline_fingerprint:
            raise HealthStateConflict("health baseline fingerprint mismatch; explicit rebaseline required")
        if current.policy_fingerprint != policy_fingerprint:
            raise HealthStateConflict("health policy fingerprint mismatch; explicit policy transition required")

    def _upsert_state(self, conn: sqlite3.Connection, state: HealthControlState) -> None:
        conn.execute(
            """
            INSERT INTO health_state_v2(
                entity_kind, entity_id, state, version, distinct_quarantine_count,
                baseline_fingerprint, policy_fingerprint, last_assessment_fingerprint,
                updated_at, recovery_ack_head, state_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(entity_kind,entity_id) DO UPDATE SET
                state=excluded.state,
                version=excluded.version,
                distinct_quarantine_count=excluded.distinct_quarantine_count,
                baseline_fingerprint=excluded.baseline_fingerprint,
                policy_fingerprint=excluded.policy_fingerprint,
                last_assessment_fingerprint=excluded.last_assessment_fingerprint,
                updated_at=excluded.updated_at,
                recovery_ack_head=excluded.recovery_ack_head,
                state_hash=excluded.state_hash
            """,
            (
                state.entity_kind.value,
                state.entity_id,
                state.state.value,
                state.version,
                state.distinct_quarantine_count,
                state.baseline_fingerprint,
                state.policy_fingerprint,
                state.last_assessment_fingerprint,
                state.updated_at.isoformat(),
                state.recovery_ack_head,
                state.fingerprint,
            ),
        )

    def _append_transition(
        self,
        conn: sqlite3.Connection,
        *,
        entity_kind: HealthEntityKind,
        entity_id: str,
        from_state: HealthState,
        to_state: HealthState,
        baseline_fingerprint: str,
        policy_fingerprint: str,
        assessment_fingerprint: str,
        action: str,
        confirmed_by: str,
        now: datetime,
    ) -> None:
        payload = {
            "entity_kind": entity_kind.value,
            "entity_id": entity_id,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "baseline_fingerprint": baseline_fingerprint,
            "policy_fingerprint": policy_fingerprint,
            "assessment_fingerprint": assessment_fingerprint,
            "action": action,
            "confirmed_by": confirmed_by,
            "occurred_at": now.isoformat(),
        }
        fingerprint = _hash(payload)
        conn.execute(
            """
            INSERT OR IGNORE INTO health_transitions_v2(
                transition_fingerprint, entity_kind, entity_id, from_state, to_state,
                baseline_fingerprint, policy_fingerprint, assessment_fingerprint,
                action, confirmed_by, occurred_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                fingerprint,
                entity_kind.value,
                entity_id,
                from_state.value,
                to_state.value,
                baseline_fingerprint,
                policy_fingerprint,
                assessment_fingerprint,
                action,
                confirmed_by,
                now.isoformat(),
            ),
        )


def _state_from_row(row) -> HealthControlState:
    try:
        state = HealthControlState(
            entity_id=row["entity_id"],
            entity_kind=HealthEntityKind(row["entity_kind"]),
            state=HealthState(row["state"]),
            version=int(row["version"]),
            distinct_quarantine_count=int(row["distinct_quarantine_count"]),
            baseline_fingerprint=row["baseline_fingerprint"],
            policy_fingerprint=row["policy_fingerprint"],
            last_assessment_fingerprint=row["last_assessment_fingerprint"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
            recovery_ack_head=row["recovery_ack_head"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HealthStateConflict("stored health state is malformed") from exc
    stored_hash = row["state_hash"]
    if not isinstance(stored_hash, str) or not _SHA256_RE.fullmatch(stored_hash):
        raise HealthStateConflict("stored health state hash is invalid")
    if state.fingerprint != stored_hash:
        raise HealthStateConflict("stored health state hash mismatch")
    return state


def _mean_volatility(values: tuple[Decimal, ...]) -> tuple[Decimal, Decimal]:
    if len(values) < 2:
        raise HealthGovernanceError("at least two health observations are required")
    with localcontext() as context:
        context.prec = 50
        count = Decimal(len(values))
        mean = sum(values, _ZERO) / count
        variance = sum(((value - mean) ** 2 for value in values), _ZERO) / count
        return +mean, +variance.sqrt()


def _ordered(observations: tuple[HealthReturnObservation, ...]) -> None:
    if len(observations) < 2:
        raise ValueError("health series requires at least two observations")
    previous: datetime | None = None
    for item in observations:
        if not isinstance(item, HealthReturnObservation):
            raise ValueError("health series must contain HealthReturnObservation")
        if previous is not None and item.occurred_at <= previous:
            raise ValueError("health observations must be strictly increasing and unique")
        previous = item.occurred_at


def _series_payload(entity_id, entity_kind, source_hash, observations, phase):
    return {
        "entity_id": entity_id,
        "entity_kind": entity_kind.value,
        "source_hash": source_hash,
        "phase": phase,
        "observations": [
            [item.occurred_at.isoformat(), item.available_at.isoformat(), str(item.value)]
            for item in observations
        ],
    }


def _identity(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")


def _hash_value(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase SHA-256 hex")


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _finite(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def _hash(payload: object) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
