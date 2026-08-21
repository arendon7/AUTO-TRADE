from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBinding,
    CryptoLifecycleSnapshot,
    CryptoLifecycleState,
    CryptoLifecycleStatus,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation
from autotrade.brokers.paper_portfolio import PaperPortfolioPosition, PaperPortfolioSnapshot
from autotrade.domain import MarketSnapshot, OrderIntent, OrderRecord, OrderStatus, OrderType, Side
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderRejectedByControlPlane
from autotrade.paper_close_control_plane import (
    PaperCloseControlPlaneBlocked,
    R7RiskReducingOrderManagementSystem,
    prepare_paper_close_control_plane,
)
from autotrade.paper_close_plan import prepare_crypto_close_plan
from autotrade.safety import CapitalSafetyKernel, SafetyLimits
from autotrade.state import InMemorySafetyStateStore

NOW = datetime(2026, 8, 21, 15, 55, tzinfo=timezone.utc)


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
        request_id="req-account-r7-control",
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
        avg_entry_price=Decimal("72760"),
        current_price=Decimal("72800"),
        market_value=Decimal("10.4802152"),
        cost_basis=Decimal("10.47"),
        unrealized_pl=Decimal("0.0102152"),
        unrealized_plpc=Decimal("0.000975"),
    )
    return PaperPortfolioSnapshot(
        account=account,
        positions=(position,),
        open_orders=(),
        positions_request_id="req-pos-r7-control",
        orders_request_id="req-orders-r7-control",
        positions_response_sha256="c" * 64,
        orders_response_sha256="d" * 64,
        observed_at=NOW,
    )


