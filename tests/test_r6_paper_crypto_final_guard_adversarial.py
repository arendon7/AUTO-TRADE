from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from autotrade.domain import MarketSnapshot
from autotrade.health_bridge import HealthRiskMode
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import BrokerOrderType, ProductCapabilities, TimeInForce
from autotrade.state import InMemoryOrderStore
from autotrade.brokers.alpaca_paper_crypto_final_guard import (
    CryptoFinalWriteBlocked,
    CryptoFinalWritePhase,
    CryptoPaperFinalWriteGuard,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import SQLiteCryptoPaperLifecycle
from autotrade.brokers.alpaca_paper_crypto_order import (
    CryptoOrderRole,
    build_crypto_long_protection_order,
    deterministic_crypto_client_order_id,
)
from test_r6_paper_crypto_canary_coordinator import NOW, _account, _asset, _market
from test_r6_paper_crypto_final_guard import (
    _HealthyBridge,
    _authorize_pre,
    _flat_attestation,
    _flat_portfolio,
    _profile,
    _setup,
)


def _call(ctx, **overrides):
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


def _guard(ctx, *, order_store=None, health=None):
    return CryptoPaperFinalWriteGuard(
        order_store=order_store or ctx.order_store,
        safety_state_store=ctx.safety,
        portfolio_store=ctx.portfolio,
        health_bridge=health or _HealthyBridge(),
        portfolio_health_entity_id="portfolio-main",
    )


def _set_portfolio(ctx, **changes):
    current = ctx.portfolio.get()
    snapshot = replace(current.snapshot, **changes)
    updated = ctx.portfolio.compare_and_set(
        expected_version=current.version,
        snapshot=snapshot,
        now=NOW + timedelta(seconds=4),
    )
    assert updated is not None
    return updated


def test_final_guard_constructor_and_call_types_fail_closed(tmp_path) -> None:
    ctx = _setup(tmp_path)
    with pytest.raises(ValueError, match="portfolio_health_entity_id"):
        CryptoPaperFinalWriteGuard(
            order_store=ctx.order_store,
            safety_state_store=ctx.safety,
            portfolio_store=ctx.portfolio,
            health_bridge=_HealthyBridge(),
            portfolio_health_entity_id=" bad ",
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        _call(ctx, now=datetime(2026, 8, 13, 5, 0))
    with pytest.raises(ValueError, match="phase"):
        _call(ctx, phase="PRE_CONSUME")


def test_expired_package_decision_and_risk_are_explicitly_blocked(tmp_path) -> None:
    ctx = _setup(tmp_path)
    with pytest.raises(CryptoFinalWriteBlocked) as exc:
        _call(ctx, now=NOW + timedelta(seconds=25))
    text = str(exc.value)
    assert "prepared crypto package is expired" in text
    assert "prepared RiskDecision is expired" in text
    assert "human crypto operator decision is not valid" in text


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda order: replace(order, time_in_force=TimeInForce.GTC), "requires LIMIT IOC"),
        (lambda order: replace(order, quantity=Decimal("0.0002")), "economics changed"),
        (lambda order: replace(order, client_order_id="different-client-id"), "identity changed"),
    ],
)
def test_broker_order_may_not_drift_after_human_preparation(tmp_path, mutation, match) -> None:
    ctx = _setup(tmp_path)
    with pytest.raises(CryptoFinalWriteBlocked, match=match):
        _call(ctx, broker_order=mutation(ctx.broker_order))


def test_protective_order_cannot_be_substituted_for_entry_authority(tmp_path) -> None:
    ctx = _setup(tmp_path)
    protection = build_crypto_long_protection_order(
        symbol="BTC/USD",
        confirmed_entry_filled_quantity=ctx.package.quantity,
        confirmed_net_long_quantity=ctx.package.quantity,
        requested_protection_quantity=ctx.package.quantity,
        stop_price=Decimal("95000"),
        limit_price=Decimal("94500"),
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=ctx.package.lifecycle_id,
            role=CryptoOrderRole.PROTECTION,
        ),
        product_profile=ctx.prepared_profile,
        asset_attestation=ctx.prepared_asset,
    )
    with pytest.raises(CryptoFinalWriteBlocked, match="ENTRY role only"):
        _call(ctx, broker_order=protection)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("request_id", "prepared-account-changed", "prepared account evidence"),
        ("account_reference", "e" * 64, "prepared account evidence"),
    ],
)
def test_original_account_evidence_is_immutable_after_human_approval(tmp_path, field, value, match) -> None:
    ctx = _setup(tmp_path)
    with pytest.raises(CryptoFinalWriteBlocked, match=match):
        _call(ctx, prepared_account=replace(ctx.prepared_account, **{field: value}))


