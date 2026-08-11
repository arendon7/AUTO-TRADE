from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
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


class RegimeGovernanceError(RuntimeError):
    pass


class RegimeCalibrationError(RegimeGovernanceError):
    pass


class RegimeModelConflict(RegimeGovernanceError):
    pass


class RegimeEvaluationPhase(StrEnum):
    TRAIN = "TRAIN"
    DEVELOPMENT = "DEVELOPMENT"
    FINAL_HOLDOUT = "FINAL_HOLDOUT"


class RegimeState(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RegimeFeatureObservation:
    occurred_at: datetime
    available_at: datetime
    value: Decimal

    def __post_init__(self) -> None:
        if not _aware(self.occurred_at) or not _aware(self.available_at):
            raise ValueError("regime feature timestamps must be timezone-aware")
        if self.available_at < self.occurred_at:
            raise ValueError("available_at cannot precede occurred_at")
        if not _finite(self.value):
            raise ValueError("regime feature value must be finite Decimal")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "occurred_at": self.occurred_at.isoformat(),
                "available_at": self.available_at.isoformat(),
                "value": str(self.value),
            }
        )


@dataclass(frozen=True, slots=True)
class RegimeCalibrationSeries:
    feature_name: str
    phase: CalibrationPhase
    source_hash: str
    observations: tuple[RegimeFeatureObservation, ...]

    def __post_init__(self) -> None:
        _identity(self.feature_name, "feature_name")
        if not isinstance(self.phase, CalibrationPhase):
            raise ValueError("calibration phase must be TRAIN or DEVELOPMENT")
        _source_hash(self.source_hash)
        _validate_series(self.observations, require_causal=True)

    @property
    def fingerprint(self) -> str:
        return _hash(_series_payload(self.feature_name, self.phase.value, self.source_hash, self.observations))


@dataclass(frozen=True, slots=True)
class RegimeEvaluationSeries:
    feature_name: str
    phase: RegimeEvaluationPhase
    source_hash: str
    observations: tuple[RegimeFeatureObservation, ...]

    def __post_init__(self) -> None:
        _identity(self.feature_name, "feature_name")
        if not isinstance(self.phase, RegimeEvaluationPhase):
            raise ValueError("evaluation phase must be RegimeEvaluationPhase")
        _source_hash(self.source_hash)
        _validate_series(self.observations, require_causal=True)

    @property
    def fingerprint(self) -> str:
        return _hash(_series_payload(self.feature_name, self.phase.value, self.source_hash, self.observations))


@dataclass(frozen=True, slots=True)
class RegimeCalibrationSpec:
    low_quantile: Decimal
    high_quantile: Decimal
    min_observations: int

    def __post_init__(self) -> None:
        if not _finite(self.low_quantile) or not _finite(self.high_quantile):
            raise ValueError("regime quantiles must be finite Decimal")
        if not (_ZERO < self.low_quantile < self.high_quantile < _ONE):
            raise ValueError("regime quantiles must satisfy 0 < low < high < 1")
        if (
            isinstance(self.min_observations, bool)
            or not isinstance(self.min_observations, int)
            or self.min_observations < 3
        ):
            raise ValueError("min_observations must be integer >= 3")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "low_quantile": str(self.low_quantile),
                "high_quantile": str(self.high_quantile),
                "min_observations": self.min_observations,
            }
        )


