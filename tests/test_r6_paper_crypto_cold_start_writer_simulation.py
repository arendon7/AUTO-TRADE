from __future__ import annotations

from datetime import timedelta
import json

import pytest

from autotrade.ledger import InMemoryEventLedger
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_attempt import (
    SQLiteCryptoColdStartExecutionAttemptRegistry,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_execution_bridge import (
    CryptoColdStartExecutionBridge,
)
from autotrade.brokers.alpaca_paper_crypto_cold_start_final_guard import (
    CryptoColdStartFinalWriteAttestation,
    CryptoColdStartFinalWritePhase,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBlocked,
    CryptoLifecycleStatus,
)
from autotrade.brokers.alpaca_paper_crypto_order import CryptoOrderRole
from autotrade.brokers.alpaca_paper_crypto_pre_io import (
    CryptoPreIoInterlockError,
    DeterministicCryptoPaperSimulationTransport,
)
from autotrade.brokers.alpaca_paper_crypto_writer import (
    ALPACA_PAPER_TRADING_HOST,
    CRYPTO_ORDERS_PATH,
    AlpacaPaperCryptoWriteResponse,
    AlpacaPaperCryptoWriter,
    AlpacaPaperCryptoWriterConfig,
    CryptoPaperWriterAmbiguous,
    GuardedAlpacaPaperCryptoWriteTransport,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from test_r6_paper_crypto_canary_coordinator import NOW, _decision, _intent, _market
from test_r6_paper_crypto_cold_start_final_guard import _pre, _setup


class _TestOnlyColdStartEntryTransport(GuardedAlpacaPaperCryptoWriteTransport):
    """Test-only nominal capability; production checker never scans test classes."""

    role = CryptoOrderRole.ENTRY

    def __init__(self, *, delegate, authorizer) -> None:
        self.delegate = delegate
        self.authorizer = authorizer
        self.last_attestation = None
        self.delegated_calls = 0

    def post(self, *, host, path, headers, body, timeout_seconds, max_response_bytes):
        if host != ALPACA_PAPER_TRADING_HOST or path != CRYPTO_ORDERS_PATH:
            raise CryptoPreIoInterlockError("test cold-start transport endpoint drift")
        if self.last_attestation is not None or self.delegated_calls != 0:
            raise CryptoPreIoInterlockError("test cold-start transport is one-shot")
        payload = json.loads(body.decode("utf-8"))
        attestation = self.authorizer()
        if not isinstance(attestation, CryptoColdStartFinalWriteAttestation):
            raise CryptoPreIoInterlockError("test cold-start authorizer returned wrong type")
        if attestation.phase is not CryptoColdStartFinalWritePhase.PRE_IO:
            raise CryptoPreIoInterlockError("test cold-start transport requires PRE_IO")
        if attestation.lifecycle_status is not CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN:
            raise CryptoPreIoInterlockError("test cold-start PRE_IO must observe UNKNOWN")
        if attestation.entry_attempt_count != 1:
            raise CryptoPreIoInterlockError("test cold-start PRE_IO requires one attempt")
        if attestation.client_order_id != payload.get("client_order_id"):
            raise CryptoPreIoInterlockError("test cold-start client_order_id mismatch")
        self.last_attestation = attestation
        response = self.delegate.post(
            host=host,
            path=path,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        self.delegated_calls += 1
        return response


class _AmbiguousSimulationDelegate:
    def __init__(self) -> None:
        self.calls = 0

    def post(self, **_kwargs) -> AlpacaPaperCryptoWriteResponse:
        self.calls += 1
        raise TimeoutError("synthetic ambiguous timeout after possible broker receive")


def _risk_and_market(ctx):
    attestation = _market()
    intent = _intent(quantity=ctx.package.quantity, limit_price=ctx.package.limit_price)
    decision = _decision(intent, attestation, approved_notional=ctx.package.notional)
    return decision, attestation.market


def _stage(ctx):
    pre = _pre(ctx)
    checkpoint = SQLiteCryptoColdStartExecutionAttemptRegistry(ctx.core).record_pre_consume(pre)
    decision, market = _risk_and_market(ctx)
    bridge = CryptoColdStartExecutionBridge(
        order_store=ctx.order_store,
        ledger=InMemoryEventLedger(),
        authority_provider=ctx.authority,
    )
    stage = bridge.stage_after_checkpoint(
        package=ctx.package,
        operator_decision=ctx.operator_decision,
        operator_registry=ctx.operator_registry,
        checkpoint=checkpoint,
        risk_decision=decision,
        market=market,
        consume_at=NOW + timedelta(seconds=4, milliseconds=200),
        stage_at=NOW + timedelta(seconds=4, milliseconds=300),
    )
    return pre, checkpoint, stage


def _authorizer(ctx, pre):
    def authorize():
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
            now=NOW + timedelta(seconds=4, milliseconds=450),
            phase=CryptoColdStartFinalWritePhase.PRE_IO,
            expected_attempt_id=ctx.operator_decision.context.attempt_id,
            previous_attestation=pre,
        )

    return authorize


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(
        key_id="simulation-paper-key",
        secret_key="simulation-paper-secret",
    )


def test_simulated_writer_marks_unknown_before_cold_start_preio_and_delegates_once(tmp_path) -> None:
    ctx = _setup(tmp_path)
    pre, checkpoint, stage = _stage(ctx)
    assert stage.checkpoint_hash == checkpoint.record_hash
    assert ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_PREPARED

    simulation = DeterministicCryptoPaperSimulationTransport()
    transport = _TestOnlyColdStartEntryTransport(
        delegate=simulation,
        authorizer=_authorizer(ctx, pre),
    )
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True),
        transport=transport,
    )
    receipt = writer.submit_once(
        lifecycle=ctx.lifecycle,
        lifecycle_id=ctx.package.lifecycle_id,
        order=ctx.broker_order,
        credentials=_credentials(),
        now=NOW + timedelta(seconds=4, milliseconds=400),
    )

    assert receipt.client_order_id == ctx.package.client_order_id
    assert receipt.broker_order_id == "simulation-broker-order-1"
    assert simulation.calls == 1
    assert transport.delegated_calls == 1
    assert transport.last_attestation is not None
    assert transport.last_attestation.phase is CryptoColdStartFinalWritePhase.PRE_IO
    lifecycle = ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state
    assert lifecycle.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert lifecycle.entry_attempt_count == 1


