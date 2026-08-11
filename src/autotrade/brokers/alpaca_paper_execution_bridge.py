from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from autotrade.domain import (
    MarketSnapshot,
    OrderRecord,
    OrderStatus,
    RiskDecision,
    market_fingerprint,
    risk_decision_fingerprint,
)
from autotrade.oms import ExternalSubmissionHandoff, OrderManagementSystem

from .alpaca_paper_canary_coordinator import PreparedPaperCanaryPackage
from .alpaca_paper_operator_decision import (
    PaperOperatorDecision,
    PaperOperatorDecisionContext,
    PaperOperatorDecisionStatus,
    SQLitePaperOperatorDecisionRegistry,
)


class PaperExecutionBridgeError(RuntimeError):
    pass


class PaperExecutionBridgeBlocked(PaperExecutionBridgeError):
    pass


@dataclass(frozen=True, slots=True)
class PaperExecutionStageResult:
    package_hash: str
    operator_decision_hash: str
    attempt_id: str
    order: OrderRecord
    handoff: ExternalSubmissionHandoff

    def __post_init__(self) -> None:
        if self.order.status is not OrderStatus.SUBMITTING:
            raise ValueError("execution bridge result requires OMS SUBMITTING state")
        if self.order.order_id != self.handoff.order_id:
            raise ValueError("execution bridge result order/handoff mismatch")


