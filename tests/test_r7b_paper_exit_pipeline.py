from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_crypto_writer import AlpacaPaperCryptoWriteResponse
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.paper import PaperBroker
from autotrade.brokers.paper_exit_order_read import PaperExitOrderReadResponse
from autotrade.brokers.paper_portfolio import PaperPortfolioPosition, PaperPortfolioSnapshot
from autotrade.domain import MarketSnapshot, OrderType, PortfolioSnapshot
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.paper_close_plan import prepare_crypto_close_plan
from autotrade.paper_exit_attempt import (
    PaperExitAttemptBlocked,
    PaperExitStatus,
    SQLitePaperExitAttempt,
)
from autotrade.paper_exit_coordinator import build_exit_intent, prepare_paper_exit
from autotrade.paper_exit_final_guard import PaperExitFinalGuard, PaperExitFinalGuardBlocked
from autotrade.paper_exit_order import build_paper_exit_order
from autotrade.paper_exit_reconciliation import PaperExitReconciler
from autotrade.paper_exit_writer import (
    PaperExitWriter,
    PaperExitWriterAmbiguous,
    PaperExitWriterBlocked,
    PaperExitWriterConfig,
    PaperExitWriterDisabled,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.safety import CapitalSafetyKernel, SafetyLimits


NOW = datetime(2026, 8, 21, 15, 10, 0, tzinfo=timezone.utc)
ACCOUNT_ID = "12345678-1234-1234-1234-123456789abc"
OWNER = "R6_CRYPTO_FIRST_CANARY"
QTY = Decimal("0.000143959")
LIMIT = Decimal("72782")
POSITION_NOTIONAL = Decimal("10.480000")


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("paper-r7b-key", "paper-r7b-secret")


def _account(credentials: AlpacaPaperCredentials) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference="a" * 64,
        credential_reference=credentials.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("99989"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=False,
        attested_at=NOW,
        request_id="req-r7b-account",
        source_host=ALPACA_PAPER_TRADING_HOST,
        source_path="/v2/account",
    )


def _position(*, quantity: Decimal = QTY, available: Decimal | None = None) -> PaperPortfolioPosition:
    available = quantity if available is None else available
    return PaperPortfolioPosition(
        asset_id="btc-asset-r7b",
        broker_symbol="BTCUSD",
        symbol="BTC/USD",
        asset_class="crypto",
        exchange="CRYPTO",
        side="long",
        quantity=quantity,
        available_quantity=available,
        avg_entry_price=Decimal("72760.25"),
        current_price=Decimal("72800"),
        market_value=quantity * Decimal("72800"),
        cost_basis=quantity * Decimal("72760.25"),
        unrealized_pl=Decimal("0.01"),
        unrealized_plpc=Decimal("0.000955"),
    )


def _broker_portfolio(
    credentials: AlpacaPaperCredentials,
    *,
    at: datetime = NOW,
    quantity: Decimal | None = QTY,
    available: Decimal | None = None,
    open_orders=(),
) -> PaperPortfolioSnapshot:
    positions = () if quantity is None else (_position(quantity=quantity, available=available),)
    return PaperPortfolioSnapshot(
        account=replace(_account(credentials), attested_at=at),
        positions=positions,
        open_orders=tuple(open_orders),
        positions_request_id="req-r7b-positions",
        orders_request_id="req-r7b-orders",
        positions_response_sha256="b" * 64,
        orders_response_sha256="c" * 64,
        observed_at=at,
    )


def _market(*, at: datetime = NOW) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTC/USD",
        bid=Decimal("72790"),
        ask=Decimal("72800"),
        last=Decimal("72795"),
        observed_at=at,
    )


def _safety_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="r7b-owner-bound-portfolio",
        equity=Decimal("100000"),
        gross_exposure=POSITION_NOTIONAL,
        net_exposure=POSITION_NOTIONAL,
        daily_pnl=Decimal("0"),
        drawdown=Decimal("0"),
        open_orders=0,
        signed_position_notional_by_symbol={"BTC/USD": POSITION_NOTIONAL},
        strategy_gross_exposure={OWNER: POSITION_NOTIONAL},
        strategy_signed_position_notional_by_symbol={OWNER: {"BTC/USD": POSITION_NOTIONAL}},
        reconciliation_ok=True,
        broker_state_known=True,
    )


