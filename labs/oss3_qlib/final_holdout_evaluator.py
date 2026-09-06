"""OSS-3D2K single-use predictive FINAL_HOLDOUT evaluator.

D2K is the first OSS-3 frontier allowed to expose protected FINAL_HOLDOUT
outcomes.  It consumes the exact D2J authorization identity durably before
checkout, proves that the protected wrapper can see that durable consumption,
replays the winner's original D2A TRAIN bundle without refitting on
DEVELOPMENT, requires the exact source D2G runtime environment, executes the
frozen model under the pinned Qlib runtime, and evaluates only the
D2J-preregistered Rank-IC gates.

Any failure after durable authorization consumption is terminal.  PASS is
predictive evidence only: D2K grants no profitability, promotion, broker,
PAPER, capital or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from math import comb, isfinite, sqrt
import os
from pathlib import Path
import re
import sqlite3
from typing import Mapping, Sequence

import pandas as pd

from autotrade.research.oss3_development_inference import DevelopmentInferenceRequest
from autotrade.research.oss3_factor_matrix_artifact import FactorMatrixArtifact
from autotrade.research.oss3_supervised_label_artifact import SupervisedLabelArtifact
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact
from autotrade.research.registry import HoldoutPermit, SQLiteExperimentRegistry

from .family_environment_attestation import (
    CandidateEnvironmentAttestation,
    collect_candidate_environment_attestation,
)
from .family_model_contract import (
    QLIB_VERSION,
    candidate_from_config_hash,
    candidate_runtime_config,
    family_runner_code_hash,
)
from .final_holdout_protocol import (
    FINAL_VALIDATION_PURPOSE,
    OSS3D2J_COMMITMENT_VERSION,
    OSS3D2J_CONTRACT_VERSION,
    OSS3FinalHoldoutProtocolReceipt,
    OSS3ProtectedFinalHoldoutCommitment,
)
from .network_guard import deny_network


OSS3D2K_MATERIAL_VERSION = "OSS3D2K_PROTECTED_FINAL_HOLDOUT_MATERIAL_V1"
OSS3D2K_FEATURE_ARTIFACT_VERSION = "OSS3D2K_PROTECTED_FINAL_FEATURE_ARTIFACT_V1"
OSS3D2K_LABEL_ARTIFACT_VERSION = "OSS3D2K_PROTECTED_FINAL_LABEL_ARTIFACT_V1"
OSS3D2K_START_VERSION = "OSS3D2K_FINAL_HOLDOUT_START_V1"
OSS3D2K_RECEIPT_VERSION = "OSS3D2K_FINAL_HOLDOUT_EVALUATION_V1"
_ISSUED_BY = "OSS3D2K_FINAL_HOLDOUT_EVALUATOR"

MAX_HOLDOUT_ROWS = 2_000_000
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9._:/-]{1,64}$")

SENSITIVE_ENV_PREFIXES = (
    "APCA_",
    "ALPACA_",
    "IBKR_",
    "BINANCE_",
    "COINBASE_",
    "KRAKEN_",
    "BYBIT_",
    "OKX_",
    "BITGET_",
    "KUCOIN_",
    "BROKER_",
)

# Every source that can alter protocol interpretation, source-runtime identity,
# TRAIN replay, protected-material validation, network isolation or Qlib
# execution is bound into the D2K semantic identity.
SEMANTIC_FILES = (
    "labs/oss3_qlib/final_holdout_evaluator.py",
    "labs/oss3_qlib/final_holdout_protocol.py",
    "labs/oss3_qlib/family_model_contract.py",
    "labs/oss3_qlib/family_environment_attestation.py",
    "labs/oss3_qlib/environment_attestation.py",
    "labs/oss3_qlib/network_guard.py",
    "labs/oss3_qlib/requirements.txt",
    "src/autotrade/research/oss3_development_inference.py",
    "src/autotrade/research/oss3_factor_matrix_artifact.py",
    "src/autotrade/research/oss3_supervised_label_artifact.py",
    "src/autotrade/research/oss3_training_bundle.py",
)


class OSS3FinalHoldoutEvaluationError(RuntimeError):
    """Base D2K evaluation failure."""


class OSS3FinalHoldoutEvaluationIntegrityError(OSS3FinalHoldoutEvaluationError):
    """Frozen identity or durable evidence drifted."""


class OSS3FinalHoldoutEvaluationGovernanceError(OSS3FinalHoldoutEvaluationError):
    """Operation violates one-shot FINAL_HOLDOUT governance."""


class OSS3FinalHoldoutAlreadyConsumed(OSS3FinalHoldoutEvaluationError):
    """The D2J authorization or protected holdout is already spent."""


class OSS3FinalHoldoutDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class FinalHoldoutFeatureRow:
    as_of: str
    available_at: str
    symbol: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        as_of = _parse_canonical_utc(self.as_of, "feature as_of")
        available = _parse_canonical_utc(self.available_at, "feature available_at")
        if available > as_of:
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "FINAL_HOLDOUT feature is not point-in-time available"
            )
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("invalid FINAL_HOLDOUT feature symbol")
        if not isinstance(self.values, tuple) or not self.values:
            raise ValueError("FINAL_HOLDOUT feature values must be a non-empty tuple")
        for value in self.values:
            _require_finite(value, "FINAL_HOLDOUT feature value")

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of,
            "available_at": self.available_at,
            "symbol": self.symbol,
            "values": [float(value) for value in self.values],
        }


@dataclass(frozen=True, slots=True)
class FinalHoldoutLabelRow:
    label_as_of: str
    horizon_end: str
    available_at: str
    symbol: str
    value: float

    def __post_init__(self) -> None:
        origin = _parse_canonical_utc(self.label_as_of, "label_as_of")
        horizon = _parse_canonical_utc(self.horizon_end, "horizon_end")
        available = _parse_canonical_utc(self.available_at, "label available_at")
        if not origin < horizon:
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "FINAL_HOLDOUT label horizon must be future"
            )
        if available < horizon:
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "FINAL_HOLDOUT label cannot be available before horizon_end"
            )
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("invalid FINAL_HOLDOUT label symbol")
        _require_finite(self.value, "FINAL_HOLDOUT label value")

    def to_dict(self) -> dict[str, object]:
        return {
            "label_as_of": self.label_as_of,
            "horizon_end": self.horizon_end,
            "available_at": self.available_at,
            "symbol": self.symbol,
            "value": float(self.value),
        }


@dataclass(frozen=True, slots=True)
class OSS3FinalHoldoutMaterial:
    """Canonical protected features + labels; never returned before checkout."""

    material_version: str
    source_campaign_id: str
    research_split_hash: str
    source_universe_hash: str
    feature_schema_hash: str
    label_definition_hash: str
    feature_source_dataset_hash: str
    label_source_dataset_hash: str
    partition_start: str
    partition_end: str
    feature_names: tuple[str, ...]
    feature_rows: tuple[FinalHoldoutFeatureRow, ...]
    label_rows: tuple[FinalHoldoutLabelRow, ...]

    def __post_init__(self) -> None:
        if self.material_version != OSS3D2K_MATERIAL_VERSION:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "noncanonical D2K material version"
            )
        _require_id(self.source_campaign_id, "source_campaign_id")
        for name in (
            "research_split_hash",
            "source_universe_hash",
            "feature_schema_hash",
            "label_definition_hash",
            "feature_source_dataset_hash",
            "label_source_dataset_hash",
        ):
            _require_hash(getattr(self, name), name)
        start = _parse_canonical_utc(self.partition_start, "partition_start")
        end = _parse_canonical_utc(self.partition_end, "partition_end")
        if not start < end:
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "FINAL_HOLDOUT material window must be positive"
            )
        if (
            not isinstance(self.feature_names, tuple)
            or not self.feature_names
            or len(set(self.feature_names)) != len(self.feature_names)
            or any(not isinstance(name, str) or not name for name in self.feature_names)
        ):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "FINAL_HOLDOUT feature_names must be unique non-empty strings"
            )
        if not isinstance(self.feature_rows, tuple) or not isinstance(self.label_rows, tuple):
            raise TypeError("FINAL_HOLDOUT rows must be immutable tuples")
        if not self.feature_rows or not self.label_rows:
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "FINAL_HOLDOUT material cannot be empty"
            )
        if len(self.feature_rows) != len(self.label_rows):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "FINAL_HOLDOUT feature/label row counts differ"
            )
        if len(self.feature_rows) > MAX_HOLDOUT_ROWS:
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "FINAL_HOLDOUT exceeds D2K row bound"
            )
        if any(not isinstance(row, FinalHoldoutFeatureRow) for row in self.feature_rows):
            raise TypeError("feature_rows must contain FinalHoldoutFeatureRow")
        if any(not isinstance(row, FinalHoldoutLabelRow) for row in self.label_rows):
            raise TypeError("label_rows must contain FinalHoldoutLabelRow")
        if any(len(row.values) != len(self.feature_names) for row in self.feature_rows):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "FINAL_HOLDOUT feature width differs from schema"
            )

        feature_keys = tuple((row.as_of, row.symbol) for row in self.feature_rows)
        label_keys = tuple((row.label_as_of, row.symbol) for row in self.label_rows)
        if feature_keys != tuple(sorted(feature_keys)):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "FINAL_HOLDOUT feature rows must be canonical sorted order"
            )
        if label_keys != tuple(sorted(label_keys)):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "FINAL_HOLDOUT label rows must be canonical sorted order"
            )
        if feature_keys != label_keys:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "FINAL_HOLDOUT feature/label keysets differ"
            )
        if len(set(feature_keys)) != len(feature_keys):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "FINAL_HOLDOUT keyset contains duplicates"
            )
        for timestamp, _ in feature_keys:
            observed = _parse_canonical_utc(timestamp, "FINAL_HOLDOUT row timestamp")
            if observed < start or observed >= end:
                raise OSS3FinalHoldoutEvaluationGovernanceError(
                    "FINAL_HOLDOUT row falls outside committed partition"
                )
        # Construction must itself prove that the D2J public commitment is
        # structurally valid, including minimum sample adequacy.
        _ = self.commitment

    @property
    def feature_artifact_hash(self) -> str:
        return _hash(
            {
                "artifact_version": OSS3D2K_FEATURE_ARTIFACT_VERSION,
                "source_campaign_id": self.source_campaign_id,
                "research_split_hash": self.research_split_hash,
                "source_universe_hash": self.source_universe_hash,
                "feature_schema_hash": self.feature_schema_hash,
                "source_dataset_hash": self.feature_source_dataset_hash,
                "partition_start": self.partition_start,
                "partition_end": self.partition_end,
                "feature_names": list(self.feature_names),
                "rows": [row.to_dict() for row in self.feature_rows],
            }
        )

    @property
    def label_artifact_hash(self) -> str:
        return _hash(
            {
                "artifact_version": OSS3D2K_LABEL_ARTIFACT_VERSION,
                "source_campaign_id": self.source_campaign_id,
                "research_split_hash": self.research_split_hash,
                "source_universe_hash": self.source_universe_hash,
                "label_definition_hash": self.label_definition_hash,
                "source_dataset_hash": self.label_source_dataset_hash,
                "partition_start": self.partition_start,
                "partition_end": self.partition_end,
                "rows": [row.to_dict() for row in self.label_rows],
            }
        )

    @property
    def evaluation_keyset_hash(self) -> str:
        return _keyset_hash(tuple((row.as_of, row.symbol) for row in self.feature_rows))

    @property
    def cross_section_key_hash(self) -> str:
        counts = _cross_section_counts(tuple(row.as_of for row in self.feature_rows))
        return _hash([[timestamp, count] for timestamp, count in counts])

    @property
    def commitment(self) -> OSS3ProtectedFinalHoldoutCommitment:
        counts = _cross_section_counts(tuple(row.as_of for row in self.feature_rows))
        minimum = min(count for _, count in counts)
        return OSS3ProtectedFinalHoldoutCommitment(
            commitment_version=OSS3D2J_COMMITMENT_VERSION,
            source_campaign_id=self.source_campaign_id,
            research_split_hash=self.research_split_hash,
            source_universe_hash=self.source_universe_hash,
            label_definition_hash=self.label_definition_hash,
            feature_artifact_hash=self.feature_artifact_hash,
            label_artifact_hash=self.label_artifact_hash,
            evaluation_keyset_hash=self.evaluation_keyset_hash,
            cross_section_key_hash=self.cross_section_key_hash,
            partition_start=self.partition_start,
            partition_end=self.partition_end,
            row_count=len(self.feature_rows),
            cross_section_count=len(counts),
            minimum_cross_section_observation_count=minimum,
            label_values_exposed=False,
            final_holdout_observed=False,
        )


class ProtectedOSS3FinalHoldout:
    """Opaque one-process FINAL_HOLDOUT wrapper with durable checkout proof.

    The actual material is private.  A structurally correct HoldoutPermit is
    insufficient: checkout also re-opens the canonical D2K SQLite registry in
    query-only mode and requires the exact permit and start receipt to have
    already been committed durably.  This closes the accidental forged-permit
    bypass that a field-only permit check would leave open.
    """

    __slots__ = ("__material", "__checked_out")

    def __init__(self, material: OSS3FinalHoldoutMaterial) -> None:
        if not isinstance(material, OSS3FinalHoldoutMaterial):
            raise TypeError("material must be OSS3FinalHoldoutMaterial")
        self.__material = material
        self.__checked_out = False

    @property
    def commitment(self) -> OSS3ProtectedFinalHoldoutCommitment:
        return self.__material.commitment

    def _checkout(
        self,
        *,
        permit: HoldoutPermit,
        expected_authorization_id: str,
        start_receipt: "OSS3FinalHoldoutStartReceipt",
        registry_path: str | Path,
    ) -> OSS3FinalHoldoutMaterial:
        if self.__checked_out:
            raise OSS3FinalHoldoutAlreadyConsumed(
                "protected OSS-3 FINAL_HOLDOUT already checked out"
            )
        if not isinstance(permit, HoldoutPermit):
            raise TypeError("permit must be HoldoutPermit")
        if not isinstance(start_receipt, OSS3FinalHoldoutStartReceipt):
            raise TypeError("start_receipt must be OSS3FinalHoldoutStartReceipt")
        if (
            permit.permit_id != expected_authorization_id
            or permit.purpose != FINAL_VALIDATION_PURPOSE
            or permit.issued_by != _ISSUED_BY
            or start_receipt.holdout_authorization_id != permit.permit_id
            or start_receipt.holdout_commitment_fingerprint != self.commitment.fingerprint
        ):
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "FINAL_HOLDOUT checkout requires exact D2K permit/start identity"
            )
        _verify_durable_checkout_authorization(
            registry_path=registry_path,
            permit=permit,
            start_receipt=start_receipt,
            commitment=self.commitment,
        )
        self.__checked_out = True
        return self.__material


@dataclass(frozen=True, slots=True)
class FinalHoldoutPredictionRow:
    timestamp: str
    symbol: str
    score: float

    def __post_init__(self) -> None:
        _parse_canonical_utc(self.timestamp, "prediction timestamp")
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("invalid prediction symbol")
        _require_finite(self.score, "prediction score")

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "score": float(self.score),
        }


@dataclass(frozen=True, slots=True)
class FinalHoldoutCrossSection:
    timestamp: str
    observation_count: int
    rank_ic: float

    def __post_init__(self) -> None:
        _parse_canonical_utc(self.timestamp, "cross-section timestamp")
        if (
            not isinstance(self.observation_count, int)
            or isinstance(self.observation_count, bool)
            or self.observation_count < 3
        ):
            raise ValueError("cross-section observation_count must be >= 3")
        _require_finite(self.rank_ic, "rank_ic")
        if not -1.0 <= float(self.rank_ic) <= 1.0:
            raise ValueError("rank_ic outside [-1,1]")

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "observation_count": self.observation_count,
            "rank_ic": float(self.rank_ic),
        }


@dataclass(frozen=True, slots=True)
class FinalHoldoutPredictiveMetrics:
    observation_count: int
    valid_cross_section_count: int
    nonzero_rank_ic_cross_section_count: int
    positive_rank_ic_cross_section_count: int
    negative_rank_ic_cross_section_count: int
    mean_cross_sectional_rank_ic: float
    one_sided_exact_sign_test_p_value: float

    def __post_init__(self) -> None:
        for name in (
            "observation_count",
            "valid_cross_section_count",
            "nonzero_rank_ic_cross_section_count",
            "positive_rank_ic_cross_section_count",
            "negative_rank_ic_cross_section_count",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"invalid {name}")
        if self.observation_count < 1 or self.valid_cross_section_count < 1:
            raise ValueError("FINAL_HOLDOUT metrics cannot be empty")
        if (
            self.positive_rank_ic_cross_section_count
            + self.negative_rank_ic_cross_section_count
            != self.nonzero_rank_ic_cross_section_count
        ):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "nonzero Rank IC count mismatch"
            )
        if self.nonzero_rank_ic_cross_section_count > self.valid_cross_section_count:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "nonzero Rank IC count exceeds valid cross sections"
            )
        _require_finite(
            self.mean_cross_sectional_rank_ic,
            "mean_cross_sectional_rank_ic",
        )
        _require_finite(
            self.one_sided_exact_sign_test_p_value,
            "one_sided_exact_sign_test_p_value",
        )
        if not -1.0 <= self.mean_cross_sectional_rank_ic <= 1.0:
            raise ValueError("mean_cross_sectional_rank_ic outside [-1,1]")
        if not 0.0 <= self.one_sided_exact_sign_test_p_value <= 1.0:
            raise ValueError("sign-test p-value outside [0,1]")

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_count": self.observation_count,
            "valid_cross_section_count": self.valid_cross_section_count,
            "nonzero_rank_ic_cross_section_count": self.nonzero_rank_ic_cross_section_count,
            "positive_rank_ic_cross_section_count": self.positive_rank_ic_cross_section_count,
            "negative_rank_ic_cross_section_count": self.negative_rank_ic_cross_section_count,
            "mean_cross_sectional_rank_ic": float(self.mean_cross_sectional_rank_ic),
            "one_sided_exact_sign_test_p_value": float(
                self.one_sided_exact_sign_test_p_value
            ),
        }


@dataclass(frozen=True, slots=True)
class OSS3FinalHoldoutGate:
    gate_id: str
    comparison: str
    threshold: float
    observed: float
    passed: bool

    def __post_init__(self) -> None:
        if self.gate_id not in {
            "FINAL_NONZERO_RANK_IC_CROSS_SECTIONS_MIN",
            "FINAL_MEAN_CROSS_SECTIONAL_RANK_IC_MIN",
            "FINAL_ONE_SIDED_EXACT_SIGN_TEST_P_MAX",
        }:
            raise ValueError("noncanonical D2K gate id")
        if self.comparison not in {">=", "<="}:
            raise ValueError("invalid D2K gate comparison")
        _require_finite(self.threshold, "gate threshold")
        _require_finite(self.observed, "gate observed")
        expected = (
            self.observed >= self.threshold
            if self.comparison == ">="
            else self.observed <= self.threshold
        )
        if self.passed is not expected:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "D2K gate pass flag differs from mechanical comparison"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "comparison": self.comparison,
            "threshold": float(self.threshold),
            "observed": float(self.observed),
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class OSS3FinalHoldoutStartReceipt:
    evaluation_id: str
    start_version: str
    protocol_id: str
    protocol_receipt_hash: str
    winner_binding_fingerprint: str
    holdout_commitment_fingerprint: str
    holdout_authorization_id: str
    source_request_hash: str
    training_bundle_hash: str
    model_config_hash: str
    source_runner_code_hash: str
    source_environment_attestation_hash: str
    source_runtime_environment_hash: str
    final_environment_attestation_hash: str
    final_runtime_environment_hash: str
    evaluator_semantic_hash: str
    started_at: str
    start_hash: str

    def __post_init__(self) -> None:
        for name in ("evaluation_id", "protocol_id", "holdout_authorization_id"):
            _require_id(getattr(self, name), name)
        for name in (
            "protocol_receipt_hash",
            "winner_binding_fingerprint",
            "holdout_commitment_fingerprint",
            "source_request_hash",
            "training_bundle_hash",
            "model_config_hash",
            "source_runner_code_hash",
            "source_environment_attestation_hash",
            "source_runtime_environment_hash",
            "final_environment_attestation_hash",
            "final_runtime_environment_hash",
            "evaluator_semantic_hash",
            "start_hash",
        ):
            _require_hash(getattr(self, name), name)
        if self.start_version != OSS3D2K_START_VERSION:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "noncanonical D2K start version"
            )
        if self.final_environment_attestation_hash != self.source_environment_attestation_hash:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "D2K final environment attestation differs from frozen source environment"
            )
        if self.final_runtime_environment_hash != self.source_runtime_environment_hash:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "D2K final runtime identity differs from frozen source runtime"
            )
        _parse_canonical_utc(self.started_at, "started_at")
        if self.start_hash != _hash(self.to_dict(include_hash=False)):
            raise OSS3FinalHoldoutEvaluationIntegrityError("D2K start hash mismatch")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "evaluation_id": self.evaluation_id,
            "start_version": self.start_version,
            "protocol_id": self.protocol_id,
            "protocol_receipt_hash": self.protocol_receipt_hash,
            "winner_binding_fingerprint": self.winner_binding_fingerprint,
            "holdout_commitment_fingerprint": self.holdout_commitment_fingerprint,
            "holdout_authorization_id": self.holdout_authorization_id,
            "source_request_hash": self.source_request_hash,
            "training_bundle_hash": self.training_bundle_hash,
            "model_config_hash": self.model_config_hash,
            "source_runner_code_hash": self.source_runner_code_hash,
            "source_environment_attestation_hash": self.source_environment_attestation_hash,
            "source_runtime_environment_hash": self.source_runtime_environment_hash,
            "final_environment_attestation_hash": self.final_environment_attestation_hash,
            "final_runtime_environment_hash": self.final_runtime_environment_hash,
            "evaluator_semantic_hash": self.evaluator_semantic_hash,
            "started_at": self.started_at,
        }
        if include_hash:
            payload["start_hash"] = self.start_hash
        return payload


@dataclass(frozen=True, slots=True)
class OSS3FinalHoldoutEvaluationReceipt:
    evaluation_id: str
    receipt_version: str
    protocol_id: str
    protocol_receipt_hash: str
    start_hash: str
    winner_binding_fingerprint: str
    holdout_commitment_fingerprint: str
    holdout_authorization_id: str
    source_request_hash: str
    training_bundle_hash: str
    model_config_hash: str
    source_environment_attestation_hash: str
    source_runtime_environment_hash: str
    final_environment_attestation_hash: str
    final_runtime_environment_hash: str
    evaluator_semantic_hash: str
    prediction_payload_hash: str
    result_hash: str
    decision: OSS3FinalHoldoutDecision
    gates: tuple[OSS3FinalHoldoutGate, ...]
    failed_gate_ids: tuple[str, ...]
    metrics: FinalHoldoutPredictiveMetrics | None
    failure_code: str
    started_at: str
    terminal_at: str
    final_holdout_observed: bool
    final_holdout_consumed: bool
    holdout_permit_consumed: bool
    retuning_allowed: bool
    reselection_allowed: bool
    fallback_candidate_allowed: bool
    second_attempt_allowed: bool
    predictive_validation_passed: bool
    profitability_claim_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        for name in ("evaluation_id", "protocol_id", "holdout_authorization_id"):
            _require_id(getattr(self, name), name)
        for name in (
            "protocol_receipt_hash",
            "start_hash",
            "winner_binding_fingerprint",
            "holdout_commitment_fingerprint",
            "source_request_hash",
            "training_bundle_hash",
            "model_config_hash",
            "source_environment_attestation_hash",
            "source_runtime_environment_hash",
            "final_environment_attestation_hash",
            "final_runtime_environment_hash",
            "evaluator_semantic_hash",
            "receipt_hash",
        ):
            _require_hash(getattr(self, name), name)
        if self.prediction_payload_hash:
            _require_hash(self.prediction_payload_hash, "prediction_payload_hash")
        if self.result_hash:
            _require_hash(self.result_hash, "result_hash")
        if self.receipt_version != OSS3D2K_RECEIPT_VERSION:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "noncanonical D2K receipt version"
            )
        if self.final_environment_attestation_hash != self.source_environment_attestation_hash:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "terminal D2K environment differs from frozen source environment"
            )
        if self.final_runtime_environment_hash != self.source_runtime_environment_hash:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "terminal D2K runtime differs from frozen source runtime"
            )
        started = _parse_canonical_utc(self.started_at, "started_at")
        terminal = _parse_canonical_utc(self.terminal_at, "terminal_at")
        if terminal < started:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "terminal_at predates started_at"
            )
        if (
            not self.final_holdout_observed
            or not self.final_holdout_consumed
            or not self.holdout_permit_consumed
        ):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "terminal D2K receipt must record consumed/observed FINAL_HOLDOUT"
            )
        if (
            self.retuning_allowed
            or self.reselection_allowed
            or self.fallback_candidate_allowed
            or self.second_attempt_allowed
        ):
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "D2K forbids retuning, reselection, fallback and second attempts"
            )
        if self.profitability_claim_authorized:
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "D2K predictive validation cannot claim profitability"
            )
        if (
            self.promotion_authorized
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "D2K cannot authorize promotion/execution"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "D2K cannot grant capital/LIVE authority"
            )

        if self.failure_code:
            if self.decision is not OSS3FinalHoldoutDecision.FAIL:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "structural D2K failure must be terminal FAIL"
                )
            if self.metrics is not None or self.gates or self.failed_gate_ids:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "structural failure may not fabricate metric/gate evidence"
                )
            if self.prediction_payload_hash or self.result_hash:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "structural failure may not fabricate result hashes"
                )
            if self.predictive_validation_passed:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "structural failure cannot pass predictive validation"
                )
        else:
            if self.metrics is None:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "metric D2K receipt requires metrics"
                )
            if len(self.gates) != 3:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "D2K requires exactly three preregistered gates"
                )
            expected_ids = (
                "FINAL_NONZERO_RANK_IC_CROSS_SECTIONS_MIN",
                "FINAL_MEAN_CROSS_SECTIONAL_RANK_IC_MIN",
                "FINAL_ONE_SIDED_EXACT_SIGN_TEST_P_MAX",
            )
            if tuple(gate.gate_id for gate in self.gates) != expected_ids:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "D2K gate order drifted"
                )
            failed = tuple(gate.gate_id for gate in self.gates if not gate.passed)
            if failed != self.failed_gate_ids:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "D2K failed_gate_ids mismatch"
                )
            expected_decision = (
                OSS3FinalHoldoutDecision.PASS
                if not failed
                else OSS3FinalHoldoutDecision.FAIL
            )
            if self.decision is not expected_decision:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "D2K decision must be mechanically derived from gates"
                )
            if self.predictive_validation_passed is not (
                self.decision is OSS3FinalHoldoutDecision.PASS
            ):
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "predictive_validation_passed differs from terminal decision"
                )
            _require_hash(self.prediction_payload_hash, "prediction_payload_hash")
            _require_hash(self.result_hash, "result_hash")
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "D2K receipt hash mismatch"
            )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "evaluation_id": self.evaluation_id,
            "receipt_version": self.receipt_version,
            "protocol_id": self.protocol_id,
            "protocol_receipt_hash": self.protocol_receipt_hash,
            "start_hash": self.start_hash,
            "winner_binding_fingerprint": self.winner_binding_fingerprint,
            "holdout_commitment_fingerprint": self.holdout_commitment_fingerprint,
            "holdout_authorization_id": self.holdout_authorization_id,
            "source_request_hash": self.source_request_hash,
            "training_bundle_hash": self.training_bundle_hash,
            "model_config_hash": self.model_config_hash,
            "source_environment_attestation_hash": self.source_environment_attestation_hash,
            "source_runtime_environment_hash": self.source_runtime_environment_hash,
            "final_environment_attestation_hash": self.final_environment_attestation_hash,
            "final_runtime_environment_hash": self.final_runtime_environment_hash,
            "evaluator_semantic_hash": self.evaluator_semantic_hash,
            "prediction_payload_hash": self.prediction_payload_hash,
            "result_hash": self.result_hash,
            "decision": self.decision.value,
            "gates": [gate.to_dict() for gate in self.gates],
            "failed_gate_ids": list(self.failed_gate_ids),
            "metrics": self.metrics.to_dict() if self.metrics is not None else None,
            "failure_code": self.failure_code,
            "started_at": self.started_at,
            "terminal_at": self.terminal_at,
            "final_holdout_observed": self.final_holdout_observed,
            "final_holdout_consumed": self.final_holdout_consumed,
            "holdout_permit_consumed": self.holdout_permit_consumed,
            "retuning_allowed": self.retuning_allowed,
            "reselection_allowed": self.reselection_allowed,
            "fallback_candidate_allowed": self.fallback_candidate_allowed,
            "second_attempt_allowed": self.second_attempt_allowed,
            "predictive_validation_passed": self.predictive_validation_passed,
            "profitability_claim_authorized": self.profitability_claim_authorized,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }
        if include_hash:
            payload["receipt_hash"] = self.receipt_hash
        return payload


class QlibFinalHoldoutDatasetAdapter:
    """Original TRAIN bundle + protected FINAL_HOLDOUT features only."""

    def __init__(
        self,
        *,
        train_features: FactorMatrixArtifact,
        train_labels: SupervisedLabelArtifact,
        holdout: OSS3FinalHoldoutMaterial,
    ) -> None:
        if train_features.manifest.partition != "TRAIN":
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "D2K refit is forbidden: train_features must remain TRAIN"
            )
        if train_labels.manifest.partition != "TRAIN":
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "D2K refit is forbidden: train_labels must remain TRAIN"
            )
        feature_names = tuple(feature.name for feature in train_features.features)
        if feature_names != holdout.feature_names:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "FINAL_HOLDOUT feature names differ from frozen TRAIN schema"
            )
        train_keys = tuple((row.as_of, row.symbol) for row in train_features.rows)
        label_keys = tuple((row.label_as_of, row.symbol) for row in train_labels.rows)
        if train_keys != label_keys:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "TRAIN feature/label keyset mismatch"
            )
        label_name = train_labels.label.name
        train_index = pd.MultiIndex.from_tuples(
            [(pd.Timestamp(row.as_of), row.symbol) for row in train_features.rows],
            names=("datetime", "instrument"),
        )
        train_columns = pd.MultiIndex.from_tuples(
            [("feature", name) for name in feature_names] + [("label", label_name)]
        )
        train_values = [
            list(map(float, feature.values)) + [float(label.value)]
            for feature, label in zip(train_features.rows, train_labels.rows, strict=True)
        ]
        self._train = pd.DataFrame(
            train_values,
            index=train_index,
            columns=train_columns,
            dtype="float64",
        )
        test_index = pd.MultiIndex.from_tuples(
            [(pd.Timestamp(row.as_of), row.symbol) for row in holdout.feature_rows],
            names=("datetime", "instrument"),
        )
        test_columns = pd.MultiIndex.from_tuples(
            [("feature", name) for name in feature_names]
        )
        self._test = pd.DataFrame(
            [list(map(float, row.values)) for row in holdout.feature_rows],
            index=test_index,
            columns=test_columns,
            dtype="float64",
        )
        _assert_finite_frame(self._train, "TRAIN")
        _assert_finite_frame(self._test, "FINAL_HOLDOUT")

    def prepare(
        self,
        segment: object,
        col_set: object,
        data_key: object = "infer",
    ) -> pd.DataFrame:
        if segment == "train":
            if (
                data_key != "learn"
                or not isinstance(col_set, (list, tuple))
                or tuple(col_set) != ("feature", "label")
            ):
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "unexpected Qlib TRAIN prepare contract"
                )
            return self._train.copy(deep=True)
        if segment == "test":
            if data_key != "infer" or col_set != "feature":
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "unexpected Qlib FINAL_HOLDOUT prepare contract"
                )
            return self._test["feature"].copy(deep=True)
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K Qlib adapter supports train/test only"
        )


class SQLiteOSS3FinalHoldoutEvaluationRegistry:
    """Durable one-attempt evaluator with atomic permit burn + start record."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Reuse the canonical permit table contract; D2K then couples its own
        # start row to the permit in one BEGIN IMMEDIATE transaction.
        SQLiteExperimentRegistry(self.path)
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oss3_final_holdout_evaluation_starts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id TEXT NOT NULL UNIQUE,
                    protocol_id TEXT NOT NULL UNIQUE,
                    protocol_receipt_hash TEXT NOT NULL UNIQUE,
                    winner_binding_fingerprint TEXT NOT NULL UNIQUE,
                    holdout_commitment_fingerprint TEXT NOT NULL UNIQUE,
                    holdout_authorization_id TEXT NOT NULL UNIQUE,
                    source_request_hash TEXT NOT NULL,
                    training_bundle_hash TEXT NOT NULL,
                    model_config_hash TEXT NOT NULL,
                    source_environment_attestation_hash TEXT NOT NULL,
                    source_runtime_environment_hash TEXT NOT NULL,
                    final_environment_attestation_hash TEXT NOT NULL,
                    final_runtime_environment_hash TEXT NOT NULL,
                    evaluator_semantic_hash TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    start_hash TEXT NOT NULL UNIQUE,
                    start_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS oss3_final_holdout_evaluations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id TEXT NOT NULL UNIQUE,
                    protocol_id TEXT NOT NULL UNIQUE,
                    start_hash TEXT NOT NULL UNIQUE,
                    holdout_authorization_id TEXT NOT NULL UNIQUE,
                    decision TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS oss3_final_holdout_starts_no_update
                BEFORE UPDATE ON oss3_final_holdout_evaluation_starts
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2K start registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_final_holdout_starts_no_delete
                BEFORE DELETE ON oss3_final_holdout_evaluation_starts
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2K start registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_final_holdout_terminal_no_update
                BEFORE UPDATE ON oss3_final_holdout_evaluations
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2K terminal registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_final_holdout_terminal_no_delete
                BEFORE DELETE ON oss3_final_holdout_evaluations
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2K terminal registry is append-only');
                END;
                """
            )
            conn.commit()
        finally:
            conn.close()

    def evaluate(
        self,
        *,
        evaluation_id: str,
        protocol: OSS3FinalHoldoutProtocolReceipt,
        source_request: DevelopmentInferenceRequest,
        training_bundle: TrainingBundleArtifact,
        train_features: FactorMatrixArtifact,
        train_labels: SupervisedLabelArtifact,
        holdout: ProtectedOSS3FinalHoldout,
        now: datetime,
    ) -> OSS3FinalHoldoutEvaluationReceipt:
        _require_id(evaluation_id, "evaluation_id")
        _require_aware(now, "now")
        _verify_protocol(protocol)
        if not isinstance(holdout, ProtectedOSS3FinalHoldout):
            raise TypeError("holdout must be ProtectedOSS3FinalHoldout")
        commitment = holdout.commitment
        if commitment.fingerprint != protocol.holdout_commitment.fingerprint:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "protected FINAL_HOLDOUT differs from D2J commitment"
            )
        _verify_replay_inputs(
            protocol=protocol,
            source_request=source_request,
            training_bundle=training_bundle,
            train_features=train_features,
            train_labels=train_labels,
            holdout_commitment=commitment,
        )
        # Secrets are rejected before runtime attestation and, critically,
        # before permit consumption.
        _reject_broker_credentials()
        final_attestation = _verify_exact_final_runtime(protocol)

        permit = HoldoutPermit(
            permit_id=protocol.expected_holdout_authorization_id,
            issued_by=_ISSUED_BY,
            purpose=FINAL_VALIDATION_PURPOSE,
        )
        start = _build_start(
            evaluation_id=evaluation_id,
            protocol=protocol,
            source_request=source_request,
            training_bundle=training_bundle,
            final_attestation=final_attestation,
            started_at=now,
        )
        self._consume_and_record_start(permit=permit, start=start)

        try:
            # The wrapper independently proves that this exact start + permit
            # are already durable in this registry before exposing labels.
            material = holdout._checkout(
                permit=permit,
                expected_authorization_id=protocol.expected_holdout_authorization_id,
                start_receipt=start,
                registry_path=self.path,
            )
            if material.commitment.fingerprint != start.holdout_commitment_fingerprint:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "FINAL_HOLDOUT changed after durable authorization consumption"
                )
            predictions = _run_frozen_final_model(
                protocol=protocol,
                source_request=source_request,
                training_bundle=training_bundle,
                train_features=train_features,
                train_labels=train_labels,
                holdout=material,
            )
            metrics, cross_sections = _evaluate_predictions(
                protocol=protocol,
                predictions=predictions,
                holdout=material,
            )
            receipt = _build_metric_receipt(
                start=start,
                protocol=protocol,
                predictions=predictions,
                metrics=metrics,
                cross_sections=cross_sections,
                terminal_at=now,
            )
        except Exception as exc:
            receipt = _build_structural_failure_receipt(
                start=start,
                protocol=protocol,
                failure_code=f"EVALUATION_ERROR:{type(exc).__name__}",
                terminal_at=now,
            )
        return self._record_terminal(receipt)

    def _consume_and_record_start(
        self,
        *,
        permit: HoldoutPermit,
        start: OSS3FinalHoldoutStartReceipt,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT evaluation_id FROM oss3_final_holdout_evaluation_starts "
                "WHERE protocol_id = ? OR holdout_authorization_id = ? "
                "OR holdout_commitment_fingerprint = ?",
                (
                    start.protocol_id,
                    start.holdout_authorization_id,
                    start.holdout_commitment_fingerprint,
                ),
            ).fetchone()
            if prior is not None:
                raise OSS3FinalHoldoutAlreadyConsumed(
                    "OSS-3 FINAL_HOLDOUT protocol already consumed"
                )
            try:
                conn.execute(
                    "INSERT INTO holdout_permits(permit_id, issued_by, purpose, used_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        permit.permit_id,
                        permit.issued_by,
                        permit.purpose,
                        start.started_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise OSS3FinalHoldoutAlreadyConsumed(permit.permit_id) from exc
            conn.execute(
                """
                INSERT INTO oss3_final_holdout_evaluation_starts(
                    evaluation_id, protocol_id, protocol_receipt_hash,
                    winner_binding_fingerprint, holdout_commitment_fingerprint,
                    holdout_authorization_id, source_request_hash,
                    training_bundle_hash, model_config_hash,
                    source_environment_attestation_hash,
                    source_runtime_environment_hash,
                    final_environment_attestation_hash,
                    final_runtime_environment_hash,
                    evaluator_semantic_hash, started_at, start_hash, start_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    start.evaluation_id,
                    start.protocol_id,
                    start.protocol_receipt_hash,
                    start.winner_binding_fingerprint,
                    start.holdout_commitment_fingerprint,
                    start.holdout_authorization_id,
                    start.source_request_hash,
                    start.training_bundle_hash,
                    start.model_config_hash,
                    start.source_environment_attestation_hash,
                    start.source_runtime_environment_hash,
                    start.final_environment_attestation_hash,
                    start.final_runtime_environment_hash,
                    start.evaluator_semantic_hash,
                    start.started_at,
                    start.start_hash,
                    _canonical_json(start.to_dict()),
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise OSS3FinalHoldoutAlreadyConsumed(
                "OSS-3 FINAL_HOLDOUT start conflicts with durable state"
            ) from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _record_terminal(
        self,
        receipt: OSS3FinalHoldoutEvaluationReceipt,
    ) -> OSS3FinalHoldoutEvaluationReceipt:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM oss3_final_holdout_evaluations WHERE evaluation_id = ?",
                (receipt.evaluation_id,),
            ).fetchone()
            if existing is not None:
                current = _terminal_from_row(existing)
                if current != receipt:
                    raise OSS3FinalHoldoutEvaluationIntegrityError(
                        "terminal D2K receipt conflict"
                    )
                conn.execute("COMMIT")
                return current
            start = conn.execute(
                "SELECT start_hash FROM oss3_final_holdout_evaluation_starts "
                "WHERE evaluation_id = ?",
                (receipt.evaluation_id,),
            ).fetchone()
            if start is None or str(start["start_hash"]) != receipt.start_hash:
                raise OSS3FinalHoldoutEvaluationIntegrityError(
                    "terminal D2K receipt has no exact durable start"
                )
            conn.execute(
                """
                INSERT INTO oss3_final_holdout_evaluations(
                    evaluation_id, protocol_id, start_hash,
                    holdout_authorization_id, decision, receipt_hash, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.evaluation_id,
                    receipt.protocol_id,
                    receipt.start_hash,
                    receipt.holdout_authorization_id,
                    receipt.decision.value,
                    receipt.receipt_hash,
                    _canonical_json(receipt.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return receipt
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def read_oss3d2k_evaluation_read_only(
    path: str | Path,
    *,
    protocol_id: str,
) -> OSS3FinalHoldoutEvaluationReceipt | None:
    """Verify terminal state; a consumed start without terminal forbids retry."""
    _require_id(protocol_id, "protocol_id")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K durable registry does not exist"
        )
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        _require_d2k_tables(conn)
        start = conn.execute(
            "SELECT * FROM oss3_final_holdout_evaluation_starts WHERE protocol_id = ?",
            (protocol_id,),
        ).fetchone()
        terminal = conn.execute(
            "SELECT * FROM oss3_final_holdout_evaluations WHERE protocol_id = ?",
            (protocol_id,),
        ).fetchone()
        if terminal is None:
            if start is not None:
                raise OSS3FinalHoldoutAlreadyConsumed(
                    "FINAL_HOLDOUT authorization consumed without terminal receipt; no retry allowed"
                )
            return None
        if start is None:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "terminal D2K receipt missing durable start"
            )
        start_receipt = _start_from_row(start)
        receipt = _terminal_from_row(terminal)
        if (
            receipt.start_hash != start_receipt.start_hash
            or receipt.evaluation_id != start_receipt.evaluation_id
            or receipt.protocol_id != start_receipt.protocol_id
            or receipt.holdout_authorization_id
            != start_receipt.holdout_authorization_id
            or receipt.source_environment_attestation_hash
            != start_receipt.source_environment_attestation_hash
            or receipt.final_environment_attestation_hash
            != start_receipt.final_environment_attestation_hash
            or receipt.final_runtime_environment_hash
            != start_receipt.final_runtime_environment_hash
        ):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "D2K start/terminal chain mismatch"
            )
        permit = conn.execute(
            "SELECT issued_by, purpose, used_at FROM holdout_permits WHERE permit_id = ?",
            (receipt.holdout_authorization_id,),
        ).fetchone()
        _verify_permit_row(permit, start_receipt)
        return receipt
    finally:
        conn.close()


def evaluator_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    payload: list[dict[str, str]] = []
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                f"missing D2K semantic file: {relative}"
            )
        payload.append(
            {
                "path": relative,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
        )
    return _hash(payload)


def _verify_protocol(protocol: OSS3FinalHoldoutProtocolReceipt) -> None:
    if not isinstance(protocol, OSS3FinalHoldoutProtocolReceipt):
        raise TypeError("protocol must be OSS3FinalHoldoutProtocolReceipt")
    if protocol.contract_version != OSS3D2J_CONTRACT_VERSION:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K requires canonical D2J protocol"
        )
    if (
        protocol.final_holdout_observed
        or protocol.final_holdout_consumed
        or protocol.holdout_permit_issued
        or protocol.holdout_permit_consumed
        or protocol.final_holdout_checkout_authorized
    ):
        raise OSS3FinalHoldoutEvaluationGovernanceError(
            "D2K requires untouched D2J FINAL_HOLDOUT protocol"
        )
    if (
        protocol.promotion_authorized
        or protocol.execution_authorized
        or protocol.paper_execution_authorized
    ):
        raise OSS3FinalHoldoutEvaluationGovernanceError(
            "D2J protocol may not authorize promotion/execution"
        )
    if protocol.capital_authority != "NONE" or protocol.live_trading != "BLOCKED":
        raise OSS3FinalHoldoutEvaluationGovernanceError(
            "D2J protocol may not grant capital/LIVE authority"
        )


def _verify_replay_inputs(
    *,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    source_request: DevelopmentInferenceRequest,
    training_bundle: TrainingBundleArtifact,
    train_features: FactorMatrixArtifact,
    train_labels: SupervisedLabelArtifact,
    holdout_commitment: OSS3ProtectedFinalHoldoutCommitment,
) -> None:
    if not isinstance(source_request, DevelopmentInferenceRequest):
        raise TypeError("source_request must be DevelopmentInferenceRequest")
    if not isinstance(training_bundle, TrainingBundleArtifact):
        raise TypeError("training_bundle must be TrainingBundleArtifact")
    if not isinstance(train_features, FactorMatrixArtifact):
        raise TypeError("train_features must be FactorMatrixArtifact")
    if not isinstance(train_labels, SupervisedLabelArtifact):
        raise TypeError("train_labels must be SupervisedLabelArtifact")
    if not isinstance(holdout_commitment, OSS3ProtectedFinalHoldoutCommitment):
        raise TypeError("holdout_commitment must be OSS3ProtectedFinalHoldoutCommitment")
    winner = protocol.winner_binding
    request = source_request.manifest
    bundle = training_bundle.manifest

    if source_request.request_hash != winner.request_hash:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K source D2A request differs from D2I winner"
        )
    for name, expected, actual in (
        ("model_family", winner.model_family, request.model_family),
        ("model_config_hash", winner.model_config_hash, request.model_config_hash),
        (
            "shared_runner_code_hash",
            winner.shared_runner_code_hash,
            request.expected_runner_code_hash,
        ),
        ("required_qlib_version", QLIB_VERSION, request.required_qlib_version),
        ("training_bundle_hash", request.training_bundle_hash, training_bundle.artifact_hash),
        (
            "training_bundle_manifest_hash",
            request.training_bundle_manifest_hash,
            bundle.fingerprint,
        ),
        ("train_feature_artifact_hash", request.train_feature_artifact_hash, train_features.artifact_hash),
        ("train_label_artifact_hash", request.train_label_artifact_hash, train_labels.artifact_hash),
        ("campaign_id", request.campaign_id, bundle.campaign_id),
        ("research_split_hash", request.research_split_hash, bundle.research_split_hash),
        ("source_universe_hash", request.source_universe_hash, bundle.source_universe_hash),
        ("feature_schema_hash", request.feature_schema_hash, bundle.feature_schema_hash),
        ("label_definition_hash", request.label_definition_hash, bundle.label_definition_hash),
        ("holdout campaign", request.campaign_id, holdout_commitment.source_campaign_id),
        ("holdout research split", request.research_split_hash, holdout_commitment.research_split_hash),
        ("holdout universe", request.source_universe_hash, holdout_commitment.source_universe_hash),
        ("holdout label definition", request.label_definition_hash, holdout_commitment.label_definition_hash),
    ):
        if expected != actual:
            raise OSS3FinalHoldoutEvaluationIntegrityError(f"D2K {name} mismatch")
    if request.expected_runner_code_hash != family_runner_code_hash():
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K source D2G semantic runner has drifted"
        )
    candidate = candidate_from_config_hash(request.model_config_hash)
    if candidate.candidate_id != winner.selected_trial_id:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K candidate identity differs from frozen winner"
        )
    rebuilt = TrainingBundleArtifact.build(
        features=train_features,
        labels=train_labels,
    )
    if rebuilt.artifact_hash != training_bundle.artifact_hash:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K concrete TRAIN artifacts do not reproduce source bundle"
        )
    if bundle.partition != "TRAIN":
        raise OSS3FinalHoldoutEvaluationGovernanceError(
            "D2K may not refit on DEVELOPMENT"
        )
    if _parse_canonical_utc(
        holdout_commitment.partition_start,
        "holdout start",
    ) < _parse_canonical_utc(
        request.inference_end,
        "source DEVELOPMENT inference_end",
    ):
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K FINAL_HOLDOUT overlaps source DEVELOPMENT window"
        )


def _verify_exact_final_runtime(
    protocol: OSS3FinalHoldoutProtocolReceipt,
) -> CandidateEnvironmentAttestation:
    """Require the final evaluator to reproduce the winner's D2G environment."""
    winner = protocol.winner_binding
    attestation = collect_candidate_environment_attestation(
        model_config_hash=winner.model_config_hash
    )
    attestation.verify_current_contract()
    if attestation.artifact_hash != winner.environment_attestation_hash:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K final environment attestation differs from frozen D2G winner"
        )
    if attestation.runtime_environment.fingerprint != winner.runtime_environment_hash:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K final model-neutral runtime differs from frozen D2G winner"
        )
    if attestation.manifest.runner_code_hash != winner.shared_runner_code_hash:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K final runner identity differs from frozen D2G winner"
        )
    if attestation.manifest.qlib_version != QLIB_VERSION:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K final environment does not contain exact Qlib version"
        )
    return attestation


