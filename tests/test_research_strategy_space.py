from __future__ import annotations

import pytest

from autotrade.research.strategy_space import (
    StrategyProgram,
    StrategySearchSpace,
    StrategySpaceError,
)


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


def _breakout_space() -> StrategySearchSpace:
    return StrategySearchSpace(
        family_id="donchian-hourly",
        strategy_version="1.0.0",
        kind="donchian_breakout",
        dimensions={
            "lookback_bars": (24, 48),
            "order_quantity": ("1",),
            "position_mode": ("long_short",),
        },
        max_candidates=4,
    )


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
    assert first.canonical_hash == second.canonical_hash
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


def test_program_freezes_multiple_families_and_trial_ids() -> None:
    first = StrategyProgram(
        program_id="r7-core",
        spaces=(_momentum_space(), _breakout_space()),
        max_total_candidates=16,
    )
    second = StrategyProgram(
        program_id="r7-core",
        spaces=(_breakout_space(), _momentum_space()),
        max_total_candidates=16,
    )

    assert first.candidate_count == 6
    assert len(first.expected_trial_ids) == 6
    assert first.canonical_hash == second.canonical_hash
    assert first.expected_trial_ids == second.expected_trial_ids
    assert len(set(first.expected_trial_ids)) == 6


def test_program_rejects_duplicate_families_and_grid_explosion() -> None:
    with pytest.raises(StrategySpaceError, match="family_id values must be unique"):
        StrategyProgram(
            program_id="bad",
            spaces=(_momentum_space(), _momentum_space()),
        )

    with pytest.raises(StrategySpaceError, match="exceeds max_total_candidates"):
        StrategyProgram(
            program_id="too-large",
            spaces=(_momentum_space(), _breakout_space()),
            max_total_candidates=5,
        )


def test_program_rejects_candidate_outside_frozen_universe() -> None:
    program = StrategyProgram(
        program_id="r7-core",
        spaces=(_momentum_space(),),
    )
    outsider = _breakout_space().candidates()[0]

    with pytest.raises(StrategySpaceError, match="outside frozen"):
        program.trial_id_for(outsider)
