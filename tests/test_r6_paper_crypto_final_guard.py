from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from autotrade.domain import PortfolioSnapshot
from autotrade.health_bridge import HealthRiskMode
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import ProductCapabilities
from autotrade.state import InMemoryOrderStore, InMemoryPortfolioStore, InMemorySafetyStateStore
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import CryptoPaperCanaryCoordinator
from autotrade.brokers.alpaca_paper_crypto_final_guard import (
    CryptoFinalWriteBlocked,
    CryptoFinalWritePhase,
    CryptoPaperFinalWriteGuard,
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


class _HealthyBridge:
    def effective_control(self, *, strategy_id, portfolio_entity_id, now):
        del strategy_id, portfolio_entity_id, now
        return SimpleNamespace(
            mode=HealthRiskMode.NORMAL,
            reason="healthy",
            blocks_new_risk=False,
            order_multiplier=Decimal("1"),
            strategy_multiplier=Decimal("1"),
            portfolio_multiplier=Decimal("1"),
            strategy_state_fingerprint="8" * 64,
            portfolio_state_fingerprint="9" * 64,
        )


def _flat_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="crypto-final-flat",
        equity=Decimal("100000"),
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


def _flat_attestation(account, *, at, positions=0, orders=0):
    return PaperFlatAccountAttestation(
        account_attestation_fingerprint=account.fingerprint,
        credential_reference=account.credential_reference,
        position_count=positions,
        open_order_count=orders,
        positions_response_hash="4" * 64,
        orders_response_hash="5" * 64,
        positions_request_id="req-final-positions",
        orders_request_id="req-final-orders",
        attested_at=at,
    )


def _profile(asset):
    return ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )


def _setup(tmp_path):
    order_store = InMemoryOrderStore()
    safety_store = InMemorySafetyStateStore()
    portfolio_store = InMemoryPortfolioStore()
    portfolio_store.initialize(_flat_portfolio(), now=NOW)
    health = _HealthyBridge()
    ledger = InMemoryEventLedger()
    oms = OrderManagementSystem(
        broker=_NoBroker(),
        ledger=ledger,
        order_store=order_store,
        safety_state_store=safety_store,
        health_bridge=health,
        portfolio_health_entity_id="portfolio-main",
    )

    prepared_account = _account()
    prepared_asset = _asset(prepared_account)
    prepared_profile = _profile(prepared_asset)
    prepared_market = _market()
    intent = _intent()
    decision = _decision(intent, prepared_market)
    lifecycle = SQLiteCryptoPaperLifecycle(SQLiteRuntime(tmp_path / "life.sqlite3"))
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
        confirmed_pair_position_quantity=Decimal("0"),
    )

    operator_registry = SQLiteCryptoOperatorDecisionRegistry(
        SQLiteRuntime(tmp_path / "operator.sqlite3")
    )
    context = CryptoOperatorDecisionContext.from_prepared_package(
        prepared.package,
        attempt_id="crypto-final-attempt-001",
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

    guard = CryptoPaperFinalWriteGuard(
        order_store=order_store,
        safety_state_store=safety_store,
        portfolio_store=portfolio_store,
        health_bridge=health,
        portfolio_health_entity_id="portfolio-main",
    )
    return SimpleNamespace(
        guard=guard,
        oms=oms,
        order_store=order_store,
        safety=safety_store,
        portfolio=portfolio_store,
        lifecycle=lifecycle,
        operator_registry=operator_registry,
        operator_decision=operator_state.decision,
        package=prepared.package,
        broker_order=prepared.broker_order,
        prepared_account=prepared_account,
        prepared_asset=prepared_asset,
        prepared_profile=prepared_profile,
        prepared_market=prepared_market,
        decision=decision,
        fresh_account=fresh_account,
        fresh_asset=fresh_asset,
        fresh_profile=fresh_profile,
        fresh_market=fresh_market,
        fresh_flat=fresh_flat,
    )


def _authorize_pre(ctx, **overrides):
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
        now=NOW + timedelta(seconds=4, milliseconds=200),
        phase=CryptoFinalWritePhase.PRE_CONSUME,
    )
    values.update(overrides)
    return ctx.guard.authorize(**values)


def _advance_to_pre_io(ctx, pre):
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=ctx.operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=4, milliseconds=300),
    )
    ctx.oms.stage_external_submission(
        order_id=ctx.package.order_id,
        handoff_id="6" * 64,
        decision=ctx.decision,
        market=ctx.prepared_market.market,
        now=NOW + timedelta(seconds=4, milliseconds=400),
    )
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=500),
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
        now=NOW + timedelta(seconds=4, milliseconds=600),
        phase=CryptoFinalWritePhase.PRE_IO,
        expected_attempt_id=ctx.operator_decision.context.attempt_id,
        previous_attestation=pre,
    )


