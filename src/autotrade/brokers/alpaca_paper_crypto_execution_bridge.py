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

from .alpaca_paper_crypto_canary_coordinator import PreparedCryptoPaperCanaryPackage
from .alpaca_paper_crypto_execution_attempt import CryptoExecutionAttemptCheckpoint
from .alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecision,
    CryptoOperatorDecisionContext,
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
)


class CryptoPaperExecutionBridgeError(RuntimeError):
    pass


class CryptoPaperExecutionBridgeBlocked(CryptoPaperExecutionBridgeError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoPaperExecutionStageResult:
    package_hash: str
    operator_decision_hash: str
    attempt_id: str
    checkpoint_hash: str
    order: OrderRecord
    handoff: ExternalSubmissionHandoff

    def __post_init__(self) -> None:
        if self.order.status is not OrderStatus.SUBMITTING:
            raise ValueError("crypto execution bridge result requires OMS SUBMITTING state")
        if self.order.order_id != self.handoff.order_id:
            raise ValueError("crypto execution bridge result order/handoff mismatch")


class CryptoPaperExecutionBridge:
    """No-network checkpoint-aware bridge into OMS SUBMITTING.

    PRE_CONSUME evidence must already be durably checkpointed. This bridge is
    the only crypto R6 production surface allowed to consume that exact human
    decision and call OMS.stage_external_submission. It cannot write to Alpaca.

    A crash after consumption but before OMS staging is resumable only by the
    same checkpoint/attempt. Once the writer later crosses durable UNKNOWN,
    restart policy moves to reconciliation-only outside this bridge.
    """

    def __init__(self, *, oms: OrderManagementSystem) -> None:
        if not isinstance(oms, OrderManagementSystem):
            raise TypeError("crypto execution bridge requires authoritative OrderManagementSystem")
        self._oms = oms

    def stage_after_checkpoint(
        self,
        *,
        package: PreparedCryptoPaperCanaryPackage,
        operator_decision: CryptoOperatorDecision,
        operator_registry: SQLiteCryptoOperatorDecisionRegistry,
        checkpoint: CryptoExecutionAttemptCheckpoint,
        risk_decision: RiskDecision,
        market: MarketSnapshot,
        consume_at: datetime,
        stage_at: datetime,
    ) -> CryptoPaperExecutionStageResult:
        if not isinstance(package, PreparedCryptoPaperCanaryPackage):
            raise CryptoPaperExecutionBridgeBlocked("prepared crypto PAPER canary package is required")
        if not isinstance(operator_decision, CryptoOperatorDecision):
            raise CryptoPaperExecutionBridgeBlocked("durable crypto human operator decision is required")
        if not isinstance(operator_registry, SQLiteCryptoOperatorDecisionRegistry):
            raise CryptoPaperExecutionBridgeBlocked("authoritative crypto operator registry is required")
        if not isinstance(checkpoint, CryptoExecutionAttemptCheckpoint):
            raise CryptoPaperExecutionBridgeBlocked("durable PRE_CONSUME checkpoint is required")
        if not isinstance(risk_decision, RiskDecision):
            raise CryptoPaperExecutionBridgeBlocked("RiskDecision is required")
        if not isinstance(market, MarketSnapshot):
            raise CryptoPaperExecutionBridgeBlocked("MarketSnapshot is required")
        _require_aware(consume_at, "consume_at")
        _require_aware(stage_at, "stage_at")
        consume_instant = consume_at.astimezone(timezone.utc)
        stage_instant = stage_at.astimezone(timezone.utc)
        if consume_instant > stage_instant:
            raise CryptoPaperExecutionBridgeBlocked("operator consumption cannot occur after OMS staging")

        if package.network_write_authorized is not False:
            raise CryptoPaperExecutionBridgeBlocked("prepared crypto package cannot carry network authority")
        if package.next_action != "OPERATOR_DECISION_REQUIRED":
            raise CryptoPaperExecutionBridgeBlocked("prepared crypto package action is not operator decision")
        if package.order_status != OrderStatus.VALIDATED.value:
            raise CryptoPaperExecutionBridgeBlocked("crypto execution bridge requires prepared VALIDATED state")
        if consume_instant < package.prepared_at or stage_instant >= package.execution_deadline:
            raise CryptoPaperExecutionBridgeBlocked("prepared crypto package execution deadline is not valid")

        attempt_id = operator_decision.context.attempt_id
        if checkpoint.attempt_id != attempt_id:
            raise CryptoPaperExecutionBridgeBlocked("checkpoint attempt does not match operator decision")
        if checkpoint.package_hash != package.package_hash:
            raise CryptoPaperExecutionBridgeBlocked("checkpoint package does not match prepared crypto package")
        if checkpoint.preparation_hash != operator_decision.context.preparation_hash:
            raise CryptoPaperExecutionBridgeBlocked("checkpoint preparation does not match operator decision")
        if checkpoint.operator_decision_hash != operator_decision.decision_hash:
            raise CryptoPaperExecutionBridgeBlocked("checkpoint operator decision hash mismatch")
        if checkpoint.order_id != package.order_id:
            raise CryptoPaperExecutionBridgeBlocked("checkpoint order does not match prepared package")
        if checkpoint.client_order_id != package.client_order_id:
            raise CryptoPaperExecutionBridgeBlocked("checkpoint client_order_id does not match prepared package")

        if risk_decision.decision_id != package.risk_decision_id:
            raise CryptoPaperExecutionBridgeBlocked("RiskDecision id does not match prepared package")
        if risk_decision.valid_until != package.risk_decision_valid_until:
            raise CryptoPaperExecutionBridgeBlocked("RiskDecision expiry does not match prepared package")
        if risk_decision.safety_state_version != package.risk_decision_safety_state_version:
            raise CryptoPaperExecutionBridgeBlocked("RiskDecision Safety version does not match prepared package")
        if risk_decision.market_fingerprint != package.market_fingerprint:
            raise CryptoPaperExecutionBridgeBlocked("RiskDecision market fingerprint does not match prepared package")
        if risk_decision.intent_fingerprint != package.intent_fingerprint:
            raise CryptoPaperExecutionBridgeBlocked("RiskDecision intent fingerprint does not match prepared package")
        if risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint:
            raise CryptoPaperExecutionBridgeBlocked("RiskDecision fingerprint does not match prepared package")
        if market_fingerprint(market) != package.market_fingerprint:
            raise CryptoPaperExecutionBridgeBlocked("MarketSnapshot does not match prepared package")

        expected_context = CryptoOperatorDecisionContext.from_prepared_package(
            package,
            attempt_id=attempt_id,
        )
        if operator_decision.context != expected_context:
            raise CryptoPaperExecutionBridgeBlocked("operator decision does not match exact prepared crypto package")
        if operator_decision.issued_at < package.prepared_at:
            raise CryptoPaperExecutionBridgeBlocked("operator decision predates prepared crypto package")
        if operator_decision.issued_at >= package.execution_deadline:
            raise CryptoPaperExecutionBridgeBlocked("operator decision was issued after package deadline")

        try:
            durable = operator_registry.get(expected_context.preparation_hash)
        except Exception as exc:
            raise CryptoPaperExecutionBridgeBlocked("durable crypto operator decision is unavailable or invalid") from exc
        if durable.decision != operator_decision:
            raise CryptoPaperExecutionBridgeBlocked("supplied crypto operator decision differs from durable evidence")
        if durable.status is CryptoOperatorDecisionStatus.CONSUMED:
            if durable.consumed_attempt_id != attempt_id or durable.consumed_at is None:
                raise CryptoPaperExecutionBridgeBlocked("crypto operator decision was consumed by another attempt")
        elif durable.status is CryptoOperatorDecisionStatus.ISSUED:
            if not operator_decision.is_valid_at(consume_instant):
                raise CryptoPaperExecutionBridgeBlocked("crypto operator decision is expired or not yet valid")
        else:
            raise CryptoPaperExecutionBridgeBlocked("crypto operator decision state is not resumable")

        # Checkpoint exists before authority is consumed. Replaying this exact
        # consume after a crash is idempotent for the same attempt only.
        try:
            consumed = operator_registry.consume(
                decision=operator_decision,
                attempt_id=attempt_id,
                now=consume_instant,
            )
        except Exception as exc:
            raise CryptoPaperExecutionBridgeBlocked("crypto operator decision consumption failed") from exc
        if (
            consumed.status is not CryptoOperatorDecisionStatus.CONSUMED
            or consumed.consumed_attempt_id != attempt_id
        ):
            raise CryptoPaperExecutionBridgeBlocked("crypto operator decision was not durably consumed")

        handoff_id = crypto_execution_handoff_id(
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
            raise CryptoPaperExecutionBridgeBlocked("OMS crypto external staging failed after human decision") from exc

        if staged.status is not OrderStatus.SUBMITTING:
            raise CryptoPaperExecutionBridgeBlocked("OMS did not enter SUBMITTING")
        if handoff.handoff_id != handoff_id:
            raise CryptoPaperExecutionBridgeBlocked("OMS crypto handoff id mismatch")
        if handoff.order_id != package.order_id:
            raise CryptoPaperExecutionBridgeBlocked("OMS crypto handoff order mismatch")
        if handoff.intent_fingerprint != package.intent_fingerprint:
            raise CryptoPaperExecutionBridgeBlocked("OMS crypto handoff intent mismatch")
        if handoff.risk_decision_id != package.risk_decision_id:
            raise CryptoPaperExecutionBridgeBlocked("OMS crypto handoff RiskDecision mismatch")
        if handoff.safety_state_version != package.risk_decision_safety_state_version:
            raise CryptoPaperExecutionBridgeBlocked("OMS crypto handoff Safety version mismatch")
        if handoff.market_fingerprint != package.market_fingerprint:
            raise CryptoPaperExecutionBridgeBlocked("OMS crypto handoff market mismatch")
        if handoff.decision_valid_until != package.risk_decision_valid_until:
            raise CryptoPaperExecutionBridgeBlocked("OMS crypto handoff expiry mismatch")

        return CryptoPaperExecutionStageResult(
            package_hash=package.package_hash,
            operator_decision_hash=operator_decision.decision_hash,
            attempt_id=attempt_id,
            checkpoint_hash=checkpoint.record_hash,
            order=staged,
            handoff=handoff,
        )


def crypto_execution_handoff_id(
    *,
    package: PreparedCryptoPaperCanaryPackage,
    operator_decision: CryptoOperatorDecision,
    checkpoint: CryptoExecutionAttemptCheckpoint,
) -> str:
    if checkpoint.package_hash != package.package_hash:
        raise CryptoPaperExecutionBridgeBlocked("cannot derive handoff from mismatched checkpoint package")
    if checkpoint.operator_decision_hash != operator_decision.decision_hash:
        raise CryptoPaperExecutionBridgeBlocked("cannot derive handoff from mismatched operator decision")
    raw = "|".join(
        (
            "R6_CRYPTO_EXECUTION_HANDOFF",
            package.package_hash,
            operator_decision.decision_hash,
            checkpoint.record_hash,
            checkpoint.attempt_id,
            package.order_id,
        )
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def _require_aware(now: datetime, label: str) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"crypto execution bridge {label} must be timezone-aware")


__all__ = [
    "CryptoPaperExecutionBridge",
    "CryptoPaperExecutionBridgeBlocked",
    "CryptoPaperExecutionBridgeError",
    "CryptoPaperExecutionStageResult",
    "crypto_execution_handoff_id",
]
