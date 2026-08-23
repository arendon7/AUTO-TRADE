from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.brokers.paper_execution import (
    DeterministicPaperExecutionBroker,
    PaperExecutionConfig,
    PaperExecutionConflict,
    PaperExecutionMarketError,
)
from autotrade.domain import MarketSnapshot, OrderRecord, OrderStatus, OrderType
from autotrade.engine import TradingPipeline
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.paper_execution_evidence import (
    PaperExecutionEvidenceError,
    capture_paper_execution_evidence,
)
from autotrade.paper_execution_qualification import (
    PaperExecutionQualificationError,
    bind_research_costs_to_paper_execution,
)
from autotrade.paper_execution_scenarios import (
    PaperExecutionScenarioError,
    PaperExecutionScenarioMatrix,
    build_paper_execution_scenario,
    build_paper_execution_scenario_matrix,
)
from autotrade.research.costs import ExecutionCostModel
from autotrade.safety import CapitalSafetyKernel


def _scenario(
    *,
    scenario_id: str = "baseline",
    purpose: str = "Validation baseline",
    slippage: str = "2",
    fill: str = "1",
    age_ms: int = 2000,
    spread: str = "250",
):
    return build_paper_execution_scenario(
        scenario_id=scenario_id,
        purpose=purpose,
        slippage_bps=Decimal(slippage),
        max_fill_fraction=Decimal(fill),
        max_market_age=timedelta(milliseconds=age_ms),
        max_spread_bps=Decimal(spread),
    )


def _matrix():
    return build_paper_execution_scenario_matrix(
        [
            _scenario(),
            _scenario(scenario_id="stress", slippage="8", fill="0.5"),
        ]
    )


def _cost_model():
    return ExecutionCostModel(
        fee_bps=Decimal("5"),
        half_spread_bps=Decimal("2"),
        slippage_bps=Decimal("2"),
    )


def _validated_order(intent, *, order_id: str = "w78-validation-order"):
    return OrderRecord(
        order_id=order_id,
        intent=intent,
        risk_decision_id="risk-validation",
        status=OrderStatus.VALIDATED,
        created_at=intent.created_at,
    )


def _full_fill_evidence(*, limits, market, empty_portfolio, market_buy_intent):
    scenario = _scenario()
    ledger = InMemoryEventLedger()
    result = TradingPipeline(
        safety=CapitalSafetyKernel(limits, ledger),
        oms=OrderManagementSystem(broker=scenario.build_broker(), ledger=ledger),
    ).process_intent(
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    assert result.order is not None
    assert result.order.status is OrderStatus.FILLED
    evidence = capture_paper_execution_evidence(
        scenario=scenario,
        order=result.order,
        market=market,
        captured_at=market.observed_at,
    )
    return scenario, result.order, evidence


def test_execution_config_rejects_non_decimal_and_invalid_market_age():
    with pytest.raises(TypeError, match="slippage_bps"):
        PaperExecutionConfig(slippage_bps=2)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="max_market_age"):
        PaperExecutionConfig(max_market_age=2)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_market_age"):
        PaperExecutionConfig(max_market_age=timedelta(minutes=6))


def test_broker_contract_rejects_wrong_objects_states_and_naive_time(market, market_buy_intent):
    broker = DeterministicPaperExecutionBroker()
    with pytest.raises(ValueError, match="now"):
        broker.account_state(now=market.observed_at.replace(tzinfo=None))
    with pytest.raises(TypeError, match="OrderRecord"):
        broker.submit(order=object(), market=market, now=market.observed_at)  # type: ignore[arg-type]
    non_validated = replace(_validated_order(market_buy_intent), status=OrderStatus.SUBMITTED)
    with pytest.raises(PaperExecutionConflict, match="VALIDATED"):
        broker.submit(order=non_validated, market=market, now=market.observed_at)
    with pytest.raises(KeyError):
        broker.cancel(order_id="missing", now=market.observed_at)


