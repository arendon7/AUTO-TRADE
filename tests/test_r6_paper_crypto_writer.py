from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBinding,
    CryptoLifecycleBlocked,
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from autotrade.brokers.alpaca_paper_crypto_order import (
    CryptoOrderRole,
    build_crypto_entry_order,
    build_crypto_long_protection_order,
    deterministic_crypto_client_order_id,
)
from autotrade.brokers.alpaca_paper_crypto_writer import (
    AlpacaPaperCryptoWriteResponse,
    AlpacaPaperCryptoWriter,
    AlpacaPaperCryptoWriterConfig,
    CryptoPaperWriterAmbiguous,
    CryptoPaperWriterDisabled,
    CryptoPaperWriterPolicyError,
    CRYPTO_ORDERS_PATH,
    HttpsAlpacaPaperCryptoWriteTransport,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import BrokerOrderType, ProductCapabilities, TimeInForce


NOW = datetime(2026, 8, 13, 5, 0, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")


def _asset() -> AlpacaPaperCryptoAssetAttestation:
    return AlpacaPaperCryptoAssetAttestation(
        symbol="BTC/USD",
        asset_id="276e2673-764b-4ab6-a611-caf665ca6340",
        asset_class="crypto",
        exchange="CRYPTO",
        status="active",
        tradable=True,
        fractionable=True,
        marginable=False,
        shortable=False,
        min_order_size=Decimal("0.0001"),
        min_trade_increment=Decimal("0.0001"),
        price_increment=Decimal("1"),
        account_attestation_fingerprint="a" * 64,
        credential_reference="b" * 64,
        observed_at=NOW,
        request_id="req-crypto",
        response_sha256="c" * 64,
        source_path="/v2/assets/BTC%2FUSD",
    )


def _profile(asset: AlpacaPaperCryptoAssetAttestation) -> ProductCapabilities:
    return ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )


def _setup(tmp_path, *, lifecycle_id: str = "crypto-writer-001"):
    asset = _asset()
    profile = _profile(asset)
    entry = build_crypto_entry_order(
        symbol="BTC/USD",
        quantity=Decimal("0.0010"),
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=lifecycle_id,
            role=CryptoOrderRole.ENTRY,
        ),
        product_profile=profile,
        asset_attestation=asset,
        limit_price=Decimal("100000"),
    )
    binding = CryptoLifecycleBinding(
        lifecycle_id=lifecycle_id,
        account_attestation_fingerprint="d" * 64,
        asset_attestation_fingerprint=asset.fingerprint,
        product_profile_fingerprint=profile.fingerprint,
        symbol="BTC/USD",
        entry_order_fingerprint=entry.fingerprint,
        entry_client_order_id=entry.client_order_id,
        entry_quantity=entry.quantity,
        created_at=NOW,
    )
    runtime = SQLiteRuntime(tmp_path / f"{lifecycle_id}.sqlite3")
    lifecycle = SQLiteCryptoPaperLifecycle(runtime)
    lifecycle.prepare(binding)
    return lifecycle, asset, profile, entry, binding


def _ack(order, *, overrides: dict[str, object] | None = None) -> AlpacaPaperCryptoWriteResponse:
    payload: dict[str, object] = {
        "id": "broker-order-123",
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "asset_class": "crypto",
        "side": order.side.value,
        "type": order.order_type.value,
        "time_in_force": order.time_in_force.value,
        "status": "accepted",
        "qty": str(order.quantity),
        "filled_qty": "0",
        "limit_price": str(order.limit_price) if order.limit_price is not None else None,
        "stop_price": str(order.stop_price) if order.stop_price is not None else None,
    }
    if overrides:
        payload.update(overrides)
    return AlpacaPaperCryptoWriteResponse(
        status_code=200,
        body=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-request-id": "req-write-1"},
    )


class RecordingTransport:
    def __init__(self, response=None, *, error: Exception | None = None, before=None) -> None:
        self.response = response
        self.error = error
        self.before = before
        self.calls = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        if self.before:
            self.before()
        if self.error:
            raise self.error
        return self.response


