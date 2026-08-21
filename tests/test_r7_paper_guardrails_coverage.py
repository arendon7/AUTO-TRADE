from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

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
    PaperPortfolioIntegrityError,
    PaperPortfolioPosition,
    PaperPortfolioReadPolicy,
    PaperPortfolioSnapshot,
    _canonical_symbol,
    _decimal,
    _decimal_optional,
    _parse_open_order,
    _parse_position,
    _request_id,
    _strict_json_array,
    _string,
)
from autotrade.paper_close_plan import (
    CLOSE_PLAN_TTL,
    PaperCloseMode,
    PaperClosePlanError,
    PaperCryptoClosePlan,
    prepare_crypto_close_plan,
)

NOW = datetime(2026, 8, 21, 14, 6, 12, tzinfo=timezone.utc)
ACCOUNT_ID = "12345678-1234-1234-1234-123456789abc"


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("paper-key-r7-coverage", "paper-secret-r7-coverage")


def _account(*, credential_reference: str = "b" * 64) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference="a" * 64,
        credential_reference=credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("99989"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=False,
        attested_at=NOW,
        request_id="req-account-r7-coverage",
        source_host=ALPACA_PAPER_TRADING_HOST,
        source_path="/v2/account",
    )


def _position(**overrides: object) -> PaperPortfolioPosition:
    values: dict[str, object] = {
        "asset_id": "btc-asset",
        "broker_symbol": "BTCUSD",
        "symbol": "BTC/USD",
        "asset_class": "crypto",
        "exchange": "CRYPTO",
        "side": "long",
        "quantity": Decimal("0.000143959"),
        "available_quantity": Decimal("0.000143959"),
        "avg_entry_price": Decimal("72760.25"),
        "current_price": Decimal("72800"),
        "market_value": Decimal("10.48"),
        "cost_basis": Decimal("10.47"),
        "unrealized_pl": Decimal("0.01"),
        "unrealized_plpc": Decimal("0.000955"),
    }
    values.update(overrides)
    return PaperPortfolioPosition(**values)  # type: ignore[arg-type]


def _portfolio(*, observed_at: datetime = NOW, position: PaperPortfolioPosition | None = None) -> PaperPortfolioSnapshot:
    return PaperPortfolioSnapshot(
        account=_account(),
        positions=(position or _position(),),
        open_orders=(),
        positions_request_id="req-pos-r7-coverage",
        orders_request_id="req-orders-r7-coverage",
        positions_response_sha256="c" * 64,
        orders_response_sha256="d" * 64,
        observed_at=observed_at,
    )


def _plan() -> PaperCryptoClosePlan:
    return prepare_crypto_close_plan(
        portfolio=_portfolio(),
        symbol="BTC/USD",
        now=NOW + timedelta(seconds=1),
        limit_price=Decimal("72782"),
    )


class _AccountGateway:
    def __init__(self, credentials: AlpacaPaperCredentials) -> None:
        self.credentials = credentials

    def attest_account(self, *, credentials, expected_account_id, now):
        assert credentials is self.credentials
        assert expected_account_id == ACCOUNT_ID
        return replace(_account(credential_reference=credentials.credential_reference), attested_at=now)


class _ResponseTransport:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content_type: str = "application/json",
        body: bytes | None = None,
        final_url: str | None = None,
        request_id: str = "req-r7-coverage",
    ) -> None:
        self.status_code = status_code
        self.content_type = content_type
        self.body = body
        self.final_url = final_url
        self.request_id = request_id

    def read(self, request: AlpacaPaperReadRequest) -> AlpacaPaperHttpResponse:
        return AlpacaPaperHttpResponse(
            status_code=self.status_code,
            body=self.body if self.body is not None else b"[]",
            final_url=self.final_url or request.url,
            headers={"content-type": self.content_type, "x-request-id": self.request_id},
        )


def _auth_headers() -> dict[str, str]:
    credentials = _credentials()
    return {
        "Accept": "application/json",
        "User-Agent": "AUTO-TRADE-R7/0.1",
        "APCA-API-KEY-ID": credentials.key_id,
        "APCA-API-SECRET-KEY": credentials.secret_key,
    }


def test_close_preparation_rejects_naive_time_and_future_broker_truth() -> None:
    with pytest.raises(PaperClosePlanError, match="timezone-aware"):
        prepare_crypto_close_plan(
            portfolio=_portfolio(),
            symbol="BTC/USD",
            now=datetime(2026, 8, 21, 14, 6, 12),
            limit_price=Decimal("72782"),
        )
    with pytest.raises(PaperClosePlanError, match="stale"):
        prepare_crypto_close_plan(
            portfolio=_portfolio(observed_at=NOW + timedelta(seconds=2)),
            symbol="BTC/USD",
            now=NOW,
            limit_price=Decimal("72782"),
        )


