from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation, AlpacaPaperCredentials
from autotrade.brokers.paper_portfolio import (
    PaperPortfolioOpenOrder,
    PaperPortfolioPosition,
    PaperPortfolioSnapshot,
)
from autotrade.paper_close_lifecycle import PaperCloseLifecycleStatus, SQLitePaperCloseLifecycle
from autotrade.paper_close_plan import prepare_crypto_close_plan
from autotrade.paper_close_writer import (
    PaperCloseWriteResponse,
    PaperCloseWriter,
    PaperCloseWriterAmbiguous,
    PaperCloseWriterBlocked,
    PaperCloseWriterConfig,
    PaperCloseWriterDisabled,
    issue_paper_close_operator_decision,
)
from autotrade.persistence import SQLiteRuntime

NOW = datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc)
ACCOUNT_ID = "12345678-1234-1234-1234-123456789abc"
ATTEMPT = "r7-close-writer-001"


def _credentials(suffix: str = "") -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(f"paper-key-r7-writer{suffix}", f"paper-secret-r7-writer{suffix}")


def _position(*, qty: str = "0.000143959", available: str | None = None, price: str = "72800") -> PaperPortfolioPosition:
    quantity = Decimal(qty)
    return PaperPortfolioPosition(
        asset_id="btc-asset-id",
        broker_symbol="BTCUSD",
        symbol="BTC/USD",
        asset_class="crypto",
        exchange="CRYPTO",
        side="long",
        quantity=quantity,
        available_quantity=Decimal(available) if available is not None else abs(quantity),
        avg_entry_price=Decimal("72760.25"),
        current_price=Decimal(price),
        market_value=Decimal("10.48"),
        cost_basis=Decimal("10.47"),
        unrealized_pl=Decimal("0.01"),
        unrealized_plpc=Decimal("0.000955"),
    )


def _sell_order() -> PaperPortfolioOpenOrder:
    return PaperPortfolioOpenOrder(
        broker_order_id="existing-sell-1",
        client_order_id="existing-sell-client-1",
        broker_symbol="BTCUSD",
        symbol="BTC/USD",
        asset_class="crypto",
        side="sell",
        order_type="limit",
        time_in_force="gtc",
        status="new",
        quantity=Decimal("0.00005"),
        filled_quantity=Decimal("0"),
        limit_price=Decimal("73000"),
        stop_price=None,
    )


def _portfolio(
    credentials: AlpacaPaperCredentials,
    *,
    observed_at: datetime,
    position: PaperPortfolioPosition | None = None,
    orders: tuple[PaperPortfolioOpenOrder, ...] = (),
    account_reference: str = "a" * 64,
    credential_reference: str | None = None,
) -> PaperPortfolioSnapshot:
    account = AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference=account_reference,
        credential_reference=credential_reference or credentials.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("99989.50"),
        portfolio_value=Decimal("100000.25"),
        shorting_enabled=False,
        attested_at=observed_at,
        request_id="req-account-r7-writer",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )
    positions = () if position is None else (position,)
    return PaperPortfolioSnapshot(
        account=account,
        positions=positions,
        open_orders=orders,
        positions_request_id="req-positions-r7-writer",
        orders_request_id="req-orders-r7-writer",
        positions_response_sha256="c" * 64,
        orders_response_sha256="d" * 64,
        observed_at=observed_at,
    )


class _Transport:
    def __init__(self, *, status: int = 200, mutate=None, exc: Exception | None = None) -> None:
        self.status = status
        self.mutate = mutate
        self.exc = exc
        self.calls = 0
        self.last = None

    def post(self, **kwargs):
        self.calls += 1
        self.last = kwargs
        if self.exc is not None:
            raise self.exc
        request = json.loads(kwargs["body"])
        document = {
            "id": "broker-r7-close-1",
            "status": "accepted",
            "client_order_id": request["client_order_id"],
        }
        if self.mutate is not None:
            document = self.mutate(document)
        return PaperCloseWriteResponse(
            status_code=self.status,
            body=json.dumps(document).encode(),
            headers={"x-request-id": "req-close-post-r7"},
        )


def _setup(tmp_path):
    credentials = _credentials()
    prepared_portfolio = _portfolio(credentials, observed_at=NOW, position=_position())
    plan = prepare_crypto_close_plan(
        portfolio=prepared_portfolio,
        symbol="BTC/USD",
        now=NOW,
        limit_price=Decimal("72780"),
    )
    lifecycle = SQLitePaperCloseLifecycle(SQLiteRuntime(tmp_path / "r7-close-writer.sqlite3"))
    lifecycle.prepare(attempt_id=ATTEMPT, plan=plan, at=NOW + timedelta(seconds=1))
    decision = issue_paper_close_operator_decision(
        attempt_id=ATTEMPT,
        plan=plan,
        confirmation="CERRAR PAPER",
        now=NOW + timedelta(seconds=1),
    )
    fresh = _portfolio(credentials, observed_at=NOW + timedelta(seconds=2), position=_position())
    return credentials, plan, lifecycle, decision, fresh


