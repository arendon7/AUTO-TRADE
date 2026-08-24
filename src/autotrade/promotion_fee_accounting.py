from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import OrderIntent, Side, intent_fingerprint
from autotrade.execution_cost_continuity import FEE_ACCOUNTING_BLOCKER
from autotrade.fee_accounting import FeeAccountingEvidence, FeeAccountingStatus
from autotrade.fee_product_economics import (
    FeeChargeConvention,
    FeeLiquidityRole,
    FeeProductEconomicsEvidence,
    FeeProductEconomicsStatus,
)
from autotrade.fee_schedule_attestation import (
    FeeScheduleAttestation,
    FeeScheduleAttestationIntegrityError,
)
from autotrade.promotion_cost_continuity import (
    PromotionCostContinuityResolution,
    PromotionCostContinuityStatus,
)


RESOLUTION_CONTRACT_VERSION = "W82_PROMOTION_FEE_ACCOUNTING_RESOLUTION_V3"
STRATEGY_VERSION_BLOCKER = "EXECUTION_STRATEGY_VERSION_UNBOUND"
SHADOW_FORWARD_BLOCKER = "SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED"
MAX_PERCENT_FEE_BPS = Decimal("10000")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PromotionFeeAccountingError(RuntimeError):
    pass


class PromotionFeeAccountingIntegrityError(PromotionFeeAccountingError):
    pass


class PromotionFeeAccountingStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class PromotionFeeAccountingResolution:
    resolution_id: str
    contract_version: str
    w81_resolution_id: str
    w81_resolution_hash: str
    promotion_assessment_id: str
    promotion_assessment_hash: str
    promotion_policy_id: str
    promotion_policy_hash: str
    selected_strategy_id: str
    selected_strategy_version: str
    continuity_evidence_hash: str
    continuity_measurement_hash: str
    fee_accounting_evidence_hash: str
    fee_contract_hash: str
    fee_product_economics_hash: str
    fee_policy_hash: str
    fee_schedule_attestation_hash: str
    documented_fee_floor_bps: Decimal
    fee_schedule_source_checked_at: datetime
    intent_fingerprint: str
    w81_resolved_at: datetime
    fee_assessed_at: datetime
    fee_product_assessed_at: datetime
    status: PromotionFeeAccountingStatus
    reason_codes: tuple[str, ...]
    resolved_promotion_blockers: tuple[str, ...]
    remaining_promotion_blockers: tuple[str, ...]
    fee_accounting_complete: bool
    fee_schedule_conservative: bool
    product_fee_economics_complete: bool
    documented_fee_floor_satisfied: bool
    broker_authoritative_fee_proven: bool
    realized_profitability_authorized: bool
    strategy_version_execution_bound: bool
    shadow_forward_promotion_bound: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    resolved_at: datetime
    resolution_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("resolution_id", self.resolution_id),
            ("w81_resolution_id", self.w81_resolution_id),
            ("promotion_assessment_id", self.promotion_assessment_id),
            ("promotion_policy_id", self.promotion_policy_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            _require_id(value, label)
        if self.contract_version != RESOLUTION_CONTRACT_VERSION:
            raise PromotionFeeAccountingIntegrityError(
                "resolution contract version is not canonical W82"
            )
        for label, value in (
            ("w81_resolution_hash", self.w81_resolution_hash),
            ("promotion_assessment_hash", self.promotion_assessment_hash),
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("continuity_evidence_hash", self.continuity_evidence_hash),
            ("continuity_measurement_hash", self.continuity_measurement_hash),
            ("fee_accounting_evidence_hash", self.fee_accounting_evidence_hash),
            ("fee_contract_hash", self.fee_contract_hash),
            ("fee_product_economics_hash", self.fee_product_economics_hash),
            ("fee_policy_hash", self.fee_policy_hash),
            ("fee_schedule_attestation_hash", self.fee_schedule_attestation_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("resolution_hash", self.resolution_hash),
        ):
            _require_hash(value, label)
        _require_non_negative_decimal(
            self.documented_fee_floor_bps, "documented_fee_floor_bps"
        )
        for label, value in (
            ("fee_schedule_source_checked_at", self.fee_schedule_source_checked_at),
            ("w81_resolved_at", self.w81_resolved_at),
            ("fee_assessed_at", self.fee_assessed_at),
            ("fee_product_assessed_at", self.fee_product_assessed_at),
            ("resolved_at", self.resolved_at),
        ):
            _require_aware(value, label)
        if _utc(self.resolved_at) < _utc(self.fee_schedule_source_checked_at):
            raise PromotionFeeAccountingIntegrityError(
                "W82 resolution may not predate fee schedule source verification"
            )
        if _utc(self.resolved_at) < _utc(self.w81_resolved_at):
            raise PromotionFeeAccountingIntegrityError(
                "W82 resolution may not predate W81 resolution"
            )
        if _utc(self.resolved_at) < _utc(self.fee_assessed_at):
            raise PromotionFeeAccountingIntegrityError(
                "W82 resolution may not predate fee evidence"
            )
        if _utc(self.resolved_at) < _utc(self.fee_product_assessed_at):
            raise PromotionFeeAccountingIntegrityError(
                "W82 resolution may not predate product fee evidence"
            )
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise PromotionFeeAccountingIntegrityError(
                "resolution reason codes must be unique sorted"
            )
        if any(
            not isinstance(value, str) or not value.strip()
            for value in self.reason_codes
        ):
            raise PromotionFeeAccountingIntegrityError(
                "resolution reason code is invalid"
            )
        expected_resolved = (
            (FEE_ACCOUNTING_BLOCKER,)
            if self.status is PromotionFeeAccountingStatus.PASS
            else ()
        )
        if self.resolved_promotion_blockers != expected_resolved:
            raise PromotionFeeAccountingIntegrityError(
                "resolved fee blocker set is inconsistent"
            )
        if self.status is PromotionFeeAccountingStatus.PASS:
            if self.reason_codes:
                raise PromotionFeeAccountingIntegrityError(
                    "PASS W82 resolution may not carry failure reasons"
                )
            if FEE_ACCOUNTING_BLOCKER in self.remaining_promotion_blockers:
                raise PromotionFeeAccountingIntegrityError(
                    "PASS W82 resolution may not retain fee blocker"
                )
            if self.fee_accounting_complete is not True:
                raise PromotionFeeAccountingIntegrityError(
                    "PASS W82 resolution requires complete fee accounting"
                )
            if self.fee_schedule_conservative is not True:
                raise PromotionFeeAccountingIntegrityError(
                    "PASS W82 resolution requires conservative fee schedule"
                )
            if self.product_fee_economics_complete is not True:
                raise PromotionFeeAccountingIntegrityError(
                    "PASS W82 resolution requires product-aware fee economics"
                )
            if self.documented_fee_floor_satisfied is not True:
                raise PromotionFeeAccountingIntegrityError(
                    "PASS W82 resolution requires documented broker fee floor"
                )
        else:
            if not self.reason_codes:
                raise PromotionFeeAccountingIntegrityError(
                    "BLOCKED W82 resolution requires reason code"
                )
            if FEE_ACCOUNTING_BLOCKER not in self.remaining_promotion_blockers:
                raise PromotionFeeAccountingIntegrityError(
                    "BLOCKED W82 resolution must retain fee blocker"
                )
            if self.fee_accounting_complete is not False:
                raise PromotionFeeAccountingIntegrityError(
                    "BLOCKED W82 resolution cannot claim complete fee accounting"
                )
        if STRATEGY_VERSION_BLOCKER not in self.remaining_promotion_blockers:
            raise PromotionFeeAccountingIntegrityError(
                "strategy-version blocker must remain open"
            )
        if SHADOW_FORWARD_BLOCKER not in self.remaining_promotion_blockers:
            raise PromotionFeeAccountingIntegrityError(
                "shadow/forward blocker must remain open"
            )
        if self.strategy_version_execution_bound is not False:
            raise PromotionFeeAccountingIntegrityError(
                "W82 may not claim strategy-version execution binding"
            )
        if self.shadow_forward_promotion_bound is not False:
            raise PromotionFeeAccountingIntegrityError(
                "W82 may not claim shadow/forward promotion binding"
            )
        if self.broker_authoritative_fee_proven is not False:
            raise PromotionFeeAccountingIntegrityError(
                "W82 simulated resolution is not broker-authoritative fee proof"
            )
        if self.realized_profitability_authorized is not False:
            raise PromotionFeeAccountingIntegrityError(
                "W82 may not authorize realized-profitability claims"
            )
        if (
            self.paper_candidate_authorized is not False
            or self.external_execution_authorized is not False
        ):
            raise PromotionFeeAccountingIntegrityError(
                "W82 may not authorize PAPER candidate or external execution"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise PromotionFeeAccountingIntegrityError(
                "W82 may not grant capital or LIVE authority"
            )
        if self.resolution_hash != _hash(_payload(self, include_hash=False)):
            raise PromotionFeeAccountingIntegrityError(
                "promotion fee accounting resolution hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def resolve_promotion_fee_accounting(
    *,
    resolution_id: str,
    w81_resolution: PromotionCostContinuityResolution,
    fee_evidence: FeeAccountingEvidence,
    product_economics: FeeProductEconomicsEvidence,
    fee_schedule_attestation: FeeScheduleAttestation,
    execution_intent: OrderIntent,
    resolved_at: datetime,
) -> PromotionFeeAccountingResolution:
    """Resolve only the fee blocker for the exact W81-qualified candidate.

    W82 requires three independent scientific layers: exact simulated fee
    arithmetic, literal product fee mechanics, and a versioned documented broker
    fee-floor attestation. The Alpaca crypto baseline assumes unknown/Tier-1
    volume and no maker guarantee, therefore 25 bps until a future certified
    evidence path proves a lower tier/role. None of these receipts grants broker
    execution authority or proves realized profitability.
    """

    _require_id(resolution_id, "resolution_id")
    if not isinstance(w81_resolution, PromotionCostContinuityResolution):
        raise TypeError(
            "w81_resolution must be PromotionCostContinuityResolution"
        )
    if not isinstance(fee_evidence, FeeAccountingEvidence):
        raise TypeError("fee_evidence must be FeeAccountingEvidence")
    if not isinstance(product_economics, FeeProductEconomicsEvidence):
        raise TypeError(
            "product_economics must be FeeProductEconomicsEvidence"
        )
    if not isinstance(fee_schedule_attestation, FeeScheduleAttestation):
        raise TypeError(
            "fee_schedule_attestation must be FeeScheduleAttestation"
        )
    if not isinstance(execution_intent, OrderIntent):
        raise TypeError("execution_intent must be OrderIntent")
    _require_aware(resolved_at, "resolved_at")
    if _utc(resolved_at) < _utc(w81_resolution.resolved_at):
        raise PromotionFeeAccountingIntegrityError(
            "W82 resolution may not predate W81 resolution"
        )
    if _utc(resolved_at) < _utc(fee_evidence.assessed_at):
        raise PromotionFeeAccountingIntegrityError(
            "W82 resolution may not predate fee evidence"
        )
    if _utc(resolved_at) < _utc(product_economics.assessed_at):
        raise PromotionFeeAccountingIntegrityError(
            "W82 resolution may not predate product fee evidence"
        )

    intent_hash = intent_fingerprint(execution_intent)
    if w81_resolution.intent_fingerprint != intent_hash:
        raise PromotionFeeAccountingIntegrityError(
            "W81 resolution/intent fingerprint mismatch"
        )
    if fee_evidence.intent_fingerprint != intent_hash:
        raise PromotionFeeAccountingIntegrityError(
            "fee evidence/intent fingerprint mismatch"
        )
    if product_economics.intent_fingerprint != intent_hash:
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence/intent fingerprint mismatch"
        )
    if execution_intent.strategy_id != w81_resolution.selected_strategy_id:
        raise PromotionFeeAccountingIntegrityError(
            "execution strategy differs from frozen candidate"
        )
    if FEE_ACCOUNTING_BLOCKER not in w81_resolution.remaining_promotion_blockers:
        raise PromotionFeeAccountingIntegrityError(
            "W81 resolution does not contain fee blocker"
        )

    if product_economics.fee_accounting_evidence_hash != fee_evidence.evidence_hash:
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence is not bound to base W82 fee evidence"
        )
    if product_economics.fee_contract_hash != fee_evidence.fee_contract_hash:
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence contract binding mismatch"
        )
    if product_economics.w81_continuity_evidence_hash != w81_resolution.continuity_evidence_hash:
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence is not bound to W81 continuity"
        )
    if product_economics.research_cost_model_hash != fee_evidence.research_cost_model_hash:
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence cost-model binding mismatch"
        )
    if product_economics.product_id != fee_evidence.product_id:
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence product binding mismatch"
        )
    if product_economics.asset_class != fee_evidence.asset_class:
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence asset-class binding mismatch"
        )
    if product_economics.venue != fee_evidence.venue:
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence venue binding mismatch"
        )
    if (
        product_economics.symbol != fee_evidence.symbol
        or fee_evidence.symbol != execution_intent.symbol
    ):
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence symbol binding mismatch"
        )
    if (
        product_economics.side is not fee_evidence.side
        or fee_evidence.side is not execution_intent.side
    ):
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence side binding mismatch"
        )
    if _utc(product_economics.market_observed_at) != _utc(
        fee_evidence.market_observed_at
    ):
        raise PromotionFeeAccountingIntegrityError(
            "product fee evidence market-time binding mismatch"
        )

    try:
        fee_schedule_attestation.validate_for(
            product_id=product_economics.product_id,
            asset_class=product_economics.asset_class,
            venue=product_economics.venue,
            symbol=product_economics.symbol,
            at=resolved_at,
        )
    except FeeScheduleAttestationIntegrityError as exc:
        raise PromotionFeeAccountingIntegrityError(str(exc)) from exc

    if (
        product_economics.charge_convention
        is not FeeChargeConvention.RECEIVED_ASSET_PERCENT
    ):
        raise PromotionFeeAccountingIntegrityError(
            "Alpaca fee schedule requires received-asset fee convention"
        )
    if product_economics.liquidity_role is not FeeLiquidityRole.WORST_CASE:
        raise PromotionFeeAccountingIntegrityError(
            "Alpaca fee schedule requires worst-case liquidity role"
        )

    if (
        product_economics.research_fee_bps > MAX_PERCENT_FEE_BPS
        or product_economics.required_minimum_fee_bps > MAX_PERCENT_FEE_BPS
    ):
        raise PromotionFeeAccountingIntegrityError(
            "percent fee may not exceed 100% at promotion boundary"
        )
    if execution_intent.side is Side.BUY:
        if any(
            item.net_base_quantity_delta < 0
            or item.net_quote_cash_delta > 0
            for item in product_economics.scenarios
        ):
            raise PromotionFeeAccountingIntegrityError(
                "BUY product fee economics have impossible net direction"
            )
    else:
        if any(
            item.net_base_quantity_delta > 0
            or item.net_quote_cash_delta < 0
            for item in product_economics.scenarios
        ):
            raise PromotionFeeAccountingIntegrityError(
                "SELL product fee economics have impossible net direction"
            )
    if (
        product_economics.charge_convention
        is FeeChargeConvention.RECEIVED_ASSET_PERCENT
        and execution_intent.side is Side.BUY
        and any(
            item.charged_fee_currency != product_economics.base_currency
            for item in product_economics.scenarios
        )
    ):
        raise PromotionFeeAccountingIntegrityError(
            "received-asset BUY fee currency binding mismatch"
        )

    documented_floor = fee_schedule_attestation.required_fee_floor_bps
    documented_fee_floor_satisfied = (
        product_economics.research_fee_bps >= documented_floor
        and product_economics.required_minimum_fee_bps >= documented_floor
    )

    reasons: list[str] = []
    if w81_resolution.status is not PromotionCostContinuityStatus.PASS:
        reasons.append("W81_CONTINUITY_RESOLUTION_NOT_PASS")
    if (
        fee_evidence.status is not FeeAccountingStatus.COMPLETE
        or not fee_evidence.fee_accounting_complete
    ):
        reasons.append("FEE_ACCOUNTING_EVIDENCE_NOT_COMPLETE")
    if fee_evidence.w81_continuity_evidence_hash != w81_resolution.continuity_evidence_hash:
        reasons.append("FEE_EVIDENCE_NOT_BOUND_TO_W81_CONTINUITY")
    if fee_evidence.sensitivity_measurement_hash != w81_resolution.continuity_measurement_hash:
        reasons.append("FEE_EVIDENCE_NOT_BOUND_TO_W81_MEASUREMENT")
    if product_economics.status is not FeeProductEconomicsStatus.PASS:
        reasons.append("FEE_PRODUCT_ECONOMICS_NOT_PASS")
    if not product_economics.fee_schedule_conservative:
        reasons.append("FEE_SCHEDULE_NOT_CONSERVATIVE")
    if not product_economics.product_fee_economics_complete:
        reasons.append("PRODUCT_FEE_ECONOMICS_INCOMPLETE")
    if product_economics.research_fee_bps < documented_floor:
        reasons.append("RESEARCH_FEE_BELOW_DOCUMENTED_BROKER_FLOOR")
    if product_economics.required_minimum_fee_bps < documented_floor:
        reasons.append("PRODUCT_POLICY_BELOW_DOCUMENTED_BROKER_FLOOR")

    status = (
        PromotionFeeAccountingStatus.PASS
        if not reasons
        else PromotionFeeAccountingStatus.BLOCKED
    )
    resolved = (
        (FEE_ACCOUNTING_BLOCKER,)
        if status is PromotionFeeAccountingStatus.PASS
        else ()
    )
    remaining = tuple(
        sorted(set(w81_resolution.remaining_promotion_blockers) - set(resolved))
    )
    values = {
        "resolution_id": resolution_id,
        "contract_version": RESOLUTION_CONTRACT_VERSION,
        "w81_resolution_id": w81_resolution.resolution_id,
        "w81_resolution_hash": w81_resolution.resolution_hash,
        "promotion_assessment_id": w81_resolution.promotion_assessment_id,
        "promotion_assessment_hash": w81_resolution.promotion_assessment_hash,
        "promotion_policy_id": w81_resolution.promotion_policy_id,
        "promotion_policy_hash": w81_resolution.promotion_policy_hash,
        "selected_strategy_id": w81_resolution.selected_strategy_id,
        "selected_strategy_version": w81_resolution.selected_strategy_version,
        "continuity_evidence_hash": w81_resolution.continuity_evidence_hash,
        "continuity_measurement_hash": w81_resolution.continuity_measurement_hash,
        "fee_accounting_evidence_hash": fee_evidence.evidence_hash,
        "fee_contract_hash": fee_evidence.fee_contract_hash,
        "fee_product_economics_hash": product_economics.evidence_hash,
        "fee_policy_hash": product_economics.fee_policy_hash,
        "fee_schedule_attestation_hash": fee_schedule_attestation.attestation_hash,
        "documented_fee_floor_bps": documented_floor,
        "fee_schedule_source_checked_at": fee_schedule_attestation.source_checked_at,
        "intent_fingerprint": intent_hash,
        "w81_resolved_at": w81_resolution.resolved_at,
        "fee_assessed_at": fee_evidence.assessed_at,
        "fee_product_assessed_at": product_economics.assessed_at,
        "status": status,
        "reason_codes": tuple(sorted(reasons)),
        "resolved_promotion_blockers": resolved,
        "remaining_promotion_blockers": remaining,
        "fee_accounting_complete": status is PromotionFeeAccountingStatus.PASS,
        "fee_schedule_conservative": product_economics.fee_schedule_conservative,
        "product_fee_economics_complete": product_economics.product_fee_economics_complete,
        "documented_fee_floor_satisfied": documented_fee_floor_satisfied,
        "broker_authoritative_fee_proven": False,
        "realized_profitability_authorized": False,
        "strategy_version_execution_bound": False,
        "shadow_forward_promotion_bound": False,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "resolved_at": resolved_at,
    }
    return PromotionFeeAccountingResolution(
        **values,
        resolution_hash=_hash(_payload_from_values(values)),
    )


