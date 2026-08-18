from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import OrderStatus
from autotrade.persistence import SQLitePortfolioStore, SQLiteRuntime
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    CryptoColdStartExecutionAttemptIntegrityError,
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_final_guard import (
    CryptoColdStartFinalWriteBlocked,
    CryptoColdStartFinalWritePhase,
    SQLiteCryptoColdStartAuthorityProvider,
)
from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    SQLiteCryptoOperatorDecisionRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_order import build_crypto_long_protection_order
from test_r6_paper_crypto_canary_coordinator import NOW, _account, _asset, _market
from test_r6_paper_crypto_cold_start_final_guard import (
    _advance,
    _flat_attestation,
    _pre,
    _profile,
    _setup,
)


def test_guard_blocks_missing_health_schema_and_uninitialized_authority(tmp_path) -> None:
    ctx = _setup(tmp_path / "missing-schema")
    conn = ctx.core.connect()
    try:
        conn.execute("DROP TABLE health_bridge_state")
        conn.execute("DROP TABLE health_state_v2")
    finally:
        conn.close()
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="core state unavailable"):
        _pre(ctx)

    empty = SQLiteRuntime(tmp_path / "empty" / "core.sqlite3")
    provider = SQLiteCryptoColdStartAuthorityProvider(empty)
    with pytest.raises(Exception):
        provider.snapshot()


def test_guard_blocks_portfolio_version_exposure_orders_and_reconciliation_drift(tmp_path) -> None:
    ctx = _setup(tmp_path / "version")
    store = SQLitePortfolioStore(ctx.core)
    current = store.get()
    assert store.compare_and_set(
        expected_version=current.version,
        snapshot=current.snapshot,
        now=NOW + timedelta(seconds=3),
    ) is not None
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="Portfolio State v1"):
        _pre(ctx)

    exposed = _setup(tmp_path / "exposed")
    store2 = SQLitePortfolioStore(exposed.core)
    current2 = store2.get()
    changed = replace(
        current2.snapshot,
        gross_exposure=Decimal("2"),
        net_exposure=Decimal("2"),
        signed_position_notional_by_symbol={"BTC/USD": Decimal("2")},
        strategy_gross_exposure={"cold-start-strategy": Decimal("2")},
        strategy_signed_position_notional_by_symbol={
            "cold-start-strategy": {"BTC/USD": Decimal("2")}
        },
    )
    assert store2.compare_and_set(
        expected_version=current2.version,
        snapshot=changed,
        now=NOW + timedelta(seconds=3),
    ) is not None
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="zero authoritative portfolio exposure"):
        _pre(exposed)

    open_order = _setup(tmp_path / "orders")
    store3 = SQLitePortfolioStore(open_order.core)
    current3 = store3.get()
    assert store3.compare_and_set(
        expected_version=current3.version,
        snapshot=replace(current3.snapshot, open_orders=1),
        now=NOW + timedelta(seconds=3),
    ) is not None
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="zero authoritative open orders"):
        _pre(open_order)

    unknown = _setup(tmp_path / "reconciliation")
    store4 = SQLitePortfolioStore(unknown.core)
    current4 = store4.get()
    assert store4.compare_and_set(
        expected_version=current4.version,
        snapshot=replace(current4.snapshot, reconciliation_ok=False, broker_state_known=False),
        now=NOW + timedelta(seconds=3),
    ) is not None
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="not reconciled/broker-known"):
        _pre(unknown)


