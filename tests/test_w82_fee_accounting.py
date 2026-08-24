from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import OrderType, Side
from autotrade.execution_cost_continuity import build_execution_cost_continuity_evidence
from autotrade.fee_accounting import (
    FeeAccountingIntegrityError,
    FeeAccountingSourceUnavailable,
    FeeAccountingStatus,
    FeeBasis,
    FeeEvidenceSource,
    build_broker_authoritative_fee_accounting_evidence,
    build_simulated_fee_accounting_contract,
    build_simulated_fee_accounting_evidence,
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
        purpose=f"W82 {scenario_id}",
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


def _cost(*, fee: str = "5", half_spread: str = "2"):
    return ExecutionCostModel(
        fee_bps=Decimal(fee),
        half_spread_bps=Decimal(half_spread),
        slippage_bps=Decimal("2"),
    )


def _build(*, limits, market, empty_portfolio, intent, cost=None, matrix=None, assessed_at=None):
    cost = cost or _cost()
    matrix = matrix or _matrix()
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
    continuity = build_execution_cost_continuity_evidence(
        evidence_id="w82-w81-continuity",
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=intent,
        market=market,
        assessed_at=market.observed_at,
    )
    contract = build_simulated_fee_accounting_contract(
        contract_id="w82-fee-contract",
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        product_id="test-product",
        asset_class="crypto",
        venue="alpaca-paper-model",
        settlement_currency="USD",
        created_at=market.observed_at,
    )
    evidence = build_simulated_fee_accounting_evidence(
        evidence_id="w82-fee-evidence",
        contract=contract,
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        continuity=continuity,
        intent=intent,
        market=market,
        assessed_at=assessed_at or market.observed_at,
    )
    return cost, matrix, qualification, report, continuity, contract, evidence


def test_w82_simulated_fee_accounting_is_complete_and_hash_bound(
    limits, market, empty_portfolio, market_buy_intent
):
    _, _, _, report, continuity, contract, evidence = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )

    assert evidence.status is FeeAccountingStatus.COMPLETE
    assert evidence.fee_accounting_complete is True
    assert evidence.source is FeeEvidenceSource.SIMULATED_MODEL
    assert evidence.fee_basis is FeeBasis.FILLED_NOTIONAL_QUOTE
    assert evidence.fee_currency == "USD"
    assert evidence.sensitivity_measurement_hash == report.measurement_report_hash
    assert evidence.w81_continuity_evidence_hash == continuity.evidence_hash
    assert evidence.fee_contract_hash == contract.contract_hash
    assert evidence.broker_authoritative_fee_proven is False
    assert evidence.realized_profitability_authorized is False
    assert evidence.paper_candidate_authorized is False
    assert evidence.external_execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"

    baseline = next(item for item in evidence.observations if item.scenario_id == "baseline")
    stress = next(item for item in evidence.observations if item.scenario_id == "stress")
    assert baseline.filled_quantity == Decimal("10")
    assert baseline.average_fill_price == Decimal("101.0202")
    assert baseline.gross_notional == Decimal("1010.2020")
    assert baseline.fee_amount == Decimal("0.5051010")
    assert baseline.gross_quote_cash_delta == Decimal("-1010.2020")
    assert baseline.net_quote_cash_delta == Decimal("-1010.7071010")
    assert stress.filled_quantity == Decimal("5.0")
    assert stress.average_fill_price == Decimal("101.0808")
    assert stress.gross_notional == Decimal("505.40400")
    assert stress.fee_amount == Decimal("0.25270200")
    assert evidence.total_fee_amount == Decimal("0.75780300")
    assert all(item.non_fee_components_counted_as_fee is False for item in evidence.observations)


def test_w82_sell_side_keeps_fee_as_cost_in_quote_currency(
    limits, market, empty_portfolio, market_buy_intent
):
    sell = replace(
        market_buy_intent,
        intent_id="w82-sell",
        idempotency_key="w82-sell-idem",
        side=Side.SELL,
    )
    *_, evidence = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=sell,
    )
    baseline = next(item for item in evidence.observations if item.scenario_id == "baseline")
    assert baseline.average_fill_price == Decimal("98.9802")
    assert baseline.gross_quote_cash_delta == Decimal("989.8020")
    assert baseline.fee_amount == Decimal("0.4949010")
    assert baseline.net_quote_cash_delta == Decimal("989.3070990")


def test_w82_valid_nonmarketable_limit_has_zero_fee(
    limits, market, empty_portfolio, market_buy_intent
):
    limit_no_fill = replace(
        market_buy_intent,
        intent_id="w82-limit-no-fill",
        idempotency_key="w82-limit-no-fill-idem",
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
    )
    *_, evidence = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=limit_no_fill,
    )
    assert all(item.fill_ratio == 0 for item in evidence.observations)
    assert all(item.filled_quantity == 0 for item in evidence.observations)
    assert all(item.average_fill_price is None for item in evidence.observations)
    assert all(item.gross_notional == 0 for item in evidence.observations)
    assert all(item.fee_amount == 0 for item in evidence.observations)
    assert all(item.net_quote_cash_delta == 0 for item in evidence.observations)
    assert all(item.reason_code == "NO_FILL_NO_FEE" for item in evidence.observations)


