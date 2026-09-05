"""OSS-3D1 training-bundle and model-training receipt contracts.

Core-side, Qlib-runtime-free binding between verified OSS-3B features,
OSS-3C labels and later OSS-3A prediction artifacts.

V1 is deliberately strict: TRAIN only; exact campaign/frozen split/window/
universe match; exact (timestamp, symbol) pairing; canonical JSON/UTC; both
source-dataset hashes retained; and no execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from .oss3_factor_matrix_artifact import FactorMatrixArtifact
from .oss3_qlib_artifact import QlibPredictionArtifact
from .oss3_supervised_label_artifact import SupervisedLabelArtifact


OSS3D1_ARTIFACT_VERSION = "OSS3D1_TRAINING_BUNDLE_ARTIFACT_V1"
OSS3D1_RECEIPT_VERSION = "OSS3D1_MODEL_TRAINING_RECEIPT_V1"
OSS3D1_PRODUCER_ID = "AUTO-TRADE/OSS3D1_TRAINING_BUNDLE"
PAIRING_POLICY = "EXACT_TIMESTAMP_SYMBOL_KEYSET_V1"
MAX_ARTIFACT_BYTES = 64_000

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_TOP_LEVEL_KEYS = frozenset({"artifact_version", "manifest", "artifact_hash"})
_MANIFEST_KEYS = frozenset(
    {
        "producer_id",
        "campaign_id",
        "research_split_hash",
        "partition",
        "partition_start",
        "partition_end",
        "source_universe_hash",
        "feature_artifact_hash",
        "label_artifact_hash",
        "feature_source_dataset_hash",
        "label_source_dataset_hash",
        "feature_schema_hash",
        "label_definition_hash",
        "sample_count",
        "pairing_policy",
    }
)


class TrainingBundleError(RuntimeError):
    pass


class TrainingBundleIntegrityError(TrainingBundleError):
    pass


class TrainingBundleCompatibilityError(TrainingBundleError):
    pass


@dataclass(frozen=True, slots=True)
class TrainingBundleManifest:
    producer_id: str
    campaign_id: str
    research_split_hash: str
    partition: str
    partition_start: str
    partition_end: str
    source_universe_hash: str
    feature_artifact_hash: str
    label_artifact_hash: str
    feature_source_dataset_hash: str
    label_source_dataset_hash: str
    feature_schema_hash: str
    label_definition_hash: str
    sample_count: int
    pairing_policy: str

    def __post_init__(self) -> None:
        if self.producer_id != OSS3D1_PRODUCER_ID:
            raise TrainingBundleCompatibilityError("noncanonical OSS-3D1 producer")
        if not _ID_RE.fullmatch(self.campaign_id):
            raise ValueError("invalid campaign_id")
        for name, value in (
            ("research_split_hash", self.research_split_hash),
            ("source_universe_hash", self.source_universe_hash),
            ("feature_artifact_hash", self.feature_artifact_hash),
            ("label_artifact_hash", self.label_artifact_hash),
            ("feature_source_dataset_hash", self.feature_source_dataset_hash),
            ("label_source_dataset_hash", self.label_source_dataset_hash),
            ("feature_schema_hash", self.feature_schema_hash),
            ("label_definition_hash", self.label_definition_hash),
        ):
            _require_hash(value, name)
        if self.partition != "TRAIN":
            raise TrainingBundleCompatibilityError("OSS-3D1 V1 accepts TRAIN only")
        start = _parse_canonical_utc(self.partition_start, "partition_start")
        end = _parse_canonical_utc(self.partition_end, "partition_end")
        if not start < end:
            raise TrainingBundleCompatibilityError("training partition window must be positive")
        if (
            not isinstance(self.sample_count, int)
            or isinstance(self.sample_count, bool)
            or self.sample_count < 1
        ):
            raise ValueError("sample_count must be a positive integer")
        if self.pairing_policy != PAIRING_POLICY:
            raise TrainingBundleCompatibilityError("noncanonical OSS-3D1 pairing policy")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "producer_id": self.producer_id,
            "campaign_id": self.campaign_id,
            "research_split_hash": self.research_split_hash,
            "partition": self.partition,
            "partition_start": self.partition_start,
            "partition_end": self.partition_end,
            "source_universe_hash": self.source_universe_hash,
            "feature_artifact_hash": self.feature_artifact_hash,
            "label_artifact_hash": self.label_artifact_hash,
            "feature_source_dataset_hash": self.feature_source_dataset_hash,
            "label_source_dataset_hash": self.label_source_dataset_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "label_definition_hash": self.label_definition_hash,
            "sample_count": self.sample_count,
            "pairing_policy": self.pairing_policy,
        }


@dataclass(frozen=True, slots=True)
class ModelTrainingReceipt:
    receipt_version: str
    training_dataset_hash: str
    training_bundle_manifest_hash: str
    feature_artifact_hash: str
    label_artifact_hash: str
    prediction_artifact_hash: str
    campaign_id: str
    research_split_hash: str
    feature_schema_hash: str
    label_definition_hash: str
    model_family: str
    model_config_hash: str
    qlib_version: str
    producer_code_hash: str
    train_start: str
    train_end: str
    inference_start: str
    inference_end: str
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D1_RECEIPT_VERSION:
            raise TrainingBundleIntegrityError("noncanonical OSS-3D1 receipt version")
        for name, value in (
            ("training_dataset_hash", self.training_dataset_hash),
            ("training_bundle_manifest_hash", self.training_bundle_manifest_hash),
            ("feature_artifact_hash", self.feature_artifact_hash),
            ("label_artifact_hash", self.label_artifact_hash),
            ("prediction_artifact_hash", self.prediction_artifact_hash),
            ("research_split_hash", self.research_split_hash),
            ("feature_schema_hash", self.feature_schema_hash),
            ("label_definition_hash", self.label_definition_hash),
            ("model_config_hash", self.model_config_hash),
            ("producer_code_hash", self.producer_code_hash),
        ):
            _require_hash(value, name)
        if not _ID_RE.fullmatch(self.campaign_id):
            raise TrainingBundleIntegrityError("invalid receipt campaign_id")
        if not _ID_RE.fullmatch(self.model_family):
            raise TrainingBundleIntegrityError("invalid receipt model_family")
        if not isinstance(self.qlib_version, str) or not self.qlib_version:
            raise TrainingBundleIntegrityError("invalid receipt qlib_version")
        train_start = _parse_canonical_utc(self.train_start, "receipt train_start")
        train_end = _parse_canonical_utc(self.train_end, "receipt train_end")
        inference_start = _parse_canonical_utc(self.inference_start, "receipt inference_start")
        inference_end = _parse_canonical_utc(self.inference_end, "receipt inference_end")
        if not train_start < train_end:
            raise TrainingBundleIntegrityError("receipt training window is invalid")
        if train_end > inference_start or not inference_start < inference_end:
            raise TrainingBundleIntegrityError("receipt inference window is invalid")
        if self.execution_authorized or self.paper_execution_authorized:
            raise TrainingBundleCompatibilityError("OSS-3D1 receipt cannot authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise TrainingBundleCompatibilityError("OSS-3D1 receipt cannot grant capital or LIVE")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "receipt_version": self.receipt_version,
            "training_dataset_hash": self.training_dataset_hash,
            "training_bundle_manifest_hash": self.training_bundle_manifest_hash,
            "feature_artifact_hash": self.feature_artifact_hash,
            "label_artifact_hash": self.label_artifact_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "campaign_id": self.campaign_id,
            "research_split_hash": self.research_split_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "label_definition_hash": self.label_definition_hash,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "qlib_version": self.qlib_version,
            "producer_code_hash": self.producer_code_hash,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "inference_start": self.inference_start,
            "inference_end": self.inference_end,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class TrainingBundleArtifact:
    artifact_version: str
    manifest: TrainingBundleManifest
    artifact_hash: str

    def __post_init__(self) -> None:
        if self.artifact_version != OSS3D1_ARTIFACT_VERSION:
            raise TrainingBundleIntegrityError("unsupported OSS-3D1 artifact version")
        _require_hash(self.artifact_hash, "artifact_hash")
        if self.artifact_hash != _artifact_hash(self.artifact_version, self.manifest):
            raise TrainingBundleIntegrityError("OSS-3D1 artifact hash mismatch")

    @property
    def training_dataset_hash(self) -> str:
        return self.artifact_hash

    @classmethod
    def build(
        cls,
        *,
        features: FactorMatrixArtifact,
        labels: SupervisedLabelArtifact,
    ) -> "TrainingBundleArtifact":
        if not isinstance(features, FactorMatrixArtifact):
            raise TypeError("features must be FactorMatrixArtifact")
        if not isinstance(labels, SupervisedLabelArtifact):
            raise TypeError("labels must be SupervisedLabelArtifact")
        fm = features.manifest
        lm = labels.manifest
        _require_equal("campaign_id", fm.campaign_id, lm.campaign_id)
        _require_equal("research_split_hash", fm.research_split_hash, lm.research_split_hash)
        _require_equal("partition", fm.partition, lm.partition)
        if fm.partition != "TRAIN":
            raise TrainingBundleCompatibilityError("OSS-3D1 V1 accepts TRAIN artifacts only")
        _require_equal("partition_start", fm.partition_start, lm.partition_start)
        _require_equal("partition_end", fm.partition_end, lm.partition_end)
        _require_equal("source_universe_hash", fm.source_universe_hash, lm.source_universe_hash)

        feature_keys = tuple((row.as_of, row.symbol) for row in features.rows)
        label_keys = tuple((row.label_as_of, row.symbol) for row in labels.rows)
        if feature_keys != label_keys:
            feature_set = set(feature_keys)
            label_set = set(label_keys)
            raise TrainingBundleCompatibilityError(
                "feature/label keysets differ "
                f"(missing_labels={len(feature_set - label_set)}, "
                f"missing_features={len(label_set - feature_set)})"
            )
        if not feature_keys:
            raise TrainingBundleCompatibilityError("training bundle cannot be empty")

        manifest = TrainingBundleManifest(
            producer_id=OSS3D1_PRODUCER_ID,
            campaign_id=fm.campaign_id,
            research_split_hash=fm.research_split_hash,
            partition=fm.partition,
            partition_start=fm.partition_start,
            partition_end=fm.partition_end,
            source_universe_hash=fm.source_universe_hash,
            feature_artifact_hash=features.artifact_hash,
            label_artifact_hash=labels.artifact_hash,
            feature_source_dataset_hash=fm.source_dataset_hash,
            label_source_dataset_hash=lm.source_dataset_hash,
            feature_schema_hash=fm.feature_schema_hash,
            label_definition_hash=lm.label_definition_hash,
            sample_count=len(feature_keys),
            pairing_policy=PAIRING_POLICY,
        )
        return cls(
            artifact_version=OSS3D1_ARTIFACT_VERSION,
            manifest=manifest,
            artifact_hash=_artifact_hash(OSS3D1_ARTIFACT_VERSION, manifest),
        )

    def bind_prediction(self, prediction: QlibPredictionArtifact) -> ModelTrainingReceipt:
        if not isinstance(prediction, QlibPredictionArtifact):
            raise TypeError("prediction must be QlibPredictionArtifact")
        pm = prediction.manifest
        m = self.manifest
        _require_equal("prediction training_dataset_hash", pm.training_dataset_hash, self.artifact_hash)
        _require_equal("prediction feature_schema_hash", pm.feature_schema_hash, m.feature_schema_hash)
        _require_equal("prediction train_start", pm.train_start, m.partition_start)
        _require_equal("prediction train_end", pm.train_end, m.partition_end)
        return ModelTrainingReceipt(
            receipt_version=OSS3D1_RECEIPT_VERSION,
            training_dataset_hash=self.artifact_hash,
            training_bundle_manifest_hash=m.fingerprint,
            feature_artifact_hash=m.feature_artifact_hash,
            label_artifact_hash=m.label_artifact_hash,
            prediction_artifact_hash=prediction.artifact_hash,
            campaign_id=m.campaign_id,
            research_split_hash=m.research_split_hash,
            feature_schema_hash=m.feature_schema_hash,
            label_definition_hash=m.label_definition_hash,
            model_family=pm.model_family,
            model_config_hash=pm.model_config_hash,
            qlib_version=pm.qlib_version,
            producer_code_hash=pm.producer_code_hash,
            train_start=pm.train_start,
            train_end=pm.train_end,
            inference_start=pm.inference_start,
            inference_end=pm.inference_end,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_version": self.artifact_version,
            "manifest": self.manifest.to_dict(),
            "artifact_hash": self.artifact_hash,
        }

    def write(self, path: Path) -> None:
        raw = _canonical_json(self.to_dict()) + b"\n"
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise TrainingBundleCompatibilityError("OSS-3D1 artifact exceeds size limit")
        path.write_bytes(raw)

    @classmethod
    def read(cls, path: Path) -> "TrainingBundleArtifact":
        if not path.is_file():
            raise TrainingBundleIntegrityError("OSS-3D1 artifact does not exist")
        if path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise TrainingBundleCompatibilityError("OSS-3D1 artifact exceeds size limit")
        try:
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrainingBundleIntegrityError("OSS-3D1 artifact must be valid UTF-8 JSON") from exc
        if not isinstance(data, dict):
            raise TrainingBundleIntegrityError("OSS-3D1 top level must be an object")
        if raw != _canonical_json(data) + b"\n":
            raise TrainingBundleIntegrityError("OSS-3D1 serialization is not canonical")
        _exact_keys(data, _TOP_LEVEL_KEYS, "top-level")
        raw_manifest = data["manifest"]
        if not isinstance(raw_manifest, dict):
            raise TrainingBundleIntegrityError("manifest must be an object")
        _exact_keys(raw_manifest, _MANIFEST_KEYS, "manifest")
        try:
            manifest = TrainingBundleManifest(**raw_manifest)
            return cls(
                artifact_version=str(data["artifact_version"]),
                manifest=manifest,
                artifact_hash=str(data["artifact_hash"]),
            )
        except (TypeError, ValueError, TrainingBundleError) as exc:
            if isinstance(exc, TrainingBundleError):
                raise
            raise TrainingBundleIntegrityError(str(exc)) from exc


def _require_equal(name: str, left: object, right: object) -> None:
    if left != right:
        raise TrainingBundleCompatibilityError(f"{name} mismatch")


def _artifact_hash(version: str, manifest: TrainingBundleManifest) -> str:
    return _hash({"artifact_version": version, "manifest": manifest.to_dict()})


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
        raise TrainingBundleIntegrityError("value is not canonical JSON") from exc


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TrainingBundleIntegrityError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], where: str) -> None:
    if set(value) != expected:
        raise TrainingBundleIntegrityError(f"OSS-3D1 {where} schema mismatch")


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase sha256")


def _parse_canonical_utc(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a timestamp string")
    if value.endswith("Z"):
        raise ValueError(f"{name} must use canonical +00:00 UTC offset")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be canonical UTC")
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use canonical ISO-8601 representation")
    return parsed