@dataclass(frozen=True, slots=True)
class RegimeModel:
    model_id: str
    version: int
    feature_name: str
    calibration_phase: CalibrationPhase
    calibration_series_fingerprint: str
    calibration_source_hash: str
    spec_fingerprint: str
    low_threshold: Decimal
    high_threshold: Decimal
    calibrated_at: datetime

    def __post_init__(self) -> None:
        _identity(self.model_id, "model_id")
        _identity(self.feature_name, "feature_name")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version <= 0:
            raise ValueError("model version must be integer > 0")
        if not isinstance(self.calibration_phase, CalibrationPhase):
            raise ValueError("calibration_phase must be TRAIN or DEVELOPMENT")
        for name, value in (
            ("calibration_series_fingerprint", self.calibration_series_fingerprint),
            ("calibration_source_hash", self.calibration_source_hash),
            ("spec_fingerprint", self.spec_fingerprint),
        ):
            if not _SHA256_RE.fullmatch(value):
                raise ValueError(f"{name} must be SHA-256 hex")
        if not _finite(self.low_threshold) or not _finite(self.high_threshold):
            raise ValueError("regime thresholds must be finite Decimal")
        if self.low_threshold >= self.high_threshold:
            raise ValueError("low_threshold must be below high_threshold")
        if not _aware(self.calibrated_at):
            raise ValueError("calibrated_at must be timezone-aware")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_payload(include_fingerprint=False))

    def to_payload(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "model_id": self.model_id,
            "version": self.version,
            "feature_name": self.feature_name,
            "calibration_phase": self.calibration_phase.value,
            "calibration_series_fingerprint": self.calibration_series_fingerprint,
            "calibration_source_hash": self.calibration_source_hash,
            "spec_fingerprint": self.spec_fingerprint,
            "low_threshold": str(self.low_threshold),
            "high_threshold": str(self.high_threshold),
            "calibrated_at": self.calibrated_at.isoformat(),
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "RegimeModel":
        expected = {
            "model_id",
            "version",
            "feature_name",
            "calibration_phase",
            "calibration_series_fingerprint",
            "calibration_source_hash",
            "spec_fingerprint",
            "low_threshold",
            "high_threshold",
            "calibrated_at",
            "fingerprint",
        }
        if not isinstance(payload, dict):
            raise ValueError("regime model payload must be object")
        missing = expected - {"fingerprint"} - set(payload)
        unknown = set(payload) - expected
        if missing or unknown:
            raise ValueError(
                f"invalid regime model payload fields; missing={sorted(missing)} unknown={sorted(unknown)}"
            )
        supplied = payload.get("fingerprint")
        if supplied is not None:
            raw = dict(payload)
            raw.pop("fingerprint", None)
            if supplied != _hash(raw):
                raise RegimeModelConflict("regime model fingerprint mismatch")
        model = cls(
            model_id=_string(payload["model_id"]),
            version=_integer(payload["version"]),
            feature_name=_string(payload["feature_name"]),
            calibration_phase=CalibrationPhase(_string(payload["calibration_phase"])),
            calibration_series_fingerprint=_string(payload["calibration_series_fingerprint"]),
            calibration_source_hash=_string(payload["calibration_source_hash"]),
            spec_fingerprint=_string(payload["spec_fingerprint"]),
            low_threshold=_decimal(payload["low_threshold"]),
            high_threshold=_decimal(payload["high_threshold"]),
            calibrated_at=_timestamp(payload["calibrated_at"]),
        )
        if supplied is not None and supplied != model.fingerprint:
            raise RegimeModelConflict("regime model fingerprint mismatch")
        return model


@dataclass(frozen=True, slots=True)
class RegimeClassification:
    state: RegimeState
    reason: str
    model_fingerprint: str
    observation_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.state, RegimeState):
            raise ValueError("state must be RegimeState")
        _identity(self.reason, "reason")
        if not _SHA256_RE.fullmatch(self.model_fingerprint):
            raise ValueError("model_fingerprint must be SHA-256 hex")
        if self.observation_fingerprint and not _SHA256_RE.fullmatch(self.observation_fingerprint):
            raise ValueError("observation_fingerprint must be empty or SHA-256 hex")


@dataclass(frozen=True, slots=True)
class RegimeEvaluationEvidence:
    model_fingerprint: str
    series_fingerprint: str
    evaluation_phase: RegimeEvaluationPhase
    classifications: tuple[RegimeClassification, ...]

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "model_fingerprint": self.model_fingerprint,
                "series_fingerprint": self.series_fingerprint,
                "evaluation_phase": self.evaluation_phase.value,
                "classifications": [
                    {
                        "state": item.state.value,
                        "reason": item.reason,
                        "model_fingerprint": item.model_fingerprint,
                        "observation_fingerprint": item.observation_fingerprint,
                    }
                    for item in self.classifications
                ],
            }
        )


