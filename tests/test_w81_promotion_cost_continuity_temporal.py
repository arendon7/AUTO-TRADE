from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import autotrade.strategy_lab_promotion as promotion
import autotrade.strategy_promotion_assessment as assessment_module
from autotrade.execution_cost_continuity import build_execution_cost_continuity_evidence
from autotrade.paper_execution_lab import run_paper_execution_sensitivity
from autotrade.paper_execution_qualification import bind_research_costs_to_paper_execution
from autotrade.paper_execution_scenarios import (
    build_paper_execution_scenario,
    build_paper_execution_scenario_matrix,
)
from autotrade.promotion_cost_continuity import (
    PromotionCostContinuityIntegrityError,
    resolve_promotion_cost_continuity,
)
from autotrade.research.costs import ExecutionCostModel
from autotrade.strategy_promotion_assessment import ZERO_ASSESSMENT_HASH


ASSESSMENT_AT = datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc)


def _matrix():
    return build_paper_execution_scenario_matrix(
        (
            build_paper_execution_scenario(
                scenario_id="baseline",
                purpose="W81 temporal baseline",
                slippage_bps=Decimal("2"),
                max_fill_fraction=Decimal("1"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("250"),
            ),
            build_paper_execution_scenario(
                scenario_id="stress",
                purpose="W81 temporal stress",
                slippage_bps=Decimal("8"),
                max_fill_fraction=Decimal("0.5"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("250"),
            ),
        )
    )


def _continuity(*, limits, market, empty_portfolio, intent, assessed_at):
    cost = ExecutionCostModel(
        fee_bps=Decimal("5"),
        half_spread_bps=Decimal("2"),
        slippage_bps=Decimal("2"),
    )
    matrix = _matrix()
    qualification = bind_research_costs_to_paper_execution(cost_model=cost, matrix=matrix)
    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    evidence = build_execution_cost_continuity_evidence(
        evidence_id="candidate-cost-continuity-temporal",
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=intent,
        market=market,
        assessed_at=assessed_at,
    )
    return report, evidence


def _assessment(*, measurement_hash: str, assessed_at: datetime = ASSESSMENT_AT):
    gates = []
    for index, gate_id in enumerate(promotion.REQUIRED_W79_GATE_IDS, start=1):
        hashes = (
            tuple(sorted((measurement_hash, "f" * 64)))
            if gate_id == "EXECUTION_SENSITIVITY"
            else (str(index) * 64,)
        )
        gates.append(
            promotion.PromotionGateEvidence(
                gate_id=gate_id,
                status=promotion.PromotionGateStatus.PASS,
                reason_codes=(),
                evidence_hashes=hashes,
            )
        )
    gates_tuple = tuple(gates)
    values = {
        "policy_id": "promotion-temporal",
        "policy_hash": "a" * 64,
        "threshold_policy_hash": "b" * 64,
        "selected_strategy_id": "strategy-a",
        "selected_strategy_version": "v1",
        "gates": gates_tuple,
        "evidence_complete": True,
        "assessment_state": promotion.PromotionAssessmentState.EVIDENCE_QUALIFIED,
        "promotion_blockers": tuple(sorted(promotion.PERMANENT_W79_PROMOTION_BLOCKERS)),
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    view = promotion.StrategyPromotionEvidenceView(
        **values,
        view_hash=promotion._hash(promotion._view_payload_from_values(values)),
    )
    return assessment_module._build_receipt(
        assessment_id="assessment-w81-temporal",
        view=view,
        ordinal=1,
        previous_assessment_hash=ZERO_ASSESSMENT_HASH,
        assessed_at=assessed_at,
    )


def test_resolution_cannot_predate_w80_assessment(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    report, continuity = _continuity(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        assessed_at=ASSESSMENT_AT - timedelta(seconds=1),
    )
    assessment = _assessment(
        measurement_hash=report.measurement_report_hash,
        assessed_at=ASSESSMENT_AT,
    )

    with pytest.raises(
        PromotionCostContinuityIntegrityError,
        match="may not predate W80 promotion assessment",
    ):
        resolve_promotion_cost_continuity(
            resolution_id="resolution-before-w80",
            assessment=assessment,
            continuity=continuity,
            execution_intent=market_buy_intent,
            resolved_at=ASSESSMENT_AT - timedelta(microseconds=1),
        )


def test_resolution_cannot_predate_w81_continuity_evidence(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    continuity_at = ASSESSMENT_AT + timedelta(seconds=3)
    report, continuity = _continuity(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        assessed_at=continuity_at,
    )
    assessment = _assessment(
        measurement_hash=report.measurement_report_hash,
        assessed_at=ASSESSMENT_AT,
    )

    with pytest.raises(
        PromotionCostContinuityIntegrityError,
        match="may not predate W81 continuity evidence",
    ):
        resolve_promotion_cost_continuity(
            resolution_id="resolution-before-w81",
            assessment=assessment,
            continuity=continuity,
            execution_intent=market_buy_intent,
            resolved_at=continuity_at - timedelta(microseconds=1),
        )


def test_resolution_receipt_preserves_both_source_times_for_revalidation(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    continuity_at = ASSESSMENT_AT + timedelta(seconds=3)
    resolved_at = continuity_at + timedelta(seconds=1)
    report, continuity = _continuity(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        assessed_at=continuity_at,
    )
    assessment = _assessment(
        measurement_hash=report.measurement_report_hash,
        assessed_at=ASSESSMENT_AT,
    )

    resolution = resolve_promotion_cost_continuity(
        resolution_id="resolution-causal",
        assessment=assessment,
        continuity=continuity,
        execution_intent=market_buy_intent,
        resolved_at=resolved_at,
    )

    assert resolution.promotion_assessed_at == ASSESSMENT_AT
    assert resolution.continuity_assessed_at == continuity_at
    assert resolution.resolved_at == resolved_at
    payload = resolution.to_dict()
    assert payload["promotion_assessed_at"] == ASSESSMENT_AT.isoformat()
    assert payload["continuity_assessed_at"] == continuity_at.isoformat()
    assert payload["resolved_at"] == resolved_at.isoformat()
