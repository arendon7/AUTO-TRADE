from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import MarketSnapshot, OrderIntent, Side, intent_fingerprint, market_fingerprint
from autotrade.paper_execution_lab import PaperExecutionSensitivityReport
from autotrade.paper_execution_qualification import PaperExecutionQualificationContract
from autotrade.paper_execution_scenarios import PaperExecutionScenario, PaperExecutionScenarioMatrix
from autotrade.research.costs import ExecutionCostModel
from autotrade.strategy_lab_promotion import PERMANENT_W79_PROMOTION_BLOCKERS


BPS_DENOMINATOR = Decimal("10000")
CONTINUITY_CONTRACT_VERSION = "W81_EXECUTION_COST_CONTINUITY_V1"
CONTINUITY_BLOCKER = "TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN"
FEE_ACCOUNTING_BLOCKER = "FEE_ACCOUNTING_INCOMPLETE"
FEE_ACCOUNTING_STATE = "INCOMPLETE_NOT_ASSESSED_BY_W81"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ExecutionCostContinuityError(RuntimeError):
    pass


class ExecutionCostContinuityIntegrityError(ExecutionCostContinuityError):
    pass


class ExecutionCostContinuityStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ExecutionCostContinuityObservation:
    scenario_id: str
    scenario_hash: str
    outcome_hash: str
    market_fingerprint: str
    symbol: str
    side: Side
    midpoint: Decimal
    touch_price: Decimal
    modeled_adverse_price: Decimal
    observed_half_spread_bps: Decimal
    scenario_slippage_bps: Decimal
    effective_non_fee_impact_bps: Decimal
    research_half_spread_bps: Decimal
    research_slippage_bps: Decimal
    research_non_fee_impact_bps: Decimal
    continuity_margin_bps: Decimal
    status: ExecutionCostContinuityStatus
    reason_code: str
    observation_hash: str

    def __post_init__(self) -> None:
        _require_id(self.scenario_id, "scenario_id")
        for label, value in (
            ("scenario_hash", self.scenario_hash),
            ("outcome_hash", self.outcome_hash),
            ("market_fingerprint", self.market_fingerprint),
            ("observation_hash", self.observation_hash),
        ):
            _require_hash(value, label)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ExecutionCostContinuityIntegrityError("symbol is required")
        if not isinstance(self.side, Side):
            raise ExecutionCostContinuityIntegrityError("side must use canonical Side")
        for label, value in (
            ("midpoint", self.midpoint),
            ("touch_price", self.touch_price),
            ("modeled_adverse_price", self.modeled_adverse_price),
        ):
            _require_positive_decimal(value, label)
        for label, value in (
            ("observed_half_spread_bps", self.observed_half_spread_bps),
            ("scenario_slippage_bps", self.scenario_slippage_bps),
            ("effective_non_fee_impact_bps", self.effective_non_fee_impact_bps),
            ("research_half_spread_bps", self.research_half_spread_bps),
            ("research_slippage_bps", self.research_slippage_bps),
            ("research_non_fee_impact_bps", self.research_non_fee_impact_bps),
        ):
            _require_non_negative_decimal(value, label)
        if not isinstance(self.continuity_margin_bps, Decimal) or not self.continuity_margin_bps.is_finite():
            raise ExecutionCostContinuityIntegrityError("continuity_margin_bps must be finite Decimal")
        if self.research_non_fee_impact_bps != self.research_half_spread_bps + self.research_slippage_bps:
            raise ExecutionCostContinuityIntegrityError("research non-fee impact is inconsistent")
        if self.continuity_margin_bps != self.effective_non_fee_impact_bps - self.research_non_fee_impact_bps:
            raise ExecutionCostContinuityIntegrityError("continuity margin is inconsistent")
        expected = (
            ExecutionCostContinuityStatus.PASS
            if self.continuity_margin_bps >= 0 and self.reason_code == "NON_FEE_CONTINUITY_CONSERVATIVE"
            else ExecutionCostContinuityStatus.BLOCKED
        )
        if self.status is not expected:
            raise ExecutionCostContinuityIntegrityError("continuity observation status/reason mismatch")
        if self.status is ExecutionCostContinuityStatus.BLOCKED and self.reason_code == "NON_FEE_CONTINUITY_CONSERVATIVE":
            raise ExecutionCostContinuityIntegrityError("BLOCKED observation may not use PASS reason")
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise ExecutionCostContinuityIntegrityError("reason_code is required")
        if self.observation_hash != _hash(_observation_payload(self, include_hash=False)):
            raise ExecutionCostContinuityIntegrityError("continuity observation hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _observation_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class ExecutionCostContinuityEvidence:
    evidence_id: str
    contract_version: str
    qualification_contract_hash: str
    research_cost_model_hash: str
    scenario_matrix_hash: str
    sensitivity_measurement_hash: str
    intent_fingerprint: str
    market_fingerprint: str
    symbol: str
    side: Side
    observed_at: datetime
    assessed_at: datetime
    observations: tuple[ExecutionCostContinuityObservation, ...]
    status: ExecutionCostContinuityStatus
    blocking_reasons: tuple[str, ...]
    resolved_promotion_blockers: tuple[str, ...]
    remaining_promotion_blockers: tuple[str, ...]
    fee_accounting_complete: bool
    fee_accounting_state: str
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    evidence_hash: str

    def __post_init__(self) -> None:
        _require_id(self.evidence_id, "evidence_id")
        if self.contract_version != CONTINUITY_CONTRACT_VERSION:
            raise ExecutionCostContinuityIntegrityError("continuity contract version is not canonical W81")
        for label, value in (
            ("qualification_contract_hash", self.qualification_contract_hash),
            ("research_cost_model_hash", self.research_cost_model_hash),
            ("scenario_matrix_hash", self.scenario_matrix_hash),
            ("sensitivity_measurement_hash", self.sensitivity_measurement_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("evidence_hash", self.evidence_hash),
        ):
            _require_hash(value, label)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ExecutionCostContinuityIntegrityError("symbol is required")
        if not isinstance(self.side, Side):
            raise ExecutionCostContinuityIntegrityError("side must use canonical Side")
        _require_aware(self.observed_at, "observed_at")
        _require_aware(self.assessed_at, "assessed_at")
        if _utc(self.assessed_at) < _utc(self.observed_at):
            raise ExecutionCostContinuityIntegrityError("assessment may not predate market observation")
        if not self.observations:
            raise ExecutionCostContinuityIntegrityError("continuity evidence requires observations")
        if self.observations != tuple(sorted(self.observations, key=lambda item: item.scenario_id)):
            raise ExecutionCostContinuityIntegrityError("continuity observations must be sorted by scenario_id")
        if len({item.scenario_id for item in self.observations}) != len(self.observations):
            raise ExecutionCostContinuityIntegrityError("duplicate continuity scenario")
        if any(item.market_fingerprint != self.market_fingerprint for item in self.observations):
            raise ExecutionCostContinuityIntegrityError("observation market fingerprint mismatch")
        if any(item.symbol != self.symbol or item.side is not self.side for item in self.observations):
            raise ExecutionCostContinuityIntegrityError("observation execution identity mismatch")
        expected_status = (
            ExecutionCostContinuityStatus.PASS
            if all(item.status is ExecutionCostContinuityStatus.PASS for item in self.observations)
            else ExecutionCostContinuityStatus.BLOCKED
        )
        if self.status is not expected_status:
            raise ExecutionCostContinuityIntegrityError("aggregate continuity status mismatch")
        expected_reasons = tuple(sorted({
            item.reason_code
            for item in self.observations
            if item.status is ExecutionCostContinuityStatus.BLOCKED
        }))
        if self.blocking_reasons != expected_reasons:
            raise ExecutionCostContinuityIntegrityError("continuity blocking reasons mismatch")
        expected_resolved = (CONTINUITY_BLOCKER,) if self.status is ExecutionCostContinuityStatus.PASS else ()
        if self.resolved_promotion_blockers != expected_resolved:
            raise ExecutionCostContinuityIntegrityError("resolved promotion blockers are inconsistent")
        expected_remaining = tuple(sorted(set(PERMANENT_W79_PROMOTION_BLOCKERS) - set(expected_resolved)))
        if self.remaining_promotion_blockers != expected_remaining:
            raise ExecutionCostContinuityIntegrityError("remaining promotion blockers are inconsistent")
        if FEE_ACCOUNTING_BLOCKER not in self.remaining_promotion_blockers:
            raise ExecutionCostContinuityIntegrityError("W81 may not close fee accounting")
        if self.fee_accounting_complete is not False or self.fee_accounting_state != FEE_ACCOUNTING_STATE:
            raise ExecutionCostContinuityIntegrityError("W81 fee accounting must remain explicitly incomplete")
        if self.paper_candidate_authorized is not False or self.external_execution_authorized is not False:
            raise ExecutionCostContinuityIntegrityError("W81 may not authorize PAPER candidate or external execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise ExecutionCostContinuityIntegrityError("W81 may not grant capital or LIVE authority")
        if self.evidence_hash != _hash(_evidence_payload(self, include_hash=False)):
            raise ExecutionCostContinuityIntegrityError("execution cost continuity evidence hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _evidence_payload(self, include_hash=True)


def build_execution_cost_continuity_evidence(
    *,
    evidence_id: str,
    cost_model: ExecutionCostModel,
    qualification: PaperExecutionQualificationContract,
    matrix: PaperExecutionScenarioMatrix,
    sensitivity_report: PaperExecutionSensitivityReport,
    intent: OrderIntent,
    market: MarketSnapshot,
    assessed_at: datetime,
) -> ExecutionCostContinuityEvidence:
    """Prove R1 -> W78 non-fee market-impact continuity without execution authority.

    W78 freshness and market-quality decisions belong to the W78 sensitivity
    outcome at execution time. W81 may be assessed later without retroactively
    making a previously valid execution measurement stale. W81 therefore binds
    the exact W78 outcome and enforces temporal causality only: assessment cannot
    predate the observed market. Fees remain a separate blocking debt item.
    """

    _require_id(evidence_id, "evidence_id")
    if not isinstance(cost_model, ExecutionCostModel):
        raise TypeError("cost_model must be ExecutionCostModel")
    if not isinstance(qualification, PaperExecutionQualificationContract):
        raise TypeError("qualification must be PaperExecutionQualificationContract")
    if not isinstance(matrix, PaperExecutionScenarioMatrix):
        raise TypeError("matrix must be PaperExecutionScenarioMatrix")
    if not isinstance(sensitivity_report, PaperExecutionSensitivityReport):
        raise TypeError("sensitivity_report must be PaperExecutionSensitivityReport")
    if not isinstance(intent, OrderIntent) or not isinstance(market, MarketSnapshot):
        raise TypeError("intent and market must use canonical domain types")
    _require_aware(assessed_at, "assessed_at")
    _require_aware(market.observed_at, "market.observed_at")

    cost_hash = _hash(cost_model.fingerprint_payload())
    if qualification.research_cost_model_hash != cost_hash:
        raise ExecutionCostContinuityIntegrityError("qualification/research cost model hash mismatch")
    if (
        qualification.research_fee_bps != cost_model.fee_bps
        or qualification.research_half_spread_bps != cost_model.half_spread_bps
        or qualification.research_slippage_bps != cost_model.slippage_bps
    ):
        raise ExecutionCostContinuityIntegrityError("qualification/research cost side columns mismatch")
    if qualification.scenario_matrix_hash != matrix.matrix_hash:
        raise ExecutionCostContinuityIntegrityError("qualification/scenario matrix hash mismatch")
    if sensitivity_report.qualification_contract_hash != qualification.contract_hash:
        raise ExecutionCostContinuityIntegrityError("sensitivity/qualification contract hash mismatch")
    if sensitivity_report.scenario_matrix_hash != matrix.matrix_hash:
        raise ExecutionCostContinuityIntegrityError("sensitivity/scenario matrix hash mismatch")

    intent_hash = intent_fingerprint(intent)
    market_hash = market_fingerprint(market)
    if sensitivity_report.intent_fingerprint != intent_hash:
        raise ExecutionCostContinuityIntegrityError("sensitivity/intent fingerprint mismatch")
    if sensitivity_report.market_fingerprint != market_hash:
        raise ExecutionCostContinuityIntegrityError("sensitivity/market fingerprint mismatch")
    if intent.symbol != market.symbol:
        raise ExecutionCostContinuityIntegrityError("intent/market symbol mismatch")
    if _utc(assessed_at) < _utc(market.observed_at):
        raise ExecutionCostContinuityIntegrityError("assessment may not predate market observation")

    outcomes = {item.scenario_id: item for item in sensitivity_report.outcomes}
    if set(outcomes) != {item.scenario_id for item in matrix.scenarios}:
        raise ExecutionCostContinuityIntegrityError("sensitivity outcome universe differs from scenario matrix")

    observations: list[ExecutionCostContinuityObservation] = []
    for scenario in matrix.scenarios:
        outcome = outcomes[scenario.scenario_id]
        if outcome.scenario_hash != scenario.scenario_hash:
            raise ExecutionCostContinuityIntegrityError(
                f"sensitivity scenario hash mismatch: {scenario.scenario_id}"
            )
        observations.append(_build_observation(
            scenario=scenario,
            outcome_hash=outcome.outcome_hash,
            has_execution_measurement=outcome.measurement_hash is not None,
            broker_rejection_reason=outcome.broker_rejection_reason,
            cost_model=cost_model,
            intent=intent,
            market=market,
        ))

    ordered = tuple(sorted(observations, key=lambda item: item.scenario_id))
    status = (
        ExecutionCostContinuityStatus.PASS
        if all(item.status is ExecutionCostContinuityStatus.PASS for item in ordered)
        else ExecutionCostContinuityStatus.BLOCKED
    )
    reasons = tuple(sorted({
        item.reason_code
        for item in ordered
        if item.status is ExecutionCostContinuityStatus.BLOCKED
    }))
    resolved = (CONTINUITY_BLOCKER,) if status is ExecutionCostContinuityStatus.PASS else ()
    remaining = tuple(sorted(set(PERMANENT_W79_PROMOTION_BLOCKERS) - set(resolved)))
    values = {
        "evidence_id": evidence_id,
        "contract_version": CONTINUITY_CONTRACT_VERSION,
        "qualification_contract_hash": qualification.contract_hash,
        "research_cost_model_hash": cost_hash,
        "scenario_matrix_hash": matrix.matrix_hash,
        "sensitivity_measurement_hash": sensitivity_report.measurement_report_hash,
        "intent_fingerprint": intent_hash,
        "market_fingerprint": market_hash,
        "symbol": intent.symbol,
        "side": intent.side,
        "observed_at": market.observed_at,
        "assessed_at": assessed_at,
        "observations": ordered,
        "status": status,
        "blocking_reasons": reasons,
        "resolved_promotion_blockers": resolved,
        "remaining_promotion_blockers": remaining,
        "fee_accounting_complete": False,
        "fee_accounting_state": FEE_ACCOUNTING_STATE,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return ExecutionCostContinuityEvidence(
        **values,
        evidence_hash=_hash(_evidence_payload_from_values(values)),
    )


def _build_observation(
    *,
    scenario: PaperExecutionScenario,
    outcome_hash: str,
    has_execution_measurement: bool,
    broker_rejection_reason: str | None,
    cost_model: ExecutionCostModel,
    intent: OrderIntent,
    market: MarketSnapshot,
) -> ExecutionCostContinuityObservation:
    _require_hash(outcome_hash, "outcome_hash")
    market_hash = market_fingerprint(market)
    midpoint, touch, adverse, half_spread, effective = _effective_non_fee_impact(
        scenario=scenario,
        side=intent.side,
        market=market,
    )
    research_non_fee = cost_model.half_spread_bps + cost_model.slippage_bps
    margin = effective - research_non_fee
    reason = _blocking_reason(
        scenario=scenario,
        market=market,
        has_execution_measurement=has_execution_measurement,
        broker_rejection_reason=broker_rejection_reason,
        continuity_margin_bps=margin,
    )
    status = ExecutionCostContinuityStatus.PASS if reason is None else ExecutionCostContinuityStatus.BLOCKED
    values = {
        "scenario_id": scenario.scenario_id,
        "scenario_hash": scenario.scenario_hash,
        "outcome_hash": outcome_hash,
        "market_fingerprint": market_hash,
        "symbol": intent.symbol,
        "side": intent.side,
        "midpoint": midpoint,
        "touch_price": touch,
        "modeled_adverse_price": adverse,
        "observed_half_spread_bps": half_spread,
        "scenario_slippage_bps": scenario.config.slippage_bps,
        "effective_non_fee_impact_bps": effective,
        "research_half_spread_bps": cost_model.half_spread_bps,
        "research_slippage_bps": cost_model.slippage_bps,
        "research_non_fee_impact_bps": research_non_fee,
        "continuity_margin_bps": margin,
        "status": status,
        "reason_code": reason or "NON_FEE_CONTINUITY_CONSERVATIVE",
    }
    return ExecutionCostContinuityObservation(
        **values,
        observation_hash=_hash(_observation_payload_from_values(values)),
    )


def _blocking_reason(
    *,
    scenario: PaperExecutionScenario,
    market: MarketSnapshot,
    has_execution_measurement: bool,
    broker_rejection_reason: str | None,
    continuity_margin_bps: Decimal,
) -> str | None:
    midpoint = (market.bid + market.ask) / Decimal("2")
    spread_bps = (market.ask - market.bid) / midpoint * BPS_DENOMINATOR
    if spread_bps > scenario.config.max_spread_bps:
        return "SPREAD_ABOVE_SCENARIO_BOUND"
    if broker_rejection_reason is not None:
        return broker_rejection_reason
    if not has_execution_measurement:
        return "W78_EXECUTION_MEASUREMENT_MISSING"
    if continuity_margin_bps < 0:
        return "EFFECTIVE_NON_FEE_IMPACT_BELOW_RESEARCH"
    return None


def _effective_non_fee_impact(
    *,
    scenario: PaperExecutionScenario,
    side: Side,
    market: MarketSnapshot,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    if market.bid <= 0 or market.ask <= 0:
        raise ExecutionCostContinuityIntegrityError("market prices must be positive")
    if market.ask < market.bid:
        raise ExecutionCostContinuityIntegrityError("market may not be crossed")
    midpoint = (market.bid + market.ask) / Decimal("2")
    touch = market.ask if side is Side.BUY else market.bid
    adverse = touch * (
        Decimal("1") + side.sign * scenario.config.slippage_bps / BPS_DENOMINATOR
    )
    if adverse <= 0:
        raise ExecutionCostContinuityIntegrityError("modeled adverse price must be positive")
    observed_half_spread = side.sign * (touch - midpoint) / midpoint * BPS_DENOMINATOR
    effective = side.sign * (adverse - midpoint) / midpoint * BPS_DENOMINATOR
    if observed_half_spread < 0 or effective < 0:
        raise ExecutionCostContinuityIntegrityError("execution impact may not be favorable to midpoint")
    return midpoint, touch, adverse, observed_half_spread, effective


def _observation_payload(value: ExecutionCostContinuityObservation, *, include_hash: bool) -> dict[str, object]:
    payload = _observation_payload_from_values({
        "scenario_id": value.scenario_id,
        "scenario_hash": value.scenario_hash,
        "outcome_hash": value.outcome_hash,
        "market_fingerprint": value.market_fingerprint,
        "symbol": value.symbol,
        "side": value.side,
        "midpoint": value.midpoint,
        "touch_price": value.touch_price,
        "modeled_adverse_price": value.modeled_adverse_price,
        "observed_half_spread_bps": value.observed_half_spread_bps,
        "scenario_slippage_bps": value.scenario_slippage_bps,
        "effective_non_fee_impact_bps": value.effective_non_fee_impact_bps,
        "research_half_spread_bps": value.research_half_spread_bps,
        "research_slippage_bps": value.research_slippage_bps,
        "research_non_fee_impact_bps": value.research_non_fee_impact_bps,
        "continuity_margin_bps": value.continuity_margin_bps,
        "status": value.status,
        "reason_code": value.reason_code,
    })
    if include_hash:
        payload["observation_hash"] = value.observation_hash
    return payload


def _observation_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["side"] = _enum_value(payload["side"])
    payload["status"] = _enum_value(payload["status"])
    for key in (
        "midpoint",
        "touch_price",
        "modeled_adverse_price",
        "observed_half_spread_bps",
        "scenario_slippage_bps",
        "effective_non_fee_impact_bps",
        "research_half_spread_bps",
        "research_slippage_bps",
        "research_non_fee_impact_bps",
        "continuity_margin_bps",
    ):
        payload[key] = _decimal(payload[key])  # type: ignore[arg-type]
    return payload


def _evidence_payload(value: ExecutionCostContinuityEvidence, *, include_hash: bool) -> dict[str, object]:
    payload = _evidence_payload_from_values({
        "evidence_id": value.evidence_id,
        "contract_version": value.contract_version,
        "qualification_contract_hash": value.qualification_contract_hash,
        "research_cost_model_hash": value.research_cost_model_hash,
        "scenario_matrix_hash": value.scenario_matrix_hash,
        "sensitivity_measurement_hash": value.sensitivity_measurement_hash,
        "intent_fingerprint": value.intent_fingerprint,
        "market_fingerprint": value.market_fingerprint,
        "symbol": value.symbol,
        "side": value.side,
        "observed_at": value.observed_at,
        "assessed_at": value.assessed_at,
        "observations": value.observations,
        "status": value.status,
        "blocking_reasons": value.blocking_reasons,
        "resolved_promotion_blockers": value.resolved_promotion_blockers,
        "remaining_promotion_blockers": value.remaining_promotion_blockers,
        "fee_accounting_complete": value.fee_accounting_complete,
        "fee_accounting_state": value.fee_accounting_state,
        "paper_candidate_authorized": value.paper_candidate_authorized,
        "external_execution_authorized": value.external_execution_authorized,
        "capital_authority": value.capital_authority,
        "live_trading": value.live_trading,
    })
    if include_hash:
        payload["evidence_hash"] = value.evidence_hash
    return payload


def _evidence_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["side"] = _enum_value(payload["side"])
    payload["status"] = _enum_value(payload["status"])
    payload["observed_at"] = _utc_iso(payload["observed_at"])  # type: ignore[arg-type]
    payload["assessed_at"] = _utc_iso(payload["assessed_at"])  # type: ignore[arg-type]
    payload["observations"] = [item.to_dict() for item in payload["observations"]]  # type: ignore[union-attr]
    for key in ("blocking_reasons", "resolved_promotion_blockers", "remaining_promotion_blockers"):
        payload[key] = list(payload[key])  # type: ignore[arg-type]
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ExecutionCostContinuityIntegrityError(f"{label} must be canonical identifier")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ExecutionCostContinuityIntegrityError(f"{label} must be lowercase sha256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionCostContinuityIntegrityError(f"{label} must be timezone-aware datetime")


def _require_positive_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ExecutionCostContinuityIntegrityError(f"{label} must be finite positive Decimal")


def _require_non_negative_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ExecutionCostContinuityIntegrityError(f"{label} must be finite non-negative Decimal")


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _enum_value(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "CONTINUITY_BLOCKER",
    "CONTINUITY_CONTRACT_VERSION",
    "ExecutionCostContinuityError",
    "ExecutionCostContinuityEvidence",
    "ExecutionCostContinuityIntegrityError",
    "ExecutionCostContinuityObservation",
    "ExecutionCostContinuityStatus",
    "FEE_ACCOUNTING_STATE",
    "build_execution_cost_continuity_evidence",
]
