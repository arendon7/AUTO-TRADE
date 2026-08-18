from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from autotrade.domain import PortfolioSnapshot
from autotrade.health_bridge import HealthBridgePolicy, SQLiteHealthBridgeStore
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.persistence import SQLitePortfolioStore, SQLiteRuntime
from autotrade.product_profile import ProductCapabilities
from autotrade.research.health import SQLiteHealthStateStore
from autotrade.risk_state import SQLiteR2SafetyStateStore
from autotrade.state import InMemoryOrderStore
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import CryptoPaperCanaryCoordinator
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_final_guard import (
    COLD_START_KILL_REASON,
    COLD_START_SCOPE,
    CryptoColdStartFinalWriteBlocked,
    CryptoColdStartFinalWritePhase,
    CryptoColdStartPaperFinalWriteGuard,
    SQLiteCryptoColdStartAuthorityProvider,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import SQLiteCryptoPaperLifecycle
from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    SQLiteCryptoOperatorDecisionRegistry,
)
from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from test_r6_paper_crypto_canary_coordinator import (
    NOW,
    _NoBroker,
    _account,
    _asset,
    _decision,
    _intent,
    _market,
)


ZERO = Decimal("0")


def _profile(asset):
    return ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )


def _flat_attestation(account, *, at, positions=0, orders=0):
    return PaperFlatAccountAttestation(
        account_attestation_fingerprint=account.fingerprint,
        credential_reference=account.credential_reference,
        position_count=positions,
        open_order_count=orders,
        positions_response_hash="4" * 64,
        orders_response_hash="5" * 64,
        positions_request_id="req-cold-start-positions",
        orders_request_id="req-cold-start-orders",
        attested_at=at,
    )


def _portfolio(account):
    return PortfolioSnapshot(
        snapshot_id=f"r6-crypto-paper-cold-start:{account.account_reference[:20]}",
        equity=account.portfolio_value,
        gross_exposure=ZERO,
        net_exposure=ZERO,
        daily_pnl=ZERO,
        drawdown=ZERO,
        open_orders=0,
        signed_position_notional_by_symbol={},
        strategy_gross_exposure={},
        strategy_signed_position_notional_by_symbol={},
        reconciliation_ok=True,
        broker_state_known=True,
    )


def _setup(tmp_path, *, notional=Decimal("2")):
    core = SQLiteRuntime(tmp_path / "core.sqlite3")
    safety = SQLiteR2SafetyStateStore(core)
    safety.activate(reason=COLD_START_KILL_REASON, now=NOW + timedelta(seconds=1))
    prepared_account = _account()
    SQLitePortfolioStore(core).initialize(_portfolio(prepared_account), now=NOW + timedelta(seconds=1))
    health_reader = SQLiteHealthStateStore(core.path)
    SQLiteHealthBridgeStore(
        core,
        health_reader=health_reader,
        policy=HealthBridgePolicy(require_strategy_state=True, require_portfolio_state=True),
    )

    order_store = InMemoryOrderStore()
    oms = OrderManagementSystem(
        broker=_NoBroker(),
        ledger=InMemoryEventLedger(),
        order_store=order_store,
    )
    prepared_asset = _asset(prepared_account)
    prepared_profile = _profile(prepared_asset)
    prepared_market = _market()
    quantity = Decimal("0.0001")
    limit_price = notional / quantity
    intent = _intent(quantity=quantity, limit_price=limit_price)
    decision = _decision(intent, prepared_market, approved_notional=notional)
    lifecycle = SQLiteCryptoPaperLifecycle(core)
    prepared = CryptoPaperCanaryCoordinator(oms=oms).prepare_entry(
        intent=intent,
        decision=decision,
        market_attestation=prepared_market,
        account_attestation=prepared_account,
        asset_attestation=prepared_asset,
        product_profile=prepared_profile,
        lifecycle=lifecycle,
        now=NOW + timedelta(seconds=2),
        certified_tracks=("R0", "R1", "R2", "R3", "R4", "R5"),
        reconciliation_clean=True,
        unresolved_unknown_orders=0,
        relevant_open_orders=0,
        confirmed_pair_position_quantity=ZERO,
    )

    operator_registry = SQLiteCryptoOperatorDecisionRegistry(core)
    context = CryptoOperatorDecisionContext.from_prepared_package(
        prepared.package,
        attempt_id="crypto-cold-start-attempt-001",
    )
    operator_state = operator_registry.record_operator_approval(
        context=context,
        operator_id="operator-001",
        issued_at=NOW + timedelta(seconds=3),
        expires_at=NOW + timedelta(seconds=10),
    )

    fresh_at = NOW + timedelta(seconds=4)
    fresh_account = _account(observed=fresh_at)
    fresh_asset = _asset(fresh_account, observed=fresh_at)
    fresh_profile = _profile(fresh_asset)
    fresh_market = _market(observed=fresh_at)
    fresh_flat = _flat_attestation(fresh_account, at=fresh_at)
    authority = SQLiteCryptoColdStartAuthorityProvider(core)
    guard = CryptoColdStartPaperFinalWriteGuard(
        order_store=order_store,
        authority_provider=authority,
    )
    return SimpleNamespace(
        core=core,
        safety=safety,
        order_store=order_store,
        guard=guard,
        authority=authority,
        lifecycle=lifecycle,
        operator_registry=operator_registry,
        operator_decision=operator_state.decision,
        package=prepared.package,
        broker_order=prepared.broker_order,
        prepared_account=prepared_account,
        prepared_asset=prepared_asset,
        prepared_profile=prepared_profile,
        fresh_account=fresh_account,
        fresh_asset=fresh_asset,
        fresh_profile=fresh_profile,
        fresh_market=fresh_market,
        fresh_flat=fresh_flat,
    )


