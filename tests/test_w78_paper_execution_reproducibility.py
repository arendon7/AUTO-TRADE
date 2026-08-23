from datetime import timedelta
from decimal import Decimal

from autotrade.engine import TradingPipeline
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.paper_execution_evidence import capture_paper_execution_evidence
from autotrade.paper_execution_scenarios import build_paper_execution_scenario
from autotrade.safety import CapitalSafetyKernel


def _run(*, scenario, limits, market, portfolio, intent, captured_at):
    ledger = InMemoryEventLedger()
    broker = scenario.build_broker()
    result = TradingPipeline(
        safety=CapitalSafetyKernel(limits, ledger),
        oms=OrderManagementSystem(broker=broker, ledger=ledger),
    ).process_intent(
        intent=intent,
        market=market,
        portfolio=portfolio,
        now=market.observed_at,
    )
    assert result.order is not None
    return capture_paper_execution_evidence(
        scenario=scenario,
        order=result.order,
        market=market,
        captured_at=captured_at,
    )


def test_same_experiment_reproduces_measurement_but_not_runtime_trace(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    scenario = build_paper_execution_scenario(
        scenario_id="reproducible",
        purpose="Prove semantic execution measurement reproducibility",
        slippage_bps=Decimal("4"),
        max_fill_fraction=Decimal("0.6"),
        max_market_age=timedelta(seconds=2),
        max_spread_bps=Decimal("250"),
    )

    first = _run(
        scenario=scenario,
        limits=limits,
        market=market,
        portfolio=empty_portfolio,
        intent=market_buy_intent,
        captured_at=market.observed_at,
    )
    second = _run(
        scenario=scenario,
        limits=limits,
        market=market,
        portfolio=empty_portfolio,
        intent=market_buy_intent,
        captured_at=market.observed_at + timedelta(milliseconds=1),
    )

    assert first.order_id != second.order_id
    assert first.evidence_hash != second.evidence_hash
    assert first.measurement_hash == second.measurement_hash
    assert first.to_dict()["measurement_hash"] == second.to_dict()["measurement_hash"]


def test_measurement_hash_changes_when_execution_assumption_changes(
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    baseline = build_paper_execution_scenario(
        scenario_id="baseline",
        purpose="Baseline execution",
        slippage_bps=Decimal("2"),
        max_fill_fraction=Decimal("1"),
        max_market_age=timedelta(seconds=2),
        max_spread_bps=Decimal("250"),
    )
    stress = build_paper_execution_scenario(
        scenario_id="stress",
        purpose="Stressed execution",
        slippage_bps=Decimal("8"),
        max_fill_fraction=Decimal("0.5"),
        max_market_age=timedelta(seconds=2),
        max_spread_bps=Decimal("250"),
    )

    first = _run(
        scenario=baseline,
        limits=limits,
        market=market,
        portfolio=empty_portfolio,
        intent=market_buy_intent,
        captured_at=market.observed_at,
    )
    second = _run(
        scenario=stress,
        limits=limits,
        market=market,
        portfolio=empty_portfolio,
        intent=market_buy_intent,
        captured_at=market.observed_at,
    )

    assert first.measurement_hash != second.measurement_hash
    assert first.fill_ratio != second.fill_ratio
    assert first.adverse_slippage_bps != second.adverse_slippage_bps
