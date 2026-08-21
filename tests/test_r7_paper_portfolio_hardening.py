from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

import autotrade.brokers.paper_portfolio as portfolio_mod
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperPolicyError,
    AlpacaPaperReadRequest,
    AlpacaPaperUnavailable,
)
from autotrade.brokers.paper_portfolio import (
    AlpacaPaperPortfolioGateway,
    OPEN_ORDERS_QUERY,
    PaperPortfolioIntegrityError,
    PaperPortfolioReadPolicy,
)

NOW = datetime(2026, 8, 21, 14, 10, tzinfo=timezone.utc)
ACCOUNT_ID = "12345678-1234-1234-1234-123456789abc"


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("paper-key-r7-hardening", "paper-secret-r7-hardening")


class _Account:
    def __init__(self, credentials: AlpacaPaperCredentials) -> None:
        self.credentials = credentials

    def attest_account(self, *, credentials, expected_account_id, now):
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
            request_id="req-account-r7-hardening",
            source_host=ALPACA_PAPER_TRADING_HOST,
            source_path="/v2/account",
        )


class _ResponseTransport:
    def __init__(self, *, positions_response: AlpacaPaperHttpResponse, orders_response: AlpacaPaperHttpResponse) -> None:
        self.positions_response = positions_response
        self.orders_response = orders_response

    def read(self, request: AlpacaPaperReadRequest) -> AlpacaPaperHttpResponse:
        return self.positions_response if request.url.endswith("/v2/positions") else self.orders_response


def _response(payload, *, url: str, request_id: str = "req-r7-hardening", status: int = 200, content_type: str = "application/json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload, separators=(",", ":")).encode()
    return AlpacaPaperHttpResponse(
        status_code=status,
        body=body,
        final_url=url,
        headers={"content-type": content_type, "x-request-id": request_id},
    )


def _gateway(positions_response, orders_response):
    credentials = _credentials()
    return credentials, AlpacaPaperPortfolioGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        account_gateway=_Account(credentials),
        transport=_ResponseTransport(positions_response=positions_response, orders_response=orders_response),
    )


def _headers(credentials: AlpacaPaperCredentials) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "AUTO-TRADE-R7/0.1",
        "APCA-API-KEY-ID": credentials.key_id,
        "APCA-API-SECRET-KEY": credentials.secret_key,
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://paper-api.alpaca.markets/v2/positions",
        "https://paper-api.alpaca.markets:444/v2/positions",
        "https://user@paper-api.alpaca.markets/v2/positions",
        "https://paper-api.alpaca.markets/v2/positions#frag",
        "https://paper-api.alpaca.markets/v2/positions?x=1",
        "https://paper-api.alpaca.markets/v2/orders?status=open&limit=500&direction=asc",
        "https://paper-api.alpaca.markets/v2/assets",
    ],
)
def test_read_policy_rejects_noncanonical_urls(url: str) -> None:
    credentials = _credentials()
    with pytest.raises(AlpacaPaperPolicyError):
        PaperPortfolioReadPolicy().validate(
            AlpacaPaperReadRequest("GET", url, 5, _headers(credentials))
        )


@pytest.mark.parametrize("timeout", [0, -1, 16])
def test_read_policy_rejects_invalid_timeout(timeout: float) -> None:
    credentials = _credentials()
    with pytest.raises(AlpacaPaperPolicyError, match="timeout"):
        PaperPortfolioReadPolicy().validate(
            AlpacaPaperReadRequest(
                "GET",
                "https://paper-api.alpaca.markets/v2/positions",
                timeout,
                _headers(credentials),
            )
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda h: {k: v for k, v in h.items() if k != "Accept"},
        lambda h: {**h, "Extra": "x"},
        lambda h: {**h, "Accept": "text/plain"},
        lambda h: {**h, "User-Agent": "wrong"},
        lambda h: {**h, "APCA-API-KEY-ID": " bad"},
        lambda h: {**h, "APCA-API-SECRET-KEY": "bad secret"},
    ],
)
def test_read_policy_rejects_noncanonical_headers(mutator) -> None:
    credentials = _credentials()
    with pytest.raises(AlpacaPaperPolicyError):
        PaperPortfolioReadPolicy().validate(
            AlpacaPaperReadRequest(
                "GET",
                "https://paper-api.alpaca.markets/v2/positions",
                5,
                mutator(_headers(credentials)),
            )
        )


def test_final_url_policy_accepts_only_exact_surfaces() -> None:
    policy = PaperPortfolioReadPolicy()
    policy.validate_final_url("https://paper-api.alpaca.markets/v2/positions")
    policy.validate_final_url(f"https://paper-api.alpaca.markets/v2/orders?{OPEN_ORDERS_QUERY}")
    for url in (
        "https://api.alpaca.markets/v2/positions",
        "https://paper-api.alpaca.markets/v2/positions?x=1",
        "https://paper-api.alpaca.markets/v2/orders?status=all",
        "https://paper-api.alpaca.markets/v2/assets",
    ):
        with pytest.raises(AlpacaPaperPolicyError):
            policy.validate_final_url(url)


