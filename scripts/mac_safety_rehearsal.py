from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json

from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderType,
    PortfolioSnapshot,
    Side,
    risk_decision_fingerprint,
)
from autotrade.ledger import InMemoryEventLedger
from autotrade.safety import CapitalSafetyKernel, SafetyLimits


REHEARSAL_LIMITS = SafetyLimits(
    limits_version="MAC_SAFETY_REHEARSAL_V1",
    allowed_symbols=frozenset({"AAPL", "MSFT", "SPY"}),
    allowed_order_types=frozenset({OrderType.LIMIT}),
    max_order_notional=Decimal("100"),
    max_position_notional=Decimal("250"),
    max_strategy_gross_exposure=Decimal("500"),
    max_portfolio_gross_exposure=Decimal("1000"),
    max_net_exposure=Decimal("1000"),
    max_leverage=Decimal("1"),
    max_daily_loss=Decimal("500"),
    max_drawdown=Decimal("1000"),
    max_open_orders=5,
    stale_market_data_ms=3000,
    price_deviation_bps=Decimal("100"),
    decision_ttl_ms=5000,
)


def _decimal(value: str, label: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"{label} must be a decimal") from exc
    if not parsed.is_finite():
        raise argparse.ArgumentTypeError(f"{label} must be finite")
    return parsed