def test_guard_blocks_broker_equity_account_and_credential_drift(tmp_path) -> None:
    ctx = _setup(tmp_path / "equity")
    changed_account = replace(ctx.fresh_account, portfolio_value=ctx.fresh_account.portfolio_value - 1)
    changed_asset = _asset(changed_account, observed=ctx.fresh_asset.observed_at)
    changed_profile = _profile(changed_asset)
    changed_flat = _flat_attestation(changed_account, at=ctx.fresh_flat.attested_at)
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="Portfolio equity differs"):
        _pre(
            ctx,
            fresh_account=changed_account,
            fresh_asset=changed_asset,
            fresh_product_profile=changed_profile,
            fresh_flat_account=changed_flat,
        )

    ref_ctx = _setup(tmp_path / "account-ref")
    other = replace(ref_ctx.fresh_account, account_reference="e" * 64)
    other_asset = _asset(other, observed=ref_ctx.fresh_asset.observed_at)
    other_profile = _profile(other_asset)
    other_flat = _flat_attestation(other, at=ref_ctx.fresh_flat.attested_at)
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="account reference changed"):
        _pre(
            ref_ctx,
            fresh_account=other,
            fresh_asset=other_asset,
            fresh_product_profile=other_profile,
            fresh_flat_account=other_flat,
        )

    cred_ctx = _setup(tmp_path / "credential")
    other_cred = replace(cred_ctx.fresh_account, credential_reference="d" * 64)
    cred_asset = _asset(other_cred, observed=cred_ctx.fresh_asset.observed_at)
    cred_profile = _profile(cred_asset)
    cred_flat = _flat_attestation(other_cred, at=cred_ctx.fresh_flat.attested_at)
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="credential reference changed"):
        _pre(
            cred_ctx,
            fresh_account=other_cred,
            fresh_asset=cred_asset,
            fresh_product_profile=cred_profile,
            fresh_flat_account=cred_flat,
        )


def test_guard_blocks_stale_future_and_product_contract_drift(tmp_path) -> None:
    stale = _setup(tmp_path / "stale")
    stale_account = _account(observed=NOW - timedelta(seconds=10))
    stale_asset = _asset(stale_account, observed=stale.fresh_asset.observed_at)
    stale_profile = _profile(stale_asset)
    stale_flat = _flat_attestation(stale_account, at=stale.fresh_flat.attested_at)
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="account evidence exceeds"):
        _pre(
            stale,
            fresh_account=stale_account,
            fresh_asset=stale_asset,
            fresh_product_profile=stale_profile,
            fresh_flat_account=stale_flat,
        )

    future = _setup(tmp_path / "future")
    future_market = _market(observed=NOW + timedelta(seconds=10))
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="market evidence is future-dated"):
        _pre(future, fresh_market=future_market)

    drift = _setup(tmp_path / "contract")
    drift_asset = _asset(
        drift.fresh_account,
        observed=drift.fresh_asset.observed_at,
        price_increment=Decimal("0.01"),
    )
    drift_profile = _profile(drift_asset)
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="asset contract changed|ProductCapabilities contract changed"):
        _pre(drift, fresh_asset=drift_asset, fresh_product_profile=drift_profile)


def test_guard_blocks_protection_order_and_expired_window(tmp_path) -> None:
    ctx = _setup(tmp_path / "role")
    protection = build_crypto_long_protection_order(
        symbol="BTC/USD",
        confirmed_entry_filled_quantity=ctx.package.quantity,
        confirmed_net_long_quantity=ctx.package.quantity,
        requested_protection_quantity=ctx.package.quantity,
        stop_price=Decimal("19000"),
        limit_price=Decimal("18900"),
        client_order_id="atr6c-protection-adversarial",
        product_profile=ctx.prepared_profile,
        asset_attestation=ctx.prepared_asset,
    )
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="ENTRY only"):
        _pre(ctx, broker_order=protection)

    expired = _setup(tmp_path / "expired")
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="expired|not valid"):
        expired.guard.authorize(
            package=expired.package,
            operator_decision=expired.operator_decision,
            operator_registry=expired.operator_registry,
            broker_order=expired.broker_order,
            lifecycle=expired.lifecycle,
            prepared_account=expired.prepared_account,
            prepared_asset=expired.prepared_asset,
            prepared_product_profile=expired.prepared_profile,
            fresh_account=expired.fresh_account,
            fresh_asset=expired.fresh_asset,
            fresh_product_profile=expired.fresh_profile,
            fresh_market=expired.fresh_market,
            fresh_flat_account=expired.fresh_flat,
            now=NOW + timedelta(seconds=30),
            phase=CryptoColdStartFinalWritePhase.PRE_CONSUME,
        )


