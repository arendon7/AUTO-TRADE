from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import Side
from autotrade.execution_cost_continuity import (
    CONTINUITY_BLOCKER,
    ExecutionCostContinuityIntegrityError,
    ExecutionCostContinuityStatus,
    FEE_ACCOUNTING_STATE,
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
        purpose=f"W81 {scenario_id}",
        slippage_bps=Decimal(slippage),
        max_fill_fraction=Decimal(fill),
        max_market_age=timedelta(seconds=2),
        max_spread_bps=Decimal("250"),
    )


def _matrix():
    return build_paper_execution_scenario_matrix(
        (
            _scenario(scenario_id="baseline", slippage="2", fill="1"),
            _scenario(scenario_id="stress", slippage="8", fill="0.5"),
        )
    )


def _cost(*, half_spread: str = "2", slippage: str = "2"):
    return ExecutionCostModel(
        fee_bps=Decimal("5"),
        half_spread_bps=Decimal(half_spread),
        slippage_bps=Decimal(slippage),
    )


def _build(
    *,
    limits,
    market,
    empty_portfolio,
    intent,
    cost_model=None,
    matrix=None,
    now=None,
):
    cost_model = cost_model or _cost()
    matrix = matrix or _matrix()
    qualification = bind_research_costs_to_paper_execution(
        cost_model=cost_model,
        matrix=matrix,
    )
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
    evidence = build_execution_cost_continuity_evidence(
        evidence_id="w81-evidence-1",
        cost_model=cost_model,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=intent,
        market=market,
        assessed_at=now,
    )
    return cost_model, matrix, qualification, report, evidence


def test_w81_passes_when_midpoint_execution_impact_is_at_least_research_non_fee_friction(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    _, _, _, _, evidence = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )

    assert evidence.status is ExecutionCostContinuityStatus.PASS
    assert evidence.blocking_reasons == ()
    assert evidence.resolved_promotion_blockers == (CONTINUITY_BLOCKER,)
    assert CONTINUITY_BLOCKER not in evidence.remaining_promotion_blockers
    assert "FEE_ACCOUNTING_INCOMPLETE" in evidence.remaining_promotion_blockers
    assert evidence.fee_accounting_complete is False
    assert evidence.fee_accounting_state == FEE_ACCOUNTING_STATE
    assert evidence.paper_candidate_authorized is False
    assert evidence.external_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"
    assert all(item.status is ExecutionCostContinuityStatus.PASS for item in evidence.observations)

    baseline = next(item for item in evidence.observations if item.scenario_id == "baseline")
    assert baseline.midpoint == Decimal("100")
    assert baseline.touch_price == Decimal("101")
    assert baseline.modeled_adverse_price == Decimal("101.0202")
    assert baseline.observed_half_spread_bps == Decimal("100")
    assert baseline.effective_non_fee_impact_bps == Decimal("102.0200")
    assert baseline.research_non_fee_impact_bps == Decimal("4")
    assert baseline.continuity_margin_bps == Decimal("98.0200")


