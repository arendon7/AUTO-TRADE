from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

import autotrade.paper_candidate_admission as admission
import autotrade.promotion_shadow_forward_final_verification as w84
from autotrade.paper_candidate_admission import (
    PaperCandidateAdmissionPolicyRegistration,
    PaperCandidateAdmissionReceipt,
    PaperCandidateAdmissionStatus,
    SQLitePaperCandidateAdmissionRegistry,
)
from autotrade.promotion_cost_continuity import PromotionCostContinuityResolution
from autotrade.promotion_fee_accounting import PromotionFeeAccountingResolution
from autotrade.promotion_shadow_forward_final_verification import (
    PromotionShadowForwardFinalVerification,
)
from autotrade.promotion_strategy_version_binding import PromotionStrategyVersionResolution
from autotrade.strategy_lab_promotion import StrategyPromotionPolicy
from autotrade.strategy_promotion_assessment import StrategyPromotionAssessmentReceipt


FINAL_ADMISSION_VERIFICATION_VERSION = "W85_PAPER_CANDIDATE_ADMISSION_FINAL_VERIFICATION_V2"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class PaperCandidateAdmissionFinalVerificationIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PaperCandidateAdmissionFinalVerification:
    """Canonical W85 historical admission proof.

    A historical W84 finalization is necessary provenance but is not the W85
    freshness authority. V2 also binds the admission-time source proof already
    persisted inside the durable PASS receipt and derives freshness only from
    its durable measurement capture. Current candidate eligibility remains a
    separate lifecycle projection.
    """

    verification_id: str
    contract_version: str
    authority_key: str
    admission_id: str
    admission_hash: str
    policy_id: str
    policy_hash: str
    policy_registration_hash: str
    promotion_policy_hash: str
    threshold_policy_hash: str
    w80_assessment_hash: str
    w81_resolution_hash: str
    w82_resolution_hash: str
    w83_resolution_hash: str
    w83_binding_hash: str
    w84_finalization_hash: str
    w84_source_verification_hash: str
    w84_policy_hash: str
    w84_evidence_hash: str
    w84_measurement_plan_hash: str
    w84_measurement_runtime_hash: str
    w84_admission_source_proof_hash: str
    w84_admission_source_verification_hash: str
    w84_admission_source_capture_at: datetime
    w84_admission_source_verified_at: datetime
    selected_trial_fingerprint: str
    strategy_spec_hash: str
    loaded_runtime_code_hash: str
    fee_product_economics_hash: str
    intent_fingerprint: str
    admitted_at: datetime
    valid_until: datetime
    process_verified_at: datetime
    admission_source_truth_verified: bool
    w84_source_truth_verified: bool
    w84_admission_source_proof_bound: bool
    historical_w84_timestamp_used_for_freshness: bool
    paper_candidate_was_admitted: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    verification_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("verification_id", self.verification_id),
            ("admission_id", self.admission_id),
            ("policy_id", self.policy_id),
        ):
            _require_id(value, label)
        if self.contract_version != FINAL_ADMISSION_VERIFICATION_VERSION:
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "final admission verification version is not canonical W85"
            )
        for label, value in (
            ("authority_key", self.authority_key),
            ("admission_hash", self.admission_hash),
            ("policy_hash", self.policy_hash),
            ("policy_registration_hash", self.policy_registration_hash),
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("threshold_policy_hash", self.threshold_policy_hash),
            ("w80_assessment_hash", self.w80_assessment_hash),
            ("w81_resolution_hash", self.w81_resolution_hash),
            ("w82_resolution_hash", self.w82_resolution_hash),
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("w83_binding_hash", self.w83_binding_hash),
            ("w84_finalization_hash", self.w84_finalization_hash),
            ("w84_source_verification_hash", self.w84_source_verification_hash),
            ("w84_policy_hash", self.w84_policy_hash),
            ("w84_evidence_hash", self.w84_evidence_hash),
            ("w84_measurement_plan_hash", self.w84_measurement_plan_hash),
            ("w84_measurement_runtime_hash", self.w84_measurement_runtime_hash),
            ("w84_admission_source_proof_hash", self.w84_admission_source_proof_hash),
            (
                "w84_admission_source_verification_hash",
                self.w84_admission_source_verification_hash,
            ),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("loaded_runtime_code_hash", self.loaded_runtime_code_hash),
            ("fee_product_economics_hash", self.fee_product_economics_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("verification_hash", self.verification_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("w84_admission_source_capture_at", self.w84_admission_source_capture_at),
            ("w84_admission_source_verified_at", self.w84_admission_source_verified_at),
            ("admitted_at", self.admitted_at),
            ("valid_until", self.valid_until),
            ("process_verified_at", self.process_verified_at),
        ):
            _require_aware(value, label)
        if _utc(self.valid_until) <= _utc(self.admitted_at):
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "verified admission validity must follow admission time"
            )
        if _utc(self.process_verified_at) < _utc(self.admitted_at):
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "final verification process clock may not predate admission"
            )
        if _utc(self.w84_admission_source_capture_at) > _utc(self.admitted_at):
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "verified admission cannot predate durable W84 source capture"
            )
        if _utc(self.w84_admission_source_verified_at) != _utc(self.admitted_at):
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "final verification must bind exact admission-time W84 source proof"
            )
        if self.admission_source_truth_verified is not True:
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "canonical W85 verification requires durable admission source truth"
            )
        if self.w84_source_truth_verified is not True:
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "canonical W85 verification requires final W84 source truth"
            )
        if self.w84_admission_source_proof_bound is not True:
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "canonical W85 verification requires admission-time source proof binding"
            )
        if self.historical_w84_timestamp_used_for_freshness is not False:
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "historical W84 process timestamp may not be W85 freshness authority"
            )
        if self.paper_candidate_was_admitted is not True:
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "canonical W85 verification requires a historical PASS candidate admission"
            )
        _require_no_execution_authority(
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
        )
        if self.verification_hash != _hash(_payload(self, include_hash=False)):
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                "final admission verification hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def finalize_paper_candidate_admission(
    *,
    verification_id: str,
    admission_registry: SQLitePaperCandidateAdmissionRegistry,
    admission_id: str,
    promotion_policy: StrategyPromotionPolicy,
    w80_assessment: StrategyPromotionAssessmentReceipt,
    w81_resolution: PromotionCostContinuityResolution,
    w82_resolution: PromotionFeeAccountingResolution,
    w83_resolution: PromotionStrategyVersionResolution,
    w84_finalization: PromotionShadowForwardFinalVerification,
) -> PaperCandidateAdmissionFinalVerification:
    """Re-read durable V2 W85 admission and bind its exact W79→W84 chain."""

    _require_id(verification_id, "verification_id")
    _require_id(admission_id, "admission_id")
    if not isinstance(admission_registry, SQLitePaperCandidateAdmissionRegistry):
        raise TypeError("admission_registry must be SQLitePaperCandidateAdmissionRegistry")

    durable_receipt = admission_registry.get(admission_id)
    if durable_receipt is None:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "final W85 verification requires durable admission receipt"
        )
    durable_registration = admission_registry.get_policy(durable_receipt.policy_id)
    if durable_registration is None:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "final W85 verification requires durable policy registration"
        )

    _validate_durable_admission(durable_receipt, durable_registration)
    _validate_exact_chain(
        receipt=durable_receipt,
        registration=durable_registration,
        promotion_policy=promotion_policy,
        w80_assessment=w80_assessment,
        w81_resolution=w81_resolution,
        w82_resolution=w82_resolution,
        w83_resolution=w83_resolution,
        w84_finalization=w84_finalization,
    )

    now = _now_utc()
    _require_aware(now, "final admission verification process clock")
    if _utc(now) < _utc(durable_receipt.admitted_at):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "final W85 verification process clock predates admission"
        )

    assert durable_receipt.valid_until is not None
    assert durable_receipt.w84_admission_source_proof_hash is not None
    assert durable_receipt.w84_admission_source_verification_hash is not None
    assert durable_receipt.w84_admission_source_capture_at is not None
    assert durable_receipt.w84_admission_source_verified_at is not None
    values = {
        "verification_id": verification_id,
        "contract_version": FINAL_ADMISSION_VERIFICATION_VERSION,
        "authority_key": durable_receipt.authority_key,
        "admission_id": durable_receipt.admission_id,
        "admission_hash": durable_receipt.admission_hash,
        "policy_id": durable_receipt.policy_id,
        "policy_hash": durable_receipt.policy_hash,
        "policy_registration_hash": durable_receipt.policy_registration_hash,
        "promotion_policy_hash": promotion_policy.policy_hash,
        "threshold_policy_hash": promotion_policy.threshold_policy_hash,
        "w80_assessment_hash": w80_assessment.assessment_hash,
        "w81_resolution_hash": w81_resolution.resolution_hash,
        "w82_resolution_hash": w82_resolution.resolution_hash,
        "w83_resolution_hash": w83_resolution.resolution_hash,
        "w83_binding_hash": w83_resolution.binding_evidence_hash,
        "w84_finalization_hash": w84_finalization.finalization_hash,
        "w84_source_verification_hash": w84_finalization.source_verification_hash,
        "w84_policy_hash": w84_finalization.policy_hash,
        "w84_evidence_hash": w84_finalization.evidence_hash,
        "w84_measurement_plan_hash": w84_finalization.measurement_plan_hash,
        "w84_measurement_runtime_hash": w84_finalization.measurement_runtime_hash,
        "w84_admission_source_proof_hash": durable_receipt.w84_admission_source_proof_hash,
        "w84_admission_source_verification_hash": (
            durable_receipt.w84_admission_source_verification_hash
        ),
        "w84_admission_source_capture_at": durable_receipt.w84_admission_source_capture_at,
        "w84_admission_source_verified_at": durable_receipt.w84_admission_source_verified_at,
        "selected_trial_fingerprint": w83_resolution.selected_trial_fingerprint,
        "strategy_spec_hash": w83_resolution.strategy_spec_hash,
        "loaded_runtime_code_hash": w83_resolution.loaded_runtime_code_hash,
        "fee_product_economics_hash": w83_resolution.fee_product_economics_hash,
        "intent_fingerprint": w83_resolution.intent_fingerprint,
        "admitted_at": durable_receipt.admitted_at,
        "valid_until": durable_receipt.valid_until,
        "process_verified_at": now,
        "admission_source_truth_verified": True,
        "w84_source_truth_verified": True,
        "w84_admission_source_proof_bound": True,
        "historical_w84_timestamp_used_for_freshness": False,
        "paper_candidate_was_admitted": True,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperCandidateAdmissionFinalVerification(
        **values,
        verification_hash=_hash(_payload_from_values(values)),
    )


def _validate_durable_admission(
    receipt: PaperCandidateAdmissionReceipt,
    registration: PaperCandidateAdmissionPolicyRegistration,
) -> None:
    if receipt.status is not PaperCandidateAdmissionStatus.PASS:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "final W85 verification requires PASS admission"
        )
    if receipt.paper_candidate_authorized is not True or receipt.valid_until is None:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "PASS admission does not contain finite candidate admission"
        )
    if (
        receipt.w84_admission_source_proof_hash is None
        or receipt.w84_admission_source_verification_hash is None
        or receipt.w84_admission_source_capture_at is None
        or receipt.w84_admission_source_verified_at is None
    ):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "PASS admission is missing durable V2 W84 admission-source provenance"
        )
    if (
        receipt.policy_id != registration.policy.policy_id
        or receipt.policy_hash != registration.policy.policy_hash
        or receipt.policy_registration_hash != registration.registration_hash
        or receipt.authority_key != registration.policy.authority_key
    ):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "durable admission and policy registration identity mismatch"
        )
    if _utc(receipt.admitted_at) <= _utc(registration.registered_at):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "durable admission must follow policy registration"
        )
    if _utc(receipt.w84_admission_source_verified_at) != _utc(receipt.admitted_at):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "durable source proof must be bound to exact W85 admission clock"
        )
    if _utc(receipt.w84_admission_source_capture_at) > _utc(receipt.admitted_at):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "durable W84 source capture cannot follow W85 admission"
        )
    expected_valid_until = receipt.admitted_at + admission.timedelta(
        seconds=registration.policy.candidate_validity_seconds
    )
    if receipt.valid_until != expected_valid_until:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "candidate validity does not match frozen admission policy"
        )
    if (
        receipt.paper_execution_authorized is not False
        or receipt.external_execution_authorized is not False
        or receipt.runtime_execution_authorized is not False
        or receipt.capital_authority != "NONE"
        or receipt.live_trading != "BLOCKED"
    ):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "durable admission illegally carries execution/capital/LIVE authority"
        )


