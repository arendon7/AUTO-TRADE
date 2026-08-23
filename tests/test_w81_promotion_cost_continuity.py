from dataclasses import replace
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
    PromotionCostContinuityStatus,
    resolve_promotion_cost_continuity,
)
from autotrade.research.costs import ExecutionCostModel
from autotrade.strategy_promotion_assessment import ZERO_ASSESSMENT_HASH


def _matrix():
    return build_paper_execution_scenario_matrix(
        (
            build_paper_execution_scenario(
                scenario_id="baseline",
                purpose="W81 candidate baseline",
                slippage_bps=Decimal("2"),
                max_fill_fraction=Decimal("1"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("250"),
            ),
            build_paper_execution_scenario(
                scenario_id="stress",
                purpose="W81 candidate stress",
                slippage_bps=Decimal("8"),
                max_fill_fraction=Decimal("0.5"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("250"),
            ),
        )
    )


def _cost(*, half_spread="2"):
    return ExecutionCostModel(
        fee_bps=Decimal("5"),
        half_spread_bps=Decimal(half_spread),
        slippage_bps=Decimal("2"),
    )


def _continuity(*, limits, market, empty_portfolio, intent, half_spread="2"):
    matrix = _matrix()
    cost = _cost(half_spread=half_spread)
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
        evidence_id="candidate-cost-continuity",
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=intent,
        market=market,
        assessed_at=market.observed_at,
    )
    return report, evidence


def _assessment(*, measurement_hash: str, execution_status=promotion.PromotionGateStatus.PASS):
    gates = []
    for index, gate_id in enumerate(promotion.REQUIRED_W79_GATE_IDS, start=1):
        if gate_id == "EXECUTION_SENSITIVITY":
            status = execution_status
            hashes = tuple(sorted((measurement_hash, "f" * 64)))
        else:
            status = promotion.PromotionGateStatus.PASS
            hashes = (str(index) * 64,)
        reasons = () if status is promotion.PromotionGateStatus.PASS else ("EXECUTION_GATE_TEST_BLOCK",)
        gates.append(
            promotion.PromotionGateEvidence(
                gate_id=gate_id,
                status=status,
                reason_codes=reasons,
                evidence_hashes=hashes,
            )
        )
    gates_tuple = tuple(gates)
    states = {item.status for item in gates_tuple}
    if promotion.PromotionGateStatus.BLOCKED in states:
        state = promotion.PromotionAssessmentState.BLOCKED
    elif promotion.PromotionGateStatus.FAIL in states:
        state = promotion.PromotionAssessmentState.REJECTED
    elif promotion.PromotionGateStatus.MISSING in states:
        state = promotion.PromotionAssessmentState.INCOMPLETE
    else:
        state = promotion.PromotionAssessmentState.EVIDENCE_QUALIFIED
    values = {
        "policy_id": "promotion-a",
        "policy_hash": "a" * 64,
        "threshold_policy_hash": "b" * 64,
        "selected_strategy_id": "strategy-a",
        "selected_strategy_version": "v1",
        "gates": gates_tuple,
        "evidence_complete": all(item.status is promotion.PromotionGateStatus.PASS for item in gates_tuple),
        "assessment_state": state,
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
        assessment_id="assessment-w81",
        view=view,
        ordinal=1,
        previous_assessment_hash=ZERO_ASSESSMENT_HASH,
        assessed_at=datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc),
    )


def _resolved_at(assessment):
    return assessment.assessed_at + timedelta(seconds=1)


def test_resolution_removes_only_continuity_blocker_for_exact_w80_candidate(
    limits, market, empty_portfolio, market_buy_intent
):
    report, continuity = _continuity(
        limits=limits, market=market, empty_portfolio=empty_portfolio, intent=market_buy_intent
    )
    assessment = _assessment(measurement_hash=report.measurement_report_hash)

    resolution = resolve_promotion_cost_continuity(
        resolution_id="w81-resolution-1",
        assessment=assessment,
        continuity=continuity,
        execution_intent=market_buy_intent,
        resolved_at=_resolved_at(assessment),
    )

    assert resolution.status is PromotionCostContinuityStatus.PASS
    assert resolution.reason_codes == ()
    assert resolution.promotion_assessed_at == assessment.assessed_at
    assert resolution.continuity_assessed_at == continuity.assessed_at
    assert resolution.resolved_promotion_blockers == ("TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN",)
    assert "TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN" not in resolution.remaining_promotion_blockers
    assert "FEE_ACCOUNTING_INCOMPLETE" in resolution.remaining_promotion_blockers
    assert "EXECUTION_STRATEGY_VERSION_UNBOUND" in resolution.remaining_promotion_blockers
    assert "SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED" in resolution.remaining_promotion_blockers
    assert resolution.fee_accounting_complete is False
    assert resolution.strategy_version_execution_bound is False
    assert resolution.paper_candidate_authorized is False
    assert resolution.capital_authority == "NONE"
    assert resolution.live_trading == "BLOCKED"


def test_resolution_blocks_if_w81_measurement_is_not_in_w80_execution_gate(
    limits, market, empty_portfolio, market_buy_intent
):
    _, continuity = _continuity(
        limits=limits, market=market, empty_portfolio=empty_portfolio, intent=market_buy_intent
    )
    assessment = _assessment(measurement_hash="e" * 64)

    resolution = resolve_promotion_cost_continuity(
        resolution_id="w81-resolution-unbound",
        assessment=assessment,
        continuity=continuity,
        execution_intent=market_buy_intent,
        resolved_at=_resolved_at(assessment),
    )

    assert resolution.status is PromotionCostContinuityStatus.BLOCKED
    assert resolution.reason_codes == ("W81_MEASUREMENT_NOT_BOUND_TO_W80_EXECUTION_GATE",)
    assert resolution.resolved_promotion_blockers == ()
    assert "TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN" in resolution.remaining_promotion_blockers


