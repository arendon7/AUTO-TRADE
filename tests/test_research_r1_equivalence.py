from datetime import timedelta
from decimal import Decimal
import json

import pytest

from autotrade.research.backtest import BacktestConfig, BacktestEngine
from autotrade.research.bootstrap import (
    InvalidBootstrapConfig,
    MovingBlockBootstrapConfig,
    moving_block_bootstrap,
)
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.dsl import InvalidStrategySpec, StrategySpec
from autotrade.research.gates import (
    SampleAdequacyPolicy,
    evaluate_sample_adequacy,
)
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.validation import (
    SQLiteValidationRegistry,
    ValidationEvidenceConflict,
    ValidationEvidenceSpec,
)


def make_cross_dataset(now):
    instrument = InstrumentMetadata(
        symbol="TEST-USD",
        venue="TEST",
        quote_currency="USD",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("1"),
    )
    closes = ["100", "100", "100", "102", "104", "98", "97"]
    bars = []
    for index, raw in enumerate(closes):
        close = Decimal(raw)
        bars.append(
            Bar(
                symbol="TEST-USD",
                started_at=now + timedelta(minutes=index),
                timeframe_seconds=60,
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("10000"),
            )
        )
    return MarketDataset(
        instrument=instrument,
        bars=tuple(bars),
        source="r1-equivalence-fixture",
    )


def strategy_spec_json(*, reordered=False):
    payload = {
        "strategy_id": "ma-cross-r1",
        "strategy_version": "1.0.0",
        "kind": "moving_average_cross",
        "parameters": {
            "short_window": 2,
            "long_window": 3,
            "order_quantity": "5",
            "position_mode": "long_flat",
        },
        "initial_stop_pct": "0.03",
    }
    if not reordered:
        return json.dumps(payload)
    return json.dumps(
        {
            "initial_stop_pct": payload["initial_stop_pct"],
            "parameters": {
                "position_mode": "long_flat",
                "order_quantity": "5",
                "long_window": 3,
                "short_window": 2,
            },
            "kind": payload["kind"],
            "strategy_version": payload["strategy_version"],
            "strategy_id": payload["strategy_id"],
        }
    )


def backtest_config():
    return BacktestConfig(
        initial_cash=Decimal("100000"),
        cost_model=ExecutionCostModel(
            fee_bps=Decimal("1"),
            half_spread_bps=Decimal("1"),
            slippage_bps=Decimal("1"),
        ),
        execution_delay_bars=1,
        annualization_factor=Decimal("252"),
        max_leverage=Decimal("2"),
        max_volume_participation=Decimal("0.1"),
        allow_short=False,
    )


def test_safe_dsl_has_canonical_hash_and_no_dynamic_code_surface():
    first = StrategySpec.from_json(strategy_spec_json())
    second = StrategySpec.from_json(strategy_spec_json(reordered=True))

    assert first.canonical_hash == second.canonical_hash
    assert first.build().strategy_id == "ma-cross-r1"
    assert first.initial_stop_pct == Decimal("0.03")
    assert not any(
        token in first.canonical_payload
        for token in ("module", "callable", "broker", "network", "oms", "import")
    )

    injected = json.loads(strategy_spec_json())
    injected["parameters"]["__import__"] = "os"
    with pytest.raises(InvalidStrategySpec, match="unknown strategy parameters"):
        StrategySpec.from_json(json.dumps(injected))

    top_level_injected = json.loads(strategy_spec_json())
    top_level_injected["callable"] = "os.system"
    with pytest.raises(InvalidStrategySpec, match="unknown top-level fields"):
        StrategySpec.from_json(json.dumps(top_level_injected))


def test_dsl_backtest_is_future_bar_only_and_stop_is_metadata_not_broker_claim(now):
    dataset = make_cross_dataset(now)
    strategy = StrategySpec.from_json(strategy_spec_json()).build()
    result = BacktestEngine().run(
        dataset=dataset,
        strategy=strategy,
        config=backtest_config(),
    )

    assert [fill.bar_index for fill in result.fills] == [4, 6]
    assert result.fills[0].occurred_at >= dataset.bars[3].ended_at
    assert result.fills[1].occurred_at >= dataset.bars[5].ended_at
    assert strategy.parameters["initial_stop_pct"] == "0.03"
    assert "broker" not in strategy.on_bar.__qualname__.lower()


def test_dsl_rejects_missing_stop_bad_windows_and_unknown_kind():
    payload = json.loads(strategy_spec_json())
    payload.pop("initial_stop_pct")
    with pytest.raises(InvalidStrategySpec, match="missing top-level fields"):
        StrategySpec.from_json(json.dumps(payload))

    payload = json.loads(strategy_spec_json())
    payload["parameters"]["short_window"] = 3
    payload["parameters"]["long_window"] = 3
    with pytest.raises(InvalidStrategySpec, match="short_window must be < long_window"):
        StrategySpec.from_json(json.dumps(payload))

    payload = json.loads(strategy_spec_json())
    payload["kind"] = "python_eval"
    with pytest.raises(InvalidStrategySpec, match="unsupported strategy kind"):
        StrategySpec.from_json(json.dumps(payload))


