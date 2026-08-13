from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderType,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
)
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import ProductCapabilities
from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import (
    CryptoCanaryPreparationBlocked,
    CryptoPaperCanaryCoordinator,
    FIRST_CANARY_MAX_NOTIONAL,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketAttestation
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
)


NOW = datetime(2026, 8, 13, 6, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64


class _NoBroker:
    def submit(self, **_kwargs):
        raise AssertionError("offline crypto coordinator may not call broker")


def _account(*, observed: datetime = NOW) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="12345678-abcd-abcd-abcd-123456789012",
        account_reference="f" * 64,
        credential_reference=HASH_B,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=True,
        attested_at=observed,
        request_id="req-account",
        source_host=ALPACA_PAPER_TRADING_HOST,
        source_path="/v2/account",
    )


def _asset(account, *, observed: datetime = NOW, price_increment: Decimal = Decimal("1")):
    return AlpacaPaperCryptoAssetAttestation(
        symbol="BTC/USD",
        asset_id="asset-btc",
        asset_class="crypto",
        exchange="CRYPTO",
        status="active",
        tradable=True,
        fractionable=True,
        marginable=False,
        shortable=False,
        min_order_size=Decimal("0.0001"),
        min_trade_increment=Decimal("0.0001"),
        price_increment=price_increment,
        account_attestation_fingerprint=account.fingerprint,
        credential_reference=account.credential_reference,
        observed_at=observed,
        request_id="req-asset",
        response_sha256=HASH_A,
        source_path="/v2/assets/BTC%2FUSD",
    )


def _market(*, observed: datetime = NOW):
    snapshot = MarketSnapshot(
        symbol="BTC/USD",
        bid=Decimal("99999"),
        ask=Decimal("100000"),
        last=Decimal("99999.5"),
        observed_at=observed - timedelta(seconds=1),
    )
    return AlpacaPaperCryptoMarketAttestation(
        market=snapshot,
        location="us",
        orderbook_observed_at=observed - timedelta(seconds=1),
        trade_observed_at=observed - timedelta(seconds=1),
        received_at=observed,
        orderbook_response_sha256="c" * 64,
        trade_response_sha256="d" * 64,
    )


def _intent(*, quantity=Decimal("0.0001"), limit_price=Decimal("100000")):
    return OrderIntent(
        intent_id="crypto-intent-001",
        idempotency_key="crypto-intent-001-key",
        strategy_id="R6_CRYPTO_FIRST_CANARY",
        symbol="BTC/USD",
        side=Side.BUY,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        created_at=NOW,
        limit_price=limit_price,
    )


def _decision(intent, market, *, approved_notional=None, valid_until=None):
    return RiskDecision(
        decision_id="crypto-decision-001",
        intent_id=intent.intent_id,
        status=RiskDecisionStatus.APPROVED,
        reason_code="APPROVED",
        reason_detail="bounded crypto first canary",
        evaluated_at=NOW,
        valid_until=valid_until or (NOW + timedelta(seconds=20)),
        limits_version="r6-crypto-first-canary-v1",
        intent_fingerprint=intent_fingerprint(intent),
        market_fingerprint=market_fingerprint(market.market),
        approved_notional=approved_notional or (intent.quantity * intent.limit_price),
        risk_reducing=False,
        safety_state_version=0,
    )


def _coordinator():
    return CryptoPaperCanaryCoordinator(
        oms=OrderManagementSystem(broker=_NoBroker(), ledger=InMemoryEventLedger())
    )


