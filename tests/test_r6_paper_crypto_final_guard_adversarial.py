from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from autotrade.health_bridge import HealthRiskMode
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import ProductCapabilities, TimeInForce
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
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_final_guard import (
    _HealthyBridge,
    _authorize_pre,
    _profile,
    _setup,
)


def _call(ctx, *, guard=None, **overrides):
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
    return (guard or ctx.guard).authorize(**values)


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


def test_expired_package_risk_and_operator_are_all_visible(tmp_path) -> None:
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


def test_protective_order_cannot_replace_entry_authority(tmp_path) -> None:
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
    "target,mutation,match",
    [
        ("account", lambda x: replace(x, request_id="changed-account-request"), "prepared account evidence"),
        ("asset", lambda x: replace(x, response_sha256="e" * 64), "prepared asset evidence"),
        (
            "profile",
            lambda x: replace(x, observed_at=x.observed_at + timedelta(microseconds=1)),
            "prepared ProductCapabilities",
        ),
    ],
)
def test_original_human_approved_evidence_is_immutable(tmp_path, target, mutation, match) -> None:
    ctx = _setup(tmp_path)
    kwargs = {}
    if target == "account":
        kwargs["prepared_account"] = mutation(ctx.prepared_account)
    elif target == "asset":
        kwargs["prepared_asset"] = mutation(ctx.prepared_asset)
    else:
        kwargs["prepared_product_profile"] = mutation(ctx.prepared_profile)
    with pytest.raises(CryptoFinalWriteBlocked, match=match):
        _call(ctx, **kwargs)


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
    with pytest.raises(CryptoFinalWriteBlocked, match=match):
        _call(ctx, fresh_account=replace(ctx.fresh_account, **changes))


def test_fresh_asset_must_bind_fresh_account_and_credential(tmp_path) -> None:
    ctx = _setup(tmp_path / "account")
    with pytest.raises(CryptoFinalWriteBlocked, match="not bound to fresh account"):
        _call(ctx, fresh_asset=replace(ctx.fresh_asset, account_attestation_fingerprint="e" * 64))
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
def test_fresh_broker_precision_is_rechecked(tmp_path, changes, match) -> None:
    ctx = _setup(tmp_path)
    asset = replace(ctx.fresh_asset, **changes)
    with pytest.raises(CryptoFinalWriteBlocked) as exc:
        _call(ctx, fresh_asset=asset, fresh_product_profile=_profile(asset))
    assert match in str(exc.value)


def test_fresh_product_profile_must_remain_crypto_bound_and_ioc_capable(tmp_path) -> None:
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

    ctx2 = _setup(tmp_path / "unbound")
    with pytest.raises(CryptoFinalWriteBlocked, match="not bound to fresh asset"):
        _call(ctx2, fresh_product_profile=replace(ctx2.fresh_profile, source_fingerprint="e" * 64))

    ctx3 = _setup(tmp_path / "ioc")
    restricted = replace(ctx3.fresh_profile, allowed_time_in_force=frozenset({TimeInForce.GTC}))
    with pytest.raises(CryptoFinalWriteBlocked, match="reject first-canary order"):
        _call(ctx3, fresh_product_profile=restricted)


def test_market_flat_account_identity_and_clocks_are_rechecked(tmp_path) -> None:
    ctx = _setup(tmp_path / "market")
    with pytest.raises(CryptoFinalWriteBlocked, match="market symbol mismatch"):
        _call(ctx, fresh_market=replace(ctx.fresh_market, market=replace(ctx.fresh_market.market, symbol="ETH/USD")))

    ctx2 = _setup(tmp_path / "flat-account")
    with pytest.raises(CryptoFinalWriteBlocked, match="flat-account evidence is not bound"):
        _call(ctx2, fresh_flat_account=replace(ctx2.fresh_flat, account_attestation_fingerprint="e" * 64))

    ctx3 = _setup(tmp_path / "flat-credential")
    with pytest.raises(CryptoFinalWriteBlocked, match="flat-account credential reference mismatch"):
        _call(ctx3, fresh_flat_account=replace(ctx3.fresh_flat, credential_reference="f" * 64))

    ctx4 = _setup(tmp_path / "stale-flat")
    with pytest.raises(CryptoFinalWriteBlocked, match="flat-account evidence exceeds 5-second"):
        _call(ctx4, fresh_flat_account=replace(ctx4.fresh_flat, attested_at=NOW - timedelta(seconds=2)))


def test_missing_authoritative_oms_order_fails_closed(tmp_path) -> None:
    ctx = _setup(tmp_path)
    with pytest.raises(CryptoFinalWriteBlocked, match="authoritative OMS order is missing"):
        _call(ctx, guard=_guard(ctx, order_store=InMemoryOrderStore()))


def test_safety_circuit_and_version_changes_are_separate_blocks(tmp_path) -> None:
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
def test_authoritative_portfolio_controls_are_rechecked(tmp_path, changes, match) -> None:
    ctx = _setup(tmp_path)
    _set_portfolio(ctx, **changes)
    with pytest.raises(CryptoFinalWriteBlocked, match=match):
        _authorize_pre(ctx)


def test_authoritative_symbol_positions_are_rechecked(tmp_path) -> None:
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


class _HealthBridge:
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


@pytest.mark.parametrize(
    "bridge,match",
    [
        (_HealthBridge(mode=HealthRiskMode.REDUCED), "Health mode is not NORMAL"),
        (_HealthBridge(multiplier=Decimal("0.5")), "multipliers are not exactly 1"),
        (_HealthBridge(fail=True), "Health control unavailable"),
    ],
)
def test_health_is_rechecked_at_final_write(tmp_path, bridge, match) -> None:
    ctx = _setup(tmp_path)
    with pytest.raises(CryptoFinalWriteBlocked, match=match):
        _call(ctx, guard=_guard(ctx, health=bridge))


def test_preconsume_rejects_phase_arguments_and_advanced_state(tmp_path) -> None:
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
    assert "expected attempt_id" in str(exc.value)
    assert "requires actual PRE_CONSUME" in str(exc.value)

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
    raced_market = replace(ctx.fresh_market, market=replace(ctx.fresh_market.market, last=Decimal("99998")))
    with pytest.raises(CryptoFinalWriteBlocked, match="fresh market evidence changed"):
        _call(
            ctx,
            fresh_market=raced_market,
            phase=CryptoFinalWritePhase.PRE_IO,
            expected_attempt_id=ctx.operator_decision.context.attempt_id,
            previous_attestation=pre,
            now=NOW + timedelta(seconds=4, milliseconds=100),
        )


def test_missing_lifecycle_fails_closed(tmp_path) -> None:
    ctx = _setup(tmp_path)
    empty = SQLiteCryptoPaperLifecycle(SQLiteRuntime(tmp_path / "empty-life.sqlite3"))
    with pytest.raises(CryptoFinalWriteBlocked, match="lifecycle is unavailable or corrupt"):
        _call(ctx, lifecycle=empty)