def test_w81_blocks_favorable_observed_spread_that_would_weaken_research_friction(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    tight = replace(
        market,
        bid=Decimal("99.99"),
        ask=Decimal("100.01"),
        last=Decimal("100"),
    )
    _, _, _, _, evidence = _build(
        limits=limits,
        market=tight,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        cost_model=_cost(half_spread="5", slippage="2"),
    )

    assert evidence.status is ExecutionCostContinuityStatus.BLOCKED
    assert evidence.resolved_promotion_blockers == ()
    assert CONTINUITY_BLOCKER in evidence.remaining_promotion_blockers
    assert evidence.blocking_reasons == ("EFFECTIVE_NON_FEE_IMPACT_BELOW_RESEARCH",)

    baseline = next(item for item in evidence.observations if item.scenario_id == "baseline")
    assert baseline.observed_half_spread_bps == Decimal("1.0000")
    assert baseline.effective_non_fee_impact_bps == Decimal("3.00020000")
    assert baseline.research_non_fee_impact_bps == Decimal("7")
    assert baseline.continuity_margin_bps == Decimal("-3.99980000")
    assert baseline.status is ExecutionCostContinuityStatus.BLOCKED


def test_w81_binds_exact_cost_model_matrix_report_intent_and_market(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    cost_model, matrix, qualification, report, _ = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )

    with pytest.raises(ExecutionCostContinuityIntegrityError, match="cost model hash mismatch"):
        build_execution_cost_continuity_evidence(
            evidence_id="w81-cost-drift",
            cost_model=_cost(half_spread="3"),
            qualification=qualification,
            matrix=matrix,
            sensitivity_report=report,
            intent=market_buy_intent,
            market=market,
            assessed_at=market.observed_at,
        )

    changed_market = replace(market, ask=Decimal("101.01"))
    with pytest.raises(ExecutionCostContinuityIntegrityError, match="market fingerprint mismatch"):
        build_execution_cost_continuity_evidence(
            evidence_id="w81-market-drift",
            cost_model=cost_model,
            qualification=qualification,
            matrix=matrix,
            sensitivity_report=report,
            intent=market_buy_intent,
            market=changed_market,
            assessed_at=market.observed_at,
        )

    changed_intent = replace(market_buy_intent, idempotency_key="idem-changed")
    with pytest.raises(ExecutionCostContinuityIntegrityError, match="intent fingerprint mismatch"):
        build_execution_cost_continuity_evidence(
            evidence_id="w81-intent-drift",
            cost_model=cost_model,
            qualification=qualification,
            matrix=matrix,
            sensitivity_report=report,
            intent=changed_intent,
            market=market,
            assessed_at=market.observed_at,
        )


def test_w81_blocks_when_w78_has_no_execution_measurement(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    oversized = replace(market_buy_intent, quantity=Decimal("100000"))
    _, _, _, _, evidence = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=oversized,
    )

    assert evidence.status is ExecutionCostContinuityStatus.BLOCKED
    assert evidence.blocking_reasons == ("W78_EXECUTION_MEASUREMENT_MISSING",)
    assert CONTINUITY_BLOCKER in evidence.remaining_promotion_blockers


def test_w81_preserves_sell_side_midpoint_adversity(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    sell = replace(
        market_buy_intent,
        intent_id="intent-sell",
        idempotency_key="idem-sell",
        side=Side.SELL,
    )
    _, _, _, _, evidence = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=sell,
    )

    assert evidence.status is ExecutionCostContinuityStatus.PASS
    baseline = next(item for item in evidence.observations if item.scenario_id == "baseline")
    assert baseline.midpoint == Decimal("100")
    assert baseline.touch_price == Decimal("99")
    assert baseline.modeled_adverse_price == Decimal("98.9802")
    assert baseline.observed_half_spread_bps == Decimal("100")
    assert baseline.effective_non_fee_impact_bps == Decimal("101.9800")
    assert baseline.continuity_margin_bps == Decimal("97.9800")


def test_w81_rejected_market_quality_cannot_be_reinterpreted_as_continuity_pass(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    matrix = build_paper_execution_scenario_matrix(
        (
            build_paper_execution_scenario(
                scenario_id="baseline",
                purpose="W81 baseline",
                slippage_bps=Decimal("2"),
                max_fill_fraction=Decimal("1"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("250"),
            ),
            build_paper_execution_scenario(
                scenario_id="freshness_stress",
                purpose="W81 freshness stress",
                slippage_bps=Decimal("4"),
                max_fill_fraction=Decimal("1"),
                max_market_age=timedelta(milliseconds=500),
                max_spread_bps=Decimal("250"),
            ),
        )
    )
    cost_model = _cost()
    qualification = bind_research_costs_to_paper_execution(cost_model=cost_model, matrix=matrix)
    run_at = market.observed_at + timedelta(milliseconds=750)
    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=run_at,
    )
    evidence = build_execution_cost_continuity_evidence(
        evidence_id="w81-stale",
        cost_model=cost_model,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=market_buy_intent,
        market=market,
        assessed_at=run_at,
    )

    assert evidence.status is ExecutionCostContinuityStatus.BLOCKED
    assert "STALE_MARKET_SNAPSHOT" in evidence.blocking_reasons
    rejected = next(item for item in evidence.observations if item.scenario_id == "freshness_stress")
    assert rejected.status is ExecutionCostContinuityStatus.BLOCKED


def test_w81_evidence_hash_is_reproducible_and_authority_is_immutable(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    cost_model = _cost()
    matrix = _matrix()
    qualification = bind_research_costs_to_paper_execution(cost_model=cost_model, matrix=matrix)
    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    kwargs = dict(
        evidence_id="w81-repro",
        cost_model=cost_model,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=market_buy_intent,
        market=market,
        assessed_at=market.observed_at,
    )
    first = build_execution_cost_continuity_evidence(**kwargs)
    second = build_execution_cost_continuity_evidence(**kwargs)

    assert first.evidence_hash == second.evidence_hash
    assert [item.observation_hash for item in first.observations] == [
        item.observation_hash for item in second.observations
    ]

    with pytest.raises(ExecutionCostContinuityIntegrityError, match="fee accounting"):
        replace(first, fee_accounting_complete=True)
    with pytest.raises(ExecutionCostContinuityIntegrityError, match="authorize"):
        replace(first, paper_candidate_authorized=True)
    with pytest.raises(ExecutionCostContinuityIntegrityError, match="capital or LIVE"):
        replace(first, live_trading="ENABLED")