def test_simulated_ambiguous_timeout_stays_unknown_and_never_blind_retries(tmp_path) -> None:
    ctx = _setup(tmp_path)
    pre, _, _ = _stage(ctx)
    ambiguous = _AmbiguousSimulationDelegate()
    transport = _TestOnlyColdStartEntryTransport(
        delegate=ambiguous,
        authorizer=_authorizer(ctx, pre),
    )
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True),
        transport=transport,
    )

    with pytest.raises(CryptoPaperWriterAmbiguous, match="outcome is unknown"):
        writer.submit_once(
            lifecycle=ctx.lifecycle,
            lifecycle_id=ctx.package.lifecycle_id,
            order=ctx.broker_order,
            credentials=_credentials(),
            now=NOW + timedelta(seconds=4, milliseconds=400),
        )
    state = ctx.lifecycle.snapshot(ctx.package.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert state.entry_attempt_count == 1
    assert ambiguous.calls == 1

    with pytest.raises(CryptoLifecycleBlocked, match="ENTRY_PREPARED"):
        writer.submit_once(
            lifecycle=ctx.lifecycle,
            lifecycle_id=ctx.package.lifecycle_id,
            order=ctx.broker_order,
            credentials=_credentials(),
            now=NOW + timedelta(seconds=4, milliseconds=500),
        )
    assert ambiguous.calls == 1