def _pre(ctx, **overrides):
    values = dict(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        broker_order=ctx.broker_order,
        lifecycle=ctx.lifecycle,
        prepared_account=ctx.prepared_account,
        prepared_asset=ctx.prepared_asset,
        prepared_product_profile=ctx.prepared_profile,
        fresh_account=ctx.fresh_account,
        fresh_asset=ctx.fresh_asset,
        fresh_product_profile=ctx.fresh_profile,
        fresh_market=ctx.fresh_market,
        fresh_flat_account=ctx.fresh_flat,
        now=NOW + timedelta(seconds=4, milliseconds=100),
        phase=CryptoColdStartFinalWritePhase.PRE_CONSUME,
    )
    values.update(overrides)
    return ctx.guard.authorize(**values)


def _advance(ctx, pre):
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=ctx.operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=4, milliseconds=200),
    )
    current = ctx.order_store.get_by_order_id(ctx.package.order_id)
    assert current is not None
    ctx.order_store.update(
        replace(current, status=current.status.SUBMITTING, submitted_at=NOW + timedelta(seconds=4, milliseconds=300))
    )
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=400),
    )
    return ctx.guard.authorize(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        broker_order=ctx.broker_order,
        lifecycle=ctx.lifecycle,
        prepared_account=ctx.prepared_account,
        prepared_asset=ctx.prepared_asset,
        prepared_product_profile=ctx.prepared_profile,
        fresh_account=ctx.fresh_account,
        fresh_asset=ctx.fresh_asset,
        fresh_product_profile=ctx.fresh_profile,
        fresh_market=ctx.fresh_market,
        fresh_flat_account=ctx.fresh_flat,
        now=NOW + timedelta(seconds=4, milliseconds=500),
        phase=CryptoColdStartFinalWritePhase.PRE_IO,
        expected_attempt_id=ctx.operator_decision.context.attempt_id,
        previous_attestation=pre,
    )


def test_cold_start_guard_two_phase_happy_path_preserves_missing_health(tmp_path) -> None:
    ctx = _setup(tmp_path)
    pre = _pre(ctx)
    assert pre.phase is CryptoColdStartFinalWritePhase.PRE_CONSUME
    assert pre.bootstrap_scope == COLD_START_SCOPE
    assert pre.bootstrap_kill_reason == COLD_START_KILL_REASON
    assert pre.operator_status.value == "ISSUED"
    assert pre.lifecycle_status.value == "ENTRY_PREPARED"
    assert pre.entry_attempt_count == 0
    snapshot = ctx.authority.snapshot()
    assert snapshot.kill_switch_active is True
    assert snapshot.health_state_rows == 0
    assert snapshot.health_bridge_rows == 0

    checkpoint = SQLiteCryptoColdStartExecutionAttemptRegistry(ctx.core).record_pre_consume(pre)
    assert checkpoint.pre_consume == pre
    assert SQLiteCryptoColdStartExecutionAttemptRegistry(ctx.core).get(checkpoint.attempt_id) == checkpoint

    final = _advance(ctx, pre)
    assert final.phase is CryptoColdStartFinalWritePhase.PRE_IO
    assert final.operator_status.value == "CONSUMED"
    assert final.lifecycle_status.value == "ENTRY_SUBMISSION_UNKNOWN"
    assert final.entry_attempt_count == 1
    assert final.previous_attestation_hash == pre.attestation_hash
    assert final.authority_state_fingerprint == pre.authority_state_fingerprint