def test_successful_writer_burns_unknown_before_single_post(tmp_path) -> None:
    credentials, plan, lifecycle, decision, fresh = _setup(tmp_path)
    transport = _Transport()
    writer = PaperCloseWriter(config=PaperCloseWriterConfig(enabled=True), transport=transport)
    receipt = writer.submit_once(
        lifecycle=lifecycle,
        attempt_id=ATTEMPT,
        plan=plan,
        decision=decision,
        fresh_portfolio=fresh,
        credentials=credentials,
        now=NOW + timedelta(seconds=2),
    )
    assert transport.calls == 1
    assert transport.last["host"] == "paper-api.alpaca.markets"
    assert transport.last["path"] == "/v2/orders"
    payload = json.loads(transport.last["body"])
    assert payload["symbol"] == "BTC/USD"
    assert payload["side"] == "sell"
    assert payload["type"] == "limit"
    assert payload["time_in_force"] == "ioc"
    assert payload["qty"] == "0.000143959"
    assert payload["limit_price"] == "72780"
    assert receipt.broker_order_id == "broker-r7-close-1"
    state = lifecycle.snapshot(ATTEMPT).state
    assert state.status is PaperCloseLifecycleStatus.SUBMISSION_UNKNOWN
    assert state.submission_attempt_count == 1
    assert state.restart_action == "RECONCILE_ONLY"
    with pytest.raises(PaperCloseWriterBlocked):
        writer.submit_once(
            lifecycle=lifecycle,
            attempt_id=ATTEMPT,
            plan=plan,
            decision=decision,
            fresh_portfolio=fresh,
            credentials=credentials,
            now=NOW + timedelta(seconds=3),
        )
    assert transport.calls == 1


def test_writer_disabled_by_default_does_not_burn_attempt(tmp_path) -> None:
    credentials, plan, lifecycle, decision, fresh = _setup(tmp_path)
    transport = _Transport()
    with pytest.raises(PaperCloseWriterDisabled):
        PaperCloseWriter(transport=transport).submit_once(
            lifecycle=lifecycle,
            attempt_id=ATTEMPT,
            plan=plan,
            decision=decision,
            fresh_portfolio=fresh,
            credentials=credentials,
            now=NOW + timedelta(seconds=2),
        )
    assert transport.calls == 0
    assert lifecycle.snapshot(ATTEMPT).state.status is PaperCloseLifecycleStatus.PREPARED


def test_transport_exception_is_ambiguous_and_attempt_stays_burned(tmp_path) -> None:
    credentials, plan, lifecycle, decision, fresh = _setup(tmp_path)
    transport = _Transport(exc=TimeoutError("network timeout"))
    writer = PaperCloseWriter(config=PaperCloseWriterConfig(enabled=True), transport=transport)
    with pytest.raises(PaperCloseWriterAmbiguous, match="GET-only"):
        writer.submit_once(
            lifecycle=lifecycle,
            attempt_id=ATTEMPT,
            plan=plan,
            decision=decision,
            fresh_portfolio=fresh,
            credentials=credentials,
            now=NOW + timedelta(seconds=2),
        )
    assert transport.calls == 1
    assert lifecycle.snapshot(ATTEMPT).state.restart_action == "RECONCILE_ONLY"


def test_non_2xx_and_malformed_success_are_ambiguous_after_burn(tmp_path) -> None:
    cases = [
        _Transport(status=422),
        _Transport(mutate=lambda d: {**d, "client_order_id": "wrong"}),
        _Transport(mutate=lambda d: {"status": "accepted", "client_order_id": d["client_order_id"]}),
    ]
    for index, transport in enumerate(cases, start=1):
        credentials = _credentials(str(index))
        prepared = _portfolio(credentials, observed_at=NOW, position=_position())
        plan = prepare_crypto_close_plan(portfolio=prepared, symbol="BTC/USD", now=NOW, limit_price=Decimal("72780"))
        attempt = f"r7-close-writer-case-{index}"
        lifecycle = SQLitePaperCloseLifecycle(SQLiteRuntime(tmp_path / f"writer-case-{index}.sqlite3"))
        lifecycle.prepare(attempt_id=attempt, plan=plan, at=NOW + timedelta(seconds=1))
        decision = issue_paper_close_operator_decision(attempt_id=attempt, plan=plan, confirmation="CERRAR PAPER", now=NOW + timedelta(seconds=1))
        fresh = _portfolio(credentials, observed_at=NOW + timedelta(seconds=2), position=_position())
        writer = PaperCloseWriter(config=PaperCloseWriterConfig(enabled=True), transport=transport)
        with pytest.raises(PaperCloseWriterAmbiguous):
            writer.submit_once(
                lifecycle=lifecycle,
                attempt_id=attempt,
                plan=plan,
                decision=decision,
                fresh_portfolio=fresh,
                credentials=credentials,
                now=NOW + timedelta(seconds=2),
            )
        assert transport.calls == 1
        assert lifecycle.snapshot(attempt).state.status is PaperCloseLifecycleStatus.SUBMISSION_UNKNOWN


