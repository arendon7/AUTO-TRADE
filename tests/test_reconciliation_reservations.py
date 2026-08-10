from autotrade.bootstrap import build_durable_paper_core
from autotrade.domain import intent_fingerprint
from autotrade.state import ReservationStatus, RiskReservation


def test_startup_reconciliation_blocks_orphan_reservation(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    db = tmp_path / "state.db"
    core = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    portfolio = core.portfolio_store.get()
    view = core.reservation_store.active_view()
    core.reservation_store.reserve(
        RiskReservation(
            reservation_id="orphan-r1",
            idempotency_key=market_buy_intent.idempotency_key,
            intent_fingerprint=intent_fingerprint(market_buy_intent),
            strategy_id=market_buy_intent.strategy_id,
            symbol=market_buy_intent.symbol,
            signed_notional="1010",
            status=ReservationStatus.RESERVED,
            portfolio_version=portfolio.version,
            created_at=market.observed_at,
            updated_at=market.observed_at,
        ),
        expected_generation=view.generation,
        expected_portfolio_version=portfolio.version,
    )

    restarted = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    assert restarted.startup_reconciliation.ok is False
    assert "ORPHAN_RISK_RESERVATION" in {
        issue.code for issue in restarted.startup_reconciliation.issues
    }
    state = restarted.portfolio_store.get().snapshot
    assert state.reconciliation_ok is False
    assert state.broker_state_known is True
