"""OSS-3D2O isolated economic-prediction provenance runner.

D2O closes the material-provenance gap left intentionally explicit by D2N.
It proves that one economic ``QlibPredictionArtifact`` was produced by replaying
the exact frozen D2I/D2L winner on the exact original TRAIN bundle, against one
hash-bound point-in-time economic feature artifact, under the exact source D2G
runtime environment and frozen Qlib model contract.

D2O is deliberately narrower than D2N:
- it receives feature vectors, not economic market bars;
- those feature vectors may be market-derived and are therefore explicitly
  marked as observed by the runner;
- it receives no economic labels, forward returns, PnL, fills or gate results;
- it cannot select/retune a model or portfolio policy;
- it never touches D2K/D2N permits or SQLite state;
- it grants no broker, OMS, Safety, OrderIntent, PAPER, capital or LIVE authority.

The feature artifact proves the exact input consumed by the model.  It does not
by itself prove that an upstream feature-engineering producer transformed raw
market data correctly; that upstream derivation remains a separately auditable
lineage concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import re
from typing import Mapping

from autotrade.research.oss3_development_inference import DevelopmentInferenceRequest
from autotrade.research.oss3_factor_matrix_artifact import FactorMatrixArtifact
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact, QlibPredictionRow
from autotrade.research.oss3_supervised_label_artifact import SupervisedLabelArtifact
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact

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
from .final_holdout_protocol import OSS3FinalHoldoutProtocolReceipt
from .network_guard import deny_network
from .predictive_economic_protocol import OSS3PredictiveEconomicProtocolReceipt
from .predictive_strategy_contract import OSS3PredictiveStrategyPreregistrationReceipt


OSS3D2O_FEATURE_ARTIFACT_VERSION = "OSS3D2O_ECONOMIC_FEATURE_ARTIFACT_V1"
OSS3D2O_PROVENANCE_VERSION = "OSS3D2O_ECONOMIC_PREDICTION_PROVENANCE_V1"
POINT_IN_TIME_POLICY = "AVAILABLE_AT_LE_AS_OF"
SUPPORT_POLICY = "EVERY_NONTERMINAL_ECONOMIC_BAR_CLOSE_FULL_UNIVERSE_V1"
MAX_FEATURE_ROWS = 2_000_000
MAX_FEATURES = 512

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,31}$")

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

SEMANTIC_FILES = (
    "labs/oss3_qlib/economic_prediction_provenance.py",
    "labs/oss3_qlib/predictive_economic_protocol.py",
    "labs/oss3_qlib/predictive_strategy_contract.py",
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
    "src/autotrade/research/oss3_qlib_artifact.py",
)


class EconomicPredictionProvenanceError(RuntimeError):
    """Base D2O failure."""


class EconomicPredictionProvenanceIntegrityError(EconomicPredictionProvenanceError):
    """Frozen model/input/runtime identity drifted."""


class EconomicPredictionProvenanceGovernanceError(EconomicPredictionProvenanceError):
    """Operation exceeds the no-outcome research boundary."""


@dataclass(frozen=True, slots=True)
class EconomicFeatureRow:
    """One point-in-time feature vector for the economic prediction window."""

    as_of: str
    available_at: str
    symbol: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        as_of = _parse_utc(self.as_of, "feature as_of")
        available = _parse_utc(self.available_at, "feature available_at")
        if available > as_of:
            raise EconomicPredictionProvenanceGovernanceError(
                "economic feature was not available at as_of"
            )
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("invalid economic feature symbol")
        if not isinstance(self.values, tuple) or not self.values:
            raise ValueError("economic feature values must be a non-empty tuple")
        for value in self.values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("economic feature value must be numeric")
            if not isfinite(float(value)):
                raise EconomicPredictionProvenanceGovernanceError(
                    "economic feature values must be finite"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of,
            "available_at": self.available_at,
            "symbol": self.symbol,
            "values": [float(value) for value in self.values],
        }


@dataclass(frozen=True, slots=True)
class EconomicPredictionFeatureArtifact:
    """Exact feature payload consumed by D2O, bound to one D2M commitment."""

    artifact_version: str
    economic_protocol_id: str
    economic_protocol_receipt_hash: str
    economic_holdout_commitment_fingerprint: str
    source_universe_hash: str
    source_dataset_set_hash: str
    feature_source_hash: str
    feature_producer_code_hash: str
    feature_schema_hash: str
    feature_names: tuple[str, ...]
    symbols: tuple[str, ...]
    timeframe_seconds: int
    partition_start: str
    partition_end: str
    bar_count: int
    support_policy: str
    point_in_time_policy: str
    rows: tuple[EconomicFeatureRow, ...]
    market_derived_features_observed: bool
    economic_labels_included: bool
    economic_outcomes_included: bool
    adaptive_feature_search: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    artifact_hash: str

    def __post_init__(self) -> None:
        if self.artifact_version != OSS3D2O_FEATURE_ARTIFACT_VERSION:
            raise EconomicPredictionProvenanceIntegrityError(
                "noncanonical D2O feature artifact version"
            )
        _require_id(self.economic_protocol_id, "economic_protocol_id")
        for name in (
            "economic_protocol_receipt_hash",
            "economic_holdout_commitment_fingerprint",
            "source_universe_hash",
            "source_dataset_set_hash",
            "feature_source_hash",
            "feature_producer_code_hash",
            "feature_schema_hash",
            "artifact_hash",
        ):
            _require_hash(getattr(self, name), name)
        if (
            not isinstance(self.feature_names, tuple)
            or not self.feature_names
            or len(self.feature_names) > MAX_FEATURES
            or len(set(self.feature_names)) != len(self.feature_names)
            or any(not isinstance(name, str) or not name for name in self.feature_names)
        ):
            raise EconomicPredictionProvenanceIntegrityError(
                "economic feature names must be unique non-empty strings"
            )
        if self.symbols != tuple(sorted(self.symbols)) or len(set(self.symbols)) != len(self.symbols):
            raise EconomicPredictionProvenanceIntegrityError(
                "economic feature symbols must be unique canonical sorted order"
            )
        if len(self.symbols) < 3 or any(not _SYMBOL_RE.fullmatch(symbol) for symbol in self.symbols):
            raise EconomicPredictionProvenanceGovernanceError(
                "economic feature artifact requires at least three valid symbols"
            )
        if (
            isinstance(self.timeframe_seconds, bool)
            or not isinstance(self.timeframe_seconds, int)
            or self.timeframe_seconds <= 0
        ):
            raise ValueError("timeframe_seconds must be positive integer")
        if isinstance(self.bar_count, bool) or not isinstance(self.bar_count, int) or self.bar_count < 2:
            raise ValueError("bar_count must be integer >= 2")
        start = _parse_utc(self.partition_start, "partition_start")
        end = _parse_utc(self.partition_end, "partition_end")
        if not start < end:
            raise ValueError("economic feature partition must be positive")
        if start + timedelta(seconds=self.timeframe_seconds * self.bar_count) != end:
            raise EconomicPredictionProvenanceIntegrityError(
                "economic feature partition/bar_count/timeframe are inconsistent"
            )
        if self.support_policy != SUPPORT_POLICY or self.point_in_time_policy != POINT_IN_TIME_POLICY:
            raise EconomicPredictionProvenanceGovernanceError(
                "economic feature support/point-in-time policy drifted"
            )
        if not isinstance(self.rows, tuple) or not self.rows or len(self.rows) > MAX_FEATURE_ROWS:
            raise EconomicPredictionProvenanceGovernanceError(
                "economic feature row count outside D2O bound"
            )
        if any(not isinstance(row, EconomicFeatureRow) for row in self.rows):
            raise TypeError("rows must contain EconomicFeatureRow")
        if any(len(row.values) != len(self.feature_names) for row in self.rows):
            raise EconomicPredictionProvenanceIntegrityError(
                "economic feature width differs from schema"
            )
        expected_keys = _expected_support(
            start=start,
            timeframe_seconds=self.timeframe_seconds,
            bar_count=self.bar_count,
            symbols=self.symbols,
        )
        observed_keys = tuple((row.as_of, row.symbol) for row in self.rows)
        if observed_keys != expected_keys:
            raise EconomicPredictionProvenanceIntegrityError(
                "economic feature support differs from committed clock/universe"
            )
        if self.market_derived_features_observed is not True:
            raise EconomicPredictionProvenanceGovernanceError(
                "D2O must explicitly acknowledge observed market-derived features"
            )
        if self.economic_labels_included or self.economic_outcomes_included or self.adaptive_feature_search:
            raise EconomicPredictionProvenanceGovernanceError(
                "D2O feature artifact may not contain outcomes/labels or adaptive feature search"
            )
        _deny_authority(
            execution_authorized=self.execution_authorized,
            paper_execution_authorized=self.paper_execution_authorized,
            capital_authority=self.capital_authority,
            live_trading=self.live_trading,
        )
        if self.artifact_hash != _hash(self.to_dict(include_hash=False)):
            raise EconomicPredictionProvenanceIntegrityError(
                "economic feature artifact hash mismatch"
            )

    @classmethod
    def build(
        cls,
        *,
        economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
        d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
        feature_source_hash: str,
        feature_producer_code_hash: str,
        feature_names: tuple[str, ...],
        rows: tuple[EconomicFeatureRow, ...],
    ) -> "EconomicPredictionFeatureArtifact":
        if not isinstance(economic_protocol, OSS3PredictiveEconomicProtocolReceipt):
            raise TypeError("economic_protocol must be OSS3PredictiveEconomicProtocolReceipt")
        if not isinstance(d2l_receipt, OSS3PredictiveStrategyPreregistrationReceipt):
            raise TypeError("d2l_receipt must be OSS3PredictiveStrategyPreregistrationReceipt")
        _verify_d2m_d2l(economic_protocol=economic_protocol, d2l_receipt=d2l_receipt)
        _require_hash(feature_source_hash, "feature_source_hash")
        _require_hash(feature_producer_code_hash, "feature_producer_code_hash")
        commitment = economic_protocol.economic_holdout_commitment
        values: dict[str, object] = {
            "artifact_version": OSS3D2O_FEATURE_ARTIFACT_VERSION,
            "economic_protocol_id": economic_protocol.economic_protocol_id,
            "economic_protocol_receipt_hash": economic_protocol.receipt_hash,
            "economic_holdout_commitment_fingerprint": commitment.fingerprint,
            "source_universe_hash": commitment.universe_hash,
            "source_dataset_set_hash": commitment.source_dataset_set_hash,
            "feature_source_hash": feature_source_hash,
            "feature_producer_code_hash": feature_producer_code_hash,
            "feature_schema_hash": d2l_receipt.binding.feature_schema_hash,
            "feature_names": feature_names,
            "symbols": commitment.symbols,
            "timeframe_seconds": commitment.timeframe_seconds,
            "partition_start": commitment.partition_start,
            "partition_end": commitment.partition_end,
            "bar_count": commitment.bar_count,
            "support_policy": SUPPORT_POLICY,
            "point_in_time_policy": POINT_IN_TIME_POLICY,
            "rows": rows,
            "market_derived_features_observed": True,
            "economic_labels_included": False,
            "economic_outcomes_included": False,
            "adaptive_feature_search": False,
            "execution_authorized": False,
            "paper_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        payload = _feature_payload(values)
        return cls(**values, artifact_hash=_hash(payload))

    @property
    def row_payload_hash(self) -> str:
        return _hash([row.to_dict() for row in self.rows])

    @property
    def support_hash(self) -> str:
        return _hash([[row.as_of, row.symbol] for row in self.rows])

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload = _feature_payload(
            {
                "artifact_version": self.artifact_version,
                "economic_protocol_id": self.economic_protocol_id,
                "economic_protocol_receipt_hash": self.economic_protocol_receipt_hash,
                "economic_holdout_commitment_fingerprint": self.economic_holdout_commitment_fingerprint,
                "source_universe_hash": self.source_universe_hash,
                "source_dataset_set_hash": self.source_dataset_set_hash,
                "feature_source_hash": self.feature_source_hash,
                "feature_producer_code_hash": self.feature_producer_code_hash,
                "feature_schema_hash": self.feature_schema_hash,
                "feature_names": self.feature_names,
                "symbols": self.symbols,
                "timeframe_seconds": self.timeframe_seconds,
                "partition_start": self.partition_start,
                "partition_end": self.partition_end,
                "bar_count": self.bar_count,
                "support_policy": self.support_policy,
                "point_in_time_policy": self.point_in_time_policy,
                "rows": self.rows,
                "market_derived_features_observed": self.market_derived_features_observed,
                "economic_labels_included": self.economic_labels_included,
                "economic_outcomes_included": self.economic_outcomes_included,
                "adaptive_feature_search": self.adaptive_feature_search,
                "execution_authorized": self.execution_authorized,
                "paper_execution_authorized": self.paper_execution_authorized,
                "capital_authority": self.capital_authority,
                "live_trading": self.live_trading,
            }
        )
        if include_hash:
            payload["artifact_hash"] = self.artifact_hash
        return payload


@dataclass(frozen=True, slots=True)
class OSS3EconomicPredictionProvenanceReceipt:
    receipt_version: str
    economic_protocol_id: str
    economic_protocol_receipt_hash: str
    source_d2l_receipt_hash: str
    source_d2l_binding_hash: str
    source_d2j_protocol_id: str
    source_d2j_protocol_receipt_hash: str
    source_request_hash: str
    training_bundle_hash: str
    train_feature_artifact_hash: str
    train_label_artifact_hash: str
    model_family: str
    model_config_hash: str
    qlib_version: str
    shared_model_runner_code_hash: str
    provenance_runner_semantic_hash: str
    source_environment_attestation_hash: str
    source_runtime_environment_hash: str
    observed_environment_attestation_hash: str
    observed_runtime_environment_hash: str
    economic_feature_artifact_hash: str
    economic_feature_row_payload_hash: str
    economic_feature_support_hash: str
    prediction_artifact_hash: str
    prediction_payload_hash: str
    prediction_generated_by_qlib: bool
    original_train_bundle_replayed: bool
    training_labels_loaded: bool
    market_derived_features_loaded: bool
    economic_labels_loaded: bool
    economic_outcomes_loaded: bool
    network_allowed: bool
    broker_credentials_present: bool
    adaptive_search: bool
    hyperparameter_optimization: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D2O_PROVENANCE_VERSION:
            raise EconomicPredictionProvenanceIntegrityError(
                "noncanonical D2O provenance receipt version"
            )
        for name in ("economic_protocol_id", "source_d2j_protocol_id", "model_family"):
            _require_id(getattr(self, name), name)
        for name in (
            "economic_protocol_receipt_hash",
            "source_d2l_receipt_hash",
            "source_d2l_binding_hash",
            "source_d2j_protocol_receipt_hash",
            "source_request_hash",
            "training_bundle_hash",
            "train_feature_artifact_hash",
            "train_label_artifact_hash",
            "model_config_hash",
            "shared_model_runner_code_hash",
            "provenance_runner_semantic_hash",
            "source_environment_attestation_hash",
            "source_runtime_environment_hash",
            "observed_environment_attestation_hash",
            "observed_runtime_environment_hash",
            "economic_feature_artifact_hash",
            "economic_feature_row_payload_hash",
            "economic_feature_support_hash",
            "prediction_artifact_hash",
            "prediction_payload_hash",
            "receipt_hash",
        ):
            _require_hash(getattr(self, name), name)
        if self.qlib_version != QLIB_VERSION:
            raise EconomicPredictionProvenanceGovernanceError("D2O requires exact Qlib version")
        if (
            self.prediction_generated_by_qlib is not True
            or self.original_train_bundle_replayed is not True
            or self.training_labels_loaded is not True
            or self.market_derived_features_loaded is not True
        ):
            raise EconomicPredictionProvenanceIntegrityError(
                "D2O provenance must prove Qlib generation, TRAIN replay and feature loading"
            )
        if (
            self.economic_labels_loaded
            or self.economic_outcomes_loaded
            or self.network_allowed
            or self.broker_credentials_present
            or self.adaptive_search
            or self.hyperparameter_optimization
        ):
            raise EconomicPredictionProvenanceGovernanceError(
                "D2O provenance exceeds frozen no-outcome/no-network boundary"
            )
        if self.source_environment_attestation_hash != self.observed_environment_attestation_hash:
            raise EconomicPredictionProvenanceIntegrityError(
                "D2O observed environment differs from frozen D2G source"
            )
        if self.source_runtime_environment_hash != self.observed_runtime_environment_hash:
            raise EconomicPredictionProvenanceIntegrityError(
                "D2O observed runtime differs from frozen D2G source"
            )
        if self.shared_model_runner_code_hash != family_runner_code_hash():
            raise EconomicPredictionProvenanceIntegrityError(
                "D2O shared model runner semantic identity drifted"
            )
        if self.provenance_runner_semantic_hash != economic_prediction_provenance_semantic_hash():
            raise EconomicPredictionProvenanceIntegrityError(
                "D2O provenance runner semantic identity drifted"
            )
        _deny_authority(
            execution_authorized=self.execution_authorized,
            paper_execution_authorized=self.paper_execution_authorized,
            capital_authority=self.capital_authority,
            live_trading=self.live_trading,
        )
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise EconomicPredictionProvenanceIntegrityError(
                "D2O provenance receipt hash mismatch"
            )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "receipt_version": self.receipt_version,
            "economic_protocol_id": self.economic_protocol_id,
            "economic_protocol_receipt_hash": self.economic_protocol_receipt_hash,
            "source_d2l_receipt_hash": self.source_d2l_receipt_hash,
            "source_d2l_binding_hash": self.source_d2l_binding_hash,
            "source_d2j_protocol_id": self.source_d2j_protocol_id,
            "source_d2j_protocol_receipt_hash": self.source_d2j_protocol_receipt_hash,
            "source_request_hash": self.source_request_hash,
            "training_bundle_hash": self.training_bundle_hash,
            "train_feature_artifact_hash": self.train_feature_artifact_hash,
            "train_label_artifact_hash": self.train_label_artifact_hash,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "qlib_version": self.qlib_version,
            "shared_model_runner_code_hash": self.shared_model_runner_code_hash,
            "provenance_runner_semantic_hash": self.provenance_runner_semantic_hash,
            "source_environment_attestation_hash": self.source_environment_attestation_hash,
            "source_runtime_environment_hash": self.source_runtime_environment_hash,
            "observed_environment_attestation_hash": self.observed_environment_attestation_hash,
            "observed_runtime_environment_hash": self.observed_runtime_environment_hash,
            "economic_feature_artifact_hash": self.economic_feature_artifact_hash,
            "economic_feature_row_payload_hash": self.economic_feature_row_payload_hash,
            "economic_feature_support_hash": self.economic_feature_support_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "prediction_payload_hash": self.prediction_payload_hash,
            "prediction_generated_by_qlib": self.prediction_generated_by_qlib,
            "original_train_bundle_replayed": self.original_train_bundle_replayed,
            "training_labels_loaded": self.training_labels_loaded,
            "market_derived_features_loaded": self.market_derived_features_loaded,
            "economic_labels_loaded": self.economic_labels_loaded,
            "economic_outcomes_loaded": self.economic_outcomes_loaded,
            "network_allowed": self.network_allowed,
            "broker_credentials_present": self.broker_credentials_present,
            "adaptive_search": self.adaptive_search,
            "hyperparameter_optimization": self.hyperparameter_optimization,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }
        if include_hash:
            payload["receipt_hash"] = self.receipt_hash
        return payload


class QlibEconomicFeatureDatasetAdapter:
    """Exact original TRAIN bundle plus feature-only economic inference rows."""

    def __init__(
        self,
        *,
        train_features: FactorMatrixArtifact,
        train_labels: SupervisedLabelArtifact,
        economic_features: EconomicPredictionFeatureArtifact,
    ) -> None:
        if train_features.manifest.partition != "TRAIN" or train_labels.manifest.partition != "TRAIN":
            raise EconomicPredictionProvenanceGovernanceError(
                "D2O may fit only on the original TRAIN partition"
            )
        feature_names = tuple(feature.name for feature in train_features.features)
        if feature_names != economic_features.feature_names:
            raise EconomicPredictionProvenanceIntegrityError(
                "economic feature names differ from original TRAIN schema"
            )
        train_keys = tuple((row.as_of, row.symbol) for row in train_features.rows)
        label_keys = tuple((row.label_as_of, row.symbol) for row in train_labels.rows)
        if train_keys != label_keys:
            raise EconomicPredictionProvenanceIntegrityError(
                "TRAIN feature/label keyset mismatch"
            )

        import pandas as pd

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
            [(pd.Timestamp(row.as_of), row.symbol) for row in economic_features.rows],
            names=("datetime", "instrument"),
        )
        test_columns = pd.MultiIndex.from_tuples(
            [("feature", name) for name in feature_names]
        )
        self._test = pd.DataFrame(
            [list(map(float, row.values)) for row in economic_features.rows],
            index=test_index,
            columns=test_columns,
            dtype="float64",
        )
        if not isfinite(float(self._train.to_numpy(dtype="float64").sum())):
            raise EconomicPredictionProvenanceIntegrityError("TRAIN frame is non-finite")
        if not isfinite(float(self._test.to_numpy(dtype="float64").sum())):
            raise EconomicPredictionProvenanceIntegrityError("economic feature frame is non-finite")

    def prepare(self, segment: object, col_set: object, data_key: object = "infer"):
        if segment == "train":
            if (
                data_key != "learn"
                or not isinstance(col_set, (list, tuple))
                or tuple(col_set) != ("feature", "label")
            ):
                raise EconomicPredictionProvenanceIntegrityError(
                    "unexpected Qlib TRAIN prepare contract"
                )
            return self._train.copy(deep=True)
        if segment == "test":
            if data_key != "infer" or col_set != "feature":
                raise EconomicPredictionProvenanceIntegrityError(
                    "unexpected Qlib economic inference prepare contract"
                )
            return self._test["feature"].copy(deep=True)
        raise EconomicPredictionProvenanceIntegrityError(
            "D2O Qlib adapter supports train/test only"
        )


def run_economic_prediction_provenance(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
    source_request: DevelopmentInferenceRequest,
    training_bundle: TrainingBundleArtifact,
    train_features: FactorMatrixArtifact,
    train_labels: SupervisedLabelArtifact,
    economic_features: EconomicPredictionFeatureArtifact,
) -> tuple[QlibPredictionArtifact, CandidateEnvironmentAttestation, OSS3EconomicPredictionProvenanceReceipt]:
    """Replay the frozen winner and produce one provenance-bound prediction artifact."""
    _reject_broker_credentials()
    _verify_full_lineage(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        source_request=source_request,
        training_bundle=training_bundle,
        train_features=train_features,
        train_labels=train_labels,
        economic_features=economic_features,
    )
    binding = d2l_receipt.binding
    attestation = collect_candidate_environment_attestation(
        model_config_hash=binding.model_config_hash
    )
    attestation.verify_current_contract()
    if attestation.artifact_hash != binding.environment_attestation_hash:
        raise EconomicPredictionProvenanceIntegrityError(
            "D2O environment differs from frozen D2G winner"
        )
    if attestation.runtime_environment.fingerprint != binding.runtime_environment_hash:
        raise EconomicPredictionProvenanceIntegrityError(
            "D2O model-neutral runtime differs from frozen D2G winner"
        )
    if attestation.manifest.runner_code_hash != binding.shared_runner_code_hash:
        raise EconomicPredictionProvenanceIntegrityError(
            "D2O runner identity differs from frozen D2G winner"
        )

    candidate = candidate_from_config_hash(binding.model_config_hash)
    if candidate.candidate_id != binding.selected_trial_id:
        raise EconomicPredictionProvenanceIntegrityError(
            "D2O candidate differs from frozen DEVELOPMENT winner"
        )
    config = candidate_runtime_config(candidate)
    dataset = QlibEconomicFeatureDatasetAdapter(
        train_features=train_features,
        train_labels=train_labels,
        economic_features=economic_features,
    )
    with deny_network():
        import qlib
        from qlib.contrib.model.linear import LinearModel

        actual_version = str(getattr(qlib, "__version__", ""))
        if actual_version != QLIB_VERSION:
            raise EconomicPredictionProvenanceIntegrityError(
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

    rows = _prediction_rows(scores=scores, economic_features=economic_features)
    request = source_request.manifest
    prediction = QlibPredictionArtifact.build(
        qlib_version=QLIB_VERSION,
        model_family=binding.model_family,
        model_config_hash=binding.model_config_hash,
        training_dataset_hash=training_bundle.artifact_hash,
        feature_schema_hash=binding.feature_schema_hash,
        producer_code_hash=binding.shared_runner_code_hash,
        train_start=datetime.fromisoformat(request.train_start),
        train_end=datetime.fromisoformat(request.train_end),
        inference_start=datetime.fromisoformat(economic_features.rows[0].as_of),
        inference_end=datetime.fromisoformat(economic_features.partition_end),
        rows=rows,
    )
    receipt = _build_receipt(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        source_request=source_request,
        training_bundle=training_bundle,
        train_features=train_features,
        train_labels=train_labels,
        economic_features=economic_features,
        prediction=prediction,
        attestation=attestation,
    )
    verify_economic_prediction_provenance(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        source_request=source_request,
        training_bundle=training_bundle,
        train_features=train_features,
        train_labels=train_labels,
        economic_features=economic_features,
        prediction=prediction,
        attestation=attestation,
        receipt=receipt,
    )
    return prediction, attestation, receipt


def verify_economic_prediction_provenance(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
    source_request: DevelopmentInferenceRequest,
    training_bundle: TrainingBundleArtifact,
    train_features: FactorMatrixArtifact,
    train_labels: SupervisedLabelArtifact,
    economic_features: EconomicPredictionFeatureArtifact,
    prediction: QlibPredictionArtifact,
    attestation: CandidateEnvironmentAttestation,
    receipt: OSS3EconomicPredictionProvenanceReceipt,
) -> None:
    """Rebind one D2O receipt to all concrete model/input/runtime outputs."""
    _verify_full_lineage(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        source_request=source_request,
        training_bundle=training_bundle,
        train_features=train_features,
        train_labels=train_labels,
        economic_features=economic_features,
    )
    binding = d2l_receipt.binding
    if attestation.artifact_hash != binding.environment_attestation_hash:
        raise EconomicPredictionProvenanceIntegrityError("provenance attestation mismatch")
    if attestation.runtime_environment.fingerprint != binding.runtime_environment_hash:
        raise EconomicPredictionProvenanceIntegrityError("provenance runtime mismatch")
    manifest = prediction.manifest
    for name, expected in (
        ("model_family", binding.model_family),
        ("model_config_hash", binding.model_config_hash),
        ("qlib_version", binding.qlib_version),
        ("training_dataset_hash", training_bundle.artifact_hash),
        ("feature_schema_hash", binding.feature_schema_hash),
        ("producer_code_hash", binding.shared_runner_code_hash),
    ):
        if getattr(manifest, name) != expected:
            raise EconomicPredictionProvenanceIntegrityError(
                f"provenance prediction {name} mismatch"
            )
    expected_keys = tuple((row.as_of, row.symbol) for row in economic_features.rows)
    actual_keys = tuple((row.timestamp, row.symbol) for row in prediction.rows)
    if actual_keys != expected_keys:
        raise EconomicPredictionProvenanceIntegrityError(
            "provenance prediction support differs from economic feature artifact"
        )
    expected = _build_receipt(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        d2j_protocol=d2j_protocol,
        source_request=source_request,
        training_bundle=training_bundle,
        train_features=train_features,
        train_labels=train_labels,
        economic_features=economic_features,
        prediction=prediction,
        attestation=attestation,
    )
    if receipt != expected:
        raise EconomicPredictionProvenanceIntegrityError(
            "D2O receipt does not rebind to concrete provenance outputs"
        )


def economic_prediction_provenance_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    payload: list[dict[str, str]] = []
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise EconomicPredictionProvenanceIntegrityError(
                f"D2O semantic file is missing: {relative}"
            )
        payload.append(
            {"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()}
        )
    return _hash(payload)


def _verify_full_lineage(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
    source_request: DevelopmentInferenceRequest,
    training_bundle: TrainingBundleArtifact,
    train_features: FactorMatrixArtifact,
    train_labels: SupervisedLabelArtifact,
    economic_features: EconomicPredictionFeatureArtifact,
) -> None:
    for value, expected_type, name in (
        (economic_protocol, OSS3PredictiveEconomicProtocolReceipt, "economic_protocol"),
        (d2l_receipt, OSS3PredictiveStrategyPreregistrationReceipt, "d2l_receipt"),
        (d2j_protocol, OSS3FinalHoldoutProtocolReceipt, "d2j_protocol"),
        (source_request, DevelopmentInferenceRequest, "source_request"),
        (training_bundle, TrainingBundleArtifact, "training_bundle"),
        (train_features, FactorMatrixArtifact, "train_features"),
        (train_labels, SupervisedLabelArtifact, "train_labels"),
        (economic_features, EconomicPredictionFeatureArtifact, "economic_features"),
    ):
        if not isinstance(value, expected_type):
            raise TypeError(f"{name} must be {expected_type.__name__}")
    _verify_d2m_d2l(economic_protocol=economic_protocol, d2l_receipt=d2l_receipt)
    binding = d2l_receipt.binding
    if economic_protocol.source_d2j_protocol_id != d2j_protocol.protocol_id:
        raise EconomicPredictionProvenanceIntegrityError("D2O D2M/D2J protocol mismatch")
    if economic_protocol.source_d2j_protocol_receipt_hash != d2j_protocol.receipt_hash:
        raise EconomicPredictionProvenanceIntegrityError("D2O D2M/D2J receipt mismatch")
    if d2l_receipt.protocol_id != d2j_protocol.protocol_id:
        raise EconomicPredictionProvenanceIntegrityError("D2O D2L/D2J protocol mismatch")
    if source_request.request_hash != binding.request_hash:
        raise EconomicPredictionProvenanceIntegrityError(
            "D2O source request differs from frozen winner"
        )
    request = source_request.manifest
    for name, expected, actual in (
        ("model_family", binding.model_family, request.model_family),
        ("model_config_hash", binding.model_config_hash, request.model_config_hash),
        ("runner_code_hash", binding.shared_runner_code_hash, request.expected_runner_code_hash),
        ("qlib_version", binding.qlib_version, request.required_qlib_version),
        ("training_bundle", binding.training_dataset_hash, training_bundle.artifact_hash),
        ("feature_schema", binding.feature_schema_hash, request.feature_schema_hash),
    ):
        if expected != actual:
            raise EconomicPredictionProvenanceIntegrityError(f"D2O {name} mismatch")
    if binding.shared_runner_code_hash != family_runner_code_hash():
        raise EconomicPredictionProvenanceIntegrityError(
            "D2O frozen D2G semantic runner has drifted"
        )
    rebuilt = TrainingBundleArtifact.build(features=train_features, labels=train_labels)
    if rebuilt.artifact_hash != training_bundle.artifact_hash:
        raise EconomicPredictionProvenanceIntegrityError(
            "D2O concrete TRAIN artifacts do not reproduce source bundle"
        )
    if request.train_feature_artifact_hash != train_features.artifact_hash:
        raise EconomicPredictionProvenanceIntegrityError("D2O TRAIN feature artifact mismatch")
    if request.train_label_artifact_hash != train_labels.artifact_hash:
        raise EconomicPredictionProvenanceIntegrityError("D2O TRAIN label artifact mismatch")
    if train_features.manifest.partition != "TRAIN" or train_labels.manifest.partition != "TRAIN":
        raise EconomicPredictionProvenanceGovernanceError("D2O forbids refit outside TRAIN")
    if economic_features.economic_protocol_id != economic_protocol.economic_protocol_id:
        raise EconomicPredictionProvenanceIntegrityError("D2O feature/D2M protocol mismatch")
    if economic_features.economic_protocol_receipt_hash != economic_protocol.receipt_hash:
        raise EconomicPredictionProvenanceIntegrityError("D2O feature/D2M receipt mismatch")
    commitment = economic_protocol.economic_holdout_commitment
    for name, expected, actual in (
        ("economic commitment", commitment.fingerprint, economic_features.economic_holdout_commitment_fingerprint),
        ("universe", commitment.universe_hash, economic_features.source_universe_hash),
        ("dataset set", commitment.source_dataset_set_hash, economic_features.source_dataset_set_hash),
        ("feature schema", binding.feature_schema_hash, economic_features.feature_schema_hash),
        ("symbols", commitment.symbols, economic_features.symbols),
        ("timeframe", commitment.timeframe_seconds, economic_features.timeframe_seconds),
        ("partition_start", commitment.partition_start, economic_features.partition_start),
        ("partition_end", commitment.partition_end, economic_features.partition_end),
        ("bar_count", commitment.bar_count, economic_features.bar_count),
    ):
        if expected != actual:
            raise EconomicPredictionProvenanceIntegrityError(
                f"D2O economic feature {name} mismatch"
            )
    train_feature_names = tuple(feature.name for feature in train_features.features)
    if train_feature_names != economic_features.feature_names:
        raise EconomicPredictionProvenanceIntegrityError(
            "D2O economic feature names differ from original TRAIN schema"
        )


def _verify_d2m_d2l(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
) -> None:
    binding = d2l_receipt.binding
    if economic_protocol.source_d2l_receipt_hash != d2l_receipt.receipt_hash:
        raise EconomicPredictionProvenanceIntegrityError("D2O D2M/D2L receipt mismatch")
    if economic_protocol.source_d2l_binding_hash != binding.binding_hash:
        raise EconomicPredictionProvenanceIntegrityError("D2O D2M/D2L binding mismatch")
    if economic_protocol.source_strategy_semantic_hash != binding.strategy_semantic_hash:
        raise EconomicPredictionProvenanceIntegrityError("D2O strategy semantic mismatch")


def _prediction_rows(
    *,
    scores: object,
    economic_features: EconomicPredictionFeatureArtifact,
) -> tuple[QlibPredictionRow, ...]:
    if not hasattr(scores, "items"):
        raise EconomicPredictionProvenanceIntegrityError(
            "Qlib economic prediction is not indexed"
        )
    expected_keys = tuple((row.as_of, row.symbol) for row in economic_features.rows)
    observed: list[tuple[str, str]] = []
    values: list[float] = []
    for key, score in scores.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise EconomicPredictionProvenanceIntegrityError(
                "Qlib prediction index must be (datetime,instrument)"
            )
        timestamp, symbol = key
        if not hasattr(timestamp, "to_pydatetime"):
            raise EconomicPredictionProvenanceIntegrityError(
                "Qlib prediction timestamp is invalid"
            )
        as_of = timestamp.to_pydatetime()
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise EconomicPredictionProvenanceIntegrityError(
                "Qlib prediction timestamp lost timezone"
            )
        observed.append((as_of.isoformat(), str(symbol)))
        numeric = float(score)
        if not isfinite(numeric):
            raise EconomicPredictionProvenanceIntegrityError(
                "Qlib economic prediction contains non-finite score"
            )
        values.append(numeric)
    if tuple(observed) != expected_keys:
        raise EconomicPredictionProvenanceIntegrityError(
            "Qlib economic prediction support drifted from feature artifact"
        )
    return tuple(
        QlibPredictionRow(timestamp=timestamp, symbol=symbol, score=score)
        for (timestamp, symbol), score in zip(observed, values, strict=True)
    )


def _build_receipt(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
    source_request: DevelopmentInferenceRequest,
    training_bundle: TrainingBundleArtifact,
    train_features: FactorMatrixArtifact,
    train_labels: SupervisedLabelArtifact,
    economic_features: EconomicPredictionFeatureArtifact,
    prediction: QlibPredictionArtifact,
    attestation: CandidateEnvironmentAttestation,
) -> OSS3EconomicPredictionProvenanceReceipt:
    binding = d2l_receipt.binding
    values: dict[str, object] = {
        "receipt_version": OSS3D2O_PROVENANCE_VERSION,
        "economic_protocol_id": economic_protocol.economic_protocol_id,
        "economic_protocol_receipt_hash": economic_protocol.receipt_hash,
        "source_d2l_receipt_hash": d2l_receipt.receipt_hash,
        "source_d2l_binding_hash": binding.binding_hash,
        "source_d2j_protocol_id": d2j_protocol.protocol_id,
        "source_d2j_protocol_receipt_hash": d2j_protocol.receipt_hash,
        "source_request_hash": source_request.request_hash,
        "training_bundle_hash": training_bundle.artifact_hash,
        "train_feature_artifact_hash": train_features.artifact_hash,
        "train_label_artifact_hash": train_labels.artifact_hash,
        "model_family": binding.model_family,
        "model_config_hash": binding.model_config_hash,
        "qlib_version": binding.qlib_version,
        "shared_model_runner_code_hash": binding.shared_runner_code_hash,
        "provenance_runner_semantic_hash": economic_prediction_provenance_semantic_hash(),
        "source_environment_attestation_hash": binding.environment_attestation_hash,
        "source_runtime_environment_hash": binding.runtime_environment_hash,
        "observed_environment_attestation_hash": attestation.artifact_hash,
        "observed_runtime_environment_hash": attestation.runtime_environment.fingerprint,
        "economic_feature_artifact_hash": economic_features.artifact_hash,
        "economic_feature_row_payload_hash": economic_features.row_payload_hash,
        "economic_feature_support_hash": economic_features.support_hash,
        "prediction_artifact_hash": prediction.artifact_hash,
        "prediction_payload_hash": prediction.manifest.prediction_payload_hash,
        "prediction_generated_by_qlib": True,
        "original_train_bundle_replayed": True,
        "training_labels_loaded": True,
        "market_derived_features_loaded": True,
        "economic_labels_loaded": False,
        "economic_outcomes_loaded": False,
        "network_allowed": False,
        "broker_credentials_present": False,
        "adaptive_search": False,
        "hyperparameter_optimization": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS3EconomicPredictionProvenanceReceipt(
        **values,
        receipt_hash=_hash(values),
    )


def _expected_support(
    *,
    start: datetime,
    timeframe_seconds: int,
    bar_count: int,
    symbols: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    step = timedelta(seconds=timeframe_seconds)
    return tuple(
        ((start + step * index).isoformat(), symbol)
        for index in range(1, bar_count)
        for symbol in symbols
    )


def _feature_payload(values: Mapping[str, object]) -> dict[str, object]:
    rows = values["rows"]
    feature_names = values["feature_names"]
    symbols = values["symbols"]
    return {
        "artifact_version": values["artifact_version"],
        "economic_protocol_id": values["economic_protocol_id"],
        "economic_protocol_receipt_hash": values["economic_protocol_receipt_hash"],
        "economic_holdout_commitment_fingerprint": values["economic_holdout_commitment_fingerprint"],
        "source_universe_hash": values["source_universe_hash"],
        "source_dataset_set_hash": values["source_dataset_set_hash"],
        "feature_source_hash": values["feature_source_hash"],
        "feature_producer_code_hash": values["feature_producer_code_hash"],
        "feature_schema_hash": values["feature_schema_hash"],
        "feature_names": list(feature_names),
        "symbols": list(symbols),
        "timeframe_seconds": values["timeframe_seconds"],
        "partition_start": values["partition_start"],
        "partition_end": values["partition_end"],
        "bar_count": values["bar_count"],
        "support_policy": values["support_policy"],
        "point_in_time_policy": values["point_in_time_policy"],
        "rows": [row.to_dict() for row in rows],
        "market_derived_features_observed": values["market_derived_features_observed"],
        "economic_labels_included": values["economic_labels_included"],
        "economic_outcomes_included": values["economic_outcomes_included"],
        "adaptive_feature_search": values["adaptive_feature_search"],
        "execution_authorized": values["execution_authorized"],
        "paper_execution_authorized": values["paper_execution_authorized"],
        "capital_authority": values["capital_authority"],
        "live_trading": values["live_trading"],
    }


def _reject_broker_credentials() -> None:
    present = sorted(
        key
        for key, value in os.environ.items()
        if value and any(key.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES)
    )
    if present:
        raise EconomicPredictionProvenanceGovernanceError(
            "OSS-3D2O refuses broker/exchange credential variables: " + ",".join(present)
        )


def _deny_authority(
    *,
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise EconomicPredictionProvenanceGovernanceError(
            "D2O cannot authorize execution"
        )
    if capital_authority != "NONE" or live_trading != "BLOCKED":
        raise EconomicPredictionProvenanceGovernanceError(
            "D2O cannot grant capital or LIVE authority"
        )


def _parse_utc(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    canonical = parsed.astimezone(timezone.utc)
    if canonical.isoformat() != value:
        raise ValueError(f"{name} must be canonical UTC ISO-8601")
    return canonical


def _require_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