def _payload(
    value: PromotionFeeAccountingResolution, *, include_hash: bool
) -> dict[str, object]:
    payload = _payload_from_values(
        {
            "resolution_id": value.resolution_id,
            "contract_version": value.contract_version,
            "w81_resolution_id": value.w81_resolution_id,
            "w81_resolution_hash": value.w81_resolution_hash,
            "promotion_assessment_id": value.promotion_assessment_id,
            "promotion_assessment_hash": value.promotion_assessment_hash,
            "promotion_policy_id": value.promotion_policy_id,
            "promotion_policy_hash": value.promotion_policy_hash,
            "selected_strategy_id": value.selected_strategy_id,
            "selected_strategy_version": value.selected_strategy_version,
            "continuity_evidence_hash": value.continuity_evidence_hash,
            "continuity_measurement_hash": value.continuity_measurement_hash,
            "fee_accounting_evidence_hash": value.fee_accounting_evidence_hash,
            "fee_contract_hash": value.fee_contract_hash,
            "fee_product_economics_hash": value.fee_product_economics_hash,
            "fee_policy_hash": value.fee_policy_hash,
            "fee_schedule_attestation_hash": value.fee_schedule_attestation_hash,
            "documented_fee_floor_bps": value.documented_fee_floor_bps,
            "fee_schedule_source_checked_at": value.fee_schedule_source_checked_at,
            "intent_fingerprint": value.intent_fingerprint,
            "w81_resolved_at": value.w81_resolved_at,
            "fee_assessed_at": value.fee_assessed_at,
            "fee_product_assessed_at": value.fee_product_assessed_at,
            "status": value.status,
            "reason_codes": value.reason_codes,
            "resolved_promotion_blockers": value.resolved_promotion_blockers,
            "remaining_promotion_blockers": value.remaining_promotion_blockers,
            "fee_accounting_complete": value.fee_accounting_complete,
            "fee_schedule_conservative": value.fee_schedule_conservative,
            "product_fee_economics_complete": value.product_fee_economics_complete,
            "documented_fee_floor_satisfied": value.documented_fee_floor_satisfied,
            "broker_authoritative_fee_proven": value.broker_authoritative_fee_proven,
            "realized_profitability_authorized": value.realized_profitability_authorized,
            "strategy_version_execution_bound": value.strategy_version_execution_bound,
            "shadow_forward_promotion_bound": value.shadow_forward_promotion_bound,
            "paper_candidate_authorized": value.paper_candidate_authorized,
            "external_execution_authorized": value.external_execution_authorized,
            "capital_authority": value.capital_authority,
            "live_trading": value.live_trading,
            "resolved_at": value.resolved_at,
        }
    )
    if include_hash:
        payload["resolution_hash"] = value.resolution_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["status"] = _enum_value(payload["status"])
    payload["reason_codes"] = list(payload["reason_codes"])
    payload["resolved_promotion_blockers"] = list(
        payload["resolved_promotion_blockers"]
    )
    payload["remaining_promotion_blockers"] = list(
        payload["remaining_promotion_blockers"]
    )
    payload["documented_fee_floor_bps"] = _decimal(
        payload["documented_fee_floor_bps"]
    )
    for key in (
        "fee_schedule_source_checked_at",
        "w81_resolved_at",
        "fee_assessed_at",
        "fee_product_assessed_at",
        "resolved_at",
    ):
        payload[key] = _utc_iso(payload[key])
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PromotionFeeAccountingIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PromotionFeeAccountingIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PromotionFeeAccountingIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _require_non_negative_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise PromotionFeeAccountingIntegrityError(
            f"{label} must be finite Decimal >= 0"
        )


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
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "MAX_PERCENT_FEE_BPS",
    "PromotionFeeAccountingError",
    "PromotionFeeAccountingIntegrityError",
    "PromotionFeeAccountingResolution",
    "PromotionFeeAccountingStatus",
    "RESOLUTION_CONTRACT_VERSION",
    "SHADOW_FORWARD_BLOCKER",
    "STRATEGY_VERSION_BLOCKER",
    "resolve_promotion_fee_accounting",
]