def _validate_exact_chain(
    *,
    receipt: PaperCandidateAdmissionReceipt,
    registration: PaperCandidateAdmissionPolicyRegistration,
    promotion_policy: StrategyPromotionPolicy,
    w80_assessment: StrategyPromotionAssessmentReceipt,
    w81_resolution: PromotionCostContinuityResolution,
    w82_resolution: PromotionFeeAccountingResolution,
    w83_resolution: PromotionStrategyVersionResolution,
    w84_finalization: PromotionShadowForwardFinalVerification,
) -> None:
    try:
        admission._validate_chain(
            policy=registration.policy,
            promotion_policy=promotion_policy,
            w80_assessment=w80_assessment,
            w81_resolution=w81_resolution,
            w82_resolution=w82_resolution,
            w83_resolution=w83_resolution,
            w84_finalization=w84_finalization,
        )
    except (TypeError, admission.PaperCandidateAdmissionError) as exc:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "intermediate W85 chain validation failed"
        ) from exc

    expected_w84_hash = w84._hash(w84._payload(w84_finalization, include_hash=False))
    if w84_finalization.finalization_hash != expected_w84_hash:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "W84 finalization hash mismatch"
        )
    if (
        w84_finalization.source_truth_verified is not True
        or w84_finalization.process_clock_freshness_verified is not True
        or w84_finalization.strategy_version_execution_bound is not True
        or w84_finalization.shadow_forward_promotion_bound is not True
        or w84_finalization.remaining_promotion_blockers
    ):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "W84 final source/binding state is not fully qualified"
        )
    if w84_finalization.w83_resolution_hash != w83_resolution.resolution_hash:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "W84 finalization does not bind exact W83 resolution"
        )
    if w84_finalization.w83_binding_hash != w83_resolution.binding_evidence_hash:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "W84 finalization does not bind exact W83 execution binding"
        )
    if receipt.w84_finalization_id != w84_finalization.finalization_id:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "admission receipt W84 finalization id mismatch"
        )
    if receipt.w84_finalization_hash != w84_finalization.finalization_hash:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "admission receipt W84 finalization hash mismatch"
        )
    if receipt.w84_source_verification_hash != w84_finalization.source_verification_hash:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "admission receipt W84 source verification mismatch"
        )
    if receipt.w84_measurement_plan_hash != w84_finalization.measurement_plan_hash:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "admission receipt W84 measurement plan mismatch"
        )
    if (
        receipt.promotion_policy_hash != promotion_policy.policy_hash
        or receipt.threshold_policy_hash != promotion_policy.threshold_policy_hash
        or receipt.w80_assessment_hash != w80_assessment.assessment_hash
        or receipt.w81_resolution_hash != w81_resolution.resolution_hash
        or receipt.w82_resolution_hash != w82_resolution.resolution_hash
        or receipt.w83_resolution_hash != w83_resolution.resolution_hash
        or receipt.selected_trial_fingerprint != w83_resolution.selected_trial_fingerprint
        or receipt.strategy_spec_hash != w83_resolution.strategy_spec_hash
        or receipt.loaded_runtime_code_hash != w83_resolution.loaded_runtime_code_hash
        or receipt.fee_product_economics_hash != w83_resolution.fee_product_economics_hash
        or receipt.intent_fingerprint != w83_resolution.intent_fingerprint
    ):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "admission receipt does not bind exact W79→W84 source chain"
        )

    assert receipt.w84_admission_source_capture_at is not None
    assert receipt.w84_admission_source_verified_at is not None
    age_at_admission = int(
        (
            _utc(receipt.admitted_at)
            - _utc(receipt.w84_admission_source_capture_at)
        ).total_seconds()
    )
    if age_at_admission < 0:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "admission predates durable W84 source capture"
        )
    if age_at_admission > registration.policy.max_w84_finalization_age_seconds:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "admission exceeded frozen durable-source freshness budget"
        )
    if _utc(receipt.w84_admission_source_verified_at) != _utc(receipt.admitted_at):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "admission source proof verification clock is not exact admission clock"
        )


