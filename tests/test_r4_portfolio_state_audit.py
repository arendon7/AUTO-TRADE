from dataclasses import replace
from decimal import Decimal

import pytest

from autotrade.portfolio_integrity import (
    PortfolioIntegrityError,
    portfolio_snapshot_error,
    validate_portfolio_snapshot,
)
from autotrade.state import InMemoryPortfolioStore


def test_semantic_integrity_requires_strategy_maps_to_sum_to_aggregate(empty_portfolio):
    invalid = replace(
        empty_portfolio,
        gross_exposure=Decimal("100"),
        net_exposure=Decimal("100"),
        signed_position_notional_by_symbol={"TEST-USD": Decimal("100")},
        strategy_gross_exposure={},
        strategy_signed_position_notional_by_symbol={},
    )
    assert "aggregate position map does not equal" in portfolio_snapshot_error(invalid)
    with pytest.raises(PortfolioIntegrityError, match="aggregate position map does not equal"):
        validate_portfolio_snapshot(invalid)


def test_inmemory_store_rejects_invalid_initial_snapshot(now, empty_portfolio):
    store = InMemoryPortfolioStore()
    invalid = replace(empty_portfolio, gross_exposure=Decimal("1"))
    with pytest.raises(PortfolioIntegrityError, match="gross_exposure does not match"):
        store.initialize(invalid, now=now)


def test_inmemory_store_detaches_mutable_input_snapshot(now, empty_portfolio):
    positions: dict[str, Decimal] = {}
    strategy_positions: dict[str, dict[str, Decimal]] = {}
    initial = replace(
        empty_portfolio,
        signed_position_notional_by_symbol=positions,
        strategy_signed_position_notional_by_symbol=strategy_positions,
    )
    store = InMemoryPortfolioStore()
    store.initialize(initial, now=now)

    positions["ATTACK-USD"] = Decimal("999")
    strategy_positions["attacker"] = {"ATTACK-USD": Decimal("999")}

    current = store.get()
    assert current.version == 1
    assert current.snapshot.signed_position_notional_by_symbol == {}
    assert current.snapshot.strategy_signed_position_notional_by_symbol == {}


def test_inmemory_store_returns_detached_snapshot_not_live_internal_alias(now, empty_portfolio):
    store = InMemoryPortfolioStore()
    store.initialize(empty_portfolio, now=now)

    returned = store.get()
    returned.snapshot.signed_position_notional_by_symbol["ATTACK-USD"] = Decimal("999")
    returned.snapshot.strategy_gross_exposure["attacker"] = Decimal("999")
    returned.snapshot.strategy_signed_position_notional_by_symbol["attacker"] = {
        "ATTACK-USD": Decimal("999")
    }

    second = store.get()
    assert second.version == 1
    assert second.snapshot.signed_position_notional_by_symbol == {}
    assert second.snapshot.strategy_gross_exposure == {}
    assert second.snapshot.strategy_signed_position_notional_by_symbol == {}


def test_inmemory_cas_rejects_invalid_candidate_without_version_change(now, empty_portfolio):
    store = InMemoryPortfolioStore()
    initial = store.initialize(empty_portfolio, now=now)
    invalid = replace(empty_portfolio, net_exposure=Decimal("1"))
    with pytest.raises(PortfolioIntegrityError, match="net_exposure does not match"):
        store.compare_and_set(
            expected_version=initial.version,
            snapshot=invalid,
            now=now,
        )
    assert store.get() == initial


def test_inmemory_reconciliation_status_requires_real_booleans(now, empty_portfolio):
    store = InMemoryPortfolioStore()
    store.initialize(empty_portfolio, now=now)
    with pytest.raises(PortfolioIntegrityError, match="flags must be boolean"):
        store.set_reconciliation_status(
            reconciliation_ok=1,  # type: ignore[arg-type]
            broker_state_known=True,
            now=now,
        )


def test_inmemory_versions_change_only_on_real_state_change(now, empty_portfolio):
    store = InMemoryPortfolioStore()
    first = store.initialize(empty_portfolio, now=now)
    same = store.set_reconciliation_status(
        reconciliation_ok=True,
        broker_state_known=True,
        now=now,
    )
    assert same.version == first.version

    changed = store.set_reconciliation_status(
        reconciliation_ok=False,
        broker_state_known=False,
        now=now,
    )
    assert changed.version == first.version + 1
    replay = store.set_reconciliation_status(
        reconciliation_ok=False,
        broker_state_known=False,
        now=now,
    )
    assert replay.version == changed.version


def test_inmemory_initialize_is_idempotent_but_does_not_replace_existing_state(now, empty_portfolio):
    store = InMemoryPortfolioStore()
    first = store.initialize(empty_portfolio, now=now)
    alternate = replace(empty_portfolio, snapshot_id="alternate-snapshot")
    second = store.initialize(alternate, now=now)
    assert second == first
    assert store.get() == first
