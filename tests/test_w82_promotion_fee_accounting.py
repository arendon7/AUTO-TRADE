from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import autotrade.strategy_lab_promotion as promotion
import autotrade.strategy_promotion_assessment as assessment_module
from autotrade.execution_cost_continuity import build_execution_cost_continuity_evidence
from autotrade.fee_accounting import (
    FeeAccountingIntegrityError,
    build_simulated_fee_accounting_contract,
    build_simulated_fee_accounting_evidence,
)
from autotrade.fee_product_economics import (
    FeeChargeConvention,
    FeeLiquidityRole,
    build_fee_product_economics_evidence,
    build_fee_product_policy,
)
from autotrade.fee_schedule_attestation import (
    ALPACA_CRYPTO_CONSERVATIVE_FLOOR_BPS,
    build_alpaca_crypto_worst_case_fee_attestation,
)
from autotrade.paper_execution_lab import run_paper_execution_sensitivity
from autotrade.paper_execution_qualification import bind_research_costs_to_paper_execution
from autotrade.paper_execution_scenarios import (
    build_paper_execution_scenario,
    build_paper_execution_scenario_matrix,
)
from autotrade.promotion_cost_continuity import resolve_promotion_cost_continuity
from autotrade.promotion_fee_accounting import (
    PromotionFeeAccountingIntegrityError,
    PromotionFeeAccountingStatus,
    SHADOW_FORWARD_BLOCKER,
    STRATEGY_VERSION_BLOCKER,
    resolve_promotion_fee_accounting,
)
from autotrade.research.costs import ExecutionCostModel
from autotrade.strategy_promotion_assessment import ZERO_ASSESSMENT_HASH


def _matrix():
    return build_paper_execution_scenario_matrix(
        (
            build_paper_execution_scenario(
                scenario_id="baseline",
                purpose="W82 candidate baseline",
                slippage_bps=Decimal("2"),
                max_fill_fraction=Decimal("1"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("250"),
            ),
            build_paper_execution_scenario(
                scenario_id="stress",
                purpose="W82 candidate stress",
                slippage_bps=Decimal("8"),
                max_fill_fraction=Decimal("0.5"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("250"),
            ),
        )
    )


def _assessment(*, measurement_hash: str):
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
        "policy_id": "promotion-a",
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
        assessment_id="assessment-w82",
        view=view,
        ordinal=1,
        previous_assessment_hash=ZERO_ASSESSMENT_HASH,
        assessed_at=datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc),
    )


def _candidate(
    *,
    limits,
    market,
    empty_portfolio,
    intent,
    bind_measurement=True,
    research_fee_bps="25",
    minimum_fee_bps=None,
):
    minimum_fee_bps = minimum_fee_bps or research_fee_bps
    cost = ExecutionCostModel(
        fee_bps=Decimal(research_fee_bps),
        half_spread_bps=Decimal("2"),
        slippage_bps=Decimal("2"),
    )
    matrix = _matrix()
    qualification = bind_research_costs_to_paper_execution(
        cost_model=cost, matrix=matrix
    )
    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )
    continuity = build_execution_cost_continuity_evidence(
        evidence_id="w82-candidate-continuity",
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=intent,
        market=market,
        assessed_at=market.observed_at,
    )
    assessment = _assessment(
        measurement_hash=(
            report.measurement_report_hash if bind_measurement else "e" * 64
        )
    )
    w81 = resolve_promotion_cost_continuity(
        resolution_id="w81-before-w82",
        assessment=assessment,
        continuity=continuity,
        execution_intent=intent,
        resolved_at=assessment.assessed_at + timedelta(seconds=1),
    )
    contract = build_simulated_fee_accounting_contract(
        contract_id="w82-candidate-fee-contract",
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        product_id="test-product",
        asset_class="crypto",
        venue="alpaca-paper-model",
        settlement_currency="USD",
        created_at=market.observed_at,
    )
    fee = build_simulated_fee_accounting_evidence(
        evidence_id="w82-candidate-fee-evidence",
        contract=contract,
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        continuity=continuity,
        intent=intent,
        market=market,
        assessed_at=w81.resolved_at + timedelta(seconds=1),
    )
    policy = build_fee_product_policy(
        policy_id="w82-product-fee-policy",
        product_id="test-product",
        asset_class="crypto",
        venue="alpaca-paper-model",
        symbol=intent.symbol,
        base_currency="TEST",
        quote_currency="USD",
        charge_convention=FeeChargeConvention.RECEIVED_ASSET_PERCENT,
        liquidity_role=FeeLiquidityRole.WORST_CASE,
        minimum_fee_bps=Decimal(minimum_fee_bps),
        source_reference="w82-hash-bound-product-policy",
        effective_at=market.observed_at - timedelta(seconds=1),
    )
    product = build_fee_product_economics_evidence(
        evidence_id="w82-candidate-product-economics",
        policy=policy,
        fee_evidence=fee,
        cost_model=cost,
        execution_intent=intent,
        assessed_at=fee.assessed_at + timedelta(seconds=1),
    )
    attestation = build_alpaca_crypto_worst_case_fee_attestation(
        attestation_id="w82-alpaca-fee-schedule",
        product_id=product.product_id,
        venue=product.venue,
        symbol=product.symbol,
    )
    return w81, fee, product, attestation


