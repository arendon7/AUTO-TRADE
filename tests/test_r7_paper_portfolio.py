from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperPolicyError,
    AlpacaPaperReadRequest,
)
from autotrade.brokers.alpaca_paper_portfolio import (
    AlpacaPaperPortfolioGateway,
    OPEN_ORDERS_QUERY,
    PaperPortfolioDisabled,
    PaperPortfolioIntegrityError,
    PaperPortfolioReadPolicy,
)

NOW = datetime(2026, 8, 21, 14, 6, tzinfo=timezone.utc)
ACCOUNT_ID = "12345678-1234-1234-1234-123456789abc"


class _Account:
    def __init__(self, credentials: AlpacaPaperCredentials) -> None:
        self.credentials = credentials
        self.calls = 0

    def attest_account(self, *, credentials, expected_account_id, now):
        self.calls += 1
        assert credentials is self.credentials
        assert expected_account_id == ACCOUNT_ID
        return AlpacaPaperAccountAttestation(
            account_id=ACCOUNT_ID,
            account_reference="a" * 64,
            credential_reference=credentials.credential_reference,
            status="ACTIVE",
            currency="USD",
            buying_power=Decimal("99989.50"),
            portfolio_value=Decimal("100000.25"),
            shorting_enabled=False,
            attested_at=now,
            request_id="req-account-r7",
            source_host=ALPACA_PAPER_TRADING_HOST,
            source_path="/v2/account",
        )


class _Transport:
    def __init__(self, positions, orders) -> None:
        self.positions = positions
        self.orders = orders
        self.requests: list[AlpacaPaperReadRequest] = []

    def read(self, request: AlpacaPaperReadRequest) -> AlpacaPaperHttpResponse:
        self.requests.append(request)
        if request.url.endswith("/v2/positions"):
            payload = self.positions
            request_id = "req-positions-r7"
        else:
            assert request.url.endswith(f"/v2/orders?{OPEN_ORDERS_QUERY}")
            payload = self.orders
            request_id = "req-orders-r7"
        return AlpacaPaperHttpResponse(
            status_code=200,
            body=json.dumps(payload, separators=(",", ":")).encode(),
            final_url=request.url,
            headers={"content-type": "application/json", "x-request-id": request_id},
        )


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("paper-key-r7", "paper-secret-r7")


def _btc_position(**overrides):
    value = {
        "asset_id": "btc-asset-id",
        "symbol": "BTCUSD",
        "exchange": "CRYPTO",
        "asset_class": "crypto",
        "avg_entry_price": "72760.25",
        "qty": "0.000143959",
        "qty_available": "0.000143959",
        "side": "long",
        "market_value": "10.48",
        "cost_basis": "10.47",
        "unrealized_pl": "0.01",
        "unrealized_plpc": "0.000955",
        "current_price": "72800.00",
    }
    value.update(overrides)
    return value


def _open_order(**overrides):
    value = {
        "id": "broker-open-order-1",
        "client_order_id": "atr7-order-1",
        "symbol": "BTCUSD",
        "asset_class": "crypto",
        "side": "sell",
        "type": "limit",
        "time_in_force": "ioc",
        "status": "new",
        "qty": "0.0001",
        "filled_qty": "0",
        "limit_price": "72700",
        "stop_price": None,
    }
    value.update(overrides)
    return value


def test_snapshot_reads_account_positions_and_open_orders_get_only() -> None:
    credentials = _credentials()
    account = _Account(credentials)
    transport = _Transport([_btc_position()], [_open_order()])
    gateway = AlpacaPaperPortfolioGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        account_gateway=account,
        transport=transport,
    )
    snapshot = gateway.snapshot(credentials=credentials, expected_account_id=ACCOUNT_ID, now=NOW)
    assert account.calls == 1
    assert [request.method for request in transport.requests] == ["GET", "GET"]
    assert all("paper-api.alpaca.markets" in request.url for request in transport.requests)
    assert snapshot.positions[0].broker_symbol == "BTCUSD"
    assert snapshot.positions[0].symbol == "BTC/USD"
    assert str(snapshot.positions[0].quantity) == "0.000143959"
    assert snapshot.open_orders[0].symbol == "BTC/USD"
    assert snapshot.to_dict()["broker_write_performed"] is False
    assert snapshot.to_dict()["credentials_persisted"] is False
    assert snapshot.to_dict()["live_trading"] == "BLOCKED"
    serialized = json.dumps(snapshot.to_dict(), sort_keys=True)
    assert credentials.key_id not in serialized
    assert credentials.secret_key not in serialized
    assert len(snapshot.fingerprint) == 64


