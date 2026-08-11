from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.bootstrap import build_durable_paper_core
from autotrade.oms import OrderRejectedByControlPlane
from autotrade.persistence import SQLiteEventLedger, SQLiteRuntime
from autotrade.risk_state import SQLiteR2SafetyStateStore, SQLiteRiskTelemetryStore


def test_daily_loss_breach_activates_durable_circuit_and_blocks_new_risk(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    db = tmp_path / "daily-loss.db"
    core = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    state = core.risk_telemetry.record_equity(
        equity=Decimal("99000"),
        now=market.observed_at + timedelta(milliseconds=100),
    )
    assert state.daily_pnl == Decimal("-1000")
    assert core.safety.circuit_active is True
    assert core.safety.state_store.get().circuit_reason.startswith("MAX_DAILY_LOSS")

    blocked = core.pipeline.process_intent(
        intent=market_buy_intent,
        market=replace(market, observed_at=market.observed_at + timedelta(milliseconds=100)),
        now=market.observed_at + timedelta(milliseconds=100),
    )
    assert blocked.order is None
    assert blocked.decision.reason_code == "CIRCUIT_ACTIVE"
    assert core.broker.submission_count == 0
    assert core.ledger.verify_integrity() is True
    assert "CIRCUIT_ACTIVATED" in {event.event_type for event in core.ledger.all_events()}

    restarted = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at + timedelta(seconds=1),
    )
    assert restarted.safety.circuit_active is True
    assert restarted.risk_telemetry.get().daily_pnl == Decimal("-1000")


def test_drawdown_breach_activates_circuit_when_daily_loss_limit_is_not_binding(
    tmp_path, limits, market, empty_portfolio
):
    wide_loss_limits = replace(limits, max_daily_loss=Decimal("1000000"))
    core = build_durable_paper_core(
        db_path=tmp_path / "drawdown.db",
        limits=wide_loss_limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    core.risk_telemetry.record_equity(
        equity=Decimal("110000"),
        now=market.observed_at + timedelta(seconds=1),
    )
    state = core.risk_telemetry.record_equity(
        equity=Decimal("99000"),
        now=market.observed_at + timedelta(seconds=2),
    )
    assert state.drawdown == Decimal("0.1")
    assert core.safety.circuit_active is True
    assert core.safety.state_store.get().circuit_reason.startswith("MAX_DRAWDOWN")


def test_session_rollover_resets_metrics_but_never_auto_clears_circuit(
    tmp_path, limits, market, empty_portfolio
):
    core = build_durable_paper_core(
        db_path=tmp_path / "rollover.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    core.risk_telemetry.record_equity(
        equity=Decimal("99000"),
        now=market.observed_at + timedelta(milliseconds=10),
    )
    assert core.safety.circuit_active is True

    next_day = market.observed_at + timedelta(days=1)
    rolled = core.risk_telemetry.record_equity(equity=Decimal("100500"), now=next_day)
    assert rolled.daily_pnl == 0
    assert rolled.drawdown == 0
    assert rolled.day_start_equity == Decimal("100500")
    assert core.safety.circuit_active is True

    core.safety.acknowledge_circuit(
        confirmed_by="human-operator",
        reason="reviewed next-session recovery evidence",
        now=next_day + timedelta(milliseconds=1),
    )
    assert core.safety.circuit_active is False
    assert core.ledger.all_events()[-1].event_type == "CIRCUIT_ACKNOWLEDGED"


def test_circuit_activation_invalidates_decision_approved_milliseconds_before_submit(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    core = build_durable_paper_core(
        db_path=tmp_path / "stale-decision.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    decision = core.safety.evaluate(
        intent=market_buy_intent,
        market=market,
        portfolio=core.portfolio_store.get().snapshot,
        now=market.observed_at,
    )
    approved_version = decision.safety_state_version

    core.risk_telemetry.record_equity(
        equity=Decimal("99000"),
        now=market.observed_at + timedelta(milliseconds=10),
    )
    assert core.safety.state_store.get().version > approved_version

    with pytest.raises(OrderRejectedByControlPlane, match="safety state changed"):
        core.oms.submit(
            intent=market_buy_intent,
            decision=decision,
            market=market,
            now=market.observed_at + timedelta(milliseconds=10),
        )
    assert core.broker.submission_count == 0


def test_kill_and_circuit_flags_do_not_clear_each_other(tmp_path, limits, market, empty_portfolio):
    core = build_durable_paper_core(
        db_path=tmp_path / "strict-state.db",
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    core.safety.activate_kill_switch(reason="manual emergency", now=market.observed_at)
    core.risk_telemetry.record_equity(
        equity=Decimal("99000"),
        now=market.observed_at + timedelta(milliseconds=10),
    )
    state = core.safety.state_store.get()
    assert state.kill_switch_active is True
    assert state.circuit_active is True

    core.safety.reset_kill_switch(
        confirmed_by="human-operator",
        now=market.observed_at + timedelta(milliseconds=20),
    )
    state = core.safety.state_store.get()
    assert state.kill_switch_active is False
    assert state.circuit_active is True

    core.safety.activate_kill_switch(
        reason="second emergency",
        now=market.observed_at + timedelta(milliseconds=30),
    )
    core.safety.acknowledge_circuit(
        confirmed_by="human-operator",
        reason="circuit reviewed only",
        now=market.observed_at + timedelta(milliseconds=40),
    )
    state = core.safety.state_store.get()
    assert state.circuit_active is False
    assert state.kill_switch_active is True


def test_risk_telemetry_rejects_invalid_or_time_reversing_updates(tmp_path, limits, now):
    runtime = SQLiteRuntime(tmp_path / "telemetry-validation.db")
    SQLiteR2SafetyStateStore(runtime)
    store = SQLiteRiskTelemetryStore(
        runtime,
        max_daily_loss=limits.max_daily_loss,
        max_drawdown=limits.max_drawdown,
    )
    store.initialize(equity=Decimal("100000"), now=now)

    for invalid in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError, match="equity"):
            store.record_equity(equity=invalid, now=now)

    store.record_equity(equity=Decimal("100100"), now=now + timedelta(seconds=1))
    with pytest.raises(ValueError, match="backward"):
        store.record_equity(equity=Decimal("100200"), now=now + timedelta(milliseconds=500))


def test_automatic_circuit_event_is_part_of_same_valid_ledger_chain(tmp_path, limits, now):
    runtime = SQLiteRuntime(tmp_path / "atomic-ledger.db")
    safety_store = SQLiteR2SafetyStateStore(runtime)
    telemetry = SQLiteRiskTelemetryStore(
        runtime,
        max_daily_loss=limits.max_daily_loss,
        max_drawdown=limits.max_drawdown,
    )
    telemetry.initialize(equity=Decimal("100000"), now=now)
    telemetry.record_equity(equity=Decimal("99000"), now=now + timedelta(seconds=1))

    assert safety_store.get().circuit_active is True
    ledger = SQLiteEventLedger(runtime)
    assert ledger.verify_integrity() is True
    events = ledger.all_events()
    assert len(events) == 1
    assert events[0].event_type == "CIRCUIT_ACTIVATED"
    assert events[0].payload["source"] == "RISK_TELEMETRY"
