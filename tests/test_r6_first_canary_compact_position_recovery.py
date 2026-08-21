from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from autotrade.brokers.alpaca_paper_crypto_order import (
    AlpacaPaperCryptoOrderRequest,
    CryptoOrderRole,
    CryptoOrderSide,
)
from autotrade.brokers.alpaca_paper_crypto_reconciliation import (
    CryptoBrokerReconciliation,
    CryptoPaperReconciliationIntegrityError,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
)
from autotrade.first_canary_fee_aware_recovery import (
    FirstCanaryCompactPositionReconciliationGateway,
    _broker_position_symbol,
    _canonical_position_response_symbol,
    _parse_first_canary_position,
)
from autotrade.product_profile import BrokerOrderType, TimeInForce


NOW = datetime(2026, 8, 20, 19, 30, tzinfo=timezone.utc)
GROSS = Decimal("0.000144320")
NET = Decimal("0.0001439592")
CLIENT_ORDER_ID = "atr6c-entry-compact-position-test"
CREDENTIALS = AlpacaPaperCredentials(
    key_id="PK-COMPACT-POSITION-TEST",
    secret_key="SK-COMPACT-POSITION-TEST",
)


class CaptureReadTransport:
    def __init__(self, *, status_code: int, payload: dict[str, object], request_id: str):
        self.status_code = status_code
        self.payload = payload
        self.request_id = request_id
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        body = json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode()
        return AlpacaPaperHttpResponse(
            status_code=self.status_code,
            body=body,
            final_url=request.url,
            headers={
                "content-type": "application/json",
                "x-request-id": self.request_id,
            },
        )


def _entry_order() -> AlpacaPaperCryptoOrderRequest:
    return AlpacaPaperCryptoOrderRequest(
        role=CryptoOrderRole.ENTRY,
        symbol="BTC/USD",
        side=CryptoOrderSide.BUY,
        quantity=GROSS,
        order_type=BrokerOrderType.LIMIT,
        time_in_force=TimeInForce.IOC,
        client_order_id=CLIENT_ORDER_ID,
        product_profile_fingerprint="a" * 64,
        asset_attestation_fingerprint="b" * 64,
        limit_price=Decimal("72755.3"),
    )


def _order_payload() -> dict[str, object]:
    return {
        "id": "broker-order-compact-position",
        "client_order_id": CLIENT_ORDER_ID,
        "symbol": "BTC/USD",
        "asset_class": "crypto",
        "side": "buy",
        "type": "limit",
        "time_in_force": "ioc",
        "status": "filled",
        "qty": str(GROSS),
        "filled_qty": str(GROSS),
        "limit_price": "72755.3",
        "stop_price": None,
    }


def _position_payload(symbol: str = "BTCUSD") -> dict[str, object]:
    return {
        "symbol": symbol,
        "asset_class": "crypto",
        "qty": str(NET),
        "side": "long",
        "market_value": "10.47",
        "avg_entry_price": "72740.0",
    }


def _response(*, status_code: int, payload: dict[str, object], request_id: str):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return AlpacaPaperHttpResponse(
        status_code=status_code,
        body=body,
        final_url="https://paper-api.alpaca.markets/v2/positions/BTCUSD",
        headers={"content-type": "application/json", "x-request-id": request_id},
    )


def test_compact_position_symbol_is_narrow_and_deterministic() -> None:
    assert _broker_position_symbol("BTC/USD") == "BTCUSD"
    assert _broker_position_symbol("ETH/USD") == "ETHUSD"
    assert _canonical_position_response_symbol(
        "BTCUSD", expected_symbol="BTC/USD"
    ) == "BTC/USD"
    assert _canonical_position_response_symbol(
        "BTC/USD", expected_symbol="BTC/USD"
    ) == "BTC/USD"
    with pytest.raises(
        CryptoPaperReconciliationIntegrityError,
        match="position identity mismatch",
    ):
        _canonical_position_response_symbol("ETHUSD", expected_symbol="BTC/USD")


def test_compact_position_parser_preserves_raw_response_hash() -> None:
    response = _response(
        status_code=200,
        payload=_position_payload("BTCUSD"),
        request_id="req-position-compact",
    )
    snapshot = _parse_first_canary_position(
        response=response,
        expected_symbol="BTC/USD",
        credential_reference=CREDENTIALS.credential_reference,
        observed_at=NOW,
    )
    assert snapshot.symbol == "BTC/USD"
    assert snapshot.quantity == NET
    assert snapshot.absent is False
    assert snapshot.response_sha256 == sha256(response.body).hexdigest()


def test_compact_position_parser_keeps_404_as_zero_absence() -> None:
    response = _response(
        status_code=404,
        payload={"code": 40410000, "message": "position does not exist"},
        request_id="req-position-404",
    )
    snapshot = _parse_first_canary_position(
        response=response,
        expected_symbol="BTC/USD",
        credential_reference=CREDENTIALS.credential_reference,
        observed_at=NOW,
    )
    assert snapshot.symbol == "BTC/USD"
    assert snapshot.quantity == Decimal("0")
    assert snapshot.absent is True


def test_first_canary_gateway_uses_btcusd_position_get_and_preserves_net_exposure() -> None:
    order_transport = CaptureReadTransport(
        status_code=200,
        payload=_order_payload(),
        request_id="req-order-filled",
    )
    position_transport = CaptureReadTransport(
        status_code=200,
        payload=_position_payload("BTCUSD"),
        request_id="req-position-filled",
    )
    gateway = FirstCanaryCompactPositionReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=order_transport,
        position_transport=position_transport,
    )
    evidence = gateway.reconcile(
        credentials=CREDENTIALS,
        order=_entry_order(),
        now=NOW,
    )
    assert isinstance(evidence, CryptoBrokerReconciliation)
    assert evidence.order.filled_quantity == GROSS
    assert evidence.position.quantity == NET
    assert evidence.position.symbol == "BTC/USD"
    assert [request.method for request in order_transport.requests] == ["GET"]
    assert [request.method for request in position_transport.requests] == ["GET"]
    assert order_transport.requests[0].url.endswith(
        "/v2/orders:by_client_order_id?client_order_id=" + CLIENT_ORDER_ID
    )
    assert position_transport.requests[0].url == (
        "https://paper-api.alpaca.markets/v2/positions/BTCUSD"
    )
    assert "%2F" not in position_transport.requests[0].url


def test_first_canary_gateway_fails_closed_on_wrong_compact_position_identity() -> None:
    gateway = FirstCanaryCompactPositionReconciliationGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        order_transport=CaptureReadTransport(
            status_code=200,
            payload=_order_payload(),
            request_id="req-order-wrong-position",
        ),
        position_transport=CaptureReadTransport(
            status_code=200,
            payload=_position_payload("ETHUSD"),
            request_id="req-position-wrong-symbol",
        ),
    )
    with pytest.raises(
        CryptoPaperReconciliationIntegrityError,
        match="position identity mismatch",
    ):
        gateway.reconcile(credentials=CREDENTIALS, order=_entry_order(), now=NOW)
