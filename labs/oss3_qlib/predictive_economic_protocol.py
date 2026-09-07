"""OSS-3D2M preregistered cost-aware economic qualification protocol.

D2M freezes the economic interpretation of the D2L predictive strategy before
any FINAL_HOLDOUT checkout.  It does not read market bars, labels, predictions
or economic outcomes.  It records only value-opaque dataset identity plus the
cost/execution/decision policies that a later one-shot economic evaluator must
use.

Scientific ordering for a fresh campaign:

    D2I winner -> D2J protected FINAL_HOLDOUT protocol
        -> D2L predictor-to-strategy preregistration
            -> D2M economic protocol preregistration
                -> later D2K predictive holdout evaluation
                -> later D2N economic holdout evaluation

D2M must be committed into the same authoritative SQLite file used by the D2L
ordering guard and future D2K/D2N starts.  Registration fails closed if the D2L
receipt is absent or if a D2K start/holdout permit already exists.

The canonical v1 cost model is deliberately conservative relative to a zero-
cost backtest (10 bps fee + 5 bps half-spread + 5 bps slippage).  It is a
research assumption, not broker truth.  Later W81/W82 continuity must prove
that live/PAPER product economics are no worse than the frozen research model;
otherwise promotion remains blocked.

No broker, OMS, Safety, OrderIntent, network, subprocess, PAPER, capital or LIVE
authority exists in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping

from autotrade.research.costs import ExecutionCostModel

from .final_holdout_protocol import OSS3FinalHoldoutProtocolReceipt
from .predictive_strategy_contract import (
    OSS3PredictiveStrategyPreregistrationReceipt,
    SHARED_SQLITE_ORDERING_CONTRACT,
)


OSS3D2M_COST_POLICY_VERSION = "OSS3D2M_ECONOMIC_COST_POLICY_V1"
OSS3D2M_DECISION_POLICY_VERSION = "OSS3D2M_ECONOMIC_DECISION_POLICY_V1"
OSS3D2M_HOLDOUT_COMMITMENT_VERSION = "OSS3D2M_ECONOMIC_HOLDOUT_COMMITMENT_V1"
OSS3D2M_PROTOCOL_VERSION = "OSS3D2M_PREREGISTERED_ECONOMIC_PROTOCOL_V1"
OSS3D2M_ORDERING_CONTRACT = "OSS3D2M_D2L_D2K_SHARED_SQLITE_ORDERING_V1"

ANNUALIZATION_POLICY = "SECONDS_PER_365_DAY_YEAR_DIV_TIMEFRAME_V1"
EXECUTION_PRICE_POLICY = "NEXT_BAR_OPEN_PLUS_FROZEN_COST_MODEL_V1"
VOLUME_POLICY = "BAR_VOLUME_PARTICIPATION_CAP_V1"
ROUNDING_POLICY = "INSTRUMENT_QUANTITY_STEP_FLOOR_V1"
CASH_POLICY = "NO_MARGIN_LONG_ONLY_CASH_BOUNDED_V1"
ECONOMIC_HOLDOUT_PURPOSE = "ONE_SHOT_COST_AWARE_OOS_ECONOMIC_QUALIFICATION"

MIN_ECONOMIC_HOLDOUT_BARS = 60
MIN_ECONOMIC_HOLDOUT_SYMBOLS = 3

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,31}$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")
_ZERO = Decimal("0")
_ONE = Decimal("1")

SEMANTIC_FILES = (
    "labs/oss3_qlib/predictive_economic_protocol.py",
    "labs/oss3_qlib/predictive_strategy_contract.py",
    "labs/oss3_qlib/final_holdout_protocol.py",
    "src/autotrade/research/costs.py",
)


class PredictiveEconomicProtocolError(RuntimeError):
    """Base OSS-3D2M failure."""


class PredictiveEconomicProtocolIntegrityError(PredictiveEconomicProtocolError):
    """Frozen lineage, policy or durable identity drifted."""


class PredictiveEconomicProtocolGovernanceError(PredictiveEconomicProtocolError):
    """Operation violates pre-holdout research-only governance."""


class PredictiveEconomicProtocolConflict(PredictiveEconomicProtocolError):
    """Append-only durable state conflicts with the requested protocol."""


@dataclass(frozen=True, slots=True)
class EconomicCostPolicy:
    policy_version: str
    policy_id: str
    fee_bps: Decimal
    half_spread_bps: Decimal
    slippage_bps: Decimal
    execution_price_policy: str
    volume_policy: str
    rounding_policy: str
    cash_policy: str
    max_volume_participation: Decimal
    min_trade_notional: Decimal
    allow_zero_total_costs: bool
    allow_short: bool
    allow_leverage: bool
    allow_margin: bool
    requires_w81_w82_continuity: bool
    broker_authoritative_costs_claimed: bool

    def __post_init__(self) -> None:
        if self.policy_version != OSS3D2M_COST_POLICY_VERSION:
            raise PredictiveEconomicProtocolIntegrityError(
                "noncanonical D2M cost policy version"
            )
        _require_id(self.policy_id, "policy_id")
        for name, value in (
            ("fee_bps", self.fee_bps),
            ("half_spread_bps", self.half_spread_bps),
            ("slippage_bps", self.slippage_bps),
            ("max_volume_participation", self.max_volume_participation),
            ("min_trade_notional", self.min_trade_notional),
        ):
            _require_decimal(value, name)
            if value < _ZERO:
                raise ValueError(f"{name} must be >= 0")
        if self.total_cost_bps <= _ZERO:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M forbids zero-cost economic qualification"
            )
        if self.allow_zero_total_costs:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M canonical policy may not opt into zero costs"
            )
        if not _ZERO < self.max_volume_participation <= _ONE:
            raise ValueError("max_volume_participation must be in (0,1]")
        if self.min_trade_notional <= _ZERO:
            raise ValueError("min_trade_notional must be > 0")
        if self.execution_price_policy != EXECUTION_PRICE_POLICY:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M execution-price policy drifted"
            )
        if self.volume_policy != VOLUME_POLICY:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M volume policy drifted"
            )
        if self.rounding_policy != ROUNDING_POLICY:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M quantity rounding policy drifted"
            )
        if self.cash_policy != CASH_POLICY:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M cash policy drifted"
            )
        if self.allow_short or self.allow_leverage or self.allow_margin:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M v1 is long-only, no-margin and unlevered"
            )
        if self.requires_w81_w82_continuity is not True:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M cannot replace later W81/W82 cost continuity"
            )
        if self.broker_authoritative_costs_claimed:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M research costs are not broker-authoritative"
            )
        # Reuse the canonical cost-model validation rather than duplicating it.
        _ = self.execution_cost_model

    @property
    def total_cost_bps(self) -> Decimal:
        return self.fee_bps + self.half_spread_bps + self.slippage_bps

    @property
    def execution_cost_model(self) -> ExecutionCostModel:
        return ExecutionCostModel(
            fee_bps=self.fee_bps,
            half_spread_bps=self.half_spread_bps,
            slippage_bps=self.slippage_bps,
            allow_zero_total_costs=False,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "policy_id": self.policy_id,
            "fee_bps": str(self.fee_bps),
            "half_spread_bps": str(self.half_spread_bps),
            "slippage_bps": str(self.slippage_bps),
            "total_cost_bps": str(self.total_cost_bps),
            "execution_price_policy": self.execution_price_policy,
            "volume_policy": self.volume_policy,
            "rounding_policy": self.rounding_policy,
            "cash_policy": self.cash_policy,
            "max_volume_participation": str(self.max_volume_participation),
            "min_trade_notional": str(self.min_trade_notional),
            "allow_zero_total_costs": self.allow_zero_total_costs,
            "allow_short": self.allow_short,
            "allow_leverage": self.allow_leverage,
            "allow_margin": self.allow_margin,
            "requires_w81_w82_continuity": self.requires_w81_w82_continuity,
            "broker_authoritative_costs_claimed": self.broker_authoritative_costs_claimed,
        }


def canonical_oss3d2m_cost_policy() -> EconomicCostPolicy:
    """Return the single canonical research cost policy for D2M v1."""
    return EconomicCostPolicy(
        policy_version=OSS3D2M_COST_POLICY_VERSION,
        policy_id="oss3d2m-conservative-research-costs-v1",
        fee_bps=Decimal("10"),
        half_spread_bps=Decimal("5"),
        slippage_bps=Decimal("5"),
        execution_price_policy=EXECUTION_PRICE_POLICY,
        volume_policy=VOLUME_POLICY,
        rounding_policy=ROUNDING_POLICY,
        cash_policy=CASH_POLICY,
        max_volume_participation=Decimal("0.10"),
        min_trade_notional=Decimal("10"),
        allow_zero_total_costs=False,
        allow_short=False,
        allow_leverage=False,
        allow_margin=False,
        requires_w81_w82_continuity=True,
        broker_authoritative_costs_claimed=False,
    )


@dataclass(frozen=True, slots=True)
class EconomicDecisionPolicy:
    policy_version: str
    policy_id: str
    annualization_policy: str
    min_net_return: Decimal
    min_sharpe: Decimal
    min_profit_factor: Decimal
    max_drawdown: Decimal
    min_fills: int
    min_rebalances: int
    require_positive_net_return: bool
    require_all_gates: bool
    max_evaluations: int
    retuning_allowed: bool
    reselection_allowed: bool
    fallback_policy_allowed: bool
    second_attempt_allowed: bool
    failure_is_terminal: bool

    def __post_init__(self) -> None:
        if self.policy_version != OSS3D2M_DECISION_POLICY_VERSION:
            raise PredictiveEconomicProtocolIntegrityError(
                "noncanonical D2M decision policy version"
            )
        _require_id(self.policy_id, "policy_id")
        if self.annualization_policy != ANNUALIZATION_POLICY:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M annualization policy drifted"
            )
        for name, value in (
            ("min_net_return", self.min_net_return),
            ("min_sharpe", self.min_sharpe),
            ("min_profit_factor", self.min_profit_factor),
            ("max_drawdown", self.max_drawdown),
        ):
            _require_decimal(value, name)
        if self.min_net_return != Decimal("0"):
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M net-return floor is frozen at > 0"
            )
        if self.min_sharpe != Decimal("1.5"):
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M Sharpe floor is frozen at 1.5"
            )
        if self.min_profit_factor != Decimal("1.3"):
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M profit-factor floor is frozen at 1.3"
            )
        if self.max_drawdown != Decimal("0.15"):
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M max-drawdown ceiling is frozen at 0.15"
            )
        for name, value, minimum in (
            ("min_fills", self.min_fills, 1),
            ("min_rebalances", self.min_rebalances, 1),
            ("max_evaluations", self.max_evaluations, 1),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be integer >= {minimum}")
        if self.min_fills != 10 or self.min_rebalances != 10:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M activity floors are frozen at 10 fills / 10 rebalances"
            )
        if self.require_positive_net_return is not True or self.require_all_gates is not True:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M requires positive net return and all gates"
            )
        if self.max_evaluations != 1:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M economic holdout is one-shot"
            )
        if (
            self.retuning_allowed
            or self.reselection_allowed
            or self.fallback_policy_allowed
            or self.second_attempt_allowed
        ):
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M forbids retuning, reselection, fallback and second attempts"
            )
        if self.failure_is_terminal is not True:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M economic failure must be terminal"
            )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "policy_id": self.policy_id,
            "annualization_policy": self.annualization_policy,
            "min_net_return": str(self.min_net_return),
            "min_sharpe": str(self.min_sharpe),
            "min_profit_factor": str(self.min_profit_factor),
            "max_drawdown": str(self.max_drawdown),
            "min_fills": self.min_fills,
            "min_rebalances": self.min_rebalances,
            "require_positive_net_return": self.require_positive_net_return,
            "require_all_gates": self.require_all_gates,
            "max_evaluations": self.max_evaluations,
            "retuning_allowed": self.retuning_allowed,
            "reselection_allowed": self.reselection_allowed,
            "fallback_policy_allowed": self.fallback_policy_allowed,
            "second_attempt_allowed": self.second_attempt_allowed,
            "failure_is_terminal": self.failure_is_terminal,
        }


def canonical_oss3d2m_decision_policy() -> EconomicDecisionPolicy:
    return EconomicDecisionPolicy(
        policy_version=OSS3D2M_DECISION_POLICY_VERSION,
        policy_id="oss3d2m-economic-qualification-gates-v1",
        annualization_policy=ANNUALIZATION_POLICY,
        min_net_return=Decimal("0"),
        min_sharpe=Decimal("1.5"),
        min_profit_factor=Decimal("1.3"),
        max_drawdown=Decimal("0.15"),
        min_fills=10,
        min_rebalances=10,
        require_positive_net_return=True,
        require_all_gates=True,
        max_evaluations=1,
        retuning_allowed=False,
        reselection_allowed=False,
        fallback_policy_allowed=False,
        second_attempt_allowed=False,
        failure_is_terminal=True,
    )


@dataclass(frozen=True, slots=True)
class EconomicHoldoutCommitment:
    commitment_version: str
    commitment_id: str
    purpose: str
    universe_hash: str
    universe_name: str
    source_dataset_set_hash: str
    symbols: tuple[str, ...]
    quote_currency: str
    timeframe_seconds: int
    partition_start: str
    partition_end: str
    bar_count: int
    symbol_count: int
    market_values_exposed: bool
    economic_outcomes_observed: bool

    def __post_init__(self) -> None:
        if self.commitment_version != OSS3D2M_HOLDOUT_COMMITMENT_VERSION:
            raise PredictiveEconomicProtocolIntegrityError(
                "noncanonical D2M economic holdout commitment version"
            )
        _require_id(self.commitment_id, "commitment_id")
        if self.purpose != ECONOMIC_HOLDOUT_PURPOSE:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M economic holdout purpose drifted"
            )
        _require_hash(self.universe_hash, "universe_hash")
        _require_hash(self.source_dataset_set_hash, "source_dataset_set_hash")
        if not isinstance(self.universe_name, str) or not self.universe_name.strip():
            raise ValueError("universe_name is required")
        if self.symbols != tuple(sorted(self.symbols)):
            raise PredictiveEconomicProtocolIntegrityError(
                "economic holdout symbols must use canonical sorted order"
            )
        if len(set(self.symbols)) != len(self.symbols):
            raise PredictiveEconomicProtocolIntegrityError(
                "economic holdout symbols must be unique"
            )
        if len(self.symbols) < MIN_ECONOMIC_HOLDOUT_SYMBOLS:
            raise PredictiveEconomicProtocolGovernanceError(
                "economic holdout requires at least three symbols"
            )
        if any(not _SYMBOL_RE.fullmatch(symbol) for symbol in self.symbols):
            raise ValueError("invalid economic holdout symbol")
        if not _CURRENCY_RE.fullmatch(self.quote_currency):
            raise ValueError("invalid quote_currency")
        if (
            isinstance(self.timeframe_seconds, bool)
            or not isinstance(self.timeframe_seconds, int)
            or self.timeframe_seconds <= 0
        ):
            raise ValueError("timeframe_seconds must be positive integer")
        start = _parse_canonical_utc(self.partition_start, "partition_start")
        end = _parse_canonical_utc(self.partition_end, "partition_end")
        if not start < end:
            raise ValueError("economic holdout window must be positive")
        if (
            isinstance(self.bar_count, bool)
            or not isinstance(self.bar_count, int)
            or self.bar_count < MIN_ECONOMIC_HOLDOUT_BARS
        ):
            raise PredictiveEconomicProtocolGovernanceError(
                "economic holdout bar_count below preregistered floor"
            )
        if self.symbol_count != len(self.symbols):
            raise PredictiveEconomicProtocolIntegrityError(
                "symbol_count differs from symbols"
            )
        if self.market_values_exposed or self.economic_outcomes_observed:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M commitment must remain value-opaque and unobserved"
            )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "commitment_version": self.commitment_version,
            "commitment_id": self.commitment_id,
            "purpose": self.purpose,
            "universe_hash": self.universe_hash,
            "universe_name": self.universe_name,
            "source_dataset_set_hash": self.source_dataset_set_hash,
            "symbols": list(self.symbols),
            "quote_currency": self.quote_currency,
            "timeframe_seconds": self.timeframe_seconds,
            "partition_start": self.partition_start,
            "partition_end": self.partition_end,
            "bar_count": self.bar_count,
            "symbol_count": self.symbol_count,
            "market_values_exposed": self.market_values_exposed,
            "economic_outcomes_observed": self.economic_outcomes_observed,
        }


@dataclass(frozen=True, slots=True)
class OSS3PredictiveEconomicProtocolReceipt:
    protocol_version: str
    economic_protocol_id: str
    ordering_contract: str
    source_d2l_receipt_hash: str
    source_d2l_binding_hash: str
    source_strategy_id: str
    source_strategy_version: str
    source_strategy_semantic_hash: str
    source_d2j_protocol_id: str
    source_d2j_protocol_receipt_hash: str
    expected_holdout_authorization_id: str
    cost_policy: EconomicCostPolicy
    decision_policy: EconomicDecisionPolicy
    economic_holdout_commitment: EconomicHoldoutCommitment
    protocol_code_hash: str
    registered_at: str
    d2l_preregistration_proven: bool
    d2k_start_absent_at_commit: bool
    d2k_permit_absent_at_commit: bool
    predictive_final_holdout_observed: bool
    economic_holdout_observed: bool
    economic_holdout_consumed: bool
    profitability_claim_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if self.protocol_version != OSS3D2M_PROTOCOL_VERSION:
            raise PredictiveEconomicProtocolIntegrityError(
                "noncanonical D2M protocol version"
            )
        _require_id(self.economic_protocol_id, "economic_protocol_id")
        _require_id(self.source_strategy_id, "source_strategy_id")
        _require_id(self.source_strategy_version, "source_strategy_version")
        _require_id(self.source_d2j_protocol_id, "source_d2j_protocol_id")
        _require_id(
            self.expected_holdout_authorization_id,
            "expected_holdout_authorization_id",
        )
        if self.ordering_contract != OSS3D2M_ORDERING_CONTRACT:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M ordering contract drifted"
            )
        for name in (
            "source_d2l_receipt_hash",
            "source_d2l_binding_hash",
            "source_strategy_semantic_hash",
            "source_d2j_protocol_receipt_hash",
            "protocol_code_hash",
            "receipt_hash",
        ):
            _require_hash(getattr(self, name), name)
        if not isinstance(self.cost_policy, EconomicCostPolicy):
            raise TypeError("cost_policy must be EconomicCostPolicy")
        if not isinstance(self.decision_policy, EconomicDecisionPolicy):
            raise TypeError("decision_policy must be EconomicDecisionPolicy")
        if not isinstance(self.economic_holdout_commitment, EconomicHoldoutCommitment):
            raise TypeError("economic_holdout_commitment must be EconomicHoldoutCommitment")
        _parse_canonical_utc(self.registered_at, "registered_at")
        if self.d2l_preregistration_proven is not True:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M requires durable D2L preregistration proof"
            )
        if self.d2k_start_absent_at_commit is not True or self.d2k_permit_absent_at_commit is not True:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M must commit before D2K start and permit consumption"
            )
        if (
            self.predictive_final_holdout_observed
            or self.economic_holdout_observed
            or self.economic_holdout_consumed
            or self.profitability_claim_authorized
            or self.promotion_authorized
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M protocol exceeds pre-holdout research authority"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M cannot grant capital or LIVE authority"
            )
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise PredictiveEconomicProtocolIntegrityError(
                "D2M protocol receipt hash mismatch"
            )

    @property
    def cost_policy_fingerprint(self) -> str:
        return self.cost_policy.fingerprint

    @property
    def decision_policy_fingerprint(self) -> str:
        return self.decision_policy.fingerprint

    @property
    def economic_holdout_commitment_fingerprint(self) -> str:
        return self.economic_holdout_commitment.fingerprint

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol_version": self.protocol_version,
            "economic_protocol_id": self.economic_protocol_id,
            "ordering_contract": self.ordering_contract,
            "source_d2l_receipt_hash": self.source_d2l_receipt_hash,
            "source_d2l_binding_hash": self.source_d2l_binding_hash,
            "source_strategy_id": self.source_strategy_id,
            "source_strategy_version": self.source_strategy_version,
            "source_strategy_semantic_hash": self.source_strategy_semantic_hash,
            "source_d2j_protocol_id": self.source_d2j_protocol_id,
            "source_d2j_protocol_receipt_hash": self.source_d2j_protocol_receipt_hash,
            "expected_holdout_authorization_id": self.expected_holdout_authorization_id,
            "cost_policy": self.cost_policy.to_dict(),
            "cost_policy_fingerprint": self.cost_policy.fingerprint,
            "decision_policy": self.decision_policy.to_dict(),
            "decision_policy_fingerprint": self.decision_policy.fingerprint,
            "economic_holdout_commitment": self.economic_holdout_commitment.to_dict(),
            "economic_holdout_commitment_fingerprint": self.economic_holdout_commitment.fingerprint,
            "protocol_code_hash": self.protocol_code_hash,
            "registered_at": self.registered_at,
            "d2l_preregistration_proven": self.d2l_preregistration_proven,
            "d2k_start_absent_at_commit": self.d2k_start_absent_at_commit,
            "d2k_permit_absent_at_commit": self.d2k_permit_absent_at_commit,
            "predictive_final_holdout_observed": self.predictive_final_holdout_observed,
            "economic_holdout_observed": self.economic_holdout_observed,
            "economic_holdout_consumed": self.economic_holdout_consumed,
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


class SQLiteOSS3PredictiveEconomicProtocolRegistry:
    """Append-only D2M registry ordered before D2K on one SQLite file."""

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
                CREATE TABLE IF NOT EXISTS oss3_predictive_economic_protocols (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    economic_protocol_id TEXT NOT NULL UNIQUE,
                    source_d2l_receipt_hash TEXT NOT NULL UNIQUE,
                    source_d2l_binding_hash TEXT NOT NULL UNIQUE,
                    source_strategy_semantic_hash TEXT NOT NULL UNIQUE,
                    source_d2j_protocol_id TEXT NOT NULL UNIQUE,
                    source_d2j_protocol_receipt_hash TEXT NOT NULL UNIQUE,
                    expected_holdout_authorization_id TEXT NOT NULL UNIQUE,
                    cost_policy_hash TEXT NOT NULL,
                    decision_policy_hash TEXT NOT NULL,
                    economic_holdout_commitment_hash TEXT NOT NULL UNIQUE,
                    protocol_code_hash TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS oss3_predictive_economic_protocols_no_update
                BEFORE UPDATE ON oss3_predictive_economic_protocols
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2M registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_predictive_economic_protocols_no_delete
                BEFORE DELETE ON oss3_predictive_economic_protocols
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2M registry is append-only');
                END;
                """
            )
            conn.commit()
        finally:
            conn.close()

    def preregister(
        self,
        *,
        economic_protocol_id: str,
        d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
        d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
        economic_holdout_commitment: EconomicHoldoutCommitment,
        now: datetime,
        cost_policy: EconomicCostPolicy | None = None,
        decision_policy: EconomicDecisionPolicy | None = None,
    ) -> OSS3PredictiveEconomicProtocolReceipt:
        _require_id(economic_protocol_id, "economic_protocol_id")
        _require_aware(now, "now")
        if not isinstance(d2l_receipt, OSS3PredictiveStrategyPreregistrationReceipt):
            raise TypeError("d2l_receipt must be OSS3PredictiveStrategyPreregistrationReceipt")
        if not isinstance(d2j_protocol, OSS3FinalHoldoutProtocolReceipt):
            raise TypeError("d2j_protocol must be OSS3FinalHoldoutProtocolReceipt")
        if not isinstance(economic_holdout_commitment, EconomicHoldoutCommitment):
            raise TypeError("economic_holdout_commitment must be EconomicHoldoutCommitment")
        selected_cost = canonical_oss3d2m_cost_policy() if cost_policy is None else cost_policy
        selected_decision = (
            canonical_oss3d2m_decision_policy() if decision_policy is None else decision_policy
        )
        if not isinstance(selected_cost, EconomicCostPolicy):
            raise TypeError("cost_policy must be EconomicCostPolicy")
        if not isinstance(selected_decision, EconomicDecisionPolicy):
            raise TypeError("decision_policy must be EconomicDecisionPolicy")
        _verify_d2l_d2j_binding(d2l_receipt=d2l_receipt, d2j_protocol=d2j_protocol)

        code_hash = predictive_economic_protocol_code_hash()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _require_exact_d2l_durable_state(conn=conn, d2l_receipt=d2l_receipt)
            _require_no_d2k_state(conn=conn, d2j_protocol=d2j_protocol)
            candidate = _build_protocol_receipt(
                economic_protocol_id=economic_protocol_id,
                d2l_receipt=d2l_receipt,
                d2j_protocol=d2j_protocol,
                cost_policy=selected_cost,
                decision_policy=selected_decision,
                economic_holdout_commitment=economic_holdout_commitment,
                code_hash=code_hash,
                registered_at=now,
            )
            existing = conn.execute(
                "SELECT * FROM oss3_predictive_economic_protocols WHERE economic_protocol_id = ?",
                (economic_protocol_id,),
            ).fetchone()
            if existing is not None:
                current = _receipt_from_row(existing)
                if current != candidate:
                    raise PredictiveEconomicProtocolConflict(
                        "economic_protocol_id is already bound to another D2M receipt"
                    )
                conn.execute("COMMIT")
                return current

            existing_source = conn.execute(
                "SELECT * FROM oss3_predictive_economic_protocols WHERE source_d2l_receipt_hash = ?",
                (d2l_receipt.receipt_hash,),
            ).fetchone()
            if existing_source is not None:
                current = _receipt_from_row(existing_source)
                if current != candidate:
                    raise PredictiveEconomicProtocolConflict(
                        "D2L strategy already has another D2M economic protocol"
                    )
                conn.execute("COMMIT")
                return current

            conn.execute(
                """
                INSERT INTO oss3_predictive_economic_protocols(
                    economic_protocol_id,
                    source_d2l_receipt_hash,
                    source_d2l_binding_hash,
                    source_strategy_semantic_hash,
                    source_d2j_protocol_id,
                    source_d2j_protocol_receipt_hash,
                    expected_holdout_authorization_id,
                    cost_policy_hash,
                    decision_policy_hash,
                    economic_holdout_commitment_hash,
                    protocol_code_hash,
                    registered_at,
                    receipt_hash,
                    receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.economic_protocol_id,
                    candidate.source_d2l_receipt_hash,
                    candidate.source_d2l_binding_hash,
                    candidate.source_strategy_semantic_hash,
                    candidate.source_d2j_protocol_id,
                    candidate.source_d2j_protocol_receipt_hash,
                    candidate.expected_holdout_authorization_id,
                    candidate.cost_policy.fingerprint,
                    candidate.decision_policy.fingerprint,
                    candidate.economic_holdout_commitment.fingerprint,
                    candidate.protocol_code_hash,
                    candidate.registered_at,
                    candidate.receipt_hash,
                    _canonical_json(candidate.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return candidate
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise PredictiveEconomicProtocolConflict(
                "D2M durable identity conflict"
            ) from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_for_strategy(
        self,
        strategy_semantic_hash: str,
    ) -> OSS3PredictiveEconomicProtocolReceipt | None:
        _require_hash(strategy_semantic_hash, "strategy_semantic_hash")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM oss3_predictive_economic_protocols WHERE source_strategy_semantic_hash = ?",
                (strategy_semantic_hash,),
            ).fetchone()
            return _receipt_from_row(row) if row is not None else None
        finally:
            conn.close()


def read_oss3d2m_protocol_read_only(
    path: str | Path,
    *,
    economic_protocol_id: str,
) -> OSS3PredictiveEconomicProtocolReceipt | None:
    _require_id(economic_protocol_id, "economic_protocol_id")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise PredictiveEconomicProtocolIntegrityError(
            "D2M durable registry does not exist"
        )
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        if not _table_exists(conn, "oss3_predictive_economic_protocols"):
            return None
        row = conn.execute(
            "SELECT * FROM oss3_predictive_economic_protocols WHERE economic_protocol_id = ?",
            (economic_protocol_id,),
        ).fetchone()
        return _receipt_from_row(row) if row is not None else None
    finally:
        conn.close()


def predictive_economic_protocol_code_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    payload: list[dict[str, str]] = []
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise PredictiveEconomicProtocolIntegrityError(
                f"missing D2M semantic file: {relative}"
            )
        payload.append(
            {"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()}
        )
    return _hash(payload)


def _verify_d2l_d2j_binding(
    *,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
) -> None:
    binding = d2l_receipt.binding
    for name, expected, actual in (
        ("D2J protocol id", d2j_protocol.protocol_id, d2l_receipt.protocol_id),
        ("D2J receipt hash", d2j_protocol.receipt_hash, d2l_receipt.protocol_receipt_hash),
        (
            "holdout authorization id",
            d2j_protocol.expected_holdout_authorization_id,
            d2l_receipt.expected_holdout_authorization_id,
        ),
        ("binding protocol id", d2j_protocol.protocol_id, binding.protocol_id),
        ("binding protocol receipt", d2j_protocol.receipt_hash, binding.protocol_receipt_hash),
        ("winner model config", d2j_protocol.model_config_hash, binding.model_config_hash),
    ):
        if expected != actual:
            raise PredictiveEconomicProtocolIntegrityError(
                f"D2M D2L/D2J lineage mismatch: {name}"
            )
    if d2l_receipt.ordering_contract != SHARED_SQLITE_ORDERING_CONTRACT:
        raise PredictiveEconomicProtocolIntegrityError(
            "D2M requires canonical D2L ordering contract"
        )
    if not d2l_receipt.shared_sqlite_ordering_enforced or not d2l_receipt.d2k_start_absent_at_commit:
        raise PredictiveEconomicProtocolGovernanceError(
            "D2L preregistration did not prove pre-D2K ordering"
        )
    if (
        d2l_receipt.final_holdout_observed
        or d2l_receipt.final_holdout_consumed
        or binding.final_holdout_observed
        or binding.final_holdout_consumed
    ):
        raise PredictiveEconomicProtocolGovernanceError(
            "D2M requires pristine pre-holdout D2L evidence"
        )
    if (
        d2j_protocol.final_holdout_observed
        or d2j_protocol.final_holdout_consumed
        or d2j_protocol.holdout_permit_issued
        or d2j_protocol.holdout_permit_consumed
        or d2j_protocol.final_holdout_checkout_authorized
    ):
        raise PredictiveEconomicProtocolGovernanceError(
            "D2M must be preregistered before D2K consumes D2J"
        )


def _require_exact_d2l_durable_state(
    *,
    conn: sqlite3.Connection,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
) -> None:
    if not _table_exists(conn, "oss3_predictive_strategy_preregistrations"):
        raise PredictiveEconomicProtocolGovernanceError(
            "D2M requires D2L preregistration in the same SQLite file"
        )
    row = conn.execute(
        "SELECT receipt_hash, binding_hash, strategy_semantic_hash, receipt_json "
        "FROM oss3_predictive_strategy_preregistrations WHERE protocol_id = ?",
        (d2l_receipt.protocol_id,),
    ).fetchone()
    if row is None:
        raise PredictiveEconomicProtocolGovernanceError(
            "D2M cannot find durable D2L preregistration in shared SQLite"
        )
    expected = (
        d2l_receipt.receipt_hash,
        d2l_receipt.binding.binding_hash,
        d2l_receipt.binding.strategy_semantic_hash,
        _canonical_json(d2l_receipt.to_dict()),
    )
    actual = (
        str(row["receipt_hash"]),
        str(row["binding_hash"]),
        str(row["strategy_semantic_hash"]),
        str(row["receipt_json"]),
    )
    if actual != expected:
        raise PredictiveEconomicProtocolIntegrityError(
            "shared SQLite D2L state differs from supplied receipt"
        )


def _require_no_d2k_state(
    *,
    conn: sqlite3.Connection,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
) -> None:
    if _table_exists(conn, "oss3_final_holdout_evaluation_starts"):
        row = conn.execute(
            "SELECT evaluation_id FROM oss3_final_holdout_evaluation_starts "
            "WHERE protocol_id = ? OR holdout_authorization_id = ? LIMIT 1",
            (
                d2j_protocol.protocol_id,
                d2j_protocol.expected_holdout_authorization_id,
            ),
        ).fetchone()
        if row is not None:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M economic assumptions cannot be frozen after D2K start"
            )
    if _table_exists(conn, "holdout_permits"):
        row = conn.execute(
            "SELECT permit_id FROM holdout_permits WHERE permit_id = ? LIMIT 1",
            (d2j_protocol.expected_holdout_authorization_id,),
        ).fetchone()
        if row is not None:
            raise PredictiveEconomicProtocolGovernanceError(
                "D2M economic assumptions cannot be frozen after D2K permit consumption"
            )


def _build_protocol_receipt(
    *,
    economic_protocol_id: str,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
    cost_policy: EconomicCostPolicy,
    decision_policy: EconomicDecisionPolicy,
    economic_holdout_commitment: EconomicHoldoutCommitment,
    code_hash: str,
    registered_at: datetime,
) -> OSS3PredictiveEconomicProtocolReceipt:
    binding = d2l_receipt.binding
    values: dict[str, object] = {
        "protocol_version": OSS3D2M_PROTOCOL_VERSION,
        "economic_protocol_id": economic_protocol_id,
        "ordering_contract": OSS3D2M_ORDERING_CONTRACT,
        "source_d2l_receipt_hash": d2l_receipt.receipt_hash,
        "source_d2l_binding_hash": binding.binding_hash,
        "source_strategy_id": binding.strategy_id,
        "source_strategy_version": binding.strategy_version,
        "source_strategy_semantic_hash": binding.strategy_semantic_hash,
        "source_d2j_protocol_id": d2j_protocol.protocol_id,
        "source_d2j_protocol_receipt_hash": d2j_protocol.receipt_hash,
        "expected_holdout_authorization_id": d2j_protocol.expected_holdout_authorization_id,
        "cost_policy": cost_policy,
        "decision_policy": decision_policy,
        "economic_holdout_commitment": economic_holdout_commitment,
        "protocol_code_hash": code_hash,
        "registered_at": registered_at.astimezone(timezone.utc).isoformat(),
        "d2l_preregistration_proven": True,
        "d2k_start_absent_at_commit": True,
        "d2k_permit_absent_at_commit": True,
        "predictive_final_holdout_observed": False,
        "economic_holdout_observed": False,
        "economic_holdout_consumed": False,
        "profitability_claim_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS3PredictiveEconomicProtocolReceipt(
        **values,
        receipt_hash=_hash(_protocol_payload_from_values(values)),
    )


def _receipt_from_row(row: sqlite3.Row) -> OSS3PredictiveEconomicProtocolReceipt:
    try:
        payload = json.loads(str(row["receipt_json"]))
        if not isinstance(payload, Mapping):
            raise TypeError("receipt_json must be object")
        values = dict(payload)
        values.pop("cost_policy_fingerprint", None)
        values.pop("decision_policy_fingerprint", None)
        values.pop("economic_holdout_commitment_fingerprint", None)
        cost_raw = values.get("cost_policy")
        decision_raw = values.get("decision_policy")
        commitment_raw = values.get("economic_holdout_commitment")
        if not isinstance(cost_raw, Mapping):
            raise TypeError("cost_policy must be object")
        if not isinstance(decision_raw, Mapping):
            raise TypeError("decision_policy must be object")
        if not isinstance(commitment_raw, Mapping):
            raise TypeError("economic_holdout_commitment must be object")
        values["cost_policy"] = _cost_policy_from_dict(cost_raw)
        values["decision_policy"] = _decision_policy_from_dict(decision_raw)
        values["economic_holdout_commitment"] = _commitment_from_dict(commitment_raw)
        receipt = OSS3PredictiveEconomicProtocolReceipt(**values)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise PredictiveEconomicProtocolIntegrityError(
            "invalid durable D2M receipt"
        ) from exc
    for column, expected in (
        ("economic_protocol_id", receipt.economic_protocol_id),
        ("source_d2l_receipt_hash", receipt.source_d2l_receipt_hash),
        ("source_d2l_binding_hash", receipt.source_d2l_binding_hash),
        ("source_strategy_semantic_hash", receipt.source_strategy_semantic_hash),
        ("source_d2j_protocol_id", receipt.source_d2j_protocol_id),
        ("source_d2j_protocol_receipt_hash", receipt.source_d2j_protocol_receipt_hash),
        ("expected_holdout_authorization_id", receipt.expected_holdout_authorization_id),
        ("cost_policy_hash", receipt.cost_policy.fingerprint),
        ("decision_policy_hash", receipt.decision_policy.fingerprint),
        (
            "economic_holdout_commitment_hash",
            receipt.economic_holdout_commitment.fingerprint,
        ),
        ("protocol_code_hash", receipt.protocol_code_hash),
        ("registered_at", receipt.registered_at),
        ("receipt_hash", receipt.receipt_hash),
    ):
        if str(row[column]) != expected:
            raise PredictiveEconomicProtocolIntegrityError(
                f"D2M durable column mismatch: {column}"
            )
    if _canonical_json(receipt.to_dict()) != str(row["receipt_json"]):
        raise PredictiveEconomicProtocolIntegrityError(
            "D2M durable serialization drifted"
        )
    return receipt


def _cost_policy_from_dict(payload: Mapping[str, object]) -> EconomicCostPolicy:
    values = dict(payload)
    values.pop("total_cost_bps", None)
    for name in (
        "fee_bps",
        "half_spread_bps",
        "slippage_bps",
        "max_volume_participation",
        "min_trade_notional",
    ):
        values[name] = Decimal(str(values[name]))
    return EconomicCostPolicy(**values)


def _decision_policy_from_dict(payload: Mapping[str, object]) -> EconomicDecisionPolicy:
    values = dict(payload)
    for name in ("min_net_return", "min_sharpe", "min_profit_factor", "max_drawdown"):
        values[name] = Decimal(str(values[name]))
    return EconomicDecisionPolicy(**values)


def _commitment_from_dict(payload: Mapping[str, object]) -> EconomicHoldoutCommitment:
    values = dict(payload)
    raw_symbols = values.get("symbols")
    if not isinstance(raw_symbols, list):
        raise TypeError("symbols must be list")
    values["symbols"] = tuple(str(item) for item in raw_symbols)
    return EconomicHoldoutCommitment(**values)


def _protocol_payload_from_values(values: Mapping[str, object]) -> dict[str, object]:
    payload = dict(values)
    cost = payload.get("cost_policy")
    decision = payload.get("decision_policy")
    commitment = payload.get("economic_holdout_commitment")
    if isinstance(cost, EconomicCostPolicy):
        payload["cost_policy"] = cost.to_dict()
        payload["cost_policy_fingerprint"] = cost.fingerprint
    if isinstance(decision, EconomicDecisionPolicy):
        payload["decision_policy"] = decision.to_dict()
        payload["decision_policy_fingerprint"] = decision.fingerprint
    if isinstance(commitment, EconomicHoldoutCommitment):
        payload["economic_holdout_commitment"] = commitment.to_dict()
        payload["economic_holdout_commitment_fingerprint"] = commitment.fingerprint
    return payload


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


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
