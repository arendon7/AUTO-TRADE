"""OSS-3D2N one-shot cost-aware economic holdout evaluator.

D2N is the economic counterpart to D2K.  It consumes an already-preregistered
D2M protocol, requires the exact D2L strategy binding and an already-terminal
D2K predictive PASS in the same authoritative SQLite file, then evaluates one
protected, temporally later economic holdout exactly once.

The evaluator never runs Qlib.  It consumes only a frozen-model
``QlibPredictionArtifact`` already bound to the D2L strategy and an exact
``AlignedMarketUniverse`` protected by the D2M commitment.  Predictions at the
close of bar t become target weights and may rebalance only at bar t+1 open.

D2N uses the frozen D2M cost policy, quantity-step floor, volume cap,
min-notional and cash constraints.  It is long-only and unlevered.  A PASS is
only cost-aware OOS research evidence.  It does not authorize promotion,
PAPER, capital or LIVE trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from math import inf, isfinite, isinf, sqrt
import os
from pathlib import Path
import re
import sqlite3
from statistics import fmean, stdev
from typing import Mapping

from autotrade.domain import Side
from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact
from autotrade.research.universe import AlignedMarketUniverse

from .final_holdout_evaluator import (
    OSS3FinalHoldoutDecision,
    read_oss3d2k_evaluation_read_only,
)
from .final_holdout_protocol import OSS3FinalHoldoutProtocolReceipt
from .predictive_economic_protocol import (
    ANNUALIZATION_POLICY,
    EXECUTION_PRICE_POLICY,
    ROUNDING_POLICY,
    VOLUME_POLICY,
    EconomicHoldoutCommitment,
    OSS3PredictiveEconomicProtocolReceipt,
)
from .predictive_strategy_contract import (
    OSS3PredictiveStrategyPreregistrationReceipt,
    PredictiveTargetAllocation,
    project_prediction_artifact,
)


OSS3D2N_MATERIAL_VERSION = "OSS3D2N_PROTECTED_ECONOMIC_HOLDOUT_MATERIAL_V1"
OSS3D2N_START_VERSION = "OSS3D2N_ECONOMIC_HOLDOUT_START_V1"
OSS3D2N_METRICS_VERSION = "OSS3D2N_ECONOMIC_HOLDOUT_METRICS_V1"
OSS3D2N_RECEIPT_VERSION = "OSS3D2N_ECONOMIC_HOLDOUT_EVALUATION_V1"
OSS3D2N_FILL_VERSION = "OSS3D2N_RESEARCH_FILL_V1"
OSS3D2N_EQUITY_VERSION = "OSS3D2N_EQUITY_POINT_V1"

INITIAL_CASH = Decimal("100000")
INITIAL_CASH_POLICY = "FIXED_100000_QUOTE_UNITS_V1"
PREDICTION_SUPPORT_POLICY = "EVERY_NONTERMINAL_BAR_CLOSE_FULL_UNIVERSE_V1"
PROFIT_FACTOR_POLICY = "REALIZED_SELL_PNL_AVERAGE_COST_V1"
NO_TERMINAL_LIQUIDATION_POLICY = "MARK_TO_MARKET_ONLY_NO_FORCED_FINAL_TRADE_V1"
TEMPORAL_POLICY = "ECONOMIC_HOLDOUT_STRICTLY_AFTER_PREDICTIVE_HOLDOUT_V1"

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_SECONDS_PER_365_DAY_YEAR = Decimal(365 * 24 * 60 * 60)

_BROKER_CREDENTIAL_PREFIXES = (
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
    "labs/oss3_qlib/economic_holdout_evaluator.py",
    "labs/oss3_qlib/predictive_economic_protocol.py",
    "labs/oss3_qlib/predictive_strategy_contract.py",
    "labs/oss3_qlib/final_holdout_evaluator.py",
    "labs/oss3_qlib/final_holdout_protocol.py",
    "src/autotrade/research/costs.py",
    "src/autotrade/research/market.py",
    "src/autotrade/research/universe.py",
    "src/autotrade/research/oss3_qlib_artifact.py",
)


class OSS3EconomicHoldoutError(RuntimeError):
    """Base OSS-3D2N failure."""


class OSS3EconomicHoldoutIntegrityError(OSS3EconomicHoldoutError):
    """Frozen material or durable lineage drifted."""


class OSS3EconomicHoldoutGovernanceError(OSS3EconomicHoldoutError):
    """Operation exceeds one-shot research governance."""


class OSS3EconomicHoldoutAlreadyConsumed(OSS3EconomicHoldoutGovernanceError):
    """The economic protocol already has a durable start/terminal result."""


class OSS3EconomicDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class EconomicHoldoutMaterial:
    material_version: str
    commitment_id: str
    universe: AlignedMarketUniverse
    prediction: QlibPredictionArtifact

    def __post_init__(self) -> None:
        if self.material_version != OSS3D2N_MATERIAL_VERSION:
            raise OSS3EconomicHoldoutIntegrityError(
                "noncanonical D2N economic material version"
            )
        _require_id(self.commitment_id, "commitment_id")
        if not isinstance(self.universe, AlignedMarketUniverse):
            raise TypeError("universe must be AlignedMarketUniverse")
        if not isinstance(self.prediction, QlibPredictionArtifact):
            raise TypeError("prediction must be QlibPredictionArtifact")
        _verify_prediction_support(
            universe=self.universe,
            prediction=self.prediction,
        )

    @property
    def commitment(self) -> EconomicHoldoutCommitment:
        universe = self.universe
        start = universe.datasets[0].bars[0].started_at.astimezone(timezone.utc)
        end = universe.datasets[0].bars[-1].ended_at.astimezone(timezone.utc)
        return EconomicHoldoutCommitment(
            commitment_version="OSS3D2M_ECONOMIC_HOLDOUT_COMMITMENT_V1",
            commitment_id=self.commitment_id,
            purpose="ONE_SHOT_COST_AWARE_OOS_ECONOMIC_QUALIFICATION",
            universe_hash=universe.universe_hash,
            universe_name=universe.universe_name,
            source_dataset_set_hash=_dataset_set_hash(universe),
            symbols=universe.symbols,
            quote_currency=universe.quote_currency,
            timeframe_seconds=universe.timeframe_seconds,
            partition_start=start.isoformat(),
            partition_end=end.isoformat(),
            bar_count=universe.bar_count,
            symbol_count=len(universe.symbols),
            market_values_exposed=False,
            economic_outcomes_observed=False,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "material_version": self.material_version,
                "commitment_fingerprint": self.commitment.fingerprint,
                "prediction_artifact_hash": self.prediction.artifact_hash,
                "prediction_payload_hash": self.prediction.manifest.prediction_payload_hash,
            }
        )


class ProtectedEconomicHoldout:
    """One-shot wrapper that releases market values only after durable D2N start."""

    def __init__(self, material: EconomicHoldoutMaterial) -> None:
        if not isinstance(material, EconomicHoldoutMaterial):
            raise TypeError("material must be EconomicHoldoutMaterial")
        self._material = material
        self._consumed = False

    @property
    def commitment(self) -> EconomicHoldoutCommitment:
        return self._material.commitment

    def _checkout(
        self,
        *,
        start_receipt: "OSS3EconomicEvaluationStart",
        registry_path: str | Path,
    ) -> EconomicHoldoutMaterial:
        if self._consumed:
            raise OSS3EconomicHoldoutAlreadyConsumed(
                "protected economic holdout already consumed"
            )
        if not isinstance(start_receipt, OSS3EconomicEvaluationStart):
            raise TypeError("start_receipt must be OSS3EconomicEvaluationStart")
        if start_receipt.economic_holdout_commitment_fingerprint != self.commitment.fingerprint:
            raise OSS3EconomicHoldoutIntegrityError(
                "D2N start does not authorize this economic holdout commitment"
            )
        _prove_durable_start(registry_path=registry_path, start=start_receipt)
        self._consumed = True
        return self._material


@dataclass(frozen=True, slots=True)
class OSS3EconomicResearchFill:
    fill_version: str
    fill_id: str
    allocation_fingerprint: str
    symbol: str
    side: str
    quantity: Decimal
    reference_price: Decimal
    execution_price: Decimal
    fee: Decimal
    signal_index: int
    execution_index: int
    occurred_at: str
    volume_participation: Decimal
    realized_pnl: Decimal

    def __post_init__(self) -> None:
        if self.fill_version != OSS3D2N_FILL_VERSION:
            raise OSS3EconomicHoldoutIntegrityError("noncanonical D2N fill version")
        _require_id(self.fill_id, "fill_id")
        _require_hash(self.allocation_fingerprint, "allocation_fingerprint")
        if self.side not in {Side.BUY.value, Side.SELL.value}:
            raise ValueError("invalid fill side")
        for name, value in (
            ("quantity", self.quantity),
            ("reference_price", self.reference_price),
            ("execution_price", self.execution_price),
            ("fee", self.fee),
            ("volume_participation", self.volume_participation),
            ("realized_pnl", self.realized_pnl),
        ):
            _require_decimal(value, name)
        if self.quantity <= _ZERO or self.reference_price <= _ZERO or self.execution_price <= _ZERO:
            raise ValueError("fill quantity/prices must be positive")
        if self.fee < _ZERO or not _ZERO <= self.volume_participation <= _ONE:
            raise ValueError("invalid fill fee/participation")
        if self.side == Side.BUY.value and self.realized_pnl != _ZERO:
            raise OSS3EconomicHoldoutIntegrityError(
                "BUY fill may not realize PnL in long-only average-cost accounting"
            )
        for name, value in (("signal_index", self.signal_index), ("execution_index", self.execution_index)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be integer >=0")
        if self.execution_index != self.signal_index + 1:
            raise OSS3EconomicHoldoutGovernanceError(
                "D2N fill must execute exactly one bar after signal"
            )
        _parse_canonical_utc(self.occurred_at, "fill occurred_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "fill_version": self.fill_version,
            "fill_id": self.fill_id,
            "allocation_fingerprint": self.allocation_fingerprint,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "reference_price": str(self.reference_price),
            "execution_price": str(self.execution_price),
            "fee": str(self.fee),
            "signal_index": self.signal_index,
            "execution_index": self.execution_index,
            "occurred_at": self.occurred_at,
            "volume_participation": str(self.volume_participation),
            "realized_pnl": str(self.realized_pnl),
        }


@dataclass(frozen=True, slots=True)
class OSS3EconomicEquityPoint:
    point_version: str
    occurred_at: str
    cash: Decimal
    positions: tuple[tuple[str, Decimal], ...]
    equity: Decimal
    gross_exposure: Decimal

    def __post_init__(self) -> None:
        if self.point_version != OSS3D2N_EQUITY_VERSION:
            raise OSS3EconomicHoldoutIntegrityError("noncanonical D2N equity version")
        _parse_canonical_utc(self.occurred_at, "equity occurred_at")
        for name, value in (
            ("cash", self.cash),
            ("equity", self.equity),
            ("gross_exposure", self.gross_exposure),
        ):
            _require_decimal(value, name)
        if self.cash < _ZERO or self.equity <= _ZERO or self.gross_exposure < _ZERO:
            raise OSS3EconomicHoldoutGovernanceError(
                "D2N long-only no-margin portfolio produced invalid cash/equity"
            )
        symbols = tuple(symbol for symbol, _ in self.positions)
        if symbols != tuple(sorted(symbols)) or len(set(symbols)) != len(symbols):
            raise OSS3EconomicHoldoutIntegrityError("equity positions must be canonical")
        for _, quantity in self.positions:
            _require_decimal(quantity, "position quantity")
            if quantity < _ZERO:
                raise OSS3EconomicHoldoutGovernanceError("D2N may not hold short positions")

    def to_dict(self) -> dict[str, object]:
        return {
            "point_version": self.point_version,
            "occurred_at": self.occurred_at,
            "cash": str(self.cash),
            "positions": [[symbol, str(quantity)] for symbol, quantity in self.positions],
            "equity": str(self.equity),
            "gross_exposure": str(self.gross_exposure),
        }


@dataclass(frozen=True, slots=True)
class OSS3EconomicMetrics:
    metrics_version: str
    initial_cash: Decimal
    net_return: float
    annualization_factor: float
    sharpe: float
    profit_factor: float
    max_drawdown: float
    turnover: float
    total_fees: float
    max_volume_participation: float
    average_gross_exposure_ratio: float
    max_gross_exposure_ratio: float
    average_target_tracking_error: float
    max_target_tracking_error: float
    fills: int
    rebalances: int
    realized_closing_fills: int
    realized_win_count: int
    realized_loss_count: int
    profit_factor_policy: str
    terminal_liquidation_policy: str

    def __post_init__(self) -> None:
        if self.metrics_version != OSS3D2N_METRICS_VERSION:
            raise OSS3EconomicHoldoutIntegrityError("noncanonical D2N metrics version")
        _require_decimal(self.initial_cash, "initial_cash")
        if self.initial_cash != INITIAL_CASH:
            raise OSS3EconomicHoldoutGovernanceError("D2N initial NAV is frozen")
        for name in (
            "net_return",
            "annualization_factor",
            "sharpe",
            "max_drawdown",
            "turnover",
            "total_fees",
            "max_volume_participation",
            "average_gross_exposure_ratio",
            "max_gross_exposure_ratio",
            "average_target_tracking_error",
            "max_target_tracking_error",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if isinstance(self.profit_factor, bool) or not isinstance(self.profit_factor, (int, float)):
            raise ValueError("profit_factor must be numeric")
        if not (isfinite(float(self.profit_factor)) or isinf(float(self.profit_factor))):
            raise ValueError("profit_factor invalid")
        if self.max_drawdown < 0 or self.turnover < 0 or self.total_fees < 0:
            raise ValueError("negative risk/cost metric")
        for name in (
            "fills",
            "rebalances",
            "realized_closing_fills",
            "realized_win_count",
            "realized_loss_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be integer >=0")
        if self.realized_win_count + self.realized_loss_count > self.realized_closing_fills:
            raise OSS3EconomicHoldoutIntegrityError("realized trade counts inconsistent")
        if self.profit_factor_policy != PROFIT_FACTOR_POLICY:
            raise OSS3EconomicHoldoutGovernanceError("D2N profit-factor policy drifted")
        if self.terminal_liquidation_policy != NO_TERMINAL_LIQUIDATION_POLICY:
            raise OSS3EconomicHoldoutGovernanceError("D2N terminal policy drifted")

    def to_dict(self) -> dict[str, object]:
        return {
            "metrics_version": self.metrics_version,
            "initial_cash": str(self.initial_cash),
            "net_return": float(self.net_return),
            "annualization_factor": float(self.annualization_factor),
            "sharpe": float(self.sharpe),
            "profit_factor": "inf" if isinf(float(self.profit_factor)) else float(self.profit_factor),
            "max_drawdown": float(self.max_drawdown),
            "turnover": float(self.turnover),
            "total_fees": float(self.total_fees),
            "max_volume_participation": float(self.max_volume_participation),
            "average_gross_exposure_ratio": float(self.average_gross_exposure_ratio),
            "max_gross_exposure_ratio": float(self.max_gross_exposure_ratio),
            "average_target_tracking_error": float(self.average_target_tracking_error),
            "max_target_tracking_error": float(self.max_target_tracking_error),
            "fills": self.fills,
            "rebalances": self.rebalances,
            "realized_closing_fills": self.realized_closing_fills,
            "realized_win_count": self.realized_win_count,
            "realized_loss_count": self.realized_loss_count,
            "profit_factor_policy": self.profit_factor_policy,
            "terminal_liquidation_policy": self.terminal_liquidation_policy,
        }


@dataclass(frozen=True, slots=True)
class OSS3EconomicGateEvidence:
    gate_id: str
    operator: str
    threshold: str
    observed: str
    passed: bool

    def __post_init__(self) -> None:
        _require_id(self.gate_id, "gate_id")
        if self.operator not in {">", ">=", "<="}:
            raise ValueError("unsupported economic gate operator")
        if not isinstance(self.passed, bool):
            raise TypeError("gate passed must be bool")

    def to_dict(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "operator": self.operator,
            "threshold": self.threshold,
            "observed": self.observed,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class OSS3EconomicEvaluationStart:
    start_version: str
    evaluation_id: str
    economic_protocol_id: str
    economic_protocol_receipt_hash: str
    source_d2l_receipt_hash: str
    source_strategy_semantic_hash: str
    source_d2j_protocol_id: str
    source_d2j_protocol_receipt_hash: str
    predictive_d2k_receipt_hash: str
    economic_holdout_commitment_fingerprint: str
    economic_prediction_artifact_hash: str
    evaluator_semantic_hash: str
    initial_cash: Decimal
    started_at: str
    start_hash: str

    def __post_init__(self) -> None:
        if self.start_version != OSS3D2N_START_VERSION:
            raise OSS3EconomicHoldoutIntegrityError("noncanonical D2N start version")
        for name in (
            "evaluation_id",
            "economic_protocol_id",
            "source_d2j_protocol_id",
        ):
            _require_id(getattr(self, name), name)
        for name in (
            "economic_protocol_receipt_hash",
            "source_d2l_receipt_hash",
            "source_strategy_semantic_hash",
            "source_d2j_protocol_receipt_hash",
            "predictive_d2k_receipt_hash",
            "economic_holdout_commitment_fingerprint",
            "economic_prediction_artifact_hash",
            "evaluator_semantic_hash",
            "start_hash",
        ):
            _require_hash(getattr(self, name), name)
        _require_decimal(self.initial_cash, "initial_cash")
        if self.initial_cash != INITIAL_CASH:
            raise OSS3EconomicHoldoutGovernanceError("D2N start initial cash drifted")
        _parse_canonical_utc(self.started_at, "started_at")
        if self.start_hash != _hash(self.to_dict(include_hash=False)):
            raise OSS3EconomicHoldoutIntegrityError("D2N start hash mismatch")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "start_version": self.start_version,
            "evaluation_id": self.evaluation_id,
            "economic_protocol_id": self.economic_protocol_id,
            "economic_protocol_receipt_hash": self.economic_protocol_receipt_hash,
            "source_d2l_receipt_hash": self.source_d2l_receipt_hash,
            "source_strategy_semantic_hash": self.source_strategy_semantic_hash,
            "source_d2j_protocol_id": self.source_d2j_protocol_id,
            "source_d2j_protocol_receipt_hash": self.source_d2j_protocol_receipt_hash,
            "predictive_d2k_receipt_hash": self.predictive_d2k_receipt_hash,
            "economic_holdout_commitment_fingerprint": self.economic_holdout_commitment_fingerprint,
            "economic_prediction_artifact_hash": self.economic_prediction_artifact_hash,
            "evaluator_semantic_hash": self.evaluator_semantic_hash,
            "initial_cash": str(self.initial_cash),
            "started_at": self.started_at,
        }
        if include_hash:
            payload["start_hash"] = self.start_hash
        return payload


@dataclass(frozen=True, slots=True)
class OSS3EconomicEvaluationReceipt:
    receipt_version: str
    evaluation_id: str
    economic_protocol_id: str
    economic_protocol_receipt_hash: str
    start_hash: str
    source_d2l_receipt_hash: str
    source_strategy_id: str
    source_strategy_version: str
    source_strategy_semantic_hash: str
    source_d2j_protocol_id: str
    source_d2j_protocol_receipt_hash: str
    predictive_d2k_receipt_hash: str
    economic_holdout_commitment_fingerprint: str
    economic_prediction_artifact_hash: str
    evaluator_semantic_hash: str
    result_hash: str
    decision: OSS3EconomicDecision
    gates: tuple[OSS3EconomicGateEvidence, ...]
    failed_gate_ids: tuple[str, ...]
    metrics: OSS3EconomicMetrics | None
    failure_code: str
    started_at: str
    terminal_at: str
    predictive_validation_passed: bool
    economic_holdout_observed: bool
    economic_holdout_consumed: bool
    economic_validation_passed: bool
    second_attempt_allowed: bool
    retuning_allowed: bool
    reselection_allowed: bool
    profitability_claim_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D2N_RECEIPT_VERSION:
            raise OSS3EconomicHoldoutIntegrityError("noncanonical D2N receipt version")
        for name in (
            "evaluation_id",
            "economic_protocol_id",
            "source_strategy_id",
            "source_strategy_version",
            "source_d2j_protocol_id",
        ):
            _require_id(getattr(self, name), name)
        for name in (
            "economic_protocol_receipt_hash",
            "start_hash",
            "source_d2l_receipt_hash",
            "source_strategy_semantic_hash",
            "source_d2j_protocol_receipt_hash",
            "predictive_d2k_receipt_hash",
            "economic_holdout_commitment_fingerprint",
            "economic_prediction_artifact_hash",
            "evaluator_semantic_hash",
            "result_hash",
            "receipt_hash",
        ):
            _require_hash(getattr(self, name), name)
        if not isinstance(self.decision, OSS3EconomicDecision):
            raise TypeError("decision must be OSS3EconomicDecision")
        _parse_canonical_utc(self.started_at, "started_at")
        _parse_canonical_utc(self.terminal_at, "terminal_at")
        if _parse_canonical_utc(self.terminal_at, "terminal_at") < _parse_canonical_utc(self.started_at, "started_at"):
            raise OSS3EconomicHoldoutIntegrityError("terminal_at predates started_at")
        if self.predictive_validation_passed is not True:
            raise OSS3EconomicHoldoutGovernanceError("D2N requires predictive D2K PASS")
        if self.economic_holdout_observed is not True or self.economic_holdout_consumed is not True:
            raise OSS3EconomicHoldoutIntegrityError("terminal D2N must consume economic holdout")
        if self.second_attempt_allowed or self.retuning_allowed or self.reselection_allowed:
            raise OSS3EconomicHoldoutGovernanceError("D2N terminal receipt cannot allow second chance")
        if (
            self.profitability_claim_authorized
            or self.promotion_authorized
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise OSS3EconomicHoldoutGovernanceError("D2N may not grant trading authority")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise OSS3EconomicHoldoutGovernanceError("D2N may not grant capital/LIVE")
        if self.metrics is None:
            if self.decision is not OSS3EconomicDecision.FAIL or not self.failure_code:
                raise OSS3EconomicHoldoutIntegrityError("structural D2N failure receipt invalid")
            if self.gates or self.failed_gate_ids:
                raise OSS3EconomicHoldoutIntegrityError("structural D2N failure may not fabricate gates")
            if self.economic_validation_passed:
                raise OSS3EconomicHoldoutIntegrityError("structural failure cannot pass")
        else:
            if self.failure_code:
                raise OSS3EconomicHoldoutIntegrityError("metric receipt cannot carry failure_code")
            expected_failed = tuple(gate.gate_id for gate in self.gates if not gate.passed)
            if self.failed_gate_ids != expected_failed:
                raise OSS3EconomicHoldoutIntegrityError("failed gate ids mismatch")
            expected_decision = (
                OSS3EconomicDecision.PASS
                if self.gates and all(gate.passed for gate in self.gates)
                else OSS3EconomicDecision.FAIL
            )
            if self.decision is not expected_decision:
                raise OSS3EconomicHoldoutIntegrityError("D2N decision differs from gates")
            if self.economic_validation_passed is not (self.decision is OSS3EconomicDecision.PASS):
                raise OSS3EconomicHoldoutIntegrityError("economic_validation_passed mismatch")
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise OSS3EconomicHoldoutIntegrityError("D2N receipt hash mismatch")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "receipt_version": self.receipt_version,
            "evaluation_id": self.evaluation_id,
            "economic_protocol_id": self.economic_protocol_id,
            "economic_protocol_receipt_hash": self.economic_protocol_receipt_hash,
            "start_hash": self.start_hash,
            "source_d2l_receipt_hash": self.source_d2l_receipt_hash,
            "source_strategy_id": self.source_strategy_id,
            "source_strategy_version": self.source_strategy_version,
            "source_strategy_semantic_hash": self.source_strategy_semantic_hash,
            "source_d2j_protocol_id": self.source_d2j_protocol_id,
            "source_d2j_protocol_receipt_hash": self.source_d2j_protocol_receipt_hash,
            "predictive_d2k_receipt_hash": self.predictive_d2k_receipt_hash,
            "economic_holdout_commitment_fingerprint": self.economic_holdout_commitment_fingerprint,
            "economic_prediction_artifact_hash": self.economic_prediction_artifact_hash,
            "evaluator_semantic_hash": self.evaluator_semantic_hash,
            "result_hash": self.result_hash,
            "decision": self.decision.value,
            "gates": [gate.to_dict() for gate in self.gates],
            "failed_gate_ids": list(self.failed_gate_ids),
            "metrics": self.metrics.to_dict() if self.metrics is not None else None,
            "failure_code": self.failure_code,
            "started_at": self.started_at,
            "terminal_at": self.terminal_at,
            "predictive_validation_passed": self.predictive_validation_passed,
            "economic_holdout_observed": self.economic_holdout_observed,
            "economic_holdout_consumed": self.economic_holdout_consumed,
            "economic_validation_passed": self.economic_validation_passed,
            "second_attempt_allowed": self.second_attempt_allowed,
            "retuning_allowed": self.retuning_allowed,
            "reselection_allowed": self.reselection_allowed,
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


class SQLiteOSS3EconomicHoldoutEvaluationRegistry:
    """One-start/one-terminal economic evaluator on the shared authoritative SQLite."""

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
                CREATE TABLE IF NOT EXISTS oss3_economic_holdout_evaluation_starts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id TEXT NOT NULL UNIQUE,
                    economic_protocol_id TEXT NOT NULL UNIQUE,
                    economic_protocol_receipt_hash TEXT NOT NULL UNIQUE,
                    source_d2l_receipt_hash TEXT NOT NULL,
                    source_strategy_semantic_hash TEXT NOT NULL UNIQUE,
                    source_d2j_protocol_id TEXT NOT NULL UNIQUE,
                    source_d2j_protocol_receipt_hash TEXT NOT NULL,
                    predictive_d2k_receipt_hash TEXT NOT NULL UNIQUE,
                    economic_holdout_commitment_fingerprint TEXT NOT NULL UNIQUE,
                    economic_prediction_artifact_hash TEXT NOT NULL UNIQUE,
                    evaluator_semantic_hash TEXT NOT NULL,
                    initial_cash TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    start_hash TEXT NOT NULL UNIQUE,
                    start_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS oss3_economic_holdout_evaluations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id TEXT NOT NULL UNIQUE,
                    economic_protocol_id TEXT NOT NULL UNIQUE,
                    start_hash TEXT NOT NULL UNIQUE,
                    decision TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS oss3_economic_holdout_starts_no_update
                BEFORE UPDATE ON oss3_economic_holdout_evaluation_starts
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2N start registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_economic_holdout_starts_no_delete
                BEFORE DELETE ON oss3_economic_holdout_evaluation_starts
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2N start registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_economic_holdout_terminal_no_update
                BEFORE UPDATE ON oss3_economic_holdout_evaluations
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2N terminal registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_economic_holdout_terminal_no_delete
                BEFORE DELETE ON oss3_economic_holdout_evaluations
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2N terminal registry is append-only');
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
        economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
        d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
        d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
        holdout: ProtectedEconomicHoldout,
        now: datetime,
    ) -> OSS3EconomicEvaluationReceipt:
        _require_id(evaluation_id, "evaluation_id")
        _require_aware(now, "now")
        _verify_protocol_chain(
            economic_protocol=economic_protocol,
            d2l_receipt=d2l_receipt,
            d2j_protocol=d2j_protocol,
        )
        if not isinstance(holdout, ProtectedEconomicHoldout):
            raise TypeError("holdout must be ProtectedEconomicHoldout")
        if holdout.commitment.fingerprint != economic_protocol.economic_holdout_commitment.fingerprint:
            raise OSS3EconomicHoldoutIntegrityError(
                "protected economic holdout differs from D2M commitment"
            )
        _verify_temporal_separation(
            d2j_protocol=d2j_protocol,
            economic_commitment=holdout.commitment,
        )
        _reject_broker_credentials()
        predictive = _require_d2k_pass(
            path=self.path,
            d2j_protocol=d2j_protocol,
        )
        evaluator_hash = economic_evaluator_semantic_hash()
        start = _build_start(
            evaluation_id=evaluation_id,
            economic_protocol=economic_protocol,
            d2l_receipt=d2l_receipt,
            d2j_protocol=d2j_protocol,
            predictive_receipt_hash=predictive.receipt_hash,
            holdout=holdout,
            evaluator_hash=evaluator_hash,
            started_at=now,
        )
        self._record_start(start)

        try:
            material = holdout._checkout(start_receipt=start, registry_path=self.path)
            if material.commitment.fingerprint != start.economic_holdout_commitment_fingerprint:
                raise OSS3EconomicHoldoutIntegrityError(
                    "economic holdout changed after durable start"
                )
            allocations = project_prediction_artifact(
                binding=d2l_receipt.binding,
                prediction=material.prediction,
            )
            result = _simulate(
                economic_protocol=economic_protocol,
                universe=material.universe,
                allocations=allocations,
            )
            receipt = _build_metric_receipt(
                start=start,
                economic_protocol=economic_protocol,
                d2l_receipt=d2l_receipt,
                result=result,
                terminal_at=now,
            )
        except Exception as exc:
            receipt = _build_structural_failure_receipt(
                start=start,
                economic_protocol=economic_protocol,
                d2l_receipt=d2l_receipt,
                failure_code=f"EVALUATION_ERROR:{type(exc).__name__}",
                terminal_at=now,
            )
        self._record_terminal(receipt)
        return receipt

    def _record_start(self, start: OSS3EconomicEvaluationStart) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT start_json FROM oss3_economic_holdout_evaluation_starts "
                "WHERE economic_protocol_id = ?",
                (start.economic_protocol_id,),
            ).fetchone()
            if existing is not None:
                raise OSS3EconomicHoldoutAlreadyConsumed(
                    "economic protocol already has a consumed start"
                )
            terminal = conn.execute(
                "SELECT receipt_hash FROM oss3_economic_holdout_evaluations "
                "WHERE economic_protocol_id = ?",
                (start.economic_protocol_id,),
            ).fetchone()
            if terminal is not None:
                raise OSS3EconomicHoldoutAlreadyConsumed(
                    "economic protocol already has terminal evidence"
                )
            _require_exact_d2m_durable_state(conn=conn, start=start)
            _require_exact_d2k_pass_durable(conn=conn, start=start)
            conn.execute(
                """
                INSERT INTO oss3_economic_holdout_evaluation_starts(
                    evaluation_id, economic_protocol_id,
                    economic_protocol_receipt_hash, source_d2l_receipt_hash,
                    source_strategy_semantic_hash, source_d2j_protocol_id,
                    source_d2j_protocol_receipt_hash, predictive_d2k_receipt_hash,
                    economic_holdout_commitment_fingerprint,
                    economic_prediction_artifact_hash, evaluator_semantic_hash,
                    initial_cash, started_at, start_hash, start_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    start.evaluation_id,
                    start.economic_protocol_id,
                    start.economic_protocol_receipt_hash,
                    start.source_d2l_receipt_hash,
                    start.source_strategy_semantic_hash,
                    start.source_d2j_protocol_id,
                    start.source_d2j_protocol_receipt_hash,
                    start.predictive_d2k_receipt_hash,
                    start.economic_holdout_commitment_fingerprint,
                    start.economic_prediction_artifact_hash,
                    start.evaluator_semantic_hash,
                    str(start.initial_cash),
                    start.started_at,
                    start.start_hash,
                    _canonical_json(start.to_dict()),
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise OSS3EconomicHoldoutAlreadyConsumed(
                "economic one-shot durable identity conflict"
            ) from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _record_terminal(self, receipt: OSS3EconomicEvaluationReceipt) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            start_row = conn.execute(
                "SELECT start_hash FROM oss3_economic_holdout_evaluation_starts "
                "WHERE evaluation_id = ? AND economic_protocol_id = ?",
                (receipt.evaluation_id, receipt.economic_protocol_id),
            ).fetchone()
            if start_row is None or str(start_row["start_hash"]) != receipt.start_hash:
                raise OSS3EconomicHoldoutIntegrityError("D2N terminal lacks exact durable start")
            existing = conn.execute(
                "SELECT receipt_json FROM oss3_economic_holdout_evaluations "
                "WHERE economic_protocol_id = ?",
                (receipt.economic_protocol_id,),
            ).fetchone()
            if existing is not None:
                current = _receipt_from_row(existing)
                if current != receipt:
                    raise OSS3EconomicHoldoutAlreadyConsumed(
                        "economic protocol already has different terminal evidence"
                    )
                conn.execute("COMMIT")
                return
            conn.execute(
                """
                INSERT INTO oss3_economic_holdout_evaluations(
                    evaluation_id, economic_protocol_id, start_hash,
                    decision, receipt_hash, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.evaluation_id,
                    receipt.economic_protocol_id,
                    receipt.start_hash,
                    receipt.decision.value,
                    receipt.receipt_hash,
                    _canonical_json(receipt.to_dict()),
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise OSS3EconomicHoldoutAlreadyConsumed(
                "D2N terminal durable identity conflict"
            ) from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def read_oss3d2n_evaluation_read_only(
    path: str | Path,
    *,
    economic_protocol_id: str,
) -> OSS3EconomicEvaluationReceipt | None:
    _require_id(economic_protocol_id, "economic_protocol_id")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise OSS3EconomicHoldoutIntegrityError("D2N registry does not exist")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        if not _table_exists(conn, "oss3_economic_holdout_evaluations"):
            return None
        row = conn.execute(
            "SELECT * FROM oss3_economic_holdout_evaluations WHERE economic_protocol_id = ?",
            (economic_protocol_id,),
        ).fetchone()
        return _receipt_from_row(row) if row is not None else None
    finally:
        conn.close()


@dataclass(frozen=True, slots=True)
class _SimulationResult:
    allocations: tuple[PredictiveTargetAllocation, ...]
    fills: tuple[OSS3EconomicResearchFill, ...]
    equity_curve: tuple[OSS3EconomicEquityPoint, ...]
    period_returns: tuple[tuple[str, Decimal], ...]
    metrics: OSS3EconomicMetrics

    @property
    def result_hash(self) -> str:
        return _hash(
            {
                "allocation_fingerprints": [item.fingerprint for item in self.allocations],
                "fills": [item.to_dict() for item in self.fills],
                "equity_curve": [item.to_dict() for item in self.equity_curve],
                "period_returns": [[timestamp, str(value)] for timestamp, value in self.period_returns],
                "metrics": self.metrics.to_dict(),
            }
        )


def _simulate(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    universe: AlignedMarketUniverse,
    allocations: tuple[PredictiveTargetAllocation, ...],
) -> _SimulationResult:
    _verify_allocation_support(universe=universe, allocations=allocations)
    cost_policy = economic_protocol.cost_policy
    if cost_policy.execution_price_policy != EXECUTION_PRICE_POLICY:
        raise OSS3EconomicHoldoutGovernanceError("unexpected D2M execution-price policy")
    if cost_policy.volume_policy != VOLUME_POLICY or cost_policy.rounding_policy != ROUNDING_POLICY:
        raise OSS3EconomicHoldoutGovernanceError("unexpected D2M execution constraint policy")
    if cost_policy.allow_short or cost_policy.allow_leverage or cost_policy.allow_margin:
        raise OSS3EconomicHoldoutGovernanceError("D2N accepts long-only unlevered D2M policy")
    model = cost_policy.execution_cost_model

    cash = INITIAL_CASH
    positions = {symbol: _ZERO for symbol in universe.symbols}
    average_cost = {symbol: _ZERO for symbol in universe.symbols}
    fills: list[OSS3EconomicResearchFill] = []
    equity_curve: list[OSS3EconomicEquityPoint] = []
    realized: list[Decimal] = []
    turnover = _ZERO
    total_fees = _ZERO
    max_participation = _ZERO
    tracking_errors: list[Decimal] = []

    for execution_index in range(1, universe.bar_count):
        signal_index = execution_index - 1
        allocation = allocations[signal_index]
        bars = {
            dataset.instrument.symbol: dataset.bars[execution_index]
            for dataset in universe.datasets
        }
        equity_open = cash + sum(
            (positions[symbol] * bars[symbol].open for symbol in universe.symbols),
            _ZERO,
        )
        if equity_open <= _ZERO:
            raise OSS3EconomicHoldoutGovernanceError("non-positive equity before rebalance")
        weights = dict(allocation.target_weights)
        if tuple(sorted(weights)) != universe.symbols:
            raise OSS3EconomicHoldoutIntegrityError("allocation/universe symbol mismatch")
        targets: dict[str, Decimal] = {}
        for dataset in universe.datasets:
            symbol = dataset.instrument.symbol
            raw = weights[symbol] * equity_open / bars[symbol].open
            targets[symbol] = _floor_step(raw, dataset.instrument.quantity_step)

        # Risk reduction first so buys never depend on margin.
        for dataset in universe.datasets:
            symbol = dataset.instrument.symbol
            desired = positions[symbol] - targets[symbol]
            if desired <= _ZERO:
                continue
            trade = _bounded_quantity(
                desired=desired,
                available=positions[symbol],
                volume=bars[symbol].volume,
                step=dataset.instrument.quantity_step,
                max_participation=cost_policy.max_volume_participation,
            )
            if trade <= _ZERO:
                continue
            reference = bars[symbol].open
            if trade * reference < cost_policy.min_trade_notional:
                continue
            execution_price = model.execution_price(side=Side.SELL, reference_price=reference)
            fee = model.fee(quantity=trade, execution_price=execution_price)
            proceeds = trade * execution_price - fee
            realized_pnl = proceeds - trade * average_cost[symbol]
            cash += proceeds
            positions[symbol] -= trade
            if positions[symbol] == _ZERO:
                average_cost[symbol] = _ZERO
            realized.append(realized_pnl)
            participation = trade / bars[symbol].volume
            fill = _fill(
                allocation=allocation,
                symbol=symbol,
                side=Side.SELL,
                quantity=trade,
                reference_price=reference,
                execution_price=execution_price,
                fee=fee,
                signal_index=signal_index,
                execution_index=execution_index,
                occurred_at=bars[symbol].started_at,
                volume_participation=participation,
                realized_pnl=realized_pnl,
            )
            fills.append(fill)
            turnover += trade * execution_price
            total_fees += fee
            max_participation = max(max_participation, participation)

        # Buys use only remaining cash, with entry fee capitalized into avg cost.
        for dataset in universe.datasets:
            symbol = dataset.instrument.symbol
            desired = targets[symbol] - positions[symbol]
            if desired <= _ZERO:
                continue
            volume_cap = _floor_step(
                bars[symbol].volume * cost_policy.max_volume_participation,
                dataset.instrument.quantity_step,
            )
            trade = min(_floor_step(desired, dataset.instrument.quantity_step), volume_cap)
            if trade <= _ZERO:
                continue
            reference = bars[symbol].open
            execution_price = model.execution_price(side=Side.BUY, reference_price=reference)
            unit_fee = model.fee(quantity=_ONE, execution_price=execution_price)
            affordable = _floor_step(
                cash / (execution_price + unit_fee),
                dataset.instrument.quantity_step,
            )
            trade = min(trade, affordable)
            trade = _floor_step(trade, dataset.instrument.quantity_step)
            if trade <= _ZERO or trade * reference < cost_policy.min_trade_notional:
                continue
            fee = model.fee(quantity=trade, execution_price=execution_price)
            spend = trade * execution_price + fee
            if spend > cash:
                continue
            old_quantity = positions[symbol]
            old_basis = old_quantity * average_cost[symbol]
            new_quantity = old_quantity + trade
            average_cost[symbol] = (old_basis + spend) / new_quantity
            positions[symbol] = new_quantity
            cash -= spend
            participation = trade / bars[symbol].volume
            fill = _fill(
                allocation=allocation,
                symbol=symbol,
                side=Side.BUY,
                quantity=trade,
                reference_price=reference,
                execution_price=execution_price,
                fee=fee,
                signal_index=signal_index,
                execution_index=execution_index,
                occurred_at=bars[symbol].started_at,
                volume_participation=participation,
                realized_pnl=_ZERO,
            )
            fills.append(fill)
            turnover += trade * execution_price
            total_fees += fee
            max_participation = max(max_participation, participation)

        point = _mark_equity(universe=universe, index=execution_index, cash=cash, positions=positions)
        equity_curve.append(point)
        target_error = sum(
            (
                abs(
                    positions[symbol] * bars[symbol].close / point.equity
                    - weights[symbol]
                )
                for symbol in universe.symbols
            ),
            _ZERO,
        )
        tracking_errors.append(target_error)

    if not equity_curve:
        raise OSS3EconomicHoldoutGovernanceError("economic evaluation produced no equity points")
    period_returns = _period_returns(INITIAL_CASH, tuple(equity_curve))
    annualization = _SECONDS_PER_365_DAY_YEAR / Decimal(universe.timeframe_seconds)
    metrics = _metrics(
        equity_curve=tuple(equity_curve),
        period_returns=period_returns,
        annualization_factor=annualization,
        realized=tuple(realized),
        turnover=turnover,
        total_fees=total_fees,
        max_participation=max_participation,
        tracking_errors=tuple(tracking_errors),
        fills=len(fills),
        rebalances=len(allocations),
    )
    return _SimulationResult(
        allocations=allocations,
        fills=tuple(fills),
        equity_curve=tuple(equity_curve),
        period_returns=period_returns,
        metrics=metrics,
    )


def _metrics(
    *,
    equity_curve: tuple[OSS3EconomicEquityPoint, ...],
    period_returns: tuple[tuple[str, Decimal], ...],
    annualization_factor: Decimal,
    realized: tuple[Decimal, ...],
    turnover: Decimal,
    total_fees: Decimal,
    max_participation: Decimal,
    tracking_errors: tuple[Decimal, ...],
    fills: int,
    rebalances: int,
) -> OSS3EconomicMetrics:
    returns = [float(value) for _, value in period_returns]
    period_stdev = stdev(returns) if len(returns) >= 2 else 0.0
    mean_return = fmean(returns) if returns else 0.0
    annualization = float(annualization_factor)
    sharpe = mean_return / period_stdev * sqrt(annualization) if period_stdev > 0 else 0.0
    peak = float(INITIAL_CASH)
    max_drawdown = 0.0
    exposure: list[float] = []
    for point in equity_curve:
        equity = float(point.equity)
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
        exposure.append(float(point.gross_exposure / point.equity))
    wins = [float(value) for value in realized if value > _ZERO]
    losses = [float(value) for value in realized if value < _ZERO]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = inf
    else:
        profit_factor = 0.0
    tracking = [float(value) for value in tracking_errors]
    return OSS3EconomicMetrics(
        metrics_version=OSS3D2N_METRICS_VERSION,
        initial_cash=INITIAL_CASH,
        net_return=float(equity_curve[-1].equity / INITIAL_CASH - _ONE),
        annualization_factor=annualization,
        sharpe=sharpe,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        turnover=float(turnover / INITIAL_CASH),
        total_fees=float(total_fees),
        max_volume_participation=float(max_participation),
        average_gross_exposure_ratio=fmean(exposure) if exposure else 0.0,
        max_gross_exposure_ratio=max(exposure, default=0.0),
        average_target_tracking_error=fmean(tracking) if tracking else 0.0,
        max_target_tracking_error=max(tracking, default=0.0),
        fills=fills,
        rebalances=rebalances,
        realized_closing_fills=len(realized),
        realized_win_count=len(wins),
        realized_loss_count=len(losses),
        profit_factor_policy=PROFIT_FACTOR_POLICY,
        terminal_liquidation_policy=NO_TERMINAL_LIQUIDATION_POLICY,
    )


def _build_metric_receipt(
    *,
    start: OSS3EconomicEvaluationStart,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    result: _SimulationResult,
    terminal_at: datetime,
) -> OSS3EconomicEvaluationReceipt:
    policy = economic_protocol.decision_policy
    metrics = result.metrics
    gates = (
        _gate("ECONOMIC_NET_RETURN_POSITIVE", ">", str(policy.min_net_return), metrics.net_return, metrics.net_return > float(policy.min_net_return)),
        _gate("ECONOMIC_SHARPE_MIN", ">=", str(policy.min_sharpe), metrics.sharpe, metrics.sharpe >= float(policy.min_sharpe)),
        _gate("ECONOMIC_PROFIT_FACTOR_MIN", ">=", str(policy.min_profit_factor), metrics.profit_factor, metrics.profit_factor >= float(policy.min_profit_factor)),
        _gate("ECONOMIC_MAX_DRAWDOWN_MAX", "<=", str(policy.max_drawdown), metrics.max_drawdown, metrics.max_drawdown <= float(policy.max_drawdown)),
        _gate("ECONOMIC_FILLS_MIN", ">=", str(policy.min_fills), metrics.fills, metrics.fills >= policy.min_fills),
        _gate("ECONOMIC_REBALANCES_MIN", ">=", str(policy.min_rebalances), metrics.rebalances, metrics.rebalances >= policy.min_rebalances),
    )
    failed = tuple(gate.gate_id for gate in gates if not gate.passed)
    decision = OSS3EconomicDecision.PASS if not failed else OSS3EconomicDecision.FAIL
    values = _receipt_common(
        start=start,
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        result_hash=result.result_hash,
        terminal_at=terminal_at,
    )
    values.update(
        {
            "decision": decision,
            "gates": gates,
            "failed_gate_ids": failed,
            "metrics": metrics,
            "failure_code": "",
            "economic_validation_passed": decision is OSS3EconomicDecision.PASS,
        }
    )
    return OSS3EconomicEvaluationReceipt(
        **values,
        receipt_hash=_hash(_receipt_payload_from_values(values)),
    )


def _build_structural_failure_receipt(
    *,
    start: OSS3EconomicEvaluationStart,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    failure_code: str,
    terminal_at: datetime,
) -> OSS3EconomicEvaluationReceipt:
    result_hash = _hash({"start_hash": start.start_hash, "failure_code": failure_code})
    values = _receipt_common(
        start=start,
        economic_protocol=economic_protocol,
        d2l_receipt=d2l_receipt,
        result_hash=result_hash,
        terminal_at=terminal_at,
    )
    values.update(
        {
            "decision": OSS3EconomicDecision.FAIL,
            "gates": (),
            "failed_gate_ids": (),
            "metrics": None,
            "failure_code": failure_code,
            "economic_validation_passed": False,
        }
    )
    return OSS3EconomicEvaluationReceipt(
        **values,
        receipt_hash=_hash(_receipt_payload_from_values(values)),
    )


def _receipt_common(
    *,
    start: OSS3EconomicEvaluationStart,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    result_hash: str,
    terminal_at: datetime,
) -> dict[str, object]:
    binding = d2l_receipt.binding
    return {
        "receipt_version": OSS3D2N_RECEIPT_VERSION,
        "evaluation_id": start.evaluation_id,
        "economic_protocol_id": start.economic_protocol_id,
        "economic_protocol_receipt_hash": start.economic_protocol_receipt_hash,
        "start_hash": start.start_hash,
        "source_d2l_receipt_hash": start.source_d2l_receipt_hash,
        "source_strategy_id": binding.strategy_id,
        "source_strategy_version": binding.strategy_version,
        "source_strategy_semantic_hash": binding.strategy_semantic_hash,
        "source_d2j_protocol_id": start.source_d2j_protocol_id,
        "source_d2j_protocol_receipt_hash": start.source_d2j_protocol_receipt_hash,
        "predictive_d2k_receipt_hash": start.predictive_d2k_receipt_hash,
        "economic_holdout_commitment_fingerprint": start.economic_holdout_commitment_fingerprint,
        "economic_prediction_artifact_hash": start.economic_prediction_artifact_hash,
        "evaluator_semantic_hash": start.evaluator_semantic_hash,
        "result_hash": result_hash,
        "started_at": start.started_at,
        "terminal_at": terminal_at.astimezone(timezone.utc).isoformat(),
        "predictive_validation_passed": True,
        "economic_holdout_observed": True,
        "economic_holdout_consumed": True,
        "second_attempt_allowed": False,
        "retuning_allowed": False,
        "reselection_allowed": False,
        "profitability_claim_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def _build_start(
    *,
    evaluation_id: str,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
    predictive_receipt_hash: str,
    holdout: ProtectedEconomicHoldout,
    evaluator_hash: str,
    started_at: datetime,
) -> OSS3EconomicEvaluationStart:
    values: dict[str, object] = {
        "start_version": OSS3D2N_START_VERSION,
        "evaluation_id": evaluation_id,
        "economic_protocol_id": economic_protocol.economic_protocol_id,
        "economic_protocol_receipt_hash": economic_protocol.receipt_hash,
        "source_d2l_receipt_hash": d2l_receipt.receipt_hash,
        "source_strategy_semantic_hash": d2l_receipt.binding.strategy_semantic_hash,
        "source_d2j_protocol_id": d2j_protocol.protocol_id,
        "source_d2j_protocol_receipt_hash": d2j_protocol.receipt_hash,
        "predictive_d2k_receipt_hash": predictive_receipt_hash,
        "economic_holdout_commitment_fingerprint": holdout.commitment.fingerprint,
        "economic_prediction_artifact_hash": holdout._material.prediction.artifact_hash,
        "evaluator_semantic_hash": evaluator_hash,
        "initial_cash": INITIAL_CASH,
        "started_at": started_at.astimezone(timezone.utc).isoformat(),
    }
    return OSS3EconomicEvaluationStart(
        **values,
        start_hash=_hash(_start_payload_from_values(values)),
    )


def _verify_protocol_chain(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
) -> None:
    if not isinstance(economic_protocol, OSS3PredictiveEconomicProtocolReceipt):
        raise TypeError("economic_protocol must be OSS3PredictiveEconomicProtocolReceipt")
    if not isinstance(d2l_receipt, OSS3PredictiveStrategyPreregistrationReceipt):
        raise TypeError("d2l_receipt must be OSS3PredictiveStrategyPreregistrationReceipt")
    if not isinstance(d2j_protocol, OSS3FinalHoldoutProtocolReceipt):
        raise TypeError("d2j_protocol must be OSS3FinalHoldoutProtocolReceipt")
    binding = d2l_receipt.binding
    for name, expected, actual in (
        ("D2L receipt", economic_protocol.source_d2l_receipt_hash, d2l_receipt.receipt_hash),
        ("D2L binding", economic_protocol.source_d2l_binding_hash, binding.binding_hash),
        ("strategy id", economic_protocol.source_strategy_id, binding.strategy_id),
        ("strategy version", economic_protocol.source_strategy_version, binding.strategy_version),
        ("strategy semantic hash", economic_protocol.source_strategy_semantic_hash, binding.strategy_semantic_hash),
        ("D2J protocol id", economic_protocol.source_d2j_protocol_id, d2j_protocol.protocol_id),
        ("D2J receipt", economic_protocol.source_d2j_protocol_receipt_hash, d2j_protocol.receipt_hash),
        ("D2L/D2J protocol", d2l_receipt.protocol_id, d2j_protocol.protocol_id),
        ("D2L/D2J receipt", d2l_receipt.protocol_receipt_hash, d2j_protocol.receipt_hash),
    ):
        if expected != actual:
            raise OSS3EconomicHoldoutIntegrityError(f"D2N protocol lineage mismatch: {name}")
    if economic_protocol.predictive_final_holdout_observed or economic_protocol.economic_holdout_observed:
        raise OSS3EconomicHoldoutGovernanceError("D2N requires pristine D2M protocol receipt")
    if economic_protocol.cost_policy.allow_short or economic_protocol.cost_policy.allow_leverage or economic_protocol.cost_policy.allow_margin:
        raise OSS3EconomicHoldoutGovernanceError("D2N requires long-only unlevered D2M policy")
    if economic_protocol.decision_policy.max_evaluations != 1:
        raise OSS3EconomicHoldoutGovernanceError("D2N requires one-shot D2M policy")


def _verify_temporal_separation(
    *,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
    economic_commitment: EconomicHoldoutCommitment,
) -> None:
    predictive_end = _parse_canonical_utc(
        d2j_protocol.holdout_commitment.partition_end,
        "predictive holdout end",
    )
    economic_start = _parse_canonical_utc(
        economic_commitment.partition_start,
        "economic holdout start",
    )
    if economic_start <= predictive_end:
        raise OSS3EconomicHoldoutGovernanceError(
            "economic holdout must start strictly after predictive FINAL_HOLDOUT"
        )


def _require_d2k_pass(*, path: Path, d2j_protocol: OSS3FinalHoldoutProtocolReceipt):
    receipt = read_oss3d2k_evaluation_read_only(path, protocol_id=d2j_protocol.protocol_id)
    if receipt is None:
        raise OSS3EconomicHoldoutGovernanceError("D2N requires terminal D2K evidence")
    if receipt.protocol_receipt_hash != d2j_protocol.receipt_hash:
        raise OSS3EconomicHoldoutIntegrityError("D2K/D2J receipt mismatch")
    if receipt.decision is not OSS3FinalHoldoutDecision.PASS or receipt.predictive_validation_passed is not True:
        raise OSS3EconomicHoldoutGovernanceError("D2N requires predictive D2K PASS")
    return receipt


def _require_exact_d2m_durable_state(
    *,
    conn: sqlite3.Connection,
    start: OSS3EconomicEvaluationStart,
) -> None:
    if not _table_exists(conn, "oss3_predictive_economic_protocols"):
        raise OSS3EconomicHoldoutGovernanceError("D2N requires durable D2M protocol")
    row = conn.execute(
        "SELECT receipt_hash, source_d2l_receipt_hash, source_strategy_semantic_hash, "
        "source_d2j_protocol_id FROM oss3_predictive_economic_protocols "
        "WHERE economic_protocol_id = ?",
        (start.economic_protocol_id,),
    ).fetchone()
    if row is None:
        raise OSS3EconomicHoldoutGovernanceError("D2M protocol not found in shared SQLite")
    expected = (
        start.economic_protocol_receipt_hash,
        start.source_d2l_receipt_hash,
        start.source_strategy_semantic_hash,
        start.source_d2j_protocol_id,
    )
    actual = tuple(str(row[name]) for name in (
        "receipt_hash",
        "source_d2l_receipt_hash",
        "source_strategy_semantic_hash",
        "source_d2j_protocol_id",
    ))
    if actual != expected:
        raise OSS3EconomicHoldoutIntegrityError("durable D2M state differs from D2N start")


def _require_exact_d2k_pass_durable(
    *,
    conn: sqlite3.Connection,
    start: OSS3EconomicEvaluationStart,
) -> None:
    if not _table_exists(conn, "oss3_final_holdout_evaluations"):
        raise OSS3EconomicHoldoutGovernanceError("D2N requires durable D2K terminal table")
    row = conn.execute(
        "SELECT decision, receipt_hash FROM oss3_final_holdout_evaluations "
        "WHERE protocol_id = ?",
        (start.source_d2j_protocol_id,),
    ).fetchone()
    if row is None or str(row["decision"]) != OSS3FinalHoldoutDecision.PASS.value:
        raise OSS3EconomicHoldoutGovernanceError("durable D2K terminal decision is not PASS")
    if str(row["receipt_hash"]) != start.predictive_d2k_receipt_hash:
        raise OSS3EconomicHoldoutIntegrityError("durable D2K receipt differs from D2N start")


def _prove_durable_start(
    *,
    registry_path: str | Path,
    start: OSS3EconomicEvaluationStart,
) -> None:
    resolved = Path(registry_path).resolve()
    if not resolved.is_file():
        raise OSS3EconomicHoldoutIntegrityError("D2N registry path does not exist")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        if not _table_exists(conn, "oss3_economic_holdout_evaluation_starts"):
            raise OSS3EconomicHoldoutIntegrityError("D2N durable start table missing")
        row = conn.execute(
            "SELECT start_json FROM oss3_economic_holdout_evaluation_starts "
            "WHERE evaluation_id = ? AND economic_protocol_id = ?",
            (start.evaluation_id, start.economic_protocol_id),
        ).fetchone()
        if row is None or str(row["start_json"]) != _canonical_json(start.to_dict()):
            raise OSS3EconomicHoldoutIntegrityError("D2N exact durable start not proven")
    finally:
        conn.close()


def _verify_prediction_support(
    *,
    universe: AlignedMarketUniverse,
    prediction: QlibPredictionArtifact,
) -> None:
    expected_times = tuple(
        bar.ended_at.astimezone(timezone.utc).isoformat()
        for bar in universe.datasets[0].bars[:-1]
    )
    rows_by_time: dict[str, list[str]] = {}
    for row in prediction.rows:
        rows_by_time.setdefault(row.timestamp, []).append(row.symbol)
    if tuple(sorted(rows_by_time)) != expected_times:
        raise OSS3EconomicHoldoutGovernanceError(
            "economic predictions must cover every nonterminal bar close exactly"
        )
    for timestamp in expected_times:
        if tuple(sorted(rows_by_time[timestamp])) != universe.symbols:
            raise OSS3EconomicHoldoutIntegrityError(
                "economic prediction cross-section differs from exact universe"
            )


def _verify_allocation_support(
    *,
    universe: AlignedMarketUniverse,
    allocations: tuple[PredictiveTargetAllocation, ...],
) -> None:
    if len(allocations) != universe.bar_count - 1:
        raise OSS3EconomicHoldoutIntegrityError("D2N allocation count mismatch")
    expected_times = tuple(
        bar.ended_at.astimezone(timezone.utc).isoformat()
        for bar in universe.datasets[0].bars[:-1]
    )
    if tuple(item.as_of for item in allocations) != expected_times:
        raise OSS3EconomicHoldoutIntegrityError("D2N allocations do not align to signal bars")
    for allocation in allocations:
        if allocation.execution_delay_bars != 1 or allocation.same_bar_execution_allowed:
            raise OSS3EconomicHoldoutGovernanceError("D2N requires next-bar D2L allocations")
        if tuple(symbol for symbol, _ in allocation.target_weights) != universe.symbols:
            raise OSS3EconomicHoldoutIntegrityError("D2L allocation symbols differ from universe")


def _dataset_set_hash(universe: AlignedMarketUniverse) -> str:
    return _hash(
        [
            [dataset.instrument.symbol, dataset.dataset_hash]
            for dataset in universe.datasets
        ]
    )


def _bounded_quantity(
    *,
    desired: Decimal,
    available: Decimal,
    volume: Decimal,
    step: Decimal,
    max_participation: Decimal,
) -> Decimal:
    if desired <= _ZERO or available <= _ZERO or volume <= _ZERO:
        return _ZERO
    volume_cap = _floor_step(volume * max_participation, step)
    return _floor_step(min(desired, available, volume_cap), step)


def _floor_step(quantity: Decimal, step: Decimal) -> Decimal:
    if quantity <= _ZERO:
        return _ZERO
    return (quantity // step) * step


def _fill(
    *,
    allocation: PredictiveTargetAllocation,
    symbol: str,
    side: Side,
    quantity: Decimal,
    reference_price: Decimal,
    execution_price: Decimal,
    fee: Decimal,
    signal_index: int,
    execution_index: int,
    occurred_at: datetime,
    volume_participation: Decimal,
    realized_pnl: Decimal,
) -> OSS3EconomicResearchFill:
    fill_id = (
        f"oss3d2n:{allocation.fingerprint[:16]}:{symbol}:"
        f"{side.value}:{execution_index}"
    )
    return OSS3EconomicResearchFill(
        fill_version=OSS3D2N_FILL_VERSION,
        fill_id=fill_id,
        allocation_fingerprint=allocation.fingerprint,
        symbol=symbol,
        side=side.value,
        quantity=quantity,
        reference_price=reference_price,
        execution_price=execution_price,
        fee=fee,
        signal_index=signal_index,
        execution_index=execution_index,
        occurred_at=occurred_at.astimezone(timezone.utc).isoformat(),
        volume_participation=volume_participation,
        realized_pnl=realized_pnl,
    )


def _mark_equity(
    *,
    universe: AlignedMarketUniverse,
    index: int,
    cash: Decimal,
    positions: dict[str, Decimal],
) -> OSS3EconomicEquityPoint:
    equity = cash
    gross = _ZERO
    for dataset in universe.datasets:
        symbol = dataset.instrument.symbol
        value = positions[symbol] * dataset.bars[index].close
        equity += value
        gross += value
    if equity <= _ZERO or cash < _ZERO:
        raise OSS3EconomicHoldoutGovernanceError("invalid long-only equity state")
    return OSS3EconomicEquityPoint(
        point_version=OSS3D2N_EQUITY_VERSION,
        occurred_at=universe.datasets[0].bars[index].ended_at.astimezone(timezone.utc).isoformat(),
        cash=cash,
        positions=tuple((symbol, positions[symbol]) for symbol in universe.symbols),
        equity=equity,
        gross_exposure=gross,
    )


def _period_returns(
    initial_cash: Decimal,
    points: tuple[OSS3EconomicEquityPoint, ...],
) -> tuple[tuple[str, Decimal], ...]:
    previous = initial_cash
    result: list[tuple[str, Decimal]] = []
    for point in points:
        value = point.equity / previous - _ONE
        result.append((point.occurred_at, value))
        previous = point.equity
    return tuple(result)


def _gate(
    gate_id: str,
    operator: str,
    threshold: str,
    observed: object,
    passed: bool,
) -> OSS3EconomicGateEvidence:
    if isinstance(observed, float) and isinf(observed):
        observed_text = "inf"
    else:
        observed_text = str(observed)
    return OSS3EconomicGateEvidence(
        gate_id=gate_id,
        operator=operator,
        threshold=threshold,
        observed=observed_text,
        passed=bool(passed),
    )


def _start_payload_from_values(values: Mapping[str, object]) -> dict[str, object]:
    payload = dict(values)
    if isinstance(payload.get("initial_cash"), Decimal):
        payload["initial_cash"] = str(payload["initial_cash"])
    return payload


def _receipt_payload_from_values(values: Mapping[str, object]) -> dict[str, object]:
    payload = dict(values)
    decision = payload.get("decision")
    if isinstance(decision, OSS3EconomicDecision):
        payload["decision"] = decision.value
    gates = payload.get("gates")
    if isinstance(gates, tuple):
        payload["gates"] = [item.to_dict() for item in gates]
    failed = payload.get("failed_gate_ids")
    if isinstance(failed, tuple):
        payload["failed_gate_ids"] = list(failed)
    metrics = payload.get("metrics")
    if isinstance(metrics, OSS3EconomicMetrics):
        payload["metrics"] = metrics.to_dict()
    return payload


def _receipt_from_row(row: sqlite3.Row) -> OSS3EconomicEvaluationReceipt:
    try:
        payload = json.loads(str(row["receipt_json"]))
        if not isinstance(payload, Mapping):
            raise TypeError("receipt_json must be object")
        values = dict(payload)
        values["decision"] = OSS3EconomicDecision(str(values["decision"]))
        gates_raw = values.get("gates")
        if not isinstance(gates_raw, list):
            raise TypeError("gates must be list")
        values["gates"] = tuple(OSS3EconomicGateEvidence(**dict(item)) for item in gates_raw)
        failed = values.get("failed_gate_ids")
        if not isinstance(failed, list):
            raise TypeError("failed_gate_ids must be list")
        values["failed_gate_ids"] = tuple(str(item) for item in failed)
        metrics_raw = values.get("metrics")
        if metrics_raw is not None:
            if not isinstance(metrics_raw, Mapping):
                raise TypeError("metrics must be object")
            metrics_values = dict(metrics_raw)
            metrics_values["initial_cash"] = Decimal(str(metrics_values["initial_cash"]))
            if metrics_values.get("profit_factor") == "inf":
                metrics_values["profit_factor"] = inf
            values["metrics"] = OSS3EconomicMetrics(**metrics_values)
        receipt = OSS3EconomicEvaluationReceipt(**values)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise OSS3EconomicHoldoutIntegrityError("invalid durable D2N receipt") from exc
    if str(row["evaluation_id"]) != receipt.evaluation_id:
        raise OSS3EconomicHoldoutIntegrityError("D2N durable evaluation_id mismatch")
    if str(row["economic_protocol_id"]) != receipt.economic_protocol_id:
        raise OSS3EconomicHoldoutIntegrityError("D2N durable protocol mismatch")
    if str(row["start_hash"]) != receipt.start_hash:
        raise OSS3EconomicHoldoutIntegrityError("D2N durable start hash mismatch")
    if str(row["decision"]) != receipt.decision.value:
        raise OSS3EconomicHoldoutIntegrityError("D2N durable decision mismatch")
    if str(row["receipt_hash"]) != receipt.receipt_hash:
        raise OSS3EconomicHoldoutIntegrityError("D2N durable receipt hash mismatch")
    if str(row["receipt_json"]) != _canonical_json(receipt.to_dict()):
        raise OSS3EconomicHoldoutIntegrityError("D2N durable serialization drifted")
    return receipt


def economic_evaluator_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    payload: list[dict[str, str]] = []
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise OSS3EconomicHoldoutIntegrityError(f"missing D2N semantic file: {relative}")
        payload.append({"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()})
    return _hash(payload)


def _reject_broker_credentials() -> None:
    present = sorted(
        name for name, value in os.environ.items()
        if value and any(name.startswith(prefix) for prefix in _BROKER_CREDENTIAL_PREFIXES)
    )
    if present:
        raise OSS3EconomicHoldoutGovernanceError(
            "D2N refuses broker/exchange credentials in research evaluator"
        )


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _parse_canonical_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be UTC")
    if value != parsed.astimezone(timezone.utc).isoformat():
        raise ValueError(f"{name} must use canonical UTC serialization")
    return parsed


def _require_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_decimal(value: object, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be finite Decimal")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


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
