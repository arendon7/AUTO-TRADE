from datetime import timedelta


def test_terminal_broker_snapshot_replay_at_later_time_is_idempotent(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    from autotrade.bootstrap import build_durable_paper_core

    db = tmp_path / "semantic-replay.db"
    core = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at,
    )
    first = core.pipeline.process_intent(
        intent=market_buy_intent,
        market=market,
        now=market.observed_at,
    )
    assert first.order is not None
    before = tuple(event.event_id for event in core.ledger.all_events())

    restarted = build_durable_paper_core(
        db_path=db,
        limits=limits,
        initial_portfolio=empty_portfolio,
        now=market.observed_at + timedelta(seconds=5),
    )
    assert restarted.startup_reconciliation.ok is True
    after = tuple(event.event_id for event in restarted.ledger.all_events())
    # Re-observation cannot create a second semantic ORDER_BROKER_RESULT.
    result_ids = [value for value in after if value.startswith("order-result:")]
    assert len(result_ids) == 1
    assert set(before).issubset(set(after))
