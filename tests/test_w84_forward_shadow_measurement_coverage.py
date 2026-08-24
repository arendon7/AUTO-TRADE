from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

import autotrade.forward_shadow_measurement as measurement
from autotrade.forward_shadow_measurement import (
    FORWARD_MEASUREMENT_PLAN_VERSION,
    FORWARD_MEASUREMENT_RECEIPT_VERSION,
    FORWARD_MEASUREMENT_RUNTIME_VERSION,
    ForwardMeasurementPlan,
    ForwardMeasurementRuntimeIdentity,
    ForwardShadowMeasurementIntegrityError,
    ForwardShadowMeasurementReceipt,
    build_forward_measurement_plan,
    build_forward_measurement_runtime_identity,
    build_forward_shadow_measurements,
    verify_shadow_measurement_binding,
)
from autotrade.research.market import MarketDataset
from test_w84_forward_shadow_measurement import (
    _measurement_inputs,
    _plan,
    _receipts,
    _w83,
)


def _baseline(limits, market, empty_portfolio, market_buy_intent):
    chain, binding, resolution = _w83(
        limits, market, empty_portfolio, market_buy_intent
    )
    planned_at, activation, history, post_freeze, config = _measurement_inputs(
        chain, resolution
    )
    plan = _plan(
        chain, binding, resolution, history, config, planned_at, activation
    )
    receipts = _receipts(
        chain, binding, resolution, plan, history, post_freeze, config
    )
    return (
        chain,
        binding,
        resolution,
        planned_at,
        activation,
        history,
        post_freeze,
        config,
        plan,
        receipts,
    )


def _forge_dataclass(cls, value, **changes):
    forged = object.__new__(cls)
    for field in fields(cls):
        object.__setattr__(
            forged,
            field.name,
            changes.get(field.name, getattr(value, field.name)),
        )
    return forged


def _valid_receipt_with(receipt: ForwardShadowMeasurementReceipt, **changes):
    base_names = (
        "contract_version",
        "plan_id",
        "plan_hash",
        "policy_hash",
        "ordinal",
        "selected_strategy_id",
        "selected_strategy_version",
        "strategy_spec_hash",
        "w83_runtime_hash",
        "measurement_runtime_hash",
        "backtest_config_hash",
        "dataset_source",
        "prefix_dataset_hash",
        "prefix_result_hash",
        "period_started_at",
        "period_ended_at",
        "equity_before",
        "equity_after",
        "return_fraction",
        "previous_measurement_hash",
        "paper_candidate_authorized",
        "external_execution_authorized",
        "runtime_execution_authorized",
        "capital_authority",
        "live_trading",
    )
    values = {name: getattr(receipt, name) for name in base_names}
    values.update({k: v for k, v in changes.items() if k in values})
    measurement_hash = measurement._hash(
        measurement._measurement_payload_from_values(values)
    )
    captured_at = changes.get("captured_at", receipt.captured_at)
    receipt_values = {
        **values,
        "measurement_hash": measurement_hash,
        "captured_at": captured_at,
    }
    return ForwardShadowMeasurementReceipt(
        **receipt_values,
        receipt_hash=measurement._hash(
            measurement._measurement_payload_from_values(
                receipt_values,
                include_capture=True,
            )
        ),
    )


def test_w84_runtime_identity_constructor_fail_closed_branches():
    runtime = build_forward_measurement_runtime_identity()
    assert runtime.to_dict()["identity_hash"] == runtime.identity_hash

    cases = (
        {"version": "wrong"},
        {"python_runtime": "python-3.12"},
        {"w83_runtime_hash": "not-a-hash"},
        {"backtest_source_hash": "not-a-hash"},
        {"costs_source_hash": "not-a-hash"},
        {"domain_source_hash": "not-a-hash"},
        {"identity_hash": "a" * 64},
    )
    for changes in cases:
        with pytest.raises(ForwardShadowMeasurementIntegrityError):
            replace(runtime, **changes)