def test_original_asset_and_product_evidence_are_immutable(tmp_path) -> None:
    ctx = _setup(tmp_path / "asset")
    with pytest.raises(CryptoFinalWriteBlocked, match="prepared asset evidence"):
        _call(ctx, prepared_asset=replace(ctx.prepared_asset, response_sha256="e" * 64))

    ctx2 = _setup(tmp_path / "profile")
    with pytest.raises(CryptoFinalWriteBlocked, match="prepared ProductCapabilities"):
        _call(
            ctx2,
            prepared_product_profile=replace(
                ctx2.prepared_profile,
                observed_at=ctx2.prepared_profile.observed_at + timedelta(microseconds=1),
            ),
        )


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"account_reference": "e" * 64}, "stable account reference changed"),
        ({"credential_reference": "f" * 64}, "credential reference changed"),
        ({"status": "INACTIVE"}, "not ACTIVE USD"),
        ({"currency": "EUR"}, "not ACTIVE USD"),
        ({"buying_power": Decimal("5")}, "buying power"),
        ({"portfolio_value": Decimal("100")}, "conservative first-canary cap"),
    ],
)
def test_fresh_account_must_preserve_identity_and_capacity(tmp_path, changes, match) -> None:
    ctx = _setup(tmp_path)
    changed = replace(ctx.fresh_account, **changes)
    with pytest.raises(CryptoFinalWriteBlocked, match=match):
        _call(ctx, fresh_account=changed)


def test_fresh_asset_must_bind_fresh_account_and_credential(tmp_path) -> None:
    ctx = _setup(tmp_path / "account")
    with pytest.raises(CryptoFinalWriteBlocked, match="not bound to fresh account"):
        _call(
            ctx,
            fresh_asset=replace(ctx.fresh_asset, account_attestation_fingerprint="e" * 64),
        )

    ctx2 = _setup(tmp_path / "credential")
    with pytest.raises(CryptoFinalWriteBlocked, match="asset credential reference mismatch"):
        _call(ctx2, fresh_asset=replace(ctx2.fresh_asset, credential_reference="f" * 64))


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"min_order_size": Decimal("0.001")}, "below fresh broker minimum"),
        ({"min_trade_increment": Decimal("0.0003")}, "trade increment"),
        ({"price_increment": Decimal("3")}, "price increment"),
    ],
)
def test_fresh_broker_precision_cannot_invalidate_prepared_economics(tmp_path, changes, match) -> None:
    ctx = _setup(tmp_path)
    changed_asset = replace(ctx.fresh_asset, **changes)
    changed_profile = _profile(changed_asset)
    with pytest.raises(CryptoFinalWriteBlocked) as exc:
        _call(ctx, fresh_asset=changed_asset, fresh_product_profile=changed_profile)
    assert match in str(exc.value)


def test_fresh_product_profile_must_remain_crypto_and_bound_to_fresh_asset(tmp_path) -> None:
    ctx = _setup(tmp_path / "equity")
    equity = ProductCapabilities.us_equity_alpaca_paper(
        source_fingerprint=ctx.fresh_asset.fingerprint,
        observed_at=ctx.fresh_asset.observed_at,
        fractionable=True,
        marginable=True,
        shortable=True,
    )
    with pytest.raises(CryptoFinalWriteBlocked, match="not CRYPTO"):
        _call(ctx, fresh_product_profile=equity)

    ctx2 = _setup(tmp_path / "source")
    unbound = replace(ctx2.fresh_profile, source_fingerprint="e" * 64)
    with pytest.raises(CryptoFinalWriteBlocked, match="not bound to fresh asset"):
        _call(ctx2, fresh_product_profile=unbound)

    ctx3 = _setup(tmp_path / "tif")
    restricted = replace(
        ctx3.fresh_profile,
        allowed_time_in_force=frozenset({TimeInForce.GTC}),
    )
    with pytest.raises(CryptoFinalWriteBlocked, match="reject first-canary order"):
        _call(ctx3, fresh_product_profile=restricted)