def _source_order() -> OrderRecord:
    intent = OrderIntent(
        intent_id="original-first-canary-intent",
        idempotency_key="original-first-canary-idem",
        strategy_id="r6-first-canary-connectivity",
        symbol="BTC/USD",
        side=Side.BUY,
        quantity=Decimal("0.00014432"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("72760"),
        created_at=NOW - timedelta(minutes=5),
    )
    return OrderRecord(
        order_id="original-oms-entry-order",
        intent=intent,
        risk_decision_id="original-risk-decision",
        status=OrderStatus.SUBMITTING,
        created_at=NOW - timedelta(minutes=5),
        submitted_at=NOW - timedelta(minutes=5),
    )


def _lifecycle(order: OrderRecord, *, quantity: Decimal = Decimal("0.000143959")) -> CryptoLifecycleSnapshot:
    account_fp = "1" * 64
    asset_fp = "2" * 64
    product_fp = "3" * 64
    raw = ":".join((order.order_id, account_fp, asset_fp, product_fp))
    lifecycle_id = "r6c-entry-" + sha256(raw.encode()).hexdigest()[:40]
    binding = CryptoLifecycleBinding(
        lifecycle_id=lifecycle_id,
        account_attestation_fingerprint=account_fp,
        asset_attestation_fingerprint=asset_fp,
        product_profile_fingerprint=product_fp,
        symbol="BTC/USD",
        entry_order_fingerprint="4" * 64,
        entry_client_order_id="atr6c-entry-source",
        entry_quantity=order.intent.quantity,
        created_at=NOW - timedelta(minutes=5),
    )
    state = CryptoLifecycleState(
        lifecycle_id=lifecycle_id,
        binding_hash=binding.fingerprint,
        status=CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED,
        event_sequence=2,
        event_head_hash="5" * 64,
        entry_attempt_count=1,
        entry_broker_order_id="broker-entry-source",
        entry_broker_status="filled",
        entry_filled_quantity=order.intent.quantity,
        entry_terminal=True,
        confirmed_net_long_quantity=quantity,
        protection_order_fingerprint=None,
        protection_client_order_id=None,
        protection_quantity=Decimal("0"),
        protection_attempt_count=0,
        protection_broker_order_id=None,
        protection_broker_status=None,
        protection_filled_quantity=Decimal("0"),
        updated_at=NOW,
        control_hash="6" * 64,
    )
    return CryptoLifecycleSnapshot(binding=binding, state=state, events=())


def _limits() -> SafetyLimits:
    return SafetyLimits(
        limits_version="r7-close-test",
        allowed_symbols=frozenset({"BTC/USD"}),
        allowed_order_types=frozenset({OrderType.LIMIT}),
        max_order_notional=Decimal("100"),
        max_position_notional=Decimal("100"),
        max_strategy_gross_exposure=Decimal("100"),
        max_portfolio_gross_exposure=Decimal("100"),
        max_net_exposure=Decimal("100"),
        max_leverage=Decimal("1"),
        max_daily_loss=Decimal("1"),
        max_drawdown=Decimal("0.01"),
        max_open_orders=5,
        stale_market_data_ms=5000,
        price_deviation_bps=Decimal("100"),
        decision_ttl_ms=20000,
    )


def _setup():
    broker = object()
    ledger = InMemoryEventLedger()
    state = InMemorySafetyStateStore()
    safety = CapitalSafetyKernel(_limits(), ledger, state_store=state)
    oms = R7RiskReducingOrderManagementSystem(
        broker=broker, ledger=ledger, safety_state_store=state
    )
    portfolio = _portfolio()
    plan = prepare_crypto_close_plan(
        portfolio=portfolio,
        symbol="BTC/USD",
        now=NOW,
        limit_price=Decimal("72780"),
    )
    market = MarketSnapshot(
        symbol="BTC/USD",
        bid=Decimal("72790"),
        ask=Decimal("72810"),
        last=Decimal("72800"),
        observed_at=NOW,
    )
    source = _source_order()
    lifecycle = _lifecycle(source)
    return ledger, state, safety, oms, portfolio, plan, market, source, lifecycle


def test_close_is_derived_from_source_strategy_and_real_safety_oms() -> None:
    _, _, safety, oms, portfolio, plan, market, source, lifecycle = _setup()
    prepared = prepare_paper_close_control_plane(
        attempt_id="r7-close-control-001",
        plan=plan,
        broker_portfolio=portfolio,
        market=market,
        source_entry_order=source,
        source_lifecycle=lifecycle,
        safety=safety,
        oms=oms,
        now=NOW,
    )
    assert prepared.strategy_id == source.intent.strategy_id
    assert prepared.intent.side is Side.SELL
    assert prepared.intent.quantity == plan.quantity
    assert prepared.decision.status.value == "APPROVED"
    assert prepared.decision.risk_reducing is True
    assert prepared.order.status is OrderStatus.VALIDATED
    assert prepared.conservative_portfolio.daily_pnl == -portfolio.account.portfolio_value
    assert prepared.conservative_portfolio.drawdown == Decimal("1")
    assert prepared.conservative_portfolio.signed_position_notional_by_symbol["BTC/USD"] > 0


def test_active_kill_switch_still_allows_only_strict_risk_reduction_stage() -> None:
    ledger, state, safety, oms, portfolio, plan, market, source, lifecycle = _setup()
    safety.activate_kill_switch(reason="cold-start remains closed to new risk", now=NOW - timedelta(seconds=1))
    prepared = prepare_paper_close_control_plane(
        attempt_id="r7-close-control-kill",
        plan=plan,
        broker_portfolio=portfolio,
        market=market,
        source_entry_order=source,
        source_lifecycle=lifecycle,
        safety=safety,
        oms=oms,
        now=NOW,
    )
    assert prepared.decision.risk_reducing is True
    staged, handoff = oms.stage_risk_reducing_external_submission(
        prepared=prepared, market=market, now=NOW + timedelta(milliseconds=1)
    )
    assert staged.status is OrderStatus.SUBMITTING
    assert len(handoff.handoff_hash) == 64
    events = [e for e in ledger.all_events() if e.event_type == "RISK_REDUCING_EXTERNAL_ORDER_HANDOFF_AUTHORIZED"]
    assert len(events) == 1


def test_safety_state_version_change_blocks_after_preparation() -> None:
    _, _, safety, oms, portfolio, plan, market, source, lifecycle = _setup()
    prepared = prepare_paper_close_control_plane(
        attempt_id="r7-close-control-version",
        plan=plan,
        broker_portfolio=portfolio,
        market=market,
        source_entry_order=source,
        source_lifecycle=lifecycle,
        safety=safety,
        oms=oms,
        now=NOW,
    )
    safety.activate_kill_switch(reason="changed after decision", now=NOW + timedelta(microseconds=100))
    with pytest.raises(OrderRejectedByControlPlane, match="safety state changed after risk approval"):
        oms.stage_risk_reducing_external_submission(
            prepared=prepared, market=market, now=NOW + timedelta(milliseconds=1)
        )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("lifecycle_qty", "lifecycle exposure"),
        ("lifecycle_status", "unprotected"),
        ("source_side", "matching long entry"),
        ("position_drift", "broker Portfolio differs from close plan truth"),
    ],
)
def test_attribution_mismatch_fails_before_safety_or_oms(mutation: str, match: str) -> None:
    _, _, safety, oms, portfolio, plan, market, source, lifecycle = _setup()
    if mutation == "lifecycle_qty":
        lifecycle = _lifecycle(source, quantity=Decimal("0.0001"))
    elif mutation == "lifecycle_status":
        lifecycle = CryptoLifecycleSnapshot(
            binding=lifecycle.binding,
            state=__import__("dataclasses").replace(lifecycle.state, status=CryptoLifecycleStatus.PROTECTED_OPEN),
            events=(),
        )
    elif mutation == "source_side":
        source = __import__("dataclasses").replace(
            source,
            intent=__import__("dataclasses").replace(source.intent, side=Side.SELL),
        )
    elif mutation == "position_drift":
        drifted = __import__("dataclasses").replace(portfolio.positions[0], quantity=Decimal("0.00014"))
        portfolio = __import__("dataclasses").replace(portfolio, positions=(drifted,))
    with pytest.raises(PaperCloseControlPlaneBlocked, match=match):
        prepare_paper_close_control_plane(
            attempt_id="r7-close-control-bad",
            plan=plan,
            broker_portfolio=portfolio,
            market=market,
            source_entry_order=source,
            source_lifecycle=lifecycle,
            safety=safety,
            oms=oms,
            now=NOW,
        )


def test_first_r7_close_rejects_changed_broker_truth_before_attribution() -> None:
    _, _, safety, oms, portfolio, plan, market, source, lifecycle = _setup()
    second = __import__("dataclasses").replace(
        portfolio.positions[0], asset_id="eth", broker_symbol="ETHUSD", symbol="ETH/USD"
    )
    bad = __import__("dataclasses").replace(portfolio, positions=portfolio.positions + (second,))
    with pytest.raises(PaperCloseControlPlaneBlocked, match="broker Portfolio differs from close plan truth"):
        prepare_paper_close_control_plane(
            attempt_id="r7-close-control-multi",
            plan=plan,
            broker_portfolio=bad,
            market=market,
            source_entry_order=source,
            source_lifecycle=lifecycle,
            safety=safety,
            oms=oms,
            now=NOW,
        )
