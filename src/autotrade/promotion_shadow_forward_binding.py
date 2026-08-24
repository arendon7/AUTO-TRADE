from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

import autotrade.promotion_strategy_version_binding as w83_resolution_module
from autotrade.promotion_fee_accounting import SHADOW_FORWARD_BLOCKER
from autotrade.promotion_strategy_version_binding import PromotionStrategyVersionResolution
from autotrade.research.forward import FrozenForwardPolicy, SQLiteForwardEvidenceRegistry
from autotrade.research.shadow import FrozenShadowConfig, SQLitePortfolioShadowRegistry
import autotrade.strategy_execution_binding as w83_binding_module
from autotrade.strategy_execution_binding import (
    ExecutionStrategyBindingEvidence,
    ExecutionStrategyBindingStatus,
)


SHADOW_FORWARD_PROMOTION_POLICY_VERSION = "W84_SHADOW_FORWARD_PROMOTION_POLICY_V1"
SHADOW_FORWARD_PROMOTION_EVIDENCE_VERSION = "W84_SHADOW_FORWARD_PROMOTION_EVIDENCE_V1"
PROMOTION_SHADOW_FORWARD_RESOLUTION_VERSION = "W84_PROMOTION_SHADOW_FORWARD_RESOLUTION_V1"
GENESIS_HASH = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class ShadowForwardPromotionIntegrityError(RuntimeError):
    pass


class ShadowForwardPromotionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class ShadowForwardPromotionPolicy:
    """Candidate-bound preregistration committed by the R5 frozen shadow config."""

    policy_id: str
    contract_version: str
    w83_resolution_id: str
    w83_resolution_hash: str
    w83_binding_id: str
    w83_binding_hash: str
    promotion_policy_id: str
    promotion_policy_hash: str
    development_campaign_id: str
    selected_trial_id: str
    selected_trial_fingerprint: str
    selected_strategy_id: str
    selected_strategy_version: str
    strategy_spec_hash: str
    runtime_code_hash: str
    intent_fingerprint: str
    shadow_config_id: str
    shadow_initial_nav: Decimal
    shadow_activated_at: datetime
    forward_campaign_id: str
    forward_activated_at: datetime
    required_forward_periods: int
    minimum_forward_duration_seconds: int
    min_cumulative_return: Decimal
    max_peak_to_trough_drawdown: Decimal
    frozen_at: datetime
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    policy_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("policy_id", self.policy_id),
            ("w83_resolution_id", self.w83_resolution_id),
            ("w83_binding_id", self.w83_binding_id),
            ("promotion_policy_id", self.promotion_policy_id),
            ("development_campaign_id", self.development_campaign_id),
            ("selected_trial_id", self.selected_trial_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
            ("shadow_config_id", self.shadow_config_id),
            ("forward_campaign_id", self.forward_campaign_id),
        ):
            _require_id(value, label)
        if self.contract_version != SHADOW_FORWARD_PROMOTION_POLICY_VERSION:
            raise ShadowForwardPromotionIntegrityError(
                "promotion policy version is not canonical W84"
            )
        for label, value in (
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("w83_binding_hash", self.w83_binding_hash),
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("runtime_code_hash", self.runtime_code_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("policy_hash", self.policy_hash),
        ):
            _require_hash(value, label)
        if self.forward_campaign_id == self.development_campaign_id:
            raise ShadowForwardPromotionIntegrityError(
                "forward campaign must be distinct from DEVELOPMENT campaign"
            )
        _require_positive_decimal(self.shadow_initial_nav, "shadow_initial_nav")
        _require_positive_int(self.required_forward_periods, "required_forward_periods")
        _require_positive_int(
            self.minimum_forward_duration_seconds,
            "minimum_forward_duration_seconds",
        )
        _require_finite_decimal(self.min_cumulative_return, "min_cumulative_return")
        if self.min_cumulative_return <= Decimal("-1"):
            raise ShadowForwardPromotionIntegrityError(
                "min_cumulative_return must be greater than -1"
            )
        _require_finite_decimal(
            self.max_peak_to_trough_drawdown,
            "max_peak_to_trough_drawdown",
        )
        if not Decimal("0") <= self.max_peak_to_trough_drawdown <= Decimal("1"):
            raise ShadowForwardPromotionIntegrityError(
                "max_peak_to_trough_drawdown must be within [0,1]"
            )
        for label, value in (
            ("frozen_at", self.frozen_at),
            ("shadow_activated_at", self.shadow_activated_at),
            ("forward_activated_at", self.forward_activated_at),
        ):
            _require_aware(value, label)
        if not (
            _utc(self.frozen_at)
            <= _utc(self.shadow_activated_at)
            <= _utc(self.forward_activated_at)
        ):
            raise ShadowForwardPromotionIntegrityError(
                "W84 chronology must be frozen_at <= shadow activation <= forward activation"
            )
        _require_no_authority(
            paper=self.paper_candidate_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
            label="W84 policy",
        )
        if self.policy_hash != _hash(_policy_payload(self, include_hash=False)):
            raise ShadowForwardPromotionIntegrityError(
                "shadow/forward promotion policy hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _policy_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class ShadowForwardPromotionEvidence:
    evidence_id: str
    contract_version: str
    policy_id: str
    policy_hash: str
    w83_resolution_id: str
    w83_resolution_hash: str
    w83_binding_hash: str
    selected_strategy_id: str
    selected_strategy_version: str
    strategy_spec_hash: str
    runtime_code_hash: str
    shadow_config_fingerprint: str
    shadow_policy_commitment_hash: str
    shadow_control_hash: str
    shadow_sequence: int
    shadow_head_hash: str
    forward_policy_fingerprint: str
    forward_control_hash: str
    forward_sequence: int
    forward_head_hash: str
    required_forward_periods: int
    qualification_periods_used: int
    qualification_head_hash: str
    qualification_started_at: datetime | None
    qualification_ended_at: datetime | None
    qualification_duration_seconds: int
    cumulative_return: Decimal
    peak_to_trough_drawdown: Decimal
    status: ShadowForwardPromotionStatus
    reason_codes: tuple[str, ...]
    exact_candidate_shadow_bound: bool
    policy_preregistered_in_shadow_config: bool
    forward_policy_committed: bool
    full_observed_forward_tail_bound: bool
    fixed_forward_window_bound: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    assessed_at: datetime
    evidence_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("evidence_id", self.evidence_id),
            ("policy_id", self.policy_id),
            ("w83_resolution_id", self.w83_resolution_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            _require_id(value, label)
        if self.contract_version != SHADOW_FORWARD_PROMOTION_EVIDENCE_VERSION:
            raise ShadowForwardPromotionIntegrityError(
                "promotion evidence version is not canonical W84"
            )
        for label, value in (
            ("policy_hash", self.policy_hash),
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("w83_binding_hash", self.w83_binding_hash),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("runtime_code_hash", self.runtime_code_hash),
            ("shadow_config_fingerprint", self.shadow_config_fingerprint),
            ("shadow_policy_commitment_hash", self.shadow_policy_commitment_hash),
            ("shadow_control_hash", self.shadow_control_hash),
            ("shadow_head_hash", self.shadow_head_hash),
            ("forward_policy_fingerprint", self.forward_policy_fingerprint),
            ("forward_control_hash", self.forward_control_hash),
            ("forward_head_hash", self.forward_head_hash),
            ("qualification_head_hash", self.qualification_head_hash),
            ("evidence_hash", self.evidence_hash),
        ):
            _require_hash(value, label)
        _require_nonnegative_int(self.shadow_sequence, "shadow_sequence")
        _require_nonnegative_int(self.forward_sequence, "forward_sequence")
        _require_positive_int(self.required_forward_periods, "required_forward_periods")
        _require_nonnegative_int(
            self.qualification_periods_used, "qualification_periods_used"
        )
        _require_nonnegative_int(
            self.qualification_duration_seconds, "qualification_duration_seconds"
        )
        if self.qualification_periods_used > self.required_forward_periods:
            raise ShadowForwardPromotionIntegrityError(
                "qualification period count may not exceed frozen horizon"
            )
        if (self.qualification_started_at is None) != (
            self.qualification_ended_at is None
        ):
            raise ShadowForwardPromotionIntegrityError(
                "qualification time bounds must both be present or both absent"
            )
        if self.qualification_started_at is None:
            if (
                self.qualification_periods_used != 0
                or self.qualification_duration_seconds != 0
                or self.qualification_head_hash != GENESIS_HASH
            ):
                raise ShadowForwardPromotionIntegrityError(
                    "empty qualification window requires zero counts and genesis head"
                )
        else:
            _require_aware(self.qualification_started_at, "qualification_started_at")
            _require_aware(self.qualification_ended_at, "qualification_ended_at")
            if _utc(self.qualification_started_at) >= _utc(self.qualification_ended_at):
                raise ShadowForwardPromotionIntegrityError(
                    "qualification window must have positive duration"
                )
        _require_finite_decimal(self.cumulative_return, "cumulative_return")
        if self.cumulative_return <= Decimal("-1"):
            raise ShadowForwardPromotionIntegrityError(
                "cumulative_return must be greater than -1"
            )
        _require_finite_decimal(
            self.peak_to_trough_drawdown, "peak_to_trough_drawdown"
        )
        if not Decimal("0") <= self.peak_to_trough_drawdown <= Decimal("1"):
            raise ShadowForwardPromotionIntegrityError(
                "peak_to_trough_drawdown must be within [0,1]"
            )
        if not isinstance(self.status, ShadowForwardPromotionStatus):
            raise ShadowForwardPromotionIntegrityError(
                "status must use ShadowForwardPromotionStatus"
            )
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ShadowForwardPromotionIntegrityError(
                "reason_codes must be unique sorted"
            )
        if any(not isinstance(code, str) or not code.strip() for code in self.reason_codes):
            raise ShadowForwardPromotionIntegrityError(
                "reason_codes must contain non-empty strings"
            )
        if self.status is ShadowForwardPromotionStatus.PASS and self.reason_codes:
            raise ShadowForwardPromotionIntegrityError(
                "PASS W84 evidence may not carry failure reasons"
            )
        if self.status is not ShadowForwardPromotionStatus.PASS and not self.reason_codes:
            raise ShadowForwardPromotionIntegrityError(
                "non-PASS W84 evidence requires reason codes"
            )
        for label, value in (
            ("exact_candidate_shadow_bound", self.exact_candidate_shadow_bound),
            (
                "policy_preregistered_in_shadow_config",
                self.policy_preregistered_in_shadow_config,
            ),
            ("forward_policy_committed", self.forward_policy_committed),
            ("full_observed_forward_tail_bound", self.full_observed_forward_tail_bound),
            ("fixed_forward_window_bound", self.fixed_forward_window_bound),
        ):
            if value is not True:
                raise ShadowForwardPromotionIntegrityError(
                    f"W84 evidence requires {label}=true"
                )
        _require_no_authority(
            paper=self.paper_candidate_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
            label="W84 evidence",
        )
        _require_aware(self.assessed_at, "assessed_at")
        if self.qualification_ended_at is not None and _utc(self.assessed_at) < _utc(
            self.qualification_ended_at
        ):
            raise ShadowForwardPromotionIntegrityError(
                "W84 assessment cannot predate qualification evidence"
            )
        if self.evidence_hash != _hash(_evidence_payload(self, include_hash=False)):
            raise ShadowForwardPromotionIntegrityError(
                "shadow/forward promotion evidence hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _evidence_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PromotionShadowForwardResolution:
    resolution_id: str
    contract_version: str
    evidence_id: str
    evidence_hash: str
    policy_id: str
    policy_hash: str
    w83_resolution_id: str
    w83_resolution_hash: str
    promotion_policy_id: str
    promotion_policy_hash: str
    selected_trial_id: str
    selected_trial_fingerprint: str
    selected_strategy_id: str
    selected_strategy_version: str
    strategy_spec_hash: str
    runtime_code_hash: str
    resolved_promotion_blockers: tuple[str, ...]
    remaining_promotion_blockers: tuple[str, ...]
    strategy_version_execution_bound: bool
    shadow_forward_promotion_bound: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    resolved_at: datetime
    resolution_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("resolution_id", self.resolution_id),
            ("evidence_id", self.evidence_id),
            ("policy_id", self.policy_id),
            ("w83_resolution_id", self.w83_resolution_id),
            ("promotion_policy_id", self.promotion_policy_id),
            ("selected_trial_id", self.selected_trial_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            _require_id(value, label)
        if self.contract_version != PROMOTION_SHADOW_FORWARD_RESOLUTION_VERSION:
            raise ShadowForwardPromotionIntegrityError(
                "promotion resolution version is not canonical W84"
            )
        for label, value in (
            ("evidence_hash", self.evidence_hash),
            ("policy_hash", self.policy_hash),
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("runtime_code_hash", self.runtime_code_hash),
            ("resolution_hash", self.resolution_hash),
        ):
            _require_hash(value, label)
        if self.resolved_promotion_blockers != (SHADOW_FORWARD_BLOCKER,):
            raise ShadowForwardPromotionIntegrityError(
                "W84 may resolve only SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED"
            )
        if self.remaining_promotion_blockers != tuple(
            sorted(set(self.remaining_promotion_blockers))
        ):
            raise ShadowForwardPromotionIntegrityError(
                "remaining blockers must be unique sorted"
            )
        if SHADOW_FORWARD_BLOCKER in self.remaining_promotion_blockers:
            raise ShadowForwardPromotionIntegrityError(
                "W84 resolved blocker may not remain present"
            )
        if (
            self.strategy_version_execution_bound is not True
            or self.shadow_forward_promotion_bound is not True
        ):
            raise ShadowForwardPromotionIntegrityError(
                "W84 promotion binding flags are inconsistent"
            )
        _require_no_authority(
            paper=self.paper_candidate_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
            label="W84 resolution",
        )
        _require_aware(self.resolved_at, "resolved_at")
        if self.resolution_hash != _hash(_resolution_payload(self, include_hash=False)):
            raise ShadowForwardPromotionIntegrityError(
                "shadow/forward promotion resolution hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _resolution_payload(self, include_hash=True)


def build_shadow_forward_promotion_policy(
    *,
    policy_id: str,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
    shadow_config_id: str,
    shadow_initial_nav: Decimal,
    shadow_activated_at: datetime,
    forward_campaign_id: str,
    forward_activated_at: datetime,
    required_forward_periods: int,
    minimum_forward_duration_seconds: int,
    min_cumulative_return: Decimal,
    max_peak_to_trough_drawdown: Decimal,
    frozen_at: datetime,
) -> ShadowForwardPromotionPolicy:
    """Freeze candidate identity, shadow plan, forward horizon and thresholds pre-outcome."""

    _require_id(policy_id, "policy_id")
    _require_id(shadow_config_id, "shadow_config_id")
    _require_id(forward_campaign_id, "forward_campaign_id")
    _validate_w83_pair(
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
    )
    if _utc(frozen_at) < max(
        _utc(w83_resolution.resolved_at),
        _utc(binding_evidence.assessed_at),
    ):
        raise ShadowForwardPromotionIntegrityError(
            "W84 policy freeze cannot predate exact W83 proof"
        )
    values = {
        "policy_id": policy_id,
        "contract_version": SHADOW_FORWARD_PROMOTION_POLICY_VERSION,
        "w83_resolution_id": w83_resolution.resolution_id,
        "w83_resolution_hash": w83_resolution.resolution_hash,
        "w83_binding_id": binding_evidence.binding_id,
        "w83_binding_hash": binding_evidence.evidence_hash,
        "promotion_policy_id": w83_resolution.promotion_policy_id,
        "promotion_policy_hash": w83_resolution.promotion_policy_hash,
        "development_campaign_id": binding_evidence.development_campaign_id,
        "selected_trial_id": w83_resolution.selected_trial_id,
        "selected_trial_fingerprint": w83_resolution.selected_trial_fingerprint,
        "selected_strategy_id": w83_resolution.selected_strategy_id,
        "selected_strategy_version": w83_resolution.selected_strategy_version,
        "strategy_spec_hash": w83_resolution.strategy_spec_hash,
        "runtime_code_hash": w83_resolution.loaded_runtime_code_hash,
        "intent_fingerprint": w83_resolution.intent_fingerprint,
        "shadow_config_id": shadow_config_id,
        "shadow_initial_nav": shadow_initial_nav,
        "shadow_activated_at": shadow_activated_at,
        "forward_campaign_id": forward_campaign_id,
        "forward_activated_at": forward_activated_at,
        "required_forward_periods": required_forward_periods,
        "minimum_forward_duration_seconds": minimum_forward_duration_seconds,
        "min_cumulative_return": min_cumulative_return,
        "max_peak_to_trough_drawdown": max_peak_to_trough_drawdown,
        "frozen_at": frozen_at,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return ShadowForwardPromotionPolicy(
        **values,
        policy_hash=_hash(_policy_payload_from_values(values)),
    )


def build_candidate_shadow_config(
    *,
    policy: ShadowForwardPromotionPolicy,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
) -> FrozenShadowConfig:
    """Build the R5 config whose source hash commits the complete W84 preregistration."""

    _validate_policy(policy)
    _validate_w83_pair(
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
    )
    _validate_policy_w83_binding(
        policy=policy,
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
    )
    return FrozenShadowConfig(
        config_id=policy.shadow_config_id,
        activated_at=policy.shadow_activated_at,
        initial_nav=policy.shadow_initial_nav,
        strategy_weights={w83_resolution.selected_strategy_id: Decimal("1")},
        source_config_hash=policy.policy_hash,
    )


def build_bound_forward_policy(
    *,
    policy: ShadowForwardPromotionPolicy,
    shadow_config: FrozenShadowConfig,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
) -> FrozenForwardPolicy:
    _validate_policy(policy)
    _validate_w83_pair(
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
    )
    _validate_policy_w83_binding(
        policy=policy,
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
    )
    _validate_candidate_shadow_config(
        policy=policy,
        shadow_config=shadow_config,
        w83_resolution=w83_resolution,
    )
    return FrozenForwardPolicy(
        campaign_id=policy.forward_campaign_id,
        activated_at=policy.forward_activated_at,
        shadow_config_fingerprint=shadow_config.fingerprint,
        frozen_parameters_hash=policy.policy_hash,
        source_code_hash=w83_resolution.loaded_runtime_code_hash,
    )


def assess_shadow_forward_promotion(
    *,
    evidence_id: str,
    policy: ShadowForwardPromotionPolicy,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
    shadow_registry: SQLitePortfolioShadowRegistry,
    forward_registry: SQLiteForwardEvidenceRegistry,
    assessed_at: datetime,
) -> ShadowForwardPromotionEvidence:
    """Read and bind verified R5 evidence to a fixed preregistered qualification window."""

    _require_id(evidence_id, "evidence_id")
    if not isinstance(shadow_registry, SQLitePortfolioShadowRegistry):
        raise TypeError("shadow_registry must be SQLitePortfolioShadowRegistry")
    if not isinstance(forward_registry, SQLiteForwardEvidenceRegistry):
        raise TypeError("forward_registry must be SQLiteForwardEvidenceRegistry")
    _validate_policy(policy)
    _validate_w83_pair(
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
    )
    _validate_policy_w83_binding(
        policy=policy,
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
    )
    _require_aware(assessed_at, "assessed_at")

    shadow_config = shadow_registry.get_config()
    shadow_records = shadow_registry.list_records()
    shadow_control = shadow_registry.control_state()
    forward_policy = forward_registry.get_policy()
    forward_records = forward_registry.list_records()
    forward_control = forward_registry.control_state()

    _validate_candidate_shadow_config(
        policy=policy,
        shadow_config=shadow_config,
        w83_resolution=w83_resolution,
    )
    if (
        forward_policy.campaign_id != policy.forward_campaign_id
        or forward_policy.activated_at != policy.forward_activated_at
        or forward_policy.shadow_config_fingerprint != shadow_config.fingerprint
        or forward_policy.frozen_parameters_hash != policy.policy_hash
        or forward_policy.source_code_hash != w83_resolution.loaded_runtime_code_hash
    ):
        raise ShadowForwardPromotionIntegrityError(
            "forward policy does not exactly commit frozen W84 policy/runtime"
        )

    _validate_control_snapshot(
        shadow_sequence=shadow_control.sequence,
        shadow_head_hash=shadow_control.head_hash,
        shadow_records=shadow_records,
        forward_sequence=forward_control.sequence,
        forward_head_hash=forward_control.head_hash,
        forward_records=forward_records,
    )

    eligible_shadow = tuple(
        record
        for record in shadow_records
        if _utc(record.period_started_at) >= _utc(policy.forward_activated_at)
    )
    if tuple(record.record_hash for record in eligible_shadow) != tuple(
        record.shadow_record_hash for record in forward_records
    ):
        raise ShadowForwardPromotionIntegrityError(
            "forward evidence must include complete observed eligible shadow tail"
        )
    for shadow_record, forward_record in zip(eligible_shadow, forward_records):
        if (
            shadow_record.period_started_at != forward_record.period_started_at
            or shadow_record.period_ended_at != forward_record.period_ended_at
            or shadow_record.config_fingerprint
            != forward_record.shadow_config_fingerprint
            or shadow_record.weighted_return != forward_record.portfolio_return
            or shadow_record.nav_after != forward_record.nav_after
        ):
            raise ShadowForwardPromotionIntegrityError(
                "forward evidence no longer matches exact verified shadow record"
            )

    # Detect append races between the first verified snapshot and evidence materialization.
    shadow_control_after = shadow_registry.control_state()
    forward_control_after = forward_registry.control_state()
    if shadow_control_after != shadow_control or forward_control_after != forward_control:
        raise ShadowForwardPromotionIntegrityError(
            "R5 evidence changed during W84 assessment"
        )

    qualification_records = forward_records[: policy.required_forward_periods]
    periods_used = len(qualification_records)
    start_at = qualification_records[0].period_started_at if qualification_records else None
    end_at = qualification_records[-1].period_ended_at if qualification_records else None
    duration_seconds = (
        int((_utc(end_at) - _utc(start_at)).total_seconds())
        if start_at is not None and end_at is not None
        else 0
    )
    cumulative_return, max_drawdown = _forward_metrics(qualification_records)
    qualification_head = (
        qualification_records[-1].evidence_hash
        if qualification_records
        else GENESIS_HASH
    )

    reasons: list[str] = []
    if periods_used < policy.required_forward_periods:
        reasons.append("FORWARD_WINDOW_INCOMPLETE")
        status = ShadowForwardPromotionStatus.PENDING
    else:
        if len(forward_records) > policy.required_forward_periods:
            reasons.append("FORWARD_WINDOW_OVERRUN")
        if _utc(start_at) != _utc(policy.forward_activated_at):
            reasons.append("FORWARD_START_MISMATCH")
        if duration_seconds < policy.minimum_forward_duration_seconds:
            reasons.append("FORWARD_DURATION_BELOW_MINIMUM")
        if cumulative_return < policy.min_cumulative_return:
            reasons.append("FORWARD_CUMULATIVE_RETURN_BELOW_MINIMUM")
        if max_drawdown > policy.max_peak_to_trough_drawdown:
            reasons.append("FORWARD_DRAWDOWN_ABOVE_MAXIMUM")
        status = (
            ShadowForwardPromotionStatus.PASS
            if not reasons
            else ShadowForwardPromotionStatus.FAIL
        )
    reasons_tuple = tuple(sorted(reasons))
    if end_at is not None and _utc(assessed_at) < _utc(end_at):
        raise ShadowForwardPromotionIntegrityError(
            "W84 assessment cannot predate qualification evidence"
        )

    values = {
        "evidence_id": evidence_id,
        "contract_version": SHADOW_FORWARD_PROMOTION_EVIDENCE_VERSION,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "w83_resolution_id": w83_resolution.resolution_id,
        "w83_resolution_hash": w83_resolution.resolution_hash,
        "w83_binding_hash": binding_evidence.evidence_hash,
        "selected_strategy_id": w83_resolution.selected_strategy_id,
        "selected_strategy_version": w83_resolution.selected_strategy_version,
        "strategy_spec_hash": w83_resolution.strategy_spec_hash,
        "runtime_code_hash": w83_resolution.loaded_runtime_code_hash,
        "shadow_config_fingerprint": shadow_config.fingerprint,
        "shadow_policy_commitment_hash": shadow_config.source_config_hash,
        "shadow_control_hash": shadow_control.control_hash,
        "shadow_sequence": shadow_control.sequence,
        "shadow_head_hash": shadow_control.head_hash,
        "forward_policy_fingerprint": forward_policy.fingerprint,
        "forward_control_hash": forward_control.control_hash,
        "forward_sequence": forward_control.sequence,
        "forward_head_hash": forward_control.head_hash,
        "required_forward_periods": policy.required_forward_periods,
        "qualification_periods_used": periods_used,
        "qualification_head_hash": qualification_head,
        "qualification_started_at": start_at,
        "qualification_ended_at": end_at,
        "qualification_duration_seconds": duration_seconds,
        "cumulative_return": cumulative_return,
        "peak_to_trough_drawdown": max_drawdown,
        "status": status,
        "reason_codes": reasons_tuple,
        "exact_candidate_shadow_bound": True,
        "policy_preregistered_in_shadow_config": True,
        "forward_policy_committed": True,
        "full_observed_forward_tail_bound": True,
        "fixed_forward_window_bound": True,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "assessed_at": assessed_at,
    }
    return ShadowForwardPromotionEvidence(
        **values,
        evidence_hash=_hash(_evidence_payload_from_values(values)),
    )


def resolve_promotion_shadow_forward_binding(
    *,
    resolution_id: str,
    evidence: ShadowForwardPromotionEvidence,
    policy: ShadowForwardPromotionPolicy,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
    resolved_at: datetime,
) -> PromotionShadowForwardResolution:
    """Remove only the Shadow/Forward blocker for exact PASS W84 evidence."""

    _require_id(resolution_id, "resolution_id")
    if not isinstance(evidence, ShadowForwardPromotionEvidence):
        raise TypeError("evidence must be ShadowForwardPromotionEvidence")
    _validate_policy(policy)
    _validate_w83_pair(
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
    )
    _validate_policy_w83_binding(
        policy=policy,
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
    )
    _validate_evidence(evidence)
    if evidence.status is not ShadowForwardPromotionStatus.PASS:
        raise ShadowForwardPromotionIntegrityError(
            "W84 cannot resolve blocker without PASS shadow/forward evidence"
        )
    if (
        evidence.policy_id != policy.policy_id
        or evidence.policy_hash != policy.policy_hash
        or evidence.w83_resolution_id != w83_resolution.resolution_id
        or evidence.w83_resolution_hash != w83_resolution.resolution_hash
        or evidence.w83_binding_hash != binding_evidence.evidence_hash
        or evidence.selected_strategy_id != w83_resolution.selected_strategy_id
        or evidence.selected_strategy_version != w83_resolution.selected_strategy_version
        or evidence.strategy_spec_hash != w83_resolution.strategy_spec_hash
        or evidence.runtime_code_hash != w83_resolution.loaded_runtime_code_hash
        or evidence.shadow_policy_commitment_hash != policy.policy_hash
        or evidence.required_forward_periods != policy.required_forward_periods
        or evidence.qualification_periods_used != policy.required_forward_periods
    ):
        raise ShadowForwardPromotionIntegrityError(
            "W84 evidence does not match exact frozen candidate/policy/window"
        )
    _require_aware(resolved_at, "resolved_at")
    if _utc(resolved_at) < max(
        _utc(evidence.assessed_at),
        _utc(w83_resolution.resolved_at),
    ):
        raise ShadowForwardPromotionIntegrityError(
            "W84 resolution violates temporal causality"
        )

    remaining = tuple(
        sorted(
            blocker
            for blocker in w83_resolution.remaining_promotion_blockers
            if blocker != SHADOW_FORWARD_BLOCKER
        )
    )
    values = {
        "resolution_id": resolution_id,
        "contract_version": PROMOTION_SHADOW_FORWARD_RESOLUTION_VERSION,
        "evidence_id": evidence.evidence_id,
        "evidence_hash": evidence.evidence_hash,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "w83_resolution_id": w83_resolution.resolution_id,
        "w83_resolution_hash": w83_resolution.resolution_hash,
        "promotion_policy_id": w83_resolution.promotion_policy_id,
        "promotion_policy_hash": w83_resolution.promotion_policy_hash,
        "selected_trial_id": w83_resolution.selected_trial_id,
        "selected_trial_fingerprint": w83_resolution.selected_trial_fingerprint,
        "selected_strategy_id": w83_resolution.selected_strategy_id,
        "selected_strategy_version": w83_resolution.selected_strategy_version,
        "strategy_spec_hash": w83_resolution.strategy_spec_hash,
        "runtime_code_hash": w83_resolution.loaded_runtime_code_hash,
        "resolved_promotion_blockers": (SHADOW_FORWARD_BLOCKER,),
        "remaining_promotion_blockers": remaining,
        "strategy_version_execution_bound": True,
        "shadow_forward_promotion_bound": True,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "resolved_at": resolved_at,
    }
    return PromotionShadowForwardResolution(
        **values,
        resolution_hash=_hash(_resolution_payload_from_values(values)),
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
    ):
        raise ShadowForwardPromotionIntegrityError("W83 input hash mismatch")
    if (
        binding_evidence.status is not ExecutionStrategyBindingStatus.PASS
        or binding_evidence.strategy_version_binding_proven is not True
        or w83_resolution.strategy_version_execution_bound is not True
        or w83_resolution.shadow_forward_promotion_bound is not False
        or SHADOW_FORWARD_BLOCKER
        not in w83_resolution.remaining_promotion_blockers
    ):
        raise ShadowForwardPromotionIntegrityError(
            "W83 prerequisite is not exact unresolved Shadow/Forward input"
        )
    _require_no_authority(
        paper=w83_resolution.paper_candidate_authorized,
        external=w83_resolution.external_execution_authorized,
        runtime=w83_resolution.runtime_execution_authorized,
        capital=w83_resolution.capital_authority,
        live=w83_resolution.live_trading,
        label="W83 prerequisite",
    )
    _require_no_authority(
        paper=binding_evidence.paper_candidate_authorized,
        external=binding_evidence.external_execution_authorized,
        runtime=binding_evidence.runtime_execution_authorized,
        capital=binding_evidence.capital_authority,
        live=binding_evidence.live_trading,
        label="W83 binding",
    )
    if (
        w83_resolution.binding_id != binding_evidence.binding_id
        or w83_resolution.binding_evidence_hash != binding_evidence.evidence_hash
        or w83_resolution.promotion_policy_id != binding_evidence.promotion_policy_id
        or w83_resolution.promotion_policy_hash != binding_evidence.promotion_policy_hash
        or w83_resolution.selected_trial_id != binding_evidence.selected_trial_id
        or w83_resolution.selected_trial_fingerprint
        != binding_evidence.selected_trial_fingerprint
        or w83_resolution.selected_strategy_id != binding_evidence.selected_strategy_id
        or w83_resolution.selected_strategy_version
        != binding_evidence.selected_strategy_version
        or w83_resolution.strategy_spec_hash != binding_evidence.strategy_spec_hash
        or w83_resolution.intent_fingerprint != binding_evidence.intent_fingerprint
        or w83_resolution.trial_code_version != binding_evidence.trial_code_version
        or w83_resolution.loaded_runtime_code_hash
        != binding_evidence.trial_code_version
    ):
        raise ShadowForwardPromotionIntegrityError(
            "W83 resolution and binding evidence identity mismatch"
        )


def _validate_policy(policy: ShadowForwardPromotionPolicy) -> None:
    if not isinstance(policy, ShadowForwardPromotionPolicy):
        raise TypeError("policy must be ShadowForwardPromotionPolicy")
    if policy.policy_hash != _hash(_policy_payload(policy, include_hash=False)):
        raise ShadowForwardPromotionIntegrityError(
            "shadow/forward promotion policy hash mismatch"
        )


def _validate_policy_w83_binding(
    *,
    policy: ShadowForwardPromotionPolicy,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
) -> None:
    if (
        policy.w83_resolution_id != w83_resolution.resolution_id
        or policy.w83_resolution_hash != w83_resolution.resolution_hash
        or policy.w83_binding_id != binding_evidence.binding_id
        or policy.w83_binding_hash != binding_evidence.evidence_hash
        or policy.promotion_policy_id != w83_resolution.promotion_policy_id
        or policy.promotion_policy_hash != w83_resolution.promotion_policy_hash
        or policy.development_campaign_id != binding_evidence.development_campaign_id
        or policy.selected_trial_id != w83_resolution.selected_trial_id
        or policy.selected_trial_fingerprint
        != w83_resolution.selected_trial_fingerprint
        or policy.selected_strategy_id != w83_resolution.selected_strategy_id
        or policy.selected_strategy_version
        != w83_resolution.selected_strategy_version
        or policy.strategy_spec_hash != w83_resolution.strategy_spec_hash
        or policy.runtime_code_hash != w83_resolution.loaded_runtime_code_hash
        or policy.intent_fingerprint != w83_resolution.intent_fingerprint
    ):
        raise ShadowForwardPromotionIntegrityError(
            "W84 policy does not match exact W83 candidate/runtime identity"
        )
    if _utc(policy.frozen_at) < max(
        _utc(w83_resolution.resolved_at),
        _utc(binding_evidence.assessed_at),
    ):
        raise ShadowForwardPromotionIntegrityError(
            "W84 policy freeze predates exact W83 proof"
        )


def _validate_candidate_shadow_config(
    *,
    policy: ShadowForwardPromotionPolicy,
    shadow_config: FrozenShadowConfig,
    w83_resolution: PromotionStrategyVersionResolution,
) -> None:
    if not isinstance(shadow_config, FrozenShadowConfig):
        raise TypeError("shadow_config must be FrozenShadowConfig")
    if (
        shadow_config.config_id != policy.shadow_config_id
        or shadow_config.initial_nav != policy.shadow_initial_nav
        or shadow_config.activated_at != policy.shadow_activated_at
        or shadow_config.source_config_hash != policy.policy_hash
    ):
        raise ShadowForwardPromotionIntegrityError(
            "shadow config does not exactly commit frozen W84 policy"
        )
    if dict(shadow_config.strategy_weights) != {
        w83_resolution.selected_strategy_id: Decimal("1")
    }:
        raise ShadowForwardPromotionIntegrityError(
            "W84 requires exclusive 100% selected-candidate shadow weight"
        )


def _validate_control_snapshot(
    *,
    shadow_sequence: int,
    shadow_head_hash: str,
    shadow_records: tuple,
    forward_sequence: int,
    forward_head_hash: str,
    forward_records: tuple,
) -> None:
    expected_shadow_head = shadow_records[-1].record_hash if shadow_records else GENESIS_HASH
    expected_forward_head = (
        forward_records[-1].evidence_hash if forward_records else GENESIS_HASH
    )
    if shadow_sequence != len(shadow_records) or shadow_head_hash != expected_shadow_head:
        raise ShadowForwardPromotionIntegrityError(
            "shadow control snapshot does not match verified records"
        )
    if (
        forward_sequence != len(forward_records)
        or forward_head_hash != expected_forward_head
    ):
        raise ShadowForwardPromotionIntegrityError(
            "forward control snapshot does not match verified records"
        )


def _forward_metrics(records: tuple) -> tuple[Decimal, Decimal]:
    wealth = Decimal("1")
    peak = Decimal("1")
    max_drawdown = Decimal("0")
    for record in records:
        wealth *= Decimal("1") + record.portfolio_return
        if wealth > peak:
            peak = wealth
        drawdown = (peak - wealth) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return wealth - Decimal("1"), max_drawdown


def _validate_evidence(evidence: ShadowForwardPromotionEvidence) -> None:
    if evidence.evidence_hash != _hash(_evidence_payload(evidence, include_hash=False)):
        raise ShadowForwardPromotionIntegrityError(
            "shadow/forward promotion evidence hash mismatch"
        )
    _require_no_authority(
        paper=evidence.paper_candidate_authorized,
        external=evidence.external_execution_authorized,
        runtime=evidence.runtime_execution_authorized,
        capital=evidence.capital_authority,
        live=evidence.live_trading,
        label="W84 evidence",
    )


def _policy_payload(
    value: ShadowForwardPromotionPolicy, *, include_hash: bool
) -> dict[str, object]:
    payload = _policy_payload_from_values(
        {
            name: getattr(value, name)
            for name in (
                "policy_id",
                "contract_version",
                "w83_resolution_id",
                "w83_resolution_hash",
                "w83_binding_id",
                "w83_binding_hash",
                "promotion_policy_id",
                "promotion_policy_hash",
                "development_campaign_id",
                "selected_trial_id",
                "selected_trial_fingerprint",
                "selected_strategy_id",
                "selected_strategy_version",
                "strategy_spec_hash",
                "runtime_code_hash",
                "intent_fingerprint",
                "shadow_config_id",
                "shadow_initial_nav",
                "shadow_activated_at",
                "forward_campaign_id",
                "forward_activated_at",
                "required_forward_periods",
                "minimum_forward_duration_seconds",
                "min_cumulative_return",
                "max_peak_to_trough_drawdown",
                "frozen_at",
                "paper_candidate_authorized",
                "external_execution_authorized",
                "runtime_execution_authorized",
                "capital_authority",
                "live_trading",
            )
        }
    )
    if include_hash:
        payload["policy_hash"] = value.policy_hash
    return payload


def _policy_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["shadow_initial_nav"] = str(payload["shadow_initial_nav"])
    payload["shadow_activated_at"] = _utc_iso(payload["shadow_activated_at"])
    payload["forward_activated_at"] = _utc_iso(payload["forward_activated_at"])
    payload["min_cumulative_return"] = str(payload["min_cumulative_return"])
    payload["max_peak_to_trough_drawdown"] = str(
        payload["max_peak_to_trough_drawdown"]
    )
    payload["frozen_at"] = _utc_iso(payload["frozen_at"])
    return payload


def _evidence_payload(
    value: ShadowForwardPromotionEvidence, *, include_hash: bool
) -> dict[str, object]:
    payload = _evidence_payload_from_values(
        {
            name: getattr(value, name)
            for name in (
                "evidence_id",
                "contract_version",
                "policy_id",
                "policy_hash",
                "w83_resolution_id",
                "w83_resolution_hash",
                "w83_binding_hash",
                "selected_strategy_id",
                "selected_strategy_version",
                "strategy_spec_hash",
                "runtime_code_hash",
                "shadow_config_fingerprint",
                "shadow_policy_commitment_hash",
                "shadow_control_hash",
                "shadow_sequence",
                "shadow_head_hash",
                "forward_policy_fingerprint",
                "forward_control_hash",
                "forward_sequence",
                "forward_head_hash",
                "required_forward_periods",
                "qualification_periods_used",
                "qualification_head_hash",
                "qualification_started_at",
                "qualification_ended_at",
                "qualification_duration_seconds",
                "cumulative_return",
                "peak_to_trough_drawdown",
                "status",
                "reason_codes",
                "exact_candidate_shadow_bound",
                "policy_preregistered_in_shadow_config",
                "forward_policy_committed",
                "full_observed_forward_tail_bound",
                "fixed_forward_window_bound",
                "paper_candidate_authorized",
                "external_execution_authorized",
                "runtime_execution_authorized",
                "capital_authority",
                "live_trading",
                "assessed_at",
            )
        }
    )
    if include_hash:
        payload["evidence_hash"] = value.evidence_hash
    return payload


def _evidence_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["qualification_started_at"] = _optional_utc_iso(
        payload["qualification_started_at"]
    )
    payload["qualification_ended_at"] = _optional_utc_iso(
        payload["qualification_ended_at"]
    )
    payload["cumulative_return"] = str(payload["cumulative_return"])
    payload["peak_to_trough_drawdown"] = str(payload["peak_to_trough_drawdown"])
    payload["status"] = str(payload["status"])
    payload["reason_codes"] = list(payload["reason_codes"])
    payload["assessed_at"] = _utc_iso(payload["assessed_at"])
    return payload


def _resolution_payload(
    value: PromotionShadowForwardResolution, *, include_hash: bool
) -> dict[str, object]:
    payload = _resolution_payload_from_values(
        {
            name: getattr(value, name)
            for name in (
                "resolution_id",
                "contract_version",
                "evidence_id",
                "evidence_hash",
                "policy_id",
                "policy_hash",
                "w83_resolution_id",
                "w83_resolution_hash",
                "promotion_policy_id",
                "promotion_policy_hash",
                "selected_trial_id",
                "selected_trial_fingerprint",
                "selected_strategy_id",
                "selected_strategy_version",
                "strategy_spec_hash",
                "runtime_code_hash",
                "resolved_promotion_blockers",
                "remaining_promotion_blockers",
                "strategy_version_execution_bound",
                "shadow_forward_promotion_bound",
                "paper_candidate_authorized",
                "external_execution_authorized",
                "runtime_execution_authorized",
                "capital_authority",
                "live_trading",
                "resolved_at",
            )
        }
    )
    if include_hash:
        payload["resolution_hash"] = value.resolution_hash
    return payload


def _resolution_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["resolved_promotion_blockers"] = list(
        payload["resolved_promotion_blockers"]
    )
    payload["remaining_promotion_blockers"] = list(
        payload["remaining_promotion_blockers"]
    )
    payload["resolved_at"] = _utc_iso(payload["resolved_at"])
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
        raise ShadowForwardPromotionIntegrityError(
            f"{label} may not grant PAPER, execution, capital, or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ShadowForwardPromotionIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ShadowForwardPromotionIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ShadowForwardPromotionIntegrityError(f"{label} must be integer >=1")


def _require_nonnegative_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ShadowForwardPromotionIntegrityError(f"{label} must be integer >=0")


def _require_finite_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ShadowForwardPromotionIntegrityError(f"{label} must be finite Decimal")


def _require_positive_decimal(value: Decimal, label: str) -> None:
    _require_finite_decimal(value, label)
    if value <= 0:
        raise ShadowForwardPromotionIntegrityError(f"{label} must be > 0")


def _require_aware(value: datetime, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ShadowForwardPromotionIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _utc_iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise ShadowForwardPromotionIntegrityError("datetime value required")
    return _utc(value).isoformat()


def _optional_utc_iso(value: object) -> str | None:
    if value is None:
        return None
    return _utc_iso(value)


def _hash(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "GENESIS_HASH",
    "PROMOTION_SHADOW_FORWARD_RESOLUTION_VERSION",
    "SHADOW_FORWARD_PROMOTION_EVIDENCE_VERSION",
    "SHADOW_FORWARD_PROMOTION_POLICY_VERSION",
    "PromotionShadowForwardResolution",
    "ShadowForwardPromotionEvidence",
    "ShadowForwardPromotionIntegrityError",
    "ShadowForwardPromotionPolicy",
    "ShadowForwardPromotionStatus",
    "assess_shadow_forward_promotion",
    "build_bound_forward_policy",
    "build_candidate_shadow_config",
    "build_shadow_forward_promotion_policy",
    "resolve_promotion_shadow_forward_binding",
]
