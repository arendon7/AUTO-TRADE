"""OSS-3B point-in-time factor-matrix artifact.

This module defines the only factor-matrix format that may cross from an
AUTO-TRADE research producer into a future isolated ML/Qlib laboratory.
It is intentionally research-only and excludes FINAL_HOLDOUT data.

Scientific boundary:
- TRAIN or DEVELOPMENT only; FINAL_HOLDOUT is structurally rejected;
- exact campaign + frozen research-split identity are hash-bound;
- features only: no label/target field exists in the schema;
- exact feature definitions and source lineage are hash-bound;
- every row carries `available_at` and `as_of`, with available_at <= as_of;
- every `as_of` is inside the declared partition window;
- all values are finite in V1; missing values are forbidden;
- rows are unique and canonically sorted by (as_of, symbol);
- canonical JSON has one byte representation and rejects duplicate keys;
- no Qlib runtime, network, process, broker, OMS, Safety or execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence


OSS3B_ARTIFACT_VERSION = "OSS3B_FACTOR_MATRIX_ARTIFACT_V1"
OSS3B_EVIDENCE_VERSION = "OSS3B_FACTOR_MATRIX_EVIDENCE_V1"
OSS3B_PRODUCER_ID = "AUTO-TRADE/OSS3B_FACTOR_EXPORTER"
MAX_ARTIFACT_BYTES = 50_000_000
MAX_ROWS = 2_000_000
MAX_FEATURES = 512

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,31}$")
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")

_TOP_LEVEL_KEYS = frozenset(
    {"artifact_version", "manifest", "features", "rows", "artifact_hash"}
)
_MANIFEST_KEYS = frozenset(
    {
        "producer_id",
        "producer_code_hash",
        "campaign_id",
        "research_split_hash",
        "source_dataset_hash",
        "source_universe_hash",
        "partition",
        "partition_start",
        "partition_end",
        "feature_count",
        "row_count",
        "missing_value_policy",
        "point_in_time_policy",
        "feature_schema_hash",
        "row_payload_hash",
    }
)
_FEATURE_KEYS = frozenset(
    {"name", "dtype", "role", "formula_hash", "source_id", "source_hash", "lookback_bars"}
)
_ROW_KEYS = frozenset({"as_of", "available_at", "symbol", "values"})


class FactorMatrixPartition(str, Enum):
    TRAIN = "TRAIN"
    DEVELOPMENT = "DEVELOPMENT"


class FactorMatrixArtifactError(RuntimeError):
    """Base OSS-3B failure."""


class FactorMatrixIntegrityError(FactorMatrixArtifactError):
    """Artifact identity/schema/provenance is inconsistent."""


class FactorMatrixGovernanceError(FactorMatrixArtifactError):
    """Artifact violates point-in-time or research-only governance."""


@dataclass(frozen=True, slots=True)
class FactorDefinition:
    """One immutable numeric feature definition."""

    name: str
    dtype: str
    role: str
    formula_hash: str
    source_id: str
    source_hash: str
    lookback_bars: int

    def __post_init__(self) -> None:
        if not _NAME_RE.fullmatch(self.name):
            raise ValueError("invalid feature name")
        if self.dtype != "float64":
            raise FactorMatrixGovernanceError("OSS-3B V1 feature dtype must be float64")
        if self.role != "FEATURE":
            raise FactorMatrixGovernanceError("OSS-3B V1 permits FEATURE role only")
        _require_hash(self.formula_hash, "formula_hash")
        if not _SOURCE_ID_RE.fullmatch(self.source_id):
            raise ValueError("invalid feature source_id")
        _require_hash(self.source_hash, "source_hash")
        if (
            not isinstance(self.lookback_bars, int)
            or isinstance(self.lookback_bars, bool)
            or not 0 <= self.lookback_bars <= 1_000_000
        ):
            raise ValueError("lookback_bars is outside the OSS-3B bound")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "role": self.role,
            "formula_hash": self.formula_hash,
            "source_id": self.source_id,
            "source_hash": self.source_hash,
            "lookback_bars": self.lookback_bars,
        }


@dataclass(frozen=True, slots=True)
class FactorMatrixRow:
    """Point-in-time feature vector for one symbol."""

    as_of: str
    available_at: str
    symbol: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        as_of = _parse_canonical_utc(self.as_of, "as_of")
        available_at = _parse_canonical_utc(self.available_at, "available_at")
        if available_at > as_of:
            raise FactorMatrixGovernanceError(
                "feature vector was not available at the declared as_of"
            )
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("invalid factor symbol")
        if not isinstance(self.values, tuple):
            raise TypeError("factor values must be an immutable tuple")
        for value in self.values:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError("factor value must be numeric")
            if not isfinite(float(value)):
                raise FactorMatrixGovernanceError(
                    "OSS-3B V1 forbids missing or non-finite factor values"
                )

    @property
    def as_of_at(self) -> datetime:
        return _parse_canonical_utc(self.as_of, "as_of")

    @property
    def availability_at(self) -> datetime:
        return _parse_canonical_utc(self.available_at, "available_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of,
            "available_at": self.available_at,
            "symbol": self.symbol,
            "values": [float(value) for value in self.values],
        }


@dataclass(frozen=True, slots=True)
class FactorMatrixManifest:
    """Immutable provenance, campaign and partition envelope."""

    producer_id: str
    producer_code_hash: str
    campaign_id: str
    research_split_hash: str
    source_dataset_hash: str
    source_universe_hash: str
    partition: str
    partition_start: str
    partition_end: str
    feature_count: int
    row_count: int
    missing_value_policy: str
    point_in_time_policy: str
    feature_schema_hash: str
    row_payload_hash: str

    def __post_init__(self) -> None:
        if self.producer_id != OSS3B_PRODUCER_ID:
            raise FactorMatrixGovernanceError("noncanonical OSS-3B producer")
        if not _ID_RE.fullmatch(self.campaign_id):
            raise ValueError("invalid OSS-3B campaign_id")
        for name, value in (
            ("producer_code_hash", self.producer_code_hash),
            ("research_split_hash", self.research_split_hash),
            ("source_dataset_hash", self.source_dataset_hash),
            ("source_universe_hash", self.source_universe_hash),
            ("feature_schema_hash", self.feature_schema_hash),
            ("row_payload_hash", self.row_payload_hash),
        ):
            _require_hash(value, name)
        try:
            FactorMatrixPartition(self.partition)
        except ValueError as exc:
            raise FactorMatrixGovernanceError(
                "OSS-3B partition must be TRAIN or DEVELOPMENT; FINAL_HOLDOUT is forbidden"
            ) from exc
        start = _parse_canonical_utc(self.partition_start, "partition_start")
        end = _parse_canonical_utc(self.partition_end, "partition_end")
        if not start < end:
            raise FactorMatrixGovernanceError("partition window must be positive")
        _validate_counts(self.feature_count, self.row_count)
        if self.missing_value_policy != "FORBID":
            raise FactorMatrixGovernanceError("OSS-3B V1 missing-value policy must be FORBID")
        if self.point_in_time_policy != "AVAILABLE_AT_LE_AS_OF":
            raise FactorMatrixGovernanceError("noncanonical OSS-3B point-in-time policy")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    @property
    def partition_bounds(self) -> tuple[datetime, datetime]:
        return (
            _parse_canonical_utc(self.partition_start, "partition_start"),
            _parse_canonical_utc(self.partition_end, "partition_end"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "producer_id": self.producer_id,
            "producer_code_hash": self.producer_code_hash,
            "campaign_id": self.campaign_id,
            "research_split_hash": self.research_split_hash,
            "source_dataset_hash": self.source_dataset_hash,
            "source_universe_hash": self.source_universe_hash,
            "partition": self.partition,
            "partition_start": self.partition_start,
            "partition_end": self.partition_end,
            "feature_count": self.feature_count,
            "row_count": self.row_count,
            "missing_value_policy": self.missing_value_policy,
            "point_in_time_policy": self.point_in_time_policy,
            "feature_schema_hash": self.feature_schema_hash,
            "row_payload_hash": self.row_payload_hash,
        }


@dataclass(frozen=True, slots=True)
class FactorMatrixEvidence:
    """Verified dataset identity for downstream research-only consumers."""

    evidence_version: str
    artifact_hash: str
    manifest_fingerprint: str
    campaign_id: str
    research_split_hash: str
    partition: str
    partition_start: str
    partition_end: str
    feature_schema_hash: str
    source_dataset_hash: str
    source_universe_hash: str
    producer_code_hash: str
    row_count: int
    feature_count: int
    point_in_time_policy: str
    labels_included: bool = False
    final_holdout_included: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3B_EVIDENCE_VERSION:
            raise FactorMatrixIntegrityError("noncanonical OSS-3B evidence version")
        if not _ID_RE.fullmatch(self.campaign_id):
            raise FactorMatrixIntegrityError("invalid evidence campaign_id")
        for name, value in (
            ("artifact_hash", self.artifact_hash),
            ("manifest_fingerprint", self.manifest_fingerprint),
            ("research_split_hash", self.research_split_hash),
            ("feature_schema_hash", self.feature_schema_hash),
            ("source_dataset_hash", self.source_dataset_hash),
            ("source_universe_hash", self.source_universe_hash),
            ("producer_code_hash", self.producer_code_hash),
        ):
            _require_hash(value, name)
        try:
            FactorMatrixPartition(self.partition)
        except ValueError as exc:
            raise FactorMatrixGovernanceError("evidence contains forbidden partition") from exc
        start = _parse_canonical_utc(self.partition_start, "partition_start")
        end = _parse_canonical_utc(self.partition_end, "partition_end")
        if not start < end:
            raise FactorMatrixIntegrityError("evidence partition window is invalid")
        try:
            _validate_counts(self.feature_count, self.row_count)
        except ValueError as exc:
            raise FactorMatrixIntegrityError("evidence counts are invalid") from exc
        if self.point_in_time_policy != "AVAILABLE_AT_LE_AS_OF":
            raise FactorMatrixIntegrityError("point-in-time policy drifted")
        if self.labels_included or self.final_holdout_included:
            raise FactorMatrixGovernanceError(
                "OSS-3B evidence may not contain labels or FINAL_HOLDOUT"
            )
        if self.execution_authorized or self.paper_execution_authorized:
            raise FactorMatrixGovernanceError("OSS-3B evidence cannot authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise FactorMatrixGovernanceError("OSS-3B evidence cannot grant capital or LIVE")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    @property
    def qlib_training_dataset_hash(self) -> str:
        """Exact dataset identity a future isolated Qlib producer must bind."""
        return self.artifact_hash

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_version": self.evidence_version,
            "artifact_hash": self.artifact_hash,
            "manifest_fingerprint": self.manifest_fingerprint,
            "campaign_id": self.campaign_id,
            "research_split_hash": self.research_split_hash,
            "partition": self.partition,
            "partition_start": self.partition_start,
            "partition_end": self.partition_end,
            "feature_schema_hash": self.feature_schema_hash,
            "source_dataset_hash": self.source_dataset_hash,
            "source_universe_hash": self.source_universe_hash,
            "producer_code_hash": self.producer_code_hash,
            "row_count": self.row_count,
            "feature_count": self.feature_count,
            "point_in_time_policy": self.point_in_time_policy,
            "labels_included": self.labels_included,
            "final_holdout_included": self.final_holdout_included,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class FactorMatrixArtifact:
    artifact_version: str
    manifest: FactorMatrixManifest
    features: tuple[FactorDefinition, ...]
    rows: tuple[FactorMatrixRow, ...]
    artifact_hash: str

    def __post_init__(self) -> None:
        if self.artifact_version != OSS3B_ARTIFACT_VERSION:
            raise FactorMatrixIntegrityError("unsupported OSS-3B artifact version")
        _require_hash(self.artifact_hash, "artifact_hash")
        _validate_features(self.features, self.manifest)
        _validate_rows(self.rows, self.features, self.manifest)
        expected = _artifact_hash(
            artifact_version=self.artifact_version,
            manifest=self.manifest,
            features=self.features,
            rows=self.rows,
        )
        if expected != self.artifact_hash:
            raise FactorMatrixIntegrityError("OSS-3B artifact hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        campaign_id: str,
        research_split_hash: str,
        partition: FactorMatrixPartition,
        partition_start: datetime,
        partition_end: datetime,
        producer_code_hash: str,
        source_dataset_hash: str,
        source_universe_hash: str,
        features: Iterable[FactorDefinition],
        rows: Iterable[FactorMatrixRow],
    ) -> "FactorMatrixArtifact":
        if not isinstance(partition, FactorMatrixPartition):
            raise TypeError("partition must be FactorMatrixPartition")
        feature_tuple = tuple(features)
        row_tuple = tuple(rows)
        manifest = FactorMatrixManifest(
            producer_id=OSS3B_PRODUCER_ID,
            producer_code_hash=producer_code_hash,
            campaign_id=campaign_id,
            research_split_hash=research_split_hash,
            source_dataset_hash=source_dataset_hash,
            source_universe_hash=source_universe_hash,
            partition=partition.value,
            partition_start=_canonical_utc(partition_start, "partition_start"),
            partition_end=_canonical_utc(partition_end, "partition_end"),
            feature_count=len(feature_tuple),
            row_count=len(row_tuple),
            missing_value_policy="FORBID",
            point_in_time_policy="AVAILABLE_AT_LE_AS_OF",
            feature_schema_hash=_feature_schema_hash(feature_tuple),
            row_payload_hash=_row_payload_hash(row_tuple),
        )
        artifact_hash = _artifact_hash(
            artifact_version=OSS3B_ARTIFACT_VERSION,
            manifest=manifest,
            features=feature_tuple,
            rows=row_tuple,
        )
        return cls(
            artifact_version=OSS3B_ARTIFACT_VERSION,
            manifest=manifest,
            features=feature_tuple,
            rows=row_tuple,
            artifact_hash=artifact_hash,
        )

    def to_research_evidence(self) -> FactorMatrixEvidence:
        manifest = self.manifest
        return FactorMatrixEvidence(
            evidence_version=OSS3B_EVIDENCE_VERSION,
            artifact_hash=self.artifact_hash,
            manifest_fingerprint=manifest.fingerprint,
            campaign_id=manifest.campaign_id,
            research_split_hash=manifest.research_split_hash,
            partition=manifest.partition,
            partition_start=manifest.partition_start,
            partition_end=manifest.partition_end,
            feature_schema_hash=manifest.feature_schema_hash,
            source_dataset_hash=manifest.source_dataset_hash,
            source_universe_hash=manifest.source_universe_hash,
            producer_code_hash=manifest.producer_code_hash,
            row_count=manifest.row_count,
            feature_count=manifest.feature_count,
            point_in_time_policy=manifest.point_in_time_policy,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_version": self.artifact_version,
            "manifest": self.manifest.to_dict(),
            "features": [feature.to_dict() for feature in self.features],
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
    def read(cls, path: str | Path) -> "FactorMatrixArtifact":
        target = Path(path)
        if not target.is_file():
            raise FactorMatrixIntegrityError("OSS-3B artifact does not exist")
        if target.stat().st_size > MAX_ARTIFACT_BYTES:
            raise FactorMatrixGovernanceError("OSS-3B artifact exceeds size limit")
        try:
            raw = target.read_text(encoding="utf-8")
            document = json.loads(raw, object_pairs_hook=_reject_duplicate_json_pairs)
        except FactorMatrixArtifactError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FactorMatrixIntegrityError("OSS-3B artifact is not valid UTF-8 JSON") from exc
        if not isinstance(document, dict) or frozenset(document) != _TOP_LEVEL_KEYS:
            raise FactorMatrixIntegrityError("OSS-3B top-level schema mismatch")
        manifest_raw = document["manifest"]
        features_raw = document["features"]
        rows_raw = document["rows"]
        if not isinstance(manifest_raw, dict) or frozenset(manifest_raw) != _MANIFEST_KEYS:
            raise FactorMatrixIntegrityError("OSS-3B manifest schema mismatch")
        if not isinstance(features_raw, list):
            raise FactorMatrixIntegrityError("OSS-3B features must be an array")
        if not isinstance(rows_raw, list):
            raise FactorMatrixIntegrityError("OSS-3B rows must be an array")
        try:
            manifest = _manifest_from_mapping(manifest_raw)
            features = tuple(_feature_from_mapping(value) for value in features_raw)
            rows = tuple(_row_from_mapping(value) for value in rows_raw)
            artifact_version = _string(document, "artifact_version")
            artifact_hash = _string(document, "artifact_hash")
            artifact = cls(
                artifact_version=artifact_version,
                manifest=manifest,
                features=features,
                rows=rows,
                artifact_hash=artifact_hash,
            )
        except FactorMatrixArtifactError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise FactorMatrixIntegrityError("OSS-3B artifact fields are invalid") from exc
        expected_raw = _canonical_json(artifact.to_dict()) + "\n"
        if raw != expected_raw:
            raise FactorMatrixIntegrityError("OSS-3B artifact serialization is not canonical")
        return artifact


def _validate_features(
    features: Sequence[FactorDefinition], manifest: FactorMatrixManifest
) -> None:
    if len(features) != manifest.feature_count:
        raise FactorMatrixIntegrityError("feature_count does not match feature schema")
    if not features:
        raise FactorMatrixGovernanceError("OSS-3B requires at least one feature")
    if len(features) > MAX_FEATURES:
        raise FactorMatrixGovernanceError("too many OSS-3B features")
    names = [feature.name for feature in features]
    if len(names) != len(set(names)):
        raise FactorMatrixGovernanceError("duplicate feature name")
    if _feature_schema_hash(features) != manifest.feature_schema_hash:
        raise FactorMatrixIntegrityError("feature schema hash mismatch")


def _validate_rows(
    rows: Sequence[FactorMatrixRow],
    features: Sequence[FactorDefinition],
    manifest: FactorMatrixManifest,
) -> None:
    if len(rows) != manifest.row_count:
        raise FactorMatrixIntegrityError("row_count does not match rows")
    if not rows:
        raise FactorMatrixGovernanceError("OSS-3B requires at least one row")
    if len(rows) > MAX_ROWS:
        raise FactorMatrixGovernanceError("too many OSS-3B rows")
    expected_order = tuple(sorted(rows, key=lambda row: (row.as_of, row.symbol)))
    if tuple(rows) != expected_order:
        raise FactorMatrixGovernanceError("factor rows must be canonically sorted")
    identities = [(row.as_of, row.symbol) for row in rows]
    if len(identities) != len(set(identities)):
        raise FactorMatrixGovernanceError("duplicate factor row identity")
    start, end = manifest.partition_bounds
    for row in rows:
        if len(row.values) != len(features):
            raise FactorMatrixIntegrityError("factor row width does not match feature schema")
        if not start <= row.as_of_at < end:
            raise FactorMatrixGovernanceError("factor row as_of is outside partition")
        if row.availability_at > row.as_of_at:
            raise FactorMatrixGovernanceError("factor row violates point-in-time availability")
    if _row_payload_hash(rows) != manifest.row_payload_hash:
        raise FactorMatrixIntegrityError("row payload hash mismatch")


def _validate_counts(feature_count: int, row_count: int) -> None:
    if (
        not isinstance(feature_count, int)
        or isinstance(feature_count, bool)
        or not 1 <= feature_count <= MAX_FEATURES
    ):
        raise ValueError("feature_count is outside the OSS-3B bound")
    if (
        not isinstance(row_count, int)
        or isinstance(row_count, bool)
        or not 1 <= row_count <= MAX_ROWS
    ):
        raise ValueError("row_count is outside the OSS-3B bound")


def _manifest_from_mapping(data: Mapping[str, object]) -> FactorMatrixManifest:
    return FactorMatrixManifest(
        producer_id=_string(data, "producer_id"),
        producer_code_hash=_string(data, "producer_code_hash"),
        campaign_id=_string(data, "campaign_id"),
        research_split_hash=_string(data, "research_split_hash"),
        source_dataset_hash=_string(data, "source_dataset_hash"),
        source_universe_hash=_string(data, "source_universe_hash"),
        partition=_string(data, "partition"),
        partition_start=_string(data, "partition_start"),
        partition_end=_string(data, "partition_end"),
        feature_count=_integer(data, "feature_count"),
        row_count=_integer(data, "row_count"),
        missing_value_policy=_string(data, "missing_value_policy"),
        point_in_time_policy=_string(data, "point_in_time_policy"),
        feature_schema_hash=_string(data, "feature_schema_hash"),
        row_payload_hash=_string(data, "row_payload_hash"),
    )


def _feature_from_mapping(value: object) -> FactorDefinition:
    if not isinstance(value, dict) or frozenset(value) != _FEATURE_KEYS:
        raise FactorMatrixIntegrityError("OSS-3B feature schema mismatch")
    return FactorDefinition(
        name=_string(value, "name"),
        dtype=_string(value, "dtype"),
        role=_string(value, "role"),
        formula_hash=_string(value, "formula_hash"),
        source_id=_string(value, "source_id"),
        source_hash=_string(value, "source_hash"),
        lookback_bars=_integer(value, "lookback_bars"),
    )


def _row_from_mapping(value: object) -> FactorMatrixRow:
    if not isinstance(value, dict) or frozenset(value) != _ROW_KEYS:
        raise FactorMatrixIntegrityError("OSS-3B row schema mismatch")
    values = value["values"]
    if not isinstance(values, list):
        raise FactorMatrixIntegrityError("OSS-3B row values must be an array")
    converted: list[float] = []
    for item in values:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            raise FactorMatrixIntegrityError("factor value must be numeric")
        converted.append(float(item))
    return FactorMatrixRow(
        as_of=_string(value, "as_of"),
        available_at=_string(value, "available_at"),
        symbol=_string(value, "symbol"),
        values=tuple(converted),
    )


def _feature_schema_hash(features: Sequence[FactorDefinition]) -> str:
    return _hash([feature.to_dict() for feature in features])


def _row_payload_hash(rows: Sequence[FactorMatrixRow]) -> str:
    return _hash([row.to_dict() for row in rows])


def _artifact_hash(
    *,
    artifact_version: str,
    manifest: FactorMatrixManifest,
    features: Sequence[FactorDefinition],
    rows: Sequence[FactorMatrixRow],
) -> str:
    return _hash(
        {
            "artifact_version": artifact_version,
            "manifest": manifest.to_dict(),
            "features": [feature.to_dict() for feature in features],
            "rows": [row.to_dict() for row in rows],
        }
    )


def _canonical_utc(value: datetime, name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


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
            raise FactorMatrixIntegrityError(f"duplicate JSON object key: {key}")
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
