from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    RiskDecision,
    intent_fingerprint,
)
from autotrade.oms import OrderManagementSystem

from .alpaca_paper_bracket import (
    AlpacaEquityBracketBuilder,
    AlpacaEquityBracketRequest,
    PaperEquityVenueRules,
)
from .alpaca_paper_canary import (
    PaperCanaryApproval,
    PaperCanaryContext,
    PaperCanaryGate,
)
from .alpaca_paper_canary_permit import (
    PaperCanaryPermitState,
    PaperCanaryPermitStatus,
    SQLitePaperCanaryPermitRegistry,
)
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation
from .alpaca_paper_submission import (
    PaperSubmissionBinding,
    PaperSubmissionState,
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUIRED_TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")


class PaperCanaryCoordinatorError(RuntimeError):
    pass


class PaperCanaryPreparationBlocked(PaperCanaryCoordinatorError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedPaperCanaryPackage:
    order_id: str
    client_order_id: str
    intent_fingerprint: str
    risk_decision_id: str
    risk_decision_safety_state_version: int
    market_fingerprint: str
    risk_decision_valid_until: datetime
    account_attestation_fingerprint: str
    submission_binding_hash: str
    submission_control_hash: str
    submission_event_head_hash: str
    bracket_payload_hash: str
    instrument_master_fingerprint: str
    canary_approval_hash: str
    permit_event_hash: str
    attempt_id: str
    notional: Decimal
    effective_notional_cap: Decimal
    approval_issued_at: datetime
    approval_expires_at: datetime
    execution_deadline: datetime
    prepared_at: datetime
    order_status: str
    network_write_authorized: bool
    next_action: str
    package_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("order_id", self.order_id),
            ("client_order_id", self.client_order_id),
            ("risk_decision_id", self.risk_decision_id),
            ("attempt_id", self.attempt_id),
        ):
            _require_id(value, label)
        for label, value in (
            ("intent_fingerprint", self.intent_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("submission_binding_hash", self.submission_binding_hash),
            ("submission_control_hash", self.submission_control_hash),
            ("submission_event_head_hash", self.submission_event_head_hash),
            ("bracket_payload_hash", self.bracket_payload_hash),
            ("instrument_master_fingerprint", self.instrument_master_fingerprint),
            ("canary_approval_hash", self.canary_approval_hash),
            ("permit_event_hash", self.permit_event_hash),
            ("package_hash", self.package_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("risk_decision_valid_until", self.risk_decision_valid_until),
            ("approval_issued_at", self.approval_issued_at),
            ("approval_expires_at", self.approval_expires_at),
            ("execution_deadline", self.execution_deadline),
            ("prepared_at", self.prepared_at),
        ):
            _require_aware(value, label)
        if (
            isinstance(self.risk_decision_safety_state_version, bool)
            or not isinstance(self.risk_decision_safety_state_version, int)
            or self.risk_decision_safety_state_version < 0
        ):
            raise ValueError("risk_decision_safety_state_version must be a non-negative integer")
        if not _finite_positive(self.notional):
            raise ValueError("notional must be finite and positive")
        if not _finite_positive(self.effective_notional_cap):
            raise ValueError("effective_notional_cap must be finite and positive")
        if self.notional > self.effective_notional_cap:
            raise ValueError("prepared canary notional exceeds effective cap")
        if self.approval_expires_at <= self.approval_issued_at:
            raise ValueError("approval expiry must be after issuance")
        expected_deadline = min(self.approval_expires_at, self.risk_decision_valid_until)
        if self.execution_deadline != expected_deadline:
            raise ValueError("execution_deadline must be the earliest authority expiry")
        if not self.approval_issued_at <= self.prepared_at < self.approval_expires_at:
            raise ValueError("prepared_at must be inside canary approval window")
        if self.prepared_at >= self.risk_decision_valid_until:
            raise ValueError("prepared package cannot outlive RiskDecision")
        if self.order_status != OrderStatus.VALIDATED.value:
            raise ValueError("offline coordinator must leave OMS order VALIDATED")
        if self.network_write_authorized is not False:
            raise ValueError("prepared package cannot authorize network write")
        if self.next_action != "OPERATOR_DECISION_REQUIRED":
            raise ValueError("prepared package must require explicit operator decision")
        expected_hash = _hash_json(_package_payload(self, include_hash=False))
        if self.package_hash != expected_hash:
            raise ValueError("prepared package hash mismatch")

    def canonical_payload(self) -> dict[str, object]:
        return _package_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PaperCanaryPreparationResult:
    package: PreparedPaperCanaryPackage
    order: OrderRecord
    bracket: AlpacaEquityBracketRequest
    binding: PaperSubmissionBinding
    submission_state: PaperSubmissionState
    approval: PaperCanaryApproval
    permit: PaperCanaryPermitState


class PaperCanaryCoordinator:
    """Offline-only coordinator for the first manual bounded PAPER canary.

    This component deliberately ends at OPERATOR_DECISION_REQUIRED. It may
    validate an OMS order, build the exact equity bracket, freeze the durable
    submission binding, run the strict canary gate and issue a durable canary
    permit. It MUST NOT stage OMS to SUBMITTING and has no writer/network API.
    """

    def __init__(
        self,
        *,
        oms: OrderManagementSystem,
        bracket_builder: AlpacaEquityBracketBuilder | None = None,
        canary_gate: PaperCanaryGate | None = None,
    ) -> None:
        if not isinstance(oms, OrderManagementSystem):
            raise TypeError("coordinator requires authoritative OrderManagementSystem")
        self._oms = oms
        self._bracket_builder = bracket_builder or AlpacaEquityBracketBuilder()
        self._canary_gate = canary_gate or PaperCanaryGate()

    def prepare(
        self,
        *,
        intent: OrderIntent,
        decision: RiskDecision,
        market: MarketSnapshot,
        account_attestation: AlpacaPaperAccountAttestation,
        venue_rules: PaperEquityVenueRules,
        take_profit_price: Decimal,
        stop_loss_price: Decimal,
        submission_registry: SQLitePaperSubmissionRegistry,
        permit_registry: SQLitePaperCanaryPermitRegistry,
        now: datetime,
        certified_tracks: tuple[str, ...],
        reconciliation_clean: bool,
        unresolved_unknown_orders: int,
        kill_switch_engaged: bool,
        health_allows_new_exposure: bool,
        prior_canary_submissions: int,
    ) -> PaperCanaryPreparationResult:
        _require_aware(now, "now")
        if certified_tracks != _REQUIRED_TRACKS:
            raise PaperCanaryPreparationBlocked("certified track set must be exactly R0-R5")
        if unresolved_unknown_orders < 0 or prior_canary_submissions < 0:
            raise PaperCanaryPreparationBlocked("canary counters cannot be negative")

        order = self._oms.validate_for_external_submission(
            intent=intent,
            decision=decision,
            market=market,
            now=now,
        )
        if order.status is not OrderStatus.VALIDATED:
            raise PaperCanaryPreparationBlocked("coordinator requires OMS VALIDATED state")

        bracket = self._bracket_builder.build(
            order=order,
            venue_rules=venue_rules,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
        )
        if bracket.order_id != order.order_id:
            raise PaperCanaryPreparationBlocked("bracket order identity mismatch")

        binding = PaperSubmissionBinding.from_order(
            order=order,
            account_attestation_fingerprint=account_attestation.fingerprint,
            order_payload_hash=bracket.payload_hash,
            created_at=order.created_at,
        )
        submission_state = submission_registry.prepare(binding)
        if submission_state.status is not PaperSubmissionStatus.PREPARED:
            raise PaperCanaryPreparationBlocked("prepared coordinator state is not PREPARED")
        if submission_state.attempt_count != 0:
            raise PaperCanaryPreparationBlocked("prepared coordinator state already has submit attempts")

        approval = self._canary_gate.approve(
            PaperCanaryContext(
                order=order,
                binding=binding,
                submission_state=submission_state,
                account_attestation=account_attestation,
                now=now,
                certified_tracks=certified_tracks,
                reconciliation_clean=reconciliation_clean,
                unresolved_unknown_orders=unresolved_unknown_orders,
                kill_switch_engaged=kill_switch_engaged,
                health_allows_new_exposure=health_allows_new_exposure,
                prior_canary_submissions=prior_canary_submissions,
            )
        )
        permit = permit_registry.issue(approval)
        if permit.status is not PaperCanaryPermitStatus.ISSUED:
            raise PaperCanaryPreparationBlocked(
                "coordinator refuses an already-consumed canary permit"
            )
        if permit.order_id != binding.order_id or permit.binding_hash != binding.fingerprint:
            raise PaperCanaryPreparationBlocked("durable permit does not match frozen submission")

        # Final brokerless replay closes the preparation race: Safety, Market,
        # Health and idempotent OMS identity must still be valid after durable
        # binding + permit issuance. This intentionally leaves the order VALIDATED.
        replay = self._oms.validate_for_external_submission(
            intent=intent,
            decision=decision,
            market=market,
            now=now,
        )
        if replay != order or replay.status is not OrderStatus.VALIDATED:
            raise PaperCanaryPreparationBlocked("OMS changed during offline preparation")

        attempt_id = deterministic_canary_attempt_id(
            order=order,
            decision=decision,
            binding=binding,
            bracket=bracket,
            approval=approval,
        )
        package = _build_package(
            order=order,
            decision=decision,
            binding=binding,
            submission_state=submission_state,
            bracket=bracket,
            approval=approval,
            permit=permit,
            attempt_id=attempt_id,
            prepared_at=now,
        )
        return PaperCanaryPreparationResult(
            package=package,
            order=order,
            bracket=bracket,
            binding=binding,
            submission_state=submission_state,
            approval=approval,
            permit=permit,
        )


def deterministic_canary_attempt_id(
    *,
    order: OrderRecord,
    decision: RiskDecision,
    binding: PaperSubmissionBinding,
    bracket: AlpacaEquityBracketRequest,
    approval: PaperCanaryApproval,
) -> str:
    payload = {
        "account_attestation_fingerprint": approval.account_attestation_fingerprint,
        "approval_hash": approval.approval_hash,
        "binding_hash": binding.fingerprint,
        "bracket_payload_hash": bracket.payload_hash,
        "client_order_id": binding.client_order_id,
        "intent_fingerprint": intent_fingerprint(order.intent),
        "order_id": order.order_id,
        "risk_decision_id": order.risk_decision_id,
        "risk_decision_safety_state_version": decision.safety_state_version,
        "market_fingerprint": decision.market_fingerprint,
    }
    return f"r6-paper-attempt-{_hash_json(payload)[:48]}"


def _build_package(
    *,
    order: OrderRecord,
    decision: RiskDecision,
    binding: PaperSubmissionBinding,
    submission_state: PaperSubmissionState,
    bracket: AlpacaEquityBracketRequest,
    approval: PaperCanaryApproval,
    permit: PaperCanaryPermitState,
    attempt_id: str,
    prepared_at: datetime,
) -> PreparedPaperCanaryPackage:
    values: dict[str, object] = {
        "order_id": order.order_id,
        "client_order_id": binding.client_order_id,
        "intent_fingerprint": intent_fingerprint(order.intent),
        "risk_decision_id": decision.decision_id,
        "risk_decision_safety_state_version": decision.safety_state_version,
        "market_fingerprint": decision.market_fingerprint,
        "risk_decision_valid_until": decision.valid_until,
        "account_attestation_fingerprint": approval.account_attestation_fingerprint,
        "submission_binding_hash": binding.fingerprint,
        "submission_control_hash": submission_state.control_hash,
        "submission_event_head_hash": submission_state.event_head_hash,
        "bracket_payload_hash": bracket.payload_hash,
        "instrument_master_fingerprint": bracket.instrument_master_fingerprint,
        "canary_approval_hash": approval.approval_hash,
        "permit_event_hash": permit.event_hash,
        "attempt_id": attempt_id,
        "notional": approval.notional,
        "effective_notional_cap": approval.effective_notional_cap,
        "approval_issued_at": approval.issued_at,
        "approval_expires_at": approval.expires_at,
        "execution_deadline": min(approval.expires_at, decision.valid_until),
        "prepared_at": prepared_at.astimezone(timezone.utc),
        "order_status": order.status.value,
        "network_write_authorized": False,
        "next_action": "OPERATOR_DECISION_REQUIRED",
    }
    payload = _package_payload_from_values(values)
    values["package_hash"] = _hash_json(payload)
    return PreparedPaperCanaryPackage(**values)  # type: ignore[arg-type]


def _package_payload(package: PreparedPaperCanaryPackage, *, include_hash: bool) -> dict[str, object]:
    values = {
        "order_id": package.order_id,
        "client_order_id": package.client_order_id,
        "intent_fingerprint": package.intent_fingerprint,
        "risk_decision_id": package.risk_decision_id,
        "risk_decision_safety_state_version": package.risk_decision_safety_state_version,
        "market_fingerprint": package.market_fingerprint,
        "risk_decision_valid_until": package.risk_decision_valid_until,
        "account_attestation_fingerprint": package.account_attestation_fingerprint,
        "submission_binding_hash": package.submission_binding_hash,
        "submission_control_hash": package.submission_control_hash,
        "submission_event_head_hash": package.submission_event_head_hash,
        "bracket_payload_hash": package.bracket_payload_hash,
        "instrument_master_fingerprint": package.instrument_master_fingerprint,
        "canary_approval_hash": package.canary_approval_hash,
        "permit_event_hash": package.permit_event_hash,
        "attempt_id": package.attempt_id,
        "notional": package.notional,
        "effective_notional_cap": package.effective_notional_cap,
        "approval_issued_at": package.approval_issued_at,
        "approval_expires_at": package.approval_expires_at,
        "execution_deadline": package.execution_deadline,
        "prepared_at": package.prepared_at,
        "order_status": package.order_status,
        "network_write_authorized": package.network_write_authorized,
        "next_action": package.next_action,
    }
    payload = _package_payload_from_values(values)
    if include_hash:
        payload["package_hash"] = package.package_hash
    return payload


def _package_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    return {
        "account_attestation_fingerprint": values["account_attestation_fingerprint"],
        "approval_expires_at": _iso(values["approval_expires_at"]),
        "approval_issued_at": _iso(values["approval_issued_at"]),
        "attempt_id": values["attempt_id"],
        "bracket_payload_hash": values["bracket_payload_hash"],
        "canary_approval_hash": values["canary_approval_hash"],
        "client_order_id": values["client_order_id"],
        "effective_notional_cap": str(values["effective_notional_cap"]),
        "execution_deadline": _iso(values["execution_deadline"]),
        "instrument_master_fingerprint": values["instrument_master_fingerprint"],
        "intent_fingerprint": values["intent_fingerprint"],
        "network_write_authorized": values["network_write_authorized"],
        "next_action": values["next_action"],
        "notional": str(values["notional"]),
        "order_id": values["order_id"],
        "order_status": values["order_status"],
        "permit_event_hash": values["permit_event_hash"],
        "prepared_at": _iso(values["prepared_at"]),
        "risk_decision_id": values["risk_decision_id"],
        "risk_decision_safety_state_version": values["risk_decision_safety_state_version"],
        "market_fingerprint": values["market_fingerprint"],
        "risk_decision_valid_until": _iso(values["risk_decision_valid_until"]),
        "submission_binding_hash": values["submission_binding_hash"],
        "submission_control_hash": values["submission_control_hash"],
        "submission_event_head_hash": values["submission_event_head_hash"],
    }


def _iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise ValueError("package datetime field is invalid")
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _hash_json(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _finite_positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be canonical identifier")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
