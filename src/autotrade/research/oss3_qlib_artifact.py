"""OSS-3A isolated Qlib prediction-artifact contract.

The Qlib runtime is intentionally OUTSIDE AUTO-TRADE. This module accepts
only a canonical local JSON artifact produced by an isolated research process.
It performs deterministic structural/provenance verification and converts the
artifact into immutable research evidence. It has no network, broker, OMS,
Safety, OrderIntent, PAPER, capital or LIVE authority.

Security/scientific boundary:
- no dynamic imports, eval, exec, pickle or code loading;
- no network/process execution;
- exact producer identity (microsoft/qlib);
- exact hashes for training data, feature schema, model config and producer code;
- train window must end before inference starts;
- every prediction must live inside the declared inference window;
- rows are unique and canonically sorted by (timestamp, symbol);
- scores must be finite;
- duplicate JSON object keys are rejected;
- artifact bytes must use the canonical serialization;
- artifact and payload hashes are recomputed on ingestion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence


OSS3A_ARTIFACT_VERSION = "OSS3A_QLIB_PREDICTION_ARTIFACT_V1"
OSS3A_EVIDENCE_VERSION = "OSS3A_QLIB_PREDICTION_EVIDENCE_V1"
QLIB_PRODUCER_ID = "microsoft/qlib"
QLIB_LICENSE_ID = "MIT"
MAX_ARTIFACT_BYTES = 25_000_000
MAX_PREDICTIONS = 2_000_000

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,31}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

_TOP_LEVEL_KEYS = frozenset({"artifact_version", "manifest", "rows", "artifact_hash"})
_MANIFEST_KEYS = frozenset(
    {
        "producer_id",
        "producer_license",
        "qlib_version",
        "model_family",
        "model_config_hash",
        "training_dataset_hash",
        "feature_schema_hash",
        "producer_code_hash",
        "train_start",
        "train_end",
        "inference_start",
        "inference_end",
        "prediction_count",
        "prediction_payload_hash",
    }
)
_ROW_KEYS = frozenset({"timestamp", "symbol", "score"})


class QlibArtifactError(RuntimeError):
    """Base class for OSS-3A artifact failures."""


class QlibArtifactIntegrityError(QlibArtifactError):
    """The artifact does not match its declared immutable identity."""


class QlibArtifactGovernanceError(QlibArtifactError):
    """The artifact violates the research-only scientific boundary."""


@dataclass(frozen=True, slots=True, order=True)
class QlibPredictionRow:
    """One canonical cross-sectional prediction."""

    timestamp: str
    symbol: str
    score: float

    def __post_init__(self) -> None:
        _parse_canonical_utc(self.timestamp, "prediction timestamp")
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("invalid prediction symbol")
        if not isinstance(self.score, (int, float)) or isinstance(self.score, bool):
            raise ValueError("prediction score must be numeric")
        if not isfinite(float(self.score)):
            raise ValueError("prediction score must be finite")

    @property
    def observed_at(self) -> datetime:
        return _parse_canonical_utc(self.timestamp, "prediction timestamp")

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "score": float(self.score),
        }


@dataclass(frozen=True, slots=True)
class QlibPredictionManifest:
    """Immutable provenance envelope produced by the isolated Qlib lab."""

    producer_id: str
    producer_license: str
    qlib_version: str
    model_family: str
    model_config_hash: str
    training_dataset_hash: str
    feature_schema_hash: str
    producer_code_hash: str
    train_start: str
    train_end: str
    inference_start: str
    inference_end: str
    prediction_count: int
    prediction_payload_hash: str

    def __post_init__(self) -> None:
        if self.producer_id != QLIB_PRODUCER_ID:
            raise QlibArtifactGovernanceError("OSS-3A producer must be microsoft/qlib")
        if self.producer_license != QLIB_LICENSE_ID:
            raise QlibArtifactGovernanceError("OSS-3A Qlib producer license must be MIT")
        if not _VERSION_RE.fullmatch(self.qlib_version):
            raise ValueError("invalid qlib_version")
        if not _ID_RE.fullmatch(self.model_family):
            raise ValueError("invalid model_family")
        for name, value in (
            ("model_config_hash", self.model_config_hash),
            ("training_dataset_hash", self.training_dataset_hash),
            ("feature_schema_hash", self.feature_schema_hash),
            ("producer_code_hash", self.producer_code_hash),
            ("prediction_payload_hash", self.prediction_payload_hash),
        ):
            _require_hash(value, name)
        train_start = _parse_canonical_utc(self.train_start, "train_start")
        train_end = _parse_canonical_utc(self.train_end, "train_end")
        inference_start = _parse_canonical_utc(self.inference_start, "inference_start")
        inference_end = _parse_canonical_utc(self.inference_end, "inference_end")
        if not train_start < train_end:
            raise QlibArtifactGovernanceError("training window must be positive")
        if train_end > inference_start:
            raise QlibArtifactGovernanceError(
                "training window may not overlap the inference window"
            )
        if not inference_start < inference_end:
            raise QlibArtifactGovernanceError("inference window must be positive")
        if (
            not isinstance(self.prediction_count, int)
            or isinstance(self.prediction_count, bool)
            or not 1 <= self.prediction_count <= MAX_PREDICTIONS
        ):
            raise ValueError("prediction_count is outside the OSS-3A bound")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    @property
    def inference_bounds(self) -> tuple[datetime, datetime]:
        return (
            _parse_canonical_utc(self.inference_start, "inference_start"),
            _parse_canonical_utc(self.inference_end, "inference_end"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "producer_id": self.producer_id,
            "producer_license": self.producer_license,
            "qlib_version": self.qlib_version,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "training_dataset_hash": self.training_dataset_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "producer_code_hash": self.producer_code_hash,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "inference_start": self.inference_start,
            "inference_end": self.inference_end,
            "prediction_count": self.prediction_count,
            "prediction_payload_hash": self.prediction_payload_hash,
        }


@dataclass(frozen=True, slots=True)
class QlibPredictionEvidence:
    """Verified research evidence; deliberately contains no execution authority."""

    evidence_version: str
    artifact_hash: str
    manifest_fingerprint: str
    producer_id: str
    qlib_version: str
    model_family: str
    model_config_hash: str
    training_dataset_hash: str
    feature_schema_hash: str
    producer_code_hash: str
    inference_start: str
    inference_end: str
    prediction_count: int
    prediction_payload_hash: str
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3A_EVIDENCE_VERSION:
            raise QlibArtifactIntegrityError("noncanonical OSS-3A evidence version")
        for name, value in (
            ("artifact_hash", self.artifact_hash),
            ("manifest_fingerprint", self.manifest_fingerprint),
            ("model_config_hash", self.model_config_hash),
            ("training_dataset_hash", self.training_dataset_hash),
            ("feature_schema_hash", self.feature_schema_hash),
            ("producer_code_hash", self.producer_code_hash),
            ("prediction_payload_hash", self.prediction_payload_hash),
        ):
            _require_hash(value, name)
        if self.producer_id != QLIB_PRODUCER_ID:
            raise QlibArtifactIntegrityError("evidence producer identity drifted")
        if self.execution_authorized or self.paper_execution_authorized:
            raise QlibArtifactGovernanceError("OSS-3A evidence cannot authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise QlibArtifactGovernanceError("OSS-3A evidence cannot grant capital or LIVE")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_version": self.evidence_version,
            "artifact_hash": self.artifact_hash,
            "manifest_fingerprint": self.manifest_fingerprint,
            "producer_id": self.producer_id,
            "qlib_version": self.qlib_version,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "training_dataset_hash": self.training_dataset_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "producer_code_hash": self.producer_code_hash,
            "inference_start": self.inference_start,
            "inference_end": self.inference_end,
            "prediction_count": self.prediction_count,
            "prediction_payload_hash": self.prediction_payload_hash,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class QlibPredictionArtifact:
    artifact_version: str
    manifest: QlibPredictionManifest
    rows: tuple[QlibPredictionRow, ...]
    artifact_hash: str

    def __post_init__(self) -> None:
        if self.artifact_version != OSS3A_ARTIFACT_VERSION:
            raise QlibArtifactIntegrityError("unsupported OSS-3A artifact version")
        _require_hash(self.artifact_hash, "artifact_hash")
        _validate_rows_against_manifest(self.rows, self.manifest)
        expected = _artifact_hash(
            artifact_version=self.artifact_version,
            manifest=self.manifest,
            rows=self.rows,
        )
        if self.artifact_hash != expected:
            raise QlibArtifactIntegrityError("OSS-3A artifact hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        qlib_version: str,
        model_family: str,
        model_config_hash: str,
        training_dataset_hash: str,
        feature_schema_hash: str,
        producer_code_hash: str,
        train_start: datetime,
        train_end: datetime,
        inference_start: datetime,
        inference_end: datetime,
        rows: Iterable[QlibPredictionRow],
    ) -> "QlibPredictionArtifact":
        canonical_rows = tuple(rows)
        payload_hash = _prediction_payload_hash(canonical_rows)
        manifest = QlibPredictionManifest(
            producer_id=QLIB_PRODUCER_ID,
            producer_license=QLIB_LICENSE_ID,
            qlib_version=qlib_version,
            model_family=model_family,
            model_config_hash=model_config_hash,
            training_dataset_hash=training_dataset_hash,
            feature_schema_hash=feature_schema_hash,
            producer_code_hash=producer_code_hash,
            train_start=_canonical_utc(train_start, "train_start"),
            train_end=_canonical_utc(train_end, "train_end"),
            inference_start=_canonical_utc(inference_start, "inference_start"),
            inference_end=_canonical_utc(inference_end, "inference_end"),
            prediction_count=len(canonical_rows),
            prediction_payload_hash=payload_hash,
        )
        artifact_hash = _artifact_hash(
            artifact_version=OSS3A_ARTIFACT_VERSION,
            manifest=manifest,
            rows=canonical_rows,
        )
        return cls(
            artifact_version=OSS3A_ARTIFACT_VERSION,
            manifest=manifest,
            rows=canonical_rows,
            artifact_hash=artifact_hash,
        )

    def to_research_evidence(self) -> QlibPredictionEvidence:
        manifest = self.manifest
        return QlibPredictionEvidence(
            evidence_version=OSS3A_EVIDENCE_VERSION,
            artifact_hash=self.artifact_hash,
            manifest_fingerprint=manifest.fingerprint,
            producer_id=manifest.producer_id,
            qlib_version=manifest.qlib_version,
            model_family=manifest.model_family,
            model_config_hash=manifest.model_config_hash,
            training_dataset_hash=manifest.training_dataset_hash,
            feature_schema_hash=manifest.feature_schema_hash,
            producer_code_hash=manifest.producer_code_hash,
            inference_start=manifest.inference_start,
            inference_end=manifest.inference_end,
            prediction_count=manifest.prediction_count,
            prediction_payload_hash=manifest.prediction_payload_hash,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_version": self.artifact_version,
            "manifest": self.manifest.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
            "artifact_hash": self.artifact_hash,
        }

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = _canonical_json(self.to_dict()) + "\n"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(raw, encoding="utf-8")
        temporary.replace(target)

    @classmethod
    def read(cls, path: str | Path) -> "QlibPredictionArtifact":
        target = Path(path)
        if not target.is_file():
            raise QlibArtifactIntegrityError("OSS-3A artifact does not exist")
        if target.stat().st_size > MAX_ARTIFACT_BYTES:
            raise QlibArtifactGovernanceError("OSS-3A artifact exceeds size limit")
        try:
            raw = target.read_text(encoding="utf-8")
            document = json.loads(raw, object_pairs_hook=_reject_duplicate_json_pairs)
        except QlibArtifactIntegrityError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise QlibArtifactIntegrityError("OSS-3A artifact is not valid UTF-8 JSON") from exc
        if not isinstance(document, dict) or frozenset(document) != _TOP_LEVEL_KEYS:
            raise QlibArtifactIntegrityError("OSS-3A top-level schema mismatch")
        manifest_raw = document["manifest"]
        rows_raw = document["rows"]
        if not isinstance(manifest_raw, dict) or frozenset(manifest_raw) != _MANIFEST_KEYS:
            raise QlibArtifactIntegrityError("OSS-3A manifest schema mismatch")
        if not isinstance(rows_raw, list):
            raise QlibArtifactIntegrityError("OSS-3A rows must be an array")
        try:
            manifest = _manifest_from_mapping(manifest_raw)
            rows = tuple(_row_from_mapping(row) for row in rows_raw)
            artifact_hash = document["artifact_hash"]
            if not isinstance(artifact_hash, str):
                raise TypeError("artifact_hash must be a string")
            artifact_version = document["artifact_version"]
            if not isinstance(artifact_version, str):
                raise TypeError("artifact_version must be a string")
            artifact = cls(
                artifact_version=artifact_version,
                manifest=manifest,
                rows=rows,
                artifact_hash=artifact_hash,
            )
        except QlibArtifactError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise QlibArtifactIntegrityError("OSS-3A artifact fields are invalid") from exc
        expected_raw = _canonical_json(artifact.to_dict()) + "\n"
        if raw != expected_raw:
            raise QlibArtifactIntegrityError("OSS-3A artifact serialization is not canonical")
        return artifact


def _manifest_from_mapping(data: Mapping[str, object]) -> QlibPredictionManifest:
    return QlibPredictionManifest(
        producer_id=_string(data, "producer_id"),
        producer_license=_string(data, "producer_license"),
        qlib_version=_string(data, "qlib_version"),
        model_family=_string(data, "model_family"),
        model_config_hash=_string(data, "model_config_hash"),
        training_dataset_hash=_string(data, "training_dataset_hash"),
        feature_schema_hash=_string(data, "feature_schema_hash"),
        producer_code_hash=_string(data, "producer_code_hash"),
        train_start=_string(data, "train_start"),
        train_end=_string(data, "train_end"),
        inference_start=_string(data, "inference_start"),
        inference_end=_string(data, "inference_end"),
        prediction_count=_integer(data, "prediction_count"),
        prediction_payload_hash=_string(data, "prediction_payload_hash"),
    )


def _row_from_mapping(value: object) -> QlibPredictionRow:
    if not isinstance(value, dict) or frozenset(value) != _ROW_KEYS:
        raise QlibArtifactIntegrityError("OSS-3A prediction-row schema mismatch")
    timestamp = _string(value, "timestamp")
    symbol = _string(value, "symbol")
    score = value["score"]
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise QlibArtifactIntegrityError("prediction score must be numeric")
    try:
        return QlibPredictionRow(timestamp=timestamp, symbol=symbol, score=float(score))
    except ValueError as exc:
        raise QlibArtifactIntegrityError("invalid prediction row") from exc


def _validate_rows_against_manifest(
    rows: Sequence[QlibPredictionRow], manifest: QlibPredictionManifest
) -> None:
    if len(rows) != manifest.prediction_count:
        raise QlibArtifactIntegrityError("prediction_count does not match rows")
    if not rows:
        raise QlibArtifactGovernanceError("OSS-3A requires at least one prediction")
    if len(rows) > MAX_PREDICTIONS:
        raise QlibArtifactGovernanceError("too many OSS-3A predictions")
    expected_order = tuple(sorted(rows, key=lambda row: (row.timestamp, row.symbol)))
    if tuple(rows) != expected_order:
        raise QlibArtifactGovernanceError(
            "predictions must be canonically sorted by timestamp then symbol"
        )
    keys = [(row.timestamp, row.symbol) for row in rows]
    if len(keys) != len(set(keys)):
        raise QlibArtifactGovernanceError("duplicate prediction identity")
    inference_start, inference_end = manifest.inference_bounds
    for row in rows:
        observed_at = row.observed_at
        if not inference_start <= observed_at < inference_end:
            raise QlibArtifactGovernanceError(
                "prediction timestamp is outside the declared inference window"
            )
    if _prediction_payload_hash(rows) != manifest.prediction_payload_hash:
        raise QlibArtifactIntegrityError("prediction payload hash mismatch")


def _prediction_payload_hash(rows: Sequence[QlibPredictionRow]) -> str:
    return _hash([row.to_dict() for row in rows])


def _artifact_hash(
    *,
    artifact_version: str,
    manifest: QlibPredictionManifest,
    rows: Sequence[QlibPredictionRow],
) -> str:
    return _hash(
        {
            "artifact_version": artifact_version,
            "manifest": manifest.to_dict(),
            "rows": [row.to_dict() for row in rows],
        }
    )


def _canonical_utc(value: datetime, name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat()


def _parse_canonical_utc(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be canonical UTC")
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        raise ValueError(f"{name} must use canonical +00:00 representation")
    return normalized


def _reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise QlibArtifactIntegrityError(f"duplicate JSON object key: {key}")
        document[key] = value
    return document


def _string(data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _integer(data: Mapping[str, object], key: str) -> int:
    value = data[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer")
    return value


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase sha256")


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
