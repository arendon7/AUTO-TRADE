from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

import autotrade.promotion_shadow_forward_final_verification as final_module
from autotrade.forward_shadow_measurement import (
    ForwardMeasurementPlan,
    ForwardShadowMeasurementReceipt,
)
from autotrade.promotion_shadow_forward_binding import (
    PromotionShadowForwardResolution,
    ShadowForwardPromotionEvidence,
    ShadowForwardPromotionPolicy,
)
from autotrade.promotion_shadow_forward_final_verification import (
    PromotionShadowForwardFinalVerification,
    ShadowForwardFinalVerificationIntegrityError,
)
from autotrade.promotion_strategy_version_binding import PromotionStrategyVersionResolution
from autotrade.research.forward import SQLiteForwardEvidenceRegistry
from autotrade.research.shadow import SQLitePortfolioShadowRegistry
from autotrade.strategy_execution_binding import ExecutionStrategyBindingEvidence


ADMISSION_SOURCE_PROOF_VERSION = "W85_W84_ADMISSION_SOURCE_PROOF_V2"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class PaperCandidateAdmissionSourceIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class W84AdmissionSourcePackage:
    """Transient inputs needed to rerun canonical W84 final verification.

    The durable authorities remain the existing R5 Shadow/Forward registries.
    This package grants no database mutation, broker, OMS, Safety, order or
    capital authority.
    """

    base_resolution: PromotionShadowForwardResolution
    evidence: ShadowForwardPromotionEvidence
    policy: ShadowForwardPromotionPolicy
    measurement_plan: ForwardMeasurementPlan
    binding_evidence: ExecutionStrategyBindingEvidence
    shadow_registry: SQLitePortfolioShadowRegistry
    forward_registry: SQLiteForwardEvidenceRegistry
    measurement_receipts: tuple[ForwardShadowMeasurementReceipt, ...]

    def __post_init__(self) -> None:
        expected = (
            (self.base_resolution, PromotionShadowForwardResolution, "base_resolution"),
            (self.evidence, ShadowForwardPromotionEvidence, "evidence"),
            (self.policy, ShadowForwardPromotionPolicy, "policy"),
            (self.measurement_plan, ForwardMeasurementPlan, "measurement_plan"),
            (self.binding_evidence, ExecutionStrategyBindingEvidence, "binding_evidence"),
            (self.shadow_registry, SQLitePortfolioShadowRegistry, "shadow_registry"),
            (self.forward_registry, SQLiteForwardEvidenceRegistry, "forward_registry"),
        )
        for value, kind, label in expected:
            if not isinstance(value, kind):
                raise PaperCandidateAdmissionSourceIntegrityError(
                    f"{label} must be {kind.__name__}"
                )
        if not isinstance(self.measurement_receipts, tuple) or any(
            not isinstance(item, ForwardShadowMeasurementReceipt)
            for item in self.measurement_receipts
        ):
            raise PaperCandidateAdmissionSourceIntegrityError(
                "measurement_receipts must be tuple[ForwardShadowMeasurementReceipt, ...]"
            )


