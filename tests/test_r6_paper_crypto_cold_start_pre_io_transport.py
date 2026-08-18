from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from autotrade.cold_start_oms import ColdStartOrderManagementSystem
from autotrade.domain import PortfolioSnapshot
from autotrade.health_bridge import HealthBridgePolicy, SQLiteHealthBridgeStore
from autotrade.ledger import InMemoryEventLedger
from autotrade.oms import OrderManagementSystem
from autotrade.persistence import SQLitePortfolioStore, SQLiteRuntime
from autotrade.research.health import SQLiteHealthStateStore
from autotrade.risk_state import SQLiteR2SafetyStateStore
from autotrade.state import InMemoryOrderStore
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import CryptoPaperCanaryCoordinator
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_bridge import CryptoColdStartExecutionBridge
from autotrade.brokers.alpaca_paper_crypto_cold_start_final_guard import (
    COLD_START_KILL_REASON,
    CryptoColdStartPaperFinalWriteGuard,
    SQLiteCryptoColdStartAuthorityProvider,
)
import autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io as cold_pre_io
from autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io import (
    ColdStartFinalGuardedCryptoEntryTransport,
    CryptoColdStartPreIoExecutionContext,
    CryptoColdStartPreIoInterlockError,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io_authority import CryptoColdStartPreIoAuthority
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus, SQLiteCryptoPaperLifecycle
from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    SQLiteCryptoOperatorDecisionRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_pre_io import DeterministicCryptoPaperSimulationTransport
from autotrade.brokers.alpaca_paper_crypto_writer import (
    ALPACA_PAPER_TRADING_HOST,
    CRYPTO_ORDERS_PATH,
    AlpacaPaperCryptoWriteResponse,
    AlpacaPaperCryptoWriter,
    AlpacaPaperCryptoWriterConfig,
    CryptoPaperWriterAmbiguous,
    CryptoPaperWriterPolicyError,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from test_r6_paper_crypto_canary_coordinator import NOW, _NoBroker, _account, _asset, _decision, _intent, _market
from test_r6_paper_crypto_cold_start_final_guard import _flat_attestation, _profile


KEY_ID = "simulation-paper-key"
SECRET = "simulation-paper-secret"
CREDENTIAL_REFERENCE = sha256(KEY_ID.encode("utf-8")).hexdigest()
ZERO = Decimal("0")


class _AmbiguousDelegate:
    def __init__(self) -> None:
        self.calls = 0

    def post(self, **_kwargs) -> AlpacaPaperCryptoWriteResponse:
        self.calls += 1
        raise TimeoutError("synthetic possible receive")


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


def _setup(tmp_path):
    core = SQLiteRuntime(tmp_path / "core.sqlite3")
    safety = SQLiteR2SafetyStateStore(core)
    safety.activate(reason=COLD_START_KILL_REASON, now=NOW + timedelta(seconds=1))
    prepared_account = replace(_account(), credential_reference=CREDENTIAL_REFERENCE)
    SQLitePortfolioStore(core).initialize(_portfolio(prepared_account), now=NOW + timedelta(seconds=1))
    health_reader = SQLiteHealthStateStore(core.path)
    SQLiteHealthBridgeStore(
        core,
        health_reader=health_reader,
        policy=HealthBridgePolicy(require_strategy_state=True, require_portfolio_state=True),
    )

    order_store = InMemoryOrderStore()
    preparation_oms = OrderManagementSystem(
        broker=_NoBroker(), ledger=InMemoryEventLedger(), order_store=order_store
    )
    prepared_asset = _asset(prepared_account)
    prepared_profile = _profile(prepared_asset)
    prepared_market = _market()
    intent = _intent(quantity=Decimal("0.0001"), limit_price=Decimal("20000"))
    decision = _decision(intent, prepared_market, approved_notional=Decimal("2"))
    lifecycle = SQLiteCryptoPaperLifecycle(core)
    prepared = CryptoPaperCanaryCoordinator(oms=preparation_oms).prepare_entry(
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
    decision_context = CryptoOperatorDecisionContext.from_prepared_package(
        prepared.package, attempt_id="crypto-cold-start-transport-attempt-001"
    )
    operator_state = operator_registry.record_operator_approval(
        context=decision_context,
        operator_id="operator-001",
        issued_at=NOW + timedelta(seconds=3),
        expires_at=NOW + timedelta(seconds=10),
    )

    fresh_at = NOW + timedelta(seconds=4)
    fresh_account = replace(_account(observed=fresh_at), credential_reference=CREDENTIAL_REFERENCE)
    fresh_asset = _asset(fresh_account, observed=fresh_at)
    fresh_profile = _profile(fresh_asset)
    fresh_market = _market(observed=fresh_at)
    fresh_flat = _flat_attestation(fresh_account, at=fresh_at)
    authority_provider = SQLiteCryptoColdStartAuthorityProvider(core)
    guard = CryptoColdStartPaperFinalWriteGuard(
        order_store=order_store, authority_provider=authority_provider
    )
    pre = guard.authorize(
        package=prepared.package,
        operator_decision=operator_state.decision,
        operator_registry=operator_registry,
        broker_order=prepared.broker_order,
        lifecycle=lifecycle,
        prepared_account=prepared_account,
        prepared_asset=prepared_asset,
        prepared_product_profile=prepared_profile,
        fresh_account=fresh_account,
        fresh_asset=fresh_asset,
        fresh_product_profile=fresh_profile,
        fresh_market=fresh_market,
        fresh_flat_account=fresh_flat,
        now=NOW + timedelta(seconds=4, milliseconds=100),
        phase=cold_pre_io.CryptoColdStartFinalWritePhase.PRE_CONSUME,
    )
    checkpoint_registry = SQLiteCryptoColdStartExecutionAttemptRegistry(core)
    checkpoint = checkpoint_registry.record_pre_consume(pre)

    risk = _decision(intent, prepared_market, approved_notional=Decimal("2"))
    ledger = InMemoryEventLedger()
    cold_oms = ColdStartOrderManagementSystem(
        broker=_NoBroker(),
        ledger=ledger,
        order_store=order_store,
        safety_state_store=safety,
    )
    bridge = CryptoColdStartExecutionBridge(
        oms=cold_oms,
        authority_provider=authority_provider,
    )
    bridge.stage_after_checkpoint(
        package=prepared.package,
        operator_decision=operator_state.decision,
        operator_registry=operator_registry,
        checkpoint=checkpoint,
        risk_decision=risk,
        market=prepared_market.market,
        consume_at=NOW + timedelta(seconds=4, milliseconds=200),
        stage_at=NOW + timedelta(seconds=4, milliseconds=300),
    )
    pre_io_authority = CryptoColdStartPreIoAuthority(
        guard=guard,
        checkpoint_registry=checkpoint_registry,
        oms=cold_oms,
    )
    execution_context = CryptoColdStartPreIoExecutionContext(
        package=prepared.package,
        operator_decision=operator_state.decision,
        operator_registry=operator_registry,
        broker_order=prepared.broker_order,
        lifecycle=lifecycle,
        prepared_account=prepared_account,
        prepared_asset=prepared_asset,
        prepared_product_profile=prepared_profile,
        fresh_account=fresh_account,
        fresh_asset=fresh_asset,
        fresh_product_profile=fresh_profile,
        fresh_market=fresh_market,
        fresh_flat_account=fresh_flat,
    )
    return SimpleNamespace(
        lifecycle=lifecycle,
        package=prepared.package,
        broker_order=prepared.broker_order,
        authority=pre_io_authority,
        context=execution_context,
        checkpoint=checkpoint,
    )


def _headers(key_id=KEY_ID, secret=SECRET):
    return {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}


def test_production_cold_start_transport_delegates_once_only_after_durable_preio(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path)
    monkeypatch.setattr(cold_pre_io, "_utc_now", lambda: NOW + timedelta(seconds=4, milliseconds=450))
    delegate = DeterministicCryptoPaperSimulationTransport()
    transport = ColdStartFinalGuardedCryptoEntryTransport(
        delegate=delegate,
        authority=ctx.authority,
        context=ctx.context,
    )
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True), transport=transport
    )
    receipt = writer.submit_once(
        lifecycle=ctx.lifecycle,
        lifecycle_id=ctx.package.lifecycle_id,
        order=ctx.broker_order,
        credentials=AlpacaPaperCredentials(key_id=KEY_ID, secret_key=SECRET),
        now=NOW + timedelta(seconds=4, milliseconds=400),
    )
    assert receipt.client_order_id == ctx.package.client_order_id
    assert delegate.calls == 1
    assert transport.delegated_calls == 1
    assert transport.last_attestation is not None
    assert transport.last_attestation.previous_attestation_hash == ctx.checkpoint.pre_consume.attestation_hash
    assert transport.last_attestation.credential_reference == CREDENTIAL_REFERENCE
    state = ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert state.entry_attempt_count == 1


def test_cold_start_transport_rejects_wrong_effective_key_before_delegate(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path)
    monkeypatch.setattr(cold_pre_io, "_utc_now", lambda: NOW + timedelta(seconds=4, milliseconds=450))
    delegate = DeterministicCryptoPaperSimulationTransport()
    transport = ColdStartFinalGuardedCryptoEntryTransport(
        delegate=delegate, authority=ctx.authority, context=ctx.context
    )
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True), transport=transport
    )
    with pytest.raises(CryptoPaperWriterAmbiguous) as captured:
        writer.submit_once(
            lifecycle=ctx.lifecycle,
            lifecycle_id=ctx.package.lifecycle_id,
            order=ctx.broker_order,
            credentials=AlpacaPaperCredentials(key_id="different-paper-key", secret_key=SECRET),
            now=NOW + timedelta(seconds=4, milliseconds=400),
        )
    assert isinstance(captured.value.__cause__, CryptoColdStartPreIoInterlockError)
    assert "credential reference" in str(captured.value.__cause__)
    assert delegate.calls == 0
    assert transport.last_attestation is None
    assert ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN


def test_cold_start_transport_latches_preio_before_ambiguous_delegate_and_is_one_shot(tmp_path, monkeypatch) -> None:
    ctx = _setup(tmp_path)
    monkeypatch.setattr(cold_pre_io, "_utc_now", lambda: NOW + timedelta(seconds=4, milliseconds=450))
    delegate = _AmbiguousDelegate()
    transport = ColdStartFinalGuardedCryptoEntryTransport(
        delegate=delegate, authority=ctx.authority, context=ctx.context
    )
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True), transport=transport
    )
    credentials = AlpacaPaperCredentials(key_id=KEY_ID, secret_key=SECRET)
    with pytest.raises(CryptoPaperWriterAmbiguous):
        writer.submit_once(
            lifecycle=ctx.lifecycle,
            lifecycle_id=ctx.package.lifecycle_id,
            order=ctx.broker_order,
            credentials=credentials,
            now=NOW + timedelta(seconds=4, milliseconds=400),
        )
    assert delegate.calls == 1
    assert transport.last_attestation is not None
    assert transport.delegated_calls == 0
    with pytest.raises(CryptoColdStartPreIoInterlockError, match="one-shot"):
        transport.post(
            host=ALPACA_PAPER_TRADING_HOST,
            path=CRYPTO_ORDERS_PATH,
            headers=_headers(),
            body=json.dumps(ctx.broker_order.to_payload(), sort_keys=True, separators=(",", ":")).encode(),
            timeout_seconds=5,
            max_response_bytes=1024,
        )
    assert delegate.calls == 1


