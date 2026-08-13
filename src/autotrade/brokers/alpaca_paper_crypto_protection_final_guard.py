from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
import re

from autotrade.domain import OrderStatus

from .alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from .alpaca_paper_crypto_order import AlpacaPaperCryptoOrderRequest, CryptoOrderRole
from .alpaca_paper_crypto_protection_coordinator import PreparedCryptoProtectionPackage
from .alpaca_paper_crypto_protection_operator_decision import (
    CryptoProtectionOperatorDecision,
    CryptoProtectionOperatorDecisionStatus,
    SQLiteCryptoProtectionOperatorDecisionRegistry,
)
from .alpaca_paper_crypto_reconciliation import CryptoBrokerPositionSnapshot


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
POSITION_FRESHNESS_TTL = timedelta(seconds=10)


class CryptoProtectionFinalWritePhase(str, Enum):
    PRE_CONSUME = "PRE_CONSUME"
    PRE_IO = "PRE_IO"


class CryptoProtectionFinalGuardError(RuntimeError):
    pass


class CryptoProtectionFinalGuardBlocked(CryptoProtectionFinalGuardError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoProtectionFinalWriteAttestation:
    phase: CryptoProtectionFinalWritePhase
    package_hash: str
    operator_decision_hash: str
    attempt_id: str
    lifecycle_id: str
    order_id: str
    client_order_id: str
    lifecycle_status: CryptoLifecycleStatus
    lifecycle_control_hash: str
    lifecycle_event_head_hash: str
    protection_attempt_count: int
    oms_order_status: OrderStatus
    position_quantity: Decimal
    position_request_id: str
    position_response_sha256: str
    position_observed_at: datetime
    observed_at: datetime
    previous_attestation_hash: str | None
    attestation_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.phase, CryptoProtectionFinalWritePhase):
            raise TypeError("protection final attestation phase is invalid")
        for label, value in (
            ("package_hash", self.package_hash),
            ("operator_decision_hash", self.operator_decision_hash),
            ("lifecycle_control_hash", self.lifecycle_control_hash),
            ("lifecycle_event_head_hash", self.lifecycle_event_head_hash),
            ("position_response_sha256", self.position_response_sha256),
            ("attestation_hash", self.attestation_hash),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if self.previous_attestation_hash is not None and not _HASH_RE.fullmatch(self.previous_attestation_hash):
            raise ValueError("previous_attestation_hash must be lowercase SHA-256")
        if not isinstance(self.lifecycle_status, CryptoLifecycleStatus):
            raise TypeError("protection lifecycle status is invalid")
        if not isinstance(self.oms_order_status, OrderStatus):
            raise TypeError("protection OMS order status is invalid")
        if self.protection_attempt_count not in (0, 1):
            raise ValueError("protection final attestation allows attempt count 0 or 1 only")
        if not isinstance(self.position_quantity, Decimal) or not self.position_quantity.is_finite() or self.position_quantity <= 0:
            raise ValueError("protection final attestation requires positive finite position")
        _require_aware(self.position_observed_at, "position_observed_at")
        _require_aware(self.observed_at, "observed_at")
        if self.attestation_hash != _hash_json(_attestation_payload(self, include_hash=False)):
            raise ValueError("protection final attestation hash mismatch")


class CryptoPaperProtectionFinalGuard:
    """Last fail-closed guard for a protective SELL before authority consumption/I/O.

    This module performs no network I/O. A caller must supply a fresh GET-only
    broker position snapshot. PRE_CONSUME is only valid while the protective OMS
    order is VALIDATED and human authority is ISSUED. PRE_IO is only valid after
    the exact authority has been consumed, OMS is SUBMITTING, and lifecycle has
    crossed durable PROTECTION_SUBMISSION_UNKNOWN exactly once.
    """

    def __init__(self, *, order_store) -> None:
        if not callable(getattr(order_store, "get_by_order_id", None)):
            raise TypeError("protection final guard requires authoritative order store")
        self._orders = order_store

    def authorize(
        self,
        *,
        package: PreparedCryptoProtectionPackage,
        operator_decision: CryptoProtectionOperatorDecision,
        operator_registry: SQLiteCryptoProtectionOperatorDecisionRegistry,
        broker_order: AlpacaPaperCryptoOrderRequest,
        lifecycle: SQLiteCryptoPaperLifecycle,
        fresh_position: CryptoBrokerPositionSnapshot,
        now: datetime,
        phase: CryptoProtectionFinalWritePhase,
        expected_attempt_id: str | None = None,
        previous_attestation: CryptoProtectionFinalWriteAttestation | None = None,
    ) -> CryptoProtectionFinalWriteAttestation:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        if not isinstance(package, PreparedCryptoProtectionPackage):
            raise CryptoProtectionFinalGuardBlocked("exact prepared protection package is required")
        if not isinstance(operator_decision, CryptoProtectionOperatorDecision):
            raise CryptoProtectionFinalGuardBlocked("exact protection operator decision is required")
        if not isinstance(operator_registry, SQLiteCryptoProtectionOperatorDecisionRegistry):
            raise CryptoProtectionFinalGuardBlocked("authoritative protection operator registry is required")
        if not isinstance(broker_order, AlpacaPaperCryptoOrderRequest) or broker_order.role is not CryptoOrderRole.PROTECTION:
            raise CryptoProtectionFinalGuardBlocked("exact PROTECTION broker request is required")
        if not isinstance(lifecycle, SQLiteCryptoPaperLifecycle):
            raise CryptoProtectionFinalGuardBlocked("authoritative crypto lifecycle is required")
        if not isinstance(fresh_position, CryptoBrokerPositionSnapshot):
            raise CryptoProtectionFinalGuardBlocked("fresh GET-only broker position snapshot is required")
        if not isinstance(phase, CryptoProtectionFinalWritePhase):
            raise CryptoProtectionFinalGuardBlocked("protection final guard phase is invalid")

        if instant >= package.execution_deadline.astimezone(timezone.utc):
            raise CryptoProtectionFinalGuardBlocked("prepared protection package is expired")
        if instant >= package.risk_decision_valid_until.astimezone(timezone.utc):
            raise CryptoProtectionFinalGuardBlocked("protection RiskDecision is expired")
        if operator_decision.context.prepared_package_hash != package.package_hash:
            raise CryptoProtectionFinalGuardBlocked("protection operator decision package mismatch")
        if operator_decision.context.lifecycle_id != package.lifecycle_id:
            raise CryptoProtectionFinalGuardBlocked("protection operator decision lifecycle mismatch")
        if operator_decision.context.order_id != package.order_id:
            raise CryptoProtectionFinalGuardBlocked("protection operator decision order mismatch")
        if operator_decision.context.client_order_id != package.client_order_id:
            raise CryptoProtectionFinalGuardBlocked("protection operator decision client_order_id mismatch")
        attempt_id = operator_decision.context.attempt_id
        if expected_attempt_id is not None and expected_attempt_id != attempt_id:
            raise CryptoProtectionFinalGuardBlocked("protection expected attempt mismatch")

        if broker_order.fingerprint != package.crypto_order_fingerprint:
            raise CryptoProtectionFinalGuardBlocked("protective broker request differs from immutable package")
        if broker_order.payload_hash != package.crypto_order_payload_hash:
            raise CryptoProtectionFinalGuardBlocked("protective broker payload differs from immutable package")
        if broker_order.client_order_id != package.client_order_id:
            raise CryptoProtectionFinalGuardBlocked("protective client_order_id differs from immutable package")
        if broker_order.quantity != package.confirmed_net_long_quantity:
            raise CryptoProtectionFinalGuardBlocked("protective quantity differs from confirmed net long")

        decision_state = operator_registry.get(operator_decision.context.preparation_hash)
        if decision_state.decision != operator_decision:
            raise CryptoProtectionFinalGuardBlocked("durable protection operator decision differs")

        snapshot = lifecycle.snapshot(package.lifecycle_id)
        if snapshot.binding.fingerprint != package.lifecycle_binding_hash:
            raise CryptoProtectionFinalGuardBlocked("protection lifecycle binding drifted")
        if snapshot.state.protection_client_order_id != package.client_order_id:
            raise CryptoProtectionFinalGuardBlocked("lifecycle protection client_order_id drifted")
        if snapshot.state.protection_order_fingerprint != package.crypto_order_fingerprint:
            raise CryptoProtectionFinalGuardBlocked("lifecycle protection order fingerprint drifted")
        if snapshot.state.protection_quantity != package.confirmed_net_long_quantity:
            raise CryptoProtectionFinalGuardBlocked("lifecycle protection quantity drifted")
        if snapshot.state.confirmed_net_long_quantity != package.confirmed_net_long_quantity:
            raise CryptoProtectionFinalGuardBlocked("lifecycle confirmed net long drifted")

        oms_order = self._orders.get_by_order_id(package.order_id)
        if oms_order is None:
            raise CryptoProtectionFinalGuardBlocked("protective OMS order is missing")

        self._validate_position(package=package, position=fresh_position, now=instant)

        if phase is CryptoProtectionFinalWritePhase.PRE_CONSUME:
            if previous_attestation is not None:
                raise CryptoProtectionFinalGuardBlocked("PRE_CONSUME cannot chain a prior attestation")
            if decision_state.status is not CryptoProtectionOperatorDecisionStatus.ISSUED:
                raise CryptoProtectionFinalGuardBlocked("PRE_CONSUME requires ISSUED protection human authority")
            if not operator_decision.is_valid_at(instant):
                raise CryptoProtectionFinalGuardBlocked("protection human authority is expired or not yet valid")
            if snapshot.state.status is not CryptoLifecycleStatus.PROTECTION_PREPARED:
                raise CryptoProtectionFinalGuardBlocked("PRE_CONSUME requires PROTECTION_PREPARED")
            if snapshot.state.protection_attempt_count != 0:
                raise CryptoProtectionFinalGuardBlocked("PRE_CONSUME requires zero protection attempts")
            if snapshot.state.control_hash != package.lifecycle_control_hash:
                raise CryptoProtectionFinalGuardBlocked("PRE_CONSUME lifecycle control changed after preparation")
            if snapshot.state.event_head_hash != package.lifecycle_event_head_hash:
                raise CryptoProtectionFinalGuardBlocked("PRE_CONSUME lifecycle event head changed after preparation")
            if oms_order.status is not OrderStatus.VALIDATED:
                raise CryptoProtectionFinalGuardBlocked("PRE_CONSUME requires protective OMS VALIDATED")
            previous_hash = None
        else:
            if previous_attestation is None:
                raise CryptoProtectionFinalGuardBlocked("PRE_IO requires PRE_CONSUME attestation")
            self._validate_previous(
                previous=previous_attestation,
                package=package,
                decision=operator_decision,
                attempt_id=attempt_id,
            )
            if decision_state.status is not CryptoProtectionOperatorDecisionStatus.CONSUMED:
                raise CryptoProtectionFinalGuardBlocked("PRE_IO requires CONSUMED protection human authority")
            if decision_state.consumed_attempt_id != attempt_id:
                raise CryptoProtectionFinalGuardBlocked("PRE_IO protection decision consumed by different attempt")
            if snapshot.state.status is not CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN:
                raise CryptoProtectionFinalGuardBlocked("PRE_IO requires PROTECTION_SUBMISSION_UNKNOWN")
            if snapshot.state.protection_attempt_count != 1:
                raise CryptoProtectionFinalGuardBlocked("PRE_IO requires exactly one protection attempt")
            if oms_order.status is not OrderStatus.SUBMITTING:
                raise CryptoProtectionFinalGuardBlocked("PRE_IO requires protective OMS SUBMITTING")
            previous_hash = previous_attestation.attestation_hash

        values = {
            "phase": phase,
            "package_hash": package.package_hash,
            "operator_decision_hash": operator_decision.decision_hash,
            "attempt_id": attempt_id,
            "lifecycle_id": package.lifecycle_id,
            "order_id": package.order_id,
            "client_order_id": package.client_order_id,
            "lifecycle_status": snapshot.state.status,
            "lifecycle_control_hash": snapshot.state.control_hash,
            "lifecycle_event_head_hash": snapshot.state.event_head_hash,
            "protection_attempt_count": snapshot.state.protection_attempt_count,
            "oms_order_status": oms_order.status,
            "position_quantity": fresh_position.quantity,
            "position_request_id": fresh_position.request_id,
            "position_response_sha256": fresh_position.response_sha256,
            "position_observed_at": fresh_position.observed_at,
            "observed_at": instant,
            "previous_attestation_hash": previous_hash,
        }
        return CryptoProtectionFinalWriteAttestation(
            **values,
            attestation_hash=_hash_json(_attestation_payload_from_values(values)),
        )

    @staticmethod
    def _validate_position(
        *,
        package: PreparedCryptoProtectionPackage,
        position: CryptoBrokerPositionSnapshot,
        now: datetime,
    ) -> None:
        if position.symbol != package.symbol:
            raise CryptoProtectionFinalGuardBlocked("fresh broker position symbol differs from protection package")
        if position.absent or position.quantity <= 0:
            raise CryptoProtectionFinalGuardBlocked("fresh broker position is absent or flat before protection")
        if position.quantity != package.confirmed_net_long_quantity:
            raise CryptoProtectionFinalGuardBlocked("fresh broker position differs from exact confirmed net long")
        if position.observed_at > now + timedelta(seconds=3):
            raise CryptoProtectionFinalGuardBlocked("fresh broker position is future-dated")
        age = now - position.observed_at.astimezone(timezone.utc)
        if age < timedelta(seconds=-3) or age >= POSITION_FRESHNESS_TTL:
            raise CryptoProtectionFinalGuardBlocked("fresh broker position is stale")

    @staticmethod
    def _validate_previous(
        *,
        previous: CryptoProtectionFinalWriteAttestation,
        package: PreparedCryptoProtectionPackage,
        decision: CryptoProtectionOperatorDecision,
        attempt_id: str,
    ) -> None:
        if not isinstance(previous, CryptoProtectionFinalWriteAttestation):
            raise CryptoProtectionFinalGuardBlocked("previous protection final evidence has wrong type")
        if previous.phase is not CryptoProtectionFinalWritePhase.PRE_CONSUME:
            raise CryptoProtectionFinalGuardBlocked("PRE_IO previous evidence must be PRE_CONSUME")
        if previous.package_hash != package.package_hash:
            raise CryptoProtectionFinalGuardBlocked("PRE_IO previous package hash mismatch")
        if previous.operator_decision_hash != decision.decision_hash:
            raise CryptoProtectionFinalGuardBlocked("PRE_IO previous operator decision hash mismatch")
        if previous.attempt_id != attempt_id:
            raise CryptoProtectionFinalGuardBlocked("PRE_IO previous attempt mismatch")
        if previous.client_order_id != package.client_order_id:
            raise CryptoProtectionFinalGuardBlocked("PRE_IO previous client_order_id mismatch")
        if previous.lifecycle_status is not CryptoLifecycleStatus.PROTECTION_PREPARED:
            raise CryptoProtectionFinalGuardBlocked("PRE_IO previous lifecycle phase is invalid")
        if previous.protection_attempt_count != 0:
            raise CryptoProtectionFinalGuardBlocked("PRE_IO previous evidence already had protection attempt")
        if previous.oms_order_status is not OrderStatus.VALIDATED:
            raise CryptoProtectionFinalGuardBlocked("PRE_IO previous OMS state was not VALIDATED")
        if previous.position_quantity != package.confirmed_net_long_quantity:
            raise CryptoProtectionFinalGuardBlocked("PRE_IO previous position differs from package")


def _attestation_payload(
    value: CryptoProtectionFinalWriteAttestation,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload = _attestation_payload_from_values(
        {
            "phase": value.phase,
            "package_hash": value.package_hash,
            "operator_decision_hash": value.operator_decision_hash,
            "attempt_id": value.attempt_id,
            "lifecycle_id": value.lifecycle_id,
            "order_id": value.order_id,
            "client_order_id": value.client_order_id,
            "lifecycle_status": value.lifecycle_status,
            "lifecycle_control_hash": value.lifecycle_control_hash,
            "lifecycle_event_head_hash": value.lifecycle_event_head_hash,
            "protection_attempt_count": value.protection_attempt_count,
            "oms_order_status": value.oms_order_status,
            "position_quantity": value.position_quantity,
            "position_request_id": value.position_request_id,
            "position_response_sha256": value.position_response_sha256,
            "position_observed_at": value.position_observed_at,
            "observed_at": value.observed_at,
            "previous_attestation_hash": value.previous_attestation_hash,
        }
    )
    if include_hash:
        payload["attestation_hash"] = value.attestation_hash
    return payload


def _attestation_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, Enum):
            payload[key] = value.value
        elif isinstance(value, datetime):
            payload[key] = value.astimezone(timezone.utc).isoformat()
        elif isinstance(value, Decimal):
            payload[key] = format(value, "f")
        else:
            payload[key] = value
    return payload


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _hash_json(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CryptoPaperProtectionFinalGuard",
    "CryptoProtectionFinalGuardBlocked",
    "CryptoProtectionFinalGuardError",
    "CryptoProtectionFinalWriteAttestation",
    "CryptoProtectionFinalWritePhase",
    "POSITION_FRESHNESS_TTL",
]
