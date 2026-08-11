from dataclasses import replace
from decimal import Decimal

import pytest

from autotrade.domain import Fill, OrderRecord, OrderStatus
from autotrade.execution_state import FillIntegrityConflict, SQLiteFillAwarePortfolioStore
from autotrade.persistence import SQLiteRuntime
from autotrade.state import InMemoryPortfolioStore


def order_and_fill(intent, now):
    order = OrderRecord(
        order_id="projection-order",
        intent=intent,
        risk_decision_id="risk",
        status=OrderStatus.PARTIALLY_FILLED,
        created_at=now,
        submitted_at=now,
    )
    fill = Fill(
        fill_id="projection-fill",
        order_id=order.order_id,
        symbol=intent.symbol,
        side=intent.side,
        quantity=Decimal("4"),
        price=Decimal("100"),
        occurred_at=now,
    )
    return order, fill


def test_inmemory_projection_duplicate_is_idempotent_but_conflict_fails(
    empty_portfolio, market_buy_intent, now
):
    store = InMemoryPortfolioStore()
    store.initialize(empty_portfolio, now=now)
    order, fill = order_and_fill(market_buy_intent, now)
    first = store.apply_fills(order, (fill,), now=now)
    replay = store.apply_fills(order, (fill,), now=now)
    assert replay.version == first.version

    with pytest.raises(ValueError, match="conflicting applied fill identity"):
        store.apply_fills(
            order,
            (replace(fill, price=Decimal("101")),),
            now=now,
        )


def test_sqlite_projection_duplicate_is_idempotent_but_conflict_fails(
    tmp_path, empty_portfolio, market_buy_intent, now
):
    store = SQLiteFillAwarePortfolioStore(SQLiteRuntime(tmp_path / "projection.db"))
    store.initialize(empty_portfolio, now=now)
    order, fill = order_and_fill(market_buy_intent, now)
    first = store.apply_fills(order, (fill,), now=now)
    replay = store.apply_fills(order, (fill,), now=now)
    assert replay.version == first.version

    with pytest.raises(FillIntegrityConflict):
        store.apply_fills(
            order,
            (replace(fill, quantity=Decimal("5")),),
            now=now,
        )
