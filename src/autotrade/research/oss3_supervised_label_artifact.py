"""OSS-3C supervised-label artifact.

Research-only contract for future supervised targets. Labels are deliberately
separate from OSS-3B point-in-time features.

V1 invariants:
- TRAIN or DEVELOPMENT only; FINAL_HOLDOUT is structurally unavailable;
- campaign and frozen research-split identities are hash-bound;
- each label has label_as_of < horizon_end <= available_at;
- the full target horizon and its availability remain inside the declared
  partition: partition_start <= label_as_of and available_at < partition_end;
- exactly one finite float64 LABEL definition per artifact;
- canonical JSON, duplicate-key rejection and full provenance hashes;
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


OSS3C_ARTIFACT_VERSION = "OSS3C_SUPERVISED_LABEL_ARTIFACT_V1"
OSS3C_EVIDENCE_VERSION = "OSS3C_SUPERVISED_LABEL_EVIDENCE_V1"
OSS3C_PRODUCER_ID = "AUTO-TRADE/OSS3C_LABEL_EXPORTER"
MAX_ARTIFACT_BYTES = 50_000_000
MAX_ROWS = 2_000_000

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,31}$")

_TOP_LEVEL_KEYS = frozenset(
    {"artifact_version", "manifest", "label", "rows", "artifact_hash"}
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
        "row_count",
        "label_definition_hash",
        "horizon_policy",
        "availability_policy",
        "missing_value_policy",
        "row_payload_hash",
    }
)
_LABEL_KEYS = frozenset(
    {"name", "dtype", "role", "formula_hash", "source_id", "source_hash"}
)
_ROW_KEYS = frozenset(
    {"label_as_of", "horizon_end", "available_at", "symbol", "value"}
)


class LabelPartition(str, Enum):
    TRAIN = "TRAIN"
    DEVELOPMENT = "DEVELOPMENT"


class LabelArtifactError(RuntimeError):
    """Base OSS-3C failure."""


class LabelIntegrityError(LabelArtifactError):
    """Artifact schema, identity or provenance is inconsistent."""


class LabelGovernanceError(LabelArtifactError):
    """Artifact violates temporal or research-only governance."""


@dataclass(frozen=True, slots=True)
class LabelDefinition:
    name: str
    dtype: str
    role: str
    formula_hash: str
    source_id: str
    source_hash: str

    def __post_init__(self) -> None:
        if not _NAME_RE.fullmatch(self.name):
            raise ValueError("invalid label name")
        if self.dtype != "float64":
            raise LabelGovernanceError("OSS-3C V1 label dtype must be float64")
        if self.role != "LABEL":
            raise LabelGovernanceError("OSS-3C V1 permits LABEL role only")
        _require_hash(self.formula_hash, "formula_hash")
        if not _ID_RE.fullmatch(self.source_id):
            raise ValueError("invalid label source_id")
        _require_hash(self.source_hash, "source_hash")

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
        }


@dataclass(frozen=True, slots=True)
class SupervisedLabelRow:
    label_as_of: str
    horizon_end: str
    available_at: str
    symbol: str
    value: float

    def __post_init__(self) -> None:
        origin = _parse_canonical_utc(self.label_as_of, "label_as_of")
        horizon = _parse_canonical_utc(self.horizon_end, "horizon_end")
        available = _parse_canonical_utc(self.available_at, "available_at")
        if not origin < horizon:
            raise LabelGovernanceError("label horizon must end strictly after label_as_of")
        if available < horizon:
            raise LabelGovernanceError("label cannot be available before horizon_end")
        if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
            raise ValueError("label value must be numeric")
        if not isfinite(float(self.value)):
            raise LabelGovernanceError("OSS-3C V1 forbids missing or non-finite labels")
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("invalid label symbol")

    @property
    def origin_at(self) -> datetime:
        return _parse_canonical_utc(self.label_as_of, "label_as_of")

    @property
    def horizon_at(self) -> datetime:
        return _parse_canonical_utc(self.horizon_end, "horizon_end")

    @property
    def availability_at(self) -> datetime:
        return _parse_canonical_utc(self.available_at, "available_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "label_as_of": self.label_as_of,
            "horizon_end": self.horizon_end,
            "available_at": self.available_at,
            "symbol": self.symbol,
            "value": float(self.value),
        }


@dataclass(frozen=True, slots=True)
class LabelManifest:
    producer_id: str
    producer_code_hash: str
    campaign_id: str
    research_split_hash: str
    source_dataset_hash: str
    source_universe_hash: str
    partition: str
    partition_start: str
    partition_end: str
    row_count: int
    label_definition_hash: str
    horizon_policy: str
    availability_policy: str
    missing_value_policy: str
    row_payload_hash: str

    def __post_init__(self) -> None:
        if self.producer_id != OSS3C_PRODUCER_ID:
            raise LabelGovernanceError("noncanonical OSS-3C producer")
        _require_hash(self.producer_code_hash, "producer_code_hash")
        if not _ID_RE.fullmatch(self.campaign_id):
            raise ValueError("invalid campaign_id")
        for name, value in (
            ("research_split_hash", self.research_split_hash),
            ("source_dataset_hash", self.source_dataset_hash),
            ("source_universe_hash", self.source_universe_hash),
            ("label_definition_hash", self.label_definition_hash),
            ("row_payload_hash", self.row_payload_hash),
        ):
            _require_hash(value, name)
        try:
            LabelPartition(self.partition)
        except ValueError as exc:
            raise LabelGovernanceError(
                "OSS-3C partition must be TRAIN or DEVELOPMENT; FINAL_HOLDOUT is forbidden"
            ) from exc
        start = _parse_canonical_utc(self.partition_start, "partition_start")
        end = _parse_canonical_utc(self.partition_end, "partition_end")
        if not start < end:
            raise LabelGovernanceError("partition window must be positive")
        if (
            not isinstance(self.row_count, int)
            or isinstance(self.row_count, bool)
            or not 1 <= self.row_count <= MAX_ROWS
        ):
            raise ValueError("row_count is outside the OSS-3C bound")
        if self.horizon_policy != "EXPLICIT_FUTURE_HORIZON":
            raise LabelGovernanceError("noncanonical OSS-3C horizon policy")
        if self.availability_policy != "AVAILABLE_AT_GE_HORIZON_END":
            raise LabelGovernanceError("noncanonical OSS-3C availability policy")
        if self.missing_value_policy != "FORBID":
            raise LabelGovernanceError("OSS-3C V1 missing-value policy must be FORBID")

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
            "row_count": self.row_count,
            "label_definition_hash": self.label_definition_hash,
            "horizon_policy": self.horizon_policy,
            "availability_policy": self.availability_policy,
            "missing_value_policy": self.missing_value_policy,
            "row_payload_hash": self.row_payload_hash,
        }


@dataclass(frozen=True, slots=True)
class LabelEvidence:
    evidence_version: str
    artifact_hash: str
    manifest_fingerprint: str
    campaign_id: str
    research_split_hash: str
    partition: str
    partition_start: str
    partition_end: str
    label_definition_hash: str
    source_dataset_hash: str
    source_universe_hash: str
    producer_code_hash: str
    row_count: int
    horizon_policy: str
    availability_policy: str
    final_holdout_included: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3C_EVIDENCE_VERSION:
            raise LabelIntegrityError("noncanonical OSS-3C evidence version")
        _require_hash(self.artifact_hash, "artifact_hash")
        _require_hash(self.manifest_fingerprint, "manifest_fingerprint")
        if not _ID_RE.fullmatch(self.campaign_id):
            raise LabelIntegrityError("invalid evidence campaign_id")
        for name, value in (
            ("research_split_hash", self.research_split_hash),
            ("label_definition_hash", self.label_definition_hash),
            ("source_dataset_hash", self.source_dataset_hash),
            ("source_universe_hash", self.source_universe_hash),
            ("producer_code_hash", self.producer_code_hash),
        ):
            _require_hash(value, name)
        try:
            LabelPartition(self.partition)
        except ValueError as exc:
            raise LabelGovernanceError("evidence contains forbidden partition") from exc
        start = _parse_canonical_utc(self.partition_start, "partition_start")
        end = _parse_canonical_utc(self.partition_end, "partition_end")
        if not start < end:
            raise LabelIntegrityError("evidence partition window is invalid")
        if (
            not isinstance(self.row_count, int)
            or isinstance(self.row_count, bool)
            or self.row_count < 1
        ):
            raise LabelIntegrityError("evidence row_count is invalid")
        if self.horizon_policy != "EXPLICIT_FUTURE_HORIZON":
            raise LabelIntegrityError("evidence horizon policy drifted")
        if self.availability_policy != "AVAILABLE_AT_GE_HORIZON_END":
            raise LabelIntegrityError("evidence availability policy drifted")
        if self.final_holdout_included:
            raise LabelGovernanceError("OSS-3C evidence may not contain FINAL_HOLDOUT")
        if self.execution_authorized or self.paper_execution_authorized:
            raise LabelGovernanceError("OSS-3C evidence cannot authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise LabelGovernanceError("OSS-3C evidence cannot grant capital or LIVE")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    @property
    def qlib_label_dataset_hash(self) -> str:
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
            "label_definition_hash": self.label_definition_hash,
            "source_dataset_hash": self.source_dataset_hash,
            "source_universe_hash": self.source_universe_hash,
            "producer_code_hash": self.producer_code_hash,
            "row_count": self.row_count,
            "horizon_policy": self.horizon_policy,
            "availability_policy": self.availability_policy,
            "final_holdout_included": self.final_holdout_included,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class SupervisedLabelArtifact:
    artifact_version: str
    manifest: LabelManifest
    label: LabelDefinition
    rows: tuple[SupervisedLabelRow, ...]
    artifact_hash: str

    def __post_init__(self) -> None:
        if self.artifact_version != OSS3C_ARTIFACT_VERSION:
            raise LabelIntegrityError("unsupported OSS-3C artifact version")
        _require_hash(self.artifact_hash, "artifact_hash")
        if self.manifest.label_definition_hash != self.label.fingerprint:
            raise LabelIntegrityError("label definition hash mismatch")
        _validate_rows(self.rows, self.manifest)
        expected = _artifact_hash(
            artifact_version=self.artifact_version,
            manifest=self.manifest,
            label=self.label,
            rows=self.rows,
        )
        if expected != self.artifact_hash:
            raise LabelIntegrityError("OSS-3C artifact hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        campaign_id: str,
        research_split_hash: str,
        partition: LabelPartition,
        partition_start: datetime,
        partition_end: datetime,
        producer_code_hash: str,
        source_dataset_hash: str,
        source_universe_hash: str,
        label: LabelDefinition,
        rows: Iterable[SupervisedLabelRow],
    ) -> "SupervisedLabelArtifact":
        if not isinstance(partition, LabelPartition):
            raise TypeError("partition must be LabelPartition")
        if not isinstance(label, LabelDefinition):
            raise TypeError("label must be LabelDefinition")
        row_tuple = tuple(rows)
        manifest = LabelManifest(
            producer_id=OSS3C_PRODUCER_ID,
            producer_code_hash=producer_code_hash,
            campaign_id=campaign_id,
            research_split_hash=research_split_hash,
            source_dataset_hash=source_dataset_hash,
            source_universe_hash=source_universe_hash,
            partition=partition.value,
            partition_start=_canonical_utc(partition_start, "partition_start"),
            partition_end=_canonical_utc(partition_end, "partition_end"),
            row_count=len(row_tuple),
            label_definition_hash=label.fingerprint,
            horizon_policy="EXPLICIT_FUTURE_HORIZON",
            availability_policy="AVAILABLE_AT_GE_HORIZON_END",
            missing_value_policy="FORBID",
            row_payload_hash=_row_payload_hash(row_tuple),
        )
        artifact_hash = _artifact_hash(
            artifact_version=OSS3C_ARTIFACT_VERSION,
            manifest=manifest,
            label=label,
            rows=row_tuple,
        )
        return cls(
            artifact_version=OSS3C_ARTIFACT_VERSION,
            manifest=manifest,
            label=label,
            rows=row_tuple,
            artifact_hash=artifact_hash,
        )

    def to_research_evidence(self) -> LabelEvidence:
        m = self.manifest
        return LabelEvidence(
            evidence_version=OSS3C_EVIDENCE_VERSION,
            artifact_hash=self.artifact_hash,
            manifest_fingerprint=m.fingerprint,
            campaign_id=m.campaign_id,
            research_split_hash=m.research_split_hash,
            partition=m.partition,
            partition_start=m.partition_start,
            partition_end=m.partition_end,
            label_definition_hash=m.label_definition_hash,
            source_dataset_hash=m.source_dataset_hash,
            source_universe_hash=m.source_universe_hash,
            producer_code_hash=m.producer_code_hash,
            row_count=m.row_count,
            horizon_policy=m.horizon_policy,
            availability_policy=m.availability_policy,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_version": self.artifact_version,
            "manifest": self.manifest.to_dict(),
            "label": self.label.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
            "artifact_hash": self.artifact_hash,
        }

    def write(self, path: Path) -> None:
        raw = _canonical_json(self.to_dict()) + b"\n"
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise LabelGovernanceError("OSS-3C artifact exceeds size limit")
        path.write_bytes(raw)

    @classmethod
    def read(cls, path: Path) -> "SupervisedLabelArtifact":
        if not path.is_file():
            raise LabelIntegrityError("OSS-3C artifact does not exist")
        if path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise LabelGovernanceError("OSS-3C artifact exceeds size limit")
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
            data = json.loads(text, object_pairs_hook=_no_duplicate_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LabelIntegrityError("OSS-3C artifact must be valid UTF-8 JSON") from exc
        except LabelIntegrityError:
            raise
        if not isinstance(data, dict):
            raise LabelIntegrityError("OSS-3C top level must be an object")
        expected_bytes = _canonical_json(data) + b"\n"
        if raw != expected_bytes:
            raise LabelIntegrityError("OSS-3C serialization is not canonical")
        return _from_mapping(data)


def _from_mapping(data: Mapping[str, object]) -> SupervisedLabelArtifact:
    _exact_keys(data, _TOP_LEVEL_KEYS, "top-level")
    manifest_raw = data["manifest"]
    label_raw = data["label"]
    rows_raw = data["rows"]
    if not isinstance(manifest_raw, dict):
        raise LabelIntegrityError("manifest must be an object")
    if not isinstance(label_raw, dict):
        raise LabelIntegrityError("label must be an object")
    if not isinstance(rows_raw, list):
        raise LabelIntegrityError("rows must be an array")
    _exact_keys(manifest_raw, _MANIFEST_KEYS, "manifest")
    _exact_keys(label_raw, _LABEL_KEYS, "label")
    try:
        manifest = LabelManifest(**manifest_raw)
        label = LabelDefinition(**label_raw)
    except (TypeError, ValueError, LabelArtifactError) as exc:
        if isinstance(exc, LabelArtifactError):
            raise
        raise LabelIntegrityError(str(exc)) from exc
    rows: list[SupervisedLabelRow] = []
    for raw_row in rows_raw:
        if not isinstance(raw_row, dict):
            raise LabelIntegrityError("row must be an object")
        _exact_keys(raw_row, _ROW_KEYS, "row")
        try:
            rows.append(SupervisedLabelRow(**raw_row))
        except (TypeError, ValueError, LabelArtifactError) as exc:
            if isinstance(exc, LabelArtifactError):
                raise
            raise LabelIntegrityError(str(exc)) from exc
    try:
        return SupervisedLabelArtifact(
            artifact_version=str(data["artifact_version"]),
            manifest=manifest,
            label=label,
            rows=tuple(rows),
            artifact_hash=str(data["artifact_hash"]),
        )
    except (TypeError, ValueError, LabelArtifactError) as exc:
        if isinstance(exc, LabelArtifactError):
            raise
        raise LabelIntegrityError(str(exc)) from exc


def _validate_rows(rows: Sequence[SupervisedLabelRow], manifest: LabelManifest) -> None:
    if len(rows) != manifest.row_count:
        raise LabelIntegrityError("row_count does not match rows")
    if _row_payload_hash(rows) != manifest.row_payload_hash:
        raise LabelIntegrityError("row payload hash mismatch")
    start, end = manifest.partition_bounds
    previous: tuple[str, str] | None = None
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if row.origin_at < start or row.availability_at >= end:
            raise LabelGovernanceError("label row or future information is outside partition")
        key = (row.label_as_of, row.symbol)
        if key in seen:
            raise LabelGovernanceError("duplicate supervised label row")
        if previous is not None and key <= previous:
            raise LabelGovernanceError("label rows must be canonically sorted")
        seen.add(key)
        previous = key


def _artifact_hash(
    *,
    artifact_version: str,
    manifest: LabelManifest,
    label: LabelDefinition,
    rows: Sequence[SupervisedLabelRow],
) -> str:
    return _hash(
        {
            "artifact_version": artifact_version,
            "manifest": manifest.to_dict(),
            "label": label.to_dict(),
            "rows": [row.to_dict() for row in rows],
        }
    )


def _row_payload_hash(rows: Sequence[SupervisedLabelRow]) -> str:
    return _hash([row.to_dict() for row in rows])


def _hash(value: object) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LabelIntegrityError("value is not canonical JSON") from exc


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LabelIntegrityError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], where: str) -> None:
    if set(value) != expected:
        raise LabelIntegrityError(f"OSS-3C {where} schema mismatch")


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase sha256")


def _canonical_utc(value: datetime, name: str) -> str:
    if not _aware(value):
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _parse_canonical_utc(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a timestamp string")
    if value.endswith("Z"):
        raise ValueError(f"{name} must use canonical +00:00 UTC offset")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not valid ISO-8601") from exc
    if not _aware(parsed):
        raise ValueError(f"{name} must be timezone-aware")
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be canonical UTC")
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use canonical ISO-8601 representation")
    return parsed


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