def test_w84_plan_constructor_fail_closed_branches(
    limits, market, empty_portfolio, market_buy_intent
):
    *_, plan, _ = _baseline(
        limits, market, empty_portfolio, market_buy_intent
    )
    assert plan.contract_version == FORWARD_MEASUREMENT_PLAN_VERSION
    assert plan.to_dict()["plan_hash"] == plan.plan_hash

    cases = (
        {"contract_version": "wrong"},
        {"measurement_runtime_version": "wrong"},
        {"runtime_python": "python-3.12"},
        {"dataset_source": ""},
        {"timeframe_seconds": 0},
        {"timeframe_seconds": True},
        {"history_bars": 0},
        {"history_bars": True},
        {"planned_at": plan.forward_activated_at},
        {"paper_candidate_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"plan_hash": "a" * 64},
    )
    for changes in cases:
        with pytest.raises(ForwardShadowMeasurementIntegrityError):
            replace(plan, **changes)


def test_w84_receipt_constructor_fail_closed_branches(
    limits, market, empty_portfolio, market_buy_intent
):
    *_, receipts = _baseline(
        limits, market, empty_portfolio, market_buy_intent
    )
    receipt = receipts[0]
    assert receipt.contract_version == FORWARD_MEASUREMENT_RECEIPT_VERSION
    assert receipt.to_dict()["receipt_hash"] == receipt.receipt_hash

    cases = (
        {"contract_version": "wrong"},
        {"plan_id": ""},
        {"plan_hash": "not-a-hash"},
        {"ordinal": 0},
        {"ordinal": True},
        {"dataset_source": ""},
        {"period_ended_at": receipt.period_started_at},
        {"captured_at": receipt.period_ended_at - timedelta(seconds=1)},
        {"equity_before": Decimal("0")},
        {"equity_after": Decimal("0")},
        {"return_fraction": Decimal("-1")},
        {"return_fraction": Decimal("0.123456")},
        {"paper_candidate_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"measurement_hash": "a" * 64},
        {"receipt_hash": "a" * 64},
    )
    for changes in cases:
        with pytest.raises(ForwardShadowMeasurementIntegrityError):
            replace(receipt, **changes)


def test_w84_public_builder_type_guards(
    limits, market, empty_portfolio, market_buy_intent
):
    (
        chain,
        binding,
        resolution,
        planned_at,
        activation,
        history,
        post_freeze,
        config,
        plan,
        _,
    ) = _baseline(limits, market, empty_portfolio, market_buy_intent)

    common = dict(
        plan_id="type-guard",
        w83_resolution=resolution,
        binding_evidence=binding,
        strategy_spec=chain["spec"],
        backtest_config=config,
        history_dataset=history,
        planned_at=planned_at,
        forward_activated_at=activation,
    )
    for key in ("strategy_spec", "backtest_config", "history_dataset"):
        values = dict(common)
        values[key] = object()
        with pytest.raises(TypeError):
            build_forward_measurement_plan(**values)

    measurement_args = dict(
        plan=plan,
        policy_hash="a" * 64,
        w83_resolution=resolution,
        binding_evidence=binding,
        strategy_spec=chain["spec"],
        backtest_config=config,
        history_dataset=history,
        post_freeze_dataset=post_freeze,
        captured_at=post_freeze.ended_at + timedelta(seconds=1),
    )
    values = dict(measurement_args)
    values["post_freeze_dataset"] = object()
    with pytest.raises(TypeError):
        build_forward_shadow_measurements(**values)

    with pytest.raises(TypeError):
        measurement._validate_plan(object())
    with pytest.raises(TypeError):
        measurement._validate_receipt(object())
    with pytest.raises(TypeError):
        measurement._validate_w83_pair(
            w83_resolution=object(), binding_evidence=binding
        )
    with pytest.raises(TypeError):
        measurement._validate_w83_pair(
            w83_resolution=resolution, binding_evidence=object()
        )


def test_w84_plan_builder_rejects_gap_alignment_lookback_and_runtime_drift(
    monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    (
        chain,
        binding,
        resolution,
        planned_at,
        activation,
        history,
        _,
        config,
        _,
        _,
    ) = _baseline(limits, market, empty_portfolio, market_buy_intent)

    gap_history = MarketDataset(
        instrument=history.instrument,
        bars=(history.bars[0], history.bars[2]),
        source=history.source,
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="contiguous"):
        build_forward_measurement_plan(
            plan_id="gap",
            w83_resolution=resolution,
            binding_evidence=binding,
            strategy_spec=chain["spec"],
            backtest_config=config,
            history_dataset=gap_history,
            planned_at=gap_history.ended_at,
            forward_activated_at=activation,
        )

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="align"):
        build_forward_measurement_plan(
            plan_id="misaligned",
            w83_resolution=resolution,
            binding_evidence=binding,
            strategy_spec=chain["spec"],
            backtest_config=config,
            history_dataset=history,
            planned_at=planned_at,
            forward_activated_at=activation + timedelta(seconds=30),
        )

    short_history = MarketDataset(
        instrument=history.instrument,
        bars=history.bars[-2:],
        source=history.source,
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="lookback"):
        build_forward_measurement_plan(
            plan_id="lookback",
            w83_resolution=resolution,
            binding_evidence=binding,
            strategy_spec=chain["spec"],
            backtest_config=config,
            history_dataset=short_history,
            planned_at=short_history.ended_at,
            forward_activated_at=activation,
        )

    current = build_forward_measurement_runtime_identity()
    monkeypatch.setattr(
        measurement,
        "build_forward_measurement_runtime_identity",
        lambda: SimpleNamespace(
            version=FORWARD_MEASUREMENT_RUNTIME_VERSION,
            python_runtime=current.python_runtime,
            w83_runtime_hash="a" * 64,
            identity_hash=current.identity_hash,
            backtest_source_hash=current.backtest_source_hash,
            costs_source_hash=current.costs_source_hash,
            domain_source_hash=current.domain_source_hash,
        ),
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="certified W83 runtime"):
        build_forward_measurement_plan(
            plan_id="runtime-drift",
            w83_resolution=resolution,
            binding_evidence=binding,
            strategy_spec=chain["spec"],
            backtest_config=config,
            history_dataset=history,
            planned_at=planned_at,
            forward_activated_at=activation,
        )


def test_w84_measurement_builder_rejects_dataset_and_capture_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    (
        chain,
        binding,
        resolution,
        _,
        _,
        history,
        post_freeze,
        config,
        plan,
        _,
    ) = _baseline(limits, market, empty_portfolio, market_buy_intent)

    common = dict(
        plan=plan,
        policy_hash="a" * 64,
        w83_resolution=resolution,
        binding_evidence=binding,
        strategy_spec=chain["spec"],
        backtest_config=config,
        history_dataset=history,
        captured_at=post_freeze.ended_at + timedelta(seconds=1),
    )

    wrong_source = replace(post_freeze, source="other-source")
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="source"):
        build_forward_shadow_measurements(
            **common, post_freeze_dataset=wrong_source
        )

    shifted = MarketDataset(
        instrument=post_freeze.instrument,
        bars=post_freeze.bars[1:],
        source=post_freeze.source,
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="start exactly"):
        build_forward_shadow_measurements(
            **common, post_freeze_dataset=shifted
        )

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="captured before"):
        build_forward_shadow_measurements(
            **common,
            post_freeze_dataset=post_freeze,
            captured_at=post_freeze.ended_at - timedelta(seconds=1),
        )


