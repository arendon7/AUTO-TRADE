from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import OrderIntent, OrderType, Side, intent_fingerprint
import autotrade.fee_product_economics as fee_product_module
from autotrade.fee_product_economics import FeeProductEconomicsEvidence
import autotrade.promotion_fee_accounting as fee_resolution_module
from autotrade.promotion_fee_accounting import (
    PromotionFeeAccountingResolution,
    PromotionFeeAccountingStatus,
    SHADOW_FORWARD_BLOCKER,
    STRATEGY_VERSION_BLOCKER,
)
from autotrade.research.dsl import StrategySpec
from autotrade.research.market import MarketDataset
from autotrade.research.strategy import ResearchSignal, StrategyContext
from autotrade.research.trials import TrialPhase, TrialSpec
import autotrade.strategy_lab_promotion as promotion_module
from autotrade.strategy_lab_promotion import StrategyPromotionPolicy


EXECUTION_STRATEGY_BINDING_VERSION = "W83_EXECUTION_STRATEGY_BINDING_V1"
RUNTIME_PROJECTION_CONTRACT_VERSION = "W83_DSL_SIGNAL_TO_EXISTING_MARKET_INTENT_V1"
_CODE_VERSION_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class ExecutionStrategyBindingError(RuntimeError):
    pass


class ExecutionStrategyBindingIntegrityError(ExecutionStrategyBindingError):
    pass


class ExecutionStrategyBindingStatus(StrEnum):
    PASS = "PASS"