def _prepare(tmp_path, **overrides):
    account = overrides.pop("account", _account())
    asset = overrides.pop("asset", _asset(account))
    market = overrides.pop("market_attestation", _market())
    intent = overrides.pop("intent", _intent())
    profile = overrides.pop(
        "product_profile",
        ProductCapabilities.crypto_alpaca_paper(
            source_fingerprint=asset.fingerprint,
            observed_at=asset.observed_at,
            fractionable=asset.fractionable,
            marginable=asset.marginable,
            shortable=asset.shortable,
        ),
    )
    decision = overrides.pop("decision", _decision(intent, market))
    lifecycle = overrides.pop(
        "lifecycle",
        SQLiteCryptoPaperLifecycle(SQLiteRuntime(tmp_path / "crypto-canary.sqlite3")),
    )
    result = _coordinator().prepare_entry(
        intent=intent,
        decision=decision,
        market_attestation=market,
        account_attestation=account,
        asset_attestation=asset,
        product_profile=profile,
        lifecycle=lifecycle,
        now=overrides.pop("now", NOW + timedelta(seconds=2)),
        certified_tracks=overrides.pop("certified_tracks", ("R0", "R1", "R2", "R3", "R4", "R5")),
        reconciliation_clean=overrides.pop("reconciliation_clean", True),
        unresolved_unknown_orders=overrides.pop("unresolved_unknown_orders", 0),
        relevant_open_orders=overrides.pop("relevant_open_orders", 0),
        confirmed_pair_position_quantity=overrides.pop("confirmed_pair_position_quantity", Decimal("0")),
    )
    assert not overrides
    return result, lifecycle


def test_crypto_canary_preparation_ends_offline_at_operator_decision(tmp_path) -> None:
    result, lifecycle = _prepare(tmp_path)
    package = result.package
    assert result.order.status.value == "VALIDATED"
    assert result.broker_order.order_type.value == "limit"
    assert result.broker_order.time_in_force.value == "ioc"
    assert result.broker_order.side.value == "buy"
    assert result.broker_order.to_payload() == {
        "symbol": "BTC/USD",
        "qty": "0.0001",
        "side": "buy",
        "type": "limit",
        "time_in_force": "ioc",
        "client_order_id": result.broker_order.client_order_id,
        "limit_price": "100000",
    }
    assert package.notional == Decimal("10")
    assert package.effective_notional_cap == FIRST_CANARY_MAX_NOTIONAL
    assert package.network_write_authorized is False
    assert package.next_action == "OPERATOR_DECISION_REQUIRED"
    assert package.opening_short is False
    assert package.uses_margin is False
    assert package.order_status == "VALIDATED"
    assert len(package.package_hash) == 64
    assert package.package_hash == result.package.canonical_payload()["package_hash"]
    state = lifecycle.snapshot(package.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.ENTRY_PREPARED
    assert state.entry_attempt_count == 0
    assert state.event_head_hash == package.lifecycle_event_head_hash
    assert state.control_hash == package.lifecycle_control_hash


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"reconciliation_clean": False}, "clean reconciliation"),
        ({"unresolved_unknown_orders": 1}, "UNKNOWN"),
        ({"relevant_open_orders": 1}, "zero relevant open orders"),
        ({"confirmed_pair_position_quantity": Decimal("0.0001")}, "zero confirmed pair position"),
        ({"certified_tracks": ("R0", "R1", "R2", "R3", "R4")}, "exactly R0-R5"),
    ],
)
def test_crypto_canary_requires_clean_flat_first_canary_context(tmp_path, kwargs, match) -> None:
    with pytest.raises(CryptoCanaryPreparationBlocked, match=match):
        _prepare(tmp_path, **kwargs)


def test_crypto_canary_rejects_stale_account_asset_or_market_evidence(tmp_path) -> None:
    stale = NOW - timedelta(seconds=31)
    stale_account = _account(observed=stale)
    with pytest.raises(CryptoCanaryPreparationBlocked, match="account evidence"):
        _prepare(tmp_path / "a", account=stale_account, asset=_asset(stale_account, observed=NOW))

    account = _account()
    with pytest.raises(CryptoCanaryPreparationBlocked, match="asset evidence"):
        _prepare(tmp_path / "b", account=account, asset=_asset(account, observed=stale))

    with pytest.raises(CryptoCanaryPreparationBlocked, match="market evidence"):
        _prepare(tmp_path / "c", market_attestation=_market(observed=stale))


