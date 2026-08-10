from datetime import timedelta
from decimal import Decimal
import json

import pytest

from autotrade.research.bootstrap import (
    InvalidBootstrapConfig,
    MovingBlockBootstrapConfig,
)
from autotrade.research.dsl import InvalidStrategySpec, StrategySpec
from autotrade.research.gates import SampleAdequacyPolicy
from autotrade.research.validation import SQLiteValidationRegistry, ValidationEvidenceSpec


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"iterations": 0, "block_size": 1, "seed": 1}, "iterations"),
        ({"iterations": 1, "block_size": 0, "seed": 1}, "block_size"),
        (
            {"iterations": 1, "block_size": 1, "seed": 1, "confidence_level": 0},
            "confidence_level",
        ),
        (
            {"iterations": 1, "block_size": 1, "seed": 1, "confidence_level": 1},
            "confidence_level",
        ),
    ],
)
def test_bootstrap_config_rejects_invalid_boundaries(kwargs, message):
    with pytest.raises(InvalidBootstrapConfig, match=message):
        MovingBlockBootstrapConfig(**kwargs)


def valid_payload():
    return {
        "strategy_id": "safe",
        "strategy_version": "1",
        "kind": "moving_average_cross",
        "parameters": {
            "short_window": 2,
            "long_window": 3,
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "initial_stop_pct": "0.02",
    }


def test_dsl_rejects_invalid_json_root_and_parameter_container():
    with pytest.raises(InvalidStrategySpec, match="valid JSON"):
        StrategySpec.from_json("{")
    with pytest.raises(InvalidStrategySpec, match="root must be an object"):
        StrategySpec.from_json("[]")

    payload = valid_payload()
    payload["parameters"] = []
    with pytest.raises(InvalidStrategySpec, match="parameters must be an object"):
        StrategySpec.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda p: p.update(strategy_id=""), "strategy_id"),
        (lambda p: p.update(strategy_version=""), "strategy_version"),
        (lambda p: p.update(initial_stop_pct="0"), "initial_stop_pct"),
        (lambda p: p.update(initial_stop_pct="NaN"), "finite"),
        (lambda p: p["parameters"].update(short_window=True), "short_window"),
        (lambda p: p["parameters"].update(long_window=1), "long_window"),
        (lambda p: p["parameters"].update(order_quantity="0"), "order_quantity"),
        (lambda p: p["parameters"].update(order_quantity="NaN"), "finite"),
        (lambda p: p["parameters"].update(position_mode="arbitrary"), "position_mode"),
    ],
)
def test_dsl_rejects_invalid_semantics(mutate, message):
    payload = valid_payload()
    mutate(payload)
    with pytest.raises(InvalidStrategySpec, match=message):
        StrategySpec.from_json(json.dumps(payload))


def test_dsl_rejects_missing_and_unknown_parameter_sets():
    payload = valid_payload()
    payload["parameters"].pop("order_quantity")
    with pytest.raises(InvalidStrategySpec, match="missing strategy parameters"):
        StrategySpec.from_json(json.dumps(payload))

    payload = valid_payload()
    payload["parameters"]["extra"] = 1
    with pytest.raises(InvalidStrategySpec, match="unknown strategy parameters"):
        StrategySpec.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    "kwargs,message",
    [
        (
            dict(min_bars=0, min_fills=0, min_unique_days=1, max_rejected_signal_fraction=1),
            "min_bars",
        ),
        (
            dict(min_bars=1, min_fills=-1, min_unique_days=1, max_rejected_signal_fraction=1),
            "min_fills",
        ),
        (
            dict(min_bars=1, min_fills=0, min_unique_days=0, max_rejected_signal_fraction=1),
            "min_unique_days",
        ),
        (
            dict(min_bars=1, min_fills=0, min_unique_days=1, max_rejected_signal_fraction=1.1),
            "max_rejected_signal_fraction",
        ),
        (
            dict(
                min_bars=1,
                min_fills=0,
                min_unique_days=1,
                max_rejected_signal_fraction=1,
                max_gap_count=-1,
            ),
            "max_gap_count",
        ),
    ],
)
def test_sample_adequacy_policy_rejects_invalid_thresholds(kwargs, message):
    with pytest.raises(ValueError, match=message):
        SampleAdequacyPolicy(**kwargs)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        (
            dict(
                strategy_fingerprint="",
                dataset_hashes=("a",),
                policy_hash="p",
                stage="development",
                code_version="c",
            ),
            "strategy_fingerprint",
        ),
        (
            dict(
                strategy_fingerprint="s",
                dataset_hashes=(),
                policy_hash="p",
                stage="development",
                code_version="c",
            ),
            "dataset_hashes",
        ),
        (
            dict(
                strategy_fingerprint="s",
                dataset_hashes=("a", "a"),
                policy_hash="p",
                stage="development",
                code_version="c",
            ),
            "unique",
        ),
        (
            dict(
                strategy_fingerprint="s",
                dataset_hashes=("a",),
                policy_hash="",
                stage="development",
                code_version="c",
            ),
            "policy_hash",
        ),
        (
            dict(
                strategy_fingerprint="s",
                dataset_hashes=("a",),
                policy_hash="p",
                stage="tuning_holdout",
                code_version="c",
            ),
            "stage",
        ),
        (
            dict(
                strategy_fingerprint="s",
                dataset_hashes=("a",),
                policy_hash="p",
                stage="development",
                code_version="",
            ),
            "code_version",
        ),
    ],
)
def test_validation_spec_rejects_incomplete_identity(kwargs, message):
    with pytest.raises(ValueError, match=message):
        ValidationEvidenceSpec(**kwargs)


def test_validation_registry_persists_across_process_like_reopen(tmp_path, now):
    path = tmp_path / "evidence.db"
    spec = ValidationEvidenceSpec(
        strategy_fingerprint="s",
        dataset_hashes=("a",),
        policy_hash="p",
        stage="final_holdout",
        code_version="c",
    )
    first = SQLiteValidationRegistry(path).record(
        spec=spec,
        passed=True,
        reason_codes=(),
        decision_payload={"value": 1},
        now=now,
    )
    reopened = SQLiteValidationRegistry(path)
    assert reopened.get(first.evidence_id) == first

    with pytest.raises(ValueError):
        reopened.record(
            spec=ValidationEvidenceSpec(
                strategy_fingerprint="other",
                dataset_hashes=("b",),
                policy_hash="p",
                stage="development",
                code_version="c",
            ),
            passed=True,
            reason_codes=(),
            decision_payload={"bad": float("nan")},
            now=now + timedelta(seconds=1),
        )


def test_validation_registry_rejects_duplicate_reason_codes(tmp_path, now):
    registry = SQLiteValidationRegistry(tmp_path / "evidence.db")
    spec = ValidationEvidenceSpec(
        strategy_fingerprint="s",
        dataset_hashes=("a",),
        policy_hash="p",
        stage="development",
        code_version="c",
    )
    with pytest.raises(ValueError, match="unique"):
        registry.record(
            spec=spec,
            passed=False,
            reason_codes=("X", "X"),
            decision_payload={},
            now=now,
        )