def _positive_decimal(value: str, label: str) -> Decimal:
    parsed = _decimal(value, label)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"{label} must be > 0")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Local-only AUTO-TRADE candidate -> Capital Safety rehearsal. "
            "No broker, writer, OMS staging or external execution authority exists here."
        )
    )
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--side", choices=[side.value for side in Side], default="BUY")
    parser.add_argument("--quantity", default="0.25")
    parser.add_argument("--order-type", choices=[kind.value for kind in OrderType], default="LIMIT")
    parser.add_argument("--limit-price", default="100")
    parser.add_argument("--bid", default="99.99")
    parser.add_argument("--ask", default="100.01")
    parser.add_argument("--last", default="100")
    parser.add_argument("--market-age-ms", type=int, default=250)
    parser.add_argument("--equity", default="10000")
    parser.add_argument("--gross-exposure", default="0")
    parser.add_argument("--net-exposure", default="0")
    parser.add_argument("--daily-pnl", default="0")
    parser.add_argument("--drawdown", default="0")
    parser.add_argument("--open-orders", type=int, default=0)
    parser.add_argument("--current-position-notional", default="0")
    parser.add_argument("--strategy-gross-exposure", default="0")
    parser.add_argument("--strategy-position-notional", default="0")
    parser.add_argument("--reconciliation-failed", action="store_true")
    parser.add_argument("--broker-state-unknown", action="store_true")
    parser.add_argument("--kill-switch", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.market_age_ms < 0:
        raise SystemExit("ERROR: --market-age-ms cannot be negative")
    if args.open_orders < 0:
        raise SystemExit("ERROR: --open-orders cannot be negative")

    now = datetime.now(timezone.utc)
    symbol = args.symbol.strip().upper()
    strategy_id = "mac-safety-rehearsal"
    quantity = _positive_decimal(args.quantity, "quantity")
    order_type = OrderType(args.order_type)
    limit_price = (
        _positive_decimal(args.limit_price, "limit-price")
        if order_type is OrderType.LIMIT
        else None
    )
    market = MarketSnapshot(
        symbol=symbol,
        bid=_positive_decimal(args.bid, "bid"),
        ask=_positive_decimal(args.ask, "ask"),
        last=_positive_decimal(args.last, "last"),
        observed_at=now - timedelta(milliseconds=args.market_age_ms),
    )
    intent = OrderIntent(
        intent_id="mac-safety-intent-001",
        idempotency_key="mac-safety-idempotency-001",
        strategy_id=strategy_id,
        symbol=symbol,
        side=Side(args.side),
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        created_at=now,
    )

    current_position = _decimal(args.current_position_notional, "current-position-notional")
    strategy_position = _decimal(args.strategy_position_notional, "strategy-position-notional")
    strategy_gross = _decimal(args.strategy_gross_exposure, "strategy-gross-exposure")
    portfolio = PortfolioSnapshot(
        snapshot_id="mac-safety-portfolio-001",
        equity=_positive_decimal(args.equity, "equity"),
        gross_exposure=_decimal(args.gross_exposure, "gross-exposure"),
        net_exposure=_decimal(args.net_exposure, "net-exposure"),
        daily_pnl=_decimal(args.daily_pnl, "daily-pnl"),
        drawdown=_decimal(args.drawdown, "drawdown"),
        open_orders=args.open_orders,
        signed_position_notional_by_symbol=(
            {symbol: current_position} if current_position != 0 else {}
        ),
        strategy_gross_exposure=(
            {strategy_id: strategy_gross} if strategy_gross != 0 else {}
        ),
        strategy_signed_position_notional_by_symbol=(
            {strategy_id: {symbol: strategy_position}} if strategy_position != 0 else {}
        ),
        reconciliation_ok=not args.reconciliation_failed,
        broker_state_known=not args.broker_state_unknown,
    )

    ledger = InMemoryEventLedger()
    safety = CapitalSafetyKernel(REHEARSAL_LIMITS, ledger)
    if args.kill_switch:
        safety.activate_kill_switch(reason="MAC_REHEARSAL_REQUESTED", now=now)

    decision = safety.evaluate(
        intent=intent,
        market=market,
        portfolio=portfolio,
        now=now,
    )
    report = {
        "mode": "LOCAL_SAFETY_REHEARSAL_ONLY",
        "candidate": {
            "strategy_id": intent.strategy_id,
            "symbol": intent.symbol,
            "side": intent.side.value,
            "quantity": str(intent.quantity),
            "order_type": intent.order_type.value,
            "limit_price": str(intent.limit_price) if intent.limit_price is not None else None,
        },
        "market": {
            "bid": str(market.bid),
            "ask": str(market.ask),
            "last": str(market.last),
            "age_ms": args.market_age_ms,
        },
        "portfolio": {
            "equity": str(portfolio.equity),
            "gross_exposure": str(portfolio.gross_exposure),
            "net_exposure": str(portfolio.net_exposure),
            "daily_pnl": str(portfolio.daily_pnl),
            "drawdown": str(portfolio.drawdown),
            "open_orders": portfolio.open_orders,
            "reconciliation_ok": portfolio.reconciliation_ok,
            "broker_state_known": portfolio.broker_state_known,
        },
        "safety_limits": {
            "limits_version": REHEARSAL_LIMITS.limits_version,
            "allowed_symbols": sorted(REHEARSAL_LIMITS.allowed_symbols),
            "allowed_order_types": sorted(kind.value for kind in REHEARSAL_LIMITS.allowed_order_types),
            "max_order_notional": str(REHEARSAL_LIMITS.max_order_notional),
            "max_position_notional": str(REHEARSAL_LIMITS.max_position_notional),
            "max_strategy_gross_exposure": str(REHEARSAL_LIMITS.max_strategy_gross_exposure),
            "max_portfolio_gross_exposure": str(REHEARSAL_LIMITS.max_portfolio_gross_exposure),
            "max_net_exposure": str(REHEARSAL_LIMITS.max_net_exposure),
            "max_leverage": str(REHEARSAL_LIMITS.max_leverage),
            "max_open_orders": REHEARSAL_LIMITS.max_open_orders,
            "stale_market_data_ms": REHEARSAL_LIMITS.stale_market_data_ms,
            "price_deviation_bps": str(REHEARSAL_LIMITS.price_deviation_bps),
        },
        "risk_decision": {
            "status": decision.status.value,
            "reason_code": decision.reason_code,
            "reason_detail": decision.reason_detail,
            "approved_notional": (
                str(decision.approved_notional)
                if decision.approved_notional is not None
                else None
            ),
            "risk_reducing": decision.risk_reducing,
            "limits_version": decision.limits_version,
            "decision_fingerprint": risk_decision_fingerprint(decision),
        },
        "risk_decision_created_by": "CapitalSafetyKernel.evaluate",
        "ledger_event_count": len(ledger.all_events()),
        "broker_network_used": False,
        "broker_write_performed": False,
        "oms_staging_performed": False,
        "operator_authority_created": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "profitability_claim": False,
        "strategy_promotion_claim": False,
        "live_trading_status": "BLOCKED",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
