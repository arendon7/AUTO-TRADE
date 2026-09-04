from __future__ import annotations

import json

import pytest

from autotrade.research.strategy_catalog import (
    InvalidLibraryStrategySpec,
    LibraryStrategySpec,
)
from autotrade.research.strategy_library import (
    DonchianBreakoutStrategy,
    MeanReversionZScoreStrategy,
    TimeSeriesMomentumStrategy,
    VolatilityManagedMomentumStrategy,
)


def _payload(kind: str) -> dict[str, object]:
    parameters: dict[str, dict[str, object]] = {
        "time_series_momentum": {
            "lookback_bars": 96,
            "order_quantity": "1",
            "entry_threshold": "0.01",
            "position_mode": "long_short",
        },
        "donchian_breakout": {
            "lookback_bars": 48,
            "order_quantity": "1",
            "position_mode": "long_short",
        },
        "mean_reversion_zscore": {
            "lookback_bars": 48,
            "entry_z": "2",
            "exit_z": "0.5",
            "order_quantity": "1",
            "position_mode": "long_short",
        },
        "volatility_managed_momentum": {
            "momentum_lookback_bars": 96,
            "volatility_window_bars": 48,
            "base_quantity": "1",
            "target_bar_volatility": "0.01",
            "min_scale": "0.1",
            "max_scale": "2",
            "entry_threshold": "0",
            "position_mode": "long_short",
        },
    }
    return {
        "strategy_id": f"candidate-{kind}",
        "strategy_version": "1.0.0",
        "kind": kind,
        "parameters": parameters[kind],
    }


@pytest.mark.parametrize(
    ("kind", "expected_type"),
    [
        ("time_series_momentum", TimeSeriesMomentumStrategy),
        ("donchian_breakout", DonchianBreakoutStrategy),
        ("mean_reversion_zscore", MeanReversionZScoreStrategy),
        ("volatility_managed_momentum", VolatilityManagedMomentumStrategy),
    ],
)
def test_catalog_builds_only_audited_strategy_types(kind: str, expected_type: type) -> None:
    spec = LibraryStrategySpec.from_json(json.dumps(_payload(kind)))

    strategy = spec.build()

    assert isinstance(strategy, expected_type)
    assert strategy.strategy_id == f"candidate-{kind}"
    assert strategy.strategy_version == "1.0.0"


def test_canonical_hash_is_independent_of_json_key_order() -> None:
    payload = _payload("time_series_momentum")
    first = LibraryStrategySpec.from_json(json.dumps(payload))
    reordered = {
        "parameters": payload["parameters"],
        "kind": payload["kind"],
        "strategy_version": payload["strategy_version"],
        "strategy_id": payload["strategy_id"],
    }
    second = LibraryStrategySpec.from_json(json.dumps(reordered))

    assert first.canonical_hash == second.canonical_hash


def test_catalog_rejects_dynamic_code_and_control_plane_fields() -> None:
    payload = _payload("time_series_momentum")
    payload["callable"] = "evil.module:run"

    with pytest.raises(InvalidLibraryStrategySpec, match="unknown top-level"):
        LibraryStrategySpec.from_json(json.dumps(payload))

    for forbidden in ("broker", "oms", "url", "command", "import_path"):
        payload = _payload("time_series_momentum")
        parameters = dict(payload["parameters"])
        parameters[forbidden] = "forbidden"
        payload["parameters"] = parameters
        with pytest.raises(InvalidLibraryStrategySpec, match="unknown parameters"):
            LibraryStrategySpec.from_json(json.dumps(payload))


def test_catalog_rejects_unknown_kind_missing_and_unknown_parameters() -> None:
    payload = _payload("time_series_momentum")
    payload["kind"] = "arbitrary_python"
    with pytest.raises(InvalidLibraryStrategySpec, match="unsupported strategy kind"):
        LibraryStrategySpec.from_json(json.dumps(payload))

    payload = _payload("donchian_breakout")
    del payload["parameters"]["order_quantity"]  # type: ignore[index]
    with pytest.raises(InvalidLibraryStrategySpec, match="missing parameters"):
        LibraryStrategySpec.from_json(json.dumps(payload))

    payload = _payload("donchian_breakout")
    payload["parameters"]["magic"] = 123  # type: ignore[index]
    with pytest.raises(InvalidLibraryStrategySpec, match="unknown parameters"):
        LibraryStrategySpec.from_json(json.dumps(payload))


def test_catalog_rejects_invalid_types_and_strategy_constraints() -> None:
    payload = _payload("time_series_momentum")
    payload["parameters"]["lookback_bars"] = True  # type: ignore[index]
    with pytest.raises(InvalidLibraryStrategySpec, match="lookback_bars"):
        LibraryStrategySpec.from_json(json.dumps(payload))

    payload = _payload("mean_reversion_zscore")
    payload["parameters"]["exit_z"] = "3"  # type: ignore[index]
    with pytest.raises(InvalidLibraryStrategySpec, match="exit_z"):
        LibraryStrategySpec.from_json(json.dumps(payload))

    payload = _payload("volatility_managed_momentum")
    payload["parameters"]["min_scale"] = "3"  # type: ignore[index]
    with pytest.raises(InvalidLibraryStrategySpec, match="min_scale"):
        LibraryStrategySpec.from_json(json.dumps(payload))


def test_catalog_rejects_invalid_json_shape() -> None:
    with pytest.raises(InvalidLibraryStrategySpec, match="valid JSON"):
        LibraryStrategySpec.from_json("{")
    with pytest.raises(InvalidLibraryStrategySpec, match="root must be an object"):
        LibraryStrategySpec.from_json("[]")
    with pytest.raises(InvalidLibraryStrategySpec, match="parameters must be an object"):
        LibraryStrategySpec.from_json(
            json.dumps(
                {
                    "strategy_id": "x",
                    "strategy_version": "1",
                    "kind": "time_series_momentum",
                    "parameters": [],
                }
            )
        )