@dataclass(frozen=True, slots=True)
class ExecutionStrategyBindingEvidence:
    binding_id: str
    contract_version: str
    runtime_projection_contract_version: str
    promotion_policy_id: str
    promotion_policy_hash: str
    development_campaign_id: str
    selected_trial_id: str
    selected_trial_fingerprint: str
    selected_strategy_id: str
    selected_strategy_version: str
    trial_code_version: str
    trial_dataset_hash: str
    trial_parameters_hash: str
    strategy_spec_hash: str
    strategy_kind: str
    dataset_hash: str
    dataset_symbol: str
    dataset_venue: str
    dataset_quote_currency: str
    fee_product_economics_hash: str
    w82_resolution_id: str
    w82_resolution_hash: str
    context_index: int
    context_fingerprint: str
    signal_id: str
    signal_fingerprint: str
    signal_generated_at: datetime
    signal_quantity_delta: Decimal
    derived_side: Side
    derived_quantity: Decimal
    semantic_projection_hash: str
    intent_fingerprint: str
    runtime_fingerprint: str
    status: ExecutionStrategyBindingStatus
    artifact_frozen_in_selected_trial: bool
    dataset_bound: bool
    intent_semantics_bound: bool
    strategy_version_binding_proven: bool
    shadow_forward_promotion_bound: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    assessed_at: datetime
    evidence_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("binding_id", self.binding_id),
            ("promotion_policy_id", self.promotion_policy_id),
            ("development_campaign_id", self.development_campaign_id),
            ("selected_trial_id", self.selected_trial_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
            ("strategy_kind", self.strategy_kind),
            ("dataset_symbol", self.dataset_symbol),
            ("dataset_venue", self.dataset_venue),
            ("dataset_quote_currency", self.dataset_quote_currency),
            ("w82_resolution_id", self.w82_resolution_id),
            ("signal_id", self.signal_id),
        ):
            _require_id(value, label)
        if self.contract_version != EXECUTION_STRATEGY_BINDING_VERSION:
            raise ExecutionStrategyBindingIntegrityError(
                "execution strategy binding version is not canonical W83"
            )
        if self.runtime_projection_contract_version != RUNTIME_PROJECTION_CONTRACT_VERSION:
            raise ExecutionStrategyBindingIntegrityError(
                "runtime projection contract version is not canonical W83"
            )
        for label, value in (
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("trial_dataset_hash", self.trial_dataset_hash),
            ("trial_parameters_hash", self.trial_parameters_hash),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("dataset_hash", self.dataset_hash),
            ("fee_product_economics_hash", self.fee_product_economics_hash),
            ("w82_resolution_hash", self.w82_resolution_hash),
            ("context_fingerprint", self.context_fingerprint),
            ("signal_fingerprint", self.signal_fingerprint),
            ("semantic_projection_hash", self.semantic_projection_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("runtime_fingerprint", self.runtime_fingerprint),
            ("evidence_hash", self.evidence_hash),
        ):
            _require_hash(value, label)
        if not _CODE_VERSION_RE.fullmatch(self.trial_code_version):
            raise ExecutionStrategyBindingIntegrityError(
                "trial_code_version must be immutable 40/64 lowercase hex identity"
            )
        if isinstance(self.context_index, bool) or not isinstance(self.context_index, int) or self.context_index < 0:
            raise ExecutionStrategyBindingIntegrityError(
                "context_index must be integer >= 0"
            )
        _require_aware(self.signal_generated_at, "signal_generated_at")
        _require_aware(self.assessed_at, "assessed_at")
        _require_finite_nonzero(self.signal_quantity_delta, "signal_quantity_delta")
        _require_positive(self.derived_quantity, "derived_quantity")
        if not isinstance(self.derived_side, Side):
            raise ExecutionStrategyBindingIntegrityError("derived_side must use Side")
        if self.status is not ExecutionStrategyBindingStatus.PASS:
            raise ExecutionStrategyBindingIntegrityError("W83 binding evidence must be PASS")
        for label, value in (
            ("artifact_frozen_in_selected_trial", self.artifact_frozen_in_selected_trial),
            ("dataset_bound", self.dataset_bound),
            ("intent_semantics_bound", self.intent_semantics_bound),
            ("strategy_version_binding_proven", self.strategy_version_binding_proven),
        ):
            if value is not True:
                raise ExecutionStrategyBindingIntegrityError(
                    f"PASS W83 evidence requires {label}=true"
                )
        if self.shadow_forward_promotion_bound is not False:
            raise ExecutionStrategyBindingIntegrityError(
                "W83 may not claim Shadow/Forward promotion binding"
            )
        if (
            self.paper_candidate_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
        ):
            raise ExecutionStrategyBindingIntegrityError(
                "W83 evidence may not authorize PAPER candidate or execution"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise ExecutionStrategyBindingIntegrityError(
                "W83 evidence may not grant capital or LIVE authority"
            )
        if _utc(self.assessed_at) < _utc(self.signal_generated_at):
            raise ExecutionStrategyBindingIntegrityError(
                "W83 assessment may not predate strategy signal"
            )
        if self.evidence_hash != _hash(_evidence_payload(self, include_hash=False)):
            raise ExecutionStrategyBindingIntegrityError(
                "execution strategy binding evidence hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _evidence_payload(self, include_hash=True)


def build_execution_strategy_binding_evidence(
    *,
    binding_id: str,
    promotion_policy: StrategyPromotionPolicy,
    selected_trial: TrialSpec,
    strategy_spec: StrategySpec,
    dataset: MarketDataset,
    fee_product_economics: FeeProductEconomicsEvidence,
    w82_resolution: PromotionFeeAccountingResolution,
    execution_intent: OrderIntent,
    context_index: int,
    current_position_quantity: Decimal,
    current_equity: Decimal,
    assessed_at: datetime,
) -> ExecutionStrategyBindingEvidence:
    """Prove exact selected-artifact/runtime semantics for an existing qualified intent.

    This function is a pure verifier. It never constructs an OrderIntent, never
    calls Safety/OMS/broker code, and grants no execution authority. The runtime
    contract is deliberately limited to deterministic DSL signal semantics plus
    a projection onto an already-existing MARKET intent whose full fingerprint
    was previously qualified by W82.
    """

    _require_id(binding_id, "binding_id")
    if not isinstance(promotion_policy, StrategyPromotionPolicy):
        raise TypeError("promotion_policy must be StrategyPromotionPolicy")
    if not isinstance(selected_trial, TrialSpec):
        raise TypeError("selected_trial must be TrialSpec")
    if not isinstance(strategy_spec, StrategySpec):
        raise TypeError("strategy_spec must be StrategySpec")
    if not isinstance(dataset, MarketDataset):
        raise TypeError("dataset must be MarketDataset")
    if not isinstance(fee_product_economics, FeeProductEconomicsEvidence):
        raise TypeError("fee_product_economics must be FeeProductEconomicsEvidence")
    if not isinstance(w82_resolution, PromotionFeeAccountingResolution):
        raise TypeError("w82_resolution must be PromotionFeeAccountingResolution")
    if not isinstance(execution_intent, OrderIntent):
        raise TypeError("execution_intent must be OrderIntent")
    _require_aware(assessed_at, "assessed_at")
    _require_finite(current_position_quantity, "current_position_quantity")
    _require_positive(current_equity, "current_equity")
    if isinstance(context_index, bool) or not isinstance(context_index, int):
        raise ExecutionStrategyBindingIntegrityError("context_index must be integer")
    if context_index < 0 or context_index >= len(dataset.bars):
        raise ExecutionStrategyBindingIntegrityError("context_index outside dataset")

    expected_policy_hash = promotion_module._hash(
        promotion_module._policy_payload(promotion_policy, include_hash=False)
    )
    if promotion_policy.policy_hash != expected_policy_hash:
        raise ExecutionStrategyBindingIntegrityError("promotion policy hash mismatch")
    expected_w82_hash = fee_resolution_module._hash(
        fee_resolution_module._payload(w82_resolution, include_hash=False)
    )
    if w82_resolution.resolution_hash != expected_w82_hash:
        raise ExecutionStrategyBindingIntegrityError("W82 resolution hash mismatch")
    expected_product_hash = fee_product_module._hash(
        fee_product_module._evidence_payload(
            fee_product_economics, include_hash=False
        )
    )
    if fee_product_economics.evidence_hash != expected_product_hash:
        raise ExecutionStrategyBindingIntegrityError(
            "fee product economics hash mismatch"
        )

    if w82_resolution.status is not PromotionFeeAccountingStatus.PASS:
        raise ExecutionStrategyBindingIntegrityError(
            "W83 requires PASS W82 fee-accounting resolution"
        )
    if not w82_resolution.fee_accounting_complete:
        raise ExecutionStrategyBindingIntegrityError(
            "W83 requires fee-complete W82 qualification"
        )
    if STRATEGY_VERSION_BLOCKER not in w82_resolution.remaining_promotion_blockers:
        raise ExecutionStrategyBindingIntegrityError(
            "W82 resolution does not retain strategy-version blocker"
        )
    if SHADOW_FORWARD_BLOCKER not in w82_resolution.remaining_promotion_blockers:
        raise ExecutionStrategyBindingIntegrityError(
            "W82 resolution does not retain Shadow/Forward blocker"
        )
    if (
        w82_resolution.paper_candidate_authorized
        or w82_resolution.external_execution_authorized
        or w82_resolution.capital_authority != "NONE"
        or w82_resolution.live_trading != "BLOCKED"
    ):
        raise ExecutionStrategyBindingIntegrityError(
            "W82 authority boundary is not fail-closed"
        )

    if w82_resolution.promotion_policy_id != promotion_policy.policy_id:
        raise ExecutionStrategyBindingIntegrityError(
            "W82/promotion policy id mismatch"
        )
    if w82_resolution.promotion_policy_hash != promotion_policy.policy_hash:
        raise ExecutionStrategyBindingIntegrityError(
            "W82/promotion policy hash mismatch"
        )
    if (
        w82_resolution.selected_strategy_id != promotion_policy.selected_strategy_id
        or w82_resolution.selected_strategy_version
        != promotion_policy.selected_strategy_version
    ):
        raise ExecutionStrategyBindingIntegrityError(
            "W82/promotion selected strategy mismatch"
        )

    if selected_trial.phase is not TrialPhase.DEVELOPMENT:
        raise ExecutionStrategyBindingIntegrityError(
            "selected trial must be DEVELOPMENT"
        )
    if selected_trial.holdout_authorization_id:
        raise ExecutionStrategyBindingIntegrityError(
            "selected DEVELOPMENT trial may not carry HOLDOUT authorization"
        )
    if selected_trial.campaign_id != promotion_policy.development_campaign_id:
        raise ExecutionStrategyBindingIntegrityError(
            "selected trial development campaign mismatch"
        )
    if selected_trial.trial_id != promotion_policy.selected_trial_id:
        raise ExecutionStrategyBindingIntegrityError("selected trial id mismatch")
    if selected_trial.fingerprint != promotion_policy.selected_trial_fingerprint:
        raise ExecutionStrategyBindingIntegrityError(
            "selected trial fingerprint mismatch"
        )
    if (
        selected_trial.strategy_id != promotion_policy.selected_strategy_id
        or selected_trial.strategy_version
        != promotion_policy.selected_strategy_version
    ):
        raise ExecutionStrategyBindingIntegrityError(
            "selected trial strategy identity mismatch"
        )
    if not _CODE_VERSION_RE.fullmatch(selected_trial.code_version):
        raise ExecutionStrategyBindingIntegrityError(
            "selected trial code_version is not immutable 40/64 lowercase hex identity"
        )

    if (
        strategy_spec.strategy_id != selected_trial.strategy_id
        or strategy_spec.strategy_version != selected_trial.strategy_version
    ):
        raise ExecutionStrategyBindingIntegrityError(
            "StrategySpec identity differs from selected trial"
        )
    runtime_strategy = strategy_spec.build()
    runtime_parameters = dict(runtime_strategy.parameters)
    if dict(selected_trial.parameters) != runtime_parameters:
        raise ExecutionStrategyBindingIntegrityError(
            "selected trial parameters do not exactly freeze StrategySpec runtime parameters"
        )
    if selected_trial.parameters.get("spec_hash") != strategy_spec.canonical_hash:
        raise ExecutionStrategyBindingIntegrityError(
            "selected trial does not freeze exact StrategySpec canonical hash"
        )

    if selected_trial.dataset_hash != dataset.dataset_hash:
        raise ExecutionStrategyBindingIntegrityError(
            "selected trial dataset hash mismatch"
        )
    if any(bar.symbol != dataset.instrument.symbol for bar in dataset.bars):
        raise ExecutionStrategyBindingIntegrityError(
            "dataset bar symbol drift detected"
        )
    if fee_product_economics.evidence_hash != w82_resolution.fee_product_economics_hash:
        raise ExecutionStrategyBindingIntegrityError(
            "W82 resolution/product economics hash mismatch"
        )
    full_intent_hash = intent_fingerprint(execution_intent)
    if full_intent_hash != w82_resolution.intent_fingerprint:
        raise ExecutionStrategyBindingIntegrityError(
            "W82 resolution/intent fingerprint mismatch"
        )
    if fee_product_economics.intent_fingerprint != full_intent_hash:
        raise ExecutionStrategyBindingIntegrityError(
            "fee product economics/intent fingerprint mismatch"
        )
    if fee_product_economics.symbol != dataset.instrument.symbol:
        raise ExecutionStrategyBindingIntegrityError(
            "fee product/dataset symbol mismatch"
        )
    if fee_product_economics.venue != dataset.instrument.venue:
        raise ExecutionStrategyBindingIntegrityError(
            "fee product/dataset venue mismatch"
        )
    if fee_product_economics.quote_currency != dataset.instrument.quote_currency:
        raise ExecutionStrategyBindingIntegrityError(
            "fee product/dataset quote currency mismatch"
        )

    context = StrategyContext(
        symbol=dataset.instrument.symbol,
        index=context_index,
        history=dataset.bars[: context_index + 1],
        current_position_quantity=current_position_quantity,
        current_equity=current_equity,
    )
    signal = runtime_strategy.on_bar(context)
    if signal is None:
        raise ExecutionStrategyBindingIntegrityError(
            "selected StrategySpec does not emit a signal for bound context"
        )
    _validate_signal(signal, context=context)
    if execution_intent.strategy_id != strategy_spec.strategy_id:
        raise ExecutionStrategyBindingIntegrityError(
            "execution intent strategy id differs from bound StrategySpec"
        )
    if execution_intent.symbol != signal.symbol:
        raise ExecutionStrategyBindingIntegrityError(
            "execution intent symbol differs from deterministic signal"
        )
    if execution_intent.order_type is not OrderType.MARKET:
        raise ExecutionStrategyBindingIntegrityError(
            "W83 runtime projection supports MARKET intent semantics only"
        )
    derived_side = Side.BUY if signal.quantity_delta > 0 else Side.SELL
    derived_quantity = abs(signal.quantity_delta)
    if execution_intent.side is not derived_side:
        raise ExecutionStrategyBindingIntegrityError(
            "execution intent side differs from deterministic signal"
        )
    if execution_intent.quantity != derived_quantity:
        raise ExecutionStrategyBindingIntegrityError(
            "execution intent quantity differs from deterministic signal delta"
        )
    if _utc(execution_intent.created_at) < _utc(signal.generated_at):
        raise ExecutionStrategyBindingIntegrityError(
            "execution intent may not predate deterministic signal"
        )
    if _utc(assessed_at) < _utc(w82_resolution.resolved_at):
        raise ExecutionStrategyBindingIntegrityError(
            "W83 assessment may not predate W82 resolution"
        )
    if _utc(assessed_at) < _utc(execution_intent.created_at):
        raise ExecutionStrategyBindingIntegrityError(
            "W83 assessment may not predate execution intent"
        )

    trial_parameters_hash = _hash(dict(selected_trial.parameters))
    context_fingerprint = _hash(
        {
            "dataset_hash": dataset.dataset_hash,
            "context_index": context_index,
            "current_position_quantity": _decimal(current_position_quantity),
            "current_equity": _decimal(current_equity),
            "current_bar_ended_at": _utc_iso(context.current_bar.ended_at),
        }
    )
    signal_fingerprint = _hash(_signal_payload(signal))
    semantic_projection_hash = _hash(
        {
            "runtime_projection_contract_version": RUNTIME_PROJECTION_CONTRACT_VERSION,
            "strategy_spec_hash": strategy_spec.canonical_hash,
            "context_fingerprint": context_fingerprint,
            "signal_fingerprint": signal_fingerprint,
            "strategy_id": execution_intent.strategy_id,
            "symbol": execution_intent.symbol,
            "side": execution_intent.side.value,
            "quantity": _decimal(execution_intent.quantity),
            "order_type": execution_intent.order_type.value,
        }
    )
    runtime_fingerprint = _hash(
        {
            "runtime_projection_contract_version": RUNTIME_PROJECTION_CONTRACT_VERSION,
            "trial_code_version": selected_trial.code_version,
            "selected_trial_fingerprint": selected_trial.fingerprint,
            "strategy_spec_hash": strategy_spec.canonical_hash,
            "trial_parameters_hash": trial_parameters_hash,
            "semantic_projection_hash": semantic_projection_hash,
        }
    )
    values = {
        "binding_id": binding_id,
        "contract_version": EXECUTION_STRATEGY_BINDING_VERSION,
        "runtime_projection_contract_version": RUNTIME_PROJECTION_CONTRACT_VERSION,
        "promotion_policy_id": promotion_policy.policy_id,
        "promotion_policy_hash": promotion_policy.policy_hash,
        "development_campaign_id": promotion_policy.development_campaign_id,
        "selected_trial_id": selected_trial.trial_id,
        "selected_trial_fingerprint": selected_trial.fingerprint,
        "selected_strategy_id": selected_trial.strategy_id,
        "selected_strategy_version": selected_trial.strategy_version,
        "trial_code_version": selected_trial.code_version,
        "trial_dataset_hash": selected_trial.dataset_hash,
        "trial_parameters_hash": trial_parameters_hash,
        "strategy_spec_hash": strategy_spec.canonical_hash,
        "strategy_kind": strategy_spec.kind,
        "dataset_hash": dataset.dataset_hash,
        "dataset_symbol": dataset.instrument.symbol,
        "dataset_venue": dataset.instrument.venue,
        "dataset_quote_currency": dataset.instrument.quote_currency,
        "fee_product_economics_hash": fee_product_economics.evidence_hash,
        "w82_resolution_id": w82_resolution.resolution_id,
        "w82_resolution_hash": w82_resolution.resolution_hash,
        "context_index": context_index,
        "context_fingerprint": context_fingerprint,
        "signal_id": signal.signal_id,
        "signal_fingerprint": signal_fingerprint,
        "signal_generated_at": signal.generated_at,
        "signal_quantity_delta": signal.quantity_delta,
        "derived_side": derived_side,
        "derived_quantity": derived_quantity,
        "semantic_projection_hash": semantic_projection_hash,
        "intent_fingerprint": full_intent_hash,
        "runtime_fingerprint": runtime_fingerprint,
        "status": ExecutionStrategyBindingStatus.PASS,
        "artifact_frozen_in_selected_trial": True,
        "dataset_bound": True,
        "intent_semantics_bound": True,
        "strategy_version_binding_proven": True,
        "shadow_forward_promotion_bound": False,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "assessed_at": assessed_at,
    }
    return ExecutionStrategyBindingEvidence(
        **values,
        evidence_hash=_hash(_evidence_payload_from_values(values)),
    )


def _validate_signal(signal: ResearchSignal, *, context: StrategyContext) -> None:
    if signal.symbol != context.symbol:
        raise ExecutionStrategyBindingIntegrityError(
            "deterministic signal/context symbol mismatch"
        )
    if _utc(signal.generated_at) != _utc(context.current_bar.ended_at):
        raise ExecutionStrategyBindingIntegrityError(
            "deterministic signal time must equal current bar end"
        )
    _require_finite_nonzero(signal.quantity_delta, "signal.quantity_delta")


def _signal_payload(signal: ResearchSignal) -> dict[str, object]:
    return {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "generated_at": _utc_iso(signal.generated_at),
        "quantity_delta": _decimal(signal.quantity_delta),
        "reason": signal.reason,
    }


def _evidence_payload(
    value: ExecutionStrategyBindingEvidence, *, include_hash: bool
) -> dict[str, object]:
    payload = _evidence_payload_from_values(
        {
            "binding_id": value.binding_id,
            "contract_version": value.contract_version,
            "runtime_projection_contract_version": value.runtime_projection_contract_version,
            "promotion_policy_id": value.promotion_policy_id,
            "promotion_policy_hash": value.promotion_policy_hash,
            "development_campaign_id": value.development_campaign_id,
            "selected_trial_id": value.selected_trial_id,
            "selected_trial_fingerprint": value.selected_trial_fingerprint,
            "selected_strategy_id": value.selected_strategy_id,
            "selected_strategy_version": value.selected_strategy_version,
            "trial_code_version": value.trial_code_version,
            "trial_dataset_hash": value.trial_dataset_hash,
            "trial_parameters_hash": value.trial_parameters_hash,
            "strategy_spec_hash": value.strategy_spec_hash,
            "strategy_kind": value.strategy_kind,
            "dataset_hash": value.dataset_hash,
            "dataset_symbol": value.dataset_symbol,
            "dataset_venue": value.dataset_venue,
            "dataset_quote_currency": value.dataset_quote_currency,
            "fee_product_economics_hash": value.fee_product_economics_hash,
            "w82_resolution_id": value.w82_resolution_id,
            "w82_resolution_hash": value.w82_resolution_hash,
            "context_index": value.context_index,
            "context_fingerprint": value.context_fingerprint,
            "signal_id": value.signal_id,
            "signal_fingerprint": value.signal_fingerprint,
            "signal_generated_at": value.signal_generated_at,
            "signal_quantity_delta": value.signal_quantity_delta,
            "derived_side": value.derived_side,
            "derived_quantity": value.derived_quantity,
            "semantic_projection_hash": value.semantic_projection_hash,
            "intent_fingerprint": value.intent_fingerprint,
            "runtime_fingerprint": value.runtime_fingerprint,
            "status": value.status,
            "artifact_frozen_in_selected_trial": value.artifact_frozen_in_selected_trial,
            "dataset_bound": value.dataset_bound,
            "intent_semantics_bound": value.intent_semantics_bound,
            "strategy_version_binding_proven": value.strategy_version_binding_proven,
            "shadow_forward_promotion_bound": value.shadow_forward_promotion_bound,
            "paper_candidate_authorized": value.paper_candidate_authorized,
            "external_execution_authorized": value.external_execution_authorized,
            "runtime_execution_authorized": value.runtime_execution_authorized,
            "capital_authority": value.capital_authority,
            "live_trading": value.live_trading,
            "assessed_at": value.assessed_at,
        }
    )
    if include_hash:
        payload["evidence_hash"] = value.evidence_hash
    return payload


def _evidence_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["signal_generated_at"] = _utc_iso(payload["signal_generated_at"])
    payload["signal_quantity_delta"] = _decimal(payload["signal_quantity_delta"])
    payload["derived_side"] = _enum(payload["derived_side"])
    payload["derived_quantity"] = _decimal(payload["derived_quantity"])
    payload["status"] = _enum(payload["status"])
    payload["assessed_at"] = _utc_iso(payload["assessed_at"])
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ExecutionStrategyBindingIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ExecutionStrategyBindingIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ExecutionStrategyBindingIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _require_finite(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ExecutionStrategyBindingIntegrityError(
            f"{label} must be finite Decimal"
        )


def _require_positive(value: Decimal, label: str) -> None:
    _require_finite(value, label)
    if value <= 0:
        raise ExecutionStrategyBindingIntegrityError(f"{label} must be > 0")


def _require_finite_nonzero(value: Decimal, label: str) -> None:
    _require_finite(value, label)
    if value == 0:
        raise ExecutionStrategyBindingIntegrityError(f"{label} must be non-zero")


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _utc_iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise ExecutionStrategyBindingIntegrityError("datetime value required")
    return _utc(value).isoformat()


def _decimal(value: object) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ExecutionStrategyBindingIntegrityError("finite Decimal required")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _enum(value: object) -> str:
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
    "EXECUTION_STRATEGY_BINDING_VERSION",
    "RUNTIME_PROJECTION_CONTRACT_VERSION",
    "ExecutionStrategyBindingError",
    "ExecutionStrategyBindingEvidence",
    "ExecutionStrategyBindingIntegrityError",
    "ExecutionStrategyBindingStatus",
    "build_execution_strategy_binding_evidence",
]
