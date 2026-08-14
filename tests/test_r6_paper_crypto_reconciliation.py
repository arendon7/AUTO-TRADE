from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleBinding,
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from autotrade.brokers.alpaca_paper_crypto_order import (
    CryptoOrderRole,
    build_crypto_entry_order,
    build_crypto_long_protection_order,
    deterministic_crypto_client_order_id,
)
from autotrade.brokers.alpaca_paper_crypto_reconciliation import (
    AlpacaPaperCryptoReconciliationGateway,
    CryptoPaperReconciliationDisabled,
    CryptoPaperReconciliationIntegrityError,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import BrokerOrderType, ProductCapabilities, TimeInForce


NOW = datetime(2026, 8, 13, 5, 30, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")


def _asset() -> AlpacaPaperCryptoAssetAttestation:
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
        price_increment=Decimal("1"),
        account_attestation_fingerprint="a" * 64,
        credential_reference="b" * 64,
        observed_at=NOW,
        request_id="req-asset",
        response_sha256="c" * 64,
        source_path="/v2/assets/BTC%2FUSD",
    )


def _setup(tmp_path, lifecycle_id="reconcile-001"):
    asset = _asset()
    profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=True,
        marginable=False,
        shortable=False,
    )
    entry = build_crypto_entry_order(
        symbol="BTC/USD",
        quantity=Decimal("0.0010"),
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=lifecycle_id, role=CryptoOrderRole.ENTRY
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
    lifecycle = SQLiteCryptoPaperLifecycle(SQLiteRuntime(tmp_path / f"{lifecycle_id}.sqlite3"))
    lifecycle.prepare(binding)
    lifecycle.mark_entry_submission_unknown(lifecycle_id, at=NOW + timedelta(seconds=1))
    return lifecycle, asset, profile, entry, binding


def _order_payload(order, *, status="filled", filled="0.0010", overrides=None):
    payload = {
        "id": "broker-order-1",
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "asset_class": "crypto",
        "side": order.side.value,
        "type": order.order_type.value,
        "time_in_force": order.time_in_force.value,
        "status": status,
        "qty": str(order.quantity),
        "filled_qty": filled,
        "limit_price": None if order.limit_price is None else str(order.limit_price),
        "stop_price": None if order.stop_price is None else str(order.stop_price),
    }
    if overrides:
        payload.update(overrides)
    return payload


def _position_payload(*, qty="0.0010", overrides=None):
    payload = {
        "symbol": "BTC/USD",
        "asset_class": "crypto",
        "qty": qty,
        "side": "long",
        "market_value": "100",
        "avg_entry_price": "100000",
    }
    if overrides:
        payload.update(overrides)
    return payload


class FakeRead:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        return self.response(request) if callable(self.response) else self.response


def _response(payload, *, status=200, request_id="req-reconcile"):
    return AlpacaPaperHttpResponse(
        status_code=status,
        body=json.dumps(payload).encode(),
        final_url="placeholder",
        headers={"content-type": "application/json", "x-request-id": request_id},
    )


def _bound_response(payload, *, status=200, request_id="req-reconcile"):
    def make(request):
        response = _response(payload, status=status, request_id=request_id)
        return AlpacaPaperHttpResponse(
            status_code=response.status_code,
            body=response.body,
            final_url=request.url,
            headers=response.headers,
        )
    return make


def test_reconciliation_is_disabled_by_default(tmp_path) -> None:
    _lifecycle, _asset_value, _profile, entry, _binding = _setup(tmp_path)
    with pytest.raises(CryptoPaperReconciliationDisabled):
        AlpacaPaperCryptoReconciliationGateway().reconcile(
            credentials=CREDS,
            order=entry,
            now=NOW + timedelta(seconds=2),
        )


def test_entry_reconciliation_reads_exact_client_order_and_position_then_advances_lifecycle(tmp_path) -> None:
    lifecycle, _asset_value, _profile, entry, binding = _setup(tmp_path)
    order_read = FakeRead(_bound_response(_order_payload(entry)))
    position_read = FakeRead(_bound_response(_position_payload()))
    gateway = AlpacaPaperCryptoReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=order_read,
        position_transport=position_read,
    )
    reconciliation = gateway.reconcile(
        credentials=CREDS,
        order=entry,
        now=NOW + timedelta(seconds=2),
    )
    assert reconciliation.order.client_order_id == entry.client_order_id
    assert reconciliation.order.terminal is True
    assert reconciliation.position.quantity == Decimal("0.0010")
    assert reconciliation.position.credential_reference == CREDS.credential_reference
    assert len(reconciliation.fingerprint) == 64
    assert len(order_read.requests) == 1
    assert len(position_read.requests) == 1
    assert order_read.requests[0].url == (
        f"https://{ALPACA_PAPER_TRADING_HOST}/v2/orders:by_client_order_id?client_order_id={entry.client_order_id}"
    )
    assert position_read.requests[0].url == (
        f"https://{ALPACA_PAPER_TRADING_HOST}/v2/positions/BTC%2FUSD"
    )
    state = gateway.apply_to_lifecycle(
        lifecycle=lifecycle,
        lifecycle_id=binding.lifecycle_id,
        requested_order=entry,
        reconciliation=reconciliation,
        at=NOW + timedelta(seconds=3),
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED
    assert state.confirmed_net_long_quantity == Decimal("0.0010")


def test_partial_entry_is_known_but_remains_unprotectable_until_terminal(tmp_path) -> None:
    lifecycle, _asset_value, _profile, entry, binding = _setup(tmp_path)
    gateway = AlpacaPaperCryptoReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=FakeRead(_bound_response(_order_payload(entry, status="partially_filled", filled="0.0004"))),
        position_transport=FakeRead(_bound_response(_position_payload(qty="0.0004"))),
    )
    reconciliation = gateway.reconcile(credentials=CREDS, order=entry, now=NOW + timedelta(seconds=2))
    state = gateway.apply_to_lifecycle(
        lifecycle=lifecycle,
        lifecycle_id=binding.lifecycle_id,
        requested_order=entry,
        reconciliation=reconciliation,
        at=NOW + timedelta(seconds=3),
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED
    assert state.entry_terminal is False


def test_canceled_partial_entry_becomes_terminal_exposure_ready_for_protection(tmp_path) -> None:
    lifecycle, _asset_value, _profile, entry, binding = _setup(tmp_path)
    gateway = AlpacaPaperCryptoReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=FakeRead(_bound_response(_order_payload(entry, status="canceled", filled="0.0004"))),
        position_transport=FakeRead(_bound_response(_position_payload(qty="0.0004"))),
    )
    reconciliation = gateway.reconcile(credentials=CREDS, order=entry, now=NOW + timedelta(seconds=2))
    state = gateway.apply_to_lifecycle(
        lifecycle=lifecycle,
        lifecycle_id=binding.lifecycle_id,
        requested_order=entry,
        reconciliation=reconciliation,
        at=NOW + timedelta(seconds=3),
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED
    assert state.entry_terminal is True
    assert state.confirmed_net_long_quantity == Decimal("0.0004")


def test_order_not_found_never_authorizes_retry(tmp_path) -> None:
    lifecycle, _asset_value, _profile, entry, binding = _setup(tmp_path)
    gateway = AlpacaPaperCryptoReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=FakeRead(_bound_response({"message": "not found"}, status=404)),
        position_transport=FakeRead(_bound_response(_position_payload(qty="0"))),
    )
    with pytest.raises(CryptoPaperReconciliationIntegrityError, match="remains unresolved"):
        gateway.reconcile(credentials=CREDS, order=entry, now=NOW + timedelta(seconds=2))
    assert lifecycle.snapshot(binding.lifecycle_id).state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert lifecycle.snapshot(binding.lifecycle_id).state.restart_action == "RECONCILE_ONLY"


