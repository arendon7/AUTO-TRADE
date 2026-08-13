from __future__ import annotations

import json

import pytest

from autotrade.brokers.alpaca_paper_crypto_catalog import (
    CRYPTO_ASSETS_PATH,
    CRYPTO_ASSETS_QUERY,
    AlpacaPaperCryptoCatalogGateway,
    PaperCryptoCatalogDisabled,
    PaperCryptoCatalogIntegrityError,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
)


CREDS = AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")


def item(symbol: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "asset-" + symbol.replace("/", "-"),
        "class": "crypto",
        "exchange": "CRYPTO",
        "symbol": symbol,
        "name": symbol + " pair",
        "status": "active",
        "tradable": True,
        "marginable": False,
        "shortable": False,
        "fractionable": True,
        "min_order_size": "0.0001",
        "min_trade_increment": "0.0001",
        "price_increment": "0.01",
    }
    value.update(overrides)
    return value


class CatalogTransport:
    def __init__(self, payload: object, *, content_type: str = "application/json") -> None:
        self.payload = payload
        self.content_type = content_type
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        return AlpacaPaperHttpResponse(
            status_code=200,
            body=json.dumps(self.payload).encode(),
            final_url=request.url,
            headers={"content-type": self.content_type},
        )


def test_crypto_catalog_is_disabled_by_default() -> None:
    with pytest.raises(PaperCryptoCatalogDisabled):
        AlpacaPaperCryptoCatalogGateway(transport=CatalogTransport([item("BTC/USD")])).list_pairs(
            credentials=CREDS
        )


def test_crypto_catalog_uses_one_exact_paper_get_and_sorts_pairs() -> None:
    transport = CatalogTransport([item("ETH/USD"), item("BTC/USD")])
    result = AlpacaPaperCryptoCatalogGateway(
        config=AlpacaPaperGatewayConfig(enabled=True), transport=transport
    ).list_pairs(credentials=CREDS)
    assert [value.symbol for value in result] == ["BTC/USD", "ETH/USD"]
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == (
        f"https://{ALPACA_PAPER_TRADING_HOST}{CRYPTO_ASSETS_PATH}?{CRYPTO_ASSETS_QUERY}"
    )
    assert request.headers["APCA-API-KEY-ID"] == "paper-key"
    assert request.headers["APCA-API-SECRET-KEY"] == "paper-secret"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        ["bad"],
        [item("BTC/USD"), item("BTC/USD")],
        [item("BTC/USD", **{"class": "us_equity"})],
        [item("BTC/USD", exchange="ALPACA")],
        [item("BTC/USD", status="inactive")],
        [item("BTC/USD", tradable=False)],
        [item("BTC/USD", fractionable=False)],
        [item("BTC/USD", marginable=True)],
        [item("BTC/USD", shortable=True)],
        [item("BTC/USD", min_order_size="0")],
        [item("BTCUSD")],
    ],
)
def test_crypto_catalog_rejects_untrusted_shapes_or_capability_drift(payload: object) -> None:
    gateway = AlpacaPaperCryptoCatalogGateway(
        config=AlpacaPaperGatewayConfig(enabled=True), transport=CatalogTransport(payload)
    )
    with pytest.raises((PaperCryptoCatalogIntegrityError, ValueError)):
        gateway.list_pairs(credentials=CREDS)


def test_crypto_catalog_rejects_wrong_content_type() -> None:
    gateway = AlpacaPaperCryptoCatalogGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        transport=CatalogTransport([item("BTC/USD")], content_type="text/html"),
    )
    with pytest.raises(PaperCryptoCatalogIntegrityError, match="application/json"):
        gateway.list_pairs(credentials=CREDS)


def test_crypto_catalog_item_serialization_preserves_broker_precision() -> None:
    result = AlpacaPaperCryptoCatalogGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        transport=CatalogTransport(
            [item("ETH/USD", min_order_size="0.001", min_trade_increment="0.00001", price_increment="0.01")]
        ),
    ).list_pairs(credentials=CREDS)
    assert result[0].to_dict() == {
        "symbol": "ETH/USD",
        "name": "ETH/USD pair",
        "asset_id": "asset-ETH-USD",
        "min_order_size": "0.001",
        "min_trade_increment": "0.00001",
        "price_increment": "0.01",
    }
