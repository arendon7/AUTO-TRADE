from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from autotrade.domain import OrderType, RiskDecisionStatus, Side
from autotrade.product_profile import AssetClass, ProtectionModel
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from autotrade.brokers.alpaca_paper_crypto_protection_coordinator import (
    CryptoPaperProtectionCoordinator,
    CryptoProtectionPreparationBlocked,
)
from test_r6_paper_crypto_canary_coordinator import NOW, _market
from test_r6_paper_crypto_final_guard import _setup
from test_r6_paper_crypto_protection_coordinator import (
    PROTECTION_LIMIT,
    PROTECTION_STOP,
    _advance_entry_to_unprotected,
    _entry_reconciliation,
    _prepare,
    _protection_decision,
    _protection_intent,
)


def test_protection_package_rejects_invalid_identity_hash_time_and_policy_fields(tmp_path) -> None:
    _, _, result = _prepare(tmp_path / "base")
    package = result.package

    invalid_cases = (
        ({"lifecycle_id": " bad lifecycle "}, "lifecycle_id is invalid"),
        ({"symbol": package.symbol.lower()}, "symbol must be canonical"),
        ({"market_fingerprint": "not-a-hash"}, "market_fingerprint must be lowercase SHA-256"),
        ({"risk_decision_safety_state_version": True}, "safety_state_version must be non-negative integer"),
        ({"risk_decision_safety_state_version": -1}, "safety_state_version must be non-negative integer"),
        ({"prepared_at": package.prepared_at.replace(tzinfo=None)}, "prepared_at must be timezone-aware"),
        ({"quantity": Decimal("0")}, "quantity must be finite and positive"),
        ({"confirmed_net_long_quantity": package.confirmed_entry_filled_quantity * Decimal("2")}, "confirmed net long cannot exceed"),
        ({"limit_price": package.stop_price + Decimal("1")}, "limit <= stop"),
        ({"prepared_at": package.execution_deadline}, "already expired"),
        ({"execution_deadline": package.risk_decision_valid_until + timedelta(seconds=1)}, "may not outlive RiskDecision"),
        ({"order_status": "SUBMITTING"}, "must leave OMS VALIDATED"),
        ({"broker_order_type": "limit"}, "must use STOP_LIMIT"),
        ({"time_in_force": "ioc"}, "must use GTC"),
        ({"risk_reducing": False}, "Safety-classified risk reducing"),
        ({"network_write_authorized": True}, "cannot authorize broker write"),
        ({"next_action": "AUTO_SUBMIT"}, "explicit operator decision"),
    )
    for changes, message in invalid_cases:
        with pytest.raises(ValueError, match=message):
            replace(package, **changes)


def test_protection_coordinator_constructor_and_top_level_types_fail_closed(tmp_path) -> None:
    with pytest.raises(TypeError, match="authoritative OrderManagementSystem"):
        CryptoPaperProtectionCoordinator(oms=object())  # type: ignore[arg-type]

    ctx = _setup(tmp_path / "ctx")
    reconciliation = _entry_reconciliation(ctx)
    _advance_entry_to_unprotected(ctx, reconciliation)
    market = _market(observed=NOW + timedelta(seconds=5))
    intent = _protection_intent(ctx)
    decision = _protection_decision(intent, market)
    coordinator = CryptoPaperProtectionCoordinator(oms=ctx.oms)
    base = dict(
        lifecycle=ctx.lifecycle,
        lifecycle_id=ctx.package.lifecycle_id,
        entry_order=ctx.broker_order,
        entry_reconciliation=reconciliation,
        intent=intent,
        decision=decision,
        market_attestation=market,
        account_attestation=ctx.prepared_account,
        asset_attestation=ctx.prepared_asset,
        product_profile=ctx.prepared_profile,
        stop_price=PROTECTION_STOP,
        limit_price=PROTECTION_LIMIT,
        now=NOW + timedelta(seconds=6),
    )

    for key, value, message in (
        ("lifecycle", object(), "authoritative crypto lifecycle"),
        ("entry_order", object(), "exact ENTRY broker request"),
        ("entry_reconciliation", object(), "exact entry reconciliation evidence"),
    ):
        kwargs = dict(base)
        kwargs[key] = value
        with pytest.raises(CryptoProtectionPreparationBlocked, match=message):
            coordinator.prepare_protection(**kwargs)

    with pytest.raises(ValueError, match="now must be timezone-aware"):
        coordinator.prepare_protection(**{**base, "now": NOW.replace(tzinfo=None)})


