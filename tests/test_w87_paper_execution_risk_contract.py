from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect

import pytest

import autotrade.paper_execution_admission as admission_module
import autotrade.paper_execution_risk_contract as risk_module
from autotrade.domain import PortfolioSnapshot, RiskDecisionStatus
from autotrade.ledger import InMemoryEventLedger
from autotrade.paper_execution_admission import capture_paper_execution_admission
from autotrade.paper_execution_risk_contract import (
    PAPER_EXECUTION_RISK_CONTRACT_VERSION,
    PaperExecutionRiskContractBlocked,
    PaperExecutionRiskContractIntegrityError,
    PaperExecutionRiskContractStatus,
    evaluate_paper_execution_risk_contract,
)
from autotrade.persistence import SQLitePortfolioStore, SQLiteRuntime
from autotrade.safety import CapitalSafetyKernel, SafetyLimits
from autotrade.state import InMemorySafetyStateStore, VersionedPortfolioSnapshot
from test_w87_paper_execution_admission import _sealed


def _admission(monkeypatch):
    sealed = _sealed(monkeypatch)
    monkeypatch.setattr(admission_module, "_now_utc", lambda: sealed.seal.observed_at)
    admission = capture_paper_execution_admission(
        admission_id="w87-risk-source",
        sealed_result=sealed,
    )
    return sealed, admission


def _limits() -> SafetyLimits:
    return SafetyLimits(
        limits_version="w87-risk-v1",
        allowed_symbols=frozenset({"TEST/USD"}),
        allowed_order_types=frozenset({risk_module.OrderType.LIMIT}),
        max_order_notional=Decimal("5"),
        max_position_notional=Decimal("5"),
        max_strategy_gross_exposure=Decimal("5"),
        max_portfolio_gross_exposure=Decimal("5"),
        max_net_exposure=Decimal("5"),
        max_leverage=Decimal("1"),
        max_daily_loss=Decimal("10"),
        max_drawdown=Decimal("0.50"),
        max_open_orders=1,
        stale_market_data_ms=5000,
        price_deviation_bps=Decimal("100"),
        decision_ttl_ms=500,
    )


def _flat_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="w87-risk-portfolio-flat",
        equity=Decimal("1000"),
        gross_exposure=Decimal("0"),
        net_exposure=Decimal("0"),
        daily_pnl=Decimal("0"),
        drawdown=Decimal("0"),
        open_orders=0,
        signed_position_notional_by_symbol={},
        strategy_gross_exposure={},
        strategy_signed_position_notional_by_symbol={},
        reconciliation_ok=True,
        broker_state_known=True,
    )


def _safety(*, target_version: int = 4) -> CapitalSafetyKernel:
    state = InMemorySafetyStateStore()
    ledger = InMemoryEventLedger()
    safety = CapitalSafetyKernel(_limits(), ledger, state_store=state)
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


def _sealed_time():
    # State transition timestamps are audit-only here; the bridge compares the
    # canonical version and flags against W86 before evaluating fresh market.
    from test_w86_paper_runtime_market_truth import AT

    return AT


def _portfolio_store(tmp_path):
    runtime = SQLiteRuntime(tmp_path / "w87-risk.sqlite3")
    store = SQLitePortfolioStore(runtime)
    store.initialize(_flat_portfolio(), now=_sealed_time())
    return store


def _evaluate(monkeypatch, tmp_path, *, safety=None, portfolio_store=None):
    sealed, admission = _admission(monkeypatch)
    monkeypatch.setattr(risk_module, "_now_utc", lambda: sealed.seal.observed_at)
    result = evaluate_paper_execution_risk_contract(
        contract_id="w87-risk-contract",
        admission=admission,
        sealed_result=sealed,
        safety=safety or _safety(target_version=sealed.pipeline.safety_health_truth.safety_version),
        portfolio_store=portfolio_store or _portfolio_store(tmp_path),
    )
    return sealed, admission, result