def _run_frozen_final_model(
    *,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    source_request: DevelopmentInferenceRequest,
    training_bundle: TrainingBundleArtifact,
    train_features: FactorMatrixArtifact,
    train_labels: SupervisedLabelArtifact,
    holdout: OSS3FinalHoldoutMaterial,
) -> tuple[FinalHoldoutPredictionRow, ...]:
    request = source_request.manifest
    if holdout.feature_schema_hash != request.feature_schema_hash:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "FINAL_HOLDOUT feature schema differs from frozen D2A request"
        )
    if holdout.label_definition_hash != request.label_definition_hash:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "FINAL_HOLDOUT label definition differs from frozen D2A request"
        )
    if holdout.commitment.fingerprint != protocol.holdout_commitment.fingerprint:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "checked-out FINAL_HOLDOUT differs from D2J commitment"
        )
    if training_bundle.artifact_hash != request.training_bundle_hash:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K must replay exact original TRAIN bundle"
        )

    candidate = candidate_from_config_hash(request.model_config_hash)
    config = candidate_runtime_config(candidate)
    dataset = QlibFinalHoldoutDatasetAdapter(
        train_features=train_features,
        train_labels=train_labels,
        holdout=holdout,
    )
    with deny_network():
        import qlib
        from qlib.contrib.model.linear import LinearModel

        actual_version = str(getattr(qlib, "__version__", ""))
        if actual_version != QLIB_VERSION:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                f"Qlib runtime mismatch: expected {QLIB_VERSION}, got {actual_version!r}"
            )
        model = LinearModel(
            estimator=str(config["estimator"]),
            alpha=float(config["alpha"]),
            fit_intercept=bool(config["fit_intercept"]),
            include_valid=bool(config["include_valid"]),
        )
        model.fit(dataset)
        scores = model.predict(dataset, segment=str(config["prediction_segment"]))

    if not hasattr(scores, "items"):
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "Qlib FINAL_HOLDOUT prediction is not indexed"
        )
    expected_keys = tuple((row.as_of, row.symbol) for row in holdout.feature_rows)
    observed: list[tuple[str, str]] = []
    result: list[FinalHoldoutPredictionRow] = []
    for key, score in scores.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "Qlib prediction index must be (datetime,instrument)"
            )
        timestamp, symbol = key
        if not hasattr(timestamp, "to_pydatetime"):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "Qlib prediction timestamp is invalid"
            )
        as_of = timestamp.to_pydatetime()
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "Qlib prediction timestamp lost timezone"
            )
        pair = (as_of.isoformat(), str(symbol))
        observed.append(pair)
        result.append(
            FinalHoldoutPredictionRow(
                timestamp=pair[0],
                symbol=pair[1],
                score=float(score),
            )
        )
    if tuple(observed) != expected_keys:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "Qlib FINAL_HOLDOUT prediction keyset drifted"
        )
    return tuple(result)


