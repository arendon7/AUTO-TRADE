from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import OrderStatus, intent_fingerprint
from autotrade.health_bridge import (
    HealthBridgeControlProvider,
    HealthBridgeError,
    HealthRiskMode,
)
from autotrade.state import OrderStore, PortfolioStore, SafetyStateStore

from .alpaca_paper_bracket import AlpacaEquityBracketRequest
from .alpaca_paper_canary import PaperCanaryApproval
from .alpaca_paper_submission import (
    PaperSubmissionEventType,
    PaperSubmissionRegistrySnapshot,
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ZERO = Decimal("0")
_ONE = Decimal("1")


class PaperFinalWriteError(RuntimeError):
    pass


class PaperFinalWriteBlocked(PaperFinalWriteError):
    def __init__(self, reasons: list[str] | tuple[str, ...]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


class PaperFinalWritePhase(StrEnum):
    PRE_CONSUME = "PRE_CONSUME"
    PRE_IO = "PRE_IO"


@dataclass(frozen=True, slots=True)
class PaperFinalWriteAttestation:
    phase: PaperFinalWritePhase
    order_id: str
    client_order_id: str
    approval_hash: str
    binding_hash: str
    intent_fingerprint: str
    risk_decision_id: str
    safety_state_version: int
    portfolio_version: int
    portfolio_snapshot_id: str
    health_mode: HealthRiskMode
    health_reason: str
    strategy_health_fingerprint: str
    portfolio_health_fingerprint: str
    submission_status: PaperSubmissionStatus
    submission_event_sequence: int
    submission_head_hash: str
    submission_control_hash: str
    observed_at: datetime
    attestation_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.phase, PaperFinalWritePhase):
            raise ValueError("phase must be PaperFinalWritePhase")
        for label, value in (
            ("approval_hash", self.approval_hash),
            ("binding_hash", self.binding_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("submission_head_hash", self.submission_head_hash),
            ("submission_control_hash", self.submission_control_hash),
            ("attestation_hash", self.attestation_hash),
        ):
            if not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if self.safety_state_version < 0:
            raise ValueError("safety_state_version cannot be negative")
        if self.portfolio_version <= 0:
            raise ValueError("portfolio_version must be > 0")
        if self.submission_event_sequence <= 0:
            raise ValueError("submission_event_sequence must be > 0")
        _require_aware(self.observed_at, "observed_at")


class PaperFinalWriteGuard:
    """Authoritative just-in-time recheck immediately around external PAPER I/O.

    The guard trusts no caller-supplied booleans. It re-reads the authoritative
    OMS order, durable Safety/circuit state, Portfolio reconciliation state,
    Health bridge and tamper-verified global PAPER submission registry.
    It performs no network I/O and cannot place or retry an order.
    """

    def __init__(
        self,
        *,
        order_store: OrderStore,
        safety_state_store: SafetyStateStore,
        portfolio_store: PortfolioStore,
        health_bridge: HealthBridgeControlProvider,
        portfolio_health_entity_id: str,
    ) -> None:
        if (
            not isinstance(portfolio_health_entity_id, str)
            or not portfolio_health_entity_id
            or portfolio_health_entity_id != portfolio_health_entity_id.strip()
        ):
            raise ValueError("portfolio_health_entity_id must be canonical non-empty text")
        self._orders = order_store
        self._safety = safety_state_store
        self._portfolio = portfolio_store
        self._health = health_bridge
        self._portfolio_health_entity_id = portfolio_health_entity_id

    def authorize(
        self,
        *,
        approval: PaperCanaryApproval,
        expected_bracket: AlpacaEquityBracketRequest,
        submission_registry: SQLitePaperSubmissionRegistry,
        now: datetime,
        phase: PaperFinalWritePhase,
        expected_attempt_id: str | None = None,
    ) -> PaperFinalWriteAttestation:
        _require_aware(now, "now")
        if not isinstance(phase, PaperFinalWritePhase):
            raise ValueError("phase must be PaperFinalWritePhase")
        observed_at = now.astimezone(timezone.utc)
        reasons: list[str] = []

        if not approval.is_valid_at(observed_at):
            reasons.append("canary approval is not valid at final-write observation")
        if approval.order_id != expected_bracket.order_id:
            reasons.append("approval/bracket order_id mismatch")
        if approval.client_order_id != expected_bracket.client_order_id:
            reasons.append("approval/bracket client_order_id mismatch")

        current_order = self._orders.get_by_order_id(approval.order_id)
        if current_order is None:
            reasons.append("authoritative OMS order is missing")
            current_intent_fingerprint = "0" * 64
        else:
            current_intent_fingerprint = intent_fingerprint(current_order.intent)
            if current_order.status is not OrderStatus.SUBMITTING:
                reasons.append("authoritative OMS order must be durably SUBMITTING before external write")
            if current_order.risk_decision_id != approval.risk_decision_id:
                reasons.append("authoritative OMS risk_decision_id changed")
            if current_order.order_id != expected_bracket.order_id:
                reasons.append("authoritative OMS order identity mismatch")

        safety = self._safety.get()
        if safety.kill_switch_active:
            reasons.append("authoritative kill switch is active")
        if safety.circuit_active:
            reasons.append("authoritative safety circuit is active")

        try:
            versioned_portfolio = self._portfolio.get()
        except Exception as exc:  # fail closed on missing/corrupt authoritative state
            raise PaperFinalWriteBlocked(("authoritative Portfolio State unavailable",)) from exc
        portfolio = versioned_portfolio.snapshot
        if not portfolio.reconciliation_ok:
            reasons.append("authoritative Portfolio State reconciliation is not clean")
        if not portfolio.broker_state_known:
            reasons.append("authoritative broker state is unknown")

        if current_order is None:
            health = None
        else:
            try:
                health = self._health.effective_control(
                    strategy_id=current_order.intent.strategy_id,
                    portfolio_entity_id=self._portfolio_health_entity_id,
                    now=observed_at,
                )
            except (HealthBridgeError, Exception) as exc:  # noqa: BLE001 - external write must fail closed
                raise PaperFinalWriteBlocked(("authoritative Health control unavailable",)) from exc
            if health.mode is not HealthRiskMode.NORMAL:
                reasons.append("authoritative Health mode is not NORMAL")
            if (
                health.order_multiplier != _ONE
                or health.strategy_multiplier != _ONE
                or health.portfolio_multiplier != _ONE
            ):
                reasons.append("authoritative Health multipliers are not exactly 1")

        try:
            registry_snapshot = submission_registry.verified_global_snapshot(approval.order_id)
        except Exception as exc:  # tamper/missing state must block
            raise PaperFinalWriteBlocked(("durable PAPER submission registry is unavailable or corrupt",)) from exc
        binding = registry_snapshot.binding
        submission = registry_snapshot.state
        if binding.client_order_id != approval.client_order_id:
            reasons.append("frozen binding client_order_id mismatch")
        if binding.fingerprint != approval.binding_hash:
            reasons.append("canary approval binding hash mismatch")
        if binding.order_payload_hash != expected_bracket.payload_hash:
            reasons.append("frozen binding payload hash mismatch")
        if binding.risk_decision_id != approval.risk_decision_id:
            reasons.append("frozen binding risk_decision_id mismatch")
        if current_order is not None and binding.intent_fingerprint != current_intent_fingerprint:
            reasons.append("authoritative OMS intent changed after binding")

        other_states = tuple(
            state
            for state in registry_snapshot.all_states
            if state.order_id != approval.order_id
        )
        if any(state.status is PaperSubmissionStatus.UNKNOWN for state in other_states):
            reasons.append("another external PAPER submission is UNKNOWN")
        if any(state.attempt_count > 0 for state in other_states):
            reasons.append("R6 first-canary budget already has another attempted submission")

        if phase is PaperFinalWritePhase.PRE_CONSUME:
            if expected_attempt_id is not None:
                reasons.append("PRE_CONSUME must not carry expected_attempt_id")
            if submission.status is not PaperSubmissionStatus.PREPARED:
                reasons.append("PRE_CONSUME requires PREPARED submission state")
            if submission.attempt_count != 0:
                reasons.append("PRE_CONSUME requires zero prior external attempts")
            if submission.broker_order_id is not None or submission.broker_client_order_id is not None:
                reasons.append("PRE_CONSUME submission is already broker-bound")
        else:
            if not expected_attempt_id:
                reasons.append("PRE_IO requires expected_attempt_id")
            if submission.status is not PaperSubmissionStatus.UNKNOWN:
                reasons.append("PRE_IO requires durable UNKNOWN before network I/O")
            if submission.attempt_count != 1:
                reasons.append("PRE_IO requires exactly one durable submit attempt")
            latest = registry_snapshot.events[-1] if registry_snapshot.events else None
            if latest is None or latest.event_type is not PaperSubmissionEventType.SUBMIT_ATTEMPT_UNKNOWN:
                reasons.append("PRE_IO latest durable event is not SUBMIT_ATTEMPT_UNKNOWN")
            elif latest.payload.get("attempt_id") != expected_attempt_id:
                reasons.append("PRE_IO durable attempt_id mismatch")

        if reasons:
            raise PaperFinalWriteBlocked(reasons)
        assert current_order is not None
        assert health is not None

        payload = {
            "approval_hash": approval.approval_hash,
            "binding_hash": binding.fingerprint,
            "client_order_id": binding.client_order_id,
            "health_mode": health.mode.value,
            "health_reason": health.reason,
            "intent_fingerprint": current_intent_fingerprint,
            "observed_at": observed_at.isoformat(),
            "order_id": current_order.order_id,
            "phase": phase.value,
            "portfolio_health_fingerprint": health.portfolio_state_fingerprint,
            "portfolio_snapshot_id": portfolio.snapshot_id,
            "portfolio_version": versioned_portfolio.version,
            "risk_decision_id": current_order.risk_decision_id,
            "safety_state_version": safety.version,
            "strategy_health_fingerprint": health.strategy_state_fingerprint,
            "submission_control_hash": submission.control_hash,
            "submission_event_sequence": submission.event_sequence,
            "submission_head_hash": submission.event_head_hash,
            "submission_status": submission.status.value,
        }
        attestation_hash = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        return PaperFinalWriteAttestation(
            phase=phase,
            order_id=current_order.order_id,
            client_order_id=binding.client_order_id,
            approval_hash=approval.approval_hash,
            binding_hash=binding.fingerprint,
            intent_fingerprint=current_intent_fingerprint,
            risk_decision_id=current_order.risk_decision_id,
            safety_state_version=safety.version,
            portfolio_version=versioned_portfolio.version,
            portfolio_snapshot_id=portfolio.snapshot_id,
            health_mode=health.mode,
            health_reason=health.reason,
            strategy_health_fingerprint=health.strategy_state_fingerprint,
            portfolio_health_fingerprint=health.portfolio_state_fingerprint,
            submission_status=submission.status,
            submission_event_sequence=submission.event_sequence,
            submission_head_hash=submission.event_head_hash,
            submission_control_hash=submission.control_hash,
            observed_at=observed_at,
            attestation_hash=attestation_hash,
        )


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
