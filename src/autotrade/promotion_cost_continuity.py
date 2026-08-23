from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import OrderIntent, intent_fingerprint
from autotrade.execution_cost_continuity import (
    CONTINUITY_BLOCKER,
    FEE_ACCOUNTING_BLOCKER,
    ExecutionCostContinuityEvidence,
    ExecutionCostContinuityStatus,
)
from autotrade.strategy_lab_promotion import PromotionGateStatus
from autotrade.strategy_promotion_assessment import StrategyPromotionAssessmentReceipt


RESOLUTION_CONTRACT_VERSION = "W81_PROMOTION_COST_CONTINUITY_RESOLUTION_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PromotionCostContinuityError(RuntimeError):
    pass


class PromotionCostContinuityIntegrityError(PromotionCostContinuityError):
    pass


class PromotionCostContinuityStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class PromotionCostContinuityResolution:
    resolution_id: str
    contract_version: str
    promotion_assessment_id: str
    promotion_assessment_hash: str
    promotion_policy_id: str
    promotion_policy_hash: str
    selected_strategy_id: str
    selected_strategy_version: str
    execution_gate_evidence_hashes: tuple[str, ...]
    continuity_evidence_hash: str
    continuity_measurement_hash: str
    intent_fingerprint: str
    status: PromotionCostContinuityStatus
    reason_codes: tuple[str, ...]
    resolved_promotion_blockers: tuple[str, ...]
    remaining_promotion_blockers: tuple[str, ...]
    fee_accounting_complete: bool
    strategy_version_execution_bound: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    resolved_at: datetime
    resolution_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("resolution_id", self.resolution_id),
            ("promotion_assessment_id", self.promotion_assessment_id),
            ("promotion_policy_id", self.promotion_policy_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            _require_id(value, label)
        if self.contract_version != RESOLUTION_CONTRACT_VERSION:
            raise PromotionCostContinuityIntegrityError("resolution contract version is not canonical W81")
        for label, value in (
            ("promotion_assessment_hash", self.promotion_assessment_hash),
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("continuity_evidence_hash", self.continuity_evidence_hash),
            ("continuity_measurement_hash", self.continuity_measurement_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("resolution_hash", self.resolution_hash),
        ):
            _require_hash(value, label)
        if not self.execution_gate_evidence_hashes:
            raise PromotionCostContinuityIntegrityError("execution gate evidence hashes are required")
        if self.execution_gate_evidence_hashes != tuple(sorted(set(self.execution_gate_evidence_hashes))):
            raise PromotionCostContinuityIntegrityError("execution gate evidence hashes must be unique sorted")
        for value in self.execution_gate_evidence_hashes:
            _require_hash(value, "execution gate evidence hash")
        if not isinstance(self.status, PromotionCostContinuityStatus):
            raise PromotionCostContinuityIntegrityError("invalid resolution status")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise PromotionCostContinuityIntegrityError("resolution reason codes must be unique sorted")
        if any(not isinstance(value, str) or not value.strip() for value in self.reason_codes):
            raise PromotionCostContinuityIntegrityError("resolution reason code is invalid")
        expected_resolved = (CONTINUITY_BLOCKER,) if self.status is PromotionCostContinuityStatus.PASS else ()
        if self.resolved_promotion_blockers != expected_resolved:
            raise PromotionCostContinuityIntegrityError("resolved blocker set does not match resolution status")
        if CONTINUITY_BLOCKER in self.remaining_promotion_blockers and self.status is PromotionCostContinuityStatus.PASS:
            raise PromotionCostContinuityIntegrityError("PASS resolution may not retain continuity blocker")
        if self.status is PromotionCostContinuityStatus.BLOCKED and CONTINUITY_BLOCKER not in self.remaining_promotion_blockers:
            raise PromotionCostContinuityIntegrityError("BLOCKED resolution must retain continuity blocker")
        if FEE_ACCOUNTING_BLOCKER not in self.remaining_promotion_blockers:
            raise PromotionCostContinuityIntegrityError("W81 may not close fee accounting")
        if self.fee_accounting_complete is not False:
            raise PromotionCostContinuityIntegrityError("W81 fee accounting must remain incomplete")
        if self.strategy_version_execution_bound is not False:
            raise PromotionCostContinuityIntegrityError("W81 may not claim strategy-version execution binding")
        if "EXECUTION_STRATEGY_VERSION_UNBOUND" not in self.remaining_promotion_blockers:
            raise PromotionCostContinuityIntegrityError("strategy-version blocker must remain open")
        if self.status is PromotionCostContinuityStatus.PASS and self.reason_codes:
            raise PromotionCostContinuityIntegrityError("PASS resolution may not carry failure reasons")
        if self.status is PromotionCostContinuityStatus.BLOCKED and not self.reason_codes:
            raise PromotionCostContinuityIntegrityError("BLOCKED resolution requires reason code")
        if self.paper_candidate_authorized is not False or self.external_execution_authorized is not False:
            raise PromotionCostContinuityIntegrityError("W81 resolution may not authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise PromotionCostContinuityIntegrityError("W81 resolution may not grant capital or LIVE authority")
        _require_aware(self.resolved_at, "resolved_at")
        if self.resolution_hash != _hash(_payload(self, include_hash=False)):
            raise PromotionCostContinuityIntegrityError("promotion cost continuity resolution hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def resolve_promotion_cost_continuity(
    *,
    resolution_id: str,
    assessment: StrategyPromotionAssessmentReceipt,
    continuity: ExecutionCostContinuityEvidence,
    execution_intent: OrderIntent,
    resolved_at: datetime,
) -> PromotionCostContinuityResolution:
    """Bind W81 scientific continuity to the exact W80 candidate assessment.

    A standalone W81 evidence object is not enough to remove a promotion blocker.
    The exact W78 measurement hash must already be part of the W80
    EXECUTION_SENSITIVITY gate for the same selected strategy.
    """

    _require_id(resolution_id, "resolution_id")
    if not isinstance(assessment, StrategyPromotionAssessmentReceipt):
        raise TypeError("assessment must be StrategyPromotionAssessmentReceipt")
    if not isinstance(continuity, ExecutionCostContinuityEvidence):
        raise TypeError("continuity must be ExecutionCostContinuityEvidence")
    if not isinstance(execution_intent, OrderIntent):
        raise TypeError("execution_intent must be OrderIntent")
    _require_aware(resolved_at, "resolved_at")

    intent_hash = intent_fingerprint(execution_intent)
    if continuity.intent_fingerprint != intent_hash:
        raise PromotionCostContinuityIntegrityError("continuity/intent fingerprint mismatch")
    if execution_intent.strategy_id != assessment.selected_strategy_id:
        raise PromotionCostContinuityIntegrityError("continuity strategy differs from frozen W80 candidate")
    if CONTINUITY_BLOCKER not in assessment.promotion_blockers:
        raise PromotionCostContinuityIntegrityError("W80 assessment does not contain continuity blocker")
    if FEE_ACCOUNTING_BLOCKER not in assessment.promotion_blockers:
        raise PromotionCostContinuityIntegrityError("W80 assessment does not preserve fee blocker")

    execution_gate = next(
        (gate for gate in assessment.gates if gate.gate_id == "EXECUTION_SENSITIVITY"),
        None,
    )
    if execution_gate is None:
        raise PromotionCostContinuityIntegrityError("W80 assessment is missing execution gate")

    reasons: list[str] = []
    if execution_gate.status is not PromotionGateStatus.PASS:
        reasons.append("EXECUTION_SENSITIVITY_GATE_NOT_PASS")
    if continuity.status is not ExecutionCostContinuityStatus.PASS:
        reasons.append("NON_FEE_CONTINUITY_NOT_PROVEN")
    if continuity.sensitivity_measurement_hash not in execution_gate.evidence_hashes:
        reasons.append("W81_MEASUREMENT_NOT_BOUND_TO_W80_EXECUTION_GATE")

    status = PromotionCostContinuityStatus.PASS if not reasons else PromotionCostContinuityStatus.BLOCKED
    resolved = (CONTINUITY_BLOCKER,) if status is PromotionCostContinuityStatus.PASS else ()
    remaining = tuple(sorted(set(assessment.promotion_blockers) - set(resolved)))
    values = {
        "resolution_id": resolution_id,
        "contract_version": RESOLUTION_CONTRACT_VERSION,
        "promotion_assessment_id": assessment.assessment_id,
        "promotion_assessment_hash": assessment.assessment_hash,
        "promotion_policy_id": assessment.policy_id,
        "promotion_policy_hash": assessment.policy_hash,
        "selected_strategy_id": assessment.selected_strategy_id,
        "selected_strategy_version": assessment.selected_strategy_version,
        "execution_gate_evidence_hashes": tuple(sorted(execution_gate.evidence_hashes)),
        "continuity_evidence_hash": continuity.evidence_hash,
        "continuity_measurement_hash": continuity.sensitivity_measurement_hash,
        "intent_fingerprint": intent_hash,
        "status": status,
        "reason_codes": tuple(sorted(reasons)),
        "resolved_promotion_blockers": resolved,
        "remaining_promotion_blockers": remaining,
        "fee_accounting_complete": False,
        "strategy_version_execution_bound": False,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "resolved_at": resolved_at,
    }
    return PromotionCostContinuityResolution(
        **values,
        resolution_hash=_hash(_payload_from_values(values)),
    )


def _payload(value: PromotionCostContinuityResolution, *, include_hash: bool) -> dict[str, object]:
    payload = _payload_from_values(
        {
            "resolution_id": value.resolution_id,
            "contract_version": value.contract_version,
            "promotion_assessment_id": value.promotion_assessment_id,
            "promotion_assessment_hash": value.promotion_assessment_hash,
            "promotion_policy_id": value.promotion_policy_id,
            "promotion_policy_hash": value.promotion_policy_hash,
            "selected_strategy_id": value.selected_strategy_id,
            "selected_strategy_version": value.selected_strategy_version,
            "execution_gate_evidence_hashes": value.execution_gate_evidence_hashes,
            "continuity_evidence_hash": value.continuity_evidence_hash,
            "continuity_measurement_hash": value.continuity_measurement_hash,
            "intent_fingerprint": value.intent_fingerprint,
            "status": value.status,
            "reason_codes": value.reason_codes,
            "resolved_promotion_blockers": value.resolved_promotion_blockers,
            "remaining_promotion_blockers": value.remaining_promotion_blockers,
            "fee_accounting_complete": value.fee_accounting_complete,
            "strategy_version_execution_bound": value.strategy_version_execution_bound,
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
    payload["execution_gate_evidence_hashes"] = list(payload["execution_gate_evidence_hashes"])  # type: ignore[arg-type]
    payload["reason_codes"] = list(payload["reason_codes"])  # type: ignore[arg-type]
    payload["resolved_promotion_blockers"] = list(payload["resolved_promotion_blockers"])  # type: ignore[arg-type]
    payload["remaining_promotion_blockers"] = list(payload["remaining_promotion_blockers"])  # type: ignore[arg-type]
    payload["resolved_at"] = _utc_iso(payload["resolved_at"])  # type: ignore[arg-type]
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PromotionCostContinuityIntegrityError(f"{label} must be canonical identifier")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PromotionCostContinuityIntegrityError(f"{label} must be lowercase sha256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PromotionCostContinuityIntegrityError(f"{label} must be timezone-aware datetime")


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _enum_value(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "PromotionCostContinuityError",
    "PromotionCostContinuityIntegrityError",
    "PromotionCostContinuityResolution",
    "PromotionCostContinuityStatus",
    "RESOLUTION_CONTRACT_VERSION",
    "resolve_promotion_cost_continuity",
]
