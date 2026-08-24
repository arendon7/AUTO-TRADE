from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re

from autotrade.forward_shadow_measurement import (
    ForwardMeasurementPlan,
    ForwardShadowMeasurementReceipt,
)
from autotrade.promotion_fee_accounting import SHADOW_FORWARD_BLOCKER
from autotrade.promotion_shadow_forward_binding import (
    PromotionShadowForwardResolution,
    ShadowForwardPromotionEvidence,
    ShadowForwardPromotionPolicy,
)
from autotrade.promotion_shadow_forward_source_verification import (
    PromotionShadowForwardSourceVerification,
    ShadowForwardSourceVerificationIntegrityError,
    verify_promotion_shadow_forward_resolution_sources,
)
from autotrade.promotion_strategy_version_binding import PromotionStrategyVersionResolution
from autotrade.research.forward import SQLiteForwardEvidenceRegistry
from autotrade.research.shadow import SQLitePortfolioShadowRegistry
from autotrade.strategy_execution_binding import ExecutionStrategyBindingEvidence


FINAL_VERIFICATION_VERSION = "W84_SHADOW_FORWARD_FINAL_VERIFICATION_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class ShadowForwardFinalVerificationIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PromotionShadowForwardFinalVerification:
    """Canonical W84 receipt: durable source truth + process-clock freshness."""

    finalization_id: str
    contract_version: str
    source_verification_id: str
    source_verification_hash: str
    base_resolution_hash: str
    evidence_hash: str
    policy_hash: str
    w83_resolution_hash: str
    w83_binding_hash: str
    measurement_plan_hash: str
    measurement_runtime_hash: str
    measurement_capture_at: datetime
    process_verified_at: datetime
    decision_delay_seconds: int
    source_truth_verified: bool
    process_clock_freshness_verified: bool
    resolved_promotion_blockers: tuple[str, ...]
    remaining_promotion_blockers: tuple[str, ...]
    strategy_version_execution_bound: bool
    shadow_forward_promotion_bound: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    finalization_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("finalization_id", self.finalization_id),
            ("source_verification_id", self.source_verification_id),
        ):
            _require_id(value, label)
        if self.contract_version != FINAL_VERIFICATION_VERSION:
            raise ShadowForwardFinalVerificationIntegrityError(
                "final verification version is not canonical W84"
            )
        for label, value in (
            ("source_verification_hash", self.source_verification_hash),
            ("base_resolution_hash", self.base_resolution_hash),
            ("evidence_hash", self.evidence_hash),
            ("policy_hash", self.policy_hash),
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("w83_binding_hash", self.w83_binding_hash),
            ("measurement_plan_hash", self.measurement_plan_hash),
            ("measurement_runtime_hash", self.measurement_runtime_hash),
            ("finalization_hash", self.finalization_hash),
        ):
            _require_hash(value, label)
        _require_aware(self.measurement_capture_at, "measurement_capture_at")
        _require_aware(self.process_verified_at, "process_verified_at")
        if isinstance(self.decision_delay_seconds, bool) or not isinstance(
            self.decision_delay_seconds, int
        ) or self.decision_delay_seconds < 0:
            raise ShadowForwardFinalVerificationIntegrityError(
                "decision_delay_seconds must be integer >=0"
            )
        if self.source_truth_verified is not True:
            raise ShadowForwardFinalVerificationIntegrityError(
                "canonical W84 finalization requires source_truth_verified=true"
            )
        if self.process_clock_freshness_verified is not True:
            raise ShadowForwardFinalVerificationIntegrityError(
                "canonical W84 finalization requires process_clock_freshness_verified=true"
            )
        if self.resolved_promotion_blockers != (SHADOW_FORWARD_BLOCKER,):
            raise ShadowForwardFinalVerificationIntegrityError(
                "W84 finalization may certify only the Shadow/Forward blocker"
            )
        if self.remaining_promotion_blockers != tuple(
            sorted(set(self.remaining_promotion_blockers))
        ):
            raise ShadowForwardFinalVerificationIntegrityError(
                "remaining blockers must be unique sorted"
            )
        if SHADOW_FORWARD_BLOCKER in self.remaining_promotion_blockers:
            raise ShadowForwardFinalVerificationIntegrityError(
                "finalized Shadow/Forward blocker may not remain present"
            )
        if (
            self.strategy_version_execution_bound is not True
            or self.shadow_forward_promotion_bound is not True
        ):
            raise ShadowForwardFinalVerificationIntegrityError(
                "W84 final binding flags are inconsistent"
            )
        _require_no_authority(
            paper=self.paper_candidate_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
        )
        expected_delay = int(
            (_utc(self.process_verified_at) - _utc(self.measurement_capture_at)).total_seconds()
        )
        if expected_delay != self.decision_delay_seconds:
            raise ShadowForwardFinalVerificationIntegrityError(
                "decision delay does not match process clock and capture time"
            )
        if self.finalization_hash != _hash(_payload(self, include_hash=False)):
            raise ShadowForwardFinalVerificationIntegrityError(
                "W84 final verification hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def finalize_promotion_shadow_forward_resolution(
    *,
    finalization_id: str,
    source_verification_id: str,
    base_resolution: PromotionShadowForwardResolution,
    evidence: ShadowForwardPromotionEvidence,
    policy: ShadowForwardPromotionPolicy,
    measurement_plan: ForwardMeasurementPlan,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
    shadow_registry: SQLitePortfolioShadowRegistry,
    forward_registry: SQLiteForwardEvidenceRegistry,
    measurement_receipts: tuple[ForwardShadowMeasurementReceipt, ...],
) -> PromotionShadowForwardFinalVerification:
    """Finalize W84 from durable sources using an internal process clock.

    There is deliberately no caller-supplied `verified_at`. The finalizer reads
    the process clock itself, performs the source-authoritative verification at
    that instant, derives the measurement capture time from immutable receipt
    period-end + source-verified capture lag, and fails closed if the frozen
    assessment-delay budget has already expired.
    """

    _require_id(finalization_id, "finalization_id")
    _require_id(source_verification_id, "source_verification_id")
    observed_now = _now_utc()
    _require_aware(observed_now, "process clock")

    try:
        source = verify_promotion_shadow_forward_resolution_sources(
            verification_id=source_verification_id,
            base_resolution=base_resolution,
            evidence=evidence,
            policy=policy,
            measurement_plan=measurement_plan,
            w83_resolution=w83_resolution,
            binding_evidence=binding_evidence,
            shadow_registry=shadow_registry,
            forward_registry=forward_registry,
            measurement_receipts=measurement_receipts,
            verified_at=observed_now,
        )
    except (TypeError, ShadowForwardSourceVerificationIntegrityError) as exc:
        raise ShadowForwardFinalVerificationIntegrityError(
            "W84 finalization could not re-prove durable source truth"
        ) from exc

    measurement_capture_at = source.qualification_ended_at + timedelta(
        seconds=source.capture_lag_seconds
    )
    decision_delay = int(
        (_utc(observed_now) - _utc(measurement_capture_at)).total_seconds()
    )
    if decision_delay < 0:
        raise ShadowForwardFinalVerificationIntegrityError(
            "process clock predates source-verified measurement capture"
        )
    if decision_delay > policy.max_assessment_delay_seconds:
        raise ShadowForwardFinalVerificationIntegrityError(
            "source-verified W84 decision exceeded frozen process-clock freshness budget"
        )

    if (
        source.base_resolution_hash != base_resolution.resolution_hash
        or source.evidence_hash != evidence.evidence_hash
        or source.policy_hash != policy.policy_hash
        or source.w83_resolution_hash != w83_resolution.resolution_hash
        or source.w83_binding_hash != binding_evidence.evidence_hash
        or source.measurement_plan_hash != measurement_plan.plan_hash
        or source.measurement_runtime_hash != measurement_plan.measurement_runtime_hash
        or source.source_truth_verified is not True
    ):
        raise ShadowForwardFinalVerificationIntegrityError(
            "source verification identity drifted before final W84 certification"
        )

    values = {
        "finalization_id": finalization_id,
        "contract_version": FINAL_VERIFICATION_VERSION,
        "source_verification_id": source.verification_id,
        "source_verification_hash": source.verification_hash,
        "base_resolution_hash": base_resolution.resolution_hash,
        "evidence_hash": evidence.evidence_hash,
        "policy_hash": policy.policy_hash,
        "w83_resolution_hash": w83_resolution.resolution_hash,
        "w83_binding_hash": binding_evidence.evidence_hash,
        "measurement_plan_hash": measurement_plan.plan_hash,
        "measurement_runtime_hash": measurement_plan.measurement_runtime_hash,
        "measurement_capture_at": measurement_capture_at,
        "process_verified_at": observed_now,
        "decision_delay_seconds": decision_delay,
        "source_truth_verified": True,
        "process_clock_freshness_verified": True,
        "resolved_promotion_blockers": (SHADOW_FORWARD_BLOCKER,),
        "remaining_promotion_blockers": source.remaining_promotion_blockers,
        "strategy_version_execution_bound": True,
        "shadow_forward_promotion_bound": True,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PromotionShadowForwardFinalVerification(
        **values,
        finalization_hash=_hash(_payload_from_values(values)),
    )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _payload(
    value: PromotionShadowForwardFinalVerification, *, include_hash: bool
) -> dict[str, object]:
    names = (
        "finalization_id",
        "contract_version",
        "source_verification_id",
        "source_verification_hash",
        "base_resolution_hash",
        "evidence_hash",
        "policy_hash",
        "w83_resolution_hash",
        "w83_binding_hash",
        "measurement_plan_hash",
        "measurement_runtime_hash",
        "measurement_capture_at",
        "process_verified_at",
        "decision_delay_seconds",
        "source_truth_verified",
        "process_clock_freshness_verified",
        "resolved_promotion_blockers",
        "remaining_promotion_blockers",
        "strategy_version_execution_bound",
        "shadow_forward_promotion_bound",
        "paper_candidate_authorized",
        "external_execution_authorized",
        "runtime_execution_authorized",
        "capital_authority",
        "live_trading",
    )
    payload = _payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["finalization_hash"] = value.finalization_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["measurement_capture_at"] = _utc_iso(payload["measurement_capture_at"])
    payload["process_verified_at"] = _utc_iso(payload["process_verified_at"])
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
        raise ShadowForwardFinalVerificationIntegrityError(
            "final W84 verification may not grant PAPER, execution, capital, or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ShadowForwardFinalVerificationIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ShadowForwardFinalVerificationIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ShadowForwardFinalVerificationIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _utc_iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise ShadowForwardFinalVerificationIntegrityError("datetime value required")
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
    "FINAL_VERIFICATION_VERSION",
    "PromotionShadowForwardFinalVerification",
    "ShadowForwardFinalVerificationIntegrityError",
    "finalize_promotion_shadow_forward_resolution",
]