def test_w87_risk_contract_uses_authoritative_safety_and_grants_zero_execution_authority(
    monkeypatch, tmp_path
):
    sealed, admission, result = _evaluate(monkeypatch, tmp_path)
    receipt = result.receipt

    assert receipt.contract_version == PAPER_EXECUTION_RISK_CONTRACT_VERSION
    assert receipt.status is PaperExecutionRiskContractStatus.RISK_APPROVED
    assert result.decision.status is RiskDecisionStatus.APPROVED
    assert receipt.admission_hash == admission.receipt_hash
    assert receipt.readiness_seal_hash == sealed.seal.receipt_hash
    assert receipt.market_snapshot_fingerprint == sealed.pipeline.market_truth.market_snapshot_fingerprint
    assert result.intent.symbol == admission.broker_pair == "TEST/USD"
    assert result.intent.quantity == admission.canary_quantity == Decimal("0.010")
    assert result.intent.limit_price == admission.conservative_limit_price == Decimal("101")
    assert receipt.approved_notional_usd == admission.canary_notional_usd == Decimal("1.010")
    assert receipt.valid_until == min(result.decision.valid_until, sealed.seal.valid_until)
    assert receipt.exact_admission_binding_verified is True
    assert receipt.exact_market_binding_verified is True
    assert receipt.authoritative_safety_approval_verified is True
    assert receipt.portfolio_flatness_verified is True
    assert receipt.portfolio_unchanged_during_evaluation is True
    assert receipt.safety_state_unchanged_during_evaluation is True
    assert receipt.separate_human_execution_approval_required is True
    assert receipt.oms_handoff_permitted is False
    assert receipt.capital_reserved is False
    assert receipt.broker_write_performed is False
    assert receipt.paper_execution_authorized is False
    assert receipt.external_execution_authorized is False
    assert receipt.runtime_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert receipt.next_action == "CANARY_PREPARATION_REQUIRED"


def test_w87_risk_contract_intent_identity_is_deterministic_from_admission(monkeypatch, tmp_path):
    _, admission, first = _evaluate(monkeypatch, tmp_path)
    first_intent = first.intent
    rebuilt = risk_module._exact_intent(admission)

    assert rebuilt == first_intent
    assert rebuilt.intent_id.startswith("w87-risk:")
    assert rebuilt.idempotency_key.startswith("w87-risk-idem:")
    assert rebuilt.created_at == admission.captured_at


def test_w87_risk_contract_entrypoint_exposes_no_caller_clock_or_market():
    parameters = inspect.signature(evaluate_paper_execution_risk_contract).parameters
    for forbidden in ("now", "observed_at", "evaluated_at", "market", "intent", "decision"):
        assert forbidden not in parameters


def test_w87_risk_contract_refuses_stale_w86_seal(monkeypatch, tmp_path):
    sealed, admission = _admission(monkeypatch)
    monkeypatch.setattr(
        risk_module,
        "_now_utc",
        lambda: sealed.seal.valid_until + timedelta(microseconds=1),
    )
    safety = _safety(target_version=sealed.pipeline.safety_health_truth.safety_version)
    with pytest.raises(PaperExecutionRiskContractBlocked, match="not fresh"):
        evaluate_paper_execution_risk_contract(
            contract_id="w87-risk-stale",
            admission=admission,
            sealed_result=sealed,
            safety=safety,
            portfolio_store=_portfolio_store(tmp_path),
        )


def test_w87_risk_contract_refuses_safety_version_drift_even_when_controls_are_clear(
    monkeypatch, tmp_path
):
    sealed, admission = _admission(monkeypatch)
    monkeypatch.setattr(risk_module, "_now_utc", lambda: sealed.seal.observed_at)
    safety = _safety(target_version=sealed.pipeline.safety_health_truth.safety_version + 4)

    with pytest.raises(PaperExecutionRiskContractBlocked, match="no longer matches"):
        evaluate_paper_execution_risk_contract(
            contract_id="w87-risk-safety-version-drift",
            admission=admission,
            sealed_result=sealed,
            safety=safety,
            portfolio_store=_portfolio_store(tmp_path),
        )


def test_w87_risk_contract_refuses_nonflat_or_unreconciled_local_portfolio(monkeypatch):
    sealed, admission = _admission(monkeypatch)
    monkeypatch.setattr(risk_module, "_now_utc", lambda: sealed.seal.observed_at)
    safety = _safety(target_version=sealed.pipeline.safety_health_truth.safety_version)
    dirty = replace(
        _flat_portfolio(),
        snapshot_id="w87-risk-dirty",
        reconciliation_ok=False,
    )

    class Reader:
        def get(self):
            return VersionedPortfolioSnapshot(version=1, snapshot=dirty)

    with pytest.raises(PaperExecutionRiskContractBlocked, match="flat, reconciled"):
        evaluate_paper_execution_risk_contract(
            contract_id="w87-risk-dirty",
            admission=admission,
            sealed_result=sealed,
            safety=safety,
            portfolio_store=Reader(),
        )