def test_crypto_canary_rejects_equity_profile_or_unbound_product_evidence(tmp_path) -> None:
    account = _account()
    asset = _asset(account)
    equity = ProductCapabilities.us_equity_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=NOW,
        fractionable=True,
        marginable=True,
        shortable=True,
    )
    with pytest.raises(CryptoCanaryPreparationBlocked, match="CRYPTO ProductCapabilities"):
        _prepare(tmp_path / "equity", account=account, asset=asset, product_profile=equity)

    other_account = _account(observed=NOW - timedelta(seconds=1))
    other_asset = _asset(other_account)
    wrong_profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=other_asset.fingerprint,
        observed_at=NOW,
        fractionable=True,
        marginable=False,
        shortable=False,
    )
    with pytest.raises(CryptoCanaryPreparationBlocked, match="exact asset evidence"):
        _prepare(tmp_path / "unbound", account=account, asset=asset, product_profile=wrong_profile)


def test_crypto_canary_rejects_quantity_or_price_that_would_be_normalized(tmp_path) -> None:
    with pytest.raises(CryptoCanaryPreparationBlocked, match="quantity must already satisfy"):
        _prepare(tmp_path / "qty", intent=_intent(quantity=Decimal("0.00015")))
    with pytest.raises(CryptoCanaryPreparationBlocked, match="limit price must already satisfy"):
        _prepare(tmp_path / "price", intent=_intent(limit_price=Decimal("100000.5")))


def test_crypto_canary_rejects_notional_above_conservative_or_safety_cap(tmp_path) -> None:
    large = _intent(quantity=Decimal("0.0003"), limit_price=Decimal("100000"))
    market = _market()
    with pytest.raises(CryptoCanaryPreparationBlocked, match="conservative cap"):
        _prepare(
            tmp_path / "global-cap",
            intent=large,
            market_attestation=market,
            decision=_decision(large, market, approved_notional=Decimal("30")),
        )

    normal = _intent()
    with pytest.raises(CryptoCanaryPreparationBlocked, match="Safety-approved notional"):
        _prepare(
            tmp_path / "safety-cap",
            intent=normal,
            market_attestation=market,
            decision=_decision(normal, market, approved_notional=Decimal("9")),
        )


def test_crypto_canary_rejects_risk_decision_or_symbol_identity_drift(tmp_path) -> None:
    intent = _intent()
    market = _market()
    decision = _decision(intent, market)
    wrong = RiskDecision(
        decision_id=decision.decision_id,
        intent_id=decision.intent_id,
        status=decision.status,
        reason_code=decision.reason_code,
        reason_detail=decision.reason_detail,
        evaluated_at=decision.evaluated_at,
        valid_until=decision.valid_until,
        limits_version=decision.limits_version,
        intent_fingerprint="e" * 64,
        market_fingerprint=decision.market_fingerprint,
        approved_notional=decision.approved_notional,
        risk_reducing=decision.risk_reducing,
        safety_state_version=decision.safety_state_version,
    )
    with pytest.raises(CryptoCanaryPreparationBlocked, match="exact crypto intent"):
        _prepare(tmp_path / "risk", intent=intent, market_attestation=market, decision=wrong)

    eth = OrderIntent(
        intent_id="eth-intent",
        idempotency_key="eth-key",
        strategy_id="R6_CRYPTO_FIRST_CANARY",
        symbol="ETH/USD",
        side=Side.BUY,
        quantity=Decimal("0.0001"),
        order_type=OrderType.LIMIT,
        created_at=NOW,
        limit_price=Decimal("100000"),
    )
    with pytest.raises(CryptoCanaryPreparationBlocked, match="symbol identity mismatch"):
        _prepare(
            tmp_path / "symbol",
            intent=eth,
            market_attestation=market,
            decision=_decision(eth, market),
        )