def _evaluate_predictions(
    *,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    predictions: tuple[FinalHoldoutPredictionRow, ...],
    holdout: OSS3FinalHoldoutMaterial,
) -> tuple[FinalHoldoutPredictiveMetrics, tuple[FinalHoldoutCrossSection, ...]]:
    prediction_keys = tuple((row.timestamp, row.symbol) for row in predictions)
    label_keys = tuple((row.label_as_of, row.symbol) for row in holdout.label_rows)
    if prediction_keys != label_keys:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "FINAL_HOLDOUT prediction/label keysets differ"
        )
    if _keyset_hash(prediction_keys) != protocol.holdout_commitment.evaluation_keyset_hash:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "FINAL_HOLDOUT evaluation keyset differs from D2J commitment"
        )

    grouped: dict[str, list[tuple[float, float]]] = {}
    for prediction, label in zip(predictions, holdout.label_rows, strict=True):
        grouped.setdefault(prediction.timestamp, []).append(
            (float(prediction.score), float(label.value))
        )
    cross_sections: list[FinalHoldoutCrossSection] = []
    for timestamp in sorted(grouped):
        pairs = grouped[timestamp]
        if len(pairs) < protocol.policy.min_cross_section_observations:
            continue
        scores = tuple(item[0] for item in pairs)
        targets = tuple(item[1] for item in pairs)
        if _is_constant(scores) or _is_constant(targets):
            continue
        rank_ic = _pearson(_average_ranks(scores), _average_ranks(targets))
        cross_sections.append(
            FinalHoldoutCrossSection(
                timestamp=timestamp,
                observation_count=len(pairs),
                rank_ic=rank_ic,
            )
        )
    if len(cross_sections) < protocol.policy.min_holdout_cross_sections:
        raise OSS3FinalHoldoutEvaluationGovernanceError(
            "FINAL_HOLDOUT has too few non-degenerate Rank IC cross sections"
        )
    values = tuple(item.rank_ic for item in cross_sections)
    positives = sum(1 for value in values if value > 0.0)
    negatives = sum(1 for value in values if value < 0.0)
    nonzero = positives + negatives
    p_value = _one_sided_exact_sign_test(positives=positives, negatives=negatives)
    metrics = FinalHoldoutPredictiveMetrics(
        observation_count=len(predictions),
        valid_cross_section_count=len(cross_sections),
        nonzero_rank_ic_cross_section_count=nonzero,
        positive_rank_ic_cross_section_count=positives,
        negative_rank_ic_cross_section_count=negatives,
        mean_cross_sectional_rank_ic=_mean(values),
        one_sided_exact_sign_test_p_value=p_value,
    )
    return metrics, tuple(cross_sections)


