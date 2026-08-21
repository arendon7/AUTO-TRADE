from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json

from autotrade.domain import OrderStatus, intent_fingerprint
from autotrade.oms import ExternalSubmissionHandoff
from autotrade.paper_close_control_plane import PreparedPaperCloseControlPlane
from autotrade.paper_close_lifecycle import SQLitePaperCloseLifecycle
from autotrade.paper_close_plan import PaperCryptoClosePlan
from autotrade.paper_close_writer import (
    PaperCloseOperatorDecision,
    PaperCloseWriteReceipt,
    PaperCloseWriter,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.paper_portfolio import PaperPortfolioSnapshot


class PaperCloseExecutionBridgeError(RuntimeError):
    pass


class PaperCloseExecutionBridgeBlocked(PaperCloseExecutionBridgeError):
    pass


@dataclass(frozen=True, slots=True)
class PaperCloseExecutionAuthority:
    attempt_id: str
    plan_hash: str
    operator_decision_hash: str
    control_plane_fingerprint: str
    oms_order_id: str
    oms_handoff_hash: str
    risk_decision_id: str
    safety_state_version: int
    issued_at: datetime
    authority_hash: str


def bind_paper_close_execution_authority(
    *,
    plan: PaperCryptoClosePlan,
    operator_decision: PaperCloseOperatorDecision,
    control_plane: PreparedPaperCloseControlPlane,
    oms_handoff: ExternalSubmissionHandoff,
    now: datetime,
) -> PaperCloseExecutionAuthority:
    if not isinstance(plan, PaperCryptoClosePlan):
        raise PaperCloseExecutionBridgeBlocked("exact close plan is required")
    if not isinstance(operator_decision, PaperCloseOperatorDecision):
        raise PaperCloseExecutionBridgeBlocked("exact human close decision is required")
    if not isinstance(control_plane, PreparedPaperCloseControlPlane):
        raise PaperCloseExecutionBridgeBlocked("exact Safety/OMS close preparation is required")
    if not isinstance(oms_handoff, ExternalSubmissionHandoff):
        raise PaperCloseExecutionBridgeBlocked("exact OMS external handoff is required")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    instant = now.astimezone(timezone.utc)
    if not operator_decision.valid_at(instant):
        raise PaperCloseExecutionBridgeBlocked("human close decision is expired or not approved")
    if operator_decision.attempt_id != control_plane.attempt_id:
        raise PaperCloseExecutionBridgeBlocked("human decision attempt differs from Safety/OMS preparation")
    if operator_decision.plan_hash != plan.plan_hash or control_plane.plan_hash != plan.plan_hash:
        raise PaperCloseExecutionBridgeBlocked("plan hash differs across close authority chain")
    if operator_decision.portfolio_fingerprint != control_plane.portfolio_fingerprint:
        raise PaperCloseExecutionBridgeBlocked("broker Portfolio fingerprint differs across authority chain")
    if operator_decision.symbol != control_plane.intent.symbol or operator_decision.quantity != control_plane.intent.quantity:
        raise PaperCloseExecutionBridgeBlocked("human decision order terms differ from Safety intent")
    if operator_decision.limit_price != control_plane.intent.limit_price:
        raise PaperCloseExecutionBridgeBlocked("human decision limit differs from Safety intent")
    if control_plane.decision.risk_reducing is not True:
        raise PaperCloseExecutionBridgeBlocked("Safety decision is not strict risk reduction")
    if control_plane.order.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTING}:
        raise PaperCloseExecutionBridgeBlocked("OMS close order has invalid stage state")
    if oms_handoff.order_id != control_plane.order.order_id:
        raise PaperCloseExecutionBridgeBlocked("OMS handoff order differs from close preparation")
    if oms_handoff.intent_fingerprint != intent_fingerprint(control_plane.intent):
        raise PaperCloseExecutionBridgeBlocked("OMS handoff intent fingerprint mismatch")
    if oms_handoff.risk_decision_id != control_plane.decision.decision_id:
        raise PaperCloseExecutionBridgeBlocked("OMS handoff RiskDecision mismatch")
    if oms_handoff.safety_state_version != control_plane.decision.safety_state_version:
        raise PaperCloseExecutionBridgeBlocked("OMS handoff Safety version mismatch")
    if oms_handoff.market_fingerprint != control_plane.decision.market_fingerprint:
        raise PaperCloseExecutionBridgeBlocked("OMS handoff market fingerprint mismatch")
    if instant > oms_handoff.decision_valid_until.astimezone(timezone.utc):
        raise PaperCloseExecutionBridgeBlocked("OMS handoff RiskDecision has expired")

    values = {
        "attempt_id": control_plane.attempt_id,
        "plan_hash": plan.plan_hash,
        "operator_decision_hash": operator_decision.decision_hash,
        "control_plane_fingerprint": control_plane.fingerprint,
        "oms_order_id": control_plane.order.order_id,
        "oms_handoff_hash": oms_handoff.handoff_hash,
        "risk_decision_id": control_plane.decision.decision_id,
        "safety_state_version": control_plane.decision.safety_state_version,
        "issued_at": instant,
    }
    return PaperCloseExecutionAuthority(
        **values,
        authority_hash=_authority_hash(values),
    )


