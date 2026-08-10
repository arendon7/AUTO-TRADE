from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from .domain import (
    MarketSnapshot,
    OrderIntent,
    OrderType,
    PortfolioSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    intent_fingerprint,
    market_fingerprint,
)
from .ledger import EventLedger, LedgerEvent


class InvalidSafetyConfiguration(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SafetyLimits:
    limits_version: str
    allowed_symbols: frozenset[str]
    allowed_order_types: frozenset[OrderType]
    max_order_notional: Decimal
    max_position_notional: Decimal
    max_strategy_gross_exposure: Decimal
    max_portfolio_gross_exposure: Decimal
    max_net_exposure: Decimal
    max_leverage: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    max_open_orders: int
    stale_market_data_ms: int
    price_deviation_bps: Decimal
    decision_ttl_ms: int

    def __post_init__(self) -> None:
        positive_decimals = {
            "max_order_notional": self.max_order_notional,
            "max_position_notional": self.max_position_notional,
            "max_strategy_gross_exposure": self.max_strategy_gross_exposure,
            "max_portfolio_gross_exposure": self.max_portfolio_gross_exposure,
            "max_net_exposure": self.max_net_exposure,
            "max_leverage": self.max_leverage,
            "max_daily_loss": self.max_daily_loss,
            "max_drawdown": self.max_drawdown,
        }
        if not self.limits_version.strip():
            raise InvalidSafetyConfiguration("limits_version is required")
        if not self.allowed_symbols:
            raise InvalidSafetyConfiguration("allowed_symbols cannot be empty")
        if not self.allowed_order_types:
            raise InvalidSafetyConfiguration("allowed_order_types cannot be empty")
        for name, value in positive_decimals.items():
            if not value.is_finite() or value <= 0:
                raise InvalidSafetyConfiguration(f"{name} must be finite and > 0")
        if not self.price_deviation_bps.is_finite() or self.price_deviation_bps < 0:
            raise InvalidSafetyConfiguration("price_deviation_bps must be finite and >= 0")
        if self.max_open_orders <= 0:
            raise InvalidSafetyConfiguration("max_open_orders must be > 0")
        if self.stale_market_data_ms <= 0:
            raise InvalidSafetyConfiguration("stale_market_data_ms must be > 0")
        if self.decision_ttl_ms <= 0:
            raise InvalidSafetyConfiguration("decision_ttl_ms must be > 0")


class CapitalSafetyKernel:
    def __init__(self, limits: SafetyLimits, ledger: EventLedger) -> None:
        self._limits = limits
        self._ledger = ledger
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        self._lock = RLock()

    @property
    def limits(self) -> SafetyLimits:
        return self._limits

    @property
    def kill_switch_active(self) -> bool:
        with self._lock:
            return self._kill_switch_active

    def activate_kill_switch(self, *, reason: str, now: datetime) -> None:
        if not reason.strip():
            raise ValueError("kill switch reason is required")
        with self._lock:
            self._kill_switch_active = True
            self._kill_switch_reason = reason
            self._ledger.append(
                LedgerEvent(
                    event_id=f"kill:{uuid4()}",
                    event_type="KILL_SWITCH_ACTIVATED",
                    occurred_at=now,
                    payload={"reason": reason},
                )
            )

    def reset_kill_switch(self, *, confirmed_by: str, now: datetime) -> None:
        if not confirmed_by.strip():
            raise ValueError("confirmed_by is required")
        with self._lock:
            self._kill_switch_active = False
            self._kill_switch_reason = ""
            self._ledger.append(
                LedgerEvent(
                    event_id=f"kill-reset:{uuid4()}",
                    event_type="KILL_SWITCH_RESET",
                    occurred_at=now,
                    payload={"confirmed_by": confirmed_by},
                )
            )

    def evaluate(
        self,
        *,
        intent: OrderIntent,
        market: MarketSnapshot,
        portfolio: PortfolioSnapshot,
        now: datetime,
    ) -> RiskDecision:
        with self._lock:
            return self._evaluate_locked(intent=intent, market=market, portfolio=portfolio, now=now)

    def _evaluate_locked(
        self,
        *,
        intent: OrderIntent,
        market: MarketSnapshot,
        portfolio: PortfolioSnapshot,
        now: datetime,
    ) -> RiskDecision:
        fp = intent_fingerprint(intent)
        market_fp = market_fingerprint(market)

        def reject(code: str, detail: str, *, risk_reducing: bool = False) -> RiskDecision:
            return self._record_decision(
                intent=intent,
                market_fp=market_fp,
                fingerprint=fp,
                now=now,
                status=RiskDecisionStatus.REJECTED,
                reason_code=code,
                reason_detail=detail,
                approved_notional=None,
                risk_reducing=risk_reducing,
            )

        if not _aware(now) or not _aware(intent.created_at) or not _aware(market.observed_at):
            return reject("NAIVE_TIMESTAMP", "all timestamps must be timezone-aware")
        if not intent.intent_id.strip() or not intent.idempotency_key.strip() or not intent.strategy_id.strip():
            return reject("INVALID_INTENT_IDENTITY", "intent, idempotency and strategy identifiers are required")
        if intent.symbol not in self._limits.allowed_symbols:
            return reject("SYMBOL_NOT_ALLOWED", intent.symbol)
        if intent.order_type not in self._limits.allowed_order_types:
            return reject("ORDER_TYPE_NOT_ALLOWED", intent.order_type.value)
        if market.symbol != intent.symbol:
            return reject("MARKET_SYMBOL_MISMATCH", f"{market.symbol} != {intent.symbol}")
        if not _finite_positive(intent.quantity):
            return reject("INVALID_QUANTITY", str(intent.quantity))
        if not all(_finite_positive(value) for value in (market.bid, market.ask, market.last)):
            return reject("INVALID_MARKET_PRICE", "bid/ask/last must be finite and > 0")
        if market.bid > market.ask:
            return reject("INVALID_MARKET_BOOK", "bid cannot exceed ask")

        age_ms = Decimal(str((now - market.observed_at).total_seconds() * 1000))
        if age_ms < 0:
            return reject("MARKET_FROM_FUTURE", str(age_ms))
        if age_ms > self._limits.stale_market_data_ms:
            return reject("STALE_MARKET_DATA", str(age_ms))

        if intent.order_type is OrderType.LIMIT:
            if intent.limit_price is None or not _finite_positive(intent.limit_price):
                return reject("INVALID_LIMIT_PRICE", str(intent.limit_price))
            execution_price = intent.limit_price
            mid = (market.bid + market.ask) / Decimal("2")
            deviation_bps = abs(intent.limit_price - mid) / mid * Decimal("10000")
            if deviation_bps > self._limits.price_deviation_bps:
                return reject("PRICE_SANITY_BAND", str(deviation_bps))
        else:
            if intent.limit_price is not None:
                return reject("UNEXPECTED_LIMIT_PRICE", str(intent.limit_price))
            execution_price = market.ask if intent.side.value == "BUY" else market.bid

        order_notional = intent.quantity * execution_price
        if not _finite_positive(order_notional):
            return reject("INVALID_ORDER_NOTIONAL", str(order_notional))
        if order_notional > self._limits.max_order_notional:
            return reject("MAX_ORDER_NOTIONAL", str(order_notional))

        portfolio_error = _validate_portfolio(portfolio)
        if portfolio_error:
            return reject("INVALID_PORTFOLIO_SNAPSHOT", portfolio_error)
        if not portfolio.reconciliation_ok:
            return reject("RECONCILIATION_MISMATCH", portfolio.snapshot_id)
        if not portfolio.broker_state_known:
            return reject("BROKER_STATE_UNKNOWN", portfolio.snapshot_id)
        if portfolio.open_orders >= self._limits.max_open_orders:
            return reject("MAX_OPEN_ORDERS", str(portfolio.open_orders))

        current_position = portfolio.signed_position_notional_by_symbol.get(intent.symbol, Decimal("0"))
        strategy_positions = portfolio.strategy_signed_position_notional_by_symbol.get(intent.strategy_id, {})
        current_strategy_position = strategy_positions.get(intent.symbol, Decimal("0"))
        current_strategy_gross = portfolio.strategy_gross_exposure.get(intent.strategy_id, Decimal("0"))

        for name, value in (
            ("current_position", current_position),
            ("current_strategy_position", current_strategy_position),
            ("current_strategy_gross", current_strategy_gross),
        ):
            if not _finite(value):
                return reject("INVALID_PORTFOLIO_SNAPSHOT", f"{name} is not finite")
        if portfolio.gross_exposure < abs(current_position):
            return reject("INVALID_PORTFOLIO_SNAPSHOT", "gross exposure below symbol absolute exposure")
        if current_strategy_gross < abs(current_strategy_position):
            return reject("INVALID_PORTFOLIO_SNAPSHOT", "strategy gross exposure below strategy symbol exposure")

        signed_order_notional = intent.side.sign * order_notional
        projected_position = current_position + signed_order_notional
        projected_strategy_position = current_strategy_position + signed_order_notional

        aggregate_reducing = _strictly_reduces_without_flip(current_position, projected_position)
        strategy_reducing = _strictly_reduces_without_flip(current_strategy_position, projected_strategy_position)
        risk_reducing = aggregate_reducing and strategy_reducing

        projected_strategy_gross = current_strategy_gross - abs(current_strategy_position) + abs(projected_strategy_position)
        projected_gross = portfolio.gross_exposure - abs(current_position) + abs(projected_position)
        projected_net = portfolio.net_exposure + signed_order_notional

        if self._kill_switch_active and not risk_reducing:
            return reject("KILL_SWITCH_ACTIVE", self._kill_switch_reason, risk_reducing=risk_reducing)
        if portfolio.daily_pnl <= -self._limits.max_daily_loss and not risk_reducing:
            return reject("MAX_DAILY_LOSS", str(portfolio.daily_pnl), risk_reducing=risk_reducing)
        if portfolio.drawdown >= self._limits.max_drawdown and not risk_reducing:
            return reject("MAX_DRAWDOWN", str(portfolio.drawdown), risk_reducing=risk_reducing)
        if abs(projected_position) > self._limits.max_position_notional and not risk_reducing:
            return reject("MAX_POSITION_NOTIONAL", str(abs(projected_position)), risk_reducing=risk_reducing)
        if projected_strategy_gross > self._limits.max_strategy_gross_exposure and not risk_reducing:
            return reject("MAX_STRATEGY_GROSS", str(projected_strategy_gross), risk_reducing=risk_reducing)
        if projected_gross > self._limits.max_portfolio_gross_exposure and not risk_reducing:
            return reject("MAX_PORTFOLIO_GROSS", str(projected_gross), risk_reducing=risk_reducing)
        if abs(projected_net) > self._limits.max_net_exposure and not risk_reducing:
            return reject("MAX_NET_EXPOSURE", str(abs(projected_net)), risk_reducing=risk_reducing)
        projected_leverage = projected_gross / portfolio.equity
        if projected_leverage > self._limits.max_leverage and not risk_reducing:
            return reject("MAX_LEVERAGE", str(projected_leverage), risk_reducing=risk_reducing)

        return self._record_decision(
            intent=intent,
            market_fp=market_fp,
            fingerprint=fp,
            now=now,
            status=RiskDecisionStatus.APPROVED,
            reason_code="APPROVED",
            reason_detail="all hard limits passed",
            approved_notional=order_notional,
            risk_reducing=risk_reducing,
        )

    def _record_decision(
        self,
        *,
        intent: OrderIntent,
        market_fp: str,
        fingerprint: str,
        now: datetime,
        status: RiskDecisionStatus,
        reason_code: str,
        reason_detail: str,
        approved_notional: Decimal | None,
        risk_reducing: bool,
    ) -> RiskDecision:
        decision_id = str(uuid4())
        decision = RiskDecision(
            decision_id=decision_id,
            intent_id=intent.intent_id,
            status=status,
            reason_code=reason_code,
            reason_detail=reason_detail,
            evaluated_at=now,
            valid_until=now + timedelta(milliseconds=self._limits.decision_ttl_ms),
            limits_version=self._limits.limits_version,
            intent_fingerprint=fingerprint,
            market_fingerprint=market_fp,
            approved_notional=approved_notional,
            risk_reducing=risk_reducing,
        )
        self._ledger.append(
            LedgerEvent(
                event_id=f"risk:{decision_id}",
                event_type="RISK_DECISION",
                occurred_at=now,
                payload={
                    "decision_id": decision_id,
                    "intent_id": intent.intent_id,
                    "status": status.value,
                    "reason_code": reason_code,
                    "risk_reducing": str(risk_reducing).lower(),
                    "limits_version": self._limits.limits_version,
                },
            )
        )
        return decision


def _finite(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def _finite_positive(value: Decimal) -> bool:
    return _finite(value) and value > 0


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _strictly_reduces_without_flip(current: Decimal, projected: Decimal) -> bool:
    if current == 0:
        return False
    if projected == 0:
        return True
    if current * projected <= 0:
        return False
    return abs(projected) < abs(current)


def _validate_portfolio(portfolio: PortfolioSnapshot) -> str | None:
    numeric = {
        "equity": portfolio.equity,
        "gross_exposure": portfolio.gross_exposure,
        "net_exposure": portfolio.net_exposure,
        "daily_pnl": portfolio.daily_pnl,
        "drawdown": portfolio.drawdown,
    }
    for name, value in numeric.items():
        if not _finite(value):
            return f"{name} is not finite"
    if portfolio.equity <= 0:
        return "equity must be > 0"
    if portfolio.gross_exposure < 0:
        return "gross_exposure cannot be negative"
    if portfolio.drawdown < 0:
        return "drawdown cannot be negative"
    if portfolio.open_orders < 0:
        return "open_orders cannot be negative"
    return None