def test_w82_rejects_fee_schedule_cost_intent_and_market_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    cost, matrix, qualification, report, continuity, contract, _ = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    changed_cost = _cost(fee="6")
    with pytest.raises(FeeAccountingIntegrityError, match="cost model hash"):
        build_simulated_fee_accounting_evidence(
            evidence_id="fee-cost-drift",
            contract=contract,
            cost_model=changed_cost,
            qualification=qualification,
            matrix=matrix,
            sensitivity_report=report,
            continuity=continuity,
            intent=market_buy_intent,
            market=market,
            assessed_at=market.observed_at,
        )

    changed_intent = replace(market_buy_intent, idempotency_key="fee-intent-drift")
    with pytest.raises(FeeAccountingIntegrityError, match="intent fingerprint"):
        build_simulated_fee_accounting_evidence(
            evidence_id="fee-intent-drift",
            contract=contract,
            cost_model=cost,
            qualification=qualification,
            matrix=matrix,
            sensitivity_report=report,
            continuity=continuity,
            intent=changed_intent,
            market=market,
            assessed_at=market.observed_at,
        )

    changed_market = replace(market, ask=Decimal("101.01"))
    with pytest.raises(FeeAccountingIntegrityError, match="market fingerprint"):
        build_simulated_fee_accounting_evidence(
            evidence_id="fee-market-drift",
            contract=contract,
            cost_model=cost,
            qualification=qualification,
            matrix=matrix,
            sensitivity_report=report,
            continuity=continuity,
            intent=market_buy_intent,
            market=changed_market,
            assessed_at=market.observed_at,
        )


def test_w82_requires_w81_pass_and_temporal_causality(
    limits, market, empty_portfolio, market_buy_intent
):
    tight = replace(market, bid=Decimal("99.99"), ask=Decimal("100.01"), last=Decimal("100"))
    cost = _cost(half_spread="5")
    matrix = _matrix()
    qualification = bind_research_costs_to_paper_execution(cost_model=cost, matrix=matrix)
    report = run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=tight,
        portfolio=empty_portfolio,
        now=tight.observed_at,
    )
    continuity = build_execution_cost_continuity_evidence(
        evidence_id="w82-blocked-w81",
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        sensitivity_report=report,
        intent=market_buy_intent,
        market=tight,
        assessed_at=tight.observed_at,
    )
    contract = build_simulated_fee_accounting_contract(
        contract_id="w82-blocked-contract",
        cost_model=cost,
        qualification=qualification,
        matrix=matrix,
        product_id="test-product",
        asset_class="crypto",
        venue="alpaca-paper-model",
        settlement_currency="USD",
        created_at=tight.observed_at,
    )
    with pytest.raises(FeeAccountingIntegrityError, match="W81 continuity must PASS"):
        build_simulated_fee_accounting_evidence(
            evidence_id="w82-blocked-evidence",
            contract=contract,
            cost_model=cost,
            qualification=qualification,
            matrix=matrix,
            sensitivity_report=report,
            continuity=continuity,
            intent=market_buy_intent,
            market=tight,
            assessed_at=tight.observed_at,
        )

    cost, matrix, qualification, report, continuity, contract, _ = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    with pytest.raises(FeeAccountingIntegrityError, match="temporal causality"):
        build_simulated_fee_accounting_evidence(
            evidence_id="w82-time-regression",
            contract=contract,
            cost_model=cost,
            qualification=qualification,
            matrix=matrix,
            sensitivity_report=report,
            continuity=continuity,
            intent=market_buy_intent,
            market=market,
            assessed_at=market.observed_at - timedelta(microseconds=1),
        )


def test_w82_tamper_and_authority_escalation_fail_closed(
    limits, market, empty_portfolio, market_buy_intent
):
    *_, contract, evidence = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )
    with pytest.raises(FeeAccountingIntegrityError, match="contract hash"):
        replace(contract, contract_hash="0" * 64)
    first = evidence.observations[0]
    with pytest.raises(FeeAccountingIntegrityError, match="fee amount"):
        replace(first, fee_amount=first.fee_amount + Decimal("1"))
    with pytest.raises(FeeAccountingIntegrityError, match="broker-authoritative"):
        replace(evidence, broker_authoritative_fee_proven=True)
    with pytest.raises(FeeAccountingIntegrityError, match="realized-profitability"):
        replace(evidence, realized_profitability_authorized=True)
    with pytest.raises(FeeAccountingIntegrityError, match="authorize PAPER"):
        replace(evidence, paper_candidate_authorized=True)
    with pytest.raises(FeeAccountingIntegrityError, match="capital or LIVE"):
        replace(evidence, live_trading="ENABLED")


def test_w82_broker_authoritative_path_rejects_gross_net_inference():
    with pytest.raises(FeeAccountingSourceUnavailable, match="no direct broker fee source"):
        build_broker_authoritative_fee_accounting_evidence(
            gross_fill_quantity=Decimal("0.00014432"),
            net_position_quantity=Decimal("0.000143959"),
        )