def test_empty_portfolio_is_valid_broker_truth() -> None:
    credentials = _credentials()
    snapshot = AlpacaPaperPortfolioGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        account_gateway=_Account(credentials),
        transport=_Transport([], []),
    ).snapshot(credentials=credentials, expected_account_id=ACCOUNT_ID, now=NOW)
    assert snapshot.positions == ()
    assert snapshot.open_orders == ()
    assert snapshot.gross_exposure == 0


def test_wrong_account_credential_binding_fails_closed() -> None:
    credentials = _credentials()
    account = _Account(credentials)
    original = account.attest_account

    def mismatch(**kwargs):
        return replace(original(**kwargs), credential_reference="f" * 64)

    account.attest_account = mismatch  # type: ignore[method-assign]
    gateway = AlpacaPaperPortfolioGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        account_gateway=account,
        transport=_Transport([], []),
    )
    with pytest.raises(PaperPortfolioIntegrityError, match="credential binding"):
        gateway.snapshot(credentials=credentials, expected_account_id=ACCOUNT_ID, now=NOW)


def test_gateway_is_disabled_by_default() -> None:
    credentials = _credentials()
    gateway = AlpacaPaperPortfolioGateway(account_gateway=_Account(credentials), transport=_Transport([], []))
    with pytest.raises(PaperPortfolioDisabled):
        gateway.snapshot(credentials=credentials, expected_account_id=ACCOUNT_ID, now=NOW)


def test_policy_rejects_post_live_host_and_noncanonical_query() -> None:
    credentials = _credentials()
    headers = {
        "Accept": "application/json",
        "User-Agent": "AUTO-TRADE-R7/0.1",
        "APCA-API-KEY-ID": credentials.key_id,
        "APCA-API-SECRET-KEY": credentials.secret_key,
    }
    policy = PaperPortfolioReadPolicy()
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate(AlpacaPaperReadRequest("POST", "https://paper-api.alpaca.markets/v2/positions", 5, headers))
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate(AlpacaPaperReadRequest("GET", "https://api.alpaca.markets/v2/positions", 5, headers))
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate(AlpacaPaperReadRequest("GET", "https://paper-api.alpaca.markets/v2/orders?status=all", 5, headers))


def test_crypto_position_symbol_and_exposure_integrity_fail_closed() -> None:
    credentials = _credentials()
    for payload, message in (
        (_btc_position(symbol="BTCXYZ"), "quote"),
        (_btc_position(qty="0"), "zero"),
        (_btc_position(qty="-0.1", side="long"), "mismatch"),
        (_btc_position(qty_available="0.5"), "available"),
    ):
        gateway = AlpacaPaperPortfolioGateway(
            config=AlpacaPaperGatewayConfig(enabled=True),
            account_gateway=_Account(credentials),
            transport=_Transport([payload], []),
        )
        with pytest.raises(PaperPortfolioIntegrityError, match=message):
            gateway.snapshot(credentials=credentials, expected_account_id=ACCOUNT_ID, now=NOW)


def test_terminal_order_in_open_order_feed_is_rejected() -> None:
    credentials = _credentials()
    gateway = AlpacaPaperPortfolioGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        account_gateway=_Account(credentials),
        transport=_Transport([], [_open_order(status="filled")]),
    )
    with pytest.raises(PaperPortfolioIntegrityError, match="terminal"):
        gateway.snapshot(credentials=credentials, expected_account_id=ACCOUNT_ID, now=NOW)