def test_snapshot_rejects_naive_time() -> None:
    credentials = _credentials()
    gateway = AlpacaPaperPortfolioGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        account_gateway=_Account(credentials),
        transport=_ResponseTransport(
            positions_response=_response([], url="https://paper-api.alpaca.markets/v2/positions"),
            orders_response=_response([], url=f"https://paper-api.alpaca.markets/v2/orders?{OPEN_ORDERS_QUERY}"),
        ),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        gateway.snapshot(credentials=credentials, expected_account_id=ACCOUNT_ID, now=datetime(2026, 8, 21, 14, 10))


@pytest.mark.parametrize(
    ("positions_payload", "orders_payload", "match"),
    [
        (b"{", [], "strict JSON"),
        ({"not": "array"}, [], "root must be an array"),
        (b"[NaN]", [], "strict JSON"),
        (["not-object"], [], "entry must be an object"),
    ],
)
def test_snapshot_rejects_invalid_position_response(positions_payload, orders_payload, match: str) -> None:
    p = _response(positions_payload, url="https://paper-api.alpaca.markets/v2/positions")
    o = _response(orders_payload, url=f"https://paper-api.alpaca.markets/v2/orders?{OPEN_ORDERS_QUERY}")
    credentials, gateway = _gateway(p, o)
    with pytest.raises(PaperPortfolioIntegrityError, match=match):
        gateway.snapshot(credentials=credentials, expected_account_id=ACCOUNT_ID, now=NOW)


def test_gateway_rejects_http_status_content_type_and_request_id() -> None:
    good_orders = _response([], url=f"https://paper-api.alpaca.markets/v2/orders?{OPEN_ORDERS_QUERY}")
    for response, exc, match in (
        (_response([], url="https://paper-api.alpaca.markets/v2/positions", status=500), AlpacaPaperUnavailable, "unexpected"),
        (_response([], url="https://paper-api.alpaca.markets/v2/positions", content_type="text/plain"), PaperPortfolioIntegrityError, "application/json"),
        (_response([], url="https://paper-api.alpaca.markets/v2/positions", request_id=""), PaperPortfolioIntegrityError, "X-Request-ID"),
    ):
        credentials, gateway = _gateway(response, good_orders)
        with pytest.raises(exc, match=match):
            gateway.snapshot(credentials=credentials, expected_account_id=ACCOUNT_ID, now=NOW)


@pytest.mark.parametrize(
    ("symbol", "asset_class", "expected"),
    [
        ("BTC/USD", "crypto", "BTC/USD"),
        ("ETHUSDT", "crypto", "ETH/USDT"),
        ("SOLUSDC", "crypto", "SOL/USDC"),
        ("AAPL", "us_equity", "AAPL"),
        ("BRK.B", "equity", "BRK.B"),
    ],
)
def test_symbol_canonicalization_valid_cases(symbol: str, asset_class: str, expected: str) -> None:
    assert portfolio_mod._canonical_symbol(symbol, asset_class) == expected


@pytest.mark.parametrize(
    ("symbol", "asset_class"),
    [
        ("BTC/", "crypto"),
        ("$BTCUSD", "crypto"),
        ("USDUSD", "crypto"),
        ("BAD SYMBOL", "equity"),
        ("BTCUSD", "forex"),
    ],
)
def test_symbol_canonicalization_invalid_cases(symbol: str, asset_class: str) -> None:
    with pytest.raises(PaperPortfolioIntegrityError):
        portfolio_mod._canonical_symbol(symbol, asset_class)


def test_decimal_and_string_parsers_reject_corruption() -> None:
    for payload, key in (({}, "qty"), ({"qty": 1}, "qty"), ({"qty": "NaN"}, "qty"), ({"qty": "-1"}, "qty")):
        with pytest.raises(PaperPortfolioIntegrityError):
            portfolio_mod._decimal(payload, key)
    with pytest.raises(PaperPortfolioIntegrityError):
        portfolio_mod._string({"symbol": "BTC\nUSD"}, "symbol")
    with pytest.raises(PaperPortfolioIntegrityError):
        portfolio_mod._decimal_optional({"limit_price": "NaN"}, "limit_price")
    with pytest.raises(PaperPortfolioIntegrityError):
        portfolio_mod._decimal_optional({"limit_price": "-1"}, "limit_price")


def test_open_order_rejects_fill_over_qty_and_invalid_side() -> None:
    base = {
        "id": "o1",
        "client_order_id": "c1",
        "symbol": "BTCUSD",
        "asset_class": "crypto",
        "side": "sell",
        "type": "limit",
        "time_in_force": "ioc",
        "status": "new",
        "qty": "0.1",
        "filled_qty": "0",
        "limit_price": "70000",
        "stop_price": None,
    }
    with pytest.raises(PaperPortfolioIntegrityError, match="exceeds"):
        portfolio_mod._parse_open_order({**base, "filled_qty": "0.2"})
    with pytest.raises(PaperPortfolioIntegrityError, match="side"):
        portfolio_mod._parse_open_order({**base, "side": "hold"})
