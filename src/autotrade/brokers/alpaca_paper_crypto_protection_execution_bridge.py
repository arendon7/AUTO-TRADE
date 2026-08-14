from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256

from autotrade.domain import (
    MarketSnapshot,
    OrderRecord,
    OrderStatus,
    RiskDecision,
    market_fingerprint,
    risk_decision_fingerprint,
)
from autotrade.oms import ExternalSubmissionHandoff, OrderManagementSystem

from .alpaca_paper_crypto_protection_coordinator import PreparedCryptoProtectionPackage
from .alpaca_paper_crypto_protection_execution_attempt import (
    CryptoProtectionExecutionAttemptCheckpoint,
)
from .alpaca_paper_crypto_protection_operator_decision import (
    CryptoProtectionOperatorDecision,
    CryptoProtectionOperatorDecisionContext,
    CryptoProtectionOperatorDecisionStatus,
    SQLiteCryptoProtectionOperatorDecisionRegistry,
)


class CryptoProtectionExecutionBridgeError(RuntimeError):
    pass


class CryptoProtectionExecutionBridgeBlocked(CryptoProtectionExecutionBridgeError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoProtectionExecutionStageResult:
    package_hash: str
    operator_decision_hash: str
    attempt_id: str
    checkpoint_hash: str
    order: OrderRecord
    handoff: ExternalSubmissionHandoff

    def __post_init__(self) -> None:
        if self.order.status is not OrderStatus.SUBMITTING:
            raise ValueError("crypto protection execution bridge result requires OMS SUBMITTING state")
        if self.order.order_id != self.handoff.order_id:
            raise ValueError("crypto protection execution bridge result order/handoff mismatch")


class CryptoProtectionExecutionBridge:
    """No-network checkpoint-aware bridge into protective OMS SUBMITTING.

    PRE_CONSUME protection evidence must already be durably checkpointed. This
    bridge is the only protection R6 production surface allowed to consume the
    exact human decision and call OMS.stage_external_submission. It cannot write
    to Alpaca and never receives broker credentials.

    A crash after decision consumption but before OMS staging is resumable only
    by replaying the same immutable checkpoint/attempt. Once the separate writer
    later crosses PROTECTION_SUBMISSION_UNKNOWN, restart becomes reconciliation
    only and this bridge must not create another submission authority.
    """

    def __init__(self, *, oms: OrderManagementSystem) -> None:
        if not isinstance(oms, OrderManagementSystem):
            raise TypeError("crypto protection execution bridge requires authoritative OrderManagementSystem")
        self._oms = oms

    def stage_after_checkpoint(
        self,
        *,
        package: PreparedCryptoProtectionPackage,
        operator_decision: CryptoProtectionOperatorDecision,
        operator_registry: SQLiteCryptoProtectionOperatorDecisionRegistry,
        checkpoint: CryptoProtectionExecutionAttemptCheckpoint,
        risk_decision: RiskDecision,
        market: MarketSnapshot,
        consume_at: datetime,
        stage_at: datetime,
    ) -> CryptoProtectionExecutionStageResult:
        if not isinstance(package, PreparedCryptoProtectionPackage):
            raise CryptoProtectionExecutionBridgeBlocked("prepared crypto protection package is required")
        if not isinstance(operator_decision, CryptoProtectionOperatorDecision):
            raise CryptoProtectionExecutionBridgeBlocked("durable crypto protection human decision is required")
        if not isinstance(operator_registry, SQLiteCryptoProtectionOperatorDecisionRegistry):
            raise CryptoProtectionExecutionBridgeBlocked("authoritative protection operator registry is required")
        if not isinstance(checkpoint, CryptoProtectionExecutionAttemptCheckpoint):
            raise CryptoProtectionExecutionBridgeBlocked("durable protection PRE_CONSUME checkpoint is required")
        if not isinstance(risk_decision, RiskDecision):
            raise CryptoProtectionExecutionBridgeBlocked("protection RiskDecision is required")
        if not isinstance(market, MarketSnapshot):
            raise CryptoProtectionExecutionBridgeBlocked("protection MarketSnapshot is required")
        _require_aware(consume_at, "consume_at")
        _require_aware(stage_at, "stage_at")
        consume_instant = consume_at.astimezone(timezone.utc)
        stage_instant = stage_at.astimezone(timezone.utc)
        if consume_instant > stage_instant:
            raise CryptoProtectionExecutionBridgeBlocked(
                "protection operator consumption cannot occur after OMS staging"
            )

        if package.network_write_authorized is not False:
            raise CryptoProtectionExecutionBridgeBlocked("prepared protection package cannot carry network authority")
        if package.next_action != "OPERATOR_DECISION_REQUIRED":
            raise CryptoProtectionExecutionBridgeBlocked("prepared protection package action is not operator decision")
        if package.order_status != OrderStatus.VALIDATED.value:
            raise CryptoProtectionExecutionBridgeBlocked("protection execution bridge requires prepared VALIDATED state")
        if package.risk_reducing is not True:
            raise CryptoProtectionExecutionBridgeBlocked("protection execution bridge requires risk-reducing authority")
        if consume_instant < package.prepared_at or stage_instant >= package.execution_deadline:
            raise CryptoProtectionExecutionBridgeBlocked("prepared protection package execution deadline is not valid")

        attempt_id = operator_decision.context.attempt_id
        if checkpoint.attempt_id != attempt_id:
            raise CryptoProtectionExecutionBridgeBlocked("protection checkpoint attempt does not match operator decision")
        if checkpoint.package_hash != package.package_hash:
            raise CryptoProtectionExecutionBridgeBlocked("protection checkpoint package does not match prepared package")
        if checkpoint.operator_decision_hash != operator_decision.decision_hash:
            raise CryptoProtectionExecutionBridgeBlocked("protection checkpoint operator decision hash mismatch")
        if checkpoint.lifecycle_id != package.lifecycle_id:
            raise CryptoProtectionExecutionBridgeBlocked("protection checkpoint lifecycle does not match prepared package")
        if checkpoint.order_id != package.order_id:
            raise CryptoProtectionExecutionBridgeBlocked("protection checkpoint order does not match prepared package")
        if checkpoint.client_order_id != package.client_order_id:
            raise CryptoProtectionExecutionBridgeBlocked("protection checkpoint client_order_id does not match prepared package")

        if risk_decision.decision_id != package.risk_decision_id:
            raise CryptoProtectionExecutionBridgeBlocked("protection RiskDecision id does not match prepared package")
        if risk_decision.valid_until != package.risk_decision_valid_until:
            raise CryptoProtectionExecutionBridgeBlocked("protection RiskDecision expiry does not match prepared package")
        if risk_decision.safety_state_version != package.risk_decision_safety_state_version:
            raise CryptoProtectionExecutionBridgeBlocked("protection RiskDecision Safety version does not match prepared package")
        if risk_decision.market_fingerprint != package.market_fingerprint:
            raise CryptoProtectionExecutionBridgeBlocked("protection RiskDecision market fingerprint does not match prepared package")
        if risk_decision.intent_fingerprint != package.intent_fingerprint:
            raise CryptoProtectionExecutionBridgeBlocked("protection RiskDecision intent fingerprint does not match prepared package")
        if risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint:
            raise CryptoProtectionExecutionBridgeBlocked("protection RiskDecision fingerprint does not match prepared package")
        if risk_decision.risk_reducing is not True:
            raise CryptoProtectionExecutionBridgeBlocked("protection RiskDecision is not risk reducing")
        if market_fingerprint(market) != package.market_fingerprint:
            raise CryptoProtectionExecutionBridgeBlocked("protection MarketSnapshot does not match prepared package")

        expected_context = CryptoProtectionOperatorDecisionContext.from_prepared_package(
            package,
            attempt_id=attempt_id,
        )
        if operator_decision.context != expected_context:
            raise CryptoProtectionExecutionBridgeBlocked(
                "protection operator decision does not match exact prepared package"
            )
        if operator_decision.issued_at < package.prepared_at:
            raise CryptoProtectionExecutionBridgeBlocked("protection operator decision predates prepared package")
        if operator_decision.issued_at >= package.execution_deadline:
            raise CryptoProtectionExecutionBridgeBlocked("protection operator decision was issued after package deadline")

        try:
            durable = operator_registry.get(expected_context.preparation_hash)
        except Exception as exc:
            raise CryptoProtectionExecutionBridgeBlocked(
                "durable protection operator decision is unavailable or invalid"
            ) from exc
        if durable.decision != operator_decision:
            raise CryptoProtectionExecutionBridgeBlocked(
                "supplied protection operator decision differs from durable evidence"
            )
        if durable.status is CryptoProtectionOperatorDecisionStatus.CONSUMED:
            if durable.consumed_attempt_id != attempt_id or durable.consumed_at is None:
                raise CryptoProtectionExecutionBridgeBlocked(
                    "protection operator decision was consumed by another attempt"
                )
        elif durable.status is CryptoProtectionOperatorDecisionStatus.ISSUED:
            if not operator_decision.is_valid_at(consume_instant):
                raise CryptoProtectionExecutionBridgeBlocked(
                    "protection operator decision is expired or not yet valid"
                )
        else:
            raise CryptoProtectionExecutionBridgeBlocked(
                "protection operator decision state is not resumable"
            )

        try:
            consumed = operator_registry.consume(
                decision=operator_decision,
                attempt_id=attempt_id,
                now=consume_instant,
            )
        except Exception as exc:
            raise CryptoProtectionExecutionBridgeBlocked(
                "protection operator decision consumption failed"
            ) from exc
        if (
            consumed.status is not CryptoProtectionOperatorDecisionStatus.CONSUMED
            or consumed.consumed_attempt_id != attempt_id
        ):
            raise CryptoProtectionExecutionBridgeBlocked(
                "protection operator decision was not durably consumed"
            )

        handoff_id = crypto_protection_execution_handoff_id(
            package=package,
            operator_decision=operator_decision,
            checkpoint=checkpoint,
        )
        try:
            staged, handoff = self._oms.stage_external_submission(
                order_id=package.order_id,
                handoff_id=handoff_id,
                decision=risk_decision,
                market=market,
                now=stage_instant,
            )
        except Exception as exc:
            raise CryptoProtectionExecutionBridgeBlocked(
                "OMS crypto protection external staging failed after human decision"
            ) from exc

        if staged.status is not OrderStatus.SUBMITTING:
            raise CryptoProtectionExecutionBridgeBlocked("protective OMS did not enter SUBMITTING")
        if handoff.handoff_id != handoff_id:
            raise CryptoProtectionExecutionBridgeBlocked("protective OMS handoff id mismatch")
        if handoff.order_id != package.order_id:
            raise CryptoProtectionExecutionBridgeBlocked("protective OMS handoff order mismatch")
        if handoff.intent_fingerprint != package.intent_fingerprint:
            raise CryptoProtectionExecutionBridgeBlocked("protective OMS handoff intent mismatch")
        if handoff.risk_decision_id != package.risk_decision_id:
            raise CryptoProtectionExecutionBridgeBlocked("protective OMS handoff RiskDecision mismatch")
        if handoff.safety_state_version != package.risk_decision_safety_state_version:
            raise CryptoProtectionExecutionBridgeBlocked("protective OMS handoff Safety version mismatch")
        if handoff.market_fingerprint != package.market_fingerprint:
            raise CryptoProtectionExecutionBridgeBlocked("protective OMS handoff market mismatch")
        if handoff.decision_valid_until != package.risk_decision_valid_until:
            raise CryptoProtectionExecutionBridgeBlocked("protective OMS handoff expiry mismatch")

        return CryptoProtectionExecutionStageResult(
            package_hash=package.package_hash,
            operator_decision_hash=operator_decision.decision_hash,
            attempt_id=attempt_id,
            checkpoint_hash=checkpoint.record_hash,
            order=staged,
            handoff=handoff,
        )


def crypto_protection_execution_handoff_id(
    *,
    package: PreparedCryptoProtectionPackage,
    operator_decision: CryptoProtectionOperatorDecision,
    checkpoint: CryptoProtectionExecutionAttemptCheckpoint,
) -> str:
    if checkpoint.package_hash != package.package_hash:
        raise CryptoProtectionExecutionBridgeBlocked(
            "cannot derive protective handoff from mismatched checkpoint package"
        )
    if checkpoint.operator_decision_hash != operator_decision.decision_hash:
        raise CryptoProtectionExecutionBridgeBlocked(
            "cannot derive protective handoff from mismatched operator decision"
        )
    if checkpoint.lifecycle_id != package.lifecycle_id or checkpoint.order_id != package.order_id:
        raise CryptoProtectionExecutionBridgeBlocked(
            "cannot derive protective handoff from mismatched lifecycle/order"
        )
    raw = "|".join(
        (
            "R6_CRYPTO_PROTECTION_EXECUTION_HANDOFF",
            package.package_hash,
            operator_decision.decision_hash,
            checkpoint.record_hash,
            checkpoint.attempt_id,
            package.lifecycle_id,
            package.order_id,
            package.client_order_id,
        )
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def _require_aware(now: datetime, label: str) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"crypto protection execution bridge {label} must be timezone-aware")


__all__ = [
    "CryptoProtectionExecutionBridge",
    "CryptoProtectionExecutionBridgeBlocked",
    "CryptoProtectionExecutionBridgeError",
    "CryptoProtectionExecutionStageResult",
    "crypto_protection_execution_handoff_id",
]
