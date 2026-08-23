from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderStatus,
    PortfolioSnapshot,
    RiskDecisionStatus,
    intent_fingerprint,
    market_fingerprint,
)
from autotrade.engine import TradingPipeline
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import BrokerSubmissionAmbiguous, OrderManagementSystem
from autotrade.paper_execution_evidence import capture_paper_execution_evidence
from autotrade.paper_execution_qualification import PaperExecutionQualificationContract
from autotrade.paper_execution_scenarios import PaperExecutionScenarioMatrix
from autotrade.safety import CapitalSafetyKernel, SafetyLimits


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperExecutionSensitivityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PaperExecutionScenarioOutcome:
    scenario_id: str
    scenario_hash: str
    risk_status: str
    risk_reason_code: str
    order_status: str | None
    broker_rejection_reason: str | None
    fill_ratio: Decimal | None
    adverse_slippage_bps: Decimal | None
    measurement_hash: str | None
    trace_evidence_hash: str | None
    outcome_hash: str

    def __post_init__(self) -> None:
        for label, value in (("scenario_hash", self.scenario_hash), ("outcome_hash", self.outcome_hash)):
            _require_hash(value, label)
        if self.measurement_hash is not None:
            _require_hash(self.measurement_hash, "measurement_hash")
        if self.trace_evidence_hash is not None:
            _require_hash(self.trace_evidence_hash, "trace_evidence_hash")
        if self.risk_status not in {item.value for item in RiskDecisionStatus}:
            raise PaperExecutionSensitivityError("invalid risk_status")
        if self.order_status is not None:
            try:
                OrderStatus(self.order_status)
            except ValueError as exc:
                raise PaperExecutionSensitivityError("invalid order_status") from exc
        if self.fill_ratio is not None and (
            not isinstance(self.fill_ratio, Decimal)
            or not self.fill_ratio.is_finite()
            or self.fill_ratio < 0
            or self.fill_ratio > 1
        ):
            raise PaperExecutionSensitivityError("fill_ratio must be finite within [0,1]")
        if self.adverse_slippage_bps is not None and (
            not isinstance(self.adverse_slippage_bps, Decimal)
            or not self.adverse_slippage_bps.is_finite()
            or self.adverse_slippage_bps < 0
        ):
            raise PaperExecutionSensitivityError("adverse_slippage_bps must be finite and non-negative")
        if self.order_status is None:
            if self.risk_status != RiskDecisionStatus.REJECTED.value:
                raise PaperExecutionSensitivityError("missing order requires a rejected RiskDecision")
            if any(
                value is not None
                for value in (
                    self.broker_rejection_reason,
                    self.fill_ratio,
                    self.adverse_slippage_bps,
                    self.measurement_hash,
                    self.trace_evidence_hash,
                )
            ):
                raise PaperExecutionSensitivityError("risk-rejected outcome may not claim execution evidence")
        else:
            if self.risk_status != RiskDecisionStatus.APPROVED.value:
                raise PaperExecutionSensitivityError("execution outcome requires approved RiskDecision")
            if self.measurement_hash is None or self.trace_evidence_hash is None or self.fill_ratio is None:
                raise PaperExecutionSensitivityError("execution outcome requires complete evidence hashes")
            if self.order_status == OrderStatus.REJECTED.value and not self.broker_rejection_reason:
                raise PaperExecutionSensitivityError("broker rejection requires reason code")
            if self.order_status != OrderStatus.REJECTED.value and self.broker_rejection_reason is not None:
                raise PaperExecutionSensitivityError("non-rejected order may not claim broker rejection reason")
        if self.outcome_hash != _hash(_outcome_measurement_payload(self)):
            raise PaperExecutionSensitivityError("scenario outcome hash mismatch")

    def measurement_dict(self) -> dict[str, object]:
        payload = _outcome_measurement_payload(self)
        payload["outcome_hash"] = self.outcome_hash
        return payload

    def trace_dict(self) -> dict[str, object]:
        payload = self.measurement_dict()
        payload["trace_evidence_hash"] = self.trace_evidence_hash
        return payload


