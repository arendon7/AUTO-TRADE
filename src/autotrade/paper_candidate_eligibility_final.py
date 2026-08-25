from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

import autotrade.paper_candidate_admission_final_verification as final_admission
import autotrade.paper_candidate_admission_lifecycle as lifecycle
from autotrade.paper_candidate_admission import PaperCandidateAdmissionReceipt
from autotrade.paper_candidate_admission_final_verification import (
    PaperCandidateAdmissionFinalVerification,
)
from autotrade.paper_candidate_admission_lifecycle import (
    PaperCandidateEligibilityState,
    PaperCandidateLifecycleAction,
    SQLitePaperCandidateLifecycleRegistry,
)


FINAL_ELIGIBILITY_VERSION = "W85_PAPER_CANDIDATE_FINAL_ELIGIBILITY_V2"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class PaperCandidateFinalEligibilityIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PaperCandidateFinalEligibility:
    """Canonical W85 current eligibility snapshot for downstream work.

    Historical admission remains immutable. Current eligibility is recomputed
    from durable lifecycle history and an internal process clock. Expiry always
    wins over earlier suspension/revocation state. This snapshot carries no
    PAPER execution, external/runtime execution, capital or LIVE authority.
    """

    projection_id: str
    contract_version: str
    authority_key: str
    admission_id: str
    admission_hash: str
    final_admission_verification_hash: str
    w84_admission_source_proof_hash: str
    admission_valid_until: datetime
    lifecycle_head_hash: str
    lifecycle_events_count: int
    state: PaperCandidateEligibilityState
    observed_at: datetime
    paper_candidate_currently_eligible: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    projection_hash: str

    def __post_init__(self) -> None:
        _require_id(self.projection_id, "projection_id")
        if self.contract_version != FINAL_ELIGIBILITY_VERSION:
            raise PaperCandidateFinalEligibilityIntegrityError(
                "final eligibility version is not canonical W85"
            )
        for label, value in (
            ("authority_key", self.authority_key),
            ("admission_hash", self.admission_hash),
            ("final_admission_verification_hash", self.final_admission_verification_hash),
            ("w84_admission_source_proof_hash", self.w84_admission_source_proof_hash),
            ("lifecycle_head_hash", self.lifecycle_head_hash),
            ("projection_hash", self.projection_hash),
        ):
            _require_hash(value, label)
        _require_id(self.admission_id, "admission_id")
        _require_aware(self.admission_valid_until, "admission_valid_until")
        _require_aware(self.observed_at, "observed_at")
        if (
            isinstance(self.lifecycle_events_count, bool)
            or not isinstance(self.lifecycle_events_count, int)
            or self.lifecycle_events_count < 0
        ):
            raise PaperCandidateFinalEligibilityIntegrityError(
                "lifecycle_events_count must be integer >=0"
            )
        if not isinstance(self.state, PaperCandidateEligibilityState):
            raise PaperCandidateFinalEligibilityIntegrityError(
                "invalid final candidate state"
            )
        expected_eligible = self.state is PaperCandidateEligibilityState.ACTIVE
        if self.paper_candidate_currently_eligible is not expected_eligible:
            raise PaperCandidateFinalEligibilityIntegrityError(
                "current eligibility flag does not match final state"
            )
        expired_by_clock = _utc(self.observed_at) > _utc(self.admission_valid_until)
        if expired_by_clock != (self.state is PaperCandidateEligibilityState.EXPIRED):
            raise PaperCandidateFinalEligibilityIntegrityError(
                "final eligibility must give expiry precedence to process-clock validity"
            )
        _require_no_execution_authority(
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
        )
        if self.projection_hash != _hash(_payload(self, include_hash=False)):
            raise PaperCandidateFinalEligibilityIntegrityError(
                "final eligibility projection hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def project_final_paper_candidate_eligibility(
    *,
    projection_id: str,
    final_verification: PaperCandidateAdmissionFinalVerification,
    admission_receipt: PaperCandidateAdmissionReceipt,
    lifecycle_registry: SQLitePaperCandidateLifecycleRegistry,
) -> PaperCandidateFinalEligibility:
    _require_id(projection_id, "projection_id")
    _validate_final_verification(final_verification)
    if not isinstance(lifecycle_registry, SQLitePaperCandidateLifecycleRegistry):
        raise TypeError("lifecycle_registry must be SQLitePaperCandidateLifecycleRegistry")
    if admission_receipt.w84_admission_source_proof_hash is None:
        raise PaperCandidateFinalEligibilityIntegrityError(
            "final eligibility requires durable V2 admission source proof"
        )
    if (
        admission_receipt.admission_id != final_verification.admission_id
        or admission_receipt.admission_hash != final_verification.admission_hash
        or admission_receipt.authority_key != final_verification.authority_key
        or admission_receipt.valid_until != final_verification.valid_until
        or admission_receipt.w84_admission_source_proof_hash
        != final_verification.w84_admission_source_proof_hash
        or admission_receipt.w84_admission_source_verification_hash
        != final_verification.w84_admission_source_verification_hash
        or admission_receipt.w84_admission_source_capture_at
        != final_verification.w84_admission_source_capture_at
        or admission_receipt.w84_admission_source_verified_at
        != final_verification.w84_admission_source_verified_at
    ):
        raise PaperCandidateFinalEligibilityIntegrityError(
            "final eligibility source admission does not match canonical W85 V2 verification"
        )

    events = lifecycle_registry.list_for_admission(admission_receipt)
    now = _now_utc()
    _require_aware(now, "final eligibility process clock")
    if _utc(now) < _utc(final_verification.process_verified_at):
        raise PaperCandidateFinalEligibilityIntegrityError(
            "final eligibility process clock predates admission verification"
        )

    state = _state_with_expiry_precedence(
        admission_valid_until=admission_receipt.valid_until,
        events=events,
        observed_at=now,
    )
    head_hash = events[-1].event_hash if events else lifecycle.ZERO_EVENT_HASH
    values = {
        "projection_id": projection_id,
        "contract_version": FINAL_ELIGIBILITY_VERSION,
        "authority_key": admission_receipt.authority_key,
        "admission_id": admission_receipt.admission_id,
        "admission_hash": admission_receipt.admission_hash,
        "final_admission_verification_hash": final_verification.verification_hash,
        "w84_admission_source_proof_hash": final_verification.w84_admission_source_proof_hash,
        "admission_valid_until": admission_receipt.valid_until,
        "lifecycle_head_hash": head_hash,
        "lifecycle_events_count": len(events),
        "state": state,
        "observed_at": now,
        "paper_candidate_currently_eligible": state is PaperCandidateEligibilityState.ACTIVE,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperCandidateFinalEligibility(
        **values,
        projection_hash=_hash(_payload_from_values(values)),
    )


def _state_with_expiry_precedence(
    *,
    admission_valid_until: datetime | None,
    events: tuple[lifecycle.PaperCandidateLifecycleEvent, ...],
    observed_at: datetime,
) -> PaperCandidateEligibilityState:
    if admission_valid_until is None:
        raise PaperCandidateFinalEligibilityIntegrityError(
            "final eligibility requires finite admission validity"
        )
    if _utc(observed_at) > _utc(admission_valid_until):
        return PaperCandidateEligibilityState.EXPIRED
    if not events:
        return PaperCandidateEligibilityState.ACTIVE
    last = events[-1]
    if last.action is PaperCandidateLifecycleAction.REVOKE:
        return PaperCandidateEligibilityState.REVOKED
    if last.action is PaperCandidateLifecycleAction.SUSPEND:
        return PaperCandidateEligibilityState.SUSPENDED
    if last.action is PaperCandidateLifecycleAction.REINSTATE:
        return PaperCandidateEligibilityState.ACTIVE
    raise PaperCandidateFinalEligibilityIntegrityError(
        "unprojectable final candidate lifecycle state"
    )


def _validate_final_verification(
    value: PaperCandidateAdmissionFinalVerification,
) -> None:
    if not isinstance(value, PaperCandidateAdmissionFinalVerification):
        raise TypeError(
            "final_verification must be PaperCandidateAdmissionFinalVerification"
        )
    expected = final_admission._hash(final_admission._payload(value, include_hash=False))
    if value.verification_hash != expected:
        raise PaperCandidateFinalEligibilityIntegrityError(
            "final admission verification hash mismatch"
        )
    if (
        value.admission_source_truth_verified is not True
        or value.w84_source_truth_verified is not True
        or value.w84_admission_source_proof_bound is not True
        or value.historical_w84_timestamp_used_for_freshness is not False
        or value.paper_candidate_was_admitted is not True
        or value.paper_execution_authorized is not False
        or value.external_execution_authorized is not False
        or value.runtime_execution_authorized is not False
        or value.capital_authority != "NONE"
        or value.live_trading != "BLOCKED"
    ):
        raise PaperCandidateFinalEligibilityIntegrityError(
            "final admission verification authority/source state is invalid"
        )


def _payload(
    value: PaperCandidateFinalEligibility, *, include_hash: bool
) -> dict[str, object]:
    names = tuple(
        field
        for field in PaperCandidateFinalEligibility.__dataclass_fields__
        if field != "projection_hash"
    )
    payload = _payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["projection_hash"] = value.projection_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    state = payload["state"]
    if not isinstance(state, PaperCandidateEligibilityState):
        raise PaperCandidateFinalEligibilityIntegrityError("state type invalid")
    payload["state"] = state.value
    for key in ("admission_valid_until", "observed_at"):
        raw = payload[key]
        if not isinstance(raw, datetime):
            raise PaperCandidateFinalEligibilityIntegrityError(f"{key} type invalid")
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
        raise PaperCandidateFinalEligibilityIntegrityError(
            "final W85 eligibility may not grant PAPER execution, capital, or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperCandidateFinalEligibilityIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperCandidateFinalEligibilityIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperCandidateFinalEligibilityIntegrityError(
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
    "FINAL_ELIGIBILITY_VERSION",
    "PaperCandidateFinalEligibility",
    "PaperCandidateFinalEligibilityIntegrityError",
    "project_final_paper_candidate_eligibility",
]
