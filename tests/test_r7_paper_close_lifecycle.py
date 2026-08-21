from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation
from autotrade.brokers.paper_portfolio import PaperPortfolioPosition, PaperPortfolioSnapshot
from autotrade.paper_close_lifecycle import (
    PaperCloseLifecycleBlocked,
    PaperCloseLifecycleConflict,
    PaperCloseLifecycleIntegrityError,
    PaperCloseLifecycleStatus,
    SQLitePaperCloseLifecycle,
)
from autotrade.paper_close_plan import prepare_crypto_close_plan
from autotrade.persistence import SQLiteRuntime

NOW = datetime(2026, 8, 21, 14, 20, tzinfo=timezone.utc)
ATTEMPT = "r7-close-btc-001"


def _portfolio(*, qty: str = "0.000143959", available: str | None = None) -> PaperPortfolioSnapshot:
    quantity = Decimal(qty)
    available_quantity = Decimal(available) if available is not None else abs(quantity)
    account = AlpacaPaperAccountAttestation(
        account_id="12345678-1234-1234-1234-123456789abc",
        account_reference="a" * 64,
        credential_reference="b" * 64,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("99989.50"),
        portfolio_value=Decimal("100000.25"),
        shorting_enabled=False,
        attested_at=NOW,
        request_id="req-account-r7-close",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )
    position = PaperPortfolioPosition(
        asset_id="btc-asset-id",
        broker_symbol="BTCUSD",
        symbol="BTC/USD",
        asset_class="crypto",
        exchange="CRYPTO",
        side="long",
        quantity=quantity,
        available_quantity=available_quantity,
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
        positions_request_id="req-positions-r7-close",
        orders_request_id="req-orders-r7-close",
        positions_response_sha256="c" * 64,
        orders_response_sha256="d" * 64,
        observed_at=NOW,
    )


def _plan(*, at: datetime = NOW):
    return prepare_crypto_close_plan(
        portfolio=_portfolio(),
        symbol="BTC/USD",
        now=at,
        limit_price=Decimal("72780"),
    )


def _lifecycle(tmp_path):
    return SQLitePaperCloseLifecycle(SQLiteRuntime(tmp_path / "r7-close.sqlite3"))