def test_resolution_blocks_if_w80_execution_gate_is_not_pass(
    limits, market, empty_portfolio, market_buy_intent
):
    report, continuity = _continuity(
        limits=limits, market=market, empty_portfolio=empty_portfolio, intent=market_buy_intent
    )
    assessment = _assessment(
        measurement_hash=report.measurement_report_hash,
        execution_status=promotion.PromotionGateStatus.BLOCKED,
    )

    resolution = resolve_promotion_cost_continuity(
        resolution_id="w81-resolution-gate-blocked",
        assessment=assessment,
        continuity=continuity,
        execution_intent=market_buy_intent,
        resolved_at=_resolved_at(assessment),
    )

    assert resolution.status is PromotionCostContinuityStatus.BLOCKED
    assert resolution.reason_codes == ("EXECUTION_SENSITIVITY_GATE_NOT_PASS",)
    assert "TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN" in resolution.remaining_promotion_blockers


def test_resolution_blocks_when_scientific_continuity_itself_is_not_proven(
    limits, market, empty_portfolio, market_buy_intent
):
    tight = replace(market, bid=Decimal("99.99"), ask=Decimal("100.01"), last=Decimal("100"))
    report, continuity = _continuity(
        limits=limits,
        market=tight,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        half_spread="5",
    )
    assessment = _assessment(measurement_hash=report.measurement_report_hash)

    resolution = resolve_promotion_cost_continuity(
        resolution_id="w81-resolution-continuity-blocked",
        assessment=assessment,
        continuity=continuity,
        execution_intent=market_buy_intent,
        resolved_at=_resolved_at(assessment),
    )

    assert resolution.status is PromotionCostContinuityStatus.BLOCKED
    assert resolution.reason_codes == ("NON_FEE_CONTINUITY_NOT_PROVEN",)
    assert "TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN" in resolution.remaining_promotion_blockers


def test_resolution_rejects_strategy_or_intent_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    report, continuity = _continuity(
        limits=limits, market=market, empty_portfolio=empty_portfolio, intent=market_buy_intent
    )
    assessment = _assessment(measurement_hash=report.measurement_report_hash)
    resolved_at = _resolved_at(assessment)

    drifted = replace(market_buy_intent, idempotency_key="drift")
    with pytest.raises(PromotionCostContinuityIntegrityError, match="fingerprint mismatch"):
        resolve_promotion_cost_continuity(
            resolution_id="w81-resolution-intent-drift",
            assessment=assessment,
            continuity=continuity,
            execution_intent=drifted,
            resolved_at=resolved_at,
        )

    other_strategy = replace(
        market_buy_intent,
        strategy_id="strategy-other",
        idempotency_key="strategy-other-idem",
    )
    _, other_continuity = _continuity(
        limits=limits, market=market, empty_portfolio=empty_portfolio, intent=other_strategy
    )
    with pytest.raises(PromotionCostContinuityIntegrityError, match="frozen W80 candidate"):
        resolve_promotion_cost_continuity(
            resolution_id="w81-resolution-strategy-drift",
            assessment=assessment,
            continuity=other_continuity,
            execution_intent=other_strategy,
            resolved_at=resolved_at,
        )


def test_resolution_rejects_temporal_regression(
    limits, market, empty_portfolio, market_buy_intent
):
    report, continuity = _continuity(
        limits=limits, market=market, empty_portfolio=empty_portfolio, intent=market_buy_intent
    )
    assessment = _assessment(measurement_hash=report.measurement_report_hash)

    with pytest.raises(PromotionCostContinuityIntegrityError, match="predate W80"):
        resolve_promotion_cost_continuity(
            resolution_id="w81-resolution-time-regression",
            assessment=assessment,
            continuity=continuity,
            execution_intent=market_buy_intent,
            resolved_at=assessment.assessed_at - timedelta(microseconds=1),
        )


def test_resolution_hash_is_reproducible_and_cannot_be_mutated_into_authority(
    limits, market, empty_portfolio, market_buy_intent
):
    report, continuity = _continuity(
        limits=limits, market=market, empty_portfolio=empty_portfolio, intent=market_buy_intent
    )
    assessment = _assessment(measurement_hash=report.measurement_report_hash)
    kwargs = dict(
        resolution_id="w81-resolution-repro",
        assessment=assessment,
        continuity=continuity,
        execution_intent=market_buy_intent,
        resolved_at=_resolved_at(assessment),
    )
    first = resolve_promotion_cost_continuity(**kwargs)
    second = resolve_promotion_cost_continuity(**kwargs)
    assert first.resolution_hash == second.resolution_hash

    with pytest.raises(PromotionCostContinuityIntegrityError, match="fee accounting"):
        replace(first, fee_accounting_complete=True)
    with pytest.raises(PromotionCostContinuityIntegrityError, match="strategy-version"):
        replace(first, strategy_version_execution_bound=True)
    with pytest.raises(PromotionCostContinuityIntegrityError, match="authorize"):
        replace(first, paper_candidate_authorized=True)
    with pytest.raises(PromotionCostContinuityIntegrityError, match="capital or LIVE"):
        replace(first, live_trading="ENABLED")