def test_crypto_writer_is_disabled_by_default_and_performs_zero_io(tmp_path) -> None:
    lifecycle, _asset_value, _profile_value, entry, binding = _setup(tmp_path)
    transport = RecordingTransport(_ack(entry))
    with pytest.raises(CryptoPaperWriterDisabled):
        AlpacaPaperCryptoWriter(transport=transport).submit_once(
            lifecycle=lifecycle,
            lifecycle_id=binding.lifecycle_id,
            order=entry,
            credentials=CREDS,
            now=NOW + timedelta(seconds=1),
        )
    assert transport.calls == []
    assert lifecycle.snapshot(binding.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_PREPARED


def test_entry_writer_persists_unknown_before_network_and_never_blind_retries(tmp_path) -> None:
    lifecycle, _asset_value, _profile_value, entry, binding = _setup(tmp_path)

    def assert_unknown_before_io() -> None:
        state = lifecycle.snapshot(binding.lifecycle_id).state
        assert state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
        assert state.entry_attempt_count == 1
        assert state.restart_action == "RECONCILE_ONLY"

    transport = RecordingTransport(_ack(entry), before=assert_unknown_before_io)
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True),
        transport=transport,
    )
    receipt = writer.submit_once(
        lifecycle=lifecycle,
        lifecycle_id=binding.lifecycle_id,
        order=entry,
        credentials=CREDS,
        now=NOW + timedelta(seconds=1),
    )
    assert receipt.client_order_id == entry.client_order_id
    assert receipt.request_fingerprint == entry.fingerprint
    assert receipt.requested_quantity == entry.quantity
    assert len(receipt.fingerprint) == 64
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["host"] == ALPACA_PAPER_TRADING_HOST
    assert call["path"] == CRYPTO_ORDERS_PATH
    assert call["headers"]["APCA-API-KEY-ID"] == "paper-key"
    assert call["headers"]["APCA-API-SECRET-KEY"] == "paper-secret"
    sent = json.loads(call["body"])
    assert sent == entry.to_payload()
    assert "order_class" not in sent

    with pytest.raises(CryptoLifecycleBlocked, match="ENTRY_PREPARED"):
        writer.submit_once(
            lifecycle=lifecycle,
            lifecycle_id=binding.lifecycle_id,
            order=entry,
            credentials=CREDS,
            now=NOW + timedelta(seconds=2),
        )
    assert len(transport.calls) == 1


