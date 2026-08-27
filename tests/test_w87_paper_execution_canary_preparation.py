from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect

import pytest

import autotrade.brokers.alpaca_paper_crypto_canary_coordinator as r6_coordinator
import autotrade.paper_execution_canary_preparation as prep_module
import autotrade.paper_execution_canary_preparation_guard as guard_module
import autotrade.paper_execution_risk_contract as risk_module
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import CryptoPaperCanaryCoordinator
from autotrade.brokers.base import BrokerExecution
from autotrade.domain import MarketSnapshot, OrderRecord
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.paper_execution_canary_preparation import (
    PAPER_EXECUTION_CANARY_PREPARATION_VERSION,
    PaperExecutionCanaryPreparationBlocked,
    PaperExecutionCanaryPreparationIntegrityError,
    PaperExecutionCanaryPreparationStatus,
)
from autotrade.paper_execution_canary_preparation_guard import (
    PaperExecutionCanaryPreparationGuardBlocked,
    prepare_guarded_paper_execution_canary,
)
from autotrade.paper_execution_risk_contract import evaluate_paper_execution_risk_contract
from autotrade.persistence import SQLiteRuntime
from autotrade.state import VersionedPortfolioSnapshot
from test_w87_paper_execution_risk_contract import (
    _admission,
    _portfolio_store,
    _safety,
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
        raise AssertionError("W87-C must never call ExecutionBroker.submit")


def _stack(monkeypatch, tmp_path, *, relax_r6_cap: bool = True):
    # The shared W86 fixture deliberately models a USD 1,000 portfolio. The
    # production R6 cap is 0.1%, i.e. USD 1.00, while the canonical W87 fixture
    # rounds to USD 1.010 because of broker quantity increments. Most W87-C tests
    # need to exercise behavior *after* that cap, so they temporarily widen only
    # the coordinator constant to 0.2%. A dedicated test below leaves production
    # 0.1% untouched and proves the bridge blocks before OMS/lifecycle mutation.
    if relax_r6_cap:
        monkeypatch.setattr(
            r6_coordinator,
            "FIRST_CANARY_MAX_ACCOUNT_FRACTION",
            Decimal("0.002"),
        )

    sealed, admission = _admission(monkeypatch)
    safety = _safety(
        target_version=sealed.pipeline.safety_health_truth.safety_version
    )
    portfolio_store = _portfolio_store(tmp_path)
    monkeypatch.setattr(risk_module, "_now_utc", lambda: sealed.seal.observed_at)
    risk_result = evaluate_paper_execution_risk_contract(
        contract_id="w87-prep-risk",
        admission=admission,
        sealed_result=sealed,
        safety=safety,
        portfolio_store=portfolio_store,
    )
    monkeypatch.setattr(prep_module, "_now_utc", lambda: sealed.seal.observed_at)
    broker = _NoWriteBroker()
    oms = OrderManagementSystem(
        broker=broker,
        ledger=InMemoryEventLedger(),
        safety_state_store=safety.state_store,
    )
    coordinator = CryptoPaperCanaryCoordinator(oms=oms)
    runtime = SQLiteRuntime(tmp_path / "w87-canary-preparation.sqlite3")
    return (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    )


def _prepare(monkeypatch, tmp_path, *, bridge_id="w87-preparation"):
    stack = _stack(monkeypatch, tmp_path)
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        _,
        coordinator,
        runtime,
    ) = stack
    result = prepare_guarded_paper_execution_canary(
        bridge_id=bridge_id,
        admission=admission,
        sealed_result=sealed,
        risk_result=risk_result,
        safety=safety,
        portfolio_store=portfolio_store,
        coordinator=coordinator,
        runtime=runtime,
    )
    return stack, result


def test_w87_canary_preparation_reuses_r6_and_stops_at_operator_decision(
    monkeypatch, tmp_path
):
    stack, result = _prepare(monkeypatch, tmp_path)
    sealed, admission, risk_result, _, _, broker, _, _ = stack
    receipt = result.receipt
    package = result.package

    assert receipt.contract_version == PAPER_EXECUTION_CANARY_PREPARATION_VERSION
    assert receipt.status is PaperExecutionCanaryPreparationStatus.PREPARED
    assert receipt.admission_hash == admission.receipt_hash
    assert receipt.risk_contract_hash == risk_result.receipt.receipt_hash
    assert receipt.readiness_seal_hash == sealed.seal.receipt_hash
    assert receipt.package_hash == package.package_hash
    assert package.intent_fingerprint == risk_result.receipt.intent_fingerprint
    assert package.risk_decision_fingerprint == risk_result.receipt.risk_decision_fingerprint
    assert package.market_fingerprint == risk_result.receipt.market_snapshot_fingerprint
    assert package.account_attestation_fingerprint == sealed.pipeline.account_attestation.fingerprint
    assert package.asset_attestation_fingerprint == sealed.pipeline.asset_truth.asset_attestation_fingerprint
    assert package.market_attestation_fingerprint == sealed.pipeline.market_truth.market_attestation_fingerprint
    assert package.symbol == admission.broker_pair == "TEST/USD"
    assert package.quantity == admission.canary_quantity == Decimal("0.010")
    assert package.limit_price == admission.conservative_limit_price == Decimal("101")
    assert package.notional == admission.canary_notional_usd == Decimal("1.010")
    assert package.execution_deadline <= risk_result.receipt.valid_until
    assert package.order_status == "VALIDATED"
    assert package.network_write_authorized is False
    assert package.next_action == "OPERATOR_DECISION_REQUIRED"
    assert receipt.lifecycle_status == "ENTRY_PREPARED"
    assert receipt.lifecycle_entry_attempt_count == 0
    assert receipt.operator_decision_required is True
    assert receipt.separate_human_execution_approval_required is True
    assert receipt.capital_reserved is False
    assert receipt.broker_write_performed is False
    assert receipt.paper_execution_authorized is False
    assert receipt.external_execution_authorized is False
    assert receipt.runtime_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert receipt.next_action == "OPERATOR_DECISION_REQUIRED"
    assert broker.submit_calls == 0