class PaperCloseExecutionBridge:
    """Only production R7 surface allowed to invoke the close writer."""

    def __init__(self, *, writer: PaperCloseWriter) -> None:
        if not isinstance(writer, PaperCloseWriter):
            raise TypeError("R7 close execution bridge requires PaperCloseWriter")
        self._writer = writer

    def execute_once(
        self,
        *,
        authority: PaperCloseExecutionAuthority,
        plan: PaperCryptoClosePlan,
        operator_decision: PaperCloseOperatorDecision,
        control_plane: PreparedPaperCloseControlPlane,
        lifecycle: SQLitePaperCloseLifecycle,
        fresh_portfolio: PaperPortfolioSnapshot,
        credentials: AlpacaPaperCredentials,
        now: datetime,
    ) -> PaperCloseWriteReceipt:
        if not isinstance(authority, PaperCloseExecutionAuthority):
            raise PaperCloseExecutionBridgeBlocked("bound R7 close execution authority is required")
        expected = _authority_hash(
            {
                "attempt_id": authority.attempt_id,
                "plan_hash": authority.plan_hash,
                "operator_decision_hash": authority.operator_decision_hash,
                "control_plane_fingerprint": authority.control_plane_fingerprint,
                "oms_order_id": authority.oms_order_id,
                "oms_handoff_hash": authority.oms_handoff_hash,
                "risk_decision_id": authority.risk_decision_id,
                "safety_state_version": authority.safety_state_version,
                "issued_at": authority.issued_at,
            }
        )
        if authority.authority_hash != expected:
            raise PaperCloseExecutionBridgeBlocked("R7 close execution authority hash mismatch")
        if authority.attempt_id != control_plane.attempt_id or authority.plan_hash != plan.plan_hash:
            raise PaperCloseExecutionBridgeBlocked("R7 execution authority attempt/plan mismatch")
        if authority.operator_decision_hash != operator_decision.decision_hash:
            raise PaperCloseExecutionBridgeBlocked("R7 execution authority human decision mismatch")
        if authority.control_plane_fingerprint != control_plane.fingerprint:
            raise PaperCloseExecutionBridgeBlocked("R7 execution authority control-plane mismatch")
        if authority.oms_order_id != control_plane.order.order_id:
            raise PaperCloseExecutionBridgeBlocked("R7 execution authority OMS order mismatch")
        if authority.risk_decision_id != control_plane.decision.decision_id:
            raise PaperCloseExecutionBridgeBlocked("R7 execution authority RiskDecision mismatch")
        if authority.safety_state_version != control_plane.decision.safety_state_version:
            raise PaperCloseExecutionBridgeBlocked("R7 execution authority Safety version mismatch")
        return self._writer.submit_once(
            lifecycle=lifecycle,
            attempt_id=authority.attempt_id,
            plan=plan,
            decision=operator_decision,
            fresh_portfolio=fresh_portfolio,
            credentials=credentials,
            now=now,
        )


def _authority_hash(values: dict[str, object]) -> str:
    canonical: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            canonical[key] = value.astimezone(timezone.utc).isoformat()
        else:
            canonical[key] = value
    return sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


__all__ = [
    "PaperCloseExecutionAuthority",
    "PaperCloseExecutionBridge",
    "PaperCloseExecutionBridgeBlocked",
    "PaperCloseExecutionBridgeError",
    "bind_paper_close_execution_authority",
]