def test_stable_contract_fingerprints_ignore_fresh_observation_identity() -> None:
    account_a = _account(observed=NOW)
    asset_a = _asset(account_a, observed=NOW)
    profile_a = _profile(asset_a)
    account_b = _account(observed=NOW + timedelta(seconds=1))
    asset_b = _asset(account_b, observed=NOW + timedelta(seconds=1))
    profile_b = _profile(asset_b)
    assert asset_a.fingerprint != asset_b.fingerprint
    assert asset_a.contract_fingerprint == asset_b.contract_fingerprint
    assert profile_a.fingerprint != profile_b.fingerprint
    assert profile_a.contract_fingerprint == profile_b.contract_fingerprint


def test_final_guard_two_phase_happy_path_is_preconsume_then_unknown_preio(tmp_path) -> None:
    ctx = _setup(tmp_path)
    pre = _authorize_pre(ctx)
    assert pre.phase is CryptoFinalWritePhase.PRE_CONSUME
    assert pre.operator_status.value == "ISSUED"
    assert pre.lifecycle_status.value == "ENTRY_PREPARED"
    assert pre.entry_attempt_count == 0
    assert pre.previous_attestation_hash is None
    assert pre.asset_contract_fingerprint == ctx.prepared_asset.contract_fingerprint
    assert pre.product_contract_fingerprint == ctx.prepared_profile.contract_fingerprint

    final = _advance_to_pre_io(ctx, pre)
    assert final.phase is CryptoFinalWritePhase.PRE_IO
    assert final.operator_status.value == "CONSUMED"
    assert final.lifecycle_status.value == "ENTRY_SUBMISSION_UNKNOWN"
    assert final.entry_attempt_count == 1
    assert final.previous_attestation_hash == pre.attestation_hash
    assert final.package_hash == pre.package_hash
    assert final.operator_decision_hash == pre.operator_decision_hash
    assert final.fresh_account_fingerprint == pre.fresh_account_fingerprint
    assert final.fresh_market_attestation_fingerprint == pre.fresh_market_attestation_fingerprint


def test_preconsume_rejects_product_contract_drift_even_with_fresh_valid_evidence(tmp_path) -> None:
    ctx = _setup(tmp_path)
    drifted_asset = _asset(
        ctx.fresh_account,
        observed=ctx.fresh_asset.observed_at,
        price_increment=Decimal("0.01"),
    )
    drifted_profile = _profile(drifted_asset)
    with pytest.raises(CryptoFinalWriteBlocked, match="asset contract changed"):
        _authorize_pre(
            ctx,
            fresh_asset=drifted_asset,
            fresh_product_profile=drifted_profile,
        )


def test_preconsume_rejects_account_identity_or_credential_drift(tmp_path) -> None:
    ctx = _setup(tmp_path)
    changed = replace(ctx.fresh_account, account_id="87654321-abcd-abcd-abcd-123456789012")
    changed_asset = _asset(changed, observed=ctx.fresh_asset.observed_at)
    changed_profile = _profile(changed_asset)
    changed_flat = _flat_attestation(changed, at=ctx.fresh_flat.attested_at)
    with pytest.raises(CryptoFinalWriteBlocked, match="fresh account_id changed"):
        _authorize_pre(
            ctx,
            fresh_account=changed,
            fresh_asset=changed_asset,
            fresh_product_profile=changed_profile,
            fresh_flat_account=changed_flat,
        )


def test_preconsume_rejects_stale_or_nonflat_final_broker_evidence(tmp_path) -> None:
    ctx = _setup(tmp_path)
    stale_account = _account(observed=NOW - timedelta(seconds=6))
    stale_asset = _asset(stale_account, observed=NOW + timedelta(seconds=4))
    stale_profile = _profile(stale_asset)
    stale_flat = _flat_attestation(stale_account, at=NOW + timedelta(seconds=4))
    with pytest.raises(CryptoFinalWriteBlocked, match="account evidence exceeds 5-second"):
        _authorize_pre(
            ctx,
            fresh_account=stale_account,
            fresh_asset=stale_asset,
            fresh_product_profile=stale_profile,
            fresh_flat_account=stale_flat,
        )

    nonflat = _flat_attestation(ctx.fresh_account, at=ctx.fresh_flat.attested_at, positions=1)
    with pytest.raises(CryptoFinalWriteBlocked, match="not flat"):
        _authorize_pre(ctx, fresh_flat_account=nonflat)