def _limits() -> SafetyLimits:
    return SafetyLimits(
        limits_version="r7b-test-v1",
        allowed_symbols=frozenset({"BTC/USD"}),
        allowed_order_types=frozenset({OrderType.LIMIT}),
        max_order_notional=Decimal("100"),
        max_position_notional=Decimal("100"),
        max_strategy_gross_exposure=Decimal("100"),
        max_portfolio_gross_exposure=Decimal("100"),
        max_net_exposure=Decimal("100"),
        max_leverage=Decimal("1"),
        max_daily_loss=Decimal("100"),
        max_drawdown=Decimal("0.20"),
        max_open_orders=10,
        stale_market_data_ms=1000,
        price_deviation_bps=Decimal("50"),
        decision_ttl_ms=1000,
    )


def _prepared(tmp_path):
    credentials = _credentials()
    broker_portfolio = _broker_portfolio(credentials)
    plan = prepare_crypto_close_plan(
        portfolio=broker_portfolio,
        symbol="BTC/USD",
        now=NOW,
        limit_price=LIMIT,
    )
    intent = build_exit_intent(
        plan=plan,
        attempt_id="r7b-exit-attempt-1",
        owner_strategy_id=OWNER,
    )
    ledger = InMemoryEventLedger()
    safety = CapitalSafetyKernel(_limits(), ledger)
    decision = safety.evaluate(
        intent=intent,
        market=_market(),
        portfolio=_safety_portfolio(),
        now=NOW,
    )
    assert decision.risk_reducing is True
    oms = OrderManagementSystem(broker=PaperBroker(), ledger=ledger)
    lifecycle = SQLitePaperExitAttempt(SQLiteRuntime(tmp_path / "r7b.sqlite3"))
    prepared = prepare_paper_exit(
        plan=plan,
        attempt_id="r7b-exit-attempt-1",
        owner_strategy_id=OWNER,
        intent=intent,
        decision=decision,
        market=_market(),
        oms=oms,
        lifecycle=lifecycle,
        now=NOW,
    )
    return credentials, broker_portfolio, plan, intent, decision, oms, lifecycle, prepared


class _CountingWriteTransport:
    def __init__(self, lifecycle: SQLitePaperExitAttempt, attempt_id: str, client_order_id: str) -> None:
        self.lifecycle = lifecycle
        self.attempt_id = attempt_id
        self.client_order_id = client_order_id
        self.calls = 0
        self.raise_error = False

    def post(self, *, host, path, headers, body, timeout_seconds, max_response_bytes):
        self.calls += 1
        snapshot = self.lifecycle.snapshot(self.attempt_id)
        assert snapshot.state.status is PaperExitStatus.SUBMISSION_UNKNOWN
        assert snapshot.state.attempt_count == 1
        assert host == ALPACA_PAPER_TRADING_HOST
        assert path == "/v2/orders"
        payload = json.loads(body)
        assert payload["client_order_id"] == self.client_order_id
        if self.raise_error:
            raise TimeoutError("simulated ambiguous network outcome")
        response = {
            "id": "2fa68a01-7408-48c5-b3c2-0b387987d2cc",
            "client_order_id": self.client_order_id,
            "status": "accepted",
        }
        return AlpacaPaperCryptoWriteResponse(
            status_code=201,
            body=json.dumps(response).encode(),
            headers={"content-type": "application/json", "x-request-id": "req-r7b-post"},
        )


class _StaticReadTransport:
    def __init__(self, response: PaperExitOrderReadResponse) -> None:
        self.response = response
        self.calls = 0

    def read(self, *, client_order_id, headers):
        self.calls += 1
        assert client_order_id
        assert headers["APCA-API-KEY-ID"] == _credentials().key_id
        return self.response