def test_network_exception_leaves_entry_unknown_and_requires_reconciliation(tmp_path) -> None:
    lifecycle, _asset_value, _profile_value, entry, binding = _setup(tmp_path)
    transport = RecordingTransport(error=TimeoutError("simulated timeout"))
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True),
        transport=transport,
    )
    with pytest.raises(CryptoPaperWriterAmbiguous, match="must be reconciled"):
        writer.submit_once(
            lifecycle=lifecycle,
            lifecycle_id=binding.lifecycle_id,
            order=entry,
            credentials=CREDS,
            now=NOW + timedelta(seconds=1),
        )
    state = lifecycle.snapshot(binding.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert state.restart_action == "RECONCILE_ONLY"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_order_id": "wrong-client"},
        {"symbol": "ETH/USD"},
        {"asset_class": "us_equity"},
        {"side": "sell"},
        {"type": "market"},
        {"time_in_force": "ioc"},
        {"qty": "0.002"},
        {"filled_qty": "0.002"},
        {"limit_price": "99999"},
    ],
)
def test_untrusted_entry_ack_is_ambiguous_not_success(tmp_path, overrides) -> None:
    lifecycle, _asset_value, _profile_value, entry, binding = _setup(
        tmp_path,
        lifecycle_id="ack-" + next(iter(overrides)).replace("_", "-"),
    )
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True),
        transport=RecordingTransport(_ack(entry, overrides=overrides)),
    )
    with pytest.raises(CryptoPaperWriterAmbiguous, match="reconcile"):
        writer.submit_once(
            lifecycle=lifecycle,
            lifecycle_id=binding.lifecycle_id,
            order=entry,
            credentials=CREDS,
            now=NOW + timedelta(seconds=1),
        )
    assert lifecycle.snapshot(binding.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN


def test_writer_binding_mismatch_fails_before_unknown_or_io(tmp_path) -> None:
    lifecycle, _asset_value, _profile_value, entry, binding = _setup(tmp_path)
    transport = RecordingTransport(_ack(entry))
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True),
        transport=transport,
    )
    forged = type(entry)(
        role=entry.role,
        symbol=entry.symbol,
        side=entry.side,
        quantity=entry.quantity,
        order_type=entry.order_type,
        time_in_force=entry.time_in_force,
        client_order_id=entry.client_order_id,
        product_profile_fingerprint="e" * 64,
        asset_attestation_fingerprint=entry.asset_attestation_fingerprint,
        limit_price=entry.limit_price,
        stop_price=entry.stop_price,
    )
    with pytest.raises(CryptoPaperWriterPolicyError, match="product profile"):
        writer.submit_once(
            lifecycle=lifecycle,
            lifecycle_id=binding.lifecycle_id,
            order=forged,
            credentials=CREDS,
            now=NOW + timedelta(seconds=1),
        )
    assert lifecycle.snapshot(binding.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_PREPARED
    assert transport.calls == []


def test_protection_writer_uses_exact_sell_stop_limit_and_unknown_before_io(tmp_path) -> None:
    lifecycle, asset, profile, _entry, binding = _setup(tmp_path)
    lifecycle.mark_entry_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=1))
    state = lifecycle.reconcile_entry(
        binding.lifecycle_id,
        broker_order_id="broker-entry-1",
        broker_status="filled",
        filled_quantity=Decimal("0.0010"),
        confirmed_net_long_quantity=Decimal("0.0010"),
        at=NOW + timedelta(seconds=2),
    )
    protection = build_crypto_long_protection_order(
        symbol="BTC/USD",
        confirmed_entry_filled_quantity=state.entry_filled_quantity,
        confirmed_net_long_quantity=state.confirmed_net_long_quantity,
        requested_protection_quantity=state.confirmed_net_long_quantity,
        stop_price=Decimal("95000"),
        limit_price=Decimal("94500"),
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=binding.lifecycle_id,
            role=CryptoOrderRole.PROTECTION,
        ),
        product_profile=profile,
        asset_attestation=asset,
    )
    lifecycle.prepare_protection(
        binding.lifecycle_id,
        order=protection,
        at=NOW + timedelta(seconds=3),
    )

    def assert_unknown_before_io() -> None:
        state = lifecycle.snapshot(binding.lifecycle_id).state
        assert state.status is CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN
        assert state.protection_attempt_count == 1

    transport = RecordingTransport(_ack(protection), before=assert_unknown_before_io)
    receipt = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True),
        transport=transport,
    ).submit_once(
        lifecycle=lifecycle,
        lifecycle_id=binding.lifecycle_id,
        order=protection,
        credentials=CREDS,
        now=NOW + timedelta(seconds=4),
    )
    assert receipt.role is CryptoOrderRole.PROTECTION
    payload = json.loads(transport.calls[0]["body"])
    assert payload["side"] == "sell"
    assert payload["type"] == "stop_limit"
    assert payload["time_in_force"] == "gtc"
    assert payload["qty"] == "0.001"
    assert payload["stop_price"] == "95000"
    assert payload["limit_price"] == "94500"
    assert "order_class" not in payload


def test_writer_configuration_and_transport_reject_non_paper_destination() -> None:
    with pytest.raises(CryptoPaperWriterPolicyError, match="exact Alpaca PAPER host"):
        AlpacaPaperCryptoWriterConfig(enabled=True, host="api.alpaca.markets")
    with pytest.raises(CryptoPaperWriterPolicyError, match="exact PAPER orders endpoint"):
        HttpsAlpacaPaperCryptoWriteTransport().post(
            host="api.alpaca.markets",
            path=CRYPTO_ORDERS_PATH,
            headers={},
            body=b"{}",
            timeout_seconds=1,
            max_response_bytes=1024,
        )
    with pytest.raises(CryptoPaperWriterPolicyError, match="exact PAPER orders endpoint"):
        HttpsAlpacaPaperCryptoWriteTransport().post(
            host=ALPACA_PAPER_TRADING_HOST,
            path="/v2/account",
            headers={},
            body=b"{}",
            timeout_seconds=1,
            max_response_bytes=1024,
        )