def calibrate_regime_model(
    *,
    model_id: str,
    version: int,
    series: RegimeCalibrationSeries,
    spec: RegimeCalibrationSpec,
    now: datetime,
) -> RegimeModel:
    if not isinstance(series, RegimeCalibrationSeries):
        raise TypeError("series must be RegimeCalibrationSeries; HOLDOUT evaluation series cannot calibrate")
    if not isinstance(spec, RegimeCalibrationSpec):
        raise TypeError("spec must be RegimeCalibrationSpec")
    if not _aware(now):
        raise ValueError("calibration time must be timezone-aware")
    if len(series.observations) < spec.min_observations:
        raise RegimeCalibrationError(
            f"calibration observations {len(series.observations)} below required {spec.min_observations}"
        )
    if any(item.available_at > now for item in series.observations):
        raise RegimeCalibrationError("calibration cannot use features unavailable at calibration time")

    values = tuple(sorted(item.value for item in series.observations))
    low = _nearest_rank(values, spec.low_quantile)
    high = _nearest_rank(values, spec.high_quantile)
    if low >= high:
        raise RegimeCalibrationError("calibrated thresholds are degenerate")
    return RegimeModel(
        model_id=model_id,
        version=version,
        feature_name=series.feature_name,
        calibration_phase=series.phase,
        calibration_series_fingerprint=series.fingerprint,
        calibration_source_hash=series.source_hash,
        spec_fingerprint=spec.fingerprint,
        low_threshold=low,
        high_threshold=high,
        calibrated_at=now,
    )


def classify_regime(
    model: RegimeModel,
    observation: RegimeFeatureObservation | None,
    *,
    now: datetime,
    max_age: timedelta,
) -> RegimeClassification:
    if not isinstance(model, RegimeModel):
        raise TypeError("model must be RegimeModel")
    if not _aware(now):
        raise ValueError("classification time must be timezone-aware")
    if max_age <= timedelta(0):
        raise ValueError("max_age must be > 0")
    if observation is None:
        return _classification(model, RegimeState.UNKNOWN, "MISSING_FEATURE", "")
    if not isinstance(observation, RegimeFeatureObservation):
        raise TypeError("observation must be RegimeFeatureObservation or None")
    fingerprint = observation.fingerprint
    if observation.available_at > now:
        return _classification(model, RegimeState.UNKNOWN, "FEATURE_NOT_YET_AVAILABLE", fingerprint)
    if now - observation.available_at > max_age:
        return _classification(model, RegimeState.UNKNOWN, "STALE_FEATURE", fingerprint)
    if observation.value < model.low_threshold:
        state = RegimeState.LOW
    elif observation.value > model.high_threshold:
        state = RegimeState.HIGH
    else:
        state = RegimeState.NORMAL
    return _classification(model, state, "CLASSIFIED", fingerprint)


def evaluate_regime_model(
    model: RegimeModel,
    series: RegimeEvaluationSeries,
    *,
    max_age: timedelta,
) -> RegimeEvaluationEvidence:
    if not isinstance(series, RegimeEvaluationSeries):
        raise TypeError("series must be RegimeEvaluationSeries")
    if series.feature_name != model.feature_name:
        raise RegimeGovernanceError("evaluation feature does not match frozen model")
    classifications = tuple(
        classify_regime(model, item, now=item.available_at, max_age=max_age)
        for item in series.observations
    )
    return RegimeEvaluationEvidence(
        model_fingerprint=model.fingerprint,
        series_fingerprint=series.fingerprint,
        evaluation_phase=series.phase,
        classifications=classifications,
    )


