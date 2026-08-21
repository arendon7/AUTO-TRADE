from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
)
from autotrade.brokers.paper_portfolio import PaperPortfolioPosition, PaperPortfolioSnapshot
from autotrade.paper_close_lifecycle import PaperCloseLifecycleStatus, SQLitePaperCloseLifecycle
from autotrade.paper_close_plan import prepare_crypto_close_plan
from autotrade.paper_close_reconciliation import (
    AlpacaPaperCloseReconciliationGateway,
    PaperCloseReconciliationDisabled,
    PaperCloseReconciliationIntegrityError,
    paper_close_client_order_id,
)
from autotrade.persistence import SQLiteRuntime

NOW = datetime(2026, 8, 21, 14, 40, tzinfo=timezone.utc)
ACCOUNT_ID = "12345678-1234-1234-1234-123456789abc"
ATTEMPT = "r7-close-reconcile-001"


def _credentials(suffix: str = "") -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(f"paper-key-r7-recon{suffix}", f"paper-secret-r7-recon{suffix}")


def _position(qty: str = "0.000143959") -> PaperPortfolioPosition:
    q = Decimal(qty)
    return PaperPortfolioPosition(
        asset_id="btc-asset-id",
        broker_symbol="BTCUSD",
        symbol="BTC/USD",
        asset_class="crypto",
        exchange="CRYPTO",
        side="long",
        quantity=q,
        available_quantity=q,
        avg_entry_price=Decimal("72760.25"),
        current_price=Decimal("72800"),
        market_value=Decimal("10.48"),
        cost_basis=Decimal("10.47"),
        unrealized_pl=Decimal("0.01"),
        unrealized_plpc=Decimal("0.000955"),
    )


def _portfolio(credentials, *, at, position=True, qty="0.000143959", account_ref="a" * 64, cred_ref=None):
    account = AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference=account_ref,
        credential_reference=cred_ref or credentials.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("99989.50"),
        portfolio_value=Decimal("100000.25"),
        shorting_enabled=False,
        attested_at=at,
        request_id="req-account-r7-recon",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )
    return PaperPortfolioSnapshot(
        account=account,
        positions=(_position(qty),) if position else (),
        open_orders=(),
        positions_request_id="req-pos-r7-recon",
        orders_request_id="req-orders-r7-recon",
        positions_response_sha256="c" * 64,
        orders_response_sha256="d" * 64,
        observed_at=at,
    )


class _PortfolioGateway:
    def __init__(self, snapshot):
        self.value = snapshot
        self.calls = 0
    def snapshot(self, **kwargs):
        self.calls += 1
        return self.value


class _OrderTransport:
    def __init__(self, *, plan, attempt, status="filled", filled=None, http_status=200, mutate=None):
        self.plan = plan
        self.attempt = attempt
        self.status = status
        self.filled = plan.quantity if filled is None else Decimal(filled)
        self.http_status = http_status
        self.mutate = mutate
        self.calls = 0
        self.request = None
    def read(self, request):
        self.calls += 1
        self.request = request
        cid = paper_close_client_order_id(attempt_id=self.attempt, plan_hash=self.plan.plan_hash)
        payload = {
            "id": "broker-close-r7-recon",
            "client_order_id": cid,
            "symbol": "BTCUSD",
            "asset_class": "crypto",
            "side": "sell",
            "type": "limit",
            "time_in_force": "ioc",
            "status": self.status,
            "qty": str(self.plan.quantity),
            "filled_qty": str(self.filled),
            "limit_price": str(self.plan.limit_price),
        }
        if self.mutate:
            payload = self.mutate(payload)
        return AlpacaPaperHttpResponse(
            status_code=self.http_status,
            body=json.dumps(payload).encode(),
            final_url=request.url,
            headers={"content-type": "application/json", "x-request-id": "req-order-r7-recon"},
        )


def _setup(tmp_path):
    credentials = _credentials()
    prepared = _portfolio(credentials, at=NOW)
    plan = prepare_crypto_close_plan(portfolio=prepared, symbol="BTC/USD", now=NOW, limit_price=Decimal("72780"))
    lifecycle = SQLitePaperCloseLifecycle(SQLiteRuntime(tmp_path / "r7-close-recon.sqlite3"))
    lifecycle.prepare(attempt_id=ATTEMPT, plan=plan, at=NOW + timedelta(seconds=1))
    lifecycle.mark_submission_unknown(ATTEMPT, at=NOW + timedelta(seconds=2))
    return credentials, plan, lifecycle