@pytest.mark.parametrize("limit", [Decimal("0"), Decimal("Infinity"), "72782"])
def test_close_preparation_rejects_invalid_limit_types_and_values(limit) -> None:
    with pytest.raises(PaperClosePlanError, match="limit_price"):
        prepare_crypto_close_plan(
            portfolio=_portfolio(),
            symbol="BTC/USD",
            now=NOW,
            limit_price=limit,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("slippage", [Decimal("-0.1"), Decimal("Infinity"), "25"])
def test_close_preparation_rejects_invalid_slippage(slippage) -> None:
    with pytest.raises(PaperClosePlanError, match="slippage"):
        prepare_crypto_close_plan(
            portfolio=_portfolio(),
            symbol="BTC/USD",
            now=NOW,
            limit_price=Decimal("72782"),
            max_slippage_bps=slippage,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("Infinity"), "0.0001"])
def test_close_preparation_rejects_invalid_partial_quantity(quantity) -> None:
    with pytest.raises(PaperClosePlanError, match="partial close quantity"):
        prepare_crypto_close_plan(
            portfolio=_portfolio(),
            symbol="BTC/USD",
            now=NOW,
            quantity=quantity,  # type: ignore[arg-type]
            limit_price=Decimal("72782"),
        )


def test_explicit_available_quantity_is_classified_as_full_close() -> None:
    portfolio = _portfolio()
    plan = prepare_crypto_close_plan(
        portfolio=portfolio,
        symbol="BTC/USD",
        now=NOW,
        quantity=portfolio.positions[0].available_quantity,
        limit_price=Decimal("72782"),
    )
    assert plan.mode is PaperCloseMode.FULL


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"asset_class": "us_equity"}, "crypto"),
        ({"side": "buy"}, "SELL"),
        ({"quantity": Decimal("0")}, "quantity"),
        ({"quantity": Decimal("1")}, "position"),
        ({"observed_available_quantity": Decimal("0.00001")}, "available"),
        ({"max_slippage_bps": Decimal("51")}, "slippage"),
        ({"limit_price": Decimal("72000")}, "slippage"),
        ({"order_type": "market"}, "LIMIT IOC"),
        ({"risk_reducing": False}, "risk-reducing"),
        ({"network_write_authorized": True}, "risk-reducing"),
        ({"retry_post": True}, "retry"),
        ({"live_trading": "ENABLED"}, "LIVE"),
    ],
)
def test_close_plan_dataclass_invariants_fail_closed(changes, match) -> None:
    with pytest.raises(ValueError, match=match):
        replace(_plan(), **changes)


def test_close_plan_rejects_naive_expiry_bad_ttl_and_tampered_hash() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="expires_at"):
        replace(plan, expires_at=plan.expires_at.replace(tzinfo=None))
    with pytest.raises(ValueError, match="TTL"):
        replace(plan, expires_at=plan.prepared_at + CLOSE_PLAN_TTL + timedelta(seconds=1))
    with pytest.raises(ValueError, match="hash"):
        replace(plan, plan_hash="f" * 64)


@pytest.mark.parametrize(
    "read_request",
    [
        AlpacaPaperReadRequest("GET", "http://paper-api.alpaca.markets/v2/positions", 5, _auth_headers()),
        AlpacaPaperReadRequest("GET", "https://paper-api.alpaca.markets:444/v2/positions", 5, _auth_headers()),
        AlpacaPaperReadRequest("GET", "https://paper-api.alpaca.markets/v2/positions?x=1", 5, _auth_headers()),
        AlpacaPaperReadRequest("GET", "https://paper-api.alpaca.markets/v2/account", 5, _auth_headers()),
        AlpacaPaperReadRequest("GET", "https://paper-api.alpaca.markets/v2/positions#fragment", 5, _auth_headers()),
        AlpacaPaperReadRequest("GET", "https://paper-api.alpaca.markets/v2/positions", 0, _auth_headers()),
        AlpacaPaperReadRequest("GET", "https://paper-api.alpaca.markets/v2/positions", 16, _auth_headers()),
    ],
)
def test_portfolio_policy_rejects_noncanonical_requests(read_request) -> None:
    with pytest.raises(AlpacaPaperPolicyError):
        PaperPortfolioReadPolicy().validate(read_request)