@dataclass(frozen=True, slots=True)
class PaperExecutionSensitivityReport:
    qualification_contract_hash: str
    scenario_matrix_hash: str
    intent_fingerprint: str
    market_fingerprint: str
    portfolio_hash: str
    safety_limits_hash: str
    safety_limits_version: str
    outcomes: tuple[PaperExecutionScenarioOutcome, ...]
    full_fill_count: int
    partial_fill_count: int
    zero_fill_count: int
    broker_rejection_count: int
    risk_rejection_count: int
    minimum_fill_ratio: Decimal | None
    maximum_adverse_slippage_bps: Decimal | None
    external_execution_authorized: bool
    live_trading: str
    measurement_report_hash: str
    trace_report_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("qualification_contract_hash", self.qualification_contract_hash),
            ("scenario_matrix_hash", self.scenario_matrix_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("portfolio_hash", self.portfolio_hash),
            ("safety_limits_hash", self.safety_limits_hash),
            ("measurement_report_hash", self.measurement_report_hash),
            ("trace_report_hash", self.trace_report_hash),
        ):
            _require_hash(value, label)
        if not self.safety_limits_version.strip():
            raise PaperExecutionSensitivityError("safety_limits_version is required")
        if not self.outcomes:
            raise PaperExecutionSensitivityError("sensitivity report requires outcomes")
        if tuple(sorted(self.outcomes, key=lambda item: item.scenario_id)) != self.outcomes:
            raise PaperExecutionSensitivityError("outcomes must be sorted by scenario_id")
        if len({item.scenario_id for item in self.outcomes}) != len(self.outcomes):
            raise PaperExecutionSensitivityError("duplicate scenario outcome")
        counts = (
            self.full_fill_count,
            self.partial_fill_count,
            self.zero_fill_count,
            self.broker_rejection_count,
            self.risk_rejection_count,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
            raise PaperExecutionSensitivityError("outcome counts must be non-negative integers")
        if sum(counts) != len(self.outcomes):
            raise PaperExecutionSensitivityError("outcome counts do not cover scenario matrix")
        if self.minimum_fill_ratio is not None and (
            not isinstance(self.minimum_fill_ratio, Decimal)
            or not Decimal("0") <= self.minimum_fill_ratio <= Decimal("1")
        ):
            raise PaperExecutionSensitivityError("minimum_fill_ratio must be within [0,1]")
        if self.maximum_adverse_slippage_bps is not None and (
            not isinstance(self.maximum_adverse_slippage_bps, Decimal)
            or self.maximum_adverse_slippage_bps < 0
        ):
            raise PaperExecutionSensitivityError("maximum_adverse_slippage_bps must be non-negative")
        if self.external_execution_authorized is not False or self.live_trading != "BLOCKED":
            raise PaperExecutionSensitivityError("sensitivity report may not grant external/LIVE authority")
        if self.measurement_report_hash != _hash(_report_measurement_payload(self)):
            raise PaperExecutionSensitivityError("measurement report hash mismatch")
        if self.trace_report_hash != _hash(_report_trace_payload(self)):
            raise PaperExecutionSensitivityError("trace report hash mismatch")

    def to_dict(self) -> dict[str, object]:
        payload = _report_trace_payload(self)
        payload["measurement_report_hash"] = self.measurement_report_hash
        payload["trace_report_hash"] = self.trace_report_hash
        return payload


def run_paper_execution_sensitivity(
    *,
    qualification: PaperExecutionQualificationContract,
    matrix: PaperExecutionScenarioMatrix,
    limits: SafetyLimits,
    intent: OrderIntent,
    market: MarketSnapshot,
    portfolio: PortfolioSnapshot,
    now: datetime,
) -> PaperExecutionSensitivityReport:
    if not isinstance(qualification, PaperExecutionQualificationContract):
        raise TypeError("qualification must be PaperExecutionQualificationContract")
    if not isinstance(matrix, PaperExecutionScenarioMatrix):
        raise TypeError("matrix must be PaperExecutionScenarioMatrix")
    if qualification.scenario_matrix_hash != matrix.matrix_hash:
        raise PaperExecutionSensitivityError("qualification/matrix hash mismatch")
    if not isinstance(limits, SafetyLimits):
        raise TypeError("limits must be SafetyLimits")
    if not isinstance(intent, OrderIntent) or not isinstance(market, MarketSnapshot):
        raise TypeError("intent and market must use canonical domain types")
    if not isinstance(portfolio, PortfolioSnapshot):
        raise TypeError("portfolio must be PortfolioSnapshot")
    _require_aware(now, "now")

    outcomes: list[PaperExecutionScenarioOutcome] = []
    for scenario in matrix.scenarios:
        ledger = InMemoryEventLedger()
        broker = scenario.build_broker()
        pipeline = TradingPipeline(
            safety=CapitalSafetyKernel(limits, ledger),
            oms=OrderManagementSystem(broker=broker, ledger=ledger),
        )
        try:
            result = pipeline.process_intent(
                intent=intent,
                market=market,
                portfolio=portfolio,
                now=now,
            )
        except BrokerSubmissionAmbiguous as exc:
            raise PaperExecutionSensitivityError(
                f"no-network W78 scenario entered ambiguous broker state: {scenario.scenario_id}"
            ) from exc

        if result.order is None:
            outcome = _build_risk_rejected_outcome(
                scenario_id=scenario.scenario_id,
                scenario_hash=scenario.scenario_hash,
                risk_status=result.decision.status.value,
                risk_reason_code=result.decision.reason_code,
            )
        else:
            evidence = capture_paper_execution_evidence(
                scenario=scenario,
                order=result.order,
                market=market,
                captured_at=now,
            )
            rejection_reason = broker.rejection_reason_for_order(result.order.order_id)
            outcome = _build_execution_outcome(
                scenario_id=scenario.scenario_id,
                scenario_hash=scenario.scenario_hash,
                risk_status=result.decision.status.value,
                risk_reason_code=result.decision.reason_code,
                order_status=result.order.status.value,
                broker_rejection_reason=rejection_reason,
                fill_ratio=evidence.fill_ratio,
                adverse_slippage_bps=evidence.adverse_slippage_bps,
                measurement_hash=evidence.measurement_hash,
                trace_evidence_hash=evidence.evidence_hash,
            )
        outcomes.append(outcome)

    ordered = tuple(sorted(outcomes, key=lambda item: item.scenario_id))
    statuses = [item.order_status for item in ordered]
    ratios = [item.fill_ratio for item in ordered if item.fill_ratio is not None]
    slippages = [
        item.adverse_slippage_bps
        for item in ordered
        if item.adverse_slippage_bps is not None
    ]
    values = {
        "qualification_contract_hash": qualification.contract_hash,
        "scenario_matrix_hash": matrix.matrix_hash,
        "intent_fingerprint": intent_fingerprint(intent),
        "market_fingerprint": market_fingerprint(market),
        "portfolio_hash": _portfolio_hash(portfolio),
        "safety_limits_hash": _safety_limits_hash(limits),
        "safety_limits_version": limits.limits_version,
        "outcomes": ordered,
        "full_fill_count": sum(status == OrderStatus.FILLED.value for status in statuses),
        "partial_fill_count": sum(status == OrderStatus.PARTIALLY_FILLED.value for status in statuses),
        "zero_fill_count": sum(status == OrderStatus.SUBMITTED.value for status in statuses),
        "broker_rejection_count": sum(status == OrderStatus.REJECTED.value for status in statuses),
        "risk_rejection_count": sum(status is None for status in statuses),
        "minimum_fill_ratio": min(ratios) if ratios else None,
        "maximum_adverse_slippage_bps": max(slippages) if slippages else None,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    measurement_hash = _hash(_report_measurement_payload_from_values(values))
    trace_values = dict(values)
    trace_values["measurement_report_hash"] = measurement_hash
    return PaperExecutionSensitivityReport(
        **values,
        measurement_report_hash=measurement_hash,
        trace_report_hash=_hash(_report_trace_payload_from_values(trace_values)),
    )


def _build_risk_rejected_outcome(
    *, scenario_id: str, scenario_hash: str, risk_status: str, risk_reason_code: str
) -> PaperExecutionScenarioOutcome:
    values = {
        "scenario_id": scenario_id,
        "scenario_hash": scenario_hash,
        "risk_status": risk_status,
        "risk_reason_code": risk_reason_code,
        "order_status": None,
        "broker_rejection_reason": None,
        "fill_ratio": None,
        "adverse_slippage_bps": None,
        "measurement_hash": None,
        "trace_evidence_hash": None,
    }
    return PaperExecutionScenarioOutcome(
        **values,
        outcome_hash=_hash(_outcome_measurement_payload_from_values(values)),
    )


def _build_execution_outcome(**values) -> PaperExecutionScenarioOutcome:
    return PaperExecutionScenarioOutcome(
        **values,
        outcome_hash=_hash(_outcome_measurement_payload_from_values(values)),
    )


def _outcome_measurement_payload(value: PaperExecutionScenarioOutcome) -> dict[str, object]:
    return _outcome_measurement_payload_from_values(
        {
            "scenario_id": value.scenario_id,
            "scenario_hash": value.scenario_hash,
            "risk_status": value.risk_status,
            "risk_reason_code": value.risk_reason_code,
            "order_status": value.order_status,
            "broker_rejection_reason": value.broker_rejection_reason,
            "fill_ratio": value.fill_ratio,
            "adverse_slippage_bps": value.adverse_slippage_bps,
            "measurement_hash": value.measurement_hash,
        }
    )


def _outcome_measurement_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload.pop("trace_evidence_hash", None)
    payload.pop("outcome_hash", None)
    for key in ("fill_ratio", "adverse_slippage_bps"):
        raw = payload[key]
        payload[key] = None if raw is None else _decimal(raw)  # type: ignore[arg-type]
    return payload


def _report_measurement_payload(value: PaperExecutionSensitivityReport) -> dict[str, object]:
    return _report_measurement_payload_from_values(
        {
            "qualification_contract_hash": value.qualification_contract_hash,
            "scenario_matrix_hash": value.scenario_matrix_hash,
            "intent_fingerprint": value.intent_fingerprint,
            "market_fingerprint": value.market_fingerprint,
            "portfolio_hash": value.portfolio_hash,
            "safety_limits_hash": value.safety_limits_hash,
            "safety_limits_version": value.safety_limits_version,
            "outcomes": value.outcomes,
            "full_fill_count": value.full_fill_count,
            "partial_fill_count": value.partial_fill_count,
            "zero_fill_count": value.zero_fill_count,
            "broker_rejection_count": value.broker_rejection_count,
            "risk_rejection_count": value.risk_rejection_count,
            "minimum_fill_ratio": value.minimum_fill_ratio,
            "maximum_adverse_slippage_bps": value.maximum_adverse_slippage_bps,
            "external_execution_authorized": value.external_execution_authorized,
            "live_trading": value.live_trading,
        }
    )


def _report_measurement_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload.pop("measurement_report_hash", None)
    payload.pop("trace_report_hash", None)
    outcomes = payload["outcomes"]
    payload["outcomes"] = [item.measurement_dict() for item in outcomes]  # type: ignore[union-attr]
    for key in ("minimum_fill_ratio", "maximum_adverse_slippage_bps"):
        raw = payload[key]
        payload[key] = None if raw is None else _decimal(raw)  # type: ignore[arg-type]
    return payload


def _report_trace_payload(value: PaperExecutionSensitivityReport) -> dict[str, object]:
    return _report_trace_payload_from_values(
        {
            "qualification_contract_hash": value.qualification_contract_hash,
            "scenario_matrix_hash": value.scenario_matrix_hash,
            "intent_fingerprint": value.intent_fingerprint,
            "market_fingerprint": value.market_fingerprint,
            "portfolio_hash": value.portfolio_hash,
            "safety_limits_hash": value.safety_limits_hash,
            "safety_limits_version": value.safety_limits_version,
            "outcomes": value.outcomes,
            "full_fill_count": value.full_fill_count,
            "partial_fill_count": value.partial_fill_count,
            "zero_fill_count": value.zero_fill_count,
            "broker_rejection_count": value.broker_rejection_count,
            "risk_rejection_count": value.risk_rejection_count,
            "minimum_fill_ratio": value.minimum_fill_ratio,
            "maximum_adverse_slippage_bps": value.maximum_adverse_slippage_bps,
            "external_execution_authorized": value.external_execution_authorized,
            "live_trading": value.live_trading,
            "measurement_report_hash": value.measurement_report_hash,
        }
    )


def _report_trace_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload.pop("trace_report_hash", None)
    outcomes = payload["outcomes"]
    payload["outcomes"] = [item.trace_dict() for item in outcomes]  # type: ignore[union-attr]
    for key in ("minimum_fill_ratio", "maximum_adverse_slippage_bps"):
        raw = payload[key]
        payload[key] = None if raw is None else _decimal(raw)  # type: ignore[arg-type]
    return payload


def _portfolio_hash(portfolio: PortfolioSnapshot) -> str:
    payload = {
        "snapshot_id": portfolio.snapshot_id,
        "equity": _decimal(portfolio.equity),
        "gross_exposure": _decimal(portfolio.gross_exposure),
        "net_exposure": _decimal(portfolio.net_exposure),
        "daily_pnl": _decimal(portfolio.daily_pnl),
        "drawdown": _decimal(portfolio.drawdown),
        "open_orders": portfolio.open_orders,
        "signed_position_notional_by_symbol": _decimal_map(portfolio.signed_position_notional_by_symbol),
        "strategy_gross_exposure": _decimal_map(portfolio.strategy_gross_exposure),
        "strategy_signed_position_notional_by_symbol": {
            strategy: _decimal_map(values)
            for strategy, values in sorted(portfolio.strategy_signed_position_notional_by_symbol.items())
        },
        "reconciliation_ok": portfolio.reconciliation_ok,
        "broker_state_known": portfolio.broker_state_known,
    }
    return _hash(payload)


def _safety_limits_hash(limits: SafetyLimits) -> str:
    payload = {
        "limits_version": limits.limits_version,
        "allowed_symbols": sorted(limits.allowed_symbols),
        "allowed_order_types": sorted(item.value for item in limits.allowed_order_types),
        "max_order_notional": _decimal(limits.max_order_notional),
        "max_position_notional": _decimal(limits.max_position_notional),
        "max_strategy_gross_exposure": _decimal(limits.max_strategy_gross_exposure),
        "max_portfolio_gross_exposure": _decimal(limits.max_portfolio_gross_exposure),
        "max_net_exposure": _decimal(limits.max_net_exposure),
        "max_leverage": _decimal(limits.max_leverage),
        "max_daily_loss": _decimal(limits.max_daily_loss),
        "max_drawdown": _decimal(limits.max_drawdown),
        "max_open_orders": limits.max_open_orders,
        "stale_market_data_ms": limits.stale_market_data_ms,
        "price_deviation_bps": _decimal(limits.price_deviation_bps),
        "decision_ttl_ms": limits.decision_ttl_ms,
    }
    return _hash(payload)


def _decimal_map(values) -> dict[str, str]:
    return {key: _decimal(value) for key, value in sorted(values.items())}


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperExecutionSensitivityError(f"{label} must be lowercase sha256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperExecutionSensitivityError(f"{label} must be timezone-aware datetime")


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "PaperExecutionScenarioOutcome",
    "PaperExecutionSensitivityError",
    "PaperExecutionSensitivityReport",
    "run_paper_execution_sensitivity",
]
