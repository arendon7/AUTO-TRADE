from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect

import pytest

import autotrade.brokers.alpaca_paper_crypto_canary_coordinator as r6_coordinator
import autotrade.paper_execution_canary_preparation as prep_module
import autotrade.paper_execution_human_review as review_module
import autotrade.paper_execution_risk_contract as risk_module
import autotrade.paper_execution_risk_handoff as handoff_module
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import (
    CryptoPaperCanaryCoordinator,
)
from autotrade.domain import MarketSnapshot, OrderRecord
from autotrade.brokers.base import BrokerExecution
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.paper_execution_canary_preparation_guard import (
    prepare_guarded_paper_execution_canary,
)
from autotrade.paper_execution_human_review import (
    MIN_HUMAN_APPROVAL_REMAINING,
    PAPER_EXECUTION_HUMAN_REVIEW_VERSION,
    PaperExecutionHumanReviewBlocked,
    PaperExecutionHumanReviewIntegrityError,
    PaperExecutionHumanReviewStatus,
    prepare_paper_execution_human_review,
)
from autotrade.paper_execution_risk_contract import (
    evaluate_paper_execution_risk_contract,
)
from autotrade.paper_execution_risk_handoff import (
    PAPER_EXECUTION_RISK_HANDOFF_VERSION,
    PaperExecutionRiskHandoffBlocked,
    PaperExecutionRiskHandoffIntegrityError,
    PaperExecutionRiskHandoffReceipt,
    latch_paper_execution_risk_handoff,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.safety import CapitalSafetyKernel
from autotrade.state import InMemorySafetyStateStore
from test_w87_paper_execution_canary_preparation import _prepare, _stack
from test_w87_paper_execution_risk_contract import (
    _admission,
    _limits,
    _portfolio_store,
    _sealed_time,
)


class _NoWriteBroker:
    def __init__(self) -> None:
        self.submit_calls = 0

    def submit(
        self,
        *,
        order: OrderRecord,
        market: MarketSnapshot,
        now,
    ) -> BrokerExecution:
        self.submit_calls += 1
        raise AssertionError("W87-D must never call ExecutionBroker.submit")


def _long_safety(*, target_version: int) -> CapitalSafetyKernel:
    state = InMemorySafetyStateStore()
    safety = CapitalSafetyKernel(
        replace(_limits(), decision_ttl_ms=15_000),
        InMemoryEventLedger(),
        state_store=state,
    )
    now = _sealed_time()
    while state.get().version < target_version:
        version = state.get().version
        if version % 4 == 0:
            safety.activate_kill_switch(reason="version-bump", now=now)
        elif version % 4 == 1:
            safety.reset_kill_switch(confirmed_by="w87-test", now=now)
        elif version % 4 == 2:
            safety.activate_circuit(reason="version-bump", now=now)
        else:
            safety.acknowledge_circuit(
                confirmed_by="w87-test",
                reason="version-bump-complete",
                now=now,
            )
    assert state.get().kill_switch_active is False
    assert state.get().circuit_active is False
    return safety


def _long_review_stack(monkeypatch, tmp_path):
    monkeypatch.setattr(
        r6_coordinator,
        "FIRST_CANARY_MAX_ACCOUNT_FRACTION",
        Decimal("0.002"),
    )
    sealed, admission = _admission(monkeypatch)
    safety = _long_safety(
        target_version=sealed.pipeline.safety_health_truth.safety_version
    )
    portfolio_store = _portfolio_store(tmp_path)

    monkeypatch.setattr(risk_module, "_now_utc", lambda: sealed.seal.observed_at)
    source_risk = evaluate_paper_execution_risk_contract(
        contract_id="w87-human-risk",
        admission=admission,
        sealed_result=sealed,
        safety=safety,
        portfolio_store=portfolio_store,
    )
    assert source_risk.decision.valid_until > sealed.seal.valid_until
    assert source_risk.receipt.valid_until == sealed.seal.valid_until

    latch_at = sealed.seal.observed_at + timedelta(milliseconds=100)
    monkeypatch.setattr(handoff_module, "_now_utc", lambda: latch_at)
    handoff = latch_paper_execution_risk_handoff(
        handoff_id="w87-human-handoff",
        risk_result=source_risk,
        sealed_result=sealed,
    )
    assert isinstance(handoff.receipt, PaperExecutionRiskHandoffReceipt)
    assert handoff.receipt.valid_until == source_risk.decision.valid_until
    assert handoff.receipt.source_risk_contract_hash == source_risk.receipt.receipt_hash

    prep_at = sealed.seal.observed_at + timedelta(milliseconds=200)
    monkeypatch.setattr(prep_module, "_now_utc", lambda: prep_at)
    broker = _NoWriteBroker()
    oms = OrderManagementSystem(
        broker=broker,
        ledger=InMemoryEventLedger(),
        safety_state_store=safety.state_store,
    )
    coordinator = CryptoPaperCanaryCoordinator(oms=oms)
    runtime = SQLiteRuntime(tmp_path / "w87-human-review.sqlite3")
    preparation = prepare_guarded_paper_execution_canary(
        bridge_id="w87-human-preparation",
        admission=admission,
        sealed_result=sealed,
        risk_result=handoff,
        safety=safety,
        portfolio_store=portfolio_store,
        coordinator=coordinator,
        runtime=runtime,
    )
    return (
        sealed,
        admission,
        source_risk,
        handoff,
        preparation,
        safety,
        portfolio_store,
        broker,
        runtime,
    )


def test_w87_risk_handoff_latches_fresh_seal_but_retains_only_riskdecision_window(
    monkeypatch, tmp_path
):
    sealed, _, source, handoff, preparation, _, _, broker, _ = _long_review_stack(
        monkeypatch, tmp_path
    )
    receipt = handoff.receipt

    assert receipt.contract_version == PAPER_EXECUTION_RISK_HANDOFF_VERSION
    assert receipt.source_risk_contract_hash == source.receipt.receipt_hash
    assert receipt.readiness_seal_hash == sealed.seal.receipt_hash
    assert receipt.handoff_latched_at < sealed.seal.valid_until
    assert receipt.valid_until == source.decision.valid_until
    assert receipt.valid_until > receipt.readiness_seal_valid_until
    assert receipt.seal_fresh_at_handoff is True
    assert receipt.risk_decision_window_retained is True
    assert receipt.oms_handoff_permitted is False
    assert receipt.capital_reserved is False
    assert receipt.broker_write_performed is False
    assert receipt.paper_execution_authorized is False
    assert receipt.external_execution_authorized is False
    assert receipt.runtime_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert preparation.receipt.risk_contract_hash == receipt.receipt_hash
    assert preparation.package.execution_deadline <= receipt.valid_until
    assert preparation.package.execution_deadline > sealed.seal.valid_until
    assert broker.submit_calls == 0


def test_w87_human_review_can_be_prepared_after_seal_expiry_without_issuing_approval(
    monkeypatch, tmp_path
):
    sealed, _, _, handoff, preparation, _, _, broker, _ = _long_review_stack(
        monkeypatch, tmp_path
    )
    review_at = sealed.seal.valid_until + timedelta(seconds=1)
    assert review_at < preparation.package.execution_deadline
    assert (
        preparation.package.execution_deadline - review_at
        >= MIN_HUMAN_APPROVAL_REMAINING
    )
    monkeypatch.setattr(review_module, "_now_utc", lambda: review_at)

    result = prepare_paper_execution_human_review(
        review_id="w87-human-review",
        preparation=preparation,
        risk_handoff=handoff,
    )
    receipt = result.receipt

    assert receipt.contract_version == PAPER_EXECUTION_HUMAN_REVIEW_VERSION
    assert receipt.status is PaperExecutionHumanReviewStatus.REVIEW_PREPARED
    assert receipt.risk_handoff_hash == handoff.receipt.receipt_hash
    assert receipt.package_hash == preparation.package.package_hash
    assert receipt.operator_preparation_hash == result.operator_context.preparation_hash
    assert receipt.approval_challenge.startswith(
        f"APPROVE CRYPTO PAPER {preparation.package.symbol} "
    )
    assert receipt.human_operator_approval_required is True
    assert receipt.operator_decision_status == "NOT_ISSUED"
    assert receipt.operator_decision_issued is False
    assert receipt.operator_decision_consumed is False
    assert receipt.oms_handoff_permitted is False
    assert receipt.capital_reserved is False
    assert receipt.broker_write_performed is False
    assert receipt.external_post_authorized is False
    assert receipt.paper_execution_authorized is False
    assert receipt.runtime_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert receipt.next_action == "HUMAN_OPERATOR_APPROVAL_REQUIRED"
    assert broker.submit_calls == 0


def test_w87_human_review_fails_closed_when_authoritative_decision_window_is_too_short(
    monkeypatch, tmp_path
):
    (
        sealed,
        admission,
        source_risk,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)

    monkeypatch.setattr(handoff_module, "_now_utc", lambda: sealed.seal.observed_at)
    handoff = latch_paper_execution_risk_handoff(
        handoff_id="w87-short-handoff",
        risk_result=source_risk,
        sealed_result=sealed,
    )
    preparation = prepare_guarded_paper_execution_canary(
        bridge_id="w87-short-preparation",
        admission=admission,
        sealed_result=sealed,
        risk_result=handoff,
        safety=safety,
        portfolio_store=portfolio_store,
        coordinator=coordinator,
        runtime=runtime,
    )
    monkeypatch.setattr(review_module, "_now_utc", lambda: sealed.seal.observed_at)

    assert (
        preparation.package.execution_deadline - sealed.seal.observed_at
        < MIN_HUMAN_APPROVAL_REMAINING
    )
    with pytest.raises(
        PaperExecutionHumanReviewBlocked,
        match="too close to expiry",
    ):
        prepare_paper_execution_human_review(
            review_id="w87-short-review",
            preparation=preparation,
            risk_handoff=handoff,
        )
    assert broker.submit_calls == 0


def test_w87_risk_handoff_cannot_be_created_after_source_or_seal_expiry(
    monkeypatch, tmp_path
):
    (
        sealed,
        _,
        source_risk,
        _,
        _,
        _,
        _,
        _,
    ) = _stack(monkeypatch, tmp_path)
    monkeypatch.setattr(
        handoff_module,
        "_now_utc",
        lambda: source_risk.receipt.valid_until + timedelta(microseconds=1),
    )
    with pytest.raises(PaperExecutionRiskHandoffBlocked, match="stale"):
        latch_paper_execution_risk_handoff(
            handoff_id="w87-stale-handoff",
            risk_result=source_risk,
            sealed_result=sealed,
        )


def test_w87_risk_handoff_rejects_source_hash_tamper(monkeypatch, tmp_path):
    _, _, _, handoff, _, _, _, _, _ = _long_review_stack(monkeypatch, tmp_path)
    with pytest.raises(
        PaperExecutionRiskHandoffIntegrityError,
        match="exact source risk contract",
    ):
        replace(
            handoff.receipt,
            source_risk_contract_hash="f" * 64,
        )


def test_w87_human_review_requires_explicit_handoff_not_plain_w87_b(
    monkeypatch, tmp_path
):
    stack, preparation = _prepare(monkeypatch, tmp_path)
    source_risk = stack[2]
    with pytest.raises(
        PaperExecutionHumanReviewIntegrityError,
        match="explicit W87 risk handoff",
    ):
        prepare_paper_execution_human_review(
            review_id="w87-review-no-handoff",
            preparation=preparation,
            risk_handoff=source_risk,
        )


def test_w87_human_review_entrypoints_expose_no_clock_credentials_or_approval_issuer():
    handoff_parameters = inspect.signature(
        latch_paper_execution_risk_handoff
    ).parameters
    review_parameters = inspect.signature(
        prepare_paper_execution_human_review
    ).parameters

    for forbidden in (
        "now",
        "credentials",
        "writer",
        "transport",
        "operator_id",
        "confirmation",
        "registry",
        "runtime",
        "live",
        "environment",
    ):
        assert forbidden not in handoff_parameters
        assert forbidden not in review_parameters