class PaperCanaryExecutionBridge:
    """No-network bridge from explicit human approval to OMS SUBMITTING.

    The offline coordinator ends at VALIDATED + OPERATOR_DECISION_REQUIRED.
    This bridge is the only R6 production surface allowed to consume the
    durable human decision and call OMS.stage_external_submission. It has no
    writer or transport API. The external POST remains solely writer-owned.

    Crash safety is deliberate: the operator decision is consumed first. If a
    process dies before OMS staging, the exact same attempt may resume while
    the prepared package remains inside its execution deadline. A different
    attempt cannot reuse the decision.
    """

    def __init__(self, *, oms: OrderManagementSystem) -> None:
        if not isinstance(oms, OrderManagementSystem):
            raise TypeError("execution bridge requires authoritative OrderManagementSystem")
        self._oms = oms

    def stage_after_operator_decision(
        self,
        *,
        package: PreparedPaperCanaryPackage,
        operator_decision: PaperOperatorDecision,
        operator_registry: SQLitePaperOperatorDecisionRegistry,
        risk_decision: RiskDecision,
        market: MarketSnapshot,
        now: datetime,
    ) -> PaperExecutionStageResult:
        if not isinstance(package, PreparedPaperCanaryPackage):
            raise PaperExecutionBridgeBlocked("prepared PAPER canary package is required")
        if not isinstance(operator_decision, PaperOperatorDecision):
            raise PaperExecutionBridgeBlocked("durable human operator decision is required")
        if not isinstance(operator_registry, SQLitePaperOperatorDecisionRegistry):
            raise PaperExecutionBridgeBlocked("authoritative operator decision registry is required")
        if not isinstance(risk_decision, RiskDecision):
            raise PaperExecutionBridgeBlocked("RiskDecision is required")
        if not isinstance(market, MarketSnapshot):
            raise PaperExecutionBridgeBlocked("MarketSnapshot is required")
        _require_aware(now)
        instant = now.astimezone(timezone.utc)

        if package.network_write_authorized is not False:
            raise PaperExecutionBridgeBlocked("prepared package cannot carry network authority")
        if package.next_action != "OPERATOR_DECISION_REQUIRED":
            raise PaperExecutionBridgeBlocked("prepared package action is not operator decision")
        if package.order_status != OrderStatus.VALIDATED.value:
            raise PaperExecutionBridgeBlocked("execution bridge requires prepared VALIDATED state")
        if instant < package.prepared_at or instant >= package.execution_deadline:
            raise PaperExecutionBridgeBlocked("prepared package execution deadline is not valid")

        if risk_decision.decision_id != package.risk_decision_id:
            raise PaperExecutionBridgeBlocked("RiskDecision id does not match prepared package")
        if risk_decision.valid_until != package.risk_decision_valid_until:
            raise PaperExecutionBridgeBlocked("RiskDecision expiry does not match prepared package")
        if risk_decision.safety_state_version != package.risk_decision_safety_state_version:
            raise PaperExecutionBridgeBlocked("RiskDecision Safety version does not match prepared package")
        if risk_decision.market_fingerprint != package.market_fingerprint:
            raise PaperExecutionBridgeBlocked("RiskDecision market fingerprint does not match prepared package")
        if risk_decision.intent_fingerprint != package.intent_fingerprint:
            raise PaperExecutionBridgeBlocked("RiskDecision intent fingerprint does not match prepared package")
        if risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint:
            raise PaperExecutionBridgeBlocked("RiskDecision fingerprint does not match prepared package")
        if market_fingerprint(market) != package.market_fingerprint:
            raise PaperExecutionBridgeBlocked("MarketSnapshot does not match prepared package")

        expected_context = PaperOperatorDecisionContext.from_prepared_package(package)
        if operator_decision.context != expected_context:
            raise PaperExecutionBridgeBlocked("operator decision does not match exact prepared package")
        if operator_decision.issued_at < package.prepared_at:
            raise PaperExecutionBridgeBlocked("operator decision predates prepared package")
        if operator_decision.issued_at >= package.execution_deadline:
            raise PaperExecutionBridgeBlocked("operator decision was issued after package deadline")

        try:
            durable = operator_registry.get(expected_context.preparation_hash)
        except Exception as exc:
            raise PaperExecutionBridgeBlocked("durable operator decision is unavailable or invalid") from exc
        if durable.decision != operator_decision:
            raise PaperExecutionBridgeBlocked("supplied operator decision does not match durable evidence")
        if durable.status is PaperOperatorDecisionStatus.CONSUMED:
            if durable.consumed_attempt_id != package.attempt_id or durable.consumed_at is None:
                raise PaperExecutionBridgeBlocked("operator decision was consumed by another attempt")
        elif durable.status is PaperOperatorDecisionStatus.ISSUED:
            if not operator_decision.is_valid_at(instant):
                raise PaperExecutionBridgeBlocked("operator decision is expired or not yet valid")
        else:
            raise PaperExecutionBridgeBlocked("operator decision state is not resumable")

        # Consume before changing OMS state. A crash here has zero broker I/O;
        # the same attempt may replay consume idempotently and continue staging.
        try:
            consumed = operator_registry.consume(
                decision=operator_decision,
                attempt_id=package.attempt_id,
                now=instant,
            )
        except Exception as exc:
            raise PaperExecutionBridgeBlocked("operator decision consumption failed") from exc
        if (
            consumed.status is not PaperOperatorDecisionStatus.CONSUMED
            or consumed.consumed_attempt_id != package.attempt_id
        ):
            raise PaperExecutionBridgeBlocked("operator decision was not durably consumed")

        try:
            staged, handoff = self._oms.stage_external_submission(
                order_id=package.order_id,
                handoff_id=package.canary_approval_hash,
                decision=risk_decision,
                market=market,
                now=instant,
            )
        except Exception as exc:
            raise PaperExecutionBridgeBlocked("OMS external staging failed after human decision") from exc

        if staged.status is not OrderStatus.SUBMITTING:
            raise PaperExecutionBridgeBlocked("OMS did not enter SUBMITTING")
        if handoff.handoff_id != package.canary_approval_hash:
            raise PaperExecutionBridgeBlocked("OMS handoff does not match canary approval")
        if handoff.order_id != package.order_id:
            raise PaperExecutionBridgeBlocked("OMS handoff order does not match prepared package")
        if handoff.intent_fingerprint != package.intent_fingerprint:
            raise PaperExecutionBridgeBlocked("OMS handoff intent does not match prepared package")
        if handoff.risk_decision_id != package.risk_decision_id:
            raise PaperExecutionBridgeBlocked("OMS handoff RiskDecision does not match prepared package")
        if handoff.safety_state_version != package.risk_decision_safety_state_version:
            raise PaperExecutionBridgeBlocked("OMS handoff Safety version does not match prepared package")
        if handoff.market_fingerprint != package.market_fingerprint:
            raise PaperExecutionBridgeBlocked("OMS handoff market does not match prepared package")
        if handoff.decision_valid_until != package.risk_decision_valid_until:
            raise PaperExecutionBridgeBlocked("OMS handoff expiry does not match prepared package")

        return PaperExecutionStageResult(
            package_hash=package.package_hash,
            operator_decision_hash=operator_decision.decision_hash,
            attempt_id=package.attempt_id,
            order=staged,
            handoff=handoff,
        )


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("execution bridge time must be timezone-aware")