@dataclass(frozen=True, slots=True)
class W84AdmissionSourceProof:
    """Hash-bound proof that W85 reran the canonical W84 finalizer.

    `verified_at` is the W85 admission decision clock supplied internally by the
    admission registry. `canonical_finalization_verified_at` is the independent
    process clock read inside W84's canonical finalizer. Both are hash-bound;
    historical W84 `process_verified_at` is never used as W85 freshness truth.
    """

    proof_id: str
    contract_version: str
    finalization_id: str
    finalization_hash: str
    original_source_verification_hash: str
    admission_finalization_id: str
    admission_finalization_hash: str
    admission_source_verification_hash: str
    base_resolution_hash: str
    evidence_hash: str
    policy_hash: str
    w83_resolution_hash: str
    w83_binding_hash: str
    measurement_plan_hash: str
    measurement_runtime_hash: str
    source_capture_at: datetime
    verified_at: datetime
    canonical_finalization_verified_at: datetime
    source_age_seconds: int
    source_truth_verified: bool
    canonical_w84_finalization_reproved: bool
    historical_finalization_timestamp_trusted_for_freshness: bool
    paper_candidate_authorized: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    proof_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("proof_id", self.proof_id),
            ("finalization_id", self.finalization_id),
            ("admission_finalization_id", self.admission_finalization_id),
        ):
            _require_id(value, label)
        if self.contract_version != ADMISSION_SOURCE_PROOF_VERSION:
            raise PaperCandidateAdmissionSourceIntegrityError(
                "admission source proof version is not canonical W85"
            )
        for label, value in (
            ("finalization_hash", self.finalization_hash),
            ("original_source_verification_hash", self.original_source_verification_hash),
            ("admission_finalization_hash", self.admission_finalization_hash),
            ("admission_source_verification_hash", self.admission_source_verification_hash),
            ("base_resolution_hash", self.base_resolution_hash),
            ("evidence_hash", self.evidence_hash),
            ("policy_hash", self.policy_hash),
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("w83_binding_hash", self.w83_binding_hash),
            ("measurement_plan_hash", self.measurement_plan_hash),
            ("measurement_runtime_hash", self.measurement_runtime_hash),
            ("proof_hash", self.proof_hash),
        ):
            _require_hash(value, label)
        _require_aware(self.source_capture_at, "source_capture_at")
        _require_aware(self.verified_at, "verified_at")
        _require_aware(
            self.canonical_finalization_verified_at,
            "canonical_finalization_verified_at",
        )
        if isinstance(self.source_age_seconds, bool) or not isinstance(
            self.source_age_seconds, int
        ) or self.source_age_seconds < 0:
            raise PaperCandidateAdmissionSourceIntegrityError(
                "source_age_seconds must be integer >=0"
            )
        expected_age = int(
            (_utc(self.verified_at) - _utc(self.source_capture_at)).total_seconds()
        )
        if self.source_age_seconds != expected_age:
            raise PaperCandidateAdmissionSourceIntegrityError(
                "source age does not match W85 admission clock and durable capture"
            )
        if self.source_truth_verified is not True:
            raise PaperCandidateAdmissionSourceIntegrityError(
                "W85 source proof requires source_truth_verified=true"
            )
        if self.canonical_w84_finalization_reproved is not True:
            raise PaperCandidateAdmissionSourceIntegrityError(
                "W85 source proof requires canonical W84 finalization rerun"
            )
        if self.historical_finalization_timestamp_trusted_for_freshness is not False:
            raise PaperCandidateAdmissionSourceIntegrityError(
                "W85 must not trust historical finalization timestamp for admission freshness"
            )
        if (
            self.paper_candidate_authorized is not False
            or self.paper_execution_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
        ):
            raise PaperCandidateAdmissionSourceIntegrityError(
                "W85 source proof may not grant candidate, execution, capital, or LIVE authority"
            )
        if self.proof_hash != _hash(_proof_payload(self, include_hash=False)):
            raise PaperCandidateAdmissionSourceIntegrityError(
                "W85 admission source proof hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _proof_payload(self, include_hash=True)


def verify_w84_sources_for_candidate_admission(
    *,
    proof_id: str,
    finalization: PromotionShadowForwardFinalVerification,
    w83_resolution: PromotionStrategyVersionResolution,
    source_package: W84AdmissionSourcePackage,
    verified_at: datetime,
) -> W84AdmissionSourceProof:
    """Re-prove W84 via its canonical internal-process-clock finalizer.

    W85 deliberately does not call W84's intermediate source verifier. The
    canonical W84 finalizer re-reads durable R5/measurement truth, obtains its
    own process clock, and re-enforces W84 freshness before W85 can consider
    candidate admission. W85 separately binds its own internal decision clock.
    """

    _require_id(proof_id, "proof_id")
    if not isinstance(finalization, PromotionShadowForwardFinalVerification):
        raise TypeError("finalization must be PromotionShadowForwardFinalVerification")
    if not isinstance(w83_resolution, PromotionStrategyVersionResolution):
        raise TypeError("w83_resolution must be PromotionStrategyVersionResolution")
    if not isinstance(source_package, W84AdmissionSourcePackage):
        raise TypeError("source_package must be W84AdmissionSourcePackage")
    _require_aware(verified_at, "verified_at")

    _validate_historical_finalization(finalization)

    try:
        admission_finalization = final_module.finalize_promotion_shadow_forward_resolution(
            finalization_id=f"{proof_id}:final",
            source_verification_id=f"{proof_id}:source",
            base_resolution=source_package.base_resolution,
            evidence=source_package.evidence,
            policy=source_package.policy,
            measurement_plan=source_package.measurement_plan,
            w83_resolution=w83_resolution,
            binding_evidence=source_package.binding_evidence,
            shadow_registry=source_package.shadow_registry,
            forward_registry=source_package.forward_registry,
            measurement_receipts=source_package.measurement_receipts,
        )
    except (TypeError, ShadowForwardFinalVerificationIntegrityError) as exc:
        raise PaperCandidateAdmissionSourceIntegrityError(
            "W85 admission could not rerun canonical W84 final verification"
        ) from exc

    _require_finalizations_match(
        historical=finalization,
        admission=admission_finalization,
        source_package=source_package,
        w83_resolution=w83_resolution,
    )

    source_age_seconds = int(
        (_utc(verified_at) - _utc(admission_finalization.measurement_capture_at)).total_seconds()
    )
    if source_age_seconds < 0:
        raise PaperCandidateAdmissionSourceIntegrityError(
            "W85 admission clock predates durable W84 measurement capture"
        )

    values = {
        "proof_id": proof_id,
        "contract_version": ADMISSION_SOURCE_PROOF_VERSION,
        "finalization_id": finalization.finalization_id,
        "finalization_hash": finalization.finalization_hash,
        "original_source_verification_hash": finalization.source_verification_hash,
        "admission_finalization_id": admission_finalization.finalization_id,
        "admission_finalization_hash": admission_finalization.finalization_hash,
        "admission_source_verification_hash": admission_finalization.source_verification_hash,
        "base_resolution_hash": admission_finalization.base_resolution_hash,
        "evidence_hash": admission_finalization.evidence_hash,
        "policy_hash": admission_finalization.policy_hash,
        "w83_resolution_hash": admission_finalization.w83_resolution_hash,
        "w83_binding_hash": admission_finalization.w83_binding_hash,
        "measurement_plan_hash": admission_finalization.measurement_plan_hash,
        "measurement_runtime_hash": admission_finalization.measurement_runtime_hash,
        "source_capture_at": admission_finalization.measurement_capture_at,
        "verified_at": verified_at,
        "canonical_finalization_verified_at": admission_finalization.process_verified_at,
        "source_age_seconds": source_age_seconds,
        "source_truth_verified": True,
        "canonical_w84_finalization_reproved": True,
        "historical_finalization_timestamp_trusted_for_freshness": False,
        "paper_candidate_authorized": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return W84AdmissionSourceProof(
        **values,
        proof_hash=_hash(_proof_payload_from_values(values)),
    )


def _validate_historical_finalization(
    finalization: PromotionShadowForwardFinalVerification,
) -> None:
    expected_hash = final_module._hash(
        final_module._payload(finalization, include_hash=False)
    )
    if finalization.finalization_hash != expected_hash:
        raise PaperCandidateAdmissionSourceIntegrityError(
            "historical W84 finalization self-hash mismatch"
        )
    if (
        finalization.source_truth_verified is not True
        or finalization.process_clock_freshness_verified is not True
        or finalization.strategy_version_execution_bound is not True
        or finalization.shadow_forward_promotion_bound is not True
        or finalization.paper_candidate_authorized is not False
        or finalization.external_execution_authorized is not False
        or finalization.runtime_execution_authorized is not False
        or finalization.capital_authority != "NONE"
        or finalization.live_trading != "BLOCKED"
    ):
        raise PaperCandidateAdmissionSourceIntegrityError(
            "historical W84 finalization semantic/authority boundary is not intact"
        )


def _require_finalizations_match(
    *,
    historical: PromotionShadowForwardFinalVerification,
    admission: PromotionShadowForwardFinalVerification,
    source_package: W84AdmissionSourcePackage,
    w83_resolution: PromotionStrategyVersionResolution,
) -> None:
    checks = (
        (historical.base_resolution_hash, admission.base_resolution_hash, "base resolution hash"),
        (historical.evidence_hash, admission.evidence_hash, "evidence hash"),
        (historical.policy_hash, admission.policy_hash, "W84 policy hash"),
        (historical.w83_resolution_hash, admission.w83_resolution_hash, "W83 resolution hash"),
        (historical.w83_binding_hash, admission.w83_binding_hash, "W83 binding hash"),
        (historical.measurement_plan_hash, admission.measurement_plan_hash, "measurement plan hash"),
        (
            historical.measurement_runtime_hash,
            admission.measurement_runtime_hash,
            "measurement runtime hash",
        ),
    )
    for expected, actual, label in checks:
        if actual != expected:
            raise PaperCandidateAdmissionSourceIntegrityError(
                f"W84 admission rerun changed {label}"
            )
    if historical.w83_resolution_hash != w83_resolution.resolution_hash:
        raise PaperCandidateAdmissionSourceIntegrityError(
            "historical W84 finalization does not bind exact W83 resolution"
        )
    if historical.w83_binding_hash != source_package.binding_evidence.evidence_hash:
        raise PaperCandidateAdmissionSourceIntegrityError(
            "historical W84 finalization does not bind exact W83 execution evidence"
        )
    if historical.measurement_plan_hash != source_package.measurement_plan.plan_hash:
        raise PaperCandidateAdmissionSourceIntegrityError(
            "historical W84 finalization does not bind supplied measurement plan"
        )
    if _utc(historical.measurement_capture_at) != _utc(admission.measurement_capture_at):
        raise PaperCandidateAdmissionSourceIntegrityError(
            "historical W84 measurement capture disagrees with canonical admission rerun"
        )
    if historical.resolved_promotion_blockers != admission.resolved_promotion_blockers:
        raise PaperCandidateAdmissionSourceIntegrityError(
            "historical/admission W84 resolved blocker set mismatch"
        )
    if historical.remaining_promotion_blockers != admission.remaining_promotion_blockers:
        raise PaperCandidateAdmissionSourceIntegrityError(
            "historical/admission W84 remaining blocker set mismatch"
        )


def _proof_payload(
    value: W84AdmissionSourceProof, *, include_hash: bool
) -> dict[str, object]:
    names = tuple(
        name for name in W84AdmissionSourceProof.__dataclass_fields__ if name != "proof_hash"
    )
    payload = _proof_payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["proof_hash"] = value.proof_hash
    return payload


def _proof_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    for key in (
        "source_capture_at",
        "verified_at",
        "canonical_finalization_verified_at",
    ):
        value = payload[key]
        if not isinstance(value, datetime):
            raise PaperCandidateAdmissionSourceIntegrityError(f"{key} must be datetime")
        payload[key] = _utc(value).isoformat()
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperCandidateAdmissionSourceIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperCandidateAdmissionSourceIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperCandidateAdmissionSourceIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "ADMISSION_SOURCE_PROOF_VERSION",
    "PaperCandidateAdmissionSourceIntegrityError",
    "W84AdmissionSourcePackage",
    "W84AdmissionSourceProof",
    "verify_w84_sources_for_candidate_admission",
]