def _build_start(
    *,
    evaluation_id: str,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    source_request: DevelopmentInferenceRequest,
    training_bundle: TrainingBundleArtifact,
    final_attestation: CandidateEnvironmentAttestation,
    started_at: datetime,
) -> OSS3FinalHoldoutStartReceipt:
    winner = protocol.winner_binding
    values = {
        "evaluation_id": evaluation_id,
        "start_version": OSS3D2K_START_VERSION,
        "protocol_id": protocol.protocol_id,
        "protocol_receipt_hash": protocol.receipt_hash,
        "winner_binding_fingerprint": winner.fingerprint,
        "holdout_commitment_fingerprint": protocol.holdout_commitment.fingerprint,
        "holdout_authorization_id": protocol.expected_holdout_authorization_id,
        "source_request_hash": source_request.request_hash,
        "training_bundle_hash": training_bundle.artifact_hash,
        "model_config_hash": winner.model_config_hash,
        "source_runner_code_hash": winner.shared_runner_code_hash,
        "source_environment_attestation_hash": winner.environment_attestation_hash,
        "source_runtime_environment_hash": winner.runtime_environment_hash,
        "final_environment_attestation_hash": final_attestation.artifact_hash,
        "final_runtime_environment_hash": final_attestation.runtime_environment.fingerprint,
        "evaluator_semantic_hash": evaluator_semantic_hash(),
        "started_at": started_at.astimezone(timezone.utc).isoformat(),
    }
    return OSS3FinalHoldoutStartReceipt(
        **values,
        start_hash=_hash(values),
    )