def _resolve(*, resolution_id, w81, fee, product, attestation, intent):
    return resolve_promotion_fee_accounting(
        resolution_id=resolution_id,
        w81_resolution=w81,
        fee_evidence=fee,
        product_economics=product,
        fee_schedule_attestation=attestation,
        execution_intent=intent,
        resolved_at=product.assessed_at + timedelta(seconds=1),
    )


def test_w82_resolution_removes_only_fee_blocker_for_exact_w81_candidate(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    result = _resolve(
        resolution_id="w82-resolution-pass",
        w81=w81,
        fee=fee,
        product=product,
        attestation=attestation,
        intent=market_buy_intent,
    )
    assert result.status is PromotionFeeAccountingStatus.PASS
    assert result.resolved_promotion_blockers == ("FEE_ACCOUNTING_INCOMPLETE",)
    assert "FEE_ACCOUNTING_INCOMPLETE" not in result.remaining_promotion_blockers
    assert STRATEGY_VERSION_BLOCKER in result.remaining_promotion_blockers
    assert SHADOW_FORWARD_BLOCKER in result.remaining_promotion_blockers
    assert result.fee_accounting_complete is True
    assert result.fee_schedule_conservative is True
    assert result.product_fee_economics_complete is True
    assert result.documented_fee_floor_satisfied is True
    assert result.documented_fee_floor_bps == ALPACA_CRYPTO_CONSERVATIVE_FLOOR_BPS
    assert result.fee_schedule_attestation_hash == attestation.attestation_hash
    assert result.broker_authoritative_fee_proven is False
    assert result.realized_profitability_authorized is False
    assert result.strategy_version_execution_bound is False
    assert result.shadow_forward_promotion_bound is False
    assert result.paper_candidate_authorized is False
    assert result.external_execution_authorized is False
    assert result.capital_authority == "NONE"
    assert result.live_trading == "BLOCKED"


def test_w82_resolution_blocks_when_w81_candidate_resolution_did_not_pass(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        bind_measurement=False,
    )
    result = _resolve(
        resolution_id="w82-resolution-w81-blocked",
        w81=w81,
        fee=fee,
        product=product,
        attestation=attestation,
        intent=market_buy_intent,
    )
    assert result.status is PromotionFeeAccountingStatus.BLOCKED
    assert result.reason_codes == ("W81_CONTINUITY_RESOLUTION_NOT_PASS",)
    assert result.resolved_promotion_blockers == ()
    assert "FEE_ACCOUNTING_INCOMPLETE" in result.remaining_promotion_blockers
    assert result.fee_accounting_complete is False


def test_w82_caller_cannot_lower_documented_alpaca_floor_to_make_five_bps_pass(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        research_fee_bps="5",
        minimum_fee_bps="5",
    )
    assert product.fee_schedule_conservative is True
    assert product.product_fee_economics_complete is True

    result = _resolve(
        resolution_id="w82-resolution-document-floor",
        w81=w81,
        fee=fee,
        product=product,
        attestation=attestation,
        intent=market_buy_intent,
    )
    assert result.status is PromotionFeeAccountingStatus.BLOCKED
    assert result.reason_codes == (
        "PRODUCT_POLICY_BELOW_DOCUMENTED_BROKER_FLOOR",
        "RESEARCH_FEE_BELOW_DOCUMENTED_BROKER_FLOOR",
    )
    assert result.documented_fee_floor_satisfied is False
    assert "FEE_ACCOUNTING_INCOMPLETE" in result.remaining_promotion_blockers


def test_w82_resolution_blocks_when_research_fee_is_below_product_policy(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        research_fee_bps="5",
        minimum_fee_bps="25",
    )
    result = _resolve(
        resolution_id="w82-resolution-underpriced",
        w81=w81,
        fee=fee,
        product=product,
        attestation=attestation,
        intent=market_buy_intent,
    )
    assert result.status is PromotionFeeAccountingStatus.BLOCKED
    assert result.reason_codes == (
        "FEE_PRODUCT_ECONOMICS_NOT_PASS",
        "FEE_SCHEDULE_NOT_CONSERVATIVE",
        "PRODUCT_FEE_ECONOMICS_INCOMPLETE",
        "RESEARCH_FEE_BELOW_DOCUMENTED_BROKER_FLOOR",
    )
    assert result.documented_fee_floor_satisfied is False
    assert "FEE_ACCOUNTING_INCOMPLETE" in result.remaining_promotion_blockers


def test_w82_resolution_rejects_identity_tamper_and_temporal_regression(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    drifted = replace(market_buy_intent, idempotency_key="w82-intent-drift")
    with pytest.raises(PromotionFeeAccountingIntegrityError, match="fingerprint mismatch"):
        resolve_promotion_fee_accounting(
            resolution_id="w82-resolution-intent-drift",
            w81_resolution=w81,
            fee_evidence=fee,
            product_economics=product,
            fee_schedule_attestation=attestation,
            execution_intent=drifted,
            resolved_at=product.assessed_at + timedelta(seconds=1),
        )
    with pytest.raises(FeeAccountingIntegrityError, match="evidence hash"):
        replace(fee, w81_continuity_evidence_hash="c" * 64)
    with pytest.raises(PromotionFeeAccountingIntegrityError, match="predate product fee evidence"):
        resolve_promotion_fee_accounting(
            resolution_id="w82-resolution-time-regression",
            w81_resolution=w81,
            fee_evidence=fee,
            product_economics=product,
            fee_schedule_attestation=attestation,
            execution_intent=market_buy_intent,
            resolved_at=product.assessed_at - timedelta(microseconds=1),
        )


def test_w82_resolution_hash_reproducible_and_cannot_mint_authority(
    limits, market, empty_portfolio, market_buy_intent
):
    w81, fee, product, attestation = _candidate(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    kwargs = dict(
        resolution_id="w82-resolution-repro",
        w81_resolution=w81,
        fee_evidence=fee,
        product_economics=product,
        fee_schedule_attestation=attestation,
        execution_intent=market_buy_intent,
        resolved_at=product.assessed_at + timedelta(seconds=1),
    )
    first = resolve_promotion_fee_accounting(**kwargs)
    second = resolve_promotion_fee_accounting(**kwargs)
    assert first.resolution_hash == second.resolution_hash
    with pytest.raises(PromotionFeeAccountingIntegrityError, match="documented broker fee floor"):
        replace(first, documented_fee_floor_satisfied=False)
    with pytest.raises(PromotionFeeAccountingIntegrityError, match="broker-authoritative"):
        replace(first, broker_authoritative_fee_proven=True)
    with pytest.raises(PromotionFeeAccountingIntegrityError, match="realized-profitability"):
        replace(first, realized_profitability_authorized=True)
    with pytest.raises(PromotionFeeAccountingIntegrityError, match="strategy-version"):
        replace(first, strategy_version_execution_bound=True)
    with pytest.raises(PromotionFeeAccountingIntegrityError, match="shadow/forward"):
        replace(first, shadow_forward_promotion_bound=True)
    with pytest.raises(PromotionFeeAccountingIntegrityError, match="authorize PAPER"):
        replace(first, paper_candidate_authorized=True)
    with pytest.raises(PromotionFeeAccountingIntegrityError, match="capital or LIVE"):
        replace(first, live_trading="ENABLED")
