from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.paper_execution_qualification import (
    FEE_ACCOUNTING_MODE,
    PaperExecutionQualificationError,
    bind_research_costs_to_paper_execution,
)
from autotrade.paper_execution_scenarios import (
    build_paper_execution_scenario,
    build_paper_execution_scenario_matrix,
)
from autotrade.research.costs import ExecutionCostModel


def _scenario(*, scenario_id: str, slippage: str, fill: str):
    return build_paper_execution_scenario(
        scenario_id=scenario_id,
        purpose=f"Qualification case {scenario_id}",
        slippage_bps=Decimal(slippage),
        max_fill_fraction=Decimal(fill),
        max_market_age=timedelta(seconds=2),
        max_spread_bps=Decimal("250"),
    )


def _costs(*, slippage: str = "3"):
    return ExecutionCostModel(
        fee_bps=Decimal("5"),
        half_spread_bps=Decimal("2"),
        slippage_bps=Decimal(slippage),
    )


def test_qualification_contract_is_reproducible_and_grants_no_execution_authority():
    matrix = build_paper_execution_scenario_matrix(
        [
            _scenario(scenario_id="baseline", slippage="3", fill="1"),
            _scenario(scenario_id="liquidity_stress", slippage="8", fill="0.4"),
        ]
    )
    first = bind_research_costs_to_paper_execution(cost_model=_costs(), matrix=matrix)
    replay = bind_research_costs_to_paper_execution(cost_model=_costs(), matrix=matrix)

    assert first == replay
    assert first.contract_hash == replay.contract_hash
    assert first.research_fee_bps == Decimal("5")
    assert first.research_half_spread_bps == Decimal("2")
    assert first.research_slippage_bps == Decimal("3")
    assert first.minimum_scenario_slippage_bps == Decimal("3")
    assert first.maximum_scenario_slippage_bps == Decimal("8")
    assert first.minimum_fill_fraction == Decimal("0.4")
    assert first.scenario_count == 2
    assert first.has_full_liquidity_case is True
    assert first.has_execution_stress_case is True
    assert first.fee_accounting_mode == FEE_ACCOUNTING_MODE
    assert first.external_execution_authorized is False
    assert first.live_trading == "BLOCKED"


def test_every_scenario_must_preserve_or_worsen_research_slippage():
    matrix = build_paper_execution_scenario_matrix(
        [
            _scenario(scenario_id="optimistic", slippage="2", fill="1"),
            _scenario(scenario_id="stress", slippage="8", fill="0.5"),
        ]
    )

    with pytest.raises(PaperExecutionQualificationError, match="at least as adverse"):
        bind_research_costs_to_paper_execution(cost_model=_costs(slippage="3"), matrix=matrix)


def test_matrix_requires_full_liquidity_reference_case():
    matrix = build_paper_execution_scenario_matrix(
        [
            _scenario(scenario_id="partial_a", slippage="3", fill="0.8"),
            _scenario(scenario_id="partial_b", slippage="6", fill="0.4"),
        ]
    )

    with pytest.raises(PaperExecutionQualificationError, match="full-liquidity"):
        bind_research_costs_to_paper_execution(cost_model=_costs(), matrix=matrix)


def test_matrix_requires_stricter_execution_stress_case():
    matrix = build_paper_execution_scenario_matrix(
        [
            build_paper_execution_scenario(
                scenario_id="same_a",
                purpose="same slippage full fill A",
                slippage_bps=Decimal("3"),
                max_fill_fraction=Decimal("1"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("250"),
            ),
            build_paper_execution_scenario(
                scenario_id="same_b",
                purpose="same slippage full fill B with distinct spread gate",
                slippage_bps=Decimal("3"),
                max_fill_fraction=Decimal("1"),
                max_market_age=timedelta(seconds=2),
                max_spread_bps=Decimal("300"),
            ),
        ]
    )

    with pytest.raises(PaperExecutionQualificationError, match="stricter than research"):
        bind_research_costs_to_paper_execution(cost_model=_costs(), matrix=matrix)


def test_contract_hash_detects_semantically_valid_tampering():
    matrix = build_paper_execution_scenario_matrix(
        [
            _scenario(scenario_id="baseline", slippage="3", fill="1"),
            _scenario(scenario_id="stress", slippage="7", fill="0.5"),
        ]
    )
    contract = bind_research_costs_to_paper_execution(cost_model=_costs(), matrix=matrix)

    with pytest.raises(PaperExecutionQualificationError, match="hash mismatch"):
        replace(contract, research_fee_bps=Decimal("6"))


def test_contract_cannot_be_mutated_to_grant_external_authority():
    matrix = build_paper_execution_scenario_matrix(
        [
            _scenario(scenario_id="baseline", slippage="3", fill="1"),
            _scenario(scenario_id="stress", slippage="7", fill="0.5"),
        ]
    )
    contract = bind_research_costs_to_paper_execution(cost_model=_costs(), matrix=matrix)

    with pytest.raises(PaperExecutionQualificationError, match="may not grant"):
        replace(contract, external_execution_authorized=True)
    with pytest.raises(PaperExecutionQualificationError, match="may not grant"):
        replace(contract, live_trading="ENABLED")
