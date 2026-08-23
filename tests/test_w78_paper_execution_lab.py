from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import OrderStatus, OrderType
from autotrade.paper_execution_lab import (
    PaperExecutionSensitivityError,
    run_paper_execution_sensitivity,
)
from autotrade.paper_execution_qualification import bind_research_costs_to_paper_execution
from autotrade.paper_execution_scenarios import (
    build_paper_execution_scenario,
    build_paper_execution_scenario_matrix,
)
from autotrade.research.costs import ExecutionCostModel


def _scenario(
    *,
    scenario_id: str,
    slippage: str,
    fill: str,
    max_age_ms: int = 2000,
    max_spread_bps: str = "250",
):
    return build_paper_execution_scenario(
        scenario_id=scenario_id,
        purpose=f"Sensitivity Lab {scenario_id}",
        slippage_bps=Decimal(slippage),
        max_fill_fraction=Decimal(fill),
        max_market_age=timedelta(milliseconds=max_age_ms),
        max_spread_bps=Decimal(max_spread_bps),
    )


def _matrix(*scenarios):
    return build_paper_execution_scenario_matrix(scenarios)


def _qualification(matrix):
    return bind_research_costs_to_paper_execution(
        cost_model=ExecutionCostModel(
            fee_bps=Decimal("5"),
            half_spread_bps=Decimal("2"),
            slippage_bps=Decimal("2"),
        ),
        matrix=matrix,
    )


def test_sensitivity_lab_runs_same_intent_across_full_and_partial_fill_stress(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    matrix = _matrix(
        _scenario(scenario_id="baseline", slippage="2", fill="1"),
        _scenario(scenario_id="liquidity_stress", slippage="8", fill="0.4"),
    )
    qualification = _qualification(matrix)

    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )

    assert report.full_fill_count == 1
    assert report.partial_fill_count == 1
    assert report.zero_fill_count == 0
    assert report.broker_rejection_count == 0
    assert report.risk_rejection_count == 0
    assert report.minimum_fill_ratio == Decimal("0.4")
    assert report.maximum_adverse_slippage_bps == Decimal("8")
    assert report.external_execution_authorized is False
    assert report.live_trading == "BLOCKED"
    assert [item.order_status for item in report.outcomes] == [
        OrderStatus.FILLED.value,
        OrderStatus.PARTIALLY_FILLED.value,
    ]


def test_sensitivity_measurement_report_is_reproducible_while_trace_changes(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    matrix = _matrix(
        _scenario(scenario_id="baseline", slippage="2", fill="1"),
        _scenario(scenario_id="stress", slippage="7", fill="0.5"),
    )
    qualification = _qualification(matrix)

    first = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    second = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )

    assert first.measurement_report_hash == second.measurement_report_hash
    assert first.trace_report_hash != second.trace_report_hash
    assert [item.outcome_hash for item in first.outcomes] == [
        item.outcome_hash for item in second.outcomes
    ]
    assert [item.trace_evidence_hash for item in first.outcomes] != [
        item.trace_evidence_hash for item in second.outcomes
    ]


def test_limit_execution_sensitivity_exposes_fill_vs_no_fill_boundary(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    matrix = _matrix(
        _scenario(scenario_id="baseline", slippage="2", fill="1"),
        _scenario(scenario_id="slippage_stress", slippage="8", fill="1"),
    )
    qualification = _qualification(matrix)
    limit_intent = replace(
        market_buy_intent,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("101.05"),
    )

    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=limit_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )

    assert report.full_fill_count == 1
    assert report.zero_fill_count == 1
    assert report.minimum_fill_ratio == Decimal("0")
    assert {item.order_status for item in report.outcomes} == {
        OrderStatus.FILLED.value,
        OrderStatus.SUBMITTED.value,
    }


def test_sensitivity_lab_records_deterministic_broker_rejection_reason(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    matrix = _matrix(
        _scenario(scenario_id="baseline", slippage="2", fill="1", max_age_ms=2000),
        _scenario(scenario_id="freshness_stress", slippage="4", fill="1", max_age_ms=500),
    )
    qualification = _qualification(matrix)

    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at + timedelta(milliseconds=750),
    )

    assert report.full_fill_count == 1
    assert report.broker_rejection_count == 1
    rejected = next(item for item in report.outcomes if item.order_status == OrderStatus.REJECTED.value)
    assert rejected.broker_rejection_reason == "STALE_MARKET_SNAPSHOT"
    assert rejected.fill_ratio == Decimal("0")
    assert rejected.adverse_slippage_bps is None


def test_capital_safety_rejection_is_recorded_without_execution_claim(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    matrix = _matrix(
        _scenario(scenario_id="baseline", slippage="2", fill="1"),
        _scenario(scenario_id="stress", slippage="8", fill="0.5"),
    )
    qualification = _qualification(matrix)
    oversized = replace(market_buy_intent, quantity=Decimal("100000"))

    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=oversized,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )

    assert report.risk_rejection_count == 2
    assert report.full_fill_count == 0
    assert report.partial_fill_count == 0
    assert report.zero_fill_count == 0
    assert report.broker_rejection_count == 0
    assert report.minimum_fill_ratio is None
    assert report.maximum_adverse_slippage_bps is None
    assert all(item.order_status is None for item in report.outcomes)
    assert all(item.measurement_hash is None for item in report.outcomes)


def test_sensitivity_report_cannot_be_mutated_into_execution_authority(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    matrix = _matrix(
        _scenario(scenario_id="baseline", slippage="2", fill="1"),
        _scenario(scenario_id="stress", slippage="8", fill="0.5"),
    )
    report = run_paper_execution_sensitivity(
        qualification=_qualification(matrix),
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )

    with pytest.raises(PaperExecutionSensitivityError, match="may not grant"):
        replace(report, external_execution_authorized=True)
    with pytest.raises(PaperExecutionSensitivityError, match="may not grant"):
        replace(report, live_trading="ENABLED")
