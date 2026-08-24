from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import OrderIntent, Side, intent_fingerprint
from autotrade.fee_accounting import (
    BPS_DENOMINATOR,
    FeeAccountingEvidence,
    FeeAccountingStatus,
    FeeEvidenceSource,
)
from autotrade.research.costs import ExecutionCostModel


FEE_PRODUCT_ECONOMICS_VERSION = "W82_FEE_PRODUCT_ECONOMICS_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")


class FeeProductEconomicsError(RuntimeError):
    pass


class FeeProductEconomicsIntegrityError(FeeProductEconomicsError):
    pass


class FeeChargeConvention(StrEnum):
    QUOTE_NOTIONAL_PERCENT = "QUOTE_NOTIONAL_PERCENT"
    RECEIVED_ASSET_PERCENT = "RECEIVED_ASSET_PERCENT"


class FeeLiquidityRole(StrEnum):
    FIXED = "FIXED"
    MAKER = "MAKER"
    TAKER = "TAKER"
    WORST_CASE = "WORST_CASE"


class FeeProductEconomicsStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class FeeProductPolicy:
    policy_id: str
    version: str
    product_id: str
    asset_class: str
    venue: str
    symbol: str
    base_currency: str
    quote_currency: str
    charge_convention: FeeChargeConvention
    liquidity_role: FeeLiquidityRole
    minimum_fee_bps: Decimal
    source_reference: str
    effective_at: datetime
    policy_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("policy_id", self.policy_id),
            ("product_id", self.product_id),
            ("asset_class", self.asset_class),
            ("venue", self.venue),
        ):
            _require_id(value, label)
        if self.version != FEE_PRODUCT_ECONOMICS_VERSION:
            raise FeeProductEconomicsIntegrityError("fee product policy version is not canonical W82")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise FeeProductEconomicsIntegrityError("symbol is required")
        _require_currency(self.base_currency, "base_currency")
        _require_currency(self.quote_currency, "quote_currency")
        if self.base_currency == self.quote_currency:
            raise FeeProductEconomicsIntegrityError("base and quote currencies must differ")
        if not isinstance(self.charge_convention, FeeChargeConvention):
            raise FeeProductEconomicsIntegrityError("charge_convention is invalid")
        if not isinstance(self.liquidity_role, FeeLiquidityRole):
            raise FeeProductEconomicsIntegrityError("liquidity_role is invalid")
        _require_non_negative_decimal(self.minimum_fee_bps, "minimum_fee_bps")
        if not isinstance(self.source_reference, str) or not self.source_reference.strip():
            raise FeeProductEconomicsIntegrityError("source_reference is required")
        _require_aware(self.effective_at, "effective_at")
        _require_hash(self.policy_hash, "policy_hash")
        if self.policy_hash != _hash(_policy_payload(self, include_hash=False)):
            raise FeeProductEconomicsIntegrityError("fee product policy hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _policy_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class FeeProductScenarioEconomics:
    scenario_id: str
    source_fee_observation_hash: str
    symbol: str
    side: Side
    gross_filled_quantity: Decimal
    execution_price: Decimal | None
    gross_quote_notional: Decimal
    research_fee_bps: Decimal
    required_minimum_fee_bps: Decimal
    charged_fee_currency: str
    charged_fee_amount: Decimal
    fee_quote_equivalent: Decimal
    net_base_quantity_delta: Decimal
    net_quote_cash_delta: Decimal
    status: FeeProductEconomicsStatus
    reason_code: str
    scenario_hash: str

    def __post_init__(self) -> None:
        _require_id(self.scenario_id, "scenario_id")
        _require_hash(self.source_fee_observation_hash, "source_fee_observation_hash")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise FeeProductEconomicsIntegrityError("symbol is required")
        if not isinstance(self.side, Side):
            raise FeeProductEconomicsIntegrityError("side must use canonical Side")
        _require_non_negative_decimal(self.gross_filled_quantity, "gross_filled_quantity")
        if self.execution_price is not None:
            _require_positive_decimal(self.execution_price, "execution_price")
        _require_non_negative_decimal(self.gross_quote_notional, "gross_quote_notional")
        _require_non_negative_decimal(self.research_fee_bps, "research_fee_bps")
        _require_non_negative_decimal(self.required_minimum_fee_bps, "required_minimum_fee_bps")
        _require_currency(self.charged_fee_currency, "charged_fee_currency")
        _require_non_negative_decimal(self.charged_fee_amount, "charged_fee_amount")
        _require_non_negative_decimal(self.fee_quote_equivalent, "fee_quote_equivalent")
        _require_finite_decimal(self.net_base_quantity_delta, "net_base_quantity_delta")
        _require_finite_decimal(self.net_quote_cash_delta, "net_quote_cash_delta")
        if not isinstance(self.status, FeeProductEconomicsStatus):
            raise FeeProductEconomicsIntegrityError("invalid product fee status")
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise FeeProductEconomicsIntegrityError("reason_code is required")
        expected_status = (
            FeeProductEconomicsStatus.PASS
            if self.research_fee_bps >= self.required_minimum_fee_bps
            else FeeProductEconomicsStatus.BLOCKED
        )
        if self.status is not expected_status:
            raise FeeProductEconomicsIntegrityError("product fee status does not match policy floor")
        if self.status is FeeProductEconomicsStatus.PASS and self.reason_code != "FEE_POLICY_CONSERVATIVE":
            raise FeeProductEconomicsIntegrityError("PASS product fee scenario reason is invalid")
        if self.status is FeeProductEconomicsStatus.BLOCKED and self.reason_code != "RESEARCH_FEE_BELOW_POLICY":
            raise FeeProductEconomicsIntegrityError("BLOCKED product fee scenario reason is invalid")
        if self.gross_filled_quantity == 0:
            if self.execution_price is not None:
                raise FeeProductEconomicsIntegrityError("zero fill may not carry execution price")
            if any(
                value != 0
                for value in (
                    self.gross_quote_notional,
                    self.charged_fee_amount,
                    self.fee_quote_equivalent,
                    self.net_base_quantity_delta,
                    self.net_quote_cash_delta,
                )
            ):
                raise FeeProductEconomicsIntegrityError("zero fill must have zero product economics")
        else:
            if self.execution_price is None:
                raise FeeProductEconomicsIntegrityError("filled product economics requires execution price")
            if self.gross_quote_notional != self.gross_filled_quantity * self.execution_price:
                raise FeeProductEconomicsIntegrityError("gross quote notional mismatch")
        _require_hash(self.scenario_hash, "scenario_hash")
        if self.scenario_hash != _hash(_scenario_payload(self, include_hash=False)):
            raise FeeProductEconomicsIntegrityError("product fee scenario hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _scenario_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class FeeProductEconomicsEvidence:
    evidence_id: str
    version: str
    fee_policy_hash: str
    fee_accounting_evidence_hash: str
    fee_contract_hash: str
    research_cost_model_hash: str
    w81_continuity_evidence_hash: str
    intent_fingerprint: str
    product_id: str
    asset_class: str
    venue: str
    symbol: str
    side: Side
    base_currency: str
    quote_currency: str
    charge_convention: FeeChargeConvention
    liquidity_role: FeeLiquidityRole
    research_fee_bps: Decimal
    required_minimum_fee_bps: Decimal
    market_observed_at: datetime
    assessed_at: datetime
    scenarios: tuple[FeeProductScenarioEconomics, ...]
    status: FeeProductEconomicsStatus
    reason_codes: tuple[str, ...]
    fee_schedule_conservative: bool
    product_fee_economics_complete: bool
    literal_broker_fee_semantics_modeled: bool
    broker_authoritative_fee_proven: bool
    realized_profitability_authorized: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    evidence_hash: str

    def __post_init__(self) -> None:
        _require_id(self.evidence_id, "evidence_id")
        if self.version != FEE_PRODUCT_ECONOMICS_VERSION:
            raise FeeProductEconomicsIntegrityError("product economics evidence version is not canonical W82")
        for label, value in (
            ("fee_policy_hash", self.fee_policy_hash),
            ("fee_accounting_evidence_hash", self.fee_accounting_evidence_hash),
            ("fee_contract_hash", self.fee_contract_hash),
            ("research_cost_model_hash", self.research_cost_model_hash),
            ("w81_continuity_evidence_hash", self.w81_continuity_evidence_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("evidence_hash", self.evidence_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("product_id", self.product_id),
            ("asset_class", self.asset_class),
            ("venue", self.venue),
        ):
            _require_id(value, label)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise FeeProductEconomicsIntegrityError("symbol is required")
        if not isinstance(self.side, Side):
            raise FeeProductEconomicsIntegrityError("side must use canonical Side")
        _require_currency(self.base_currency, "base_currency")
        _require_currency(self.quote_currency, "quote_currency")
        if not isinstance(self.charge_convention, FeeChargeConvention):
            raise FeeProductEconomicsIntegrityError("charge_convention is invalid")
        if not isinstance(self.liquidity_role, FeeLiquidityRole):
            raise FeeProductEconomicsIntegrityError("liquidity_role is invalid")
        _require_non_negative_decimal(self.research_fee_bps, "research_fee_bps")
        _require_non_negative_decimal(self.required_minimum_fee_bps, "required_minimum_fee_bps")
        _require_aware(self.market_observed_at, "market_observed_at")
        _require_aware(self.assessed_at, "assessed_at")
        if _utc(self.assessed_at) < _utc(self.market_observed_at):
            raise FeeProductEconomicsIntegrityError("product fee assessment may not predate market observation")
        if not self.scenarios:
            raise FeeProductEconomicsIntegrityError("product fee evidence requires scenarios")
        if self.scenarios != tuple(sorted(self.scenarios, key=lambda item: item.scenario_id)):
            raise FeeProductEconomicsIntegrityError("product fee scenarios must be sorted")
        if len({item.scenario_id for item in self.scenarios}) != len(self.scenarios):
            raise FeeProductEconomicsIntegrityError("duplicate product fee scenario")
        if any(item.symbol != self.symbol or item.side is not self.side for item in self.scenarios):
            raise FeeProductEconomicsIntegrityError("product fee scenario identity mismatch")
        expected_status = (
            FeeProductEconomicsStatus.PASS
            if all(item.status is FeeProductEconomicsStatus.PASS for item in self.scenarios)
            else FeeProductEconomicsStatus.BLOCKED
        )
        if self.status is not expected_status:
            raise FeeProductEconomicsIntegrityError("aggregate product fee status mismatch")
        expected_reasons = tuple(sorted({
            item.reason_code
            for item in self.scenarios
            if item.status is FeeProductEconomicsStatus.BLOCKED
        }))
        if self.reason_codes != expected_reasons:
            raise FeeProductEconomicsIntegrityError("aggregate product fee reasons mismatch")
        expected_pass = self.status is FeeProductEconomicsStatus.PASS
        if self.fee_schedule_conservative is not expected_pass:
            raise FeeProductEconomicsIntegrityError("fee schedule conservative flag mismatch")
        if self.product_fee_economics_complete is not expected_pass:
            raise FeeProductEconomicsIntegrityError("product fee completeness flag mismatch")
        if self.literal_broker_fee_semantics_modeled is not True:
            raise FeeProductEconomicsIntegrityError("W82B must model literal product fee charge semantics")
        if self.broker_authoritative_fee_proven is not False:
            raise FeeProductEconomicsIntegrityError("simulated product economics is not broker fee proof")
        if self.realized_profitability_authorized is not False:
            raise FeeProductEconomicsIntegrityError("W82B may not authorize realized profitability")
        if self.paper_candidate_authorized is not False or self.external_execution_authorized is not False:
            raise FeeProductEconomicsIntegrityError("W82B may not authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise FeeProductEconomicsIntegrityError("W82B may not grant capital or LIVE authority")
        if self.evidence_hash != _hash(_evidence_payload(self, include_hash=False)):
            raise FeeProductEconomicsIntegrityError("product fee evidence hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _evidence_payload(self, include_hash=True)


def build_fee_product_policy(
    *,
    policy_id: str,
    product_id: str,
    asset_class: str,
    venue: str,
    symbol: str,
    base_currency: str,
    quote_currency: str,
    charge_convention: FeeChargeConvention,
    liquidity_role: FeeLiquidityRole,
    minimum_fee_bps: Decimal,
    source_reference: str,
    effective_at: datetime,
) -> FeeProductPolicy:
    values = {
        "policy_id": policy_id,
        "version": FEE_PRODUCT_ECONOMICS_VERSION,
        "product_id": product_id,
        "asset_class": asset_class,
        "venue": venue,
        "symbol": symbol,
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "charge_convention": charge_convention,
        "liquidity_role": liquidity_role,
        "minimum_fee_bps": minimum_fee_bps,
        "source_reference": source_reference,
        "effective_at": effective_at,
    }
    return FeeProductPolicy(**values, policy_hash=_hash(_policy_payload_from_values(values)))


def build_fee_product_economics_evidence(
    *,
    evidence_id: str,
    policy: FeeProductPolicy,
    fee_evidence: FeeAccountingEvidence,
    cost_model: ExecutionCostModel,
    execution_intent: OrderIntent,
    assessed_at: datetime,
) -> FeeProductEconomicsEvidence:
    if not isinstance(policy, FeeProductPolicy):
        raise TypeError("policy must be FeeProductPolicy")
    if not isinstance(fee_evidence, FeeAccountingEvidence):
        raise TypeError("fee_evidence must be FeeAccountingEvidence")
    if not isinstance(cost_model, ExecutionCostModel):
        raise TypeError("cost_model must be ExecutionCostModel")
    if not isinstance(execution_intent, OrderIntent):
        raise TypeError("execution_intent must be OrderIntent")
    _require_aware(assessed_at, "assessed_at")
    if fee_evidence.status is not FeeAccountingStatus.COMPLETE or not fee_evidence.fee_accounting_complete:
        raise FeeProductEconomicsIntegrityError("base W82 fee accounting must be COMPLETE")
    if fee_evidence.source is not FeeEvidenceSource.SIMULATED_MODEL:
        raise FeeProductEconomicsIntegrityError("W82B currently accepts simulated W82 fee evidence only")
    if policy.product_id != fee_evidence.product_id:
        raise FeeProductEconomicsIntegrityError("fee product policy product mismatch")
    if policy.asset_class != fee_evidence.asset_class:
        raise FeeProductEconomicsIntegrityError("fee product policy asset-class mismatch")
    if policy.venue != fee_evidence.venue:
        raise FeeProductEconomicsIntegrityError("fee product policy venue mismatch")
    if policy.symbol != fee_evidence.symbol or policy.symbol != execution_intent.symbol:
        raise FeeProductEconomicsIntegrityError("fee product policy symbol mismatch")
    if policy.quote_currency != fee_evidence.fee_currency:
        raise FeeProductEconomicsIntegrityError("base W82 quote-equivalent currency differs from policy quote currency")
    if _utc(policy.effective_at) > _utc(fee_evidence.market_observed_at):
        raise FeeProductEconomicsIntegrityError("fee product policy was not effective at execution observation")
    if _utc(assessed_at) < _utc(fee_evidence.assessed_at):
        raise FeeProductEconomicsIntegrityError("product fee assessment may not predate base W82 evidence")
    intent_hash = intent_fingerprint(execution_intent)
    if fee_evidence.intent_fingerprint != intent_hash:
        raise FeeProductEconomicsIntegrityError("fee product economics intent fingerprint mismatch")
    cost_hash = _hash(cost_model.fingerprint_payload())
    if cost_hash != fee_evidence.research_cost_model_hash:
        raise FeeProductEconomicsIntegrityError("fee product economics cost model hash mismatch")

    scenarios: list[FeeProductScenarioEconomics] = []
    for observation in fee_evidence.observations:
        scenarios.append(_build_scenario(
            policy=policy,
            observation=observation,
            research_fee_bps=cost_model.fee_bps,
            side=execution_intent.side,
            symbol=execution_intent.symbol,
        ))
    ordered = tuple(sorted(scenarios, key=lambda item: item.scenario_id))
    status = (
        FeeProductEconomicsStatus.PASS
        if all(item.status is FeeProductEconomicsStatus.PASS for item in ordered)
        else FeeProductEconomicsStatus.BLOCKED
    )
    reasons = tuple(sorted({
        item.reason_code for item in ordered if item.status is FeeProductEconomicsStatus.BLOCKED
    }))
    values = {
        "evidence_id": evidence_id,
        "version": FEE_PRODUCT_ECONOMICS_VERSION,
        "fee_policy_hash": policy.policy_hash,
        "fee_accounting_evidence_hash": fee_evidence.evidence_hash,
        "fee_contract_hash": fee_evidence.fee_contract_hash,
        "research_cost_model_hash": fee_evidence.research_cost_model_hash,
        "w81_continuity_evidence_hash": fee_evidence.w81_continuity_evidence_hash,
        "intent_fingerprint": intent_hash,
        "product_id": policy.product_id,
        "asset_class": policy.asset_class,
        "venue": policy.venue,
        "symbol": policy.symbol,
        "side": execution_intent.side,
        "base_currency": policy.base_currency,
        "quote_currency": policy.quote_currency,
        "charge_convention": policy.charge_convention,
        "liquidity_role": policy.liquidity_role,
        "research_fee_bps": cost_model.fee_bps,
        "required_minimum_fee_bps": policy.minimum_fee_bps,
        "market_observed_at": fee_evidence.market_observed_at,
        "assessed_at": assessed_at,
        "scenarios": ordered,
        "status": status,
        "reason_codes": reasons,
        "fee_schedule_conservative": status is FeeProductEconomicsStatus.PASS,
        "product_fee_economics_complete": status is FeeProductEconomicsStatus.PASS,
        "literal_broker_fee_semantics_modeled": True,
        "broker_authoritative_fee_proven": False,
        "realized_profitability_authorized": False,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return FeeProductEconomicsEvidence(
        **values,
        evidence_hash=_hash(_evidence_payload_from_values(values)),
    )


def _build_scenario(*, policy, observation, research_fee_bps, side, symbol):
    filled = observation.filled_quantity
    price = observation.average_fill_price
    gross = observation.gross_notional
    rate = research_fee_bps / BPS_DENOMINATOR
    if filled == 0:
        charged_currency = policy.base_currency if (
            policy.charge_convention is FeeChargeConvention.RECEIVED_ASSET_PERCENT and side is Side.BUY
        ) else policy.quote_currency
        charged_amount = Decimal("0")
        quote_equivalent = Decimal("0")
        net_base = Decimal("0")
        net_quote = Decimal("0")
    else:
        if price is None:
            raise FeeProductEconomicsIntegrityError("filled base fee observation lacks execution price")
        if policy.charge_convention is FeeChargeConvention.RECEIVED_ASSET_PERCENT and side is Side.BUY:
            charged_currency = policy.base_currency
            charged_amount = filled * rate
            quote_equivalent = charged_amount * price
            net_base = filled - charged_amount
            net_quote = -gross
        else:
            charged_currency = policy.quote_currency
            charged_amount = gross * rate
            quote_equivalent = charged_amount
            net_base = filled if side is Side.BUY else -filled
            net_quote = -(gross + charged_amount) if side is Side.BUY else gross - charged_amount
        if quote_equivalent != observation.fee_amount:
            raise FeeProductEconomicsIntegrityError(
                "product-aware fee quote equivalent differs from base W82 fee accounting"
            )
    status = (
        FeeProductEconomicsStatus.PASS
        if research_fee_bps >= policy.minimum_fee_bps
        else FeeProductEconomicsStatus.BLOCKED
    )
    reason = "FEE_POLICY_CONSERVATIVE" if status is FeeProductEconomicsStatus.PASS else "RESEARCH_FEE_BELOW_POLICY"
    values = {
        "scenario_id": observation.scenario_id,
        "source_fee_observation_hash": observation.observation_hash,
        "symbol": symbol,
        "side": side,
        "gross_filled_quantity": filled,
        "execution_price": price,
        "gross_quote_notional": gross,
        "research_fee_bps": research_fee_bps,
        "required_minimum_fee_bps": policy.minimum_fee_bps,
        "charged_fee_currency": charged_currency,
        "charged_fee_amount": charged_amount,
        "fee_quote_equivalent": quote_equivalent,
        "net_base_quantity_delta": net_base,
        "net_quote_cash_delta": net_quote,
        "status": status,
        "reason_code": reason,
    }
    return FeeProductScenarioEconomics(
        **values,
        scenario_hash=_hash(_scenario_payload_from_values(values)),
    )


def _policy_payload(value: FeeProductPolicy, *, include_hash: bool) -> dict[str, object]:
    payload = _policy_payload_from_values({
        "policy_id": value.policy_id,
        "version": value.version,
        "product_id": value.product_id,
        "asset_class": value.asset_class,
        "venue": value.venue,
        "symbol": value.symbol,
        "base_currency": value.base_currency,
        "quote_currency": value.quote_currency,
        "charge_convention": value.charge_convention,
        "liquidity_role": value.liquidity_role,
        "minimum_fee_bps": value.minimum_fee_bps,
        "source_reference": value.source_reference,
        "effective_at": value.effective_at,
    })
    if include_hash:
        payload["policy_hash"] = value.policy_hash
    return payload


def _policy_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["charge_convention"] = _enum_value(payload["charge_convention"])
    payload["liquidity_role"] = _enum_value(payload["liquidity_role"])
    payload["minimum_fee_bps"] = _decimal(payload["minimum_fee_bps"])
    payload["effective_at"] = _utc_iso(payload["effective_at"])
    return payload


def _scenario_payload(value: FeeProductScenarioEconomics, *, include_hash: bool) -> dict[str, object]:
    payload = _scenario_payload_from_values({
        "scenario_id": value.scenario_id,
        "source_fee_observation_hash": value.source_fee_observation_hash,
        "symbol": value.symbol,
        "side": value.side,
        "gross_filled_quantity": value.gross_filled_quantity,
        "execution_price": value.execution_price,
        "gross_quote_notional": value.gross_quote_notional,
        "research_fee_bps": value.research_fee_bps,
        "required_minimum_fee_bps": value.required_minimum_fee_bps,
        "charged_fee_currency": value.charged_fee_currency,
        "charged_fee_amount": value.charged_fee_amount,
        "fee_quote_equivalent": value.fee_quote_equivalent,
        "net_base_quantity_delta": value.net_base_quantity_delta,
        "net_quote_cash_delta": value.net_quote_cash_delta,
        "status": value.status,
        "reason_code": value.reason_code,
    })
    if include_hash:
        payload["scenario_hash"] = value.scenario_hash
    return payload


def _scenario_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["side"] = _enum_value(payload["side"])
    payload["status"] = _enum_value(payload["status"])
    for key in (
        "gross_filled_quantity",
        "execution_price",
        "gross_quote_notional",
        "research_fee_bps",
        "required_minimum_fee_bps",
        "charged_fee_amount",
        "fee_quote_equivalent",
        "net_base_quantity_delta",
        "net_quote_cash_delta",
    ):
        value = payload[key]
        payload[key] = None if value is None else _decimal(value)
    return payload


def _evidence_payload(value: FeeProductEconomicsEvidence, *, include_hash: bool) -> dict[str, object]:
    payload = _evidence_payload_from_values({
        "evidence_id": value.evidence_id,
        "version": value.version,
        "fee_policy_hash": value.fee_policy_hash,
        "fee_accounting_evidence_hash": value.fee_accounting_evidence_hash,
        "fee_contract_hash": value.fee_contract_hash,
        "research_cost_model_hash": value.research_cost_model_hash,
        "w81_continuity_evidence_hash": value.w81_continuity_evidence_hash,
        "intent_fingerprint": value.intent_fingerprint,
        "product_id": value.product_id,
        "asset_class": value.asset_class,
        "venue": value.venue,
        "symbol": value.symbol,
        "side": value.side,
        "base_currency": value.base_currency,
        "quote_currency": value.quote_currency,
        "charge_convention": value.charge_convention,
        "liquidity_role": value.liquidity_role,
        "research_fee_bps": value.research_fee_bps,
        "required_minimum_fee_bps": value.required_minimum_fee_bps,
        "market_observed_at": value.market_observed_at,
        "assessed_at": value.assessed_at,
        "scenarios": value.scenarios,
        "status": value.status,
        "reason_codes": value.reason_codes,
        "fee_schedule_conservative": value.fee_schedule_conservative,
        "product_fee_economics_complete": value.product_fee_economics_complete,
        "literal_broker_fee_semantics_modeled": value.literal_broker_fee_semantics_modeled,
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
    payload["charge_convention"] = _enum_value(payload["charge_convention"])
    payload["liquidity_role"] = _enum_value(payload["liquidity_role"])
    payload["status"] = _enum_value(payload["status"])
    for key in ("research_fee_bps", "required_minimum_fee_bps"):
        payload[key] = _decimal(payload[key])
    for key in ("market_observed_at", "assessed_at"):
        payload[key] = _utc_iso(payload[key])
    payload["scenarios"] = [item.to_dict() for item in payload["scenarios"]]
    payload["reason_codes"] = list(payload["reason_codes"])
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise FeeProductEconomicsIntegrityError(f"{label} must be canonical identifier")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise FeeProductEconomicsIntegrityError(f"{label} must be lowercase sha256")


def _require_currency(value: str, label: str) -> None:
    if not isinstance(value, str) or not _CURRENCY_RE.fullmatch(value):
        raise FeeProductEconomicsIntegrityError(f"{label} must be canonical uppercase currency code")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FeeProductEconomicsIntegrityError(f"{label} must be timezone-aware datetime")


def _require_finite_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise FeeProductEconomicsIntegrityError(f"{label} must be finite Decimal")


def _require_non_negative_decimal(value: Decimal, label: str) -> None:
    _require_finite_decimal(value, label)
    if value < 0:
        raise FeeProductEconomicsIntegrityError(f"{label} must be >= 0")


def _require_positive_decimal(value: Decimal, label: str) -> None:
    _require_finite_decimal(value, label)
    if value <= 0:
        raise FeeProductEconomicsIntegrityError(f"{label} must be > 0")


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
    "FEE_PRODUCT_ECONOMICS_VERSION",
    "FeeChargeConvention",
    "FeeLiquidityRole",
    "FeeProductEconomicsError",
    "FeeProductEconomicsEvidence",
    "FeeProductEconomicsIntegrityError",
    "FeeProductEconomicsStatus",
    "FeeProductPolicy",
    "FeeProductScenarioEconomics",
    "build_fee_product_economics_evidence",
    "build_fee_product_policy",
]