def _entry_validation_fixture(tmp_path):
    ctx = _setup(tmp_path / "ctx")
    reconciliation = _entry_reconciliation(ctx)
    _advance_entry_to_unprotected(ctx, reconciliation)
    snapshot = ctx.lifecycle.snapshot(ctx.package.lifecycle_id)
    order = reconciliation.order
    position = reconciliation.position
    binding = snapshot.binding
    state = snapshot.state
    entry_order = ctx.broker_order
    return ctx, binding, state, entry_order, reconciliation, order, position


def _simple_reconciliation(order, position, observed_at):
    return SimpleNamespace(order=order, position=position, observed_at=observed_at)


def test_entry_reconciliation_validator_rejects_identity_fill_position_and_time_drift(tmp_path) -> None:
    _, binding, state, entry_order, reconciliation, order, position = _entry_validation_fixture(tmp_path)
    validate = CryptoPaperProtectionCoordinator._validate_entry_reconciliation
    now = NOW + timedelta(seconds=6)

    base_order = dict(
        terminal=order.terminal,
        filled_quantity=order.filled_quantity,
        client_order_id=order.client_order_id,
        symbol=order.symbol,
        quantity=order.quantity,
        broker_order_id=order.broker_order_id,
        status=order.status,
    )
    base_position = dict(
        absent=position.absent,
        quantity=position.quantity,
        symbol=position.symbol,
    )

    bad_state = SimpleNamespace(
        status=CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED,
        entry_terminal=False,
        entry_filled_quantity=state.entry_filled_quantity,
        confirmed_net_long_quantity=state.confirmed_net_long_quantity,
        entry_broker_order_id=state.entry_broker_order_id,
        entry_broker_status=state.entry_broker_status,
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="no protectable confirmed long exposure"):
        validate(
            binding=binding,
            state=bad_state,
            entry_order=entry_order,
            reconciliation=reconciliation,
            now=now,
        )

    fake_entry_order = SimpleNamespace(
        client_order_id="different-client-order",
        fingerprint=entry_order.fingerprint,
        quantity=entry_order.quantity,
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="client_order_id differs from lifecycle"):
        validate(binding=binding, state=state, entry_order=fake_entry_order, reconciliation=reconciliation, now=now)

    fake_entry_order = SimpleNamespace(
        client_order_id=entry_order.client_order_id,
        fingerprint="f" * 64,
        quantity=entry_order.quantity,
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="fingerprint differs from lifecycle"):
        validate(binding=binding, state=state, entry_order=fake_entry_order, reconciliation=reconciliation, now=now)

    cases = (
        ({**base_order, "terminal": False}, base_position, reconciliation.observed_at, "terminal with positive fill"),
        ({**base_order, "client_order_id": "different-client"}, base_position, reconciliation.observed_at, "client_order_id mismatch"),
        ({**base_order, "symbol": "ETH/USD"}, base_position, reconciliation.observed_at, "symbol mismatch"),
        ({**base_order, "quantity": order.quantity * Decimal("2")}, base_position, reconciliation.observed_at, "requested quantity mismatch"),
        ({**base_order, "filled_quantity": order.filled_quantity / Decimal("2")}, base_position, reconciliation.observed_at, "reconciled fill differs"),
        ({**base_order, "broker_order_id": "different-broker-order"}, base_position, reconciliation.observed_at, "broker order id differs"),
        ({**base_order, "status": "canceled"}, base_position, reconciliation.observed_at, "broker status differs"),
        (base_order, {**base_position, "absent": True, "quantity": Decimal("0")}, reconciliation.observed_at, "confirm existing long position"),
        (base_order, {**base_position, "quantity": order.filled_quantity * Decimal("2")}, reconciliation.observed_at, "exceeds reconciled entry fill"),
        (base_order, base_position, now + timedelta(seconds=4), "future-dated"),
        (base_order, base_position, now - timedelta(seconds=30), "stale"),
    )
    for order_values, position_values, observed_at, message in cases:
        fake_order = SimpleNamespace(**order_values)
        fake_position = SimpleNamespace(**position_values)
        fake_reconciliation = _simple_reconciliation(fake_order, fake_position, observed_at)
        with pytest.raises(CryptoProtectionPreparationBlocked, match=message):
            validate(
                binding=binding,
                state=state,
                entry_order=entry_order,
                reconciliation=fake_reconciliation,
                now=now,
            )