def test_moving_block_bootstrap_is_reproducible_and_preserves_block_dependence():
    returns = (0.01, 0.02, -0.01, 0.03, -0.02, 0.015)
    config = MovingBlockBootstrapConfig(
        iterations=200,
        block_size=2,
        seed=42,
        confidence_level=0.90,
    )
    first = moving_block_bootstrap(returns, config=config)
    second = moving_block_bootstrap(returns, config=config)
    different_seed = moving_block_bootstrap(
        returns,
        config=MovingBlockBootstrapConfig(
            iterations=200,
            block_size=2,
            seed=43,
            confidence_level=0.90,
        ),
    )

    assert first == second
    assert first.distribution != different_seed.distribution
    assert first.observations == len(returns)
    assert 0 <= first.probability_positive <= 1
    assert first.lower_compounded_return <= first.median_compounded_return <= first.upper_compounded_return


@pytest.mark.parametrize(
    "returns,config,error",
    [
        ((), MovingBlockBootstrapConfig(iterations=10, block_size=1, seed=1), "cannot be empty"),
        ((0.1,), MovingBlockBootstrapConfig(iterations=10, block_size=2, seed=1), "cannot exceed"),
        ((float("nan"),), MovingBlockBootstrapConfig(iterations=10, block_size=1, seed=1), "finite"),
        ((-1.0,), MovingBlockBootstrapConfig(iterations=10, block_size=1, seed=1), "greater than -1"),
    ],
)
def test_bootstrap_fails_closed_on_invalid_inputs(returns, config, error):
    with pytest.raises(InvalidBootstrapConfig, match=error):
        moving_block_bootstrap(returns, config=config)


def test_sample_adequacy_gate_is_explicit_and_dataset_bound(now):
    dataset = make_cross_dataset(now)
    result = BacktestEngine().run(
        dataset=dataset,
        strategy=StrategySpec.from_json(strategy_spec_json()).build(),
        config=backtest_config(),
    )
    passing = evaluate_sample_adequacy(
        result=result,
        dataset=dataset,
        policy=SampleAdequacyPolicy(
            min_bars=7,
            min_fills=2,
            min_unique_days=1,
            max_rejected_signal_fraction=0,
            max_gap_count=0,
        ),
    )
    failing = evaluate_sample_adequacy(
        result=result,
        dataset=dataset,
        policy=SampleAdequacyPolicy(
            min_bars=100,
            min_fills=10,
            min_unique_days=2,
            max_rejected_signal_fraction=0,
            max_gap_count=0,
        ),
    )

    assert passing.passed
    assert not failing.passed
    assert set(failing.reason_codes) >= {
        "INSUFFICIENT_BARS",
        "INSUFFICIENT_FILLS",
        "INSUFFICIENT_UNIQUE_DAYS",
    }


def test_validation_registry_is_idempotent_and_detects_nondeterministic_evidence(tmp_path, now):
    registry = SQLiteValidationRegistry(tmp_path / "validation.db")
    spec = ValidationEvidenceSpec(
        strategy_fingerprint="strategy-hash",
        dataset_hashes=("dataset-a", "dataset-b"),
        policy_hash="policy-hash",
        stage="development",
        code_version="r1-test-sha",
    )
    first = registry.record(
        spec=spec,
        passed=True,
        reason_codes=(),
        decision_payload={"sample": {"passed": True}, "robustness": {"passed": True}},
        now=now,
    )
    repeated = registry.record(
        spec=spec,
        passed=True,
        reason_codes=(),
        decision_payload={"sample": {"passed": True}, "robustness": {"passed": True}},
        now=now + timedelta(seconds=1),
    )

    assert repeated.evidence_id == first.evidence_id
    assert registry.get(first.evidence_id) == first

    with pytest.raises(ValidationEvidenceConflict, match="different evidence"):
        registry.record(
            spec=spec,
            passed=False,
            reason_codes=("ROBUSTNESS_FAILED",),
            decision_payload={"sample": {"passed": True}, "robustness": {"passed": False}},
            now=now + timedelta(seconds=2),
        )


def test_validation_registry_rejects_incoherent_evidence(tmp_path, now):
    registry = SQLiteValidationRegistry(tmp_path / "validation.db")
    spec = ValidationEvidenceSpec(
        strategy_fingerprint="strategy-hash",
        dataset_hashes=("dataset-a",),
        policy_hash="policy-hash",
        stage="development",
        code_version="r1-test-sha",
    )
    with pytest.raises(ValueError, match="passed validation"):
        registry.record(
            spec=spec,
            passed=True,
            reason_codes=("SHOULD_NOT_EXIST",),
            decision_payload={},
            now=now,
        )
    with pytest.raises(ValueError, match="failed validation"):
        registry.record(
            spec=spec,
            passed=False,
            reason_codes=(),
            decision_payload={},
            now=now,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        registry.record(
            spec=spec,
            passed=True,
            reason_codes=(),
            decision_payload={},
            now=now.replace(tzinfo=None),
        )