def test_fresh_market_and_flat_account_identity_are_strict(tmp_path) -> None:
    ctx = _setup(tmp_path / "market")
    eth_snapshot = replace(ctx.fresh_market.market, symbol="ETH/USD")
    with pytest.raises(CryptoFinalWriteBlocked, match="market symbol mismatch"):
        _call(ctx, fresh_market=replace(ctx.fresh_market, market=eth_snapshot))

    ctx2 = _setup(tmp_path / "flat-account")
    with pytest.raises(CryptoFinalWriteBlocked, match="flat-account evidence is not bound"):
        _call(
            ctx2,
            fresh_flat_account=replace(
                ctx2.fresh_flat,
                account_attestation_fingerprint="e" * 64,
            ),
        )

    ctx3 = _setup(tmp_path / "flat-credential")
    with pytest.raises(CryptoFinalWriteBlocked, match="flat-account credential reference mismatch"):
        _call(
            ctx3,
            fresh_flat_account=replace(ctx3.fresh_flat, credential_reference="f" * 64),
        )


def test_each_final_evidence_clock_fails_closed_when_future_or_stale(tmp_path) -> None:
    ctx = _setup(tmp_path / "future-market")
    future = replace(
        ctx.fresh_market,
        received_at=NOW + timedelta(seconds=7),
        orderbook_observed_at=NOW + timedelta(seconds=7),
        trade_observed_at=NOW + timedelta(seconds=7),
        market=replace(ctx.fresh_market.market, observed_at=NOW + timedelta(seconds=7)),
    )
    with pytest.raises(CryptoFinalWriteBlocked, match="market evidence is future-dated"):
        _call(ctx, fresh_market=future)

    ctx2 = _setup(tmp_path / "stale-flat")
    with pytest.raises(CryptoFinalWriteBlocked, match="flat-account evidence exceeds 5-second"):
        _call(
            ctx2,
            fresh_flat_account=replace(ctx2.fresh_flat, attested_at=NOW - timedelta(seconds=2)),
        )


def test_missing_authoritative_oms_order_blocks_without_health_dereference(tmp_path) -> None:
    ctx = _setup(tmp_path)
    empty = InMemoryOrderStore()
    guard = _guard(ctx, order_store=empty)
    with pytest.raises(CryptoFinalWriteBlocked, match="authoritative OMS order is missing"):
        _call(ctx, phase=CryptoFinalWritePhase.PRE_CONSUME, now=NOW + timedelta(seconds=4), **{"package": ctx.package, "operator_decision": ctx.operator_decision, "operator_registry": ctx.operator_registry, "broker_order": ctx.broker_order, "lifecycle": ctx.lifecycle, "prepared_account": ctx.prepared_account, "prepared_asset": ctx.prepared_asset, "prepared_product_profile": ctx.prepared_profile, "fresh_account": ctx.fresh_account, "fresh_asset": ctx.fresh_asset, "fresh_product_profile": ctx.fresh_profile, "fresh_market": ctx.fresh_market, "fresh_flat_account": ctx.fresh_flat})


def test_safety_circuit_and_version_changes_are_separately_rejected(tmp_path) -> None:
    ctx = _setup(tmp_path / "circuit")
    ctx.safety.activate_circuit(reason="circuit", now=NOW + timedelta(seconds=4))
    with pytest.raises(CryptoFinalWriteBlocked, match="safety circuit"):
        _authorize_pre(ctx)

    ctx2 = _setup(tmp_path / "version")
    ctx2.safety.activate(reason="temporary", now=NOW + timedelta(seconds=3))
    ctx2.safety.reset(now=NOW + timedelta(seconds=3, milliseconds=100))
    with pytest.raises(CryptoFinalWriteBlocked, match="Safety state version changed"):
        _authorize_pre(ctx2)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"reconciliation_ok": False}, "reconciliation is not clean"),
        ({"broker_state_known": False}, "broker state is unknown"),
        ({"open_orders": 1}, "has open orders"),
    ],
)
def test_authoritative_portfolio_control_fields_are_rechecked(tmp_path, changes, match) -> None:
    ctx = _setup(tmp_path)
    _set_portfolio(ctx, **changes)
    with pytest.raises(CryptoFinalWriteBlocked, match=match):
        _authorize_pre(ctx)


