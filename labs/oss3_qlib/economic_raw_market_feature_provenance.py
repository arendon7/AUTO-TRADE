"""OSS-3D2Q causal raw-market -> economic-feature provenance.

D2Q closes the upstream input gap left explicit by D2O/D2P.  It deterministically
reconstructs the exact economic ``EconomicPredictionFeatureArtifact`` from
immutable aligned OHLCV bars while proving that every emitted feature row uses
only bars whose close time is <= that row's ``as_of``.

The v1 contract is deliberately narrow.  It supports exactly the two factor
semantics currently frozen for the OSS-3 linear family:

* ``momentum_20``: close_t / close_(t-20) - 1;
* ``volatility_20``: population standard deviation of the twenty one-bar simple
  close returns ending at t.

Both formulas consume CLOSE only, use Decimal arithmetic at precision 50, and
require exactly twenty pre-partition warmup bars.  D2Q verifies that the TRAIN
factor declarations carry the exact canonical formula hashes and lookbacks
before it will derive economic features.  This proves formula identity, not yet
that the historical TRAIN row values themselves were originally generated from
raw bars; TRAIN-value provenance remains a separate frontier.

D2Q necessarily receives the full offline economic market path as immutable
research material, but the formula evaluator receives only a causal prefix for
each row.  Future-bar perturbation invariance is part of the executable
contract.  D2Q consumes no labels, predictions, PnL, fills, gates or portfolio
results and grants no broker/OMS/Safety/PAPER/capital/LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from autotrade.research.market import Bar
from autotrade.research.oss3_factor_matrix_artifact import (
    FactorDefinition,
    FactorMatrixArtifact,
    FactorMatrixPartition,
)
from autotrade.research.universe import AlignedMarketUniverse

from .economic_prediction_provenance import (
    EconomicFeatureRow,
    EconomicPredictionFeatureArtifact,
)
from .predictive_economic_protocol import OSS3PredictiveEconomicProtocolReceipt
from .predictive_strategy_contract import OSS3PredictiveStrategyPreregistrationReceipt


OSS3D2Q_SOURCE_VERSION = "OSS3D2Q_RAW_MARKET_FEATURE_SOURCE_V1"
OSS3D2Q_FORMULA_VERSION = "OSS3D2Q_CANONICAL_FACTOR_FORMULA_V1"
OSS3D2Q_RECEIPT_VERSION = "OSS3D2Q_RAW_MARKET_FEATURE_PROVENANCE_V1"
CAUSAL_POLICY = "BAR_ENDED_AT_LE_AS_OF_PREFIX_ONLY_V1"
ARITHMETIC_POLICY = "DECIMAL_PRECISION_50_TO_FLOAT64_V1"
WARMUP_POLICY = "EXACT_PREPARTITION_LOOKBACK_BARS_V1"
INPUT_FIELD_POLICY = "CLOSE_ONLY_V1"
CANONICAL_FEATURE_NAMES = ("momentum_20", "volatility_20")
CANONICAL_LOOKBACK_BARS = 20

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_ZERO = Decimal("0")

SEMANTIC_FILES = (
    "labs/oss3_qlib/economic_raw_market_feature_provenance.py",
    "labs/oss3_qlib/economic_prediction_provenance.py",
    "labs/oss3_qlib/predictive_economic_protocol.py",
    "labs/oss3_qlib/predictive_strategy_contract.py",
    "src/autotrade/research/market.py",
    "src/autotrade/research/universe.py",
    "src/autotrade/research/oss3_factor_matrix_artifact.py",
)


class RawMarketFeatureProvenanceError(RuntimeError):
    """Base OSS-3D2Q failure."""


class RawMarketFeatureProvenanceIntegrityError(RawMarketFeatureProvenanceError):
    """Raw-market, formula or downstream feature identity drifted."""


class RawMarketFeatureProvenanceGovernanceError(RawMarketFeatureProvenanceError):
    """Operation violates causal, fixed-formula, research-only governance."""


@dataclass(frozen=True, slots=True)
class CanonicalFeatureFormula:
    formula_version: str
    name: str
    expression: str
    input_fields: tuple[str, ...]
    lookback_bars: int
    return_convention: str
    aggregation: str
    warmup_policy: str
    causal_policy: str
    arithmetic_policy: str

    def __post_init__(self) -> None:
        if self.formula_version != OSS3D2Q_FORMULA_VERSION:
            raise RawMarketFeatureProvenanceIntegrityError("noncanonical D2Q formula version")
        if self.name not in CANONICAL_FEATURE_NAMES:
            raise RawMarketFeatureProvenanceGovernanceError("unsupported D2Q feature formula")
        if self.input_fields != ("close",):
            raise RawMarketFeatureProvenanceGovernanceError("D2Q v1 formulas may consume CLOSE only")
        if self.lookback_bars != CANONICAL_LOOKBACK_BARS:
            raise RawMarketFeatureProvenanceGovernanceError("D2Q v1 lookback is frozen at 20 bars")
        if self.warmup_policy != WARMUP_POLICY or self.causal_policy != CAUSAL_POLICY:
            raise RawMarketFeatureProvenanceGovernanceError("D2Q causal/warmup policy drifted")
        if self.arithmetic_policy != ARITHMETIC_POLICY:
            raise RawMarketFeatureProvenanceGovernanceError("D2Q arithmetic policy drifted")
        if not self.expression or not self.return_convention or not self.aggregation:
            raise ValueError("D2Q formula semantics must be explicit")

    @property
    def formula_hash(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "formula_version": self.formula_version,
            "name": self.name,
            "expression": self.expression,
            "input_fields": list(self.input_fields),
            "lookback_bars": self.lookback_bars,
            "return_convention": self.return_convention,
            "aggregation": self.aggregation,
            "warmup_policy": self.warmup_policy,
            "causal_policy": self.causal_policy,
            "arithmetic_policy": self.arithmetic_policy,
        }


def canonical_oss3d2q_formulas() -> tuple[CanonicalFeatureFormula, ...]:
    """Return the complete immutable D2Q v1 formula family."""
    return (
        CanonicalFeatureFormula(
            formula_version=OSS3D2Q_FORMULA_VERSION,
            name="momentum_20",
            expression="close_t / close_t_minus_20 - 1",
            input_fields=("close",),
            lookback_bars=20,
            return_convention="SIMPLE_CLOSE_RETURN",
            aggregation="POINT_ESTIMATE",
            warmup_policy=WARMUP_POLICY,
            causal_policy=CAUSAL_POLICY,
            arithmetic_policy=ARITHMETIC_POLICY,
        ),
        CanonicalFeatureFormula(
            formula_version=OSS3D2Q_FORMULA_VERSION,
            name="volatility_20",
            expression="sqrt(mean((r_i-mean(r))^2)), r_i=close_i/close_i_minus_1-1, i=t-19..t",
            input_fields=("close",),
            lookback_bars=20,
            return_convention="SIMPLE_CLOSE_RETURN",
            aggregation="POPULATION_STANDARD_DEVIATION_20_RETURNS",
            warmup_policy=WARMUP_POLICY,
            causal_policy=CAUSAL_POLICY,
            arithmetic_policy=ARITHMETIC_POLICY,
        ),
    )


def canonical_oss3d2q_formula_registry_hash() -> str:
    return _hash([formula.to_dict() for formula in canonical_oss3d2q_formulas()])


def canonical_oss3d2q_factor_definitions(
    *,
    source_id: str,
    source_hash: str,
) -> tuple[FactorDefinition, ...]:
    """Build OSS-3B declarations carrying exact D2Q formula identities."""
    _require_hash(source_hash, "source_hash")
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("source_id is required")
    return tuple(
        FactorDefinition(
            name=formula.name,
            dtype="float64",
            role="FEATURE",
            formula_hash=formula.formula_hash,
            source_id=source_id,
            source_hash=source_hash,
            lookback_bars=formula.lookback_bars,
        )
        for formula in canonical_oss3d2q_formulas()
    )


@dataclass(frozen=True, slots=True)
class RawMarketFeatureSource:
    """Exact warmup + committed economic market material for causal derivation."""

    source_version: str
    warmup_universe: AlignedMarketUniverse
    economic_universe: AlignedMarketUniverse
    full_economic_path_loaded: bool
    labels_included: bool
    prediction_values_included: bool
    economic_metrics_included: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.source_version != OSS3D2Q_SOURCE_VERSION:
            raise RawMarketFeatureProvenanceIntegrityError("noncanonical D2Q source version")
        if not isinstance(self.warmup_universe, AlignedMarketUniverse):
            raise TypeError("warmup_universe must be AlignedMarketUniverse")
        if not isinstance(self.economic_universe, AlignedMarketUniverse):
            raise TypeError("economic_universe must be AlignedMarketUniverse")
        if self.warmup_universe.symbols != self.economic_universe.symbols:
            raise RawMarketFeatureProvenanceIntegrityError("warmup/economic symbols differ")
        if self.warmup_universe.quote_currency != self.economic_universe.quote_currency:
            raise RawMarketFeatureProvenanceIntegrityError("warmup/economic quote currency differs")
        if self.warmup_universe.timeframe_seconds != self.economic_universe.timeframe_seconds:
            raise RawMarketFeatureProvenanceIntegrityError("warmup/economic timeframe differs")
        if self.warmup_universe.bar_count != CANONICAL_LOOKBACK_BARS:
            raise RawMarketFeatureProvenanceGovernanceError(
                "D2Q requires exactly twenty pre-partition warmup bars"
            )
        for symbol in self.economic_universe.symbols:
            warmup_dataset = self.warmup_universe.dataset(symbol)
            economic_dataset = self.economic_universe.dataset(symbol)
            if warmup_dataset.instrument != economic_dataset.instrument:
                raise RawMarketFeatureProvenanceIntegrityError(
                    "warmup/economic instrument metadata differs"
                )
            if warmup_dataset.bars[-1].ended_at != economic_dataset.bars[0].started_at:
                raise RawMarketFeatureProvenanceGovernanceError(
                    "warmup must end exactly where economic partition starts"
                )
        if self.full_economic_path_loaded is not True:
            raise RawMarketFeatureProvenanceIntegrityError(
                "D2Q must explicitly acknowledge offline full-path material"
            )
        if self.labels_included or self.prediction_values_included or self.economic_metrics_included:
            raise RawMarketFeatureProvenanceGovernanceError(
                "D2Q raw source may contain bars only, not labels/predictions/economic metrics"
            )
        _deny_authority(
            execution_authorized=self.execution_authorized,
            paper_execution_authorized=self.paper_execution_authorized,
            capital_authority=self.capital_authority,
            live_trading=self.live_trading,
        )

    @classmethod
    def build(
        cls,
        *,
        warmup_universe: AlignedMarketUniverse,
        economic_universe: AlignedMarketUniverse,
    ) -> "RawMarketFeatureSource":
        return cls(
            source_version=OSS3D2Q_SOURCE_VERSION,
            warmup_universe=warmup_universe,
            economic_universe=economic_universe,
            full_economic_path_loaded=True,
            labels_included=False,
            prediction_values_included=False,
            economic_metrics_included=False,
            execution_authorized=False,
            paper_execution_authorized=False,
            capital_authority="NONE",
            live_trading="BLOCKED",
        )

    @property
    def warmup_dataset_set_hash(self) -> str:
        return _dataset_set_hash(self.warmup_universe)

    @property
    def economic_dataset_set_hash(self) -> str:
        return _dataset_set_hash(self.economic_universe)

    @property
    def source_hash(self) -> str:
        return _hash(
            {
                "source_version": self.source_version,
                "warmup_universe_hash": self.warmup_universe.universe_hash,
                "warmup_dataset_set_hash": self.warmup_dataset_set_hash,
                "economic_universe_hash": self.economic_universe.universe_hash,
                "economic_dataset_set_hash": self.economic_dataset_set_hash,
                "symbols": list(self.economic_universe.symbols),
                "quote_currency": self.economic_universe.quote_currency,
                "timeframe_seconds": self.economic_universe.timeframe_seconds,
                "warmup_bars": self.warmup_universe.bar_count,
            }
        )


@dataclass(frozen=True, slots=True)
class OSS3RawMarketFeatureProvenanceReceipt:
    receipt_version: str
    economic_protocol_id: str
    economic_protocol_receipt_hash: str
    source_d2l_receipt_hash: str
    source_d2l_binding_hash: str
    train_feature_artifact_hash: str
    train_feature_schema_hash: str
    formula_registry_hash: str
    formula_hashes: tuple[tuple[str, str], ...]
    warmup_universe_hash: str
    warmup_dataset_set_hash: str
    economic_universe_hash: str
    economic_dataset_set_hash: str
    raw_market_source_hash: str
    feature_producer_semantic_hash: str
    economic_feature_artifact_hash: str
    economic_feature_row_payload_hash: str
    economic_feature_support_hash: str
    training_formula_identity_verified: bool
    training_feature_values_rederived: bool
    raw_market_values_loaded: bool
    full_economic_path_loaded: bool
    causal_prefix_enforced: bool
    future_market_values_used_per_row: bool
    exact_warmup_enforced: bool
    economic_labels_loaded: bool
    prediction_values_loaded: bool
    economic_metrics_loaded: bool
    adaptive_feature_search: bool
    network_allowed: bool
    profitability_claim_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D2Q_RECEIPT_VERSION:
            raise RawMarketFeatureProvenanceIntegrityError("noncanonical D2Q receipt version")
        _require_id(self.economic_protocol_id, "economic_protocol_id")
        for name in (
            "economic_protocol_receipt_hash",
            "source_d2l_receipt_hash",
            "source_d2l_binding_hash",
            "train_feature_artifact_hash",
            "train_feature_schema_hash",
            "formula_registry_hash",
            "warmup_universe_hash",
            "warmup_dataset_set_hash",
            "economic_universe_hash",
            "economic_dataset_set_hash",
            "raw_market_source_hash",
            "feature_producer_semantic_hash",
            "economic_feature_artifact_hash",
            "economic_feature_row_payload_hash",
            "economic_feature_support_hash",
            "receipt_hash",
        ):
            _require_hash(getattr(self, name), name)
        expected_formula_hashes = tuple(
            (formula.name, formula.formula_hash)
            for formula in canonical_oss3d2q_formulas()
        )
        if self.formula_hashes != expected_formula_hashes:
            raise RawMarketFeatureProvenanceIntegrityError("D2Q formula family drifted")
        if self.formula_registry_hash != canonical_oss3d2q_formula_registry_hash():
            raise RawMarketFeatureProvenanceIntegrityError("D2Q formula registry hash drifted")
        if self.feature_producer_semantic_hash != raw_market_feature_producer_semantic_hash():
            raise RawMarketFeatureProvenanceIntegrityError("D2Q producer semantic hash drifted")
        if self.training_formula_identity_verified is not True:
            raise RawMarketFeatureProvenanceIntegrityError("D2Q must verify TRAIN formula identity")
        if self.training_feature_values_rederived:
            raise RawMarketFeatureProvenanceGovernanceError(
                "D2Q v1 does not claim historical TRAIN value rederivation"
            )
        if (
            self.raw_market_values_loaded is not True
            or self.full_economic_path_loaded is not True
            or self.causal_prefix_enforced is not True
            or self.exact_warmup_enforced is not True
        ):
            raise RawMarketFeatureProvenanceIntegrityError(
                "D2Q must acknowledge raw input and prove causal/warmup enforcement"
            )
        if (
            self.future_market_values_used_per_row
            or self.economic_labels_loaded
            or self.prediction_values_loaded
            or self.economic_metrics_loaded
            or self.adaptive_feature_search
            or self.network_allowed
            or self.profitability_claim_authorized
            or self.promotion_authorized
        ):
            raise RawMarketFeatureProvenanceGovernanceError(
                "D2Q exceeds fixed causal feature-producer authority"
            )
        _deny_authority(
            execution_authorized=self.execution_authorized,
            paper_execution_authorized=self.paper_execution_authorized,
            capital_authority=self.capital_authority,
            live_trading=self.live_trading,
        )
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise RawMarketFeatureProvenanceIntegrityError("D2Q receipt hash mismatch")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "receipt_version": self.receipt_version,
            "economic_protocol_id": self.economic_protocol_id,
            "economic_protocol_receipt_hash": self.economic_protocol_receipt_hash,
            "source_d2l_receipt_hash": self.source_d2l_receipt_hash,
            "source_d2l_binding_hash": self.source_d2l_binding_hash,
            "train_feature_artifact_hash": self.train_feature_artifact_hash,
            "train_feature_schema_hash": self.train_feature_schema_hash,
            "formula_registry_hash": self.formula_registry_hash,
            "formula_hashes": [[name, value] for name, value in self.formula_hashes],
            "warmup_universe_hash": self.warmup_universe_hash,
            "warmup_dataset_set_hash": self.warmup_dataset_set_hash,
            "economic_universe_hash": self.economic_universe_hash,
            "economic_dataset_set_hash": self.economic_dataset_set_hash,
            "raw_market_source_hash": self.raw_market_source_hash,
            "feature_producer_semantic_hash": self.feature_producer_semantic_hash,
            "economic_feature_artifact_hash": self.economic_feature_artifact_hash,
            "economic_feature_row_payload_hash": self.economic_feature_row_payload_hash,
            "economic_feature_support_hash": self.economic_feature_support_hash,
            "training_formula_identity_verified": self.training_formula_identity_verified,
            "training_feature_values_rederived": self.training_feature_values_rederived,
            "raw_market_values_loaded": self.raw_market_values_loaded,
            "full_economic_path_loaded": self.full_economic_path_loaded,
            "causal_prefix_enforced": self.causal_prefix_enforced,
            "future_market_values_used_per_row": self.future_market_values_used_per_row,
            "exact_warmup_enforced": self.exact_warmup_enforced,
            "economic_labels_loaded": self.economic_labels_loaded,
            "prediction_values_loaded": self.prediction_values_loaded,
            "economic_metrics_loaded": self.economic_metrics_loaded,
            "adaptive_feature_search": self.adaptive_feature_search,
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


def derive_canonical_feature_rows(
    *,
    raw_source: RawMarketFeatureSource,
    train_features: FactorMatrixArtifact,
) -> tuple[EconomicFeatureRow, ...]:
    """Derive every nonterminal economic row using only its causal bar prefix."""
    if not isinstance(raw_source, RawMarketFeatureSource):
        raise TypeError("raw_source must be RawMarketFeatureSource")
    _verify_train_formula_identity(train_features)
    formulas = canonical_oss3d2q_formulas()
    economic = raw_source.economic_universe
    rows: list[EconomicFeatureRow] = []
    for signal_index in range(economic.bar_count - 1):
        as_of = economic.datasets[0].bars[signal_index].ended_at.astimezone(timezone.utc)
        for symbol in economic.symbols:
            history = _causal_closed_history(
                raw_source=raw_source,
                symbol=symbol,
                economic_signal_index=signal_index,
                as_of=as_of,
            )
            values = tuple(_compute_formula(formula, history) for formula in formulas)
            rows.append(
                EconomicFeatureRow(
                    as_of=as_of.isoformat(),
                    available_at=as_of.isoformat(),
                    symbol=symbol,
                    values=values,
                )
            )
    return tuple(rows)


def derive_economic_feature_artifact_from_raw_market(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    train_features: FactorMatrixArtifact,
    raw_source: RawMarketFeatureSource,
) -> tuple[EconomicPredictionFeatureArtifact, OSS3RawMarketFeatureProvenanceReceipt]:
    """Build the exact D2O feature artifact plus causal raw-market provenance."""
    _verify_lineage(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        train_features=train_features,
        raw_source=raw_source,
    )
    rows = derive_canonical_feature_rows(raw_source=raw_source, train_features=train_features)
    artifact = EconomicPredictionFeatureArtifact.build(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        feature_source_hash=raw_source.source_hash,
        feature_producer_code_hash=raw_market_feature_producer_semantic_hash(),
        feature_names=CANONICAL_FEATURE_NAMES,
        rows=rows,
    )
    receipt = _build_receipt(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        train_features=train_features,
        raw_source=raw_source,
        artifact=artifact,
    )
    verify_raw_market_feature_provenance(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        train_features=train_features,
        raw_source=raw_source,
        artifact=artifact,
        receipt=receipt,
    )
    return artifact, receipt


def verify_raw_market_feature_provenance(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    train_features: FactorMatrixArtifact,
    raw_source: RawMarketFeatureSource,
    artifact: EconomicPredictionFeatureArtifact,
    receipt: OSS3RawMarketFeatureProvenanceReceipt,
) -> None:
    _verify_lineage(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        train_features=train_features,
        raw_source=raw_source,
    )
    expected_rows = derive_canonical_feature_rows(
        raw_source=raw_source,
        train_features=train_features,
    )
    expected_artifact = EconomicPredictionFeatureArtifact.build(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        feature_source_hash=raw_source.source_hash,
        feature_producer_code_hash=raw_market_feature_producer_semantic_hash(),
        feature_names=CANONICAL_FEATURE_NAMES,
        rows=expected_rows,
    )
    if artifact != expected_artifact:
        raise RawMarketFeatureProvenanceIntegrityError(
            "D2Q economic feature artifact does not reproduce raw market source"
        )
    expected_receipt = _build_receipt(
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        train_features=train_features,
        raw_source=raw_source,
        artifact=artifact,
    )
    if receipt != expected_receipt:
        raise RawMarketFeatureProvenanceIntegrityError(
            "D2Q receipt does not rebind to concrete raw-market derivation"
        )


def raw_market_feature_producer_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    payload: list[dict[str, str]] = []
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise RawMarketFeatureProvenanceIntegrityError(
                f"D2Q semantic file is missing: {relative}"
            )
        payload.append({"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()})
    return _hash(payload)


def _verify_lineage(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    train_features: FactorMatrixArtifact,
    raw_source: RawMarketFeatureSource,
) -> None:
    if not isinstance(economic_protocol, OSS3PredictiveEconomicProtocolReceipt):
        raise TypeError("economic_protocol must be OSS3PredictiveEconomicProtocolReceipt")
    if not isinstance(d2l_receipt, OSS3PredictiveStrategyPreregistrationReceipt):
        raise TypeError("d2l_receipt must be OSS3PredictiveStrategyPreregistrationReceipt")
    if not isinstance(train_features, FactorMatrixArtifact):
        raise TypeError("train_features must be FactorMatrixArtifact")
    if not isinstance(raw_source, RawMarketFeatureSource):
        raise TypeError("raw_source must be RawMarketFeatureSource")
    binding = d2l_receipt.binding
    if economic_protocol.source_d2l_receipt_hash != d2l_receipt.receipt_hash:
        raise RawMarketFeatureProvenanceIntegrityError("D2Q D2M/D2L receipt mismatch")
    if economic_protocol.source_d2l_binding_hash != binding.binding_hash:
        raise RawMarketFeatureProvenanceIntegrityError("D2Q D2M/D2L binding mismatch")
    if train_features.manifest.partition != FactorMatrixPartition.TRAIN.value:
        raise RawMarketFeatureProvenanceGovernanceError("D2Q requires exact TRAIN feature artifact")
    if train_features.manifest.feature_schema_hash != binding.feature_schema_hash:
        raise RawMarketFeatureProvenanceIntegrityError("D2Q TRAIN schema differs from frozen winner")
    _verify_train_formula_identity(train_features)
    commitment = economic_protocol.economic_holdout_commitment
    economic = raw_source.economic_universe
    checks = (
        ("universe hash", commitment.universe_hash, economic.universe_hash),
        ("dataset set hash", commitment.source_dataset_set_hash, raw_source.economic_dataset_set_hash),
        ("symbols", commitment.symbols, economic.symbols),
        ("quote currency", commitment.quote_currency, economic.quote_currency),
        ("timeframe", commitment.timeframe_seconds, economic.timeframe_seconds),
        ("bar count", commitment.bar_count, economic.bar_count),
        (
            "partition start",
            commitment.partition_start,
            economic.datasets[0].bars[0].started_at.astimezone(timezone.utc).isoformat(),
        ),
        (
            "partition end",
            commitment.partition_end,
            economic.datasets[0].bars[-1].ended_at.astimezone(timezone.utc).isoformat(),
        ),
    )
    for name, expected, actual in checks:
        if expected != actual:
            raise RawMarketFeatureProvenanceIntegrityError(f"D2Q economic {name} mismatch")


def _verify_train_formula_identity(train_features: FactorMatrixArtifact) -> None:
    if not isinstance(train_features, FactorMatrixArtifact):
        raise TypeError("train_features must be FactorMatrixArtifact")
    if train_features.manifest.partition != FactorMatrixPartition.TRAIN.value:
        raise RawMarketFeatureProvenanceGovernanceError("D2Q formula identity must come from TRAIN")
    if tuple(feature.name for feature in train_features.features) != CANONICAL_FEATURE_NAMES:
        raise RawMarketFeatureProvenanceGovernanceError(
            "D2Q v1 requires exact canonical feature order"
        )
    formulas = canonical_oss3d2q_formulas()
    for definition, formula in zip(train_features.features, formulas, strict=True):
        if definition.formula_hash != formula.formula_hash:
            raise RawMarketFeatureProvenanceIntegrityError(
                f"TRAIN formula hash mismatch for {formula.name}"
            )
        if definition.lookback_bars != formula.lookback_bars:
            raise RawMarketFeatureProvenanceIntegrityError(
                f"TRAIN lookback mismatch for {formula.name}"
            )
        if definition.dtype != "float64" or definition.role != "FEATURE":
            raise RawMarketFeatureProvenanceGovernanceError(
                f"TRAIN feature declaration invalid for {formula.name}"
            )


def _causal_closed_history(
    *,
    raw_source: RawMarketFeatureSource,
    symbol: str,
    economic_signal_index: int,
    as_of: datetime,
) -> tuple[Bar, ...]:
    warmup = raw_source.warmup_universe.dataset(symbol).bars
    economic_prefix = raw_source.economic_universe.dataset(symbol).bars[: economic_signal_index + 1]
    history = warmup + economic_prefix
    required = CANONICAL_LOOKBACK_BARS + 1
    if len(history) < required:
        raise RawMarketFeatureProvenanceGovernanceError("insufficient causal history")
    selected = history[-required:]
    if any(bar.ended_at.astimezone(timezone.utc) > as_of for bar in selected):
        raise RawMarketFeatureProvenanceGovernanceError(
            "future market bar entered D2Q feature row"
        )
    if selected[-1].ended_at.astimezone(timezone.utc) != as_of:
        raise RawMarketFeatureProvenanceIntegrityError(
            "D2Q causal history does not terminate at feature as_of"
        )
    return selected


def _compute_formula(
    formula: CanonicalFeatureFormula,
    history: tuple[Bar, ...],
) -> float:
    if len(history) != CANONICAL_LOOKBACK_BARS + 1:
        raise RawMarketFeatureProvenanceIntegrityError("D2Q formula history width drifted")
    closes = tuple(bar.close for bar in history)
    with localcontext() as context:
        context.prec = 50
        if formula.name == "momentum_20":
            value = closes[-1] / closes[0] - Decimal("1")
        elif formula.name == "volatility_20":
            returns = tuple(
                closes[index] / closes[index - 1] - Decimal("1")
                for index in range(1, len(closes))
            )
            mean = sum(returns, _ZERO) / Decimal(len(returns))
            variance = sum(((item - mean) ** 2 for item in returns), _ZERO) / Decimal(len(returns))
            value = variance.sqrt()
        else:
            raise RawMarketFeatureProvenanceGovernanceError("unsupported D2Q formula")
    return float(value)


def _build_receipt(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    train_features: FactorMatrixArtifact,
    raw_source: RawMarketFeatureSource,
    artifact: EconomicPredictionFeatureArtifact,
) -> OSS3RawMarketFeatureProvenanceReceipt:
    values: dict[str, object] = {
        "receipt_version": OSS3D2Q_RECEIPT_VERSION,
        "economic_protocol_id": economic_protocol.economic_protocol_id,
        "economic_protocol_receipt_hash": economic_protocol.receipt_hash,
        "source_d2l_receipt_hash": d2l_receipt.receipt_hash,
        "source_d2l_binding_hash": d2l_receipt.binding.binding_hash,
        "train_feature_artifact_hash": train_features.artifact_hash,
        "train_feature_schema_hash": train_features.manifest.feature_schema_hash,
        "formula_registry_hash": canonical_oss3d2q_formula_registry_hash(),
        "formula_hashes": tuple(
            (formula.name, formula.formula_hash)
            for formula in canonical_oss3d2q_formulas()
        ),
        "warmup_universe_hash": raw_source.warmup_universe.universe_hash,
        "warmup_dataset_set_hash": raw_source.warmup_dataset_set_hash,
        "economic_universe_hash": raw_source.economic_universe.universe_hash,
        "economic_dataset_set_hash": raw_source.economic_dataset_set_hash,
        "raw_market_source_hash": raw_source.source_hash,
        "feature_producer_semantic_hash": raw_market_feature_producer_semantic_hash(),
        "economic_feature_artifact_hash": artifact.artifact_hash,
        "economic_feature_row_payload_hash": artifact.row_payload_hash,
        "economic_feature_support_hash": artifact.support_hash,
        "training_formula_identity_verified": True,
        "training_feature_values_rederived": False,
        "raw_market_values_loaded": True,
        "full_economic_path_loaded": True,
        "causal_prefix_enforced": True,
        "future_market_values_used_per_row": False,
        "exact_warmup_enforced": True,
        "economic_labels_loaded": False,
        "prediction_values_loaded": False,
        "economic_metrics_loaded": False,
        "adaptive_feature_search": False,
        "network_allowed": False,
        "profitability_claim_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS3RawMarketFeatureProvenanceReceipt(
        **values,
        receipt_hash=_hash(_receipt_payload(values)),
    )


def _dataset_set_hash(universe: AlignedMarketUniverse) -> str:
    return _hash(
        [
            [dataset.instrument.symbol, dataset.dataset_hash]
            for dataset in universe.datasets
        ]
    )


def _receipt_payload(values: Mapping[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["formula_hashes"] = [list(item) for item in values["formula_hashes"]]
    return payload


def _deny_authority(
    *,
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise RawMarketFeatureProvenanceGovernanceError("D2Q cannot authorize execution")
    if capital_authority != "NONE" or live_trading != "BLOCKED":
        raise RawMarketFeatureProvenanceGovernanceError("D2Q cannot grant capital/LIVE")


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