def test_prepare_is_durable_idempotent_and_write_inert(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    plan = _plan()
    state = lifecycle.prepare(attempt_id=ATTEMPT, plan=plan, at=NOW)
    replay = lifecycle.prepare(attempt_id=ATTEMPT, plan=plan, at=NOW)
    assert replay == state
    assert state.status is PaperCloseLifecycleStatus.PREPARED
    assert state.submission_attempt_count == 0
    assert state.retry_post is False
    assert state.restart_action == "CONTINUE_SAME_ATTEMPT_ONLY"
    assert state.to_dict()["live_trading"] == "BLOCKED"
    snapshot = lifecycle.snapshot(ATTEMPT)
    assert len(snapshot.events) == 1
    assert snapshot.events[0].event_type.value == "PREPARED"


def test_unknown_is_burned_before_future_post_and_never_retryable(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    lifecycle.prepare(attempt_id=ATTEMPT, plan=_plan(), at=NOW)
    unknown = lifecycle.mark_submission_unknown(ATTEMPT, at=NOW + timedelta(seconds=1))
    assert unknown.status is PaperCloseLifecycleStatus.SUBMISSION_UNKNOWN
    assert unknown.submission_attempt_count == 1
    assert unknown.restart_action == "RECONCILE_ONLY"
    assert unknown.retry_post is False
    with pytest.raises(PaperCloseLifecycleBlocked):
        lifecycle.mark_submission_unknown(ATTEMPT, at=NOW + timedelta(seconds=2))


def test_reconcile_open_acknowledged_and_partial_states(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    plan = _plan()
    lifecycle.prepare(attempt_id=ATTEMPT, plan=plan, at=NOW)
    lifecycle.mark_submission_unknown(ATTEMPT, at=NOW + timedelta(seconds=1))
    ack = lifecycle.reconcile(
        ATTEMPT,
        broker_order_id="broker-close-1",
        broker_status="new",
        filled_quantity=Decimal("0"),
        remaining_position=plan.observed_position_quantity,
        at=NOW + timedelta(seconds=2),
    )
    assert ack.status is PaperCloseLifecycleStatus.ACKNOWLEDGED
    assert ack.restart_action == "MONITOR_AND_RECONCILE"
    partial_fill = plan.quantity / Decimal("2")
    partial = lifecycle.reconcile(
        ATTEMPT,
        broker_order_id="broker-close-1",
        broker_status="partially_filled",
        filled_quantity=partial_fill,
        remaining_position=plan.observed_position_quantity - partial_fill,
        at=NOW + timedelta(seconds=3),
    )
    assert partial.status is PaperCloseLifecycleStatus.PARTIALLY_FILLED
    assert partial.broker_filled_quantity == partial_fill


def test_terminal_fill_to_zero_reconciles_flat(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    plan = _plan()
    lifecycle.prepare(attempt_id=ATTEMPT, plan=plan, at=NOW)
    lifecycle.mark_submission_unknown(ATTEMPT, at=NOW + timedelta(seconds=1))
    flat = lifecycle.reconcile(
        ATTEMPT,
        broker_order_id="broker-close-2",
        broker_status="filled",
        filled_quantity=plan.quantity,
        remaining_position=Decimal("0"),
        at=NOW + timedelta(seconds=2),
    )
    assert flat.status is PaperCloseLifecycleStatus.FLAT_RECONCILED
    assert flat.restart_action == "IDLE"
    assert len(lifecycle.snapshot(ATTEMPT).events) == 3


def test_terminal_partial_or_rejected_with_exposure_is_terminal_reconciled_not_retry(tmp_path) -> None:
    for index, status in enumerate(("canceled", "expired", "rejected"), start=1):
        lifecycle = SQLitePaperCloseLifecycle(SQLiteRuntime(tmp_path / f"r7-close-{index}.sqlite3"))
        plan = _plan()
        attempt = f"r7-close-terminal-{index}"
        lifecycle.prepare(attempt_id=attempt, plan=plan, at=NOW)
        lifecycle.mark_submission_unknown(attempt, at=NOW + timedelta(seconds=1))
        state = lifecycle.reconcile(
            attempt,
            broker_order_id=f"broker-close-terminal-{index}",
            broker_status=status,
            filled_quantity=Decimal("0"),
            remaining_position=plan.observed_position_quantity,
            at=NOW + timedelta(seconds=2),
        )
        assert state.status is PaperCloseLifecycleStatus.TERMINAL_RECONCILED
        assert state.retry_post is False
        assert state.restart_action == "IDLE"


def test_same_plan_cannot_bind_to_second_attempt(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    plan = _plan()
    lifecycle.prepare(attempt_id=ATTEMPT, plan=plan, at=NOW)
    with pytest.raises(PaperCloseLifecycleConflict, match="plan"):
        lifecycle.prepare(attempt_id="r7-close-btc-002", plan=plan, at=NOW)


def test_same_attempt_cannot_rebind_different_plan(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    first = _plan()
    lifecycle.prepare(attempt_id=ATTEMPT, plan=first, at=NOW)
    second = prepare_crypto_close_plan(
        portfolio=_portfolio(),
        symbol="BTC/USD",
        now=NOW + timedelta(seconds=1),
        quantity=Decimal("0.0001"),
        limit_price=Decimal("72780"),
    )
    with pytest.raises(PaperCloseLifecycleConflict, match="attempt_id"):
        lifecycle.prepare(attempt_id=ATTEMPT, plan=second, at=NOW + timedelta(seconds=1))


def test_prepare_rejects_expired_or_not_yet_valid_plan(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    plan = _plan()
    with pytest.raises(PaperCloseLifecycleBlocked, match="fresh"):
        lifecycle.prepare(attempt_id=ATTEMPT, plan=plan, at=NOW - timedelta(seconds=1))
    with pytest.raises(PaperCloseLifecycleBlocked, match="fresh"):
        lifecycle.prepare(attempt_id=ATTEMPT, plan=plan, at=plan.expires_at)


@pytest.mark.parametrize(
    ("status", "filled", "remaining", "match"),
    [
        ("new", "0.00001", "0.000133959", "reports fills"),
        ("partially_filled", "0", "0.000143959", "partial"),
        ("partially_filled", "0.000143959", "0", "partial"),
        ("filled", "0.0002", "0", "exceeds"),
        ("new", "0", "0.0002", "increased"),
    ],
)
def test_reconciliation_integrity_fail_closed(tmp_path, status: str, filled: str, remaining: str, match: str) -> None:
    lifecycle = _lifecycle(tmp_path)
    lifecycle.prepare(attempt_id=ATTEMPT, plan=_plan(), at=NOW)
    lifecycle.mark_submission_unknown(ATTEMPT, at=NOW + timedelta(seconds=1))
    with pytest.raises(PaperCloseLifecycleIntegrityError, match=match):
        lifecycle.reconcile(
            ATTEMPT,
            broker_order_id="broker-close-bad",
            broker_status=status,
            filled_quantity=Decimal(filled),
            remaining_position=Decimal(remaining),
            at=NOW + timedelta(seconds=2),
        )


def test_reconcile_requires_one_burned_attempt_and_supported_broker_status(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    lifecycle.prepare(attempt_id=ATTEMPT, plan=_plan(), at=NOW)
    with pytest.raises(PaperCloseLifecycleBlocked, match="exactly one"):
        lifecycle.reconcile(
            ATTEMPT,
            broker_order_id="broker-close-pre",
            broker_status="new",
            filled_quantity=Decimal("0"),
            remaining_position=Decimal("0.000143959"),
            at=NOW + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="unsupported"):
        lifecycle.reconcile(
            ATTEMPT,
            broker_order_id="broker-close-pre",
            broker_status="mystery",
            filled_quantity=Decimal("0"),
            remaining_position=Decimal("0.000143959"),
            at=NOW + timedelta(seconds=1),
        )


def test_halt_forces_reconciliation_only_and_can_recover(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    plan = _plan()
    lifecycle.prepare(attempt_id=ATTEMPT, plan=plan, at=NOW)
    lifecycle.mark_submission_unknown(ATTEMPT, at=NOW + timedelta(seconds=1))
    halted = lifecycle.halt(ATTEMPT, reason="network ambiguity", at=NOW + timedelta(seconds=2))
    assert halted.status is PaperCloseLifecycleStatus.HALTED_RECONCILIATION_REQUIRED
    assert halted.restart_action == "RECONCILE_ONLY"
    resolved = lifecycle.reconcile(
        ATTEMPT,
        broker_order_id="broker-close-halted",
        broker_status="filled",
        filled_quantity=plan.quantity,
        remaining_position=Decimal("0"),
        at=NOW + timedelta(seconds=3),
    )
    assert resolved.status is PaperCloseLifecycleStatus.FLAT_RECONCILED
    with pytest.raises(PaperCloseLifecycleBlocked):
        lifecycle.halt(ATTEMPT, reason="too late", at=NOW + timedelta(seconds=4))


def test_lifecycle_rejects_backward_time_missing_attempt_and_invalid_inputs(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    lifecycle.prepare(attempt_id=ATTEMPT, plan=_plan(), at=NOW)
    with pytest.raises(PaperCloseLifecycleBlocked, match="backwards"):
        lifecycle.mark_submission_unknown(ATTEMPT, at=NOW - timedelta(seconds=1))
    with pytest.raises(PaperCloseLifecycleIntegrityError, match="missing"):
        lifecycle.snapshot("r7-close-missing")
    with pytest.raises(ValueError):
        lifecycle.mark_submission_unknown("bad id!", at=NOW)
    with pytest.raises(ValueError):
        lifecycle.halt(ATTEMPT, reason="", at=NOW)


def test_control_hash_tampering_is_detected(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "r7-close-tamper.sqlite3")
    lifecycle = SQLitePaperCloseLifecycle(runtime)
    lifecycle.prepare(attempt_id=ATTEMPT, plan=_plan(), at=NOW)
    conn = runtime.connect()
    try:
        conn.execute("UPDATE r7_paper_close_control SET control_hash = ? WHERE attempt_id = ?", ("f" * 64, ATTEMPT))
    finally:
        conn.close()
    with pytest.raises(PaperCloseLifecycleIntegrityError, match="control hash"):
        lifecycle.snapshot(ATTEMPT)


def test_event_chain_tampering_is_detected(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "r7-close-event-tamper.sqlite3")
    lifecycle = SQLitePaperCloseLifecycle(runtime)
    lifecycle.prepare(attempt_id=ATTEMPT, plan=_plan(), at=NOW)
    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE r7_paper_close_events SET event_hash = ? WHERE attempt_id = ? AND sequence = 1",
            ("e" * 64, ATTEMPT),
        )
    finally:
        conn.close()
    with pytest.raises(PaperCloseLifecycleIntegrityError, match="event hash|control head"):
        lifecycle.snapshot(ATTEMPT)
