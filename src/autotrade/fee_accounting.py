from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import MarketSnapshot, OrderIntent, Side, intent_fingerprint, market_fingerprint
from autotrade.execution_cost_continuity import (
    ExecutionCostContinuityEvidence,
    ExecutionCostContinuityStatus,
    FEE_ACCOUNTING_BLOCKER,
)
from autotrade.paper_execution_lab import PaperExecutionSensitivityReport
from autotrade.paper_execution_qualification import PaperExecutionQualificationContract
from autotrade.paper_execution_scenarios import PaperExecutionScenarioMatrix
from autotrade.research.costs import ExecutionCostModel


BPS_DENOMINATOR = Decimal("10000")
FEE_ACCOUNTING_CONTRACT_VERSION = "W82_FEE_ACCOUNTING_V1"
FEE_ACCOUNTING_SCOPE = "SIMULATED_QUALIFICATION_ONLY"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")


class FeeAccountingError(RuntimeError):
    pass


class FeeAccountingIntegrityError(FeeAccountingError):
    pass


class FeeAccountingSourceUnavailable(FeeAccountingError):
    pass


class FeeEvidenceSource(StrEnum):
    SIMULATED_MODEL = "SIMULATED_MODEL"
    BROKER_AUTHORITATIVE = "BROKER_AUTHORITATIVE"


class FeeBasis(StrEnum):
    FILLED_NOTIONAL_QUOTE = "FILLED_NOTIONAL_QUOTE"


