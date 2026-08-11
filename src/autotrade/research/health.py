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
    state: HealthState
    version: int
    distinct_quarantine_count: int
    last_assessment_fingerprint: str
    updated_at: datetime


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
    """Durable monotone health state with append-only transition evidence."""

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
                CREATE TABLE IF NOT EXISTS health_state (
                    entity_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    distinct_quarantine_count INTEGER NOT NULL CHECK(distinct_quarantine_count >= 0),
                    last_assessment_fingerprint TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS health_transitions (
                    transition_fingerprint TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    assessment_fingerprint TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    def get(self, entity_id: str) -> HealthControlState | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM health_state WHERE entity_id=?", (entity_id,)).fetchone()
        finally:
            conn.close()
        return None if row is None else _state_from_row(row)

    def apply_assessment(
        self,
        assessment: HealthAssessment,
        policy: HealthPolicy,
        *,
        now: datetime,
    ) -> HealthControlState:
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
                "SELECT * FROM health_state WHERE entity_id=?", (assessment.entity_id,)
            ).fetchone()
            if row is None:
                quarantine_count = (
                    1 if assessment.proposed_state is HealthState.QUARANTINED else 0
                )
                target = assessment.proposed_state
                current_state = HealthState.HEALTHY
                updated = HealthControlState(
                    entity_id=assessment.entity_id,
                    state=target,
                    version=1,
                    distinct_quarantine_count=quarantine_count,
                    last_assessment_fingerprint=assessment.fingerprint,
                    updated_at=now,
                )
            else:
                current = _state_from_row(row)
                current_state = current.state
                if current.last_assessment_fingerprint == assessment.fingerprint:
                    conn.commit()
                    return current

                target = current.state
                quarantine_count = current.distinct_quarantine_count
                if assessment.proposed_state is HealthState.QUARANTINED:
                    quarantine_count += 1
                    target = max(
                        (target, HealthState.QUARANTINED),
                        key=lambda state: _SEVERITY[state],
                    )
                    if quarantine_count >= policy.retire_after_distinct_quarantines:
                        target = HealthState.RETIRED
                elif _SEVERITY[assessment.proposed_state] > _SEVERITY[target]:
                    target = assessment.proposed_state

                # Every distinct assessment changes durable evidence state even
                # if the conservative health severity remains unchanged.
                updated = HealthControlState(
                    entity_id=current.entity_id,
                    state=target,
                    version=current.version + 1,
                    distinct_quarantine_count=quarantine_count,
                    last_assessment_fingerprint=assessment.fingerprint,
                    updated_at=now,
                )

            self._upsert_state(conn, updated)
            self._append_transition(
                conn,
                entity_id=updated.entity_id,
                from_state=current_state,
                to_state=updated.state,
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
        confirmed_by: str,
        now: datetime,
    ) -> HealthControlState:
        _identity(confirmed_by, "confirmed_by")
        if not _aware(now):
            raise ValueError("recovery time must be timezone-aware")
        assessment = assess_health(baseline, observed, policy, now=now)
        if assessment.proposed_state is not HealthState.HEALTHY:
            raise HealthRecoveryRejected("recovery requires fresh HEALTHY evidence")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM health_state WHERE entity_id=?", (assessment.entity_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise HealthRecoveryRejected("no health state exists for entity")
            current = _state_from_row(row)
            if current.state is HealthState.RETIRED:
                conn.rollback()
                raise HealthRecoveryRejected("RETIRED state cannot be recovered automatically")
            if current.state is HealthState.HEALTHY:
                conn.commit()
                return current
            target = (
                HealthState.HEALTHY
                if current.state is HealthState.DEGRADED
                else HealthState.DEGRADED
            )
            updated = HealthControlState(
                entity_id=current.entity_id,
                state=target,
                version=current.version + 1,
                distinct_quarantine_count=current.distinct_quarantine_count,
                last_assessment_fingerprint=assessment.fingerprint,
                updated_at=now,
            )
            self._upsert_state(conn, updated)
            self._append_transition(
                conn,
                entity_id=current.entity_id,
                from_state=current.state,
                to_state=target,
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

    def _upsert_state(self, conn: sqlite3.Connection, state: HealthControlState) -> None:
        conn.execute(
            """
            INSERT INTO health_state(entity_id,state,version,distinct_quarantine_count,last_assessment_fingerprint,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(entity_id) DO UPDATE SET
                state=excluded.state,
                version=excluded.version,
                distinct_quarantine_count=excluded.distinct_quarantine_count,
                last_assessment_fingerprint=excluded.last_assessment_fingerprint,
                updated_at=excluded.updated_at
            """,
            (
                state.entity_id,
                state.state.value,
                state.version,
                state.distinct_quarantine_count,
                state.last_assessment_fingerprint,
                state.updated_at.isoformat(),
            ),
        )

    def _append_transition(
        self,
        conn: sqlite3.Connection,
        *,
        entity_id: str,
        from_state: HealthState,
        to_state: HealthState,
        assessment_fingerprint: str,
        action: str,
        confirmed_by: str,
        now: datetime,
    ) -> None:
        payload = {
            "entity_id": entity_id,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "assessment_fingerprint": assessment_fingerprint,
            "action": action,
            "confirmed_by": confirmed_by,
            "occurred_at": now.isoformat(),
        }
        fingerprint = _hash(payload)
        conn.execute(
            "INSERT OR IGNORE INTO health_transitions VALUES(?,?,?,?,?,?,?,?)",
            (
                fingerprint,
                entity_id,
                from_state.value,
                to_state.value,
                assessment_fingerprint,
                action,
                confirmed_by,
                now.isoformat(),
            ),
        )


def _state_from_row(row) -> HealthControlState:
    return HealthControlState(
        entity_id=row["entity_id"],
        state=HealthState(row["state"]),
        version=int(row["version"]),
        distinct_quarantine_count=int(row["distinct_quarantine_count"]),
        last_assessment_fingerprint=row["last_assessment_fingerprint"],
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


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