def test_broker_rejects_invalid_order_shapes_before_simulation(market, market_buy_intent):
    broker = DeterministicPaperExecutionBroker()
    zero_quantity = replace(market_buy_intent, quantity=Decimal("0"))
    with pytest.raises(PaperExecutionConflict, match="quantity"):
        broker.submit(
            order=_validated_order(zero_quantity, order_id="zero-qty"),
            market=market,
            now=market.observed_at,
        )

    market_with_limit = replace(market_buy_intent, limit_price=Decimal("100"))
    with pytest.raises(PaperExecutionConflict, match="market paper order"):
        broker.submit(
            order=_validated_order(market_with_limit, order_id="market-limit"),
            market=market,
            now=market.observed_at,
        )

    invalid_limit = replace(
        market_buy_intent,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("0"),
    )
    with pytest.raises(PaperExecutionConflict, match="limit paper order"):
        broker.submit(
            order=_validated_order(invalid_limit, order_id="bad-limit"),
            market=market,
            now=market.observed_at,
        )


def test_broker_rejects_invalid_market_contracts(market, market_buy_intent):
    broker = DeterministicPaperExecutionBroker()
    order = _validated_order(market_buy_intent)
    with pytest.raises(TypeError, match="MarketSnapshot"):
        broker.submit(order=order, market=object(), now=market.observed_at)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="market.observed_at"):
        broker.submit(
            order=order,
            market=replace(market, observed_at=market.observed_at.replace(tzinfo=None)),
            now=market.observed_at,
        )
    with pytest.raises(PaperExecutionMarketError, match="bid"):
        broker.submit(
            order=order,
            market=replace(market, bid=Decimal("0")),
            now=market.observed_at,
        )


def test_scenario_validation_rejects_bad_purpose_config_and_hash():
    with pytest.raises(PaperExecutionScenarioError, match="purpose"):
        _scenario(purpose=" ")

    valid = _scenario()
    with pytest.raises(TypeError, match="scenario config"):
        replace(valid, config=object())  # type: ignore[arg-type]
    with pytest.raises(PaperExecutionScenarioError, match="scenario_hash"):
        replace(valid, scenario_hash="bad")
    with pytest.raises(PaperExecutionScenarioError, match="hash mismatch"):
        replace(valid, scenario_hash="0" * 64)


def test_scenario_matrix_rejects_shape_order_identity_and_hash():
    baseline = _scenario()
    stress = _scenario(scenario_id="stress", slippage="8", fill="0.5")
    valid = build_paper_execution_scenario_matrix([baseline, stress])

    with pytest.raises(PaperExecutionScenarioError, match="at most 32"):
        PaperExecutionScenarioMatrix(scenarios=(baseline,) * 33, matrix_hash="0" * 64)
    with pytest.raises(TypeError, match="non-scenario"):
        PaperExecutionScenarioMatrix(scenarios=(baseline, "bad"), matrix_hash="0" * 64)  # type: ignore[arg-type]
    with pytest.raises(PaperExecutionScenarioError, match="sorted"):
        PaperExecutionScenarioMatrix(scenarios=(stress, baseline), matrix_hash="0" * 64)

    duplicate_id = _scenario(scenario_id="baseline", slippage="9", fill="0.4")
    with pytest.raises(PaperExecutionScenarioError, match="scenario_id values"):
        PaperExecutionScenarioMatrix(
            scenarios=(baseline, duplicate_id),
            matrix_hash="0" * 64,
        )
    with pytest.raises(PaperExecutionScenarioError, match="matrix_hash"):
        replace(valid, matrix_hash="bad")
    with pytest.raises(PaperExecutionScenarioError, match="hash mismatch"):
        replace(valid, matrix_hash="0" * 64)