def test_preconsume_rejects_safety_change_or_authoritative_portfolio_exposure(tmp_path) -> None:
    ctx = _setup(tmp_path / "safety")
    ctx.safety.activate(reason="manual kill", now=NOW + timedelta(seconds=4))
    with pytest.raises(CryptoFinalWriteBlocked, match="kill switch"):
        _authorize_pre(ctx)

    ctx2 = _setup(tmp_path / "portfolio")
    exposed = replace(
        _flat_portfolio(),
        snapshot_id="crypto-final-exposed",
        gross_exposure=Decimal("10"),
        net_exposure=Decimal("10"),
        signed_position_notional_by_symbol={"BTC/USD": Decimal("10")},
        strategy_gross_exposure={"R6_CRYPTO_FIRST_CANARY": Decimal("10")},
        strategy_signed_position_notional_by_symbol={
            "R6_CRYPTO_FIRST_CANARY": {"BTC/USD": Decimal("10")}
        },
    )
    current = ctx2.portfolio.get()
    assert ctx2.portfolio.compare_and_set(
        expected_version=current.version,
        snapshot=exposed,
        now=NOW + timedelta(seconds=4),
    ) is not None
    with pytest.raises(CryptoFinalWriteBlocked, match="zero authoritative portfolio exposure"):
        _authorize_pre(ctx2)


def test_preio_rejects_unconsumed_operator_or_missing_unknown_transition(tmp_path) -> None:
    ctx = _setup(tmp_path / "operator")
    pre = _authorize_pre(ctx)
    ctx.oms.stage_external_submission(
        order_id=ctx.package.order_id,
        handoff_id="7" * 64,
        decision=ctx.decision,
        market=ctx.prepared_market.market,
        now=NOW + timedelta(seconds=4, milliseconds=400),
    )
    with pytest.raises(CryptoFinalWriteBlocked, match="consumed human"):
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
            now=NOW + timedelta(seconds=4, milliseconds=600),
            phase=CryptoFinalWritePhase.PRE_IO,
            expected_attempt_id=ctx.operator_decision.context.attempt_id,
            previous_attestation=pre,
        )

    ctx2 = _setup(tmp_path / "unknown")
    pre2 = _authorize_pre(ctx2)
    ctx2.operator_registry.consume(
        decision=ctx2.operator_decision,
        attempt_id=ctx2.operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=4, milliseconds=300),
    )
    ctx2.oms.stage_external_submission(
        order_id=ctx2.package.order_id,
        handoff_id="a" * 64,
        decision=ctx2.decision,
        market=ctx2.prepared_market.market,
        now=NOW + timedelta(seconds=4, milliseconds=400),
    )
    with pytest.raises(CryptoFinalWriteBlocked, match="ENTRY_SUBMISSION_UNKNOWN"):
        ctx2.guard.authorize(
            package=ctx2.package,
            operator_decision=ctx2.operator_decision,
            operator_registry=ctx2.operator_registry,
            broker_order=ctx2.broker_order,
            lifecycle=ctx2.lifecycle,
            prepared_account=ctx2.prepared_account,
            prepared_asset=ctx2.prepared_asset,
            prepared_product_profile=ctx2.prepared_profile,
            fresh_account=ctx2.fresh_account,
            fresh_asset=ctx2.fresh_asset,
            fresh_product_profile=ctx2.fresh_profile,
            fresh_market=ctx2.fresh_market,
            fresh_flat_account=ctx2.fresh_flat,
            now=NOW + timedelta(seconds=4, milliseconds=600),
            phase=CryptoFinalWritePhase.PRE_IO,
            expected_attempt_id=ctx2.operator_decision.context.attempt_id,
            previous_attestation=pre2,
        )


def test_preio_rejects_state_race_between_phases(tmp_path) -> None:
    ctx = _setup(tmp_path)
    pre = _authorize_pre(ctx)
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=ctx.operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=4, milliseconds=300),
    )
    ctx.oms.stage_external_submission(
        order_id=ctx.package.order_id,
        handoff_id="b" * 64,
        decision=ctx.decision,
        market=ctx.prepared_market.market,
        now=NOW + timedelta(seconds=4, milliseconds=400),
    )
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=500),
    )
    ctx.safety.activate_circuit(reason="race", now=NOW + timedelta(seconds=4, milliseconds=550))
    with pytest.raises(CryptoFinalWriteBlocked, match="Safety state"):
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
            now=NOW + timedelta(seconds=4, milliseconds=600),
            phase=CryptoFinalWritePhase.PRE_IO,
            expected_attempt_id=ctx.operator_decision.context.attempt_id,
            previous_attestation=pre,
        )


def test_fresh_market_price_can_change_without_increasing_fixed_limit_economics(tmp_path) -> None:
    ctx = _setup(tmp_path)
    changed_market = replace(
        ctx.fresh_market,
        market=replace(
            ctx.fresh_market.market,
            bid=Decimal("89999"),
            ask=Decimal("90000"),
            last=Decimal("89999.5"),
        ),
    )
    pre = _authorize_pre(ctx, fresh_market=changed_market)
    assert pre.phase is CryptoFinalWritePhase.PRE_CONSUME
    assert ctx.package.notional == ctx.package.quantity * ctx.package.limit_price
