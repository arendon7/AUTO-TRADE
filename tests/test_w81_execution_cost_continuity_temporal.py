from datetime import timedelta
from decimal import Decimal

from autotrade.execution_cost_continuity import (
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


def test_valid_w78_measurement_does_not_become_stale_when_w81_is_assessed_later(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    cost_model = ExecutionCostModel(
        fee_bps=Decimal("5"),
        half_spread_bps=Decimal("2"),
        slippage_bps=Decimal("2"),
    )
    matrix = build_paper_execution_scenario_matrix(
        (
            build_paper_execution_scenario(
                scenario_id="baseline",
                purpose="W81 valid execution-time freshness",
                slippage_bps=Decimal("2"),
                max_fill_fraction=Decimal("1"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("250"),
            ),
            build_paper_execution_scenario(
                scenario_id="stress",
                purpose="W81 delayed continuity assessment",
                slippage_bps=Decimal("8"),
                max_fill_fraction=Decimal("0.5"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("250"),
            ),
        )
    )
    qualification = bind_research_costs_to_paper_execution(
        cost_model=cost_model,
        matrix=matrix,
    )

    # W78 owns the execution-time freshness decision. The quote is fresh when
    # the sensitivity measurement is produced.
    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    assert all(item.broker_rejection_reason is None for item in report.outcomes)
    assert all(item.measurement_hash is not None for item in report.outcomes)

    # W81 may persist/analyse that immutable W78 evidence later. Elapsed wall
    # time must not retroactively change the original execution-time result.
    evidence = build_execution_cost_continuity_evidence(
        evidence_id="w81-delayed-assessment",
        cost_model=cost_model,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=market_buy_intent,
        market=market,
        assessed_at=market.observed_at + timedelta(minutes=5),
    )

    assert evidence.assessed_at > evidence.observed_at
    assert evidence.status is ExecutionCostContinuityStatus.PASS
    assert evidence.blocking_reasons == ()
    assert all(item.status is ExecutionCostContinuityStatus.PASS for item in evidence.observations)
