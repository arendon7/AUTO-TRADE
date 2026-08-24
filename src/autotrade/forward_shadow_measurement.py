from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import inspect
import json
from pathlib import Path
import re
import sys

import autotrade.promotion_strategy_version_binding as w83_resolution_module
import autotrade.strategy_execution_binding as w83_binding_module
from autotrade.domain import Side
from autotrade.promotion_strategy_version_binding import (
    PromotionStrategyVersionResolution,
    build_safe_dsl_runtime_identity,
)
from autotrade.research.backtest import BacktestConfig, BacktestEngine
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.dsl import StrategySpec
from autotrade.research.market import MarketDataset
from autotrade.research.shadow import ShadowPeriodRecord, StrategyShadowObservation
from autotrade.strategy_execution_binding import ExecutionStrategyBindingEvidence


FORWARD_MEASUREMENT_PLAN_VERSION = "W84_FORWARD_MEASUREMENT_PLAN_V2"
FORWARD_MEASUREMENT_RUNTIME_VERSION = "W84_FORWARD_MEASUREMENT_RUNTIME_V1"
FORWARD_MEASUREMENT_RECEIPT_VERSION = "W84_FORWARD_MEASUREMENT_RECEIPT_V1"
GENESIS_MEASUREMENT_HASH = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_PYTHON_RUNTIME_RE = re.compile(r"^[A-Za-z0-9._-]+-[0-9]+\.[0-9]+\.[0-9]+$")


class ForwardShadowMeasurementIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ForwardMeasurementRuntimeIdentity:
    version: str
    python_runtime: str
    w83_runtime_hash: str
    backtest_source_hash: str
    costs_source_hash: str
    domain_source_hash: str
    identity_hash: str

    def __post_init__(self) -> None:
        if self.version != FORWARD_MEASUREMENT_RUNTIME_VERSION:
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement runtime version is not canonical W84"
            )
        if not isinstance(self.python_runtime, str) or not _PYTHON_RUNTIME_RE.fullmatch(
            self.python_runtime
        ):
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement runtime must include exact Python patch version"
            )
        for label, value in (
            ("w83_runtime_hash", self.w83_runtime_hash),
            ("backtest_source_hash", self.backtest_source_hash),
            ("costs_source_hash", self.costs_source_hash),
            ("domain_source_hash", self.domain_source_hash),
            ("identity_hash", self.identity_hash),
        ):
            _require_hash(value, label)
        if self.identity_hash != _hash(_runtime_payload(self, include_hash=False)):
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement runtime identity hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _runtime_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class ForwardMeasurementPlan:
    """Pre-outcome commitment for exact candidate forward measurement semantics.

    `history_dataset_hash` binds only market data already closed at `planned_at`.
    Data between `planned_at` and `forward_activated_at` is processed later only
    to establish deterministic strategy state; it is never qualification return.
    """

    plan_id: str
    contract_version: str
    w83_resolution_id: str
    w83_resolution_hash: str
    w83_binding_hash: str
    selected_strategy_id: str
    selected_strategy_version: str
    strategy_spec_hash: str
    w83_runtime_hash: str
    measurement_runtime_version: str
    measurement_runtime_hash: str
    runtime_python: str
    backtest_source_hash: str
    costs_source_hash: str
    domain_source_hash: str
    backtest_config_hash: str
    initial_cash: Decimal
    history_dataset_hash: str
    dataset_source: str
    dataset_symbol: str
    dataset_venue: str
    dataset_quote_currency: str
    timeframe_seconds: int
    history_bars: int
    planned_at: datetime
    forward_activated_at: datetime
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    plan_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("plan_id", self.plan_id),
            ("w83_resolution_id", self.w83_resolution_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
            ("dataset_symbol", self.dataset_symbol),
            ("dataset_venue", self.dataset_venue),
            ("dataset_quote_currency", self.dataset_quote_currency),
        ):
            _require_id(value, label)
        if self.contract_version != FORWARD_MEASUREMENT_PLAN_VERSION:
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement plan version is not canonical W84"
            )
        if self.measurement_runtime_version != FORWARD_MEASUREMENT_RUNTIME_VERSION:
            raise ForwardShadowMeasurementIntegrityError(
                "measurement runtime version is not canonical W84"
            )
        for label, value in (
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("w83_binding_hash", self.w83_binding_hash),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("w83_runtime_hash", self.w83_runtime_hash),
            ("measurement_runtime_hash", self.measurement_runtime_hash),
            ("backtest_source_hash", self.backtest_source_hash),
            ("costs_source_hash", self.costs_source_hash),
            ("domain_source_hash", self.domain_source_hash),
            ("backtest_config_hash", self.backtest_config_hash),
            ("history_dataset_hash", self.history_dataset_hash),
            ("plan_hash", self.plan_hash),
        ):
            _require_hash(value, label)
        if not _PYTHON_RUNTIME_RE.fullmatch(self.runtime_python):
            raise ForwardShadowMeasurementIntegrityError(
                "runtime_python must include exact Python patch version"
            )
        _require_positive_decimal(self.initial_cash, "initial_cash")
        if not isinstance(self.dataset_source, str) or not self.dataset_source.strip():
            raise ForwardShadowMeasurementIntegrityError("dataset_source is required")
        _require_positive_int(self.timeframe_seconds, "timeframe_seconds")
        _require_positive_int(self.history_bars, "history_bars")
        _require_aware(self.planned_at, "planned_at")
        _require_aware(self.forward_activated_at, "forward_activated_at")
        if _utc(self.planned_at) >= _utc(self.forward_activated_at):
            raise ForwardShadowMeasurementIntegrityError(
                "measurement plan must be frozen before forward activation"
            )
        _require_no_authority(
            paper=self.paper_candidate_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
            label="W84 measurement plan",
        )
        runtime = ForwardMeasurementRuntimeIdentity(
            version=self.measurement_runtime_version,
            python_runtime=self.runtime_python,
            w83_runtime_hash=self.w83_runtime_hash,
            backtest_source_hash=self.backtest_source_hash,
            costs_source_hash=self.costs_source_hash,
            domain_source_hash=self.domain_source_hash,
            identity_hash=self.measurement_runtime_hash,
        )
        if runtime.w83_runtime_hash != self.w83_runtime_hash:
            raise ForwardShadowMeasurementIntegrityError(
                "measurement runtime no longer binds W83 runtime"
            )
        if self.plan_hash != _hash(_plan_payload(self, include_hash=False)):
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement plan hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _plan_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class ForwardShadowMeasurementReceipt:
    contract_version: str
    plan_id: str
    plan_hash: str
    policy_hash: str
    ordinal: int
    selected_strategy_id: str
    selected_strategy_version: str
    strategy_spec_hash: str
    w83_runtime_hash: str
    measurement_runtime_hash: str
    backtest_config_hash: str
    dataset_source: str
    prefix_dataset_hash: str
    prefix_result_hash: str
    period_started_at: datetime
    period_ended_at: datetime
    equity_before: Decimal
    equity_after: Decimal
    return_fraction: Decimal
    previous_measurement_hash: str
    measurement_hash: str
    captured_at: datetime
    receipt_hash: str
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.contract_version != FORWARD_MEASUREMENT_RECEIPT_VERSION:
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement receipt version is not canonical W84"
            )
        for label, value in (
            ("plan_id", self.plan_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            _require_id(value, label)
        for label, value in (
            ("plan_hash", self.plan_hash),
            ("policy_hash", self.policy_hash),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("w83_runtime_hash", self.w83_runtime_hash),
            ("measurement_runtime_hash", self.measurement_runtime_hash),
            ("backtest_config_hash", self.backtest_config_hash),
            ("prefix_dataset_hash", self.prefix_dataset_hash),
            ("prefix_result_hash", self.prefix_result_hash),
            ("previous_measurement_hash", self.previous_measurement_hash),
            ("measurement_hash", self.measurement_hash),
            ("receipt_hash", self.receipt_hash),
        ):
            _require_hash(value, label)
        _require_positive_int(self.ordinal, "ordinal")
        if not isinstance(self.dataset_source, str) or not self.dataset_source.strip():
            raise ForwardShadowMeasurementIntegrityError("dataset_source is required")
        _require_aware(self.period_started_at, "period_started_at")
        _require_aware(self.period_ended_at, "period_ended_at")
        _require_aware(self.captured_at, "captured_at")
        if _utc(self.period_started_at) >= _utc(self.period_ended_at):
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement period must have positive duration"
            )
        if _utc(self.captured_at) < _utc(self.period_ended_at):
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement cannot be captured before period end"
            )
        _require_positive_decimal(self.equity_before, "equity_before")
        _require_positive_decimal(self.equity_after, "equity_after")
        _require_finite_decimal(self.return_fraction, "return_fraction")
        if self.return_fraction <= Decimal("-1"):
            raise ForwardShadowMeasurementIntegrityError(
                "return_fraction must be greater than -1"
            )
        expected_return = self.equity_after / self.equity_before - Decimal("1")
        if self.return_fraction != expected_return:
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement return is not reproducible from equity"
            )
        _require_no_authority(
            paper=self.paper_candidate_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
            label="W84 measurement receipt",
        )
        if self.measurement_hash != _hash(
            _measurement_payload(self, include_capture=False)
        ):
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement hash mismatch"
            )
        if self.receipt_hash != _hash(_measurement_payload(self, include_capture=True)):
            raise ForwardShadowMeasurementIntegrityError(
                "forward measurement receipt hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _measurement_payload(self, include_capture=True, include_receipt_hash=True)

    def to_shadow_observation(self) -> StrategyShadowObservation:
        return StrategyShadowObservation(
            strategy_id=self.selected_strategy_id,
            period_started_at=self.period_started_at,
            period_ended_at=self.period_ended_at,
            return_fraction=self.return_fraction,
            source_fingerprint=self.measurement_hash,
        )


def build_forward_measurement_runtime_identity() -> ForwardMeasurementRuntimeIdentity:
    """Bind all local code that determines W84 forward measurement semantics."""

    w83_runtime = build_safe_dsl_runtime_identity()
    values = {
        "version": FORWARD_MEASUREMENT_RUNTIME_VERSION,
        "python_runtime": (
            f"{sys.implementation.name}-"
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "w83_runtime_hash": w83_runtime.identity_hash,
        "backtest_source_hash": _source_sha256(
            BacktestEngine, "autotrade/research/backtest.py"
        ),
        "costs_source_hash": _source_sha256(
            ExecutionCostModel, "autotrade/research/costs.py"
        ),
        "domain_source_hash": _source_sha256(Side, "autotrade/domain.py"),
    }
    return ForwardMeasurementRuntimeIdentity(
        **values,
        identity_hash=_hash(_runtime_payload_from_values(values)),
    )


def build_forward_measurement_plan(
    *,
    plan_id: str,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
    strategy_spec: StrategySpec,
    backtest_config: BacktestConfig,
    history_dataset: MarketDataset,
    planned_at: datetime,
    forward_activated_at: datetime,
) -> ForwardMeasurementPlan:
    """Freeze exact candidate/config/history before any qualification outcome exists."""

    _require_id(plan_id, "plan_id")
    _validate_w83_pair(w83_resolution=w83_resolution, binding_evidence=binding_evidence)
    if not isinstance(strategy_spec, StrategySpec):
        raise TypeError("strategy_spec must be StrategySpec")
    if not isinstance(backtest_config, BacktestConfig):
        raise TypeError("backtest_config must be BacktestConfig")
    if not isinstance(history_dataset, MarketDataset):
        raise TypeError("history_dataset must be MarketDataset")
    _require_aware(planned_at, "planned_at")
    _require_aware(forward_activated_at, "forward_activated_at")

    if (
        strategy_spec.strategy_id != w83_resolution.selected_strategy_id
        or strategy_spec.strategy_version != w83_resolution.selected_strategy_version
        or strategy_spec.canonical_hash != w83_resolution.strategy_spec_hash
    ):
        raise ForwardShadowMeasurementIntegrityError(
            "measurement StrategySpec does not match exact W83 candidate artifact"
        )
    _validate_dataset_identity(
        dataset=history_dataset,
        binding_evidence=binding_evidence,
        expected_source=None,
        expected_timeframe=None,
    )
    if history_dataset.gap_indexes():
        raise ForwardShadowMeasurementIntegrityError(
            "history dataset must be strictly contiguous"
        )
    if _utc(history_dataset.ended_at) != _utc(planned_at):
        raise ForwardShadowMeasurementIntegrityError(
            "history dataset must end exactly at measurement plan freeze"
        )
    if _utc(planned_at) >= _utc(forward_activated_at):
        raise ForwardShadowMeasurementIntegrityError(
            "measurement plan freeze must strictly predate forward activation"
        )
    delta_seconds = int((_utc(forward_activated_at) - _utc(planned_at)).total_seconds())
    if delta_seconds % history_dataset.timeframe_seconds:
        raise ForwardShadowMeasurementIntegrityError(
            "forward activation must align to frozen market timeframe"
        )
    long_window = int(strategy_spec.parameters["long_window"])
    if len(history_dataset.bars) < long_window + 1:
        raise ForwardShadowMeasurementIntegrityError(
            "history dataset is insufficient for exact StrategySpec lookback"
        )

    runtime = build_forward_measurement_runtime_identity()
    if runtime.w83_runtime_hash != w83_resolution.loaded_runtime_code_hash:
        raise ForwardShadowMeasurementIntegrityError(
            "loaded research runtime differs from certified W83 runtime"
        )
    values = {
        "plan_id": plan_id,
        "contract_version": FORWARD_MEASUREMENT_PLAN_VERSION,
        "w83_resolution_id": w83_resolution.resolution_id,
        "w83_resolution_hash": w83_resolution.resolution_hash,
        "w83_binding_hash": binding_evidence.evidence_hash,
        "selected_strategy_id": w83_resolution.selected_strategy_id,
        "selected_strategy_version": w83_resolution.selected_strategy_version,
        "strategy_spec_hash": strategy_spec.canonical_hash,
        "w83_runtime_hash": w83_resolution.loaded_runtime_code_hash,
        "measurement_runtime_version": runtime.version,
        "measurement_runtime_hash": runtime.identity_hash,
        "runtime_python": runtime.python_runtime,
        "backtest_source_hash": runtime.backtest_source_hash,
        "costs_source_hash": runtime.costs_source_hash,
        "domain_source_hash": runtime.domain_source_hash,
        "backtest_config_hash": backtest_config.config_hash,
        "initial_cash": backtest_config.initial_cash,
        "history_dataset_hash": history_dataset.dataset_hash,
        "dataset_source": history_dataset.source,
        "dataset_symbol": history_dataset.instrument.symbol,
        "dataset_venue": history_dataset.instrument.venue,
        "dataset_quote_currency": history_dataset.instrument.quote_currency,
        "timeframe_seconds": history_dataset.timeframe_seconds,
        "history_bars": len(history_dataset.bars),
        "planned_at": planned_at,
        "forward_activated_at": forward_activated_at,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return ForwardMeasurementPlan(
        **values,
        plan_hash=_hash(_plan_payload_from_values(values)),
    )


def build_forward_shadow_measurements(
    *,
    plan: ForwardMeasurementPlan,
    policy_hash: str,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
    strategy_spec: StrategySpec,
    backtest_config: BacktestConfig,
    history_dataset: MarketDataset,
    post_freeze_dataset: MarketDataset,
    captured_at: datetime,
) -> tuple[ForwardShadowMeasurementReceipt, ...]:
    """Recompute one candidate return receipt per post-activation market bar.

    Every receipt is generated from a dataset prefix ending at that exact period,
    so its stable `measurement_hash` cannot depend on any later forward bar.
    """

    _validate_plan(plan)
    _require_hash(policy_hash, "policy_hash")
    _validate_w83_pair(w83_resolution=w83_resolution, binding_evidence=binding_evidence)
    _validate_plan_inputs(
        plan=plan,
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
        strategy_spec=strategy_spec,
        backtest_config=backtest_config,
        history_dataset=history_dataset,
    )
    if not isinstance(post_freeze_dataset, MarketDataset):
        raise TypeError("post_freeze_dataset must be MarketDataset")
    _require_aware(captured_at, "captured_at")
    _validate_dataset_identity(
        dataset=post_freeze_dataset,
        binding_evidence=binding_evidence,
        expected_source=plan.dataset_source,
        expected_timeframe=plan.timeframe_seconds,
    )
    if post_freeze_dataset.gap_indexes():
        raise ForwardShadowMeasurementIntegrityError(
            "post-freeze measurement dataset must be strictly contiguous"
        )
    if _utc(post_freeze_dataset.started_at) != _utc(plan.planned_at):
        raise ForwardShadowMeasurementIntegrityError(
            "post-freeze dataset must start exactly at measurement plan freeze"
        )
    if history_dataset.bars[-1].ended_at != post_freeze_dataset.bars[0].started_at:
        raise ForwardShadowMeasurementIntegrityError(
            "history and post-freeze datasets must be exactly contiguous"
        )
    if _utc(captured_at) < _utc(post_freeze_dataset.ended_at):
        raise ForwardShadowMeasurementIntegrityError(
            "forward measurements cannot be captured before dataset end"
        )

    qualification_indexes = tuple(
        index
        for index, bar in enumerate(post_freeze_dataset.bars)
        if _utc(bar.started_at) >= _utc(plan.forward_activated_at)
    )
    if qualification_indexes:
        first_qualification = post_freeze_dataset.bars[qualification_indexes[0]]
        if _utc(first_qualification.started_at) != _utc(plan.forward_activated_at):
            raise ForwardShadowMeasurementIntegrityError(
                "first qualification bar must start exactly at forward activation"
            )
    elif _utc(post_freeze_dataset.ended_at) > _utc(plan.forward_activated_at):
        raise ForwardShadowMeasurementIntegrityError(
            "post-freeze dataset crossed activation without qualification bar"
        )

    previous_measurement_hash = GENESIS_MEASUREMENT_HASH
    receipts: list[ForwardShadowMeasurementReceipt] = []
    history_count = len(history_dataset.bars)
    for ordinal, post_index in enumerate(qualification_indexes, start=1):
        period_bar = post_freeze_dataset.bars[post_index]
        prefix_dataset = MarketDataset(
            instrument=post_freeze_dataset.instrument,
            bars=history_dataset.bars + post_freeze_dataset.bars[: post_index + 1],
            source=plan.dataset_source,
        )
        result = BacktestEngine().run(
            dataset=prefix_dataset,
            strategy=strategy_spec.build(),
            config=backtest_config,
        )
        if (
            result.dataset_hash != prefix_dataset.dataset_hash
            or result.strategy_id != plan.selected_strategy_id
            or result.strategy_version != plan.selected_strategy_version
            or result.config_hash != plan.backtest_config_hash
        ):
            raise ForwardShadowMeasurementIntegrityError(
                "backtest result identity differs from frozen measurement plan"
            )
        current_global_index = history_count + post_index
        previous_point = result.equity_curve[current_global_index - 1]
        current_point = result.equity_curve[current_global_index]
        if previous_point.occurred_at != period_bar.started_at:
            raise ForwardShadowMeasurementIntegrityError(
                "measurement baseline does not align with forward period start"
            )
        if current_point.occurred_at != period_bar.ended_at:
            raise ForwardShadowMeasurementIntegrityError(
                "measurement endpoint does not align with forward period end"
            )
        equity_before = previous_point.equity
        equity_after = current_point.equity
        _require_positive_decimal(equity_before, "equity_before")
        _require_positive_decimal(equity_after, "equity_after")
        return_fraction = equity_after / equity_before - Decimal("1")
        values = {
            "contract_version": FORWARD_MEASUREMENT_RECEIPT_VERSION,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "policy_hash": policy_hash,
            "ordinal": ordinal,
            "selected_strategy_id": plan.selected_strategy_id,
            "selected_strategy_version": plan.selected_strategy_version,
            "strategy_spec_hash": plan.strategy_spec_hash,
            "w83_runtime_hash": plan.w83_runtime_hash,
            "measurement_runtime_hash": plan.measurement_runtime_hash,
            "backtest_config_hash": plan.backtest_config_hash,
            "dataset_source": plan.dataset_source,
            "prefix_dataset_hash": prefix_dataset.dataset_hash,
            "prefix_result_hash": result.result_hash,
            "period_started_at": period_bar.started_at,
            "period_ended_at": period_bar.ended_at,
            "equity_before": equity_before,
            "equity_after": equity_after,
            "return_fraction": return_fraction,
            "previous_measurement_hash": previous_measurement_hash,
            "paper_candidate_authorized": False,
            "external_execution_authorized": False,
            "runtime_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        measurement_hash = _hash(_measurement_payload_from_values(values))
        receipt_values = {
            **values,
            "measurement_hash": measurement_hash,
            "captured_at": captured_at,
        }
        receipt = ForwardShadowMeasurementReceipt(
            **receipt_values,
            receipt_hash=_hash(
                _measurement_payload_from_values(
                    receipt_values,
                    include_capture=True,
                )
            ),
        )
        receipts.append(receipt)
        previous_measurement_hash = receipt.measurement_hash
    return tuple(receipts)


def verify_shadow_measurement_binding(
    *,
    plan: ForwardMeasurementPlan,
    policy_hash: str,
    selected_strategy_id: str,
    shadow_records: tuple[ShadowPeriodRecord, ...],
    receipts: tuple[ForwardShadowMeasurementReceipt, ...],
    assessed_at: datetime,
) -> str:
    """Require every eligible R5 observation to equal its recomputed W84 receipt."""

    _validate_plan(plan)
    _require_hash(policy_hash, "policy_hash")
    _require_id(selected_strategy_id, "selected_strategy_id")
    _require_aware(assessed_at, "assessed_at")
    if selected_strategy_id != plan.selected_strategy_id:
        raise ForwardShadowMeasurementIntegrityError(
            "selected strategy does not match frozen measurement plan"
        )
    if len(shadow_records) != len(receipts):
        raise ForwardShadowMeasurementIntegrityError(
            "every eligible shadow record requires one measurement receipt"
        )
    previous_hash = GENESIS_MEASUREMENT_HASH
    for ordinal, (record, receipt) in enumerate(zip(shadow_records, receipts), start=1):
        _validate_receipt(receipt)
        if (
            receipt.ordinal != ordinal
            or receipt.plan_id != plan.plan_id
            or receipt.plan_hash != plan.plan_hash
            or receipt.policy_hash != policy_hash
            or receipt.selected_strategy_id != selected_strategy_id
            or receipt.selected_strategy_version != plan.selected_strategy_version
            or receipt.strategy_spec_hash != plan.strategy_spec_hash
            or receipt.w83_runtime_hash != plan.w83_runtime_hash
            or receipt.measurement_runtime_hash != plan.measurement_runtime_hash
            or receipt.backtest_config_hash != plan.backtest_config_hash
            or receipt.dataset_source != plan.dataset_source
            or receipt.previous_measurement_hash != previous_hash
        ):
            raise ForwardShadowMeasurementIntegrityError(
                "measurement receipt identity/chain differs from frozen plan"
            )
        if _utc(receipt.captured_at) > _utc(assessed_at):
            raise ForwardShadowMeasurementIntegrityError(
                "measurement receipt cannot be captured after W84 assessment"
            )
        raw = record.observation_payloads.get(selected_strategy_id)
        if not isinstance(raw, str):
            raise ForwardShadowMeasurementIntegrityError(
                "candidate shadow observation payload is missing"
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ForwardShadowMeasurementIntegrityError(
                "candidate shadow observation payload is not JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ForwardShadowMeasurementIntegrityError(
                "candidate shadow observation payload must be an object"
            )
        if (
            payload.get("strategy_id") != selected_strategy_id
            or payload.get("period_started_at") != _utc_iso(receipt.period_started_at)
            or payload.get("period_ended_at") != _utc_iso(receipt.period_ended_at)
            or payload.get("return_fraction") != str(receipt.return_fraction)
            or payload.get("source_fingerprint") != receipt.measurement_hash
            or record.weighted_return != receipt.return_fraction
        ):
            raise ForwardShadowMeasurementIntegrityError(
                "shadow observation is not the exact deterministic W84 measurement"
            )
        previous_hash = receipt.measurement_hash
    return previous_hash


def measurement_receipts_hash(
    receipts: tuple[ForwardShadowMeasurementReceipt, ...],
) -> str:
    for receipt in receipts:
        _validate_receipt(receipt)
    return _hash([receipt.measurement_hash for receipt in receipts])


def _validate_plan_inputs(
    *,
    plan: ForwardMeasurementPlan,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
    strategy_spec: StrategySpec,
    backtest_config: BacktestConfig,
    history_dataset: MarketDataset,
) -> None:
    if not isinstance(strategy_spec, StrategySpec):
        raise TypeError("strategy_spec must be StrategySpec")
    if not isinstance(backtest_config, BacktestConfig):
        raise TypeError("backtest_config must be BacktestConfig")
    if not isinstance(history_dataset, MarketDataset):
        raise TypeError("history_dataset must be MarketDataset")
    current_runtime = build_forward_measurement_runtime_identity()
    if (
        plan.w83_resolution_id != w83_resolution.resolution_id
        or plan.w83_resolution_hash != w83_resolution.resolution_hash
        or plan.w83_binding_hash != binding_evidence.evidence_hash
        or plan.selected_strategy_id != w83_resolution.selected_strategy_id
        or plan.selected_strategy_version != w83_resolution.selected_strategy_version
        or plan.strategy_spec_hash != strategy_spec.canonical_hash
        or plan.strategy_spec_hash != w83_resolution.strategy_spec_hash
        or plan.w83_runtime_hash != w83_resolution.loaded_runtime_code_hash
        or plan.measurement_runtime_hash != current_runtime.identity_hash
        or plan.runtime_python != current_runtime.python_runtime
        or plan.backtest_source_hash != current_runtime.backtest_source_hash
        or plan.costs_source_hash != current_runtime.costs_source_hash
        or plan.domain_source_hash != current_runtime.domain_source_hash
        or plan.backtest_config_hash != backtest_config.config_hash
        or plan.initial_cash != backtest_config.initial_cash
        or plan.history_dataset_hash != history_dataset.dataset_hash
        or plan.history_bars != len(history_dataset.bars)
        or plan.dataset_source != history_dataset.source
        or plan.timeframe_seconds != history_dataset.timeframe_seconds
        or _utc(plan.planned_at) != _utc(history_dataset.ended_at)
    ):
        raise ForwardShadowMeasurementIntegrityError(
            "measurement inputs differ from frozen W84 measurement plan"
        )
    _validate_dataset_identity(
        dataset=history_dataset,
        binding_evidence=binding_evidence,
        expected_source=plan.dataset_source,
        expected_timeframe=plan.timeframe_seconds,
    )


def _validate_w83_pair(
    *,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
) -> None:
    if not isinstance(w83_resolution, PromotionStrategyVersionResolution):
        raise TypeError("w83_resolution must be PromotionStrategyVersionResolution")
    if not isinstance(binding_evidence, ExecutionStrategyBindingEvidence):
        raise TypeError("binding_evidence must be ExecutionStrategyBindingEvidence")
    expected_resolution_hash = w83_resolution_module._hash(
        w83_resolution_module._payload(w83_resolution, include_hash=False)
    )
    expected_binding_hash = w83_binding_module._hash(
        w83_binding_module._evidence_payload(binding_evidence, include_hash=False)
    )
    if (
        w83_resolution.resolution_hash != expected_resolution_hash
        or binding_evidence.evidence_hash != expected_binding_hash
        or w83_resolution.binding_evidence_hash != binding_evidence.evidence_hash
        or w83_resolution.selected_strategy_id != binding_evidence.selected_strategy_id
        or w83_resolution.selected_strategy_version != binding_evidence.selected_strategy_version
        or w83_resolution.strategy_spec_hash != binding_evidence.strategy_spec_hash
    ):
        raise ForwardShadowMeasurementIntegrityError(
            "W83 resolution/binding identity mismatch"
        )
    current_w83_runtime = build_safe_dsl_runtime_identity()
    if current_w83_runtime.identity_hash != w83_resolution.loaded_runtime_code_hash:
        raise ForwardShadowMeasurementIntegrityError(
            "loaded research runtime differs from certified W83 runtime"
        )


def _validate_dataset_identity(
    *,
    dataset: MarketDataset,
    binding_evidence: ExecutionStrategyBindingEvidence,
    expected_source: str | None,
    expected_timeframe: int | None,
) -> None:
    if (
        dataset.instrument.symbol != binding_evidence.dataset_symbol
        or dataset.instrument.venue != binding_evidence.dataset_venue
        or dataset.instrument.quote_currency != binding_evidence.dataset_quote_currency
    ):
        raise ForwardShadowMeasurementIntegrityError(
            "forward measurement dataset market identity differs from W83 research identity"
        )
    if expected_source is not None and dataset.source != expected_source:
        raise ForwardShadowMeasurementIntegrityError(
            "forward measurement dataset source differs from frozen plan"
        )
    if expected_timeframe is not None and dataset.timeframe_seconds != expected_timeframe:
        raise ForwardShadowMeasurementIntegrityError(
            "forward measurement timeframe differs from frozen plan"
        )


def _validate_plan(plan: ForwardMeasurementPlan) -> None:
    if not isinstance(plan, ForwardMeasurementPlan):
        raise TypeError("plan must be ForwardMeasurementPlan")
    if plan.plan_hash != _hash(_plan_payload(plan, include_hash=False)):
        raise ForwardShadowMeasurementIntegrityError(
            "forward measurement plan hash mismatch"
        )


def _validate_receipt(receipt: ForwardShadowMeasurementReceipt) -> None:
    if not isinstance(receipt, ForwardShadowMeasurementReceipt):
        raise TypeError("measurement receipt must be ForwardShadowMeasurementReceipt")
    if receipt.measurement_hash != _hash(
        _measurement_payload(receipt, include_capture=False)
    ):
        raise ForwardShadowMeasurementIntegrityError("measurement hash mismatch")
    if receipt.receipt_hash != _hash(
        _measurement_payload(receipt, include_capture=True)
    ):
        raise ForwardShadowMeasurementIntegrityError("measurement receipt hash mismatch")


def _source_sha256(subject: object, expected_suffix: str) -> str:
    source_file = inspect.getsourcefile(subject)
    if source_file is None:
        raise ForwardShadowMeasurementIntegrityError(
            f"cannot locate runtime source {expected_suffix}"
        )
    path = Path(source_file).resolve()
    normalized = path.as_posix()
    if not normalized.endswith(expected_suffix):
        raise ForwardShadowMeasurementIntegrityError(
            f"runtime source path does not end with {expected_suffix}"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ForwardShadowMeasurementIntegrityError(
            f"cannot read runtime source {expected_suffix}"
        ) from exc
    return sha256(raw).hexdigest()


def _runtime_payload(
    value: ForwardMeasurementRuntimeIdentity, *, include_hash: bool
) -> dict[str, object]:
    payload = _runtime_payload_from_values(
        {
            "version": value.version,
            "python_runtime": value.python_runtime,
            "w83_runtime_hash": value.w83_runtime_hash,
            "backtest_source_hash": value.backtest_source_hash,
            "costs_source_hash": value.costs_source_hash,
            "domain_source_hash": value.domain_source_hash,
        }
    )
    if include_hash:
        payload["identity_hash"] = value.identity_hash
    return payload


def _runtime_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    return dict(values)


def _plan_payload(value: ForwardMeasurementPlan, *, include_hash: bool) -> dict[str, object]:
    payload = _plan_payload_from_values(
        {
            name: getattr(value, name)
            for name in (
                "plan_id",
                "contract_version",
                "w83_resolution_id",
                "w83_resolution_hash",
                "w83_binding_hash",
                "selected_strategy_id",
                "selected_strategy_version",
                "strategy_spec_hash",
                "w83_runtime_hash",
                "measurement_runtime_version",
                "measurement_runtime_hash",
                "runtime_python",
                "backtest_source_hash",
                "costs_source_hash",
                "domain_source_hash",
                "backtest_config_hash",
                "initial_cash",
                "history_dataset_hash",
                "dataset_source",
                "dataset_symbol",
                "dataset_venue",
                "dataset_quote_currency",
                "timeframe_seconds",
                "history_bars",
                "planned_at",
                "forward_activated_at",
                "paper_candidate_authorized",
                "external_execution_authorized",
                "runtime_execution_authorized",
                "capital_authority",
                "live_trading",
            )
        }
    )
    if include_hash:
        payload["plan_hash"] = value.plan_hash
    return payload


def _plan_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["initial_cash"] = str(payload["initial_cash"])
    payload["planned_at"] = _utc_iso(payload["planned_at"])
    payload["forward_activated_at"] = _utc_iso(payload["forward_activated_at"])
    return payload


def _measurement_payload(
    value: ForwardShadowMeasurementReceipt,
    *,
    include_capture: bool,
    include_receipt_hash: bool = False,
) -> dict[str, object]:
    values = {
        name: getattr(value, name)
        for name in (
            "contract_version",
            "plan_id",
            "plan_hash",
            "policy_hash",
            "ordinal",
            "selected_strategy_id",
            "selected_strategy_version",
            "strategy_spec_hash",
            "w83_runtime_hash",
            "measurement_runtime_hash",
            "backtest_config_hash",
            "dataset_source",
            "prefix_dataset_hash",
            "prefix_result_hash",
            "period_started_at",
            "period_ended_at",
            "equity_before",
            "equity_after",
            "return_fraction",
            "previous_measurement_hash",
            "paper_candidate_authorized",
            "external_execution_authorized",
            "runtime_execution_authorized",
            "capital_authority",
            "live_trading",
        )
    }
    if include_capture:
        values["measurement_hash"] = value.measurement_hash
        values["captured_at"] = value.captured_at
    payload = _measurement_payload_from_values(values, include_capture=include_capture)
    if include_receipt_hash:
        payload["receipt_hash"] = value.receipt_hash
    return payload


def _measurement_payload_from_values(
    values: dict[str, object], *, include_capture: bool = False
) -> dict[str, object]:
    payload = dict(values)
    payload["period_started_at"] = _utc_iso(payload["period_started_at"])
    payload["period_ended_at"] = _utc_iso(payload["period_ended_at"])
    payload["equity_before"] = str(payload["equity_before"])
    payload["equity_after"] = str(payload["equity_after"])
    payload["return_fraction"] = str(payload["return_fraction"])
    if include_capture:
        payload["captured_at"] = _utc_iso(payload["captured_at"])
    return payload


def _require_no_authority(
    *,
    paper: bool,
    external: bool,
    runtime: bool,
    capital: str,
    live: str,
    label: str,
) -> None:
    if (
        paper is not False
        or external is not False
        or runtime is not False
        or capital != "NONE"
        or live != "BLOCKED"
    ):
        raise ForwardShadowMeasurementIntegrityError(
            f"{label} may not grant PAPER, execution, capital, or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ForwardShadowMeasurementIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ForwardShadowMeasurementIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ForwardShadowMeasurementIntegrityError(f"{label} must be integer >=1")


def _require_finite_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ForwardShadowMeasurementIntegrityError(f"{label} must be finite Decimal")


def _require_positive_decimal(value: Decimal, label: str) -> None:
    _require_finite_decimal(value, label)
    if value <= 0:
        raise ForwardShadowMeasurementIntegrityError(f"{label} must be > 0")


def _require_aware(value: datetime, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ForwardShadowMeasurementIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _utc_iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise ForwardShadowMeasurementIntegrityError("datetime value required")
    return _utc(value).isoformat()


def _hash(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "FORWARD_MEASUREMENT_PLAN_VERSION",
    "FORWARD_MEASUREMENT_RECEIPT_VERSION",
    "FORWARD_MEASUREMENT_RUNTIME_VERSION",
    "GENESIS_MEASUREMENT_HASH",
    "ForwardMeasurementPlan",
    "ForwardMeasurementRuntimeIdentity",
    "ForwardShadowMeasurementIntegrityError",
    "ForwardShadowMeasurementReceipt",
    "build_forward_measurement_plan",
    "build_forward_measurement_runtime_identity",
    "build_forward_shadow_measurements",
    "measurement_receipts_hash",
    "verify_shadow_measurement_binding",
]
