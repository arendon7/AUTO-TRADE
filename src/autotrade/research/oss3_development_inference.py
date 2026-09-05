"""OSS-3D2A DEVELOPMENT inference request and prediction-binding contracts.

This module is core-side and external-runtime-free. It binds an already
verified OSS-3D1 TRAIN bundle to one OSS-3B DEVELOPMENT feature artifact and
produces an immutable request that a future isolated Qlib laboratory may
consume.

Scientific/security boundary:
- TRAIN labels are represented only through the frozen OSS-3D1 bundle hash;
- DEVELOPMENT labels are not an input to this module and are forbidden by
  policy, so inference cannot inspect evaluation targets through this API;
- DEVELOPMENT features must match campaign, frozen split, universe and exact
  feature schema of the TRAIN bundle;
- the complete expected prediction key-set (timestamp, symbol) is hash-bound;
- dry-run and prediction binding revalidate the concrete TRAIN/DEVELOPMENT
  artifacts against the request rather than trusting serialized claims alone;
- a dry run validates the contract only and never fabricates an OSS-3A artifact;
- a real OSS-3A artifact is accepted only when training, model, runtime,
  inference-window and prediction-key identities all match exactly;
- no Qlib runtime import, dynamic code loading, network, subprocess, broker,
  OMS, Safety, OrderIntent, PAPER, capital or LIVE authority exists here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping, Sequence

from .oss3_factor_matrix_artifact import FactorMatrixArtifact
from .oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from .oss3_training_bundle import TrainingBundleArtifact


OSS3D2A_REQUEST_VERSION = "OSS3D2A_DEVELOPMENT_INFERENCE_REQUEST_V1"
OSS3D2A_DRY_RUN_VERSION = "OSS3D2A_DEVELOPMENT_INFERENCE_DRY_RUN_V1"
OSS3D2A_RECEIPT_VERSION = "OSS3D2A_DEVELOPMENT_PREDICTION_RECEIPT_V1"
OSS3D2A_PRODUCER_ID = "AUTO-TRADE/OSS3D2A_DEVELOPMENT_INFERENCE"
PREDICTION_KEY_POLICY = "EXACT_TIMESTAMP_SYMBOL_KEYSET_V1"
LABEL_ACCESS_POLICY = "FORBID_DEVELOPMENT_LABELS_V1"
DEVELOPMENT_POINT_IN_TIME_POLICY = "AVAILABLE_AT_LE_AS_OF"
MAX_REQUEST_BYTES = 96_000

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

_TOP_LEVEL_KEYS = frozenset({"request_version", "manifest", "request_hash"})
_MANIFEST_KEYS = frozenset(
    {
        "producer_id",
        "campaign_id",
        "research_split_hash",
        "training_bundle_hash",
        "training_bundle_manifest_hash",
        "train_feature_artifact_hash",
        "train_label_artifact_hash",
        "source_universe_hash",
        "feature_schema_hash",
        "label_definition_hash",
        "train_start",
        "train_end",
        "development_partition",
        "development_feature_artifact_hash",
        "development_source_dataset_hash",
        "development_row_payload_hash",
        "development_point_in_time_policy",
        "inference_start",
        "inference_end",
        "inference_row_count",
        "inference_keyset_hash",
        "model_family",
        "model_config_hash",
        "required_qlib_version",
        "expected_runner_code_hash",
        "prediction_key_policy",
        "label_access_policy",
    }
)


class DevelopmentInferenceError(RuntimeError):
    """Base OSS-3D2A failure."""


class DevelopmentInferenceIntegrityError(DevelopmentInferenceError):
    """Serialized or downstream evidence does not match its claimed identity."""


class DevelopmentInferenceGovernanceError(DevelopmentInferenceError):
    """The request violates the research-only or no-label inference boundary."""


class DevelopmentInferenceCompatibilityError(DevelopmentInferenceError):
    """TRAIN, DEVELOPMENT or prediction identities cannot be safely combined."""


@dataclass(frozen=True, slots=True)
class DevelopmentInferenceManifest:
    producer_id: str
    campaign_id: str
    research_split_hash: str
    training_bundle_hash: str
    training_bundle_manifest_hash: str
    train_feature_artifact_hash: str
    train_label_artifact_hash: str
    source_universe_hash: str
    feature_schema_hash: str
    label_definition_hash: str
    train_start: str
    train_end: str
    development_partition: str
    development_feature_artifact_hash: str
    development_source_dataset_hash: str
    development_row_payload_hash: str
    development_point_in_time_policy: str
    inference_start: str
    inference_end: str
    inference_row_count: int
    inference_keyset_hash: str
    model_family: str
    model_config_hash: str
    required_qlib_version: str
    expected_runner_code_hash: str
    prediction_key_policy: str
    label_access_policy: str

    def __post_init__(self) -> None:
        if self.producer_id != OSS3D2A_PRODUCER_ID:
            raise DevelopmentInferenceGovernanceError("noncanonical OSS-3D2A producer")
        if not _ID_RE.fullmatch(self.campaign_id):
            raise ValueError("invalid campaign_id")
        for name, value in (
            ("research_split_hash", self.research_split_hash),
            ("training_bundle_hash", self.training_bundle_hash),
            ("training_bundle_manifest_hash", self.training_bundle_manifest_hash),
            ("train_feature_artifact_hash", self.train_feature_artifact_hash),
            ("train_label_artifact_hash", self.train_label_artifact_hash),
            ("source_universe_hash", self.source_universe_hash),
            ("feature_schema_hash", self.feature_schema_hash),
            ("label_definition_hash", self.label_definition_hash),
            ("development_feature_artifact_hash", self.development_feature_artifact_hash),
            ("development_source_dataset_hash", self.development_source_dataset_hash),
            ("development_row_payload_hash", self.development_row_payload_hash),
            ("inference_keyset_hash", self.inference_keyset_hash),
            ("model_config_hash", self.model_config_hash),
            ("expected_runner_code_hash", self.expected_runner_code_hash),
        ):
            _require_hash(value, name)
        if self.development_partition != "DEVELOPMENT":
            raise DevelopmentInferenceGovernanceError(
                "OSS-3D2A accepts DEVELOPMENT feature artifacts only"
            )
        if self.development_point_in_time_policy != DEVELOPMENT_POINT_IN_TIME_POLICY:
            raise DevelopmentInferenceGovernanceError(
                "noncanonical DEVELOPMENT point-in-time policy"
            )
        if not _ID_RE.fullmatch(self.model_family):
            raise ValueError("invalid model_family")
        if not _VERSION_RE.fullmatch(self.required_qlib_version):
            raise ValueError("invalid required_qlib_version")
        train_start = _parse_canonical_utc(self.train_start, "train_start")
        train_end = _parse_canonical_utc(self.train_end, "train_end")
        inference_start = _parse_canonical_utc(self.inference_start, "inference_start")
        inference_end = _parse_canonical_utc(self.inference_end, "inference_end")
        if not train_start < train_end:
            raise DevelopmentInferenceCompatibilityError("training window must be positive")
        if train_end > inference_start:
            raise DevelopmentInferenceCompatibilityError(
                "TRAIN window may not overlap DEVELOPMENT inference window"
            )
        if not inference_start < inference_end:
            raise DevelopmentInferenceCompatibilityError("inference window must be positive")
        if (
            not isinstance(self.inference_row_count, int)
            or isinstance(self.inference_row_count, bool)
            or self.inference_row_count < 1
        ):
            raise ValueError("inference_row_count must be a positive integer")
        if self.prediction_key_policy != PREDICTION_KEY_POLICY:
            raise DevelopmentInferenceGovernanceError(
                "noncanonical OSS-3D2A prediction-key policy"
            )
        if self.label_access_policy != LABEL_ACCESS_POLICY:
            raise DevelopmentInferenceGovernanceError(
                "OSS-3D2A DEVELOPMENT label-access policy must forbid labels"
            )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "producer_id": self.producer_id,
            "campaign_id": self.campaign_id,
            "research_split_hash": self.research_split_hash,
            "training_bundle_hash": self.training_bundle_hash,
            "training_bundle_manifest_hash": self.training_bundle_manifest_hash,
            "train_feature_artifact_hash": self.train_feature_artifact_hash,
            "train_label_artifact_hash": self.train_label_artifact_hash,
            "source_universe_hash": self.source_universe_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "label_definition_hash": self.label_definition_hash,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "development_partition": self.development_partition,
            "development_feature_artifact_hash": self.development_feature_artifact_hash,
            "development_source_dataset_hash": self.development_source_dataset_hash,
            "development_row_payload_hash": self.development_row_payload_hash,
            "development_point_in_time_policy": self.development_point_in_time_policy,
            "inference_start": self.inference_start,
            "inference_end": self.inference_end,
            "inference_row_count": self.inference_row_count,
            "inference_keyset_hash": self.inference_keyset_hash,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "required_qlib_version": self.required_qlib_version,
            "expected_runner_code_hash": self.expected_runner_code_hash,
            "prediction_key_policy": self.prediction_key_policy,
            "label_access_policy": self.label_access_policy,
        }


@dataclass(frozen=True, slots=True)
class DevelopmentInferenceDryRunEvidence:
    evidence_version: str
    request_hash: str
    request_manifest_hash: str
    campaign_id: str
    research_split_hash: str
    training_bundle_hash: str
    development_feature_artifact_hash: str
    feature_schema_hash: str
    inference_keyset_hash: str
    inference_row_count: int
    label_access_policy: str
    development_labels_loaded: bool = False
    final_holdout_loaded: bool = False
    external_runtime_invoked: bool = False
    qlib_imported: bool = False
    prediction_artifact_created: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D2A_DRY_RUN_VERSION:
            raise DevelopmentInferenceIntegrityError("noncanonical OSS-3D2A dry-run version")
        if not _ID_RE.fullmatch(self.campaign_id):
            raise DevelopmentInferenceIntegrityError("invalid dry-run campaign_id")
        for name, value in (
            ("request_hash", self.request_hash),
            ("request_manifest_hash", self.request_manifest_hash),
            ("research_split_hash", self.research_split_hash),
            ("training_bundle_hash", self.training_bundle_hash),
            ("development_feature_artifact_hash", self.development_feature_artifact_hash),
            ("feature_schema_hash", self.feature_schema_hash),
            ("inference_keyset_hash", self.inference_keyset_hash),
        ):
            _require_hash(value, name)
        if (
            not isinstance(self.inference_row_count, int)
            or isinstance(self.inference_row_count, bool)
            or self.inference_row_count < 1
        ):
            raise DevelopmentInferenceIntegrityError("invalid dry-run inference_row_count")
        if self.label_access_policy != LABEL_ACCESS_POLICY:
            raise DevelopmentInferenceGovernanceError("dry run may not weaken label policy")
        if self.development_labels_loaded or self.final_holdout_loaded:
            raise DevelopmentInferenceGovernanceError(
                "OSS-3D2A dry run cannot load DEVELOPMENT labels or FINAL_HOLDOUT"
            )
        if self.external_runtime_invoked or self.qlib_imported or self.prediction_artifact_created:
            raise DevelopmentInferenceGovernanceError(
                "OSS-3D2A dry run cannot invoke Qlib or fabricate prediction provenance"
            )
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
            context="dry run",
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_version": self.evidence_version,
            "request_hash": self.request_hash,
            "request_manifest_hash": self.request_manifest_hash,
            "campaign_id": self.campaign_id,
            "research_split_hash": self.research_split_hash,
            "training_bundle_hash": self.training_bundle_hash,
            "development_feature_artifact_hash": self.development_feature_artifact_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "inference_keyset_hash": self.inference_keyset_hash,
            "inference_row_count": self.inference_row_count,
            "label_access_policy": self.label_access_policy,
            "development_labels_loaded": self.development_labels_loaded,
            "final_holdout_loaded": self.final_holdout_loaded,
            "external_runtime_invoked": self.external_runtime_invoked,
            "qlib_imported": self.qlib_imported,
            "prediction_artifact_created": self.prediction_artifact_created,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class DevelopmentPredictionReceipt:
    receipt_version: str
    request_hash: str
    request_manifest_hash: str
    prediction_artifact_hash: str
    prediction_manifest_hash: str
    campaign_id: str
    research_split_hash: str
    training_bundle_hash: str
    development_feature_artifact_hash: str
    source_universe_hash: str
    feature_schema_hash: str
    label_definition_hash: str
    inference_keyset_hash: str
    prediction_count: int
    model_family: str
    model_config_hash: str
    qlib_version: str
    producer_code_hash: str
    train_start: str
    train_end: str
    inference_start: str
    inference_end: str
    development_labels_loaded: bool = False
    final_holdout_loaded: bool = False
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D2A_RECEIPT_VERSION:
            raise DevelopmentInferenceIntegrityError("noncanonical OSS-3D2A receipt version")
        if not _ID_RE.fullmatch(self.campaign_id):
            raise DevelopmentInferenceIntegrityError("invalid receipt campaign_id")
        if not _ID_RE.fullmatch(self.model_family):
            raise DevelopmentInferenceIntegrityError("invalid receipt model_family")
        if not _VERSION_RE.fullmatch(self.qlib_version):
            raise DevelopmentInferenceIntegrityError("invalid receipt qlib_version")
        for name, value in (
            ("request_hash", self.request_hash),
            ("request_manifest_hash", self.request_manifest_hash),
            ("prediction_artifact_hash", self.prediction_artifact_hash),
            ("prediction_manifest_hash", self.prediction_manifest_hash),
            ("research_split_hash", self.research_split_hash),
            ("training_bundle_hash", self.training_bundle_hash),
            ("development_feature_artifact_hash", self.development_feature_artifact_hash),
            ("source_universe_hash", self.source_universe_hash),
            ("feature_schema_hash", self.feature_schema_hash),
            ("label_definition_hash", self.label_definition_hash),
            ("inference_keyset_hash", self.inference_keyset_hash),
            ("model_config_hash", self.model_config_hash),
            ("producer_code_hash", self.producer_code_hash),
        ):
            _require_hash(value, name)
        if (
            not isinstance(self.prediction_count, int)
            or isinstance(self.prediction_count, bool)
            or self.prediction_count < 1
        ):
            raise DevelopmentInferenceIntegrityError("invalid prediction_count")
        train_start = _parse_canonical_utc(self.train_start, "receipt train_start")
        train_end = _parse_canonical_utc(self.train_end, "receipt train_end")
        inference_start = _parse_canonical_utc(self.inference_start, "receipt inference_start")
        inference_end = _parse_canonical_utc(self.inference_end, "receipt inference_end")
        if not train_start < train_end or train_end > inference_start:
            raise DevelopmentInferenceIntegrityError("invalid receipt train/inference boundary")
        if not inference_start < inference_end:
            raise DevelopmentInferenceIntegrityError("invalid receipt inference window")
        if self.development_labels_loaded or self.final_holdout_loaded:
            raise DevelopmentInferenceGovernanceError(
                "prediction receipt cannot claim DEVELOPMENT label or FINAL_HOLDOUT access"
            )
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
            context="prediction receipt",
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "receipt_version": self.receipt_version,
            "request_hash": self.request_hash,
            "request_manifest_hash": self.request_manifest_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "prediction_manifest_hash": self.prediction_manifest_hash,
            "campaign_id": self.campaign_id,
            "research_split_hash": self.research_split_hash,
            "training_bundle_hash": self.training_bundle_hash,
            "development_feature_artifact_hash": self.development_feature_artifact_hash,
            "source_universe_hash": self.source_universe_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "label_definition_hash": self.label_definition_hash,
            "inference_keyset_hash": self.inference_keyset_hash,
            "prediction_count": self.prediction_count,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "qlib_version": self.qlib_version,
            "producer_code_hash": self.producer_code_hash,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "inference_start": self.inference_start,
            "inference_end": self.inference_end,
            "development_labels_loaded": self.development_labels_loaded,
            "final_holdout_loaded": self.final_holdout_loaded,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class DevelopmentInferenceRequest:
    request_version: str
    manifest: DevelopmentInferenceManifest
    request_hash: str

    def __post_init__(self) -> None:
        if self.request_version != OSS3D2A_REQUEST_VERSION:
            raise DevelopmentInferenceIntegrityError("unsupported OSS-3D2A request version")
        _require_hash(self.request_hash, "request_hash")
        if self.request_hash != _request_hash(self.request_version, self.manifest):
            raise DevelopmentInferenceIntegrityError("OSS-3D2A request hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        training_bundle: TrainingBundleArtifact,
        development_features: FactorMatrixArtifact,
        model_family: str,
        model_config_hash: str,
        required_qlib_version: str,
        expected_runner_code_hash: str,
    ) -> "DevelopmentInferenceRequest":
        if not isinstance(training_bundle, TrainingBundleArtifact):
            raise TypeError("training_bundle must be TrainingBundleArtifact")
        if not isinstance(development_features, FactorMatrixArtifact):
            raise TypeError("development_features must be FactorMatrixArtifact")
        bm = training_bundle.manifest
        dm = development_features.manifest
        if bm.partition != "TRAIN":
            raise DevelopmentInferenceCompatibilityError("training bundle must be TRAIN")
        if dm.partition != "DEVELOPMENT":
            raise DevelopmentInferenceGovernanceError(
                "OSS-3D2A accepts DEVELOPMENT feature artifacts only"
            )
        _require_equal("campaign_id", bm.campaign_id, dm.campaign_id)
        _require_equal("research_split_hash", bm.research_split_hash, dm.research_split_hash)
        _require_equal("source_universe_hash", bm.source_universe_hash, dm.source_universe_hash)
        _require_equal("feature_schema_hash", bm.feature_schema_hash, dm.feature_schema_hash)
        train_end = _parse_canonical_utc(bm.partition_end, "TRAIN partition_end")
        inference_start = _parse_canonical_utc(dm.partition_start, "DEVELOPMENT partition_start")
        if train_end > inference_start:
            raise DevelopmentInferenceCompatibilityError(
                "TRAIN window may not overlap DEVELOPMENT inference window"
            )
        _require_hash(model_config_hash, "model_config_hash")
        _require_hash(expected_runner_code_hash, "expected_runner_code_hash")
        if not _ID_RE.fullmatch(model_family):
            raise ValueError("invalid model_family")
        if not _VERSION_RE.fullmatch(required_qlib_version):
            raise ValueError("invalid required_qlib_version")
        manifest = DevelopmentInferenceManifest(
            producer_id=OSS3D2A_PRODUCER_ID,
            campaign_id=bm.campaign_id,
            research_split_hash=bm.research_split_hash,
            training_bundle_hash=training_bundle.artifact_hash,
            training_bundle_manifest_hash=bm.fingerprint,
            train_feature_artifact_hash=bm.feature_artifact_hash,
            train_label_artifact_hash=bm.label_artifact_hash,
            source_universe_hash=bm.source_universe_hash,
            feature_schema_hash=bm.feature_schema_hash,
            label_definition_hash=bm.label_definition_hash,
            train_start=bm.partition_start,
            train_end=bm.partition_end,
            development_partition=dm.partition,
            development_feature_artifact_hash=development_features.artifact_hash,
            development_source_dataset_hash=dm.source_dataset_hash,
            development_row_payload_hash=dm.row_payload_hash,
            development_point_in_time_policy=dm.point_in_time_policy,
            inference_start=dm.partition_start,
            inference_end=dm.partition_end,
            inference_row_count=dm.row_count,
            inference_keyset_hash=_factor_keyset_hash(development_features),
            model_family=model_family,
            model_config_hash=model_config_hash,
            required_qlib_version=required_qlib_version,
            expected_runner_code_hash=expected_runner_code_hash,
            prediction_key_policy=PREDICTION_KEY_POLICY,
            label_access_policy=LABEL_ACCESS_POLICY,
        )
        return cls(
            request_version=OSS3D2A_REQUEST_VERSION,
            manifest=manifest,
            request_hash=_request_hash(OSS3D2A_REQUEST_VERSION, manifest),
        )

    def verify_inputs(
        self,
        *,
        training_bundle: TrainingBundleArtifact,
        development_features: FactorMatrixArtifact,
    ) -> None:
        """Rebind a serialized request to the concrete artifacts it references."""
        if not isinstance(training_bundle, TrainingBundleArtifact):
            raise TypeError("training_bundle must be TrainingBundleArtifact")
        if not isinstance(development_features, FactorMatrixArtifact):
            raise TypeError("development_features must be FactorMatrixArtifact")
        m = self.manifest
        bm = training_bundle.manifest
        dm = development_features.manifest
        if bm.partition != "TRAIN":
            raise DevelopmentInferenceCompatibilityError("training bundle must be TRAIN")
        if dm.partition != "DEVELOPMENT":
            raise DevelopmentInferenceGovernanceError(
                "OSS-3D2A accepts DEVELOPMENT feature artifacts only"
            )
        for name, expected, actual in (
            ("training_bundle_hash", m.training_bundle_hash, training_bundle.artifact_hash),
            ("training_bundle_manifest_hash", m.training_bundle_manifest_hash, bm.fingerprint),
            ("train_feature_artifact_hash", m.train_feature_artifact_hash, bm.feature_artifact_hash),
            ("train_label_artifact_hash", m.train_label_artifact_hash, bm.label_artifact_hash),
            ("campaign_id", m.campaign_id, bm.campaign_id),
            ("research_split_hash", m.research_split_hash, bm.research_split_hash),
            ("source_universe_hash", m.source_universe_hash, bm.source_universe_hash),
            ("feature_schema_hash", m.feature_schema_hash, bm.feature_schema_hash),
            ("label_definition_hash", m.label_definition_hash, bm.label_definition_hash),
            ("train_start", m.train_start, bm.partition_start),
            ("train_end", m.train_end, bm.partition_end),
            ("development_partition", m.development_partition, dm.partition),
            (
                "development_feature_artifact_hash",
                m.development_feature_artifact_hash,
                development_features.artifact_hash,
            ),
            (
                "development_source_dataset_hash",
                m.development_source_dataset_hash,
                dm.source_dataset_hash,
            ),
            ("development_row_payload_hash", m.development_row_payload_hash, dm.row_payload_hash),
            (
                "development_point_in_time_policy",
                m.development_point_in_time_policy,
                dm.point_in_time_policy,
            ),
            ("DEVELOPMENT campaign_id", m.campaign_id, dm.campaign_id),
            ("DEVELOPMENT research_split_hash", m.research_split_hash, dm.research_split_hash),
            ("DEVELOPMENT source_universe_hash", m.source_universe_hash, dm.source_universe_hash),
            ("DEVELOPMENT feature_schema_hash", m.feature_schema_hash, dm.feature_schema_hash),
            ("inference_start", m.inference_start, dm.partition_start),
            ("inference_end", m.inference_end, dm.partition_end),
            ("inference_row_count", m.inference_row_count, dm.row_count),
            ("inference_keyset_hash", m.inference_keyset_hash, _factor_keyset_hash(development_features)),
        ):
            _require_equal(name, expected, actual)

    def dry_run(
        self,
        *,
        training_bundle: TrainingBundleArtifact,
        development_features: FactorMatrixArtifact,
    ) -> DevelopmentInferenceDryRunEvidence:
        """Validate readiness without invoking or impersonating an external runtime."""
        self.verify_inputs(
            training_bundle=training_bundle,
            development_features=development_features,
        )
        m = self.manifest
        return DevelopmentInferenceDryRunEvidence(
            evidence_version=OSS3D2A_DRY_RUN_VERSION,
            request_hash=self.request_hash,
            request_manifest_hash=m.fingerprint,
            campaign_id=m.campaign_id,
            research_split_hash=m.research_split_hash,
            training_bundle_hash=m.training_bundle_hash,
            development_feature_artifact_hash=m.development_feature_artifact_hash,
            feature_schema_hash=m.feature_schema_hash,
            inference_keyset_hash=m.inference_keyset_hash,
            inference_row_count=m.inference_row_count,
            label_access_policy=m.label_access_policy,
        )

    def bind_prediction(
        self,
        *,
        prediction: QlibPredictionArtifact,
        training_bundle: TrainingBundleArtifact,
        development_features: FactorMatrixArtifact,
    ) -> DevelopmentPredictionReceipt:
        """Bind a *real* verified OSS-3A result; this method never runs Qlib."""
        self.verify_inputs(
            training_bundle=training_bundle,
            development_features=development_features,
        )
        if not isinstance(prediction, QlibPredictionArtifact):
            raise TypeError("prediction must be QlibPredictionArtifact")
        pm = prediction.manifest
        m = self.manifest
        _require_equal("prediction training_dataset_hash", m.training_bundle_hash, pm.training_dataset_hash)
        _require_equal("prediction feature_schema_hash", m.feature_schema_hash, pm.feature_schema_hash)
        _require_equal("prediction model_family", m.model_family, pm.model_family)
        _require_equal("prediction model_config_hash", m.model_config_hash, pm.model_config_hash)
        _require_equal("prediction qlib_version", m.required_qlib_version, pm.qlib_version)
        _require_equal("prediction producer_code_hash", m.expected_runner_code_hash, pm.producer_code_hash)
        _require_equal("prediction train_start", m.train_start, pm.train_start)
        _require_equal("prediction train_end", m.train_end, pm.train_end)
        _require_equal("prediction inference_start", m.inference_start, pm.inference_start)
        _require_equal("prediction inference_end", m.inference_end, pm.inference_end)
        if pm.prediction_count != m.inference_row_count:
            raise DevelopmentInferenceCompatibilityError("prediction_count mismatch")
        if _prediction_keyset_hash(prediction.rows) != m.inference_keyset_hash:
            raise DevelopmentInferenceCompatibilityError("prediction keyset mismatch")
        return DevelopmentPredictionReceipt(
            receipt_version=OSS3D2A_RECEIPT_VERSION,
            request_hash=self.request_hash,
            request_manifest_hash=m.fingerprint,
            prediction_artifact_hash=prediction.artifact_hash,
            prediction_manifest_hash=pm.fingerprint,
            campaign_id=m.campaign_id,
            research_split_hash=m.research_split_hash,
            training_bundle_hash=m.training_bundle_hash,
            development_feature_artifact_hash=m.development_feature_artifact_hash,
            source_universe_hash=m.source_universe_hash,
            feature_schema_hash=m.feature_schema_hash,
            label_definition_hash=m.label_definition_hash,
            inference_keyset_hash=m.inference_keyset_hash,
            prediction_count=pm.prediction_count,
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
            "request_version": self.request_version,
            "manifest": self.manifest.to_dict(),
            "request_hash": self.request_hash,
        }

    def write(self, path: str | Path) -> None:
        target = Path(path)
        raw = _canonical_json(self.to_dict()) + b"\n"
        if len(raw) > MAX_REQUEST_BYTES:
            raise DevelopmentInferenceGovernanceError("OSS-3D2A request exceeds size limit")
        target.write_bytes(raw)

    @classmethod
    def read(cls, path: str | Path) -> "DevelopmentInferenceRequest":
        target = Path(path)
        if not target.is_file():
            raise DevelopmentInferenceIntegrityError("OSS-3D2A request does not exist")
        if target.stat().st_size > MAX_REQUEST_BYTES:
            raise DevelopmentInferenceGovernanceError("OSS-3D2A request exceeds size limit")
        try:
            raw = target.read_bytes()
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_object)
        except DevelopmentInferenceError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DevelopmentInferenceIntegrityError(
                "OSS-3D2A request must be valid UTF-8 JSON"
            ) from exc
        if not isinstance(document, dict):
            raise DevelopmentInferenceIntegrityError("OSS-3D2A top level must be an object")
        if raw != _canonical_json(document) + b"\n":
            raise DevelopmentInferenceIntegrityError("OSS-3D2A serialization is not canonical")
        _exact_keys(document, _TOP_LEVEL_KEYS, "top-level")
        raw_manifest = document["manifest"]
        if not isinstance(raw_manifest, dict):
            raise DevelopmentInferenceIntegrityError("manifest must be an object")
        _exact_keys(raw_manifest, _MANIFEST_KEYS, "manifest")
        try:
            manifest = DevelopmentInferenceManifest(**raw_manifest)
            request_version = document["request_version"]
            request_hash = document["request_hash"]
            if not isinstance(request_version, str) or not isinstance(request_hash, str):
                raise TypeError("request identity fields must be strings")
            return cls(
                request_version=request_version,
                manifest=manifest,
                request_hash=request_hash,
            )
        except DevelopmentInferenceError:
            raise
        except (TypeError, ValueError) as exc:
            raise DevelopmentInferenceIntegrityError("OSS-3D2A request fields are invalid") from exc


def _factor_keyset_hash(artifact: FactorMatrixArtifact) -> str:
    return _hash_keyset(tuple((row.as_of, row.symbol) for row in artifact.rows))


def _prediction_keyset_hash(rows: Sequence[QlibPredictionRow]) -> str:
    return _hash_keyset(tuple((row.timestamp, row.symbol) for row in rows))


def _hash_keyset(pairs: Sequence[tuple[str, str]]) -> str:
    return sha256(_canonical_json([[timestamp, symbol] for timestamp, symbol in pairs])).hexdigest()


def _request_hash(version: str, manifest: DevelopmentInferenceManifest) -> str:
    return _hash({"request_version": version, "manifest": manifest.to_dict()})


def _hash(value: object) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase sha256 hex string")


def _require_equal(name: str, expected: object, actual: object) -> None:
    if expected != actual:
        raise DevelopmentInferenceCompatibilityError(f"{name} mismatch")


def _parse_canonical_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a canonical UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat()
    if value != canonical:
        raise ValueError(f"{name} must use canonical UTC serialization")
    return parsed


def _exact_keys(data: Mapping[str, object], expected: frozenset[str], context: str) -> None:
    if frozenset(data) != expected:
        raise DevelopmentInferenceIntegrityError(f"OSS-3D2A {context} schema mismatch")


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DevelopmentInferenceIntegrityError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _deny_authority(
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
    *,
    context: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise DevelopmentInferenceGovernanceError(
            f"OSS-3D2A {context} cannot authorize execution"
        )
    if capital_authority != "NONE" or live_trading != "BLOCKED":
        raise DevelopmentInferenceGovernanceError(
            f"OSS-3D2A {context} cannot grant capital or LIVE"
        )