def test_absent_position_is_zero_only_with_explicit_404_response(tmp_path) -> None:
    lifecycle, _asset_value, _profile, entry, binding = _setup(tmp_path)
    gateway = AlpacaPaperCryptoReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=FakeRead(_bound_response(_order_payload(entry, status="canceled", filled="0"))),
        position_transport=FakeRead(_bound_response({"message": "position does not exist"}, status=404)),
    )
    reconciliation = gateway.reconcile(credentials=CREDS, order=entry, now=NOW + timedelta(seconds=2))
    assert reconciliation.position.absent is True
    assert reconciliation.position.quantity == 0
    assert reconciliation.position.credential_reference == CREDS.credential_reference
    state = gateway.apply_to_lifecycle(
        lifecycle=lifecycle,
        lifecycle_id=binding.lifecycle_id,
        requested_order=entry,
        reconciliation=reconciliation,
        at=NOW + timedelta(seconds=3),
    )
    assert state.status is CryptoLifecycleStatus.ENTRY_TERMINAL_NO_FILL


@pytest.mark.parametrize(
    "order_overrides,position_overrides,match",
    [
        ({"client_order_id": "wrong"}, None, "client_order_id mismatch"),
        ({"symbol": "ETH/USD"}, None, "product identity mismatch"),
        ({"asset_class": "us_equity"}, None, "product identity mismatch"),
        ({"side": "sell"}, None, "order semantics mismatch"),
        ({"qty": "0.002"}, None, "quantity mismatch"),
        ({"filled_qty": "0.002"}, None, "quantity mismatch"),
        (None, {"symbol": "ETH/USD"}, "position identity mismatch"),
        (None, {"asset_class": "us_equity"}, "position identity mismatch"),
        (None, {"side": "short"}, "long-only"),
    ],
)
def test_reconciliation_rejects_cross_product_or_quantity_drift(
    tmp_path,
    order_overrides,
    position_overrides,
    match,
) -> None:
    _lifecycle, _asset_value, _profile, entry, _binding = _setup(
        tmp_path, lifecycle_id="reconcile-drift-" + match.split()[0].replace("_", "-")
    )
    order_payload = _order_payload(entry, overrides=order_overrides)
    position_payload = _position_payload(overrides=position_overrides)
    gateway = AlpacaPaperCryptoReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=FakeRead(_bound_response(order_payload)),
        position_transport=FakeRead(_bound_response(position_payload)),
    )
    with pytest.raises(CryptoPaperReconciliationIntegrityError, match=match):
        gateway.reconcile(credentials=CREDS, order=entry, now=NOW + timedelta(seconds=2))