def test_w87_canary_preparation_entrypoint_exposes_no_clock_flags_or_execution_inputs():
    parameters = inspect.signature(prepare_guarded_paper_execution_canary).parameters
    for forbidden in (
        "now",
        "certified_tracks",
        "reconciliation_clean",
        "unresolved_unknown_orders",
        "relevant_open_orders",
        "confirmed_pair_position_quantity",
        "credentials",
        "writer",
        "transport",
        "environment",
        "live",
    ):
        assert forbidden not in parameters


def test_w87_canary_preparation_refuses_r6_conservative_cap_before_oms(
    monkeypatch, tmp_path
):
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path, relax_r6_cap=False)

    assert r6_coordinator.FIRST_CANARY_MAX_ACCOUNT_FRACTION == Decimal("0.001")
    assert admission.canary_notional_usd == Decimal("1.010")
    assert sealed.pipeline.account_attestation.portfolio_value == Decimal("1000")

    with pytest.raises(
        PaperExecutionCanaryPreparationGuardBlocked,
        match="R6 first-canary conservative cap before OMS/lifecycle",
    ):
        prepare_guarded_paper_execution_canary(
            bridge_id="w87-prep-r6-cap",
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            safety=safety,
            portfolio_store=portfolio_store,
            coordinator=coordinator,
            runtime=runtime,
        )

    conn = runtime.connect()
    try:
        lifecycle_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='alpaca_crypto_lifecycle_control'"
        ).fetchone()
        orders = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
    finally:
        conn.close()
    assert lifecycle_table is None
    assert orders == 0
    assert broker.submit_calls == 0


def test_w87_canary_preparation_refuses_stale_w86_or_risk_before_oms(monkeypatch, tmp_path):
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)
    monkeypatch.setattr(
        prep_module,
        "_now_utc",
        lambda: risk_result.receipt.valid_until + timedelta(microseconds=1),
    )

    with pytest.raises(PaperExecutionCanaryPreparationBlocked, match="stale"):
        prepare_guarded_paper_execution_canary(
            bridge_id="w87-prep-stale",
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            safety=safety,
            portfolio_store=portfolio_store,
            coordinator=coordinator,
            runtime=runtime,
        )
    assert broker.submit_calls == 0


def test_w87_canary_preparation_refuses_kill_switch_or_safety_version_drift(
    monkeypatch, tmp_path
):
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)
    safety.activate_kill_switch(
        reason="w87-preparation-race",
        now=sealed.seal.observed_at,
    )

    with pytest.raises(PaperExecutionCanaryPreparationGuardBlocked, match="no longer matches"):
        prepare_guarded_paper_execution_canary(
            bridge_id="w87-prep-killed",
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            safety=safety,
            portfolio_store=portfolio_store,
            coordinator=coordinator,
            runtime=runtime,
        )
    assert broker.submit_calls == 0


def test_w87_canary_preparation_refuses_portfolio_drift_before_oms(monkeypatch, tmp_path):
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)
    original = portfolio_store.get()

    class DriftedPortfolioReader:
        def get(self):
            return VersionedPortfolioSnapshot(
                version=original.version + 1,
                snapshot=original.snapshot,
            )

    with pytest.raises(PaperExecutionCanaryPreparationGuardBlocked, match="no longer matches"):
        prepare_guarded_paper_execution_canary(
            bridge_id="w87-prep-portfolio-drift",
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            safety=safety,
            portfolio_store=DriftedPortfolioReader(),
            coordinator=coordinator,
            runtime=runtime,
        )
    assert broker.submit_calls == 0