def test_filled_close_plus_absent_position_reconciles_flat(tmp_path) -> None:
    credentials, plan, lifecycle = _setup(tmp_path)
    order = _OrderTransport(plan=plan, attempt=ATTEMPT)
    portfolio = _PortfolioGateway(_portfolio(credentials, at=NOW + timedelta(seconds=3), position=False))
    result = AlpacaPaperCloseReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=order,
        portfolio_gateway=portfolio,
    ).reconcile(
        lifecycle=lifecycle,
        attempt_id=ATTEMPT,
        plan=plan,
        credentials=credentials,
        expected_account_id=ACCOUNT_ID,
        now=NOW + timedelta(seconds=3),
    )
    assert order.calls == 1 and portfolio.calls == 1
    assert order.request.method == "GET"
    assert "orders:by_client_order_id" in order.request.url
    assert result.remaining_position == 0
    assert len(result.fingerprint) == 64
    state = lifecycle.snapshot(ATTEMPT).state
    assert state.status is PaperCloseLifecycleStatus.FLAT_RECONCILED
    assert state.submission_attempt_count == 1
    assert state.retry_post is False


def test_partial_fill_reconciles_remaining_position_without_retry(tmp_path) -> None:
    credentials, plan, lifecycle = _setup(tmp_path)
    half = plan.quantity / Decimal("2")
    remaining = plan.observed_position_quantity - half
    order = _OrderTransport(plan=plan, attempt=ATTEMPT, status="partially_filled", filled=str(half))
    portfolio = _PortfolioGateway(_portfolio(credentials, at=NOW + timedelta(seconds=3), qty=str(remaining)))
    result = AlpacaPaperCloseReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True), order_transport=order, portfolio_gateway=portfolio
    ).reconcile(
        lifecycle=lifecycle, attempt_id=ATTEMPT, plan=plan, credentials=credentials,
        expected_account_id=ACCOUNT_ID, now=NOW + timedelta(seconds=3)
    )
    assert result.remaining_position == remaining
    state = lifecycle.snapshot(ATTEMPT).state
    assert state.status is PaperCloseLifecycleStatus.PARTIALLY_FILLED
    assert state.restart_action == "MONITOR_AND_RECONCILE"
    assert state.retry_post is False


def test_terminal_reject_with_remaining_exposure_is_resolved_not_reposted(tmp_path) -> None:
    credentials, plan, lifecycle = _setup(tmp_path)
    order = _OrderTransport(plan=plan, attempt=ATTEMPT, status="rejected", filled="0")
    portfolio = _PortfolioGateway(_portfolio(credentials, at=NOW + timedelta(seconds=3)))
    AlpacaPaperCloseReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True), order_transport=order, portfolio_gateway=portfolio
    ).reconcile(
        lifecycle=lifecycle, attempt_id=ATTEMPT, plan=plan, credentials=credentials,
        expected_account_id=ACCOUNT_ID, now=NOW + timedelta(seconds=3)
    )
    state = lifecycle.snapshot(ATTEMPT).state
    assert state.status is PaperCloseLifecycleStatus.TERMINAL_RECONCILED
    assert state.retry_post is False
    assert state.restart_action == "IDLE"


def test_gateway_disabled_and_unburned_attempt_fail_closed(tmp_path) -> None:
    credentials, plan, lifecycle = _setup(tmp_path)
    with pytest.raises(PaperCloseReconciliationDisabled):
        AlpacaPaperCloseReconciliationGateway().reconcile(
            lifecycle=lifecycle, attempt_id=ATTEMPT, plan=plan, credentials=credentials,
            expected_account_id=ACCOUNT_ID, now=NOW + timedelta(seconds=3)
        )
    second = SQLitePaperCloseLifecycle(SQLiteRuntime(tmp_path / "r7-close-recon-unburned.sqlite3"))
    second.prepare(attempt_id="r7-close-reconcile-unburned", plan=plan, at=NOW + timedelta(seconds=1))
    gateway = AlpacaPaperCloseReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=_OrderTransport(plan=plan, attempt="r7-close-reconcile-unburned"),
        portfolio_gateway=_PortfolioGateway(_portfolio(credentials, at=NOW + timedelta(seconds=3))),
    )
    with pytest.raises(PaperCloseReconciliationIntegrityError, match="exactly one"):
        gateway.reconcile(
            lifecycle=second, attempt_id="r7-close-reconcile-unburned", plan=plan, credentials=credentials,
            expected_account_id=ACCOUNT_ID, now=NOW + timedelta(seconds=3)
        )