def test_cold_start_transport_rejects_endpoint_payload_json_and_headers_before_authority(tmp_path) -> None:
    ctx = _setup(tmp_path)
    delegate = DeterministicCryptoPaperSimulationTransport()

    def transport():
        return ColdStartFinalGuardedCryptoEntryTransport(
            delegate=delegate, authority=ctx.authority, context=ctx.context
        )

    with pytest.raises(CryptoPaperWriterPolicyError, match="exact PAPER orders endpoint"):
        transport().post(
            host="api.alpaca.markets",
            path=CRYPTO_ORDERS_PATH,
            headers=_headers(),
            body=b"{}",
            timeout_seconds=5,
            max_response_bytes=1024,
        )
    with pytest.raises(CryptoColdStartPreIoInterlockError, match="invalid JSON"):
        transport().post(
            host=ALPACA_PAPER_TRADING_HOST,
            path=CRYPTO_ORDERS_PATH,
            headers=_headers(),
            body=b"{",
            timeout_seconds=5,
            max_response_bytes=1024,
        )
    with pytest.raises(CryptoColdStartPreIoInterlockError, match="differs from prepared"):
        transport().post(
            host=ALPACA_PAPER_TRADING_HOST,
            path=CRYPTO_ORDERS_PATH,
            headers=_headers(),
            body=json.dumps({**ctx.broker_order.to_payload(), "qty": "999"}).encode(),
            timeout_seconds=5,
            max_response_bytes=1024,
        )
    correct_body = json.dumps(
        ctx.broker_order.to_payload(), sort_keys=True, separators=(",", ":")
    ).encode()
    with pytest.raises(CryptoColdStartPreIoInterlockError, match="Key ID is missing"):
        transport().post(
            host=ALPACA_PAPER_TRADING_HOST,
            path=CRYPTO_ORDERS_PATH,
            headers={"APCA-API-SECRET-KEY": SECRET},
            body=correct_body,
            timeout_seconds=5,
            max_response_bytes=1024,
        )
    with pytest.raises(CryptoColdStartPreIoInterlockError, match="Secret is missing"):
        transport().post(
            host=ALPACA_PAPER_TRADING_HOST,
            path=CRYPTO_ORDERS_PATH,
            headers={"APCA-API-KEY-ID": KEY_ID},
            body=correct_body,
            timeout_seconds=5,
            max_response_bytes=1024,
        )
    assert delegate.calls == 0
