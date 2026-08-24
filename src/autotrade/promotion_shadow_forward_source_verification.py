from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

import autotrade.forward_shadow_measurement as measurement_module
import autotrade.promotion_shadow_forward_binding as w84
from autotrade.forward_shadow_measurement import (
    ForwardMeasurementPlan,
    ForwardShadowMeasurementReceipt,
    measurement_receipts_hash,
    verify_shadow_measurement_binding,
)
from autotrade.promotion_fee_accounting import SHADOW_FORWARD_BLOCKER
from autotrade.promotion_shadow_forward_binding import (
    PromotionShadowForwardResolution,
    ShadowForwardPromotionEvidence,
    ShadowForwardPromotionPolicy,
    ShadowForwardPromotionStatus,
)
from autotrade.promotion_strategy_version_binding import PromotionStrategyVersionResolution
from autotrade.research.forward import SQLiteForwardEvidenceRegistry
from autotrade.research.shadow import SQLitePortfolioShadowRegistry
from autotrade.strategy_execution_binding import ExecutionStrategyBindingEvidence


SOURCE_VERIFICATION_VERSION = "W84_SHADOW_FORWARD_SOURCE_VERIFICATION_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class ShadowForwardSourceVerificationIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PromotionShadowForwardSourceVerification:
    verification_id: str
    contract_version: str
    base_resolution_id: str
    base_resolution_hash: str
    evidence_id: str
    evidence_hash: str
    policy_id: str
    policy_hash: str
    w83_resolution_hash: str
    w83_binding_hash: str
    measurement_plan_hash: str
    measurement_runtime_hash: str
    shadow_config_fingerprint: str
    shadow_control_hash: str
    shadow_sequence: int
    shadow_head_hash: str
    forward_policy_fingerprint: str
    forward_control_hash: str
    forward_sequence: int
    forward_head_hash: str
    measurement_receipts_hash: str
    measurement_head_hash: str
    measurement_receipts_count: int
    qualification_started_at: datetime
    qualification_ended_at: datetime
    qualification_duration_seconds: int
    cumulative_return: Decimal
    peak_to_trough_drawdown: Decimal
    capture_lag_seconds: int
    assessment_delay_seconds: int
    source_truth_verified: bool
    resolved_promotion_blockers: tuple[str, ...]
    remaining_promotion_blockers: tuple[str, ...]
    strategy_version_execution_bound: bool
    shadow_forward_promotion_bound: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    verified_at: datetime
    verification_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("verification_id", self.verification_id),
            ("base_resolution_id", self.base_resolution_id),
            ("evidence_id", self.evidence_id),
            ("policy_id", self.policy_id),
        ):
            _require_id(value, label)
        if self.contract_version != SOURCE_VERIFICATION_VERSION:
            raise ShadowForwardSourceVerificationIntegrityError(
                "source verification version is not canonical W84"
            )
        for label, value in (
            ("base_resolution_hash", self.base_resolution_hash),
            ("evidence_hash", self.evidence_hash),
            ("policy_hash", self.policy_hash),
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("w83_binding_hash", self.w83_binding_hash),
            ("measurement_plan_hash", self.measurement_plan_hash),
            ("measurement_runtime_hash", self.measurement_runtime_hash),
            ("shadow_config_fingerprint", self.shadow_config_fingerprint),
            ("shadow_control_hash", self.shadow_control_hash),
            ("shadow_head_hash", self.shadow_head_hash),
            ("forward_policy_fingerprint", self.forward_policy_fingerprint),
            ("forward_control_hash", self.forward_control_hash),
            ("forward_head_hash", self.forward_head_hash),
            ("measurement_receipts_hash", self.measurement_receipts_hash),
            ("measurement_head_hash", self.measurement_head_hash),
            ("verification_hash", self.verification_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("shadow_sequence", self.shadow_sequence),
            ("forward_sequence", self.forward_sequence),
            ("measurement_receipts_count", self.measurement_receipts_count),
            ("qualification_duration_seconds", self.qualification_duration_seconds),
            ("capture_lag_seconds", self.capture_lag_seconds),
            ("assessment_delay_seconds", self.assessment_delay_seconds),
        ):
            _require_nonnegative_int(value, label)
        for label, value in (
            ("qualification_started_at", self.qualification_started_at),
            ("qualification_ended_at", self.qualification_ended_at),
            ("verified_at", self.verified_at),
        ):
            _require_aware(value, label)
        if _utc(self.qualification_started_at) >= _utc(self.qualification_ended_at):
            raise ShadowForwardSourceVerificationIntegrityError(
                "source-verified qualification window must have positive duration"
            )
        _require_finite_decimal(self.cumulative_return, "cumulative_return")
        _require_finite_decimal(self.peak_to_trough_drawdown, "peak_to_trough_drawdown")
        if self.cumulative_return <= Decimal("-1"):
            raise ShadowForwardSourceVerificationIntegrityError(
                "source-verified cumulative return must be greater than -1"
            )
        if not Decimal("0") <= self.peak_to_trough_drawdown <= Decimal("1"):
            raise ShadowForwardSourceVerificationIntegrityError(
                "source-verified drawdown must be within [0,1]"
            )
        if self.source_truth_verified is not True:
            raise ShadowForwardSourceVerificationIntegrityError(
                "canonical W84 resolution requires source_truth_verified=true"
            )
        if self.resolved_promotion_blockers != (SHADOW_FORWARD_BLOCKER,):
            raise ShadowForwardSourceVerificationIntegrityError(
                "source verification may certify only the Shadow/Forward blocker resolution"
            )
        if self.remaining_promotion_blockers != tuple(
            sorted(set(self.remaining_promotion_blockers))
        ):
            raise ShadowForwardSourceVerificationIntegrityError(
                "remaining blockers must be unique sorted"
            )
        if SHADOW_FORWARD_BLOCKER in self.remaining_promotion_blockers:
            raise ShadowForwardSourceVerificationIntegrityError(
                "source-verified Shadow/Forward blocker may not remain present"
            )
        if (
            self.strategy_version_execution_bound is not True
            or self.shadow_forward_promotion_bound is not True
        ):
            raise ShadowForwardSourceVerificationIntegrityError(
                "source-verified W84 binding flags are inconsistent"
            )
        _require_no_authority(
            paper=self.paper_candidate_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
        )
        if self.verification_hash != _hash(_payload(self, include_hash=False)):
            raise ShadowForwardSourceVerificationIntegrityError(
                "source verification hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def verify_promotion_shadow_forward_resolution_sources(
    *,
    verification_id: str,
    base_resolution: PromotionShadowForwardResolution,
    evidence: ShadowForwardPromotionEvidence,
    policy: ShadowForwardPromotionPolicy,
    measurement_plan: ForwardMeasurementPlan,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
    shadow_registry: SQLitePortfolioShadowRegistry,
    forward_registry: SQLiteForwardEvidenceRegistry,
    measurement_receipts: tuple[ForwardShadowMeasurementReceipt, ...],
    verified_at: datetime,
) -> PromotionShadowForwardSourceVerification:
    """Re-prove the final W84 resolution from durable R5 + measurement truth.

    `PromotionShadowForwardResolution` is an intermediate identity/blocker receipt.
    This verifier is the canonical final W84 trust boundary: it does not trust a
    rehashed PASS evidence object. It re-reads the hash-verified R5 registries,
    rebinds deterministic measurement receipts, recomputes the fixed-horizon
    metrics and only then certifies the blocker removal. No registry mutation,
    broker, network, OMS, Safety or execution authority is granted here.
    """

    _require_id(verification_id, "verification_id")
    if not isinstance(base_resolution, PromotionShadowForwardResolution):
        raise TypeError("base_resolution must be PromotionShadowForwardResolution")
    if not isinstance(evidence, ShadowForwardPromotionEvidence):
        raise TypeError("evidence must be ShadowForwardPromotionEvidence")
    if not isinstance(policy, ShadowForwardPromotionPolicy):
        raise TypeError("policy must be ShadowForwardPromotionPolicy")
    if not isinstance(measurement_plan, ForwardMeasurementPlan):
        raise TypeError("measurement_plan must be ForwardMeasurementPlan")
    if not isinstance(shadow_registry, SQLitePortfolioShadowRegistry):
        raise TypeError("shadow_registry must be SQLitePortfolioShadowRegistry")
    if not isinstance(forward_registry, SQLiteForwardEvidenceRegistry):
        raise TypeError("forward_registry must be SQLiteForwardEvidenceRegistry")
    if not isinstance(measurement_receipts, tuple) or any(
        not isinstance(item, ForwardShadowMeasurementReceipt)
        for item in measurement_receipts
    ):
        raise TypeError("measurement_receipts must be tuple[ForwardShadowMeasurementReceipt, ...]")
    _require_aware(verified_at, "verified_at")

    _validate_parent_hashes(
        base_resolution=base_resolution,
        evidence=evidence,
        policy=policy,
        measurement_plan=measurement_plan,
    )
    try:
        w84._validate_w83_pair(
            w83_resolution=w83_resolution,
            binding_evidence=binding_evidence,
        )
        w84._validate_measurement_plan(
            measurement_plan=measurement_plan,
            w83_resolution=w83_resolution,
            binding_evidence=binding_evidence,
        )
        w84._validate_policy_bindings(
            policy=policy,
            measurement_plan=measurement_plan,
            w83_resolution=w83_resolution,
            binding_evidence=binding_evidence,
        )
    except (TypeError, w84.ShadowForwardPromotionIntegrityError) as exc:
        raise ShadowForwardSourceVerificationIntegrityError(
            "W84 source verification parent identity mismatch"
        ) from exc

    _validate_base_resolution_identity(
        base_resolution=base_resolution,
        evidence=evidence,
        policy=policy,
        measurement_plan=measurement_plan,
        w83_resolution=w83_resolution,
        binding_evidence=binding_evidence,
    )

    shadow_config = shadow_registry.get_config()
    shadow_records = shadow_registry.list_records()
    shadow_control = shadow_registry.control_state()
    forward_policy = forward_registry.get_policy()
    forward_records = forward_registry.list_records()
    forward_control = forward_registry.control_state()

    try:
        w84._validate_candidate_shadow_config(
            policy=policy,
            shadow_config=shadow_config,
            w83_resolution=w83_resolution,
        )
        w84._validate_control_snapshot(
            shadow_sequence=shadow_control.sequence,
            shadow_head_hash=shadow_control.head_hash,
            shadow_records=shadow_records,
            forward_sequence=forward_control.sequence,
            forward_head_hash=forward_control.head_hash,
            forward_records=forward_records,
        )
    except (TypeError, w84.ShadowForwardPromotionIntegrityError) as exc:
        raise ShadowForwardSourceVerificationIntegrityError(
            "W84 durable R5 source snapshot failed verification"
        ) from exc

    if (
        forward_policy.campaign_id != policy.forward_campaign_id
        or _utc(forward_policy.activated_at) != _utc(policy.forward_activated_at)
        or forward_policy.shadow_config_fingerprint != shadow_config.fingerprint
        or forward_policy.frozen_parameters_hash != policy.policy_hash
        or forward_policy.source_code_hash != policy.measurement_runtime_hash
    ):
        raise ShadowForwardSourceVerificationIntegrityError(
            "R5 Forward policy does not recommit exact W84 policy/runtime"
        )

    eligible_shadow = tuple(
        record
        for record in shadow_records
        if _utc(record.period_started_at) >= _utc(policy.forward_activated_at)
    )
    if len(eligible_shadow) != policy.required_forward_periods:
        raise ShadowForwardSourceVerificationIntegrityError(
            "source-verified PASS requires exact preregistered Shadow horizon"
        )
    if len(forward_records) != policy.required_forward_periods:
        raise ShadowForwardSourceVerificationIntegrityError(
            "source-verified PASS requires exact preregistered Forward horizon"
        )
    if tuple(record.record_hash for record in eligible_shadow) != tuple(
        record.shadow_record_hash for record in forward_records
    ):
        raise ShadowForwardSourceVerificationIntegrityError(
            "R5 Forward records do not cover exact eligible Shadow tail"
        )
    for shadow_record, forward_record in zip(eligible_shadow, forward_records):
        if (
            shadow_record.period_started_at != forward_record.period_started_at
            or shadow_record.period_ended_at != forward_record.period_ended_at
            or shadow_record.config_fingerprint != forward_record.shadow_config_fingerprint
            or shadow_record.weighted_return != forward_record.portfolio_return
            or shadow_record.nav_after != forward_record.nav_after
        ):
            raise ShadowForwardSourceVerificationIntegrityError(
                "R5 Forward record differs from durable Shadow source truth"
            )

    if len(measurement_receipts) != policy.required_forward_periods:
        raise ShadowForwardSourceVerificationIntegrityError(
            "source-verified PASS requires exact measurement receipt horizon"
        )
    try:
        measurement_head = verify_shadow_measurement_binding(
            plan=measurement_plan,
            policy_hash=policy.policy_hash,
            selected_strategy_id=w83_resolution.selected_strategy_id,
            shadow_records=eligible_shadow,
            receipts=measurement_receipts,
            assessed_at=evidence.assessed_at,
        )
        receipts_hash = measurement_receipts_hash(measurement_receipts)
    except measurement_module.ForwardShadowMeasurementIntegrityError as exc:
        raise ShadowForwardSourceVerificationIntegrityError(
            "measurement receipts do not bind exact durable Shadow source"
        ) from exc

    start_at = forward_records[0].period_started_at
    end_at = forward_records[-1].period_ended_at
    duration_seconds = int((_utc(end_at) - _utc(start_at)).total_seconds())
    cumulative_return, max_drawdown = _forward_metrics(forward_records)
    forward_head = forward_records[-1].evidence_hash

    if _utc(start_at) != _utc(policy.forward_activated_at):
        raise ShadowForwardSourceVerificationIntegrityError(
            "source-verified Forward window does not start at frozen activation"
        )
    if duration_seconds < policy.minimum_forward_duration_seconds:
        raise ShadowForwardSourceVerificationIntegrityError(
            "source-verified Forward duration is below frozen minimum"
        )
    if cumulative_return < policy.min_cumulative_return:
        raise ShadowForwardSourceVerificationIntegrityError(
            "source-verified cumulative return is below frozen threshold"
        )
    if max_drawdown > policy.max_peak_to_trough_drawdown:
        raise ShadowForwardSourceVerificationIntegrityError(
            "source-verified drawdown exceeds frozen threshold"
        )

    if any(receipt.captured_at != evidence.measurement_captured_at for receipt in measurement_receipts):
        raise ShadowForwardSourceVerificationIntegrityError(
            "measurement capture timestamp differs from exact source receipts"
        )
    data_cutoff = measurement_receipts[-1].period_ended_at
    capture_lag = int(
        (_utc(evidence.measurement_captured_at) - _utc(data_cutoff)).total_seconds()
    )
    assessment_delay = int(
        (_utc(evidence.assessed_at) - _utc(evidence.measurement_captured_at)).total_seconds()
    )
    if not 0 <= capture_lag <= policy.max_capture_lag_seconds:
        raise ShadowForwardSourceVerificationIntegrityError(
            "source-derived capture lag exceeds frozen budget"
        )
    if not 0 <= assessment_delay <= policy.max_assessment_delay_seconds:
        raise ShadowForwardSourceVerificationIntegrityError(
            "source-derived assessment delay exceeds frozen budget"
        )

    _validate_evidence_against_sources(
        evidence=evidence,
        policy=policy,
        shadow_config_fingerprint=shadow_config.fingerprint,
        shadow_control_hash=shadow_control.control_hash,
        shadow_sequence=shadow_control.sequence,
        shadow_head_hash=shadow_control.head_hash,
        forward_policy_fingerprint=forward_policy.fingerprint,
        forward_control_hash=forward_control.control_hash,
        forward_sequence=forward_control.sequence,
        forward_head_hash=forward_control.head_hash,
        receipts_hash=receipts_hash,
        measurement_head=measurement_head,
        start_at=start_at,
        end_at=end_at,
        duration_seconds=duration_seconds,
        cumulative_return=cumulative_return,
        max_drawdown=max_drawdown,
        data_cutoff=data_cutoff,
        capture_lag=capture_lag,
        assessment_delay=assessment_delay,
    )

    if (
        shadow_registry.control_state() != shadow_control
        or forward_registry.control_state() != forward_control
    ):
        raise ShadowForwardSourceVerificationIntegrityError(
            "R5 source truth changed during final W84 verification"
        )

    if _utc(verified_at) < max(
        _utc(base_resolution.resolved_at),
        _utc(evidence.assessed_at),
        _utc(w83_resolution.resolved_at),
    ):
        raise ShadowForwardSourceVerificationIntegrityError(
            "source verification violates temporal causality"
        )

    values = {
        "verification_id": verification_id,
        "contract_version": SOURCE_VERIFICATION_VERSION,
        "base_resolution_id": base_resolution.resolution_id,
        "base_resolution_hash": base_resolution.resolution_hash,
        "evidence_id": evidence.evidence_id,
        "evidence_hash": evidence.evidence_hash,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "w83_resolution_hash": w83_resolution.resolution_hash,
        "w83_binding_hash": binding_evidence.evidence_hash,
        "measurement_plan_hash": measurement_plan.plan_hash,
        "measurement_runtime_hash": measurement_plan.measurement_runtime_hash,
        "shadow_config_fingerprint": shadow_config.fingerprint,
        "shadow_control_hash": shadow_control.control_hash,
        "shadow_sequence": shadow_control.sequence,
        "shadow_head_hash": shadow_control.head_hash,
        "forward_policy_fingerprint": forward_policy.fingerprint,
        "forward_control_hash": forward_control.control_hash,
        "forward_sequence": forward_control.sequence,
        "forward_head_hash": forward_head,
        "measurement_receipts_hash": receipts_hash,
        "measurement_head_hash": measurement_head,
        "measurement_receipts_count": len(measurement_receipts),
        "qualification_started_at": start_at,
        "qualification_ended_at": end_at,
        "qualification_duration_seconds": duration_seconds,
        "cumulative_return": cumulative_return,
        "peak_to_trough_drawdown": max_drawdown,
        "capture_lag_seconds": capture_lag,
        "assessment_delay_seconds": assessment_delay,
        "source_truth_verified": True,
        "resolved_promotion_blockers": (SHADOW_FORWARD_BLOCKER,),
        "remaining_promotion_blockers": base_resolution.remaining_promotion_blockers,
        "strategy_version_execution_bound": True,
        "shadow_forward_promotion_bound": True,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "verified_at": verified_at,
    }
    return PromotionShadowForwardSourceVerification(
        **values,
        verification_hash=_hash(_payload_from_values(values)),
    )


def _validate_parent_hashes(
    *,
    base_resolution: PromotionShadowForwardResolution,
    evidence: ShadowForwardPromotionEvidence,
    policy: ShadowForwardPromotionPolicy,
    measurement_plan: ForwardMeasurementPlan,
) -> None:
    if base_resolution.resolution_hash != w84._hash(
        w84._resolution_payload(base_resolution, include_hash=False)
    ):
        raise ShadowForwardSourceVerificationIntegrityError(
            "base W84 resolution hash mismatch"
        )
    if evidence.evidence_hash != w84._hash(w84._evidence_payload(evidence, include_hash=False)):
        raise ShadowForwardSourceVerificationIntegrityError("W84 evidence hash mismatch")
    if policy.policy_hash != w84._hash(w84._policy_payload(policy, include_hash=False)):
        raise ShadowForwardSourceVerificationIntegrityError("W84 policy hash mismatch")
    if measurement_plan.plan_hash != measurement_module._hash(
        measurement_module._plan_payload(measurement_plan, include_hash=False)
    ):
        raise ShadowForwardSourceVerificationIntegrityError(
            "W84 measurement plan hash mismatch"
        )


def _validate_base_resolution_identity(
    *,
    base_resolution: PromotionShadowForwardResolution,
    evidence: ShadowForwardPromotionEvidence,
    policy: ShadowForwardPromotionPolicy,
    measurement_plan: ForwardMeasurementPlan,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
) -> None:
    if (
        base_resolution.evidence_id != evidence.evidence_id
        or base_resolution.evidence_hash != evidence.evidence_hash
        or base_resolution.policy_id != policy.policy_id
        or base_resolution.policy_hash != policy.policy_hash
        or base_resolution.w83_resolution_id != w83_resolution.resolution_id
        or base_resolution.w83_resolution_hash != w83_resolution.resolution_hash
        or base_resolution.selected_strategy_id != w83_resolution.selected_strategy_id
        or base_resolution.selected_strategy_version != w83_resolution.selected_strategy_version
        or base_resolution.strategy_spec_hash != w83_resolution.strategy_spec_hash
        or base_resolution.runtime_code_hash != w83_resolution.loaded_runtime_code_hash
        or base_resolution.measurement_plan_hash != measurement_plan.plan_hash
        or base_resolution.measurement_runtime_hash != measurement_plan.measurement_runtime_hash
        or base_resolution.resolved_promotion_blockers != (SHADOW_FORWARD_BLOCKER,)
        or SHADOW_FORWARD_BLOCKER in base_resolution.remaining_promotion_blockers
        or base_resolution.strategy_version_execution_bound is not True
        or base_resolution.shadow_forward_promotion_bound is not True
    ):
        raise ShadowForwardSourceVerificationIntegrityError(
            "base W84 resolution is not exact intermediate identity"
        )
    _require_no_authority(
        paper=base_resolution.paper_candidate_authorized,
        external=base_resolution.external_execution_authorized,
        runtime=base_resolution.runtime_execution_authorized,
        capital=base_resolution.capital_authority,
        live=base_resolution.live_trading,
    )
    if base_resolution.promotion_policy_hash != binding_evidence.promotion_policy_hash:
        raise ShadowForwardSourceVerificationIntegrityError(
            "base W84 resolution promotion policy identity mismatch"
        )


def _validate_evidence_against_sources(
    *,
    evidence: ShadowForwardPromotionEvidence,
    policy: ShadowForwardPromotionPolicy,
    shadow_config_fingerprint: str,
    shadow_control_hash: str,
    shadow_sequence: int,
    shadow_head_hash: str,
    forward_policy_fingerprint: str,
    forward_control_hash: str,
    forward_sequence: int,
    forward_head_hash: str,
    receipts_hash: str,
    measurement_head: str,
    start_at: datetime,
    end_at: datetime,
    duration_seconds: int,
    cumulative_return: Decimal,
    max_drawdown: Decimal,
    data_cutoff: datetime,
    capture_lag: int,
    assessment_delay: int,
) -> None:
    proof_flags = (
        evidence.exact_candidate_shadow_bound,
        evidence.policy_preregistered_in_shadow_config,
        evidence.measurement_plan_preregistered,
        evidence.per_observation_measurement_bound,
        evidence.prefix_only_measurement_bound,
        evidence.measurement_freshness_bound,
        evidence.forward_policy_committed,
        evidence.full_observed_forward_tail_bound,
        evidence.fixed_forward_window_bound,
    )
    if evidence.status is not ShadowForwardPromotionStatus.PASS or evidence.reason_codes != ():
        raise ShadowForwardSourceVerificationIntegrityError(
            "canonical source verification requires reason-free PASS evidence"
        )
    if any(value is not True for value in proof_flags):
        raise ShadowForwardSourceVerificationIntegrityError(
            "W84 PASS proof flags are not all source-authoritative"
        )
    if (
        evidence.required_forward_periods != policy.required_forward_periods
        or evidence.qualification_periods_used != policy.required_forward_periods
        or evidence.measurement_receipts_count != policy.required_forward_periods
        or evidence.shadow_config_fingerprint != shadow_config_fingerprint
        or evidence.shadow_policy_commitment_hash != policy.policy_hash
        or evidence.shadow_control_hash != shadow_control_hash
        or evidence.shadow_sequence != shadow_sequence
        or evidence.shadow_head_hash != shadow_head_hash
        or evidence.forward_policy_fingerprint != forward_policy_fingerprint
        or evidence.forward_control_hash != forward_control_hash
        or evidence.forward_sequence != forward_sequence
        or evidence.forward_head_hash != forward_head_hash
        or evidence.qualification_head_hash != forward_head_hash
        or evidence.measurement_receipts_hash != receipts_hash
        or evidence.measurement_head_hash != measurement_head
        or evidence.qualification_measurement_head_hash != measurement_head
        or evidence.qualification_started_at != start_at
        or evidence.qualification_ended_at != end_at
        or evidence.qualification_duration_seconds != duration_seconds
        or evidence.cumulative_return != cumulative_return
        or evidence.peak_to_trough_drawdown != max_drawdown
        or evidence.measurement_data_cutoff_at != data_cutoff
        or evidence.capture_lag_seconds != capture_lag
        or evidence.assessment_delay_seconds != assessment_delay
    ):
        raise ShadowForwardSourceVerificationIntegrityError(
            "rehash-valid W84 evidence disagrees with durable source truth"
        )
    _require_no_authority(
        paper=evidence.paper_candidate_authorized,
        external=evidence.external_execution_authorized,
        runtime=evidence.runtime_execution_authorized,
        capital=evidence.capital_authority,
        live=evidence.live_trading,
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


def _payload(
    value: PromotionShadowForwardSourceVerification, *, include_hash: bool
) -> dict[str, object]:
    names = (
        "verification_id",
        "contract_version",
        "base_resolution_id",
        "base_resolution_hash",
        "evidence_id",
        "evidence_hash",
        "policy_id",
        "policy_hash",
        "w83_resolution_hash",
        "w83_binding_hash",
        "measurement_plan_hash",
        "measurement_runtime_hash",
        "shadow_config_fingerprint",
        "shadow_control_hash",
        "shadow_sequence",
        "shadow_head_hash",
        "forward_policy_fingerprint",
        "forward_control_hash",
        "forward_sequence",
        "forward_head_hash",
        "measurement_receipts_hash",
        "measurement_head_hash",
        "measurement_receipts_count",
        "qualification_started_at",
        "qualification_ended_at",
        "qualification_duration_seconds",
        "cumulative_return",
        "peak_to_trough_drawdown",
        "capture_lag_seconds",
        "assessment_delay_seconds",
        "source_truth_verified",
        "resolved_promotion_blockers",
        "remaining_promotion_blockers",
        "strategy_version_execution_bound",
        "shadow_forward_promotion_bound",
        "paper_candidate_authorized",
        "external_execution_authorized",
        "runtime_execution_authorized",
        "capital_authority",
        "live_trading",
        "verified_at",
    )
    payload = _payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["verification_hash"] = value.verification_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    for name in (
        "qualification_started_at",
        "qualification_ended_at",
        "verified_at",
    ):
        payload[name] = _utc_iso(payload[name])
    payload["cumulative_return"] = str(payload["cumulative_return"])
    payload["peak_to_trough_drawdown"] = str(payload["peak_to_trough_drawdown"])
    payload["resolved_promotion_blockers"] = list(payload["resolved_promotion_blockers"])
    payload["remaining_promotion_blockers"] = list(payload["remaining_promotion_blockers"])
    return payload


def _require_no_authority(
    *, paper: bool, external: bool, runtime: bool, capital: str, live: str
) -> None:
    if (
        paper is not False
        or external is not False
        or runtime is not False
        or capital != "NONE"
        or live != "BLOCKED"
    ):
        raise ShadowForwardSourceVerificationIntegrityError(
            "source-verified W84 may not grant PAPER, execution, capital, or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ShadowForwardSourceVerificationIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ShadowForwardSourceVerificationIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_nonnegative_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ShadowForwardSourceVerificationIntegrityError(
            f"{label} must be integer >=0"
        )


def _require_finite_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ShadowForwardSourceVerificationIntegrityError(
            f"{label} must be finite Decimal"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ShadowForwardSourceVerificationIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _utc_iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise ShadowForwardSourceVerificationIntegrityError("datetime value required")
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
    "SOURCE_VERIFICATION_VERSION",
    "PromotionShadowForwardSourceVerification",
    "ShadowForwardSourceVerificationIntegrityError",
    "verify_promotion_shadow_forward_resolution_sources",
]