def _product_validation_fixture(tmp_path):
    ctx = _setup(tmp_path / "ctx")
    reconciliation = _entry_reconciliation(ctx)
    _advance_entry_to_unprotected(ctx, reconciliation)
    binding = ctx.lifecycle.snapshot(ctx.package.lifecycle_id).binding
    market = _market(observed=NOW + timedelta(seconds=5))
    intent = _protection_intent(ctx)
    decision = _protection_decision(intent, market)
    return ctx, binding, market, intent, decision


def _fake_account(ctx, *, status="ACTIVE", currency="USD", fingerprint=None, credential_reference=None, attested_at=None):
    return SimpleNamespace(
        status=status,
        currency=currency,
        fingerprint=fingerprint or ctx.prepared_account.fingerprint,
        credential_reference=credential_reference or ctx.prepared_account.credential_reference,
        attested_at=attested_at or ctx.prepared_account.attested_at,
    )


def _fake_asset(ctx, account, *, account_fingerprint=None, credential_reference=None, fingerprint=None, observed_at=None):
    return SimpleNamespace(
        account_attestation_fingerprint=account_fingerprint or account.fingerprint,
        credential_reference=credential_reference or account.credential_reference,
        fingerprint=fingerprint or ctx.prepared_asset.fingerprint,
        observed_at=observed_at or ctx.prepared_asset.observed_at,
    )


class _FakeProduct:
    def __init__(self, ctx, *, fingerprint=None, asset_class=AssetClass.CRYPTO, protection_model=ProtectionModel.CRYPTO_STOP_LIMIT, marginable=False, shortable=False, observed_at=None):
        self.fingerprint = fingerprint or ctx.prepared_profile.fingerprint
        self.asset_class = asset_class
        self.protection_model = protection_model
        self.marginable = marginable
        self.shortable = shortable
        self.observed_at = observed_at or ctx.prepared_profile.observed_at

    def require_order(self, **_kwargs):
        return None

    def require_margin(self, **_kwargs):
        return None

    def require_opening_short(self, **_kwargs):
        return None


