"""OSS-3D2L preregistered predictor-to-strategy semantics.

D2L converts the already-selected OSS-3 DEVELOPMENT winner into one explicit,
deterministic *research target-allocation contract*.  It does not execute Qlib,
read labels, create orders or grant execution authority.

The scientific ordering is intentional:

    D2I winner -> D2J value-opaque FINAL_HOLDOUT protocol
        -> D2L portfolio semantics preregistration
            -> later D2K one-shot FINAL_HOLDOUT evaluation

A D2L preregistration is valid only when it is committed in the same
canonical SQLite file intended for D2K before any D2K start/permit consumption
for the protocol.  This prevents choosing portfolio construction after seeing
FINAL_HOLDOUT outcomes.

D2L consumes only score ranks.  It never uses DEVELOPMENT label values, score
magnitude thresholds or post-hoc score-sign inversion.  Output is immutable
target-weight evidence with a mandatory one-bar execution delay; it is not an
OrderIntent and has no broker/OMS/Safety/PAPER/capital/LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
import sqlite3
from typing import Mapping, Sequence

from autotrade.research.oss3_qlib_artifact import (
    QlibPredictionArtifact,
    QlibPredictionRow,
)

from .family_evaluation_batch import FrozenCandidateOutput
from .final_holdout_protocol import (
    OSS3D2J_CONTRACT_VERSION,
    OSS3FinalHoldoutProtocolReceipt,
)


OSS3D2L_POLICY_VERSION = "OSS3D2L_PREDICTIVE_PORTFOLIO_POLICY_V1"
OSS3D2L_BINDING_VERSION = "OSS3D2L_PREDICTIVE_STRATEGY_BINDING_V1"
OSS3D2L_ALLOCATION_VERSION = "OSS3D2L_PREDICTIVE_TARGET_ALLOCATION_V1"
OSS3D2L_PREREGISTRATION_VERSION = "OSS3D2L_PREDICTIVE_STRATEGY_PREREGISTRATION_V1"
SHARED_SQLITE_ORDERING_CONTRACT = "OSS3D2L_D2K_SHARED_SQLITE_ORDERING_V1"

STRATEGY_ID = "oss3-qlib-cross-sectional-long-only"
RUNTIME_KIND = "QLIB_CROSS_SECTIONAL_SCORE_TARGETS"
SCORE_DIRECTION = "DESCENDING"
SELECTION_MODE = "TOP_FRACTION"
WEIGHTING_MODE = "EQUAL_WEIGHT"
REBALANCE_MODE = "EVERY_PREDICTION_TIMESTAMP"
TIE_BREAK_POLICY = "SYMBOL_ASC"
EXECUTION_DELAY_BARS = 1

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,31}$")
_ZERO = Decimal("0")
_ONE = Decimal("1")

SEMANTIC_FILES = (
    "labs/oss3_qlib/predictive_strategy_contract.py",
    "labs/oss3_qlib/development_winner_seal.py",
    "labs/oss3_qlib/final_holdout_protocol.py",
    "labs/oss3_qlib/family_evaluation_batch.py",
    "src/autotrade/research/oss3_qlib_artifact.py",
)


class PredictiveStrategyContractError(RuntimeError):
    """Base OSS-3D2L failure."""


class PredictiveStrategyContractIntegrityError(PredictiveStrategyContractError):
    """Frozen model, prediction, policy or durable evidence drifted."""


class PredictiveStrategyContractGovernanceError(PredictiveStrategyContractError):
    """Operation violates pre-holdout, research-only strategy governance."""


class PredictiveStrategyContractConflict(PredictiveStrategyContractError):
    """Append-only D2L identity conflicts with existing durable state."""


@dataclass(frozen=True, slots=True)
class PredictivePortfolioPolicy:
    """One non-adaptive, rank-only portfolio construction policy."""

    policy_version: str
    policy_id: str
    score_direction: str
    selection_mode: str
    weighting_mode: str
    rebalance_mode: str
    tie_break_policy: str
    selection_fraction: Decimal
    min_selected_assets: int
    max_selected_assets: int
    gross_target: Decimal
    max_weight_per_asset: Decimal
    reserve_cash_min: Decimal
    min_cross_section_observations: int
    execution_delay_bars: int
    require_positive_winner_metric: bool
    rank_score_only: bool
    adaptive_portfolio_search: bool
    hyperparameter_optimization: bool
    score_sign_flip_allowed: bool
    shorting_allowed: bool
    leverage_allowed: bool
    same_bar_execution_allowed: bool

    def __post_init__(self) -> None:
        if self.policy_version != OSS3D2L_POLICY_VERSION:
            raise PredictiveStrategyContractIntegrityError(
                "noncanonical D2L policy version"
            )
        _require_id(self.policy_id, "policy_id")
        if self.score_direction != SCORE_DIRECTION:
            raise PredictiveStrategyContractGovernanceError(
                "D2L score direction is frozen to descending"
            )
        if self.selection_mode != SELECTION_MODE:
            raise PredictiveStrategyContractGovernanceError(
                "D2L selection mode is frozen to TOP_FRACTION"
            )
        if self.weighting_mode != WEIGHTING_MODE:
            raise PredictiveStrategyContractGovernanceError(
                "D2L weighting mode is frozen to equal weight"
            )
        if self.rebalance_mode != REBALANCE_MODE:
            raise PredictiveStrategyContractGovernanceError(
                "D2L rebalance mode is frozen"
            )
        if self.tie_break_policy != TIE_BREAK_POLICY:
            raise PredictiveStrategyContractGovernanceError(
                "D2L tie-break policy is frozen"
            )
        for name, value in (
            ("selection_fraction", self.selection_fraction),
            ("gross_target", self.gross_target),
            ("max_weight_per_asset", self.max_weight_per_asset),
            ("reserve_cash_min", self.reserve_cash_min),
        ):
            _require_decimal(value, name)
        if not _ZERO < self.selection_fraction <= _ONE:
            raise ValueError("selection_fraction must be in (0,1]")
        if not _ZERO < self.gross_target <= _ONE:
            raise ValueError("gross_target must be in (0,1]")
        if not _ZERO < self.max_weight_per_asset <= _ONE:
            raise ValueError("max_weight_per_asset must be in (0,1]")
        if not _ZERO <= self.reserve_cash_min < _ONE:
            raise ValueError("reserve_cash_min must be in [0,1)")
        if self.gross_target > _ONE - self.reserve_cash_min:
            raise PredictiveStrategyContractGovernanceError(
                "gross_target violates mandatory cash reserve"
            )
        if self.max_weight_per_asset > self.gross_target:
            raise PredictiveStrategyContractGovernanceError(
                "per-asset cap may not exceed gross target"
            )
        for name, value, minimum in (
            ("min_selected_assets", self.min_selected_assets, 1),
            ("max_selected_assets", self.max_selected_assets, 1),
            ("min_cross_section_observations", self.min_cross_section_observations, 3),
            ("execution_delay_bars", self.execution_delay_bars, 1),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be integer >= {minimum}")
        if self.min_selected_assets > self.max_selected_assets:
            raise ValueError("min_selected_assets exceeds max_selected_assets")
        if self.execution_delay_bars != EXECUTION_DELAY_BARS:
            raise PredictiveStrategyContractGovernanceError(
                "D2L requires exactly one-bar minimum execution delay"
            )
        if self.require_positive_winner_metric is not True:
            raise PredictiveStrategyContractGovernanceError(
                "D2L may not invert a non-positive winner after selection"
            )
        if self.rank_score_only is not True:
            raise PredictiveStrategyContractGovernanceError(
                "D2L may use score ranks only"
            )
        if (
            self.adaptive_portfolio_search
            or self.hyperparameter_optimization
            or self.score_sign_flip_allowed
            or self.shorting_allowed
            or self.leverage_allowed
            or self.same_bar_execution_allowed
        ):
            raise PredictiveStrategyContractGovernanceError(
                "D2L canonical policy forbids adaptive search, sign flip, shorting, leverage and same-bar execution"
            )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "policy_id": self.policy_id,
            "score_direction": self.score_direction,
            "selection_mode": self.selection_mode,
            "weighting_mode": self.weighting_mode,
            "rebalance_mode": self.rebalance_mode,
            "tie_break_policy": self.tie_break_policy,
            "selection_fraction": str(self.selection_fraction),
            "min_selected_assets": self.min_selected_assets,
            "max_selected_assets": self.max_selected_assets,
            "gross_target": str(self.gross_target),
            "max_weight_per_asset": str(self.max_weight_per_asset),
            "reserve_cash_min": str(self.reserve_cash_min),
            "min_cross_section_observations": self.min_cross_section_observations,
            "execution_delay_bars": self.execution_delay_bars,
            "require_positive_winner_metric": self.require_positive_winner_metric,
            "rank_score_only": self.rank_score_only,
            "adaptive_portfolio_search": self.adaptive_portfolio_search,
            "hyperparameter_optimization": self.hyperparameter_optimization,
            "score_sign_flip_allowed": self.score_sign_flip_allowed,
            "shorting_allowed": self.shorting_allowed,
            "leverage_allowed": self.leverage_allowed,
            "same_bar_execution_allowed": self.same_bar_execution_allowed,
        }


def canonical_oss3d2l_policy() -> PredictivePortfolioPolicy:
    """Return the single safety-first D2L v1 policy; no data-driven alternatives."""
    return PredictivePortfolioPolicy(
        policy_version=OSS3D2L_POLICY_VERSION,
        policy_id="oss3d2l-canonical-long-only-v1",
        score_direction=SCORE_DIRECTION,
        selection_mode=SELECTION_MODE,
        weighting_mode=WEIGHTING_MODE,
        rebalance_mode=REBALANCE_MODE,
        tie_break_policy=TIE_BREAK_POLICY,
        selection_fraction=Decimal("0.25"),
        min_selected_assets=1,
        max_selected_assets=5,
        gross_target=Decimal("0.75"),
        max_weight_per_asset=Decimal("0.25"),
        reserve_cash_min=Decimal("0.25"),
        min_cross_section_observations=3,
        execution_delay_bars=EXECUTION_DELAY_BARS,
        require_positive_winner_metric=True,
        rank_score_only=True,
        adaptive_portfolio_search=False,
        hyperparameter_optimization=False,
        score_sign_flip_allowed=False,
        shorting_allowed=False,
        leverage_allowed=False,
        same_bar_execution_allowed=False,
    )


@dataclass(frozen=True, slots=True)
class PredictiveRankedAsset:
    rank: int
    symbol: str
    score: float
    selected: bool
    target_weight: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise ValueError("rank must be integer >=1")
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("invalid ranked symbol")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise ValueError("score must be numeric")
        if not isfinite(float(self.score)):
            raise ValueError("score must be finite")
        _require_decimal(self.target_weight, "target_weight")
        if self.target_weight < _ZERO or self.target_weight > _ONE:
            raise ValueError("target_weight outside [0,1]")
        if self.selected is not (self.target_weight > _ZERO):
            raise PredictiveStrategyContractIntegrityError(
                "selected flag differs from positive target weight"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "symbol": self.symbol,
            "score": float(self.score),
            "selected": self.selected,
            "target_weight": str(self.target_weight),
        }


@dataclass(frozen=True, slots=True)
class PredictiveTargetAllocation:
    allocation_version: str
    strategy_id: str
    strategy_version: str
    strategy_semantic_hash: str
    policy_fingerprint: str
    prediction_artifact_hash: str
    as_of: str
    source_cross_section_hash: str
    source_symbol_count: int
    selected_asset_count: int
    rankings: tuple[PredictiveRankedAsset, ...]
    target_weights: tuple[tuple[str, Decimal], ...]
    invested_weight: Decimal
    cash_weight: Decimal
    execution_delay_bars: int
    same_bar_execution_allowed: bool
    order_intents_generated: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.allocation_version != OSS3D2L_ALLOCATION_VERSION:
            raise PredictiveStrategyContractIntegrityError(
                "noncanonical D2L allocation version"
            )
        _require_id(self.strategy_id, "strategy_id")
        _require_id(self.strategy_version, "strategy_version")
        for name in (
            "strategy_semantic_hash",
            "policy_fingerprint",
            "prediction_artifact_hash",
            "source_cross_section_hash",
        ):
            _require_hash(getattr(self, name), name)
        _parse_canonical_utc(self.as_of, "allocation as_of")
        for name, value, minimum in (
            ("source_symbol_count", self.source_symbol_count, 3),
            ("selected_asset_count", self.selected_asset_count, 1),
            ("execution_delay_bars", self.execution_delay_bars, 1),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be integer >= {minimum}")
        if self.selected_asset_count > self.source_symbol_count:
            raise PredictiveStrategyContractIntegrityError(
                "selected assets exceed source cross-section"
            )
        if len(self.rankings) != self.source_symbol_count:
            raise PredictiveStrategyContractIntegrityError(
                "ranking count differs from source cross-section"
            )
        if tuple(item.rank for item in self.rankings) != tuple(
            range(1, self.source_symbol_count + 1)
        ):
            raise PredictiveStrategyContractIntegrityError(
                "rankings must be contiguous canonical order"
            )
        ranking_symbols = tuple(item.symbol for item in self.rankings)
        if len(set(ranking_symbols)) != len(ranking_symbols):
            raise PredictiveStrategyContractIntegrityError(
                "ranking symbols must be unique"
            )
        weight_symbols = tuple(symbol for symbol, _ in self.target_weights)
        if weight_symbols != tuple(sorted(weight_symbols)):
            raise PredictiveStrategyContractIntegrityError(
                "target weights must use sorted symbols"
            )
        if set(weight_symbols) != set(ranking_symbols):
            raise PredictiveStrategyContractIntegrityError(
                "target weights must cover exact ranking symbols"
            )
        for symbol, weight in self.target_weights:
            if not _SYMBOL_RE.fullmatch(symbol):
                raise ValueError("invalid target-weight symbol")
            _require_decimal(weight, "target weight")
            if weight < _ZERO or weight > _ONE:
                raise ValueError("target weight outside [0,1]")
        expected_selected = sum(1 for item in self.rankings if item.selected)
        if expected_selected != self.selected_asset_count:
            raise PredictiveStrategyContractIntegrityError(
                "selected_asset_count mismatch"
            )
        by_symbol = dict(self.target_weights)
        if any(by_symbol[item.symbol] != item.target_weight for item in self.rankings):
            raise PredictiveStrategyContractIntegrityError(
                "ranking target weights differ from canonical target_weights"
            )
        _require_decimal(self.invested_weight, "invested_weight")
        _require_decimal(self.cash_weight, "cash_weight")
        expected_invested = sum((weight for _, weight in self.target_weights), _ZERO)
        if self.invested_weight != expected_invested:
            raise PredictiveStrategyContractIntegrityError(
                "invested_weight mismatch"
            )
        if self.cash_weight != _ONE - self.invested_weight:
            raise PredictiveStrategyContractIntegrityError("cash_weight mismatch")
        if not _ZERO <= self.invested_weight <= _ONE:
            raise ValueError("invested weight outside [0,1]")
        if self.execution_delay_bars != EXECUTION_DELAY_BARS:
            raise PredictiveStrategyContractGovernanceError(
                "allocation execution delay drifted"
            )
        _deny_authority(
            same_bar_execution_allowed=self.same_bar_execution_allowed,
            order_intents_generated=self.order_intents_generated,
            execution_authorized=self.execution_authorized,
            paper_execution_authorized=self.paper_execution_authorized,
            capital_authority=self.capital_authority,
            live_trading=self.live_trading,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "allocation_version": self.allocation_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_semantic_hash": self.strategy_semantic_hash,
            "policy_fingerprint": self.policy_fingerprint,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "as_of": self.as_of,
            "source_cross_section_hash": self.source_cross_section_hash,
            "source_symbol_count": self.source_symbol_count,
            "selected_asset_count": self.selected_asset_count,
            "rankings": [item.to_dict() for item in self.rankings],
            "target_weights": [[symbol, str(weight)] for symbol, weight in self.target_weights],
            "invested_weight": str(self.invested_weight),
            "cash_weight": str(self.cash_weight),
            "execution_delay_bars": self.execution_delay_bars,
            "same_bar_execution_allowed": self.same_bar_execution_allowed,
            "order_intents_generated": self.order_intents_generated,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class OSS3PredictiveStrategyBinding:
    binding_version: str
    protocol_id: str
    protocol_receipt_hash: str
    expected_holdout_authorization_id: str
    winner_binding_fingerprint: str
    source_d2i_seal_fingerprint: str
    selected_trial_id: str
    model_family: str
    model_config_hash: str
    request_hash: str
    development_prediction_artifact_hash: str
    development_prediction_payload_hash: str
    development_prediction_receipt_hash: str
    environment_attestation_hash: str
    d2g_run_evidence_hash: str
    shared_runner_code_hash: str
    runtime_environment_hash: str
    qlib_version: str
    training_dataset_hash: str
    feature_schema_hash: str
    source_inference_start: str
    source_inference_end: str
    policy: PredictivePortfolioPolicy
    binding_code_hash: str
    development_allocation_fingerprints: tuple[str, ...]
    development_allocation_evidence_hash: str
    strategy_id: str
    strategy_version: str
    runtime_kind: str
    strategy_semantic_hash: str
    development_predictions_used: bool
    development_labels_used: bool
    policy_frozen_before_final_holdout: bool
    policy_selected_after_final_holdout: bool
    final_holdout_observed: bool
    final_holdout_consumed: bool
    retuning_allowed: bool
    reselection_allowed: bool
    score_sign_flip_allowed: bool
    profitability_claim_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    binding_hash: str

    def __post_init__(self) -> None:
        if self.binding_version != OSS3D2L_BINDING_VERSION:
            raise PredictiveStrategyContractIntegrityError(
                "noncanonical D2L binding version"
            )
        for name in (
            "protocol_id",
            "expected_holdout_authorization_id",
            "selected_trial_id",
            "model_family",
            "strategy_id",
            "strategy_version",
            "runtime_kind",
        ):
            _require_id(getattr(self, name), name)
        for name in (
            "protocol_receipt_hash",
            "winner_binding_fingerprint",
            "source_d2i_seal_fingerprint",
            "model_config_hash",
            "request_hash",
            "development_prediction_artifact_hash",
            "development_prediction_payload_hash",
            "development_prediction_receipt_hash",
            "environment_attestation_hash",
            "d2g_run_evidence_hash",
            "shared_runner_code_hash",
            "runtime_environment_hash",
            "training_dataset_hash",
            "feature_schema_hash",
            "binding_code_hash",
            "development_allocation_evidence_hash",
            "strategy_semantic_hash",
            "binding_hash",
        ):
            _require_hash(getattr(self, name), name)
        _parse_canonical_utc(self.source_inference_start, "source_inference_start")
        _parse_canonical_utc(self.source_inference_end, "source_inference_end")
        if not isinstance(self.policy, PredictivePortfolioPolicy):
            raise TypeError("policy must be PredictivePortfolioPolicy")
        if not self.development_allocation_fingerprints:
            raise PredictiveStrategyContractIntegrityError(
                "D2L requires deterministic DEVELOPMENT allocation evidence"
            )
        for value in self.development_allocation_fingerprints:
            _require_hash(value, "development allocation fingerprint")
        if self.development_allocation_evidence_hash != _hash(
            list(self.development_allocation_fingerprints)
        ):
            raise PredictiveStrategyContractIntegrityError(
                "DEVELOPMENT allocation evidence hash mismatch"
            )
        if self.strategy_id != STRATEGY_ID or self.runtime_kind != RUNTIME_KIND:
            raise PredictiveStrategyContractGovernanceError(
                "D2L strategy identity/runtime kind drifted"
            )
        if self.binding_code_hash != predictive_strategy_code_hash():
            raise PredictiveStrategyContractIntegrityError(
                "D2L semantic source identity drifted"
            )
        expected_semantic = _strategy_semantic_hash_from_binding(self)
        if self.strategy_semantic_hash != expected_semantic:
            raise PredictiveStrategyContractIntegrityError(
                "strategy semantic hash mismatch"
            )
        expected_version = _strategy_version(expected_semantic)
        if self.strategy_version != expected_version:
            raise PredictiveStrategyContractIntegrityError(
                "strategy version does not derive from semantic hash"
            )
        if self.development_predictions_used is not True:
            raise PredictiveStrategyContractIntegrityError(
                "D2L binding must prove DEVELOPMENT score semantics"
            )
        if self.development_labels_used:
            raise PredictiveStrategyContractGovernanceError(
                "D2L may not use DEVELOPMENT labels"
            )
        if self.policy_frozen_before_final_holdout is not True:
            raise PredictiveStrategyContractGovernanceError(
                "D2L policy must be frozen before FINAL_HOLDOUT"
            )
        if (
            self.policy_selected_after_final_holdout
            or self.final_holdout_observed
            or self.final_holdout_consumed
            or self.retuning_allowed
            or self.reselection_allowed
            or self.score_sign_flip_allowed
            or self.profitability_claim_authorized
            or self.promotion_authorized
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise PredictiveStrategyContractGovernanceError(
                "D2L binding exceeds pre-holdout research-only authority"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise PredictiveStrategyContractGovernanceError(
                "D2L cannot grant capital or LIVE authority"
            )
        if self.binding_hash != _hash(self.to_dict(include_hash=False)):
            raise PredictiveStrategyContractIntegrityError("D2L binding hash mismatch")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "binding_version": self.binding_version,
            "protocol_id": self.protocol_id,
            "protocol_receipt_hash": self.protocol_receipt_hash,
            "expected_holdout_authorization_id": self.expected_holdout_authorization_id,
            "winner_binding_fingerprint": self.winner_binding_fingerprint,
            "source_d2i_seal_fingerprint": self.source_d2i_seal_fingerprint,
            "selected_trial_id": self.selected_trial_id,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "request_hash": self.request_hash,
            "development_prediction_artifact_hash": self.development_prediction_artifact_hash,
            "development_prediction_payload_hash": self.development_prediction_payload_hash,
            "development_prediction_receipt_hash": self.development_prediction_receipt_hash,
            "environment_attestation_hash": self.environment_attestation_hash,
            "d2g_run_evidence_hash": self.d2g_run_evidence_hash,
            "shared_runner_code_hash": self.shared_runner_code_hash,
            "runtime_environment_hash": self.runtime_environment_hash,
            "qlib_version": self.qlib_version,
            "training_dataset_hash": self.training_dataset_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "source_inference_start": self.source_inference_start,
            "source_inference_end": self.source_inference_end,
            "policy": self.policy.to_dict(),
            "policy_fingerprint": self.policy.fingerprint,
            "binding_code_hash": self.binding_code_hash,
            "development_allocation_fingerprints": list(
                self.development_allocation_fingerprints
            ),
            "development_allocation_evidence_hash": self.development_allocation_evidence_hash,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "runtime_kind": self.runtime_kind,
            "strategy_semantic_hash": self.strategy_semantic_hash,
            "development_predictions_used": self.development_predictions_used,
            "development_labels_used": self.development_labels_used,
            "policy_frozen_before_final_holdout": self.policy_frozen_before_final_holdout,
            "policy_selected_after_final_holdout": self.policy_selected_after_final_holdout,
            "final_holdout_observed": self.final_holdout_observed,
            "final_holdout_consumed": self.final_holdout_consumed,
            "retuning_allowed": self.retuning_allowed,
            "reselection_allowed": self.reselection_allowed,
            "score_sign_flip_allowed": self.score_sign_flip_allowed,
            "profitability_claim_authorized": self.profitability_claim_authorized,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }
        if include_hash:
            payload["binding_hash"] = self.binding_hash
        return payload


@dataclass(frozen=True, slots=True)
class OSS3PredictiveStrategyPreregistrationReceipt:
    receipt_version: str
    ordering_contract: str
    binding: OSS3PredictiveStrategyBinding
    protocol_id: str
    protocol_receipt_hash: str
    expected_holdout_authorization_id: str
    registered_at: str
    shared_sqlite_ordering_enforced: bool
    d2k_start_absent_at_commit: bool
    final_holdout_observed: bool
    final_holdout_consumed: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D2L_PREREGISTRATION_VERSION:
            raise PredictiveStrategyContractIntegrityError(
                "noncanonical D2L preregistration version"
            )
        if self.ordering_contract != SHARED_SQLITE_ORDERING_CONTRACT:
            raise PredictiveStrategyContractIntegrityError(
                "D2L ordering contract drifted"
            )
        if not isinstance(self.binding, OSS3PredictiveStrategyBinding):
            raise TypeError("binding must be OSS3PredictiveStrategyBinding")
        _require_id(self.protocol_id, "protocol_id")
        _require_id(
            self.expected_holdout_authorization_id,
            "expected_holdout_authorization_id",
        )
        _require_hash(self.protocol_receipt_hash, "protocol_receipt_hash")
        _require_hash(self.receipt_hash, "receipt_hash")
        _parse_canonical_utc(self.registered_at, "registered_at")
        if (
            self.protocol_id != self.binding.protocol_id
            or self.protocol_receipt_hash != self.binding.protocol_receipt_hash
            or self.expected_holdout_authorization_id
            != self.binding.expected_holdout_authorization_id
        ):
            raise PredictiveStrategyContractIntegrityError(
                "D2L preregistration/binding protocol mismatch"
            )
        if self.shared_sqlite_ordering_enforced is not True:
            raise PredictiveStrategyContractGovernanceError(
                "D2L requires shared SQLite ordering with D2K"
            )
        if self.d2k_start_absent_at_commit is not True:
            raise PredictiveStrategyContractGovernanceError(
                "D2L must commit before any D2K start"
            )
        if (
            self.final_holdout_observed
            or self.final_holdout_consumed
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise PredictiveStrategyContractGovernanceError(
                "D2L preregistration exceeds pre-holdout research authority"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise PredictiveStrategyContractGovernanceError(
                "D2L preregistration cannot grant capital/LIVE"
            )
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise PredictiveStrategyContractIntegrityError(
                "D2L preregistration receipt hash mismatch"
            )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "receipt_version": self.receipt_version,
            "ordering_contract": self.ordering_contract,
            "binding": self.binding.to_dict(),
            "protocol_id": self.protocol_id,
            "protocol_receipt_hash": self.protocol_receipt_hash,
            "expected_holdout_authorization_id": self.expected_holdout_authorization_id,
            "registered_at": self.registered_at,
            "shared_sqlite_ordering_enforced": self.shared_sqlite_ordering_enforced,
            "d2k_start_absent_at_commit": self.d2k_start_absent_at_commit,
            "final_holdout_observed": self.final_holdout_observed,
            "final_holdout_consumed": self.final_holdout_consumed,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }
        if include_hash:
            payload["receipt_hash"] = self.receipt_hash
        return payload


def build_predictive_strategy_binding(
    *,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    winner_output: FrozenCandidateOutput,
    policy: PredictivePortfolioPolicy | None = None,
) -> OSS3PredictiveStrategyBinding:
    """Bind D2J winner scores to deterministic long-only target semantics.

    No labels or FINAL_HOLDOUT material are accepted by this API.
    """
    if not isinstance(protocol, OSS3FinalHoldoutProtocolReceipt):
        raise TypeError("protocol must be OSS3FinalHoldoutProtocolReceipt")
    if not isinstance(winner_output, FrozenCandidateOutput):
        raise TypeError("winner_output must be FrozenCandidateOutput")
    selected_policy = canonical_oss3d2l_policy() if policy is None else policy
    if not isinstance(selected_policy, PredictivePortfolioPolicy):
        raise TypeError("policy must be PredictivePortfolioPolicy")
    _verify_pristine_protocol(protocol)
    _verify_winner_output(protocol=protocol, winner_output=winner_output)
    if (
        selected_policy.require_positive_winner_metric
        and protocol.winner_binding.source_winner_primary_metric <= 0.0
    ):
        raise PredictiveStrategyContractGovernanceError(
            "D2L refuses post-hoc score inversion for non-positive DEVELOPMENT winner"
        )

    manifest = winner_output.prediction.manifest
    code_hash = predictive_strategy_code_hash()
    allocation_seed = _build_allocations(
        prediction=winner_output.prediction,
        policy=selected_policy,
        strategy_id=STRATEGY_ID,
        strategy_version="PENDING",
        strategy_semantic_hash="0" * 64,
    )
    provisional_allocation_hashes = tuple(
        _allocation_semantic_fingerprint(item) for item in allocation_seed
    )
    allocation_evidence_hash = _hash(list(provisional_allocation_hashes))
    semantic_values = {
        "protocol_receipt_hash": protocol.receipt_hash,
        "winner_binding_fingerprint": protocol.winner_binding.fingerprint,
        "source_d2i_seal_fingerprint": protocol.source_d2i_seal_fingerprint,
        "selected_trial_id": protocol.selected_trial_id,
        "model_family": protocol.winner_binding.model_family,
        "model_config_hash": protocol.model_config_hash,
        "request_hash": protocol.winner_binding.request_hash,
        "development_prediction_artifact_hash": winner_output.prediction.artifact_hash,
        "development_prediction_payload_hash": manifest.prediction_payload_hash,
        "development_prediction_receipt_hash": winner_output.receipt.fingerprint,
        "environment_attestation_hash": winner_output.attestation.artifact_hash,
        "d2g_run_evidence_hash": winner_output.run_evidence_fingerprint,
        "shared_runner_code_hash": protocol.winner_binding.shared_runner_code_hash,
        "runtime_environment_hash": protocol.winner_binding.runtime_environment_hash,
        "qlib_version": manifest.qlib_version,
        "training_dataset_hash": manifest.training_dataset_hash,
        "feature_schema_hash": manifest.feature_schema_hash,
        "policy_fingerprint": selected_policy.fingerprint,
        "binding_code_hash": code_hash,
        "development_allocation_evidence_hash": allocation_evidence_hash,
        "strategy_id": STRATEGY_ID,
        "runtime_kind": RUNTIME_KIND,
    }
    semantic_hash = _hash(semantic_values)
    strategy_version = _strategy_version(semantic_hash)
    allocations = _build_allocations(
        prediction=winner_output.prediction,
        policy=selected_policy,
        strategy_id=STRATEGY_ID,
        strategy_version=strategy_version,
        strategy_semantic_hash=semantic_hash,
    )
    allocation_fingerprints = tuple(item.fingerprint for item in allocations)
    # The semantic allocation hash excludes the strategy version/hash placeholders,
    # while public allocation fingerprints include the final strategy identity.
    if _hash([_allocation_semantic_fingerprint(item) for item in allocations]) != allocation_evidence_hash:
        raise PredictiveStrategyContractIntegrityError(
            "D2L allocation semantics changed while deriving strategy identity"
        )

    values: dict[str, object] = {
        "binding_version": OSS3D2L_BINDING_VERSION,
        "protocol_id": protocol.protocol_id,
        "protocol_receipt_hash": protocol.receipt_hash,
        "expected_holdout_authorization_id": protocol.expected_holdout_authorization_id,
        "winner_binding_fingerprint": protocol.winner_binding.fingerprint,
        "source_d2i_seal_fingerprint": protocol.source_d2i_seal_fingerprint,
        "selected_trial_id": protocol.selected_trial_id,
        "model_family": protocol.winner_binding.model_family,
        "model_config_hash": protocol.model_config_hash,
        "request_hash": protocol.winner_binding.request_hash,
        "development_prediction_artifact_hash": winner_output.prediction.artifact_hash,
        "development_prediction_payload_hash": manifest.prediction_payload_hash,
        "development_prediction_receipt_hash": winner_output.receipt.fingerprint,
        "environment_attestation_hash": winner_output.attestation.artifact_hash,
        "d2g_run_evidence_hash": winner_output.run_evidence_fingerprint,
        "shared_runner_code_hash": protocol.winner_binding.shared_runner_code_hash,
        "runtime_environment_hash": protocol.winner_binding.runtime_environment_hash,
        "qlib_version": manifest.qlib_version,
        "training_dataset_hash": manifest.training_dataset_hash,
        "feature_schema_hash": manifest.feature_schema_hash,
        "source_inference_start": manifest.inference_start,
        "source_inference_end": manifest.inference_end,
        "policy": selected_policy,
        "binding_code_hash": code_hash,
        "development_allocation_fingerprints": allocation_fingerprints,
        "development_allocation_evidence_hash": allocation_evidence_hash,
        "strategy_id": STRATEGY_ID,
        "strategy_version": strategy_version,
        "runtime_kind": RUNTIME_KIND,
        "strategy_semantic_hash": semantic_hash,
        "development_predictions_used": True,
        "development_labels_used": False,
        "policy_frozen_before_final_holdout": True,
        "policy_selected_after_final_holdout": False,
        "final_holdout_observed": False,
        "final_holdout_consumed": False,
        "retuning_allowed": False,
        "reselection_allowed": False,
        "score_sign_flip_allowed": False,
        "profitability_claim_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS3PredictiveStrategyBinding(
        **values,
        binding_hash=_hash(_binding_payload_from_values(values)),
    )


def project_prediction_artifact(
    *,
    binding: OSS3PredictiveStrategyBinding,
    prediction: QlibPredictionArtifact,
) -> tuple[PredictiveTargetAllocation, ...]:
    """Project one compatible frozen-model prediction artifact into target weights.

    This remains research target evidence only.  It does not create orders.
    """
    if not isinstance(binding, OSS3PredictiveStrategyBinding):
        raise TypeError("binding must be OSS3PredictiveStrategyBinding")
    if not isinstance(prediction, QlibPredictionArtifact):
        raise TypeError("prediction must be QlibPredictionArtifact")
    manifest = prediction.manifest
    for name, expected, actual in (
        ("model_family", binding.model_family, manifest.model_family),
        ("model_config_hash", binding.model_config_hash, manifest.model_config_hash),
        ("qlib_version", binding.qlib_version, manifest.qlib_version),
        ("training_dataset_hash", binding.training_dataset_hash, manifest.training_dataset_hash),
        ("feature_schema_hash", binding.feature_schema_hash, manifest.feature_schema_hash),
        ("producer_code_hash", binding.shared_runner_code_hash, manifest.producer_code_hash),
    ):
        if expected != actual:
            raise PredictiveStrategyContractIntegrityError(
                f"prediction artifact differs from frozen D2L strategy: {name}"
            )
    if _parse_canonical_utc(manifest.inference_start, "prediction inference_start") < _parse_canonical_utc(
        binding.source_inference_start,
        "binding source_inference_start",
    ):
        raise PredictiveStrategyContractGovernanceError(
            "D2L may not project predictions from an earlier inference era"
        )
    return _build_allocations(
        prediction=prediction,
        policy=binding.policy,
        strategy_id=binding.strategy_id,
        strategy_version=binding.strategy_version,
        strategy_semantic_hash=binding.strategy_semantic_hash,
    )


class SQLiteOSS3PredictiveStrategyRegistry:
    """Append-only D2L preregistry ordered atomically before D2K in one SQLite."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oss3_predictive_strategy_preregistrations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    protocol_id TEXT NOT NULL UNIQUE,
                    protocol_receipt_hash TEXT NOT NULL UNIQUE,
                    expected_holdout_authorization_id TEXT NOT NULL UNIQUE,
                    winner_binding_fingerprint TEXT NOT NULL UNIQUE,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL UNIQUE,
                    strategy_semantic_hash TEXT NOT NULL UNIQUE,
                    policy_hash TEXT NOT NULL,
                    binding_hash TEXT NOT NULL UNIQUE,
                    registered_at TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS oss3_predictive_strategy_prereg_no_update
                BEFORE UPDATE ON oss3_predictive_strategy_preregistrations
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2L registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_predictive_strategy_prereg_no_delete
                BEFORE DELETE ON oss3_predictive_strategy_preregistrations
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2L registry is append-only');
                END;
                """
            )
            conn.commit()
        finally:
            conn.close()

    def preregister(
        self,
        *,
        protocol: OSS3FinalHoldoutProtocolReceipt,
        binding: OSS3PredictiveStrategyBinding,
        now: datetime,
    ) -> OSS3PredictiveStrategyPreregistrationReceipt:
        _require_aware(now, "now")
        _verify_pristine_protocol(protocol)
        _verify_binding_protocol(protocol=protocol, binding=binding)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_row = conn.execute(
                "SELECT * FROM oss3_predictive_strategy_preregistrations WHERE protocol_id = ?",
                (protocol.protocol_id,),
            ).fetchone()
            if existing_row is not None:
                existing = _preregistration_from_row(existing_row)
                if existing.binding != binding:
                    raise PredictiveStrategyContractConflict(
                        "D2J protocol already has another D2L strategy binding"
                    )
                conn.execute("COMMIT")
                return existing

            _require_no_d2k_start(conn=conn, protocol=protocol)
            receipt = _build_preregistration_receipt(
                binding=binding,
                protocol=protocol,
                registered_at=now,
            )
            conn.execute(
                """
                INSERT INTO oss3_predictive_strategy_preregistrations(
                    protocol_id, protocol_receipt_hash,
                    expected_holdout_authorization_id,
                    winner_binding_fingerprint,
                    strategy_id, strategy_version, strategy_semantic_hash,
                    policy_hash, binding_hash, registered_at,
                    receipt_hash, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.protocol_id,
                    receipt.protocol_receipt_hash,
                    receipt.expected_holdout_authorization_id,
                    binding.winner_binding_fingerprint,
                    binding.strategy_id,
                    binding.strategy_version,
                    binding.strategy_semantic_hash,
                    binding.policy.fingerprint,
                    binding.binding_hash,
                    receipt.registered_at,
                    receipt.receipt_hash,
                    _canonical_json(receipt.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return receipt
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise PredictiveStrategyContractConflict(
                "D2L durable preregistration identity conflict"
            ) from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_for_protocol(
        self,
        protocol_id: str,
    ) -> OSS3PredictiveStrategyPreregistrationReceipt | None:
        _require_id(protocol_id, "protocol_id")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM oss3_predictive_strategy_preregistrations WHERE protocol_id = ?",
                (protocol_id,),
            ).fetchone()
            return _preregistration_from_row(row) if row is not None else None
        finally:
            conn.close()


def read_oss3d2l_preregistration_read_only(
    path: str | Path,
    *,
    protocol_id: str,
) -> OSS3PredictiveStrategyPreregistrationReceipt | None:
    _require_id(protocol_id, "protocol_id")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise PredictiveStrategyContractIntegrityError(
            "D2L durable registry does not exist"
        )
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        if not _table_exists(conn, "oss3_predictive_strategy_preregistrations"):
            return None
        row = conn.execute(
            "SELECT * FROM oss3_predictive_strategy_preregistrations WHERE protocol_id = ?",
            (protocol_id,),
        ).fetchone()
        return _preregistration_from_row(row) if row is not None else None
    finally:
        conn.close()


def predictive_strategy_code_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    payload: list[dict[str, str]] = []
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise PredictiveStrategyContractIntegrityError(
                f"missing D2L semantic file: {relative}"
            )
        payload.append(
            {"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()}
        )
    return _hash(payload)


def _verify_pristine_protocol(protocol: OSS3FinalHoldoutProtocolReceipt) -> None:
    if protocol.contract_version != OSS3D2J_CONTRACT_VERSION:
        raise PredictiveStrategyContractIntegrityError(
            "D2L requires canonical D2J protocol"
        )
    if (
        protocol.final_holdout_observed
        or protocol.final_holdout_consumed
        or protocol.holdout_permit_issued
        or protocol.holdout_permit_consumed
        or protocol.final_holdout_checkout_authorized
        or protocol.predictive_validation_passed
    ):
        raise PredictiveStrategyContractGovernanceError(
            "D2L requires untouched D2J FINAL_HOLDOUT state"
        )
    if (
        protocol.profitability_claim_authorized
        or protocol.promotion_authorized
        or protocol.execution_authorized
        or protocol.paper_execution_authorized
        or protocol.capital_authority != "NONE"
        or protocol.live_trading != "BLOCKED"
    ):
        raise PredictiveStrategyContractGovernanceError(
            "D2J protocol exceeds research-only authority"
        )


def _verify_winner_output(
    *,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    winner_output: FrozenCandidateOutput,
) -> None:
    winner = protocol.winner_binding
    manifest = winner_output.request.manifest
    prediction_manifest = winner_output.prediction.manifest
    for name, expected, actual in (
        ("candidate_id", winner.selected_trial_id, winner_output.candidate_id),
        ("request_hash", winner.request_hash, winner_output.request.request_hash),
        ("prediction_artifact_hash", winner.prediction_artifact_hash, winner_output.prediction.artifact_hash),
        ("prediction_receipt_hash", winner.prediction_receipt_hash, winner_output.receipt.fingerprint),
        ("environment_attestation_hash", winner.environment_attestation_hash, winner_output.attestation.artifact_hash),
        ("d2g_run_evidence_hash", winner.d2g_run_evidence_hash, winner_output.run_evidence_fingerprint),
        ("runtime_environment_hash", winner.runtime_environment_hash, winner_output.runtime_environment.fingerprint),
        ("model_family", winner.model_family, manifest.model_family),
        ("model_config_hash", winner.model_config_hash, manifest.model_config_hash),
        ("shared_runner_code_hash", winner.shared_runner_code_hash, manifest.expected_runner_code_hash),
        ("prediction model_family", winner.model_family, prediction_manifest.model_family),
        ("prediction model_config_hash", winner.model_config_hash, prediction_manifest.model_config_hash),
        ("prediction producer_code_hash", winner.shared_runner_code_hash, prediction_manifest.producer_code_hash),
        ("prediction feature_schema_hash", manifest.feature_schema_hash, prediction_manifest.feature_schema_hash),
        ("prediction training_dataset_hash", manifest.training_bundle_hash, prediction_manifest.training_dataset_hash),
    ):
        if expected != actual:
            raise PredictiveStrategyContractIntegrityError(
                f"D2L winner lineage mismatch: {name}"
            )


def _verify_binding_protocol(
    *,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    binding: OSS3PredictiveStrategyBinding,
) -> None:
    for name, expected, actual in (
        ("protocol_id", protocol.protocol_id, binding.protocol_id),
        ("protocol_receipt_hash", protocol.receipt_hash, binding.protocol_receipt_hash),
        (
            "expected_holdout_authorization_id",
            protocol.expected_holdout_authorization_id,
            binding.expected_holdout_authorization_id,
        ),
        (
            "winner_binding_fingerprint",
            protocol.winner_binding.fingerprint,
            binding.winner_binding_fingerprint,
        ),
        (
            "source_d2i_seal_fingerprint",
            protocol.source_d2i_seal_fingerprint,
            binding.source_d2i_seal_fingerprint,
        ),
        ("selected_trial_id", protocol.selected_trial_id, binding.selected_trial_id),
        ("model_config_hash", protocol.model_config_hash, binding.model_config_hash),
    ):
        if expected != actual:
            raise PredictiveStrategyContractIntegrityError(
                f"D2L binding/protocol mismatch: {name}"
            )


def _build_allocations(
    *,
    prediction: QlibPredictionArtifact,
    policy: PredictivePortfolioPolicy,
    strategy_id: str,
    strategy_version: str,
    strategy_semantic_hash: str,
) -> tuple[PredictiveTargetAllocation, ...]:
    grouped: dict[str, list[QlibPredictionRow]] = {}
    for row in prediction.rows:
        grouped.setdefault(row.timestamp, []).append(row)
    if not grouped:
        raise PredictiveStrategyContractIntegrityError(
            "prediction artifact has no cross sections"
        )
    allocations: list[PredictiveTargetAllocation] = []
    for timestamp in sorted(grouped):
        rows = grouped[timestamp]
        if len(rows) < policy.min_cross_section_observations:
            raise PredictiveStrategyContractGovernanceError(
                "prediction cross section is below D2L support floor"
            )
        symbols = tuple(row.symbol for row in rows)
        if len(set(symbols)) != len(symbols):
            raise PredictiveStrategyContractIntegrityError(
                "prediction cross section contains duplicate symbols"
            )
        ordered = sorted(rows, key=lambda row: (-float(row.score), row.symbol))
        selected_count = _selected_count(len(ordered), policy)
        selected_symbols = {row.symbol for row in ordered[:selected_count]}
        raw_equal = policy.gross_target / Decimal(selected_count)
        selected_weight = min(raw_equal, policy.max_weight_per_asset)
        weights = tuple(
            sorted(
                (
                    (row.symbol, selected_weight if row.symbol in selected_symbols else _ZERO)
                    for row in ordered
                ),
                key=lambda item: item[0],
            )
        )
        invested = sum((weight for _, weight in weights), _ZERO)
        if invested > policy.gross_target:
            raise PredictiveStrategyContractIntegrityError(
                "D2L allocation exceeds gross target"
            )
        if _ONE - invested < policy.reserve_cash_min:
            raise PredictiveStrategyContractIntegrityError(
                "D2L allocation violates cash reserve"
            )
        by_symbol = dict(weights)
        ranking = tuple(
            PredictiveRankedAsset(
                rank=index,
                symbol=row.symbol,
                score=float(row.score),
                selected=row.symbol in selected_symbols,
                target_weight=by_symbol[row.symbol],
            )
            for index, row in enumerate(ordered, start=1)
        )
        cross_section_hash = _hash([row.to_dict() for row in sorted(rows)])
        allocations.append(
            PredictiveTargetAllocation(
                allocation_version=OSS3D2L_ALLOCATION_VERSION,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                strategy_semantic_hash=strategy_semantic_hash,
                policy_fingerprint=policy.fingerprint,
                prediction_artifact_hash=prediction.artifact_hash,
                as_of=timestamp,
                source_cross_section_hash=cross_section_hash,
                source_symbol_count=len(ordered),
                selected_asset_count=selected_count,
                rankings=ranking,
                target_weights=weights,
                invested_weight=invested,
                cash_weight=_ONE - invested,
                execution_delay_bars=policy.execution_delay_bars,
                same_bar_execution_allowed=False,
                order_intents_generated=False,
                execution_authorized=False,
                paper_execution_authorized=False,
                capital_authority="NONE",
                live_trading="BLOCKED",
            )
        )
    return tuple(allocations)


def _selected_count(size: int, policy: PredictivePortfolioPolicy) -> int:
    fraction_count = int(
        (Decimal(size) * policy.selection_fraction).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    return min(
        size,
        policy.max_selected_assets,
        max(policy.min_selected_assets, fraction_count),
    )


def _allocation_semantic_fingerprint(allocation: PredictiveTargetAllocation) -> str:
    return _hash(
        {
            "as_of": allocation.as_of,
            "source_cross_section_hash": allocation.source_cross_section_hash,
            "rankings": [
                {
                    "rank": item.rank,
                    "symbol": item.symbol,
                    "score": float(item.score),
                    "selected": item.selected,
                    "target_weight": str(item.target_weight),
                }
                for item in allocation.rankings
            ],
            "target_weights": [
                [symbol, str(weight)] for symbol, weight in allocation.target_weights
            ],
            "invested_weight": str(allocation.invested_weight),
            "cash_weight": str(allocation.cash_weight),
            "execution_delay_bars": allocation.execution_delay_bars,
        }
    )


def _strategy_semantic_hash_from_binding(binding: OSS3PredictiveStrategyBinding) -> str:
    return _hash(
        {
            "protocol_receipt_hash": binding.protocol_receipt_hash,
            "winner_binding_fingerprint": binding.winner_binding_fingerprint,
            "source_d2i_seal_fingerprint": binding.source_d2i_seal_fingerprint,
            "selected_trial_id": binding.selected_trial_id,
            "model_family": binding.model_family,
            "model_config_hash": binding.model_config_hash,
            "request_hash": binding.request_hash,
            "development_prediction_artifact_hash": binding.development_prediction_artifact_hash,
            "development_prediction_payload_hash": binding.development_prediction_payload_hash,
            "development_prediction_receipt_hash": binding.development_prediction_receipt_hash,
            "environment_attestation_hash": binding.environment_attestation_hash,
            "d2g_run_evidence_hash": binding.d2g_run_evidence_hash,
            "shared_runner_code_hash": binding.shared_runner_code_hash,
            "runtime_environment_hash": binding.runtime_environment_hash,
            "qlib_version": binding.qlib_version,
            "training_dataset_hash": binding.training_dataset_hash,
            "feature_schema_hash": binding.feature_schema_hash,
            "policy_fingerprint": binding.policy.fingerprint,
            "binding_code_hash": binding.binding_code_hash,
            "development_allocation_evidence_hash": binding.development_allocation_evidence_hash,
            "strategy_id": binding.strategy_id,
            "runtime_kind": binding.runtime_kind,
        }
    )


def _strategy_version(strategy_semantic_hash: str) -> str:
    _require_hash(strategy_semantic_hash, "strategy_semantic_hash")
    return f"oss3d2l-{strategy_semantic_hash[:24]}"


def _build_preregistration_receipt(
    *,
    binding: OSS3PredictiveStrategyBinding,
    protocol: OSS3FinalHoldoutProtocolReceipt,
    registered_at: datetime,
) -> OSS3PredictiveStrategyPreregistrationReceipt:
    values: dict[str, object] = {
        "receipt_version": OSS3D2L_PREREGISTRATION_VERSION,
        "ordering_contract": SHARED_SQLITE_ORDERING_CONTRACT,
        "binding": binding,
        "protocol_id": protocol.protocol_id,
        "protocol_receipt_hash": protocol.receipt_hash,
        "expected_holdout_authorization_id": protocol.expected_holdout_authorization_id,
        "registered_at": registered_at.astimezone(timezone.utc).isoformat(),
        "shared_sqlite_ordering_enforced": True,
        "d2k_start_absent_at_commit": True,
        "final_holdout_observed": False,
        "final_holdout_consumed": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS3PredictiveStrategyPreregistrationReceipt(
        **values,
        receipt_hash=_hash(_preregistration_payload_from_values(values)),
    )


def _require_no_d2k_start(
    *,
    conn: sqlite3.Connection,
    protocol: OSS3FinalHoldoutProtocolReceipt,
) -> None:
    if _table_exists(conn, "oss3_final_holdout_evaluation_starts"):
        row = conn.execute(
            "SELECT evaluation_id FROM oss3_final_holdout_evaluation_starts "
            "WHERE protocol_id = ? OR holdout_authorization_id = ? LIMIT 1",
            (protocol.protocol_id, protocol.expected_holdout_authorization_id),
        ).fetchone()
        if row is not None:
            raise PredictiveStrategyContractGovernanceError(
                "D2L strategy policy cannot be preregistered after D2K start"
            )
    if _table_exists(conn, "holdout_permits"):
        permit = conn.execute(
            "SELECT permit_id FROM holdout_permits WHERE permit_id = ? LIMIT 1",
            (protocol.expected_holdout_authorization_id,),
        ).fetchone()
        if permit is not None:
            raise PredictiveStrategyContractGovernanceError(
                "D2L strategy policy cannot be preregistered after holdout permit consumption"
            )


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _preregistration_from_row(
    row: sqlite3.Row,
) -> OSS3PredictiveStrategyPreregistrationReceipt:
    try:
        payload = json.loads(str(row["receipt_json"]))
        if not isinstance(payload, Mapping):
            raise TypeError("receipt_json must be an object")
        values = dict(payload)
        binding_payload = values.get("binding")
        if not isinstance(binding_payload, Mapping):
            raise TypeError("binding must be object")
        values["binding"] = _binding_from_dict(binding_payload)
        receipt = OSS3PredictiveStrategyPreregistrationReceipt(**values)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise PredictiveStrategyContractIntegrityError(
            "invalid durable D2L preregistration receipt"
        ) from exc
    for column, expected in (
        ("protocol_id", receipt.protocol_id),
        ("protocol_receipt_hash", receipt.protocol_receipt_hash),
        (
            "expected_holdout_authorization_id",
            receipt.expected_holdout_authorization_id,
        ),
        ("winner_binding_fingerprint", receipt.binding.winner_binding_fingerprint),
        ("strategy_id", receipt.binding.strategy_id),
        ("strategy_version", receipt.binding.strategy_version),
        ("strategy_semantic_hash", receipt.binding.strategy_semantic_hash),
        ("policy_hash", receipt.binding.policy.fingerprint),
        ("binding_hash", receipt.binding.binding_hash),
        ("registered_at", receipt.registered_at),
        ("receipt_hash", receipt.receipt_hash),
    ):
        if str(row[column]) != expected:
            raise PredictiveStrategyContractIntegrityError(
                f"D2L durable column mismatch: {column}"
            )
    if _canonical_json(receipt.to_dict()) != str(row["receipt_json"]):
        raise PredictiveStrategyContractIntegrityError(
            "D2L durable receipt serialization drifted"
        )
    return receipt


def _binding_from_dict(payload: Mapping[str, object]) -> OSS3PredictiveStrategyBinding:
    values = dict(payload)
    values.pop("policy_fingerprint", None)
    policy_payload = values.get("policy")
    if not isinstance(policy_payload, Mapping):
        raise TypeError("policy must be object")
    values["policy"] = _policy_from_dict(policy_payload)
    raw_allocations = values.get("development_allocation_fingerprints")
    if not isinstance(raw_allocations, list):
        raise TypeError("development_allocation_fingerprints must be list")
    values["development_allocation_fingerprints"] = tuple(
        str(item) for item in raw_allocations
    )
    return OSS3PredictiveStrategyBinding(**values)


def _policy_from_dict(payload: Mapping[str, object]) -> PredictivePortfolioPolicy:
    values = dict(payload)
    for name in (
        "selection_fraction",
        "gross_target",
        "max_weight_per_asset",
        "reserve_cash_min",
    ):
        values[name] = Decimal(str(values[name]))
    return PredictivePortfolioPolicy(**values)


def _binding_payload_from_values(values: Mapping[str, object]) -> dict[str, object]:
    payload = dict(values)
    policy = payload.get("policy")
    if isinstance(policy, PredictivePortfolioPolicy):
        payload["policy"] = policy.to_dict()
        payload["policy_fingerprint"] = policy.fingerprint
    allocations = payload.get("development_allocation_fingerprints")
    if isinstance(allocations, tuple):
        payload["development_allocation_fingerprints"] = list(allocations)
    return payload


def _preregistration_payload_from_values(values: Mapping[str, object]) -> dict[str, object]:
    payload = dict(values)
    binding = payload.get("binding")
    if isinstance(binding, OSS3PredictiveStrategyBinding):
        payload["binding"] = binding.to_dict()
    return payload


def _deny_authority(
    *,
    same_bar_execution_allowed: bool,
    order_intents_generated: bool,
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if (
        same_bar_execution_allowed
        or order_intents_generated
        or execution_authorized
        or paper_execution_authorized
    ):
        raise PredictiveStrategyContractGovernanceError(
            "D2L target allocation may not grant execution authority"
        )
    if capital_authority != "NONE" or live_trading != "BLOCKED":
        raise PredictiveStrategyContractGovernanceError(
            "D2L target allocation may not grant capital/LIVE"
        )


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_decimal(value: object, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be finite Decimal")


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
