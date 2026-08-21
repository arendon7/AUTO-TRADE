from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from autotrade.brokers.paper_portfolio import PaperPortfolioSnapshot
from autotrade.domain import MarketSnapshot, OrderStatus, RiskDecision, market_fingerprint, risk_decision_fingerprint
from autotrade.oms import ExternalSubmissionHandoff, OrderManagementSystem
from autotrade.paper_close_plan import PaperCryptoClosePlan
from autotrade.paper_exit_attempt import PaperExitStatus, SQLitePaperExitAttempt
from autotrade.paper_exit_coordinator import PreparedPaperExitPackage
from autotrade.paper_exit_order import PaperExitOrder


FINAL_PORTFOLIO_TTL = timedelta(seconds=10)


class PaperExitFinalGuardError(RuntimeError):
    pass


class PaperExitFinalGuardBlocked(PaperExitFinalGuardError):
    pass


@dataclass(frozen=True, slots=True)
class PaperExitWritePermit:
    attempt_id: str
    package_hash: str
    plan_hash: str
    exit_order_hash: str
    client_order_id: str
    oms_order_id: str
    oms_handoff_hash: str
    fresh_portfolio_fingerprint: str
    lifecycle_control_hash: str
    lifecycle_event_head_hash: str
    observed_at: datetime
    expires_at: datetime
    write_authorized: bool
    retry_post: bool
    live_trading: str
    permit_hash: str

    def __post_init__(self) -> None:
        if self.write_authorized is not True or self.retry_post is not False or self.live_trading != "BLOCKED":
            raise ValueError("R7 exit permit authority invariants are invalid")
        if self.observed_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("R7 exit permit times must be timezone-aware")
        if self.observed_at >= self.expires_at:
            raise ValueError("R7 exit permit is expired")
        if self.permit_hash != _hash(_permit_payload(self, include_hash=False)):
            raise ValueError("R7 exit permit hash mismatch")


@dataclass(frozen=True, slots=True)
class PaperExitFinalGuardResult:
    permit: PaperExitWritePermit
    handoff: ExternalSubmissionHandoff