class FeeAccountingStatus(StrEnum):
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class FeeAccountingContract:
    contract_id: str
    contract_version: str
    research_cost_model_hash: str
    research_fee_bps: Decimal
    qualification_contract_hash: str
    scenario_matrix_hash: str
    product_id: str
    asset_class: str
    venue: str
    settlement_currency: str
    fee_currency: str
    fee_basis: FeeBasis
    source: FeeEvidenceSource
    accounting_scope: str
    broker_authoritative_supported: bool
    created_at: datetime
    contract_hash: str

    def __post_init__(self) -> None:
        _require_id(self.contract_id, "contract_id")
        if self.contract_version != FEE_ACCOUNTING_CONTRACT_VERSION:
            raise FeeAccountingIntegrityError("fee contract version is not canonical W82")
        for label, value in (
            ("research_cost_model_hash", self.research_cost_model_hash),
            ("qualification_contract_hash", self.qualification_contract_hash),
            ("scenario_matrix_hash", self.scenario_matrix_hash),
            ("contract_hash", self.contract_hash),
        ):
            _require_hash(value, label)
        _require_non_negative_decimal(self.research_fee_bps, "research_fee_bps")
        for label, value in (
            ("product_id", self.product_id),
            ("asset_class", self.asset_class),
            ("venue", self.venue),
        ):
            _require_id(value, label)
        _require_currency(self.settlement_currency, "settlement_currency")
        _require_currency(self.fee_currency, "fee_currency")
        if self.fee_basis is not FeeBasis.FILLED_NOTIONAL_QUOTE:
            raise FeeAccountingIntegrityError("W82 supports only filled-notional quote-currency fees")
        if self.fee_currency != self.settlement_currency:
            raise FeeAccountingIntegrityError("quote-notional fee must use settlement currency")
        if self.source is not FeeEvidenceSource.SIMULATED_MODEL:
            raise FeeAccountingIntegrityError("W82 v1 contract may not impersonate broker-authoritative fees")
        if self.accounting_scope != FEE_ACCOUNTING_SCOPE:
            raise FeeAccountingIntegrityError("W82 fee accounting scope is invalid")
        if self.broker_authoritative_supported is not False:
            raise FeeAccountingIntegrityError("W82 has no broker-authoritative fee adapter")
        _require_aware(self.created_at, "created_at")
        if self.contract_hash != _hash(_contract_payload(self, include_hash=False)):
            raise FeeAccountingIntegrityError("fee accounting contract hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _contract_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class FeeAccountingObservation:
    scenario_id: str
    scenario_hash: str
    outcome_hash: str
    measurement_hash: str
    w81_observation_hash: str
    symbol: str
    side: Side
    order_status: str
    fill_ratio: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    gross_notional: Decimal
    fee_bps: Decimal
    fee_amount: Decimal
    gross_quote_cash_delta: Decimal
    net_quote_cash_delta: Decimal
    fee_currency: str
    fee_basis: FeeBasis
    source: FeeEvidenceSource
    broker_authoritative: bool
    non_fee_components_counted_as_fee: bool
    reason_code: str
    observation_hash: str

    def __post_init__(self) -> None:
        _require_id(self.scenario_id, "scenario_id")
        for label, value in (
            ("scenario_hash", self.scenario_hash),
            ("outcome_hash", self.outcome_hash),
            ("measurement_hash", self.measurement_hash),
            ("w81_observation_hash", self.w81_observation_hash),
            ("observation_hash", self.observation_hash),
        ):
            _require_hash(value, label)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise FeeAccountingIntegrityError("symbol is required")
        if not isinstance(self.side, Side):
            raise FeeAccountingIntegrityError("side must use canonical Side")
        if not isinstance(self.order_status, str) or not self.order_status.strip():
            raise FeeAccountingIntegrityError("order_status is required")
        for label, value in (
            ("fill_ratio", self.fill_ratio),
            ("filled_quantity", self.filled_quantity),
            ("gross_notional", self.gross_notional),
            ("fee_bps", self.fee_bps),
            ("fee_amount", self.fee_amount),
        ):
            _require_non_negative_decimal(value, label)
        if self.fill_ratio > 1:
            raise FeeAccountingIntegrityError("fill_ratio may not exceed one")
        if self.filled_quantity == 0:
            if self.average_fill_price is not None:
                raise FeeAccountingIntegrityError("zero-fill fee observation may not claim average fill price")
            if any(value != 0 for value in (self.gross_notional, self.fee_amount, self.gross_quote_cash_delta, self.net_quote_cash_delta)):
                raise FeeAccountingIntegrityError("zero-fill fee observation must have zero economics")
            if self.reason_code != "NO_FILL_NO_FEE":
                raise FeeAccountingIntegrityError("zero-fill fee observation requires NO_FILL_NO_FEE")
        else:
            _require_positive_decimal(self.average_fill_price, "average_fill_price")
            expected_gross = self.filled_quantity * self.average_fill_price  # type: ignore[operator]
            if self.gross_notional != expected_gross:
                raise FeeAccountingIntegrityError("gross notional is inconsistent")
            expected_fee = self.gross_notional * self.fee_bps / BPS_DENOMINATOR
            if self.fee_amount != expected_fee:
                raise FeeAccountingIntegrityError("fee amount is inconsistent with research fee basis")
            expected_gross_delta = -self.side.sign * self.gross_notional
            if self.gross_quote_cash_delta != expected_gross_delta:
                raise FeeAccountingIntegrityError("gross quote cash delta is inconsistent")
            if self.net_quote_cash_delta != self.gross_quote_cash_delta - self.fee_amount:
                raise FeeAccountingIntegrityError("net quote cash delta is inconsistent")
            if self.reason_code != "SIMULATED_FEE_FROM_RESEARCH_MODEL":
                raise FeeAccountingIntegrityError("filled simulated fee observation reason is invalid")
        _require_currency(self.fee_currency, "fee_currency")
        if self.fee_basis is not FeeBasis.FILLED_NOTIONAL_QUOTE:
            raise FeeAccountingIntegrityError("fee observation basis is unsupported")
        if self.source is not FeeEvidenceSource.SIMULATED_MODEL or self.broker_authoritative is not False:
            raise FeeAccountingIntegrityError("simulated fee observation may not claim broker authority")
        if self.non_fee_components_counted_as_fee is not False:
            raise FeeAccountingIntegrityError("spread/slippage may not be double-counted as fee")
        if self.observation_hash != _hash(_observation_payload(self, include_hash=False)):
            raise FeeAccountingIntegrityError("fee observation hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _observation_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class FeeAccountingEvidence:
    evidence_id: str
    contract_version: str
    fee_contract_hash: str
    research_cost_model_hash: str
    qualification_contract_hash: str
    scenario_matrix_hash: str
    sensitivity_measurement_hash: str
    w81_continuity_evidence_hash: str
    intent_fingerprint: str
    market_fingerprint: str
    symbol: str
    side: Side
    product_id: str
    asset_class: str
    venue: str
    fee_currency: str
    fee_basis: FeeBasis
    source: FeeEvidenceSource
    accounting_scope: str
    market_observed_at: datetime
    assessed_at: datetime
    observations: tuple[FeeAccountingObservation, ...]
    total_gross_notional: Decimal
    total_fee_amount: Decimal
    aggregate_net_quote_cash_delta: Decimal
    status: FeeAccountingStatus
    reason_codes: tuple[str, ...]
    fee_accounting_complete: bool
    broker_authoritative_fee_proven: bool
    realized_profitability_authorized: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    evidence_hash: str

    def __post_init__(self) -> None:
        _require_id(self.evidence_id, "evidence_id")
        if self.contract_version != FEE_ACCOUNTING_CONTRACT_VERSION:
            raise FeeAccountingIntegrityError("fee evidence contract version is not canonical W82")
        for label, value in (
            ("fee_contract_hash", self.fee_contract_hash),
            ("research_cost_model_hash", self.research_cost_model_hash),
            ("qualification_contract_hash", self.qualification_contract_hash),
            ("scenario_matrix_hash", self.scenario_matrix_hash),
            ("sensitivity_measurement_hash", self.sensitivity_measurement_hash),
            ("w81_continuity_evidence_hash", self.w81_continuity_evidence_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("evidence_hash", self.evidence_hash),
        ):
            _require_hash(value, label)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise FeeAccountingIntegrityError("symbol is required")
        if not isinstance(self.side, Side):
            raise FeeAccountingIntegrityError("side must use canonical Side")
        for label, value in (("product_id", self.product_id), ("asset_class", self.asset_class), ("venue", self.venue)):
            _require_id(value, label)
        _require_currency(self.fee_currency, "fee_currency")
        if self.source is not FeeEvidenceSource.SIMULATED_MODEL:
            raise FeeAccountingIntegrityError("W82 evidence may not claim broker-authoritative source")
        if self.accounting_scope != FEE_ACCOUNTING_SCOPE:
            raise FeeAccountingIntegrityError("fee evidence scope is invalid")
        _require_aware(self.market_observed_at, "market_observed_at")
        _require_aware(self.assessed_at, "assessed_at")
        if _utc(self.assessed_at) < _utc(self.market_observed_at):
            raise FeeAccountingIntegrityError("fee assessment may not predate market observation")
        if not self.observations:
            raise FeeAccountingIntegrityError("fee evidence requires scenario observations")
        if self.observations != tuple(sorted(self.observations, key=lambda item: item.scenario_id)):
            raise FeeAccountingIntegrityError("fee observations must be sorted by scenario_id")
        if len({item.scenario_id for item in self.observations}) != len(self.observations):
            raise FeeAccountingIntegrityError("duplicate fee scenario observation")
        if any(item.symbol != self.symbol or item.side is not self.side for item in self.observations):
            raise FeeAccountingIntegrityError("fee observation execution identity mismatch")
        if any(item.fee_currency != self.fee_currency or item.fee_basis is not self.fee_basis for item in self.observations):
            raise FeeAccountingIntegrityError("fee observation currency/basis mismatch")
        expected_gross = sum((item.gross_notional for item in self.observations), Decimal("0"))
        expected_fee = sum((item.fee_amount for item in self.observations), Decimal("0"))
        expected_net = sum((item.net_quote_cash_delta for item in self.observations), Decimal("0"))
        if self.total_gross_notional != expected_gross or self.total_fee_amount != expected_fee or self.aggregate_net_quote_cash_delta != expected_net:
            raise FeeAccountingIntegrityError("aggregate fee economics are inconsistent")
        if self.status is not FeeAccountingStatus.COMPLETE or self.reason_codes:
            raise FeeAccountingIntegrityError("canonical W82 simulated evidence must be COMPLETE without failure reasons")
        if self.fee_accounting_complete is not True:
            raise FeeAccountingIntegrityError("COMPLETE W82 evidence must mark fee accounting complete")
        if self.broker_authoritative_fee_proven is not False:
            raise FeeAccountingIntegrityError("simulated completeness is not broker-authoritative fee proof")
        if self.realized_profitability_authorized is not False:
            raise FeeAccountingIntegrityError("W82 simulated evidence may not authorize realized-profitability claims")
        if self.paper_candidate_authorized is not False or self.external_execution_authorized is not False:
            raise FeeAccountingIntegrityError("W82 may not authorize PAPER candidate or external execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise FeeAccountingIntegrityError("W82 may not grant capital or LIVE authority")
        if self.evidence_hash != _hash(_evidence_payload(self, include_hash=False)):
            raise FeeAccountingIntegrityError("fee accounting evidence hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _evidence_payload(self, include_hash=True)


def build_simulated_fee_accounting_contract(
    *,
    contract_id: str,
    cost_model: ExecutionCostModel,
    qualification: PaperExecutionQualificationContract,
    matrix: PaperExecutionScenarioMatrix,
    product_id: str,
    asset_class: str,
    venue: str,
    settlement_currency: str,
    created_at: datetime,
) -> FeeAccountingContract:
    _require_id(contract_id, "contract_id")
    if not isinstance(cost_model, ExecutionCostModel):
        raise TypeError("cost_model must be ExecutionCostModel")
    if not isinstance(qualification, PaperExecutionQualificationContract):
        raise TypeError("qualification must be PaperExecutionQualificationContract")
    if not isinstance(matrix, PaperExecutionScenarioMatrix):
        raise TypeError("matrix must be PaperExecutionScenarioMatrix")
    _require_aware(created_at, "created_at")
    _require_currency(settlement_currency, "settlement_currency")
    cost_hash = _hash(cost_model.fingerprint_payload())
    if qualification.research_cost_model_hash != cost_hash:
        raise FeeAccountingIntegrityError("qualification/research cost model hash mismatch")
    if qualification.research_fee_bps != cost_model.fee_bps:
        raise FeeAccountingIntegrityError("qualification/research fee assumption mismatch")
    if qualification.scenario_matrix_hash != matrix.matrix_hash:
        raise FeeAccountingIntegrityError("qualification/scenario matrix hash mismatch")
    values = {
        "contract_id": contract_id,
        "contract_version": FEE_ACCOUNTING_CONTRACT_VERSION,
        "research_cost_model_hash": cost_hash,
        "research_fee_bps": cost_model.fee_bps,
        "qualification_contract_hash": qualification.contract_hash,
        "scenario_matrix_hash": matrix.matrix_hash,
        "product_id": product_id,
        "asset_class": asset_class,
        "venue": venue,
        "settlement_currency": settlement_currency,
        "fee_currency": settlement_currency,
        "fee_basis": FeeBasis.FILLED_NOTIONAL_QUOTE,
        "source": FeeEvidenceSource.SIMULATED_MODEL,
        "accounting_scope": FEE_ACCOUNTING_SCOPE,
        "broker_authoritative_supported": False,
        "created_at": created_at,
    }
    return FeeAccountingContract(**values, contract_hash=_hash(_contract_payload_from_values(values)))


def build_simulated_fee_accounting_evidence(
    *,
    evidence_id: str,
    contract: FeeAccountingContract,
    cost_model: ExecutionCostModel,
    qualification: PaperExecutionQualificationContract,
    matrix: PaperExecutionScenarioMatrix,
    sensitivity_report: PaperExecutionSensitivityReport,
    continuity: ExecutionCostContinuityEvidence,
    intent: OrderIntent,
    market: MarketSnapshot,
    assessed_at: datetime,
) -> FeeAccountingEvidence:
    _require_id(evidence_id, "evidence_id")
    if not isinstance(contract, FeeAccountingContract):
        raise TypeError("contract must be FeeAccountingContract")
    if not isinstance(cost_model, ExecutionCostModel):
        raise TypeError("cost_model must be ExecutionCostModel")
    if not isinstance(qualification, PaperExecutionQualificationContract):
        raise TypeError("qualification must be PaperExecutionQualificationContract")
    if not isinstance(matrix, PaperExecutionScenarioMatrix):
        raise TypeError("matrix must be PaperExecutionScenarioMatrix")
    if not isinstance(sensitivity_report, PaperExecutionSensitivityReport):
        raise TypeError("sensitivity_report must be PaperExecutionSensitivityReport")
    if not isinstance(continuity, ExecutionCostContinuityEvidence):
        raise TypeError("continuity must be ExecutionCostContinuityEvidence")
    if not isinstance(intent, OrderIntent) or not isinstance(market, MarketSnapshot):
        raise TypeError("intent and market must use canonical domain types")
    _require_aware(assessed_at, "assessed_at")

    cost_hash = _hash(cost_model.fingerprint_payload())
    if contract.research_cost_model_hash != cost_hash or qualification.research_cost_model_hash != cost_hash:
        raise FeeAccountingIntegrityError("fee contract/research cost model hash mismatch")
    if contract.research_fee_bps != cost_model.fee_bps or qualification.research_fee_bps != cost_model.fee_bps:
        raise FeeAccountingIntegrityError("fee schedule drift after contract creation")
    if contract.qualification_contract_hash != qualification.contract_hash:
        raise FeeAccountingIntegrityError("fee contract/qualification hash mismatch")
    if contract.scenario_matrix_hash != matrix.matrix_hash or qualification.scenario_matrix_hash != matrix.matrix_hash:
        raise FeeAccountingIntegrityError("fee contract/scenario matrix hash mismatch")
    if sensitivity_report.qualification_contract_hash != qualification.contract_hash:
        raise FeeAccountingIntegrityError("sensitivity/qualification hash mismatch")
    if sensitivity_report.scenario_matrix_hash != matrix.matrix_hash:
        raise FeeAccountingIntegrityError("sensitivity/scenario matrix hash mismatch")
    if continuity.status is not ExecutionCostContinuityStatus.PASS:
        raise FeeAccountingIntegrityError("W81 continuity must PASS before fee completeness")
    if FEE_ACCOUNTING_BLOCKER not in continuity.remaining_promotion_blockers:
        raise FeeAccountingIntegrityError("W81 continuity did not preserve fee blocker")
    if continuity.qualification_contract_hash != qualification.contract_hash:
        raise FeeAccountingIntegrityError("W81/qualification hash mismatch")
    if continuity.scenario_matrix_hash != matrix.matrix_hash:
        raise FeeAccountingIntegrityError("W81/scenario matrix hash mismatch")
    if continuity.sensitivity_measurement_hash != sensitivity_report.measurement_report_hash:
        raise FeeAccountingIntegrityError("W81/sensitivity measurement hash mismatch")

    intent_hash = intent_fingerprint(intent)
    market_hash = market_fingerprint(market)
    if sensitivity_report.intent_fingerprint != intent_hash or continuity.intent_fingerprint != intent_hash:
        raise FeeAccountingIntegrityError("fee accounting intent fingerprint mismatch")
    if sensitivity_report.market_fingerprint != market_hash or continuity.market_fingerprint != market_hash:
        raise FeeAccountingIntegrityError("fee accounting market fingerprint mismatch")
    if intent.symbol != market.symbol or continuity.symbol != intent.symbol or continuity.side is not intent.side:
        raise FeeAccountingIntegrityError("fee accounting execution identity mismatch")
    if _utc(assessed_at) < _utc(market.observed_at) or _utc(assessed_at) < _utc(continuity.assessed_at):
        raise FeeAccountingIntegrityError("fee assessment violates temporal causality")

    outcomes = {item.scenario_id: item for item in sensitivity_report.outcomes}
    w81 = {item.scenario_id: item for item in continuity.observations}
    scenario_ids = {item.scenario_id for item in matrix.scenarios}
    if set(outcomes) != scenario_ids or set(w81) != scenario_ids:
        raise FeeAccountingIntegrityError("fee accounting scenario universe mismatch")

    observations: list[FeeAccountingObservation] = []
    for scenario in matrix.scenarios:
        outcome = outcomes[scenario.scenario_id]
        continuity_observation = w81[scenario.scenario_id]
        if outcome.scenario_hash != scenario.scenario_hash or continuity_observation.scenario_hash != scenario.scenario_hash:
            raise FeeAccountingIntegrityError(f"scenario hash mismatch: {scenario.scenario_id}")
        if continuity_observation.outcome_hash != outcome.outcome_hash:
            raise FeeAccountingIntegrityError(f"W81 outcome hash mismatch: {scenario.scenario_id}")
        if outcome.measurement_hash is None or outcome.order_status is None or outcome.fill_ratio is None:
            raise FeeAccountingIntegrityError("W82 fee completeness requires W78 execution measurement for every scenario")
        filled_quantity = intent.quantity * outcome.fill_ratio
        average_fill_price: Decimal | None = None
        gross_notional = Decimal("0")
        fee_amount = Decimal("0")
        gross_delta = Decimal("0")
        net_delta = Decimal("0")
        reason = "NO_FILL_NO_FEE"
        if filled_quantity > 0:
            average_fill_price = continuity_observation.modeled_adverse_price
            gross_notional = filled_quantity * average_fill_price
            fee_amount = gross_notional * cost_model.fee_bps / BPS_DENOMINATOR
            gross_delta = -intent.side.sign * gross_notional
            net_delta = gross_delta - fee_amount
            reason = "SIMULATED_FEE_FROM_RESEARCH_MODEL"
        values = {
            "scenario_id": scenario.scenario_id,
            "scenario_hash": scenario.scenario_hash,
            "outcome_hash": outcome.outcome_hash,
            "measurement_hash": outcome.measurement_hash,
            "w81_observation_hash": continuity_observation.observation_hash,
            "symbol": intent.symbol,
            "side": intent.side,
            "order_status": outcome.order_status,
            "fill_ratio": outcome.fill_ratio,
            "filled_quantity": filled_quantity,
            "average_fill_price": average_fill_price,
            "gross_notional": gross_notional,
            "fee_bps": cost_model.fee_bps,
            "fee_amount": fee_amount,
            "gross_quote_cash_delta": gross_delta,
            "net_quote_cash_delta": net_delta,
            "fee_currency": contract.fee_currency,
            "fee_basis": contract.fee_basis,
            "source": FeeEvidenceSource.SIMULATED_MODEL,
            "broker_authoritative": False,
            "non_fee_components_counted_as_fee": False,
            "reason_code": reason,
        }
        observations.append(FeeAccountingObservation(
            **values,
            observation_hash=_hash(_observation_payload_from_values(values)),
        ))

    ordered = tuple(sorted(observations, key=lambda item: item.scenario_id))
    values = {
        "evidence_id": evidence_id,
        "contract_version": FEE_ACCOUNTING_CONTRACT_VERSION,
        "fee_contract_hash": contract.contract_hash,
        "research_cost_model_hash": cost_hash,
        "qualification_contract_hash": qualification.contract_hash,
        "scenario_matrix_hash": matrix.matrix_hash,
        "sensitivity_measurement_hash": sensitivity_report.measurement_report_hash,
        "w81_continuity_evidence_hash": continuity.evidence_hash,
        "intent_fingerprint": intent_hash,
        "market_fingerprint": market_hash,
        "symbol": intent.symbol,
        "side": intent.side,
        "product_id": contract.product_id,
        "asset_class": contract.asset_class,
        "venue": contract.venue,
        "fee_currency": contract.fee_currency,
        "fee_basis": contract.fee_basis,
        "source": FeeEvidenceSource.SIMULATED_MODEL,
        "accounting_scope": FEE_ACCOUNTING_SCOPE,
        "market_observed_at": market.observed_at,
        "assessed_at": assessed_at,
        "observations": ordered,
        "total_gross_notional": sum((item.gross_notional for item in ordered), Decimal("0")),
        "total_fee_amount": sum((item.fee_amount for item in ordered), Decimal("0")),
        "aggregate_net_quote_cash_delta": sum((item.net_quote_cash_delta for item in ordered), Decimal("0")),
        "status": FeeAccountingStatus.COMPLETE,
        "reason_codes": (),
        "fee_accounting_complete": True,
        "broker_authoritative_fee_proven": False,
        "realized_profitability_authorized": False,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return FeeAccountingEvidence(**values, evidence_hash=_hash(_evidence_payload_from_values(values)))


def build_broker_authoritative_fee_accounting_evidence(*_args: object, **_kwargs: object) -> FeeAccountingEvidence:
    """Fail closed until an audited broker source exposes explicit fee semantics.

    The first-canary gross-fill vs net-position delta is intentionally not accepted
    as fee evidence. A future adapter must be reviewed separately and bind a direct
    broker fee field/source response before this function can be implemented.
    """

    raise FeeAccountingSourceUnavailable(
        "BROKER_AUTHORITATIVE fee accounting is unsupported: no direct broker fee source is certified"
    )


def _contract_payload(value: FeeAccountingContract, *, include_hash: bool) -> dict[str, object]:
    payload = _contract_payload_from_values({
        "contract_id": value.contract_id,
        "contract_version": value.contract_version,
        "research_cost_model_hash": value.research_cost_model_hash,
        "research_fee_bps": value.research_fee_bps,
        "qualification_contract_hash": value.qualification_contract_hash,
        "scenario_matrix_hash": value.scenario_matrix_hash,
        "product_id": value.product_id,
        "asset_class": value.asset_class,
        "venue": value.venue,
        "settlement_currency": value.settlement_currency,
        "fee_currency": value.fee_currency,
        "fee_basis": value.fee_basis,
        "source": value.source,
        "accounting_scope": value.accounting_scope,
        "broker_authoritative_supported": value.broker_authoritative_supported,
        "created_at": value.created_at,
    })
    if include_hash:
        payload["contract_hash"] = value.contract_hash
    return payload


def _contract_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["research_fee_bps"] = _decimal(payload["research_fee_bps"])  # type: ignore[arg-type]
    payload["fee_basis"] = _enum_value(payload["fee_basis"])
    payload["source"] = _enum_value(payload["source"])
    payload["created_at"] = _utc_iso(payload["created_at"])  # type: ignore[arg-type]
    return payload


def _observation_payload(value: FeeAccountingObservation, *, include_hash: bool) -> dict[str, object]:
    payload = _observation_payload_from_values({
        "scenario_id": value.scenario_id,
        "scenario_hash": value.scenario_hash,
        "outcome_hash": value.outcome_hash,
        "measurement_hash": value.measurement_hash,
        "w81_observation_hash": value.w81_observation_hash,
        "symbol": value.symbol,
        "side": value.side,
        "order_status": value.order_status,
        "fill_ratio": value.fill_ratio,
        "filled_quantity": value.filled_quantity,
        "average_fill_price": value.average_fill_price,
        "gross_notional": value.gross_notional,
        "fee_bps": value.fee_bps,
        "fee_amount": value.fee_amount,
        "gross_quote_cash_delta": value.gross_quote_cash_delta,
        "net_quote_cash_delta": value.net_quote_cash_delta,
        "fee_currency": value.fee_currency,
        "fee_basis": value.fee_basis,
        "source": value.source,
        "broker_authoritative": value.broker_authoritative,
        "non_fee_components_counted_as_fee": value.non_fee_components_counted_as_fee,
        "reason_code": value.reason_code,
    })
    if include_hash:
        payload["observation_hash"] = value.observation_hash
    return payload


def _observation_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["side"] = _enum_value(payload["side"])
    payload["fee_basis"] = _enum_value(payload["fee_basis"])
    payload["source"] = _enum_value(payload["source"])
    for key in (
        "fill_ratio",
        "filled_quantity",
        "average_fill_price",
        "gross_notional",
        "fee_bps",
        "fee_amount",
        "gross_quote_cash_delta",
        "net_quote_cash_delta",
    ):
        raw = payload[key]
        payload[key] = None if raw is None else _decimal(raw)  # type: ignore[arg-type]
    return payload


def _evidence_payload(value: FeeAccountingEvidence, *, include_hash: bool) -> dict[str, object]:
    payload = _evidence_payload_from_values({
        "evidence_id": value.evidence_id,
        "contract_version": value.contract_version,
        "fee_contract_hash": value.fee_contract_hash,
        "research_cost_model_hash": value.research_cost_model_hash,
        "qualification_contract_hash": value.qualification_contract_hash,
        "scenario_matrix_hash": value.scenario_matrix_hash,
        "sensitivity_measurement_hash": value.sensitivity_measurement_hash,
        "w81_continuity_evidence_hash": value.w81_continuity_evidence_hash,
        "intent_fingerprint": value.intent_fingerprint,
        "market_fingerprint": value.market_fingerprint,
        "symbol": value.symbol,
        "side": value.side,
        "product_id": value.product_id,
        "asset_class": value.asset_class,
        "venue": value.venue,
        "fee_currency": value.fee_currency,
        "fee_basis": value.fee_basis,
        "source": value.source,
        "accounting_scope": value.accounting_scope,
        "market_observed_at": value.market_observed_at,
        "assessed_at": value.assessed_at,
        "observations": value.observations,
        "total_gross_notional": value.total_gross_notional,
        "total_fee_amount": value.total_fee_amount,
        "aggregate_net_quote_cash_delta": value.aggregate_net_quote_cash_delta,
        "status": value.status,
        "reason_codes": value.reason_codes,
        "fee_accounting_complete": value.fee_accounting_complete,
        "broker_authoritative_fee_proven": value.broker_authoritative_fee_proven,
        "realized_profitability_authorized": value.realized_profitability_authorized,
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
    payload["fee_basis"] = _enum_value(payload["fee_basis"])
    payload["source"] = _enum_value(payload["source"])
    payload["status"] = _enum_value(payload["status"])
    payload["reason_codes"] = list(payload["reason_codes"])  # type: ignore[arg-type]
    payload["observations"] = [item.to_dict() for item in payload["observations"]]  # type: ignore[union-attr]
    for key in ("total_gross_notional", "total_fee_amount", "aggregate_net_quote_cash_delta"):
        payload[key] = _decimal(payload[key])  # type: ignore[arg-type]
    for key in ("market_observed_at", "assessed_at"):
        payload[key] = _utc_iso(payload[key])  # type: ignore[arg-type]
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise FeeAccountingIntegrityError(f"{label} must be canonical identifier")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise FeeAccountingIntegrityError(f"{label} must be lowercase sha256")


def _require_currency(value: str, label: str) -> None:
    if not isinstance(value, str) or not _CURRENCY_RE.fullmatch(value):
        raise FeeAccountingIntegrityError(f"{label} must be canonical uppercase currency code")


def _require_non_negative_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise FeeAccountingIntegrityError(f"{label} must be finite Decimal >= 0")


def _require_positive_decimal(value: Decimal | None, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise FeeAccountingIntegrityError(f"{label} must be finite Decimal > 0")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FeeAccountingIntegrityError(f"{label} must be timezone-aware datetime")


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
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
    "FEE_ACCOUNTING_CONTRACT_VERSION",
    "FEE_ACCOUNTING_SCOPE",
    "FeeAccountingContract",
    "FeeAccountingError",
    "FeeAccountingEvidence",
    "FeeAccountingIntegrityError",
    "FeeAccountingObservation",
    "FeeAccountingSourceUnavailable",
    "FeeAccountingStatus",
    "FeeBasis",
    "FeeEvidenceSource",
    "build_broker_authoritative_fee_accounting_evidence",
    "build_simulated_fee_accounting_contract",
    "build_simulated_fee_accounting_evidence",
]