def _build_metric_receipt(
    *,
    start: OSS3FinalHoldoutStartReceipt,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    predictions: tuple[FinalHoldoutPredictionRow, ...],
    metrics: FinalHoldoutPredictiveMetrics,
    cross_sections: tuple[FinalHoldoutCrossSection, ...],
    terminal_at: datetime,
) -> OSS3FinalHoldoutEvaluationReceipt:
    policy = protocol.policy
    gates = (
        _gate(
            "FINAL_NONZERO_RANK_IC_CROSS_SECTIONS_MIN",
            ">=",
            float(policy.min_nonzero_rank_ic_cross_sections),
            float(metrics.nonzero_rank_ic_cross_section_count),
        ),
        _gate(
            "FINAL_MEAN_CROSS_SECTIONAL_RANK_IC_MIN",
            ">=",
            float(policy.min_mean_cross_sectional_rank_ic),
            float(metrics.mean_cross_sectional_rank_ic),
        ),
        _gate(
            "FINAL_ONE_SIDED_EXACT_SIGN_TEST_P_MAX",
            "<=",
            float(policy.max_one_sided_sign_test_p_value),
            float(metrics.one_sided_exact_sign_test_p_value),
        ),
    )
    failed = tuple(gate.gate_id for gate in gates if not gate.passed)
    decision = (
        OSS3FinalHoldoutDecision.PASS
        if not failed
        else OSS3FinalHoldoutDecision.FAIL
    )
    prediction_hash = _hash([row.to_dict() for row in predictions])
    result_hash = _hash(
        {
            "prediction_payload_hash": prediction_hash,
            "metrics": metrics.to_dict(),
            "cross_sections": [item.to_dict() for item in cross_sections],
            "gates": [gate.to_dict() for gate in gates],
        }
    )
    values = _terminal_base(
        start=start,
        protocol=protocol,
        terminal_at=terminal_at,
    )
    values.update(
        {
            "prediction_payload_hash": prediction_hash,
            "result_hash": result_hash,
            "decision": decision,
            "gates": gates,
            "failed_gate_ids": failed,
            "metrics": metrics,
            "failure_code": "",
            "predictive_validation_passed": decision is OSS3FinalHoldoutDecision.PASS,
        }
    )
    return OSS3FinalHoldoutEvaluationReceipt(
        **values,
        receipt_hash=_hash(_terminal_payload_for_hash(values)),
    )