def test_authoritative_symbol_positions_are_rechecked_even_if_net_aggregate_is_zero(tmp_path) -> None:
    ctx = _setup(tmp_path)
    _set_portfolio(
        ctx,
        gross_exposure=Decimal("20"),
        net_exposure=Decimal("0"),
        signed_position_notional_by_symbol={"BTC/USD": Decimal("10"), "ETH/USD": Decimal("-10")},
        strategy_gross_exposure={"R6_CRYPTO_FIRST_CANARY": Decimal("20")},
        strategy_signed_position_notional_by_symbol={
            "R6_CRYPTO_FIRST_CANARY": {"BTC/USD": Decimal("10"), "ETH/USD": Decimal("-10")}
        },
    )
    with pytest.raises(CryptoFinalWriteBlocked) as exc:
        _authorize_pre(ctx)
    assert "zero authoritative portfolio exposure" in str(exc.value)
    assert "zero authoritative symbol positions" in str(exc.value)


class _HealthModeBridge:
    def __init__(self, *, mode=HealthRiskMode.NORMAL, multiplier=Decimal("1"), fail=False):
        self.mode = mode
        self.multiplier = multiplier
        self.fail = fail

    def effective_control(self, **_kwargs):
        if self.fail:
            raise RuntimeError("health unavailable")
        return SimpleNamespace(
            mode=self.mode,
            reason="test",
            blocks_new_risk=self.mode is not HealthRiskMode.NORMAL,
            order_multiplier=self.multiplier,
            strategy_multiplier=self.multiplier,
            portfolio_multiplier=self.multiplier,
            strategy_state_fingerprint="8" * 64,
            portfolio_state_fingerprint="9" * 64,
        )


def test_health_mode_multiplier_and_unavailability_fail_closed(tmp_path) -> None:
    ctx = _setup(tmp_path / "mode")
    guard = _guard(ctx, health=_HealthModeBridge(mode=HealthRiskMode.CAUTION))
    with pytest.raises(CryptoFinalWriteBlocked, match="Health mode is not NORMAL"):
        guard.authorize(
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

    ctx2 = _setup(tmp_path / "multiplier")
    guard2 = _guard(ctx2, health=_HealthModeBridge(multiplier=Decimal("0.5")))
    with pytest.raises(CryptoFinalWriteBlocked, match="multipliers are not exactly 1"):
        guard2.authorize(
            package=ctx2.package, operator_decision=ctx2.operator_decision,
            operator_registry=ctx2.operator_registry, broker_order=ctx2.broker_order,
            lifecycle=ctx2.lifecycle, prepared_account=ctx2.prepared_account,
            prepared_asset=ctx2.prepared_asset, prepared_product_profile=ctx2.prepared_profile,
            fresh_account=ctx2.fresh_account, fresh_asset=ctx2.fresh_asset,
            fresh_product_profile=ctx2.fresh_profile, fresh_market=ctx2.fresh_market,
            fresh_flat_account=ctx2.fresh_flat, now=NOW + timedelta(seconds=4, milliseconds=200),
            phase=CryptoFinalWritePhase.PRE_CONSUME,
        )

    ctx3 = _setup(tmp_path / "unavailable")
    guard3 = _guard(ctx3, health=_HealthModeBridge(fail=True))
    with pytest.raises(CryptoFinalWriteBlocked, match="Health control unavailable"):
        guard3.authorize(
            package=ctx3.package, operator_decision=ctx3.operator_decision,
            operator_registry=ctx3.operator_registry, broker_order=ctx3.broker_order,
            lifecycle=ctx3.lifecycle, prepared_account=ctx3.prepared_account,
            prepared_asset=ctx3.prepared_asset, prepared_product_profile=ctx3.prepared_profile,
            fresh_account=ctx3.fresh_account, fresh_asset=ctx3.fresh_asset,
            fresh_product_profile=ctx3.fresh_profile, fresh_market=ctx3.fresh_market,
            fresh_flat_account=ctx3.fresh_flat, now=NOW + timedelta(seconds=4, milliseconds=200),
            phase=CryptoFinalWritePhase.PRE_CONSUME,
        )


def test_preconsume_rejects_phase_arguments_and_state_already_advanced(tmp_path) -> None:
    ctx = _setup(tmp_path / "args")
    pre = _authorize_pre(ctx)
    with pytest.raises(CryptoFinalWriteBlocked) as exc:
        _call(
            ctx,
            expected_attempt_id=ctx.operator_decision.context.attempt_id,
            previous_attestation=pre,
        )
    assert "must not carry expected_attempt_id" in str(exc.value)
    assert "must not carry previous attestation" in str(exc.value)

    ctx2 = _setup(tmp_path / "advanced")
    ctx2.operator_registry.consume(
        decision=ctx2.operator_decision,
        attempt_id=ctx2.operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=4),
    )
    ctx2.oms.stage_external_submission(
        order_id=ctx2.package.order_id,
        handoff_id="c" * 64,
        decision=ctx2.decision,
        market=ctx2.prepared_market.market,
        now=NOW + timedelta(seconds=4, milliseconds=50),
    )
    ctx2.lifecycle.mark_entry_submission_unknown(
        ctx2.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=100),
    )
    with pytest.raises(CryptoFinalWriteBlocked) as exc2:
        _authorize_pre(ctx2)
    text = str(exc2.value)
    assert "OMS VALIDATED" in text
    assert "unconsumed ISSUED" in text
    assert "ENTRY_PREPARED" in text
    assert "zero crypto entry attempts" in text