def test_qualification_builder_rejects_wrong_types_and_weak_matrices():
    matrix = _matrix()
    with pytest.raises(TypeError, match="cost_model"):
        bind_research_costs_to_paper_execution(cost_model=object(), matrix=matrix)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="matrix"):
        bind_research_costs_to_paper_execution(cost_model=_cost_model(), matrix=object())  # type: ignore[arg-type]

    weaker = build_paper_execution_scenario_matrix(
        [
            _scenario(scenario_id="weak", slippage="1", fill="1"),
            _scenario(scenario_id="stress", slippage="8", fill="0.5"),
        ]
    )
    with pytest.raises(PaperExecutionQualificationError, match="at least as adverse"):
        bind_research_costs_to_paper_execution(cost_model=_cost_model(), matrix=weaker)

    no_full = build_paper_execution_scenario_matrix(
        [
            _scenario(scenario_id="partial_a", slippage="2", fill="0.8"),
            _scenario(scenario_id="partial_b", slippage="8", fill="0.5"),
        ]
    )
    with pytest.raises(PaperExecutionQualificationError, match="full-liquidity"):
        bind_research_costs_to_paper_execution(cost_model=_cost_model(), matrix=no_full)

    no_stress = build_paper_execution_scenario_matrix(
        [
            _scenario(scenario_id="base_a", slippage="2", fill="1", age_ms=2000),
            _scenario(scenario_id="base_b", slippage="2", fill="1", age_ms=3000),
        ]
    )
    with pytest.raises(PaperExecutionQualificationError, match="stricter"):
        bind_research_costs_to_paper_execution(cost_model=_cost_model(), matrix=no_stress)


def test_qualification_contract_rejects_semantic_tampering():
    contract = bind_research_costs_to_paper_execution(cost_model=_cost_model(), matrix=_matrix())

    mutations = (
        (dict(research_cost_model_hash="bad"), "lowercase sha256"),
        (dict(research_fee_bps=Decimal("-1")), "non-negative"),
        (dict(minimum_scenario_slippage_bps=Decimal("1")), "may not weaken"),
        (dict(maximum_scenario_slippage_bps=Decimal("1")), "range is invalid"),
        (dict(minimum_fill_fraction=Decimal("0")), "within"),
        (dict(scenario_count=True), "scenario_count"),
        (dict(has_full_liquidity_case=False), "full-liquidity"),
        (dict(has_execution_stress_case=False), "stressed execution"),
        (dict(fee_accounting_mode="BAD"), "fee accounting"),
        (dict(contract_hash="0" * 64), "hash mismatch"),
    )
    for kwargs, message in mutations:
        with pytest.raises(PaperExecutionQualificationError, match=message):
            replace(contract, **kwargs)


