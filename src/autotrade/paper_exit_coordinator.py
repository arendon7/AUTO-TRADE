from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json

from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    OrderType,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
    risk_decision_fingerprint,
)
from autotrade.oms import OrderManagementSystem
from autotrade.paper_close_plan import PaperCryptoClosePlan
from autotrade.paper_exit_attempt import PaperExitSnapshot, SQLitePaperExitAttempt
from autotrade.paper_exit_order import PaperExitOrder, build_paper_exit_order


MANUAL_EXIT_STRATEGY_ID = "R7_MANUAL_RISK_REDUCTION"


class PaperExitCoordinatorError(RuntimeError):
    pass


class PaperExitCoordinatorBlocked(PaperExitCoordinatorError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedPaperExitPackage:
    attempt_id: str
    plan_hash: str
    exit_order_hash: str
    broker_payload_hash: str
    client_order_id: str
    oms_order_id: str
    intent_fingerprint: str
    risk_decision_id: str
    risk_decision_fingerprint: str
    safety_state_version: int
    market_fingerprint: str
    prepared_at: datetime
    execution_deadline: datetime
    risk_reducing: bool
    network_write_authorized: bool
    retry_post: bool
    live_trading: str
    package_hash: str

    def __post_init__(self) -> None:
        if self.risk_reducing is not True or self.network_write_authorized is not False:
            raise ValueError("prepared R7 exit package must be risk reducing and write inert")
        if self.retry_post is not False or self.live_trading != "BLOCKED":
            raise ValueError("prepared R7 exit package forbids retry and LIVE")
        if self.prepared_at.tzinfo is None or self.execution_deadline.tzinfo is None:
            raise ValueError("R7 exit package times must be timezone-aware")
        if self.prepared_at >= self.execution_deadline:
            raise ValueError("R7 exit package is already expired")
        if self.package_hash != _hash(_package_payload(self, include_hash=False)):
            raise ValueError("R7 exit package hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _package_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PaperExitPreparationResult:
    package: PreparedPaperExitPackage
    intent: OrderIntent
    exit_order: PaperExitOrder
    oms_order: OrderRecord
    lifecycle: PaperExitSnapshot


def build_manual_exit_intent(*, plan: PaperCryptoClosePlan, attempt_id: str) -> OrderIntent:
    if not isinstance(plan, PaperCryptoClosePlan):
        raise PaperExitCoordinatorBlocked("exact close plan is required")
    digest = sha256(f"AUTO-TRADE:R7:MANUAL-EXIT:{attempt_id}:{plan.plan_hash}".encode()).hexdigest()
    return OrderIntent(
        intent_id=f"r7-exit-{digest[:32]}",
        idempotency_key=f"r7-exit-idem-{digest[:40]}",
        strategy_id=MANUAL_EXIT_STRATEGY_ID,
        symbol=plan.symbol,
        side=Side.SELL,
        quantity=plan.quantity,
        order_type=OrderType.LIMIT,
        created_at=plan.prepared_at,
        limit_price=plan.limit_price,
    )


def prepare_paper_exit(
    *,
    plan: PaperCryptoClosePlan,
    attempt_id: str,
    intent: OrderIntent,
    decision: RiskDecision,
    market: MarketSnapshot,
    oms: OrderManagementSystem,
    lifecycle: SQLitePaperExitAttempt,
    now: datetime,
) -> PaperExitPreparationResult:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PaperExitCoordinatorBlocked("R7 exit preparation time must be timezone-aware")
    instant = now.astimezone(timezone.utc)
    if not isinstance(plan, PaperCryptoClosePlan):
        raise PaperExitCoordinatorBlocked("exact close plan is required")
    if not isinstance(intent, OrderIntent) or not isinstance(decision, RiskDecision) or not isinstance(market, MarketSnapshot):
        raise PaperExitCoordinatorBlocked("exact intent, RiskDecision and MarketSnapshot are required")
    if not isinstance(oms, OrderManagementSystem) or not isinstance(lifecycle, SQLitePaperExitAttempt):
        raise PaperExitCoordinatorBlocked("authoritative OMS and exit lifecycle are required")
    expected_intent = build_manual_exit_intent(plan=plan, attempt_id=attempt_id)
    if intent != expected_intent:
        raise PaperExitCoordinatorBlocked("R7 manual exit intent differs from deterministic close plan intent")
    if instant < plan.prepared_at.astimezone(timezone.utc) or instant >= plan.expires_at.astimezone(timezone.utc):
        raise PaperExitCoordinatorBlocked("R7 close plan is expired or not yet valid")
    if decision.status is not RiskDecisionStatus.APPROVED or decision.risk_reducing is not True:
        raise PaperExitCoordinatorBlocked("R7 exit requires APPROVED risk-reducing Capital Safety decision")
    if decision.intent_id != intent.intent_id or decision.intent_fingerprint != intent_fingerprint(intent):
        raise PaperExitCoordinatorBlocked("R7 exit RiskDecision is not bound to exact intent")
    if decision.market_fingerprint != market_fingerprint(market):
        raise PaperExitCoordinatorBlocked("R7 exit RiskDecision is not bound to exact market")
    if market.symbol != plan.symbol:
        raise PaperExitCoordinatorBlocked("R7 exit market symbol differs from close plan")
    if instant > decision.valid_until.astimezone(timezone.utc):
        raise PaperExitCoordinatorBlocked("R7 exit RiskDecision is expired")

    oms_order = oms.validate_for_external_submission(
        intent=intent,
        decision=decision,
        market=market,
        now=instant,
    )
    if oms_order.status is not OrderStatus.VALIDATED:
        raise PaperExitCoordinatorBlocked("R7 exit OMS did not remain VALIDATED")
    exit_order = build_paper_exit_order(plan=plan, attempt_id=attempt_id)
    lifecycle_snapshot = lifecycle.prepare(plan=plan, order=exit_order, at=instant)
    if lifecycle_snapshot.state.attempt_count != 0:
        raise PaperExitCoordinatorBlocked("prepared R7 exit already consumed POST authority")

    execution_deadline = min(plan.expires_at.astimezone(timezone.utc), decision.valid_until.astimezone(timezone.utc))
    if instant >= execution_deadline:
        raise PaperExitCoordinatorBlocked("R7 exit execution deadline is exhausted")
    values = {
        "attempt_id": attempt_id,
        "plan_hash": plan.plan_hash,
        "exit_order_hash": exit_order.order_hash,
        "broker_payload_hash": exit_order.payload_hash,
        "client_order_id": exit_order.client_order_id,
        "oms_order_id": oms_order.order_id,
        "intent_fingerprint": intent_fingerprint(intent),
        "risk_decision_id": decision.decision_id,
        "risk_decision_fingerprint": risk_decision_fingerprint(decision),
        "safety_state_version": decision.safety_state_version,
        "market_fingerprint": market_fingerprint(market),
        "prepared_at": instant,
        "execution_deadline": execution_deadline,
        "risk_reducing": True,
        "network_write_authorized": False,
        "retry_post": False,
        "live_trading": "BLOCKED",
    }
    package = PreparedPaperExitPackage(**values, package_hash=_hash(_package_payload_from_values(values)))
    return PaperExitPreparationResult(
        package=package,
        intent=intent,
        exit_order=exit_order,
        oms_order=oms_order,
        lifecycle=lifecycle_snapshot,
    )


def _package_payload(value: PreparedPaperExitPackage, *, include_hash: bool) -> dict[str, object]:
    payload = {
        "attempt_id": value.attempt_id,
        "plan_hash": value.plan_hash,
        "exit_order_hash": value.exit_order_hash,
        "broker_payload_hash": value.broker_payload_hash,
        "client_order_id": value.client_order_id,
        "oms_order_id": value.oms_order_id,
        "intent_fingerprint": value.intent_fingerprint,
        "risk_decision_id": value.risk_decision_id,
        "risk_decision_fingerprint": value.risk_decision_fingerprint,
        "safety_state_version": value.safety_state_version,
        "market_fingerprint": value.market_fingerprint,
        "prepared_at": value.prepared_at.astimezone(timezone.utc).isoformat(),
        "execution_deadline": value.execution_deadline.astimezone(timezone.utc).isoformat(),
        "risk_reducing": value.risk_reducing,
        "network_write_authorized": value.network_write_authorized,
        "retry_post": value.retry_post,
        "live_trading": value.live_trading,
    }
    if include_hash:
        payload["package_hash"] = value.package_hash
    return payload


def _package_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["prepared_at"] = values["prepared_at"].astimezone(timezone.utc).isoformat()  # type: ignore[union-attr]
    payload["execution_deadline"] = values["execution_deadline"].astimezone(timezone.utc).isoformat()  # type: ignore[union-attr]
    return payload


def _hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


__all__ = [
    "MANUAL_EXIT_STRATEGY_ID",
    "PaperExitCoordinatorBlocked",
    "PaperExitCoordinatorError",
    "PaperExitPreparationResult",
    "PreparedPaperExitPackage",
    "build_manual_exit_intent",
    "prepare_paper_exit",
]