def test_product_evidence_validator_rejects_safety_binding_capability_and_staleness(tmp_path) -> None:
    ctx, binding, market, intent, decision = _product_validation_fixture(tmp_path)
    validate = CryptoPaperProtectionCoordinator._validate_product_evidence
    now = NOW + timedelta(seconds=6)
    account = _fake_account(ctx)
    asset = _fake_asset(ctx, account)
    product = _FakeProduct(ctx)

    def call(*, candidate_intent=intent, candidate_decision=decision, candidate_market=market, candidate_account=account, candidate_asset=asset, candidate_product=product):
        return validate(
            binding=binding,
            intent=candidate_intent,
            decision=candidate_decision,
            market_attestation=candidate_market,
            account_attestation=candidate_account,
            asset_attestation=candidate_asset,
            product_profile=candidate_product,
            now=now,
        )

    wrong_side = SimpleNamespace(
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        symbol=intent.symbol,
        limit_price=intent.limit_price,
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="SELL LIMIT"):
        call(candidate_intent=wrong_side)

    wrong_symbol = SimpleNamespace(
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        symbol="ETH/USD",
        limit_price=intent.limit_price,
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="symbol differs"):
        call(candidate_intent=wrong_symbol)

    no_limit = SimpleNamespace(
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        symbol=intent.symbol,
        limit_price=None,
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="positive limit price"):
        call(candidate_intent=no_limit)

    denied = SimpleNamespace(status=RiskDecisionStatus.DENIED, risk_reducing=True)
    with pytest.raises(CryptoProtectionPreparationBlocked, match="APPROVED risk-reducing"):
        call(candidate_decision=denied)

    wrong_intent_decision = SimpleNamespace(
        status=RiskDecisionStatus.APPROVED,
        risk_reducing=True,
        intent_id="different-intent",
        intent_fingerprint=decision.intent_fingerprint,
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="exact intent"):
        call(candidate_decision=wrong_intent_decision)

    wrong_market_decision = SimpleNamespace(
        status=RiskDecisionStatus.APPROVED,
        risk_reducing=True,
        intent_id=intent.intent_id,
        intent_fingerprint=decision.intent_fingerprint,
        market_fingerprint="f" * 64,
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="exact market"):
        call(candidate_decision=wrong_market_decision)

    expired = SimpleNamespace(
        status=RiskDecisionStatus.APPROVED,
        risk_reducing=True,
        intent_id=intent.intent_id,
        intent_fingerprint=decision.intent_fingerprint,
        market_fingerprint=decision.market_fingerprint,
        valid_until=now - timedelta(seconds=1),
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="expired"):
        call(candidate_decision=expired)

    wrong_notional = SimpleNamespace(
        status=RiskDecisionStatus.APPROVED,
        risk_reducing=True,
        intent_id=intent.intent_id,
        intent_fingerprint=decision.intent_fingerprint,
        market_fingerprint=decision.market_fingerprint,
        valid_until=decision.valid_until,
        approved_notional=decision.approved_notional + Decimal("1"),
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="approved notional"):
        call(candidate_decision=wrong_notional)

    with pytest.raises(CryptoProtectionPreparationBlocked, match="active USD PAPER account"):
        call(candidate_account=_fake_account(ctx, status="BLOCKED"))

    bad_account = _fake_account(ctx, fingerprint="a" * 64)
    with pytest.raises(CryptoProtectionPreparationBlocked, match="asset/account evidence binding mismatch"):
        call(candidate_account=bad_account, candidate_asset=asset)

    bad_asset = _fake_asset(ctx, account, credential_reference="different-credential")
    with pytest.raises(CryptoProtectionPreparationBlocked, match="credential references differ"):
        call(candidate_asset=bad_asset)

    bad_asset = _fake_asset(ctx, account, fingerprint="b" * 64)
    with pytest.raises(CryptoProtectionPreparationBlocked, match="lifecycle-bound asset evidence"):
        call(candidate_asset=bad_asset)

    with pytest.raises(CryptoProtectionPreparationBlocked, match="lifecycle-bound product profile"):
        call(candidate_product=_FakeProduct(ctx, fingerprint="c" * 64))

    with pytest.raises(CryptoProtectionPreparationBlocked, match="CRYPTO ProductCapabilities"):
        call(candidate_product=_FakeProduct(ctx, asset_class=AssetClass.EQUITY))

    with pytest.raises(CryptoProtectionPreparationBlocked, match="CRYPTO_STOP_LIMIT"):
        call(candidate_product=_FakeProduct(ctx, protection_model=ProtectionModel.BROKER_NATIVE_BRACKET))

    with pytest.raises(CryptoProtectionPreparationBlocked, match="forbids margin/short"):
        call(candidate_product=_FakeProduct(ctx, marginable=True))

    stale_account = _fake_account(ctx, attested_at=now - timedelta(seconds=31))
    stale_asset = _fake_asset(ctx, stale_account)
    with pytest.raises(CryptoProtectionPreparationBlocked, match="account evidence is stale"):
        call(candidate_account=stale_account, candidate_asset=stale_asset)

    stale_asset = _fake_asset(ctx, account, observed_at=now - timedelta(seconds=31))
    with pytest.raises(CryptoProtectionPreparationBlocked, match="asset evidence is stale"):
        call(candidate_asset=stale_asset)

    stale_product = _FakeProduct(ctx, observed_at=now - timedelta(seconds=31))
    with pytest.raises(CryptoProtectionPreparationBlocked, match="product profile evidence is stale"):
        call(candidate_product=stale_product)

    stale_market = SimpleNamespace(
        market=market.market,
        received_at=now - timedelta(seconds=31),
    )
    with pytest.raises(CryptoProtectionPreparationBlocked, match="market evidence is stale"):
        call(candidate_market=stale_market)