def _build_structural_failure_receipt(
    *,
    start: OSS3FinalHoldoutStartReceipt,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    failure_code: str,
    terminal_at: datetime,
) -> OSS3FinalHoldoutEvaluationReceipt:
    if not failure_code:
        raise ValueError("failure_code is required")
    values = _terminal_base(
        start=start,
        protocol=protocol,
        terminal_at=terminal_at,
    )
    values.update(
        {
            "prediction_payload_hash": "",
            "result_hash": "",
            "decision": OSS3FinalHoldoutDecision.FAIL,
            "gates": (),
            "failed_gate_ids": (),
            "metrics": None,
            "failure_code": failure_code,
            "predictive_validation_passed": False,
        }
    )
    return OSS3FinalHoldoutEvaluationReceipt(
        **values,
        receipt_hash=_hash(_terminal_payload_for_hash(values)),
    )


def _terminal_base(
    *,
    start: OSS3FinalHoldoutStartReceipt,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    terminal_at: datetime,
) -> dict[str, object]:
    return {
        "evaluation_id": start.evaluation_id,
        "receipt_version": OSS3D2K_RECEIPT_VERSION,
        "protocol_id": protocol.protocol_id,
        "protocol_receipt_hash": protocol.receipt_hash,
        "start_hash": start.start_hash,
        "winner_binding_fingerprint": protocol.winner_binding.fingerprint,
        "holdout_commitment_fingerprint": protocol.holdout_commitment.fingerprint,
        "holdout_authorization_id": protocol.expected_holdout_authorization_id,
        "source_request_hash": start.source_request_hash,
        "training_bundle_hash": start.training_bundle_hash,
        "model_config_hash": start.model_config_hash,
        "source_environment_attestation_hash": start.source_environment_attestation_hash,
        "source_runtime_environment_hash": start.source_runtime_environment_hash,
        "final_environment_attestation_hash": start.final_environment_attestation_hash,
        "final_runtime_environment_hash": start.final_runtime_environment_hash,
        "evaluator_semantic_hash": start.evaluator_semantic_hash,
        "started_at": start.started_at,
        "terminal_at": terminal_at.astimezone(timezone.utc).isoformat(),
        "final_holdout_observed": True,
        "final_holdout_consumed": True,
        "holdout_permit_consumed": True,
        "retuning_allowed": False,
        "reselection_allowed": False,
        "fallback_candidate_allowed": False,
        "second_attempt_allowed": False,
        "profitability_claim_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def _terminal_payload_for_hash(values: Mapping[str, object]) -> dict[str, object]:
    payload = dict(values)
    decision = payload.get("decision")
    if isinstance(decision, OSS3FinalHoldoutDecision):
        payload["decision"] = decision.value
    gates = payload.get("gates")
    if isinstance(gates, tuple):
        payload["gates"] = [gate.to_dict() for gate in gates]
    failed = payload.get("failed_gate_ids")
    if isinstance(failed, tuple):
        payload["failed_gate_ids"] = list(failed)
    metrics = payload.get("metrics")
    if isinstance(metrics, FinalHoldoutPredictiveMetrics):
        payload["metrics"] = metrics.to_dict()
    return payload


def _gate(
    gate_id: str,
    comparison: str,
    threshold: float,
    observed: float,
) -> OSS3FinalHoldoutGate:
    passed = observed >= threshold if comparison == ">=" else observed <= threshold
    return OSS3FinalHoldoutGate(
        gate_id=gate_id,
        comparison=comparison,
        threshold=threshold,
        observed=observed,
        passed=passed,
    )


def _verify_durable_checkout_authorization(
    *,
    registry_path: str | Path,
    permit: HoldoutPermit,
    start_receipt: OSS3FinalHoldoutStartReceipt,
    commitment: OSS3ProtectedFinalHoldoutCommitment,
) -> None:
    """Prove permit + start were committed before protected material release."""
    resolved = Path(registry_path).resolve()
    if not resolved.is_file():
        raise OSS3FinalHoldoutEvaluationGovernanceError(
            "FINAL_HOLDOUT checkout has no durable D2K registry"
        )
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        _require_d2k_tables(conn)
        row = conn.execute(
            "SELECT * FROM oss3_final_holdout_evaluation_starts WHERE start_hash = ?",
            (start_receipt.start_hash,),
        ).fetchone()
        if row is None:
            raise OSS3FinalHoldoutEvaluationGovernanceError(
                "FINAL_HOLDOUT checkout start is not durably recorded"
            )
        durable_start = _start_from_row(row)
        if durable_start != start_receipt:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "FINAL_HOLDOUT checkout start differs from durable receipt"
            )
        if durable_start.holdout_commitment_fingerprint != commitment.fingerprint:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                "FINAL_HOLDOUT checkout commitment differs from durable start"
            )
        permit_row = conn.execute(
            "SELECT issued_by, purpose, used_at FROM holdout_permits WHERE permit_id = ?",
            (permit.permit_id,),
        ).fetchone()
        _verify_permit_row(permit_row, durable_start)
        terminal = conn.execute(
            "SELECT receipt_hash FROM oss3_final_holdout_evaluations WHERE evaluation_id = ?",
            (durable_start.evaluation_id,),
        ).fetchone()
        if terminal is not None:
            raise OSS3FinalHoldoutAlreadyConsumed(
                "FINAL_HOLDOUT evaluation is already terminal"
            )
    finally:
        conn.close()


