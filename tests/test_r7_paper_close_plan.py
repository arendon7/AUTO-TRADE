from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation
from autotrade.brokers.alpaca_paper_portfolio import PaperPortfolioPosition, PaperPortfolioSnapshot
from autotrade.paper_close_plan import PaperCloseMode, PaperClosePlanError, prepare_crypto_close_plan

NOW = datetime(2026, 8, 21, 14, 6, 12, tzinfo=timezone.utc)


def _portfolio() -> PaperPortfolioSnapshot:
    account = AlpacaPaperAccountAttestation(
        account_id="12345678-1234-1234-1234-123456789abc",
        account_reference="a" * 64,
        credential_reference="b" * 64,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("99989"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=False,
        attested_at=NOW,
        request_id="req-account-close",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )
    position = PaperPortfolioPosition(
        asset_id="btc-asset",
        broker_symbol="BTCUSD",
        symbol="BTC/USD",
        asset_class="crypto",
        exchange="CRYPTO",
        side="long",
        quantity=Decimal("0.000143959"),
        available_quantity=Decimal("0.000143959"),
        avg_entry_price=Decimal("72760.25"),
        current_price=Decimal("72800"),
        market_value=Decimal("10.48"),
        cost_basis=Decimal("10.47"),
        unrealized_pl=Decimal("0.01"),
        unrealized_plpc=Decimal("0.000955"),
    )
    return PaperPortfolioSnapshot(
        account=account,
        positions=(position,),
        open_orders=(),
        positions_request_id="req-pos-close",
        orders_request_id="req-orders-close",
        positions_response_sha256="c" * 64,
        orders_response_sha256="d" * 64,
        observed_at=NOW,
    )


def test_full_close_plan_binds_exact_available_broker_position() -> None:
    portfolio = _portfolio()
    plan = prepare_crypto_close_plan(
        portfolio=portfolio,
        symbol="BTC/USD",
        now=NOW + timedelta(seconds=1),
        limit_price=Decimal("72782"),
    )
    assert plan.mode is PaperCloseMode.FULL
    assert plan.quantity == Decimal("0.000143959")
    assert plan.broker_symbol == "BTCUSD"
    assert plan.side == "sell"
    assert plan.order_type == "limit"
    assert plan.time_in_force == "ioc"
    assert plan.risk_reducing is True
    assert plan.network_write_authorized is False
    assert plan.retry_post is False
    assert plan.live_trading == "BLOCKED"
    assert plan.portfolio_fingerprint == portfolio.fingerprint
    assert len(plan.plan_hash) == 64


def test_partial_close_plan_never_exceeds_available_quantity() -> None:
    plan = prepare_crypto_close_plan(
        portfolio=_portfolio(),
        symbol="BTC/USD",
        now=NOW,
        quantity=Decimal("0.00005"),
        limit_price=Decimal("72782"),
    )
    assert plan.mode is PaperCloseMode.PARTIAL
    assert plan.quantity == Decimal("0.00005")
    with pytest.raises(PaperClosePlanError, match="available"):
        prepare_crypto_close_plan(
            portfolio=_portfolio(),
            symbol="BTC/USD",
            now=NOW,
            quantity=Decimal("0.001"),
            limit_price=Decimal("72782"),
        )


def test_stale_or_wrong_position_fails_closed() -> None:
    with pytest.raises(PaperClosePlanError, match="stale"):
        prepare_crypto_close_plan(
            portfolio=_portfolio(),
            symbol="BTC/USD",
            now=NOW + timedelta(seconds=16),
            limit_price=Decimal("72782"),
        )
    with pytest.raises(PaperClosePlanError, match="exactly one"):
        prepare_crypto_close_plan(
            portfolio=_portfolio(),
            symbol="ETH/USD",
            now=NOW,
            limit_price=Decimal("72782"),
        )


def test_short_or_unavailable_position_cannot_use_long_close_contract() -> None:
    base = _portfolio()
    short = replace(base.positions[0], side="short", quantity=Decimal("-0.1"), available_quantity=Decimal("0.1"))
    portfolio = replace(base, positions=(short,))
    with pytest.raises(PaperClosePlanError, match="positive long"):
        prepare_crypto_close_plan(portfolio=portfolio, symbol="BTC/USD", now=NOW, limit_price=Decimal("72782"))
    unavailable = replace(base.positions[0], available_quantity=Decimal("0"))
    with pytest.raises(PaperClosePlanError, match="no available"):
        prepare_crypto_close_plan(
            portfolio=replace(base, positions=(unavailable,)),
            symbol="BTC/USD",
            now=NOW,
            limit_price=Decimal("72782"),
        )


def test_limit_price_and_slippage_are_hard_bounded() -> None:
    with pytest.raises(PaperClosePlanError, match="hard cap"):
        prepare_crypto_close_plan(
            portfolio=_portfolio(),
            symbol="BTC/USD",
            now=NOW,
            limit_price=Decimal("72700"),
            max_slippage_bps=Decimal("100"),
        )
    with pytest.raises(PaperClosePlanError, match="bounded"):
        prepare_crypto_close_plan(
            portfolio=_portfolio(),
            symbol="BTC/USD",
            now=NOW,
            limit_price=Decimal("72000"),
        )
