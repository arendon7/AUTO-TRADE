from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import inspect
import json
from pathlib import Path
import re
import sys

from autotrade.domain import OrderIntent, Side, intent_fingerprint
from autotrade.promotion_fee_accounting import (
    PromotionFeeAccountingResolution,
    PromotionFeeAccountingStatus,
    SHADOW_FORWARD_BLOCKER,
    STRATEGY_VERSION_BLOCKER,
)
from autotrade.research import dsl as research_dsl
from autotrade.research.dsl import StrategySpec
from autotrade.research.market import Bar, MarketDataset
from autotrade.research.strategy import ResearchSignal, StrategyContext
from autotrade.research.trials import TrialPhase, TrialRecord, TrialSpec, TrialStatus
from autotrade.strategy_lab_promotion import StrategyPromotionPolicy


BINDING_CONTRACT_VERSION = "W83_EXECUTION_STRATEGY_VERSION_BINDING_V1"
RESOLUTION_CONTRACT_VERSION = "W83_PROMOTION_STRATEGY_VERSION_RESOLUTION_V1"
BINDING_SCOPE = "DETERMINISTIC_QUALIFICATION_ONLY"
DSL_KIND = "moving_average_cross"
_REQUIRED_TRIAL_PARAMETERS = frozenset(
    {
        "dsl_kind",
        "short_window",
        "long_window",
        "order_quantity",
        "position_mode",
        "initial_stop_pct",
    }
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class StrategyVersionBindingError(RuntimeError):
    pass


class StrategyVersionBindingIntegrityError(StrategyVersionBindingError):
    pass


class StrategyVersionBindingStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ExecutionStrategyVersionBindingEvidence:
    evidence_id: str
    contract_version: str
    binding_scope: str
    promotion_policy_id: str
    promotion_policy_hash: str
    selected_trial_id: str
    selected_trial_fingerprint: str
    selected_strategy_id: str
    selected_strategy_version: str
    trial_code_version: str
    runtime_code_version: str
    strategy_artifact_hash: str
    dataset_hash: str
    dataset_symbol: str
    dataset_venue: str
    context_hash: str
    signal_id: str | None
    signal_hash: str | None
    signal_generated_at: datetime | None
    derived_side: Side | None
    derived_quantity: Decimal | None
    w82_resolution_id: str
    w82_resolution_hash: str
    promotion_assessment_id: str
    promotion_assessment_hash: str
    intent_fingerprint: str
    intent_created_at: datetime
    status: StrategyVersionBindingStatus
    reason_codes: tuple[str, ...]
    strategy_version_execution_bound: bool
    shadow_forward_promotion_bound: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    assessed_at: datetime
    evidence_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("evidence_id", self.evidence_id),
            ("promotion_policy_id", self.promotion_policy_id),
            ("selected_trial_id", self.selected_trial_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
            ("w82_resolution_id", self.w82_resolution_id),
            ("promotion_assessment_id", self.promotion_assessment_id),
        ):
            _require_id(value, label)
        if self.contract_version != BINDING_CONTRACT_VERSION:
            raise StrategyVersionBindingIntegrityError(
                "binding contract version is not canonical W83"
            )
        if self.binding_scope != BINDING_SCOPE:
            raise StrategyVersionBindingIntegrityError(
                "W83 binding scope is not deterministic qualification"
            )
        for label, value in (
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("strategy_artifact_hash", self.strategy_artifact_hash),
            ("dataset_hash", self.dataset_hash),
            ("context_hash", self.context_hash),
            ("w82_resolution_hash", self.w82_resolution_hash),
            ("promotion_assessment_hash", self.promotion_assessment_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("evidence_hash", self.evidence_hash),
        ):
            _require_hash(value, label)
        if not self.trial_code_version.strip() or not self.runtime_code_version.strip():
            raise StrategyVersionBindingIntegrityError(
                "trial/runtime code versions are required"
            )
        if self.trial_code_version != self.runtime_code_version:
            raise StrategyVersionBindingIntegrityError(
                "trial code version must equal loaded deterministic runtime version"
            )
        if not self.dataset_symbol.strip() or not self.dataset_venue.strip():
            raise StrategyVersionBindingIntegrityError(
                "dataset symbol and venue are required"
            )
        _require_aware(self.intent_created_at, "intent_created_at")
        _require_aware(self.assessed_at, "assessed_at")
        if _utc(self.assessed_at) < _utc(self.intent_created_at):
            raise StrategyVersionBindingIntegrityError(
                "W83 assessment may not predate the bound intent"
            )
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise StrategyVersionBindingIntegrityError(
                "binding reason codes must be unique sorted"
            )
        if any(
            not isinstance(value, str) or not value.strip()
            for value in self.reason_codes
        ):
            raise StrategyVersionBindingIntegrityError(
                "binding reason code is invalid"
            )
        if self.signal_id is None:
            if (
                self.signal_hash is not None
                or self.signal_generated_at is not None
                or self.derived_side is not None
                or self.derived_quantity is not None
            ):
                raise StrategyVersionBindingIntegrityError(
                    "absent signal must not carry derived signal fields"
                )
        else:
            if not self.signal_id.strip():
                raise StrategyVersionBindingIntegrityError("signal_id cannot be blank")
            if self.signal_hash is None:
                raise StrategyVersionBindingIntegrityError(
                    "present signal requires signal hash"
                )
            _require_hash(self.signal_hash, "signal_hash")
            if self.signal_generated_at is None:
                raise StrategyVersionBindingIntegrityError(
                    "present signal requires generated timestamp"
                )
            _require_aware(self.signal_generated_at, "signal_generated_at")
            if self.derived_side not in (Side.BUY, Side.SELL):
                raise StrategyVersionBindingIntegrityError(
                    "present signal requires BUY/SELL derived side"
                )
            if self.derived_quantity is None:
                raise StrategyVersionBindingIntegrityError(
                    "present signal requires derived quantity"
                )
            _require_positive_decimal(self.derived_quantity, "derived_quantity")
        if self.status is StrategyVersionBindingStatus.PASS:
            if self.reason_codes:
                raise StrategyVersionBindingIntegrityError(
                    "PASS W83 evidence may not carry failure reasons"
                )
            if self.signal_id is None:
                raise StrategyVersionBindingIntegrityError(
                    "PASS W83 evidence requires deterministic signal"
                )
            if self.strategy_version_execution_bound is not True:
                raise StrategyVersionBindingIntegrityError(
                    "PASS W83 evidence must bind strategy version"
                )
        else:
            if not self.reason_codes:
                raise StrategyVersionBindingIntegrityError(
                    "BLOCKED W83 evidence requires reason code"
                )
            if self.strategy_version_execution_bound is not False:
                raise StrategyVersionBindingIntegrityError(
                    "BLOCKED W83 evidence may not bind strategy version"
                )
        if self.shadow_forward_promotion_bound is not False:
            raise StrategyVersionBindingIntegrityError(
                "W83 may not bind Shadow/Forward promotion"
            )
        if (
            self.paper_candidate_authorized is not False
            or self.external_execution_authorized is not False
        ):
            raise StrategyVersionBindingIntegrityError(
                "W83 may not authorize PAPER candidate or external execution"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise StrategyVersionBindingIntegrityError(
                "W83 may not grant capital or LIVE authority"
            )
        if self.evidence_hash != _hash(_binding_payload(self, include_hash=False)):
            raise StrategyVersionBindingIntegrityError(
                "strategy-version binding evidence hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _binding_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PromotionStrategyVersionResolution:
    resolution_id: str
    contract_version: str
    w82_resolution_id: str
    w82_resolution_hash: str
    binding_evidence_id: str
    binding_evidence_hash: str
    promotion_assessment_id: str
    promotion_assessment_hash: str
    promotion_policy_id: str
    promotion_policy_hash: str
    selected_strategy_id: str
    selected_strategy_version: str
    strategy_artifact_hash: str
    runtime_code_version: str
    intent_fingerprint: str
    status: StrategyVersionBindingStatus
    reason_codes: tuple[str, ...]
    resolved_promotion_blockers: tuple[str, ...]
    remaining_promotion_blockers: tuple[str, ...]
    strategy_version_execution_bound: bool
    shadow_forward_promotion_bound: bool
    broker_authoritative_fee_proven: bool
    realized_profitability_authorized: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    resolved_at: datetime
    resolution_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("resolution_id", self.resolution_id),
            ("w82_resolution_id", self.w82_resolution_id),
            ("binding_evidence_id", self.binding_evidence_id),
            ("promotion_assessment_id", self.promotion_assessment_id),
            ("promotion_policy_id", self.promotion_policy_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            _require_id(value, label)
        if self.contract_version != RESOLUTION_CONTRACT_VERSION:
            raise StrategyVersionBindingIntegrityError(
                "resolution contract version is not canonical W83"
            )
        for label, value in (
            ("w82_resolution_hash", self.w82_resolution_hash),
            ("binding_evidence_hash", self.binding_evidence_hash),
            ("promotion_assessment_hash", self.promotion_assessment_hash),
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("strategy_artifact_hash", self.strategy_artifact_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("resolution_hash", self.resolution_hash),
        ):
            _require_hash(value, label)
        if not self.runtime_code_version.strip():
            raise StrategyVersionBindingIntegrityError(
                "runtime code version is required"
            )
        _require_aware(self.resolved_at, "resolved_at")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise StrategyVersionBindingIntegrityError(
                "resolution reason codes must be unique sorted"
            )
        expected_resolved = (
            (STRATEGY_VERSION_BLOCKER,)
            if self.status is StrategyVersionBindingStatus.PASS
            else ()
        )
        if self.resolved_promotion_blockers != expected_resolved:
            raise StrategyVersionBindingIntegrityError(
                "resolved strategy-version blocker set is inconsistent"
            )
        if self.status is StrategyVersionBindingStatus.PASS:
            if self.reason_codes:
                raise StrategyVersionBindingIntegrityError(
                    "PASS W83 resolution may not carry failure reasons"
                )
            if STRATEGY_VERSION_BLOCKER in self.remaining_promotion_blockers:
                raise StrategyVersionBindingIntegrityError(
                    "PASS W83 resolution may not retain strategy-version blocker"
                )
            if self.strategy_version_execution_bound is not True:
                raise StrategyVersionBindingIntegrityError(
                    "PASS W83 resolution must bind strategy version"
                )
        else:
            if not self.reason_codes:
                raise StrategyVersionBindingIntegrityError(
                    "BLOCKED W83 resolution requires reason code"
                )
            if STRATEGY_VERSION_BLOCKER not in self.remaining_promotion_blockers:
                raise StrategyVersionBindingIntegrityError(
                    "BLOCKED W83 resolution must retain strategy-version blocker"
                )
            if self.strategy_version_execution_bound is not False:
                raise StrategyVersionBindingIntegrityError(
                    "BLOCKED W83 resolution may not bind strategy version"
                )
        if SHADOW_FORWARD_BLOCKER not in self.remaining_promotion_blockers:
            raise StrategyVersionBindingIntegrityError(
                "Shadow/Forward blocker must remain open after W83"
            )
        if self.shadow_forward_promotion_bound is not False:
            raise StrategyVersionBindingIntegrityError(
                "W83 may not resolve Shadow/Forward promotion"
            )
        if self.broker_authoritative_fee_proven is not False:
            raise StrategyVersionBindingIntegrityError(
                "W83 may not create broker-authoritative fee proof"
            )
        if self.realized_profitability_authorized is not False:
            raise StrategyVersionBindingIntegrityError(
                "W83 may not authorize realized-profitability claims"
            )
        if (
            self.paper_candidate_authorized is not False
            or self.external_execution_authorized is not False
        ):
            raise StrategyVersionBindingIntegrityError(
                "W83 may not authorize PAPER candidate or execution"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise StrategyVersionBindingIntegrityError(
                "W83 may not grant capital or LIVE authority"
            )
        if self.resolution_hash != _hash(_resolution_payload(self, include_hash=False)):
            raise StrategyVersionBindingIntegrityError(
                "strategy-version resolution hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _resolution_payload(self, include_hash=True)


def safe_dsl_runtime_code_version() -> str:
    """Return the exact loaded safe-DSL runtime identity.

    The W79 TrialSpec must preregister this value. W83 therefore fails closed if
    the DSL implementation bytes or Python major/minor runtime changed after
    trial preregistration.
    """

    source_path = inspect.getsourcefile(research_dsl.StrategySpec)
    if source_path is None:
        raise StrategyVersionBindingIntegrityError(
            "cannot locate safe DSL runtime source"
        )
    raw = Path(source_path).read_bytes()
    implementation = sys.implementation.name
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return (
        f"{implementation}-{python_version}|"
        f"dsl-sha256:{sha256(raw).hexdigest()}"
    )


def strategy_spec_from_preregistered_trial(trial_spec: TrialSpec) -> StrategySpec:
    """Reconstruct the only W83-safe StrategySpec from preregistered trial material."""

    if not isinstance(trial_spec, TrialSpec):
        raise TypeError("trial_spec must be TrialSpec")
    if trial_spec.phase is not TrialPhase.DEVELOPMENT:
        raise StrategyVersionBindingIntegrityError(
            "W83 strategy binding requires selected DEVELOPMENT trial"
        )
    runtime_version = safe_dsl_runtime_code_version()
    if trial_spec.code_version != runtime_version:
        raise StrategyVersionBindingIntegrityError(
            "selected trial code_version differs from loaded safe DSL runtime"
        )
    params = dict(trial_spec.parameters)
    if frozenset(params) != _REQUIRED_TRIAL_PARAMETERS:
        raise StrategyVersionBindingIntegrityError(
            "selected trial parameters do not match canonical W83 DSL schema"
        )
    if params["dsl_kind"] != DSL_KIND:
        raise StrategyVersionBindingIntegrityError(
            "selected trial DSL kind is unsupported by W83"
        )
    if type(params["short_window"]) is not int or type(params["long_window"]) is not int:
        raise StrategyVersionBindingIntegrityError(
            "moving-average windows must be preregistered as integers"
        )
    if not isinstance(params["order_quantity"], str):
        raise StrategyVersionBindingIntegrityError(
            "order_quantity must be preregistered as canonical decimal string"
        )
    if not isinstance(params["initial_stop_pct"], str):
        raise StrategyVersionBindingIntegrityError(
            "initial_stop_pct must be preregistered as canonical decimal string"
        )
    if not isinstance(params["position_mode"], str):
        raise StrategyVersionBindingIntegrityError(
            "position_mode must be preregistered as string"
        )
    try:
        order_quantity = Decimal(params["order_quantity"])
        initial_stop_pct = Decimal(params["initial_stop_pct"])
    except (InvalidOperation, ValueError) as exc:
        raise StrategyVersionBindingIntegrityError(
            "preregistered decimal strategy parameter is invalid"
        ) from exc
    if not order_quantity.is_finite() or order_quantity <= 0:
        raise StrategyVersionBindingIntegrityError(
            "preregistered order_quantity must be finite and > 0"
        )
    if (
        not initial_stop_pct.is_finite()
        or initial_stop_pct <= 0
        or initial_stop_pct >= 1
    ):
        raise StrategyVersionBindingIntegrityError(
            "preregistered initial_stop_pct must be between 0 and 1"
        )
    spec = StrategySpec(
        strategy_id=trial_spec.strategy_id,
        strategy_version=trial_spec.strategy_version,
        kind=DSL_KIND,
        parameters={
            "short_window": params["short_window"],
            "long_window": params["long_window"],
            "order_quantity": params["order_quantity"],
            "position_mode": params["position_mode"],
        },
        initial_stop_pct=initial_stop_pct,
    )
    runtime = spec.build()
    if (
        runtime.strategy_id != trial_spec.strategy_id
        or runtime.strategy_version != trial_spec.strategy_version
        or dict(runtime.parameters) != dict(spec.parameters)
    ):
        raise StrategyVersionBindingIntegrityError(
            "safe DSL runtime identity differs from reconstructed StrategySpec"
        )
    return spec


def build_execution_strategy_version_binding(
    *,
    evidence_id: str,
    promotion_policy: StrategyPromotionPolicy,
    selected_trial: TrialRecord,
    w82_resolution: PromotionFeeAccountingResolution,
    execution_intent: OrderIntent,
    market_dataset: MarketDataset,
    strategy_context: StrategyContext,
    assessed_at: datetime,
) -> ExecutionStrategyVersionBindingEvidence:
    """Prove deterministic candidate -> artifact -> signal -> intent identity.

    This is scientific qualification evidence only. It consumes an existing
    OrderIntent for identity checking and never constructs, stages or submits one.
    """

    _require_id(evidence_id, "evidence_id")
    if not isinstance(promotion_policy, StrategyPromotionPolicy):
        raise TypeError("promotion_policy must be StrategyPromotionPolicy")
    if not isinstance(selected_trial, TrialRecord):
        raise TypeError("selected_trial must be TrialRecord")
    if not isinstance(w82_resolution, PromotionFeeAccountingResolution):
        raise TypeError("w82_resolution must be PromotionFeeAccountingResolution")
    if not isinstance(execution_intent, OrderIntent):
        raise TypeError("execution_intent must be OrderIntent")
    if not isinstance(market_dataset, MarketDataset):
        raise TypeError("market_dataset must be MarketDataset")
    if not isinstance(strategy_context, StrategyContext):
        raise TypeError("strategy_context must be StrategyContext")
    _require_aware(assessed_at, "assessed_at")
    _require_aware(execution_intent.created_at, "execution_intent.created_at")

    _validate_candidate_chain(
        promotion_policy=promotion_policy,
        selected_trial=selected_trial,
        w82_resolution=w82_resolution,
        execution_intent=execution_intent,
        assessed_at=assessed_at,
    )
    _validate_dataset_context(
        selected_trial=selected_trial,
        execution_intent=execution_intent,
        market_dataset=market_dataset,
        strategy_context=strategy_context,
    )

    spec = strategy_spec_from_preregistered_trial(selected_trial.spec)
    runtime_version = safe_dsl_runtime_code_version()
    artifact_hash = spec.canonical_hash
    if strategy_spec_from_preregistered_trial(selected_trial.spec).canonical_hash != artifact_hash:
        raise StrategyVersionBindingIntegrityError(
            "strategy artifact reconstruction is not deterministic"
        )

    runtime = spec.build()
    signal = runtime.on_bar(strategy_context)
    reasons: set[str] = set()

    signal_id: str | None = None
    signal_hash: str | None = None
    signal_generated_at: datetime | None = None
    derived_side: Side | None = None
    derived_quantity: Decimal | None = None

    if signal is None:
        reasons.add("NO_DETERMINISTIC_SIGNAL_FOR_EXECUTION_INTENT")
    else:
        _validate_signal_shape(signal)
        signal_id = signal.signal_id
        signal_hash = _hash(_signal_payload(signal))
        signal_generated_at = signal.generated_at
        derived_side = Side.BUY if signal.quantity_delta > 0 else Side.SELL
        derived_quantity = abs(signal.quantity_delta)

        if signal.symbol != execution_intent.symbol:
            reasons.add("SIGNAL_SYMBOL_MISMATCH")
        if derived_side is not execution_intent.side:
            reasons.add("SIGNAL_SIDE_MISMATCH")
        if derived_quantity != execution_intent.quantity:
            reasons.add("SIGNAL_QUANTITY_MISMATCH")
        if _utc(execution_intent.created_at) < _utc(signal.generated_at):
            reasons.add("INTENT_PRECEDES_STRATEGY_SIGNAL")

    status = (
        StrategyVersionBindingStatus.PASS
        if not reasons
        else StrategyVersionBindingStatus.BLOCKED
    )
    values = {
        "evidence_id": evidence_id,
        "contract_version": BINDING_CONTRACT_VERSION,
        "binding_scope": BINDING_SCOPE,
        "promotion_policy_id": promotion_policy.policy_id,
        "promotion_policy_hash": promotion_policy.policy_hash,
        "selected_trial_id": selected_trial.spec.trial_id,
        "selected_trial_fingerprint": selected_trial.spec.fingerprint,
        "selected_strategy_id": selected_trial.spec.strategy_id,
        "selected_strategy_version": selected_trial.spec.strategy_version,
        "trial_code_version": selected_trial.spec.code_version,
        "runtime_code_version": runtime_version,
        "strategy_artifact_hash": artifact_hash,
        "dataset_hash": market_dataset.dataset_hash,
        "dataset_symbol": market_dataset.instrument.symbol,
        "dataset_venue": market_dataset.instrument.venue,
        "context_hash": _hash(_context_payload(strategy_context)),
        "signal_id": signal_id,
        "signal_hash": signal_hash,
        "signal_generated_at": signal_generated_at,
        "derived_side": derived_side,
        "derived_quantity": derived_quantity,
        "w82_resolution_id": w82_resolution.resolution_id,
        "w82_resolution_hash": w82_resolution.resolution_hash,
        "promotion_assessment_id": w82_resolution.promotion_assessment_id,
        "promotion_assessment_hash": w82_resolution.promotion_assessment_hash,
        "intent_fingerprint": intent_fingerprint(execution_intent),
        "intent_created_at": execution_intent.created_at,
        "status": status,
        "reason_codes": tuple(sorted(reasons)),
        "strategy_version_execution_bound": (
            status is StrategyVersionBindingStatus.PASS
        ),
        "shadow_forward_promotion_bound": False,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "assessed_at": assessed_at,
    }
    return ExecutionStrategyVersionBindingEvidence(
        **values,
        evidence_hash=_hash(_binding_payload_from_values(values)),
    )


def resolve_execution_strategy_version_binding(
    *,
    resolution_id: str,
    w82_resolution: PromotionFeeAccountingResolution,
    binding_evidence: ExecutionStrategyVersionBindingEvidence,
    execution_intent: OrderIntent,
    resolved_at: datetime,
) -> PromotionStrategyVersionResolution:
    """Remove only EXECUTION_STRATEGY_VERSION_UNBOUND for exact PASS evidence."""

    _require_id(resolution_id, "resolution_id")
    if not isinstance(w82_resolution, PromotionFeeAccountingResolution):
        raise TypeError("w82_resolution must be PromotionFeeAccountingResolution")
    if not isinstance(binding_evidence, ExecutionStrategyVersionBindingEvidence):
        raise TypeError(
            "binding_evidence must be ExecutionStrategyVersionBindingEvidence"
        )
    if not isinstance(execution_intent, OrderIntent):
        raise TypeError("execution_intent must be OrderIntent")
    _require_aware(resolved_at, "resolved_at")
    if _utc(resolved_at) < _utc(binding_evidence.assessed_at):
        raise StrategyVersionBindingIntegrityError(
            "W83 resolution may not predate binding evidence"
        )
    if _utc(resolved_at) < _utc(w82_resolution.resolved_at):
        raise StrategyVersionBindingIntegrityError(
            "W83 resolution may not predate W82 resolution"
        )
    _validate_w82_resolution(w82_resolution)
    intent_hash = intent_fingerprint(execution_intent)
    if binding_evidence.w82_resolution_id != w82_resolution.resolution_id:
        raise StrategyVersionBindingIntegrityError(
            "binding evidence/W82 resolution id mismatch"
        )
    if binding_evidence.w82_resolution_hash != w82_resolution.resolution_hash:
        raise StrategyVersionBindingIntegrityError(
            "binding evidence/W82 resolution hash mismatch"
        )
    if binding_evidence.intent_fingerprint != intent_hash:
        raise StrategyVersionBindingIntegrityError(
            "binding evidence/intent fingerprint mismatch"
        )
    if w82_resolution.intent_fingerprint != intent_hash:
        raise StrategyVersionBindingIntegrityError(
            "W82 resolution/intent fingerprint mismatch"
        )
    if (
        binding_evidence.promotion_policy_id != w82_resolution.promotion_policy_id
        or binding_evidence.promotion_policy_hash != w82_resolution.promotion_policy_hash
    ):
        raise StrategyVersionBindingIntegrityError(
            "binding evidence promotion-policy mismatch"
        )
    if (
        binding_evidence.promotion_assessment_id
        != w82_resolution.promotion_assessment_id
        or binding_evidence.promotion_assessment_hash
        != w82_resolution.promotion_assessment_hash
    ):
        raise StrategyVersionBindingIntegrityError(
            "binding evidence promotion-assessment mismatch"
        )
    if (
        binding_evidence.selected_strategy_id != w82_resolution.selected_strategy_id
        or binding_evidence.selected_strategy_version
        != w82_resolution.selected_strategy_version
    ):
        raise StrategyVersionBindingIntegrityError(
            "binding evidence selected-strategy mismatch"
        )

    status = binding_evidence.status
    reasons = binding_evidence.reason_codes
    remaining = set(w82_resolution.remaining_promotion_blockers)
    if status is StrategyVersionBindingStatus.PASS:
        remaining.discard(STRATEGY_VERSION_BLOCKER)
    remaining_blockers = tuple(sorted(remaining))
    values = {
        "resolution_id": resolution_id,
        "contract_version": RESOLUTION_CONTRACT_VERSION,
        "w82_resolution_id": w82_resolution.resolution_id,
        "w82_resolution_hash": w82_resolution.resolution_hash,
        "binding_evidence_id": binding_evidence.evidence_id,
        "binding_evidence_hash": binding_evidence.evidence_hash,
        "promotion_assessment_id": w82_resolution.promotion_assessment_id,
        "promotion_assessment_hash": w82_resolution.promotion_assessment_hash,
        "promotion_policy_id": w82_resolution.promotion_policy_id,
        "promotion_policy_hash": w82_resolution.promotion_policy_hash,
        "selected_strategy_id": w82_resolution.selected_strategy_id,
        "selected_strategy_version": w82_resolution.selected_strategy_version,
        "strategy_artifact_hash": binding_evidence.strategy_artifact_hash,
        "runtime_code_version": binding_evidence.runtime_code_version,
        "intent_fingerprint": intent_hash,
        "status": status,
        "reason_codes": reasons,
        "resolved_promotion_blockers": (
            (STRATEGY_VERSION_BLOCKER,)
            if status is StrategyVersionBindingStatus.PASS
            else ()
        ),
        "remaining_promotion_blockers": remaining_blockers,
        "strategy_version_execution_bound": (
            status is StrategyVersionBindingStatus.PASS
        ),
        "shadow_forward_promotion_bound": False,
        "broker_authoritative_fee_proven": False,
        "realized_profitability_authorized": False,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "resolved_at": resolved_at,
    }
    return PromotionStrategyVersionResolution(
        **values,
        resolution_hash=_hash(_resolution_payload_from_values(values)),
    )


def _validate_candidate_chain(
    *,
    promotion_policy: StrategyPromotionPolicy,
    selected_trial: TrialRecord,
    w82_resolution: PromotionFeeAccountingResolution,
    execution_intent: OrderIntent,
    assessed_at: datetime,
) -> None:
    if selected_trial.status is not TrialStatus.COMPLETED:
        raise StrategyVersionBindingIntegrityError("selected trial must be completed")
    if selected_trial.spec.phase is not TrialPhase.DEVELOPMENT:
        raise StrategyVersionBindingIntegrityError(
            "selected trial must be DEVELOPMENT"
        )
    _require_aware(selected_trial.preregistered_at, "selected_trial.preregistered_at")
    if selected_trial.terminal_at is None:
        raise StrategyVersionBindingIntegrityError(
            "completed selected trial requires terminal_at"
        )
    _require_aware(selected_trial.terminal_at, "selected_trial.terminal_at")
    if _utc(selected_trial.preregistered_at) > _utc(selected_trial.terminal_at):
        raise StrategyVersionBindingIntegrityError(
            "selected trial terminal time predates preregistration"
        )
    if _utc(assessed_at) < _utc(selected_trial.terminal_at):
        raise StrategyVersionBindingIntegrityError(
            "W83 assessment may not predate selected trial completion"
        )
    if (
        promotion_policy.selected_trial_id != selected_trial.spec.trial_id
        or promotion_policy.selected_trial_fingerprint != selected_trial.spec.fingerprint
    ):
        raise StrategyVersionBindingIntegrityError(
            "selected trial does not match frozen W79 promotion policy"
        )
    if (
        promotion_policy.selected_strategy_id != selected_trial.spec.strategy_id
        or promotion_policy.selected_strategy_version != selected_trial.spec.strategy_version
    ):
        raise StrategyVersionBindingIntegrityError(
            "selected strategy does not match frozen W79 promotion policy"
        )
    _validate_w82_resolution(w82_resolution)
    if _utc(assessed_at) < _utc(w82_resolution.resolved_at):
        raise StrategyVersionBindingIntegrityError(
            "W83 assessment may not predate W82 resolution"
        )
    if (
        w82_resolution.promotion_policy_id != promotion_policy.policy_id
        or w82_resolution.promotion_policy_hash != promotion_policy.policy_hash
    ):
        raise StrategyVersionBindingIntegrityError(
            "W82 resolution does not bind the supplied W79 promotion policy"
        )
    if (
        w82_resolution.selected_strategy_id != promotion_policy.selected_strategy_id
        or w82_resolution.selected_strategy_version
        != promotion_policy.selected_strategy_version
    ):
        raise StrategyVersionBindingIntegrityError(
            "W82 selected strategy differs from W79 frozen candidate"
        )
    intent_hash = intent_fingerprint(execution_intent)
    if w82_resolution.intent_fingerprint != intent_hash:
        raise StrategyVersionBindingIntegrityError(
            "W82 resolution/intent fingerprint mismatch"
        )
    if execution_intent.strategy_id != promotion_policy.selected_strategy_id:
        raise StrategyVersionBindingIntegrityError(
            "execution intent strategy_id differs from frozen candidate"
        )
    _require_positive_decimal(execution_intent.quantity, "execution_intent.quantity")


def _validate_w82_resolution(w82_resolution: PromotionFeeAccountingResolution) -> None:
    if w82_resolution.status is not PromotionFeeAccountingStatus.PASS:
        raise StrategyVersionBindingIntegrityError(
            "W83 requires PASS W82 fee-accounting resolution"
        )
    if w82_resolution.strategy_version_execution_bound is not False:
        raise StrategyVersionBindingIntegrityError(
            "W83 input must still have strategy-version blocker unresolved"
        )
    if STRATEGY_VERSION_BLOCKER not in w82_resolution.remaining_promotion_blockers:
        raise StrategyVersionBindingIntegrityError(
            "W82 input is missing strategy-version blocker"
        )
    if SHADOW_FORWARD_BLOCKER not in w82_resolution.remaining_promotion_blockers:
        raise StrategyVersionBindingIntegrityError(
            "W82 input is missing Shadow/Forward blocker"
        )
    if (
        w82_resolution.broker_authoritative_fee_proven is not False
        or w82_resolution.realized_profitability_authorized is not False
        or w82_resolution.paper_candidate_authorized is not False
        or w82_resolution.external_execution_authorized is not False
        or w82_resolution.capital_authority != "NONE"
        or w82_resolution.live_trading != "BLOCKED"
    ):
        raise StrategyVersionBindingIntegrityError(
            "W82 authority/no-claims boundary is not intact"
        )


def _validate_dataset_context(
    *,
    selected_trial: TrialRecord,
    execution_intent: OrderIntent,
    market_dataset: MarketDataset,
    strategy_context: StrategyContext,
) -> None:
    if selected_trial.spec.dataset_hash != market_dataset.dataset_hash:
        raise StrategyVersionBindingIntegrityError(
            "market dataset differs from preregistered selected trial"
        )
    if strategy_context.symbol != market_dataset.instrument.symbol:
        raise StrategyVersionBindingIntegrityError(
            "strategy context symbol differs from preregistered dataset"
        )
    if execution_intent.symbol != market_dataset.instrument.symbol:
        raise StrategyVersionBindingIntegrityError(
            "execution intent symbol differs from preregistered dataset"
        )
    if type(strategy_context.index) is not int:
        raise StrategyVersionBindingIntegrityError(
            "strategy context index must be integer"
        )
    if not isinstance(strategy_context.history, tuple) or not strategy_context.history:
        raise StrategyVersionBindingIntegrityError(
            "strategy context history must be non-empty tuple"
        )
    if any(not isinstance(bar, Bar) for bar in strategy_context.history):
        raise StrategyVersionBindingIntegrityError(
            "strategy context history contains non-Bar value"
        )
    if strategy_context.index != len(strategy_context.history) - 1:
        raise StrategyVersionBindingIntegrityError(
            "strategy context index must identify final history bar"
        )
    if strategy_context.index >= len(market_dataset.bars):
        raise StrategyVersionBindingIntegrityError(
            "strategy context index exceeds preregistered dataset"
        )
    expected_history = market_dataset.bars[: strategy_context.index + 1]
    if strategy_context.history != expected_history:
        raise StrategyVersionBindingIntegrityError(
            "strategy context history is not exact dataset prefix"
        )
    _require_decimal(
        strategy_context.current_position_quantity,
        "strategy_context.current_position_quantity",
    )
    _require_positive_decimal(strategy_context.current_equity, "strategy_context.current_equity")


def _validate_signal_shape(signal: ResearchSignal) -> None:
    if not isinstance(signal, ResearchSignal):
        raise StrategyVersionBindingIntegrityError(
            "safe DSL runtime returned invalid signal type"
        )
    _require_aware(signal.generated_at, "signal.generated_at")
    _require_decimal(signal.quantity_delta, "signal.quantity_delta")
    if signal.quantity_delta == 0:
        raise StrategyVersionBindingIntegrityError(
            "safe DSL runtime returned zero-quantity signal"
        )


def _binding_payload(
    value: ExecutionStrategyVersionBindingEvidence, *, include_hash: bool
) -> dict[str, object]:
    payload = {
        "evidence_id": value.evidence_id,
        "contract_version": value.contract_version,
        "binding_scope": value.binding_scope,
        "promotion_policy_id": value.promotion_policy_id,
        "promotion_policy_hash": value.promotion_policy_hash,
        "selected_trial_id": value.selected_trial_id,
        "selected_trial_fingerprint": value.selected_trial_fingerprint,
        "selected_strategy_id": value.selected_strategy_id,
        "selected_strategy_version": value.selected_strategy_version,
        "trial_code_version": value.trial_code_version,
        "runtime_code_version": value.runtime_code_version,
        "strategy_artifact_hash": value.strategy_artifact_hash,
        "dataset_hash": value.dataset_hash,
        "dataset_symbol": value.dataset_symbol,
        "dataset_venue": value.dataset_venue,
        "context_hash": value.context_hash,
        "signal_id": value.signal_id,
        "signal_hash": value.signal_hash,
        "signal_generated_at": _datetime_value(value.signal_generated_at),
        "derived_side": value.derived_side.value if value.derived_side else None,
        "derived_quantity": _decimal_value(value.derived_quantity),
        "w82_resolution_id": value.w82_resolution_id,
        "w82_resolution_hash": value.w82_resolution_hash,
        "promotion_assessment_id": value.promotion_assessment_id,
        "promotion_assessment_hash": value.promotion_assessment_hash,
        "intent_fingerprint": value.intent_fingerprint,
        "intent_created_at": _datetime_value(value.intent_created_at),
        "status": value.status.value,
        "reason_codes": list(value.reason_codes),
        "strategy_version_execution_bound": value.strategy_version_execution_bound,
        "shadow_forward_promotion_bound": value.shadow_forward_promotion_bound,
        "paper_candidate_authorized": value.paper_candidate_authorized,
        "external_execution_authorized": value.external_execution_authorized,
        "capital_authority": value.capital_authority,
        "live_trading": value.live_trading,
        "assessed_at": _datetime_value(value.assessed_at),
    }
    if include_hash:
        payload["evidence_hash"] = value.evidence_hash
    return payload


def _binding_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    return {
        "evidence_id": values["evidence_id"],
        "contract_version": values["contract_version"],
        "binding_scope": values["binding_scope"],
        "promotion_policy_id": values["promotion_policy_id"],
        "promotion_policy_hash": values["promotion_policy_hash"],
        "selected_trial_id": values["selected_trial_id"],
        "selected_trial_fingerprint": values["selected_trial_fingerprint"],
        "selected_strategy_id": values["selected_strategy_id"],
        "selected_strategy_version": values["selected_strategy_version"],
        "trial_code_version": values["trial_code_version"],
        "runtime_code_version": values["runtime_code_version"],
        "strategy_artifact_hash": values["strategy_artifact_hash"],
        "dataset_hash": values["dataset_hash"],
        "dataset_symbol": values["dataset_symbol"],
        "dataset_venue": values["dataset_venue"],
        "context_hash": values["context_hash"],
        "signal_id": values["signal_id"],
        "signal_hash": values["signal_hash"],
        "signal_generated_at": _datetime_value(values["signal_generated_at"]),
        "derived_side": (
            values["derived_side"].value
            if isinstance(values["derived_side"], Side)
            else None
        ),
        "derived_quantity": _decimal_value(values["derived_quantity"]),
        "w82_resolution_id": values["w82_resolution_id"],
        "w82_resolution_hash": values["w82_resolution_hash"],
        "promotion_assessment_id": values["promotion_assessment_id"],
        "promotion_assessment_hash": values["promotion_assessment_hash"],
        "intent_fingerprint": values["intent_fingerprint"],
        "intent_created_at": _datetime_value(values["intent_created_at"]),
        "status": values["status"].value,
        "reason_codes": list(values["reason_codes"]),
        "strategy_version_execution_bound": values["strategy_version_execution_bound"],
        "shadow_forward_promotion_bound": values["shadow_forward_promotion_bound"],
        "paper_candidate_authorized": values["paper_candidate_authorized"],
        "external_execution_authorized": values["external_execution_authorized"],
        "capital_authority": values["capital_authority"],
        "live_trading": values["live_trading"],
        "assessed_at": _datetime_value(values["assessed_at"]),
    }


def _resolution_payload(
    value: PromotionStrategyVersionResolution, *, include_hash: bool
) -> dict[str, object]:
    payload = {
        "resolution_id": value.resolution_id,
        "contract_version": value.contract_version,
        "w82_resolution_id": value.w82_resolution_id,
        "w82_resolution_hash": value.w82_resolution_hash,
        "binding_evidence_id": value.binding_evidence_id,
        "binding_evidence_hash": value.binding_evidence_hash,
        "promotion_assessment_id": value.promotion_assessment_id,
        "promotion_assessment_hash": value.promotion_assessment_hash,
        "promotion_policy_id": value.promotion_policy_id,
        "promotion_policy_hash": value.promotion_policy_hash,
        "selected_strategy_id": value.selected_strategy_id,
        "selected_strategy_version": value.selected_strategy_version,
        "strategy_artifact_hash": value.strategy_artifact_hash,
        "runtime_code_version": value.runtime_code_version,
        "intent_fingerprint": value.intent_fingerprint,
        "status": value.status.value,
        "reason_codes": list(value.reason_codes),
        "resolved_promotion_blockers": list(value.resolved_promotion_blockers),
        "remaining_promotion_blockers": list(value.remaining_promotion_blockers),
        "strategy_version_execution_bound": value.strategy_version_execution_bound,
        "shadow_forward_promotion_bound": value.shadow_forward_promotion_bound,
        "broker_authoritative_fee_proven": value.broker_authoritative_fee_proven,
        "realized_profitability_authorized": value.realized_profitability_authorized,
        "paper_candidate_authorized": value.paper_candidate_authorized,
        "external_execution_authorized": value.external_execution_authorized,
        "capital_authority": value.capital_authority,
        "live_trading": value.live_trading,
        "resolved_at": _datetime_value(value.resolved_at),
    }
    if include_hash:
        payload["resolution_hash"] = value.resolution_hash
    return payload


def _resolution_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    return {
        "resolution_id": values["resolution_id"],
        "contract_version": values["contract_version"],
        "w82_resolution_id": values["w82_resolution_id"],
        "w82_resolution_hash": values["w82_resolution_hash"],
        "binding_evidence_id": values["binding_evidence_id"],
        "binding_evidence_hash": values["binding_evidence_hash"],
        "promotion_assessment_id": values["promotion_assessment_id"],
        "promotion_assessment_hash": values["promotion_assessment_hash"],
        "promotion_policy_id": values["promotion_policy_id"],
        "promotion_policy_hash": values["promotion_policy_hash"],
        "selected_strategy_id": values["selected_strategy_id"],
        "selected_strategy_version": values["selected_strategy_version"],
        "strategy_artifact_hash": values["strategy_artifact_hash"],
        "runtime_code_version": values["runtime_code_version"],
        "intent_fingerprint": values["intent_fingerprint"],
        "status": values["status"].value,
        "reason_codes": list(values["reason_codes"]),
        "resolved_promotion_blockers": list(values["resolved_promotion_blockers"]),
        "remaining_promotion_blockers": list(values["remaining_promotion_blockers"]),
        "strategy_version_execution_bound": values["strategy_version_execution_bound"],
        "shadow_forward_promotion_bound": values["shadow_forward_promotion_bound"],
        "broker_authoritative_fee_proven": values["broker_authoritative_fee_proven"],
        "realized_profitability_authorized": values["realized_profitability_authorized"],
        "paper_candidate_authorized": values["paper_candidate_authorized"],
        "external_execution_authorized": values["external_execution_authorized"],
        "capital_authority": values["capital_authority"],
        "live_trading": values["live_trading"],
        "resolved_at": _datetime_value(values["resolved_at"]),
    }


def _context_payload(context: StrategyContext) -> dict[str, object]:
    return {
        "symbol": context.symbol,
        "index": context.index,
        "current_position_quantity": str(context.current_position_quantity),
        "current_equity": str(context.current_equity),
        "history": [_bar_payload(bar) for bar in context.history],
    }


def _bar_payload(bar: Bar) -> dict[str, object]:
    return {
        "symbol": bar.symbol,
        "started_at": _datetime_value(bar.started_at),
        "timeframe_seconds": bar.timeframe_seconds,
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
    }


def _signal_payload(signal: ResearchSignal) -> dict[str, object]:
    return {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "generated_at": _datetime_value(signal.generated_at),
        "quantity_delta": str(signal.quantity_delta),
        "reason": signal.reason,
    }


def _hash(payload: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise StrategyVersionBindingIntegrityError(f"{label} is invalid")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise StrategyVersionBindingIntegrityError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StrategyVersionBindingIntegrityError(
            f"{label} must be timezone-aware"
        )


def _require_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise StrategyVersionBindingIntegrityError(
            f"{label} must be finite Decimal"
        )


def _require_positive_decimal(value: Decimal, label: str) -> None:
    _require_decimal(value, label)
    if value <= 0:
        raise StrategyVersionBindingIntegrityError(f"{label} must be > 0")


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _datetime_value(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise StrategyVersionBindingIntegrityError(
            "canonical timestamp value must be datetime"
        )
    _require_aware(value, "canonical timestamp")
    return _utc(value).isoformat()


def _decimal_value(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise StrategyVersionBindingIntegrityError(
            "canonical decimal value must be finite Decimal"
        )
    return str(value)