def _order_response(order, *, status="filled", filled: Decimal | None = None, http_status=200):
    if http_status == 404:
        return PaperExitOrderReadResponse(
            status_code=404,
            body=b'{"code":40410000,"message":"order not found"}',
            headers={"content-type": "application/json", "x-request-id": "req-r7b-get-404"},
        )
    filled = order.quantity if filled is None else filled
    body = {
        "id": "2fa68a01-7408-48c5-b3c2-0b387987d2cc",
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "asset_class": "crypto",
        "side": "sell",
        "type": "limit",
        "time_in_force": "ioc",
        "status": status,
        "qty": format(order.quantity, "f"),
        "filled_qty": format(filled, "f"),
        "limit_price": format(order.limit_price, "f"),
    }
    return PaperExitOrderReadResponse(
        status_code=200,
        body=json.dumps(body).encode(),
        headers={"content-type": "application/json", "x-request-id": "req-r7b-get"},
    )


def test_owner_bound_capital_safety_prepares_write_inert_exit(tmp_path) -> None:
    _, _, _, intent, decision, _, lifecycle, prepared = _prepared(tmp_path)
    assert intent.strategy_id == OWNER
    assert decision.risk_reducing is True
    assert prepared.package.owner_strategy_id == OWNER
    assert prepared.package.network_write_authorized is False
    assert prepared.package.retry_post is False
    assert prepared.package.live_trading == "BLOCKED"
    assert prepared.exit_order.owner_strategy_id == OWNER
    assert prepared.lifecycle.state.status is PaperExitStatus.PREPARED
    assert lifecycle.snapshot(prepared.exit_order.attempt_id).state.attempt_count == 0


def test_wrong_strategy_ownership_is_not_mislabeled_risk_reducing(tmp_path) -> None:
    credentials = _credentials()
    plan = prepare_crypto_close_plan(
        portfolio=_broker_portfolio(credentials),
        symbol="BTC/USD",
        now=NOW,
        limit_price=LIMIT,
    )
    intent = build_exit_intent(plan=plan, attempt_id="r7b-wrong-owner", owner_strategy_id="NOT_THE_OWNER")
    ledger = InMemoryEventLedger()
    decision = CapitalSafetyKernel(_limits(), ledger).evaluate(
        intent=intent,
        market=_market(),
        portfolio=_safety_portfolio(),
        now=NOW,
    )
    assert decision.risk_reducing is False


def test_final_guard_requires_fresh_position_and_stages_only_exact_exit(tmp_path) -> None:
    credentials, _, plan, _, decision, oms, lifecycle, prepared = _prepared(tmp_path)
    fresh = _broker_portfolio(credentials, at=NOW + timedelta(milliseconds=100))
    result = PaperExitFinalGuard().authorize(
        package=prepared.package,
        plan=plan,
        exit_order=prepared.exit_order,
        fresh_portfolio=fresh,
        decision=decision,
        market=_market(),
        oms=oms,
        lifecycle=lifecycle,
        now=NOW + timedelta(milliseconds=200),
    )
    assert result.permit.write_authorized is True
    assert result.permit.retry_post is False
    assert result.permit.live_trading == "BLOCKED"
    with pytest.raises(PaperExitFinalGuardBlocked, match="available"):
        PaperExitFinalGuard().authorize(
            package=prepared.package,
            plan=plan,
            exit_order=prepared.exit_order,
            fresh_portfolio=_broker_portfolio(
                credentials,
                at=NOW + timedelta(milliseconds=100),
                available=Decimal("0.00001"),
            ),
            decision=decision,
            market=_market(),
            oms=oms,
            lifecycle=lifecycle,
            now=NOW + timedelta(milliseconds=200),
        )


def test_writer_is_disabled_by_default(tmp_path) -> None:
    credentials, _, plan, _, decision, oms, lifecycle, prepared = _prepared(tmp_path)
    permit = PaperExitFinalGuard().authorize(
        package=prepared.package,
        plan=plan,
        exit_order=prepared.exit_order,
        fresh_portfolio=_broker_portfolio(credentials, at=NOW + timedelta(milliseconds=100)),
        decision=decision,
        market=_market(),
        oms=oms,
        lifecycle=lifecycle,
        now=NOW + timedelta(milliseconds=200),
    ).permit
    with pytest.raises(PaperExitWriterDisabled):
        PaperExitWriter().submit_once(
            lifecycle=lifecycle,
            order=prepared.exit_order,
            permit=permit,
            credentials=credentials,
            now=NOW + timedelta(milliseconds=300),
        )
    assert lifecycle.snapshot(prepared.exit_order.attempt_id).state.status is PaperExitStatus.PREPARED