def test_w84_binding_rejects_selection_length_capture_and_receipt_identity_drift(
    tmp_path, limits, market, empty_portfolio, market_buy_intent
):
    (
        _,
        _,
        resolution,
        _,
        activation,
        _,
        _,
        _,
        plan,
        receipts,
    ) = _baseline(limits, market, empty_portfolio, market_buy_intent)
    policy_hash = "a" * 64

    from autotrade.research.shadow import FrozenShadowConfig, SQLitePortfolioShadowRegistry

    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(
        FrozenShadowConfig(
            config_id="w84-cov-shadow",
            activated_at=activation,
            initial_nav=Decimal("100000"),
            strategy_weights={resolution.selected_strategy_id: Decimal("1")},
            source_config_hash=policy_hash,
        )
    )
    for receipt in receipts:
        shadow.append_period((receipt.to_shadow_observation(),))
    records = shadow.list_records()
    assessed_at = receipts[-1].captured_at + timedelta(seconds=1)

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="selected strategy"):
        verify_shadow_measurement_binding(
            plan=plan,
            policy_hash=policy_hash,
            selected_strategy_id="other-strategy",
            shadow_records=records,
            receipts=receipts,
            assessed_at=assessed_at,
        )

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="one measurement receipt"):
        verify_shadow_measurement_binding(
            plan=plan,
            policy_hash=policy_hash,
            selected_strategy_id=resolution.selected_strategy_id,
            shadow_records=records,
            receipts=receipts[:-1],
            assessed_at=assessed_at,
        )

    late = _valid_receipt_with(
        receipts[0], captured_at=assessed_at + timedelta(seconds=1)
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="captured after"):
        verify_shadow_measurement_binding(
            plan=plan,
            policy_hash=policy_hash,
            selected_strategy_id=resolution.selected_strategy_id,
            shadow_records=records[:1],
            receipts=(late,),
            assessed_at=assessed_at,
        )

    wrong_policy = _valid_receipt_with(receipts[0], policy_hash="b" * 64)
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="identity/chain"):
        verify_shadow_measurement_binding(
            plan=plan,
            policy_hash=policy_hash,
            selected_strategy_id=resolution.selected_strategy_id,
            shadow_records=records[:1],
            receipts=(wrong_policy,),
            assessed_at=assessed_at,
        )