def _require_d2k_tables(conn: sqlite3.Connection) -> None:
    tables = {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    required = {
        "holdout_permits",
        "oss3_final_holdout_evaluation_starts",
        "oss3_final_holdout_evaluations",
    }
    if not required.issubset(tables):
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K durable registry tables are missing"
        )


def _verify_permit_row(
    permit_row: sqlite3.Row | None,
    start_receipt: OSS3FinalHoldoutStartReceipt,
) -> None:
    if permit_row is None:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K durable start is missing its consumed permit"
        )
    if (
        str(permit_row["issued_by"]) != _ISSUED_BY
        or str(permit_row["purpose"]) != FINAL_VALIDATION_PURPOSE
        or str(permit_row["used_at"]) != start_receipt.started_at
    ):
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K consumed permit side columns drifted"
        )


def _start_from_row(row: sqlite3.Row) -> OSS3FinalHoldoutStartReceipt:
    try:
        payload = json.loads(str(row["start_json"]))
        if not isinstance(payload, Mapping):
            raise TypeError("start_json must be object")
        receipt = OSS3FinalHoldoutStartReceipt(**dict(payload))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "invalid durable D2K start receipt"
        ) from exc
    for column, expected in (
        ("evaluation_id", receipt.evaluation_id),
        ("protocol_id", receipt.protocol_id),
        ("protocol_receipt_hash", receipt.protocol_receipt_hash),
        ("winner_binding_fingerprint", receipt.winner_binding_fingerprint),
        ("holdout_commitment_fingerprint", receipt.holdout_commitment_fingerprint),
        ("holdout_authorization_id", receipt.holdout_authorization_id),
        ("source_request_hash", receipt.source_request_hash),
        ("training_bundle_hash", receipt.training_bundle_hash),
        ("model_config_hash", receipt.model_config_hash),
        ("source_environment_attestation_hash", receipt.source_environment_attestation_hash),
        ("source_runtime_environment_hash", receipt.source_runtime_environment_hash),
        ("final_environment_attestation_hash", receipt.final_environment_attestation_hash),
        ("final_runtime_environment_hash", receipt.final_runtime_environment_hash),
        ("evaluator_semantic_hash", receipt.evaluator_semantic_hash),
        ("started_at", receipt.started_at),
        ("start_hash", receipt.start_hash),
    ):
        if str(row[column]) != expected:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                f"D2K durable start column mismatch: {column}"
            )
    if _canonical_json(receipt.to_dict()) != str(row["start_json"]):
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K durable start serialization drifted"
        )
    return receipt


