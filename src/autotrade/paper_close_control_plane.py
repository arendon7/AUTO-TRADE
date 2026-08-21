from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256

from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleSnapshot,
    CryptoLifecycleStatus,
)
from autotrade.brokers.paper_portfolio import PaperPortfolioSnapshot
from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
)
from autotrade.ledger import LedgerEvent
from autotrade.oms import (
    ExternalSubmissionHandoff,
    ExternalSubmissionHandoffConflict,
    OrderManagementSystem,
    _build_external_handoff,
)
from autotrade.paper_close_plan import PaperCryptoClosePlan
from autotrade.safety import CapitalSafetyKernel


class PaperCloseControlPlaneError(RuntimeError):
    pass


class PaperCloseControlPlaneBlocked(PaperCloseControlPlaneError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedPaperCloseControlPlane:
    attempt_id: str
    plan_hash: str
    source_entry_order_id: str
    source_entry_intent_fingerprint: str
    source_lifecycle_id: str
    strategy_id: str
    portfolio_fingerprint: str
    conservative_portfolio: PortfolioSnapshot
    intent: OrderIntent
    decision: RiskDecision
    order: OrderRecord
    prepared_at: datetime

    @property
    def fingerprint(self) -> str:
        return sha256(
            ":".join(
                (
                    self.attempt_id,
                    self.plan_hash,
                    self.source_entry_order_id,
                    self.source_entry_intent_fingerprint,
                    self.source_lifecycle_id,
                    self.strategy_id,
                    self.portfolio_fingerprint,
                    intent_fingerprint(self.intent),
                    self.decision.decision_id,
                    self.order.order_id,
                    self.prepared_at.astimezone(timezone.utc).isoformat(),
                )
            ).encode("utf-8")
        ).hexdigest()


def prepare_paper_close_control_plane(
    *,
    attempt_id: str,
    plan: PaperCryptoClosePlan,
    broker_portfolio: PaperPortfolioSnapshot,
    market: MarketSnapshot,
    source_entry_order: OrderRecord,
    source_lifecycle: CryptoLifecycleSnapshot,
    safety: CapitalSafetyKernel,
    oms: OrderManagementSystem,
    now: datetime,
) -> PreparedPaperCloseControlPlane:
    if not isinstance(plan, PaperCryptoClosePlan):
        raise PaperCloseControlPlaneBlocked("exact close plan is required")
    if not isinstance(broker_portfolio, PaperPortfolioSnapshot):
        raise PaperCloseControlPlaneBlocked("exact broker Portfolio truth is required")
    if not isinstance(source_entry_order, OrderRecord):
        raise PaperCloseControlPlaneBlocked("durable source OMS entry order is required")
    if not isinstance(source_lifecycle, CryptoLifecycleSnapshot):
        raise PaperCloseControlPlaneBlocked("durable source crypto lifecycle is required")
    if not isinstance(safety, CapitalSafetyKernel):
        raise PaperCloseControlPlaneBlocked("real CapitalSafetyKernel is required")
    if not isinstance(oms, OrderManagementSystem):
        raise PaperCloseControlPlaneBlocked("real OMS is required")
    if not isinstance(market, MarketSnapshot):
        raise PaperCloseControlPlaneBlocked("fresh MarketSnapshot is required")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ValueError("attempt_id is required")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    instant = now.astimezone(timezone.utc)
    if instant < plan.prepared_at.astimezone(timezone.utc) or instant >= plan.expires_at.astimezone(timezone.utc):
        raise PaperCloseControlPlaneBlocked("close plan is not fresh")
    if broker_portfolio.fingerprint != plan.portfolio_fingerprint:
        raise PaperCloseControlPlaneBlocked("broker Portfolio differs from close plan truth")
    if market.symbol != plan.symbol:
        raise PaperCloseControlPlaneBlocked("market symbol differs from close plan")
    if len(broker_portfolio.positions) != 1 or broker_portfolio.open_orders:
        raise PaperCloseControlPlaneBlocked(
            "first R7 close requires exactly one broker position and zero open orders for provable attribution"
        )
    position = broker_portfolio.positions[0]
    if position.symbol != plan.symbol or position.asset_class != "crypto" or position.side != "long" or position.quantity <= 0:
        raise PaperCloseControlPlaneBlocked("broker target is not the exact positive long crypto position")
    if position.quantity != plan.observed_position_quantity:
        raise PaperCloseControlPlaneBlocked("broker position quantity drifted after close plan")

    entry_intent = source_entry_order.intent
    if entry_intent.symbol != plan.symbol or entry_intent.side is not Side.BUY:
        raise PaperCloseControlPlaneBlocked("source OMS order is not the matching long entry")
    if not entry_intent.strategy_id.strip():
        raise PaperCloseControlPlaneBlocked("source entry has no strategy attribution")
    binding = source_lifecycle.binding
    state = source_lifecycle.state
    if state.status is not CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED:
        raise PaperCloseControlPlaneBlocked("first R7 close requires reconciled unprotected entry exposure")
    if binding.symbol != plan.symbol or binding.entry_quantity != entry_intent.quantity:
        raise PaperCloseControlPlaneBlocked("source lifecycle does not match OMS entry intent")
    expected_lifecycle_id = _source_lifecycle_id(
        order_id=source_entry_order.order_id,
        account_fingerprint=binding.account_attestation_fingerprint,
        asset_fingerprint=binding.asset_attestation_fingerprint,
        product_profile_fingerprint=binding.product_profile_fingerprint,
    )
    if expected_lifecycle_id != binding.lifecycle_id:
        raise PaperCloseControlPlaneBlocked("source lifecycle is not derived from supplied OMS order")
    if state.confirmed_net_long_quantity != position.quantity:
        raise PaperCloseControlPlaneBlocked("source lifecycle exposure differs from broker position truth")
    if state.entry_attempt_count != 1 or not state.entry_terminal or state.entry_filled_quantity <= 0:
        raise PaperCloseControlPlaneBlocked("source entry is not a single terminal reconciled broker attempt")

    current_notional = position.quantity * market.last
    if current_notional <= 0 or not current_notional.is_finite():
        raise PaperCloseControlPlaneBlocked("broker-grounded current notional is invalid")
    strategy_id = entry_intent.strategy_id
    projection = PortfolioSnapshot(
        snapshot_id=f"r7-close-conservative:{broker_portfolio.fingerprint[:24]}",
        equity=broker_portfolio.account.portfolio_value,
        gross_exposure=current_notional,
        net_exposure=current_notional,
        # These two fields are deliberately worst-case because Alpaca position
        # truth does not establish canonical realized daily P&L/peak history.
        # Risk-reducing decisions are permitted through these limits; new risk
        # can never use this projection.
        daily_pnl=-broker_portfolio.account.portfolio_value,
        drawdown=Decimal("1"),
        open_orders=0,
        signed_position_notional_by_symbol={plan.symbol: current_notional},
        strategy_gross_exposure={strategy_id: current_notional},
        strategy_signed_position_notional_by_symbol={strategy_id: {plan.symbol: current_notional}},
        reconciliation_ok=True,
        broker_state_known=True,
    )
    digest = sha256(f"{attempt_id}:{plan.plan_hash}".encode("utf-8")).hexdigest()
    intent = OrderIntent(
        intent_id=f"r7-close-{digest[:32]}",
        idempotency_key=f"r7-close-idem-{digest[:32]}",
        strategy_id=strategy_id,
        symbol=plan.symbol,
        side=Side.SELL,
        quantity=plan.quantity,
        order_type=OrderType.LIMIT,
        limit_price=plan.limit_price,
        created_at=instant,
    )
    decision = safety.evaluate(intent=intent, market=market, portfolio=projection, now=instant)
    if decision.status is not RiskDecisionStatus.APPROVED or decision.risk_reducing is not True:
        raise PaperCloseControlPlaneBlocked(
            f"Capital Safety did not approve strict risk reduction: {decision.reason_code}"
        )
    order = oms.validate_for_external_submission(
        intent=intent,
        decision=decision,
        market=market,
        now=instant,
    )
    if order.status is not OrderStatus.VALIDATED:
        raise PaperCloseControlPlaneBlocked("OMS did not leave close order VALIDATED")
    return PreparedPaperCloseControlPlane(
        attempt_id=attempt_id,
        plan_hash=plan.plan_hash,
        source_entry_order_id=source_entry_order.order_id,
        source_entry_intent_fingerprint=intent_fingerprint(entry_intent),
        source_lifecycle_id=binding.lifecycle_id,
        strategy_id=strategy_id,
        portfolio_fingerprint=broker_portfolio.fingerprint,
        conservative_portfolio=projection,
        intent=intent,
        decision=decision,
        order=order,
        prepared_at=instant,
    )


class R7RiskReducingOrderManagementSystem(OrderManagementSystem):
    """OMS stage dedicated to already-approved strict risk reduction.

    Unlike the normal new-risk external stage, an active kill/circuit does not
    prevent liquidation. The same Safety-state version is still mandatory and
    `_validate_control_plane` re-verifies that the exact RiskDecision is valid.
    """

    def stage_risk_reducing_external_submission(
        self,
        *,
        prepared: PreparedPaperCloseControlPlane,
        market: MarketSnapshot,
        now: datetime,
    ) -> tuple[OrderRecord, ExternalSubmissionHandoff]:
        if not isinstance(prepared, PreparedPaperCloseControlPlane):
            raise ExternalSubmissionHandoffConflict("R7 prepared close control plane is required")
        if prepared.decision.risk_reducing is not True or prepared.decision.status is not RiskDecisionStatus.APPROVED:
            raise ExternalSubmissionHandoffConflict("R7 close handoff requires approved risk-reducing decision")
        current = self._orders.get_by_order_id(prepared.order.order_id)
        if current is None or current.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTING}:
            raise ExternalSubmissionHandoffConflict("R7 close OMS order is not resumable")
        fingerprint = intent_fingerprint(current.intent)
        self._validate_control_plane(
            intent=current.intent,
            decision=prepared.decision,
            market=market,
            now=now,
            fingerprint=fingerprint,
        )
        if self._safety_state_store is None:
            raise ExternalSubmissionHandoffConflict("R7 close handoff requires authoritative Safety state")
        safety_state = self._safety_state_store.get()
        if safety_state.version != prepared.decision.safety_state_version:
            raise ExternalSubmissionHandoffConflict("Safety state version changed before R7 close handoff")
        handoff_id = sha256(
            f"R7:CLOSE:{prepared.fingerprint}:{prepared.order.order_id}".encode("utf-8")
        ).hexdigest()
        handoff = _build_external_handoff(
            handoff_id=handoff_id,
            order_id=current.order_id,
            intent_fingerprint_value=fingerprint,
            risk_decision_id=prepared.decision.decision_id,
            safety_state_version=prepared.decision.safety_state_version,
            market_fingerprint_value=prepared.decision.market_fingerprint,
            decision_valid_until=prepared.decision.valid_until,
            authorized_at=now,
        )
        existing = tuple(e for e in self._ledger.all_events() if e.event_id == handoff.event_id)
        if existing:
            if len(existing) != 1 or existing[0].payload != handoff.to_event_payload():
                raise ExternalSubmissionHandoffConflict("R7 close durable handoff conflict")
        else:
            self._append_idempotent(
                LedgerEvent(
                    event_id=handoff.event_id,
                    event_type="RISK_REDUCING_EXTERNAL_ORDER_HANDOFF_AUTHORIZED",
                    occurred_at=now,
                    payload=handoff.to_event_payload(),
                )
            )
        if current.status is OrderStatus.VALIDATED:
            staged = replace(current, status=OrderStatus.SUBMITTING, submitted_at=now)
            self._orders.update(staged)
        else:
            if current.submitted_at != now:
                raise ExternalSubmissionHandoffConflict("R7 close SUBMITTING timestamp differs from handoff")
            staged = current
        return staged, handoff


def _source_lifecycle_id(
    *, order_id: str, account_fingerprint: str, asset_fingerprint: str, product_profile_fingerprint: str
) -> str:
    raw = ":".join((order_id, account_fingerprint, asset_fingerprint, product_profile_fingerprint))
    return "r6c-entry-" + sha256(raw.encode("utf-8")).hexdigest()[:40]


__all__ = [
    "PaperCloseControlPlaneBlocked",
    "PaperCloseControlPlaneError",
    "PreparedPaperCloseControlPlane",
    "R7RiskReducingOrderManagementSystem",
    "prepare_paper_close_control_plane",
]