def test_w87_canary_preparation_detects_safety_race_after_local_preparation(
    monkeypatch, tmp_path
):
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)
    real_prepare = guard_module.prepare_paper_execution_canary

    def racing_prepare(**kwargs):
        result = real_prepare(**kwargs)
        safety.activate_kill_switch(
            reason="race-after-entry-prepared",
            now=sealed.seal.observed_at,
        )
        return result

    monkeypatch.setattr(guard_module, "prepare_paper_execution_canary", racing_prepare)
    with pytest.raises(PaperExecutionCanaryPreparationGuardBlocked, match="changed during"):
        prepare_guarded_paper_execution_canary(
            bridge_id="w87-prep-post-race",
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            safety=safety,
            portfolio_store=portfolio_store,
            coordinator=coordinator,
            runtime=runtime,
        )
    assert broker.submit_calls == 0


def test_w87_canary_preparation_detects_portfolio_race_after_local_preparation(
    monkeypatch, tmp_path
):
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)
    original = portfolio_store.get()
    real_prepare = guard_module.prepare_paper_execution_canary

    class RacingPortfolioReader:
        def __init__(self):
            self.calls = 0

        def get(self):
            self.calls += 1
            if self.calls == 1:
                return original
            return VersionedPortfolioSnapshot(
                version=original.version + 1,
                snapshot=original.snapshot,
            )

    reader = RacingPortfolioReader()
    monkeypatch.setattr(guard_module, "prepare_paper_execution_canary", real_prepare)
    with pytest.raises(PaperExecutionCanaryPreparationGuardBlocked, match="changed during"):
        prepare_guarded_paper_execution_canary(
            bridge_id="w87-prep-portfolio-post-race",
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            safety=safety,
            portfolio_store=reader,
            coordinator=coordinator,
            runtime=runtime,
        )
    assert broker.submit_calls == 0


def test_w87_canary_preparation_refuses_local_unknown_state(monkeypatch, tmp_path):
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)
    monkeypatch.setattr(prep_module, "_count_unresolved_local_unknown", lambda _runtime: 1)

    with pytest.raises(PaperExecutionCanaryPreparationBlocked, match="unresolved UNKNOWN"):
        prepare_guarded_paper_execution_canary(
            bridge_id="w87-prep-local-unknown",
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            safety=safety,
            portfolio_store=portfolio_store,
            coordinator=coordinator,
            runtime=runtime,
        )
    assert broker.submit_calls == 0


def test_w87_canary_preparation_is_idempotent_for_same_exact_contract_and_instant(
    monkeypatch, tmp_path
):
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)
    kwargs = dict(
        bridge_id="w87-prep-idempotent",
        admission=admission,
        sealed_result=sealed,
        risk_result=risk_result,
        safety=safety,
        portfolio_store=portfolio_store,
        coordinator=coordinator,
        runtime=runtime,
    )

    first = prepare_guarded_paper_execution_canary(**kwargs)
    second = prepare_guarded_paper_execution_canary(**kwargs)

    assert second.package == first.package
    assert second.receipt == first.receipt
    assert second.coordinator_result.lifecycle_state == first.coordinator_result.lifecycle_state
    assert broker.submit_calls == 0


def test_w87_canary_preparation_rejects_evidence_binding_tamper(monkeypatch, tmp_path):
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        broker,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)
    object.__setattr__(sealed.pipeline.asset_truth, "asset_attestation_fingerprint", "0" * 64)

    with pytest.raises(Exception, match="fingerprint|hash mismatch|differs"):
        prepare_guarded_paper_execution_canary(
            bridge_id="w87-prep-evidence-tamper",
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            safety=safety,
            portfolio_store=portfolio_store,
            coordinator=coordinator,
            runtime=runtime,
        )
    assert broker.submit_calls == 0


@pytest.mark.parametrize(
    "field,value",
    (
        ("capital_reserved", True),
        ("broker_write_performed", True),
        ("network_write_authorized", True),
        ("paper_execution_authorized", True),
        ("external_execution_authorized", True),
        ("runtime_execution_authorized", True),
        ("capital_authority", "RESERVED"),
        ("live_trading", "ENABLED"),
        ("operator_decision_required", False),
        ("separate_human_execution_approval_required", False),
        ("next_action", "POST_ALLOWED"),
    ),
)
def test_w87_canary_preparation_receipt_rejects_every_authority_escalation(
    monkeypatch, tmp_path, field, value
):
    _, result = _prepare(monkeypatch, tmp_path)
    with pytest.raises(
        PaperExecutionCanaryPreparationIntegrityError,
        match="local OMS/lifecycle evidence only",
    ):
        replace(result.receipt, **{field: value})


def test_w87_canary_preparation_receipt_rejects_deadline_and_hash_tamper(
    monkeypatch, tmp_path
):
    _, result = _prepare(monkeypatch, tmp_path)
    receipt = result.receipt
    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="hash mismatch"):
        replace(receipt, receipt_hash="0" * 64)
    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="outlive"):
        replace(
            receipt,
            package_execution_deadline=receipt.risk_contract_valid_until
            + timedelta(microseconds=1),
        )