def _payload(
    value: PaperCandidateAdmissionFinalVerification, *, include_hash: bool
) -> dict[str, object]:
    names = tuple(
        field
        for field in PaperCandidateAdmissionFinalVerification.__dataclass_fields__
        if field != "verification_hash"
    )
    payload = _payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["verification_hash"] = value.verification_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    for key in (
        "w84_admission_source_capture_at",
        "w84_admission_source_verified_at",
        "admitted_at",
        "valid_until",
        "process_verified_at",
    ):
        raw = payload[key]
        if not isinstance(raw, datetime):
            raise PaperCandidateAdmissionFinalVerificationIntegrityError(
                f"{key} type invalid"
            )
        payload[key] = _utc(raw).isoformat()
    return payload


def _require_no_execution_authority(
    *, paper_execution: bool, external: bool, runtime: bool, capital: str, live: str
) -> None:
    if (
        paper_execution is not False
        or external is not False
        or runtime is not False
        or capital != "NONE"
        or live != "BLOCKED"
    ):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            "final W85 admission verification may not grant execution, capital, or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperCandidateAdmissionFinalVerificationIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "FINAL_ADMISSION_VERIFICATION_VERSION",
    "PaperCandidateAdmissionFinalVerification",
    "PaperCandidateAdmissionFinalVerificationIntegrityError",
    "finalize_paper_candidate_admission",
]