class SQLiteRegimeModelRegistry:
    """Append-only frozen regime-model registry for research/governance evidence."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("regime registry requires filesystem SQLite path")
        Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS regime_models (
                    model_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    fingerprint TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    PRIMARY KEY(model_id, version)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def register(self, model: RegimeModel, *, now: datetime) -> RegimeModel:
        if not isinstance(model, RegimeModel):
            raise TypeError("model must be RegimeModel")
        if not _aware(now):
            raise ValueError("registration time must be timezone-aware")
        payload_json = _canonical_json(model.to_payload())
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT fingerprint, payload_json FROM regime_models WHERE model_id=? AND version=?",
                (model.model_id, model.version),
            ).fetchone()
            if existing is not None:
                if existing["fingerprint"] != model.fingerprint or existing["payload_json"] != payload_json:
                    conn.rollback()
                    raise RegimeModelConflict("regime model identity conflict")
                conn.commit()
                return model
            latest = conn.execute(
                "SELECT model_id,version,fingerprint,payload_json FROM regime_models WHERE model_id=? ORDER BY version DESC LIMIT 1",
                (model.model_id,),
            ).fetchone()
            if latest is None:
                if model.version != 1:
                    conn.rollback()
                    raise RegimeModelConflict("first regime model version must be 1")
            else:
                previous = _model_from_storage(latest["fingerprint"], latest["payload_json"])
                if previous.model_id != latest["model_id"] or previous.version != int(latest["version"]):
                    conn.rollback()
                    raise RegimeModelConflict("stored regime row identity mismatch")
                if model.version != previous.version + 1:
                    conn.rollback()
                    raise RegimeModelConflict("regime model versions must advance exactly by one")
            conn.execute(
                "INSERT INTO regime_models(model_id,version,fingerprint,payload_json,registered_at) VALUES(?,?,?,?,?)",
                (model.model_id, model.version, model.fingerprint, payload_json, now.isoformat()),
            )
            conn.commit()
            return model
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def latest(self, model_id: str) -> RegimeModel:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT model_id,version,fingerprint,payload_json FROM regime_models WHERE model_id=? ORDER BY version DESC LIMIT 1",
                (model_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(model_id)
        model = _model_from_storage(row["fingerprint"], row["payload_json"])
        if model.model_id != row["model_id"] or model.version != int(row["version"]):
            raise RegimeModelConflict("stored regime row identity mismatch")
        return model


def _classification(
    model: RegimeModel,
    state: RegimeState,
    reason: str,
    observation_fingerprint: str,
) -> RegimeClassification:
    return RegimeClassification(
        state=state,
        reason=reason,
        model_fingerprint=model.fingerprint,
        observation_fingerprint=observation_fingerprint,
    )


def _model_from_storage(fingerprint: object, payload_json: object) -> RegimeModel:
    if not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint):
        raise RegimeModelConflict("stored regime fingerprint is invalid")
    if not isinstance(payload_json, str):
        raise RegimeModelConflict("stored regime payload is invalid")
    try:
        payload = json.loads(payload_json)
        model = RegimeModel.from_payload(payload)
    except RegimeModelConflict:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RegimeModelConflict("stored regime payload is invalid") from exc
    if model.fingerprint != fingerprint:
        raise RegimeModelConflict("stored regime fingerprint mismatch")
    return model


def _nearest_rank(values: tuple[Decimal, ...], quantile: Decimal) -> Decimal:
    rank = int((quantile * Decimal(len(values))).to_integral_value(rounding=ROUND_CEILING))
    index = max(0, min(len(values) - 1, rank - 1))
    return values[index]


def _validate_series(
    observations: tuple[RegimeFeatureObservation, ...],
    *,
    require_causal: bool,
) -> None:
    if len(observations) < 2:
        raise ValueError("regime feature series requires at least two observations")
    previous: datetime | None = None
    for item in observations:
        if not isinstance(item, RegimeFeatureObservation):
            raise ValueError("regime observations must contain RegimeFeatureObservation")
        if previous is not None and item.occurred_at <= previous:
            raise ValueError("regime observations must be strictly increasing and unique")
        if require_causal and item.available_at != item.occurred_at:
            raise RegimeGovernanceError(
                "regime calibration/evaluation features must be causally available at occurred_at"
            )
        previous = item.occurred_at


def _series_payload(feature_name: str, phase: str, source_hash: str, observations) -> dict[str, object]:
    return {
        "feature_name": feature_name,
        "phase": phase,
        "source_hash": source_hash,
        "observations": [
            {
                "occurred_at": item.occurred_at.isoformat(),
                "available_at": item.available_at.isoformat(),
                "value": str(item.value),
            }
            for item in observations
        ],
    }


def _identity(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")


def _source_hash(value: object) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError("source_hash must be lowercase SHA-256 hex")


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected integer")
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("decimal must be encoded as string")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("decimal string is invalid") from exc


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be encoded as string")
    parsed = datetime.fromisoformat(value)
    if not _aware(parsed):
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _finite(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