def test_guard_blocks_missing_operator_and_lifecycle_drift(tmp_path) -> None:
    ctx = _setup(tmp_path / "operator")
    empty_registry = SQLiteCryptoOperatorDecisionRegistry(
        SQLiteRuntime(tmp_path / "operator" / "empty-operator.sqlite3")
    )
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="operator decision unavailable"):
        _pre(ctx, operator_registry=empty_registry)

    lifecycle = _setup(tmp_path / "lifecycle")
    lifecycle.lifecycle.mark_entry_submission_unknown(
        lifecycle.package.lifecycle_id,
        at=NOW + timedelta(seconds=3, milliseconds=500),
    )
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="ENTRY_PREPARED|lifecycle changed"):
        _pre(lifecycle)


def test_guard_preconsume_rejects_predecessor_and_preio_requires_exact_attempt(tmp_path) -> None:
    ctx = _setup(tmp_path / "predecessor")
    pre = _pre(ctx)
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="cannot carry attempt/predecessor"):
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
            now=NOW + timedelta(seconds=4, milliseconds=100),
            phase=CryptoColdStartFinalWritePhase.PRE_CONSUME,
            expected_attempt_id=ctx.operator_decision.context.attempt_id,
            previous_attestation=pre,
        )

    preio = _setup(tmp_path / "preio")
    first = _pre(preio)
    preio.operator_registry.consume(
        decision=preio.operator_decision,
        attempt_id=preio.operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=4, milliseconds=200),
    )
    current = preio.order_store.get_by_order_id(preio.package.order_id)
    assert current is not None
    preio.order_store.update(
        replace(current, status=OrderStatus.SUBMITTING, submitted_at=NOW + timedelta(seconds=4, milliseconds=300))
    )
    preio.lifecycle.mark_entry_submission_unknown(
        preio.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=400),
    )
    with pytest.raises(CryptoColdStartFinalWriteBlocked, match="attempt_id mismatch"):
        preio.guard.authorize(
            package=preio.package,
            operator_decision=preio.operator_decision,
            operator_registry=preio.operator_registry,
            broker_order=preio.broker_order,
            lifecycle=preio.lifecycle,
            prepared_account=preio.prepared_account,
            prepared_asset=preio.prepared_asset,
            prepared_product_profile=preio.prepared_profile,
            fresh_account=preio.fresh_account,
            fresh_asset=preio.fresh_asset,
            fresh_product_profile=preio.fresh_profile,
            fresh_market=preio.fresh_market,
            fresh_flat_account=preio.fresh_flat,
            now=NOW + timedelta(seconds=4, milliseconds=500),
            phase=CryptoColdStartFinalWritePhase.PRE_IO,
            expected_attempt_id="wrong-attempt",
            previous_attestation=first,
        )


def test_checkpoint_replay_is_idempotent_and_tamper_is_detected(tmp_path) -> None:
    ctx = _setup(tmp_path)
    pre = _pre(ctx)
    registry = SQLiteCryptoColdStartExecutionAttemptRegistry(ctx.core)
    first = registry.record_pre_consume(pre)
    second = registry.record_pre_consume(pre)
    assert first == second

    conn = ctx.core.connect()
    try:
        conn.execute(
            "UPDATE alpaca_crypto_cold_start_execution_attempts SET record_hash=? WHERE attempt_id=?",
            ("0" * 64, first.attempt_id),
        )
    finally:
        conn.close()
    with pytest.raises(CryptoColdStartExecutionAttemptIntegrityError):
        registry.get(first.attempt_id)


def test_checkpoint_rejects_preio_attestation_and_wrong_type(tmp_path) -> None:
    ctx = _setup(tmp_path)
    pre = _pre(ctx)
    final = _advance(ctx, pre)
    registry = SQLiteCryptoColdStartExecutionAttemptRegistry(ctx.core)
    with pytest.raises(Exception, match="PRE_CONSUME"):
        registry.record_pre_consume(final)
    with pytest.raises(TypeError):
        registry.record_pre_consume("not-an-attestation")  # type: ignore[arg-type]