def test_w87_risk_contract_fails_closed_if_portfolio_changes_during_safety(monkeypatch):
    sealed, admission = _admission(monkeypatch)
    monkeypatch.setattr(risk_module, "_now_utc", lambda: sealed.seal.observed_at)
    safety = _safety(target_version=sealed.pipeline.safety_health_truth.safety_version)
    first = VersionedPortfolioSnapshot(version=1, snapshot=_flat_portfolio())
    second = VersionedPortfolioSnapshot(version=2, snapshot=_flat_portfolio())

    class RacingReader:
        def __init__(self):
            self.calls = 0

        def get(self):
            self.calls += 1
            return first if self.calls == 1 else second

    with pytest.raises(PaperExecutionRiskContractBlocked, match="portfolio state changed"):
        evaluate_paper_execution_risk_contract(
            contract_id="w87-risk-portfolio-race",
            admission=admission,
            sealed_result=sealed,
            safety=safety,
            portfolio_store=RacingReader(),
        )


def test_w87_risk_contract_fails_closed_if_safety_changes_after_approval(monkeypatch, tmp_path):
    sealed, admission = _admission(monkeypatch)
    monkeypatch.setattr(risk_module, "_now_utc", lambda: sealed.seal.observed_at)

    class RacingSafety(CapitalSafetyKernel):
        def evaluate(self, *, intent, market, portfolio, now):
            decision = super().evaluate(
                intent=intent,
                market=market,
                portfolio=portfolio,
                now=now,
            )
            self.activate_kill_switch(reason="race-after-approval", now=now)
            return decision

    base = _safety(target_version=sealed.pipeline.safety_health_truth.safety_version)
    racing = RacingSafety(_limits(), InMemoryEventLedger(), state_store=base.state_store)

    with pytest.raises(PaperExecutionRiskContractBlocked, match="changed during"):
        evaluate_paper_execution_risk_contract(
            contract_id="w87-risk-safety-race",
            admission=admission,
            sealed_result=sealed,
            safety=racing,
            portfolio_store=_portfolio_store(tmp_path),
        )


def test_w87_risk_contract_rejects_market_binding_tamper_before_safety(monkeypatch, tmp_path):
    sealed, admission = _admission(monkeypatch)
    object.__setattr__(sealed.pipeline.market_truth, "market_snapshot_fingerprint", "0" * 64)
    monkeypatch.setattr(risk_module, "_now_utc", lambda: sealed.seal.observed_at)
    safety = _safety(target_version=sealed.pipeline.safety_health_truth.safety_version)

    with pytest.raises(Exception, match="fingerprint|hash mismatch"):
        evaluate_paper_execution_risk_contract(
            contract_id="w87-risk-market-tamper",
            admission=admission,
            sealed_result=sealed,
            safety=safety,
            portfolio_store=_portfolio_store(tmp_path),
        )


@pytest.mark.parametrize(
    "field,value",
    (
        ("oms_handoff_permitted", True),
        ("capital_reserved", True),
        ("broker_write_performed", True),
        ("paper_execution_authorized", True),
        ("external_execution_authorized", True),
        ("runtime_execution_authorized", True),
        ("capital_authority", "RESERVED"),
        ("live_trading", "ENABLED"),
        ("separate_human_execution_approval_required", False),
        ("next_action", "POST_ALLOWED"),
    ),
)
def test_w87_risk_contract_receipt_rejects_every_authority_escalation(
    monkeypatch, tmp_path, field, value
):
    _, _, result = _evaluate(monkeypatch, tmp_path)
    with pytest.raises(PaperExecutionRiskContractIntegrityError, match="may prove Safety approval only"):
        replace(result.receipt, **{field: value})


def test_w87_risk_contract_receipt_rejects_hash_notional_and_ttl_tamper(monkeypatch, tmp_path):
    _, _, result = _evaluate(monkeypatch, tmp_path)
    receipt = result.receipt
    with pytest.raises(PaperExecutionRiskContractIntegrityError, match="hash mismatch"):
        replace(receipt, receipt_hash="0" * 64)
    with pytest.raises(PaperExecutionRiskContractIntegrityError, match="quantity \* limit price"):
        replace(receipt, approved_notional_usd=receipt.approved_notional_usd + Decimal("0.01"))
    with pytest.raises(PaperExecutionRiskContractIntegrityError, match="exact minimum"):
        replace(receipt, valid_until=receipt.valid_until + timedelta(microseconds=1))