def test_evidence_contract_rejects_identity_status_numeric_and_time_tampering(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    _, _, evidence = _full_fill_evidence(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        market_buy_intent=market_buy_intent,
    )

    mutations = (
        (dict(scenario_hash="bad"), "lowercase sha256"),
        (dict(scenario_id=""), "scenario_id"),
        (dict(order_id=""), "order_id"),
        (dict(side="HOLD"), "side"),
        (dict(order_status="BOGUS"), "order_status"),
        (dict(requested_quantity=Decimal("NaN")), "finite Decimal"),
        (dict(requested_quantity=Decimal("0")), "positive"),
        (dict(filled_quantity=Decimal("11")), "outside request bounds"),
        (dict(fill_ratio=Decimal("0.5")), "fill_ratio mismatch"),
        (dict(reference_touch=Decimal("0")), "reference_touch"),
        (dict(average_fill_price=None), "average_fill_price"),
        (dict(adverse_slippage_bps=None), "slippage evidence"),
        (dict(adverse_slippage_bps=Decimal("-1")), "non-negative"),
        (dict(measurement_hash="0" * 64), "measurement hash mismatch"),
        (dict(evidence_hash="0" * 64), "evidence hash mismatch"),
    )
    for kwargs, message in mutations:
        with pytest.raises(PaperExecutionEvidenceError, match=message):
            replace(evidence, **kwargs)

    with pytest.raises(PaperExecutionEvidenceError, match="timezone-aware"):
        replace(evidence, captured_at=evidence.captured_at.replace(tzinfo=None))
    with pytest.raises(PaperExecutionEvidenceError, match="cannot predate"):
        replace(
            evidence,
            captured_at=evidence.market_observed_at - timedelta(microseconds=1),
        )


def test_evidence_status_consistency_rejects_impossible_execution_states(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    _, _, evidence = _full_fill_evidence(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        market_buy_intent=market_buy_intent,
    )
    with pytest.raises(PaperExecutionEvidenceError, match="PARTIALLY_FILLED"):
        replace(evidence, order_status=OrderStatus.PARTIALLY_FILLED.value)
    with pytest.raises(PaperExecutionEvidenceError, match="SUBMITTED"):
        replace(evidence, order_status=OrderStatus.SUBMITTED.value)
    for status in (OrderStatus.VALIDATED, OrderStatus.SUBMITTING):
        with pytest.raises(PaperExecutionEvidenceError, match="pre-execution"):
            replace(evidence, order_status=status.value)


def test_capture_evidence_rejects_wrong_types_and_inconsistent_orders(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    scenario, order, _ = _full_fill_evidence(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        market_buy_intent=market_buy_intent,
    )
    with pytest.raises(TypeError, match="scenario"):
        capture_paper_execution_evidence(
            scenario=object(),  # type: ignore[arg-type]
            order=order,
            market=market,
            captured_at=market.observed_at,
        )
    with pytest.raises(TypeError, match="OrderRecord"):
        capture_paper_execution_evidence(
            scenario=scenario,
            order=object(),  # type: ignore[arg-type]
            market=market,
            captured_at=market.observed_at,
        )
    with pytest.raises(TypeError, match="MarketSnapshot"):
        capture_paper_execution_evidence(
            scenario=scenario,
            order=order,
            market=object(),  # type: ignore[arg-type]
            captured_at=market.observed_at,
        )

    with pytest.raises(PaperExecutionEvidenceError, match="filled quantity"):
        capture_paper_execution_evidence(
            scenario=scenario,
            order=replace(order, filled_quantity=order.intent.quantity + Decimal("1")),
            market=market,
            captured_at=market.observed_at,
        )
    with pytest.raises(PaperExecutionEvidenceError, match="lacks average"):
        capture_paper_execution_evidence(
            scenario=scenario,
            order=replace(order, average_fill_price=None),
            market=market,
            captured_at=market.observed_at,
        )
    with pytest.raises(PaperExecutionEvidenceError, match="favorable slippage"):
        capture_paper_execution_evidence(
            scenario=scenario,
            order=replace(order, average_fill_price=market.ask - Decimal("0.01")),
            market=market,
            captured_at=market.observed_at,
        )


def test_zero_fill_evidence_rejects_claimed_price(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    execution_market = replace(
        market,
        bid=Decimal("99.95"),
        ask=Decimal("100.05"),
        last=Decimal("100"),
    )
    scenario = _scenario(scenario_id="no_fill", slippage="10")
    intent = replace(
        market_buy_intent,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100.10"),
    )
    ledger = InMemoryEventLedger()
    result = TradingPipeline(
        safety=CapitalSafetyKernel(limits, ledger),
        oms=OrderManagementSystem(broker=scenario.build_broker(), ledger=ledger),
    ).process_intent(
        intent=intent,
        market=execution_market,
        portfolio=empty_portfolio,
        now=execution_market.observed_at,
    )
    assert result.order is not None
    assert result.order.status is OrderStatus.SUBMITTED
    evidence = capture_paper_execution_evidence(
        scenario=scenario,
        order=result.order,
        market=execution_market,
        captured_at=execution_market.observed_at,
    )
    with pytest.raises(PaperExecutionEvidenceError, match="zero-fill evidence"):
        replace(evidence, average_fill_price=Decimal("100"))