class PaperExitFinalGuard:
    """Final no-network risk-reducing gate before one R7 PAPER SELL.

    It refreshes position truth externally before entry, then consumes only the
    supplied immutable snapshot. It stages the exact OMS order but performs no
    broker write. The separate writer must durably enter SUBMISSION_UNKNOWN
    before its sole POST call.
    """

    def authorize(
        self,
        *,
        package: PreparedPaperExitPackage,
        plan: PaperCryptoClosePlan,
        exit_order: PaperExitOrder,
        fresh_portfolio: PaperPortfolioSnapshot,
        decision: RiskDecision,
        market: MarketSnapshot,
        oms: OrderManagementSystem,
        lifecycle: SQLitePaperExitAttempt,
        now: datetime,
    ) -> PaperExitFinalGuardResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise PaperExitFinalGuardBlocked("final guard time must be timezone-aware")
        instant = now.astimezone(timezone.utc)
        if not isinstance(package, PreparedPaperExitPackage) or not isinstance(plan, PaperCryptoClosePlan):
            raise PaperExitFinalGuardBlocked("exact prepared exit package and close plan are required")
        if not isinstance(exit_order, PaperExitOrder) or not isinstance(fresh_portfolio, PaperPortfolioSnapshot):
            raise PaperExitFinalGuardBlocked("exact exit order and fresh broker portfolio are required")
        if not isinstance(decision, RiskDecision) or not isinstance(market, MarketSnapshot):
            raise PaperExitFinalGuardBlocked("exact RiskDecision and MarketSnapshot are required")
        if not isinstance(oms, OrderManagementSystem) or not isinstance(lifecycle, SQLitePaperExitAttempt):
            raise PaperExitFinalGuardBlocked("authoritative OMS and exit lifecycle are required")
        if instant >= package.execution_deadline.astimezone(timezone.utc):
            raise PaperExitFinalGuardBlocked("prepared R7 exit package is expired")
        if package.plan_hash != plan.plan_hash or package.exit_order_hash != exit_order.order_hash:
            raise PaperExitFinalGuardBlocked("R7 exit package bindings differ from plan/order")
        if package.broker_payload_hash != exit_order.payload_hash or package.client_order_id != exit_order.client_order_id:
            raise PaperExitFinalGuardBlocked("R7 exit broker payload binding drifted")
        if package.risk_decision_fingerprint != risk_decision_fingerprint(decision):
            raise PaperExitFinalGuardBlocked("R7 exit RiskDecision differs from prepared package")
        if decision.risk_reducing is not True or decision.decision_id != package.risk_decision_id:
            raise PaperExitFinalGuardBlocked("R7 exit no longer has exact risk-reducing authority")
        if market_fingerprint(market) != package.market_fingerprint or market.symbol != plan.symbol:
            raise PaperExitFinalGuardBlocked("R7 exit market evidence differs from prepared package")

        age = instant - fresh_portfolio.observed_at.astimezone(timezone.utc)
        if age < timedelta(0) or age > FINAL_PORTFOLIO_TTL:
            raise PaperExitFinalGuardBlocked("fresh broker portfolio is stale or from the future")
        if fresh_portfolio.account.account_reference != plan.account_reference:
            raise PaperExitFinalGuardBlocked("fresh PAPER account differs from prepared close account")
        if fresh_portfolio.account.credential_reference != plan.credential_reference:
            raise PaperExitFinalGuardBlocked("fresh PAPER credential differs from prepared close credential")
        matches = [item for item in fresh_portfolio.positions if item.symbol == plan.symbol]
        if len(matches) != 1:
            raise PaperExitFinalGuardBlocked("final guard requires exactly one current broker position")
        position = matches[0]
        if position.asset_class != "crypto" or position.side != "long" or position.quantity <= 0:
            raise PaperExitFinalGuardBlocked("current broker position is not positive long crypto exposure")
        if position.quantity < plan.quantity or position.available_quantity < plan.quantity:
            raise PaperExitFinalGuardBlocked("current broker position/available quantity cannot satisfy close plan")
        if any(item.client_order_id == exit_order.client_order_id for item in fresh_portfolio.open_orders):
            raise PaperExitFinalGuardBlocked("exit client_order_id already exists at broker; reconcile instead of POST")

        snapshot = lifecycle.snapshot(exit_order.attempt_id)
        if snapshot.binding.plan_hash != plan.plan_hash or snapshot.binding.order_hash != exit_order.order_hash:
            raise PaperExitFinalGuardBlocked("durable exit lifecycle binding differs from package")
        if snapshot.state.status is not PaperExitStatus.PREPARED or snapshot.state.attempt_count != 0:
            raise PaperExitFinalGuardBlocked("exit POST authority has already been consumed; reconcile only")

        handoff_id = sha256(
            f"AUTO-TRADE:R7:EXIT-HANDOFF:{package.package_hash}:{fresh_portfolio.fingerprint}".encode()
        ).hexdigest()
        staged, handoff = oms.stage_external_submission(
            order_id=package.oms_order_id,
            handoff_id=handoff_id,
            decision=decision,
            market=market,
            now=instant,
        )
        if staged.status is not OrderStatus.SUBMITTING or handoff.order_id != package.oms_order_id:
            raise PaperExitFinalGuardBlocked("OMS did not stage exact R7 exit for external submission")
        expiry = min(
            package.execution_deadline.astimezone(timezone.utc),
            fresh_portfolio.observed_at.astimezone(timezone.utc) + FINAL_PORTFOLIO_TTL,
        )
        if instant >= expiry:
            raise PaperExitFinalGuardBlocked("fresh portfolio authority expired during final guard")
        values = {
            "attempt_id": exit_order.attempt_id,
            "package_hash": package.package_hash,
            "plan_hash": plan.plan_hash,
            "exit_order_hash": exit_order.order_hash,
            "client_order_id": exit_order.client_order_id,
            "oms_order_id": package.oms_order_id,
            "oms_handoff_hash": handoff.handoff_hash,
            "fresh_portfolio_fingerprint": fresh_portfolio.fingerprint,
            "lifecycle_control_hash": snapshot.state.control_hash,
            "lifecycle_event_head_hash": snapshot.state.event_head_hash,
            "observed_at": instant,
            "expires_at": expiry,
            "write_authorized": True,
            "retry_post": False,
            "live_trading": "BLOCKED",
        }
        permit = PaperExitWritePermit(**values, permit_hash=_hash(_permit_payload_from_values(values)))
        return PaperExitFinalGuardResult(permit=permit, handoff=handoff)


def _permit_payload(value: PaperExitWritePermit, *, include_hash: bool) -> dict[str, object]:
    payload = {
        "attempt_id": value.attempt_id,
        "package_hash": value.package_hash,
        "plan_hash": value.plan_hash,
        "exit_order_hash": value.exit_order_hash,
        "client_order_id": value.client_order_id,
        "oms_order_id": value.oms_order_id,
        "oms_handoff_hash": value.oms_handoff_hash,
        "fresh_portfolio_fingerprint": value.fresh_portfolio_fingerprint,
        "lifecycle_control_hash": value.lifecycle_control_hash,
        "lifecycle_event_head_hash": value.lifecycle_event_head_hash,
        "observed_at": value.observed_at.astimezone(timezone.utc).isoformat(),
        "expires_at": value.expires_at.astimezone(timezone.utc).isoformat(),
        "write_authorized": value.write_authorized,
        "retry_post": value.retry_post,
        "live_trading": value.live_trading,
    }
    if include_hash:
        payload["permit_hash"] = value.permit_hash
    return payload


def _permit_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["observed_at"] = values["observed_at"].astimezone(timezone.utc).isoformat()  # type: ignore[union-attr]
    payload["expires_at"] = values["expires_at"].astimezone(timezone.utc).isoformat()  # type: ignore[union-attr]
    return payload


def _hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


__all__ = [
    "FINAL_PORTFOLIO_TTL",
    "PaperExitFinalGuard",
    "PaperExitFinalGuardBlocked",
    "PaperExitFinalGuardError",
    "PaperExitFinalGuardResult",
    "PaperExitWritePermit",
]