def test_writer_burns_unknown_before_exactly_one_post_and_get_reconciles_flat(tmp_path) -> None:
    credentials, _, plan, _, decision, oms, lifecycle, prepared = _prepared(tmp_path)
    permit = PaperExitFinalGuard().authorize(
        package=prepared.package,
        plan=plan,
        exit_order=prepared.exit_order,
        fresh_portfolio=_broker_portfolio(credentials, at=NOW + timedelta(milliseconds=100)),
        decision=decision,
        market=_market(),
        oms=oms,
        lifecycle=lifecycle,
        now=NOW + timedelta(milliseconds=200),
    ).permit
    transport = _CountingWriteTransport(lifecycle, prepared.exit_order.attempt_id, prepared.exit_order.client_order_id)
    receipt = PaperExitWriter(
        config=PaperExitWriterConfig(enabled=True),
        transport=transport,
    ).submit_once(
        lifecycle=lifecycle,
        order=prepared.exit_order,
        permit=permit,
        credentials=credentials,
        now=NOW + timedelta(milliseconds=300),
    )
    assert receipt.retry_post is False
    assert transport.calls == 1
    unknown = lifecycle.snapshot(prepared.exit_order.attempt_id).state
    assert unknown.status is PaperExitStatus.SUBMISSION_UNKNOWN
    assert unknown.restart_action == "RECONCILE_ONLY"

    with pytest.raises(PaperExitWriterBlocked, match="consumed"):
        PaperExitWriter(config=PaperExitWriterConfig(enabled=True), transport=transport).submit_once(
            lifecycle=lifecycle,
            order=prepared.exit_order,
            permit=permit,
            credentials=credentials,
            now=NOW + timedelta(milliseconds=350),
        )
    assert transport.calls == 1

    read = _StaticReadTransport(_order_response(prepared.exit_order, status="filled"))
    reconciled = PaperExitReconciler(transport=read).reconcile(
        lifecycle=lifecycle,
        order=prepared.exit_order,
        credentials=credentials,
        fresh_portfolio=_broker_portfolio(credentials, at=NOW + timedelta(milliseconds=400), quantity=None),
        now=NOW + timedelta(milliseconds=450),
    )
    assert read.calls == 1
    assert reconciled.state.status is PaperExitStatus.RECONCILED_FLAT
    assert reconciled.state.terminal is True
    assert reconciled.state.attempt_count == 1
    assert reconciled.observation.retry_post is False


def test_timeout_after_unknown_never_restores_post_authority(tmp_path) -> None:
    credentials, _, plan, _, decision, oms, lifecycle, prepared = _prepared(tmp_path)
    permit = PaperExitFinalGuard().authorize(
        package=prepared.package,
        plan=plan,
        exit_order=prepared.exit_order,
        fresh_portfolio=_broker_portfolio(credentials, at=NOW + timedelta(milliseconds=100)),
        decision=decision,
        market=_market(),
        oms=oms,
        lifecycle=lifecycle,
        now=NOW + timedelta(milliseconds=200),
    ).permit
    transport = _CountingWriteTransport(lifecycle, prepared.exit_order.attempt_id, prepared.exit_order.client_order_id)
    transport.raise_error = True
    writer = PaperExitWriter(config=PaperExitWriterConfig(enabled=True), transport=transport)
    with pytest.raises(PaperExitWriterAmbiguous, match="retry is forbidden"):
        writer.submit_once(
            lifecycle=lifecycle,
            order=prepared.exit_order,
            permit=permit,
            credentials=credentials,
            now=NOW + timedelta(milliseconds=300),
        )
    assert transport.calls == 1
    state = lifecycle.snapshot(prepared.exit_order.attempt_id).state
    assert state.status is PaperExitStatus.SUBMISSION_UNKNOWN
    assert state.restart_action == "RECONCILE_ONLY"
    with pytest.raises(PaperExitWriterBlocked):
        writer.submit_once(
            lifecycle=lifecycle,
            order=prepared.exit_order,
            permit=permit,
            credentials=credentials,
            now=NOW + timedelta(milliseconds=350),
        )
    assert transport.calls == 1


