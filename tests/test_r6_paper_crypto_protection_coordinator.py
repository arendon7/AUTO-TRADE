from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.domain import (
    OrderIntent,
    OrderType,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from autotrade.brokers.alpaca_paper_crypto_order import CryptoOrderRole
from autotrade.brokers.alpaca_paper_crypto_protection_coordinator import (
    CryptoPaperProtectionCoordinator,
    CryptoProtectionPreparationBlocked,
    PreparedCryptoProtectionPackage,
)
from autotrade.brokers.alpaca_paper_crypto_reconciliation import (
    CryptoBrokerOrderSnapshot,
    CryptoBrokerPositionSnapshot,
    CryptoBrokerReconciliation,
)
from test_r6_paper_crypto_canary_coordinator import NOW, _market
from test_r6_paper_crypto_final_guard import _setup


PROTECTION_STOP = Decimal("99000")
PROTECTION_LIMIT = Decimal("98900")


def _entry_reconciliation(ctx, *, status="filled", filled=None, position=None, observed=None):
    filled = ctx.broker_order.quantity if filled is None else filled
    position = filled if position is None else position
    observed = NOW + timedelta(seconds=5) if observed is None else observed
    broker = CryptoBrokerOrderSnapshot(
        broker_order_id="crypto-entry-broker-001",
        client_order_id=ctx.broker_order.client_order_id,
        symbol=ctx.broker_order.symbol,
        side=ctx.broker_order.side.value,
        order_type=ctx.broker_order.order_type.value,
        time_in_force=ctx.broker_order.time_in_force.value,
        status=status,
        quantity=ctx.broker_order.quantity,
        filled_quantity=filled,
        limit_price=ctx.broker_order.limit_price,
        stop_price=ctx.broker_order.stop_price,
    )
    pos = CryptoBrokerPositionSnapshot(
        symbol=ctx.broker_order.symbol,
        quantity=position,
        absent=False,
    )
    return CryptoBrokerReconciliation(
        order=broker,
        position=pos,
        order_request_id="entry-reconcile-order-001",
        position_request_id="entry-reconcile-position-001",
        order_response_sha256="1" * 64,
        position_response_sha256="2" * 64,
        observed_at=observed,
    )


def _advance_entry_to_unprotected(ctx, reconciliation):
    ctx.lifecycle.mark_entry_submission_unknown(
        ctx.package.lifecycle_id,
        at=reconciliation.observed_at - timedelta(milliseconds=100),
    )
    ctx.lifecycle.reconcile_entry(
        ctx.package.lifecycle_id,
        broker_order_id=reconciliation.order.broker_order_id,
        broker_status=reconciliation.order.status,
        filled_quantity=reconciliation.order.filled_quantity,
        terminal=reconciliation.order.terminal,
        confirmed_net_long_quantity=reconciliation.position.quantity,
        at=reconciliation.observed_at,
    )


def _protection_intent(ctx, *, quantity=None, limit_price=PROTECTION_LIMIT):
    quantity = ctx.broker_order.quantity if quantity is None else quantity
    return OrderIntent(
        intent_id="crypto-protection-intent-001",
        idempotency_key="crypto-protection-intent-001-key",
        strategy_id="R6_CRYPTO_FIRST_CANARY",
        symbol=ctx.broker_order.symbol,
        side=Side.SELL,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        created_at=NOW + timedelta(seconds=5),
        limit_price=limit_price,
    )


def _protection_decision(intent, market, *, risk_reducing=True, approved_notional=None, valid_until=None):
    assert intent.limit_price is not None
    return RiskDecision(
        decision_id="crypto-protection-decision-001",
        intent_id=intent.intent_id,
        status=RiskDecisionStatus.APPROVED,
        reason_code="APPROVED",
        reason_detail="exact confirmed-position protective SELL",
        evaluated_at=NOW + timedelta(seconds=5),
        valid_until=valid_until or (NOW + timedelta(seconds=20)),
        limits_version="r6-crypto-protection-v1",
        intent_fingerprint=intent_fingerprint(intent),
        market_fingerprint=market_fingerprint(market.market),
        approved_notional=approved_notional if approved_notional is not None else intent.quantity * intent.limit_price,
        risk_reducing=risk_reducing,
        safety_state_version=0,
    )


def _prepare(tmp_path, **overrides):
    ctx = overrides.pop("ctx", _setup(tmp_path / "ctx"))
    reconciliation = overrides.pop("entry_reconciliation", _entry_reconciliation(ctx))
    if overrides.pop("advance_entry", True):
        _advance_entry_to_unprotected(ctx, reconciliation)
    market = overrides.pop("market_attestation", _market(observed=NOW + timedelta(seconds=5)))
    intent = overrides.pop("intent", _protection_intent(ctx, quantity=reconciliation.position.quantity))
    decision = overrides.pop("decision", _protection_decision(intent, market))
    result = CryptoPaperProtectionCoordinator(oms=ctx.oms).prepare_protection(
        lifecycle=ctx.lifecycle,
        lifecycle_id=ctx.package.lifecycle_id,
        entry_order=overrides.pop("entry_order", ctx.broker_order),
        entry_reconciliation=reconciliation,
        intent=intent,
        decision=decision,
        market_attestation=market,
        account_attestation=overrides.pop("account_attestation", ctx.prepared_account),
        asset_attestation=overrides.pop("asset_attestation", ctx.prepared_asset),
        product_profile=overrides.pop("product_profile", ctx.prepared_profile),
        stop_price=overrides.pop("stop_price", PROTECTION_STOP),
        limit_price=overrides.pop("limit_price", PROTECTION_LIMIT),
        now=overrides.pop("now", NOW + timedelta(seconds=6)),
    )
    assert not overrides
    return ctx, reconciliation, result


def test_protection_preparation_is_exact_position_offline_and_operator_gated(tmp_path) -> None:
    ctx, reconciliation, result = _prepare(tmp_path)
    package = result.package

    assert result.order.status.value == "VALIDATED"
    assert result.order.order_id != ctx.package.order_id
    assert result.broker_order.role is CryptoOrderRole.PROTECTION
    assert result.broker_order.side.value == "sell"
    assert result.broker_order.order_type.value == "stop_limit"
    assert result.broker_order.time_in_force.value == "gtc"
    assert result.broker_order.quantity == reconciliation.position.quantity
    assert result.broker_order.stop_price == PROTECTION_STOP
    assert result.broker_order.limit_price == PROTECTION_LIMIT
    assert package.confirmed_entry_filled_quantity == reconciliation.order.filled_quantity
    assert package.confirmed_net_long_quantity == reconciliation.position.quantity
    assert package.quantity == reconciliation.position.quantity
    assert package.entry_reconciliation_fingerprint == reconciliation.fingerprint
    assert package.network_write_authorized is False
    assert package.next_action == "OPERATOR_DECISION_REQUIRED"
    assert package.risk_reducing is True
    assert package.order_status == "VALIDATED"
    assert package.broker_order_type == "stop_limit"
    assert package.time_in_force == "gtc"
    assert package.package_hash == package.canonical_payload()["package_hash"]

    state = ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.PROTECTION_PREPARED
    assert state.protection_attempt_count == 0
    assert state.protection_quantity == reconciliation.position.quantity
    assert state.confirmed_net_long_quantity == reconciliation.position.quantity
    assert state.event_head_hash == package.lifecycle_event_head_hash


def test_protection_requires_terminal_reconciled_entry_exposure(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    reconciliation = _entry_reconciliation(ctx)
    market = _market(observed=NOW + timedelta(seconds=5))
    intent = _protection_intent(ctx)
    decision = _protection_decision(intent, market)

    with pytest.raises(CryptoProtectionPreparationBlocked, match="terminal reconciled entry exposure"):
        _prepare(
            tmp_path,
            ctx=ctx,
            entry_reconciliation=reconciliation,
            advance_entry=False,
            market_attestation=market,
            intent=intent,
            decision=decision,
        )


def test_protection_rejects_position_that_differs_from_lifecycle_or_entry_fill(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    good = _entry_reconciliation(ctx)
    _advance_entry_to_unprotected(ctx, good)
    bad = _entry_reconciliation(
        ctx,
        filled=good.order.filled_quantity,
        position=good.position.quantity / Decimal("2"),
        observed=good.observed_at + timedelta(milliseconds=50),
    )
    market = _market(observed=NOW + timedelta(seconds=5))
    intent = _protection_intent(ctx, quantity=bad.position.quantity)
    decision = _protection_decision(intent, market)

    with pytest.raises(CryptoProtectionPreparationBlocked, match="lifecycle confirmed net long"):
        _prepare(
            tmp_path,
            ctx=ctx,
            entry_reconciliation=bad,
            advance_entry=False,
            market_attestation=market,
            intent=intent,
            decision=decision,
        )


def test_protection_intent_must_equal_confirmed_net_long_exactly(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    reconciliation = _entry_reconciliation(ctx)
    _advance_entry_to_unprotected(ctx, reconciliation)
    market = _market(observed=NOW + timedelta(seconds=5))
    intent = _protection_intent(ctx, quantity=reconciliation.position.quantity * Decimal("0.5"))
    decision = _protection_decision(intent, market)

    with pytest.raises(CryptoProtectionPreparationBlocked, match="quantity must equal confirmed net long exactly"):
        _prepare(
            tmp_path,
            ctx=ctx,
            entry_reconciliation=reconciliation,
            advance_entry=False,
            market_attestation=market,
            intent=intent,
            decision=decision,
        )


def test_protection_requires_separate_safety_approved_risk_reducing_decision(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    reconciliation = _entry_reconciliation(ctx)
    _advance_entry_to_unprotected(ctx, reconciliation)
    market = _market(observed=NOW + timedelta(seconds=5))
    intent = _protection_intent(ctx)
    decision = _protection_decision(intent, market, risk_reducing=False)

    with pytest.raises(CryptoProtectionPreparationBlocked, match="risk-reducing"):
        _prepare(
            tmp_path,
            ctx=ctx,
            entry_reconciliation=reconciliation,
            advance_entry=False,
            market_attestation=market,
            intent=intent,
            decision=decision,
        )


def test_protection_rejects_hidden_broker_rounding_of_quantity_or_prices(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    reconciliation = _entry_reconciliation(ctx)
    _advance_entry_to_unprotected(ctx, reconciliation)
    market = _market(observed=NOW + timedelta(seconds=5))
    intent = _protection_intent(ctx, limit_price=Decimal("98900.5"))
    decision = _protection_decision(intent, market)

    with pytest.raises(CryptoProtectionPreparationBlocked, match="prices must already satisfy exact broker increments"):
        _prepare(
            tmp_path,
            ctx=ctx,
            entry_reconciliation=reconciliation,
            advance_entry=False,
            market_attestation=market,
            intent=intent,
            decision=decision,
            stop_price=Decimal("99000.5"),
            limit_price=Decimal("98900.5"),
        )


def test_protection_rejects_stale_entry_reconciliation(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    reconciliation = _entry_reconciliation(ctx, observed=NOW + timedelta(seconds=5))
    _advance_entry_to_unprotected(ctx, reconciliation)
    market = _market(observed=NOW + timedelta(seconds=34))
    intent = _protection_intent(ctx)
    decision = _protection_decision(intent, market, valid_until=NOW + timedelta(seconds=50))

    with pytest.raises(CryptoProtectionPreparationBlocked, match="entry reconciliation is stale"):
        _prepare(
            tmp_path,
            ctx=ctx,
            entry_reconciliation=reconciliation,
            advance_entry=False,
            market_attestation=market,
            intent=intent,
            decision=decision,
            now=NOW + timedelta(seconds=35),
        )


def test_protection_package_hash_detects_rebinding(tmp_path) -> None:
    _, _, result = _prepare(tmp_path)
    with pytest.raises(ValueError, match="package hash mismatch"):
        replace(result.package, stop_price=result.package.stop_price - Decimal("1"))


def test_protection_package_rejects_overclose_even_with_recomputed_shape(tmp_path) -> None:
    _, _, result = _prepare(tmp_path)
    values = {
        field: getattr(result.package, field)
        for field in result.package.__dataclass_fields__
    }
    values["quantity"] = result.package.quantity * Decimal("2")
    with pytest.raises(ValueError, match="protective quantity must equal confirmed net long exactly"):
        PreparedCryptoProtectionPackage(**values)