def test_preio_requires_correct_attempt_and_preconsume_attestation(tmp_path) -> None:
    ctx = _setup(tmp_path)
    pre = _authorize_pre(ctx)
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=ctx.operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=4, milliseconds=20),
    )
    ctx.oms.stage_external_submission(
        order_id=ctx.package.order_id,
        handoff_id="d" * 64,
        decision=ctx.decision,
        market=ctx.prepared_market.market,
        now=NOW + timedelta(seconds=4, milliseconds=40),
    )
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=60),
    )
    with pytest.raises(CryptoFinalWriteBlocked) as exc:
        _call(
            ctx,
            phase=CryptoFinalWritePhase.PRE_IO,
            expected_attempt_id="wrong-attempt",
            previous_attestation=None,
            now=NOW + timedelta(seconds=4, milliseconds=100),
        )
    text = str(exc.value)
    assert "expected attempt_id" in text
    assert "requires actual PRE_CONSUME" in text

    # Real predecessor remains valid for the real attempt.
    final = _call(
        ctx,
        phase=CryptoFinalWritePhase.PRE_IO,
        expected_attempt_id=ctx.operator_decision.context.attempt_id,
        previous_attestation=pre,
        now=NOW + timedelta(seconds=4, milliseconds=100),
    )
    assert final.phase is CryptoFinalWritePhase.PRE_IO


def test_preio_detects_fresh_market_race_between_phases(tmp_path) -> None:
    ctx = _setup(tmp_path)
    pre = _authorize_pre(ctx)
    ctx.operator_registry.consume(
        decision=ctx.operator_decision,
        attempt_id=ctx.operator_decision.context.attempt_id,
        now=NOW + timedelta(seconds=4, milliseconds=20),
    )
    ctx.oms.stage_external_submission(
        order_id=ctx.package.order_id,
        handoff_id="e" * 64,
        decision=ctx.decision,
        market=ctx.prepared_market.market,
        now=NOW + timedelta(seconds=4, milliseconds=40),
    )
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=NOW + timedelta(seconds=4, milliseconds=60),
    )
    raced_market = replace(
        ctx.fresh_market,
        market=replace(ctx.fresh_market.market, last=Decimal("99998")),
    )
    with pytest.raises(CryptoFinalWriteBlocked, match="fresh market evidence changed"):
        _call(
            ctx,
            fresh_market=raced_market,
            phase=CryptoFinalWritePhase.PRE_IO,
            expected_attempt_id=ctx.operator_decision.context.attempt_id,
            previous_attestation=pre,
            now=NOW + timedelta(seconds=4, milliseconds=100),
        )


def test_missing_or_corrupt_lifecycle_fails_closed(tmp_path) -> None:
    ctx = _setup(tmp_path)
    empty_lifecycle = SQLiteCryptoPaperLifecycle(SQLiteRuntime(tmp_path / "empty-life.sqlite3"))
    with pytest.raises(CryptoFinalWriteBlocked, match="lifecycle is unavailable or corrupt"):
        _call(ctx, lifecycle=empty_lifecycle)