def test_operator_confirmation_is_exact_and_fresh(tmp_path) -> None:
    credentials = _credentials()
    prepared = _portfolio(credentials, observed_at=NOW, position=_position())
    plan = prepare_crypto_close_plan(portfolio=prepared, symbol="BTC/USD", now=NOW, limit_price=Decimal("72780"))
    with pytest.raises(PaperCloseWriterBlocked, match="confirmation"):
        issue_paper_close_operator_decision(attempt_id=ATTEMPT, plan=plan, confirmation="cerrar paper", now=NOW + timedelta(seconds=1))
    with pytest.raises(PaperCloseWriterBlocked, match="fresh"):
        issue_paper_close_operator_decision(attempt_id=ATTEMPT, plan=plan, confirmation="CERRAR PAPER", now=plan.expires_at)
    decision = issue_paper_close_operator_decision(attempt_id=ATTEMPT, plan=plan, confirmation="CERRAR PAPER", now=NOW + timedelta(seconds=1))
    assert decision.approved is True
    assert decision.valid_at(NOW + timedelta(seconds=2)) is True
    assert decision.valid_at(decision.expires_at) is False


@pytest.mark.parametrize(
    ("fresh_factory", "credentials_factory", "match"),
    [
        (lambda c, t: _portfolio(c, observed_at=t - timedelta(seconds=6), position=_position()), lambda c: c, "stale"),
        (lambda c, t: _portfolio(c, observed_at=t + timedelta(seconds=1), position=_position()), lambda c: c, "stale"),
        (lambda c, t: _portfolio(c, observed_at=t, position=_position(), account_reference="e" * 64), lambda c: c, "account"),
        (lambda c, t: _portfolio(c, observed_at=t, position=_position(), credential_reference="f" * 64), lambda c: c, "credential"),
        (lambda c, t: _portfolio(c, observed_at=t, position=None), lambda c: c, "exactly one"),
        (lambda c, t: _portfolio(c, observed_at=t, position=_position(qty="0.00015")), lambda c: c, "increased"),
        (lambda c, t: _portfolio(c, observed_at=t, position=_position(available="0.0001")), lambda c: c, "available"),
        (lambda c, t: _portfolio(c, observed_at=t, position=_position(), orders=(_sell_order(),)), lambda c: c, "SELL"),
        (lambda c, t: _portfolio(c, observed_at=t, position=_position(price="74000")), lambda c: c, "slippage"),
        (lambda c, t: _portfolio(c, observed_at=t, position=_position()), lambda c: _credentials("-rotated"), "credential differs"),
    ],
)
def test_final_guard_blocks_before_post(tmp_path, fresh_factory, credentials_factory, match: str) -> None:
    credentials, plan, lifecycle, decision, _fresh = _setup(tmp_path)
    now = NOW + timedelta(seconds=2)
    fresh = fresh_factory(credentials, now)
    effective = credentials_factory(credentials)
    transport = _Transport()
    writer = PaperCloseWriter(config=PaperCloseWriterConfig(enabled=True), transport=transport)
    with pytest.raises(PaperCloseWriterBlocked, match=match):
        writer.submit_once(
            lifecycle=lifecycle,
            attempt_id=ATTEMPT,
            plan=plan,
            decision=decision,
            fresh_portfolio=fresh,
            credentials=effective,
            now=now,
        )
    assert transport.calls == 0
    assert lifecycle.snapshot(ATTEMPT).state.status is PaperCloseLifecycleStatus.PREPARED


def test_mismatched_decision_and_expired_plan_block_before_post(tmp_path) -> None:
    credentials, plan, lifecycle, decision, fresh = _setup(tmp_path)
    writer = PaperCloseWriter(config=PaperCloseWriterConfig(enabled=True), transport=_Transport())
    bad_decision = replace(decision, attempt_id="r7-close-writer-other")
    # Restore internal hash consistency by using a valid decision for another attempt.
    bad_decision = issue_paper_close_operator_decision(
        attempt_id="r7-close-writer-other",
        plan=plan,
        confirmation="CERRAR PAPER",
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PaperCloseWriterBlocked, match="does not match"):
        writer.submit_once(
            lifecycle=lifecycle,
            attempt_id=ATTEMPT,
            plan=plan,
            decision=bad_decision,
            fresh_portfolio=fresh,
            credentials=credentials,
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(PaperCloseWriterBlocked, match="expired"):
        writer.submit_once(
            lifecycle=lifecycle,
            attempt_id=ATTEMPT,
            plan=plan,
            decision=decision,
            fresh_portfolio=fresh,
            credentials=credentials,
            now=plan.expires_at,
        )


def test_writer_config_rejects_live_host_bad_timeout_and_response_limit() -> None:
    with pytest.raises(ValueError, match="PAPER host"):
        PaperCloseWriterConfig(host="api.alpaca.markets")
    with pytest.raises(ValueError, match="timeout"):
        PaperCloseWriterConfig(timeout_seconds=0)
    with pytest.raises(ValueError, match="response limit"):
        PaperCloseWriterConfig(max_response_bytes=0)
