from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import Side
from autotrade.execution_cost_continuity import (
    CONTINUITY_BLOCKER,
    ExecutionCostContinuityIntegrityError,
    ExecutionCostContinuityStatus,
    build_execution_cost_continuity_evidence,
)
from autotrade.paper_execution_lab import run_paper_execution_sensitivity
from autotrade.paper_execution_qualification import bind_research_costs_to_paper_execution
from autotrade.paper_execution_scenarios import (
    build_paper_execution_scenario,
    build_paper_execution_scenario_matrix,
)
from autotrade.research.costs import ExecutionCostModel


def _scenario(*, scenario_id: str, slippage: str, fill: str = "1"):
    return build_paper_execution_scenario(
        scenario_id=scenario_id,
        purpose=f"W81 adversarial {scenario_id}",
        slippage_bps=Decimal(slippage),
        max_fill_fraction=Decimal(fill),
        max_market_age=timedelta(seconds=2),
        max_spread_bps=Decimal("250"),
    )


def _matrix(*, baseline_slippage: str = "2", stress_slippage: str = "8"):
    return build_paper_execution_scenario_matrix(
        (
            _scenario(scenario_id="baseline", slippage=baseline_slippage),
            _scenario(scenario_id="stress", slippage=stress_slippage, fill="0.5"),
        )
    )


def _cost(*, fee: str = "5", half_spread: str = "5", slippage: str = "2"):
    return ExecutionCostModel(
        fee_bps=Decimal(fee),
        half_spread_bps=Decimal(half_spread),
        slippage_bps=Decimal(slippage),
    )


def _artifacts(*, limits, market, empty_portfolio, intent, cost_model=None, matrix=None, now=None):
    cost_model = cost_model or _cost()
    matrix = matrix or _matrix()
    qualification = bind_research_costs_to_paper_execution(cost_model=cost_model, matrix=matrix)
    now = now or market.observed_at
    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=intent,
        market=market,
        portfolio=empty_portfolio,
        now=now,
    )
    return cost_model, matrix, qualification, report, now


def _evidence(*, limits, market, empty_portfolio, intent, cost_model=None, matrix=None, now=None):
    cost_model, matrix, qualification, report, now = _artifacts(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=intent,
        cost_model=cost_model,
        matrix=matrix,
        now=now,
    )
    return build_execution_cost_continuity_evidence(
        evidence_id="w81-adversarial",
        cost_model=cost_model,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=intent,
        market=market,
        assessed_at=now,
    )