def test_portfolio_policy_rejects_bad_headers_and_redirect_targets() -> None:
    headers = _auth_headers()
    headers.pop("Accept")
    with pytest.raises(AlpacaPaperPolicyError, match="allowlist"):
        PaperPortfolioReadPolicy().validate(
            AlpacaPaperReadRequest("GET", "https://paper-api.alpaca.markets/v2/positions", 5, headers)
        )
    headers = _auth_headers()
    headers["User-Agent"] = "wrong"
    with pytest.raises(AlpacaPaperPolicyError, match="non-canonical"):
        PaperPortfolioReadPolicy().validate(
            AlpacaPaperReadRequest("GET", "https://paper-api.alpaca.markets/v2/positions", 5, headers)
        )
    policy = PaperPortfolioReadPolicy()
    for url in (
        "https://api.alpaca.markets/v2/positions",
        "https://paper-api.alpaca.markets/v2/account",
        "https://paper-api.alpaca.markets:444/v2/positions",
    ):
        with pytest.raises(AlpacaPaperPolicyError):
            policy.validate_final_url(url)


@pytest.mark.parametrize(
    ("transport", "exc", "match"),
    [
        (_ResponseTransport(status_code=503), AlpacaPaperUnavailable, "status"),
        (_ResponseTransport(content_type="text/plain"), PaperPortfolioIntegrityError, "application/json"),
        (_ResponseTransport(body=b"{bad-json"), PaperPortfolioIntegrityError, "strict JSON"),
        (_ResponseTransport(body=b"{}"), PaperPortfolioIntegrityError, "array"),
        (_ResponseTransport(request_id=""), PaperPortfolioIntegrityError, "X-Request-ID"),
    ],
)
def test_portfolio_gateway_fails_closed_on_broker_response_integrity(transport, exc, match) -> None:
    credentials = _credentials()
    gateway = AlpacaPaperPortfolioGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        account_gateway=_AccountGateway(credentials),
        transport=transport,
    )
    with pytest.raises(exc, match=match):
        gateway.snapshot(credentials=credentials, expected_account_id=ACCOUNT_ID, now=NOW)


def test_portfolio_gateway_rejects_naive_now() -> None:
    credentials = _credentials()
    gateway = AlpacaPaperPortfolioGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        account_gateway=_AccountGateway(credentials),
        transport=_ResponseTransport(),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        gateway.snapshot(
            credentials=credentials,
            expected_account_id=ACCOUNT_ID,
            now=datetime(2026, 8, 21, 14, 6, 12),
        )


def test_position_parser_fallback_short_and_symbol_variants() -> None:
    raw = {
        "asset_id": "btc-asset",
        "symbol": "BTC/USD",
        "exchange": "CRYPTO",
        "asset_class": "crypto",
        "avg_entry_price": "72760.25",
        "qty": "-0.0001",
        "qty_available": None,
        "side": "short",
        "market_value": "-7.28",
        "cost_basis": "-7.27",
        "unrealized_pl": "-0.01",
        "unrealized_plpc": "-0.001",
        "current_price": "72800",
    }
    position = _parse_position(raw)
    assert position.available_quantity == Decimal("0.0001")
    assert position.risk_direction == "SHORT"
    assert _canonical_symbol("AAPL", "us_equity") == "AAPL"


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ([], "object"),
        ({"asset_class": "crypto", "symbol": "BTCUSD", "qty": "1", "side": "sideways"}, "side"),
        ({"asset_class": "forex", "symbol": "EURUSD", "qty": "1", "side": "long"}, "unsupported"),
    ],
)
def test_position_parser_rejects_malformed_entries(raw, match) -> None:
    with pytest.raises(PaperPortfolioIntegrityError, match=match):
        _parse_position(raw)


def test_open_order_parser_rejects_overfill_and_invalid_side() -> None:
    base = {
        "id": "order-1",
        "client_order_id": "client-1",
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
    with pytest.raises(PaperPortfolioIntegrityError, match="exceeds"):
        _parse_open_order({**base, "filled_qty": "0.0002"})
    with pytest.raises(PaperPortfolioIntegrityError, match="side"):
        _parse_open_order({**base, "side": "hold"})


def test_strict_json_and_scalar_helpers_reject_nonfinite_or_control_data() -> None:
    response = AlpacaPaperHttpResponse(
        status_code=200,
        body=b"[NaN]",
        final_url="https://paper-api.alpaca.markets/v2/positions",
        headers={"content-type": "application/json", "x-request-id": "req-1"},
    )
    with pytest.raises(PaperPortfolioIntegrityError, match="strict JSON"):
        _strict_json_array(response, "positions")
    with pytest.raises(PaperPortfolioIntegrityError, match="request"):
        _request_id(replace(response, headers={"content-type": "application/json"}))
    with pytest.raises(PaperPortfolioIntegrityError, match="control"):
        _string({"symbol": "BTC\nUSD"}, "symbol")
    with pytest.raises(PaperPortfolioIntegrityError, match="finite"):
        _decimal({"qty": "NaN"}, "qty")
    with pytest.raises(PaperPortfolioIntegrityError, match="non-negative"):
        _decimal_optional({"limit_price": "-1"}, "limit_price")