def test_w84_private_validation_and_source_location_fail_closed(
    monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    *_, plan, receipts = _baseline(
        limits, market, empty_portfolio, market_buy_intent
    )
    receipt = receipts[0]

    forged_plan = _forge_dataclass(
        ForwardMeasurementPlan, plan, plan_hash="a" * 64
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="plan hash"):
        measurement._validate_plan(forged_plan)

    forged_measurement = _forge_dataclass(
        ForwardShadowMeasurementReceipt,
        receipt,
        measurement_hash="a" * 64,
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="measurement hash"):
        measurement._validate_receipt(forged_measurement)

    forged_receipt = _forge_dataclass(
        ForwardShadowMeasurementReceipt,
        receipt,
        receipt_hash="a" * 64,
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="receipt hash"):
        measurement._validate_receipt(forged_receipt)

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="datetime value"):
        measurement._utc_iso("not-a-datetime")

    monkeypatch.setattr(measurement.inspect, "getsourcefile", lambda subject: None)
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="cannot locate"):
        measurement._source_sha256(object(), "autotrade/research/backtest.py")

    monkeypatch.setattr(
        measurement.inspect,
        "getsourcefile",
        lambda subject: "/tmp/wrong.py",
    )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="does not end"):
        measurement._source_sha256(object(), "autotrade/research/backtest.py")


def test_w84_dataset_identity_source_and_timeframe_fail_closed(
    limits, market, empty_portfolio, market_buy_intent
):
    (
        _,
        binding,
        _,
        _,
        _,
        history,
        _,
        _,
        _,
        _,
    ) = _baseline(limits, market, empty_portfolio, market_buy_intent)

    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="source"):
        measurement._validate_dataset_identity(
            dataset=history,
            binding_evidence=binding,
            expected_source="wrong-source",
            expected_timeframe=history.timeframe_seconds,
        )
    with pytest.raises(ForwardShadowMeasurementIntegrityError, match="timeframe"):
        measurement._validate_dataset_identity(
            dataset=history,
            binding_evidence=binding,
            expected_source=history.source,
            expected_timeframe=history.timeframe_seconds + 60,
        )