def test_404_is_order_absence_evidence_not_permission_to_retry(tmp_path) -> None:
    credentials, _, plan, _, decision, oms, lifecycle, prepared = _prepared(tmp_path)
    permit = PaperExitFinalGuard().authorize(
        package=prepared.package,
        plan=plan,
        exit_order=prepared.exit_order,
        fresh_portfolio=_broker_portfolio(credentials, at=NOW + timedelta(milliseconds=100)),
        decision=decision,
        market=_market(),
        oms=oms,
        lifecycle=lifecycle,
        now=NOW + timedelta(milliseconds=200),
    ).permit
    transport = _CountingWriteTransport(lifecycle, prepared.exit_order.attempt_id, prepared.exit_order.client_order_id)
    PaperExitWriter(config=PaperExitWriterConfig(enabled=True), transport=transport).submit_once(
        lifecycle=lifecycle,
        order=prepared.exit_order,
        permit=permit,
        credentials=credentials,
        now=NOW + timedelta(milliseconds=300),
    )
    read = _StaticReadTransport(_order_response(prepared.exit_order, http_status=404))
    result = PaperExitReconciler(transport=read).reconcile(
        lifecycle=lifecycle,
        order=prepared.exit_order,
        credentials=credentials,
        fresh_portfolio=_broker_portfolio(credentials, at=NOW + timedelta(milliseconds=400)),
        now=NOW + timedelta(milliseconds=450),
    )
    assert result.observation.found is False
    assert result.observation.broker_status == "not_found"
    assert result.state.status is PaperExitStatus.ORDER_ABSENT_UNKNOWN
    assert result.state.restart_action == "RECONCILE_ONLY"
    assert result.state.attempt_count == 1
    assert transport.calls == 1


def test_partial_and_no_fill_terminal_reconciliation_are_explicit(tmp_path) -> None:
    for suffix, broker_status, filled, remaining, expected in (
        ("partial", "canceled", Decimal("0.00004"), Decimal("0.000103959"), PaperExitStatus.RECONCILED_PARTIAL),
        ("no-fill", "rejected", Decimal("0"), QTY, PaperExitStatus.RECONCILED_NO_FILL),
    ):
        runtime = SQLitePaperExitAttempt(SQLiteRuntime(tmp_path / f"{suffix}.sqlite3"))
        credentials = _credentials()
        portfolio = _broker_portfolio(credentials)
        plan = prepare_crypto_close_plan(portfolio=portfolio, symbol="BTC/USD", now=NOW, limit_price=LIMIT)
        order = build_paper_exit_order(plan=plan, attempt_id=f"r7b-{suffix}", owner_strategy_id=OWNER)
        runtime.prepare(plan=plan, order=order, at=NOW)
        runtime.mark_submission_unknown(order.attempt_id, at=NOW + timedelta(milliseconds=1))
        result = PaperExitReconciler(transport=_StaticReadTransport(_order_response(order, status=broker_status, filled=filled))).reconcile(
            lifecycle=runtime,
            order=order,
            credentials=credentials,
            fresh_portfolio=_broker_portfolio(
                credentials,
                at=NOW + timedelta(milliseconds=100),
                quantity=remaining,
            ),
            now=NOW + timedelta(milliseconds=150),
        )
        assert result.state.status is expected
        assert result.state.attempt_count == 1


def test_second_unknown_transition_is_permanently_blocked(tmp_path) -> None:
    credentials = _credentials()
    plan = prepare_crypto_close_plan(
        portfolio=_broker_portfolio(credentials),
        symbol="BTC/USD",
        now=NOW,
        limit_price=LIMIT,
    )
    order = build_paper_exit_order(plan=plan, attempt_id="r7b-second-post", owner_strategy_id=OWNER)
    lifecycle = SQLitePaperExitAttempt(SQLiteRuntime(tmp_path / "second.sqlite3"))
    lifecycle.prepare(plan=plan, order=order, at=NOW)
    first = lifecycle.mark_submission_unknown(order.attempt_id, at=NOW + timedelta(milliseconds=1))
    assert first.attempt_count == 1
    with pytest.raises(PaperExitAttemptBlocked, match="already consumed"):
        lifecycle.mark_submission_unknown(order.attempt_id, at=NOW + timedelta(milliseconds=2))
