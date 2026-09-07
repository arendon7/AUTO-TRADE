"""OSS-3D2R complete raw-market provenance for the supervised TRAIN bundle.

D2R reconstructs the exact OSS-3B TRAIN feature artifact, OSS-3C TRAIN label
artifact and OSS-3D1 TrainingBundleArtifact from immutable aligned raw OHLCV.
Features are causal at each sample origin. Labels deliberately observe one
future TRAIN bar because supervised targets require a horizon; that access is
fixed at one bar, explicitly declared and cannot cross the TRAIN raw material.

Schema semantics are kept separate from material lineage. Factor/label
definitions use stable canonical semantic source hashes, while each artifact's
manifest.source_dataset_hash binds the concrete raw source. This lets TRAIN and
DEVELOPMENT share an exact schema when their formulas are identical without
pretending their raw bytes are the same.

Research only: no DEVELOPMENT/FINAL_HOLDOUT/economic-holdout values, Qlib,
network, broker, OMS, Safety, OrderIntent, PAPER, capital or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from autotrade.research.market import Bar
from autotrade.research.oss3_factor_matrix_artifact import (
    FactorMatrixArtifact,
    FactorMatrixPartition,
    FactorMatrixRow,
)
from autotrade.research.oss3_supervised_label_artifact import (
    LabelDefinition,
    LabelPartition,
    SupervisedLabelArtifact,
    SupervisedLabelRow,
)
from autotrade.research.oss3_training_bundle import TrainingBundleArtifact
from autotrade.research.universe import AlignedMarketUniverse

from .economic_raw_market_feature_provenance import (
    CANONICAL_FEATURE_NAMES,
    CANONICAL_LOOKBACK_BARS,
    CanonicalFeatureFormula,
    canonical_oss3d2q_factor_definitions,
    canonical_oss3d2q_formula_registry_hash,
    canonical_oss3d2q_formulas,
)


OSS3D2R_SOURCE_VERSION = "OSS3D2R_RAW_TRAINING_MARKET_SOURCE_V1"
OSS3D2R_LABEL_FORMULA_VERSION = "OSS3D2R_FORWARD_RETURN_LABEL_FORMULA_V1"
OSS3D2R_RECEIPT_VERSION = "OSS3D2R_RAW_TRAINING_BUNDLE_PROVENANCE_V1"
FEATURE_SEMANTIC_SOURCE_ID = "AUTO-TRADE/OSS3D2Q_CANONICAL_CLOSE_FACTORS"
LABEL_SEMANTIC_SOURCE_ID = "AUTO-TRADE/OSS3D2R_CANONICAL_FORWARD_LABEL"
LABEL_NAME = "forward_return_1"
LABEL_HORIZON_BARS = 1
FEATURE_CAUSAL_POLICY = "BAR_ENDED_AT_LE_FEATURE_AS_OF_PREFIX_ONLY_V1"
LABEL_HORIZON_POLICY = "NEXT_BAR_CLOSE_WITHIN_TRAIN_ONLY_V1"
SAMPLE_ORIGIN_POLICY = "EVERY_TRAIN_BAR_CLOSE_EXCEPT_TERMINAL_BAR_V1"
EXCLUSIVE_END_POLICY = "LAST_LABEL_AVAILABILITY_PLUS_ONE_MICROSECOND_V1"
ARITHMETIC_POLICY = "DECIMAL_PRECISION_50_TO_FLOAT64_V1"

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_ZERO = Decimal("0")

SEMANTIC_FILES = (
    "labs/oss3_qlib/raw_training_bundle_provenance.py",
    "labs/oss3_qlib/economic_raw_market_feature_provenance.py",
    "src/autotrade/research/market.py",
    "src/autotrade/research/universe.py",
    "src/autotrade/research/oss3_factor_matrix_artifact.py",
    "src/autotrade/research/oss3_supervised_label_artifact.py",
    "src/autotrade/research/oss3_training_bundle.py",
)


class RawTrainingBundleProvenanceError(RuntimeError):
    pass


class RawTrainingBundleProvenanceIntegrityError(RawTrainingBundleProvenanceError):
    pass


class RawTrainingBundleProvenanceGovernanceError(RawTrainingBundleProvenanceError):
    pass


@dataclass(frozen=True, slots=True)
class CanonicalTrainingLabelFormula:
    formula_version: str
    name: str
    expression: str
    input_field: str
    horizon_bars: int
    return_convention: str
    availability_policy: str
    arithmetic_policy: str

    def __post_init__(self) -> None:
        if self.formula_version != OSS3D2R_LABEL_FORMULA_VERSION:
            raise RawTrainingBundleProvenanceIntegrityError("noncanonical D2R label formula version")
        if self.name != LABEL_NAME or self.expression != "close_t_plus_1 / close_t - 1":
            raise RawTrainingBundleProvenanceGovernanceError("D2R label semantics are frozen")
        if self.input_field != "close" or self.horizon_bars != LABEL_HORIZON_BARS:
            raise RawTrainingBundleProvenanceGovernanceError("D2R label input/horizon drifted")
        if self.return_convention != "SIMPLE_CLOSE_RETURN":
            raise RawTrainingBundleProvenanceGovernanceError("D2R label return convention drifted")
        if self.availability_policy != "AVAILABLE_AT_HORIZON_BAR_CLOSE":
            raise RawTrainingBundleProvenanceGovernanceError("D2R label availability drifted")
        if self.arithmetic_policy != ARITHMETIC_POLICY:
            raise RawTrainingBundleProvenanceGovernanceError("D2R arithmetic policy drifted")

    @property
    def formula_hash(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "formula_version": self.formula_version,
            "name": self.name,
            "expression": self.expression,
            "input_field": self.input_field,
            "horizon_bars": self.horizon_bars,
            "return_convention": self.return_convention,
            "availability_policy": self.availability_policy,
            "arithmetic_policy": self.arithmetic_policy,
        }


def canonical_oss3d2r_label_formula() -> CanonicalTrainingLabelFormula:
    return CanonicalTrainingLabelFormula(
        formula_version=OSS3D2R_LABEL_FORMULA_VERSION,
        name=LABEL_NAME,
        expression="close_t_plus_1 / close_t - 1",
        input_field="close",
        horizon_bars=1,
        return_convention="SIMPLE_CLOSE_RETURN",
        availability_policy="AVAILABLE_AT_HORIZON_BAR_CLOSE",
        arithmetic_policy=ARITHMETIC_POLICY,
    )


def canonical_oss3d2r_feature_definitions() -> tuple[object, ...]:
    """Stable schema definitions; concrete raw lineage belongs in manifest."""
    return canonical_oss3d2q_factor_definitions(
        source_id=FEATURE_SEMANTIC_SOURCE_ID,
        source_hash=canonical_oss3d2q_formula_registry_hash(),
    )


def canonical_oss3d2r_label_definition() -> LabelDefinition:
    formula = canonical_oss3d2r_label_formula()
    return LabelDefinition(
        name=formula.name,
        dtype="float64",
        role="LABEL",
        formula_hash=formula.formula_hash,
        source_id=LABEL_SEMANTIC_SOURCE_ID,
        source_hash=formula.formula_hash,
    )


@dataclass(frozen=True, slots=True)
class RawTrainingMarketSource:
    source_version: str
    warmup_universe: AlignedMarketUniverse
    training_universe: AlignedMarketUniverse
    development_values_included: bool
    final_holdout_values_included: bool
    economic_holdout_values_included: bool
    prediction_values_included: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.source_version != OSS3D2R_SOURCE_VERSION:
            raise RawTrainingBundleProvenanceIntegrityError("noncanonical D2R source version")
        if not isinstance(self.warmup_universe, AlignedMarketUniverse):
            raise TypeError("warmup_universe must be AlignedMarketUniverse")
        if not isinstance(self.training_universe, AlignedMarketUniverse):
            raise TypeError("training_universe must be AlignedMarketUniverse")
        if self.warmup_universe.symbols != self.training_universe.symbols:
            raise RawTrainingBundleProvenanceIntegrityError("warmup/TRAIN symbols differ")
        if self.warmup_universe.quote_currency != self.training_universe.quote_currency:
            raise RawTrainingBundleProvenanceIntegrityError("warmup/TRAIN quote currency differs")
        if self.warmup_universe.timeframe_seconds != self.training_universe.timeframe_seconds:
            raise RawTrainingBundleProvenanceIntegrityError("warmup/TRAIN timeframe differs")
        if self.warmup_universe.bar_count != CANONICAL_LOOKBACK_BARS:
            raise RawTrainingBundleProvenanceGovernanceError("D2R requires exactly twenty warmup bars")
        if self.training_universe.bar_count < 3:
            raise RawTrainingBundleProvenanceGovernanceError("D2R requires at least three TRAIN bars")
        for symbol in self.training_universe.symbols:
            warmup = self.warmup_universe.dataset(symbol)
            training = self.training_universe.dataset(symbol)
            if warmup.instrument != training.instrument:
                raise RawTrainingBundleProvenanceIntegrityError("warmup/TRAIN instrument metadata differs")
            if warmup.bars[-1].ended_at != training.bars[0].started_at:
                raise RawTrainingBundleProvenanceGovernanceError("warmup must end exactly at raw TRAIN start")
        if (
            self.development_values_included
            or self.final_holdout_values_included
            or self.economic_holdout_values_included
            or self.prediction_values_included
        ):
            raise RawTrainingBundleProvenanceGovernanceError("D2R source may contain TRAIN raw values only")
        _deny_authority(self.execution_authorized, self.paper_execution_authorized, self.capital_authority, self.live_trading)

    @classmethod
    def build(
        cls,
        *,
        warmup_universe: AlignedMarketUniverse,
        training_universe: AlignedMarketUniverse,
    ) -> "RawTrainingMarketSource":
        return cls(
            source_version=OSS3D2R_SOURCE_VERSION,
            warmup_universe=warmup_universe,
            training_universe=training_universe,
            development_values_included=False,
            final_holdout_values_included=False,
            economic_holdout_values_included=False,
            prediction_values_included=False,
            execution_authorized=False,
            paper_execution_authorized=False,
            capital_authority="NONE",
            live_trading="BLOCKED",
        )

    @property
    def warmup_dataset_set_hash(self) -> str:
        return _dataset_set_hash(self.warmup_universe)

    @property
    def training_dataset_set_hash(self) -> str:
        return _dataset_set_hash(self.training_universe)

    @property
    def source_hash(self) -> str:
        return _hash(
            {
                "source_version": self.source_version,
                "warmup_universe_hash": self.warmup_universe.universe_hash,
                "warmup_dataset_set_hash": self.warmup_dataset_set_hash,
                "training_universe_hash": self.training_universe.universe_hash,
                "training_dataset_set_hash": self.training_dataset_set_hash,
                "symbols": list(self.training_universe.symbols),
                "quote_currency": self.training_universe.quote_currency,
                "timeframe_seconds": self.training_universe.timeframe_seconds,
                "warmup_bar_count": self.warmup_universe.bar_count,
                "training_bar_count": self.training_universe.bar_count,
            }
        )

    @property
    def sample_count(self) -> int:
        return (self.training_universe.bar_count - 1) * len(self.training_universe.symbols)

    @property
    def partition_start(self) -> datetime:
        return self.training_universe.datasets[0].bars[0].ended_at.astimezone(timezone.utc)

    @property
    def partition_end(self) -> datetime:
        # Half-open OSS-3C windows require final available_at < partition_end.
        # The microsecond is an exclusive-bound sentinel, not an additional bar.
        return self.training_universe.datasets[0].bars[-1].ended_at.astimezone(timezone.utc) + timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class OSS3RawTrainingBundleProvenanceReceipt:
    receipt_version: str
    campaign_id: str
    research_split_hash: str
    raw_training_source_hash: str
    warmup_universe_hash: str
    warmup_dataset_set_hash: str
    training_universe_hash: str
    training_dataset_set_hash: str
    feature_formula_registry_hash: str
    feature_formula_hashes: tuple[tuple[str, str], ...]
    label_formula_hash: str
    label_definition_hash: str
    producer_semantic_hash: str
    feature_artifact_hash: str
    feature_row_payload_hash: str
    label_artifact_hash: str
    label_row_payload_hash: str
    training_bundle_hash: str
    training_bundle_manifest_hash: str
    sample_count: int
    feature_causal_prefix_enforced: bool
    feature_future_values_used: bool
    label_future_values_used: bool
    label_future_values_confined_to_explicit_horizon: bool
    label_horizon_bars: int
    label_horizon_crossed_train_boundary: bool
    exact_feature_label_keyset: bool
    schema_semantics_separated_from_material_lineage: bool
    development_values_loaded: bool
    final_holdout_values_loaded: bool
    economic_holdout_values_loaded: bool
    prediction_values_loaded: bool
    adaptive_feature_search: bool
    adaptive_label_search: bool
    adaptive_horizon_search: bool
    network_allowed: bool
    profitability_claim_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D2R_RECEIPT_VERSION:
            raise RawTrainingBundleProvenanceIntegrityError("noncanonical D2R receipt version")
        _require_id(self.campaign_id, "campaign_id")
        for name in (
            "research_split_hash", "raw_training_source_hash", "warmup_universe_hash",
            "warmup_dataset_set_hash", "training_universe_hash", "training_dataset_set_hash",
            "feature_formula_registry_hash", "label_formula_hash", "label_definition_hash",
            "producer_semantic_hash", "feature_artifact_hash", "feature_row_payload_hash",
            "label_artifact_hash", "label_row_payload_hash", "training_bundle_hash",
            "training_bundle_manifest_hash", "receipt_hash",
        ):
            _require_hash(getattr(self, name), name)
        expected_features = tuple((f.name, f.formula_hash) for f in canonical_oss3d2q_formulas())
        if self.feature_formula_hashes != expected_features:
            raise RawTrainingBundleProvenanceIntegrityError("D2R feature formula family drifted")
        if self.feature_formula_registry_hash != canonical_oss3d2q_formula_registry_hash():
            raise RawTrainingBundleProvenanceIntegrityError("D2R feature registry hash drifted")
        if self.label_formula_hash != canonical_oss3d2r_label_formula().formula_hash:
            raise RawTrainingBundleProvenanceIntegrityError("D2R label formula hash drifted")
        if self.producer_semantic_hash != raw_training_bundle_producer_semantic_hash():
            raise RawTrainingBundleProvenanceIntegrityError("D2R producer semantic hash drifted")
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 1:
            raise ValueError("sample_count must be positive integer")
        if not self.feature_causal_prefix_enforced or self.feature_future_values_used:
            raise RawTrainingBundleProvenanceGovernanceError("D2R feature causality is mandatory")
        if not self.label_future_values_used or not self.label_future_values_confined_to_explicit_horizon:
            raise RawTrainingBundleProvenanceGovernanceError("D2R must acknowledge bounded future label access")
        if self.label_horizon_bars != 1 or self.label_horizon_crossed_train_boundary:
            raise RawTrainingBundleProvenanceGovernanceError("D2R label horizon governance drifted")
        if not self.exact_feature_label_keyset or not self.schema_semantics_separated_from_material_lineage:
            raise RawTrainingBundleProvenanceIntegrityError("D2R keyset/schema-lineage proof missing")
        if (
            self.development_values_loaded or self.final_holdout_values_loaded
            or self.economic_holdout_values_loaded or self.prediction_values_loaded
            or self.adaptive_feature_search or self.adaptive_label_search
            or self.adaptive_horizon_search or self.network_allowed
            or self.profitability_claim_authorized or self.promotion_authorized
        ):
            raise RawTrainingBundleProvenanceGovernanceError("D2R exceeds fixed TRAIN-only authority")
        _deny_authority(self.execution_authorized, self.paper_execution_authorized, self.capital_authority, self.live_trading)
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise RawTrainingBundleProvenanceIntegrityError("D2R receipt hash mismatch")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "receipt_version": self.receipt_version,
            "campaign_id": self.campaign_id,
            "research_split_hash": self.research_split_hash,
            "raw_training_source_hash": self.raw_training_source_hash,
            "warmup_universe_hash": self.warmup_universe_hash,
            "warmup_dataset_set_hash": self.warmup_dataset_set_hash,
            "training_universe_hash": self.training_universe_hash,
            "training_dataset_set_hash": self.training_dataset_set_hash,
            "feature_formula_registry_hash": self.feature_formula_registry_hash,
            "feature_formula_hashes": [list(item) for item in self.feature_formula_hashes],
            "label_formula_hash": self.label_formula_hash,
            "label_definition_hash": self.label_definition_hash,
            "producer_semantic_hash": self.producer_semantic_hash,
            "feature_artifact_hash": self.feature_artifact_hash,
            "feature_row_payload_hash": self.feature_row_payload_hash,
            "label_artifact_hash": self.label_artifact_hash,
            "label_row_payload_hash": self.label_row_payload_hash,
            "training_bundle_hash": self.training_bundle_hash,
            "training_bundle_manifest_hash": self.training_bundle_manifest_hash,
            "sample_count": self.sample_count,
            "feature_causal_prefix_enforced": self.feature_causal_prefix_enforced,
            "feature_future_values_used": self.feature_future_values_used,
            "label_future_values_used": self.label_future_values_used,
            "label_future_values_confined_to_explicit_horizon": self.label_future_values_confined_to_explicit_horizon,
            "label_horizon_bars": self.label_horizon_bars,
            "label_horizon_crossed_train_boundary": self.label_horizon_crossed_train_boundary,
            "exact_feature_label_keyset": self.exact_feature_label_keyset,
            "schema_semantics_separated_from_material_lineage": self.schema_semantics_separated_from_material_lineage,
            "development_values_loaded": self.development_values_loaded,
            "final_holdout_values_loaded": self.final_holdout_values_loaded,
            "economic_holdout_values_loaded": self.economic_holdout_values_loaded,
            "prediction_values_loaded": self.prediction_values_loaded,
            "adaptive_feature_search": self.adaptive_feature_search,
            "adaptive_label_search": self.adaptive_label_search,
            "adaptive_horizon_search": self.adaptive_horizon_search,
            "network_allowed": self.network_allowed,
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


def derive_raw_training_bundle(
    *,
    raw_source: RawTrainingMarketSource,
    campaign_id: str,
    research_split_hash: str,
) -> tuple[FactorMatrixArtifact, SupervisedLabelArtifact, TrainingBundleArtifact, OSS3RawTrainingBundleProvenanceReceipt]:
    features, labels, bundle = _derive_artifacts(
        raw_source=raw_source,
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
    )
    receipt = _build_receipt(
        raw_source=raw_source,
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
        features=features,
        labels=labels,
        bundle=bundle,
    )
    verify_raw_training_bundle_provenance(
        raw_source=raw_source,
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
        features=features,
        labels=labels,
        bundle=bundle,
        receipt=receipt,
    )
    return features, labels, bundle, receipt


def verify_raw_training_bundle_provenance(
    *,
    raw_source: RawTrainingMarketSource,
    campaign_id: str,
    research_split_hash: str,
    features: FactorMatrixArtifact,
    labels: SupervisedLabelArtifact,
    bundle: TrainingBundleArtifact,
    receipt: OSS3RawTrainingBundleProvenanceReceipt,
) -> None:
    expected_features, expected_labels, expected_bundle = _derive_artifacts(
        raw_source=raw_source,
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
    )
    if features != expected_features:
        raise RawTrainingBundleProvenanceIntegrityError("D2R TRAIN feature artifact differs from raw derivation")
    if labels != expected_labels:
        raise RawTrainingBundleProvenanceIntegrityError("D2R TRAIN label artifact differs from raw derivation")
    if bundle != expected_bundle:
        raise RawTrainingBundleProvenanceIntegrityError("D2R TrainingBundle differs from raw derivation")
    expected_receipt = _build_receipt(
        raw_source=raw_source,
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
        features=features,
        labels=labels,
        bundle=bundle,
    )
    if receipt != expected_receipt:
        raise RawTrainingBundleProvenanceIntegrityError("D2R receipt does not rebind to exact raw derivation")


def raw_training_bundle_producer_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    payload: list[dict[str, str]] = []
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise RawTrainingBundleProvenanceIntegrityError(f"D2R semantic file missing: {relative}")
        payload.append({"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()})
    return _hash(payload)


def _derive_artifacts(
    *,
    raw_source: RawTrainingMarketSource,
    campaign_id: str,
    research_split_hash: str,
) -> tuple[FactorMatrixArtifact, SupervisedLabelArtifact, TrainingBundleArtifact]:
    if not isinstance(raw_source, RawTrainingMarketSource):
        raise TypeError("raw_source must be RawTrainingMarketSource")
    _require_id(campaign_id, "campaign_id")
    _require_hash(research_split_hash, "research_split_hash")
    feature_rows, label_rows = _derive_paired_rows(raw_source)
    producer_hash = raw_training_bundle_producer_semantic_hash()
    features = FactorMatrixArtifact.build(
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
        partition=FactorMatrixPartition.TRAIN,
        partition_start=raw_source.partition_start,
        partition_end=raw_source.partition_end,
        producer_code_hash=producer_hash,
        source_dataset_hash=raw_source.source_hash,
        source_universe_hash=raw_source.training_universe.universe_hash,
        features=canonical_oss3d2r_feature_definitions(),
        rows=feature_rows,
    )
    labels = SupervisedLabelArtifact.build(
        campaign_id=campaign_id,
        research_split_hash=research_split_hash,
        partition=LabelPartition.TRAIN,
        partition_start=raw_source.partition_start,
        partition_end=raw_source.partition_end,
        producer_code_hash=producer_hash,
        source_dataset_hash=raw_source.source_hash,
        source_universe_hash=raw_source.training_universe.universe_hash,
        label=canonical_oss3d2r_label_definition(),
        rows=label_rows,
    )
    return features, labels, TrainingBundleArtifact.build(features=features, labels=labels)


def _derive_paired_rows(raw_source: RawTrainingMarketSource) -> tuple[tuple[FactorMatrixRow, ...], tuple[SupervisedLabelRow, ...]]:
    formulas = canonical_oss3d2q_formulas()
    training = raw_source.training_universe
    feature_rows: list[FactorMatrixRow] = []
    label_rows: list[SupervisedLabelRow] = []
    for origin_index in range(training.bar_count - 1):
        origin_as_of = training.datasets[0].bars[origin_index].ended_at.astimezone(timezone.utc)
        for symbol in training.symbols:
            history = _causal_feature_history(raw_source, symbol, origin_index, origin_as_of)
            feature_rows.append(
                FactorMatrixRow(
                    as_of=origin_as_of.isoformat(),
                    available_at=origin_as_of.isoformat(),
                    symbol=symbol,
                    values=tuple(_compute_feature_formula(f, history) for f in formulas),
                )
            )
            dataset = training.dataset(symbol)
            origin_bar = dataset.bars[origin_index]
            horizon_bar = dataset.bars[origin_index + 1]
            horizon_end = horizon_bar.ended_at.astimezone(timezone.utc)
            if horizon_end >= raw_source.partition_end:
                raise RawTrainingBundleProvenanceGovernanceError("label horizon crossed TRAIN partition")
            label_rows.append(
                SupervisedLabelRow(
                    label_as_of=origin_as_of.isoformat(),
                    horizon_end=horizon_end.isoformat(),
                    available_at=horizon_end.isoformat(),
                    symbol=symbol,
                    value=_forward_return(origin_bar.close, horizon_bar.close),
                )
            )
    feature_keys = tuple((row.as_of, row.symbol) for row in feature_rows)
    label_keys = tuple((row.label_as_of, row.symbol) for row in label_rows)
    if feature_keys != label_keys:
        raise RawTrainingBundleProvenanceIntegrityError("feature/label keyset drifted")
    if len(feature_rows) != raw_source.sample_count:
        raise RawTrainingBundleProvenanceIntegrityError("sample count drifted")
    return tuple(feature_rows), tuple(label_rows)


def _causal_feature_history(raw_source: RawTrainingMarketSource, symbol: str, origin_index: int, as_of: datetime) -> tuple[Bar, ...]:
    history = raw_source.warmup_universe.dataset(symbol).bars + raw_source.training_universe.dataset(symbol).bars[: origin_index + 1]
    selected = history[-(CANONICAL_LOOKBACK_BARS + 1):]
    if len(selected) != CANONICAL_LOOKBACK_BARS + 1:
        raise RawTrainingBundleProvenanceGovernanceError("insufficient feature history")
    if any(bar.ended_at.astimezone(timezone.utc) > as_of for bar in selected):
        raise RawTrainingBundleProvenanceGovernanceError("future TRAIN bar entered feature derivation")
    if selected[-1].ended_at.astimezone(timezone.utc) != as_of:
        raise RawTrainingBundleProvenanceIntegrityError("feature history does not end at as_of")
    return selected


def _compute_feature_formula(formula: CanonicalFeatureFormula, history: tuple[Bar, ...]) -> float:
    closes = tuple(bar.close for bar in history)
    with localcontext() as context:
        context.prec = 50
        if formula.name == "momentum_20":
            value = closes[-1] / closes[0] - Decimal("1")
        elif formula.name == "volatility_20":
            returns = tuple(closes[i] / closes[i - 1] - Decimal("1") for i in range(1, len(closes)))
            mean = sum(returns, _ZERO) / Decimal(len(returns))
            variance = sum(((item - mean) ** 2 for item in returns), _ZERO) / Decimal(len(returns))
            value = variance.sqrt()
        else:
            raise RawTrainingBundleProvenanceGovernanceError("unsupported D2R feature formula")
    return float(value)


def _forward_return(origin_close: Decimal, horizon_close: Decimal) -> float:
    with localcontext() as context:
        context.prec = 50
        return float(horizon_close / origin_close - Decimal("1"))


def _build_receipt(
    *, raw_source: RawTrainingMarketSource, campaign_id: str, research_split_hash: str,
    features: FactorMatrixArtifact, labels: SupervisedLabelArtifact, bundle: TrainingBundleArtifact,
) -> OSS3RawTrainingBundleProvenanceReceipt:
    values: dict[str, object] = {
        "receipt_version": OSS3D2R_RECEIPT_VERSION,
        "campaign_id": campaign_id,
        "research_split_hash": research_split_hash,
        "raw_training_source_hash": raw_source.source_hash,
        "warmup_universe_hash": raw_source.warmup_universe.universe_hash,
        "warmup_dataset_set_hash": raw_source.warmup_dataset_set_hash,
        "training_universe_hash": raw_source.training_universe.universe_hash,
        "training_dataset_set_hash": raw_source.training_dataset_set_hash,
        "feature_formula_registry_hash": canonical_oss3d2q_formula_registry_hash(),
        "feature_formula_hashes": tuple((f.name, f.formula_hash) for f in canonical_oss3d2q_formulas()),
        "label_formula_hash": canonical_oss3d2r_label_formula().formula_hash,
        "label_definition_hash": labels.label.fingerprint,
        "producer_semantic_hash": raw_training_bundle_producer_semantic_hash(),
        "feature_artifact_hash": features.artifact_hash,
        "feature_row_payload_hash": features.manifest.row_payload_hash,
        "label_artifact_hash": labels.artifact_hash,
        "label_row_payload_hash": labels.manifest.row_payload_hash,
        "training_bundle_hash": bundle.artifact_hash,
        "training_bundle_manifest_hash": bundle.manifest.fingerprint,
        "sample_count": bundle.manifest.sample_count,
        "feature_causal_prefix_enforced": True,
        "feature_future_values_used": False,
        "label_future_values_used": True,
        "label_future_values_confined_to_explicit_horizon": True,
        "label_horizon_bars": 1,
        "label_horizon_crossed_train_boundary": False,
        "exact_feature_label_keyset": True,
        "schema_semantics_separated_from_material_lineage": True,
        "development_values_loaded": False,
        "final_holdout_values_loaded": False,
        "economic_holdout_values_loaded": False,
        "prediction_values_loaded": False,
        "adaptive_feature_search": False,
        "adaptive_label_search": False,
        "adaptive_horizon_search": False,
        "network_allowed": False,
        "profitability_claim_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS3RawTrainingBundleProvenanceReceipt(**values, receipt_hash=_hash(_receipt_payload(values)))


def _dataset_set_hash(universe: AlignedMarketUniverse) -> str:
    return _hash([[dataset.instrument.symbol, dataset.dataset_hash] for dataset in universe.datasets])


def _receipt_payload(values: Mapping[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["feature_formula_hashes"] = [list(item) for item in values["feature_formula_hashes"]]
    return payload


def _deny_authority(execution_authorized: bool, paper_execution_authorized: bool, capital_authority: str, live_trading: str) -> None:
    if execution_authorized or paper_execution_authorized:
        raise RawTrainingBundleProvenanceGovernanceError("D2R cannot authorize execution")
    if capital_authority != "NONE" or live_trading != "BLOCKED":
        raise RawTrainingBundleProvenanceGovernanceError("D2R cannot grant capital/LIVE")


def _require_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
