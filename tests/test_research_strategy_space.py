from __future__ import annotations

import pytest

from autotrade.research.strategy_space import StrategySearchSpace, StrategySpaceError


def _momentum_space(**overrides: object) -> StrategySearchSpace:
    payload: dict[str, object] = {
        "family_id": "tsmom-hourly",
        "strategy_version": "1.0.0",
        "kind": "time_series_momentum",
        "dimensions": {
            "lookback_bars": (24, 48),
            "order_quantity": ("1",),
            "entry_threshold": ("0", "0.01"),
            "position_mode": ("long_short",),
        },
        "max_candidates": 8,
    }
    payload.update(overrides)
    return StrategySearchSpace(**payload)  # type: ignore[arg-type]


def test_space_generates_complete_deterministic_family() -> None:
    first = _momentum_space()
    second = _momentum_space(
        dimensions={
            "position_mode": ("long_short",),
            "entry_threshold": ("0.01", "0"),
            "order_quantity": ("1",),
            "lookback_bars": (48, 24),
        }
    )

    first_candidates = first.candidates()
    second_candidates = second.candidates()

    assert first.candidate_count == 4
    assert len(first_candidates) == 4
    assert [item.strategy_id for item in first_candidates] == [
        item.strategy_id for item in second_candidates
    ]
    assert [item.canonical_hash for item in first_candidates] == [
        item.canonical_hash for item in second_candidates
    ]


def test_space_candidate_ids_change_when_parameters_change() -> None:
    base = _momentum_space()
    changed = _momentum_space(
        dimensions={
            "lookback_bars": (24, 96),
            "order_quantity": ("1",),
            "entry_threshold": ("0", "0.01"),
            "position_mode": ("long_short",),
        }
    )

    assert {item.strategy_id for item in base.candidates()} != {
        item.strategy_id for item in changed.candidates()
    }


def test_space_rejects_grid_explosion_before_campaign_creation() -> None:
    with pytest.raises(StrategySpaceError, match="exceeds max_candidates"):
        _momentum_space(
            dimensions={
                "lookback_bars": tuple(range(1, 21)),
                "order_quantity": ("1", "2"),
                "entry_threshold": ("0", "0.01"),
                "position_mode": ("long_short", "long_flat"),
            },
            max_candidates=64,
        )


def test_space_rejects_unknown_or_incomplete_parameter_dimensions() -> None:
    with pytest.raises(StrategySpaceError, match="invalid candidate"):
        StrategySearchSpace(
            family_id="bad",
            strategy_version="1",
            kind="time_series_momentum",
            dimensions={
                "lookback_bars": (24,),
                "order_quantity": ("1",),
                "position_mode": ("long_short",),
            },
        )

    with pytest.raises(StrategySpaceError, match="invalid candidate"):
        StrategySearchSpace(
            family_id="bad",
            strategy_version="1",
            kind="time_series_momentum",
            dimensions={
                "lookback_bars": (24,),
                "order_quantity": ("1",),
                "entry_threshold": ("0",),
                "position_mode": ("long_short",),
                "broker": ("binance",),
            },
        )


def test_space_rejects_duplicate_values_and_invalid_shapes() -> None:
    with pytest.raises(StrategySpaceError, match="duplicate"):
        _momentum_space(
            dimensions={
                "lookback_bars": (24, 24),
                "order_quantity": ("1",),
                "entry_threshold": ("0",),
                "position_mode": ("long_short",),
            }
        )
    with pytest.raises(StrategySpaceError, match="dimensions cannot be empty"):
        _momentum_space(dimensions={})
    with pytest.raises(StrategySpaceError, match="max_candidates"):
        _momentum_space(max_candidates=0)


def test_space_hash_captures_search_governance() -> None:
    base = _momentum_space(max_candidates=8)
    changed_limit = _momentum_space(max_candidates=16)

    assert base.canonical_hash != changed_limit.canonical_hash
