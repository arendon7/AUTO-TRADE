from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import OrderStatus, OrderType
from autotrade.engine import TradingPipeline
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.paper_execution_evidence import (
    PaperExecutionEvidenceError,
    capture_paper_execution_evidence,
)
from autotrade.paper_execution_scenarios import (
    PaperExecutionScenarioError,
    build_paper_execution_scenario,
    build_paper_execution_scenario_matrix,
)
from autotrade.safety import CapitalSafetyKernel


def _scenario(
    *,
    scenario_id: str,
    slippage_bps: str,
    fill_fraction: str,
    spread_bps: str = "250",
):
    return build_paper_execution_scenario(
        scenario_id=scenario_id,
        purpose=f"Execution stress case {scenario_id}",
        slippage_bps=Decimal(slippage_bps),
        max_fill_fraction=Decimal(fill_fraction),
        max_market_age=timedelta(seconds=2),
        max_spread_bps=Decimal(spread_bps),
    )


def _run(*, scenario, limits, market, empty_portfolio, intent):
    broker = scenario.build_broker()
    ledger = InMemoryEventLedger()
    result = TradingPipeline(
        safety=CapitalSafetyKernel(limits, ledger),
        oms=OrderManagementSystem(broker=broker, ledger=ledger),
    ).process_intent(
        intent=intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    assert result.order is not None
    return result.order


def test_scenario_hash_is_reproducible_and_changes_with_assumption():
    first = _scenario(scenario_id="baseline", slippage_bps="2", fill_fraction="1")
    replay = _scenario(scenario_id="baseline", slippage_bps="2", fill_fraction="1")
    stressed = _scenario(scenario_id="baseline", slippage_bps="8", fill_fraction="1")

    assert first == replay
    assert first.scenario_hash == replay.scenario_hash
    assert stressed.scenario_hash != first.scenario_hash


def test_scenario_matrix_is_sorted_and_hash_stable():
    baseline = _scenario(scenario_id="baseline", slippage_bps="2", fill_fraction="1")
    partial = _scenario(scenario_id="partial", slippage_bps="5", fill_fraction="0.4")
    stress = _scenario(scenario_id="stress", slippage_bps="12", fill_fraction="0.7")

    first = build_paper_execution_scenario_matrix([stress, baseline, partial])
    replay = build_paper_execution_scenario_matrix([partial, stress, baseline])

    assert [item.scenario_id for item in first.scenarios] == ["baseline", "partial", "stress"]
    assert replay.matrix_hash == first.matrix_hash
    assert replay.to_dict() == first.to_dict()


def test_scenario_matrix_requires_multiple_distinct_assumptions():
    baseline = _scenario(scenario_id="baseline", slippage_bps="2", fill_fraction="1")
    duplicate_config = _scenario(scenario_id="same_config", slippage_bps="2", fill_fraction="1")

    with pytest.raises(PaperExecutionScenarioError, match="at least two"):
        build_paper_execution_scenario_matrix([baseline])
    with pytest.raises(PaperExecutionScenarioError, match="duplicate execution assumptions"):
        build_paper_execution_scenario_matrix([baseline, duplicate_config])


def test_scenario_id_is_canonical():
    with pytest.raises(PaperExecutionScenarioError, match="scenario_id"):
        build_paper_execution_scenario(
            scenario_id="Baseline Bad",
            purpose="bad id",
            slippage_bps=Decimal("2"),
            max_fill_fraction=Decimal("1"),
            max_market_age=timedelta(seconds=2),
            max_spread_bps=Decimal("250"),
        )


def test_partial_fill_evidence_binds_scenario_intent_market_and_execution(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    scenario = _scenario(scenario_id="partial", slippage_bps="2", fill_fraction="0.4")
    order = _run(
        scenario=scenario,
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    assert order.status is OrderStatus.PARTIALLY_FILLED

    evidence = capture_paper_execution_evidence(
        scenario=scenario,
        order=order,
        market=market,
        captured_at=market.observed_at,
    )

    assert evidence.scenario_hash == scenario.scenario_hash
    assert evidence.requested_quantity == Decimal("10")
    assert evidence.filled_quantity == Decimal("4.0")
    assert evidence.fill_ratio == Decimal("0.4")
    assert evidence.reference_touch == Decimal("101")
    assert evidence.average_fill_price == Decimal("101.0202")
    assert evidence.adverse_slippage_bps == Decimal("2")
    assert evidence.order_status == OrderStatus.PARTIALLY_FILLED.value


def test_zero_fill_limit_evidence_does_not_invent_price_or_slippage(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    # Keep the limit safely inside Capital Safety's deviation bound while placing
    # it below the W78 10 bps adverse execution price. This isolates the intended
    # execution-model no-fill behavior instead of being rejected by Safety first.
    execution_market = replace(
        market,
        bid=Decimal("99.95"),
        ask=Decimal("100.05"),
        last=Decimal("100"),
    )
    scenario = _scenario(scenario_id="no_fill", slippage_bps="10", fill_fraction="1")
    intent = replace(
        market_buy_intent,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100.10"),
    )
    order = _run(
        scenario=scenario,
        limits=limits,
        market=execution_market,
        empty_portfolio=empty_portfolio,
        intent=intent,
    )
    assert order.status is OrderStatus.SUBMITTED
    assert order.filled_quantity == Decimal("0")

    evidence = capture_paper_execution_evidence(
        scenario=scenario,
        order=order,
        market=execution_market,
        captured_at=execution_market.observed_at,
    )
    assert evidence.fill_ratio == Decimal("0")
    assert evidence.average_fill_price is None
    assert evidence.adverse_slippage_bps is None


def test_execution_evidence_hash_detects_tampering(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    scenario = _scenario(scenario_id="baseline", slippage_bps="2", fill_fraction="1")
    order = _run(
        scenario=scenario,
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    evidence = capture_paper_execution_evidence(
        scenario=scenario,
        order=order,
        market=market,
        captured_at=market.observed_at,
    )

    with pytest.raises(PaperExecutionEvidenceError, match="hash mismatch"):
        replace(evidence, scenario_id="tampered")


def test_execution_evidence_rejects_semantically_impossible_status(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    scenario = _scenario(scenario_id="partial", slippage_bps="2", fill_fraction="0.4")
    order = _run(
        scenario=scenario,
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    evidence = capture_paper_execution_evidence(
        scenario=scenario,
        order=order,
        market=market,
        captured_at=market.observed_at,
    )

    with pytest.raises(PaperExecutionEvidenceError, match="FILLED evidence"):
        replace(evidence, order_status=OrderStatus.FILLED.value)


def test_unknown_state_cannot_be_misrepresented_as_qualification_evidence(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    scenario = _scenario(scenario_id="baseline", slippage_bps="2", fill_fraction="1")
    order = _run(
        scenario=scenario,
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    unknown = replace(order, status=OrderStatus.UNKNOWN)

    with pytest.raises(PaperExecutionEvidenceError, match="UNKNOWN execution requires reconciliation"):
        capture_paper_execution_evidence(
            scenario=scenario,
            order=unknown,
            market=market,
            captured_at=market.observed_at,
        )


def test_execution_evidence_rejects_symbol_mismatch(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    scenario = _scenario(scenario_id="baseline", slippage_bps="2", fill_fraction="1")
    order = _run(
        scenario=scenario,
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    wrong_market = replace(market, symbol="OTHER-USD")

    with pytest.raises(PaperExecutionEvidenceError, match="symbol mismatch"):
        capture_paper_execution_evidence(
            scenario=scenario,
            order=order,
            market=wrong_market,
            captured_at=market.observed_at,
        )