def test_order_404_remains_burned_get_only(tmp_path) -> None:
    credentials, plan, lifecycle = _setup(tmp_path)
    gateway = AlpacaPaperCloseReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=_OrderTransport(plan=plan, attempt=ATTEMPT, http_status=404),
        portfolio_gateway=_PortfolioGateway(_portfolio(credentials, at=NOW + timedelta(seconds=3))),
    )
    with pytest.raises(PaperCloseReconciliationIntegrityError, match="remains burned"):
        gateway.reconcile(
            lifecycle=lifecycle, attempt_id=ATTEMPT, plan=plan, credentials=credentials,
            expected_account_id=ACCOUNT_ID, now=NOW + timedelta(seconds=3)
        )
    assert lifecycle.snapshot(ATTEMPT).state.restart_action == "RECONCILE_ONLY"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda p: {**p, "client_order_id": "wrong"}, "client_order_id"),
        (lambda p: {**p, "symbol": "ETHUSD"}, "symbol"),
        (lambda p: {**p, "side": "buy"}, "semantics"),
        (lambda p: {**p, "type": "market"}, "semantics"),
        (lambda p: {**p, "time_in_force": "gtc"}, "semantics"),
        (lambda p: {**p, "status": "mystery"}, "unsupported"),
        (lambda p: {**p, "qty": "0.1"}, "quantity/price"),
        (lambda p: {**p, "filled_qty": "1"}, "quantity/price"),
        (lambda p: {**p, "limit_price": "1"}, "quantity/price"),
    ],
)
def test_order_truth_mismatch_fails_closed(tmp_path, mutate, match) -> None:
    credentials, plan, lifecycle = _setup(tmp_path)
    gateway = AlpacaPaperCloseReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=_OrderTransport(plan=plan, attempt=ATTEMPT, mutate=mutate),
        portfolio_gateway=_PortfolioGateway(_portfolio(credentials, at=NOW + timedelta(seconds=3))),
    )
    with pytest.raises(PaperCloseReconciliationIntegrityError, match=match):
        gateway.reconcile(
            lifecycle=lifecycle, attempt_id=ATTEMPT, plan=plan, credentials=credentials,
            expected_account_id=ACCOUNT_ID, now=NOW + timedelta(seconds=3)
        )


def test_wrong_credentials_account_and_exposure_growth_fail_closed(tmp_path) -> None:
    credentials, plan, lifecycle = _setup(tmp_path)
    cases = [
        (_credentials("-other"), _portfolio(credentials, at=NOW + timedelta(seconds=3)), "credential differs"),
        (credentials, _portfolio(credentials, at=NOW + timedelta(seconds=3), account_ref="e" * 64), "account differs"),
        (credentials, _portfolio(credentials, at=NOW + timedelta(seconds=3), qty="0.0002"), "increased"),
    ]
    for index, (effective, snapshot, match) in enumerate(cases):
        gateway = AlpacaPaperCloseReconciliationGateway(
            config=AlpacaPaperGatewayConfig(enabled=True),
            order_transport=_OrderTransport(plan=plan, attempt=ATTEMPT),
            portfolio_gateway=_PortfolioGateway(snapshot),
        )
        with pytest.raises(PaperCloseReconciliationIntegrityError, match=match):
            gateway.reconcile(
                lifecycle=lifecycle, attempt_id=ATTEMPT, plan=plan, credentials=effective,
                expected_account_id=ACCOUNT_ID, now=NOW + timedelta(seconds=3 + index)
            )


def test_client_order_id_is_deterministic_and_plan_bound() -> None:
    first = paper_close_client_order_id(attempt_id=ATTEMPT, plan_hash="a" * 64)
    assert first == paper_close_client_order_id(attempt_id=ATTEMPT, plan_hash="a" * 64)
    assert first != paper_close_client_order_id(attempt_id=ATTEMPT, plan_hash="b" * 64)
    assert first.startswith("atr7-close-")
    with pytest.raises(ValueError):
        paper_close_client_order_id(attempt_id="", plan_hash="a" * 64)
    with pytest.raises(ValueError):
        paper_close_client_order_id(attempt_id=ATTEMPT, plan_hash="bad")