def test_protection_reconciliation_uses_same_client_id_and_position_truth(tmp_path) -> None:
    lifecycle, asset, profile, entry, binding = _setup(tmp_path)
    entry_gateway = AlpacaPaperCryptoReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=FakeRead(_bound_response(_order_payload(entry))),
        position_transport=FakeRead(_bound_response(_position_payload())),
    )
    entry_reconciliation = entry_gateway.reconcile(credentials=CREDS, order=entry, now=NOW + timedelta(seconds=2))
    state = entry_gateway.apply_to_lifecycle(
        lifecycle=lifecycle,
        lifecycle_id=binding.lifecycle_id,
        requested_order=entry,
        reconciliation=entry_reconciliation,
        at=NOW + timedelta(seconds=3),
    )
    protection = build_crypto_long_protection_order(
        symbol="BTC/USD",
        confirmed_entry_filled_quantity=state.entry_filled_quantity,
        confirmed_net_long_quantity=state.confirmed_net_long_quantity,
        requested_protection_quantity=state.confirmed_net_long_quantity,
        stop_price=Decimal("95000"),
        limit_price=Decimal("94500"),
        client_order_id=deterministic_crypto_client_order_id(
            lifecycle_id=binding.lifecycle_id, role=CryptoOrderRole.PROTECTION
        ),
        product_profile=profile,
        asset_attestation=asset,
    )
    lifecycle.prepare_protection(binding.lifecycle_id, order=protection, at=NOW + timedelta(seconds=4))
    lifecycle.mark_protection_submission_unknown(binding.lifecycle_id, at=NOW + timedelta(seconds=5))
    protect_gateway = AlpacaPaperCryptoReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=FakeRead(_bound_response(_order_payload(protection, status="new", filled="0"))),
        position_transport=FakeRead(_bound_response(_position_payload())),
    )
    reconciliation = protect_gateway.reconcile(
        credentials=CREDS,
        order=protection,
        now=NOW + timedelta(seconds=6),
    )
    assert reconciliation.position.credential_reference == CREDS.credential_reference
    state = protect_gateway.apply_to_lifecycle(
        lifecycle=lifecycle,
        lifecycle_id=binding.lifecycle_id,
        requested_order=protection,
        reconciliation=reconciliation,
        at=NOW + timedelta(seconds=7),
    )
    assert state.status is CryptoLifecycleStatus.PROTECTED_OPEN
    assert state.confirmed_net_long_quantity == Decimal("0.0010")