def test_sell_exact_math_blocks_boundary_that_naive_addition_would_pass(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    tight = replace(
        market,
        bid=Decimal("99.95"),
        ask=Decimal("100.05"),
        last=Decimal("100"),
    )
    sell = replace(
        market_buy_intent,
        intent_id="w81-sell-boundary",
        idempotency_key="w81-sell-boundary",
        side=Side.SELL,
    )
    evidence = _evidence(
        limits=limits,
        market=tight,
        empty_portfolio=empty_portfolio,
        intent=sell,
        cost_model=_cost(half_spread="5", slippage="2"),
    )

    baseline = next(item for item in evidence.observations if item.scenario_id == "baseline")
    stress = next(item for item in evidence.observations if item.scenario_id == "stress")

    # Naive 5 bps observed half-spread + 2 bps slippage = 7 bps would PASS.
    # Exact W78 SELL math applies slippage to bid: 99.95 * 0.9998 = 99.93001,
    # which is only 6.999 bps adverse versus midpoint 100.
    assert baseline.observed_half_spread_bps == Decimal("5.0000")
    assert baseline.modeled_adverse_price == Decimal("99.930010")
    assert baseline.effective_non_fee_impact_bps == Decimal("6.9990000")
    assert baseline.research_non_fee_impact_bps == Decimal("7")
    assert baseline.continuity_margin_bps == Decimal("-0.0010000")
    assert baseline.status is ExecutionCostContinuityStatus.BLOCKED
    assert stress.status is ExecutionCostContinuityStatus.PASS
    assert evidence.status is ExecutionCostContinuityStatus.BLOCKED
    assert CONTINUITY_BLOCKER in evidence.remaining_promotion_blockers
    assert evidence.resolved_promotion_blockers == ()


def test_buy_same_boundary_is_slightly_more_conservative_due_touch_compounding(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    tight = replace(
        market,
        bid=Decimal("99.95"),
        ask=Decimal("100.05"),
        last=Decimal("100"),
    )
    evidence = _evidence(
        limits=limits,
        market=tight,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        cost_model=_cost(half_spread="5", slippage="2"),
    )
    baseline = next(item for item in evidence.observations if item.scenario_id == "baseline")

    assert baseline.modeled_adverse_price == Decimal("100.070010")
    assert baseline.effective_non_fee_impact_bps == Decimal("7.0010000")
    assert baseline.continuity_margin_bps == Decimal("0.0010000")
    assert baseline.status is ExecutionCostContinuityStatus.PASS
    assert evidence.status is ExecutionCostContinuityStatus.PASS


def test_one_conservative_stress_scenario_cannot_wash_out_optimistic_baseline(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    tight = replace(market, bid=Decimal("99.99"), ask=Decimal("100.01"), last=Decimal("100"))
    evidence = _evidence(
        limits=limits,
        market=tight,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        cost_model=_cost(half_spread="5", slippage="2"),
    )
    states = {item.scenario_id: item.status for item in evidence.observations}

    assert states == {
        "baseline": ExecutionCostContinuityStatus.BLOCKED,
        "stress": ExecutionCostContinuityStatus.PASS,
    }
    assert evidence.status is ExecutionCostContinuityStatus.BLOCKED
    assert evidence.resolved_promotion_blockers == ()


def test_matrix_rebinding_is_rejected_before_continuity_can_be_claimed(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    cost_model, matrix, qualification, report, now = _artifacts(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    other_matrix = _matrix(baseline_slippage="3", stress_slippage="9")

    with pytest.raises(ExecutionCostContinuityIntegrityError, match="qualification/scenario matrix hash mismatch"):
        build_execution_cost_continuity_evidence(
            evidence_id="w81-matrix-rebind",
            cost_model=cost_model,
            qualification=qualification,
            matrix=other_matrix,
            sensitivity_report=report,
            intent=market_buy_intent,
            market=market,
            assessed_at=now,
        )


def test_sensitivity_report_from_different_qualification_is_rejected(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    matrix = _matrix()
    first_cost = _cost(half_spread="5", slippage="2")
    first_q = bind_research_costs_to_paper_execution(cost_model=first_cost, matrix=matrix)
    first_report = run_paper_execution_sensitivity(
        qualification=first_q,
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    second_cost = _cost(half_spread="6", slippage="2")
    second_q = bind_research_costs_to_paper_execution(cost_model=second_cost, matrix=matrix)

    with pytest.raises(ExecutionCostContinuityIntegrityError, match="sensitivity/qualification contract hash mismatch"):
        build_execution_cost_continuity_evidence(
            evidence_id="w81-report-rebind",
            cost_model=second_cost,
            qualification=second_q,
            matrix=matrix,
            sensitivity_report=first_report,
            intent=market_buy_intent,
            market=market,
            assessed_at=market.observed_at,
        )


def test_crossed_market_fails_closed_even_when_w78_report_exists(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    crossed = replace(market, bid=Decimal("100.01"), ask=Decimal("99.99"), last=Decimal("100"))
    cost_model, matrix, qualification, report, now = _artifacts(
        limits=limits,
        market=crossed,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        cost_model=_cost(half_spread="5", slippage="2"),
    )

    with pytest.raises(ExecutionCostContinuityIntegrityError, match="market may not be crossed"):
        build_execution_cost_continuity_evidence(
            evidence_id="w81-crossed",
            cost_model=cost_model,
            qualification=qualification,
            matrix=matrix,
            sensitivity_report=report,
            intent=market_buy_intent,
            market=crossed,
            assessed_at=now,
        )


def test_assessment_timestamp_before_market_observation_is_rejected(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    cost_model, matrix, qualification, report, _ = _artifacts(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )

    with pytest.raises(ExecutionCostContinuityIntegrityError, match="may not predate market observation"):
        build_execution_cost_continuity_evidence(
            evidence_id="w81-time-regression",
            cost_model=cost_model,
            qualification=qualification,
            matrix=matrix,
            sensitivity_report=report,
            intent=market_buy_intent,
            market=market,
            assessed_at=market.observed_at - timedelta(microseconds=1),
        )


def test_evidence_and_observation_hash_tampering_are_rejected(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    evidence = _evidence(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )

    with pytest.raises(ExecutionCostContinuityIntegrityError, match="evidence hash mismatch"):
        replace(evidence, evidence_hash="f" * 64)

    first = evidence.observations[0]
    with pytest.raises(ExecutionCostContinuityIntegrityError, match="observation hash mismatch"):
        replace(first, observation_hash="f" * 64)


def test_blocker_and_fee_state_cannot_be_rewritten_after_pass(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    evidence = _evidence(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        cost_model=_cost(half_spread="1", slippage="2"),
    )
    assert evidence.status is ExecutionCostContinuityStatus.PASS

    with pytest.raises(ExecutionCostContinuityIntegrityError, match="resolved promotion blockers"):
        replace(evidence, resolved_promotion_blockers=())
    with pytest.raises(ExecutionCostContinuityIntegrityError, match="remaining promotion blockers"):
        replace(evidence, remaining_promotion_blockers=(CONTINUITY_BLOCKER,))
    with pytest.raises(ExecutionCostContinuityIntegrityError, match="fee accounting"):
        replace(evidence, fee_accounting_state="COMPLETE")