def test_cold_start_guard_requires_exact_commissioning_kill_not_manual_kill(tmp_path) -> None:
    ctx = _setup(tmp_path)
    ctx.safety.reset(now=NOW + timedelta(seconds=3, milliseconds=500))
    ctx.safety.activate(reason="MANUAL_KILL", now=NOW + timedelta(seconds=3, milliseconds=600))
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="exact commissioning kill switch"):
        _pre(ctx)


def test_cold_start_guard_never_bypasses_safety_circuit(tmp_path) -> None:
    ctx = _setup(tmp_path)
    ctx.safety.activate_circuit(reason="DAILY_LOSS", now=NOW + timedelta(seconds=3, milliseconds=500))
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="safety circuit"):
        _pre(ctx)


def test_cold_start_guard_requires_health_to_be_absent_not_healthy(tmp_path) -> None:
    ctx = _setup(tmp_path)
    conn = ctx.core.connect()
    try:
        conn.execute(
            """
            INSERT INTO health_bridge_state(
                entity_kind, entity_id, mode, risk_multiplier,
                health_state_version, health_state_fingerprint,
                baseline_fingerprint, policy_fingerprint, bridge_version,
                updated_at, state_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "STRATEGY", "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION", "NORMAL", "1",
                1, "1" * 64, "2" * 64, "3" * 64, 1, NOW.isoformat(), "4" * 64,
            ),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="Health and bridge rows to remain absent"):
        _pre(ctx)


def test_cold_start_guard_enforces_usd_five_cap_and_flat_broker(tmp_path) -> None:
    over = _setup(tmp_path / "over", notional=Decimal("6"))
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="USD 1-5"):
        _pre(over)

    ctx = _setup(tmp_path / "nonflat")
    nonflat = _flat_attestation(ctx.fresh_account, at=ctx.fresh_flat.attested_at, positions=1)
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="not flat"):
        _pre(ctx, fresh_flat_account=nonflat)


def test_preio_rejects_any_authoritative_core_change_after_checkpoint(tmp_path) -> None:
    ctx = _setup(tmp_path)
    pre = _pre(ctx)
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=ctx.operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=4, milliseconds=200),
    )
    current = ctx.order_store.get_by_order_id(ctx.package.order_id)
    assert current is not None
    ctx.order_store.update(
        replace(current, status=current.status.SUBMITTING, submitted_at=NOW + timedelta(seconds=4, milliseconds=300))
    )
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=400),
    )
    ctx.safety.activate_circuit(reason="LATE_CIRCUIT", now=NOW + timedelta(seconds=4, milliseconds=450))
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="safety circuit|core state changed"):
        ctx.guard.authorize(
            package=ctx.package,
            operator_decision=ctx.operator_decision,
            operator_registry=ctx.operator_registry,
            broker_order=ctx.broker_order,
            lifecycle=ctx.lifecycle,
            prepared_account=ctx.prepared_account,
            prepared_asset=ctx.prepared_asset,
            prepared_product_profile=ctx.prepared_profile,
            fresh_account=ctx.fresh_account,
            fresh_asset=ctx.fresh_asset,
            fresh_product_profile=ctx.fresh_profile,
            fresh_market=ctx.fresh_market,
            fresh_flat_account=ctx.fresh_flat,
            now=NOW + timedelta(seconds=4, milliseconds=500),
            phase=CryptoColdStartFinalWritePhase.PRE_IO,
            expected_attempt_id=ctx.operator_decision.context.attempt_id,
            previous_attestation=pre,
        )
