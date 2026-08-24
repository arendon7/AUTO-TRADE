from dataclasses import replace
from decimal import Decimal

import pytest

from autotrade.fee_accounting import (
    FeeAccountingIntegrityError,
    FeeAccountingStatus,
    FeeEvidenceSource,
)
from test_w82_fee_accounting import _build, _cost, _matrix


def _valid(limits, market, empty_portfolio, market_buy_intent):
    return _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
    )


def test_w82_contract_rejects_currency_basis_and_source_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    *_, contract, _ = _valid(limits, market, empty_portfolio, market_buy_intent)
    with pytest.raises(FeeAccountingIntegrityError, match="settlement_currency"):
        replace(contract, settlement_currency="usd")
    with pytest.raises(FeeAccountingIntegrityError, match="settlement currency"):
        replace(contract, fee_currency="EUR")
    with pytest.raises(FeeAccountingIntegrityError, match="only filled-notional"):
        replace(contract, fee_basis="ASSET_DEBIT")
    with pytest.raises(FeeAccountingIntegrityError, match="impersonate broker-authoritative"):
        replace(contract, source=FeeEvidenceSource.BROKER_AUTHORITATIVE)
    with pytest.raises(FeeAccountingIntegrityError, match="no broker-authoritative"):
        replace(contract, broker_authoritative_supported=True)
    with pytest.raises(FeeAccountingIntegrityError, match="scope"):
        replace(contract, accounting_scope="REALIZED_BROKER")


def test_w82_observation_rejects_double_count_source_and_net_economics_tamper(
    limits, market, empty_portfolio, market_buy_intent
):
    *_, evidence = _valid(limits, market, empty_portfolio, market_buy_intent)
    observation = evidence.observations[0]
    with pytest.raises(FeeAccountingIntegrityError, match="double-counted"):
        replace(observation, non_fee_components_counted_as_fee=True)
    with pytest.raises(FeeAccountingIntegrityError, match="broker authority"):
        replace(observation, broker_authoritative=True)
    with pytest.raises(FeeAccountingIntegrityError, match="broker authority"):
        replace(observation, source=FeeEvidenceSource.BROKER_AUTHORITATIVE)
    with pytest.raises(FeeAccountingIntegrityError, match="net quote cash delta"):
        replace(
            observation,
            net_quote_cash_delta=observation.net_quote_cash_delta + Decimal("0.01"),
        )
    with pytest.raises(FeeAccountingIntegrityError, match="gross notional"):
        replace(observation, gross_notional=observation.gross_notional + Decimal("1"))


def test_w82_aggregate_rejects_partial_or_falsified_completeness(
    limits, market, empty_portfolio, market_buy_intent
):
    *_, evidence = _valid(limits, market, empty_portfolio, market_buy_intent)
    with pytest.raises(FeeAccountingIntegrityError, match="aggregate fee economics"):
        replace(evidence, total_fee_amount=evidence.total_fee_amount + Decimal("0.01"))
    with pytest.raises(FeeAccountingIntegrityError, match="COMPLETE"):
        replace(evidence, status=FeeAccountingStatus.BLOCKED)
    with pytest.raises(FeeAccountingIntegrityError, match="complete fee accounting"):
        replace(evidence, fee_accounting_complete=False)
    with pytest.raises(FeeAccountingIntegrityError, match="broker-authoritative source"):
        replace(evidence, source=FeeEvidenceSource.BROKER_AUTHORITATIVE)
    with pytest.raises(FeeAccountingIntegrityError, match="evidence hash"):
        replace(evidence, evidence_hash="0" * 64)
    with pytest.raises(FeeAccountingIntegrityError, match="requires scenario observations"):
        replace(evidence, observations=())


def test_w82_zero_research_fee_is_explicitly_complete_not_missing(
    limits, market, empty_portfolio, market_buy_intent
):
    *_, evidence = _build(
        limits=limits,
        market=market,
        empty_portfolio=empty_portfolio,
        intent=market_buy_intent,
        cost=_cost(fee="0"),
    )
    assert evidence.status is FeeAccountingStatus.COMPLETE
    assert evidence.fee_accounting_complete is True
    assert evidence.total_fee_amount == 0
    assert all(item.fee_bps == 0 and item.fee_amount == 0 for item in evidence.observations)


def test_w82_contract_rejects_matrix_identity_drift(
    limits, market, empty_portfolio, market_buy_intent
):
    cost, _, qualification, _, _, _, _ = _valid(
        limits, market, empty_portfolio, market_buy_intent
    )
    other_matrix = _matrix()
    # Same semantic helper is deterministic and therefore produces the same hash;
    # mutate the cost model instead to prove the frozen contract cannot be rebound.
    changed_cost = _cost(fee="7")
    from autotrade.fee_accounting import build_simulated_fee_accounting_contract

    with pytest.raises(FeeAccountingIntegrityError, match="cost model hash"):
        build_simulated_fee_accounting_contract(
            contract_id="w82-rebind-rejected",
            cost_model=changed_cost,
            qualification=qualification,
            matrix=other_matrix,
            product_id="test-product",
            asset_class="crypto",
            venue="alpaca-paper-model",
            settlement_currency="USD",
            created_at=market.observed_at,
        )