def _terminal_from_row(row: sqlite3.Row) -> OSS3FinalHoldoutEvaluationReceipt:
    try:
        payload = json.loads(str(row["receipt_json"]))
    except json.JSONDecodeError as exc:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "invalid durable D2K terminal JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K terminal JSON must be object"
        )
    values = dict(payload)
    try:
        values["decision"] = OSS3FinalHoldoutDecision(str(values["decision"]))
        raw_gates = values.get("gates")
        if not isinstance(raw_gates, list):
            raise TypeError("gates must be list")
        values["gates"] = tuple(
            OSS3FinalHoldoutGate(**dict(item)) for item in raw_gates
        )
        raw_failed = values.get("failed_gate_ids")
        if not isinstance(raw_failed, list):
            raise TypeError("failed_gate_ids must be list")
        values["failed_gate_ids"] = tuple(str(item) for item in raw_failed)
        raw_metrics = values.get("metrics")
        values["metrics"] = (
            None
            if raw_metrics is None
            else FinalHoldoutPredictiveMetrics(**dict(raw_metrics))
        )
        receipt = OSS3FinalHoldoutEvaluationReceipt(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "invalid durable D2K terminal fields"
        ) from exc
    for column, expected in (
        ("evaluation_id", receipt.evaluation_id),
        ("protocol_id", receipt.protocol_id),
        ("start_hash", receipt.start_hash),
        ("holdout_authorization_id", receipt.holdout_authorization_id),
        ("decision", receipt.decision.value),
        ("receipt_hash", receipt.receipt_hash),
    ):
        if str(row[column]) != expected:
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                f"D2K durable terminal column mismatch: {column}"
            )
    if _canonical_json(receipt.to_dict()) != str(row["receipt_json"]):
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "D2K durable terminal serialization drifted"
        )
    return receipt


def _one_sided_exact_sign_test(*, positives: int, negatives: int) -> float:
    if positives < 0 or negatives < 0:
        raise ValueError("sign-test counts cannot be negative")
    total = positives + negatives
    if total == 0:
        return 1.0
    numerator = sum(comb(total, k) for k in range(positives, total + 1))
    return float(numerator / (2**total))


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 3:
        raise OSS3FinalHoldoutEvaluationGovernanceError(
            "Rank IC requires at least three paired observations"
        )
    left_mean = _mean(left)
    right_mean = _mean(right)
    left_centered = tuple(float(value) - left_mean for value in left)
    right_centered = tuple(float(value) - right_mean for value in right)
    left_ss = sum(value * value for value in left_centered)
    right_ss = sum(value * value for value in right_centered)
    if left_ss <= 0.0 or right_ss <= 0.0:
        raise OSS3FinalHoldoutEvaluationGovernanceError(
            "Rank IC undefined for constant values"
        )
    value = sum(
        a * b for a, b in zip(left_centered, right_centered, strict=True)
    ) / sqrt(left_ss * right_ss)
    if not isfinite(value):
        raise OSS3FinalHoldoutEvaluationIntegrityError(
            "Rank IC produced non-finite value"
        )
    return max(-1.0, min(1.0, float(value)))


def _average_ranks(values: Sequence[float]) -> tuple[float, ...]:
    indexed = sorted(
        enumerate(float(value) for value in values),
        key=lambda item: (item[1], item[0]),
    )
    ranks = [0.0] * len(indexed)
    cursor = 0
    while cursor < len(indexed):
        stop = cursor + 1
        while stop < len(indexed) and indexed[stop][1] == indexed[cursor][1]:
            stop += 1
        average_rank = ((cursor + 1) + stop) / 2.0
        for position in range(cursor, stop):
            ranks[indexed[position][0]] = average_rank
        cursor = stop
    return tuple(ranks)


def _is_constant(values: Sequence[float]) -> bool:
    first = float(values[0])
    return all(float(value) == first for value in values[1:])


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise OSS3FinalHoldoutEvaluationIntegrityError("cannot average empty values")
    value = sum(float(item) for item in values) / len(values)
    if not isfinite(value):
        raise OSS3FinalHoldoutEvaluationIntegrityError("mean is non-finite")
    return float(value)


def _cross_section_counts(timestamps: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for timestamp in timestamps:
        counts[timestamp] = counts.get(timestamp, 0) + 1
    return tuple((timestamp, counts[timestamp]) for timestamp in sorted(counts))


def _keyset_hash(pairs: Sequence[tuple[str, str]]) -> str:
    return _hash([[timestamp, symbol] for timestamp, symbol in pairs])


def _assert_finite_frame(frame: pd.DataFrame, context: str) -> None:
    if frame.empty:
        raise OSS3FinalHoldoutEvaluationIntegrityError(f"{context} frame is empty")
    for value in frame.to_numpy().reshape(-1):
        if not isfinite(float(value)):
            raise OSS3FinalHoldoutEvaluationIntegrityError(
                f"{context} frame contains non-finite data"
            )


def _reject_broker_credentials() -> None:
    present = sorted(
        key
        for key, value in os.environ.items()
        if value and any(key.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES)
    )
    if present:
        raise OSS3FinalHoldoutEvaluationGovernanceError(
            "D2K refuses broker/exchange credential variables: " + ",".join(present)
        )


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _parse_canonical_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat()
    if value != canonical:
        raise ValueError(f"{name} must use canonical UTC serialization")
    return parsed


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_finite(value: object, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
    ):
        raise ValueError(f"{name} must be finite")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
